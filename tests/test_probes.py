import unittest

from preflight_core.keccak import selector
from preflight_core.probes import MINT_CANDIDATES, probe_contract
from preflight_core.verdict import evaluate

VERIFIED = {"status": "exact_match", "source": "sourcify"}
CHEAP_GAS = {"ok": True, "gwei_median": 0.08, "band": "CHEAP", "native_usd": 883.9}


def code_with(*sigs):
    """Fake runtime bytecode whose hex contains the given selectors."""
    return "".join(selector(s) for s in sigs)


class FakeRpc:
    """Stand-in for rpc.Rpc: canned bytecode, scripted eth_call results.

    call_result may be a dict returned for every eth_call, or a callable
    (to, data, from_addr, value_wei) -> result for per-call scripting.
    """

    def __init__(self, code_hex, call_result=None):
        self._code = "0x" + code_hex
        self._call_result = call_result

    def call(self, method, params):
        if method == "eth_getCode":
            return self._code
        if method == "eth_getStorageAt":
            return "0x" + "00" * 32
        if method == "eth_blockNumber":
            return "0x100"
        return None

    def eth_call(self, to, data, from_addr=None, value_wei=0):
        if callable(self._call_result):
            return self._call_result(to, data, from_addr, value_wei)
        return self._call_result


def codes(reasons):
    return {r["code"] for r in reasons}


class TestSimulationEvidenceRecording(unittest.TestCase):
    """Regression tests for the 2026-11-06 review: probes recorded both
    RPC outcome kinds (__revert__ and __error__) as result "revert", so
    a network outage manufactured a BLOCKED verdict."""

    def test_transport_error_is_recorded_as_error_not_revert(self):
        rpc = FakeRpc(code_with("mint()"),
                      {"__error__": "all endpoints unreachable"})
        checks = probe_contract(rpc, "0x0000000000000000000000000000000000000001")
        attempts = checks["simulation"]["attempts"]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["result"], "error")
        self.assertTrue(checks["simulation"]["any_error"])
        self.assertFalse(checks["simulation"]["any_revert"])

    def test_outage_does_not_produce_a_blocked_verdict(self):
        rpc = FakeRpc(code_with("mint()"),
                      {"__error__": "all endpoints unreachable"})
        checks = probe_contract(rpc, "0x0000000000000000000000000000000000000001")
        verdict, reasons = evaluate(checks, {"status": "none"}, [], CHEAP_GAS)
        self.assertNotEqual(verdict, "BLOCKED")
        self.assertIn("SIMULATION_UNKNOWN", codes(reasons))
        self.assertNotIn("SIMULATION_REVERTS", codes(reasons))
        self.assertNotIn("UNVERIFIED_AND_REVERTING", codes(reasons))

    def test_deterministic_revert_is_still_recorded_as_revert(self):
        rpc = FakeRpc(code_with("mint()"),
                      {"__revert__": "execution reverted: not open"})
        checks = probe_contract(rpc, "0x0000000000000000000000000000000000000001")
        attempts = checks["simulation"]["attempts"]
        self.assertEqual(attempts[0]["result"], "revert")
        verdict, reasons = evaluate(checks, {"status": "none"}, [], CHEAP_GAS)
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("UNVERIFIED_AND_REVERTING", codes(reasons))

    def test_mixed_revert_and_error_is_not_all_reverted(self):
        def handler(to, data, from_addr, value_wei):
            if data.startswith("0x" + selector("mint()")):
                return {"__revert__": "execution reverted"}
            return {"__error__": "endpoint timeout"}

        rpc = FakeRpc(code_with("mint()", "claim()"), handler)
        checks = probe_contract(rpc, "0x0000000000000000000000000000000000000001")
        results = {a["function"]: a["result"]
                   for a in checks["simulation"]["attempts"]}
        self.assertEqual(results, {"mint()": "revert", "claim()": "error"})
        verdict, reasons = evaluate(checks, VERIFIED, [], CHEAP_GAS)
        self.assertNotIn("SIMULATION_REVERTS", codes(reasons))
        self.assertIn("SIMULATION_UNKNOWN", codes(reasons))


class TestCommitRevealHonesty(unittest.TestCase):
    """Regression test for the review's Spec High finding: reveal-only
    (commit/reveal) contracts must land on the NO_KNOWN_MINT_FUNCTION
    caution, not be simulated into a revert-driven BLOCKED."""

    def test_reveal_is_not_a_mint_candidate(self):
        self.assertNotIn("reveal(address)", MINT_CANDIDATES)

    def test_reveal_only_contract_gets_the_caution(self):
        rpc = FakeRpc(code_with("reveal(address)"),
                      {"__revert__": "execution reverted"})
        checks = probe_contract(rpc, "0x0000000000000000000000000000000000000001")
        self.assertEqual(checks["simulation"]["candidate_functions"], [])
        self.assertEqual(checks["simulation"]["attempts"], [])
        verdict, reasons = evaluate(checks, VERIFIED, [], CHEAP_GAS)
        self.assertIn("NO_KNOWN_MINT_FUNCTION", codes(reasons))
        self.assertNotEqual(verdict, "BLOCKED")


if __name__ == "__main__":
    unittest.main()
