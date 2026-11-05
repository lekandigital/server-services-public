#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# install.sh — Install the RustDesk auto display switcher
#
# Run on the Ubuntu server:
#   sudo ./install.sh
#
# What it does:
#   1. Installs xserver-xorg-video-dummy (if missing)
#   2. Deploys auto-switch script, udev rule, systemd service + timer
#   3. Ensures RustDesk headless mode is enabled
#   4. Ensures GDM is set to X11
#   5. Cleans up old manual switching scripts
#   6. Runs the auto-switcher once to set correct initial state
# ─────────────────────────────────────────────────────────────

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}This script must be run as root (sudo ./install.sh)${NC}"
    exit 1
fi

echo -e "${BLUE}════════════════════════════════════════════${NC}"
echo -e "${BLUE}  RustDesk Auto Display Switcher — Install${NC}"
echo -e "${BLUE}════════════════════════════════════════════${NC}"
echo ""

# ── 1. Dependencies ──────────────────────────────────────────
echo -e "${YELLOW}[1/6] Checking dependencies...${NC}"

if ! dpkg -l xserver-xorg-video-dummy &>/dev/null; then
    echo -e "  Installing xserver-xorg-video-dummy..."
    apt-get update -qq
    apt-get install -y -qq xserver-xorg-video-dummy
fi
echo -e "${GREEN}  ✓ xserver-xorg-video-dummy installed${NC}"

# ── 2. Deploy files ──────────────────────────────────────────
echo -e "${YELLOW}[2/6] Deploying files...${NC}"

install -m 0755 "$SCRIPT_DIR/rustdesk-display-auto-switch" /usr/local/bin/rustdesk-display-auto-switch
echo -e "  ${BLUE}→ /usr/local/bin/rustdesk-display-auto-switch${NC}"

install -m 0644 "$SCRIPT_DIR/99-rustdesk-display-switch.rules" /etc/udev/rules.d/99-rustdesk-display-switch.rules
echo -e "  ${BLUE}→ /etc/udev/rules.d/99-rustdesk-display-switch.rules${NC}"

install -m 0644 "$SCRIPT_DIR/rustdesk-display-switch.service" /etc/systemd/system/rustdesk-display-switch.service
echo -e "  ${BLUE}→ /etc/systemd/system/rustdesk-display-switch.service${NC}"

install -m 0644 "$SCRIPT_DIR/rustdesk-display-poll.timer" /etc/systemd/system/rustdesk-display-poll.timer
echo -e "  ${BLUE}→ /etc/systemd/system/rustdesk-display-poll.timer${NC}"

install -D -m 0644 "$SCRIPT_DIR/xorg-dummy.conf" /etc/rustdesk/xorg.conf
echo -e "  ${BLUE}→ /etc/rustdesk/xorg.conf (dummy template)${NC}"

echo -e "${GREEN}  ✓ All files deployed${NC}"

# ── 3. RustDesk headless mode ────────────────────────────────
echo -e "${YELLOW}[3/6] Configuring RustDesk...${NC}"

rustdesk --option allow-linux-headless Y 2>/dev/null || true
echo -e "${GREEN}  ✓ allow-linux-headless = Y${NC}"

# ── 4. GDM → X11 ────────────────────────────────────────────
echo -e "${YELLOW}[4/6] Ensuring GDM uses X11...${NC}"

GDM_CONF="/etc/gdm3/custom.conf"
if [[ -f "$GDM_CONF" ]]; then
    if grep -q "^WaylandEnable=false" "$GDM_CONF"; then
        echo -e "${GREEN}  ✓ Already set${NC}"
    elif grep -q "^#WaylandEnable=false" "$GDM_CONF"; then
        sed -i 's/^#WaylandEnable=false/WaylandEnable=false/' "$GDM_CONF"
        echo -e "${GREEN}  ✓ Uncommented WaylandEnable=false${NC}"
    else
        sed -i '/^\[daemon\]/a WaylandEnable=false' "$GDM_CONF"
        echo -e "${GREEN}  ✓ Added WaylandEnable=false${NC}"
    fi
fi

# ── 5. Clean up old manual scripts ───────────────────────────
echo -e "${YELLOW}[5/6] Cleaning up old manual scripts...${NC}"

OLD_FILES=(
    /usr/local/bin/hdmi-mode
    /usr/local/bin/rustdesk-headless-mode
    /usr/local/bin/display-mode-status
)
for f in "${OLD_FILES[@]}"; do
    if [[ -f "$f" ]]; then
        rm -f "$f"
        echo -e "  ${BLUE}→ Removed $f${NC}"
    fi
done

# Clean ~/bin for the main user
for user_home in /home/*/bin; do
    [[ -d "$user_home" ]] || continue
    for f in rustdesk-switch-to-hdmi-mode.sh rustdesk-switch-to-headless-mode.sh rustdesk-display-mode-status.sh hdmi-mode rustdesk-headless-mode display-mode-status; do
        if [[ -e "$user_home/$f" ]]; then
            rm -f "$user_home/$f"
            echo -e "  ${BLUE}→ Removed $user_home/$f${NC}"
        fi
    done
done

echo -e "${GREEN}  ✓ Old scripts cleaned up${NC}"

# ── 6. Enable and activate ───────────────────────────────────
echo -e "${YELLOW}[6/6] Enabling auto-switcher...${NC}"

udevadm control --reload-rules
systemctl daemon-reload
systemctl enable rustdesk-display-switch.service
systemctl enable --now rustdesk-display-poll.timer

echo -e "${GREEN}  ✓ udev rules reloaded${NC}"
echo -e "${GREEN}  ✓ Systemd service enabled${NC}"
echo -e "${GREEN}  ✓ Polling timer started (every 15s)${NC}"

# ── Run initial detection ─────────────────────────────────────
echo ""
echo -e "${YELLOW}Running initial display detection...${NC}"
/usr/local/bin/rustdesk-display-auto-switch || true

echo ""
echo -e "${BLUE}════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Installation complete!${NC}"
echo -e "${BLUE}════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${GREEN}How it works:${NC}"
echo -e "    • Polls every 15 seconds for display changes"
echo -e "    • Plug in a monitor  → auto-switches to real display"
echo -e "    • Unplug the monitor → auto-switches to RustDesk headless"
echo -e "    • On boot            → detects and sets the correct mode"
echo ""
echo -e "  ${GREEN}Useful commands (run on this server):${NC}"
echo -e "    Timer:    ${YELLOW}systemctl status rustdesk-display-poll.timer${NC}"
echo -e "    Logs:     ${YELLOW}journalctl -t rustdesk-display-switch -f${NC}"
echo -e "    RustDesk: ${YELLOW}sudo systemctl status rustdesk${NC}"
echo -e "    Force:    ${YELLOW}sudo rustdesk-display-auto-switch${NC}"
echo ""
echo -e "  ${GREEN}RustDesk info:${NC}"
echo -e "    ID:       $(rustdesk --get-id 2>/dev/null || echo 'unknown')"
echo -e "    Service:  $(systemctl is-active rustdesk 2>/dev/null || echo 'unknown')"
echo ""
