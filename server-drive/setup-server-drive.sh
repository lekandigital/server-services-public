#!/usr/bin/env bash
#
# Server Drive — server-side setup.
#
# Installs Samba, renders /etc/samba/smb.conf from smb.conf.template, advertises
# the share over Bonjour, and enables smbd. Idempotent: safe to re-run.
#
#   sudo ./setup-server-drive.sh [username]
#
# The Samba password is prompted for interactively, or read from $SMB_PASSWORD
# if exported. It is never written to this repo.
#
set -euo pipefail

SHARE_USER="${1:-o}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SMB_CONF="/etc/samba/smb.conf"
AVAHI_SERVICE="/etc/avahi/services/smb.service"

info() {
    printf '[server-drive] %s\n' "$1"
}

fail() {
    printf '[server-drive] ERROR: %s\n' "$1" >&2
    exit 1
}

if [ "$(id -u)" -ne 0 ]; then
    fail "Run with sudo, for example: sudo $0 ${SHARE_USER}"
fi

if ! id "$SHARE_USER" >/dev/null 2>&1; then
    fail "User '${SHARE_USER}' does not exist."
fi

[ -f "${SCRIPT_DIR}/smb.conf.template" ] || fail "Missing ${SCRIPT_DIR}/smb.conf.template"
[ -f "${SCRIPT_DIR}/avahi-smb.service" ] || fail "Missing ${SCRIPT_DIR}/avahi-smb.service"

# ----------------------------------------------------------
# Detect the LAN subnet from the default route
# ----------------------------------------------------------
LAN_IFACE="$(ip route show default | awk '/default/ {print $5; exit}')"
[ -n "$LAN_IFACE" ] || fail "No default route — cannot determine the LAN interface."

LAN_CIDR="$(ip -o -f inet addr show "$LAN_IFACE" \
    | awk '{print $4; exit}' \
    | python3 -c 'import ipaddress,sys; print(ipaddress.ip_network(sys.stdin.read().strip(), strict=False))')"
[ -n "$LAN_CIDR" ] || fail "Could not determine the subnet for ${LAN_IFACE}."

SERVER_NAME="$(hostname -s)"

info "LAN interface: ${LAN_IFACE} (${LAN_CIDR})"
info "Share user:    ${SHARE_USER}"

# ----------------------------------------------------------
# 1. Install Samba
# ----------------------------------------------------------
if dpkg -s samba >/dev/null 2>&1; then
    info "Samba already installed."
else
    info "Installing Samba..."
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq samba
fi

# ----------------------------------------------------------
# 2. Render smb.conf (backing up whatever was there)
# ----------------------------------------------------------
mkdir -p /etc/samba
if [ -f "$SMB_CONF" ]; then
    BACKUP="${SMB_CONF}.bak.$(date +%Y%m%d-%H%M%S)"
    cp -a "$SMB_CONF" "$BACKUP"
    info "Backed up existing config to ${BACKUP}"
fi

TMP_CONF="$(mktemp)"
trap 'rm -f "$TMP_CONF"' EXIT

sed -e "s|__LAN_CIDR__|${LAN_CIDR}|g" \
    -e "s|__SHARE_USER__|${SHARE_USER}|g" \
    -e "s|__SERVER_NAME__|${SERVER_NAME}|g" \
    "${SCRIPT_DIR}/smb.conf.template" > "$TMP_CONF"

# Validate before touching the live config or any service.
if ! testparm -s "$TMP_CONF" >/dev/null 2>/tmp/server-drive-testparm.err; then
    cat /tmp/server-drive-testparm.err >&2
    fail "Rendered smb.conf failed testparm validation; live config untouched."
fi

install -m 0644 "$TMP_CONF" "$SMB_CONF"
info "Wrote ${SMB_CONF}"

