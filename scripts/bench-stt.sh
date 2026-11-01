#!/usr/bin/env bash
# Compare Whisper tiny vs base (latency + transcript) on the same audio.
# Usage:
#   bash scripts/bench-stt.sh
#   bash scripts/bench-stt.sh --with-mac
#   bash scripts/bench-stt.sh --wav models/stt_samples/my_voice.wav
#   bash scripts/bench-stt.sh --wav /abs/path.wav --ref "你当时说的参考文本"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/pipeline"
# shellcheck disable=SC1091
source .venv/bin/activate
export PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}"

WITH_MAC=0
WAV=""
REF=""
args=("$@")
i=0
while [[ $i -lt ${#args[@]} ]]; do
  a="${args[$i]}"
  if [[ "$a" == "--with-mac" ]]; then
    WITH_MAC=1
  elif [[ "$a" == "--wav" ]]; then
    i=$((i + 1))
    WAV="${args[$i]:-}"
  elif [[ "$a" == "--ref" ]]; then
    i=$((i + 1))
    REF="${args[$i]:-}"
  fi
  i=$((i + 1))
done

# Resolve relative wav from repo root
if [[ -n "$WAV" && "$WAV" != /* ]]; then
  WAV="$ROOT/$WAV"
fi

export NC_BENCH_WITH_MAC="$WITH_MAC"
export NC_BENCH_WAV="$WAV"
export NC_BENCH_REF="$REF"
export NC_BENCH_ROOT="$ROOT"

python - <<'PY'
import asyncio, base64, io, os, time, wave
from pathlib import Path
import numpy as np
from companion.engines import MLXWhisperSTT, Qwen3MLXTTS

ROOT = os.environ["NC_BENCH_ROOT"]
WITH_MAC = os.environ.get("NC_BENCH_WITH_MAC") == "1"
WAV = os.environ.get("NC_BENCH_WAV") or ""
REF = os.environ.get("NC_BENCH_REF") or ""

PHRASES = [
    "你好小遥，今天过得怎么样",
    "我想弄懂 Silero VAD 的 threshold 和 endpointing",
    "请把学习目标改成完成三个 test case",
]

def load_wav_pcm16(path: str) -> tuple[bytes, float]:
    import soundfile as sf
    audio, sr = sf.read(path, always_2d=True)
    audio = audio.mean(axis=1).astype(np.float32)
    if sr != 16000:
        n = int(len(audio) * 16000 / sr)
        idx = np.linspace(0, len(audio) - 1, max(n, 1)).astype(np.int64)
        audio = audio[idx]
        sr = 16000
    pcm = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16).tobytes()
    return pcm, len(pcm) / 32000.0

def wav_b64_to_pcm16(b64: str) -> tuple[bytes, float]:
    raw = base64.b64decode(b64)
    with wave.open(io.BytesIO(raw), "rb") as wf:
        sr, ch = wf.getframerate(), wf.getnchannels()
        frames = wf.readframes(wf.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
    if ch == 2:
        audio = audio.reshape(-1, 2).mean(axis=1)
    if sr != 16000:
        n = int(len(audio) * 16000 / sr)
        idx = np.linspace(0, len(audio) - 1, max(n, 1)).astype(np.int64)
        audio = audio[idx]
    pcm = np.clip(audio, -32768, 32767).astype(np.int16).tobytes()
    return pcm, len(pcm) / 32000.0

async def make_tts_samples(tts: Qwen3MLXTTS):
    out = []
    for p in PHRASES:
        b64 = None
        async for ev in tts.synthesize(p):
            b64 = ev["data"]
        pcm, dur = wav_b64_to_pcm16(b64)
        out.append((p, pcm, dur))
    return out

async def bench_whisper(name: str, path: str, samples):
    stt = MLXWhisperSTT(path, language="zh")
    await stt.transcribe(samples[0][1])
    rows = []
    for ref, pcm, dur in samples:
        times = []
        text = ""
        for _ in range(3):
            t0 = time.perf_counter()
            text = await stt.transcribe(pcm)
            times.append(time.perf_counter() - t0)
        rows.append({
            "ref": ref,
            "hyp": text,
            "audio_s": round(dur, 2),
            "lat_s": [round(x, 3) for x in times],
            "avg_s": round(sum(times) / len(times), 3),
        })
    print(f"\n=== {name} ===")
    for r in rows:
        print(f"audio {r['audio_s']}s | latency x3={r['lat_s']} avg={r['avg_s']}s")
        print(f"  REF: {r['ref']}")
        print(f"  HYP: {r['hyp']}")
    return rows

async def bench_mac(samples):
    try:
        from companion.mac_stt import MacSpeechSTT
    except Exception as exc:
        print(f"\n=== macOS Speech SKIP ({exc}) ===")
        return []
    stt = MacSpeechSTT(locale="zh-CN", fallback=None)
    rows = []
    for ref, pcm, dur in samples:
        times, text = [], ""
        try:
            for _ in range(2):
                t0 = time.perf_counter()
                text = await stt.transcribe(pcm)
                times.append(time.perf_counter() - t0)
        except Exception as exc:
            print(f"\n=== macOS Speech FAIL: {exc} ===")
            return rows
        rows.append({
            "ref": ref,
            "hyp": text,
            "audio_s": round(dur, 2),
            "lat_s": [round(x, 3) for x in times],
            "avg_s": round(sum(times) / len(times), 3) if times else None,
        })
    print("\n=== macOS Speech ===")
    for r in rows:
        print(f"audio {r['audio_s']}s | latency x2={r['lat_s']} avg={r['avg_s']}s")
        print(f"  REF: {r['ref']}")
        print(f"  HYP: {r['hyp']}")
    return rows

async def main():
    if WAV:
        path = Path(WAV)
        if not path.exists():
            raise SystemExit(f"wav not found: {path}")
        pcm, dur = load_wav_pcm16(str(path))
        ref = REF or f"(your recording) {path.name}"
        samples = [(ref, pcm, dur)]
        print(f"Using your wav: {path} ({dur:.2f}s)")
    else:
        print("No --wav given: building TTS clips (less realistic)…")
        tts = Qwen3MLXTTS(
            f"{ROOT}/models/Qwen3-TTS-0.6B-CustomVoice-8bit",
            voice="Serena",
            language="Chinese",
        )
        tts.warmup()
        samples = await make_tts_samples(tts)

    tiny = await bench_whisper("Whisper tiny-mlx", f"{ROOT}/models/whisper-tiny-mlx", samples)
    base = await bench_whisper("Whisper base-mlx", f"{ROOT}/models/whisper-base-mlx", samples)
    mac = await bench_mac(samples) if WITH_MAC else []

    print("\n=== SUMMARY (avg latency over clips) ===")
    def mean_lat(rows):
        return round(sum(r["avg_s"] for r in rows) / len(rows), 3) if rows else None
    print(f"tiny avg latency: {mean_lat(tiny)}s")
    print(f"base avg latency: {mean_lat(base)}s")
    if mac:
        print(f"mac  avg latency: {mean_lat(mac)}s")

asyncio.run(main())
PY
