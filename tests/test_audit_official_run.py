import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from scripts.audit_official_run import (
    _response_body,
    apply_alternative_date_rule,
    classify_offending_item,
    cluster_bootstrap,
    condition_pair_transitions,
    hypergeometric_upper_tail,
    parse_bool,
    replay_validation,
    retrieved_urls,
)
from sp500_ai.analysis import RATING_SCORE


CUTOFF = date(2026, 8, 7)
PLAN = {"as_of_date": "2026-08-07", "horizon_months": 12}


def evidence(**overrides):
    item = {
        "category": "MANAGEMENT_GUIDANCE",
        "claim": "Management guided fiscal 2027 revenue growth of 5% to 6%.",
        "origin": "WEB_PRIMARY",
        "observation_date": "2027-06-30",
        "publication_date": "2026-07-29",
        "source_url": "https://example.com/release",
    }
    item.update(overrides)
    return item


def payload(*items):
    return {
        "symbol": "ABC",
        "as_of": "2026-08-07",
        "horizon_months": 12,
        "rating": "HOLD",
        "confidence_0_100": 55,
        "evidence_sufficiency": "MEDIUM",
        "forced_rating_due_to_insufficient_data": False,
        "reason_codes": ["FUNDAMENTALS"],
        "thesis": ["One", "Two", "Three"],
        "risks": ["Risk one", "Risk two", "Risk three"],
        "limitations": ["Limited"],
        "analyst_opinions_encountered": False,
        "analyst_opinions_used": False,
        "evidence": list(items),
    }


class ParseBoolTests(unittest.TestCase):
    def test_text_values_are_parsed_and_missing_is_preserved(self):
        self.assertIs(parse_bool("False"), False)
        self.assertIs(parse_bool("True"), True)
        self.assertIs(parse_bool(float("nan")), pd.NA)
        with self.assertRaises(ValueError):
            parse_bool("maybe")


class TransitionTests(unittest.TestCase):
    def test_direct_change_is_not_the_sum_of_adjacent_changes(self):
        ratings = {"A_MEMORY_ONLY": "HOLD", "B_PRICE_HISTORY": "BUY", "C_QUANTITATIVE": "HOLD", "D_CURRENT_WEB": "HOLD"}
        frame = pd.DataFrame(
            [
                {"symbol": "ABC", "repetition": 1, "condition": condition, "rating_score": RATING_SCORE[rating]}
                for condition, rating in ratings.items()
            ]
        )
        table = condition_pair_transitions(frame).set_index(["left_condition", "right_condition"])
        self.assertEqual(table.loc[("A_MEMORY_ONLY", "B_PRICE_HISTORY"), "changed"], 1)
        self.assertEqual(table.loc[("B_PRICE_HISTORY", "C_QUANTITATIVE"), "changed"], 1)
        self.assertEqual(table.loc[("A_MEMORY_ONLY", "D_CURRENT_WEB"), "changed"], 0)
        self.assertEqual(table.loc[("A_MEMORY_ONLY", "D_CURRENT_WEB"), "comparison"], "direct")


class ClusterBootstrapTests(unittest.TestCase):
    def specs(self):
        symbols = [f"S{index:02d}" for index in range(20) for _ in range(3)]
        clustered = [1.0 if int(symbol[1:]) < 10 else 0.0 for symbol in symbols]
        left = {
            "statistic": "left",
            "family": "test",
            "scale": "percent",
            "unit": "pair",
            "units": pd.DataFrame({"symbol": symbols, "value": clustered}),
        }
        right = dict(left, statistic="right", units=pd.DataFrame({"symbol": symbols, "value": [1.0] * len(symbols)}))
        difference = {"statistic": "right_minus_left", "family": "test", "left": "left", "right": "right"}
        return [left, right], [difference]

    def test_ratio_estimate_interval_and_design_effect(self):
        specs, differences = self.specs()
        result = cluster_bootstrap(specs, differences, resamples=2000, seed=7).set_index("statistic")
        left = result.loc["left"]
        self.assertAlmostEqual(left["estimate"], 50.0)
        self.assertLess(left["ci95_low"], 50.0)
        self.assertGreater(left["ci95_high"], 50.0)
        # Identical values within each three-unit security inflate variance.
        self.assertGreater(left["design_effect"], 2.0)
        difference = result.loc["right_minus_left"]
        self.assertAlmostEqual(difference["estimate"], 50.0)
        self.assertEqual(difference["share_of_resamples_with_opposite_sign"], 0.0)

    def test_fixed_seed_is_reproducible(self):
        specs, differences = self.specs()
        first = cluster_bootstrap(specs, differences, resamples=500, seed=11)
        second = cluster_bootstrap(specs, differences, resamples=500, seed=11)
        pd.testing.assert_frame_equal(first, second)


