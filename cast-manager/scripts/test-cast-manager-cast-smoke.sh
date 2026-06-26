#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
BASE_URL="${CAST_MANAGER_URL:-http://127.0.0.1:8004}"

status_file=$(mktemp)
trap 'rm -f "$status_file"' EXIT
curl --fail --silent --show-error --max-time 20 -H 'Accept: application/json' "$BASE_URL/api/cast/status" >"$status_file"
node -e 'const fs=require("fs");const s=JSON.parse(fs.readFileSync(process.argv[1],"utf8"));if(typeof s!=="object")process.exit(1);console.log(`PASS cast status: ${s.state||"idle"}, active=${!!s.activeSession}`)' "$status_file"

active=$(node -e 'const fs=require("fs");const s=JSON.parse(fs.readFileSync(process.argv[1],"utf8"));process.stdout.write(s.activeSession?"1":"0")' "$status_file")
state=$(node -e 'const fs=require("fs");const s=JSON.parse(fs.readFileSync(process.argv[1],"utf8"));process.stdout.write(String(s.state||"idle").toLowerCase())' "$status_file")
if [[ "$active" == "1" && ( "$state" == "playing" || "$state" == "paused" ) ]]; then
  action="play"
  [[ "$state" == "paused" ]] && action="pause"
  curl --fail --silent --show-error --max-time 25 -H 'Accept: application/json' -H 'Content-Type: application/json' -d "{\"action\":\"$action\"}" "$BASE_URL/api/cast/controls" >/dev/null
  echo "PASS idempotent active-session control: $action"
else
  echo "SKIP cast control: no stable active session; no TV state was changed"
fi

rg -q "queuedSeek" frontend/src/stores/castStore.ts
rg -q "await this.control\('seek', target\)" frontend/src/stores/castStore.ts
rg -q "@change=\"onChange\"" frontend/src/components/cast/Scrubber.vue
echo "PASS scrub contract: optimistic drag, one release event, latest target queued"
