"""Contract probing: the routine read-only questions, asked in parallel.

These tables and this flow come straight from George's bsc-mint-precheck
skill, where they have been exercised against dozens of live mint contracts.
The idea: a fixed battery of eth_call probes answers the routine questions
(is it a contract, is it a proxy, what does it cost, is it open, how much
supply is left, does the mint call succeed) so a human only has to think
about contracts whose shape is genuinely unfamiliar.

Everything here is eth_call / eth_getCode / eth_getStorageAt. Nothing is
signed, nothing is sent.
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from .abi import dec_address, dec_bool, dec_string, dec_uint, encode_call
from .keccak import selector

# (field, signature, decoder-kind)
VIEW_PROBES = [
    ("name", "name()", "string"),
    ("symbol", "symbol()", "string"),
    ("decimals", "decimals()", "uint"),
    ("totalSupply", "totalSupply()", "uint"),
    ("maxSupply", "maxSupply()", "uint"),
    ("MAX_SUPPLY", "MAX_SUPPLY()", "uint"),
    ("maxTotalSupply", "maxTotalSupply()", "uint"),
    ("collectionSize", "collectionSize()", "uint"),
    ("owner", "owner()", "address"),
    ("paused", "paused()", "bool"),
]
PRICE_FUNCS = [
    "mintPrice()", "price()", "cost()", "getPrice()", "publicPrice()",
    "salePrice()", "mint_price()", "claimFee()", "fee()", "tokenPrice()",
    "presalePrice()", "mintFee()", "publicSalePrice()",
]
OPEN_FUNCS = [
    "mintOpen()", "isMintOpen()", "publicSaleActive()", "saleActive()",
    "isSaleActive()", "claimOpen()", "mintingEnabled()", "isPublicSaleLive()",
    "publicMintOpen()", "saleIsActive()", "isMinting()", "isClaimOpen()",
]
WALLET_COUNT_FUNCS = [
    "mintedBy(address)", "addressMintedBalance(address)", "mintedCount(address)",
    "getMintedCount(address)", "claimedBy(address)", "mintsUsedBy(address)",
]
WALLET_BOOL_FUNCS = [
    "hasMinted(address)", "whitelisted(address)", "isWhitelisted(address)",
    "allowlist(address)", "isAllowlisted(address)", "hasClaimed(address)",
]
MINT_CANDIDATES = [
    "mint()", "mint(uint256)", "mint(address,uint256)", "freeMint()",
    "freeMint(uint256)", "publicMint(uint256)", "publicMint()",
    "claim()", "claim(uint256)", "claim(address)", "mintNFT(uint256)",
    "buy(uint256)", "mintToken(uint256)", "mintPublic(uint256)",
    # commit/reveal shapes (e.g. Agent Periodic Lab): the reveal half is a
    # standard-shape call; the commit half is often a project-specific
    # no-arg selector only the official guide documents.
    "reveal(address)",
]
ALL_SIGS = sorted(set(
    [s for _, s, _ in VIEW_PROBES] + PRICE_FUNCS + OPEN_FUNCS +
    WALLET_COUNT_FUNCS + WALLET_BOOL_FUNCS + MINT_CANDIDATES +
    ["balanceOf(address)", "supportsInterface(bytes4)", "implementation()",
     "totalMinted()", "maxMintPerWallet()", "mintLimit()"]
))
EIP1967_IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
EIP1967_ADMIN_SLOT = "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def wei_to_native(wei) -> str:
    if wei is None:
        return "unknown"
    return str((Decimal(wei) / Decimal(10 ** 18)).normalize())


def _fingerprint(code_hex: str):
    present = [s for s in ALL_SIGS if selector(s) in code_hex]
    return present, {selector(s) for s in present}


def probe_contract(rpc, contract: str, wallet: str = None) -> dict:
    """Run the full probe battery against one contract address.

    Returns a checks dict (deployment / identity / supply / price /
    open_status / wallet / simulation) plus the selector fingerprint.
    Pure reads; the caller's verdict engine turns this into a verdict.
    """
    wallet = wallet or ZERO_ADDRESS
    checks = {}

    # --- bytecode + proxy slots + block, in parallel ---
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_code = ex.submit(rpc.call, "eth_getCode", [contract, "latest"])
        f_impl = ex.submit(rpc.call, "eth_getStorageAt", [contract, EIP1967_IMPL_SLOT, "latest"])
        f_admin = ex.submit(rpc.call, "eth_getStorageAt", [contract, EIP1967_ADMIN_SLOT, "latest"])
        f_block = ex.submit(rpc.call, "eth_blockNumber", [])
        code, impl_raw, admin_raw, block = f_code.result(), f_impl.result(), f_admin.result(), f_block.result()

    code_hex = (code or "0x")[2:]
    is_contract = len(code_hex) > 0
    impl = dec_address(impl_raw) if impl_raw and int(impl_raw, 16) != 0 else None
    admin = dec_address(admin_raw) if admin_raw and int(admin_raw, 16) != 0 else None
    present_sigs, selector_set = _fingerprint(code_hex)

    deployment = {
        "is_contract": is_contract,
        "code_size_bytes": len(code_hex) // 2,
        "block_at_check": int(block, 16) if block else None,
        "is_proxy_eip1967": impl is not None,
        "implementation": impl,
        "proxy_admin": admin,
        "functions_present": present_sigs,
    }
    checks["deployment"] = deployment
    if not is_contract:
        checks["selectors"] = []
        return checks

    # For a proxy, identification follows the implementation's code.
    effective_code_hex = code_hex
    if impl:
        impl_code = rpc.call("eth_getCode", [impl, "latest"])
        if impl_code and impl_code != "0x":
            effective_code_hex = impl_code[2:]
            present_sigs, selector_set = _fingerprint(effective_code_hex)
            deployment["functions_present"] = present_sigs
            deployment["implementation_code_size_bytes"] = len(effective_code_hex) // 2
    checks["selectors"] = sorted(selector_set)

    # --- routine view / price / open / wallet probes, in parallel ---
    def probe_view(item):
        field, sig, kind = item
        try:
            res = rpc.eth_call(contract, encode_call(sig, []))
        except Exception as e:
            return field, None, str(e)
        if isinstance(res, dict):
            return field, None, res.get("__revert__") or res.get("__error__")
        if res in (None, "0x"):
            return field, None, "empty"
        dec = {"uint": dec_uint, "bool": dec_bool, "address": dec_address, "string": dec_string}[kind]
        return field, dec(res), None

    def probe_uint(sig):
        try:
            res = rpc.eth_call(contract, encode_call(sig, []))
        except Exception:
            return sig, None
        if isinstance(res, dict) or res in (None, "0x"):
            return sig, None
        return sig, dec_uint(res)

    def probe_bool(sig):
        try:
            res = rpc.eth_call(contract, encode_call(sig, []))
        except Exception:
            return sig, None
        if isinstance(res, dict) or res in (None, "0x"):
            return sig, None
        return sig, dec_bool(res)

    def probe_wallet(sig, kind):
        try:
            res = rpc.eth_call(contract, encode_call(sig, [wallet]))
        except Exception:
            return sig, None
        if isinstance(res, dict) or res in (None, "0x"):
            return sig, None
        return sig, (dec_bool(res) if kind == "bool" else dec_uint(res))

    views, prices, opens, wallet_info, ifaces = {}, {}, {}, {}, {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {}
        for item in VIEW_PROBES:
            futs[ex.submit(probe_view, item)] = ("view", item[0])
        for sig in PRICE_FUNCS:
            futs[ex.submit(probe_uint, sig)] = ("price", sig)
        for sig in OPEN_FUNCS:
            futs[ex.submit(probe_bool, sig)] = ("open", sig)
        for sig in WALLET_COUNT_FUNCS:
            futs[ex.submit(probe_wallet, sig, "uint")] = ("wallet", sig)
        for sig in WALLET_BOOL_FUNCS:
            futs[ex.submit(probe_wallet, sig, "bool")] = ("wallet", sig)
        futs[ex.submit(probe_wallet, "balanceOf(address)", "uint")] = ("wallet", "balanceOf(address)")
        futs[ex.submit(lambda: rpc.eth_call(contract, encode_call("supportsInterface(bytes4)", ["0x80ac58cd"])))] = ("iface", "erc721")
        futs[ex.submit(lambda: rpc.eth_call(contract, encode_call("supportsInterface(bytes4)", ["0xd9b67a26"])))] = ("iface", "erc1155")
        for fut, (kind, key) in futs.items():
            try:
                res = fut.result()
            except Exception:
                continue
            if kind == "view":
                field, val, _ = res
                if val is not None:
                    views[field] = val
            elif kind == "price":
                sig, val = res
                if val is not None:
                    prices[sig] = val
            elif kind == "open":
                sig, val = res
                if val is not None:
                    opens[sig] = val
            elif kind == "wallet":
                sig, val = res
                if val is not None:
                    wallet_info[sig] = val
            elif kind == "iface":
                if isinstance(res, str) and res != "0x":
                    ifaces[key] = dec_bool(res)

    # --- identity / type ---
    ctype = "unknown"
    if ifaces.get("erc721"):
        ctype = "ERC721"
    elif ifaces.get("erc1155"):
        ctype = "ERC1155"
    elif views.get("decimals") is not None and views.get("symbol"):
        ctype = "ERC20-like token"
    checks["identity"] = {
        "type": ctype,
        "name": views.get("name"),
        "symbol": views.get("symbol"),
        "decimals": views.get("decimals"),
        "owner": views.get("owner"),
        "paused": views.get("paused"),
    }

    # --- supply / quota ---
    max_supply = views.get("maxSupply") or views.get("MAX_SUPPLY") or \
        views.get("maxTotalSupply") or views.get("collectionSize")
    total_supply = views.get("totalSupply")
    remaining = None
    if max_supply is not None and total_supply is not None:
        remaining = max_supply - total_supply
    checks["supply"] = {
        "total_supply": total_supply, "max_supply": max_supply, "remaining": remaining,
    }

    # --- price: first answering probe in table order wins; disagreement is data ---
    price_wei, price_func = None, None
    for sig in PRICE_FUNCS:
        if sig in prices:
            price_wei, price_func = prices[sig], sig
            break
    distinct_prices = sorted({v for v in prices.values()})
    checks["price"] = {
        "wei": price_wei,
        "native": wei_to_native(price_wei),
        "via_function": price_func,
        "all_probes_native": {k: wei_to_native(v) for k, v in prices.items()},
        "probes_disagree": len(distinct_prices) > 1,
    }

    checks["open_status"] = {
        "probes": opens,
        "open": (any(opens.values()) if opens else None),
        "paused": views.get("paused"),
    }

    checks["wallet"] = {
        "wallet": wallet,
        "balance_of": wallet_info.get("balanceOf(address)"),
        "minted_counts": {k: v for k, v in wallet_info.items()
                          if k in WALLET_COUNT_FUNCS},
        "flags": {k: v for k, v in wallet_info.items()
                  if k in WALLET_BOOL_FUNCS},
    }

    # --- simulation: eth_call the mint candidates the bytecode actually has ---
    sims = []
    candidates = [s for s in MINT_CANDIDATES if selector(s) in selector_set]
    for sig in candidates:
        inner = sig[sig.index("(") + 1:sig.rindex(")")]
        types = inner.split(",") if inner else []
        args, ok_args = [], True
        for t in types:
            if t == "uint256":
                args.append(1)
            elif t == "address":
                args.append(wallet)
            else:
                ok_args = False
        if not ok_args:
            continue
        values = [0] if price_wei in (None, 0) else [0, price_wei]
        for val in values:
            try:
                data = encode_call(sig, args)
                res = rpc.eth_call(contract, data, from_addr=wallet, value_wei=val)
            except Exception as e:
                res = {"__error__": str(e)}
            if isinstance(res, dict):
                sims.append({
                    "function": sig, "value_native": wei_to_native(val),
                    "value_wei": val, "calldata": data,
                    "result": "revert",
                    "reason": (res.get("__revert__") or res.get("__error__") or "")[:200],
                })
            else:
                sims.append({
                    "function": sig, "value_native": wei_to_native(val),
                    "value_wei": val, "calldata": data,
                    "result": "success", "reason": None,
                })
    checks["simulation"] = {
        "note": "eth_call only - nothing was signed or sent",
        "attempts": sims,
        "candidate_functions": candidates,
        "any_success": any(s["result"] == "success" for s in sims),
        "free_success": any(s["result"] == "success" and s["value_wei"] == 0 for s in sims),
    }
    return checks
