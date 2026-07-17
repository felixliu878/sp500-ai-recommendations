import unittest

from sp500_ai.llm import ASSESSMENT_SCHEMA, DryRunClient, validate_assessment


class LLMTests(unittest.TestCase):
    def test_schema_disallows_extra_properties(self):
        self.assertFalse(ASSESSMENT_SCHEMA["additionalProperties"])

    def test_dry_run_is_unambiguously_not_ai(self):
        features = {
            "return_12m": 0.1,
            "relative_return_vs_spy_12m": 0.02,
            "distance_from_sma50": 0.01,
            "distance_from_sma200": 0.03,
            "volatility_60d_annualized": 0.25,
        }
        result = DryRunClient().assess("MU", "2026-07-17", 12, "D_CURRENT_WEB", features)
        validate_assessment(result.assessment)
        self.assertEqual(result.metadata["backend"], "DRY_RUN_NOT_AI")
        self.assertFalse(result.metadata["web_search_enabled"])
        self.assertEqual(len(result.assessment["thesis"]), 3)
        self.assertEqual(len(result.assessment["risks"]), 3)


if __name__ == "__main__":
    unittest.main()
