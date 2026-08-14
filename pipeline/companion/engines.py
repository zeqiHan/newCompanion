from __future__ import annotations

import asyncio
import base64
import math
import re
import struct
import tempfile
import wave
from io import BytesIO
from pathlib import Path
from typing import Any, AsyncIterator, Protocol

import aiohttp
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODELS = PROJECT_ROOT / "models"

# Common Whisper Chinese near-homophone fixes (tiny/base).
_DEFAULT_ASR_FIXES = {
    "拥有": "永远",
}


def correct_asr_text(text: str, fixes: dict[str, str] | None = None) -> str:
    out = text or ""
    mapping = fixes or _DEFAULT_ASR_FIXES
    for wrong, right in mapping.items():
        if wrong in out:
            out = out.replace(wrong, right)
    return out


class STTEngine(Protocol):
    async def transcribe(self, pcm16: bytes, sample_rate: int = 16000) -> str: ...


class LLMEngine(Protocol):
    def warmup(self) -> None: ...

    async def complete(
        self,
        *,
        system: str,
        history: list[Any],
        max_tokens: int,
        temperature: float,
    ) -> str: ...


class TTSEngine(Protocol):
    def warmup(self) -> None: ...

    def synthesize(self, text: str) -> AsyncIterator[dict[str, Any]]: ...


class VADEngine(Protocol):
    def is_speech(self, pcm16: bytes, sample_rate: int = 16000) -> float: ...


class StreamVAD(Protocol):
    def reset(self) -> None: ...

    def push(self, pcm16: bytes) -> list[dict[str, Any]]: ...


# ---------- Mock fallbacks ----------


class MockSTT:
    async def transcribe(self, pcm16: bytes, sample_rate: int = 16000) -> str:
        return ""


class MockLLM:
    def warmup(self) -> None:
        return None

    async def complete(
        self,
        *,
        system: str,
        history: list[Any],
        max_tokens: int,
        temperature: float,
    ) -> str:
        await asyncio.sleep(0.25)
        last = next((t.text for t in reversed(history) if t.role == "user"), "")
        if not last:
            return "我在呢。"
        if any(k in last for k in ("你好", "在吗", "嗨", "hello", "hi")):
            return "嗯，我在。今天想聊什么？"
        if "名字" in last:
            return "叫我小遥就好。"
        return f"我听到了：「{last[:40]}」。再说细一点也行。"


class MockTTS:
    def warmup(self) -> None:
        return None

    async def synthesize(self, text: str) -> AsyncIterator[dict[str, Any]]:
        duration = min(2.8, 0.55 + len(text) * 0.045)
        sr = 22050
        n = int(sr * duration)
        buf = BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            frames = bytearray()
            for i in range(n):
                t = i / sr
                env = min(1.0, t * 8) * min(1.0, (duration - t) * 4)
                sample = 0.18 * env * (
                    math.sin(2 * math.pi * 220 * t) + 0.35 * math.sin(2 * math.pi * 330 * t)
                )
                frames += struct.pack("<h", int(max(-1.0, min(1.0, sample)) * 32767))
            wf.writeframes(frames)
        yield {
            "type": "audio",
            "format": "wav",
            "data": base64.b64encode(buf.getvalue()).decode("ascii"),
            "text": text,
        }


class MockVAD:
    def is_speech(self, pcm16: bytes, sample_rate: int = 16000) -> float:
        if not pcm16:
            return 0.0
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(audio**2))) if audio.size else 0.0
        return min(1.0, rms * 8.0)


