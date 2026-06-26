#!/usr/bin/env bash
# Cast Manager environment diagnosis. Runs on Ubuntu host (or via SSH).
# Never fails completely — marks missing parts clearly.
set +e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT" || exit 1

redact() { echo "$1" | sed -E 's/(password|token|secret|key)=[^ ]+/\\1=[REDACTED]/gi'; }

section() { echo ""; echo "=== $1 ==="; }

section "Environment"
echo "pwd: $(pwd)"
echo "date: $(date -Is 2>/dev/null || date)"
echo "hostname: $(hostname 2>/dev/null)"
echo "uname: $(uname -a 2>/dev/null)"

section "Git"
git branch --show-current 2>/dev/null || echo "git: unavailable"
git status -sb 2>/dev/null | head -5 || true
git log -1 --oneline 2>/dev/null || true

section "Node"
command -v node >/dev/null && node -v || echo "node: MISSING"
command -v npm >/dev/null && npm -v || echo "npm: MISSING"

section "ffmpeg"
command -v ffmpeg >/dev/null && ffmpeg -version 2>&1 | head -3 || echo "ffmpeg: MISSING"
command -v ffprobe >/dev/null && ffprobe -version 2>&1 | head -1 || echo "ffprobe: MISSING"

section "catt"
CATT="${CATT_PATH:-$HOME/.local/bin/catt}"
if command -v "$CATT" >/dev/null 2>&1; then
  "$CATT" --version 2>&1 | head -1
  echo "catt scan (timeout 12s):"
  timeout 12s "$CATT" scan 2>&1 | head -20 || echo "catt scan: failed or timed out"
else
  echo "catt: MISSING at $CATT"
fi

section "VLC"
command -v cvlc >/dev/null && cvlc --version 2>&1 | head -1 || echo "cvlc: MISSING"

section "Network"
hostname -I 2>/dev/null || ip -4 addr show 2>/dev/null | grep inet | awk '{print $2}'
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "chosen LAN IP: ${LAN_IP:-unknown}"
PORT="${PORT:-8004}"
echo "server URL guess: http://${LAN_IP:-127.0.0.1}:${PORT}"

section "Env (redacted)"
for v in PORT CAST_PUBLIC_BASE_URL SERVER_PUBLIC_URL PUBLIC_BASE_URL CHROMECAST_NAME CATT_PATH \
  CAST_BACKEND_DEFAULT CAST_BACKEND_ORDER CAST_ENABLE_VLC_BACKEND CAST_ENABLE_HLS_BACKEND \
  CAST_LIVE_TRANSCODE_ENCODER CAST_SUBTITLE_DEFAULT CAST_SUBTITLE_BURN_IN_FALLBACK \
  CAST_ADB_ENABLED CAST_ADB_SERIAL; do
  val="${!v}"
  [ -n "$val" ] && echo "$v=$(redact "$val")" || echo "$v="
done

section "ADB"
command -v adb >/dev/null || echo "adb: MISSING"
adb devices -l 2>&1 || echo "adb devices: failed"
USB_SERIAL="$(adb devices -l 2>/dev/null | awk '/ usb:/ && /device/ {print $1; exit}')"
echo "USB ADB serial (auto): ${USB_SERIAL:-none}"
if [ -n "$USB_SERIAL" ]; then
  adb -s "$USB_SERIAL" shell getprop ro.product.model 2>&1
  adb -s "$USB_SERIAL" shell getprop ro.build.version.release 2>&1
  adb -s "$USB_SERIAL" shell wm size 2>&1
  echo "--- media_session (head) ---"
  adb -s "$USB_SERIAL" shell dumpsys media_session 2>&1 | head -30
  echo "--- activity top (head) ---"
  adb -s "$USB_SERIAL" shell dumpsys activity top 2>&1 | head -20
  echo "--- cast packages ---"
  adb -s "$USB_SERIAL" shell pm list packages 2>/dev/null | grep -Ei 'cast|chromecast|google|mediashell|receiver|media' | head -20
  if [ -n "$LAN_IP" ]; then
    echo "--- ping server $LAN_IP ---"
    adb -s "$USB_SERIAL" shell ping -c 1 -W 2 "$LAN_IP" 2>&1 | tail -2
  fi
fi

section "Cast Manager process"
pgrep -af 'node.*server.js' 2>/dev/null | head -3 || echo "cast-manager node process: not found"

section "Done"
echo "Report complete."
