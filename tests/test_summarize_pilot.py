import csv
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from scripts.summarize_pilot import build_summary


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class SummarizePilotTests(unittest.TestCase):
    def test_split_artifacts_and_exact_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / "manifest.json").write_text(
                json.dumps(
                    {
                        "run_id": "test-run",
                        "backend": "openai",
                        "planned_assessments": 2,
                        "completed_assessments": 2,
                        "failed_assessments": 0,
                        "not_attempted_assessments": 0,
                        "conditions": ["A_MEMORY_ONLY", "D_CURRENT_WEB"],
                    }
                ),
                encoding="utf-8",
            )
            (run / "acquisition_gate.json").write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "strict_for_this_run": True,
                        "issues": [],
                    }
                ),
                encoding="utf-8",
            )
            credential_sentinel = "DO_NOT_READ_THIS_CREDENTIAL_SENTINEL"
            (run / ".env").write_text(
                f"OPENAI_API_KEY={credential_sentinel}\n", encoding="utf-8"
            )
            write_csv(
                run / "assessments.csv",
                [
                    {
                        "run_id": "test-run",
                        "symbol": "MU",
                        "condition": "A_MEMORY_ONLY",
                        "repetition": 1,
                        "rating": "BUY",
                        "confidence_0_100": 80,
                        "evidence_sufficiency": "HIGH",
                        "forced_rating_due_to_insufficient_data": False,
                        "analyst_opinions_encountered": False,
                        "analyst_opinions_used": False,
                        "web_search_call_count": 0,
                        "web_search_source_count": 0,
                        "input_tokens": 1000,
                        "output_tokens": 200,
                        "total_tokens": 1200,
                    },
                    {
                        "run_id": "test-run",
                        "symbol": "MU",
                        "condition": "D_CURRENT_WEB",
                        "repetition": 1,
                        "rating": "HOLD",
                        "confidence_0_100": 40,
                        "evidence_sufficiency": "LOW",
                        "forced_rating_due_to_insufficient_data": True,
                        "analyst_opinions_encountered": True,
                        "analyst_opinions_used": False,
                        "web_search_call_count": 2,
                        "web_search_source_count": 5,
                        "input_tokens": 3000,
                        "output_tokens": 400,
                        "total_tokens": 3400,
                    },
                ],
            )
            write_csv(
                run / "analyst_comparison.csv",
                [
                    {
                        "run_id": "test-run",
                        "symbol": "MU",
                        "condition": "A_MEMORY_ONLY",
                        "repetition": 1,
                        "count_normalized_score": 1.0,
                        "count_normalized_rating": "BUY",
                        "portal_rating_yahoo": "STRONG_BUY",
                    },
                    {
                        "run_id": "test-run",
                        "symbol": "MU",
                        "condition": "D_CURRENT_WEB",
                        "repetition": 1,
                        "count_normalized_score": 1.0,
                        "count_normalized_rating": "BUY",
                        "portal_rating_yahoo": "STRONG_BUY",
                    },
                ],
            )
            (run / "errors.csv").write_text(
                "run_id,symbol,condition,repetition,error\n", encoding="utf-8"
            )

            summary = build_summary(run)

            self.assertEqual(summary["completion"]["completed_assessments"], 2)
            self.assertEqual(summary["quality"]["mean_confidence_overall"], 60.0)
            self.assertEqual(
                summary["quality"]["mean_evidence_sufficiency_score_overall"],
                2.0,
            )
            self.assertEqual(summary["web_usage"]["overall"]["web_search_calls"], 2)
            self.assertEqual(summary["token_usage"]["input_tokens"], 4000)
            self.assertEqual(summary["token_usage"]["output_tokens"], 600)
            self.assertEqual(
                Decimal(summary["estimated_api_cost"]["input_cost_usd"]),
                Decimal("0.01000"),
            )
            self.assertEqual(
                Decimal(summary["estimated_api_cost"]["output_cost_usd"]),
                Decimal("0.009"),
            )
            self.assertEqual(
                Decimal(summary["estimated_api_cost"]["web_search_cost_usd"]),
                Decimal("0.02"),
            )
            self.assertEqual(
                Decimal(
                    summary["estimated_api_cost"][
                        "exact_estimated_total_cost_usd"
                    ]
                ),
                Decimal("0.03900"),
            )
            count_matches = summary["analyst_comparison"][
                "count_normalized_baseline"
            ]["overall"]
            self.assertEqual(count_matches["eligible_assessments"], 2)
            self.assertEqual(count_matches["exact_matches"], 1)
            transition = summary["condition_transitions"][0]
            self.assertEqual(transition["rating_transitions"], {"BUY->HOLD": 1})
            self.assertEqual(transition["mean_signed_ordinal_change"], -1.0)
            self.assertEqual(summary["provenance"]["credential_files_read"], [])
            self.assertNotIn(credential_sentinel, json.dumps(summary))

    def test_missing_optional_columns_is_graceful(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / "manifest.json").write_text(
                json.dumps(
                    {
                        "backend": "dry-run",
                        "planned_assessments": 1,
                        "completed_assessments": 1,
                        "failed_assessments": 0,
                    }
                ),
                encoding="utf-8",
            )
            write_csv(
                run / "recommendations.csv",
                [
                    {
                        "symbol": "KO",
                        "condition": "C_QUANTITATIVE",
                        "repetition": 1,
                        "rating": "HOLD",
                    }
                ],
            )

            summary = build_summary(
                run,
                input_usd_per_million_tokens=Decimal("3"),
                output_usd_per_million_tokens=Decimal("20"),
                usd_per_web_search_call=Decimal("0.02"),
            )

            self.assertEqual(
                summary["ratings"]["distribution_overall"]["HOLD"], 1
            )
            self.assertIsNone(summary["quality"]["mean_confidence_overall"])
            self.assertEqual(
                summary["estimated_api_cost"]["exact_estimated_total_cost_usd"],
                "0.00",
            )
            self.assertIsNone(summary["acquisition_gate"]["status"])
            self.assertIn(
                "acquisition_gate.json is absent",
                summary["provenance"]["warnings"],
            )

    def test_failed_paid_call_makes_cost_a_lower_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / "manifest.json").write_text(
                json.dumps(
                    {
                        "backend": "openai",
                        "planned_assessments": 2,
                        "completed_assessments": 1,
                        "failed_assessments": 1,
                    }
                ),
                encoding="utf-8",
            )
            write_csv(
                run / "recommendations.csv",
                [
                    {
                        "symbol": "JPM",
                        "condition": "D_CURRENT_WEB",
                        "repetition": 1,
                        "rating": "BUY",
                        "input_tokens": 1000,
                        "output_tokens": 100,
                        "total_tokens": 1100,
                        "web_search_call_count": 1,
                    }
                ],
            )
            (run / "assessment_errors.csv").write_text(
                "run_id,execution_order,symbol,condition,repetition,error,failed_at_utc\n"
                "run,2,JPM,D_CURRENT_WEB,2,JSONDecodeError: truncated,now\n",
                encoding="utf-8",
            )

            summary = build_summary(run)

            cost = summary["estimated_api_cost"]
            self.assertFalse(cost["complete"])
            self.assertEqual(cost["status"], "LOWER_BOUND_ONLY_MISSING_USAGE")
            self.assertIsNone(cost["exact_estimated_total_cost_usd"])
            self.assertEqual(cost["unknown_paid_failure_usage_count"], 1)
            self.assertEqual(
                cost["lower_bound_total_cost_usd"],
                cost["known_total_cost_usd"],
            )

        with self.subTest("known failed usage is included in exact cost"):
            with tempfile.TemporaryDirectory() as directory:
                run = Path(directory)
                (run / "manifest.json").write_text(
                    json.dumps(
                        {
                            "backend": "openai",
                            "planned_assessments": 2,
                            "completed_assessments": 1,
                            "failed_assessments": 1,
                        }
                    ),
                    encoding="utf-8",
                )
                write_csv(
                    run / "recommendations.csv",
                    [
                        {
                            "symbol": "JPM",
                            "condition": "D_CURRENT_WEB",
                            "repetition": 1,
                            "rating": "BUY",
                            "input_tokens": 1000,
                            "output_tokens": 100,
                            "total_tokens": 1100,
                            "web_search_call_count": 1,
                        }
                    ],
                )
                write_csv(
                    run / "assessment_errors.csv",
                    [
                        {
                            "run_id": "run",
                            "execution_order": 2,
                            "symbol": "JPM",
                            "condition": "D_CURRENT_WEB",
                            "repetition": 2,
                            "error": "JSONDecodeError: truncated",
                            "failed_at_utc": "now",
                            "input_tokens": 500,
                            "output_tokens": 50,
                            "total_tokens": 550,
                            "web_search_call_count": 2,
                        }
                    ],
                )

                summary = build_summary(run)

                self.assertTrue(summary["estimated_api_cost"]["complete"])
                self.assertEqual(
                    summary["estimated_api_cost"][
                        "unknown_paid_failure_usage_count"
                    ],
                    0,
                )
                self.assertEqual(summary["token_usage"]["input_tokens"], 1500)
                self.assertEqual(summary["token_usage"]["output_tokens"], 150)
                self.assertEqual(summary["token_usage"]["total_tokens"], 1650)
                self.assertEqual(summary["token_usage"]["web_search_calls"], 3)
                self.assertEqual(
                    summary["token_usage"]["known_failed_call_usage"][
                        "calls_with_usage"
                    ],
                    1,
                )
                self.assertEqual(
                    summary["token_usage"]["known_failed_call_usage"][
                        "source"
                    ],
                    "assessment_errors.csv",
                )

    def test_generic_rating_text_does_not_create_analyst_exposure(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / "manifest.json").write_text(
                json.dumps(
                    {
                        "backend": "openai",
                        "planned_assessments": 1,
                        "completed_assessments": 1,
                        "failed_assessments": 0,
                        "conditions": ["D_CURRENT_WEB"],
                    }
                ),
                encoding="utf-8",
            )
            write_csv(
                run / "recommendations.csv",
                [
                    {
                        "symbol": "NEE",
                        "condition": "D_CURRENT_WEB",
                        "repetition": 1,
                        "rating": "HOLD",
                        "evidence": json.dumps(
                            [
                                {
                                    "claim": "Current as of the rating date.",
                                    "category": "REGULATORY",
                                }
                            ]
                        ),
                        "thesis": "[]",
                        "risks": "[]",
                        "analyst_terms_in_final_output": "DETECTED_IN_MODEL_OUTPUT_EVIDENCE",
                        "analyst_opinions_encountered": False,
                        "analyst_opinions_used": False,
                        "web_search_call_count": 0,
                        "input_tokens": 10,
                        "output_tokens": 10,
                    }
                ],
            )

            summary = build_summary(run)

            statuses = summary["analyst_exposure_audit"]["overall"][
                "final_output_audit_status_counts"
            ]
            self.assertEqual(
                statuses, {"NOT_DETECTED_IN_MODEL_OUTPUT_EVIDENCE": 1}
            )


if __name__ == "__main__":
    unittest.main()
