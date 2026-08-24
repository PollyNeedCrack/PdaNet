#!/bin/bash
# PollyPdaNet installer - tray client + privileged helper
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
PROJECT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
REAL_USER="${SUDO_USER:-$USER}"

echo -e "${GREEN}PollyPdaNet installer${NC}"
echo "======================================"

if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}Error: run with sudo: sudo ./install.sh${NC}" && exit 1
fi

echo -e "${YELLOW}[1/6]${NC} Checking dependencies..."
MISSING=()
for bin in python3 tun2socks iptables nmcli curl ip dnsproxy; do
    command -v "$bin" >/dev/null || MISSING+=("$bin")
done
for pkg in gtk3 libayatana-appindicator; do
    pacman -Q "$pkg" >/dev/null 2>&1 || MISSING+=("$pkg (pacman)")
done
if [ ${#MISSING[@]} -gt 0 ]; then
    echo -e "${YELLOW}Missing: ${MISSING[*]}${NC}"
    echo "Arch: sudo pacman -S --needed tun2socks dnsproxy gtk3 libayatana-appindicator"
    echo "USB mode also needs: sudo pacman -S --needed android-tools"
fi
command -v adb >/dev/null || echo -e "${YELLOW}note:${NC} android-tools not found - USB mode disabled until installed"

echo -e "${YELLOW}[2/6]${NC} Installing backend helper..."
install -m 755 "$PROJECT_DIR/scripts/pdanet-helper" /usr/local/bin/pdanet-helper

echo -e "${YELLOW}[3/6]${NC} Installing tray app..."
ln -sf "$PROJECT_DIR/src/pdanet_tray.py" /usr/local/bin/pdanet-app
chmod +x "$PROJECT_DIR/src/pdanet_tray.py"

echo -e "${YELLOW}[4/6]${NC} Installing icons..."
mkdir -p /usr/local/share/pollypdanet
cp "$PROJECT_DIR/assets/"*.svg /usr/local/share/pollypdanet/

echo -e "${YELLOW}[5/6]${NC} Passwordless helper permissions (polkit-free privilege)..."
SUDOERS_FILE=/etc/sudoers.d/pdanet-helper
printf '%s ALL=(root) NOPASSWD: /usr/local/bin/pdanet-helper\n' "$REAL_USER" > "$SUDOERS_FILE"
chmod 440 "$SUDOERS_FILE"

echo -e "${YELLOW}[6/6]${NC} Installing menu launcher..."
cp "$PROJECT_DIR/config/pdanet.desktop" /usr/share/applications/pdanet.desktop
chmod 644 /usr/share/applications/pdanet.desktop
update-desktop-database /usr/share/applications 2>/dev/null || true

echo ""
echo -e "${GREEN}Installed! Launch 'PdaNet' from your app menu,"
echo -e "or run: pdanet-app${NC}"
echo -e "Uninstall anytime with: sudo $PROJECT_DIR/uninstall.sh"
