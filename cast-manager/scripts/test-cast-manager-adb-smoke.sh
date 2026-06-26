#!/usr/bin/env bash
# ADB smoke test — run on Ubuntu host with Chromecast attached.
set -euo pipefail
cd "$(dirname "$0")/.."
export CAST_MANAGER_URL="${CAST_MANAGER_URL:-http://REDACTED_SERVER_IP:8004}"
export CAST_ADB_SERIAL="${CAST_ADB_SERIAL:-}"

if ! command -v adb >/dev/null 2>&1; then
  echo "SKIP: adb not available on this machine."
  echo "Run on Ubuntu host:"
  echo "  CAST_MANAGER_URL=http://REDACTED_SERVER_IP:8004 CAST_ADB_SERIAL=14291HFDD2RTE3 bash scripts/audit-casting-adb.sh"
  exit 0
fi

devices=$(adb devices 2>/dev/null | awk 'NR>1 && $2=="device"{print $1}' | wc -l | tr -d ' ')
if [[ "${devices:-0}" == "0" ]]; then
  echo "SKIP: no adb devices attached."
  echo "Run on Ubuntu host:"
  echo "  CAST_MANAGER_URL=http://REDACTED_SERVER_IP:8004 CAST_ADB_SERIAL=14291HFDD2RTE3 bash scripts/audit-casting-adb.sh"
  exit 0
fi

if [[ -x scripts/audit-casting-adb.sh ]]; then
  bash scripts/audit-casting-adb.sh
else
  echo "Missing scripts/audit-casting-adb.sh"
  exit 1
fi
