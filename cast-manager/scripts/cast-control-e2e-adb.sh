#!/usr/bin/env bash
# Full cast control E2E: start, pause, play, seek, scrub simulation, stop.
# Run on Ubuntu host with Android TV on USB ADB and cast-manager reachable.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BASE="${CAST_MANAGER_URL:-http://127.0.0.1:8004}"
MEDIA_DIR="${CAST_TEST_MEDIA_DIR:-/tmp/cast-manager-test-media}"
REPORT_DIR="${ROOT}/diagnostics/cast-control-e2e/$(date +%Y%m%d-%H%M%S)"
BACKEND="${CAST_TEST_BACKEND:-auto}"
FILE_PATH="${CAST_TEST_FILE_PATH:-}"
USB_SERIAL="${CAST_ADB_SERIAL:-}"
CATT="${CATT_PATH:-$HOME/.local/bin/catt}"
DEVICE_NAME="${CHROMECAST_NAME:-REDACTED_DEVICE}"
PASS=0
FAIL=0

log() { echo "[e2e] $*"; }
pass() { PASS=$((PASS + 1)); log "PASS: $*"; }
fail() { FAIL=$((FAIL + 1)); log "FAIL: $*"; }

save_json() {
  local name="$1" url="$2"
  shift 2
  curl -sS "$url" "$@" > "$REPORT_DIR/$name" 2>"$REPORT_DIR/${name%.json}.err" || echo '{"success":false}' > "$REPORT_DIR/$name"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --url) BASE="$2"; shift 2 ;;
    --file) FILE_PATH="$2"; shift 2 ;;
    --backend) BACKEND="$2"; shift 2 ;;
    --serial) USB_SERIAL="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 2 ;;
  esac
done

mkdir -p "$REPORT_DIR"
log "Report: $REPORT_DIR"

{
  echo "BASE=$BASE"
  echo "BACKEND=$BACKEND"
  echo "DATE=$(date -Is 2>/dev/null || date)"
} > "$REPORT_DIR/env-info.txt"
bash "$SCRIPT_DIR/cast-diagnose-env.sh" >> "$REPORT_DIR/env-info.txt" 2>&1 || true

# Server health
if curl -sS -m 5 "$BASE/api/cast/status" > "$REPORT_DIR/00-health.json"; then
  pass "server reachable at $BASE"
else
  fail "server not reachable at $BASE"
  exit 1
fi

if [ -z "$USB_SERIAL" ]; then
  USB_SERIAL="$(adb devices -l 2>/dev/null | awk '/ usb:/ && /device/ {print $1; exit}')"
fi
echo "ADB serial: ${USB_SERIAL:-none}" | tee -a "$REPORT_DIR/env-info.txt"

if [ -n "$USB_SERIAL" ]; then
  adb -s "$USB_SERIAL" logcat -c 2>/dev/null || true
  adb -s "$USB_SERIAL" shell dumpsys media_session > "$REPORT_DIR/00-media_session-before.txt" 2>/dev/null || true
fi

[ -f "$MEDIA_DIR/known_good_h264_aac.mp4" ] || bash "$SCRIPT_DIR/cast-create-test-media.sh"
FILE_PATH="${FILE_PATH:-/tmp/cast-manager-test-media/known_good_h264_aac.mp4}"

# Optional preflight
save_json "preflight.json" "$BASE/api/cast/preflight" -X POST -H 'Content-Type: application/json' \
  -d "{\"filePath\":\"$FILE_PATH\",\"backend\":\"$BACKEND\"}"

# Cast start
BODY="{\"filePath\":\"$FILE_PATH\",\"backend\":\"$BACKEND\",\"mode\":\"$BACKEND\",\"subtitle\":{\"mode\":\"off\"},\"autoTranscode\":\"auto\"}"
log "POST /api/cast/start"
CAST_RESP=$(curl -sS -X POST "$BASE/api/cast/start" -H 'Content-Type: application/json' -d "$BODY")
echo "$CAST_RESP" | tee "$REPORT_DIR/cast-start.json"
SESSION_ID=$(echo "$CAST_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('sessionId',''))" 2>/dev/null || echo "")
SUCCESS=$(echo "$CAST_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('success',False))" 2>/dev/null || echo "False")

if [ "$SUCCESS" = "True" ] || [ "$SUCCESS" = "true" ]; then
  pass "cast start API success"
else
  fail "cast start: $(echo "$CAST_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('message') or d.get('error','unknown'))" 2>/dev/null || echo unknown)"
fi

sleep 4
save_json "status-after-start.json" "$BASE/api/cast/status"
[ -n "$SESSION_ID" ] && save_json "diagnostics.json" "$BASE/api/cast/diagnostics/$SESSION_ID" || \
  save_json "diagnostics.json" "$BASE/api/cast/diagnostics"

# catt status snapshots
if command -v "$CATT" >/dev/null 2>&1; then
  "$CATT" -d "$DEVICE_NAME" status > "$REPORT_DIR/catt-after-start.txt" 2>&1 || true
fi

# Verify currentTime advances
T0=$(python3 -c "import json; d=json.load(open('$REPORT_DIR/status-after-start.json')); print(d.get('currentTime',0))" 2>/dev/null || echo 0)
sleep 4
save_json "status-t1.json" "$BASE/api/cast/status"
T1=$(python3 -c "import json; d=json.load(open('$REPORT_DIR/status-t1.json')); print(d.get('currentTime',0))" 2>/dev/null || echo 0)
if python3 - <<PY
t0=float("$T0" or 0); t1=float("$T1" or 0)
print(f"currentTime delta: {t1-t0:.1f}s ({t0} -> {t1})")
import sys; sys.exit(0 if t1 > t0 else 1)
PY
then pass "currentTime advanced"; else fail "currentTime did not advance ($T0 -> $T1)"; fi

