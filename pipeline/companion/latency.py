from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOCK = threading.Lock()
_LOG_DIR = Path.home() / "Library/Logs/newCompanion"
_LOG_FILE = _LOG_DIR / "latency.jsonl"
_README_FILE = _LOG_DIR / "latency.README.txt"

# 每个打点的中文说明（写入日志行，方便直接读 jsonl）
_MARK_HELP: dict[str, str] = {
    "origin": "本轮计时起点（创建 trace 的瞬间）",
    "speech_end": "VAD 判定用户说完（语音路径的真正锚点）",
    "stt_done": "语音识别完成，已得到文字",
    "llm_first_sentence": "大模型吐出第一句可播文本",
    "tts_first_audio": "TTS 合成出第一段音频数据",
    "ws_audio_sent": "第一帧 audio 已通过 WebSocket 发给前端",
}

_SUMMARY_MS_HELP: dict[str, str] = {
    "speech_end_to_stt_done": "说完 → STT 完成（听写耗时）",
    "stt_done_to_llm_first": "STT 完成 → 首句 LLM（思考到开口前）",
    "llm_first_to_tts_first": "首句 LLM → 首段 TTS（合成耗时）",
    "tts_first_to_ws_audio": "首段 TTS → WS 发出（网络/发送）",
    "speech_end_to_ws_audio": "说完 → 首包音频发出（端到端首响，最关心）",
    "origin_to_ws_audio": "计时起点 → 首包音频发出",
}

_README_TEXT = """newCompanion 延迟日志说明
========================

文件
----
- latency.jsonl   每次对话追加一行或多行 JSON（永不覆盖旧记录）
- 本文件          字段说明（可随时打开对照）

怎么看
------
1. 按 turn_id 看同一轮；session_id 区分不同连接。
2. event=mark  是沿途打点；event=summary 是本轮汇总（看 ms 字段）。
3. 语音路径最重要看 summary.ms.speech_end_to_ws_audio（说完到听到声音）。
4. since_speech_end_ms = 相对「说完」过了多少毫秒。
5. 文本输入没有 speech_end / stt_done，对应 ms 会是 null。

打点含义
--------
origin              本轮计时起点
speech_end          VAD 判定用户说完
stt_done            语音识别完成
llm_first_sentence  大模型第一句可播文本
tts_first_audio     TTS 第一段音频就绪
ws_audio_sent       第一帧 audio 已发给前端

summary.ms 含义
---------------
speech_end_to_stt_done   说完 → STT
stt_done_to_llm_first    STT → 首句 LLM
llm_first_to_tts_first   首句 LLM → 首段 TTS
tts_first_to_ws_audio    首段 TTS → WS 发出
speech_end_to_ws_audio   说完 → 首包音频（端到端首响）
origin_to_ws_audio       起点 → 首包音频

实时盯日志
----------
  tail -f ~/Library/Logs/newCompanion/latency.jsonl
"""


def _ensure_readme() -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not _README_FILE.exists() or _README_FILE.read_text(encoding="utf-8") != _README_TEXT:
        _README_FILE.write_text(_README_TEXT, encoding="utf-8")


def _log_path() -> Path:
    _ensure_readme()
    return _LOG_FILE


def _append_line(obj: dict[str, Any]) -> None:
    """Append one JSON object per line — never overwrite prior turns."""
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    path = _log_path()
    with _LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    # Also mirror to stdout for live tailing.
    print(f"[latency] {line.rstrip()}", flush=True)


class LatencyTrace:
    """Per-turn latency marks. Append-only; each turn has unique session+turn ids."""

    def __init__(
        self,
        *,
        session_id: str,
        turn_id: int,
        source: str,
    ) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self.source = source  # voice | text
        self.t0 = time.perf_counter()
        self.marks: dict[str, float] = {}
        self._written: set[str] = set()
        self.mark("origin")

    def mark(self, name: str, *, once: bool = False, **extra: Any) -> None:
        if once and name in self._written:
            return
        now = time.perf_counter()
        if once and name in self.marks:
            return
        self.marks[name] = now
        self._written.add(name)
        origin = self.marks.get("origin", self.t0)
        speech_end = self.marks.get("speech_end")
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "source": self.source,
            "event": "mark",
            "mark": name,
            "说明": _MARK_HELP.get(name, name),
            "since_origin_ms": round((now - origin) * 1000, 1),
        }
        if speech_end is not None:
            payload["since_speech_end_ms"] = round((now - speech_end) * 1000, 1)
            payload["相对说完_ms"] = payload["since_speech_end_ms"]
        if extra:
            payload.update(extra)
        _append_line(payload)

    def summary(self, **extra: Any) -> None:
        """Write a final rollup line with key deltas (still append-only)."""

        def delta(a: str, b: str) -> float | None:
            if a not in self.marks or b not in self.marks:
                return None
            return round((self.marks[b] - self.marks[a]) * 1000, 1)

        origin = "speech_end" if "speech_end" in self.marks else "origin"
        ms = {
            "speech_end_to_stt_done": delta("speech_end", "stt_done"),
            "stt_done_to_llm_first": delta("stt_done", "llm_first_sentence")
            if "stt_done" in self.marks
            else delta("origin", "llm_first_sentence"),
            "llm_first_to_tts_first": delta("llm_first_sentence", "tts_first_audio"),
            "tts_first_to_ws_audio": delta("tts_first_audio", "ws_audio_sent"),
            "speech_end_to_ws_audio": delta("speech_end", "ws_audio_sent"),
            "origin_to_ws_audio": delta("origin", "ws_audio_sent"),
        }
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "source": self.source,
            "event": "summary",
            "说明": "本轮延迟汇总；优先看 ms.speech_end_to_ws_audio（说完→听到声音）",
            "anchor": origin,
            "锚点说明": "语音用 speech_end；纯文本用 origin",
            "ms": ms,
            "ms说明": {k: _SUMMARY_MS_HELP[k] for k in ms},
            "分段耗时_可读": {
                _SUMMARY_MS_HELP[k]: (f"{v} ms" if v is not None else "无")
                for k, v in ms.items()
            },
            "marks_ms_since_origin": {
                k: round((v - self.marks["origin"]) * 1000, 1)
                for k, v in self.marks.items()
                if k != "origin"
            },
        }
        if extra:
            payload.update(extra)
        _append_line(payload)


def latency_log_path() -> str:
    return str(_log_path())
