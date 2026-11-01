#!/usr/bin/env bash
# Bind companion to LAN and print iPhone URLs.
# Mic on iPhone needs HTTPS — optional localtunnel if npx is available.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${COMPANION_PORT:-8765}"
export COMPANION_HOST="${COMPANION_HOST:-0.0.0.0}"
export COMPANION_PORT="$PORT"

LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
if [[ -z "${LAN_IP}" ]]; then
  LAN_IP="$(python3 - <<'PY'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(("8.8.8.8", 80))
    print(s.getsockname()[0])
finally:
    s.close()
PY
)"
fi

echo "小遥手机入口"
echo "  本机:   http://127.0.0.1:${PORT}/phone"
if [[ -n "${LAN_IP}" ]]; then
  echo "  局域网: http://${LAN_IP}:${PORT}/phone   （同一 Wi‑Fi，可打字；麦克风需 HTTPS）"
fi

if command -v npx >/dev/null 2>&1; then
  echo ""
  echo "正在开 temporary HTTPS 隧道（localtunnel）…"
  (
    sleep 2
    npx --yes localtunnel --port "$PORT" --print-requests 2>/dev/null \
      | while IFS= read -r line; do
          echo "$line"
          if [[ "$line" == https://* ]]; then
            echo ""
            echo "iPhone Safari 打开: ${line%/}/phone"
            echo "（首次可能要点「Click to Continue」）"
          fi
        done
  ) &
else
  echo ""
  echo "未找到 npx：只开局域网 HTTP。要语音请安装 Node 后再跑本脚本，或自备 cloudflared/ngrok。"
fi

exec bash "$ROOT/scripts/start-pipeline.sh"
