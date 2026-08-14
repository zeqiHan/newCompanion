#!/usr/bin/env bash
# Open companion UI sized to half / quarter. Works with Safari or Chrome.
# Cursor 内置浏览器无法真正「移到屏幕右下角」——请用本脚本开系统浏览器。
set -euo pipefail

MODE="${1:-quarter}"
URL="${2:-http://127.0.0.1:8765/}"
BROWSER="${3:-auto}" # auto | safari | chrome

SW=$(osascript -e 'tell application "Finder" to get bounds of window of desktop' | awk -F', ' '{print $3}')
SH=$(osascript -e 'tell application "Finder" to get bounds of window of desktop' | awk -F', ' '{print $4}')

if [[ "$MODE" == "quarter" ]]; then
  W=$(( SW * 34 / 100 ))
  H=$(( SH * 58 / 100 ))
  X=$(( SW - W - 18 ))
  Y=$(( SH - H - 40 ))
else
  W=$(( SW / 2 ))
  H=$SH
  X=$(( SW - W ))
  Y=0
fi

pick_browser() {
  if [[ "$BROWSER" == "safari" || "$BROWSER" == "chrome" ]]; then
    echo "$BROWSER"
    return
  fi
  # Prefer Safari when user said they use it; else Chrome if installed.
  if [[ -d "/Applications/Safari.app" ]]; then
    echo "safari"
  elif [[ -d "/Applications/Google Chrome.app" ]]; then
    echo "chrome"
  else
    echo "safari"
  fi
}

APP=$(pick_browser)
echo "opening $APP layout=$MODE size=${W}x${H} pos=${X},${Y}"

if [[ "$APP" == "chrome" ]]; then
  open -na "Google Chrome" --args --new-window --app="$URL" || open -a "Google Chrome" "$URL"
  sleep 0.9
  osascript <<EOF || true
tell application "Google Chrome"
  activate
  set bounds of front window to {$X, $Y, $(expr $X + $W), $(expr $Y + $H)}
end tell
EOF
else
  # Safari: open URL then force front window bounds
  open -a Safari "$URL"
  sleep 1.0
  osascript <<EOF || true
tell application "Safari"
  activate
  if (count of windows) is 0 then
    make new document with properties {URL:"$URL"}
  else
    set URL of current tab of front window to "$URL"
  end if
  set bounds of front window to {$X, $Y, $(expr $X + $W), $(expr $Y + $H)}
end tell
EOF
fi

echo "done. 页内「右下角」按钮在独立窗口里通常可用；Cursor 内置浏览器请改用本脚本。"
