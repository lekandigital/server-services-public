#!/bin/bash
# ============================================================
# deploy.sh — Deploy all server services on a fresh Ubuntu machine
#
# Usage:
#   chmod +x deploy.sh
#   sudo ./deploy.sh <username> <install_dir>
#
# Example:
#   sudo ./deploy.sh o /home/REDACTED_USER
#
# This script:
#   1. Installs system packages (Python, Node, ffmpeg, Chrome, etc.)
#   2. Installs pip packages for each Python service
#   3. Installs npm packages for Node services
#   4. Creates systemd service files with correct paths
#   5. Enables and starts all services
# ============================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

if [ "$#" -lt 2 ]; then
    echo -e "${RED}Usage: sudo $0 <username> <install_dir>${NC}"
    echo -e "  Example: sudo $0 o /home/REDACTED_USER/server-services"
    exit 1
fi

USER_NAME="$1"
INSTALL_DIR="$2"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CAST_MANAGER_DIR="/home/${USER_NAME}/cast_manager_v3"

echo -e "${BLUE}==========================================${NC}"
echo -e "${BLUE}  Server Services — Full Stack Deploy${NC}"
echo -e "${BLUE}==========================================${NC}"
echo -e "  User:        ${GREEN}${USER_NAME}${NC}"
echo -e "  Install dir: ${GREEN}${INSTALL_DIR}${NC}"
echo -e "  Source dir:   ${GREEN}${SCRIPT_DIR}${NC}"
echo ""

# ----------------------------------------------------------
# 1. System packages
# ----------------------------------------------------------
echo -e "${YELLOW}[1/7] Installing system packages...${NC}"
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    ffmpeg poppler-utils \
    curl wget git rsync \
    build-essential

# Chrome (for Selenium / Twitter bot)
if ! command -v google-chrome &>/dev/null; then
    echo -e "  Installing Google Chrome..."
    wget -q -O /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
    dpkg -i /tmp/google-chrome.deb || apt-get -f install -y -qq
    rm /tmp/google-chrome.deb
fi

# Node.js via nvm (for Cast Manager)
if [ ! -x "/home/${USER_NAME}/.nvm/versions/node/v20.20.0/bin/node" ]; then
    echo -e "  Installing nvm + Node.js v20.20.0..."
    if [ ! -s "/home/${USER_NAME}/.nvm/nvm.sh" ]; then
        su - "$USER_NAME" -c 'curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash'
    fi
    su - "$USER_NAME" -c 'export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && nvm install 20.20.0'
fi

# Docker (for Ollama GUI)
if ! command -v docker &>/dev/null; then
    echo -e "  Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker "$USER_NAME"
fi

echo -e "${GREEN}  ✓ System packages installed${NC}"

# ----------------------------------------------------------
# 2. Copy files to install directory
# ----------------------------------------------------------
echo -e "${YELLOW}[2/7] Copying service files to ${INSTALL_DIR}...${NC}"
mkdir -p "$INSTALL_DIR"
if [ "$(readlink -f "$SCRIPT_DIR")" = "$(readlink -f "$INSTALL_DIR")" ]; then
    echo -e "  ${BLUE}→ Source and install directories match; keeping the checkout in place${NC}"
else
    cp -r "$SCRIPT_DIR"/{server-portal,twitter-bot,whisper-transcriber,ocr-engine,system-stats,ollama-gui,image-studio,xbot-portal,vpn-portal} "$INSTALL_DIR/"
    chown -R "$USER_NAME:$USER_NAME" "$INSTALL_DIR"
fi
install -d -m 0755 -o "$USER_NAME" -g "$USER_NAME" "/home/${USER_NAME}/file-manager"
install -d -m 0755 -o "$USER_NAME" -g "$USER_NAME" "/home/${USER_NAME}/file-manager/drive"
install -d -m 0755 -o "$USER_NAME" -g "$USER_NAME" "$CAST_MANAGER_DIR"
rsync -a \
    --exclude='node_modules' --exclude='.venv' --exclude='.env' --exclude='.env.*' \
    --exclude='diagnostics' --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
    "$SCRIPT_DIR/cast-manager/" "$CAST_MANAGER_DIR/"
chown -R "$USER_NAME:$USER_NAME" "$CAST_MANAGER_DIR"
echo -e "${GREEN}  ✓ Files copied${NC}"

# ----------------------------------------------------------
# 3. Install Python dependencies
# ----------------------------------------------------------
echo -e "${YELLOW}[3/7] Installing Python dependencies...${NC}"

for svc in server-portal whisper-transcriber ocr-engine system-stats twitter-bot image-studio vpn-portal; do
    if [ -f "$INSTALL_DIR/$svc/requirements.txt" ]; then
        echo -e "  ${BLUE}→ $svc${NC}"
        pip3 install -q -r "$INSTALL_DIR/$svc/requirements.txt"
    fi
done

# Optional: extra OCR backends for multi-engine support (EasyOCR, Surya, docTR, etc.)
# Uncomment below to install. Tesseract also needs a system package.
#   pip3 install -r "$INSTALL_DIR/ocr-engine/requirements-extra-ocr.txt"
#   apt-get install -y -qq tesseract-ocr tesseract-ocr-eng

echo -e "${GREEN}  ✓ Python packages installed${NC}"

# ----------------------------------------------------------
# 4. Verify the Cast Manager runtime and integrated Drive
# ----------------------------------------------------------
echo -e "${YELLOW}[4/7] Verifying Cast Manager + Drive...${NC}"
su - "$USER_NAME" -c "export PATH='/home/${USER_NAME}/.nvm/versions/node/v20.20.0/bin':\$PATH; cd '$CAST_MANAGER_DIR'; npm install --omit=dev --no-audit --no-fund; node --check server.js; node --check lib/drive-routes.js"
echo -e "${GREEN}  ✓ Cast Manager + Drive syntax verified${NC}"

