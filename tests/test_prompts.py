import unittest

from sp500_ai.config import PROJECT_ROOT
from sp500_ai.prompts import PromptBuilder, packet_for_condition


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.builder = PromptBuilder(PROJECT_ROOT / "prompts", "test-v1")
        self.history = [{"month_end": "2026-06-30", "adjusted_close": 100, "monthly_volume": 50}]
        self.features = {"return_12m": 0.1}

    def packet(self, condition):
        return packet_for_condition(condition, self.history, self.features, None, {}, None)

    def test_information_ablation_is_enforced(self):
        self.assertEqual(self.packet("A_MEMORY_ONLY"), {"supplied_information": "company identity only"})
        self.assertNotIn("technical_and_return_features", self.packet("B_PRICE_HISTORY"))
        self.assertIn("technical_and_return_features", self.packet("C_QUANTITATIVE"))

    def test_only_condition_d_mentions_web_availability(self):
        prompts = {}
        for condition in ["A_MEMORY_ONLY", "B_PRICE_HISTORY", "C_QUANTITATIVE", "D_CURRENT_WEB"]:
            prompts[condition] = self.builder.build(
                condition, "Micron", "MU", "Information Technology", "2026-07-17", 12, self.packet(condition)
            )
        self.assertIn("Web search is available", prompts["D_CURRENT_WEB"])
        for condition in ["A_MEMORY_ONLY", "B_PRICE_HISTORY", "C_QUANTITATIVE"]:
            self.assertNotIn("Web search is available", prompts[condition])


if __name__ == "__main__":
    unittest.main()