class MockStreamVAD:
    """Energy-based streaming VAD for mock / fallback."""

    def __init__(self, threshold: float = 0.02, min_silence_ms: int = 500) -> None:
        self.threshold = threshold
        self.min_silence_frames = max(1, int(min_silence_ms / 32))  # ~32ms @ 512/16k
        self.window = 512
        self._pending = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        self._silence = 0
        self._utt = bytearray()

    def reset(self) -> None:
        self._pending = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        self._silence = 0
        self._utt.clear()

    def push(self, pcm16: bytes) -> list[dict[str, Any]]:
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        self._pending = np.concatenate([self._pending, audio])
        out: list[dict[str, Any]] = []
        while len(self._pending) >= self.window:
            frame = self._pending[: self.window]
            self._pending = self._pending[self.window :]
            pcm = (np.clip(frame, -1, 1) * 32767).astype(np.int16).tobytes()
            rms = float(np.sqrt(np.mean(frame**2)))
            speech = rms >= self.threshold
            if speech:
                if not self._in_speech:
                    self._in_speech = True
                    self._silence = 0
                    self._utt.clear()
                    out.append({"type": "speech_start", "pcm": pcm})
                else:
                    out.append({"type": "speech_frame", "pcm": pcm, "score": min(1.0, rms * 20)})
                self._utt.extend(pcm)
                self._silence = 0
            elif self._in_speech:
                self._utt.extend(pcm)
                self._silence += 1
                out.append({"type": "speech_frame", "pcm": pcm, "score": 0.0})
                if self._silence >= self.min_silence_frames:
                    self._in_speech = False
                    out.append({"type": "speech_end", "pcm": b""})
                    self._utt.clear()
                    self._silence = 0
        return out


# ---------- Local engines ----------


class SileroVAD:
    def __init__(self, threshold: float = 0.5) -> None:
        from silero_vad import load_silero_vad

        self.threshold = threshold
        self.model = load_silero_vad()

    def is_speech(self, pcm16: bytes, sample_rate: int = 16000) -> float:
        from silero_vad import get_speech_timestamps

        if not pcm16:
            return 0.0
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        timestamps = get_speech_timestamps(
            audio,
            self.model,
            sampling_rate=sample_rate,
            threshold=self.threshold,
            min_speech_duration_ms=100,
        )
        return 1.0 if timestamps else 0.0


class SileroStreamVAD:
    """Streaming Silero VAD using VADIterator (512-sample windows @ 16kHz)."""

    def __init__(
        self,
        threshold: float = 0.5,
        min_silence_ms: int = 500,
        sample_rate: int = 16000,
    ) -> None:
        import torch
        from silero_vad import VADIterator, load_silero_vad

        from .memory import mark_loaded

        self.torch = torch
        self.sample_rate = sample_rate
        self.window = 512 if sample_rate == 16000 else 256
        self.model = load_silero_vad()
        self.iterator = VADIterator(
            self.model,
            threshold=threshold,
            sampling_rate=sample_rate,
            min_silence_duration_ms=min_silence_ms,
            speech_pad_ms=30,
        )
        self._pending = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        mark_loaded("vad", note="silero")

    def reset(self) -> None:
        self.iterator.reset_states()
        self._pending = np.zeros(0, dtype=np.float32)
        self._in_speech = False

    def push(self, pcm16: bytes) -> list[dict[str, Any]]:
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        self._pending = np.concatenate([self._pending, audio])
        out: list[dict[str, Any]] = []

        while len(self._pending) >= self.window:
            frame = self._pending[: self.window]
            self._pending = self._pending[self.window :]
            pcm = (np.clip(frame, -1, 1) * 32767).astype(np.int16).tobytes()
            tensor = self.torch.from_numpy(frame)
            result = self.iterator(tensor, return_seconds=False)

            if result and "start" in result:
                self._in_speech = True
                out.append({"type": "speech_start", "pcm": pcm})
            elif result and "end" in result:
                self._in_speech = False
                out.append({"type": "speech_end", "pcm": pcm})
            elif self._in_speech:
                out.append({"type": "speech_frame", "pcm": pcm, "score": 1.0})

        return out


class MLXWhisperSTT:
    def __init__(
        self,
        model_path: str,
        language: str = "zh",
        fixes: dict[str, str] | None = None,
    ) -> None:
        self.model_path = model_path
        self.language = language
        self.fixes = fixes or dict(_DEFAULT_ASR_FIXES)

    async def transcribe(self, pcm16: bytes, sample_rate: int = 16000) -> str:
        return await asyncio.to_thread(self._transcribe_sync, pcm16, sample_rate)

    def _transcribe_sync(self, pcm16: bytes, sample_rate: int) -> str:
        import mlx_whisper

        from .memory import mark_loaded

        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        if sample_rate != 16000 and audio.size:
            n = int(len(audio) * 16000 / sample_rate)
            idx = (np.linspace(0, len(audio) - 1, n)).astype(np.int64)
            audio = audio[idx]
        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self.model_path,
            language=self.language or None,
            verbose=False,
        )
        mark_loaded("stt", path=self.model_path, note="whisper")
        text = (result or {}).get("text", "")
        return correct_asr_text(str(text).strip(), self.fixes)


