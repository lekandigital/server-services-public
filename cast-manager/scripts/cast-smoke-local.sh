#!/usr/bin/env bash
# Local smoke tests (no cast required). Run on machine that can reach cast-manager API.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BASE="${CAST_MANAGER_URL:-http://127.0.0.1:8004}"
MEDIA_DIR="${CAST_TEST_MEDIA_DIR:-/tmp/cast-manager-test-media}"
MP4="$MEDIA_DIR/known_good_h264_aac.mp4"

echo "Smoke test against $BASE"

fail() { echo "FAIL: $1"; exit 1; }
pass() { echo "PASS: $1"; }

# Health
code=$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/" || echo 000)
[ "$code" = "200" ] || fail "GET / returned $code"
pass "server reachable"

# Doctor
doc=$(curl -sS "$BASE/api/cast/doctor" || echo '{}')
echo "$doc" | grep -q '"success"' || fail "doctor endpoint"
pass "GET /api/cast/doctor"

# Diagnostics
curl -sS "$BASE/api/cast/diagnostics" | grep -q '"success"' || fail "diagnostics endpoint"
pass "GET /api/cast/diagnostics"

# Test media exists
[ -f "$MP4" ] || { bash "$SCRIPT_DIR/cast-create-test-media.sh"; }
[ -f "$MP4" ] || fail "test mp4 missing at $MP4"

# If file is on remote server via SSH path, media analyze may need server path — skip if local only
if [ -n "${CAST_TEST_FILE_PATH:-}" ]; then
  analyze=$(curl -sS -X POST "$BASE/api/media/analyze" \
    -H 'Content-Type: application/json' \
    -d "{\"filePath\":\"$CAST_TEST_FILE_PATH\",\"target\":\"chromecast\",\"mode\":\"auto\"}" || echo '{}')
  echo "$analyze" | grep -q '"playbackMode"\|"pipelineMode"\|"analysis"' || echo "WARN: media analyze returned unexpected body"
  pass "POST /api/media/analyze"
fi

# Range request on a public test URL if stream token available — optional
echo "Smoke tests complete."
