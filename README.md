# Real person note at the top here

This entire thing was made by me feeding prompts into AI Agents, im not a dev at all unless you count that as developing.
i believe this is the first PdaNet Application that works for arch properly, i use omarchy so i made it all for omarchy where it sits in the taskbar and all that.
If you want to use this hopefully all the instructions below also written by AI helps you get it set up, sorry if it dont, but this all works perfectly for me.

The only downside to this is the moment your phones screen turns off, you lose internet completely, i worked around that with an application i found on the app store that keeps the screen on all the time


# PdaNet For Linux/Arch/Omarchy

PdaNet+ tethering client for Linux with a Windows-exe-style system tray interface.

Click the tray icon for a dropdown: connection status, live data usage, USB and
WiFi tethering modes, and "Hide Tether Usage" stealth levels - all system-wide,
no per-app proxy configuration.

## How it works

| Piece | Path | Runs as |
|---|---|---|
| Tray app (GTK3 + AyatanaAppIndicator) | `src/pdanet_tray.py` | your user |
| Privileged backend (tunnel + stealth) | `scripts/pdanet-helper` | root via sudo |

**WiFi mode:** joins the PdaNet+ hotspot, auto-detects its proxy (SOCKS5 or
HTTP CONNECT, scanning ports 8000/8080/3128/8888/9090), routes ALL traffic
through `tun2socks` on a TUN device, and serves DNS locally via DoH
(dnsproxy → 1.1.1.1/8.8.8.8) so lookups survive PdaNet's proxy-only design.

**USB mode:** mirrors the official Windows client - `adb forward tcp:N
localabstract:pdanet`, then the same system-wide tunnel. Traffic is
re-originated by the phone, so it is invisible to carrier tethering checks.

**Stealth ("Hide Tether Usage"):**
- Normal - TTL normalized to 65 (matches phone-originated packets)
- Aggressive - + IPv6 fully disabled + DNS pinned through tunnel

Fail-safes: any failed connect automatically restores your previous WiFi;
Disconnect hands your old network back too.

## Install (Arch / Omarchy)

```bash
sudo pacman -S --needed tun2socks dnsproxy gtk3 libayatana-appindicator android-tools
git clone https://github.com/PollyNeedCrack/PdaNet
cd PollyPdaNet
sudo ./install.sh
```

Then launch **PdaNet** from your app menu, or run `pdanet-app --tray`
(add it to autostart for boot-to-tray).

## Usage

- **Connect WiFi...** - pick the PdaNet hotspot (`DIRECT-xx-...-PdaNet`),
  enter password, done. Proxy port/proto are auto-detected.
- **Connect USB** - plug in phone, enable USB debugging, accept the prompt.
- **Hide Tether Usage** - Off / Normal / Aggressive.
- CLI equivalents: `sudo pdanet-helper up --ssid NAME [--password PW]`,
  `usb-up`, `down`, `status`, `scan`.

## Requirements

- Linux + NetworkManager (WiFi), iptables, iproute2
- tun2socks (xjasonlyu), dnsproxy, GTK3 + libayatana-appindicator (Python gi)
- android-tools (adb) only for USB mode
- An Android phone running PdaNet+

## License

MIT
