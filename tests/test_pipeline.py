import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from sp500_ai.config import ExperimentConfig
from sp500_ai.pipeline import (
    _analyst_exposure_audit,
    _cache_price,
    _cache_research,
    _load_cached_price,
    _load_cached_research,
    run_experiment,
)
from sp500_ai.providers import (
    PriceResult,
    ResearchResult,
    YAHOO_METRIC_SET_SHA256,
    YAHOO_RESEARCH_SCHEMA_VERSION,
)


def valid_research_result(cutoff: date) -> ResearchResult:
    retrieved_at = f"{cutoff.isoformat()}T10:00:00+00:00"
    return ResearchResult(
        fundamentals={
            "market_cap": {
                "value": 100,
                "as_of_date": cutoff.isoformat(),
                "period_type": "TTM",
                "currency": "USD",
                "data_id": 1,
            },
            "missing_metrics": [],
            "data_retrieved_at_utc": retrieved_at,
            "data_cutoff": cutoff.isoformat(),
        },
        analyst_consensus={"retrievedAtUtc": retrieved_at},
        metadata={
            "schema_version": YAHOO_RESEARCH_SCHEMA_VERSION,
            "metric_set_sha256": YAHOO_METRIC_SET_SHA256,
            "data_cutoff": cutoff.isoformat(),
            "retrieved_at_utc": retrieved_at,
            "errors": {},
        },
        raw={},
        provider="Yahoo",
    )


class PipelineTests(unittest.TestCase):
    def test_operating_does_not_false_match_rating(self):
        payload = {
            "evidence": [{"claim": "Operating margin expanded.", "category": "FUNDAMENTALS"}],
            "thesis": [],
            "risks": [],
        }
        self.assertEqual(
            _analyst_exposure_audit(payload, "D_CURRENT_WEB"),
            "NOT_DETECTED_IN_MODEL_OUTPUT_EVIDENCE",
        )

    def test_explicit_price_target_is_detected(self):
        payload = {
            "evidence": [{"claim": "The analyst price target increased.", "category": "ANALYST"}],
            "thesis": [],
            "risks": [],
        }
        self.assertEqual(
            _analyst_exposure_audit(payload, "D_CURRENT_WEB"),
            "DETECTED_IN_MODEL_OUTPUT_EVIDENCE",
        )

    def test_generic_rating_date_is_not_analyst_exposure(self):
        payload = {
            "evidence": [
                {
                    "claim": "The filing was current as of the rating date.",
                    "category": "REGULATORY",
                }
            ],
            "thesis": [],
            "risks": [],
        }
        self.assertEqual(
            _analyst_exposure_audit(payload, "D_CURRENT_WEB"),
            "NOT_DETECTED_IN_MODEL_OUTPUT_EVIDENCE",
        )

    def test_error_research_cache_is_not_reused(self):
        cutoff = date(2026, 7, 24)
        result = valid_research_result(cutoff)
        result.metadata["errors"] = {"info": "rate limited"}
        with TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            _cache_research(result, cache_dir, "MU")
            self.assertIsNone(_load_cached_research(cache_dir, "MU", cutoff))

    def test_valid_research_cache_is_reused(self):
        cutoff = date(2026, 7, 24)
        with TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            _cache_research(valid_research_result(cutoff), cache_dir, "MU")
            loaded = _load_cached_research(cache_dir, "MU", cutoff)
            self.assertIsNotNone(loaded)
            self.assertEqual(
                loaded.fundamentals["market_cap"]["as_of_date"],
                cutoff.isoformat(),
            )

    def test_research_cache_rejects_bad_cutoff_fields_and_metric_dates(self):
        cutoff = date(2026, 7, 24)
        mutations = {
            "post-cutoff metric": lambda result: result.fundamentals[
                "market_cap"
            ].update({"as_of_date": "2026-07-25"}),
            "malformed metric date": lambda result: result.fundamentals[
                "market_cap"
            ].update({"as_of_date": "July 24"}),
            "fundamental cutoff mismatch": lambda result: result.fundamentals.update(
                {"data_cutoff": "2026-07-23"}
            ),
            "metadata retrieval mismatch": lambda result: result.metadata.update(
                {"retrieved_at_utc": "2026-07-23T23:59:59+00:00"}
            ),
            "analyst retrieval mismatch": lambda result: result.analyst_consensus.update(
                {"retrievedAtUtc": "2026-07-25T00:00:00+00:00"}
            ),
            "unexpected forward valuation": lambda result: result.fundamentals.update(
                {
                    "forward_pe": {
                        "value": 20,
                        "as_of_date": "2026-07-24",
                    }
                }
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), TemporaryDirectory() as directory:
                cache_dir = Path(directory)
                result = deepcopy(valid_research_result(cutoff))
                mutate(result)
                _cache_research(result, cache_dir, "MU")
                self.assertIsNone(
                    _load_cached_research(cache_dir, "MU", cutoff)
                )

    def test_price_cache_after_cutoff_is_not_reused(self):
        frame = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-07-23", "2026-07-25"]),
                "adj_close": [100.0, 101.0],
            }
        )
        result = PriceResult(frame=frame, metadata={}, raw={}, provider="test")
        with TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            _cache_price(result, cache_dir, "MU")
            loaded = _load_cached_price(
                cache_dir,
                "MU",
                date(2026, 7, 1),
                date(2026, 7, 24),
            )
            self.assertIsNone(loaded)

    def test_numeric_preflight_rejects_before_acquisition(self):
        base = {
            "experiment_name": "test",
            "as_of_date": "2026-07-24",
            "pilot_repetitions": 1,
            "full_repetitions": 1,
            "horizon_months": 12,
            "price_lookback_days": 730,
            "max_output_tokens": 3000,
            "consecutive_failure_limit": 3,
            "total_failure_limit": 6,
            "yahoo_request_delay_seconds": 0.0,
            "execution_seed": 1,
            "data_provider": "yahoo",
        }
        cases = [
            ("limit", {}, {"limit": 0}),
            ("repetitions", {}, {"repetitions": 0}),
            ("horizon_months", {"horizon_months": 0}, {}),
            ("price_lookback_days", {"price_lookback_days": 0}, {}),
            ("max_output_tokens", {"max_output_tokens": 0}, {}),
            (
                "consecutive_failure_limit",
                {"consecutive_failure_limit": 0},
                {},
            ),
            ("total_failure_limit", {"total_failure_limit": -1}, {}),
            (
                "yahoo_request_delay_seconds",
                {"yahoo_request_delay_seconds": -0.1},
                {},
            ),
            ("execution_seed", {"execution_seed": 1.5}, {}),
        ]
        for field, updates, arguments in cases:
            with self.subTest(field=field):
                config = ExperimentConfig({**base, **updates})
                with patch(
                    "sp500_ai.pipeline._save_universe_snapshot"
                ) as acquisition:
                    with self.assertRaisesRegex(ValueError, field):
                        run_experiment(
                            config,
                            scope="pilot",
                            backend="dry-run",
                            **arguments,
                        )
                    acquisition.assert_not_called()


if __name__ == "__main__":
    unittest.main()
