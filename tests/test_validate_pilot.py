import csv
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.validate_pilot import (
    _normalized_url,
    _sha256_json,
    validate,
)


CONDITIONS = (
    "A_MEMORY_ONLY",
    "B_PRICE_HISTORY",
    "C_QUANTITATIVE",
    "D_CURRENT_WEB",
)
JSON_FIELDS = {
    "reason_codes",
    "thesis",
    "risks",
    "evidence",
    "limitations",
    "analyst_web_action_matched_terms",
    "retrieved_source_urls",
}
BOOL_FIELDS = {
    "forced_rating_due_to_insufficient_data",
    "analyst_opinions_encountered",
    "analyst_opinions_used",
    "analyst_terms_detected_in_web_actions",
    "web_search_enabled",
}
INT_FIELDS = {
    "execution_order",
    "repetition",
    "rating_score",
    "confidence_0_100",
    "retry_attempt",
    "original_failure_execution_order",
    "web_search_call_count",
    "web_search_source_count",
    "input_tokens",
    "output_tokens",
    "total_tokens",
}


def _packet(condition: str) -> dict:
    if condition == "A_MEMORY_ONLY":
        return {"supplied_information": "company identity only"}
    if condition == "B_PRICE_HISTORY":
        return {
            "price_history_monthly": [
                {
                    "observation_date": "2026-07-23",
                    "adjusted_close": 100.0,
                }
            ]
        }
    return {
        "price_history_monthly": [
            {
                "observation_date": "2026-07-23",
                "adjusted_close": 100.0,
            }
        ],
        "technical_and_return_features": {"return_12m": 0.1},
        "fundamentals": {"observation_date": "2026-06-30"},
        "spy_market_context": {"return_12m": 0.08},
        "sector_context": {"return_12m": 0.09},
    }


def _evidence(condition: str) -> dict:
    if condition == "A_MEMORY_ONLY":
        return {
            "claim": "The company identity was assessed with no current data.",
            "category": "UNCERTAINTY",
            "origin": "MODEL_MEMORY",
            "observation_date": None,
            "publication_date": None,
            "source_url": None,
        }
    if condition == "B_PRICE_HISTORY":
        return {
            "claim": "The supplied price was 100.",
            "category": "PRICE_HISTORY",
            "origin": "SUPPLIED_PRICE",
            "observation_date": "2026-07-23",
            "publication_date": None,
            "source_url": None,
        }
    if condition == "C_QUANTITATIVE":
        return {
            "claim": "The supplied return was ten percent.",
            "category": "FUNDAMENTALS",
            "origin": "SUPPLIED_FUNDAMENTAL",
            "observation_date": "2026-06-30",
            "publication_date": None,
            "source_url": None,
        }
    return {
        "claim": "The issuer published a current release.",
        "category": "CATALYST",
        "origin": "WEB_PRIMARY",
        "observation_date": None,
        "publication_date": "2026-07-24",
        "source_url": "https://example.com/release?id=AAA",
    }


def _assessment(condition: str) -> dict:
    return {
        "symbol": "AAA",
        "as_of": "2026-07-24",
        "horizon_months": 12,
        "rating": "HOLD",
        "confidence_0_100": 60,
        "evidence_sufficiency": "MEDIUM",
        "forced_rating_due_to_insufficient_data": False,
        "reason_codes": ["UNCERTAINTY"],
        "thesis": ["Thesis one", "Thesis two", "Thesis three"],
        "risks": ["Risk one", "Risk two", "Risk three"],
        "evidence": [_evidence(condition)],
        "analyst_opinions_encountered": False,
        "analyst_opinions_used": False,
        "limitations": ["Research classification only"],
    }


def _prompt(condition: str, packet: dict) -> str:
    identity = {
        "symbol": "AAA",
        "company": "Alpha Corp.",
        "sector": "Industrials",
        "as_of_date": "2026-07-24",
        "horizon_months": 12,
        "prompt_version": "test-v1",
    }
    return (
        "Controlled academic prompt.\n\n"
        f"COMPANY IDENTITY\n{json.dumps(identity, indent=2, sort_keys=True)}\n\n"
        f"CONDITION DATA PACKET\n{json.dumps(packet, indent=2, sort_keys=True)}"
    )


def _raw(condition: str, execution_order: int) -> dict:
    output = []
    if condition == "D_CURRENT_WEB":
        output = [
            {
                "type": "web_search_call",
                "action": {
                    "type": "open_page",
                    "sources": [
                        {
                            "type": "url",
                            "url": "https://example.com/release?id=AAA",
                        }
                    ],
                },
            }
        ]
    return {
        "id": f"resp_{execution_order}",
        "model": "test-model",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
        },
        "output": output,
    }


