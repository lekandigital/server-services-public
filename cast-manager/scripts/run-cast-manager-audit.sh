#!/usr/bin/env bash
# Master Cast Manager audit runner.
set +e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT" || exit 1

AUDIT_TS="${AUDIT_TS:-$(date +%Y%m%d-%H%M%S)}"
AUDIT_DIR="$ROOT/diagnostics/cast-manager-audit/$AUDIT_TS"
mkdir -p "$AUDIT_DIR"
export AUDIT_DIR

BASE="${CAST_MANAGER_URL:-}"
if [ -z "$BASE" ]; then
  curl -sS -m 3 http://127.0.0.1:8004/api/cast/status >/dev/null 2>&1 && BASE="http://127.0.0.1:8004" || BASE="http://REDACTED_SERVER_IP:8004"
fi
export CAST_MANAGER_URL="$BASE"

echo "$AUDIT_TS" > "$ROOT/diagnostics/cast-manager-audit/LATEST_TS.txt"
ln -sfn "$AUDIT_DIR" "$ROOT/diagnostics/cast-manager-audit/latest"

{
  echo "=== Cast Manager Audit Run ==="
  echo "timestamp: $AUDIT_TS"
  echo "audit_dir: $AUDIT_DIR"
  echo "base_url: $BASE"
  echo "host: $(hostname)"
  echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)"
  echo ""
  echo "=== Phase 1 baseline ==="
  git status
  git branch
  git log --oneline -5
  pwd
  node -v
  npm -v
} | tee "$AUDIT_DIR/baseline.txt"

chmod +x "$SCRIPT_DIR"/audit-*.sh

PHASES=(
  "audit-env.sh"
  "audit-api-endpoints.sh"
  "audit-media-library.sh"
  "audit-browser-streaming.sh"
  "audit-thumbnails-previews.sh"
  "audit-subtitles.sh"
  "audit-portal-ui.sh"
  "audit-casting-adb.sh"
)

for p in "${PHASES[@]}"; do
  echo ""
  echo "======== Running $p ========"
  bash "$SCRIPT_DIR/$p" 2>&1 | tee "$AUDIT_DIR/run-${p%.sh}.log"
done

echo ""
echo "Audit complete: $AUDIT_DIR"
