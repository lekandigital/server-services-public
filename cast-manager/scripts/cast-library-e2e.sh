#!/usr/bin/env bash
# E2E cast tests against real library files on the Ubuntu server.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${CAST_MANAGER_URL:-http://127.0.0.1:8004}"
USB_SERIAL="${CAST_ADB_SERIAL:-}"

while [ $# -gt 0 ]; do
  case "$1" in
    --url) BASE="$2"; shift 2 ;;
    --serial) USB_SERIAL="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 2 ;;
  esac
done

if [ -z "$USB_SERIAL" ]; then
  USB_SERIAL="$(adb devices -l 2>/dev/null | awk '/ usb:/ && /device/ {print $1; exit}')"
fi
export CAST_ADB_SERIAL="$USB_SERIAL"

LIBRARY_CASES=(
  "/home/REDACTED_USER/watch_list/cast-manager-smoke-tests/smoke_mkv_h264_eac3.mkv|auto|MKV H.264+EAC3 auto"
  "/home/REDACTED_USER/watch_list/cast-manager-smoke-tests/smoke_mkv_h264_eac3.mkv|hls|MKV H.264+EAC3 HLS"
  "/home/REDACTED_USER/watch_list/cast-manager-smoke-tests/smoke_fast_hevc.mkv|auto|HEVC auto fallback"
  "/tmp/cast-manager-test-media/known_good_h264_aac.mp4|auto|MP4 golden auto"
)

FAIL=0
for entry in "${LIBRARY_CASES[@]}"; do
  IFS='|' read -r FILE BACKEND LABEL <<< "$entry"
  echo ""
  echo "========== $LABEL =========="
  if [ ! -f "$FILE" ]; then
    echo "SKIP: missing $FILE"
    continue
  fi
  export CAST_TEST_FILE_PATH="$FILE"
  if bash "$SCRIPT_DIR/cast-e2e-adb-test.sh" --backend "$BACKEND" --subtitle off 2>&1 | tail -8; then
    echo "PASS: $LABEL"
  else
    echo "FAIL: $LABEL"
    FAIL=$((FAIL + 1))
  fi
  curl -sS -X POST "$BASE/api/cast/controls" -H 'Content-Type: application/json' -d '{"action":"stop"}' >/dev/null 2>&1 || true
  sleep 3
done

# Subtitle tests on generated MP4 (has sidecar vtt/srt)
bash "$SCRIPT_DIR/cast-create-test-media.sh" >/dev/null 2>&1 || true
export CAST_TEST_FILE_PATH="/tmp/cast-manager-test-media/known_good_h264_aac.mp4"
cp -f /tmp/cast-manager-test-media/known_good_h264_aac.vtt /tmp/cast-manager-test-media/known_good_h264_aac.en.vtt 2>/dev/null || true

echo ""
echo "========== Subtitle auto (external VTT) =========="
if bash "$SCRIPT_DIR/cast-e2e-adb-test.sh" --backend auto --subtitle auto 2>&1 | tail -8; then
  echo "PASS: subtitle auto"
else
  echo "FAIL: subtitle auto"
  FAIL=$((FAIL + 1))
fi
curl -sS -X POST "$BASE/api/cast/controls" -H 'Content-Type: application/json' -d '{"action":"stop"}' >/dev/null 2>&1 || true
sleep 3

echo ""
echo "========== Subtitle burn-in (ffmpeg-live) =========="
if bash "$SCRIPT_DIR/cast-e2e-adb-test.sh" --backend ffmpeg-live --subtitle burn-in 2>&1 | tail -8; then
  echo "PASS: subtitle burn-in"
else
  echo "FAIL: subtitle burn-in"
  FAIL=$((FAIL + 1))
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "All library/subtitle E2E tests passed"
  exit 0
fi
echo "$FAIL library/subtitle test(s) failed"
exit 1