class MLXLMEngine:
    def __init__(self, model_path: str) -> None:
        self.model_path = model_path
        self._model = None
        self._tokenizer = None

    def warmup(self) -> None:
        self._ensure()

    def _ensure(self) -> None:
        if self._model is not None:
            return
        from mlx_lm import load

        from .memory import mark_loaded

        self._model, self._tokenizer = load(self.model_path)
        mark_loaded("llm", path=self.model_path, note="mlx-lm")

    def _build_prompt(self, system: str, history: list[Any]) -> str:
        self._ensure()
        assert self._tokenizer is not None
        messages = [{"role": "system", "content": system}]
        for turn in history:
            messages.append({"role": turn.role, "content": turn.text})
        if hasattr(self._tokenizer, "apply_chat_template"):
            kwargs: dict[str, Any] = {
                "tokenize": False,
                "add_generation_prompt": True,
            }
            # Qwen3 thinking mode burns tokens/latency — keep off for voice.
            try:
                return self._tokenizer.apply_chat_template(
                    messages,
                    enable_thinking=False,
                    **kwargs,
                )
            except TypeError:
                return self._tokenizer.apply_chat_template(messages, **kwargs)
        parts = [f"System: {system}"]
        for turn in history:
            role = "User" if turn.role == "user" else "Assistant"
            parts.append(f"{role}: {turn.text}")
        parts.append("Assistant:")
        return "\n".join(parts)

    @staticmethod
    def _strip_thinking(text: str) -> str:
        # Remove accidental <think>...</think> blocks if thinking slips through.
        cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"</?think>", "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    async def complete(
        self,
        *,
        system: str,
        history: list[Any],
        max_tokens: int,
        temperature: float,
    ) -> str:
        return await asyncio.to_thread(
            self._complete_sync,
            system,
            history,
            max_tokens,
            temperature,
        )

    def _complete_sync(
        self,
        system: str,
        history: list[Any],
        max_tokens: int,
        temperature: float,
    ) -> str:
        from mlx_lm import generate

        self._ensure()
        assert self._model is not None and self._tokenizer is not None
        prompt = self._build_prompt(system, history)
        text = generate(
            self._model,
            self._tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            verbose=False,
        )
        return self._strip_thinking(str(text))

    async def stream_sentences(
        self,
        *,
        system: str,
        history: list[Any],
        max_tokens: int,
        temperature: float,
    ) -> AsyncIterator[str]:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(item: str | None) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, item)

        def worker() -> None:
            try:
                from mlx_lm import stream_generate

                self._ensure()
                assert self._model is not None and self._tokenizer is not None
                prompt = self._build_prompt(system, history)
                buf = ""
                for item in stream_generate(
                    self._model,
                    self._tokenizer,
                    prompt=prompt,
                    max_tokens=max_tokens,
                ):
                    token = getattr(item, "text", None)
                    if token is None:
                        token = str(item)
                    buf += token
                    # Don't emit mid-thinking tokens.
                    if "<think>" in buf.lower() and "</think>" not in buf.lower():
                        continue
                    buf = MLXLMEngine._strip_thinking(buf) if "</think>" in buf.lower() else buf
                    while True:
                        m = re.search(r"[。！？!?\n]", buf)
                        if not m:
                            if len(buf) >= 10 and ("，" in buf or "," in buf):
                                cut = max(buf.find("，"), buf.find(","))
                                if cut >= 3:
                                    piece = buf[: cut + 1].strip()
                                    buf = buf[cut + 1 :]
                                    if piece:
                                        emit(piece)
                                    continue
                            break
                        piece = buf[: m.end()].strip()
                        buf = buf[m.end() :]
                        if piece:
                            emit(piece)
                if buf.strip():
                    emit(buf.strip())
            except Exception:
                try:
                    text = self._complete_sync(system, history, max_tokens, temperature)
                    if text:
                        emit(text)
                except Exception:
                    emit("我在听。")
            finally:
                emit(None)

        loop.run_in_executor(None, worker)
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item


