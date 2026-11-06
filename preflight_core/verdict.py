"""The verdict engine: checks in, CLEAR / CAUTION / BLOCKED out.

Pure function, no I/O - the same inputs always give the same verdict,
which is what makes it unit-testable and worth trusting. Every verdict
comes with itemised reasons, each tagged block / caution / info, so the
CLI and the JSON schema can show their working instead of asking anyone
to trust a bare label.

Rule sources, all learned the expensive way in production (2026-11-03/04):
- a "free claim" can hide an anti-Sybil fee (Nomio) or a creator fee
  (CodexNFT) -> any price above zero is a caution with the amount shown;
- a mint contract can be unverified while its token is verified, and a
  reverting simulation on an unverified contract is a hard stop
  (BNB JIAYOU) -> unverified + reverting escalates to a block;
- commit/reveal mints expose no standard mint() selector (Agent Periodic
  Lab) -> "no known mint function" is a caution, not a block;
- gas can spike 4x intraday (BBF batch) -> expensive gas is a caution
  with the band named.
"""
from decimal import Decimal


def _r(code, level, message):
    return {"code": code, "level": level, "message": message}


def evaluate(checks: dict, verification: dict, library_matches: list, gas_snapshot: dict):
    deployment = checks.get("deployment", {})
    identity = checks.get("identity", {})
    supply = checks.get("supply", {})
    price = checks.get("price", {})
    open_status = checks.get("open_status", {})
    wallet = checks.get("wallet", {})
    sim = checks.get("simulation", {})
    reasons = []

    # ---- hard blocks -------------------------------------------------
    if not deployment.get("is_contract"):
        reasons.append(_r("NOT_A_CONTRACT", "block",
                          "address has no contract code on this chain (wrong address or wrong chain)"))
        return "BLOCKED", reasons

    if supply.get("remaining") is not None and supply["remaining"] <= 0:
        reasons.append(_r("SOLD_OUT", "block",
                          f"supply exhausted: {supply.get('total_supply')} of {supply.get('max_supply')} minted, 0 remaining"))

    if identity.get("paused") is True or open_status.get("paused") is True:
        reasons.append(_r("PAUSED", "block", "contract is paused"))

    attempts = sim.get("attempts") or []
    sim_all_reverted = bool(attempts) and not sim.get("any_success")
    if sim_all_reverted:
        first = next((a for a in attempts if a.get("reason")), attempts[0])
        reasons.append(_r("SIMULATION_REVERTS", "block",
                          f"every mint/claim function reverted in eth_call simulation "
                          f"(e.g. {first['function']}: {first.get('reason') or 'bare revert'}). "
                          "Closed, sold out, ineligible wallet or wrong parameters are all possible - do not sign blind"))

    opens = open_status.get("probes") or {}
    if opens and not any(opens.values()) and not sim.get("any_success"):
        reasons.append(_r("NOT_OPEN", "block",
                          "every open-status probe that answered says the mint/claim is not open"))

    vstatus = (verification or {}).get("status", "unknown")
    if vstatus == "none" and sim_all_reverted:
        reasons.append(_r("UNVERIFIED_AND_REVERTING", "block",
                          "source code is NOT verified and the mint simulation reverts - "
                          "this is the exact shape of the BNB JIAYOU block: nothing about this contract can be trusted"))

    # ---- verification ------------------------------------------------
    if vstatus == "exact_match":
        reasons.append(_r("SOURCE_VERIFIED", "info", "source code verified on Sourcify (exact match)"))
    elif vstatus == "partial_match":
        reasons.append(_r("SOURCE_VERIFIED_PARTIAL", "info",
                          "source code verified on Sourcify (partial match: same code, different metadata)"))
    elif vstatus == "none":
        reasons.append(_r("UNVERIFIED_SOURCE", "caution",
                          "source code is NOT verified on Sourcify - nobody outside the project can read what this contract does"))
    else:
        reasons.append(_r("VERIFICATION_UNCHECKED", "caution",
                          "verification status could not be checked (Sourcify unreachable) - treat as unverified until confirmed"))

    # ---- contract shape ----------------------------------------------
    if deployment.get("is_proxy_eip1967"):
        reasons.append(_r("UPGRADEABLE_PROXY", "caution",
                          f"upgradeable proxy: the implementation ({deployment.get('implementation')}) can be swapped after this check - re-verify at signing time"))

    if identity.get("owner"):
        reasons.append(_r("OWNER_PRESENT", "info",
                          f"contract has an owner ({identity['owner']}) - check what the owner can change before trusting long-term"))

    # ---- price ---------------------------------------------------------
    price_wei = price.get("wei")
    native_usd = (gas_snapshot or {}).get("native_usd")
    if price_wei is not None and price_wei > 0:
        usd = ""
        if native_usd:
            usd_val = (Decimal(price_wei) / Decimal(10 ** 18)) * Decimal(str(native_usd))
            usd = f" (~${usd_val:.2f})"
        reasons.append(_r("PAID_MINT", "caution",
                          f"not free: price is {price.get('native')} (native) via {price.get('via_function')}{usd} - plus gas"))
    elif price_wei == 0:
        reasons.append(_r("FREE_PRICE", "info", "price probes read 0 - the mint itself is free, gas still applies"))
    if price.get("probes_disagree"):
        reasons.append(_r("PRICE_PROBES_DISAGREE", "caution",
                          f"price functions disagree with each other: {price.get('all_probes_native')} - trust the simulation value, not the label"))
    if price_wei is None:
        reasons.append(_r("PRICE_UNKNOWN", "caution",
                          "no price function answered - the real cost is only visible at simulation/signing time, watch for hidden fees"))

    # ---- open status / simulation shape --------------------------------
    if not (sim.get("candidate_functions") or []):
        reasons.append(_r("NO_KNOWN_MINT_FUNCTION", "caution",
                          "no known mint/claim function found in the bytecode - commit/reveal drops work this way (their first step is project-specific), so check the project's official guide before concluding anything"))
    elif not attempts:
        reasons.append(_r("SIMULATION_NOT_POSSIBLE", "caution",
                          "mint functions exist but none could be simulated with standard arguments"))
    if not opens and sim.get("candidate_functions"):
        reasons.append(_r("OPEN_UNCONFIRMED", "caution",
                          "no open-status function answered - openness is inferred from the simulation only"))
    if opens and not any(opens.values()) and sim.get("any_success"):
        reasons.append(_r("FLAGS_VS_SIMULATION", "info",
                          "open-status flags say closed but a simulation succeeded from this wallet - the simulation is the stronger signal"))

    if sim.get("free_success"):
        reasons.append(_r("SIMULATION_OK", "info", "a mint/claim simulation succeeded at value 0 (eth_call, nothing signed)"))
    elif sim.get("any_success"):
        reasons.append(_r("SIMULATION_OK_PAID", "info", "a mint/claim simulation succeeded at the probed price (eth_call, nothing signed)"))

    # ---- wallet --------------------------------------------------------
    flags = wallet.get("flags") or {}
    if flags.get("hasMinted(address)") is True or flags.get("hasClaimed(address)") is True:
        reasons.append(_r("ALREADY_MINTED_OR_CLAIMED", "caution",
                          "this wallet has already minted/claimed according to the contract - a second one may be blocked or paid"))
    if any(v is False for k, v in flags.items() if "whitelist" in k.lower() or "allowlist" in k.lower()):
        reasons.append(_r("WALLET_NOT_WHITELISTED", "caution",
                          "whitelist/allowlist probes answered False for this wallet"))
    if wallet.get("balance_of"):
        reasons.append(_r("ALREADY_HOLDS", "info",
                          f"wallet already holds {wallet['balance_of']} of this contract's token/NFT"))

    # ---- gas -------------------------------------------------------------
    band = (gas_snapshot or {}).get("band")
    if band == "EXPENSIVE":
        reasons.append(_r("GAS_EXPENSIVE", "caution",
                          f"gas is EXPENSIVE right now ({(gas_snapshot or {}).get('gwei_median')} gwei) - if this is not time-sensitive, waiting is cheaper"))
    elif band == "VERY_EXPENSIVE":
        reasons.append(_r("GAS_VERY_EXPENSIVE", "caution",
                          f"gas is VERY EXPENSIVE right now ({(gas_snapshot or {}).get('gwei_median')} gwei) - do not batch anything at this price"))

    # ---- history ---------------------------------------------------------
    for m in library_matches or []:
        outcome = (m.get("outcome") or "")
        if m.get("match_type") == "exact_contract":
            if "success" in outcome or "executed" in outcome:
                reasons.append(_r("HISTORY_EXACT_SUCCESS", "info",
                                  f"this exact contract is in the experience library: {m.get('project')} - outcome {outcome}"))
            elif outcome:
                reasons.append(_r("HISTORY_EXACT_FAILURE", "caution",
                                  f"this exact contract is in the experience library: {m.get('project')} - outcome {outcome}"
                                  + (f" ({m['failure_reason']})" if m.get("failure_reason") else "")))
        elif m.get("similarity", 0) >= 0.5:
            reasons.append(_r("HISTORY_SHAPE_MATCH", "info",
                              f"ABI shape matches '{m.get('project')}' (similarity {m.get('similarity')}) - identification path reused, everything still re-verified live"))

    # ---- roll up ---------------------------------------------------------
    levels = {r["level"] for r in reasons}
    if "block" in levels:
        return "BLOCKED", reasons
    if "caution" in levels:
        return "CAUTION", reasons
    return "CLEAR", reasons
