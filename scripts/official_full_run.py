#!/usr/bin/env python3
"""Prepare, approve, launch, resume, and finalize the official full experiment.

Preparation and preflight are credential-free. Commands that contact OpenAI
require the existing OPENAI_API_KEY and a plan-matching approval file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sp500_ai.config import load_dotenv
from sp500_ai.official import (
    advance_official_run,
    create_retry_shards,
    finalize_official_run,
    launch_official_phase,
    official_status,
    preflight_official_run,
    prepare_current_official_run,
    prepare_official_run,
    record_approval,
    supervise_official_run,
    sync_official_run,
)
from sp500_ai.progress import render_progress_dashboard


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Official resumable S&P 500 OpenAI Batch experiment runner"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--audit-run", type=Path, required=True)
    prepare.add_argument("--eligible-run", type=Path, required=True)
    prepare.add_argument("--protocol", type=Path)
    prepare.add_argument("--experiment-config", type=Path)
    prepare.add_argument("--output-root", type=Path)

    prepare_current = subparsers.add_parser("prepare-current")
    prepare_current.add_argument("--protocol", type=Path)
    prepare_current.add_argument("--experiment-config", type=Path)
    prepare_current.add_argument("--output-root", type=Path)
    prepare_current.add_argument("--refresh", action="store_true")

    for name in ("preflight", "status", "progress", "sync", "retry", "advance", "finalize"):
        command = subparsers.add_parser(name)
        command.add_argument("--run-dir", type=Path, required=True)
    preflight = subparsers.choices["preflight"]
    preflight.add_argument("--require-approval", action="store_true")

    approve = subparsers.add_parser("approve")
    approve.add_argument("--run-dir", type=Path, required=True)
    approve.add_argument("--approved-by", required=True)
    approve.add_argument("--note", required=True)

    launch = subparsers.add_parser("launch")
    launch.add_argument("--run-dir", type=Path, required=True)
    launch.add_argument(
        "--phase",
        required=True,
        choices=("smoke", "full", "smoke_retry", "retry"),
    )

    supervise = subparsers.add_parser("supervise")
    supervise.add_argument("--run-dir", type=Path, required=True)
    supervise.add_argument("--max-cycles", type=int)
    return parser


def _print(value) -> None:
    if isinstance(value, Path):
        value = {"path": str(value)}
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "prepare":
        _print(
            prepare_official_run(
                audit_run=args.audit_run,
                eligible_run=args.eligible_run,
                protocol_path=args.protocol,
                experiment_config_path=args.experiment_config,
                output_root=args.output_root,
            )
        )
        return
    if args.command == "prepare-current":
        _print(
            prepare_current_official_run(
                protocol_path=args.protocol,
                experiment_config_path=args.experiment_config,
                output_root=args.output_root,
                refresh=args.refresh,
            )
        )
        return
    if args.command == "preflight":
        _print(
            preflight_official_run(
                args.run_dir,
                require_approval=args.require_approval,
                write_report=True,
            )
        )
        return
    if args.command == "status":
        _print(official_status(args.run_dir))
        return
    if args.command == "progress":
        _print(render_progress_dashboard(args.run_dir))
        return
    if args.command == "approve":
        _print(
            record_approval(
                args.run_dir, approved_by=args.approved_by, note=args.note
            )
        )
        return

    load_dotenv()
    if args.command == "launch":
        _print(launch_official_phase(args.run_dir, phase=args.phase))
    elif args.command == "sync":
        _print(sync_official_run(args.run_dir))
    elif args.command == "retry":
        _print({"created_retry_shards": create_retry_shards(args.run_dir)})
    elif args.command == "advance":
        _print(advance_official_run(args.run_dir))
    elif args.command == "supervise":
        _print(
            supervise_official_run(
                args.run_dir, max_cycles=args.max_cycles
            )
        )
    elif args.command == "finalize":
        _print(finalize_official_run(args.run_dir))
    else:
        raise AssertionError(args.command)


if __name__ == "__main__":
    main()
