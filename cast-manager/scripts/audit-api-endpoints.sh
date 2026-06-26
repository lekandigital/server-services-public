#!/usr/bin/env bash
set +e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AUDIT_DIR="${AUDIT_DIR:-$ROOT/diagnostics/cast-manager-audit/latest}"
mkdir -p "$AUDIT_DIR"
export AUDIT_DIR
export CAST_MANAGER_URL="${CAST_MANAGER_URL:-}"
if [ -z "$CAST_MANAGER_URL" ]; then
  curl -sS -m 3 http://127.0.0.1:8004/api/cast/status >/dev/null 2>&1 && export CAST_MANAGER_URL="http://127.0.0.1:8004" || export CAST_MANAGER_URL="http://REDACTED_SERVER_IP:8004"
fi
python3 "$SCRIPT_DIR/audit-api-endpoints.py"
