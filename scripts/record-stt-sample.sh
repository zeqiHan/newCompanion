#!/usr/bin/env bash
# Record a short mono 16kHz wav for STT benchmarks.
#
# Arguments:
#   $1  录制时长（秒），默认 5
#   $2  输出文件名（可只写名字，会放到 models/stt_samples/；也可写绝对/相对路径）
#
# Examples:
#   bash scripts/record-stt-sample.sh
#   bash scripts/record-stt-sample.sh 6
#   bash scripts/record-stt-sample.sh 8 my_line.wav
#   bash scripts/record-stt-sample.sh 8 models/stt_samples/silero.wav
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$ROOT/models/stt_samples"
mkdir -p "$OUT_DIR"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
用法:
  bash scripts/record-stt-sample.sh [时长秒] [输出文件名]

参数:
  时长秒        录制多长时间，默认 5
  输出文件名    默认 my_voice.wav
                - 只写文件名 → 保存到 models/stt_samples/
                - 写路径 → 按该路径保存（可相对仓库根目录）

示例:
  bash scripts/record-stt-sample.sh 6 vad_test.wav
EOF
  exit 0
fi

SECS="${1:-5}"
NAME="${2:-my_voice.wav}"

if ! [[ "$SECS" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "错误: 第一个参数必须是录制时长（秒），例如 6" >&2
  echo "用法: bash scripts/record-stt-sample.sh [时长秒] [输出文件名]" >&2
  exit 1
fi

# Ensure .wav suffix for bare filenames
if [[ "$NAME" != *.wav && "$NAME" != *.WAV ]]; then
  NAME="${NAME}.wav"
fi

if [[ "$NAME" == /* ]]; then
  OUT="$NAME"
elif [[ "$NAME" == */* ]]; then
  OUT="$ROOT/$NAME"
else
  OUT="$OUT_DIR/$NAME"
fi

mkdir -p "$(dirname "$OUT")"

cd "$ROOT/pipeline"
# shellcheck disable=SC1091
source .venv/bin/activate
export PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}"

python - <<PY
import time
from pathlib import Path
import numpy as np
import sounddevice as sd
import soundfile as sf

secs = float("$SECS")
out = Path(r"""$OUT""")
sr = 16000
print(f"录制时长: {secs:g} 秒")
print(f"输出文件: {out}")
print("3…"); time.sleep(1)
print("2…"); time.sleep(1)
print("1…"); time.sleep(1)
print("开始说话 ▶")
audio = sd.rec(int(secs * sr), samplerate=sr, channels=1, dtype="float32")
sd.wait()
print("结束 ■")
peak = float(np.max(np.abs(audio))) or 1.0
audio = np.clip(audio / peak * 0.9, -1.0, 1.0)
sf.write(str(out), audio, sr, subtype="PCM_16")
print(f"已保存: {out}")
print("下一步:")
print(f'  bash scripts/bench-stt.sh --wav "{out}" --ref "你的原话" --with-mac')
PY
