"""Bluetooth through BlueZ on the system bus (org.bluez object manager)."""

from __future__ import annotations

import logging

from gi.repository import Gio, GLib

from . import Service

log = logging.getLogger("genesis.bluetooth")

ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"


class BluetoothService(Service):
    def __init__(self) -> None:
        super().__init__()
        self.powered = False
        self.devices: list[dict] = []
        self._manager = None
        try:
            # (bus_type, flags, name, object_path, get_proxy_type_func, get_proxy_type_user_data, cancellable)
            self._manager = Gio.DBusObjectManagerClient.new_for_bus_sync(
                Gio.BusType.SYSTEM, Gio.DBusObjectManagerClientFlags.NONE, "org.bluez", "/", None, None, None
            )
        except GLib.Error as exc:
            log.info("BlueZ unavailable: %s", exc)
            return
        self._manager.connect("interface-proxy-properties-changed", self._changed)
        self._manager.connect("object-added", self._changed)
        self._manager.connect("object-removed", self._changed)
        self.refresh()

    def _changed(self, *_args) -> None:
        self.refresh()

    def adapter(self):
        if self._manager is None:
            return None
        for obj in self._manager.get_objects():
            iface = obj.get_interface(ADAPTER)
            if iface is not None:
                return iface
        return None

    @staticmethod
    def _prop(proxy, name: str, default):
        value = proxy.get_cached_property(name)
        if value is None:
            return default
        try:
            return value.unpack()
        except Exception:  # noqa: BLE001
            return default

    def refresh(self) -> None:
        adapter = self.adapter()
        self.available = adapter is not None and self._manager.get_name_owner() is not None
        self.powered = bool(self._prop(adapter, "Powered", False)) if adapter is not None else False
        devices = []
        if self._manager is not None:
            for obj in self._manager.get_objects():
                dev = obj.get_interface(DEVICE)
                if dev is None:
                    continue
                name = self._prop(dev, "Alias", "") or self._prop(dev, "Name", "") or self._prop(dev, "Address", "device")
                devices.append(
                    {
                        "path": obj.get_object_path(),
                        "name": name,
                        "connected": bool(self._prop(dev, "Connected", False)),
                        "paired": bool(self._prop(dev, "Paired", False)),
                        "trusted": bool(self._prop(dev, "Trusted", False)),
                        "icon": self._prop(dev, "Icon", "") or "bluetooth-symbolic",
                        "battery": None,
                    }
                )
                batt = obj.get_interface("org.bluez.Battery1")
                if batt is not None:
                    devices[-1]["battery"] = self._prop(batt, "Percentage", None)
        devices = [d for d in devices if d["paired"] or d["connected"]]
        devices.sort(key=lambda d: (not d["connected"], d["name"].casefold()))
        self.devices = devices
        self.notify()

    # -- actions -------------------------------------------------------------------
    def set_powered(self, on: bool, on_done=None) -> None:
        adapter = self.adapter()
        if adapter is None:
            return

        def done(proxy, result):
            try:
                proxy.call_finish(result)
                ok = True
            except GLib.Error as exc:
                log.warning("cannot set Bluetooth power: %s", exc)
                ok = False
            if on_done:
                on_done(ok)

        adapter.call(
            "org.freedesktop.DBus.Properties.Set",
            GLib.Variant("(ssv)", (ADAPTER, "Powered", GLib.Variant("b", on))),
            Gio.DBusCallFlags.NONE, 10000, None, done,
        )

    def set_connected(self, path: str, connect: bool, on_done=None) -> None:
        dev = self._manager.get_interface(path, DEVICE) if self._manager is not None else None
        if dev is None:
            return

        def done(proxy, result):
            try:
                proxy.call_finish(result)
                ok = True
            except GLib.Error as exc:
                log.warning("Bluetooth %s failed: %s", "connect" if connect else "disconnect", exc)
                ok = False
            if on_done:
                on_done(ok)

        dev.call("Connect" if connect else "Disconnect", None, Gio.DBusCallFlags.NONE, 30000, None, done)

    # -- presentation ----------------------------------------------------------------
    def connected_devices(self) -> list[dict]:
        return [d for d in self.devices if d["connected"]]

    def icon_name(self) -> str:
        if not self.available or not self.powered:
            return "bluetooth-disabled-symbolic"
        return "bluetooth-active-symbolic"

    def status_text(self) -> str:
        if not self.available:
            return "Unavailable"
        if not self.powered:
            return "Off"
        connected = self.connected_devices()
        if connected:
            return connected[0]["name"] if len(connected) == 1 else f"{len(connected)} devices"
        return "On"
