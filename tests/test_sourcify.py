import unittest

from preflight_core.sourcify import interpret


class TestInterpret(unittest.TestCase):
    def test_exact_match(self):
        body = {"match": "exact_match", "runtimeMatch": "exact_match",
                "creationMatch": "exact_match", "chainId": "56",
                "address": "0x28a38A372639E8C481aD41B9B16E52c3c6366ACC"}
        self.assertEqual(interpret(200, body)[0], "exact_match")

    def test_partial_match(self):
        body = {"match": "partial_match", "chainId": "56", "address": "0x0"}
        self.assertEqual(interpret(200, body)[0], "partial_match")

    def test_runtime_match_only(self):
        body = {"match": None, "runtimeMatch": "exact_match",
                "creationMatch": None, "chainId": "56", "address": "0x0"}
        self.assertEqual(interpret(200, body)[0], "exact_match")

    def test_unverified_is_none_not_unknown(self):
        # the BNB JIAYOU shape: HTTP 404, every match field null
        body = {"match": None, "creationMatch": None, "runtimeMatch": None,
                "chainId": "56",
                "address": "0xc9b4d42abe8966496aa68a736938bf2d508f7a91"}
        self.assertEqual(interpret(404, body)[0], "none")

    def test_garbage_is_unrecognised(self):
        self.assertIsNone(interpret(200, {"unexpected": True}))
        self.assertIsNone(interpret(500, {}))
        self.assertIsNone(interpret(200, None))


if __name__ == "__main__":
    unittest.main()
