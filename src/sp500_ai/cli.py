from __future__ import annotations

import argparse
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
    print(f"Completed: {output}")


if __name__ == "__main__":
    main()