def _outer_value(field: str, value: str):
    if value == "":
        return None
    if field in JSON_FIELDS:
        return json.loads(value)
    if field in BOOL_FIELDS:
        return value == "True"
    if field in INT_FIELDS:
        return int(value)
    return value


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _create_valid_run(run: Path) -> Path:
    (run / "prompts").mkdir(parents=True)
    (run / "raw_responses").mkdir()
    rows: list[dict[str, str]] = []
    jsonl_rows: list[dict] = []
    for execution_order, condition in enumerate(CONDITIONS, start=1):
        packet = _packet(condition)
        prompt = _prompt(condition, packet)
        assessment = _assessment(condition)
        raw = _raw(condition, execution_order)
        stem = f"AAA_{condition}_r1"
        (run / "prompts" / f"{stem}.txt").write_text(
            prompt,
            encoding="utf-8",
        )
        (run / "raw_responses" / f"{stem}.json").write_text(
            json.dumps(raw),
            encoding="utf-8",
        )
        web = condition == "D_CURRENT_WEB"
        row = {
            "run_id": "test-run",
            "original_run_id": "test-run",
            "consolidated_run_id": "",
            "record_provenance": "ORIGINAL_SUCCESS",
            "retry_id": "",
            "retry_attempt": "0",
            "original_failure_execution_order": "",
            "original_failure_error_type": "",
            "retry_max_output_tokens": "",
            "retry_attempted_at_utc": "",
            "execution_order": str(execution_order),
            "assessment_created_at_utc": "2026-07-24T00:00:00+00:00",
            "symbol": "AAA",
            "company": "Alpha Corp.",
            "sector": "Industrials",
            "condition": condition,
            "repetition": "1",
            "rating": "HOLD",
            "rating_score": "0",
            "confidence_0_100": "60",
            "evidence_sufficiency": "MEDIUM",
            "forced_rating_due_to_insufficient_data": "False",
            "reason_codes": json.dumps(assessment["reason_codes"]),
            "thesis": json.dumps(assessment["thesis"]),
            "risks": json.dumps(assessment["risks"]),
            "evidence": json.dumps(assessment["evidence"]),
            "analyst_opinions_encountered": "False",
            "analyst_opinions_used": "False",
            "analyst_terms_in_final_output": "NONE",
            "analyst_terms_detected_in_web_actions": "False",
            "analyst_web_action_matched_terms": "[]",
            "web_search_call_count": "1" if web else "0",
            "web_search_source_count": "1" if web else "0",
            "limitations": json.dumps(assessment["limitations"]),
            "backend": "OPENAI_RESPONSES_API",
            "model": "test-model",
            "requested_model": "test-model",
            "response_id": f"resp_{execution_order}",
            "prompt_version": "test-v1",
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "packet_sha256": _sha256_json(packet),
            "web_search_enabled": "True" if web else "False",
            "retrieved_source_urls": json.dumps(
                ["https://example.com/release?id=AAA"] if web else []
            ),
            "input_tokens": "100",
            "output_tokens": "50",
            "total_tokens": "150",
        }
        rows.append(row)
        outer = {field: _outer_value(field, value) for field, value in row.items()}
        outer["assessment"] = assessment
        jsonl_rows.append(outer)

    _write_csv(run / "recommendations.csv", rows)
    (run / "recommendations.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in jsonl_rows),
        encoding="utf-8",
    )
    (run / "assessment_errors.csv").write_text(
        "run_id,execution_order,symbol,condition,repetition,error,failed_at_utc\n",
        encoding="utf-8",
    )
    (run / "acquisition_gate.json").write_text(
        json.dumps({"status": "PASS", "strict_for_this_run": True, "issues": []}),
        encoding="utf-8",
    )
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "test-run",
                "backend": "openai",
                "model": "test-model",
                "as_of_date": "2026-07-24",
                "horizon_months": 12,
                "symbols": ["AAA"],
                "conditions": list(CONDITIONS),
                "repetitions": 1,
                "planned_assessments": 4,
                "completed_assessments": 4,
                "failed_assessments": 0,
                "not_attempted_assessments": 0,
                "unknown_paid_failure_usage_count": 0,
            }
        ),
        encoding="utf-8",
    )
    return run


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _replace_csv_and_outer(
    run: Path,
    condition: str,
    field: str,
    csv_value: str,
) -> None:
    rows = _read_rows(run / "recommendations.csv")
    for row in rows:
        if row["condition"] == condition:
            row[field] = csv_value
    _write_csv(run / "recommendations.csv", rows)
    jsonl_rows = [
        json.loads(line)
        for line in (run / "recommendations.jsonl").read_text().splitlines()
        if line
    ]
    for row in jsonl_rows:
        if row["condition"] == condition:
            row[field] = _outer_value(field, csv_value)
    (run / "recommendations.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in jsonl_rows),
        encoding="utf-8",
    )


