from __future__ import annotations

import os
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


def _parse_dotenv_file(path: Path, *, override: bool) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value


def _load_dotenv() -> None:
    """Load .env then .env.local (local wins). Guide §2 / §5 Step A."""
    _parse_dotenv_file(PROJECT_ROOT / ".env", override=False)
    _parse_dotenv_file(PROJECT_ROOT / ".env.local", override=True)


def _apply_llm_env(cfg: dict[str, Any]) -> None:
    """LLM_* / AI_BUILDER_TOKEN → config.llm (JudgmentOS / guide §8)."""
    llm = cfg.setdefault("llm", {})
    if not isinstance(llm, dict):
        return

    provider = (os.environ.get("LLM_PROVIDER") or "").strip()
    if provider:
        llm["backend"] = (
            "openai-compat"
            if provider in {"openai-compatible", "openai-compat"}
            else provider
        )

    base = (os.environ.get("LLM_BASE_URL") or "").strip()
    if base:
        llm["base_url"] = base

    model = (os.environ.get("LLM_MODEL") or "").strip()
    if model:
        llm["model"] = model

    key = (
        (os.environ.get("LLM_API_KEY") or "").strip()
        or (os.environ.get("AI_BUILDER_TOKEN") or "").strip()
    )
    if key:
        llm["api_key"] = key

    # Cloud key present → prefer openai-compat over local mlx.
    if key and str(llm.get("backend") or "") in {"", "mlx-lm"}:
        llm["backend"] = "openai-compat"
        llm.setdefault("base_url", "https://space.ai-builders.com/backend/v1")
        llm.setdefault("model", "grok-4-fast")


def load_config() -> dict[str, Any]:
    _load_dotenv()
    cfg = load_yaml(ROOT / "config.yaml")
    _apply_llm_env(cfg)

    host = (os.environ.get("COMPANION_HOST") or "").strip()
    port = (os.environ.get("COMPANION_PORT") or "").strip()
    server = cfg.setdefault("server", {})
    if isinstance(server, dict):
        if host:
            server["host"] = host
        if port:
            try:
                server["port"] = int(port)
            except ValueError:
                pass
    return cfg


def load_persona() -> dict[str, Any]:
    return load_yaml(ROOT / "persona.yaml")
