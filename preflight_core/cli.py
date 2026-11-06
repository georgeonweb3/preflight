"""Preflight CLI.

  preflight check <contract-address-or-url> --chain bsc [--wallet 0x...]
                  [--json] [--out report.json]
  preflight --selftest

Exit codes: 0 = check completed (the verdict itself may be BLOCKED -
that is a successful check doing its job), 2 = input could not be
resolved to a contract address, 3 = self-test failed.
"""
import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .chains import CHAINS
from .check import run_check
from .keccak import selftest as keccak_selftest
from .resolve import ADDR_RE

_CARD_WIDTH = 72


def _short(addr, head=10, tail=6):
    if not addr or len(addr) <= head + tail + 3:
        return addr
    return f"{addr[:head]}...{addr[-tail:]}"


def _fmt_supply(s):
    if s.get("total_supply") is None and s.get("max_supply") is None:
        return "unknown (no supply functions answered)"
    return (f"total {s.get('total_supply')} · max {s.get('max_supply')} · "
            f"remaining {s.get('remaining')}")


def render_text(r: dict) -> str:
    chain = r["chain"]
    lines = ["=" * _CARD_WIDTH,
             f"PREFLIGHT v{r['version']} · {chain['name']} · read-only: nothing signed, nothing sent",
             "=" * _CARD_WIDTH]
    res = r.get("resolution", {})
    lines.append(f"Contract   {res.get('contract') or '(unresolved)'}")
    lines.append(f"           ({res.get('note')})")
    if r["verdict"] == "UNRESOLVED_INPUT":
        lines.append("-" * _CARD_WIDTH)
        lines.append("VERDICT    UNRESOLVED_INPUT")
        for reason in r.get("reasons", []):
            lines.append(f"  - {reason['message']}")
        lines.append("=" * _CARD_WIDTH)
        return "\n".join(lines)

    # A not-a-contract report legitimately carries only the sections
    # that ran (deployment / verification / gas) - probing stops at
    # the bytecode. Every section below is therefore read defensively;
    # the card must render for that verdict too, not crash on it.
    c = r["checks"]
    ident = c.get("identity") or {}
    ver = c.get("verification") or {"status": "unknown"}
    name = ident.get("name") or "unknown name"
    sym = f" (${ident.get('symbol')})" if ident.get("symbol") else ""
    lines.append(f"Identity   {name}{sym} · {ident.get('type')}")
    lines.append(f"Source     Sourcify: {ver.get('status', 'unknown').replace('_', ' ')}")
    dep = c.get("deployment") or {}
    if dep.get("is_proxy_eip1967"):
        lines.append(f"Proxy      yes · implementation {_short(dep.get('implementation'))}")
    price = c.get("price") or {}
    if price.get("wei") is None:
        lines.append("Price      unknown (no price function answered)")
    elif price["wei"] == 0:
        lines.append(f"Price      0 {chain['native_symbol']} via {price.get('via_function')} - free, gas only")
    else:
        lines.append(f"Price      {price['native']} {chain['native_symbol']} via {price.get('via_function')}")
    lines.append(f"Supply     {_fmt_supply(c.get('supply') or {})}")
    opens = (c.get("open_status") or {}).get("probes") or {}
    if opens:
        shown = ", ".join(f"{k}={v}" for k, v in opens.items())
        lines.append(f"Open       {shown}")
    else:
        lines.append("Open       no open-status function answered")
    w = c.get("wallet") or {}
    if r.get("wallet"):
        holds = w.get("balance_of")
        bits = [f"holds {holds if holds is not None else 'unknown'}"]
        if w.get("minted_counts"):
            bits.append("minted " + ", ".join(f"{k}={v}" for k, v in w["minted_counts"].items()))
        lines.append(f"Wallet     {_short(r['wallet'])} · " + " · ".join(bits))
    sim = c.get("simulation") or {}
    if sim.get("attempts"):
        for a in sim["attempts"]:
            extra = f" - {a['reason']}" if a.get("reason") else ""
            lines.append(f"Simulate   {a['function']} @ {a['value_native']} {chain['native_symbol']} -> {a['result'].upper()}{extra}")
    elif "simulation" in c:
        lines.append("Simulate   no known mint/claim function to simulate")
    gas_section = c.get("gas") or {}
    gas = gas_section.get("snapshot") or {}
    if gas.get("ok"):
        band = gas.get("band") or "band n/a"
        est = gas_section.get("mint_estimate") or {}
        est_txt = ""
        if est.get("ok") and est.get("est_cost_usd") is not None:
            est_txt = f" · est. mint cost ${est['est_cost_usd']:.4f} ({est['gas_units']} gas units)"
        elif est.get("ok"):
            est_txt = f" · est. mint {est['gas_units']} gas units"
        price_txt = f" · {chain['native_symbol']} ${gas['native_usd']:,.2f}" if gas.get("native_usd") else ""
        lines.append(f"Gas        {gas['gwei_median']} gwei · {band}{price_txt}{est_txt}")
    else:
        lines.append("Gas        snapshot unavailable")
    lines.append("-" * _CARD_WIDTH)
    lines.append(f"VERDICT    {r['verdict']}")
    tag = {"block": "BLOCK", "caution": "WARN ", "info": " ok  "}
    for reason in r["reasons"]:
        lines.append(f"  [{tag.get(reason['level'], '?????')}] {reason['message']}")
    lines.append("-" * _CARD_WIDTH)
    lines.append(f"History    {r.get('identification_path')}")
    block = dep.get("block_at_check")
    lines.append(f"Time       {r.get('total_seconds')}s" + (f" · block {block}" if block else ""))
    lines.append("=" * _CARD_WIDTH)
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="preflight",
                                 description="Check before you sign. Read-only mint/claim contract precheck.")
    ap.add_argument("--version", action="version", version=f"preflight {__version__}")
    ap.add_argument("--selftest", action="store_true", help="verify the built-in Keccak-256 and exit")
    sub = ap.add_subparsers(dest="cmd")
    ck = sub.add_parser("check", help="run a precheck on a contract address or project URL")
    ck.add_argument("target", help="contract address (0x...) or project/mint page URL")
    ck.add_argument("--chain", default="bsc", choices=sorted(CHAINS), help="chain adapter (default: bsc)")
    ck.add_argument("--wallet", default=None,
                    help="wallet to test eligibility/simulate from (default: zero address - eligibility results are wallet-specific, pass yours)")
    ck.add_argument("--library", default=None, help="path to an experience_library.jsonl")
    ck.add_argument("--json", action="store_true", help="print only the JSON verdict (schema preflight.verdict/1)")
    ck.add_argument("--out", default=None, help="also write the JSON verdict to this path")
    args = ap.parse_args(argv)

    if args.selftest:
        if not keccak_selftest():
            print("selftest FAILED: keccak256 mismatch", file=sys.stderr)
            return 3
        print("selftest OK: keccak256 + selectors verified")
        return 0
    if args.cmd != "check":
        ap.print_help()
        return 0

    if args.wallet is not None and not ADDR_RE.fullmatch(args.wallet):
        # A malformed wallet would silently degrade every eligibility
        # answer in the report - fail loudly before anything runs.
        ap.error(f"--wallet must be a full address (0x + 40 hex chars), got {args.wallet!r}")

    report, code = run_check(args.target, chain_key=args.chain,
                             wallet=args.wallet, library_path=args.library)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_text(report))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, default=str))
        print(f"\nJSON verdict written to {args.out}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
