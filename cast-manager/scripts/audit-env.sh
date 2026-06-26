#!/usr/bin/env bash
# Cast Manager environment audit — collects host + remote server context.
set +e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AUDIT_DIR="${AUDIT_DIR:-$ROOT/diagnostics/cast-manager-audit/latest}"
mkdir -p "$AUDIT_DIR"
OUT="$AUDIT_DIR/env.txt"

redact() { echo "$1" | sed -E 's/(password|token|secret|key|pass)=[^ ]+/\\1=[REDACTED]/gi'; }

BASE="${CAST_MANAGER_URL:-}"
if [ -z "$BASE" ]; then
  if curl -sS -m 3 http://127.0.0.1:8004/api/cast/status >/dev/null 2>&1; then
    BASE="http://127.0.0.1:8004"
  else
    BASE="http://REDACTED_SERVER_IP:8004"
  fi
fi

{
  echo "=== Cast Manager Environment Audit ==="
  echo "generated: $(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)"
  echo "audit_dir: $AUDIT_DIR"
  echo "cast_manager_url: $BASE"
  echo ""

  echo "=== Local OS ==="
  echo "pwd: $(pwd)"
  echo "hostname: $(hostname 2>/dev/null)"
  uname -a 2>/dev/null
  sw_vers 2>/dev/null || cat /etc/os-release 2>/dev/null | head -5

  echo ""
  echo "=== Git (local repo) ==="
  cd "$ROOT" && git branch --show-current 2>/dev/null
  git status -sb 2>/dev/null | head -5
  git log -1 --oneline 2>/dev/null

  echo ""
  echo "=== Node / npm (local) ==="
  command -v node >/dev/null && node -v || echo "node: MISSING"
  command -v npm >/dev/null && npm -v || echo "npm: MISSING"

  echo ""
  echo "=== ffmpeg / ffprobe (local) ==="
  command -v ffmpeg >/dev/null && ffmpeg -version 2>&1 | head -3 || echo "ffmpeg: MISSING"
  command -v ffprobe >/dev/null && ffprobe -version 2>&1 | head -1 || echo "ffprobe: MISSING"

  echo ""
  echo "=== catt (local) ==="
  CATT="${CATT_PATH:-$HOME/.local/bin/catt}"
  if command -v "$CATT" >/dev/null 2>&1; then
    "$CATT" --version 2>&1 | head -1
    echo "--- catt scan (12s timeout) ---"
    timeout 12s "$CATT" scan 2>&1 | head -25 || echo "catt scan: failed or timed out"
  else
    echo "catt: MISSING at $CATT"
  fi

  echo ""
  echo "=== VLC (local) ==="
  command -v cvlc >/dev/null && cvlc --version 2>&1 | head -1 || echo "cvlc: MISSING"

  echo ""
  echo "=== Network (local) ==="
  hostname -I 2>/dev/null || ip -4 addr show 2>/dev/null | grep inet | awk '{print $2}'
  LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo "chosen LAN IP: ${LAN_IP:-unknown}"
  PORT="${PORT:-8004}"
  echo "default PORT: $PORT"

  echo ""
  echo "=== Env vars (redacted, local shell) ==="
  for v in PORT DOWNLOAD_DIR CHROMECAST_NAME CATT_PATH CAST_PUBLIC_BASE_URL CAST_BACKEND_DEFAULT \
    CAST_ENABLE_VLC_BACKEND CAST_ENABLE_HLS_BACKEND CAST_LIVE_TRANSCODE_ENCODER \
    CAST_ADB_ENABLED CAST_ADB_SERIAL SSH_HOST SSH_USER FILE_MANAGER_ROOT; do
    val="${!v}"
    [ -n "$val" ] && echo "$v=$(redact "$val")" || echo "$v="
  done

  echo ""
  echo "=== ADB (local machine) ==="
  command -v adb >/dev/null || echo "adb: MISSING"
  adb devices -l 2>&1 || echo "adb devices: failed"
  USB_SERIAL="$(adb devices -l 2>/dev/null | awk '/device$/ && !/emulator/ {print $1; exit}')"
  echo "first device serial: ${USB_SERIAL:-none}"
  if [ -n "$USB_SERIAL" ]; then
    adb -s "$USB_SERIAL" shell getprop ro.product.model 2>&1
    adb -s "$USB_SERIAL" shell getprop ro.build.version.release 2>&1
    adb -s "$USB_SERIAL" shell wm size 2>&1
    echo "--- media_session (head) ---"
    adb -s "$USB_SERIAL" shell dumpsys media_session 2>&1 | head -40
    echo "--- activity top (head) ---"
    adb -s "$USB_SERIAL" shell dumpsys activity top 2>&1 | head -25
  else
    echo "NOTE: No local ADB device. ADB tests must run on Ubuntu host or via network ADB."
  fi

  echo ""
  echo "=== Remote server reachability ==="
  curl -sS -m 8 "$BASE/api/cast/status" 2>&1 | head -c 2000
  echo ""

  echo ""
  echo "=== Remote /api/cast/doctor ==="
  curl -sS -m 15 "$BASE/api/cast/doctor" 2>&1 | head -c 4000
  echo ""

  echo ""
  echo "=== Remote /api/cast/diagnostics ==="
  curl -sS -m 10 "$BASE/api/cast/diagnostics" 2>&1 | head -c 4000
  echo ""

  echo ""
  echo "=== Remote /api/receiver/status ==="
  curl -sS -m 10 "$BASE/api/receiver/status" 2>&1 | head -c 2000
  echo ""

  echo ""
  echo "=== Remote /api/storage/stats (root path hint) ==="
  curl -sS -m 20 "$BASE/api/storage/stats" 2>&1 | head -c 3000
  echo ""

  echo ""
  echo "=== Done ==="
} | tee "$OUT"

echo "Saved: $OUT"
