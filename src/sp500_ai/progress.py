from __future__ import annotations

import html
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "Awaiting first processed result"
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _atomic_write(path: Path, value: bytes) -> None:
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


def build_progress_snapshot(
    run_dir: str | Path, *, observed_at: datetime | None = None
) -> dict[str, Any]:
    root = Path(run_dir).expanduser().resolve()
    plan = _read_object(root / "official_plan.json")
    state = _read_object(root / "state.json")
    moment = (observed_at or _now()).astimezone(timezone.utc)
    tasks = state.get("tasks") or {}
    shards = state.get("shards") or {}
    task_counts = Counter(str(task.get("status") or "UNKNOWN") for task in tasks.values())
    planned = int(plan.get("planned_assessments") or len(tasks))
    accepted = int(task_counts.get("ACCEPTED", 0))
    failed = int(task_counts.get("FAILED", 0))
    base_pending = max(0, planned - accepted - failed)

    submitted_times = [
        parsed
        for shard in shards.values()
        if (parsed := _parse_time(shard.get("submitted_at_utc"))) is not None
    ]
    started_at = min(submitted_times) if submitted_times else None
    elapsed_seconds = (
        max(0.0, (moment - started_at).total_seconds()) if started_at else 0.0
    )

    remote_processed_uningested = 0
    active_retry_task_ids: set[str] = set()
    active_retry_submitted_times: list[datetime] = []
    batch_rows: list[dict[str, Any]] = []
    expiry_times: list[datetime] = []
    for shard_id, shard in shards.items():
        snapshot = shard.get("batch_snapshot") or {}
        counts = snapshot.get("request_counts") or {}
        remote_completed = int(counts.get("completed") or 0)
        remote_failed = int(counts.get("failed") or 0)
        remote_total = int(counts.get("total") or shard.get("request_count") or 0)
        if shard.get("status") != "INGESTED":
            remote_processed_uningested += remote_completed + remote_failed
        if (
            shard.get("phase") in {"retry", "smoke_retry"}
            and shard.get("status") != "INGESTED"
            and shard.get("batch_id")
        ):
            active_retry_task_ids.update(str(value) for value in shard.get("task_ids") or [])
            submitted = _parse_time(shard.get("submitted_at_utc"))
            if submitted is not None:
                active_retry_submitted_times.append(submitted)
        expires_at = None
        if snapshot.get("expires_at"):
            try:
                expires_at = datetime.fromtimestamp(
                    float(snapshot["expires_at"]), tz=timezone.utc
                )
            except (TypeError, ValueError, OSError):
                expires_at = None
        if expires_at is None:
            submitted = _parse_time(shard.get("submitted_at_utc"))
            if submitted is not None:
                expires_at = submitted + timedelta(hours=24)
        if expires_at is not None and shard.get("status") != "INGESTED":
            expiry_times.append(expires_at)
        batch_rows.append(
            {
                "shard_id": shard_id,
                "phase": shard.get("phase"),
                "condition": shard.get("condition"),
                "attempt": shard.get("attempt"),
                "requests": int(shard.get("request_count") or len(shard.get("task_ids") or [])),
                "local_status": shard.get("status"),
                "batch_status": shard.get("batch_status"),
                "remote_completed": remote_completed,
                "remote_failed": remote_failed,
                "remote_total": remote_total,
                "submitted_at_utc": shard.get("submitted_at_utc"),
                "expires_at_utc": expires_at.isoformat() if expires_at else None,
            }
        )

    retry_in_flight = sum(
        task_id in active_retry_task_ids and task.get("status") == "FAILED"
        for task_id, task in tasks.items()
    )
    failed_waiting_retry = max(0, failed - retry_in_flight)
    pending = base_pending + retry_in_flight
    processed_for_rate = accepted + failed_waiting_retry + remote_processed_uningested
    remaining = max(0, planned - accepted)
    blocked_with_no_pending = failed_waiting_retry > 0 and pending == 0
    eta_seconds: float | None = None
    if blocked_with_no_pending:
        eta_seconds = None
    elif remaining == 0:
        eta_seconds = 0.0
    elif active_retry_submitted_times:
        retry_started_at = min(active_retry_submitted_times)
        retry_elapsed_seconds = max(0.0, (moment - retry_started_at).total_seconds())
        retry_processed = min(retry_in_flight, remote_processed_uningested)
        retry_remaining = max(0, retry_in_flight - retry_processed)
        if retry_elapsed_seconds > 0 and retry_processed > 0:
            eta_seconds = retry_elapsed_seconds * retry_remaining / retry_processed
    elif started_at and elapsed_seconds > 0 and processed_for_rate > 0:
        eta_seconds = elapsed_seconds * remaining / processed_for_rate
    estimated_completion = (
        moment + timedelta(seconds=eta_seconds) if eta_seconds is not None else None
    )
    percent = (accepted / planned * 100.0) if planned else 0.0
    remote_percent = (
        min(planned, processed_for_rate) / planned * 100.0 if planned else 0.0
    )
    shard_counts = Counter(
        str(shard.get("status") or "UNKNOWN") for shard in shards.values()
    )
    batch_status_counts = Counter(
        str(shard.get("batch_status") or "not_submitted") for shard in shards.values()
    )
    protocol = _read_object(root / "frozen_inputs" / "official_protocol.json")
    approval_name = str(protocol["approval_policy"]["approval_filename"])
    approval = _read_object(root / approval_name)
    approval_matches = (
        approval.get("approved") is True
        and approval.get("plan_sha256") == plan.get("plan_sha256")
        and approval.get("run_id") == plan.get("run_id")
    )
    effective_max_attempts = int(plan.get("max_attempts_per_task") or 0)
    recovery_plan_path = root / "RECOVERY_PLAN.json"
    if recovery_plan_path.is_file():
        recovery_plan = _read_object(recovery_plan_path)
        if recovery_plan.get("parent_plan_sha256") == plan.get("plan_sha256"):
            effective_max_attempts = max(
                effective_max_attempts,
                int(recovery_plan.get("recovery_max_attempts_per_task") or 0),
            )
    failed_tasks = [task for task in tasks.values() if task.get("status") == "FAILED"]
    attempt_limit_reached = bool(failed_tasks) and pending == 0 and all(
        int(task.get("attempts") or 0) >= effective_max_attempts
        for task in failed_tasks
    )
    display_status = (
        "COMPLETE_AT_ATTEMPT_LIMIT"
        if attempt_limit_reached
        else str(state.get("status") or "UNKNOWN")
    )
    return {
        "run_id": plan.get("run_id"),
        "plan_sha256": plan.get("plan_sha256"),
        "run_status": state.get("status"),
        "display_status": display_status,
        "approval_status": "APPROVED" if approval_matches else "NOT_APPROVED",
        "observed_at_utc": moment.isoformat(),
        "started_at_utc": started_at.isoformat() if started_at else None,
        "elapsed": _duration(elapsed_seconds),
        "planned_assessments": planned,
        "accepted_assessments": accepted,
        "failed_waiting_retry": failed_waiting_retry,
        "retry_assessments_in_flight": retry_in_flight,
        "pending_assessments": pending,
        "verified_completion_percent": round(percent, 3),
        "remote_or_verified_progress_percent": round(remote_percent, 3),
        "observed_throughput_assessments_per_hour": (
            round(processed_for_rate / elapsed_seconds * 3600.0, 2)
            if elapsed_seconds > 0 and processed_for_rate > 0
            else None
        ),
        "estimated_remaining": (
            "Attempt limit reached"
            if attempt_limit_reached
            else ("Blocked" if blocked_with_no_pending else _duration(eta_seconds))
        ),
        "estimated_completion_utc": (
            estimated_completion.isoformat() if estimated_completion else None
        ),
        "latest_active_batch_deadline_utc": (
            max(expiry_times).isoformat() if expiry_times else None
        ),
        "eta_method": (
            (
                "Experiment finished under the approved protocol: "
                f"{failed_waiting_retry:,} cells failed validation after "
                f"{effective_max_attempts} attempts."
                if attempt_limit_reached
                else f"Blocked: {failed_waiting_retry:,} unresolved failures require recovery; no requests remain active."
            )
            if blocked_with_no_pending
            else (
                "Observed processed-task throughput; provisional until multiple batches complete."
                if eta_seconds is not None and remaining > 0
                else (
                    "Complete."
                    if remaining == 0
                    else "Unavailable until the first remote tasks are processed."
                )
            )
        ),
        "task_counts": dict(sorted(task_counts.items())),
        "shard_counts": dict(sorted(shard_counts.items())),
        "batch_status_counts": dict(sorted(batch_status_counts.items())),
        "batches": batch_rows,
    }


