#!/usr/bin/env bash
set -euo pipefail

USER_NAME="${1:-o}"
INSTALL_DIR="${2:-/home/REDACTED_USER/server-services}"

SYNCTHING_ROOT="/home/REDACTED_USER/syncthing"
SYNCTHING_CONFIG_DIR="${SYNCTHING_ROOT}/config"
FILE_MANAGER_ROOT="/home/REDACTED_USER/file-manager"
DRIVE_DIR="${FILE_MANAGER_ROOT}/drive"
SERVICE_DIR="${INSTALL_DIR}/sync-drive"

info() {
    printf '[sync-drive] %s\n' "$1"
}

fail() {
    printf '[sync-drive] ERROR: %s\n' "$1" >&2
    exit 1
}

if [ "$(id -u)" -ne 0 ]; then
    fail "Run with sudo, for example: sudo $0 ${USER_NAME} ${INSTALL_DIR}"
fi

if ! id "$USER_NAME" >/dev/null 2>&1; then
    fail "User '${USER_NAME}' does not exist."
fi

if [ "$USER_NAME" != "o" ]; then
    info "Using canonical storage path ${DRIVE_DIR}; this service is configured for the o home-server path."
    info "Requested owner is '${USER_NAME}'. Make sure UID/GID match the compose PUID/PGID if this is not user o."
fi

if [ -d "$DRIVE_DIR" ]; then
    info "Preserving existing data folder: ${DRIVE_DIR}"
else
    info "Creating data folder: ${DRIVE_DIR}"
fi

if [ -d "$SYNCTHING_CONFIG_DIR" ]; then
    info "Preserving existing Syncthing config: ${SYNCTHING_CONFIG_DIR}"
else
    info "Creating Syncthing config folder: ${SYNCTHING_CONFIG_DIR}"
fi

mkdir -p "$SYNCTHING_CONFIG_DIR" "$DRIVE_DIR"
chown -R "$USER_NAME:$USER_NAME" "$SYNCTHING_ROOT"
chown -R "$USER_NAME:$USER_NAME" "$FILE_MANAGER_ROOT"

if ! command -v docker >/dev/null 2>&1; then
    fail "Docker is not installed. Install Docker first, then rerun this script."
fi

COMPOSE_RUNNER="root"
if docker compose version >/dev/null 2>&1; then
    COMPOSE_RUNNER="root"
elif su - "$USER_NAME" -c "docker compose version" >/dev/null 2>&1; then
    COMPOSE_RUNNER="user"
else
    fail "Docker Compose is not available through 'docker compose' for root or ${USER_NAME}. Install the Docker Compose plugin first."
fi

DOCKER_RUNNER="root"
if docker info >/dev/null 2>&1; then
    DOCKER_RUNNER="root"
elif su - "$USER_NAME" -c "docker info" >/dev/null 2>&1; then
    DOCKER_RUNNER="user"
else
    fail "Docker is installed, but the daemon is not reachable by root or ${USER_NAME}. Start Docker and rerun this script."
fi

if [ ! -f "${SERVICE_DIR}/compose.yml" ]; then
    fail "Missing ${SERVICE_DIR}/compose.yml. Pass the correct install dir as the second argument."
fi

info "Starting Sync Drive from ${SERVICE_DIR}"
if [ "$COMPOSE_RUNNER" = "root" ]; then
    cd "$SERVICE_DIR"
    docker compose up -d
else
    su - "$USER_NAME" -c "cd '$SERVICE_DIR' && docker compose up -d"
fi

SERVER_IP="127.0.0.1"
if command -v hostname >/dev/null 2>&1; then
    FIRST_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    if [ -n "${FIRST_IP:-}" ]; then
        SERVER_IP="$FIRST_IP"
    fi
fi

info "Container status:"
if [ "$DOCKER_RUNNER" = "root" ]; then
    docker ps --filter name=sync-drive
else
    su - "$USER_NAME" -c "docker ps --filter name=sync-drive"
fi

info "Server UI: http://${SERVER_IP}:8384"
info "Next steps:"
info "1. Open the Syncthing UI and set a GUI username/password immediately."
info "2. Pair this server with the Mac Syncthing device."
info "3. Share folder /data/drive and accept it on the Mac at \$HOME/Server Drive."
