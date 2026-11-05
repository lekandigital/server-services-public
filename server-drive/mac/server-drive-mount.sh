#!/bin/bash
#
# Server Drive — mount helper.
#
# Run by the com.lekan.serverdrive LaunchAgent at login, on network changes,
# and every minute as a backstop. Idempotent and quiet: a no-op when already
# mounted or when the server cannot be reached.
#
# Tries each address in SERVER_HOSTS in order — the box's wired port, then its
# wifi — then the Tailscale address, so the drive survives the wifi card
# wedging and follows you off the home network. Config is written next to this
# script by setup-server-drive-mac.sh.
#
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
[ -f "${SCRIPT_DIR}/config.sh" ] && . "${SCRIPT_DIR}/config.sh"

# SERVER_HOST is the pre-2026-10-29 single-address config; still honoured.
SERVER_HOSTS="${SERVER_HOSTS:-${SERVER_HOST:-REDACTED_SERVER_WIRED_IP}}"
SERVER_USER="${SERVER_USER:-o}"
SERVER_SHARE="${SERVER_SHARE:-Server}"
SERVER_TAILSCALE="${SERVER_TAILSCALE:-}"
SERVER_BONJOUR="${SERVER_BONJOUR:-}"

HOSTS="${SERVER_HOSTS} ${SERVER_TAILSCALE}"

log() {
    printf '%s server-drive: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1"
}

mounted_on() {
    /sbin/mount | grep -q "//${SERVER_USER}@${1}/${SERVER_SHARE} on "
}

# Already mounted on any address? Match on the source, not the mountpoint — if
# /Volumes/Server was taken, macOS mounts at Server-1 and a mountpoint check
# would remount forever. A connection made from the Finder sidebar shows up as
# //o@O-MACPRO._smb._tcp.local/Server, so the Bonjour name counts too.
for host in $HOSTS; do
    if mounted_on "$host"; then
        exit 0
    fi
done
if [ -n "$SERVER_BONJOUR" ] \
    && /sbin/mount | grep -qi "//${SERVER_USER}@${SERVER_BONJOUR}[^/]*/${SERVER_SHARE} on "; then
    exit 0
fi

SKIPPED=""

try_mount() {
    local host="$1"

    # Probe the SMB port itself, not ping: the box answers ping on an address
    # smbd is not bound to, and Finder then hangs on the mount instead.
    if ! /usr/bin/nc -z -G 2 "$host" 445 >/dev/null 2>&1; then
        SKIPPED="${SKIPPED} ${host}: port 445 unreachable;"
        return 1
    fi

    # No saved credentials means Finder would pop an auth dialog — and this runs
    # unattended every minute and on every network change. Skip to the next
    # address instead.
    if ! /usr/bin/security find-internet-password -a "$SERVER_USER" -s "$host" -r "smb " >/dev/null 2>&1; then
        SKIPPED="${SKIPPED} ${host}: no Keychain entry (connect once via Finder: open smb://${SERVER_USER}@${host}/${SERVER_SHARE}, tick 'Remember this password');"
        return 1
    fi

    log "mounting smb://${SERVER_USER}@${host}/${SERVER_SHARE}"

    # osascript's `mount volume` goes through Finder, so the volume lands in
    # /Volumes and appears under Locations with an eject button — which
    # mount_smbfs alone does not give us.
    #
    # Watchdog: if Finder puts up an auth dialog anyway, `mount volume` blocks
    # until someone clicks it. Unattended, that wedges the agent, so cap it.
    /usr/bin/osascript -e "mount volume \"smb://${SERVER_USER}@${host}/${SERVER_SHARE}\"" >/dev/null 2>&1 &
    local osa_pid=$!
    ( sleep 45; kill -TERM "$osa_pid" 2>/dev/null ) >/dev/null 2>&1 &
    local watchdog_pid=$!

    # Drop both from the job table: whichever one gets killed, bash would
    # otherwise print a "Terminated: 15" notice straight into the log.
    disown "$osa_pid" 2>/dev/null
    disown "$watchdog_pid" 2>/dev/null

    # disown also makes these unwaitable, so poll instead.
    local finished=1
    local i
    for i in $(seq 1 50); do
        if ! kill -0 "$osa_pid" 2>/dev/null; then
            finished=0
            break
        fi
        sleep 1
    done
    kill -TERM "$watchdog_pid" 2>/dev/null

    if [ "$finished" -ne 0 ]; then
        log "mount timed out on ${host} (Finder was likely prompting)"
        return 1
    fi

    # The process is gone; ask the filesystem whether it actually worked.
    if mounted_on "$host"; then
        log "mounted via ${host}"
        return 0
    fi

    log "mount did not complete on ${host}"
    return 1
}

for host in $HOSTS; do
    if try_mount "$host"; then
        [ -n "$SKIPPED" ] && log "skipped earlier addresses:${SKIPPED}"
        exit 0
    fi
done

# Only worth a log line when an address was reachable but unusable; plain
# "nothing answers" is the normal off-network state and would spam every minute.
case "$SKIPPED" in
    *"no Keychain entry"*) log "not mounted:${SKIPPED}" ;;
esac
exit 1
