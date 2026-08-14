from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent.parent


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def load_config() -> dict[str, Any]:
    return load_yaml(ROOT / "config.yaml")


def load_persona() -> dict[str, Any]:
    return load_yaml(ROOT / "persona.yaml")
