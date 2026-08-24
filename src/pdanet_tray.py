#!/usr/bin/env python3
"""PdaNet Linux tray client - mirrors the Windows PdaNetPC.exe experience.

Lives in the taskbar; click the icon for a dropdown with connection modes,
hide-tether-usage levels, live session stats and more.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time

# Ensure system GTK bindings win over the test-only gi stub that lives in src/gi
_SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
if os.path.isdir(os.path.join(_SCRIPT_DIR, "gi")):
    sys.path = [p for p in sys.path if os.path.realpath(p or ".") != _SCRIPT_DIR]

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
gi.require_version("Notify", "0.7")

from gi.repository import AyatanaAppIndicator3 as AppIndicator
from gi.repository import Gdk, GLib, Gtk, Notify

INSTALLED_HELPER = pathlib.Path("/usr/local/bin/pdanet-helper")
ICONS = pathlib.Path("/usr/local/share/pollypdanet")
CONFIG_DIR = pathlib.Path.home() / ".config" / "pdanet-linux"
CONFIG_FILE = CONFIG_DIR / "tray.json"
USB_FORWARD_PORT = 48765

CSS = b"""
window { background-color: #1e1e2e; }
label { color: #cdd6f4; }
.title { font-size: 18px; font-weight: 800; }
.dim { color: #a6adc8; }
.err { color: #f38ba8; }
"""


def load_config():
    cfg = {
        "ssid": "",
        "password": "",
        "remember": True,
        "stealth": 2,
        "auto_reconnect": False,
        "wifi_port": 8000,
    }
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text()))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not cfg.get("remember"):
            cfg = dict(cfg, password="")
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass


def fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.2f} TB"


class Backend:
    """Thin async wrappers around adb (user session) and pdanet-helper (root)."""

    @staticmethod
    def _helper_cmd():
        return ["sudo", "-n", str(INSTALLED_HELPER)]

    @classmethod
    def helper(cls, args, timeout=90, done=None):
        def work():
            try:
                p = subprocess.run(
                    cls._helper_cmd() + args, capture_output=True, text=True, timeout=timeout
                )
                out = (p.stdout or "").strip()
                err = (p.stderr or "").strip()
                ok = p.returncode == 0
            except subprocess.TimeoutExpired:
                out, err, ok = "", "timed out", False
            except FileNotFoundError:
                out, err, ok = "", "pdanet-helper not installed", False
            payload = {}
            if out:
                try:
                    payload = json.loads(out.splitlines()[-1])
                except Exception:
                    payload = {"raw": out}
            if not ok and "error" not in payload:
                payload["error"] = err or "command failed"
            if done:
                GLib.idle_add(done, payload)
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def adb(args, timeout=15):
        try:
            return subprocess.run(
                ["adb"] + args, capture_output=True, text=True, timeout=timeout
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

    @classmethod
    def adb_state(cls):
        """Return one of: 'no-adb', 'no-device', 'unauthorized', 'ready'."""
        p = cls.adb(["devices"])
        if p is None:
            return "no-adb"
        lines = [l for l in p.stdout.splitlines()[1:] if l.strip()]
        if not lines:
            return "no-device"
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                if parts[1] == "unauthorized":
                    return "unauthorized"
                if parts[1] == "device":
                    return "ready"
        return "no-device"

    @classmethod
    def usb_prepare(cls, port, done):
        """Start adb server, verify device, create forward. Async."""

        def work():
            cls.adb(["start-server"], timeout=20)
            state = cls.adb_state()
            if state != "ready":
                GLib.idle_add(done, False, state)
                return
            p = cls.adb(["forward", f"tcp:{port}", "localabstract:pdanet"])
            if p is None or p.returncode != 0:
                GLib.idle_add(done, False, "forward-failed")
                return
            GLib.idle_add(done, True, "")

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def usb_cleanup(port):
        subprocess.run(
            ["adb", "forward", "--remove", f"tcp:{port}"],
            capture_output=True, timeout=10,
        )


class WifiDialog(Gtk.Dialog):
    def __init__(self, parent, cfg):
        super().__init__(
            title="Connect WiFi Hotspot",
            transient_for=parent,
            modal=True,
        )
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("Connect", Gtk.ResponseType.OK)
        self.set_default_size(420, 380)

        box = self.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)

        lbl = Gtk.Label(label="Visible networks (PdaNet hotspot):", xalign=0)
        lbl.get_style_context().add_class("dim")
        box.pack_start(lbl, False, False, 0)

        self.store = Gtk.ListStore(str, int, str, bool)
        self.tree = Gtk.TreeView(model=self.store, headers_visible=False)
        cell = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn("Network", cell, text=0)
        col.set_expand(True)
        self.tree.append_column(col)
        col2 = Gtk.TreeViewColumn("Signal", Gtk.CellRendererText(), text=1)
        self.tree.append_column(col2)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.add(self.tree)
        box.pack_start(scroll, True, True, 0)

        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        grid.attach(Gtk.Label(label="SSID:", xalign=0), 0, 0, 1, 1)
        self.ent_ssid = Gtk.Entry(text=cfg.get("ssid", ""))
        grid.attach(self.ent_ssid, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Password:", xalign=0), 0, 1, 1, 1)
        self.ent_pass = Gtk.Entry(
            visibility=False, text=cfg.get("password", ""), sensitive=bool(cfg.get("remember"))
        )
        grid.attach(self.ent_pass, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label="Proxy port:", xalign=0), 0, 2, 1, 1)
        self.ent_port = Gtk.Entry(text=str(cfg.get("wifi_port", 8000)))
        grid.attach(self.ent_port, 1, 2, 1, 1)
        self.chk_remember = Gtk.CheckButton(
            label="Remember password", active=bool(cfg.get("remember"))
        )
        self.chk_remember.connect("toggled", self._toggle_remember)
        grid.attach(self.chk_remember, 1, 3, 1, 1)
        box.pack_start(grid, False, False, 0)

        self.err = Gtk.Label(xalign=0)
        self.err.get_style_context().add_class("err")
        box.pack_start(self.err, False, False, 0)

        self.tree.get_selection().connect("changed", self._pick)
        self.refresh_networks()
        self.show_all()

    def _toggle_remember(self, btn):
        self.ent_pass.set_sensitive(btn.get_active())

    def _pick(self, sel):
        model, it = sel.get_selected()
        if it:
            self.ent_ssid.set_text(model[it][0])

    def refresh_networks(self):
        self.store.clear()
        self.err.set_text("Scanning...")

        def work():
            nets = []
            try:
                p = subprocess.run(
                    ["sudo", "-n", str(INSTALLED_HELPER), "scan"],
                    capture_output=True, text=True, timeout=25,
                )
                data = json.loads((p.stdout or "{}").splitlines()[-1])
                nets = data.get("networks", [])
            except Exception:
                pass
            GLib.idle_add(self._fill, nets)

        threading.Thread(target=work, daemon=True).start()

    def _fill(self, nets):
        self.store.clear()
        for n in nets[:30]:
            mark = "* " if n.get("in_use") else ""
            self.store.append([mark + n["ssid"], n.get("signal", 0),
                               n.get("security", ""), n.get("in_use", False)])
        if not nets:
            self.err.set_text("Scan failed - type the SSID manually.")
        else:
            self.err.set_text("")
        return False


class TrayApp:
    def __init__(self):
        self.cfg = load_config()
        self.state = "disconnected"
        self.busy = False
        self.status_data = {}

        self.indicator = AppIndicator.Indicator.new(
            "pdanet-linux", "pdanet-off", AppIndicator.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.set_icon(False)
        self.build_menu()
        self.indicator.set_menu(self.menu)

        Notify.init("PdaNet")

        GLib.timeout_add_seconds(3, self.tick_status)
        GLib.timeout_add_seconds(5, self.tick_adb)

    # ---------- icons ----------
    def set_icon(self, connected):
        path = ICONS / ("pdanet-on.svg" if connected else "pdanet-off.svg")
        if not path.exists():
            path = pathlib.Path("network-wireless-disconnected")
        try:
            self.indicator.set_icon_full(str(path), "pdanet")
        except Exception:
            try:
                self.indicator.set_icon(str(path))
            except Exception:
                pass

    # ---------- menu ----------
    def build_menu(self):
        m = Gtk.Menu.new()

        s = self.status_data
        if self.state == "connected":
            mode = s.get("mode", "wifi").upper()
            detail = s.get("ssid") or f"port {s.get('port', '?')}"
            head = f"PdaNet Connected ({mode}) - {detail}"
        elif self.busy:
            head = "PdaNet - Connecting..."
        else:
            head = "PdaNet - Not Connected"
        item = Gtk.MenuItem.new_with_label(head)
        item.set_sensitive(False)
        item.show()
        m.append(item)

        if self.state == "connected":
            up = int(s.get("uptime", 0))
            rx = int(s.get("rx_bytes", 0))
            tx = int(s.get("tx_bytes", 0))
            info = Gtk.MenuItem.new_with_label(
                f"{fmt_bytes(rx)} down / {fmt_bytes(tx)} up - {up // 60}m{up % 60}s"
            )
            info.set_sensitive(False)
            info.show()
            m.append(info)

        m.append(Gtk.SeparatorMenuItem.new())

        # --- Connect USB ---
        adb_state = getattr(self, "_adb_state", "unknown")
        usb_lbl = {
            "no-adb": "Connect USB (install android-tools)",
            "no-device": "Connect USB (no phone detected)",
            "unauthorized": "Connect USB (accept prompt on phone!)",
        }.get(adb_state, "Connect USB")
        usb = Gtk.MenuItem.new_with_label(usb_lbl)
        usb.set_sensitive(
            self.state == "disconnected" and not self.busy and adb_state == "ready"
        )
        usb.connect("activate", lambda *_: self.connect_usb())
        usb.show()
        m.append(usb)

        # --- Connect WiFi ---
        wifi = Gtk.MenuItem.new_with_label("Connect WiFi...")
        wifi.set_sensitive(self.state == "disconnected" and not self.busy)
        wifi.connect("activate", lambda *_: self.open_wifi_dialog())
        wifi.show()
        m.append(wifi)

        disc = Gtk.MenuItem.new_with_label("Disconnect")
        disc.set_sensitive(self.state == "connected" and not self.busy)
        disc.connect("activate", lambda *_: self.disconnect())
        disc.show()
        m.append(disc)

        m.append(Gtk.SeparatorMenuItem.new())

        # --- Hide tether usage ---
        hide = Gtk.MenuItem.new_with_label("Hide Tether Usage")
        hide.show()
        submenu = Gtk.Menu.new()
        self._stealth_items = []
        first = None
        for level, label in ((0, "Off"), (1, "Normal (TTL fix)"), (2, "Aggressive")):
            if first is None:
                mi = Gtk.RadioMenuItem.new_with_label([], label)
                first = mi
            else:
                mi = Gtk.RadioMenuItem.new_with_label_from_widget(first, label)
            mi.set_active(int(self.cfg.get("stealth", 2)) == level)
            mi.connect("toggled", self.on_stealth, level)
            mi.show()
            submenu.append(mi)
            self._stealth_items.append(mi)
        hide.set_submenu(submenu)
        m.append(hide)

        auto = Gtk.CheckMenuItem.new_with_label("Auto-reconnect WiFi")
        auto.set_active(bool(self.cfg.get("auto_reconnect")))
        auto.connect("toggled", self.on_auto_reconnect)
        auto.show()
        m.append(auto)

        m.append(Gtk.SeparatorMenuItem.new())

        about = Gtk.MenuItem.new_with_label("About")
        about.connect("activate", lambda *_: self.show_about())
        about.show()
        m.append(about)

        quit_ = Gtk.MenuItem.new_with_label("Quit")
        quit_.connect("activate", self.on_quit)
        quit_.show()
        m.append(quit_)

        self.menu = m
        self.indicator.set_menu(m)

    # ---------- actions ----------
    def set_busy(self, busy):
        self.busy = busy
        self.build_menu()

    def connect_usb(self):
        if self.busy:
            return
        self.set_busy(True)
        self.notify("PdaNet", "Connecting via USB...")
        Backend.usb_prepare(
            USB_FORWARD_PORT,
            lambda ok, why: self._usb_prepared(ok, why),
        )

    def _usb_prepared(self, ok, why):
        if not ok:
            self.set_busy(False)
            msg = {
                "no-adb": "adb is not installed.\nRun: omarchy pkg add android-tools",
                "no-device": "No phone detected over USB.\nEnable USB debugging and reconnect.",
                "unauthorized": "Accept the USB debugging prompt on your phone.",
                "forward-failed": "adb forward failed.",
            }.get(why, f"USB setup failed: {why}")
            self.notify_error("PdaNet USB", msg)
            return
        Backend.helper(
            ["usb-up", "--port", str(USB_FORWARD_PORT), "--stealth",
             "1" if int(self.cfg.get("stealth", 2)) >= 1 else "0"],
            done=lambda p: self._helper_result(p, "USB"),
        )

    def connect_wifi(self, ssid, password, remember, port):
        self.cfg.update(ssid=ssid, password=password if remember else "",
                        remember=remember, wifi_port=port)
        save_config(self.cfg)
        self.set_busy(True)
        args = ["up", "--ssid", ssid, "--stealth", str(int(self.cfg.get("stealth", 2))),
                "--port", str(port)]
        if password:
            args += ["--password", password]
        Backend.helper(args, timeout=120, done=lambda p: self._helper_result(p, "WiFi"))

    def disconnect(self):
        self.set_busy(True)
        Backend.helper(["down"], done=self._after_down)

    def _after_down(self, payload):
        Backend.usb_cleanup(USB_FORWARD_PORT)
        self._helper_result(payload, "Disconnect")

    def _helper_result(self, payload, label):
        self.set_busy(False)
        if payload.get("state") == "connected" or payload.get("ok") and label == "Disconnect":
            if label in ("USB", "WiFi"):
                self.notify(f"PdaNet Connected ({label})", "")
        if payload.get("error") and payload.get("error") != "already connected":
            self.notify_error(f"PdaNet {label}", payload["error"])
        self.poll_now()

    def on_stealth(self, mi, level):
        if mi.get_active():
            self.cfg["stealth"] = level
            save_config(self.cfg)

    def on_auto_reconnect(self, mi):
        self.cfg["auto_reconnect"] = mi.get_active()
        save_config(self.cfg)

    # ---------- dialogs ----------
    def open_wifi_dialog(self):
        dlg = WifiDialog(self.get_any_window(), self.cfg)

        def response(d, resp):
            if resp == Gtk.ResponseType.OK:
                ssid = d.ent_ssid.get_text().strip().lstrip("* ").strip()
                pw = d.ent_pass.get_text()
                try:
                    port = int(d.ent_port.get_text())
                except ValueError:
                    port = 8000
                remember = d.chk_remember.get_active()
                if ssid:
                    d.destroy()
                    self.set_busy(True)
                    self.connect_wifi(ssid, pw, remember, port)
                    return
                d.err.set_text("SSID required.")
            else:
                d.destroy()

        dlg.connect("response", response)
        dlg.run()
        # run() blocks until destroy; response handler does the work

    def get_any_window(self):
        wins = Gtk.Window.list_toplevels()
        return wins[0] if wins else None

    def show_about(self):
        d = Gtk.AboutDialog()
        d.set_program_name("PdaNet Linux")
        d.set_version("2.0")
        d.set_comments("PdaNet+ tethering client for Linux\nUSB + WiFi modes, carrier bypass")
        d.run()
        d.destroy()

    # ---------- polling ----------
    def poll_now(self):
        threading.Thread(target=self._poll_work, daemon=True).start()

    def tick_status(self):
        self.poll_now()
        return True

    def _poll_work(self):
        try:
            p = subprocess.run(
                self._helper_status_cmd(),
                capture_output=True, text=True, timeout=15,
            )
            data = json.loads((p.stdout or '{"state":"disconnected"}').splitlines()[-1])
        except Exception:
            data = {"state": "unknown"}
        GLib.idle_add(self.apply_status, data)

    @staticmethod
    def _helper_status_cmd():
        return ["sudo", "-n", str(INSTALLED_HELPER), "status"]

    def apply_status(self, data):
        prev = self.state
        self.status_data = data
        self.state = data.get("state", "disconnected")
        if self.state != prev or True:
            self.set_icon(self.state == "connected")
            try:
                self.indicator.set_title("PdaNet")
            except Exception:
                pass
            self.build_menu()
        return False

    def tick_adb(self):
        if self.state == "disconnected" and not self.busy:

            def work():
                st = Backend.adb_state()
                GLib.idle_add(self._apply_adb_state, st)

            threading.Thread(target=work, daemon=True).start()
        return True

    def _apply_adb_state(self, st):
        if st != getattr(self, "_adb_state", None):
            self._adb_state = st
            self.build_menu()
        return False

    # ---------- misc ----------
    def notify(self, title, body):
        try:
            n = Notify.Notification.new(title, body, str(ICONS / "pdanet-on.svg"))
            n.show()
        except Exception:
            pass

    def notify_error(self, title, body):
        try:
            n = Notify.Notification.new(title, body, "dialog-error")
            n.show()
        except Exception:
            pass

    def on_quit(self, *_):
        Notify.uninit()
        Gtk.main_quit()


def main():
    win = Gtk.OffscreenWindow()
    style = Gtk.CssProvider()
    style.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), style, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    app = TrayApp()
    if "--tray" not in sys.argv and "--hidden" not in sys.argv:
        app.open_wifi_dialog()
    Gtk.main()


if __name__ == "__main__":
    main()
