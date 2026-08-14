from __future__ import annotations

import asyncio
import base64
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from .config import load_config, load_persona
from .engines import (
    LLMEngine,
    STTEngine,
    SwitchableSTT,
    TTSEngine,
    create_engines,
    create_stream_vad,
)
from .memory import memory_snapshot


@dataclass
class Turn:
    role: str
    text: str
    ts: float = field(default_factory=time.time)


_SHARED_ENGINES: tuple[STTEngine, LLMEngine, TTSEngine] | None = None
_ENGINES_LOCK = threading.Lock()
_WARMUP_DONE = False
_WARMUP_LOCK: asyncio.Lock | None = None


def get_shared_engines(config: dict[str, Any] | None = None) -> tuple[STTEngine, LLMEngine, TTSEngine]:
    """Load STT/LLM/TTS once — reloading per WebSocket OOMs a 16GB Mac."""
    global _SHARED_ENGINES
    with _ENGINES_LOCK:
        if _SHARED_ENGINES is None:
            cfg = config or load_config()
            print("[engines] loading shared STT/LLM/TTS…", flush=True)
            _SHARED_ENGINES = create_engines(cfg)
            print("[engines] shared engines ready", flush=True)
        return _SHARED_ENGINES


def _warmup_lock() -> asyncio.Lock:
    global _WARMUP_LOCK
    if _WARMUP_LOCK is None:
        _WARMUP_LOCK = asyncio.Lock()
    return _WARMUP_LOCK


