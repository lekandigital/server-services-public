#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# uninstall.sh — Remove the RustDesk auto display switcher
#
# Run on the Ubuntu server:
#   sudo ./uninstall.sh
#
# Removes all auto-switcher components but does NOT
# uninstall RustDesk itself or the dummy Xorg driver.
# ─────────────────────────────────────────────────────────────

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}This script must be run as root (sudo ./uninstall.sh)${NC}"
    exit 1
fi

echo -e "${YELLOW}Removing RustDesk auto display switcher...${NC}"

systemctl disable --now rustdesk-display-poll.timer 2>/dev/null || true
systemctl disable rustdesk-display-switch.service 2>/dev/null || true
systemctl stop rustdesk-display-switch.service 2>/dev/null || true

rm -f /etc/systemd/system/rustdesk-display-switch.service
rm -f /etc/systemd/system/rustdesk-display-poll.timer
rm -f /etc/udev/rules.d/99-rustdesk-display-switch.rules
rm -f /usr/local/bin/rustdesk-display-auto-switch
rm -f /run/rustdesk-display-switch.lock

systemctl daemon-reload
udevadm control --reload-rules

echo -e "${GREEN}✓ Auto-switcher removed${NC}"
echo ""
echo -e "  Files kept (remove manually if you want):"
echo -e "    /etc/rustdesk/xorg.conf      (dummy template)"
echo -e "    /etc/X11/xorg.conf           (remove if in headless mode)"
echo ""
