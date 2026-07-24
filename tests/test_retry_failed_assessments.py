import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from scripts.retry_failed_assessments import execute_targeted_retry
from scripts.summarize_pilot import build_summary
from sp500_ai.llm import AssessmentResponseError, AssessmentResult


def assessment(
    symbol: str,
    *,
    as_of: str = "2026-07-24",
    rating: str = "BUY",
) -> dict:
    return {
        "symbol": symbol,
        "as_of": as_of,
        "horizon_months": 12,
        "rating": rating,
        "confidence_0_100": 60,
        "evidence_sufficiency": "MEDIUM",
        "forced_rating_due_to_insufficient_data": False,
        "reason_codes": ["FUNDAMENTALS"],
        "thesis": ["One", "Two", "Three"],
        "risks": ["Risk one", "Risk two", "Risk three"],
        "evidence": [
            {
                "claim": "Supplied evidence was evaluated.",
                "category": "FUNDAMENTALS",
                "origin": "SUPPLIED_FUNDAMENTAL",
                "observation_date": "2026-06-30",
                "publication_date": None,
                "source_url": None,
            }
        ],
        "analyst_opinions_encountered": False,
        "analyst_opinions_used": False,
        "limitations": ["Test fixture"],
    }


class FakeClient:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = []

    def assess(
        self,
        prompt: str,
        allow_web: bool,
        condition: str | None = None,
    ) -> AssessmentResult:
        self.calls.append((prompt, allow_web, condition))
        raw = {
            "id": "resp_retry",
            "model": "test-model",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
            },
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "type": "search",
                        "query": "JPM official filing",
                        "sources": [
                            {
                                "type": "url",
                                "url": "https://example.com/filing",
                            }
                        ],
                    },
                }
            ],
        }
        return AssessmentResult(
            assessment=self.payload,
            metadata={
                "backend": "OPENAI_RESPONSES_API",
                "model": "test-model",
                "requested_model": "test-model",
                "web_search_enabled": allow_web,
                "response_id": "resp_retry",
                "retrieved_source_urls": ["https://example.com/filing"],
                "analyst_terms_detected_in_web_actions": False,
                "analyst_web_action_matched_terms": [],
                "web_search_call_count": 1,
                "web_search_source_count": 1,
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
            },
            raw=raw,
        )


class FailedResponseClient:
    def assess(
        self,
        prompt: str,
        allow_web: bool,
        condition: str | None = None,
    ) -> AssessmentResult:
        raw = {
            "id": "resp_failed_retry",
            "model": "test-model",
            "usage": {
                "input_tokens": 200,
                "output_tokens": 75,
                "total_tokens": 275,
            },
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "type": "search",
                        "query": "JPM official filing",
                        "sources": [
                            {
                                "type": "url",
                                "url": "https://example.com/retry-failure",
                            }
                        ],
                    },
                }
            ],
        }
        raise AssessmentResponseError(
            "Structured assessment validation failed",
            raw=raw,
            metadata={
                "backend": "OPENAI_RESPONSES_API",
                "model": "test-model",
                "requested_model": "test-model",
                "web_search_enabled": allow_web,
                "response_id": "resp_failed_retry",
                "retrieved_source_urls": [
                    "https://example.com/retry-failure"
                ],
                "web_search_call_count": 1,
                "web_search_source_count": 1,
                "input_tokens": 200,
                "output_tokens": 75,
                "total_tokens": 275,
            },
        )


