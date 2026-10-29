"""D-Bus control interface (org.genesis.Shell) used by keybindings, Genesis
Settings and the CI smoke test through `genesis-shell-ctl`."""

from __future__ import annotations

import json
import logging

from gi.repository import Gio, GLib

log = logging.getLogger("genesis.ipc")

BUS_NAME = "org.genesis.Shell"
OBJECT_PATH = "/org/genesis/Shell"

INTERFACE_XML = """
<node>
  <interface name="org.genesis.Shell">
    <method name="ToggleLauncher"/>
    <method name="ShowLauncher"/>
    <method name="HideLauncher"/>
    <method name="ToggleControlCenter"/>
    <method name="ToggleNotificationCenter"/>
    <method name="HideOverlays"/>
    <method name="ShowPower"/>
    <method name="Lock"/>
    <method name="Logout"/>
    <method name="SetWallpaper"><arg direction="in" name="path" type="s"/><arg direction="out" name="ok" type="b"/></method>
    <method name="ReloadTheme"/>
    <method name="VolumeUp"/>
    <method name="VolumeDown"/>
    <method name="VolumeMute"/>
    <method name="BrightnessUp"/>
    <method name="BrightnessDown"/>
    <method name="Screenshot"><arg direction="in" name="mode" type="s"/></method>
    <method name="ScreenshotTo"><arg direction="in" name="path" type="s"/><arg direction="out" name="ok" type="b"/></method>
    <method name="Notify"><arg direction="in" name="summary" type="s"/><arg direction="in" name="body" type="s"/><arg direction="out" name="id" type="u"/></method>
    <method name="ShowOsd"><arg direction="in" name="icon" type="s"/><arg direction="in" name="fraction" type="d"/><arg direction="in" name="text" type="s"/></method>
    <method name="ShowSensor"><arg direction="in" name="letter" type="s"/><arg direction="out" name="ok" type="b"/></method>
    <method name="HideSensor"/>
    <method name="GetState"><arg direction="out" name="json" type="s"/></method>
    <method name="Quit"/>
  </interface>
</node>
"""


class ShellService:
    def __init__(self, shell) -> None:
        self.shell = shell
        self._node = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML)
        self._owner = Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE, self._bus_acquired, None, self._name_lost)

    def _bus_acquired(self, connection, _name) -> None:
        try:
            connection.register_object(OBJECT_PATH, self._node.interfaces[0], self._method_call, None, None)
        except GLib.Error as exc:
            log.error("cannot register %s: %s", OBJECT_PATH, exc)

    def _name_lost(self, _connection, _name) -> None:
        log.error("%s is owned by another process; is genesis-shell already running? Exiting.", BUS_NAME)
        self.shell.quit(2)

    def _method_call(self, _connection, _sender, _path, _iface, method, params, invocation) -> None:
        s = self.shell
        try:
            args = params.unpack() if params is not None else ()
            result = None
            if method == "ToggleLauncher":
                s.toggle_launcher()
            elif method == "ShowLauncher":
                s.launcher.show()
            elif method == "HideLauncher":
                s.launcher.hide()
            elif method == "ToggleControlCenter":
                s.toggle_control_center()
            elif method == "ToggleNotificationCenter":
                s.toggle_notification_center()
            elif method == "HideOverlays":
                s.hide_overlays()
            elif method == "ShowPower":
                s.show_power()
            elif method == "Lock":
                s.lock()
            elif method == "Logout":
                s.logout()
            elif method == "SetWallpaper":
                result = GLib.Variant("(b)", (s.wallpaper.set_wallpaper(args[0]),))
            elif method == "ReloadTheme":
                s.reload_theme()
            elif method == "VolumeUp":
                s.volume_step(+1)
            elif method == "VolumeDown":
                s.volume_step(-1)
            elif method == "VolumeMute":
                s.volume_mute()
            elif method == "BrightnessUp":
                s.brightness_step(+1)
            elif method == "BrightnessDown":
                s.brightness_step(-1)
            elif method == "Screenshot":
                s.screenshot(args[0] or "screen")
            elif method == "ScreenshotTo":
                result = GLib.Variant("(b)", (s.screenshot_to(args[0]),))
            elif method == "Notify":
                result = GLib.Variant("(u)", (s.notifications.notify_local(args[0], args[1]),))
            elif method == "ShowOsd":
                s.osd.show_level(args[0] or "audio-volume-high-symbolic", float(args[1]), args[2] or None)
            elif method == "ShowSensor":
                result = GLib.Variant("(b)", (s.sensor_bar is not None and s.sensor_bar.show_card(args[0]),))
            elif method == "HideSensor":
                if s.sensor_bar is not None:
                    s.sensor_bar.card.hide()
            elif method == "GetState":
                result = GLib.Variant("(s)", (json.dumps(s.state()),))
            elif method == "Quit":
                GLib.idle_add(s.quit)
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
                return
            invocation.return_value(result)
        except Exception as exc:  # noqa: BLE001
            log.exception("IPC %s failed", method)
            invocation.return_dbus_error("org.genesis.Shell.Error", str(exc))
