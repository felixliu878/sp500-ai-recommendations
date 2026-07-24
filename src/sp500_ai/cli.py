from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_config
from .pipeline import run_experiment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the S&P 500 AI information-ablation study")
    subparsers = parser.add_subparsers(dest="scope", required=True)
    for scope in ("pilot", "full"):
        command = subparsers.add_parser(scope)
        command.add_argument("--backend", choices=("dry-run", "openai"), default="dry-run")
        command.add_argument("--repetitions", type=int)
        command.add_argument("--refresh", action="store_true")
        command.add_argument("--symbols", nargs="+")
        command.add_argument("--limit", type=int)
        command.add_argument("--config", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    output = run_experiment(
        config=load_config(args.config),
        scope=args.scope,
        backend=args.backend,
        repetitions=args.repetitions,
        refresh=args.refresh,
        symbols=args.symbols,
        limit=args.limit,
    )
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failed = int(manifest.get("failed_assessments", 0))
    not_attempted = int(manifest.get("not_attempted_assessments", 0))
    completed = int(manifest.get("completed_assessments", 0))
    planned = int(manifest.get("planned_assessments", completed + failed + not_attempted))
    if failed or not_attempted:
        print(
            "Completed with failures: "
            f"{completed}/{planned} successful, {failed} failed, "
            f"{not_attempted} not attempted. Artifacts: {output}"
        )
        raise SystemExit(2)
    print(f"Completed successfully: {completed}/{planned}. Artifacts: {output}")


if __name__ == "__main__":
    main()
