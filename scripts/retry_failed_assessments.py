#!/usr/bin/env python3
"""Retry only failed assessment cells from an immutable experiment run.

The original run is never modified. Each invocation creates a timestamped
subdirectory containing retry prompts, retry responses, an attempt log, and a
fully consolidated set of analysis artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from sp500_ai.analysis import (
    RATING_SCORE,
    attach_analyst_comparison,
    condition_flip_summary,
)
from sp500_ai.config import load_config
from sp500_ai.llm import (
    AssessmentResult,
    OpenAIResponsesClient,
    _audit_web_actions,
    _extract_urls,
)
from sp500_ai.pipeline import (
    _analyst_exposure_audit,
    _json_cell,
    _safe_error,
    _sha256,
)


REQUIRED_FAILURE_COLUMNS = [
    "run_id",
    "execution_order",
    "symbol",
    "condition",
    "repetition",
    "error",
    "failed_at_utc",
]
FAILURE_COLUMNS = [
    *REQUIRED_FAILURE_COLUMNS,
    "paid_response_returned",
    "usage_complete",
    "response_id",
    "model",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "web_search_call_count",
    "web_search_source_count",
    "retrieved_source_urls",
    "failed_response_raw_artifact",
]
RETRY_PROVENANCE_COLUMNS = [
    "original_run_id",
    "consolidated_run_id",
    "record_provenance",
    "retry_id",
    "retry_attempt",
    "original_failure_execution_order",
    "original_failure_error_type",
    "retry_max_output_tokens",
    "retry_attempted_at_utc",
]
ATTEMPT_COLUMNS = [
    "retry_id",
    "retry_attempt",
    "original_run_id",
    "original_execution_order",
    "symbol",
    "condition",
    "repetition",
    "prompt_sha256",
    "max_output_tokens",
    "web_search_enabled",
    "status",
    "attempted_at_utc",
    "response_id",
    "model",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "web_search_call_count",
    "web_search_source_count",
    "paid_response_returned",
    "usage_complete",
    "retrieved_source_urls",
    "original_error_type",
    "retry_error",
    "retry_error_artifact",
    "failed_response_raw_artifact",
    "retry_raw_artifact",
]
SAFE_SYMBOL = re.compile(r"^[A-Z0-9.-]+$")
SAFE_CONDITION = re.compile(r"^[A-Z][A-Z0-9_]+$")


class AssessmentClient(Protocol):
    def assess(
        self,
        prompt: str,
        allow_web: bool,
        condition: str | None = None,
    ) -> AssessmentResult: ...


@dataclass(frozen=True)
class FailedCell:
    original_run_id: str
    execution_order: int
    symbol: str
    condition: str
    repetition: int
    original_error_type: str
    original_failed_at_utc: str
    prompt_path: Path
    prompt: str
    identity: dict[str, Any]
    packet: dict[str, Any]

    @property
    def stem(self) -> str:
        return f"{self.symbol}_{self.condition}_r{self.repetition}"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp_id(moment: datetime) -> str:
    return moment.strftime("retry_%Y%m%dT%H%M%S%fZ")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _parse_json_after_marker(prompt: str, marker: str) -> dict[str, Any]:
    marker_index = prompt.find(marker)
    if marker_index < 0:
        raise ValueError(f"Archived prompt is missing marker: {marker.strip()}")
    remainder = prompt[marker_index + len(marker) :].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(remainder)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Archived prompt contains invalid JSON after {marker.strip()}"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object after {marker.strip()}")
    return value


def _error_type(value: Any) -> str:
    text = str(value or "")
    prefix = text.split(":", 1)[0].strip()
    return prefix if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", prefix) else "UNKNOWN"


def _positive_int(value: Any, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if result < 1:
        raise ValueError(f"{field} must be positive")
    return result


def _read_manifest(original_run: Path) -> dict[str, Any]:
    path = original_run / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"Missing original manifest: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Original manifest must be a JSON object")
    if str(value.get("backend") or "").lower() != "openai":
        raise ValueError("Targeted API retry requires an original openai run")
    return value


def load_failed_cells(
    original_run: str | Path,
    manifest: dict[str, Any],
    *,
    max_failed_cells: int = 10,
) -> list[FailedCell]:
    """Load and preflight the exact failed cells and their archived prompts."""

    run_dir = Path(original_run).expanduser().resolve()
    errors_path = run_dir / "assessment_errors.csv"
    if not errors_path.is_file():
        raise FileNotFoundError(f"Missing assessment error table: {errors_path}")
    errors = pd.read_csv(errors_path, dtype=str, keep_default_na=False)
    missing_columns = set(REQUIRED_FAILURE_COLUMNS) - set(errors.columns)
    if missing_columns:
        raise ValueError(
            f"assessment_errors.csv is missing columns: {sorted(missing_columns)}"
        )
    if errors.empty:
        raise ValueError("assessment_errors.csv contains no failed cells")
    if len(errors) > max_failed_cells:
        raise ValueError(
            f"Refusing to retry {len(errors)} cells; "
            f"increase --max-failed-cells above {max_failed_cells} explicitly"
        )

    prompts_dir = (run_dir / "prompts").resolve()
    original_run_id = str(manifest.get("run_id") or "")
    as_of_date = str(manifest.get("as_of_date") or "")
    configured_conditions = {
        str(value) for value in manifest.get("conditions", []) if value
    }
    cells: list[FailedCell] = []
    seen: set[tuple[str, str, int]] = set()
    for raw in errors.to_dict(orient="records"):
        symbol = str(raw["symbol"]).strip().upper()
        condition = str(raw["condition"]).strip()
        repetition = _positive_int(raw["repetition"], "repetition")
        execution_order = _positive_int(raw["execution_order"], "execution_order")
        if not SAFE_SYMBOL.fullmatch(symbol):
            raise ValueError(f"Unsafe symbol in failure table: {symbol!r}")
        if not SAFE_CONDITION.fullmatch(condition):
            raise ValueError(f"Unsafe condition in failure table: {condition!r}")
        if configured_conditions and condition not in configured_conditions:
            raise ValueError(f"Failure condition is not in the manifest: {condition}")
        row_run_id = str(raw["run_id"]).strip()
        if original_run_id and row_run_id != original_run_id:
            raise ValueError(
                f"Failure run_id {row_run_id!r} does not match manifest "
                f"{original_run_id!r}"
            )
        key = (symbol, condition, repetition)
        if key in seen:
            raise ValueError(f"Duplicate failed cell in assessment_errors.csv: {key}")
        seen.add(key)

        prompt_path = (prompts_dir / f"{symbol}_{condition}_r{repetition}.txt").resolve()
        if prompt_path.parent != prompts_dir or not prompt_path.is_file():
            raise FileNotFoundError(
                f"Missing exact archived prompt for failed cell: {prompt_path}"
            )
        prompt = prompt_path.read_text(encoding="utf-8")
        identity = _parse_json_after_marker(prompt, "COMPANY IDENTITY\n")
        packet = _parse_json_after_marker(prompt, "CONDITION DATA PACKET\n")
        expected_identity = {
            "symbol": symbol,
            "as_of_date": as_of_date,
        }
        mismatches = {
            field: {"expected": expected, "received": identity.get(field)}
            for field, expected in expected_identity.items()
            if expected and identity.get(field) != expected
        }
        if mismatches:
            raise ValueError(
                f"Archived prompt identity does not match failure metadata: {mismatches}"
            )
        _positive_int(identity.get("horizon_months"), "horizon_months")
        cells.append(
            FailedCell(
                original_run_id=original_run_id or row_run_id,
                execution_order=execution_order,
                symbol=symbol,
                condition=condition,
                repetition=repetition,
                original_error_type=_error_type(raw["error"]),
                original_failed_at_utc=str(raw["failed_at_utc"]),
                prompt_path=prompt_path,
                prompt=prompt,
                identity=identity,
                packet=packet,
            )
        )
    return sorted(cells, key=lambda cell: cell.execution_order)


def _assessment_payload_from_jsonl(
    path: Path,
) -> tuple[
    dict[tuple[str, str, int], dict[str, Any]],
    dict[tuple[str, str, int], dict[str, Any]],
]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing original recommendations JSONL: {path}")
    payloads: dict[tuple[str, str, int], dict[str, Any]] = {}
    records: dict[tuple[str, str, int], dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Original recommendations.jsonl line {line_number} is invalid"
                ) from exc
            assessment = value.get("assessment")
            if not isinstance(value, dict) or not isinstance(assessment, dict):
                raise ValueError(
                    f"Original recommendations.jsonl line {line_number} "
                    "must contain an assessment object"
                )
            key = (
                str(value.get("symbol") or "").upper(),
                str(value.get("condition") or ""),
                _positive_int(value.get("repetition"), "repetition"),
            )
            if key in records:
                raise ValueError(f"Duplicate assessment key in original JSONL: {key}")
            records[key] = value
            payloads[key] = assessment
    return payloads, records


def _coerce_bool_column(frame: pd.DataFrame, column: str) -> None:
    if column not in frame:
        return

    def convert(value: Any) -> Any:
        if pd.isna(value):
            return value
        if isinstance(value, bool):
            return value
        lowered = str(value).strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
        return value

    frame[column] = frame[column].map(convert)


def _recompute_original_audits(
    original_run: Path,
    frame: pd.DataFrame,
    payloads: dict[tuple[str, str, int], dict[str, Any]],
) -> None:
    for index, row in frame.iterrows():
        key = (
            str(row["symbol"]).upper(),
            str(row["condition"]),
            int(row["repetition"]),
        )
        payload = payloads.get(key)
        if payload is None:
            raise ValueError(f"Original CSV row is missing from JSONL: {key}")
        frame.at[index, "analyst_terms_in_final_output"] = (
            _analyst_exposure_audit(payload, key[1])
        )
        raw_path = (
            original_run
            / "raw_responses"
            / f"{key[0]}_{key[1]}_r{key[2]}.json"
        )
        if not raw_path.is_file():
            raise FileNotFoundError(
                f"Missing successful raw response needed for audit: {raw_path}"
            )
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        audit = _audit_web_actions(raw)
        frame.at[index, "analyst_terms_detected_in_web_actions"] = audit[
            "analyst_terms_detected_in_web_actions"
        ]
        frame.at[index, "analyst_web_action_matched_terms"] = _json_cell(
            audit["analyst_web_action_matched_terms"]
        )
        frame.at[index, "web_search_call_count"] = audit["web_search_call_count"]
        frame.at[index, "web_search_source_count"] = audit[
            "web_search_source_count"
        ]


def _validate_retry_payload(cell: FailedCell, payload: dict[str, Any]) -> None:
    expected = {
        "symbol": cell.symbol,
        "as_of": cell.identity["as_of_date"],
        "horizon_months": int(cell.identity["horizon_months"]),
    }
    mismatches = {
        field: {"expected": value, "received": payload.get(field)}
        for field, value in expected.items()
        if payload.get(field) != value
    }
    if mismatches:
        raise ValueError(
            f"Retry assessment identity fields do not match archived prompt: {mismatches}"
        )


def _retry_record(
    cell: FailedCell,
    result: AssessmentResult,
    *,
    consolidated_run_id: str,
    retry_id: str,
    max_output_tokens: int,
    attempted_at_utc: str,
) -> dict[str, Any]:
    payload = result.assessment
    _validate_retry_payload(cell, payload)
    metadata = result.metadata
    return {
        "run_id": consolidated_run_id,
        "original_run_id": cell.original_run_id,
        "consolidated_run_id": consolidated_run_id,
        "record_provenance": "TARGETED_RETRY_SUCCESS",
        "retry_id": retry_id,
        "retry_attempt": 1,
        "original_failure_execution_order": cell.execution_order,
        "original_failure_error_type": cell.original_error_type,
        "retry_max_output_tokens": max_output_tokens,
        "retry_attempted_at_utc": attempted_at_utc,
        "execution_order": cell.execution_order,
        "assessment_created_at_utc": attempted_at_utc,
        "symbol": cell.symbol,
        "company": str(cell.identity.get("company") or cell.symbol),
        "sector": str(cell.identity.get("sector") or ""),
        "condition": cell.condition,
        "repetition": cell.repetition,
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
        "analyst_opinions_encountered": payload[
            "analyst_opinions_encountered"
        ],
        "analyst_opinions_used": payload["analyst_opinions_used"],
        "analyst_terms_in_final_output": _analyst_exposure_audit(
            payload, cell.condition
        ),
        "analyst_terms_detected_in_web_actions": metadata.get(
            "analyst_terms_detected_in_web_actions"
        ),
        "analyst_web_action_matched_terms": _json_cell(
            metadata.get("analyst_web_action_matched_terms", [])
        ),
        "web_search_call_count": metadata.get("web_search_call_count", 0),
        "web_search_source_count": metadata.get("web_search_source_count", 0),
        "limitations": _json_cell(payload["limitations"]),
        "backend": metadata["backend"],
        "model": metadata.get("model"),
        "requested_model": metadata.get("requested_model"),
        "response_id": metadata.get("response_id"),
        "prompt_version": str(cell.identity.get("prompt_version") or ""),
        "prompt_sha256": hashlib.sha256(
            cell.prompt.encode("utf-8")
        ).hexdigest(),
        "packet_sha256": _sha256(cell.packet),
        "web_search_enabled": metadata["web_search_enabled"],
        "retrieved_source_urls": _json_cell(
            metadata.get("retrieved_source_urls", [])
        ),
        "input_tokens": metadata.get("input_tokens"),
        "output_tokens": metadata.get("output_tokens"),
        "total_tokens": metadata.get("total_tokens"),
    }


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None or value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text:
        return None
    try:
        result = int(float(text))
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _failure_metadata(
    error: Exception,
    result: AssessmentResult | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    raw = getattr(error, "raw", None)
    metadata = getattr(error, "metadata", None)
    if not isinstance(raw, dict) and result is not None:
        raw = result.raw
    if not isinstance(metadata, dict) and result is not None:
        metadata = result.metadata
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    if isinstance(raw, dict):
        usage = raw.get("usage") or {}
        audit = _audit_web_actions(raw)
        derived = {
            "response_id": raw.get("id"),
            "model": raw.get("model"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "retrieved_source_urls": _extract_urls(raw),
            **audit,
        }
        for field, value in derived.items():
            if metadata.get(field) is None:
                metadata[field] = value
    return raw if isinstance(raw, dict) else None, metadata


def _usage_is_complete(metadata: dict[str, Any]) -> bool:
    return all(
        _optional_nonnegative_int(metadata.get(field)) is not None
        for field in (
            "input_tokens",
            "output_tokens",
            "web_search_call_count",
        )
    )


def _failed_usage_summary(
    rows: list[dict[str, Any]],
    *,
    expected_failed_calls: int | None = None,
) -> dict[str, int]:
    failed_calls = max(len(rows), int(expected_failed_calls or 0))
    complete_calls = 0
    any_usage_calls = 0
    response_payloads_archived = 0
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    web_search_calls = 0
    for row in rows:
        input_value = _optional_nonnegative_int(row.get("input_tokens"))
        output_value = _optional_nonnegative_int(row.get("output_tokens"))
        total_value = _optional_nonnegative_int(row.get("total_tokens"))
        web_value = _optional_nonnegative_int(row.get("web_search_call_count"))
        if any(
            value is not None
            for value in (input_value, output_value, total_value, web_value)
        ):
            any_usage_calls += 1
        if input_value is not None and output_value is not None and web_value is not None:
            complete_calls += 1
        input_tokens += input_value or 0
        output_tokens += output_value or 0
        total_tokens += (
            total_value
            if total_value is not None
            else (input_value or 0) + (output_value or 0)
        )
        web_search_calls += web_value or 0
        if row.get("failed_response_raw_artifact") or row.get(
            "failed_raw_artifact"
        ):
            response_payloads_archived += 1
    return {
        "failed_calls": failed_calls,
        "calls_with_any_usage": any_usage_calls,
        "calls_with_usage": complete_calls,
        "unknown_paid_failure_usage_count": max(0, failed_calls - complete_calls),
        "response_payloads_archived": response_payloads_archived,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "web_search_calls": web_search_calls,
    }


def _combine_failed_usage(*parts: dict[str, int]) -> dict[str, int]:
    fields = {
        "failed_calls",
        "calls_with_any_usage",
        "calls_with_usage",
        "unknown_paid_failure_usage_count",
        "response_payloads_archived",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "web_search_calls",
    }
    return {
        field: sum(int(part.get(field, 0)) for part in parts)
        for field in sorted(fields)
    }


def _attempt_row(
    cell: FailedCell,
    *,
    retry_id: str,
    max_output_tokens: int,
    attempted_at_utc: str,
    status: str,
    result: AssessmentResult | None = None,
    error: Exception | None = None,
    failure_metadata: dict[str, Any] | None = None,
    failed_response_raw_artifact: str | None = None,
    retry_error_artifact: str | None = None,
) -> dict[str, Any]:
    metadata = result.metadata if result else (failure_metadata or {})
    paid_response_returned = bool(
        result is not None or failed_response_raw_artifact
    )
    usage_complete = _usage_is_complete(metadata)
    return {
        "retry_id": retry_id,
        "retry_attempt": 1,
        "original_run_id": cell.original_run_id,
        "original_execution_order": cell.execution_order,
        "symbol": cell.symbol,
        "condition": cell.condition,
        "repetition": cell.repetition,
        "prompt_sha256": hashlib.sha256(
            cell.prompt.encode("utf-8")
        ).hexdigest(),
        "max_output_tokens": max_output_tokens,
        "web_search_enabled": cell.condition == "D_CURRENT_WEB",
        "status": status,
        "attempted_at_utc": attempted_at_utc,
        "response_id": metadata.get("response_id"),
        "model": metadata.get("model"),
        "input_tokens": metadata.get("input_tokens"),
        "output_tokens": metadata.get("output_tokens"),
        "total_tokens": metadata.get("total_tokens"),
        "web_search_call_count": metadata.get("web_search_call_count"),
        "web_search_source_count": metadata.get("web_search_source_count"),
        "paid_response_returned": paid_response_returned,
        "usage_complete": usage_complete,
        "retrieved_source_urls": _json_cell(
            metadata.get("retrieved_source_urls", [])
        ),
        "original_error_type": cell.original_error_type,
        "retry_error": _safe_error(error) if error else None,
        "retry_error_artifact": retry_error_artifact,
        "failed_response_raw_artifact": failed_response_raw_artifact,
        "retry_raw_artifact": (
            f"raw_responses/{cell.stem}.json"
            if result
            else (
                failed_response_raw_artifact
                or retry_error_artifact
                or f"raw_responses/{cell.stem}.error.json"
            )
        ),
    }


def _write_attempts(path: Path, attempts: list[dict[str, Any]]) -> None:
    pd.DataFrame(attempts, columns=ATTEMPT_COLUMNS).to_csv(path, index=False)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def _ordered_columns(
    original_columns: list[str], consolidated: pd.DataFrame
) -> list[str]:
    leading = ["run_id", *RETRY_PROVENANCE_COLUMNS]
    result: list[str] = []
    for column in [*leading, *original_columns, *consolidated.columns]:
        if column in consolidated.columns and column not in result:
            result.append(column)
    return result


def _copy_summary_inputs(original_run: Path, retry_dir: Path) -> None:
    for name in (
        "analyst_consensus.csv",
        "acquisition_gate.json",
        "data_quality.csv",
        "experiment_universe.csv",
    ):
        source = original_run / name
        if source.is_file():
            shutil.copy2(source, retry_dir / name)


def execute_targeted_retry(
    original_run: str | Path,
    client: AssessmentClient,
    *,
    max_output_tokens: int = 6000,
    max_failed_cells: int = 10,
    output_root: str | Path | None = None,
    retry_id: str | None = None,
    started_at: datetime | None = None,
) -> Path:
    """Execute one retry attempt for every failed cell and consolidate outputs."""

    if max_output_tokens < 1:
        raise ValueError("max_output_tokens must be positive")
    run_dir = Path(original_run).expanduser().resolve()
    manifest = _read_manifest(run_dir)
    cells = load_failed_cells(
        run_dir, manifest, max_failed_cells=max_failed_cells
    )
    original_error_frame = pd.read_csv(
        run_dir / "assessment_errors.csv",
        dtype=str,
        keep_default_na=False,
    )
    original_error_rows = original_error_frame.to_dict(orient="records")

    original_csv_path = run_dir / "recommendations.csv"
    original_jsonl_path = run_dir / "recommendations.jsonl"
    consensus_path = run_dir / "analyst_consensus.csv"
    if not original_csv_path.is_file():
        raise FileNotFoundError(
            f"Missing original recommendations CSV: {original_csv_path}"
        )
    if not consensus_path.is_file():
        raise FileNotFoundError(
            f"Missing original analyst consensus: {consensus_path}"
        )
    original_frame = pd.read_csv(original_csv_path)
    original_payloads, original_json_records = _assessment_payload_from_jsonl(
        original_jsonl_path
    )
    original_keys = {
        (str(row.symbol).upper(), str(row.condition), int(row.repetition))
        for row in original_frame.itertuples()
    }
    overlap = [
        (cell.symbol, cell.condition, cell.repetition)
        for cell in cells
        if (cell.symbol, cell.condition, cell.repetition) in original_keys
    ]
    if overlap:
        raise ValueError(
            f"Failed cells already exist in original recommendations: {overlap}"
        )
    if len(original_payloads) != len(original_frame):
        raise ValueError(
            "Original recommendations CSV and JSONL have different row counts"
        )
    _recompute_original_audits(run_dir, original_frame, original_payloads)

    start = started_at or _now_utc()
    retry_id = retry_id or _timestamp_id(start)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", retry_id):
        raise ValueError("retry_id may contain only letters, numbers, dot, dash, underscore")
    consolidated_run_id = (
        f"{manifest.get('run_id') or run_dir.name}__{retry_id}"
    )
    retry_parent = (
        Path(output_root).expanduser().resolve()
        if output_root
        else run_dir / "retries"
    )
    retry_dir = retry_parent / retry_id
    retry_dir.mkdir(parents=True, exist_ok=False)
    (retry_dir / "prompts").mkdir()
    (retry_dir / "raw_responses").mkdir()

    shutil.copy2(
        run_dir / "assessment_errors.csv",
        retry_dir / "original_assessment_errors.csv",
    )
    _copy_summary_inputs(run_dir, retry_dir)

    original_frame.insert(0, "original_run_id", original_frame["run_id"])
    original_frame["run_id"] = consolidated_run_id
    original_frame["consolidated_run_id"] = consolidated_run_id
    original_frame["record_provenance"] = "ORIGINAL_SUCCESS"
    original_frame["retry_id"] = retry_id
    original_frame["retry_attempt"] = 0
    original_frame["original_failure_execution_order"] = pd.NA
    original_frame["original_failure_error_type"] = pd.NA
    original_frame["retry_max_output_tokens"] = pd.NA
    original_frame["retry_attempted_at_utc"] = pd.NA
    for column in (
        "analyst_opinions_used",
        "forced_rating_due_to_insufficient_data",
    ):
        _coerce_bool_column(original_frame, column)

    attempts: list[dict[str, Any]] = []
    retry_records: list[dict[str, Any]] = []
    retry_payloads: dict[tuple[str, str, int], dict[str, Any]] = {}
    unresolved: list[dict[str, Any]] = []
    for cell in cells:
        retry_prompt_path = retry_dir / "prompts" / f"{cell.stem}.txt"
        retry_prompt_path.write_text(cell.prompt, encoding="utf-8")
        attempted_at = _now_utc().isoformat()
        result: AssessmentResult | None = None
        try:
            result = client.assess(
                cell.prompt,
                allow_web=cell.condition == "D_CURRENT_WEB",
                condition=cell.condition,
            )
            record = _retry_record(
                cell,
                result,
                consolidated_run_id=consolidated_run_id,
                retry_id=retry_id,
                max_output_tokens=max_output_tokens,
                attempted_at_utc=attempted_at,
            )
            retry_records.append(record)
            retry_payloads[
                (cell.symbol, cell.condition, cell.repetition)
            ] = result.assessment
            _write_json(
                retry_dir / "raw_responses" / f"{cell.stem}.json",
                result.raw,
            )
            attempts.append(
                _attempt_row(
                    cell,
                    retry_id=retry_id,
                    max_output_tokens=max_output_tokens,
                    attempted_at_utc=attempted_at,
                    status="SUCCESS",
                    result=result,
                )
            )
        except Exception as exc:
            safe_error = _safe_error(exc)
            failure_raw, failure_metadata = _failure_metadata(exc, result)
            failed_response_raw_artifact = None
            if failure_raw is not None:
                failed_response_raw_artifact = (
                    f"raw_responses/{cell.stem}.failed_response.json"
                )
                _write_json(
                    retry_dir / failed_response_raw_artifact,
                    failure_raw,
                )
            retry_error_artifact = f"raw_responses/{cell.stem}.error.json"
            retry_error = {
                "run_id": consolidated_run_id,
                "execution_order": cell.execution_order,
                "symbol": cell.symbol,
                "condition": cell.condition,
                "repetition": cell.repetition,
                "error": safe_error,
                "failed_at_utc": attempted_at,
                "paid_response_returned": failure_raw is not None,
                "usage_complete": _usage_is_complete(failure_metadata),
                "response_id": failure_metadata.get("response_id"),
                "model": failure_metadata.get("model"),
                "input_tokens": failure_metadata.get("input_tokens"),
                "output_tokens": failure_metadata.get("output_tokens"),
                "total_tokens": failure_metadata.get("total_tokens"),
                "web_search_call_count": failure_metadata.get(
                    "web_search_call_count"
                ),
                "web_search_source_count": failure_metadata.get(
                    "web_search_source_count"
                ),
                "retrieved_source_urls": _json_cell(
                    failure_metadata.get("retrieved_source_urls", [])
                ),
                "failed_response_raw_artifact": failed_response_raw_artifact,
            }
            unresolved.append(retry_error)
            _write_json(
                retry_dir / retry_error_artifact,
                retry_error,
            )
            attempts.append(
                _attempt_row(
                    cell,
                    retry_id=retry_id,
                    max_output_tokens=max_output_tokens,
                    attempted_at_utc=attempted_at,
                    status="FAILED",
                    error=exc,
                    failure_metadata=failure_metadata,
                    failed_response_raw_artifact=failed_response_raw_artifact,
                    retry_error_artifact=retry_error_artifact,
                )
            )
        _write_attempts(retry_dir / "retry_attempts.csv", attempts)

    retry_frame = pd.DataFrame(retry_records)
    combined = pd.concat([original_frame, retry_frame], ignore_index=True, sort=False)
    for column in (
        "analyst_opinions_used",
        "forced_rating_due_to_insufficient_data",
    ):
        _coerce_bool_column(combined, column)
    comparison_columns = [
        "count_normalized_score",
        "count_normalized_rating",
        "portal_rating_yahoo",
        "ordinal_gap_vs_count_normalized_analyst",
        "exact_match_count_normalized_analyst",
        "ordinal_gap_vs_portal_yahoo",
        "exact_match_portal_yahoo",
        "rating_differs_from_portal_without_reported_analyst_use_and_sufficient_evidence",
        "rating_differs_from_count_normalized_without_reported_analyst_use_and_sufficient_evidence",
    ]
    combined = combined.drop(
        columns=[column for column in comparison_columns if column in combined],
        errors="ignore",
    )
    consensus = pd.read_csv(consensus_path)
    consolidated = attach_analyst_comparison(combined, consensus)
    consolidated = consolidated.sort_values(
        ["execution_order", "symbol", "condition", "repetition"],
        kind="stable",
    ).reset_index(drop=True)
    columns = _ordered_columns(list(original_frame.columns), consolidated)
    consolidated.to_csv(
        retry_dir / "recommendations.csv", columns=columns, index=False
    )
    condition_flip_summary(consolidated).to_csv(
        retry_dir / "condition_changes.csv", index=False
    )
    pd.DataFrame(unresolved, columns=FAILURE_COLUMNS).to_csv(
        retry_dir / "assessment_errors.csv", index=False
    )

    payloads = {**original_payloads, **retry_payloads}
    with (retry_dir / "recommendations.jsonl").open(
        "w", encoding="utf-8"
    ) as handle:
        for row in consolidated.to_dict(orient="records"):
            key = (
                str(row["symbol"]).upper(),
                str(row["condition"]),
                int(row["repetition"]),
            )
            payload = payloads.get(key)
            if payload is None:
                raise ValueError(
                    f"Consolidated record is missing assessment payload: {key}"
                )
            value = _json_safe({**row, "assessment": payload})
            handle.write(
                json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n"
            )

    original_failed_count = max(
        int(manifest.get("failed_assessments") or 0),
        len(cells),
        len(original_error_rows),
    )
    original_failed_usage = _failed_usage_summary(
        original_error_rows,
        expected_failed_calls=original_failed_count,
    )
    retry_failed_usage = _failed_usage_summary(
        unresolved,
        expected_failed_calls=len(unresolved),
    )
    failed_call_usage = _combine_failed_usage(
        original_failed_usage,
        retry_failed_usage,
    )
    unknown_paid_failure_usage = failed_call_usage[
        "unknown_paid_failure_usage_count"
    ]
    original_known_tokens = int(
        pd.to_numeric(original_frame.get("total_tokens"), errors="coerce")
        .fillna(0)
        .sum()
    )
    retry_known_tokens = int(
        pd.to_numeric(retry_frame.get("total_tokens"), errors="coerce")
        .fillna(0)
        .sum()
    ) if not retry_frame.empty else 0
    completed_count = len(consolidated)
    planned_count = int(manifest.get("planned_assessments") or completed_count)
    consolidated_manifest = {
        key: value
        for key, value in manifest.items()
        if key not in {"key_status", "run_id", "created_at_utc", "total_tokens"}
    }
    consolidated_manifest.update(
        {
            "run_id": consolidated_run_id,
            "original_run_id": manifest.get("run_id"),
            "retry_id": retry_id,
            "run_started_at_utc": start.isoformat(),
            "created_at_utc": _now_utc().isoformat(),
            "consolidated_from_run_directory": run_dir.name,
            "record_provenance_values": [
                "ORIGINAL_SUCCESS",
                "TARGETED_RETRY_SUCCESS",
            ],
            "planned_assessments": planned_count,
            "completed_assessments": completed_count,
            "failed_assessments": len(unresolved),
            "not_attempted_assessments": max(
                0, planned_count - completed_count - len(unresolved)
            ),
            "original_successful_assessments": len(original_frame),
            "targeted_retry_requested": len(cells),
            "targeted_retry_succeeded": len(retry_records),
            "targeted_retry_failed": len(unresolved),
            "retry_max_output_tokens": max_output_tokens,
            "total_tokens": original_known_tokens + retry_known_tokens,
            "original_known_success_tokens": original_known_tokens,
            "retry_known_success_tokens": retry_known_tokens,
            "failed_response_input_tokens": failed_call_usage[
                "input_tokens"
            ],
            "failed_response_output_tokens": failed_call_usage[
                "output_tokens"
            ],
            "failed_response_total_tokens": failed_call_usage[
                "total_tokens"
            ],
            "failed_response_web_search_calls": failed_call_usage[
                "web_search_calls"
            ],
            "recorded_total_tokens_including_failures": (
                original_known_tokens
                + retry_known_tokens
                + failed_call_usage["total_tokens"]
            ),
            "failed_call_usage": failed_call_usage,
            "original_failed_call_usage": original_failed_usage,
            "retry_failed_call_usage": retry_failed_usage,
            "known_original_failed_call_usage_count": original_failed_usage[
                "calls_with_usage"
            ],
            "known_retry_failed_call_usage_count": retry_failed_usage[
                "calls_with_usage"
            ],
            "unknown_paid_failure_usage_count": unknown_paid_failure_usage,
            "unknown_original_failed_call_usage_count": original_failed_usage[
                "unknown_paid_failure_usage_count"
            ],
            "unknown_retry_failed_call_usage_count": retry_failed_usage[
                "unknown_paid_failure_usage_count"
            ],
            "cost_interpretation": (
                (
                    "Known token and web usage include every successful response "
                    "and every failed response with archived usage; cost remains "
                    "a lower bound where failed-call usage is unavailable."
                )
                if unknown_paid_failure_usage
                else (
                    "Known token and web usage include successful and failed "
                    "responses; recorded usage is complete for all paid attempts."
                )
            ),
            "original_artifacts_overwritten": False,
            "source_artifact_sha256": {
                "manifest.json": _sha256_path(run_dir / "manifest.json"),
                "assessment_errors.csv": _sha256_path(
                    run_dir / "assessment_errors.csv"
                ),
                "recommendations.csv": _sha256_path(original_csv_path),
                "recommendations.jsonl": _sha256_path(original_jsonl_path),
                "analyst_consensus.csv": _sha256_path(consensus_path),
            },
            "retried_prompt_sha256": {
                cell.stem: hashlib.sha256(
                    cell.prompt.encode("utf-8")
                ).hexdigest()
                for cell in cells
            },
        }
    )
    _write_json(retry_dir / "manifest.json", consolidated_manifest)
    _write_json(
        retry_dir / "retry_manifest.json",
        {
            "retry_id": retry_id,
            "consolidated_run_id": consolidated_run_id,
            "original_run_id": manifest.get("run_id"),
            "original_run_directory": run_dir.name,
            "started_at_utc": start.isoformat(),
            "completed_at_utc": _now_utc().isoformat(),
            "max_output_tokens": max_output_tokens,
            "requested_cells": [
                {
                    "symbol": cell.symbol,
                    "condition": cell.condition,
                    "repetition": cell.repetition,
                    "original_execution_order": cell.execution_order,
                    "original_error_type": cell.original_error_type,
                    "prompt_sha256": hashlib.sha256(
                        cell.prompt.encode("utf-8")
                    ).hexdigest(),
                }
                for cell in cells
            ],
            "successful_cells": len(retry_records),
            "failed_cells": len(unresolved),
            "original_artifacts_overwritten": False,
            "failed_call_usage": failed_call_usage,
            "original_failed_call_usage": original_failed_usage,
            "retry_failed_call_usage": retry_failed_usage,
            "unknown_paid_failure_usage_count": unknown_paid_failure_usage,
        },
    )
    return retry_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Retry only cells listed in assessment_errors.csv using their exact "
            "archived prompts. Original run artifacts are never overwritten."
        )
    )
    parser.add_argument("original_run_directory", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--max-output-tokens", type=int, default=6000)
    parser.add_argument("--max-failed-cells", type=int, default=10)
    parser.add_argument(
        "--output-root",
        type=Path,
        help="Default: <original-run>/retries",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Validate and list failed cells without loading credentials or calling the API.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        run_dir = args.original_run_directory.expanduser().resolve()
        manifest = _read_manifest(run_dir)
        cells = load_failed_cells(
            run_dir,
            manifest,
            max_failed_cells=args.max_failed_cells,
        )
        if args.plan_only:
            plan = {
                "original_run_id": manifest.get("run_id"),
                "failed_cell_count": len(cells),
                "max_output_tokens": args.max_output_tokens,
                "cells": [
                    {
                        "symbol": cell.symbol,
                        "condition": cell.condition,
                        "repetition": cell.repetition,
                        "original_error_type": cell.original_error_type,
                        "prompt_sha256": hashlib.sha256(
                            cell.prompt.encode("utf-8")
                        ).hexdigest(),
                    }
                    for cell in cells
                ],
                "api_called": False,
            }
            print(json.dumps(plan, indent=2, sort_keys=True))
            return

        config = load_config(args.config)
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is unavailable after load_config")
        model = str(manifest.get("model") or config.model)
        client = OpenAIResponsesClient(
            api_key=api_key,
            model=model,
            reasoning_effort=str(config.reasoning_effort),
            max_output_tokens=args.max_output_tokens,
        )
        retry_dir = execute_targeted_retry(
            run_dir,
            client,
            max_output_tokens=args.max_output_tokens,
            max_failed_cells=args.max_failed_cells,
            output_root=args.output_root,
        )
        print(f"Retry artifacts created: {retry_dir}")
    except Exception as exc:
        raise SystemExit(_safe_error(exc)) from None


if __name__ == "__main__":
    main()
