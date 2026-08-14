#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ROOT/tools/llama.cpp/llama-b10375/llama-server"
MODEL="$ROOT/models/gguf/qwen2.5-1.5b-instruct-q4_k_m.gguf"
PORT="${PORT:-8080}"

if [[ ! -x "$BIN" ]]; then
  echo "llama-server not found at $BIN" >&2
  exit 1
fi
if [[ ! -f "$MODEL" ]]; then
  echo "GGUF model missing: $MODEL" >&2
  exit 1
fi

exec "$BIN" \
  -m "$MODEL" \
  --host 127.0.0.1 \
  --port "$PORT" \
  -c 4096 \
  -ngl 99 \
  --jinja
