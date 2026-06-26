#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
npm run frontend:build
export CAST_MANAGER_URL="${CAST_MANAGER_URL:-http://127.0.0.1:8004}"
bash scripts/test-cast-manager-playwright.sh
