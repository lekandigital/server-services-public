#!/usr/bin/env bash
# ADB E2E cast test harness. Run on Ubuntu host (TV on USB ADB).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BASE="${CAST_MANAGER_URL:-http://127.0.0.1:8004}"
MEDIA_DIR="${CAST_TEST_MEDIA_DIR:-/tmp/cast-manager-test-media}"
REPORT_DIR="${ROOT}/diagnostics/cast-e2e/$(date +%Y%m%d-%H%M%S)"
BACKEND="auto"
SUBTITLE="off"
USB_SERIAL=""
FILE_PATH_ARG=""

while [ $# -gt 0 ]; do
  case "$1" in
    --backend) BACKEND="$2"; shift 2 ;;
    --subtitle) SUBTITLE="$2"; shift 2 ;;
    --file) FILE_PATH_ARG="$2"; shift 2 ;;
    --url) BASE="$2"; shift 2 ;;
    --serial) USB_SERIAL="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 2 ;;
  esac
done

mkdir -p "$REPORT_DIR"
export CAST_ADB_SERIAL="${CAST_ADB_SERIAL:-$USB_SERIAL}"

echo "E2E report: $REPORT_DIR"
bash "$SCRIPT_DIR/cast-diagnose-env.sh" > "$REPORT_DIR/env-report.txt" 2>&1 || true

if [ -z "$USB_SERIAL" ]; then
  USB_SERIAL="$(adb devices -l 2>/dev/null | awk '/ usb:/ && /device/ {print $1; exit}')"
fi
echo "Using ADB USB serial: ${USB_SERIAL:-NONE}"

[ -f "$MEDIA_DIR/known_good_h264_aac.mp4" ] || bash "$SCRIPT_DIR/cast-create-test-media.sh"

FILE_PATH="${CAST_TEST_FILE_PATH:-$FILE_PATH_ARG}"
if [ -z "$FILE_PATH" ]; then
  echo "WARN: Set CAST_TEST_FILE_PATH to server-accessible path (e.g. /tmp/cast-manager-test-media/known_good_h264_aac.mp4)"
  echo "Copy test media to server first if cast-manager runs via SSH to remote files."
  FILE_PATH="/tmp/cast-manager-test-media/known_good_h264_aac.mp4"
fi

if [ -n "$USB_SERIAL" ]; then
  adb -s "$USB_SERIAL" logcat -c 2>/dev/null || true
  adb -s "$USB_SERIAL" exec-out screencap -p > "$REPORT_DIR/00-before.png" 2>/dev/null || true
  adb -s "$USB_SERIAL" shell dumpsys media_session > "$REPORT_DIR/00-media_session.txt" 2>/dev/null || true
fi

BODY=$(cat <<EOF
{"filePath":"$FILE_PATH","backend":"$BACKEND","mode":"$BACKEND","subtitle":{"mode":"$SUBTITLE"},"autoTranscode":"auto"}
EOF
)

echo "POST /api/cast/start backend=$BACKEND"
CAST_RESP=$(curl -sS -X POST "$BASE/api/cast/start" -H 'Content-Type: application/json' -d "$BODY" || echo '{"success":false}')
echo "$CAST_RESP" | tee "$REPORT_DIR/cast-response.json"

SESSION_ID=$(echo "$CAST_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('sessionId',''))" 2>/dev/null || echo "")
SUCCESS=$(echo "$CAST_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('success',False))" 2>/dev/null || echo "False")

sleep 5
curl -sS "$BASE/api/cast/status" > "$REPORT_DIR/status.json" 2>/dev/null || true
[ -n "$SESSION_ID" ] && curl -sS "$BASE/api/cast/diagnostics/$SESSION_ID" > "$REPORT_DIR/diagnostics.json" 2>/dev/null || \
  curl -sS "$BASE/api/cast/diagnostics" > "$REPORT_DIR/diagnostics.json" 2>/dev/null || true

if [ -n "$USB_SERIAL" ]; then
  adb -s "$USB_SERIAL" exec-out screencap -p > "$REPORT_DIR/01-after-cast.png" 2>/dev/null || true
  adb -s "$USB_SERIAL" logcat -d -v time 2>/dev/null | grep -Ei 'cast|chromecast|media|player|exoplayer|codec|http|subtitle|error|fail' | tail -200 > "$REPORT_DIR/logcat-filtered.txt" || true
fi

# Pause / play / stop
curl -sS -X POST "$BASE/api/cast/controls" -H 'Content-Type: application/json' -d '{"action":"pause"}' > "$REPORT_DIR/pause.json" 2>/dev/null || true
sleep 2
curl -sS -X POST "$BASE/api/cast/controls" -H 'Content-Type: application/json' -d '{"action":"play"}' > "$REPORT_DIR/play.json" 2>/dev/null || true
sleep 2
curl -sS -X POST "$BASE/api/cast/controls" -H 'Content-Type: application/json' -d '{"action":"stop"}' > "$REPORT_DIR/stop.json" 2>/dev/null || true

{
  echo "# Cast E2E Summary"
  echo "- backend: $BACKEND"
  echo "- subtitle: $SUBTITLE"
  echo "- success: $SUCCESS"
  echo "- session: $SESSION_ID"
  echo "- file: $FILE_PATH"
  echo "- adb_usb: $USB_SERIAL"
} > "$REPORT_DIR/summary.md"

if [ "$SUCCESS" = "True" ] || [ "$SUCCESS" = "true" ]; then
  echo "E2E PASS (cast API reported success)"
  exit 0
fi
echo "E2E FAIL — see $REPORT_DIR"
exit 1
