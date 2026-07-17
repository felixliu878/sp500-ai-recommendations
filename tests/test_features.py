import unittest

import numpy as np
import pandas as pd

from sp500_ai.features import build_price_history_packet, compute_features


class FeatureTests(unittest.TestCase):
    def setUp(self):
        dates = pd.bdate_range("2024-01-02", periods=320)
        prices = np.linspace(100.0, 160.0, len(dates))
        self.frame = pd.DataFrame(
            {
                "date": dates,
                "adj_close": prices,
                "close": prices,
                "open": prices,
                "high": prices * 1.01,
                "low": prices * 0.99,
                "volume": np.arange(len(dates)) + 1_000_000,
            }
        )

    def test_feature_packet_has_expected_signals(self):
        features = compute_features(self.frame, benchmark=self.frame.copy())
        self.assertGreater(features["return_12m"], 0)
        self.assertAlmostEqual(features["relative_return_vs_spy_12m"], 0.0, places=6)
        self.assertGreater(features["distance_from_sma200"], 0)
        self.assertEqual(features["observations"], 320)

    def test_monthly_packet_is_bounded(self):
        packet = build_price_history_packet(self.frame, months=6)
        self.assertEqual(len(packet), 6)
        self.assertIn("adjusted_close", packet[-1])


if __name__ == "__main__":
    unittest.main()
