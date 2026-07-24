import unittest

import pandas as pd

from sp500_ai.analysis import analyst_score, attach_analyst_comparison, score_to_rating


class AnalysisTests(unittest.TestCase):
    def test_weighted_analyst_score(self):
        counts = {"strong_buy": 2, "buy": 1, "hold": 1, "sell": 0, "strong_sell": 0}
        self.assertEqual(analyst_score(counts), 1.25)
        self.assertEqual(score_to_rating(1.25), "BUY")

    def test_empty_consensus_is_explicitly_missing(self):
        recommendations = pd.DataFrame([{"symbol": "MU", "rating": "HOLD", "rating_score": 0}])
        result = attach_analyst_comparison(recommendations, pd.DataFrame())
        self.assertTrue(pd.isna(result.loc[0, "count_normalized_rating"]))
        self.assertTrue(pd.isna(result.loc[0, "exact_match_portal_yahoo"]))

    def test_portal_and_count_baselines_remain_distinct(self):
        recommendations = pd.DataFrame(
            [
                {
                    "symbol": "MU",
                    "rating": "BUY",
                    "rating_score": 1,
                    "analyst_opinions_used": False,
                    "forced_rating_due_to_insufficient_data": False,
                    "evidence_sufficiency": "MEDIUM",
                }
            ]
        )
        consensus = pd.DataFrame(
            [
                {
                    "symbol": "MU",
                    "count_normalized_score": 1.1,
                    "count_normalized_rating": "BUY",
                    "portal_rating_yahoo": "STRONG_BUY",
                }
            ]
        )
        result = attach_analyst_comparison(recommendations, consensus)
        self.assertTrue(result.loc[0, "exact_match_count_normalized_analyst"])
        self.assertFalse(result.loc[0, "exact_match_portal_yahoo"])
        self.assertTrue(
            result.loc[
                0,
                "rating_differs_from_portal_without_reported_analyst_use_and_sufficient_evidence",
            ]
        )
        self.assertFalse(
            result.loc[
                0,
                "rating_differs_from_count_normalized_without_reported_analyst_use_and_sufficient_evidence",
            ]
        )

    def test_forced_low_evidence_rating_is_not_an_independence_signal(self):
        recommendations = pd.DataFrame(
            [
                {
                    "symbol": "MU",
                    "rating": "HOLD",
                    "rating_score": 0,
                    "analyst_opinions_used": False,
                    "forced_rating_due_to_insufficient_data": True,
                    "evidence_sufficiency": "LOW",
                }
            ]
        )
        consensus = pd.DataFrame(
            [
                {
                    "symbol": "MU",
                    "count_normalized_score": 1.0,
                    "count_normalized_rating": "BUY",
                    "portal_rating_yahoo": "STRONG_BUY",
                }
            ]
        )
        result = attach_analyst_comparison(recommendations, consensus)
        self.assertFalse(
            result.loc[
                0,
                "rating_differs_from_portal_without_reported_analyst_use_and_sufficient_evidence",
            ]
        )


if __name__ == "__main__":
    unittest.main()
