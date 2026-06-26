#!/usr/bin/env bash
# Cast ppv.to embed via no-sandbox relay + ADB play tap (REDACTED_DEVICE)
set -euo pipefail
REMOTE="${REMOTE:-o@REDACTED_SERVER_IP}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/pinn_rtx3090}"
EMBED="${EMBED:-https://embedindia.st/embed/wc/2026-06-18/sui-bih}"
RELAY_NAME="${RELAY_NAME:-ppv-relay.html}"
CAST_DEVICE="${CAST_DEVICE:-REDACTED_DEVICE}"
ADB_DEV="${ADB_DEV:-REDACTED_CHROMECAST_IP:5555}"
CAST_MANAGER="/home/REDACTED_USER/cast_manager_v3"
CATT="/home/REDACTED_USER/.local/bin/catt"

ssh -i "$SSH_KEY" "$REMOTE" "cat > ${CAST_MANAGER}/public/app/${RELAY_NAME}" <<HTML
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stream</title>
<style>
  html, body { margin: 0; padding: 0; width: 100%; height: 100%; background: #000; overflow: hidden; }
  iframe { position: fixed; inset: 0; width: 100%; height: 100%; border: 0; }
</style>
</head>
<body>
<iframe
  src="${EMBED}"
  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
  allowfullscreen
  scrolling="no"
></iframe>
</body>
</html>
HTML

ssh -i "$SSH_KEY" "$REMOTE" bash -s -- "$CAST_DEVICE" "$RELAY_NAME" "$ADB_DEV" "$CATT" <<'EOF'
set -e
CAST_DEVICE="$1"
RELAY_NAME="$2"
ADB_DEV="$3"
CATT="$4"
"$CATT" -d "$CAST_DEVICE" stop 2>/dev/null || true
"$CATT" -d "$CAST_DEVICE" cast_site "http://REDACTED_SERVER_IP:8004/${RELAY_NAME}"
sleep 20
for _ in 1 2 3; do adb -s "$ADB_DEV" shell input tap 960 540; sleep 1; done
adb -s "$ADB_DEV" shell input keyevent KEYCODE_DPAD_CENTER
echo "Casting ${RELAY_NAME} -> ${CAST_DEVICE}"
EOF
