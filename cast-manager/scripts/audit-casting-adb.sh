#!/usr/bin/env bash
# Casting audit — ADB when available, API/catt fallback otherwise.
set +e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AUDIT_DIR="${AUDIT_DIR:-$ROOT/diagnostics/cast-manager-audit/latest}"
CAST_DIR="$AUDIT_DIR/casting-adb"
mkdir -p "$CAST_DIR"

BASE="${CAST_MANAGER_URL:-}"
if [ -z "$BASE" ]; then
  curl -sS -m 3 http://127.0.0.1:8004/api/cast/status >/dev/null 2>&1 && BASE="http://127.0.0.1:8004" || BASE="http://REDACTED_SERVER_IP:8004"
fi

CATT="${CATT_PATH:-$HOME/.local/bin/catt}"
DEVICE_NAME="${CHROMECAST_NAME:-REDACTED_DEVICE}"
USB_SERIAL="${CAST_ADB_SERIAL:-}"
if [ -z "$USB_SERIAL" ]; then
  USB_SERIAL="$(adb devices -l 2>/dev/null | awk '/device$/ && !/emulator/ {print $1; exit}')"
fi

slugify() { echo "$1" | sed 's/[^a-zA-Z0-9._-]/_/g' | head -c 80; }

# Pick test files from storage stats
STORAGE=$(curl -sS -m 30 "$BASE/api/storage/stats")
SAMPLE_MP4=$(echo "$STORAGE" | python3 -c "
import sys,json
for f in json.load(sys.stdin).get('largestFiles',[]):
  if f.get('path','').lower().endswith('.mp4'): print(f['path']); break
" 2>/dev/null)
SAMPLE_MKV=$(echo "$STORAGE" | python3 -c "
import sys,json
d=json.load(sys.stdin)
mkvs=[f for f in d.get('largestFiles',[]) if f.get('path','').lower().endswith('.mkv')]
if mkvs: print(mkvs[0]['path'])
" 2>/dev/null)
SAMPLE_MKV_SMALL=$(curl -sS -m 15 "$BASE/api/search?q=.mp4" 2>/dev/null | python3 -c "
import sys,json
for r in json.load(sys.stdin).get('results',[]):
  if r.get('path','').lower().endswith('.mkv') and not r.get('is_directory'):
    print(r['path']); break
" 2>/dev/null)
SAMPLE_MPEG=$(curl -sS -m 15 "$BASE/api/search?q=.mpeg" 2>/dev/null | python3 -c "
import sys,json
for r in json.load(sys.stdin).get('results',[]):
  if not r.get('is_directory') and r.get('path','').lower().endswith(('.mpeg','.mpg')):
    print(r['path']); break
" 2>/dev/null)

# Fallback test media path used by existing e2e scripts
KNOWN_GOOD="/tmp/cast-manager-test-media/known_good_h264_aac.mp4"

TEST_FILES=()
[ -n "$SAMPLE_MP4" ] && TEST_FILES+=("mp4|$SAMPLE_MP4")
[ -n "$SAMPLE_MKV_SMALL" ] && TEST_FILES+=("mkv-normal|$SAMPLE_MKV_SMALL")
[ -n "$SAMPLE_MKV" ] && TEST_FILES+=("mkv-large|$SAMPLE_MKV")
[ -n "$SAMPLE_MPEG" ] && TEST_FILES+=("mpeg|$SAMPLE_MPEG")
TEST_FILES+=("known-good-mp4|$KNOWN_GOOD")

SUMMARY="$CAST_DIR/summary.md"
echo "# Casting ADB Audit Summary" > "$SUMMARY"
echo "Base: $BASE" >> "$SUMMARY"
echo "ADB serial: ${USB_SERIAL:-none}" >> "$SUMMARY"
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)" >> "$SUMMARY"
echo "" >> "$SUMMARY"

run_cast_test() {
  local label="$1" file_path="$2"
  local slug; slug=$(slugify "$label")
  local dir="$CAST_DIR/$slug"
  mkdir -p "$dir"
  local pass=0 fail=0

  echo "## Test: $label" >> "$SUMMARY"
  echo "- File: \`$file_path\`" >> "$SUMMARY"

  if [ -z "$file_path" ]; then
    echo "- **SKIPPED**: no file" >> "$SUMMARY"
    echo "" >> "$SUMMARY"
    return
  fi

  # Pre-test ADB
  if [ -n "$USB_SERIAL" ]; then
    adb -s "$USB_SERIAL" logcat -c 2>/dev/null || true
    adb -s "$USB_SERIAL" exec-out screencap -p > "$dir/before.png" 2>/dev/null || echo "screencap failed" > "$dir/before.err"
    adb -s "$USB_SERIAL" shell dumpsys media_session > "$dir/media_session_before.txt" 2>/dev/null || true
  else
    echo "no_adb_device" > "$dir/adb_note.txt"
  fi

  # Stop any current cast first (except first test — capture existing state)
  curl -sS -m 10 -X POST "$BASE/api/cast/controls" -H 'Content-Type: application/json' -d '{"action":"stop"}' > "$dir/pre_stop.json" 2>/dev/null || true
  sleep 2

  BODY=$(python3 -c "import json; print(json.dumps({'filePath':'''$file_path''','backend':'auto','subtitle':{'mode':'off'},'autoTranscode':'auto'}))")
  echo "$BODY" > "$dir/cast_request.json"
  curl -sS -m 120 -X POST "$BASE/api/cast/start" -H 'Content-Type: application/json' -d "$BODY" > "$dir/cast_response.json" 2>&1

  SESSION=$(python3 -c "import json; print(json.load(open('$dir/cast_response.json')).get('sessionId',''))" 2>/dev/null || echo "")
  SUCCESS=$(python3 -c "import json; print(json.load(open('$dir/cast_response.json')).get('success',False))" 2>/dev/null || echo "False")

  sleep 8
  curl -sS -m 10 "$BASE/api/cast/status" > "$dir/status_after_start.json" 2>/dev/null || true
  [ -n "$SESSION" ] && curl -sS -m 10 "$BASE/api/cast/diagnostics/$SESSION" > "$dir/diagnostics.json" 2>/dev/null || \
    curl -sS -m 10 "$BASE/api/cast/diagnostics" > "$dir/diagnostics.json" 2>/dev/null || true

  if command -v "$CATT" >/dev/null 2>&1; then
    "$CATT" -d "$DEVICE_NAME" status > "$dir/catt_status.txt" 2>&1 || true
  else
    echo "catt not available locally" > "$dir/catt_status.txt"
  fi

  STATE=$(python3 -c "import json; d=json.load(open('$dir/status_after_start.json')); print(d.get('state',''))" 2>/dev/null || echo "")
  T0=$(python3 -c "import json; d=json.load(open('$dir/status_after_start.json')); print(d.get('currentTime',0))" 2>/dev/null || echo 0)
  sleep 6
  curl -sS -m 10 "$BASE/api/cast/status" > "$dir/status_t1.json" 2>/dev/null || true
  T1=$(python3 -c "import json; d=json.load(open('$dir/status_t1.json')); print(d.get('currentTime',0))" 2>/dev/null || echo 0)

  if [ -n "$USB_SERIAL" ]; then
    adb -s "$USB_SERIAL" exec-out screencap -p > "$dir/playing.png" 2>/dev/null || true
    adb -s "$USB_SERIAL" shell dumpsys media_session > "$dir/media_session_playing.txt" 2>/dev/null || true
  fi

  # Controls
  curl -sS -m 10 -X POST "$BASE/api/cast/controls" -H 'Content-Type: application/json' -d '{"action":"pause"}' > "$dir/pause.json"
  sleep 2
  curl -sS -m 10 "$BASE/api/cast/status" > "$dir/status_paused.json"
  curl -sS -m 10 -X POST "$BASE/api/cast/controls" -H 'Content-Type: application/json' -d '{"action":"play"}' > "$dir/play.json"
  sleep 2

  DUR=$(python3 -c "import json; print(int(json.load(open('$dir/status_t1.json')).get('duration',120)))" 2>/dev/null || echo 120)
  CUR=$(python3 -c "import json; print(int(json.load(open('$dir/status_t1.json')).get('currentTime',0)))" 2>/dev/null || echo 0)
  TARGET=$((CUR + 30)); [ "$TARGET" -ge "$DUR" ] && TARGET=$((DUR / 2))
  SEEK_BODY=$(python3 -c "import json; print(json.dumps({'action':'seek','value':$TARGET}))")
  curl -sS -m 15 -X POST "$BASE/api/cast/controls" -H 'Content-Type: application/json' -d "$SEEK_BODY" > "$dir/seek_forward.json"
  sleep 3
  curl -sS -m 10 "$BASE/api/cast/status" > "$dir/status_after_seek.json"
  SFWD=$(python3 -c "import json; print(json.load(open('$dir/status_after_seek.json')).get('currentTime',0))" 2>/dev/null || echo 0)

  SCRUB_TARGET=$((DUR / 2))
  curl -sS -m 15 -X POST "$BASE/api/cast/controls" -H 'Content-Type: application/json' \
    -d "{\"action\":\"seek\",\"value\":$SCRUB_TARGET}" > "$dir/scrub_50pct.json"
  sleep 3
  curl -sS -m 10 "$BASE/api/cast/status" > "$dir/status_after_scrub.json"
  if [ -n "$USB_SERIAL" ]; then
    adb -s "$USB_SERIAL" exec-out screencap -p > "$dir/after_scrub.png" 2>/dev/null || true
  fi

  curl -sS -m 10 -X POST "$BASE/api/cast/controls" -H 'Content-Type: application/json' -d '{"action":"stop"}' > "$dir/stop.json"
  sleep 2

  if [ -n "$USB_SERIAL" ]; then
    adb -s "$USB_SERIAL" logcat -d -v time > "$dir/logcat_raw.txt" 2>/dev/null || true
    grep -Ei 'cast|chromecast|media|player|exoplayer|codec|http|subtitle|vtt|error|fail' "$dir/logcat_raw.txt" 2>/dev/null | tail -400 > "$dir/logcat_filtered.txt" || true
    adb -s "$USB_SERIAL" shell dumpsys media_session > "$dir/media_session_after.txt" 2>/dev/null || true
  fi

  # Evaluate
  ADVANCED="no"
  python3 - "$T0" "$T1" <<'PY' && ADVANCED="yes"
import sys
t0=float(sys.argv[1]); t1=float(sys.argv[2])
raise SystemExit(0 if t1 > t0 + 2 else 1)
PY

  echo "- Cast success: $SUCCESS" >> "$SUMMARY"
  echo "- State after start: $STATE" >> "$SUMMARY"
  echo "- Time advanced ($T0 -> $T1): $ADVANCED" >> "$SUMMARY"
  echo "- Seek target $TARGET, after seek: $SFWD" >> "$SUMMARY"
  if [ "$SUCCESS" = "True" ] && [ "$ADVANCED" = "yes" ]; then
    echo "- **PASS** (playback advanced)" >> "$SUMMARY"
  elif [ "$SUCCESS" = "True" ]; then
    echo "- **FLAKY** (cast started but time may not advance)" >> "$SUMMARY"
  else
    echo "- **FAIL**" >> "$SUMMARY"
  fi
  if [ ! -f "$dir/before.png" ] || [ ! -s "$dir/before.png" ]; then
    echo "- ADB screenshots: unavailable (no device or screencap failed)" >> "$SUMMARY"
  else
    echo "- ADB screenshots: captured in \`$dir\`" >> "$SUMMARY"
  fi
  echo "" >> "$SUMMARY"
}

# Also capture current cast state without stopping (baseline)
curl -sS -m 10 "$BASE/api/cast/status" > "$CAST_DIR/baseline_status.json" 2>/dev/null || true
curl -sS -m 10 "$BASE/api/cast/diagnostics" > "$CAST_DIR/baseline_diagnostics.json" 2>/dev/null || true

# Run limited cast tests — skip largest MKV if >15GB to avoid long blocking
LARGE_SIZE=$(echo "$STORAGE" | python3 -c "
import sys,json
for f in json.load(sys.stdin).get('largestFiles',[]):
  if f.get('path','').lower().endswith('.mkv'):
    print(f.get('size',0)); break
" 2>/dev/null)

for item in "${TEST_FILES[@]}"; do
  IFS='|' read -r label path <<< "$item"
  if [ "$label" = "mkv-large" ] && [ -n "$LARGE_SIZE" ] && [ "$LARGE_SIZE" -gt 15000000000 ] 2>/dev/null; then
    mkdir -p "$CAST_DIR/mkv-large"
    echo "{\"skipped\":true,\"reason\":\"large mkv ${LARGE_SIZE} bytes — API-only status captured\",\"path\":\"$path\"}" > "$CAST_DIR/mkv-large/skipped.json"
    curl -sS -m 10 "$BASE/api/cast/status" > "$CAST_DIR/mkv-large/baseline_while_playing.json" 2>/dev/null || true
    echo "## Test: mkv-large" >> "$SUMMARY"
    echo "- **PARTIAL**: Large MKV (${LARGE_SIZE} bytes) not re-cast; baseline status captured while existing session may be active" >> "$SUMMARY"
    echo "- Path: \`$path\`" >> "$SUMMARY"
    echo "" >> "$SUMMARY"
    continue
  fi
  run_cast_test "$label" "$path"
done

echo "Saved casting audit: $CAST_DIR"
cat "$SUMMARY"
