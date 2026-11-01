from __future__ import annotations

import asyncio
import base64
import re
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
from .latency import LatencyTrace, latency_log_path
from .learning_state import (
    activate_from_utterance,
    build_state_update_prompt,
    learning_state_for_prompt,
    learning_state_payload,
    load_learning_state,
    looks_like_start_learn,
    merge_state_update,
    parse_state_json,
)
from .loop_state import loop_for_prompt, loop_payload
from .memory import memory_snapshot
from .user_memory import (
    build_summary_prompt,
    companion_memory_payload,
    load_user_memory,
    memory_for_prompt,
    replace_long_term_summary,
    should_refresh_summary,
)


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
        self._user_turn_count = 0
        self._summary_task: asyncio.Task[None] | None = None
        self._learn_task: asyncio.Task[None] | None = None
        self._latency: LatencyTrace | None = None
        print(f"[latency] append-only log → {latency_log_path()}", flush=True)

    def note_ws_audio_sent(self) -> None:
        """Called by WS layer when the first audio frame is actually sent."""
        if self._latency is not None:
            self._latency.mark("ws_audio_sent", once=True)

    def _system_prompt(self) -> str:
        base = str(self.persona.get("system_prompt", "")).strip()
        mem_cfg = self.config.get("companion_memory", {}) or {}
        every = int(mem_cfg.get("inject_summary_every_n_user_turns", 4))
        include_summary = every > 0 and (
            self._user_turn_count <= 1 or self._user_turn_count % every == 1
        )
        parts = [base]
        mem = memory_for_prompt(include_summary=include_summary).strip()
        if mem:
            parts.append(mem)
        # Learning loop state — the missing "brain" layer.
        if bool(self.config.get("learning", {}).get("enabled", True)):
            parts.append(learning_state_for_prompt())
            parts.append(loop_for_prompt())
        return "\n\n".join(parts)

    async def _update_learning_state(self, user_text: str, assistant_text: str) -> None:
        if not bool(self.config.get("learning", {}).get("enabled", True)):
            return
        state = load_learning_state()
        if not state.get("active") and not looks_like_start_learn(user_text):
            return
        try:
            cur = load_learning_state()
            system, fake_hist = build_state_update_prompt(cur, user_text, assistant_text)
            raw = await asyncio.wait_for(
                self.llm.complete(
                    system=system,
                    history=fake_hist,
                    max_tokens=220,
                    temperature=0.2,
                ),
                timeout=12.0,
            )
            patch = parse_state_json(raw or "")
            if not patch:
                print(f"[learn] state update parse miss: {raw!r}", flush=True)
                return
            merged = merge_state_update(cur, patch)
            print(
                f"[learn] state updated active={merged.get('active')} "
                f"intervention={merged.get('intervention')} "
                f"next={merged.get('next_action')!r}",
                flush=True,
            )
        except Exception as exc:
            print(f"[learn] state update failed: {exc}", flush=True)

    def _schedule_learning_state_update(self, user_text: str, assistant_text: str) -> None:
        if self._learn_task and not self._learn_task.done():
            return
        self._learn_task = asyncio.create_task(
            self._update_learning_state(user_text, assistant_text)
        )

    def _schedule_summary_refresh(self) -> None:
        every = int(
            self.config.get("companion_memory", {}).get("summary_every_n_user_turns", 4)
        )
        if not should_refresh_summary(self._user_turn_count, every=every):
            return
        if self._summary_task and not self._summary_task.done():
            return
        history = list(self.history)
        llm = self.llm

        async def _run() -> None:
            try:
                system, fake_hist = build_summary_prompt(history, load_user_memory())
                text = await llm.complete(
                    system=system,
                    history=fake_hist,
                    max_tokens=160,
                    temperature=0.3,
                )
                text = (text or "").strip()
                if text:
                    replace_long_term_summary(text)
                    print(f"[memory] long-term summary refreshed ({len(text)} chars)", flush=True)
            except Exception as exc:
                print(f"[memory] summary refresh failed: {exc}", flush=True)

        self._summary_task = asyncio.create_task(_run())

    def snapshot(self) -> dict[str, Any]:
        stt_backend = "mac-speech"
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
            "companion_memory": companion_memory_payload(),
            "learning_state": learning_state_payload(),
            "loop": loop_payload(),
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

        # Voice path already reserved turn_id at speech_end.
        if from_voice and self._latency is not None and self._latency.turn_id == self.turn_id:
            my_turn = self.turn_id
            lat = self._latency
        else:
            self.turn_id += 1
            my_turn = self.turn_id
            lat = LatencyTrace(
                session_id=self.session_id,
                turn_id=my_turn,
                source="voice" if from_voice else "text",
            )
            self._latency = lat

        self._busy = True
        self._user_turn_count += 1

        self.history.append(Turn(role="user", text=text))
        self.state = "thinking"
        yield {"type": "state", "state": self.state}
        yield {"type": "transcript", "role": "user", "text": text}

        # Bootstrap learning loop when user declares a study goal.
        if bool(self.config.get("learning", {}).get("enabled", True)):
            ls = load_learning_state()
            if looks_like_start_learn(text) and (
                not ls.get("active") or not str(ls.get("goal") or "").strip()
            ):
                activate_from_utterance(text)
                yield {"type": "learning_state", "learning_state": learning_state_payload()}
                print(f"[learn] activated from utterance: {text!r}", flush=True)

        max_tokens = int(self.config.get("llm", {}).get("max_tokens", 100))
        temperature = float(self.config.get("llm", {}).get("temperature", 0.7))
        system = self._system_prompt()

        full_parts: list[str] = []
        started_speaking = False
        llm_first_marked = False
        tts_first_marked = False
        stream_fn = getattr(self.llm, "stream_sentences", None)

        async def _speak_sentence(sentence: str) -> AsyncIterator[dict[str, Any]]:
            nonlocal started_speaking, llm_first_marked, tts_first_marked
            from .engines import _speakable_piece

            sentence = (sentence or "").strip()
            if not _speakable_piece(sentence):
                return
            chunks = _split_for_tts(sentence)
            for chunk in chunks:
                if my_turn != self.turn_id:
                    return
                if not _speakable_piece(chunk):
                    continue
                if not llm_first_marked:
                    llm_first_marked = True
                    lat.mark("llm_first_sentence", once=True, text=chunk[:80])
                full_parts.append(chunk)
                yield {"type": "reply", "text": "".join(full_parts), "partial": True}
                if not started_speaking:
                    started_speaking = True
                    self.state = "speaking"
                    yield {"type": "state", "state": self.state}
                print(f"[voice] tts sentence={chunk!r}", flush=True)
                try:
                    async for audio in self.tts.synthesize(chunk):
                        if my_turn != self.turn_id:
                            return
                        if not tts_first_marked:
                            tts_first_marked = True
                            lat.mark(
                                "tts_first_audio",
                                once=True,
                                chars=len(chunk),
                                audio_bytes=len(str(audio.get("data", ""))),
                            )
                        yield audio
                except Exception as exc:
                    print(f"[voice] tts error: {exc!r} chunk={chunk!r}", flush=True)
                    yield {"type": "info", "message": "这一句没念出来，我继续听你说。"}

        try:
            if callable(stream_fn):
                async for sentence in stream_fn(
                    system=system,
                    history=self.history,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ):
                    if my_turn != self.turn_id:
                        return
                    async for event in _speak_sentence(sentence):
                        yield event
                        if my_turn != self.turn_id:
                            return
            else:
                reply = await self.llm.complete(
                    system=system,
                    history=self.history,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                if my_turn != self.turn_id:
                    return
                reply = (reply or "").strip() or "……我在听。"
                parts = [p.strip() for p in re.split(r"(?<=[。！？!?])", reply) if p.strip()]
                if not parts:
                    parts = [reply]
                for sentence in parts:
                    async for event in _speak_sentence(sentence):
                        yield event
                        if my_turn != self.turn_id:
                            return

            reply_text = "".join(full_parts).strip() or "……我在听。"
            if not full_parts:
                async for event in _speak_sentence(reply_text):
                    yield event

            print(f"[voice] reply={reply_text!r}", flush=True)
            if my_turn != self.turn_id:
                return

            self.history.append(Turn(role="assistant", text=reply_text))
            max_turns = int(self.config.get("memory", {}).get("max_turns", 16))
            if len(self.history) > max_turns * 2:
                self.history = self.history[-max_turns * 2 :]
            yield {"type": "reply", "text": reply_text, "partial": False}
            yield {"type": "memory", "memory": memory_snapshot()}
            yield {"type": "companion_memory", "companion_memory": companion_memory_payload()}

            self._schedule_summary_refresh()
            await self._update_learning_state(text, reply_text)
            yield {"type": "learning_state", "learning_state": learning_state_payload()}
            lat.summary(reply_chars=len(reply_text))
        except Exception as exc:
            print(f"[voice] turn failed: {exc!r}", flush=True)
            lat.summary(error=str(exc))
            yield {"type": "error", "message": "这一轮出了点问题，我还在听。"}
        finally:
            if my_turn == self.turn_id:
                guard_ms = int(self.config.get("audio", {}).get("echo_guard_ms", 700))
                self._echo_guard_until = time.time() + (guard_ms / 1000.0)
                self.vad.reset()
                self._utt.clear()
                self._in_speech = False
                self._busy = False
                self.state = "listening" if self.mic_on else "idle"
                yield {"type": "echo_guard", "ms": guard_ms}
                yield {"type": "state", "state": self.state}
            else:
                self._busy = False

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

                # Consume turn_id now so empty-STT attempts don't collide in the log.
                self.turn_id += 1
                lat = LatencyTrace(
                    session_id=self.session_id,
                    turn_id=self.turn_id,
                    source="voice",
                )
                self._latency = lat
                lat.mark("speech_end", once=True, pcm_bytes=len(pcm))

                print(
                    f"[voice] speech_end pcm={len(pcm)} bytes, transcribing…",
                    flush=True,
                )
                try:
                    text = await self.stt.transcribe(pcm)
                except Exception as exc:
                    print(f"[voice] stt error: {exc}", flush=True)
                    lat.mark("stt_done", once=True, error=str(exc))
                    text = ""
                else:
                    lat.mark(
                        "stt_done",
                        once=True,
                        chars=len(text or ""),
                        text=(text or "")[:80],
                    )
                print(f"[voice] transcript={text!r}", flush=True)
                if not text:
                    lat.summary(error="empty_transcript")
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
            # Force-clear a stuck turn so「开始聆听」总能恢复。
            if self._busy:
                self.turn_id += 1
                self._busy = False
            self.state = "listening"
            events.append({"type": "state", "state": self.state, "mic_on": True})
            events.append({"type": "info", "message": "麦克风已开，直接说话即可"})
            events.append({"type": "memory", "memory": memory_snapshot()})
        else:
            if not self._busy:
                self.state = "idle"
            events.append({"type": "state", "state": self.state, "mic_on": False})
        return events


def _split_for_tts(text: str, max_chars: int = 28) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    buf = text
    while buf:
        if len(buf) <= max_chars:
            parts.append(buf.strip())
            break
        cut = -1
        window = buf[:max_chars]
        for sep in ("。", "！", "？", "!", "?", "；", ";", "，", ",", " "):
            cut = max(cut, window.rfind(sep))
        if cut < max(6, max_chars // 3):
            cut = max_chars - 1
        piece = buf[: cut + 1].strip()
        buf = buf[cut + 1 :].strip()
        if piece:
            parts.append(piece)
    return [p for p in parts if p]


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
