#!/usr/bin/env bash
set -u

USER_NAME="${1:-o}"
SYNCTHING_CONFIG_DIR="/home/REDACTED_USER/syncthing/config"
DRIVE_DIR="/home/REDACTED_USER/file-manager/drive"
FAILURES=0
WARNINGS=0

ok() {
    printf '[ OK ] %s\n' "$1"
}

warn() {
    WARNINGS=$((WARNINGS + 1))
    printf '[WARN] %s\n' "$1"
}

bad() {
    FAILURES=$((FAILURES + 1))
    printf '[FAIL] %s\n' "$1"
}

has_cmd() {
    command -v "$1" >/dev/null 2>&1
}

port_listening() {
    port="$1"
    proto="${2:-tcp}"

    if has_cmd ss; then
        if [ "$proto" = "udp" ]; then
            ss -lun 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)${port}$"
        else
            ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)${port}$"
        fi
        return $?
    fi

    if has_cmd lsof; then
        if [ "$proto" = "udp" ]; then
            lsof -nP -iUDP:"${port}" >/dev/null 2>&1
        else
            lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
        fi
        return $?
    fi

    return 2
}

printf 'Sync Drive health check\n'
printf 'Canonical data path: %s\n' "$DRIVE_DIR"
printf '\n'

if [ -d "$DRIVE_DIR" ]; then
    ok "${DRIVE_DIR} exists"
else
    bad "${DRIVE_DIR} is missing. Suggested fix: sudo mkdir -p ${DRIVE_DIR}"
fi

if id "$USER_NAME" >/dev/null 2>&1; then
    if [ -d "$DRIVE_DIR" ]; then
        if [ "$(id -u)" -eq 0 ]; then
            if sudo -u "$USER_NAME" test -w "$DRIVE_DIR"; then
                ok "${DRIVE_DIR} is writable by ${USER_NAME}"
            else
                bad "${DRIVE_DIR} is not writable by ${USER_NAME}. Suggested fix: sudo chown -R ${USER_NAME}:${USER_NAME} /home/REDACTED_USER/file-manager"
            fi
        else
            warn "Run with sudo to verify ${DRIVE_DIR} writability as ${USER_NAME}"
        fi
    fi
else
    bad "User '${USER_NAME}' does not exist"
fi

if [ -d "$SYNCTHING_CONFIG_DIR" ]; then
    ok "${SYNCTHING_CONFIG_DIR} exists"
else
    bad "${SYNCTHING_CONFIG_DIR} is missing. Suggested fix: sudo mkdir -p ${SYNCTHING_CONFIG_DIR}"
fi

if has_cmd docker; then
    ok "Docker command is available"
    if docker ps --filter name=sync-drive --format '{{.Names}} {{.Status}}' 2>/dev/null | grep -q '^sync-drive '; then
        ok "sync-drive container is running"
    else
        bad "sync-drive container is not running. Suggested fix: cd /home/REDACTED_USER/server-services/sync-drive && sudo docker compose up -d"
    fi
else
    bad "Docker command is missing. Install Docker first."
fi

if port_listening 8384 tcp; then
    ok "Port 8384/tcp is listening"
else
    rc=$?
    if [ "$rc" -eq 2 ]; then
        warn "Cannot check listening ports because neither ss nor lsof is available"
    else
        bad "Port 8384/tcp is not listening. Suggested fix: docker logs sync-drive"
    fi
fi

if port_listening 22000 tcp; then
    ok "Port 22000/tcp is listening"
else
    rc=$?
    if [ "$rc" -eq 2 ]; then
        warn "Cannot check port 22000/tcp because neither ss nor lsof is available"
    else
        bad "Port 22000/tcp is not listening. Suggested fix: docker logs sync-drive"
    fi
fi

if port_listening 22000 udp; then
    ok "Port 22000/udp is listening"
else
    rc=$?
    if [ "$rc" -eq 2 ]; then
        warn "Cannot check port 22000/udp because neither ss nor lsof is available"
    else
        warn "Port 22000/udp is not visible as listening. Check docker ps port mappings and docker logs sync-drive."
    fi
fi

if has_cmd curl; then
    if curl -fsS -o /dev/null http://127.0.0.1:8384 2>/dev/null || curl -fsS -o /dev/null http://localhost:8384 2>/dev/null; then
        ok "Syncthing UI responds locally"
    else
        bad "Cannot reach Syncthing UI at http://127.0.0.1:8384 or http://localhost:8384"
    fi
else
    warn "curl is not installed; skipped local HTTP reachability check"
fi

printf '\n'
printf 'Summary: %s failure(s), %s warning(s)\n' "$FAILURES" "$WARNINGS"

if [ "$FAILURES" -gt 0 ]; then
    exit 1
fi