def create_original_run(
    root: Path,
    *,
    known_failure_usage: bool = False,
) -> tuple[Path, str, bytes]:
    run = root / "original"
    (run / "prompts").mkdir(parents=True)
    (run / "raw_responses").mkdir()
    manifest = {
        "run_id": "original-run",
        "backend": "openai",
        "model": "test-model",
        "as_of_date": "2026-07-24",
        "conditions": ["A_MEMORY_ONLY", "D_CURRENT_WEB"],
        "planned_assessments": 2,
        "completed_assessments": 1,
        "failed_assessments": 1,
        "not_attempted_assessments": 0,
        "total_tokens": 100,
    }
    (run / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
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
    failure_record = {
        "run_id": "original-run",
        "execution_order": 2,
        "symbol": "JPM",
        "condition": "D_CURRENT_WEB",
        "repetition": 1,
        "error": "JSONDecodeError: truncated",
        "failed_at_utc": "2026-07-24T00:00:00+00:00",
        "response_id": "",
        "model": "",
        "input_tokens": "",
        "output_tokens": "",
        "total_tokens": "",
        "web_search_call_count": "",
        "failed_raw_artifact": "",
    }
    if known_failure_usage:
        failure_record.update(
            {
                "response_id": "resp_original_failure",
                "model": "test-model",
                "input_tokens": 200,
                "output_tokens": 80,
                "total_tokens": 280,
                "web_search_call_count": 2,
                "failed_raw_artifact": (
                    "raw_responses/"
                    "JPM_D_CURRENT_WEB_r1.failed_response.json"
                ),
            }
        )
    pd.DataFrame([failure_record]).to_csv(
        run / "assessment_errors.csv",
        index=False,
    )
    prompt = (
        "Controlled prompt.\n\n"
        "COMPANY IDENTITY\n"
        + json.dumps(
            {
                "symbol": "JPM",
                "company": "JPMorgan Chase & Co.",
                "sector": "Financials",
                "as_of_date": "2026-07-24",
                "horizon_months": 12,
                "prompt_version": "test-v1",
            },
            indent=2,
        )
        + "\n\nCONDITION DATA PACKET\n"
        + json.dumps({"fundamentals": {"value": 1}}, indent=2)
    )
    (run / "prompts" / "JPM_D_CURRENT_WEB_r1.txt").write_text(
        prompt, encoding="utf-8"
    )
    original_payload = assessment("MU", rating="HOLD")
    original_record = {
        "run_id": "original-run",
        "execution_order": 1,
        "assessment_created_at_utc": "2026-07-24T00:00:00+00:00",
        "symbol": "MU",
        "company": "Micron",
        "sector": "Information Technology",
        "condition": "A_MEMORY_ONLY",
        "repetition": 1,
        "rating": "HOLD",
        "rating_score": 0,
        "confidence_0_100": 60,
        "evidence_sufficiency": "MEDIUM",
        "forced_rating_due_to_insufficient_data": False,
        "reason_codes": json.dumps(original_payload["reason_codes"]),
        "thesis": json.dumps(original_payload["thesis"]),
        "risks": json.dumps(original_payload["risks"]),
        "evidence": json.dumps(original_payload["evidence"]),
        "analyst_opinions_encountered": False,
        "analyst_opinions_used": False,
        "analyst_terms_in_final_output": "STALE",
        "analyst_terms_detected_in_web_actions": False,
        "analyst_web_action_matched_terms": "[]",
        "web_search_call_count": 0,
        "web_search_source_count": 0,
        "limitations": json.dumps(original_payload["limitations"]),
        "backend": "OPENAI_RESPONSES_API",
        "model": "test-model",
        "requested_model": "test-model",
        "response_id": "resp_original",
        "prompt_version": "test-v1",
        "prompt_sha256": "prompt",
        "packet_sha256": "packet",
        "web_search_enabled": False,
        "retrieved_source_urls": "[]",
        "input_tokens": 70,
        "output_tokens": 30,
        "total_tokens": 100,
    }
    pd.DataFrame([original_record]).to_csv(
        run / "recommendations.csv", index=False
    )
    (run / "recommendations.jsonl").write_text(
        json.dumps({**original_record, "assessment": original_payload}) + "\n",
        encoding="utf-8",
    )
    (run / "raw_responses" / "MU_A_MEMORY_ONLY_r1.json").write_text(
        json.dumps(
            {
                "usage": {
                    "input_tokens": 70,
                    "output_tokens": 30,
                    "total_tokens": 100,
                },
                "output": [],
            }
        ),
        encoding="utf-8",
    )
    analyst_rows = [
        {
            "symbol": "MU",
            "count_normalized_score": 1.0,
            "count_normalized_rating": "BUY",
            "portal_rating_yahoo": "STRONG_BUY",
        },
        {
            "symbol": "JPM",
            "count_normalized_score": 1.0,
            "count_normalized_rating": "BUY",
            "portal_rating_yahoo": "BUY",
        },
    ]
    pd.DataFrame(analyst_rows).to_csv(
        run / "analyst_consensus.csv", index=False
    )
    original_failure = b'{"error":"ORIGINAL_FAILURE_SENTINEL"}\n'
    failure_path = run / "raw_responses" / "JPM_D_CURRENT_WEB_r1.error.json"
    failure_path.write_bytes(original_failure)
    if known_failure_usage:
        (
            run
            / "raw_responses"
            / "JPM_D_CURRENT_WEB_r1.failed_response.json"
        ).write_text(
            json.dumps(
                {
                    "id": "resp_original_failure",
                    "model": "test-model",
                    "usage": {
                        "input_tokens": 200,
                        "output_tokens": 80,
                        "total_tokens": 280,
                    },
                    "output": [],
                }
            ),
            encoding="utf-8",
        )
    return run, prompt, original_failure


class RetryFailedAssessmentsTests(unittest.TestCase):
    def test_successful_retry_is_append_only_and_consolidated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original, prompt, original_failure = create_original_run(root)
            client = FakeClient(assessment("JPM", rating="BUY"))

            retry_dir = execute_targeted_retry(
                original,
                client,
                max_output_tokens=6000,
                output_root=root / "retry-output",
                retry_id="retry_test",
                started_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
            )

            self.assertEqual(
                client.calls,
                [(prompt, True, "D_CURRENT_WEB")],
            )
            self.assertEqual(
                (
                    original
                    / "raw_responses"
                    / "JPM_D_CURRENT_WEB_r1.error.json"
                ).read_bytes(),
                original_failure,
            )
            self.assertEqual(
                (retry_dir / "prompts" / "JPM_D_CURRENT_WEB_r1.txt").read_text(
                    encoding="utf-8"
                ),
                prompt,
            )
            self.assertTrue(
                (retry_dir / "raw_responses" / "JPM_D_CURRENT_WEB_r1.json").is_file()
            )
            consolidated = pd.read_csv(retry_dir / "recommendations.csv")
            self.assertEqual(len(consolidated), 2)
            retried = consolidated.loc[consolidated["symbol"] == "JPM"].iloc[0]
            self.assertEqual(retried["record_provenance"], "TARGETED_RETRY_SUCCESS")
            self.assertEqual(retried["count_normalized_rating"], "BUY")
            self.assertTrue(bool(retried["exact_match_count_normalized_analyst"]))
            self.assertEqual(
                len(
                    (retry_dir / "recommendations.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ),
                2,
            )
            self.assertEqual(
                len(pd.read_csv(retry_dir / "assessment_errors.csv")), 0
            )
            manifest = json.loads(
                (retry_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["completed_assessments"], 2)
            self.assertEqual(manifest["failed_assessments"], 0)
            self.assertEqual(manifest["unknown_paid_failure_usage_count"], 1)
            self.assertFalse(manifest["original_artifacts_overwritten"])

            summary = build_summary(retry_dir)
            self.assertFalse(summary["estimated_api_cost"]["complete"])
            self.assertIsNone(
                summary["estimated_api_cost"]["exact_estimated_total_cost_usd"]
            )
            self.assertEqual(
                summary["estimated_api_cost"][
                    "unknown_paid_failure_usage_count"
                ],
                1,
            )

        with self.subTest("known original failed usage is included once"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                original, _, _ = create_original_run(
                    root,
                    known_failure_usage=True,
                )
                retry_dir = execute_targeted_retry(
                    original,
                    FakeClient(assessment("JPM", rating="BUY")),
                    output_root=root / "retry-output",
                    retry_id="retry_known_original_failure",
                    started_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
                )
                manifest = json.loads(
                    (retry_dir / "manifest.json").read_text(encoding="utf-8")
                )
                self.assertEqual(
                    manifest["unknown_paid_failure_usage_count"],
                    0,
                )
                self.assertEqual(
                    manifest["original_failed_call_usage"]["total_tokens"],
                    280,
                )
                self.assertEqual(
                    manifest["recorded_total_tokens_including_failures"],
                    530,
                )
                summary = build_summary(retry_dir)
                self.assertTrue(summary["estimated_api_cost"]["complete"])
                self.assertEqual(summary["token_usage"]["input_tokens"], 370)
                self.assertEqual(summary["token_usage"]["output_tokens"], 160)
                self.assertEqual(summary["token_usage"]["total_tokens"], 530)
                self.assertEqual(
                    summary["token_usage"]["web_search_calls"],
                    3,
                )
                self.assertEqual(
                    summary["token_usage"]["known_failed_call_usage"][
                        "source"
                    ],
                    "manifest.failed_call_usage",
                )

    def test_identity_mismatch_remains_an_unresolved_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original, _, _ = create_original_run(root)
            client = FakeClient(
                assessment("JPM", as_of="2026-07-25", rating="BUY")
            )

            retry_dir = execute_targeted_retry(
                original,
                client,
                output_root=root / "retry-output",
                retry_id="retry_bad_identity",
                started_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
            )

            errors = pd.read_csv(retry_dir / "assessment_errors.csv")
            self.assertEqual(len(errors), 1)
            self.assertIn(
                "identity fields do not match",
                str(errors.iloc[0]["error"]),
            )
            consolidated = pd.read_csv(retry_dir / "recommendations.csv")
            self.assertEqual(len(consolidated), 1)
            manifest = json.loads(
                (retry_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["completed_assessments"], 1)
            self.assertEqual(manifest["failed_assessments"], 1)
            self.assertEqual(manifest["unknown_paid_failure_usage_count"], 1)
            self.assertEqual(
                manifest["retry_failed_call_usage"]["calls_with_usage"],
                1,
            )
            self.assertTrue(
                (
                    retry_dir
                    / "raw_responses"
                    / "JPM_D_CURRENT_WEB_r1.failed_response.json"
                ).is_file()
            )

        with self.subTest("AssessmentResponseError payload is preserved"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                original, _, _ = create_original_run(root)
                retry_dir = execute_targeted_retry(
                    original,
                    FailedResponseClient(),
                    output_root=root / "retry-output",
                    retry_id="retry_failed_response",
                    started_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
                )
                failed_raw = (
                    retry_dir
                    / "raw_responses"
                    / "JPM_D_CURRENT_WEB_r1.failed_response.json"
                )
                self.assertEqual(
                    json.loads(failed_raw.read_text())["id"],
                    "resp_failed_retry",
                )
                attempts = pd.read_csv(retry_dir / "retry_attempts.csv")
                self.assertEqual(attempts.iloc[0]["status"], "FAILED")
                self.assertTrue(bool(attempts.iloc[0]["usage_complete"]))
                self.assertEqual(int(attempts.iloc[0]["total_tokens"]), 275)
                self.assertEqual(
                    attempts.iloc[0]["failed_response_raw_artifact"],
                    (
                        "raw_responses/"
                        "JPM_D_CURRENT_WEB_r1.failed_response.json"
                    ),
                )
                errors = pd.read_csv(retry_dir / "assessment_errors.csv")
                self.assertEqual(int(errors.iloc[0]["input_tokens"]), 200)
                self.assertEqual(
                    errors.iloc[0]["response_id"],
                    "resp_failed_retry",
                )
                manifest = json.loads(
                    (retry_dir / "manifest.json").read_text(encoding="utf-8")
                )
                self.assertEqual(
                    manifest["retry_failed_call_usage"]["total_tokens"],
                    275,
                )
                self.assertEqual(
                    manifest["unknown_retry_failed_call_usage_count"],
                    0,
                )
                self.assertEqual(
                    manifest["unknown_paid_failure_usage_count"],
                    1,
                )
                summary = build_summary(retry_dir)
                # The retry error row and manifest describe the same failed
                # response; it must contribute 275 tokens exactly once.
                self.assertEqual(summary["token_usage"]["total_tokens"], 375)
                self.assertEqual(summary["token_usage"]["input_tokens"], 270)
                self.assertEqual(summary["token_usage"]["output_tokens"], 105)
                self.assertEqual(
                    summary["token_usage"]["web_search_calls"],
                    1,
                )


if __name__ == "__main__":
    unittest.main()