# ----------------------------------------------------------
# 3. Samba password for the share user
# ----------------------------------------------------------
if pdbedit -L 2>/dev/null | cut -d: -f1 | grep -qx "$SHARE_USER"; then
    info "Samba user '${SHARE_USER}' already exists — leaving the password alone."
    info "To change it later: sudo smbpasswd ${SHARE_USER}"
else
    if [ -n "${SMB_PASSWORD:-}" ]; then
        info "Setting Samba password for '${SHARE_USER}' from \$SMB_PASSWORD..."
        printf '%s\n%s\n' "$SMB_PASSWORD" "$SMB_PASSWORD" | smbpasswd -s -a "$SHARE_USER" >/dev/null
    else
        info "Set a Samba password for '${SHARE_USER}' (this is NOT the Linux login password):"
        smbpasswd -a "$SHARE_USER"
    fi
    smbpasswd -e "$SHARE_USER" >/dev/null
    info "Samba user '${SHARE_USER}' enabled."
fi

# ----------------------------------------------------------
# 4. Bonjour advertisement
# ----------------------------------------------------------
if [ -d /etc/avahi/services ]; then
    install -m 0644 "${SCRIPT_DIR}/avahi-smb.service" "$AVAHI_SERVICE"
    info "Installed ${AVAHI_SERVICE}"
    systemctl reload avahi-daemon 2>/dev/null || systemctl restart avahi-daemon || true
else
    info "WARNING: avahi is not installed — the server will not auto-appear in Finder."
    info "         Connect manually with: smb://${SHARE_USER}@$(ip -o -f inet addr show "$LAN_IFACE" | awk '{print $4}' | cut -d/ -f1)/Server"
fi

# ----------------------------------------------------------
# 5. Services
# ----------------------------------------------------------
# nmbd is NetBIOS name service (SMB1-era); Bonjour replaces it for macOS.
systemctl mask --now nmbd 2>/dev/null || true
systemctl mask samba-ad-dc 2>/dev/null || true

systemctl enable smbd >/dev/null 2>&1 || true
systemctl restart smbd
info "smbd restarted."

# Recycle bin for the share. Root-owned and world-inaccessible: it holds files
# deleted from anywhere on the filesystem, including root-only paths.
mkdir -p /.recycle
chmod 0700 /.recycle
chown root:root /.recycle
info "Recycle bin ready at /.recycle (30-day retention)."

# ----------------------------------------------------------
# 5b. Timers: recycle prune + rebind watchdog
# ----------------------------------------------------------
if [ -d "${SCRIPT_DIR}/systemd" ]; then
    for unit in server-drive-recycle-prune server-drive-rebind; do
        for ext in service timer; do
            src="${SCRIPT_DIR}/systemd/${unit}.${ext}"
            [ -f "$src" ] || continue
            sed -e "s|__INSTALL_DIR__|$(dirname "$SCRIPT_DIR")|g" \
                "$src" > "/etc/systemd/system/${unit}.${ext}"
        done
    done
    systemctl daemon-reload
    systemctl enable --now server-drive-recycle-prune.timer >/dev/null 2>&1 \
        && info "Recycle prune timer enabled (daily)." \
        || info "WARNING: could not enable server-drive-recycle-prune.timer"
    systemctl enable --now server-drive-rebind.timer >/dev/null 2>&1 \
        && info "Rebind watchdog enabled (every 5 min)." \
        || info "WARNING: could not enable server-drive-rebind.timer"
fi

sleep 1
if ! systemctl is-active --quiet smbd; then
    fail "smbd failed to start — check: journalctl -u smbd -n 50"
fi

# ----------------------------------------------------------
# Done
# ----------------------------------------------------------
LAN_IP="$(ip -o -f inet addr show "$LAN_IFACE" | awk '{print $4}' | cut -d/ -f1)"
echo ""
info "Server Drive is up."
info "  Finder:  smb://${SHARE_USER}@${LAN_IP}/Server"
info "  Health:  sudo ${SCRIPT_DIR}/check-server-drive.sh"
echo ""
