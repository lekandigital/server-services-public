#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export CAST_MANAGER_URL="${CAST_MANAGER_URL:-http://127.0.0.1:8004}"
npx --prefix frontend playwright test --config frontend/playwright.config.ts "$@"
