import io
import unittest
from datetime import date
from unittest.mock import patch

from openpyxl import Workbook

from sp500_ai.providers import (
    StateStreetUniverseProvider,
    YAHOO_FUNDAMENTAL_METRICS,
    _current_recommendation,
    _parse_fundamental_timeseries,
    ticker_for_yahoo,
)


class ProviderTests(unittest.TestCase):
    def test_state_street_universe_excludes_cash_and_contra_rows(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["SPDR S&P 500 ETF Trust"])
        sheet.append([])
        sheet.append(["As of", "As of 30-Jul-2026"])
        sheet.append([])
        sheet.append(
            [
                "Name",
                "Ticker",
                "Identifier",
                "SEDOL",
                "Weight",
                "Sector",
                "Shares Held",
                "Local Currency",
            ]
        )
        sheet.append(
            [
                "APPLE INC",
                "AAPL",
                "037833100",
                "2046251",
                7.0,
                "Information Technology",
                100,
                "USD",
            ]
        )
        sheet.append(["US DOLLAR", "-", "999USDZ92", "-", 0.04, None, 1000, "USD"])
        sheet.append(["CONTRA RECEIVABLE", "ABC", "TEMP", "-", 0.01, None, 1, "USD"])
        buffer = io.BytesIO()
        workbook.save(buffer)

        with patch("sp500_ai.providers._request_bytes", return_value=buffer.getvalue()):
            frame, metadata, _ = StateStreetUniverseProvider("https://example.test").fetch()

        self.assertEqual(frame["symbol"].tolist(), ["AAPL"])
        self.assertEqual(metadata["security_count"], 1)

    def test_share_class_symbol_conversion(self):
        self.assertEqual(ticker_for_yahoo("BRK.B"), "BRK-B")

    def test_current_consensus_selects_zero_month_row(self):
        records = [
            {"period": "-1m", "strongBuy": 1, "buy": 2},
            {"period": "0m", "strongBuy": 3, "buy": 4, "hold": 1},
        ]
        result = _current_recommendation(records)
        self.assertEqual(result["period"], "0m")
        self.assertEqual(result["strongBuy"], 3)
        self.assertEqual(result["buy"], 4)

    def test_current_consensus_does_not_fallback_to_historical_row(self):
        result = _current_recommendation(
            [{"period": "-1m", "strongBuy": 1, "buy": 2}]
        )
        self.assertEqual(result, {})

    def test_fundamental_parser_applies_cutoff_and_marks_missing(self):
        metric = "trailingTotalRevenue"
        document = {
            "timeseries": {
                "result": [
                    {
                        "meta": {"type": [metric]},
                        metric: [
                            {
                                "asOfDate": "2026-05-31",
                                "periodType": "TTM",
                                "reportedValue": {"raw": 10},
                            },
                            {
                                "asOfDate": "2026-08-31",
                                "periodType": "TTM",
                                "reportedValue": {"raw": 20},
                            },
                        ],
                    },
                    {
                        "meta": {"type": ["trailingMarketCap"]},
                        "trailingMarketCap": [
                            {
                                "asOfDate": "2026-07-23",
                                "reportedValue": {"raw": 100},
                            }
                        ],
                    },
                ]
            }
        }
        result = _parse_fundamental_timeseries(document, date(2026, 7, 24))
        self.assertEqual(result[metric]["latest"]["reportedValue"]["raw"], 10)
        self.assertIn("annualFreeCashFlow", result["missing_metrics"])

    def test_fundamental_parser_rejects_missing_and_post_cutoff_dates(self):
        metric = "trailingTotalRevenue"
        document = {
            "timeseries": {
                "result": [
                    {
                        "meta": {"type": [metric]},
                        metric: [
                            {"reportedValue": {"raw": 10}},
                            {
                                "asOfDate": "2026-08-31",
                                "reportedValue": {"raw": 20},
                            },
                        ],
                    },
                    {
                        "meta": {"type": ["trailingMarketCap"]},
                        "trailingMarketCap": [
                            {
                                "asOfDate": "2026-07-23",
                                "reportedValue": {"raw": 100},
                            }
                        ],
                    },
                ]
            }
        }
        result = _parse_fundamental_timeseries(document, date(2026, 7, 24))
        self.assertIsNone(result[metric]["latest"])
        self.assertIn(metric, result["missing_metrics"])


if __name__ == "__main__":
    unittest.main()
