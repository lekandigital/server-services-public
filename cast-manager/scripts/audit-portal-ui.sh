#!/usr/bin/env bash
# Playwright portal UI audit (installs playwright locally if needed).
set +e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AUDIT_DIR="${AUDIT_DIR:-$ROOT/diagnostics/cast-manager-audit/latest}"
UI_DIR="$AUDIT_DIR/portal-ui"
mkdir -p "$UI_DIR"

BASE="${CAST_MANAGER_URL:-}"
if [ -z "$BASE" ]; then
  curl -sS -m 3 http://127.0.0.1:8004/api/cast/status >/dev/null 2>&1 && BASE="http://127.0.0.1:8004" || BASE="http://REDACTED_SERVER_IP:8004"
fi

export CAST_MANAGER_URL="$BASE"
export AUDIT_UI_DIR="$UI_DIR"

cd "$ROOT" || exit 1

# Install playwright if missing
if ! node -e "require('playwright')" 2>/dev/null; then
  echo "Installing playwright as devDependency..."
  npm install --no-save playwright@1.49.1 2>&1 | tail -5
  npx playwright install chromium 2>&1 | tail -5
fi

node "$SCRIPT_DIR/audit-portal-ui.mjs" 2>&1 | tee "$UI_DIR/run.log"
EXIT=$?

# Generate markdown report from JSON if script produced it
if [ -f "$UI_DIR/portal-ui-results.json" ]; then
  python3 <<PY > "$AUDIT_DIR/portal-ui-report.md"
import json
with open("$UI_DIR/portal-ui-results.json") as f:
    d=json.load(f)
print("# Portal UI Audit Report")
print(f"URL: {d.get('baseUrl')}")
print(f"Date: {d.get('timestamp')}")
print()
print("## Console errors")
for e in d.get("consoleErrors",[])[:30]:
    print(f"- {e}")
print()
print("## Network failures")
for e in d.get("networkFailures",[])[:40]:
    print(f"- {e.get('method','')} {e.get('url','')} -> {e.get('status','')} {e.get('failure','')}")
print()
print("## Non-JSON API responses")
for e in d.get("nonJsonApi",[])[:20]:
    print(f"- {e}")
print()
print("## Screenshots")
for s in d.get("screenshots",[]):
    print(f"- {s}")
print()
print("## Section results")
for name, info in d.get("sections",{}).items():
    print(f"### {name}")
    print(f"- loaded: {info.get('loaded')}")
    if info.get("errors"): print(f"- errors: {info['errors']}")
print()
print("## Video playback test")
vp = d.get("videoPlayback",{})
for k,v in vp.items():
    print(f"- {k}: {v}")
print()
print("## Recent POST test")
rt = d.get("recentPostTest",{})
for k,v in rt.items():
    print(f"- {k}: {v}")
print()
print("## Thumbnails")
for t in d.get("thumbnailTests",[])[:15]:
    print(f"- {t}")
PY
fi

echo "Portal UI audit exit: $EXIT"
echo "Saved: $AUDIT_DIR/portal-ui-report.md"
exit $EXIT
