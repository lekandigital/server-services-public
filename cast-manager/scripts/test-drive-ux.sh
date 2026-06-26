#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
npm run frontend:build >/dev/null

started_server=0
server_pid=""
if [[ -z "${CAST_MANAGER_URL:-}" ]]; then
  export CAST_MANAGER_URL="http://127.0.0.1:4174"
  PORT=4174 node server.js > /tmp/cast-manager-drive-ux-server.log 2>&1 &
  server_pid=$!
  started_server=1
  trap '[[ -n "${server_pid:-}" ]] && kill "$server_pid" 2>/dev/null || true' EXIT
  for _ in {1..40}; do
    if curl -fsS "$CAST_MANAGER_URL" >/dev/null 2>&1; then break; fi
    sleep 0.25
  done
fi

npx playwright test --config tests/playwright.drive-ux.config.ts "$@"

if [[ "$started_server" == "1" ]]; then
  kill "$server_pid" 2>/dev/null || true
  server_pid=""
fi
