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
        self.assertTrue(pd.isna(result.loc[0, "analyst_rating"]))
        self.assertTrue(pd.isna(result.loc[0, "exact_match_analyst"]))


if __name__ == "__main__":
    unittest.main()