# ----------------------------------------------------------
# 5. Build & deploy Ollama GUI (Docker)
# ----------------------------------------------------------
echo -e "${YELLOW}[5/7] Building Ollama GUI Docker containers...${NC}"
cd "$INSTALL_DIR/ollama-gui"
docker compose up -d --build
echo -e "${GREEN}  ✓ Ollama GUI running (Docker)${NC}"

# ----------------------------------------------------------
# 6. Install systemd service files
# ----------------------------------------------------------
echo -e "${YELLOW}[6/7] Installing systemd service files...${NC}"

declare -A SERVICE_FILES=(
    ["server-portal"]="server-portal/server-portal.service"
    ["xb-dashboard"]="twitter-bot/xb-dashboard.service"
    ["faster-whisper"]="whisper-transcriber/faster-whisper.service"
    ["paddleocr"]="ocr-engine/paddleocr.service"
    ["system-stats"]="system-stats/system-stats.service"
    ["image-studio"]="image-studio/image-studio.service"
    ["xbot-lan-dashboard"]="xbot-portal/xbot-lan-dashboard.service"
    ["proton-vpn-portal"]="vpn-portal/proton-vpn-portal.service"
)

for svc_name in "${!SERVICE_FILES[@]}"; do
    src="$INSTALL_DIR/${SERVICE_FILES[$svc_name]}"
    dest="/etc/systemd/system/${svc_name}.service"

    # Replace placeholders
    sed -e "s|__USER__|${USER_NAME}|g" \
        -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
        "$src" > "$dest"

    echo -e "  ${BLUE}→ ${svc_name}.service${NC}"
done

sed -e "s|__USER__|${USER_NAME}|g" \
    "$CAST_MANAGER_DIR/cast-manager.service" > /etc/systemd/system/cast-manager.service
echo -e "  ${BLUE}→ cast-manager.service${NC}"

systemctl daemon-reload
echo -e "${GREEN}  ✓ Systemd services installed${NC}"

# ----------------------------------------------------------
# 7. Enable and start all services
# ----------------------------------------------------------
echo -e "${YELLOW}[7/7] Enabling and starting services...${NC}"

for svc_name in server-portal xb-dashboard faster-whisper paddleocr system-stats image-studio xbot-lan-dashboard proton-vpn-portal; do
    systemctl enable "$svc_name" 2>/dev/null
    systemctl restart "$svc_name" 2>/dev/null && \
        echo -e "  ${GREEN}✓ ${svc_name}${NC}" || \
        echo -e "  ${RED}✗ ${svc_name} (failed to start — check: journalctl -u ${svc_name})${NC}"
done

# Port 8004 belongs to the combined Cast Manager process. Stop the obsolete
# standalone unit only at the switch, and restore it if Cast Manager fails.
had_file_manager=0
systemctl is-active --quiet file-manager.service && had_file_manager=1 || true
systemctl stop file-manager.service 2>/dev/null || true
if systemctl restart cast-manager.service && \
   curl --retry 20 --retry-delay 1 --retry-connrefused -fsS http://127.0.0.1:8004/api/files/config | grep -q '"feature":"Drive"'; then
    systemctl enable cast-manager.service 2>/dev/null
    systemctl disable file-manager.service 2>/dev/null || true
    rm -f /etc/systemd/system/file-manager.service
    systemctl daemon-reload
    echo -e "  ${GREEN}✓ cast-manager (with integrated Drive)${NC}"
else
    systemctl stop cast-manager.service 2>/dev/null || true
    if [ "$had_file_manager" -eq 1 ]; then systemctl start file-manager.service 2>/dev/null || true; fi
    echo -e "  ${RED}✗ cast-manager failed; previous File Manager restored when possible${NC}"
    exit 1
fi

# ----------------------------------------------------------
# Done
# ----------------------------------------------------------
echo ""
echo -e "${BLUE}==========================================${NC}"
echo -e "${GREEN}  All services deployed!${NC}"
echo -e "${BLUE}==========================================${NC}"
echo ""
echo -e "  🏠 Server Portal:        http://$(hostname -I | awk '{print $1}'):8001"
echo -e "  🦙 Ollama GUI:           http://$(hostname -I | awk '{print $1}'):8002"
echo -e "  🤖 Twitter Bot Dashboard: http://$(hostname -I | awk '{print $1}'):8003"
echo -e "  🎬 Cast Manager:         http://$(hostname -I | awk '{print $1}'):8004"
echo -e "     includes Drive:       /home/${USER_NAME}/file-manager/drive"
echo -e "  🎙️  Whisper Transcriber:  http://$(hostname -I | awk '{print $1}'):8005"
echo -e "  🔍 OCR Engine:           http://$(hostname -I | awk '{print $1}'):8006"
echo -e "  📊 System Stats:         http://$(hostname -I | awk '{print $1}'):8007"
echo -e "  🖼️  ML Image Studio:     http://$(hostname -I | awk '{print $1}'):8008"
echo -e "  📡 X-Bot Portal:         http://$(hostname -I | awk '{print $1}'):8009"
echo -e "  🎥 VLC Stream:           :8010 (directory indicator only)"
echo -e "  🔐 Proton VPN Portal:    http://$(hostname -I | awk '{print $1}'):8011"
echo ""
echo -e "  Check status: ${YELLOW}sudo systemctl status server-portal xb-dashboard faster-whisper paddleocr system-stats image-studio xbot-lan-dashboard proton-vpn-portal cast-manager${NC}"
echo -e "  View logs:    ${YELLOW}journalctl -u <service-name> -f${NC}"
