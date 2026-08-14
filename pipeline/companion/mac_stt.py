"""macOS Speech STT via MacSTT.app only (never bare CLI under Cursor).

Calling the bare `mac_stt` binary while Cursor is the responsible process
triggers TCC SIGABRT: macOS treats it as missing NSSpeechRecognitionUsageDescription
even when the binary embeds an Info.plist. Always launch MacSTT.app via `open`.
"""

from __future__ import annotations

import os
import subprocess
import time
import wave
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HELPER_DIR = PROJECT_ROOT / "tools" / "mac_stt"
HELPER_APP = HELPER_DIR / "MacSTT.app"
HELPER_BIN = HELPER_DIR / "mac_stt"  # only for grant script / Terminal


class MacSpeechSTT:
    """SFSpeechRecognizer through MacSTT.app; optional Whisper fallback."""

    def __init__(
        self,
        locale: str = "zh-CN",
        *,
        fallback: Any | None = None,
    ) -> None:
        self.locale = locale
        self.fallback = fallback
        if not HELPER_APP.exists():
            raise FileNotFoundError(
                f"MacSTT.app missing at {HELPER_APP}. Run tools/mac_stt/build.sh"
            )

    async def transcribe(self, pcm16: bytes, sample_rate: int = 16000) -> str:
        import asyncio

        return await asyncio.to_thread(self._transcribe_sync, pcm16, sample_rate)

    def _transcribe_sync(self, pcm16: bytes, sample_rate: int) -> str:
        from .memory import mark_loaded

        if not pcm16:
            return ""

        # Use a stable cache dir — TemporaryDirectory can vanish / be unreadable
        # to MacSTT.app launched via `open`.
        cache = Path.home() / "Library" / "Caches" / "newCompanion" / "mac_stt"
        cache.mkdir(parents=True, exist_ok=True)
        token = f"{os.getpid()}-{int(time.time()*1000)}"
        wav_path = cache / f"{token}.wav"
        out_path = cache / f"{token}.txt"
        err_path = cache / f"{token}.txt.err"

        text = ""
        try:
            self._write_wav(wav_path, pcm16, sample_rate)
            text = self._run_app(wav_path, out_path)
        finally:
            for p in (wav_path, out_path, err_path):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass

        if text:
            mark_loaded("stt", path=str(HELPER_APP), note="mac-speech-app")
            return text

        # Soft fallback — never spawn bare mac_stt (SIGABRT under Cursor).
        if self.fallback is not None:
            import asyncio

            try:
                result = self.fallback.transcribe(pcm16, sample_rate)
                if asyncio.iscoroutine(result):
                    text = asyncio.run(result)
                else:
                    text = result
                if text:
                    mark_loaded("stt", note="whisper-fallback")
                    return str(text).strip()
            except Exception:
                return ""
        return ""

    def _run_app(self, wav_path: Path, out_path: Path) -> str:
        import os
        import time as _time

        out_path.unlink(missing_ok=True)
        Path(str(out_path) + ".err").unlink(missing_ok=True)

        cmd = [
            "open",
            "-W",
            "-n",
            str(HELPER_APP),
            "--args",
            str(wav_path.resolve()),
            self.locale,
            str(out_path.resolve()),
        ]
        try:
            # Keep this short — conversational turns can't wait on GUI app launch.
            subprocess.run(cmd, check=False, timeout=12, capture_output=True)
        except subprocess.TimeoutExpired:
            return ""
        except Exception:
            return ""

        # Brief poll in case app exits slightly after open -W returns
        deadline = _time.time() + 2.0
        while _time.time() < deadline:
            if out_path.exists() and out_path.stat().st_size > 0:
                return out_path.read_text(encoding="utf-8").strip()
            _time.sleep(0.05)
        if out_path.exists():
            return out_path.read_text(encoding="utf-8").strip()
        return ""

    @staticmethod
    def _write_wav(path: Path, pcm16: bytes, sample_rate: int) -> None:
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm16)
