from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from sp500_ai.official import (
    _batch_error_payload,
    _base_task_id,
    create_retry_shards,
    finalize_official_run,
    launch_official_phase,
    official_status,
    preflight_official_run,
    prepare_official_run,
    record_approval,
    sync_official_run,
)
from sp500_ai.progress import build_progress_snapshot, render_progress_dashboard


CONDITIONS = [
    "A_MEMORY_ONLY",
    "B_PRICE_HISTORY",
    "C_QUANTITATIVE",
    "D_CURRENT_WEB",
]


class TaskIdParsingTests(unittest.TestCase):
    def test_base_hash_that_begins_with_attempt_like_text_is_not_a_retry(self) -> None:
        request_id = "t:EXPE:B:r1:a836614925"
        self.assertEqual(_base_task_id(request_id), (request_id, 1))

    def test_appended_attempt_suffix_is_parsed(self) -> None:
        base_id = "t:HAS:A:r3:03b4af50f4"
        self.assertEqual(_base_task_id(f"{base_id}:a2"), (base_id, 2))

    def test_nested_responses_batch_error_is_preserved(self) -> None:
        expected = {"code": "credit_balance_exhausted", "message": "no credits"}
        line = {"error": None, "response": {"body": {"error": expected}}}
        self.assertEqual(_batch_error_payload(line), expected)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _assessment(symbol: str, as_of: str, horizon: int) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "as_of": as_of,
        "horizon_months": horizon,
        "rating": "HOLD",
        "confidence_0_100": 55,
        "evidence_sufficiency": "MEDIUM",
        "forced_rating_due_to_insufficient_data": False,
        "reason_codes": ["UNCERTAINTY"],
        "thesis": ["thesis one", "thesis two", "thesis three"],
        "risks": ["risk one", "risk two", "risk three"],
        "evidence": [],
        "analyst_opinions_encountered": False,
        "analyst_opinions_used": False,
        "limitations": [],
    }


