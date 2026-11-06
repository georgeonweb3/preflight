import unittest

from preflight_core.library import match_library

ADDR = "0x28a38A372639E8C481aD41B9B16E52c3c6366ACC"


def entry(**kw):
    e = {"project": "ACAT", "chain": "BSC", "contract": ADDR,
         "outcome": "success_free_mint_executed", "failure_reason": None,
         "mint_method": None, "selectors": []}
    e.update(kw)
    return e


class TestChainScoping(unittest.TestCase):
    """Regression tests for the 2026-11-06 review addendum: library
    matching ignored the entry's chain field, so the same address on
    another chain inherited this chain's exact-contract history."""

    def test_exact_match_same_chain(self):
        m = match_library([entry()], ADDR, set(), chain="bsc")
        self.assertEqual(m[0]["match_type"], "exact_contract")

    def test_chain_comparison_is_case_insensitive(self):
        m = match_library([entry(chain="bsc")], ADDR, set(), chain="BSC")
        self.assertEqual(m[0]["match_type"], "exact_contract")

    def test_same_address_other_chain_is_not_exact(self):
        # No selectors on the entry, so there is no shape fallback
        # either: a cross-chain twin must produce no match at all.
        m = match_library([entry()], ADDR, set(), chain="base")
        self.assertEqual(m, [])

    def test_cross_chain_twin_can_only_shape_match(self):
        sels = {"a0712d68", "70a08231"}
        m = match_library([entry(selectors=sorted(sels))], ADDR, sels,
                          chain="base")
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["match_type"], "abi_shape")

    def test_entry_without_chain_still_matches_exact(self):
        e = entry()
        del e["chain"]
        m = match_library([e], ADDR, set(), chain="base")
        self.assertEqual(m[0]["match_type"], "exact_contract")

    def test_no_chain_argument_keeps_legacy_behaviour(self):
        m = match_library([entry()], ADDR, set())
        self.assertEqual(m[0]["match_type"], "exact_contract")


if __name__ == "__main__":
    unittest.main()