class HypergeometricTests(unittest.TestCase):
    def test_upper_tail_matches_enumeration(self):
        self.assertAlmostEqual(hypergeometric_upper_tail(0, 6, 2, 3), 1.0)
        self.assertAlmostEqual(hypergeometric_upper_tail(1, 6, 2, 3), 0.8)
        self.assertAlmostEqual(hypergeometric_upper_tail(2, 6, 2, 3), 0.2)


class FailureClassificationTests(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(classify_offending_item(evidence(), CUTOFF), "FORECAST_OR_GUIDANCE_PERIOD_END")
        self.assertEqual(
            classify_offending_item(
                evidence(claim="The stockholder vote was scheduled for August 12.", observation_date="2026-08-12"),
                CUTOFF,
            ),
            "PRE_ANNOUNCED_EVENT_OR_POLICY_DATE",
        )
        self.assertEqual(
            classify_offending_item(evidence(publication_date="2026-08-10"), CUTOFF),
            "POST_CUTOFF_PUBLICATION",
        )
        self.assertEqual(
            classify_offending_item(evidence(origin="OTHER", observation_date="2026-08-07"), CUTOFF),
            "URL_ON_NON_WEB_ORIGIN",
        )
        self.assertEqual(
            classify_offending_item(evidence(observation_date="2026-06-30"), CUTOFF), "NOT_OFFENDING"
        )


class AlternativeDateRuleTests(unittest.TestCase):
    def test_dated_web_forecast_endpoint_passes_only_under_sensitivity_rule(self):
        original = payload(evidence())
        self.assertIn("after the assessment cutoff", replay_validation(original, symbol="ABC", condition="D_CURRENT_WEB", plan=PLAN))
        modified, changed = apply_alternative_date_rule(original, CUTOFF)
        self.assertEqual(changed, 1)
        self.assertIsNone(replay_validation(modified, symbol="ABC", condition="D_CURRENT_WEB", plan=PLAN))
        self.assertEqual(original["evidence"][0]["observation_date"], "2027-06-30")

    def test_post_cutoff_publication_and_supplied_dates_still_fail(self):
        late = payload(evidence(publication_date="2026-08-10"))
        modified, changed = apply_alternative_date_rule(late, CUTOFF)
        self.assertEqual(changed, 0)
        self.assertIsNotNone(replay_validation(modified, symbol="ABC", condition="D_CURRENT_WEB", plan=PLAN))

        supplied = payload(
            evidence(origin="SUPPLIED_FUNDAMENTAL", publication_date=None, source_url=None, category="FUNDAMENTALS")
        )
        modified, changed = apply_alternative_date_rule(supplied, CUTOFF)
        self.assertEqual(changed, 0)
        self.assertIsNotNone(replay_validation(modified, symbol="ABC", condition="D_CURRENT_WEB", plan=PLAN))

    def test_identity_mismatch_is_rejected(self):
        error = replay_validation(payload(), symbol="XYZ", condition="D_CURRENT_WEB", plan=PLAN)
        self.assertIn("identity mismatch", error)


class RawResponseTests(unittest.TestCase):
    def test_both_archived_shapes_and_logged_urls(self):
        body = {
            "output": [
                {"type": "web_search_call", "action": {"type": "search", "sources": [{"type": "url", "url": "https://a.com/x/"}]}},
                {"type": "web_search_call", "action": {"type": "open_page", "url": "https://b.com/y#part"}},
                {"type": "message", "content": [{"type": "output_text", "text": "{}"}]},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / "raw_responses").mkdir()
            (run / "raw_responses" / "accepted.json").write_text(json.dumps(body), encoding="utf-8")
            (run / "raw_responses" / "failed.json").write_text(
                json.dumps({"custom_id": "t", "error": None, "id": "b", "response": {"body": body}}),
                encoding="utf-8",
            )
            for name in ("accepted.json", "failed.json"):
                loaded = _response_body(run, {"raw_path": f"raw_responses/{name}"})
                self.assertEqual(retrieved_urls(loaded), {"https://a.com/x", "https://b.com/y"})
            self.assertEqual(_response_body(run, {"raw_path": "raw_responses/missing.json"}), {})


if __name__ == "__main__":
    unittest.main()
