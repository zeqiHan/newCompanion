#!/usr/bin/env bash
# Run ONLY in Terminal.app. Grants Speech Recognition to MacSTT.app (not Cursor).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/tools/mac_stt/MacSTT.app"

if [[ ! -d "$APP" ]]; then
  bash "$ROOT/tools/mac_stt/build.sh"
fi

echo "重要：请确认当前窗口是「终端.app」，不是 Cursor。"
echo "即将打开系统设置 → 语音识别；请勾选 MacSTT。"
open "x-apple.systempreferences:com.apple.preference.security?Privacy_SpeechRecognition" 2>/dev/null || true

# Clean leftover literal names from the old broken mktemp template
rm -f /tmp/nc_grantXXXX.aiff /tmp/nc_grantXXXX.wav /tmp/nc_grantXXXX.txt /tmp/nc_grantXXXX.txt.err

# macOS mktemp requires the X's at the end of the template
WORKDIR=$(mktemp -d /tmp/nc_grant.XXXXXX)
TMPA="$WORKDIR/grant.aiff"
TMPW="$WORKDIR/grant.wav"
OUT="$WORKDIR/out.txt"

cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

say -v Tingting -o "$TMPA" "授权测试，你好"
afconvert -f WAVE -d LEI16 "$TMPA" "$TMPW"

echo "启动 MacSTT.app 做一次识别测试（只会走 .app，不会跑裸 mac_stt）…"
open -W -n "$APP" --args "$TMPW" "zh-CN" "$OUT" || true

if [[ -s "$OUT" ]]; then
  echo "✅ 识别成功: $(cat "$OUT")"
  echo "下一步在本窗口执行："
  echo "  bash $ROOT/scripts/start-pipeline.sh"
else
  echo "❌ 仍未识别成功。"
  if [[ -s "${OUT}.err" ]]; then
    echo "错误详情: $(cat "${OUT}.err")"
  fi
  echo "请到：系统设置 → 隐私与安全性 → 语音识别 → 打开 MacSTT"
  echo "然后重新运行本脚本。"
  exit 1
fi
