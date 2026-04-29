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
    curl wget git \
    build-essential

# Chrome (for Selenium / Twitter bot)
if ! command -v google-chrome &>/dev/null; then
    echo -e "  Installing Google Chrome..."
    wget -q -O /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
    dpkg -i /tmp/google-chrome.deb || apt-get -f install -y -qq
    rm /tmp/google-chrome.deb
fi

# Node.js via nvm (for cast-manager)
if [ ! -d "/home/${USER_NAME}/.nvm" ]; then
    echo -e "  Installing nvm + Node.js v20.20.0..."
    su - "$USER_NAME" -c 'curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash'
    su - "$USER_NAME" -c 'export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" && nvm install 20.20.0'
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
cp -r "$SCRIPT_DIR"/{server-portal,twitter-bot,cast-manager,whisper-transcriber,ocr-engine,system-stats,ollama-gui,image-studio} "$INSTALL_DIR/"
chown -R "$USER_NAME:$USER_NAME" "$INSTALL_DIR"
echo -e "${GREEN}  ✓ Files copied${NC}"

# ----------------------------------------------------------
# 3. Install Python dependencies
# ----------------------------------------------------------
echo -e "${YELLOW}[3/7] Installing Python dependencies...${NC}"

for svc in server-portal whisper-transcriber ocr-engine system-stats twitter-bot image-studio; do
    if [ -f "$INSTALL_DIR/$svc/requirements.txt" ]; then
        echo -e "  ${BLUE}→ $svc${NC}"
        pip3 install -q -r "$INSTALL_DIR/$svc/requirements.txt"
    fi
done

echo -e "${GREEN}  ✓ Python packages installed${NC}"

# ----------------------------------------------------------
# 4. Install Node.js dependencies (cast-manager)
# ----------------------------------------------------------
echo -e "${YELLOW}[4/7] Installing Node.js dependencies for cast-manager...${NC}"
su - "$USER_NAME" -c "
    export NVM_DIR=\"\$HOME/.nvm\"
    [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\"
    cd $INSTALL_DIR/cast-manager
    npm install --production
"
echo -e "${GREEN}  ✓ npm packages installed${NC}"

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
    ["cast-manager"]="cast-manager/cast-manager.service"
    ["faster-whisper"]="whisper-transcriber/faster-whisper.service"
    ["paddleocr"]="ocr-engine/paddleocr.service"
    ["system-stats"]="system-stats/system-stats.service"
    ["image-studio"]="image-studio/image-studio.service"
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

systemctl daemon-reload
echo -e "${GREEN}  ✓ Systemd services installed${NC}"

# ----------------------------------------------------------
# 7. Enable and start all services
# ----------------------------------------------------------
echo -e "${YELLOW}[7/7] Enabling and starting services...${NC}"

for svc_name in server-portal xb-dashboard cast-manager faster-whisper paddleocr system-stats image-studio; do
    systemctl enable "$svc_name" 2>/dev/null
    systemctl start "$svc_name" 2>/dev/null && \
        echo -e "  ${GREEN}✓ ${svc_name}${NC}" || \
        echo -e "  ${RED}✗ ${svc_name} (failed to start — check: journalctl -u ${svc_name})${NC}"
done

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
echo -e "  🎬 Video Manager:        http://$(hostname -I | awk '{print $1}'):8004"
echo -e "  🎙️  Whisper Transcriber:  http://$(hostname -I | awk '{print $1}'):8005"
echo -e "  🔍 OCR Engine:           http://$(hostname -I | awk '{print $1}'):8006"
echo -e "  📊 System Stats:         http://$(hostname -I | awk '{print $1}'):8007"
echo -e "  🖼️  ML Image Studio:     http://$(hostname -I | awk '{print $1}'):8008"
echo ""
echo -e "  Check status: ${YELLOW}sudo systemctl status server-portal xb-dashboard cast-manager faster-whisper paddleocr system-stats image-studio${NC}"
echo -e "  View logs:    ${YELLOW}journalctl -u <service-name> -f${NC}"
