from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

import pandas as pd

from .analysis import RATING_SCORE, attach_analyst_comparison, condition_flip_summary
from .config import PROJECT_ROOT, load_config
from .llm import ASSESSMENT_SCHEMA, _audit_web_actions, _extract_urls, validate_assessment
from .pipeline import _analyst_exposure_audit, _json_cell, run_experiment
from .progress import render_progress_dashboard


OFFICIAL_FRAMEWORK_VERSION = "openai-batch-v1.0"
OFFICIAL_PLAN_SCHEMA_VERSION = "official-plan-v1"
OFFICIAL_STATE_SCHEMA_VERSION = "official-state-v1"
BATCH_MAX_REQUESTS = 50_000
BATCH_MAX_INPUT_BYTES = 200 * 1024 * 1024
OFFICIAL_RUNTIME_FILES = (
    "pyproject.toml",
    "requirements.txt",
    "scripts/official_full_run.py",
    "src/sp500_ai/analysis.py",
    "src/sp500_ai/config.py",
    "src/sp500_ai/features.py",
    "src/sp500_ai/llm.py",
    "src/sp500_ai/official.py",
    "src/sp500_ai/pipeline.py",
    "src/sp500_ai/prompts.py",
    "src/sp500_ai/progress.py",
    "src/sp500_ai/providers.py",
)
TERMINAL_BATCH_STATUSES = {"completed", "failed", "expired", "cancelled"}
ACTIVE_BATCH_STATUSES = {"validating", "in_progress", "finalizing", "cancelling"}
SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")


class BatchTransport(Protocol):
    def find_files(self, filename: str, size_bytes: int) -> list[dict[str, Any]]: ...

    def upload_file(self, path: Path) -> dict[str, Any]: ...

    def find_batches(
        self, *, plan_sha256: str, shard_id: str, input_file_id: str
    ) -> list[dict[str, Any]]: ...

    def create_batch(
        self,
        *,
        input_file_id: str,
        endpoint: str,
        completion_window: str,
        metadata: dict[str, str],
    ) -> dict[str, Any]: ...

    def retrieve_batch(self, batch_id: str) -> dict[str, Any]: ...

    def retrieve_file(self, file_id: str) -> dict[str, Any]: ...

    def download_file(self, file_id: str) -> bytes: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _object_sha256(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value))


def _atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_json(path: Path, value: Any) -> None:
    _atomic_write_bytes(
        path,
        json.dumps(
            value, indent=2, sort_keys=True, ensure_ascii=False, default=str
        ).encode("utf-8")
        + b"\n",
    )


def _atomic_write_frame(path: Path, frame: pd.DataFrame) -> None:
    _atomic_write_bytes(path, frame.to_csv(index=False).encode("utf-8"))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError(f"Expected object at {path}:{line_number}")
            rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = b"".join(_canonical_json(row) + b"\n" for row in rows)
    _atomic_write_bytes(path, payload)


def _relative_inventory(root: Path, paths: list[Path]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in paths:
        relative = path.relative_to(root).as_posix()
        result[relative] = {
            "sha256": _sha256_path(path),
            "size_bytes": path.stat().st_size,
        }
    return dict(sorted(result.items()))


def _runtime_environment() -> dict[str, Any]:
    packages: dict[str, str | None] = {}
    for name in ("numpy", "openai", "openpyxl", "pandas", "yfinance"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "python_version": sys.version,
        "python_executable_name": Path(sys.executable).name,
        "platform": platform.platform(),
        "packages": packages,
    }


@contextmanager
def _run_lock(run_dir: Path) -> Iterator[None]:
    lock_path = run_dir / ".official_run.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            pass
        except BlockingIOError as exc:
            raise RuntimeError(
                f"Another official-run process holds the lock: {lock_path}"
            ) from exc
        yield


def _validate_protocol(protocol: dict[str, Any]) -> None:
    if protocol.get("framework_version") != OFFICIAL_FRAMEWORK_VERSION:
        raise ValueError(
            f"framework_version must be {OFFICIAL_FRAMEWORK_VERSION!r}"
        )
    if protocol.get("transport") != "openai_batch_api":
        raise ValueError("Official full runs must use openai_batch_api")
    if protocol.get("endpoint") != "/v1/responses":
        raise ValueError("Official full runs must target /v1/responses")
    if protocol.get("completion_window") != "24h":
        raise ValueError("Batch completion_window must be 24h")
    for field in (
        "repetitions",
        "shard_size",
        "max_active_batches",
        "max_attempts_per_task",
        "transfer_retry_limit",
        "poll_interval_seconds",
    ):
        value = protocol.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{field} must be a positive integer")
    backoff = protocol.get("retry_backoff_seconds")
    if isinstance(backoff, bool) or not isinstance(backoff, (int, float)) or backoff < 0:
        raise ValueError("retry_backoff_seconds must be nonnegative")
    if protocol.get("store_responses") is not False:
        raise ValueError("The frozen protocol requires store_responses=false")
    policy = protocol.get("universe_policy") or {}
    if policy.get("exclude_model_ineligible_securities") is not True:
        raise ValueError("Model-ineligible securities must be excluded explicitly")
    if policy.get("retain_securities_with_missing_analyst_baselines") is not True:
        raise ValueError("Missing analyst baselines must remain comparison missingness")


def _validate_source_runs(audit_run: Path, eligible_run: Path) -> dict[str, Any]:
    audit_manifest = _read_json(audit_run / "manifest.json")
    eligible_manifest = _read_json(eligible_run / "manifest.json")
    audit_gate = _read_json(audit_run / "acquisition_gate.json")
    eligible_gate = _read_json(eligible_run / "acquisition_gate.json")

    for label, manifest in (("audit", audit_manifest), ("eligible", eligible_manifest)):
        if manifest.get("scope") != "full" or manifest.get("backend") != "dry-run":
            raise ValueError(f"{label} source must be a full dry-run artifact")
        if int(manifest.get("failed_assessments") or 0) != 0:
            raise ValueError(f"{label} source contains failed dry assessments")
        if int(manifest.get("not_attempted_assessments") or 0) != 0:
            raise ValueError(f"{label} source contains unattempted dry assessments")
    if audit_manifest.get("as_of_date") != eligible_manifest.get("as_of_date"):
        raise ValueError("Audit and eligible runs must share the same cutoff")
    if eligible_gate.get("status") != "PASS" or eligible_gate.get("issues"):
        raise ValueError("Eligible source run must pass the model-input gate")
    if int(eligible_manifest.get("repetitions") or 0) != 1:
        raise ValueError("Eligible source must contain exactly one dry repetition")

    audit_symbols = [str(value) for value in audit_manifest.get("symbols") or []]
    eligible_symbols = [str(value) for value in eligible_manifest.get("symbols") or []]
    issue_symbols: set[str] = set()
    for issue in audit_gate.get("issues") or []:
        if not isinstance(issue, dict) or not issue.get("symbol"):
            raise ValueError(
                "The official universe policy cannot auto-exclude a context-level gate issue"
            )
        issue_symbols.add(str(issue["symbol"]))
    expected_eligible = [symbol for symbol in audit_symbols if symbol not in issue_symbols]
    if eligible_symbols != expected_eligible:
        raise ValueError(
            "Eligible source symbols must equal audit symbols minus model-gate exclusions"
        )
    if int(eligible_gate.get("security_count") or 0) != len(eligible_symbols):
        raise ValueError("Eligible gate security_count does not match the manifest")
    return {
        "audit_manifest": audit_manifest,
        "eligible_manifest": eligible_manifest,
        "audit_gate": audit_gate,
        "eligible_gate": eligible_gate,
        "excluded_symbols": sorted(issue_symbols),
    }


def _condition_request_body(
    *,
    prompt: str,
    condition: str,
    model: str,
    reasoning_effort: str,
    max_output_tokens: int,
    web_max_tool_calls: int,
    store: bool,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "input": prompt,
        "reasoning": {"effort": reasoning_effort},
        "max_output_tokens": max_output_tokens,
        "store": store,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "stock_assessment",
                "strict": True,
                "schema": ASSESSMENT_SCHEMA,
            }
        },
    }
    if condition == "D_CURRENT_WEB":
        body["tools"] = [{"type": "web_search"}]
        body["include"] = ["web_search_call.action.sources"]
        body["max_tool_calls"] = web_max_tool_calls
    return body


