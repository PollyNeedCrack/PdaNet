#!/bin/bash
# PollyPdaNet uninstaller
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
   echo "Error: run with sudo: sudo ./uninstall.sh" && exit 1
fi

# tear down any active tunnel first
/usr/local/bin/pdanet-helper down >/dev/null 2>&1 || true

rm -f /usr/local/bin/pdanet-app /usr/local/bin/pdanet-helper
rm -rf /usr/local/share/pollypdanet
rm -f /usr/local/share/applications/pdanet.desktop
rm -f /etc/sudoers.d/pdanet-helper
update-desktop-database /usr/share/applications 2>/dev/null || true

echo "PollyPdaNet removed. (User settings kept in ~/.config/pdanet-linux/)"
