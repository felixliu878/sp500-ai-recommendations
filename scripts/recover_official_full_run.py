#!/usr/bin/env python3
"""Prepare, approve, or execute one audited recovery pass for an official run.

The recovery raises only the per-task attempt ceiling. It imports the run's
frozen runtime, reconstructs every retry from the frozen task registry and
prompt hashes, and leaves the original official plan unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
import time
import types
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_frozen_runtime(run_dir: Path):
    frozen_src = run_dir / "frozen_inputs" / "runtime_source" / "src"
    if not frozen_src.is_dir():
        raise FileNotFoundError(frozen_src)
    frozen_package = frozen_src / "sp500_ai"
    for module_name in list(sys.modules):
        if module_name == "sp500_ai" or module_name.startswith("sp500_ai."):
            del sys.modules[module_name]
    package = types.ModuleType("sp500_ai")
    package.__path__ = [str(frozen_package)]
    package.__package__ = "sp500_ai"
    package.__file__ = str(frozen_package)
    sys.modules["sp500_ai"] = package
    config = importlib.import_module("sp500_ai.config")
    official = importlib.import_module("sp500_ai.official")
    config.load_dotenv(Path(__file__).resolve().parents[1] / ".env")

    # Audited parser-only correction: an attempt suffix follows a complete
    # ten-hex task hash. The original frozen parser could misread hashes that
    # happened to begin with characters such as ``a8``.
    import re

    retry_id = re.compile(r"(.+:[0-9a-f]{10}):a([2-9][0-9]*)")

    def corrected_base_task_id(request_id: str) -> tuple[str, int]:
        match = retry_id.fullmatch(request_id)
        if match:
            return match.group(1), int(match.group(2))
        return request_id, 1

    official._base_task_id = corrected_base_task_id
    return official


def prepare(run_dir: Path, recovery_max_attempts: int) -> Path:
    official = _load_frozen_runtime(run_dir)
    official.preflight_official_run(
        run_dir, require_approval=True, write_report=True
    )
    plan = _read(run_dir / "official_plan.json")
    state = _read(run_dir / "state.json")
    original_max = int(plan["max_attempts_per_task"])
    if recovery_max_attempts != original_max + 1:
        raise ValueError(
            "Recovery must add exactly one attempt beyond the frozen plan"
        )
    failed = [
        {
            "task_id": task_id,
            "attempts_completed": int(task.get("attempts") or 0),
            "last_error": str(task.get("last_error") or ""),
        }
        for task_id, task in state["tasks"].items()
        if task.get("status") == "FAILED"
    ]
    if not failed:
        raise RuntimeError("No failed tasks require recovery")
    condition_counts = Counter(item["task_id"].split(":")[2] for item in failed)
    recovery = {
        "schema_version": "1.0",
        "created_at_utc": _now(),
        "run_id": plan["run_id"],
        "parent_plan_sha256": plan["plan_sha256"],
        "original_max_attempts_per_task": original_max,
        "recovery_max_attempts_per_task": recovery_max_attempts,
        "failed_task_count_at_preparation": len(failed),
        "failed_task_counts_by_condition_code": dict(sorted(condition_counts.items())),
        "maximum_additional_requests": len(failed),
        "request_integrity_rule": (
            "Reconstruct each retry from the frozen task registry, frozen prompt, "
            "model settings, response schema, and endpoint; change only custom_id "
            "by appending the next attempt suffix."
        ),
        "tasks": failed,
    }
    recovery["recovery_plan_sha256"] = _hash(recovery)
    path = run_dir / "RECOVERY_PLAN.json"
    _write(path, recovery)
    approval = {
        "approved": False,
        "run_id": plan["run_id"],
        "recovery_plan_sha256": recovery["recovery_plan_sha256"],
        "approved_by": None,
        "approved_at_utc": None,
        "approval_note": None,
    }
    _write(run_dir / "RECOVERY_APPROVAL.json", approval)
    return path


def approve(run_dir: Path, approved_by: str, note: str) -> Path:
    recovery = _read(run_dir / "RECOVERY_PLAN.json")
    expected = dict(recovery)
    recorded_hash = expected.pop("recovery_plan_sha256")
    if _hash(expected) != recorded_hash:
        raise RuntimeError("Recovery plan hash mismatch")
    approval = {
        "approved": True,
        "run_id": recovery["run_id"],
        "recovery_plan_sha256": recorded_hash,
        "approved_by": approved_by.strip(),
        "approved_at_utc": _now(),
        "approval_note": note.strip(),
    }
    if not approval["approved_by"] or not approval["approval_note"]:
        raise ValueError("approved_by and note are required")
    path = run_dir / "RECOVERY_APPROVAL.json"
    _write(path, approval)
    return path


def _verify_recovery(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    recovery = _read(run_dir / "RECOVERY_PLAN.json")
    approval = _read(run_dir / "RECOVERY_APPROVAL.json")
    unhashed = dict(recovery)
    recorded_hash = unhashed.pop("recovery_plan_sha256")
    if _hash(unhashed) != recorded_hash:
        raise RuntimeError("Recovery plan hash mismatch")
    if not (
        approval.get("approved") is True
        and approval.get("run_id") == recovery.get("run_id")
        and approval.get("recovery_plan_sha256") == recorded_hash
        and str(approval.get("approved_by") or "").strip()
        and str(approval.get("approved_at_utc") or "").strip()
    ):
        raise RuntimeError("Recovery approval is missing or does not match the plan")
    return recovery, approval


def _with_recovery_ceiling(official: Any, ceiling: int):
    original_read = official._read_json

    def read_with_ceiling(path: Path):
        value = original_read(path)
        if Path(path).name == "official_plan.json":
            value = dict(value)
            value["max_attempts_per_task"] = ceiling
        return value

    return original_read, read_with_ceiling


def execute(run_dir: Path, max_cycles: int | None) -> dict[str, Any]:
    recovery, _ = _verify_recovery(run_dir)
    official = _load_frozen_runtime(run_dir)
    official.preflight_official_run(
        run_dir, require_approval=True, write_report=True
    )
    plan = _read(run_dir / "official_plan.json")
    if plan["plan_sha256"] != recovery["parent_plan_sha256"]:
        raise RuntimeError("The frozen official plan no longer matches recovery")
    ceiling = int(recovery["recovery_max_attempts_per_task"])
    original_read, read_with_ceiling = _with_recovery_ceiling(official, ceiling)

    # Create only missing next-attempt requests under the recovery ceiling.
    official._read_json = read_with_ceiling
    created = official.create_retry_shards(run_dir)
    official._read_json = original_read

    # Standard launch performs the original plan/approval preflight and uses
    # the unchanged frozen request settings. Upload and batch creation are
    # idempotent, so an interrupted recovery can resume safely.
    launched = official.launch_official_phase(run_dir, phase="retry")
    cycles = 0
    while True:
        official._read_json = read_with_ceiling
        status = official.sync_official_run(run_dir)
        official._read_json = original_read
        cycles += 1
        if status["status"] == "READY_TO_FINALIZE":
            official.finalize_official_run(run_dir)
            status = official.official_status(run_dir)
            break
        current_state = _read(run_dir / "state.json")
        active_recovery_batches = any(
            shard.get("phase") in {"retry", "smoke_retry"}
            and shard.get("batch_id")
            and shard.get("status") != "INGESTED"
            for shard in current_state.get("shards", {}).values()
        )
        if status["status"] == "PERMANENT_FAILURE" and not active_recovery_batches:
            break
        if max_cycles is not None and cycles >= max_cycles:
            break
        time.sleep(min(60, int(plan["poll_interval_seconds"])))
    return {"created_recovery_shards": created, "launch": launched, **status}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "approve", "execute", "status"])
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--recovery-max-attempts", type=int, default=3)
    parser.add_argument("--approved-by")
    parser.add_argument("--note")
    parser.add_argument("--max-cycles", type=int)
    args = parser.parse_args()
    run_dir = args.run_dir.expanduser().resolve()

    if args.command == "prepare":
        result: Any = {"recovery_plan": str(prepare(run_dir, args.recovery_max_attempts))}
    elif args.command == "approve":
        result = {
            "recovery_approval": str(
                approve(run_dir, args.approved_by or "", args.note or "")
            )
        }
    elif args.command == "execute":
        result = execute(run_dir, args.max_cycles)
    else:
        recovery, approval = _verify_recovery(run_dir)
        state = _read(run_dir / "state.json")
        result = {
            "recovery_plan_sha256": recovery["recovery_plan_sha256"],
            "approved_by": approval["approved_by"],
            "state_status": state["status"],
            "task_counts": dict(
                Counter(str(task["status"]) for task in state["tasks"].values())
            ),
        }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
