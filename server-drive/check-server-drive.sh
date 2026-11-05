#!/usr/bin/env bash
#
# Server Drive — health check.
#
#   sudo ./check-server-drive.sh [username]
#
# No `pipefail` here on purpose: this script is full of `cmd | grep -q` probes,
# and grep -q exiting early SIGPIPEs the producer, which pipefail would then
# report as a failed check.
set -u

SHARE_USER="${1:-o}"
FAILURES=0

ok()   { printf '  \033[0;32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[0;31m✗\033[0m %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
warn() { printf '  \033[1;33m!\033[0m %s\n' "$1"; }
head_() { printf '\n\033[0;34m%s\033[0m\n' "$1"; }

head_ "smbd"
if systemctl is-active --quiet smbd; then
    ok "smbd is active"
else
    bad "smbd is not active — journalctl -u smbd -n 50"
fi

if systemctl is-enabled --quiet smbd 2>/dev/null; then
    ok "smbd is enabled at boot"
else
    warn "smbd is not enabled at boot"
fi

head_ "Configuration"
if testparm -s /etc/samba/smb.conf >/dev/null 2>&1; then
    ok "smb.conf passes testparm"
else
    bad "smb.conf fails testparm"
fi

if testparm -s /etc/samba/smb.conf 2>/dev/null | grep -q '^\[Server\]'; then
    ok "[Server] share is defined"
else
    bad "[Server] share is missing from smb.conf"
fi

head_ "Network bindings"
LISTEN="$(ss -tlnH 2>/dev/null | awk '$4 ~ /:445$/ {print $4}')"
if [ -n "$LISTEN" ]; then
    while read -r addr; do
        ok "listening on ${addr}"
    done <<< "$LISTEN"
else
    bad "nothing is listening on port 445"
fi

# The whole point of `bind interfaces only` — the share must never be reachable
# over the ProtonVPN tunnel.
VPN_ADDRS="$(ip -o -f inet addr show proton0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)"
if [ -z "$VPN_ADDRS" ]; then
    ok "no proton0 interface present"
else
    LEAKED=0
    while read -r vpn_ip; do
        if grep -q "^${vpn_ip}:445$" <<< "$LISTEN"; then
            bad "SMB is listening on VPN address ${vpn_ip} — fix the 'interfaces' line in smb.conf"
            LEAKED=1
        fi
    done <<< "$VPN_ADDRS"
    [ "$LEAKED" -eq 0 ] && ok "not listening on any proton0 address"
fi

head_ "Share visibility"
if command -v smbclient >/dev/null 2>&1; then
    if smbclient -L localhost -U "$SHARE_USER" -N 2>/dev/null | grep -q 'Server'; then
        ok "share 'Server' is listed (anonymous listing)"
    else
        warn "no anonymous share listing — expected, guest access is off (verify: smbclient -L localhost -U ${SHARE_USER})"
    fi
else
    warn "smbclient not installed — skipping share listing"
fi

if pdbedit -L 2>/dev/null | cut -d: -f1 | grep -qx "$SHARE_USER"; then
    ok "Samba user '${SHARE_USER}' exists"
else
    bad "Samba user '${SHARE_USER}' does not exist — run: sudo smbpasswd -a ${SHARE_USER}"
fi

head_ "Bonjour"
if [ -f /etc/avahi/services/smb.service ]; then
    ok "/etc/avahi/services/smb.service installed"
else
    bad "Bonjour service file missing — server will not auto-appear in Finder"
fi

if systemctl is-active --quiet avahi-daemon; then
    ok "avahi-daemon is active"
    if command -v avahi-browse >/dev/null 2>&1; then
        # Browse the type directly — `avahi-browse -a` enumerates every service
        # on the network and routinely times out before reaching _smb._tcp.
        if timeout 10 avahi-browse _smb._tcp -t -p 2>/dev/null | grep -q '^+'; then
            ok "_smb._tcp is being advertised"
        else
            warn "_smb._tcp not seen in browse output (may take a moment to propagate)"
        fi
    fi
else
    bad "avahi-daemon is not active"
fi

head_ "Result"
if [ "$FAILURES" -eq 0 ]; then
    # Not `hostname -I` — on this box that lists the ProtonVPN address first,
    # which is precisely the address the share is NOT reachable on.
    LAN_IFACE="$(ip route show default | awk '/default/ {print $5; exit}')"
    LAN_IP="$(ip -o -f inet addr show "$LAN_IFACE" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)"
    ok "All checks passed — smb://${SHARE_USER}@${LAN_IP:-<server-ip>}/Server"
    exit 0
fi
bad "${FAILURES} check(s) failed"
exit 1