def _task_request(
    run_dir: Path, task: dict[str, Any], plan: dict[str, Any], attempt: int
) -> dict[str, Any]:
    prompt_path = run_dir / str(task["prompt_path"])
    prompt = prompt_path.read_text(encoding="utf-8")
    if _sha256_bytes(prompt.encode("utf-8")) != task["prompt_sha256"]:
        raise ValueError(f"Prompt hash mismatch for task {task['task_id']}")
    custom_id = str(task["task_id"])
    if attempt > 1:
        custom_id = f"{custom_id}:a{attempt}"
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": plan["endpoint"],
        "body": _condition_request_body(
            prompt=prompt,
            condition=str(task["condition"]),
            model=str(plan["model"]),
            reasoning_effort=str(plan["reasoning_effort"]),
            max_output_tokens=int(plan["max_output_tokens"]),
            web_max_tool_calls=int(plan["web_max_tool_calls"]),
            store=bool(plan["store_responses"]),
        ),
    }


def _task_id(symbol: str, condition: str, repetition: int, prompt_sha256: str) -> str:
    condition_code = condition.split("_", 1)[0]
    safe_symbol = re.sub(r"[^A-Za-z0-9.-]", "-", symbol)
    value = f"t:{safe_symbol}:{condition_code}:r{repetition}:{prompt_sha256[:10]}"
    if not SAFE_ID.fullmatch(value):
        raise ValueError(f"Unsafe generated task ID: {value}")
    return value


