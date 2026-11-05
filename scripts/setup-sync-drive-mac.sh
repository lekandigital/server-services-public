#!/usr/bin/env bash
set -euo pipefail

MAC_FOLDER="${HOME}/Server Drive"

info() {
    printf '[sync-drive-mac] %s\n' "$1"
}

fail() {
    printf '[sync-drive-mac] ERROR: %s\n' "$1" >&2
    exit 1
}

info "Creating Finder folder: ${MAC_FOLDER}"
mkdir -p "$MAC_FOLDER"

if ! command -v brew >/dev/null 2>&1; then
    fail "Homebrew is not installed. Install Homebrew first, then rerun this script. Syncthing was not installed."
fi

if brew list --formula syncthing >/dev/null 2>&1; then
    info "Syncthing is already installed."
else
    info "Installing Syncthing with Homebrew."
    brew install syncthing
fi

info "Starting Syncthing with brew services."
brew services start syncthing

if command -v open >/dev/null 2>&1; then
    open "$MAC_FOLDER"
    open http://127.0.0.1:8384
else
    info "The 'open' command is unavailable. Open ${MAC_FOLDER} and http://127.0.0.1:8384 manually."
fi

info "Mac folder: ${MAC_FOLDER}"
info "Next steps:"
info "1. Copy the Mac Device ID from http://127.0.0.1:8384."
info "2. Open the server UI at http://<server-ip>:8384."
info "3. Pair the Mac and server devices."
info "4. Share server folder /data/drive."
info "5. Accept it on the Mac at ${MAC_FOLDER}."
info "6. Use Send & Receive on both sides for Google Drive-like behavior."