class FakeBatchTransport:
    def __init__(
        self,
        *,
        as_of: str,
        horizon: int,
        fail_task_once: str | None = None,
    ) -> None:
        self.as_of = as_of
        self.horizon = horizon
        self.fail_task_once = fail_task_once
        self.files: dict[str, dict[str, Any]] = {}
        self.batches: dict[str, dict[str, Any]] = {}
        self.upload_calls = 0
        self.create_calls = 0
        self.download_calls = 0
        self.fail_after_upload = False
        self.fail_after_create = False
        self.fail_next_download = False
        self.truncate_next_download = False

    def find_files(self, filename: str, size_bytes: int) -> list[dict[str, Any]]:
        return [
            {key: value for key, value in item.items() if key != "content"}
            for item in self.files.values()
            if item["filename"] == filename and item["bytes"] == size_bytes
        ]

    def upload_file(self, path: Path) -> dict[str, Any]:
        self.upload_calls += 1
        content = path.read_bytes()
        file_id = f"file-{len(self.files) + 1}"
        item = {
            "id": file_id,
            "filename": path.name,
            "bytes": len(content),
            "created_at": len(self.files) + 1,
            "content": content,
        }
        self.files[file_id] = item
        if self.fail_after_upload:
            self.fail_after_upload = False
            raise TimeoutError("simulated timeout after server accepted upload")
        return {key: value for key, value in item.items() if key != "content"}

    def find_batches(
        self, *, plan_sha256: str, shard_id: str, input_file_id: str
    ) -> list[dict[str, Any]]:
        return [
            item
            for item in self.batches.values()
            if item["input_file_id"] == input_file_id
            and item["metadata"]["plan_sha256"] == plan_sha256
            and item["metadata"]["shard_id"] == shard_id
        ]

    def create_batch(
        self,
        *,
        input_file_id: str,
        endpoint: str,
        completion_window: str,
        metadata: dict[str, str],
    ) -> dict[str, Any]:
        self.create_calls += 1
        requests = [
            json.loads(line)
            for line in self.files[input_file_id]["content"].decode().splitlines()
            if line.strip()
        ]
        output_rows = []
        error_rows = []
        for request in reversed(requests):
            request_id = request["custom_id"]
            base_task_id = request_id.rsplit(":a", 1)[0] if ":a" in request_id else request_id
            if self.fail_task_once == base_task_id and ":a" not in request_id:
                error_rows.append(
                    {
                        "id": f"batch_req_{request_id}",
                        "custom_id": request_id,
                        "response": None,
                        "error": {"code": "test_error", "message": "retry me"},
                    }
                )
                continue
            symbol = request_id.split(":")[1]
            body = {
                "id": f"resp-{request_id}",
                "model": request["body"]["model"],
                "created_at": 1_785_456_000,
                "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    _assessment(symbol, self.as_of, self.horizon)
                                ),
                            }
                        ],
                    }
                ],
            }
            output_rows.append(
                {
                    "id": f"batch_req_{request_id}",
                    "custom_id": request_id,
                    "response": {
                        "status_code": 200,
                        "request_id": f"req-{request_id}",
                        "body": body,
                    },
                    "error": None,
                }
            )

        output_file_id = None
        if output_rows:
            output_file_id = f"output-{len(self.files) + 1}"
            content = b"".join(
                json.dumps(row, sort_keys=True).encode() + b"\n" for row in output_rows
            )
            self.files[output_file_id] = {
                "id": output_file_id,
                "filename": f"{metadata['shard_id']}.output.jsonl",
                "bytes": len(content),
                "created_at": len(self.files) + 1,
                "content": content,
            }
        error_file_id = None
        if error_rows:
            error_file_id = f"error-{len(self.files) + 1}"
            content = b"".join(
                json.dumps(row, sort_keys=True).encode() + b"\n" for row in error_rows
            )
            self.files[error_file_id] = {
                "id": error_file_id,
                "filename": f"{metadata['shard_id']}.error.jsonl",
                "bytes": len(content),
                "created_at": len(self.files) + 1,
                "content": content,
            }
        batch_id = f"batch-{len(self.batches) + 1}"
        batch = {
            "id": batch_id,
            "input_file_id": input_file_id,
            "endpoint": endpoint,
            "completion_window": completion_window,
            "metadata": metadata,
            "status": "completed",
            "output_file_id": output_file_id,
            "error_file_id": error_file_id,
            "created_at": len(self.batches) + 1,
        }
        self.batches[batch_id] = batch
        if self.fail_after_create:
            self.fail_after_create = False
            raise TimeoutError("simulated timeout after server accepted batch")
        return batch

    def retrieve_batch(self, batch_id: str) -> dict[str, Any]:
        return self.batches[batch_id]

    def retrieve_file(self, file_id: str) -> dict[str, Any]:
        return {key: value for key, value in self.files[file_id].items() if key != "content"}

    def download_file(self, file_id: str) -> bytes:
        self.download_calls += 1
        if self.fail_next_download:
            self.fail_next_download = False
            raise ConnectionError("simulated interrupted download")
        content = self.files[file_id]["content"]
        if self.truncate_next_download:
            self.truncate_next_download = False
            return content[:-1]
        return content


class OfficialRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.as_of = "2026-07-31"
        self.horizon = 18
        self.audit = self.root / "audit"
        self.eligible = self.root / "eligible"
        self.audit.mkdir()
        self.eligible.mkdir()
        self.protocol_path = self.root / "protocol.json"
        self.experiment_path = self.root / "experiment.json"
        self._build_sources()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _build_sources(self) -> None:
        symbols = ["MU", "AAPL"]
        audit_symbols = symbols + ["BAD"]
        experiment = {
            "as_of_date": self.as_of,
            "horizon_months": self.horizon,
            "price_history_frequency": "monthly",
            "price_history_observations": 12,
            "model": "gpt-test-snapshot",
            "reasoning_effort": "low",
            "max_output_tokens": 800,
            "prompt_version": "test-v1",
            "full_repetitions": 3,
            "conditions": CONDITIONS,
        }
        protocol = {
            "framework_version": "openai-batch-v1.0",
            "transport": "openai_batch_api",
            "endpoint": "/v1/responses",
            "completion_window": "24h",
            "repetitions": 3,
            "shard_size": 10,
            "max_active_batches": 8,
            "max_attempts_per_task": 2,
            "transfer_retry_limit": 1,
            "retry_backoff_seconds": 0,
            "poll_interval_seconds": 1,
            "store_responses": False,
            "require_smoke_batch": True,
            "smoke_symbol": "MU",
            "universe_policy": {
                "exclude_model_ineligible_securities": True,
                "retain_securities_with_missing_analyst_baselines": True,
                "require_documented_sector_override": True,
            },
            "approval_policy": {
                "require_matching_plan_sha256": True,
                "approval_filename": "FULL_RUN_APPROVAL.json",
                "allow_automatic_exact_prompt_retry": True,
            },
        }
        _write_json(self.experiment_path, experiment)
        _write_json(self.protocol_path, protocol)

        manifest_common = {
            "scope": "full",
            "backend": "dry-run",
            "as_of_date": self.as_of,
            "prompt_version": "test-v1",
            "price_history_frequency": "monthly",
            "price_history_observations": 12,
            "conditions": CONDITIONS,
            "repetitions": 1,
            "failed_assessments": 0,
            "not_attempted_assessments": 0,
        }
        _write_json(
            self.audit / "manifest.json",
            {**manifest_common, "symbols": audit_symbols, "completed_assessments": 12},
        )
        _write_json(
            self.eligible / "manifest.json",
            {**manifest_common, "symbols": symbols, "completed_assessments": 8},
        )
        _write_json(
            self.audit / "acquisition_gate.json",
            {
                "status": "FAIL",
                "security_count": 3,
                "issues": [{"symbol": "BAD", "issue": "insufficient prices"}],
            },
        )
        _write_json(
            self.eligible / "acquisition_gate.json",
            {"status": "PASS", "security_count": 2, "issues": []},
        )

        audit_universe = pd.DataFrame(
            {
                "symbol": audit_symbols,
                "company": ["Micron", "Apple", "Bad Company"],
                "sector": ["Information Technology"] * 3,
            }
        )
        eligible_universe = audit_universe.iloc[:2].copy()
        audit_universe.to_csv(self.audit / "experiment_universe.csv", index=False)
        eligible_universe.to_csv(self.eligible / "experiment_universe.csv", index=False)
        pd.DataFrame({"symbol": audit_symbols}).to_csv(
            self.audit / "data_quality.csv", index=False
        )
        pd.DataFrame({"symbol": symbols}).to_csv(
            self.eligible / "data_quality.csv", index=False
        )
        pd.DataFrame({"context": ["benchmark:SPY"]}).to_csv(
            self.eligible / "context_data_quality.csv", index=False
        )
        pd.DataFrame({"symbol": symbols, "return_1y": [0.1, 0.2]}).to_csv(
            self.eligible / "features.csv", index=False
        )
        pd.DataFrame(
            {
                "symbol": symbols,
                "count_normalized_score": [1.0, 1.0],
                "count_normalized_rating": ["BUY", "BUY"],
                "portal_rating_yahoo": ["BUY", "BUY"],
            }
        ).to_csv(self.eligible / "analyst_consensus.csv", index=False)

        prompt_dir = self.eligible / "prompts"
        prompt_dir.mkdir()
        recommendation_rows = []
        for symbol in symbols:
            for condition in CONDITIONS:
                prompt = f"Frozen prompt for {symbol} under {condition}.\n".encode()
                (prompt_dir / f"{symbol}_{condition}_r1.txt").write_bytes(prompt)
                recommendation_rows.append(
                    {
                        "symbol": symbol,
                        "condition": condition,
                        "prompt_sha256": _sha(prompt),
                        "packet_sha256": _sha(f"{symbol}:{condition}".encode()),
                    }
                )
        pd.DataFrame(recommendation_rows).to_csv(
            self.eligible / "recommendations.csv", index=False
        )

    def _prepare(self) -> Path:
        return prepare_official_run(
            audit_run=self.audit,
            eligible_run=self.eligible,
            protocol_path=self.protocol_path,
            experiment_config_path=self.experiment_path,
            output_root=self.root / "official_runs",
        )

    def test_prepare_freezes_exact_plan_and_requires_matching_approval(self) -> None:
        run_dir = self._prepare()
        plan = json.loads((run_dir / "official_plan.json").read_text())
        self.assertEqual(plan["planned_assessments"], 24)
        self.assertEqual(plan["smoke_assessments"], 4)
        self.assertEqual(plan["full_assessments"], 20)
        self.assertEqual(plan["horizon_months"], self.horizon)
        self.assertEqual(len(plan["shards"]), 5)
        progress = build_progress_snapshot(run_dir)
        self.assertEqual(progress["verified_completion_percent"], 0.0)
        self.assertEqual(progress["accepted_assessments"], 0)
        self.assertTrue((run_dir / "progress" / "index.html").is_file())
        preflight_official_run(run_dir, require_approval=False)
        with self.assertRaisesRegex(RuntimeError, "explicit approval"):
            preflight_official_run(run_dir, require_approval=True)
        record_approval(run_dir, approved_by="Professor", note="Approved test plan")
        report = preflight_official_run(run_dir, require_approval=True)
        self.assertEqual(report["approval_status"], "APPROVED")

    def test_preflight_detects_frozen_prompt_tampering(self) -> None:
        run_dir = self._prepare()
        prompt_path = next((run_dir / "prompts").glob("*.txt"))
        prompt_path.write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "frozen file hash mismatch"):
            preflight_official_run(run_dir, require_approval=False)

    def test_progress_reports_blocked_when_only_failures_remain(self) -> None:
        run_dir = self._prepare()
        state_path = run_dir / "state.json"
        state = json.loads(state_path.read_text())
        task_ids = list(state["tasks"])
        for task_id in task_ids[:-1]:
            state["tasks"][task_id]["status"] = "ACCEPTED"
        state["tasks"][task_ids[-1]]["status"] = "FAILED"
        state["status"] = "PERMANENT_FAILURE"
        _write_json(state_path, state)
        progress = build_progress_snapshot(run_dir)
        self.assertEqual(progress["pending_assessments"], 0)
        self.assertEqual(progress["estimated_remaining"], "Blocked")
        self.assertIn("unresolved failures", progress["eta_method"])

    def test_progress_counts_submitted_retry_as_in_flight(self) -> None:
        run_dir = self._prepare()
        state_path = run_dir / "state.json"
        state = json.loads(state_path.read_text())
        task_ids = list(state["tasks"])
        for task_id in task_ids[:-1]:
            state["tasks"][task_id]["status"] = "ACCEPTED"
        failed_id = task_ids[-1]
        state["tasks"][failed_id]["status"] = "FAILED"
        state["shards"]["retry:test:001:a2"] = {
            "phase": "retry",
            "status": "SUBMITTED",
            "batch_id": "batch_test",
            "batch_status": "in_progress",
            "task_ids": [failed_id],
            "request_count": 1,
            "submitted_at_utc": "2026-08-07T00:00:00+00:00",
            "batch_snapshot": {
                "request_counts": {"completed": 0, "failed": 0, "total": 1}
            },
        }
        state["status"] = "RUNNING"
        _write_json(state_path, state)
        progress = build_progress_snapshot(
            run_dir,
            observed_at=datetime(2026, 8, 7, 0, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(progress["pending_assessments"], 1)
        self.assertEqual(progress["retry_assessments_in_flight"], 1)
        self.assertEqual(progress["failed_waiting_retry"], 0)
        self.assertNotEqual(progress["estimated_remaining"], "Blocked")

    def test_progress_uses_attempt_limited_completion_label(self) -> None:
        run_dir = self._prepare()
        state_path = run_dir / "state.json"
        state = json.loads(state_path.read_text())
        task_ids = list(state["tasks"])
        for task_id in task_ids[:-1]:
            state["tasks"][task_id]["status"] = "ACCEPTED"
        failed_id = task_ids[-1]
        state["tasks"][failed_id]["status"] = "FAILED"
        state["tasks"][failed_id]["attempts"] = 2
        state["status"] = "PERMANENT_FAILURE"
        _write_json(state_path, state)

        plan = json.loads((run_dir / "official_plan.json").read_text())
        _write_json(
            run_dir / "RECOVERY_PLAN.json",
            {
                "parent_plan_sha256": plan["plan_sha256"],
                "recovery_max_attempts_per_task": 2,
            },
        )

        progress = build_progress_snapshot(run_dir)
        self.assertEqual(progress["run_status"], "PERMANENT_FAILURE")
        self.assertEqual(progress["display_status"], "COMPLETE_AT_ATTEMPT_LIMIT")
        self.assertEqual(progress["estimated_remaining"], "Attempt limit reached")

    def test_resume_upload_batch_download_retry_and_finalize(self) -> None:
        run_dir = self._prepare()
        record_approval(run_dir, approved_by="Professor", note="Approved test plan")
        plan = json.loads((run_dir / "official_plan.json").read_text())
        smoke_task = next(
            task
            for task in [json.loads(line) for line in (run_dir / plan["task_registry"]).read_text().splitlines()]
            if task["phase"] == "smoke"
        )
        transport = FakeBatchTransport(
            as_of=self.as_of,
            horizon=self.horizon,
            fail_task_once=smoke_task["task_id"],
        )

        transport.fail_after_upload = True
        with self.assertRaises(TimeoutError):
            launch_official_phase(run_dir, phase="smoke", transport=transport)
        transport.fail_after_create = True
        with self.assertRaises(TimeoutError):
            launch_official_phase(run_dir, phase="smoke", transport=transport)
        launch_official_phase(run_dir, phase="smoke", transport=transport)
        self.assertEqual(transport.upload_calls, 1)
        self.assertEqual(transport.create_calls, 1)

        transport.fail_next_download = True
        with self.assertRaises(ConnectionError):
            sync_official_run(run_dir, transport=transport)
        self.assertEqual(list((run_dir / "downloads").glob("*.jsonl")), [])
        transport.truncate_next_download = True
        with self.assertRaisesRegex(OSError, "file size mismatch"):
            sync_official_run(run_dir, transport=transport)
        self.assertEqual(list((run_dir / "downloads").glob("*.jsonl")), [])
        status = sync_official_run(run_dir, transport=transport)
        self.assertEqual(status["task_counts"], {"ACCEPTED": 3, "FAILED": 1, "PENDING": 20})

        retry_shards = create_retry_shards(run_dir)
        self.assertEqual(len(retry_shards), 1)
        state = json.loads((run_dir / "state.json").read_text())
        original_shard = next(
            shard for shard in state["shards"].values() if shard["phase"] == "smoke"
        )
        retry_shard = state["shards"][retry_shards[0]]
        original_requests = {
            row["custom_id"]: row
            for row in [
                json.loads(line)
                for line in (run_dir / original_shard["input_path"]).read_text().splitlines()
            ]
        }
        retry_request = json.loads((run_dir / retry_shard["input_path"]).read_text())
        self.assertEqual(
            retry_request["body"], original_requests[smoke_task["task_id"]]["body"]
        )

        launch_official_phase(run_dir, phase="smoke_retry", transport=transport)
        status = sync_official_run(run_dir, transport=transport)
        self.assertEqual(status["smoke_accepted"], 4)
        self.assertEqual(json.loads((run_dir / "smoke_gate.json").read_text())["status"], "PASS")

        launched = launch_official_phase(run_dir, phase="full", transport=transport)
        self.assertEqual(len(launched["launched"]), 4)
        status = sync_official_run(run_dir, transport=transport)
        self.assertEqual(status["status"], "READY_TO_FINALIZE")
        result_dir = finalize_official_run(run_dir)
        recommendations = pd.read_csv(result_dir / "recommendations.csv")
        self.assertEqual(len(recommendations), 24)
        self.assertEqual(set(recommendations["symbol"]), {"MU", "AAPL"})
        self.assertEqual(official_status(run_dir)["status"], "COMPLETE")
        progress = render_progress_dashboard(run_dir)
        self.assertEqual(progress["verified_completion_percent"], 100.0)
        self.assertEqual(progress["estimated_remaining"], "0s")
        manifest = json.loads((result_dir / "manifest.json").read_text())
        self.assertEqual(manifest["task_attempt_counts"], {"one_attempt": 23, "two_attempts": 1})


if __name__ == "__main__":
    unittest.main()