_SENT_SPLIT = re.compile(r"[。！？!?\n]")


def ensure_ack_bank(config: dict[str, Any]) -> list[dict[str, str]]:
    """Pre-render ultra-short ack wavs for instant first audio."""
    cache_dir = PROJECT_ROOT / "models" / "ack_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    phrases = list(
        config.get("latency", {}).get("ack_phrases")
        or ["嗯。", "我在。", "好。", "嗯嗯。"]
    )
    bank: list[dict[str, str]] = []

    for phrase in phrases:
        safe = "".join(c if c.isalnum() else "_" for c in phrase) or "ack"
        path = cache_dir / f"{safe}.wav"
        if not path.exists() or path.stat().st_size < 1000:
            _render_ack_wav(path, phrase, config)
        if path.exists() and path.stat().st_size > 1000:
            bank.append(
                {
                    "text": phrase,
                    "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                }
            )
    return bank


def _render_ack_wav(path: Path, phrase: str, config: dict[str, Any]) -> None:
    """Prefer macOS say (faster); fall back to Qwen3-TTS / tone."""
    import subprocess

    aiff = path.with_suffix(".aiff")
    try:
        voice = config.get("latency", {}).get("ack_voice", "Tingting")
        subprocess.run(
            ["say", "-v", voice, "-o", str(aiff), phrase],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16", str(aiff), str(path)],
            check=True,
            capture_output=True,
        )
        aiff.unlink(missing_ok=True)
        return
    except Exception:
        aiff.unlink(missing_ok=True)

    # Fallback: soft tone placeholder so UI still gets instant audio.
    duration = 0.28
    sr = 22050
    n = int(sr * duration)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        frames = bytearray()
        for i in range(n):
            t = i / sr
            env = min(1.0, t * 30) * min(1.0, (duration - t) * 12)
            sample = 0.12 * env * math.sin(2 * math.pi * 380 * t)
            frames += struct.pack("<h", int(sample * 32767))
        wf.writeframes(frames)


class OpenAICompatLLM:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg

    def warmup(self) -> None:
        return None

    async def complete(
        self,
        *,
        system: str,
        history: list[Any],
        max_tokens: int,
        temperature: float,
    ) -> str:
        messages = [{"role": "system", "content": system}]
        for turn in history:
            messages.append({"role": turn.role, "content": turn.text})

        payload = {
            "model": self.cfg.get("model", "local"),
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = {"Authorization": f"Bearer {self.cfg.get('api_key', 'local')}"}
        url = self.cfg.get("base_url", "http://127.0.0.1:8080/v1").rstrip("/") + "/chat/completions"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers, timeout=120) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        return f"（llama.cpp 暂时不可用：{resp.status} {body[:120]}）"
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"]
        except Exception as exc:
            mock = MockLLM()
            _ = exc
            return await mock.complete(
                system=system,
                history=history,
                max_tokens=max_tokens,
                temperature=temperature,
            )


class Qwen3MLXTTS:
    def __init__(
        self,
        model_path: str,
        voice: str = "Serena",
        language: str = "Chinese",
        *,
        instruct: str | None = None,
        temperature: float = 0.3,
        speed: float = 1.0,
    ) -> None:
        self.model_path = model_path
        self.voice = voice
        self.language = language
        self.instruct = instruct
        self.temperature = temperature
        self.speed = speed
        self._model = None

    def warmup(self) -> None:
        self._ensure()

    def _ensure(self) -> None:
        if self._model is not None:
            return
        from mlx_audio.tts.utils import load_model

        from .memory import mark_loaded

        self._model = load_model(Path(self.model_path))
        mark_loaded("tts", path=self.model_path, note="qwen3-tts")

    async def synthesize(self, text: str) -> AsyncIterator[dict[str, Any]]:
        path = await asyncio.to_thread(self._synthesize_file, text)
        data = Path(path).read_bytes()
        Path(path).unlink(missing_ok=True)
        try:
            Path(path).parent.rmdir()
        except OSError:
            pass
        yield {
            "type": "audio",
            "format": "wav",
            "data": base64.b64encode(data).decode("ascii"),
            "text": text,
        }

    def _synthesize_file(self, text: str) -> str:
        from mlx_audio.tts.generate import generate_audio

        self._ensure()
        out_dir = Path(tempfile.mkdtemp(prefix="nc-tts-"))
        prefix = str(out_dir / "out")
        lang = self.language
        lang_code = "zh" if lang.lower().startswith("ch") or lang.lower() == "zh" else lang

        kwargs: dict[str, Any] = {
            "model": self._model,
            "text": text,
            "file_prefix": prefix,
            "audio_format": "wav",
            "join_audio": True,
            "verbose": False,
            "play": False,
            "temperature": float(self.temperature),
            "speed": float(self.speed),
        }
        if self.voice:
            kwargs["voice"] = self.voice
        if lang_code:
            kwargs["lang_code"] = lang_code
        if self.instruct:
            kwargs["instruct"] = self.instruct

        try:
            generate_audio(**kwargs)
        except TypeError:
            for k in ("voice", "lang_code", "instruct", "temperature", "speed"):
                kwargs.pop(k, None)
            generate_audio(**kwargs)
        except Exception:
            mock_path = out_dir / "fallback.wav"
            self._write_tone(mock_path, text)
            return str(mock_path)

        wavs = sorted(out_dir.glob("*.wav"))
        if not wavs:
            mock_path = out_dir / "fallback.wav"
            self._write_tone(mock_path, text)
            return str(mock_path)
        return str(wavs[0])

    @staticmethod
    def _write_tone(path: Path, text: str) -> None:
        duration = min(2.0, 0.4 + len(text) * 0.03)
        sr = 22050
        n = int(sr * duration)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            frames = bytearray()
            for i in range(n):
                t = i / sr
                env = min(1.0, t * 8) * min(1.0, (duration - t) * 4)
                sample = 0.15 * env * math.sin(2 * math.pi * 240 * t)
                frames += struct.pack("<h", int(sample * 32767))
            wf.writeframes(frames)


def _resolve(path_value: str) -> str:
    p = Path(path_value)
    if p.is_absolute():
        return str(p)
    candidate = PROJECT_ROOT / path_value
    if candidate.exists():
        return str(candidate)
    models_candidate = MODELS / path_value
    if models_candidate.exists():
        return str(models_candidate)
    return path_value


def create_vad(config: dict[str, Any]) -> VADEngine:
    mode = config.get("mode", "mock")
    vad_cfg = config.get("vad", {})
    if mode == "local" and vad_cfg.get("backend", "silero") == "silero":
        try:
            return SileroVAD(threshold=float(vad_cfg.get("threshold", 0.5)))
        except Exception:
            return MockVAD()
    return MockVAD()


def create_stream_vad(config: dict[str, Any]) -> StreamVAD:
    vad_cfg = config.get("vad", {})
    threshold = float(vad_cfg.get("threshold", 0.5))
    min_silence_ms = int(vad_cfg.get("min_silence_ms", 500))
    mode = config.get("mode", "mock")
    if mode == "local" and vad_cfg.get("backend", "silero") == "silero":
        try:
            return SileroStreamVAD(threshold=threshold, min_silence_ms=min_silence_ms)
        except Exception:
            return MockStreamVAD(threshold=0.015, min_silence_ms=min_silence_ms)
    return MockStreamVAD(threshold=0.015, min_silence_ms=min_silence_ms)


def create_stt_engine(config: dict[str, Any], backend: str) -> STTEngine:
    """Build a single STT backend (no auto-switch)."""
    stt_cfg = config.get("stt", {})
    stt_model = _resolve(stt_cfg.get("model", "models/whisper-base-mlx"))
    fixes = stt_cfg.get("corrections") or _DEFAULT_ASR_FIXES
    language = stt_cfg.get("language", "zh")

    if backend == "mac-speech":
        from .mac_stt import MacSpeechSTT

        locale = stt_cfg.get("locale") or (
            "zh-CN" if str(language).startswith("zh") else "en-US"
        )
        # No silent Whisper fallback — user explicitly chose macOS Speech.
        return MacSpeechSTT(locale=locale, fallback=None)

    return MLXWhisperSTT(stt_model, language=language, fixes=dict(fixes))


class SwitchableSTT:
    """Runtime-selectable STT: mlx-whisper | mac-speech."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        raw = str(config.get("stt", {}).get("backend") or "mlx-whisper")
        self.backend = raw if raw in {"mlx-whisper", "mac-speech"} else "mlx-whisper"
        self._engines: dict[str, STTEngine] = {}
        self._errors: dict[str, str] = {}

    def options(self) -> list[dict[str, Any]]:
        from .mac_stt import HELPER_APP

        return [
            {
                "id": "mlx-whisper",
                "label": "Whisper",
                "ready": True,
                "hint": "本地离线，稳定",
            },
            {
                "id": "mac-speech",
                "label": "macOS 语音",
                "ready": HELPER_APP.exists(),
                "hint": "系统识别，需已授权 MacSTT",
            },
        ]

    def set_backend(self, backend: str) -> dict[str, Any]:
        backend = str(backend or "").strip()
        if backend not in {"mlx-whisper", "mac-speech"}:
            return {
                "ok": False,
                "backend": self.backend,
                "message": "不支持的 STT，请选 Whisper 或 macOS 语音",
            }
        try:
            self._ensure(backend)
        except Exception as exc:
            self._errors[backend] = str(exc)
            return {
                "ok": False,
                "backend": self.backend,
                "message": f"无法启用 {backend}: {exc}",
                "options": self.options(),
            }
        self.backend = backend
        print(f"[stt] backend → {backend}", flush=True)
        label = "macOS 语音" if backend == "mac-speech" else "Whisper"
        return {
            "ok": True,
            "backend": self.backend,
            "message": f"识别引擎：{label}",
            "options": self.options(),
        }

    def _ensure(self, backend: str) -> STTEngine:
        if backend in self._engines:
            return self._engines[backend]
        eng = create_stt_engine(self.config, backend)
        self._engines[backend] = eng
        return eng

    async def transcribe(self, pcm16: bytes, sample_rate: int = 16000) -> str:
        eng = self._ensure(self.backend)
        text = await eng.transcribe(pcm16, sample_rate)
        return text


def create_engines(config: dict[str, Any]) -> tuple[STTEngine, LLMEngine, TTSEngine]:
    mode = config.get("mode", "mock")
    llm_cfg = config.get("llm", {})
    tts_cfg = config.get("tts", {})

    stt: STTEngine = MockSTT()
    llm: LLMEngine = MockLLM()
    tts: TTSEngine = MockTTS()

    if mode != "local":
        return stt, llm, tts

    # STT — always switchable so the UI can pick Whisper / macOS Speech
    try:
        stt = SwitchableSTT(config)
        stt._ensure(stt.backend)
    except Exception as exc:
        print(f"[stt] init failed ({exc}), using mock", flush=True)
        stt = MockSTT()

    # LLM
    llm_backend = llm_cfg.get("backend", "mlx-lm")
    if llm_backend == "mlx-lm":
        llm_model = _resolve(llm_cfg.get("model", "models/Qwen3-4B-4bit"))
        try:
            llm = MLXLMEngine(llm_model)
        except Exception:
            llm = MockLLM()
    elif llm_backend == "openai-compat":
        llm = OpenAICompatLLM(llm_cfg)

    # TTS
    tts_backend = tts_cfg.get("backend", "qwen3-mlx")
    if tts_backend == "qwen3-mlx":
        tts_model = _resolve(tts_cfg.get("model", "models/Qwen3-TTS-0.6B-CustomVoice-8bit"))
        try:
            tts = Qwen3MLXTTS(
                tts_model,
                voice=tts_cfg.get("voice", "Serena"),
                language=tts_cfg.get("language", "Chinese"),
                instruct=tts_cfg.get("instruct"),
                temperature=float(tts_cfg.get("temperature", 0.3)),
                speed=float(tts_cfg.get("speed", 1.0)),
            )
        except Exception:
            tts = MockTTS()

    return stt, llm, tts
