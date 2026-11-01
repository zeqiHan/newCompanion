from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import psutil

from .config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODELS = PROJECT_ROOT / "models"

# Runtime registry: which engines are currently loaded in-process.
_LOADED: dict[str, dict[str, Any]] = {}


def mark_loaded(name: str, *, path: str | None = None, note: str = "") -> None:
    size = 0
    if path:
        p = Path(path)
        if p.is_file():
            size = p.stat().st_size
        elif p.is_dir():
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    _LOADED[name] = {
        "name": name,
        "path": path,
        "disk_bytes": size,
        "note": note,
        "loaded": True,
    }


def mark_unloaded(name: str) -> None:
    if name in _LOADED:
        _LOADED[name]["loaded"] = False


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _gb(n: int | float) -> float:
    return round(float(n) / (1024**3), 2)


def _mb(n: int | float) -> float:
    return round(float(n) / (1024**2), 1)


def memory_snapshot(*, stt_backend: str | None = None) -> dict[str, Any]:
    cfg = load_config()
    proc = psutil.Process(os.getpid())
    rss = proc.memory_info().rss
    vm = psutil.virtual_memory()

    active_stt = stt_backend or str(cfg.get("stt", {}).get("backend", "mlx-whisper"))
    note = str(_LOADED.get("stt", {}).get("note", ""))
    if "mac-speech" in note or active_stt == "mac-speech":
        stt_label = "STT (macOS Speech)"
    else:
        stt_label = "Whisper STT"

    stt_path = PROJECT_ROOT / str(cfg.get("stt", {}).get("model", "models/whisper-base-mlx"))
    llm_path = PROJECT_ROOT / str(cfg.get("llm", {}).get("model", "models/Qwen2.5-3B-Instruct-4bit"))
    tts_path = PROJECT_ROOT / str(cfg.get("tts", {}).get("model", "models/Qwen3-TTS-0.6B-CustomVoice-8bit"))

    catalog = [
        {
            "id": "stt",
            "label": stt_label,
            "model": "macOS SFSpeech" if active_stt == "mac-speech" else cfg.get("stt", {}).get("model", ""),
            "disk_bytes": _dir_size(stt_path),
            "loaded": _LOADED.get("stt", {}).get("loaded", False),
        },
        {
            "id": "llm",
            "label": "LLM",
            "model": cfg.get("llm", {}).get("model", ""),
            "disk_bytes": _dir_size(llm_path),
            "loaded": _LOADED.get("llm", {}).get("loaded", False),
        },
        {
            "id": "tts",
            "label": "Qwen3-TTS",
            "model": cfg.get("tts", {}).get("model", ""),
            "disk_bytes": _dir_size(tts_path),
            "loaded": _LOADED.get("tts", {}).get("loaded", False),
        },
        {
            "id": "vad",
            "label": "Silero VAD",
            "model": "silero-vad",
            "disk_bytes": 0,
            "loaded": _LOADED.get("vad", {}).get("loaded", False),
            "est_ram_mb": 50 if _LOADED.get("vad", {}).get("loaded") else 0,
        },
    ]

    for item in catalog:
        item["disk_mb"] = _mb(item["disk_bytes"])
        # Rough loaded estimate: weights ≈ disk for 4/8bit mlx; runtime overhead ~1.15x
        if item.get("est_ram_mb") is None:
            item["est_ram_mb"] = round(item["disk_mb"] * 1.15, 1) if item["loaded"] else 0.0

    est_models = sum(float(x["est_ram_mb"]) for x in catalog)

    return {
        "process": {
            "pid": proc.pid,
            "rss_bytes": rss,
            "rss_mb": _mb(rss),
            "rss_gb": _gb(rss),
        },
        "system": {
            "total_bytes": vm.total,
            "used_bytes": vm.used,
            "available_bytes": vm.available,
            "percent": vm.percent,
            "total_gb": _gb(vm.total),
            "used_gb": _gb(vm.used),
            "available_gb": _gb(vm.available),
        },
        "models": catalog,
        "estimate": {
            "loaded_models_mb": round(est_models, 1),
            "pipeline_rss_mb": _mb(rss),
            "note": "RSS 为管线进程实际占用；模型分项为已加载权重估算。",
        },
        "stt": {
            "model": cfg.get("stt", {}).get("model"),
            "language": cfg.get("stt", {}).get("language"),
        },
    }
