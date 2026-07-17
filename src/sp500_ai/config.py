from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path | None = None) -> None:
    """Load a minimal .env file without adding another dependency."""
    env_path = path or PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class ExperimentConfig:
    raw: dict[str, Any]
    project_root: Path = PROJECT_ROOT

    @property
    def prompt_dir(self) -> Path:
        return self.project_root / "prompts"

    @property
    def as_of_date(self) -> str:
        return str(self.raw["as_of_date"])

    @property
    def model(self) -> str:
        return os.getenv("OPENAI_MODEL", str(self.raw["model"]))

    def __getattr__(self, name: str) -> Any:
        try:
            return self.raw[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def load_config(path: Path | None = None) -> ExperimentConfig:
    load_dotenv()
    config_path = path or PROJECT_ROOT / "config" / "experiment.json"
    return ExperimentConfig(json.loads(config_path.read_text(encoding="utf-8")))