# Pause
curl -sS -X POST "$BASE/api/cast/controls" -H 'Content-Type: application/json' -d '{"action":"pause"}' > "$REPORT_DIR/pause.json"
sleep 2
save_json "status-paused.json" "$BASE/api/cast/status"
STATE=$(python3 -c "import json; print(json.load(open('$REPORT_DIR/status-paused.json')).get('state',''))" 2>/dev/null || echo "")
[ "$STATE" = "paused" ] && pass "pause state=$STATE" || fail "pause state=$STATE (expected paused)"

# Play
curl -sS -X POST "$BASE/api/cast/controls" -H 'Content-Type: application/json' -d '{"action":"play"}' > "$REPORT_DIR/play.json"
sleep 3
save_json "status-playing.json" "$BASE/api/cast/status"

# Seek forward (clamp to media duration — test clip is only ~30s)
CUR=$(python3 -c "import json; print(int(json.load(open('$REPORT_DIR/status-playing.json')).get('currentTime',0)))" 2>/dev/null || echo 0)
DUR=$(python3 -c "import json; print(int(json.load(open('$REPORT_DIR/status-playing.json')).get('duration',30)))" 2>/dev/null || echo 30)
TARGET=$(python3 -c "import json; c=int(json.load(open('$REPORT_DIR/status-playing.json')).get('currentTime',0)); d=int(json.load(open('$REPORT_DIR/status-playing.json')).get('duration',30)); print(min(d-2, c+10))")
curl -sS -X POST "$BASE/api/cast/controls" -H 'Content-Type: application/json' \
  -d "{\"action\":\"seek\",\"value\":$TARGET}" > "$REPORT_DIR/seek-forward.json"
sleep 4
save_json "status-after-seek-fwd.json" "$BASE/api/cast/status"
SFWD=$(python3 -c "import json; print(json.load(open('$REPORT_DIR/status-after-seek-fwd.json')).get('currentTime',0))" 2>/dev/null || echo 0)
if python3 - <<PY
cur=float("$CUR"); tgt=float("$TARGET"); got=float("$SFWD"); dur=float("$DUR")
ok = abs(got - tgt) <= 8 or (got > cur + 3 and got <= dur + 2)
print(f"seek forward: {cur} -> target {tgt} (dur {dur}), got {got}")
import sys; sys.exit(0 if ok else 1)
PY
then pass "seek forward near target"; else fail "seek forward missed ($CUR -> $SFWD, wanted ~$TARGET)"; fi

# Seek -10
CUR2=$(python3 -c "import json; print(int(json.load(open('$REPORT_DIR/status-after-seek-fwd.json')).get('currentTime',0)))" 2>/dev/null || echo 0)
TARGET2=$((CUR2 > 15 ? CUR2 - 10 : 0))
curl -sS -X POST "$BASE/api/cast/controls" -H 'Content-Type: application/json' \
  -d "{\"action\":\"seek\",\"value\":$TARGET2}" > "$REPORT_DIR/seek-back.json"
sleep 4
save_json "status-after-seek-back.json" "$BASE/api/cast/status"

# Scrub simulation: debounced final seek (UI pattern)
DUR=$(python3 -c "import json; print(float(json.load(open('$REPORT_DIR/status-after-seek-back.json')).get('duration',120)))" 2>/dev/null || echo 120)
SCRUB_TARGET=$(python3 -c "import math; d=float('$DUR'); print(int(min(d-5, max(0, d*0.45))))")
curl -sS -X POST "$BASE/api/cast/controls" -H 'Content-Type: application/json' \
  -d "{\"action\":\"seek\",\"value\":$SCRUB_TARGET}" > "$REPORT_DIR/scrub-seek.json"
sleep 4
save_json "status-after-scrub.json" "$BASE/api/cast/status"
pass "scrub seek dispatched to $SCRUB_TARGET"

# Stop
curl -sS -X POST "$BASE/api/cast/controls" -H 'Content-Type: application/json' -d '{"action":"stop"}' > "$REPORT_DIR/stop.json"
sleep 2
save_json "status-after-stop.json" "$BASE/api/cast/status"

if [ -n "$USB_SERIAL" ]; then
  adb -s "$USB_SERIAL" shell dumpsys media_session > "$REPORT_DIR/media_session-after.txt" 2>/dev/null || true
  adb -s "$USB_SERIAL" logcat -d -v time 2>/dev/null | grep -Ei 'cast|chromecast|media|player|exoplayer|http|error|fail' | tail -300 > "$REPORT_DIR/logcat-filtered.txt" || true
  adb -s "$USB_SERIAL" exec-out screencap -p > "$REPORT_DIR/screenshot-after.png" 2>/dev/null || true
fi

if command -v "$CATT" >/dev/null 2>&1; then
  "$CATT" -d "$DEVICE_NAME" status > "$REPORT_DIR/catt-after-stop.txt" 2>&1 || true
fi

{
  echo "# Cast Control E2E Summary"
  echo "- base: $BASE"
  echo "- backend: $BACKEND"
  echo "- file: $FILE_PATH"
  echo "- session: $SESSION_ID"
  echo "- adb: $USB_SERIAL"
  echo "- pass: $PASS"
  echo "- fail: $FAIL"
} > "$REPORT_DIR/summary.md"

log "Done: $PASS passed, $FAIL failed — $REPORT_DIR"
[ "$FAIL" -eq 0 ] && exit 0
exit 1
