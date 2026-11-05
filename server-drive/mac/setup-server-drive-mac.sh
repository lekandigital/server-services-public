#!/bin/bash
#
# Server Drive — Mac-side setup.
#
#   ./setup-server-drive-mac.sh ["host [fallback ...]"] [username] [share] [tailscale-host]
#
# Installs an auto-mount LaunchAgent so the server shows up in Finder under
# Locations. Idempotent: safe to re-run.
#
# Credentials are established by connecting once through Finder rather than by
# writing the Keychain entry directly: NetAuthAgent ignores items created by the
# `security` CLI (they lack the right ACL/partition list), so a programmatic
# entry looks fine but still triggers an auth dialog at mount time.
#
set -uo pipefail

# Tried in order by the mount helper: the box's wired port first, its wifi
# second (the wifi card wedges; see rtx3090 notes). Setup connects to the first.
SERVER_HOSTS="${1:-REDACTED_SERVER_WIRED_IP REDACTED_SERVER_IP}"
SERVER_HOST="${SERVER_HOSTS%% *}"
SERVER_USER="${2:-o}"
SERVER_SHARE="${3:-Server}"
# Optional fallback address used when the LAN one is unreachable — a tailnet IP
# or MagicDNS name. Empty means LAN-only.
SERVER_TAILSCALE="${4:-${SERVER_TAILSCALE:-}}"
# A Finder-sidebar (Bonjour) connection to this host counts as mounted.
SERVER_BONJOUR="${SERVER_BONJOUR:-o-MacPro}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SUPPORT_DIR="${HOME}/Library/Application Support/ServerDrive"
AGENT_LABEL="com.lekan.serverdrive"
AGENT_PLIST="${HOME}/Library/LaunchAgents/${AGENT_LABEL}.plist"
LOG_FILE="${HOME}/Library/Logs/server-drive.log"

info() { printf '[server-drive] %s\n' "$1"; }
fail() { printf '[server-drive] ERROR: %s\n' "$1" >&2; exit 1; }

is_mounted() {
    /sbin/mount | grep -q "//${SERVER_USER}@${SERVER_HOST}/${SERVER_SHARE} on "
}

mountpoint_of() {
    /sbin/mount | grep "//${SERVER_USER}@${SERVER_HOST}/${SERVER_SHARE} on " \
        | sed 's/.* on \(.*\) (.*/\1/'
}

[ "$(uname -s)" = "Darwin" ] || fail "This script is for macOS. Run setup-server-drive.sh on the server."
[ "$(id -u)" -ne 0 ] || fail "Do NOT run this with sudo — the Keychain entry and LaunchAgent belong to your user."

info "Server: smb://${SERVER_USER}@${SERVER_HOST}/${SERVER_SHARE}"

# ----------------------------------------------------------
# 1. Reachability
# ----------------------------------------------------------
if /usr/bin/nc -z -G 2 "$SERVER_HOST" 445 >/dev/null 2>&1; then
    info "Server is reachable (SMB port open)."
else
    fail "${SERVER_HOST}:445 is not reachable. Connect to the home network (and check smbd on the box) and re-run."
fi

# ----------------------------------------------------------
# 2. Stop Finder littering .DS_Store across the server filesystem
# ----------------------------------------------------------
# This share is root-writable and covers /etc, /usr, /var — without this,
# simply browsing those directories writes .DS_Store files into them.
defaults write com.apple.desktopservices DSDontWriteNetworkStores -bool true
info "Disabled .DS_Store creation on network volumes."

# ----------------------------------------------------------
# 3. Install the mount helper + config
# ----------------------------------------------------------
mkdir -p "$SUPPORT_DIR" "$(dirname "$LOG_FILE")"
install -m 0755 "${SCRIPT_DIR}/server-drive-mount.sh" "${SUPPORT_DIR}/server-drive-mount.sh"

