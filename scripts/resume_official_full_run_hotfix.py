"""Resume the 2026-08-07 official run with an audited ingest-only parser fix.

The frozen v1.0.0 runtime treated a base task hash beginning with ``a`` and a
digit from 2-9 as though it were the ``:aN`` suffix used for retries.  This
wrapper narrows that parser to require a complete ten-hex-character hash
*before* an appended retry suffix.  It does not alter prompts, model requests,
batch IDs, responses, or validation rules.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
import types
from pathlib import Path


RETRY_REQUEST_ID = re.compile(r"(.+:[0-9a-f]{10}):a([2-9][0-9]*)")


def corrected_base_task_id(request_id: str) -> tuple[str, int]:
    match = RETRY_REQUEST_ID.fullmatch(request_id)
    if match:
        return match.group(1), int(match.group(2))
    return request_id, 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument(
        "--sync-only",
        action="store_true",
        help="Retrieve and ingest already-submitted batches without creating new ones.",
    )
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
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

    workspace_root = Path(__file__).resolve().parents[1]
    config.load_dotenv(workspace_root / ".env")
    official._base_task_id = corrected_base_task_id
    if args.sync_only:
        status = official.sync_official_run(run_dir)
    else:
        status = official.supervise_official_run(run_dir)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
