#!/usr/bin/env bash
# Build (if needed) and open the single-click 小遥.app
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/小遥xiaoyao.app"

bash "$ROOT/apps/CompanionOverlay/build.sh"
open "$APP"
echo "已打开「小遥xiaoyao」。菜单栏可见应用名；首次会自动启动语音管线。"
