#!/usr/bin/env bash
#
# Server Drive — rebind watchdog.
#
#   sudo ./rebind-check.sh [--dry-run]
#
# `bind interfaces only = yes` makes smbd bind the addresses that exist when it
# starts. An interface that gets its address later — the Anker dock plugged in,
# or a DHCP change on enp4s0/wlp2s0 — is therefore invisible to a long-running
# smbd until it is restarted.
#
# This compares the addresses smbd *should* be serving against the ones it
# actually holds, and restarts it when they diverge. Run every few minutes by
# server-drive-rebind.timer.
#
# A restart drops every connected Finder session, so it is rationed hard:
#   - "should" comes from the `interfaces` line in smb.conf itself, so the
#     watchdog can never expect an address smbd was not told to bind.
#   - never while a client is connected: that client is being served already,
#     and a missing address is retried on the first tick with nobody on.
#   - at most once per distinct set of missing addresses. If a restart did not
#     bind them, smbd cannot, and restarting again changes nothing. (The old
#     10-minute cooldown restarted forever on an unbindable tailscale0 address:
#     144 dropped connections a day.)
#
set -uo pipefail

STATE="/run/server-drive-rebind.missing"   # missing set the last restart tried
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

info() { printf '[rebind] %s\n' "$1"; }

if ! systemctl is-active --quiet smbd; then
    info "smbd is not active — nothing to rebind."
    exit 0
fi

# Expected: the global IPv4 addresses of every interface the `interfaces` line
# names (device names and globs; `lo` and CIDR entries are skipped). IPv6 is
# ignored — privacy addresses rotate daily and `hosts allow` is IPv4-only.
read -r -a PATTERNS <<< "$(testparm -s --parameter-name=interfaces 2>/dev/null)"
EXPECTED=""
while read -r ifname cidr; do
    for pattern in "${PATTERNS[@]}"; do
        [ "$pattern" = "lo" ] && continue
        case "$pattern" in */*) continue ;; esac
        # shellcheck disable=SC2053  # glob match is the point
        if [[ "$ifname" == $pattern ]]; then
            EXPECTED="${EXPECTED}${cidr%/*}"$'\n'
            break
        fi
    done
done < <(ip -o -4 addr show scope global | awk '{print $2, $4}')

if [ -z "${EXPECTED//[[:space:]]/}" ]; then
    info "No expected addresses found — skipping."
    exit 0
fi

ACTUAL="$(ss -tlnH 2>/dev/null | awk '$4 ~ /:445$/ {sub(/:445$/, "", $4); print $4}')"

MISSING=""
while read -r addr; do
    [ -z "$addr" ] && continue
    if ! grep -qxF "$addr" <<< "$ACTUAL"; then
        MISSING="${MISSING}${addr} "
    fi
done <<< "$EXPECTED"

if [ -z "$MISSING" ]; then
    rm -f "$STATE"
    exit 0
fi

if [ -f "$STATE" ] && [ "$(cat "$STATE")" = "$MISSING" ]; then
    # Already restarted once for exactly these and they did not bind. Stay
    # quiet so the journal is not one line every 5 minutes forever.
    exit 0
fi

info "smbd is not bound to: ${MISSING}"

SESSIONS="$(smbstatus -b 2>/dev/null | grep -cE '^[0-9]+[[:space:]]')"
if [ "${SESSIONS:-0}" -gt 0 ]; then
    info "${SESSIONS} client session(s) connected — deferring the restart."
    exit 0
fi

if [ "$DRY_RUN" -eq 1 ]; then
    info "DRY RUN — would restart smbd."
    exit 0
fi

printf '%s' "$MISSING" > "$STATE"
systemctl restart smbd
sleep 2

if systemctl is-active --quiet smbd; then
    NOW="$(ss -tlnH 2>/dev/null | awk '$4 ~ /:445$/ {print $4}' | tr '\n' ' ')"
    info "Restarted smbd — now listening on: ${NOW}"
else
    info "ERROR: smbd failed to come back after restart"
    exit 1
fi