def _create_valid_retry(root: Path) -> tuple[Path, Path]:
    source = _create_valid_run(root / "original")
    consolidated_csv = (source / "recommendations.csv").read_bytes()
    consolidated_jsonl = (source / "recommendations.jsonl").read_bytes()
    d_prompt = (
        source / "prompts" / "AAA_D_CURRENT_WEB_r1.txt"
    ).read_bytes()
    d_raw = (
        source / "raw_responses" / "AAA_D_CURRENT_WEB_r1.json"
    ).read_bytes()

    source_rows = [
        row
        for row in _read_rows(source / "recommendations.csv")
        if row["condition"] != "D_CURRENT_WEB"
    ]
    _write_csv(source / "recommendations.csv", source_rows)
    source_jsonl = [
        json.loads(line)
        for line in (source / "recommendations.jsonl").read_text().splitlines()
        if line
    ]
    (source / "recommendations.jsonl").write_text(
        "".join(
            json.dumps(row, sort_keys=True) + "\n"
            for row in source_jsonl
            if row["condition"] != "D_CURRENT_WEB"
        ),
        encoding="utf-8",
    )
    (source / "assessment_errors.csv").write_text(
        "run_id,execution_order,symbol,condition,repetition,error,failed_at_utc\n"
        "test-run,4,AAA,D_CURRENT_WEB,1,JSONDecodeError,2026-07-24T00:00:00+00:00\n",
        encoding="utf-8",
    )
    source_manifest = json.loads((source / "manifest.json").read_text())
    source_manifest.update(
        {
            "completed_assessments": 3,
            "failed_assessments": 1,
        }
    )
    (source / "manifest.json").write_text(
        json.dumps(source_manifest),
        encoding="utf-8",
    )

    retry = source / "retries" / "retry_test"
    (retry / "prompts").mkdir(parents=True)
    (retry / "raw_responses").mkdir()
    (retry / "recommendations.csv").write_bytes(consolidated_csv)
    (retry / "recommendations.jsonl").write_bytes(consolidated_jsonl)
    shutil.copy2(source / "acquisition_gate.json", retry / "acquisition_gate.json")
    (retry / "assessment_errors.csv").write_text(
        "run_id,execution_order,symbol,condition,repetition,error,failed_at_utc\n",
        encoding="utf-8",
    )
    (retry / "prompts" / "AAA_D_CURRENT_WEB_r1.txt").write_bytes(d_prompt)
    (retry / "raw_responses" / "AAA_D_CURRENT_WEB_r1.json").write_bytes(d_raw)

    rows = _read_rows(retry / "recommendations.csv")
    for row in rows:
        row["run_id"] = "test-run__retry"
        row["consolidated_run_id"] = "test-run__retry"
        row["retry_id"] = "retry_test"
        if row["condition"] == "D_CURRENT_WEB":
            row["record_provenance"] = "TARGETED_RETRY_SUCCESS"
            row["retry_attempt"] = "1"
            row["original_failure_execution_order"] = "4"
            row["original_failure_error_type"] = "JSONDecodeError"
    _write_csv(retry / "recommendations.csv", rows)
    jsonl_rows = [
        json.loads(line)
        for line in (retry / "recommendations.jsonl").read_text().splitlines()
        if line
    ]
    for outer in jsonl_rows:
        row = next(
            item
            for item in rows
            if item["condition"] == outer["condition"]
        )
        for field, value in row.items():
            outer[field] = _outer_value(field, value)
    (retry / "recommendations.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in jsonl_rows),
        encoding="utf-8",
    )

    source_hashes = {
        name: hashlib.sha256((source / name).read_bytes()).hexdigest()
        for name in (
            "manifest.json",
            "assessment_errors.csv",
            "recommendations.csv",
            "recommendations.jsonl",
        )
    }
    prompt_hash = hashlib.sha256(d_prompt).hexdigest()
    consolidated_manifest = {
        **source_manifest,
        "run_id": "test-run__retry",
        "original_run_id": "test-run",
        "retry_id": "retry_test",
        "completed_assessments": 4,
        "failed_assessments": 0,
        "not_attempted_assessments": 0,
        "targeted_retry_requested": 1,
        "targeted_retry_succeeded": 1,
        "targeted_retry_failed": 0,
        "original_artifacts_overwritten": False,
        "source_artifact_sha256": source_hashes,
        "retried_prompt_sha256": {
            "AAA_D_CURRENT_WEB_r1": prompt_hash,
        },
        "unknown_paid_failure_usage_count": 1,
    }
    (retry / "manifest.json").write_text(
        json.dumps(consolidated_manifest),
        encoding="utf-8",
    )
    (retry / "retry_manifest.json").write_text(
        json.dumps(
            {
                "retry_id": "retry_test",
                "consolidated_run_id": "test-run__retry",
                "original_run_id": "test-run",
                "original_run_directory": "original",
                "successful_cells": 1,
                "failed_cells": 0,
                "original_artifacts_overwritten": False,
                "requested_cells": [
                    {
                        "symbol": "AAA",
                        "condition": "D_CURRENT_WEB",
                        "repetition": 1,
                        "original_execution_order": 4,
                        "original_error_type": "JSONDecodeError",
                        "prompt_sha256": prompt_hash,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (retry / "retry_attempts.csv").write_text(
        "symbol,condition,repetition,status\n"
        "AAA,D_CURRENT_WEB,1,SUCCESS\n",
        encoding="utf-8",
    )
    return retry, source


class ValidatePilotTests(unittest.TestCase):
    def test_validator_accepts_valid_and_rejects_integrity_mutations(self):
        self.assertEqual(
            _normalized_url("https://Example.com/a%20b/?utm_source=test"),
            _normalized_url("https://example.com/a b"),
        )
        self.assertEqual(
            _normalized_url("https://example.com/report?_sp=tracking"),
            _normalized_url("https://example.com/report"),
        )
        self.assertNotEqual(
            _normalized_url("https://example.com/report?id=A"),
            _normalized_url("https://example.com/report?id=B"),
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = _create_valid_run(root / "valid")
            self.assertEqual(validate(valid)["status"], "PASS")

            missing_universe = _create_valid_run(root / "missing_universe")
            manifest = json.loads(
                (missing_universe / "manifest.json").read_text()
            )
            manifest["symbols"].append("BBB")
            (missing_universe / "manifest.json").write_text(json.dumps(manifest))

            invalid_schema = _create_valid_run(root / "invalid_schema")
            jsonl = [
                json.loads(line)
                for line in (invalid_schema / "recommendations.jsonl")
                .read_text()
                .splitlines()
            ]
            jsonl[0]["assessment"]["rating"] = "NOT_A_RATING"
            (invalid_schema / "recommendations.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in jsonl)
            )

            invalid_raw = _create_valid_run(root / "invalid_raw")
            (
                invalid_raw
                / "raw_responses"
                / "AAA_D_CURRENT_WEB_r1.json"
            ).write_text("{}")

            invalid_packet_hash = _create_valid_run(root / "invalid_packet_hash")
            _replace_csv_and_outer(
                invalid_packet_hash,
                "C_QUANTITATIVE",
                "packet_sha256",
                "0" * 64,
            )

            invalid_origin = _create_valid_run(root / "invalid_origin")
            web_evidence = {
                **_evidence("D_CURRENT_WEB"),
                "source_url": "https://example.com/release?id=AAA",
            }
            _replace_csv_and_outer(
                invalid_origin,
                "B_PRICE_HISTORY",
                "evidence",
                json.dumps([web_evidence]),
            )
            invalid_origin_jsonl = [
                json.loads(line)
                for line in (invalid_origin / "recommendations.jsonl")
                .read_text()
                .splitlines()
            ]
            for row in invalid_origin_jsonl:
                if row["condition"] == "B_PRICE_HISTORY":
                    row["assessment"]["evidence"] = [web_evidence]
            (invalid_origin / "recommendations.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in invalid_origin_jsonl)
            )

            malformed = _create_valid_run(root / "malformed")
            (malformed / "recommendations.jsonl").write_text("{not-json}\n")

            cases = (
                (missing_universe, "manifest universe"),
                (invalid_schema, "Invalid assessment schema"),
                (invalid_raw, "Raw-response/CSV metadata"),
                (invalid_packet_hash, "Packet hash mismatches"),
                (invalid_origin, "not allowed"),
                (malformed, "Artifact parsing failed"),
            )
            for run, expected_fragment in cases:
                with self.subTest(expected_fragment=expected_fragment):
                    result = validate(run)
                    self.assertEqual(result["status"], "FAIL")
                    self.assertTrue(
                        any(
                            expected_fragment in failure
                            for failure in result["hard_failures"]
                        ),
                        result["hard_failures"],
                    )

            retry, source = _create_valid_retry(root / "retry_case")
            retry_result = validate(retry)
            self.assertEqual(retry_result["status"], "PASS_WITH_WARNINGS")
            self.assertEqual(retry_result["hard_failures"], [])
            source_manifest = json.loads((source / "manifest.json").read_text())
            source_manifest["model"] = "tampered-model"
            (source / "manifest.json").write_text(json.dumps(source_manifest))
            tampered_retry = validate(retry)
            self.assertEqual(tampered_retry["status"], "FAIL")
            self.assertTrue(
                any(
                    "Source artifact hash mismatch" in failure
                    for failure in tampered_retry["hard_failures"]
                )
            )


if __name__ == "__main__":
    unittest.main()
