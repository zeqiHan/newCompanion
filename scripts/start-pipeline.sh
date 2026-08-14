#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${COMPANION_PORT:-8765}"

# Free the port if a previous companion server is still holding it
if command -v lsof >/dev/null 2>&1; then
  PIDS=$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "${PIDS}" ]]; then
    echo "端口 $PORT 已被占用，正在结束旧进程: $PIDS"
    # shellcheck disable=SC2086
    kill $PIDS 2>/dev/null || true
    sleep 1
    PIDS=$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
    if [[ -n "${PIDS}" ]]; then
      # shellcheck disable=SC2086
      kill -9 $PIDS 2>/dev/null || true
      sleep 0.5
    fi
  fi
fi

source "${HOME}/.local/bin/env"
cd "$ROOT/pipeline"
source .venv/bin/activate
export PYTHONPATH="$ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}"
echo "启动 newCompanion → http://127.0.0.1:${PORT}/"
exec python -m companion.server