cat > "${SUPPORT_DIR}/config.sh" <<EOF
# Written by setup-server-drive-mac.sh — edit and reload the agent to change.
SERVER_HOSTS="${SERVER_HOSTS}"
SERVER_USER="${SERVER_USER}"
SERVER_SHARE="${SERVER_SHARE}"
SERVER_TAILSCALE="${SERVER_TAILSCALE}"
SERVER_BONJOUR="${SERVER_BONJOUR}"
EOF
chmod 0644 "${SUPPORT_DIR}/config.sh"
info "Installed mount helper to ${SUPPORT_DIR}"

# ----------------------------------------------------------
# 4. First connection (establishes the Keychain entry)
# ----------------------------------------------------------
# The agent is installed only after credentials are proven to work, so it can
# never sit there popping auth dialogs on a timer.
if is_mounted; then
    info "Already mounted at $(mountpoint_of)"
else
    echo ""
    info "Finder will now ask for the Samba password for '${SERVER_USER}'."
    info "TICK 'Remember this password in my keychain' — that is what makes"
    info "every future mount silent. This is the only time you type it."
    echo ""

    open "smb://${SERVER_USER}@${SERVER_HOST}/${SERVER_SHARE}"

    info "Waiting for the connection (up to 3 minutes)..."
    CONNECTED=0
    for _ in $(seq 1 90); do
        if is_mounted; then
            CONNECTED=1
            break
        fi
        sleep 2
    done

    if [ "$CONNECTED" -ne 1 ]; then
        echo ""
        info "Not connected. If the password was rejected, reset it on the server:"
        info "  ssh -t ${SERVER_USER}@${SERVER_HOST} 'sudo smbpasswd ${SERVER_USER}'"
        info "then re-run this script."
        exit 1
    fi
    info "Connected."
fi

# Confirm the credentials actually persisted — otherwise the agent is useless.
if security find-internet-password -a "$SERVER_USER" -s "$SERVER_HOST" -r "smb " >/dev/null 2>&1; then
    info "Keychain entry present — future mounts will be silent."
else
    info "WARNING: no Keychain entry found. The 'Remember this password in my"
    info "         keychain' box was probably not ticked. The drive works now,"
    info "         but will ask again after reboot. Re-run this script and tick it."
fi

# ----------------------------------------------------------
# 5. Install and load the LaunchAgent
# ----------------------------------------------------------
mkdir -p "$(dirname "$AGENT_PLIST")"
sed -e "s|__SUPPORT_DIR__|${SUPPORT_DIR}|g" \
    -e "s|__LOG_FILE__|${LOG_FILE}|g" \
    "${SCRIPT_DIR}/${AGENT_LABEL}.plist" > "$AGENT_PLIST"
chmod 0644 "$AGENT_PLIST"

plutil -lint "$AGENT_PLIST" >/dev/null 2>&1 || fail "Generated LaunchAgent plist is malformed: ${AGENT_PLIST}"

launchctl bootout "gui/$(id -u)/${AGENT_LABEL}" >/dev/null 2>&1 || true
if launchctl bootstrap "gui/$(id -u)" "$AGENT_PLIST" 2>/dev/null; then
    info "Auto-mount agent loaded (${AGENT_LABEL})."
else
    fail "Could not load the LaunchAgent. Try: launchctl bootstrap gui/$(id -u) ${AGENT_PLIST}"
fi

echo ""
info "Server Drive is set up."
info "  Mounted at:  $(mountpoint_of)"
info "  In Finder:   sidebar → Locations → ${SERVER_SHARE}"
info "  Logs:        ${LOG_FILE}"
if [ "$SERVER_HOSTS" != "$SERVER_HOST" ]; then
    info "  Fallbacks:   ${SERVER_HOSTS#* } (each needs its own Keychain entry —"
    info "               connect to it once via Finder and tick Remember)"
fi
if [ -n "$SERVER_TAILSCALE" ]; then
    info "  Off-LAN:     falls back to ${SERVER_TAILSCALE} via Tailscale"
    info ""
    info "  Keychain entries are per-host, so the first off-LAN mount will ask"
    info "  for the password once more. Tick 'Remember this password in my"
    info "  keychain' again and it stays silent from then on."
fi
open "$(mountpoint_of)" 2>/dev/null || true