class CompanionSession:
    """Owns one conversation; engines are process-global."""

    def __init__(self) -> None:
        self.config = load_config()
        self.persona = load_persona()
        self.session_id = str(uuid.uuid4())
        self.history: list[Turn] = []
        self.state = "idle"
        self.mic_on = False
        self.turn_id = 0
        self.stt: STTEngine
        self.llm: LLMEngine
        self.tts: TTSEngine
        self.stt, self.llm, self.tts = get_shared_engines(self.config)
        vad_cfg = self.config.get("vad", {})
        self.vad = create_stream_vad(self.config)
        self.min_speech_bytes = int(
            16000 * 2 * (float(vad_cfg.get("min_speech_ms", 250)) / 1000.0)
        )
        self._utt = bytearray()
        self._in_speech = False
        self._busy = False
        self._echo_guard_until = 0.0

    def snapshot(self) -> dict[str, Any]:
        stt_backend = "mlx-whisper"
        stt_options: list[dict[str, Any]] = []
        if isinstance(self.stt, SwitchableSTT):
            stt_backend = self.stt.backend
            stt_options = self.stt.options()
        return {
            "type": "state",
            "session_id": self.session_id,
            "state": self.state,
            "mic_on": self.mic_on,
            "mode": self.config.get("mode", "mock"),
            "stt_backend": stt_backend,
            "stt_options": stt_options,
            "persona": {
                "name": self.persona.get("name"),
                "display_name": self.persona.get("display_name"),
            },
            "layout": self.config.get("ui", {}).get("default_layout", "half"),
            "greeting": self.persona.get("greeting", ""),
            "memory": memory_snapshot(stt_backend=stt_backend),
        }

    def set_stt_backend(self, backend: str) -> dict[str, Any]:
        if not isinstance(self.stt, SwitchableSTT):
            return {
                "type": "stt",
                "ok": False,
                "backend": "mlx-whisper",
                "message": "当前模式不支持切换识别引擎",
            }
        result = self.stt.set_backend(backend)
        result["type"] = "stt"
        result["memory"] = memory_snapshot(stt_backend=result.get("backend"))
        return result

    async def handle_user_text(
        self, text: str, *, from_voice: bool = False
    ) -> AsyncIterator[dict[str, Any]]:
        text = (text or "").strip()
        if not text:
            return

        self.turn_id += 1
        my_turn = self.turn_id
        self._busy = True

        self.history.append(Turn(role="user", text=text))
        self.state = "thinking"
        yield {"type": "state", "state": self.state}
        yield {"type": "transcript", "role": "user", "text": text}

        max_tokens = int(self.config.get("llm", {}).get("max_tokens", 64))
        temperature = float(self.config.get("llm", {}).get("temperature", 0.7))
        system = self.persona.get("system_prompt", "")

        # Collect full reply first, then one-shot TTS for stable timbre/prosody.
        full_parts: list[str] = []
        stream_fn = getattr(self.llm, "stream_sentences", None)
        if callable(stream_fn):
            async for sentence in stream_fn(
                system=system,
                history=self.history,
                max_tokens=max_tokens,
                temperature=temperature,
            ):
                if my_turn != self.turn_id:
                    self._busy = False
                    return
                sentence = (sentence or "").strip()
                if not sentence:
                    continue
                full_parts.append(sentence)
                yield {"type": "reply", "text": "".join(full_parts), "partial": True}
        else:
            reply = await self.llm.complete(
                system=system,
                history=self.history,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            if my_turn != self.turn_id:
                self._busy = False
                return
            reply = (reply or "").strip() or "……我在听。"
            full_parts.append(reply)
            yield {"type": "reply", "text": reply}

        reply_text = "".join(full_parts).strip() or "……我在听。"
        print(f"[voice] reply={reply_text!r}", flush=True)
        if my_turn != self.turn_id:
            self._busy = False
            return

        self.state = "speaking"
        yield {"type": "state", "state": self.state}
        async for chunk in self.tts.synthesize(reply_text):
            if my_turn != self.turn_id:
                self._busy = False
                return
            yield chunk
        print(f"[voice] tts done turn={my_turn}", flush=True)

        self.history.append(Turn(role="assistant", text=reply_text))
        max_turns = int(self.config.get("memory", {}).get("max_turns", 16))
        if len(self.history) > max_turns * 2:
            self.history = self.history[-max_turns * 2 :]
        yield {"type": "reply", "text": reply_text, "partial": False}
        yield {"type": "memory", "memory": memory_snapshot()}

        # Ignore mic briefly so TTS / room echo isn't treated as the next user turn.
        guard_ms = int(self.config.get("audio", {}).get("echo_guard_ms", 700))
        self._echo_guard_until = time.time() + (guard_ms / 1000.0)
        self.vad.reset()
        self._utt.clear()
        self._in_speech = False
        yield {"type": "echo_guard", "ms": guard_ms}

        self._busy = False
        if my_turn != self.turn_id:
            return
        self.state = "listening" if self.mic_on else "idle"
        yield {"type": "state", "state": self.state}

    async def handle_audio_chunk(self, pcm16_b64: str) -> AsyncIterator[dict[str, Any]]:
        if not self.mic_on:
            return

        raw = base64.b64decode(pcm16_b64)
        if not raw:
            return

        # Drop audio during TTS / echo guard so the model's voice isn't transcribed.
        if self._busy or time.time() < self._echo_guard_until:
            return

        barge_in = bool(self.config.get("audio", {}).get("barge_in", False))
        events = self.vad.push(raw)

        for ev in events:
            etype = ev.get("type")

            if etype == "speech_start":
                if self._busy and barge_in and self.state in {"speaking", "thinking"}:
                    self.turn_id += 1
                    self._busy = False
                    yield {"type": "barge_in"}
                self._in_speech = True
                self._utt.clear()
                pre = ev.get("pcm")
                if isinstance(pre, (bytes, bytearray)):
                    self._utt.extend(pre)
                if self.state != "listening":
                    self.state = "listening"
                    yield {"type": "state", "state": self.state}
                yield {"type": "vad", "speech": True}

            elif etype == "speech_frame":
                frame = ev.get("pcm", b"")
                if self._in_speech and frame:
                    self._utt.extend(frame)
                yield {"type": "vad", "speech": True, "score": float(ev.get("score", 1.0))}

            elif etype == "speech_end":
                self._in_speech = False
                frame = ev.get("pcm", b"")
                if frame:
                    self._utt.extend(frame)
                yield {"type": "vad", "speech": False}

                if self._busy or time.time() < self._echo_guard_until:
                    self._utt.clear()
                    continue

                pcm = bytes(self._utt)
                self._utt.clear()
                if len(pcm) < self.min_speech_bytes:
                    continue

                self._busy = True
                self.state = "thinking"
                yield {"type": "state", "state": self.state}

                print(
                    f"[voice] speech_end pcm={len(pcm)} bytes, transcribing…",
                    flush=True,
                )
                try:
                    text = await self.stt.transcribe(pcm)
                except Exception as exc:
                    print(f"[voice] stt error: {exc}", flush=True)
                    text = ""
                print(f"[voice] transcript={text!r}", flush=True)
                if not text:
                    self._busy = False
                    self.state = "listening" if self.mic_on else "idle"
                    yield {"type": "state", "state": self.state}
                    yield {"type": "info", "message": "没听清，再说一次？"}
                    continue

                async for event in self.handle_user_text(text, from_voice=True):
                    yield event

    async def set_listening(self, on: bool) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        self.mic_on = bool(on)
        self.vad.reset()
        self._utt.clear()
        self._in_speech = False
        if on:
            self.state = "listening"
            events.append({"type": "state", "state": self.state, "mic_on": True})
            events.append({"type": "info", "message": "麦克风已开，直接说话即可"})
            events.append({"type": "memory", "memory": memory_snapshot()})
        else:
            if not self._busy:
                self.state = "idle"
            events.append({"type": "state", "state": self.state, "mic_on": False})
        return events


async def run_startup_warmup(session: CompanionSession) -> None:
    """Warm models once per process (not once per WebSocket)."""
    global _WARMUP_DONE
    async with _warmup_lock():
        if _WARMUP_DONE:
            return
        warm = session.config.get("warmup", {})
        print("[warmup] starting…", flush=True)
        if warm.get("stt"):
            await session.stt.transcribe(b"\x00\x00" * 1600)
        if warm.get("llm"):
            await asyncio.to_thread(session.llm.warmup)
        if warm.get("tts"):
            await asyncio.to_thread(session.tts.warmup)
        _WARMUP_DONE = True
        print("[warmup] done", flush=True)