def _dashboard_html(snapshot: dict[str, Any]) -> str:
    percent = float(snapshot["verified_completion_percent"])
    rows = []
    for batch in snapshot["batches"]:
        remote = (
            f"{batch['remote_completed']} complete / {batch['remote_failed']} failed"
            if batch["remote_total"]
            else "—"
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(batch['shard_id']))}</td>"
            f"<td>{html.escape(str(batch['condition']))}</td>"
            f"<td>{batch['requests']}</td>"
            f"<td>{html.escape(str(batch['local_status']))}</td>"
            f"<td>{html.escape(str(batch['batch_status'] or 'not submitted'))}</td>"
            f"<td>{html.escape(remote)}</td>"
            "</tr>"
        )
    cards = [
        ("Verified complete", f"{percent:.2f}%"),
        ("Accepted", f"{snapshot['accepted_assessments']:,} / {snapshot['planned_assessments']:,}"),
        ("Failed / retry", f"{snapshot['failed_waiting_retry']:,}"),
        ("Estimated remaining", str(snapshot["estimated_remaining"])),
        ("Elapsed", str(snapshot["elapsed"])),
        ("Run status", str(snapshot["display_status"])),
    ]
    card_html = "".join(
        f'<div class="card"><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>'
        for label, value in cards
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="60">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Official Experiment Progress</title>
  <style>
    :root {{ color-scheme: light; --blue:#2563eb; --ink:#172033; --muted:#657086; --panel:#fff; --bg:#f4f7fb; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    main {{ max-width:1180px; margin:36px auto; padding:0 24px 48px; }}
    h1 {{ margin:0 0 4px; font-size:30px; }}
    .sub {{ color:var(--muted); margin-bottom:24px; }}
    .bar {{ height:24px; background:#dce5f2; border-radius:999px; overflow:hidden; box-shadow:inset 0 1px 2px #b8c4d4; }}
    .fill {{ height:100%; width:{percent:.4f}%; background:linear-gradient(90deg,#2563eb,#16a7c5); min-width:{'4px' if percent > 0 else '0'}; }}
    .barlabel {{ display:flex; justify-content:space-between; margin:7px 2px 22px; color:var(--muted); }}
    .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin-bottom:22px; }}
    .card {{ background:var(--panel); border:1px solid #dce3ee; border-radius:12px; padding:15px; box-shadow:0 3px 12px #23324d0d; }}
    .card span {{ display:block; color:var(--muted); font-size:13px; margin-bottom:6px; }}
    .card strong {{ display:block; font-size:20px; overflow-wrap:anywhere; }}
    .note {{ background:#edf4ff; border-left:4px solid var(--blue); padding:12px 14px; margin:0 0 22px; }}
    .tablewrap {{ overflow:auto; background:var(--panel); border:1px solid #dce3ee; border-radius:12px; }}
    table {{ border-collapse:collapse; width:100%; min-width:850px; }}
    th,td {{ text-align:left; padding:11px 13px; border-bottom:1px solid #e7ecf3; white-space:nowrap; }}
    th {{ background:#172f4a; color:white; position:sticky; top:0; }}
    tr:last-child td {{ border-bottom:0; }}
    footer {{ color:var(--muted); margin-top:14px; font-size:13px; }}
  </style>
</head>
<body><main>
  <h1>Official S&amp;P 500 AI Experiment</h1>
  <div class="sub">Run {html.escape(str(snapshot['run_id']))} · approval {html.escape(str(snapshot['approval_status']))}</div>
  <div class="bar"><div class="fill"></div></div>
  <div class="barlabel"><span>Verified accepted cells</span><strong>{percent:.2f}%</strong></div>
  <section class="cards">{card_html}</section>
  <div class="note"><strong>ETA:</strong> {html.escape(str(snapshot['eta_method']))}
    Latest active 24-hour Batch deadline: {html.escape(str(snapshot['latest_active_batch_deadline_utc'] or 'none'))}.</div>
  <div class="tablewrap"><table>
    <thead><tr><th>Shard</th><th>Condition</th><th>Requests</th><th>Local state</th><th>OpenAI Batch</th><th>Remote counts</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
  <footer>Updated {html.escape(str(snapshot['observed_at_utc']))}. This file refreshes every 60 seconds; the supervisor rewrites it after each sync.</footer>
</main></body></html>"""


def render_progress_dashboard(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir).expanduser().resolve()
    snapshot = build_progress_snapshot(root)
    progress_dir = root / "progress"
    _atomic_write(
        progress_dir / "progress.json",
        (json.dumps(snapshot, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    _atomic_write(
        progress_dir / "index.html", _dashboard_html(snapshot).encode("utf-8")
    )
    return snapshot
