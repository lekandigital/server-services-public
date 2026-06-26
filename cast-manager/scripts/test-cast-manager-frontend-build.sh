#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
npm run frontend:build
test -f public/app/index.html
test -f public/app/assets/index-*.js
echo "OK: frontend build artifacts present"