def prepare_official_run(
    *,
    audit_run: str | Path,
    eligible_run: str | Path,
    protocol_path: str | Path | None = None,
    experiment_config_path: str | Path | None = None,
    output_root: str | Path | None = None,
    created_at: datetime | None = None,
) -> Path:
    audit_dir = Path(audit_run).expanduser().resolve()
    eligible_dir = Path(eligible_run).expanduser().resolve()
    protocol_file = Path(
        protocol_path or PROJECT_ROOT / "config" / "official_full_run.json"
    ).expanduser().resolve()
    experiment_file = Path(
        experiment_config_path or PROJECT_ROOT / "config" / "experiment.json"
    ).expanduser().resolve()
    protocol = _read_json(protocol_file)
    experiment = _read_json(experiment_file)
    _validate_protocol(protocol)
    source = _validate_source_runs(audit_dir, eligible_dir)
    eligible_manifest = source["eligible_manifest"]

    if str(experiment.get("as_of_date")) != str(eligible_manifest.get("as_of_date")):
        raise ValueError("Experiment config cutoff does not match the eligible source run")
    if list(experiment.get("conditions") or []) != list(
        eligible_manifest.get("conditions") or []
    ):
        raise ValueError("Experiment conditions do not match the eligible source run")
    if int(protocol["repetitions"]) != int(experiment.get("full_repetitions") or 0):
        raise ValueError("Official and experiment repetition counts do not match")
    for label, manifest in (
        ("audit", source["audit_manifest"]),
        ("eligible", eligible_manifest),
    ):
        if str(manifest.get("prompt_version") or "") != str(
            experiment.get("prompt_version") or ""
        ):
            raise ValueError(f"{label} source prompt version does not match experiment")
        if str(manifest.get("price_history_frequency") or "") != str(
            experiment.get("price_history_frequency") or ""
        ):
            raise ValueError(f"{label} source price frequency does not match experiment")
        if int(manifest.get("price_history_observations") or 0) != int(
            experiment.get("price_history_observations") or 0
        ):
            raise ValueError(f"{label} source price-history length does not match experiment")
        if int(manifest.get("web_max_tool_calls") or 4) != int(
            experiment.get("web_max_tool_calls") or 4
        ):
            raise ValueError(f"{label} source web-search cap does not match experiment")
    smoke_symbol = str(protocol["smoke_symbol"])
    if smoke_symbol not in eligible_manifest["symbols"]:
        raise ValueError(f"Smoke symbol is not model-eligible: {smoke_symbol}")

    moment = created_at or datetime.now(timezone.utc)
    run_id = (
        f"official_full_{eligible_manifest['as_of_date']}_"
        f"{moment.strftime('%Y%m%dT%H%M%S%fZ')}"
    )
    root = Path(output_root or PROJECT_ROOT / "official_runs").expanduser().resolve()
    run_dir = root / run_id
    if run_dir.exists():
        raise FileExistsError(run_dir)
    for directory in (
        run_dir / "frozen_inputs",
        run_dir / "prompts",
        run_dir / "batch_inputs",
        run_dir / "downloads",
        run_dir / "checkpoints",
        run_dir / "raw_responses",
        run_dir / "results",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    source_files = {
        "experiment_config.json": experiment_file,
        "official_protocol.json": protocol_file,
        "audit_manifest.json": audit_dir / "manifest.json",
        "audit_acquisition_gate.json": audit_dir / "acquisition_gate.json",
        "audit_experiment_universe.csv": audit_dir / "experiment_universe.csv",
        "audit_data_quality.csv": audit_dir / "data_quality.csv",
        "eligible_manifest.json": eligible_dir / "manifest.json",
        "eligible_acquisition_gate.json": eligible_dir / "acquisition_gate.json",
        "eligible_experiment_universe.csv": eligible_dir / "experiment_universe.csv",
        "eligible_data_quality.csv": eligible_dir / "data_quality.csv",
        "eligible_context_data_quality.csv": eligible_dir / "context_data_quality.csv",
        "eligible_features.csv": eligible_dir / "features.csv",
        "eligible_analyst_consensus.csv": eligible_dir / "analyst_consensus.csv",
        "eligible_dry_recommendations.csv": eligible_dir / "recommendations.csv",
    }
    for name, source_path in source_files.items():
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        destination = run_dir / "frozen_inputs" / name
        _atomic_write_bytes(destination, source_path.read_bytes())
        copied.append(destination)

    runtime_source_files: dict[str, dict[str, Any]] = {}
    for relative in OFFICIAL_RUNTIME_FILES:
        source_path = PROJECT_ROOT / relative
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        destination = run_dir / "frozen_inputs" / "runtime_source" / relative
        _atomic_write_bytes(destination, source_path.read_bytes())
        copied.append(destination)
        runtime_source_files[relative] = {
            "frozen_path": destination.relative_to(run_dir).as_posix(),
            "sha256": _sha256_path(destination),
            "size_bytes": destination.stat().st_size,
        }
    environment_path = run_dir / "frozen_inputs" / "runtime_environment.json"
    _atomic_write_json(environment_path, _runtime_environment())
    copied.append(environment_path)

    universe = pd.read_csv(eligible_dir / "experiment_universe.csv", dtype=str).fillna("")
    dry_rows = pd.read_csv(eligible_dir / "recommendations.csv", dtype=str).fillna("")
    conditions = [str(value) for value in experiment["conditions"]]
    symbols = [str(value) for value in eligible_manifest["symbols"]]
    universe_index = universe.set_index("symbol").to_dict(orient="index")
    dry_index = {
        (str(row["symbol"]), str(row["condition"])): row
        for row in dry_rows.to_dict(orient="records")
    }

    base_tasks: list[dict[str, Any]] = []
    prompt_paths: list[Path] = []
    for symbol in symbols:
        if symbol not in universe_index:
            raise ValueError(f"Eligible universe is missing symbol {symbol}")
        company_row = universe_index[symbol]
        for condition in conditions:
            row = dry_index.get((symbol, condition))
            if row is None:
                raise ValueError(f"Dry source is missing {symbol} {condition}")
            source_prompt = eligible_dir / "prompts" / f"{symbol}_{condition}_r1.txt"
            if not source_prompt.is_file():
                raise FileNotFoundError(source_prompt)
            prompt = source_prompt.read_bytes()
            prompt_sha = _sha256_bytes(prompt)
            if str(row.get("prompt_sha256") or "") != prompt_sha:
                raise ValueError(f"Archived prompt hash mismatch for {symbol} {condition}")
            destination = run_dir / "prompts" / f"{symbol}_{condition}.txt"
            _atomic_write_bytes(destination, prompt)
            prompt_paths.append(destination)
            base_tasks.append(
                {
                    "symbol": symbol,
                    "company": str(company_row.get("company") or symbol),
                    "sector": str(company_row.get("sector") or ""),
                    "condition": condition,
                    "prompt_path": destination.relative_to(run_dir).as_posix(),
                    "prompt_sha256": prompt_sha,
                    "packet_sha256": str(row.get("packet_sha256") or ""),
                }
            )

    tasks: list[dict[str, Any]] = []
    execution_order = 0
    for repetition in range(1, int(protocol["repetitions"]) + 1):
        for base in base_tasks:
            execution_order += 1
            phase = (
                "smoke"
                if base["symbol"] == smoke_symbol and repetition == 1
                else "full"
            )
            tasks.append(
                {
                    "task_id": _task_id(
                        str(base["symbol"]),
                        str(base["condition"]),
                        repetition,
                        str(base["prompt_sha256"]),
                    ),
                    "execution_order": execution_order,
                    "repetition": repetition,
                    "phase": phase,
                    **base,
                }
            )

    task_registry = run_dir / "frozen_inputs" / "task_registry.jsonl"
    _write_jsonl(task_registry, tasks)
    copied.append(task_registry)

    plan_base: dict[str, Any] = {
        "framework_version": OFFICIAL_FRAMEWORK_VERSION,
        "plan_schema_version": OFFICIAL_PLAN_SCHEMA_VERSION,
        "run_id": run_id,
        "created_at_utc": moment.isoformat(),
        "as_of_date": str(experiment["as_of_date"]),
        "scope": "full",
        "transport": protocol["transport"],
        "endpoint": protocol["endpoint"],
        "completion_window": protocol["completion_window"],
        "model": str(experiment["model"]),
        "reasoning_effort": str(experiment["reasoning_effort"]),
        "max_output_tokens": int(experiment["max_output_tokens"]),
        "web_max_tool_calls": int(experiment.get("web_max_tool_calls", 4)),
        "store_responses": bool(protocol["store_responses"]),
        "prompt_version": str(experiment["prompt_version"]),
        "price_history_frequency": str(experiment["price_history_frequency"]),
        "price_history_observations": int(experiment["price_history_observations"]),
        "horizon_months": int(experiment["horizon_months"]),
        "conditions": conditions,
        "repetitions": int(protocol["repetitions"]),
        "security_count_audited": len(source["audit_manifest"]["symbols"]),
        "security_count_eligible": len(symbols),
        "eligible_symbols": symbols,
        "excluded_symbols": source["excluded_symbols"],
        "planned_assessments": len(tasks),
        "smoke_assessments": sum(task["phase"] == "smoke" for task in tasks),
        "full_assessments": sum(task["phase"] == "full" for task in tasks),
        "task_registry": task_registry.relative_to(run_dir).as_posix(),
        "task_registry_sha256": _sha256_path(task_registry),
        "shard_size": int(protocol["shard_size"]),
        "max_active_batches": int(protocol["max_active_batches"]),
        "max_attempts_per_task": int(protocol["max_attempts_per_task"]),
        "transfer_retry_limit": int(protocol["transfer_retry_limit"]),
        "retry_backoff_seconds": float(protocol["retry_backoff_seconds"]),
        "poll_interval_seconds": int(protocol["poll_interval_seconds"]),
        "require_smoke_batch": bool(protocol["require_smoke_batch"]),
        "smoke_symbol": smoke_symbol,
        "universe_policy": protocol["universe_policy"],
        "source_artifacts": {
            "audit_run": str(audit_dir),
            "eligible_run": str(eligible_dir),
        },
        "runtime_source_files": runtime_source_files,
    }

    task_index = {str(task["task_id"]): task for task in tasks}
    shard_rows: list[dict[str, Any]] = []
    task_groups: list[tuple[str, str, list[dict[str, Any]]]] = [
        ("smoke", "ALL", [task for task in tasks if task["phase"] == "smoke"])
    ]
    for condition in conditions:
        task_groups.append(
            (
                "full",
                condition,
                [
                    task
                    for task in tasks
                    if task["phase"] == "full" and task["condition"] == condition
                ],
            )
        )
    for phase, condition, group in task_groups:
        for offset in range(0, len(group), int(protocol["shard_size"])):
            chunk = group[offset : offset + int(protocol["shard_size"])]
            shard_number = offset // int(protocol["shard_size"]) + 1
            condition_label = condition.lower()
            shard_id = f"{phase}:{condition_label}:{shard_number:03d}:a1"
            filename = (
                f"{run_id}_{phase}_{condition_label}_{shard_number:03d}_a1.jsonl"
            )
            path = run_dir / "batch_inputs" / filename
            requests = [
                _task_request(run_dir, task_index[str(task["task_id"])], plan_base, 1)
                for task in chunk
            ]
            _write_jsonl(path, requests)
            shard_rows.append(
                {
                    "shard_id": shard_id,
                    "phase": phase,
                    "condition": condition,
                    "attempt": 1,
                    "input_path": path.relative_to(run_dir).as_posix(),
                    "input_filename": filename,
                    "input_sha256": _sha256_path(path),
                    "input_size_bytes": path.stat().st_size,
                    "request_count": len(requests),
                    "task_ids": [str(task["task_id"]) for task in chunk],
                    "request_ids": [str(request["custom_id"]) for request in requests],
                }
            )

    copied.extend(prompt_paths)
    plan_base["frozen_files"] = _relative_inventory(run_dir, copied)
    plan_base["shards"] = shard_rows
    plan_sha = _object_sha256(plan_base)
    plan = {**plan_base, "plan_sha256": plan_sha}
    _atomic_write_json(run_dir / "official_plan.json", plan)

    task_state = {
        str(task["task_id"]): {
            "phase": task["phase"],
            "condition": task["condition"],
            "status": "PENDING",
            "attempts": 0,
            "accepted_attempt": None,
            "last_error": None,
        }
        for task in tasks
    }
    shard_state = {
        str(shard["shard_id"]): {
            **shard,
            "status": "LOCAL_READY",
            "remote_file_id": None,
            "batch_id": None,
            "batch_status": None,
            "output_file_id": None,
            "error_file_id": None,
            "output_path": None,
            "error_path": None,
            "last_synced_at_utc": None,
        }
        for shard in shard_rows
    }
    state = {
        "framework_version": OFFICIAL_FRAMEWORK_VERSION,
        "state_schema_version": OFFICIAL_STATE_SCHEMA_VERSION,
        "run_id": run_id,
        "plan_sha256": plan_sha,
        "status": "PREPARED_AWAITING_APPROVAL",
        "created_at_utc": moment.isoformat(),
        "updated_at_utc": moment.isoformat(),
        "shards": shard_state,
        "tasks": task_state,
    }
    _atomic_write_json(run_dir / "state.json", state)

    approval = {
        "approved": False,
        "plan_sha256": plan_sha,
        "run_id": run_id,
        "approved_by": None,
        "approved_at_utc": None,
        "approval_note": "Waiting for explicit professor green light.",
        "planned_assessments": len(tasks),
        "max_attempts_per_task": int(protocol["max_attempts_per_task"]),
    }
    approval_name = str(protocol["approval_policy"]["approval_filename"])
    _atomic_write_json(run_dir / approval_name, approval)
    preflight_official_run(run_dir, require_approval=False, write_report=True)
    render_progress_dashboard(run_dir)
    return run_dir


def prepare_current_official_run(
    *,
    protocol_path: str | Path | None = None,
    experiment_config_path: str | Path | None = None,
    output_root: str | Path | None = None,
    refresh: bool = False,
) -> Path:
    """Acquire a current full dry audit, filter it, and freeze an official plan.

    The provider caches and all local artifacts are written atomically. Re-running
    after an interruption reuses every valid same-cutoff cache entry and fetches
    only missing or invalid entries. This command never contacts OpenAI.
    """
    experiment_file = Path(
        experiment_config_path or PROJECT_ROOT / "config" / "experiment.json"
    ).expanduser().resolve()
    config = load_config(experiment_file)
    current_utc = datetime.now(timezone.utc).date().isoformat()
    if config.as_of_date != current_utc:
        raise ValueError(
            "Current-data preparation requires experiment as_of_date to equal the "
            f"current UTC date ({current_utc}); received {config.as_of_date}"
        )

    audit_run = run_experiment(
        config,
        scope="full",
        backend="dry-run",
        repetitions=1,
        refresh=refresh,
    )
    audit_manifest = _read_json(audit_run / "manifest.json")
    audit_gate = _read_json(audit_run / "acquisition_gate.json")
    excluded: set[str] = set()
    for issue in audit_gate.get("issues") or []:
        if not isinstance(issue, dict) or not issue.get("symbol"):
            raise RuntimeError(
                "A context-level acquisition issue cannot be resolved by excluding "
                "individual securities"
            )
        excluded.add(str(issue["symbol"]))
    eligible_symbols = [
        str(symbol)
        for symbol in audit_manifest.get("symbols") or []
        if str(symbol) not in excluded
    ]
    if not eligible_symbols:
        raise RuntimeError("No securities remain after the model-input gate")

    # Never force a second download pass: valid data acquired above are reused.
    eligible_run = run_experiment(
        config,
        scope="full",
        backend="dry-run",
        repetitions=1,
        refresh=False,
        symbols=eligible_symbols,
    )
    return prepare_official_run(
        audit_run=audit_run,
        eligible_run=eligible_run,
        protocol_path=protocol_path,
        experiment_config_path=experiment_file,
        output_root=output_root,
    )


def _plan_without_hash(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if key != "plan_sha256"}


def _approval_path(run_dir: Path) -> Path:
    protocol = _read_json(run_dir / "frozen_inputs" / "official_protocol.json")
    return run_dir / str(protocol["approval_policy"]["approval_filename"])


def preflight_official_run(
    run_dir: str | Path,
    *,
    require_approval: bool,
    write_report: bool = True,
) -> dict[str, Any]:
    root = Path(run_dir).expanduser().resolve()
    plan = _read_json(root / "official_plan.json")
    state = _read_json(root / "state.json")
    issues: list[str] = []

    if plan.get("framework_version") != OFFICIAL_FRAMEWORK_VERSION:
        issues.append("unsupported framework version")
    if plan.get("plan_schema_version") != OFFICIAL_PLAN_SCHEMA_VERSION:
        issues.append("unsupported official plan schema")
    if state.get("state_schema_version") != OFFICIAL_STATE_SCHEMA_VERSION:
        issues.append("unsupported official state schema")
    runtime_files = plan.get("runtime_source_files") or {}
    if set(runtime_files) != set(OFFICIAL_RUNTIME_FILES):
        issues.append("official runtime source inventory is incomplete")
    for relative, expected in runtime_files.items():
        live_path = PROJECT_ROOT / str(relative)
        if not live_path.is_file():
            issues.append(f"missing live runtime source: {relative}")
            continue
        if live_path.stat().st_size != int(expected["size_bytes"]):
            issues.append(f"live runtime source size mismatch: {relative}")
        if _sha256_path(live_path) != expected["sha256"]:
            issues.append(f"live runtime source hash mismatch: {relative}")
    calculated_plan_sha = _object_sha256(_plan_without_hash(plan))
    if plan.get("plan_sha256") != calculated_plan_sha:
        issues.append("official plan hash mismatch")
    if state.get("plan_sha256") != plan.get("plan_sha256"):
        issues.append("state does not belong to the official plan")

    for relative, expected in (plan.get("frozen_files") or {}).items():
        path = root / str(relative)
        if not path.is_file():
            issues.append(f"missing frozen file: {relative}")
            continue
        if path.stat().st_size != int(expected["size_bytes"]):
            issues.append(f"frozen file size mismatch: {relative}")
        if _sha256_path(path) != expected["sha256"]:
            issues.append(f"frozen file hash mismatch: {relative}")

    task_path = root / str(plan.get("task_registry") or "")
    tasks: list[dict[str, Any]] = []
    if task_path.is_file():
        try:
            tasks = _read_jsonl(task_path)
        except Exception as exc:
            issues.append(f"invalid task registry: {type(exc).__name__}: {exc}")
    else:
        issues.append("missing task registry")
    if len(tasks) != int(plan.get("planned_assessments") or -1):
        issues.append("task registry count does not match the plan")
    task_ids = [str(task.get("task_id") or "") for task in tasks]
    if len(task_ids) != len(set(task_ids)) or any(not SAFE_ID.fullmatch(value) for value in task_ids):
        issues.append("task IDs are empty, unsafe, or duplicated")
    if set(task_ids) != set((state.get("tasks") or {}).keys()):
        issues.append("state task set does not match the registry")

    request_ids: list[str] = []
    shard_task_ids: list[str] = []
    for shard in plan.get("shards") or []:
        relative = str(shard["input_path"])
        path = root / relative
        if not path.is_file():
            issues.append(f"missing batch input: {relative}")
            continue
        if path.stat().st_size != int(shard["input_size_bytes"]):
            issues.append(f"batch input size mismatch: {relative}")
        if _sha256_path(path) != shard["input_sha256"]:
            issues.append(f"batch input hash mismatch: {relative}")
        try:
            requests = _read_jsonl(path)
        except Exception as exc:
            issues.append(f"invalid batch input {relative}: {type(exc).__name__}: {exc}")
            continue
        if len(requests) != int(shard.get("request_count") or -1):
            issues.append(f"request count mismatch: {relative}")
        if len(requests) > BATCH_MAX_REQUESTS:
            issues.append(f"batch request limit exceeded: {relative}")
        if path.stat().st_size > BATCH_MAX_INPUT_BYTES:
            issues.append(f"batch input file limit exceeded: {relative}")
        for request in requests:
            request_id = str(request.get("custom_id") or "")
            request_ids.append(request_id)
            if request.get("method") != "POST" or request.get("url") != plan.get("endpoint"):
                issues.append(f"invalid method or endpoint in {relative}")
            body = request.get("body") or {}
            if body.get("model") != plan.get("model"):
                issues.append(f"model drift in {relative}")
            if body.get("store") is not False:
                issues.append(f"store must remain false in {relative}")
        shard_task_ids.extend(str(value) for value in shard.get("task_ids") or [])
    if len(request_ids) != len(set(request_ids)):
        issues.append("batch custom_id values are duplicated")
    if set(shard_task_ids) != set(task_ids):
        issues.append("planned shards do not cover the exact task registry")

    approval_status = "NOT_REQUIRED"
    if require_approval:
        approval_status = "MISSING_OR_INVALID"
        try:
            approval = _read_json(_approval_path(root))
            if (
                approval.get("approved") is True
                and approval.get("plan_sha256") == plan.get("plan_sha256")
                and approval.get("run_id") == plan.get("run_id")
                and str(approval.get("approved_by") or "").strip()
                and str(approval.get("approved_at_utc") or "").strip()
                and int(approval.get("max_attempts_per_task") or 0)
                == int(plan.get("max_attempts_per_task") or 0)
            ):
                approval_status = "APPROVED"
            else:
                issues.append("explicit approval is absent or does not match the frozen plan")
        except Exception as exc:
            issues.append(f"approval validation failed: {type(exc).__name__}: {exc}")

    report = {
        "status": "PASS" if not issues else "FAIL",
        "checked_at_utc": _now_iso(),
        "run_id": plan.get("run_id"),
        "plan_sha256": plan.get("plan_sha256"),
        "require_approval": require_approval,
        "approval_status": approval_status,
        "planned_assessments": plan.get("planned_assessments"),
        "security_count_eligible": plan.get("security_count_eligible"),
        "shard_count": len(plan.get("shards") or []),
        "issues": issues,
    }
    if write_report:
        name = "launch_preflight.json" if require_approval else "preflight.json"
        _atomic_write_json(root / name, report)
    if issues:
        raise RuntimeError(f"Official-run preflight failed: {issues}")
    return report


def record_approval(
    run_dir: str | Path, *, approved_by: str, note: str
) -> Path:
    root = Path(run_dir).expanduser().resolve()
    approver = approved_by.strip()
    if not approver:
        raise ValueError("approved_by is required")
    approval_note = note.strip()
    if not approval_note:
        raise ValueError("note is required")
    with _run_lock(root):
        preflight_official_run(root, require_approval=False, write_report=True)
        plan = _read_json(root / "official_plan.json")
        approval_path = _approval_path(root)
        approval = _read_json(approval_path)
        approval.update(
            {
                "approved": True,
                "plan_sha256": plan["plan_sha256"],
                "run_id": plan["run_id"],
                "approved_by": approver,
                "approved_at_utc": _now_iso(),
                "approval_note": approval_note,
                "planned_assessments": plan["planned_assessments"],
                "max_attempts_per_task": plan["max_attempts_per_task"],
            }
        )
        _atomic_write_json(approval_path, approval)
        preflight_official_run(root, require_approval=True, write_report=True)
        state = _read_json(root / "state.json")
        if state.get("status") == "PREPARED_AWAITING_APPROVAL":
            state["status"] = "APPROVED_READY_FOR_SMOKE"
            _save_state(root, state)
        render_progress_dashboard(root)
        return approval_path


def official_status(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir).expanduser().resolve()
    plan = _read_json(root / "official_plan.json")
    state = _read_json(root / "state.json")
    task_counts: dict[str, int] = {}
    for task in (state.get("tasks") or {}).values():
        status = str(task.get("status") or "UNKNOWN")
        task_counts[status] = task_counts.get(status, 0) + 1
    shard_counts: dict[str, int] = {}
    for shard in (state.get("shards") or {}).values():
        status = str(shard.get("status") or "UNKNOWN")
        shard_counts[status] = shard_counts.get(status, 0) + 1
    smoke_ids = {
        str(task["task_id"])
        for task in _read_jsonl(root / str(plan["task_registry"]))
        if task.get("phase") == "smoke"
    }
    smoke_accepted = sum(
        (state.get("tasks") or {}).get(task_id, {}).get("status") == "ACCEPTED"
        for task_id in smoke_ids
    )
    approval_status = "NOT_APPROVED"
    try:
        approval = _read_json(_approval_path(root))
        if (
            approval.get("approved") is True
            and approval.get("plan_sha256") == plan.get("plan_sha256")
            and approval.get("run_id") == plan.get("run_id")
        ):
            approval_status = "APPROVED"
    except Exception:
        approval_status = "INVALID"
    return {
        "run_id": plan["run_id"],
        "plan_sha256": plan["plan_sha256"],
        "status": state.get("status"),
        "approval_status": approval_status,
        "task_counts": dict(sorted(task_counts.items())),
        "shard_counts": dict(sorted(shard_counts.items())),
        "smoke_accepted": smoke_accepted,
        "smoke_planned": len(smoke_ids),
        "planned_assessments": plan["planned_assessments"],
    }


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        result = value.model_dump(mode="json")
        if isinstance(result, dict):
            return result
    raise TypeError(f"Expected a mapping-compatible API object, received {type(value)!r}")


class OpenAIBatchTransport:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for batch transport")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install requirements.txt before using batch transport") from exc
        self.client = OpenAI(api_key=api_key)

    def find_files(self, filename: str, size_bytes: int) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for item in self.client.files.list(purpose="batch", limit=100):
            value = _as_dict(item)
            if value.get("filename") == filename and int(value.get("bytes") or -1) == size_bytes:
                matches.append(value)
        return matches

    def upload_file(self, path: Path) -> dict[str, Any]:
        with path.open("rb") as handle:
            return _as_dict(self.client.files.create(file=handle, purpose="batch"))

    def find_batches(
        self, *, plan_sha256: str, shard_id: str, input_file_id: str
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for item in self.client.batches.list(limit=100):
            value = _as_dict(item)
            metadata = value.get("metadata") or {}
            if (
                value.get("input_file_id") == input_file_id
                and metadata.get("plan_sha256") == plan_sha256
                and metadata.get("shard_id") == shard_id
            ):
                matches.append(value)
        return matches

    def create_batch(
        self,
        *,
        input_file_id: str,
        endpoint: str,
        completion_window: str,
        metadata: dict[str, str],
    ) -> dict[str, Any]:
        return _as_dict(
            self.client.batches.create(
                input_file_id=input_file_id,
                endpoint=endpoint,
                completion_window=completion_window,
                metadata=metadata,
            )
        )

    def retrieve_batch(self, batch_id: str) -> dict[str, Any]:
        return _as_dict(self.client.batches.retrieve(batch_id))

    def retrieve_file(self, file_id: str) -> dict[str, Any]:
        return _as_dict(self.client.files.retrieve(file_id))

    def download_file(self, file_id: str) -> bytes:
        response = self.client.files.content(file_id)
        if hasattr(response, "read"):
            value = response.read()
        elif hasattr(response, "content"):
            value = response.content
        else:
            value = getattr(response, "text", "").encode("utf-8")
        return value if isinstance(value, bytes) else bytes(value)


def _retry_call(
    operation: Callable[[], Any], *, attempts: int, backoff_seconds: float
) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            if attempt < attempts and backoff_seconds > 0:
                time.sleep(backoff_seconds * (2 ** (attempt - 1)))
    assert last_error is not None
    raise last_error


def _save_state(run_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at_utc"] = _now_iso()
    _atomic_write_json(run_dir / "state.json", state)


def _transport_or_default(transport: BatchTransport | None) -> BatchTransport:
    return transport or OpenAIBatchTransport(os.getenv("OPENAI_API_KEY", ""))


def _ensure_remote_file(
    *,
    run_dir: Path,
    plan: dict[str, Any],
    state: dict[str, Any],
    shard_id: str,
    transport: BatchTransport,
) -> str:
    shard = state["shards"][shard_id]
    if shard.get("remote_file_id"):
        return str(shard["remote_file_id"])
    path = run_dir / str(shard["input_path"])
    if not path.is_file() or _sha256_path(path) != shard["input_sha256"]:
        raise RuntimeError(f"Local batch shard is missing or changed: {shard_id}")
    tasks = {
        str(task["task_id"]): task
        for task in _read_jsonl(run_dir / str(plan["task_registry"]))
    }
    try:
        expected_requests = [
            _task_request(
                run_dir,
                tasks[str(task_id)],
                plan,
                int(shard["attempt"]),
            )
            for task_id in shard["task_ids"]
        ]
    except KeyError as exc:
        raise RuntimeError(f"Unknown task in runtime shard {shard_id}: {exc}") from exc
    actual_requests = _read_jsonl(path)
    if _canonical_json(actual_requests) != _canonical_json(expected_requests):
        raise RuntimeError(
            f"Runtime shard does not match its frozen tasks and exact request settings: {shard_id}"
        )
    expected_request_ids = [str(request["custom_id"]) for request in expected_requests]
    if expected_request_ids != [str(value) for value in shard["request_ids"]]:
        raise RuntimeError(f"Runtime shard request IDs do not match state: {shard_id}")
    attempts = int(plan["transfer_retry_limit"])
    backoff = float(plan["retry_backoff_seconds"])
    shard["status"] = "UPLOAD_INTENT_RECORDED"
    _save_state(run_dir, state)

    def recover_or_upload() -> dict[str, Any]:
        matches = transport.find_files(
            str(shard["input_filename"]), int(shard["input_size_bytes"])
        )
        if matches:
            matches.sort(key=lambda value: int(value.get("created_at") or 0), reverse=True)
            return matches[0]
        return transport.upload_file(path)

    remote = _retry_call(recover_or_upload, attempts=attempts, backoff_seconds=backoff)
    remote_id = str(remote.get("id") or "")
    if not remote_id:
        raise RuntimeError(f"Batch file upload returned no file ID for {shard_id}")
    shard["remote_file_id"] = remote_id
    shard["status"] = "UPLOADED"
    shard["uploaded_at_utc"] = _now_iso()
    _save_state(run_dir, state)
    return remote_id


def _ensure_remote_batch(
    *,
    run_dir: Path,
    plan: dict[str, Any],
    state: dict[str, Any],
    shard_id: str,
    transport: BatchTransport,
    input_file_id: str,
) -> str:
    shard = state["shards"][shard_id]
    if shard.get("batch_id"):
        return str(shard["batch_id"])
    attempts = int(plan["transfer_retry_limit"])
    backoff = float(plan["retry_backoff_seconds"])
    shard["status"] = "BATCH_CREATE_INTENT_RECORDED"
    _save_state(run_dir, state)
    metadata = {
        "plan_sha256": str(plan["plan_sha256"]),
        "run_id": str(plan["run_id"]),
        "shard_id": shard_id,
        "input_sha256": str(shard["input_sha256"]),
    }

    def recover_or_create() -> dict[str, Any]:
        matches = transport.find_batches(
            plan_sha256=str(plan["plan_sha256"]),
            shard_id=shard_id,
            input_file_id=input_file_id,
        )
        if matches:
            matches.sort(key=lambda value: int(value.get("created_at") or 0), reverse=True)
            return matches[0]
        return transport.create_batch(
            input_file_id=input_file_id,
            endpoint=str(plan["endpoint"]),
            completion_window=str(plan["completion_window"]),
            metadata=metadata,
        )

    batch = _retry_call(recover_or_create, attempts=attempts, backoff_seconds=backoff)
    batch_id = str(batch.get("id") or "")
    if not batch_id:
        raise RuntimeError(f"Batch creation returned no batch ID for {shard_id}")
    shard["batch_id"] = batch_id
    shard["batch_status"] = str(batch.get("status") or "validating")
    shard["status"] = "SUBMITTED"
    shard["submitted_at_utc"] = _now_iso()
    _save_state(run_dir, state)
    return batch_id


def _smoke_task_ids(run_dir: Path, plan: dict[str, Any]) -> set[str]:
    return {
        str(task["task_id"])
        for task in _read_jsonl(run_dir / str(plan["task_registry"]))
        if task.get("phase") == "smoke"
    }


def _smoke_passed(run_dir: Path, plan: dict[str, Any], state: dict[str, Any]) -> bool:
    smoke_ids = _smoke_task_ids(run_dir, plan)
    return bool(smoke_ids) and all(
        state["tasks"].get(task_id, {}).get("status") == "ACCEPTED"
        for task_id in smoke_ids
    )


def launch_official_phase(
    run_dir: str | Path,
    *,
    phase: str,
    transport: BatchTransport | None = None,
) -> dict[str, Any]:
    root = Path(run_dir).expanduser().resolve()
    if phase not in {"smoke", "full", "smoke_retry", "retry"}:
        raise ValueError("phase must be smoke, full, smoke_retry, or retry")
    with _run_lock(root):
        preflight_official_run(root, require_approval=True, write_report=True)
        plan = _read_json(root / "official_plan.json")
        state = _read_json(root / "state.json")
        if phase == "full" and not _smoke_passed(root, plan, state):
            raise RuntimeError("Full shards remain locked until the smoke tasks pass")
        client = _transport_or_default(transport)
        active = sum(
            str(shard.get("batch_status") or "") in ACTIVE_BATCH_STATUSES
            for shard in state["shards"].values()
        )
        slots = max(0, int(plan["max_active_batches"]) - active)
        candidates = [
            (shard_id, shard)
            for shard_id, shard in state["shards"].items()
            if shard.get("phase") == phase and not shard.get("batch_id")
        ]
        launched: list[str] = []
        for shard_id, _ in candidates[:slots]:
            file_id = _ensure_remote_file(
                run_dir=root,
                plan=plan,
                state=state,
                shard_id=shard_id,
                transport=client,
            )
            _ensure_remote_batch(
                run_dir=root,
                plan=plan,
                state=state,
                shard_id=shard_id,
                transport=client,
                input_file_id=file_id,
            )
            launched.append(shard_id)
        state["status"] = "RUNNING"
        _save_state(root, state)
        render_progress_dashboard(root)
        return {"phase": phase, "launched": launched, "remaining": len(candidates) - len(launched)}


def _download_remote_file(
    *,
    run_dir: Path,
    plan: dict[str, Any],
    transport: BatchTransport,
    file_id: str,
    destination: Path,
) -> dict[str, Any]:
    def verified_download() -> bytes:
        metadata = transport.retrieve_file(file_id)
        value = transport.download_file(file_id)
        if not isinstance(value, bytes):
            raise TypeError("Downloaded OpenAI file content must be bytes")
        expected_size = metadata.get("bytes")
        if expected_size is not None and len(value) != int(expected_size):
            raise IOError(
                f"Downloaded file size mismatch for {file_id}: "
                f"expected {expected_size}, received {len(value)}"
            )
        return value

    value = _retry_call(
        verified_download,
        attempts=int(plan["transfer_retry_limit"]),
        backoff_seconds=float(plan["retry_backoff_seconds"]),
    )
    _atomic_write_bytes(destination, value)
    return {"path": destination.relative_to(run_dir).as_posix(), "sha256": _sha256_bytes(value)}


def _extract_output_text(body: dict[str, Any]) -> str:
    pieces: list[str] = []
    for output in body.get("output") or []:
        if not isinstance(output, dict) or output.get("type") != "message":
            continue
        for content in output.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                pieces.append(str(content.get("text") or ""))
    return "".join(pieces)


def _created_at_iso(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return _now_iso()


def _record_from_batch_body(
    *,
    task: dict[str, Any],
    plan: dict[str, Any],
    body: dict[str, Any],
    request_id: str | None,
    attempt: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    output_text = _extract_output_text(body)
    if not output_text:
        raise ValueError("Batch response contains no structured output text")
    payload = json.loads(output_text)
    if not isinstance(payload, dict):
        raise ValueError("Structured output is not a JSON object")
    expected = {
        "symbol": task["symbol"],
        "as_of": plan["as_of_date"],
        "horizon_months": int(plan["horizon_months"]),
    }
    mismatches = {
        key: {"expected": expected_value, "received": payload.get(key)}
        for key, expected_value in expected.items()
        if payload.get(key) != expected_value
    }
    if mismatches:
        raise ValueError(f"Assessment identity mismatch: {mismatches}")
    condition = str(task["condition"])
    validate_assessment(
        payload,
        cutoff=str(plan["as_of_date"]),
        allow_web=condition == "D_CURRENT_WEB",
        condition=condition,
    )
    usage = body.get("usage") or {}
    web_audit = _audit_web_actions(body)
    record = {
        "run_id": plan["run_id"],
        "execution_order": int(task["execution_order"]),
        "assessment_created_at_utc": _created_at_iso(body.get("created_at")),
        "symbol": task["symbol"],
        "company": task["company"],
        "sector": task["sector"],
        "condition": condition,
        "repetition": int(task["repetition"]),
        "rating": payload["rating"],
        "rating_score": RATING_SCORE[payload["rating"]],
        "confidence_0_100": payload["confidence_0_100"],
        "evidence_sufficiency": payload["evidence_sufficiency"],
        "forced_rating_due_to_insufficient_data": payload[
            "forced_rating_due_to_insufficient_data"
        ],
        "reason_codes": _json_cell(payload["reason_codes"]),
        "thesis": _json_cell(payload["thesis"]),
        "risks": _json_cell(payload["risks"]),
        "evidence": _json_cell(payload["evidence"]),
        "analyst_opinions_encountered": payload["analyst_opinions_encountered"],
        "analyst_opinions_used": payload["analyst_opinions_used"],
        "analyst_terms_in_final_output": _analyst_exposure_audit(payload, condition),
        "analyst_terms_detected_in_web_actions": web_audit[
            "analyst_terms_detected_in_web_actions"
        ],
        "analyst_web_action_matched_terms": _json_cell(
            web_audit["analyst_web_action_matched_terms"]
        ),
        "web_search_call_count": web_audit["web_search_call_count"],
        "web_search_source_count": web_audit["web_search_source_count"],
        "limitations": _json_cell(payload["limitations"]),
        "backend": "OPENAI_BATCH_RESPONSES_API",
        "model": body.get("model") or plan["model"],
        "requested_model": plan["model"],
        "response_id": body.get("id"),
        "batch_request_id": request_id,
        "prompt_version": plan["prompt_version"],
        "prompt_sha256": task["prompt_sha256"],
        "packet_sha256": task["packet_sha256"],
        "web_search_enabled": condition == "D_CURRENT_WEB",
        "retrieved_source_urls": _json_cell(_extract_urls(body)),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "record_provenance": "OFFICIAL_BATCH_ORIGINAL" if attempt == 1 else "OFFICIAL_BATCH_EXACT_RETRY",
        "attempt": attempt,
    }
    return record, payload


def _base_task_id(request_id: str) -> tuple[str, int]:
    match = re.fullmatch(r"(.+:[0-9a-f]{10}):a([2-9][0-9]*)", request_id)
    if match:
        return match.group(1), int(match.group(2))
    return request_id, 1


def _batch_error_payload(line: dict[str, Any]) -> Any:
    """Return either legacy top-level or Responses-style nested batch errors."""
    return line.get("error") or (
        ((line.get("response") or {}).get("body") or {}).get("error")
    )


def _mark_task_failure(
    *,
    run_dir: Path,
    state: dict[str, Any],
    task_id: str,
    attempt: int,
    error: str,
    raw: dict[str, Any] | None,
) -> None:
    task_state = state["tasks"][task_id]
    if task_state.get("status") == "ACCEPTED":
        return
    task_state["status"] = "FAILED"
    task_state["attempts"] = max(int(task_state.get("attempts") or 0), attempt)
    task_state["last_error"] = error
    checkpoint = {
        "task_id": task_id,
        "attempt": attempt,
        "status": "FAILED",
        "error": error,
        "recorded_at_utc": _now_iso(),
    }
    if raw is not None:
        raw_path = run_dir / "raw_responses" / f"{task_id.replace(':', '_')}_a{attempt}.failed.json"
        _atomic_write_json(raw_path, raw)
        checkpoint["raw_path"] = raw_path.relative_to(run_dir).as_posix()
    checkpoint_path = run_dir / "checkpoints" / task_id.replace(":", "_") / f"attempt_{attempt}.json"
    _atomic_write_json(checkpoint_path, checkpoint)


def _ingest_shard(
    *, run_dir: Path, plan: dict[str, Any], state: dict[str, Any], shard_id: str
) -> None:
    shard = state["shards"][shard_id]
    tasks = {
        str(task["task_id"]): task
        for task in _read_jsonl(run_dir / str(plan["task_registry"]))
    }
    seen: set[str] = set()
    seen_request_ids: set[str] = set()
    output_path = run_dir / str(shard.get("output_path") or "")
    if output_path.is_file():
        for line in _read_jsonl(output_path):
            request_id = str(line.get("custom_id") or "")
            task_id, attempt = _base_task_id(request_id)
            if (
                request_id not in shard["request_ids"]
                or task_id not in tasks
                or task_id not in shard["task_ids"]
                or attempt != int(shard["attempt"])
            ):
                raise ValueError(f"Unexpected task ID in batch output: {request_id}")
            if request_id in seen_request_ids or task_id in seen:
                raise ValueError(f"Duplicate task result in batch output: {request_id}")
            seen_request_ids.add(request_id)
            seen.add(task_id)
            response = line.get("response") or {}
            body = response.get("body") or {}
            try:
                if int(response.get("status_code") or 0) != 200:
                    raise ValueError(f"HTTP status {response.get('status_code')}")
                record, payload = _record_from_batch_body(
                    task=tasks[task_id],
                    plan=plan,
                    body=body,
                    request_id=response.get("request_id"),
                    attempt=attempt,
                )
                raw_path = run_dir / "raw_responses" / f"{task_id.replace(':', '_')}_a{attempt}.json"
                _atomic_write_json(raw_path, body)
                checkpoint = {
                    "task_id": task_id,
                    "attempt": attempt,
                    "status": "ACCEPTED",
                    "recorded_at_utc": _now_iso(),
                    "raw_path": raw_path.relative_to(run_dir).as_posix(),
                    "record": record,
                    "assessment": payload,
                }
                checkpoint_path = run_dir / "checkpoints" / task_id.replace(":", "_") / f"attempt_{attempt}.json"
                _atomic_write_json(checkpoint_path, checkpoint)
                task_state = state["tasks"][task_id]
                task_state["status"] = "ACCEPTED"
                task_state["attempts"] = max(int(task_state.get("attempts") or 0), attempt)
                task_state["accepted_attempt"] = attempt
                task_state["last_error"] = None
                task_state["checkpoint_path"] = checkpoint_path.relative_to(run_dir).as_posix()
            except Exception as exc:
                _mark_task_failure(
                    run_dir=run_dir,
                    state=state,
                    task_id=task_id,
                    attempt=attempt,
                    error=f"{type(exc).__name__}: {exc}",
                    raw=line,
                )

    error_path = run_dir / str(shard.get("error_path") or "")
    if error_path.is_file():
        for line in _read_jsonl(error_path):
            request_id = str(line.get("custom_id") or "")
            task_id, attempt = _base_task_id(request_id)
            if (
                request_id not in shard["request_ids"]
                or task_id not in tasks
                or task_id not in shard["task_ids"]
                or attempt != int(shard["attempt"])
            ):
                raise ValueError(f"Unexpected task ID in batch error output: {request_id}")
            if request_id in seen_request_ids or task_id in seen:
                raise ValueError(f"Duplicate task result in batch files: {request_id}")
            seen_request_ids.add(request_id)
            seen.add(task_id)
            batch_error = _batch_error_payload(line)
            _mark_task_failure(
                run_dir=run_dir,
                state=state,
                task_id=task_id,
                attempt=attempt,
                error=f"BatchError: {json.dumps(batch_error, sort_keys=True)}",
                raw=line,
            )

    for task_id in shard["task_ids"]:
        if task_id not in seen and state["tasks"][task_id].get("status") != "ACCEPTED":
            _mark_task_failure(
                run_dir=run_dir,
                state=state,
                task_id=task_id,
                attempt=int(shard["attempt"]),
                error=f"BatchTerminalWithoutResult: {shard.get('batch_status')}",
                raw=None,
            )
    shard["status"] = "INGESTED"
    shard["ingested_at_utc"] = _now_iso()


def _update_smoke_gate(run_dir: Path, plan: dict[str, Any], state: dict[str, Any]) -> None:
    smoke_ids = _smoke_task_ids(run_dir, plan)
    accepted = [
        task_id
        for task_id in smoke_ids
        if state["tasks"].get(task_id, {}).get("status") == "ACCEPTED"
    ]
    exhausted = [
        task_id
        for task_id in smoke_ids
        if state["tasks"].get(task_id, {}).get("status") == "FAILED"
        and int(state["tasks"][task_id].get("attempts") or 0)
        >= int(plan["max_attempts_per_task"])
    ]
    gate = {
        "status": (
            "PASS"
            if len(accepted) == len(smoke_ids)
            else ("FAIL" if exhausted else "PENDING")
        ),
        "checked_at_utc": _now_iso(),
        "planned": len(smoke_ids),
        "accepted": len(accepted),
        "exhausted_task_ids": exhausted,
    }
    _atomic_write_json(run_dir / "smoke_gate.json", gate)


def sync_official_run(
    run_dir: str | Path, *, transport: BatchTransport | None = None
) -> dict[str, Any]:
    root = Path(run_dir).expanduser().resolve()
    with _run_lock(root):
        plan = _read_json(root / "official_plan.json")
        state = _read_json(root / "state.json")
        client = _transport_or_default(transport)
        synced: list[str] = []
        for shard_id, shard in state["shards"].items():
            batch_id = shard.get("batch_id")
            if not batch_id or shard.get("status") == "INGESTED":
                continue
            batch = _retry_call(
                lambda batch_id=str(batch_id): client.retrieve_batch(batch_id),
                attempts=int(plan["transfer_retry_limit"]),
                backoff_seconds=float(plan["retry_backoff_seconds"]),
            )
            batch_status = str(batch.get("status") or "")
            shard["batch_status"] = batch_status
            shard["output_file_id"] = batch.get("output_file_id")
            shard["error_file_id"] = batch.get("error_file_id")
            shard["last_synced_at_utc"] = _now_iso()
            shard["batch_snapshot"] = batch
            if batch_status in TERMINAL_BATCH_STATUSES:
                if shard.get("output_file_id") and not shard.get("output_path"):
                    destination = root / "downloads" / f"{shard_id.replace(':', '_')}.output.jsonl"
                    downloaded = _download_remote_file(
                        run_dir=root,
                        plan=plan,
                        transport=client,
                        file_id=str(shard["output_file_id"]),
                        destination=destination,
                    )
                    shard["output_path"] = downloaded["path"]
                    shard["output_sha256"] = downloaded["sha256"]
                if shard.get("error_file_id") and not shard.get("error_path"):
                    destination = root / "downloads" / f"{shard_id.replace(':', '_')}.error.jsonl"
                    downloaded = _download_remote_file(
                        run_dir=root,
                        plan=plan,
                        transport=client,
                        file_id=str(shard["error_file_id"]),
                        destination=destination,
                    )
                    shard["error_path"] = downloaded["path"]
                    shard["error_sha256"] = downloaded["sha256"]
                _ingest_shard(run_dir=root, plan=plan, state=state, shard_id=shard_id)
            else:
                shard["status"] = "SUBMITTED"
            synced.append(shard_id)
            _save_state(root, state)
        _update_smoke_gate(root, plan, state)
        if all(task.get("status") == "ACCEPTED" for task in state["tasks"].values()):
            state["status"] = "READY_TO_FINALIZE"
        elif any(
            task.get("status") == "FAILED"
            and int(task.get("attempts") or 0) >= int(plan["max_attempts_per_task"])
            for task in state["tasks"].values()
        ):
            state["status"] = "PERMANENT_FAILURE"
        else:
            state["status"] = "RUNNING"
        _save_state(root, state)
        render_progress_dashboard(root)
        return {"synced_shards": synced, **official_status(root)}


def create_retry_shards(run_dir: str | Path) -> list[str]:
    root = Path(run_dir).expanduser().resolve()
    with _run_lock(root):
        plan = _read_json(root / "official_plan.json")
        state = _read_json(root / "state.json")
        tasks = {
            str(task["task_id"]): task
            for task in _read_jsonl(root / str(plan["task_registry"]))
        }
        existing_requests = {
            request_id
            for shard in state["shards"].values()
            for request_id in shard.get("request_ids") or []
        }
        retry_groups: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
        for task_id, task_state in state["tasks"].items():
            attempts = int(task_state.get("attempts") or 0)
            if task_state.get("status") != "FAILED" or attempts >= int(plan["max_attempts_per_task"]):
                continue
            attempt = attempts + 1
            request_id = f"{task_id}:a{attempt}"
            if request_id in existing_requests:
                continue
            task = tasks[task_id]
            phase = "smoke_retry" if task.get("phase") == "smoke" else "retry"
            key = (phase, str(task["condition"]), attempt)
            retry_groups.setdefault(key, []).append(task)

        created: list[str] = []
        for (phase, condition, attempt), group in retry_groups.items():
            for offset in range(0, len(group), int(plan["shard_size"])):
                chunk = group[offset : offset + int(plan["shard_size"])]
                number = (
                    1
                    + sum(
                        shard.get("phase") == phase
                        and shard.get("condition") == condition
                        and int(shard.get("attempt") or 0) == attempt
                        for shard in state["shards"].values()
                    )
                )
                shard_id = f"{phase}:{condition.lower()}:{number:03d}:a{attempt}"
                filename = f"{plan['run_id']}_{phase}_{condition.lower()}_{number:03d}_a{attempt}.jsonl"
                path = root / "batch_inputs" / filename
                requests = [
                    _task_request(root, task, plan, attempt) for task in chunk
                ]
                _write_jsonl(path, requests)
                state["shards"][shard_id] = {
                    "shard_id": shard_id,
                    "phase": phase,
                    "condition": condition,
                    "attempt": attempt,
                    "input_path": path.relative_to(root).as_posix(),
                    "input_filename": filename,
                    "input_sha256": _sha256_path(path),
                    "input_size_bytes": path.stat().st_size,
                    "request_count": len(requests),
                    "task_ids": [str(task["task_id"]) for task in chunk],
                    "request_ids": [str(request["custom_id"]) for request in requests],
                    "status": "LOCAL_READY",
                    "remote_file_id": None,
                    "batch_id": None,
                    "batch_status": None,
                    "output_file_id": None,
                    "error_file_id": None,
                    "output_path": None,
                    "error_path": None,
                    "last_synced_at_utc": None,
                }
                created.append(shard_id)
        if created:
            state["status"] = "RETRY_SHARDS_READY"
            _save_state(root, state)
            render_progress_dashboard(root)
        return created


def finalize_official_run(run_dir: str | Path) -> Path:
    root = Path(run_dir).expanduser().resolve()
    with _run_lock(root):
        plan = _read_json(root / "official_plan.json")
        state = _read_json(root / "state.json")
        incomplete = [
            task_id
            for task_id, task in state["tasks"].items()
            if task.get("status") != "ACCEPTED"
        ]
        if incomplete:
            raise RuntimeError(f"Cannot finalize with {len(incomplete)} incomplete tasks")
        records: list[dict[str, Any]] = []
        accepted_payloads: list[dict[str, Any]] = []
        for task_id, task_state in state["tasks"].items():
            checkpoint_path = root / str(task_state["checkpoint_path"])
            checkpoint = _read_json(checkpoint_path)
            records.append(dict(checkpoint["record"]))
            accepted_payloads.append(
                {
                    **dict(checkpoint["record"]),
                    "assessment": checkpoint["assessment"],
                }
            )
        records.sort(key=lambda row: int(row["execution_order"]))
        accepted_payloads.sort(key=lambda row: int(row["execution_order"]))
        recommendations = pd.DataFrame(records)
        analyst = pd.read_csv(
            root / "frozen_inputs" / "eligible_analyst_consensus.csv"
        )
        recommendations = attach_analyst_comparison(recommendations, analyst)
        result_dir = root / "results"
        _atomic_write_frame(result_dir / "recommendations.csv", recommendations)
        _write_jsonl(result_dir / "recommendations.jsonl", accepted_payloads)
        _atomic_write_frame(
            result_dir / "condition_changes.csv",
            condition_flip_summary(recommendations),
        )
        total_tokens = int(
            pd.to_numeric(recommendations.get("total_tokens"), errors="coerce")
            .fillna(0)
            .sum()
        )
        manifest = {
            "framework_version": OFFICIAL_FRAMEWORK_VERSION,
            "run_id": plan["run_id"],
            "plan_sha256": plan["plan_sha256"],
            "completed_at_utc": _now_iso(),
            "as_of_date": plan["as_of_date"],
            "backend": "openai_batch",
            "model": plan["model"],
            "prompt_version": plan["prompt_version"],
            "symbols": plan["eligible_symbols"],
            "excluded_symbols": plan["excluded_symbols"],
            "conditions": plan["conditions"],
            "repetitions": plan["repetitions"],
            "planned_assessments": plan["planned_assessments"],
            "completed_assessments": len(recommendations),
            "failed_assessments": 0,
            "total_tokens": total_tokens,
            "transport": "OpenAI Batch API /v1/responses",
            "store_responses": plan["store_responses"],
            "task_attempt_counts": {
                "one_attempt": sum(
                    int(task.get("attempts") or 0) == 1
                    for task in state["tasks"].values()
                ),
                "two_attempts": sum(
                    int(task.get("attempts") or 0) == 2
                    for task in state["tasks"].values()
                ),
            },
        }
        _atomic_write_json(result_dir / "manifest.json", manifest)
        state["status"] = "COMPLETE"
        state["completed_at_utc"] = manifest["completed_at_utc"]
        state["result_manifest"] = "results/manifest.json"
        _save_state(root, state)
        render_progress_dashboard(root)
        return result_dir


def advance_official_run(
    run_dir: str | Path, *, transport: BatchTransport | None = None
) -> dict[str, Any]:
    root = Path(run_dir).expanduser().resolve()
    client = _transport_or_default(transport)
    preflight_official_run(root, require_approval=True, write_report=True)
    sync_official_run(root, transport=client)
    plan = _read_json(root / "official_plan.json")
    state = _read_json(root / "state.json")
    if state.get("status") == "READY_TO_FINALIZE":
        finalize_official_run(root)
        return official_status(root)
    if state.get("status") == "PERMANENT_FAILURE":
        return official_status(root)

    smoke_ids = _smoke_task_ids(root, plan)
    if not _smoke_passed(root, plan, state):
        if any(state["tasks"][task_id].get("status") == "FAILED" for task_id in smoke_ids):
            created = create_retry_shards(root)
            if created:
                launch_official_phase(root, phase="smoke_retry", transport=client)
        else:
            launch_official_phase(root, phase="smoke", transport=client)
        return official_status(root)

    launch_official_phase(root, phase="full", transport=client)
    state = _read_json(root / "state.json")
    failed_non_smoke = [
        task_id
        for task_id, task in state["tasks"].items()
        if task_id not in smoke_ids and task.get("status") == "FAILED"
    ]
    if failed_non_smoke:
        created = create_retry_shards(root)
        if created:
            launch_official_phase(root, phase="retry", transport=client)
    return official_status(root)


def supervise_official_run(
    run_dir: str | Path,
    *,
    transport: BatchTransport | None = None,
    max_cycles: int | None = None,
) -> dict[str, Any]:
    root = Path(run_dir).expanduser().resolve()
    plan = _read_json(root / "official_plan.json")
    client = _transport_or_default(transport)
    cycles = 0
    while True:
        status = advance_official_run(root, transport=client)
        cycles += 1
        if status["status"] in {"COMPLETE", "PERMANENT_FAILURE"}:
            return status
        if max_cycles is not None and cycles >= max_cycles:
            return status
        time.sleep(int(plan["poll_interval_seconds"]))
