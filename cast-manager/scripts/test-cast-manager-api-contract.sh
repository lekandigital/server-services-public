#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
BASE_URL="${CAST_MANAGER_URL:-http://127.0.0.1:8004}"
tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT
fail=0
LAST_OUTPUT=''
pids=()

request() {
  local label="$1" method="$2" path="$3" body="${4:-}" timeout="${5:-30}"
  local key headers output
  key=$(printf '%s' "$label" | tr -cs 'A-Za-z0-9' '_')
  headers="$tmp_dir/headers-$key"
  output="$tmp_dir/body-$key"
  LAST_OUTPUT="$output"
  local args=(--silent --show-error --max-time "$timeout" -D "$headers" -o "$output" -w '%{http_code}' -X "$method" -H 'Accept: application/json')
  if [[ -n "$body" ]]; then args+=(-H 'Content-Type: application/json' --data "$body"); fi
  local code
  code=$(curl "${args[@]}" "$BASE_URL$path" || true)
  local content_type
  content_type=$(awk -F': ' 'tolower($1)=="content-type"{print tolower($2)}' "$headers" | tr -d '\r' | tail -1)
  if [[ "$code" =~ ^2 ]] && [[ "$content_type" == *json* ]] && node -e 'JSON.parse(require("fs").readFileSync(process.argv[1],"utf8"))' "$output" 2>/dev/null; then
    echo "PASS $label ($code)"
  else
    echo "FAIL $label ($code, content-type=$content_type)"
    head -c 240 "$output" 2>/dev/null || true; echo
    touch "$tmp_dir/fail-$key"
  fi
}

request 'GET /api/config' GET '/api/config'
request 'GET /api/storage/stats' GET '/api/storage/stats' '' 75 & pids+=("$!")
request 'GET /api/files?path=/home/REDACTED_USER/watch_list' GET '/api/files?path=%2Fhome%2Fo%2Fwatch_list' '' 30
cp "$LAST_OUTPUT" "$tmp_dir/files.json"
request 'GET /api/files/recent' GET '/api/files/recent'
request 'POST /api/files/recent' POST '/api/files/recent' '{"path":"/home/REDACTED_USER/watch_list","action":"open","type":"folder"}'

sample=$(node -e 'const fs=require("fs");const d=JSON.parse(fs.readFileSync(process.argv[1],"utf8"));const f=(d.files||[]).find(x=>/\.(mkv|mp4|m4v|mov|webm|avi)$/i.test(x.path||""));process.stdout.write(f?.path||"")' "$tmp_dir/files.json")
if [[ -n "$sample" ]]; then
  encoded=$(node -e 'process.stdout.write(encodeURIComponent(process.argv[1]))' "$sample")
  sample_json=$(node -e 'process.stdout.write(JSON.stringify({filePath:process.argv[1]}))' "$sample")
  thumb_json=$(node -e 'process.stdout.write(JSON.stringify({filePath:process.argv[1],type:"video"}))' "$sample")
  request 'GET /api/media/info?path=sample' GET "/api/media/info?path=$encoded" '' 45 & pids+=("$!")
  request 'POST /api/media/analyze' POST '/api/media/analyze' "$sample_json" 45 & pids+=("$!")
  request 'POST /api/thumbnail' POST '/api/thumbnail' "$thumb_json" 75 & pids+=("$!")
else
  echo 'SKIP media info/analyze/thumbnail: no video in media-root listing'
fi

request 'GET /api/cast/status' GET '/api/cast/status'
cp "$LAST_OUTPUT" "$tmp_dir/status.json"
request 'GET /api/cast/doctor' GET '/api/cast/doctor' '' 45
request 'GET /api/cast/devices' GET '/api/cast/devices' '' 30

active=$(node -e 'const fs=require("fs");const s=JSON.parse(fs.readFileSync(process.argv[1],"utf8"));process.stdout.write(s.activeSession?"1":"0")' "$tmp_dir/status.json" 2>/dev/null || echo 0)
state=$(node -e 'const fs=require("fs");const s=JSON.parse(fs.readFileSync(process.argv[1],"utf8"));process.stdout.write(String(s.state||"idle").toLowerCase())' "$tmp_dir/status.json" 2>/dev/null || echo idle)
if [[ "$active" == "1" && ( "$state" == "playing" || "$state" == "paused" ) ]]; then
  action=play; [[ "$state" == "paused" ]] && action=pause
  request 'POST /api/cast/controls (idempotent)' POST '/api/cast/controls' "{\"action\":\"$action\"}" 30
else
  echo 'SKIP POST /api/cast/controls: no active stable session'
fi

request 'POST /api/url/analyze regression embed' POST '/api/url/analyze' '{"url":"https://ntvs.cx/embed?t=regression"}'
request 'GET /api/torrents' GET '/api/torrents' '' 30
request 'GET /api/files/starred' GET '/api/files/starred'
request 'GET /api/files/trash' GET '/api/files/trash'
request 'GET /api/shares' GET '/api/shares'
request 'GET /api/activity' GET '/api/activity'
request 'GET /api/disk' GET '/api/disk' '' 30
request 'GET /api/search?q=mkv' GET '/api/search?q=mkv' '' 30

for pid in "${pids[@]}"; do wait "$pid"; done
if compgen -G "$tmp_dir/fail-*" >/dev/null; then fail=1; fi

exit "$fail"
