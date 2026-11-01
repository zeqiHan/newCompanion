#!/usr/bin/env bash
set -euo pipefail
APP_SRC="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$APP_SRC/../.." && pwd)"
STAGE="$APP_SRC/CompanionOverlay.app"
DIST="$ROOT/小遥xiaoyao.app"

rm -rf "$STAGE" "$DIST"
mkdir -p "$STAGE/Contents/MacOS" "$STAGE/Contents/Resources"

swiftc -O \
  "$APP_SRC/main.swift" \
  -o "$STAGE/Contents/MacOS/CompanionOverlay" \
  -framework AppKit \
  -framework WebKit \
  -framework Foundation

# Customize Info.plist display name for Dock/Finder
cp "$APP_SRC/Info.plist" "$STAGE/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName 小遥xiaoyao" "$STAGE/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleName 小遥xiaoyao" "$STAGE/Contents/Info.plist" 2>/dev/null || true

# Remember where the repo lives so /Applications install still finds the pipeline
printf '%s\n' "$ROOT" > "$STAGE/Contents/Resources/ProjectRoot.txt"

# Optional: simple icon from portrait if sips available
if [[ -f "$ROOT/ui/public/portrait.png" ]]; then
  ICONSET="$APP_SRC/AppIcon.iconset"
  rm -rf "$ICONSET"
  mkdir -p "$ICONSET"
  sips -z 32 32     "$ROOT/ui/public/portrait.png" --out "$ICONSET/icon_16x16.png" >/dev/null
  sips -z 64 64     "$ROOT/ui/public/portrait.png" --out "$ICONSET/diana.k@example.org" >/dev/null
  sips -z 128 128   "$ROOT/ui/public/portrait.png" --out "$ICONSET/icon_128x128.png" >/dev/null
  sips -z 256 256   "$ROOT/ui/public/portrait.png" --out "$ICONSET/hannah.h@example.com" >/dev/null
  sips -z 256 256   "$ROOT/ui/public/portrait.png" --out "$ICONSET/icon_256x256.png" >/dev/null
  sips -z 512 512   "$ROOT/ui/public/portrait.png" --out "$ICONSET/wendy.h@example.net" >/dev/null
  sips -z 512 512   "$ROOT/ui/public/portrait.png" --out "$ICONSET/icon_512x512.png" >/dev/null
  sips -z 1024 1024 "$ROOT/ui/public/portrait.png" --out "$ICONSET/walt.e@example.net" >/dev/null
  iconutil -c icns "$ICONSET" -o "$STAGE/Contents/Resources/AppIcon.icns" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string AppIcon" "$STAGE/Contents/Info.plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile AppIcon" "$STAGE/Contents/Info.plist" 2>/dev/null || true
  rm -rf "$ICONSET"
fi

codesign --force --deep -s - --identifier "local.newcompanion.overlay" "$STAGE" >/dev/null
rm -rf "$DIST"
cp -R "$STAGE" "$DIST"
codesign --force --deep -s - --identifier "local.newcompanion.overlay" "$DIST" >/dev/null

echo "built: $DIST"
echo "也可以点项目里的「小遥xiaoyao」图标启动（会自动拉起管线 + 悬浮窗）"
