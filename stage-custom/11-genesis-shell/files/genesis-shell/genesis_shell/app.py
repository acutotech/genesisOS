"""Shell process: wires services, windows and actions together."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gdk, GLib, Gtk, GtkLayerShell  # noqa: E402

ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")

from .config import CACHE_DIR, Config  # noqa: E402
from .control_center import ControlCenter  # noqa: E402
from .desktop import DesktopIcons  # noqa: E402
from .ipc import ShellService  # noqa: E402
from .launcher import Launcher  # noqa: E402
from .notifications import NotificationCenter, NotificationService  # noqa: E402
from .osd import Osd  # noqa: E402
from .panel import Panel  # noqa: E402
from .power import PowerDialog  # noqa: E402
from .sensors import SensorBar  # noqa: E402
from .services.apps import AppIndex  # noqa: E402
from .services.audio import AudioService  # noqa: E402
from .services.battery import BatteryService  # noqa: E402
from .services.bluetooth import BluetoothService  # noqa: E402
from .services.brightness import BrightnessService  # noqa: E402
from .services.network import NetworkService  # noqa: E402
from .services.sensors import SensorService  # noqa: E402
from .services.toplevels import ToplevelService  # noqa: E402
from .theme import Theme  # noqa: E402
from .util import monitors, pictures_dir, primary_monitor, run_async, spawn, which  # noqa: E402
from .wallpaper import WallpaperManager  # noqa: E402

log = logging.getLogger("genesis.shell")


class Shell:
    def __init__(self) -> None:
        theme = Gtk.IconTheme.get_default()
        if theme is not None and os.path.isdir(ICON_DIR):
            theme.append_search_path(ICON_DIR)  # genesis-control-center-symbolic and friends
        self.exit_code = 0
        self.config = Config()
        self.theme = Theme(self.config)

        # services (each one degrades to "unavailable" instead of raising)
        self.apps = AppIndex()
        self.audio = AudioService()
        self.network = NetworkService()
        self.bluetooth = BluetoothService()
        self.battery = BatteryService()
        self.brightness = BrightnessService()
        self.toplevels = ToplevelService()
        self.notifications = NotificationService(self)
        self.sensors = SensorService(self.config, run_async)

        # windows
        self.wallpaper = WallpaperManager(self)
        self.desktop = self._optional("desktop icons", DesktopIcons)
        self.panels: dict[Gdk.Monitor, Panel] = {}
        self.launcher = Launcher(self)
        self.control_center = ControlCenter(self)
        self.notification_center = NotificationCenter(self, "genesis-notification-center")
        self.osd = Osd(self)
        self.power = PowerDialog(self)
        self.sensor_bar = self._optional("sensor bar", SensorBar)
        self.rebuild_panels()
        self.ipc = ShellService(self)

        for svc in (self.audio, self.network, self.bluetooth, self.battery, self.notifications):
            svc.connect(self._update_status)
        self.toplevels.connect(self._refresh_docks)
        self.apps.connect(self._refresh_docks)
        self.config.connect(self._on_config)

        display = Gdk.Display.get_default()
        display.connect("monitor-added", lambda *_a: self.rebuild_panels())
        display.connect("monitor-removed", lambda *_a: self.rebuild_panels())
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, self._on_signal, sig)

    def _optional(self, what: str, factory):
        """Build an extra component; if it fails the shell runs on without it."""
        try:
            return factory(self)
        except Exception:  # noqa: BLE001
            log.exception("%s failed to start; the shell runs on without it", what)
            return None

    # -- lifecycle ------------------------------------------------------------------
    def run(self) -> int:
        log.info("Genesis Shell ready: %d monitor(s), services audio=%s network=%s bluetooth=%s battery=%s toplevels=%s",
                 len(self.panels), self.audio.available, self.network.available, self.bluetooth.available, self.battery.available, self.toplevels.available)
        Gtk.main()
        return self.exit_code

    def quit(self, code: int = 0) -> bool:
        self.exit_code = code
        Gtk.main_quit()
        return False

    def _on_signal(self, sig) -> bool:
        if sig == signal.SIGHUP:
            self.reload_theme()
            return True
        self.quit(0)
        return False

    # -- panels -----------------------------------------------------------------------
    def rebuild_panels(self) -> None:
        for panel in self.panels.values():
            panel.destroy()
        self.panels = {}
        mons = monitors()
        if not mons:
            log.warning("no monitors known to GTK yet; panels will be created on monitor-added")
        for mon in mons:
            try:
                self.panels[mon] = Panel(self, mon)
            except Exception:  # noqa: BLE001
                log.exception("cannot create panel on %s", mon.get_model() if hasattr(mon, "get_model") else mon)
        self._update_status()

    def panel_position(self) -> str:
        return "bottom" if self.config.get("panel", "position").lower() == "bottom" else "top"

    def panel_thickness(self) -> int:
        """Space the panel occupies at its edge, including the floating margin."""
        return max(32, self.config.getint("panel", "height")) + (8 if self.config.getbool("panel", "floating") else 0)

    def _update_status(self) -> None:
        for panel in self.panels.values():
            panel.update_status()

    def _refresh_docks(self) -> None:
        for panel in self.panels.values():
            panel.refresh_dock()

    def redraw_glass(self) -> None:
        for panel in self.panels.values():
            panel.redraw()
        for overlay in (self.launcher, self.control_center, self.notification_center, self.power):
            if overlay.visible:
                overlay.window.queue_draw()
        for win in (self.notifications.toasts.window, self.osd.window):
            if win.get_visible():
                win.queue_draw()
        if self.sensor_bar is not None:
            self.sensor_bar.redraw()

    def _on_config(self) -> None:
        changed = getattr(self.config, "changed", {"appearance", "panel"})
        if "appearance" in changed:
            self.theme.apply()
        if "panel" in changed:
            self.rebuild_panels()  # position, height, launchers, clock format
        self.redraw_glass()

    def reload_theme(self) -> None:
        self.config.reload()
        self.theme.apply()
        self.rebuild_panels()
        self.redraw_glass()
        # components that read their settings on change, not on redraw
        if self.desktop is not None:
            self.desktop.rebuild()
        if self.sensor_bar is not None:
            self.sensor_bar.rebind()
            self.sensor_bar.refresh()

    # -- monitors ------------------------------------------------------------------------
    def current_monitor(self) -> Gdk.Monitor | None:
        """Monitor under the pointer, falling back to the primary one."""
        display = Gdk.Display.get_default()
        try:
            seat = display.get_default_seat()
            pointer = seat.get_pointer() if seat else None
            if pointer is not None:
                _screen, x, y = pointer.get_position()
                mon = display.get_monitor_at_point(x, y)
                if mon is not None:
                    return mon
        except Exception:  # noqa: BLE001 - no pointer under Wayland without focus
            pass
        return primary_monitor()

    def on_overlay_changed(self) -> None:
        pass

    # -- overlays -----------------------------------------------------------------------------
    def hide_overlays(self, except_for=None) -> None:
        for overlay in (self.launcher, self.control_center, self.notification_center, self.power):
            if overlay is not except_for:
                overlay.hide()

    def toggle_launcher(self, monitor=None) -> None:
        self.hide_overlays(self.launcher)
        self.launcher.toggle(monitor)

    def toggle_control_center(self, monitor=None) -> None:
        self.hide_overlays(self.control_center)
        self.control_center.toggle(monitor)

    def toggle_notification_center(self, monitor=None) -> None:
        self.hide_overlays(self.notification_center)
        self.notification_center.toggle(monitor)

    def show_power(self) -> None:
        self.hide_overlays(self.power)
        self.power.show()

    # -- actions --------------------------------------------------------------------------------
    def open_settings(self, page: str | None = None) -> None:
        argv = [self.config.get("shortcuts", "settings")]
        if page:
            argv.append(page)
        if not spawn(argv):
            self.notifications.notify_local("Settings unavailable", "genesis-settings is not installed.")

    def volume_step(self, direction: int) -> None:
        if not self.audio.available:
            return
        self.audio.step(0.05 * direction)
        self.osd.show_level(self.audio.icon_name(), 0.0 if self.audio.muted else self.audio.volume)

    def volume_mute(self) -> None:
        if not self.audio.available:
            return
        self.audio.toggle_mute()
        self.osd.show_level(self.audio.icon_name(), 0.0 if self.audio.muted else self.audio.volume, "Muted" if self.audio.muted else None)

    def brightness_step(self, direction: int) -> None:
        if not self.brightness.available:
            return
        self.brightness.step(0.05 * direction)
        self.osd.show_level("display-brightness-symbolic", self.brightness.level)

    def screenshot(self, mode: str = "screen") -> None:
        if not which("grim"):
            self.notifications.notify_local("Screenshot failed", "grim is not installed.")
            return
        target = os.path.join(pictures_dir(), "Screenshots")
        os.makedirs(target, exist_ok=True)
        path = os.path.join(target, time.strftime("Screenshot_%Y-%m-%d_%H%M%S.png"))
        if mode == "area" and which("slurp"):
            argv = ["/bin/sh", "-c", 'geometry="$(slurp)" && grim -g "$geometry" "$0"', path]
        else:
            argv = ["grim", path]

        def done(status, _out):
            if status == 0 and os.path.isfile(path):
                if which("wl-copy"):
                    spawn(["/bin/sh", "-c", f'wl-copy < "{path}"'])
                self.notifications.post("Screenshot", path, "Screenshot saved", path.replace(GLib.get_home_dir(), "~"), [], {"image-path": path, "desktop-entry": "pcmanfm"})
            elif mode != "area" or status != 1:  # slurp cancelled is status 1: stay quiet
                self.notifications.notify_local("Screenshot failed", f"grim exited with status {status}.")

        # overlays would end up in the picture
        self.hide_overlays()
        GLib.timeout_add(250, lambda: (run_async(argv, done), False)[1])

    def screenshot_to(self, path: str) -> bool:
        """Synchronous screenshot for the smoke test."""
        if not which("grim"):
            return False
        try:
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            res = subprocess.run(["grim", path], capture_output=True, text=True, timeout=20, check=False)
            if res.returncode != 0:
                log.warning("grim failed: %s", res.stderr.strip())
            return res.returncode == 0
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("grim failed: %s", exc)
            return False

    def lock(self) -> None:
        if not which("swaylock"):
            self.notifications.notify_local("Cannot lock", "swaylock is not installed.")
            return
        argv = ["swaylock", "-f", "-c", "0b1420", "--indicator-radius", "90", "--indicator-thickness", "8",
                "--ring-color", self.config.get("appearance", "accent").lstrip("#"), "--key-hl-color", "ffffff",
                "--inside-color", "0e1520cc", "--inside-ver-color", "0e1520cc", "--inside-wrong-color", "4a1420cc",
                "--ring-ver-color", "3ddc97", "--ring-wrong-color", "ff5d5d", "--text-color", "e9eff6",
                "--line-color", "00000000", "--separator-color", "00000000", "--font", "Inter"]
        mon = primary_monitor()
        blur = self.wallpaper.blurred_surface(mon) if mon is not None else None
        if blur is not None:
            key = self.wallpaper._blur_key(mon)
            if key is not None:
                argv += ["-i", str(self.wallpaper._blur_file(key)), "-s", "fill"]
        spawn(argv)

    def logout(self) -> None:
        if os.environ.get("LABWC_PID") and which("labwc"):
            spawn(["labwc", "-e"])
        elif os.environ.get("XDG_SESSION_ID"):
            spawn(["loginctl", "terminate-session", os.environ["XDG_SESSION_ID"]])
        else:
            self.quit(0)

    def reboot(self) -> None:
        spawn(["systemctl", "reboot"])

    def poweroff(self) -> None:
        spawn(["systemctl", "poweroff"])

    # -- introspection for tests ------------------------------------------------------------------
    def state(self) -> dict:
        return {
            "version": __import__("genesis_shell").__version__,
            "monitors": len(self.panels),
            "launcher": self.launcher.visible,
            "control_center": self.control_center.visible,
            "notification_center": self.notification_center.visible,
            "power": self.power.visible,
            "overlays": {
                "launcher": self.launcher.describe(), "control_center": self.control_center.describe(),
                "notification_center": self.notification_center.describe(), "power": self.power.describe(),
            },
            "panels": [{"monitor": str(m.get_model()), "mapped": bool(p.window.get_mapped())} for m, p in self.panels.items()],
            "toplevels": [{"app_id": t.app_id, "title": t.title, "active": t.activated} for t in self.toplevels.windows()],
            "notifications": len(self.notifications.items),
            "notification_log": [n.summary for n in self.notifications.items[:12]],   # newest first
            "services": {
                "audio": self.audio.available, "network": self.network.available, "bluetooth": self.bluetooth.available,
                "battery": self.battery.available, "brightness": self.brightness.available, "toplevels": self.toplevels.available,
                "notifications": self.notifications.available,
            },
            "wallpaper": self.wallpaper.path(),
            "apps": len(self.apps.entries),
            "sensors": self.sensor_bar.describe() if self.sensor_bar is not None else None,
            "desktop": self.desktop.describe() if self.desktop is not None else None,
        }


def main() -> int:
    level = logging.DEBUG if os.environ.get("GENESIS_SHELL_DEBUG") else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(name)s %(levelname)s: %(message)s", stream=sys.stderr)
    if not os.environ.get("WAYLAND_DISPLAY"):
        log.error("WAYLAND_DISPLAY is not set: Genesis Shell needs a Wayland session (labwc).")
        return 1
    if not GtkLayerShell.is_supported():
        log.error("the compositor does not support wlr-layer-shell; cannot start.")
        return 1
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    shell = Shell()
    return shell.run()
