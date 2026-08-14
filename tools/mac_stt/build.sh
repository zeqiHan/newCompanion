#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

swiftc -parse-as-library -O -o mac_stt main.swift \
  -framework Speech -framework Foundation \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker Info.plist

codesign --force -s - --identifier "local.newcompanion.mac-stt" mac_stt

APP="MacSTT.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp Info.plist "$APP/Contents/Info.plist"
# Prefer launching via .app so TCC attaches to our bundle id (not Cursor).
cp mac_stt "$APP/Contents/MacOS/mac_stt"
codesign --force --deep -s - --identifier "local.newcompanion.mac-stt" "$APP"

echo "built: $ROOT/mac_stt"
echo "built: $ROOT/$APP"
codesign -dv "$ROOT/mac_stt" 2>&1 | rg 'Info.plist|Identifier|Format' || true
otool -l "$ROOT/mac_stt" | rg -A2 '__info_plist' | head -5
