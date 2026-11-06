"""Experience library: past mint outcomes, matched against new contracts.

One JSON object per line: project, contract, outcome, failure reason and
the contract's selector fingerprint. Matching is exact-contract first,
then ABI-shape similarity (Jaccard over the selector set).

The rule from the parent skill stands: history is a hint, never a
substitute. A match changes what we tell the user ("this shape failed
before, here is why") - it never skips a live check, and addresses,
prices and parameters are always re-verified in the same run.
"""
import json
from pathlib import Path

DEFAULT_LIBRARY = Path(__file__).resolve().parent.parent / "data" / "experience_library.jsonl"


def load_library(path=None):
    path = Path(path) if path else DEFAULT_LIBRARY
    entries = []
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def match_library(entries, contract, selector_set):
    """Up to 3 matches: exact contract, then selector-set similarity >= 0.25."""
    out = []
    for e in entries:
        if e.get("contract") and contract and e["contract"].lower() == contract.lower():
            out.append({"project": e.get("project"), "similarity": 1.0,
                        "match_type": "exact_contract", "outcome": e.get("outcome"),
                        "failure_reason": e.get("failure_reason"),
                        "mint_method": e.get("mint_method")})
            continue
        sels = set(e.get("selectors") or [])
        if sels and selector_set:
            inter = len(sels & selector_set)
            union = len(sels | selector_set)
            sim = inter / union if union else 0.0
            if sim >= 0.25:
                out.append({"project": e.get("project"), "similarity": round(sim, 3),
                            "match_type": "abi_shape", "outcome": e.get("outcome"),
                            "failure_reason": e.get("failure_reason"),
                            "mint_method": e.get("mint_method")})
    out.sort(key=lambda x: -x["similarity"])
    return out[:3]
