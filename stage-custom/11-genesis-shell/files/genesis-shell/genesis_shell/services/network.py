"""Network state through libnm (GObject introspection); actions through nmcli."""

from __future__ import annotations

import logging

import gi

from gi.repository import GLib

from . import Service
from ..util import run_async, which

log = logging.getLogger("genesis.network")

try:
    gi.require_version("NM", "1.0")
    from gi.repository import NM  # type: ignore
except (ValueError, ImportError):  # pragma: no cover - depends on the image
    NM = None


class NetworkService(Service):
    def __init__(self) -> None:
        super().__init__()
        self.client = None
        self.wifi_enabled = False
        self.connected = False
        self.wired = False
        self.ssid = ""
        self.strength = 0
        self.ifname = ""
        self.connecting = False
        if NM is None:
            log.warning("gir1.2-nm-1.0 missing: network indicator disabled")
            return
        try:
            self.client = NM.Client.new(None)
        except GLib.Error as exc:
            log.warning("NetworkManager unavailable: %s", exc)
            return
        self.available = True
        for prop in ("primary-connection", "wireless-enabled", "state", "connectivity", "active-connections"):
            self.client.connect(f"notify::{prop}", self._changed)
        self.client.connect("device-added", lambda _c, dev: self._hook_device(dev))
        self.client.connect("device-removed", self._changed)
        for dev in self.client.get_devices():
            self._hook_device(dev)
        self.refresh()

    # -- plumbing ------------------------------------------------------------------
    def _hook_device(self, dev) -> None:
        try:
            dev.connect("state-changed", self._changed)
            if dev.get_device_type() == NM.DeviceType.WIFI:
                dev.connect("notify::active-access-point", self._changed)
                dev.connect("access-point-added", self._changed)
                dev.connect("access-point-removed", self._changed)
        except Exception:  # noqa: BLE001
            log.exception("cannot watch device")
        self._changed()

    def _changed(self, *_args) -> None:
        self.refresh()

    def wifi_device(self):
        if self.client is None:
            return None
        for dev in self.client.get_devices():
            if dev.get_device_type() == NM.DeviceType.WIFI:
                return dev
        return None

    @staticmethod
    def _ssid_text(ap) -> str:
        if ap is None:
            return ""
        ssid = ap.get_ssid()
        if ssid is None:
            return ""
        try:
            return NM.utils_ssid_to_utf8(ssid.get_data()) or ""
        except Exception:  # noqa: BLE001
            return ""

    # -- state -----------------------------------------------------------------------
    def refresh(self) -> None:
        if self.client is None:
            return
        try:
            self.wifi_enabled = bool(self.client.wireless_get_enabled())
            primary = self.client.get_primary_connection()
            state = self.client.get_state()
            self.connected = primary is not None and state >= NM.State.CONNECTED_LOCAL
            ctype = primary.get_connection_type() if primary is not None else ""
            self.wired = self.connected and ctype in ("802-3-ethernet", "bridge", "bond")
            dev = self.wifi_device()
            self.ifname = dev.get_iface() if dev is not None else ""
            ap = dev.get_active_access_point() if dev is not None else None
            self.ssid = self._ssid_text(ap)
            self.strength = int(ap.get_strength()) if ap is not None else 0
            self.connecting = dev is not None and dev.get_state() in (
                NM.DeviceState.PREPARE, NM.DeviceState.CONFIG, NM.DeviceState.NEED_AUTH,
                NM.DeviceState.IP_CONFIG, NM.DeviceState.IP_CHECK, NM.DeviceState.SECONDARIES,
            )
        except Exception:  # noqa: BLE001
            log.exception("network refresh failed")
        self.notify()

    def access_points(self) -> list[dict]:
        dev = self.wifi_device()
        if dev is None:
            return []
        active = dev.get_active_access_point()
        best: dict[str, dict] = {}
        known = self.known_ssids()
        for ap in dev.get_access_points():
            ssid = self._ssid_text(ap)
            if not ssid:
                continue
            secure = bool(int(ap.get_flags()) & 0x1) or int(ap.get_wpa_flags()) != 0 or int(ap.get_rsn_flags()) != 0
            entry = {
                "ssid": ssid,
                "strength": int(ap.get_strength()),
                "secure": secure,
                "active": active is not None and ap.get_path() == active.get_path(),
                "known": ssid in known,
            }
            if ssid not in best or entry["active"] or entry["strength"] > best[ssid]["strength"]:
                if ssid in best and best[ssid]["active"]:
                    continue
                best[ssid] = entry
        return sorted(best.values(), key=lambda e: (not e["active"], -e["strength"], e["ssid"].casefold()))

    def known_ssids(self) -> set[str]:
        names = set()
        if self.client is None:
            return names
        for conn in self.client.get_connections():
            try:
                if conn.get_connection_type() == "802-11-wireless":
                    names.add(conn.get_id())
            except Exception:  # noqa: BLE001
                continue
        return names

    def scan(self) -> None:
        dev = self.wifi_device()
        if dev is None or not self.wifi_enabled:
            return

        def done(d, result):
            try:
                d.request_scan_finish(result)
            except GLib.Error as exc:  # too frequent, device busy: harmless
                log.debug("scan: %s", exc)

        try:
            dev.request_scan_async(None, done)
        except Exception as exc:  # noqa: BLE001
            log.debug("scan request failed: %s", exc)

    # -- actions -----------------------------------------------------------------------
    def set_wifi_enabled(self, enabled: bool, on_done=None) -> None:
        if which("nmcli"):
            run_async(["nmcli", "radio", "wifi", "on" if enabled else "off"], lambda st, _o: on_done and on_done(st == 0))
        elif self.client is not None:
            self.client.wireless_set_enabled(enabled)

    def connect_ssid(self, ssid: str, password: str | None = None, on_done=None) -> None:
        argv = ["nmcli", "connection", "up", "id", ssid] if ssid in self.known_ssids() else ["nmcli", "device", "wifi", "connect", ssid]
        if password and argv[1] == "device":
            argv += ["password", password]
        if self.ifname and argv[1] == "device":
            argv += ["ifname", self.ifname]
        run_async(argv, lambda st, out: on_done and on_done(st == 0, out.strip()))

    def disconnect_wifi(self, on_done=None) -> None:
        if self.ifname:
            run_async(["nmcli", "device", "disconnect", self.ifname], lambda st, out: on_done and on_done(st == 0, out))

    def forget(self, ssid: str, on_done=None) -> None:
        run_async(["nmcli", "connection", "delete", "id", ssid], lambda st, out: on_done and on_done(st == 0, out))

    # -- presentation --------------------------------------------------------------------
    def icon_name(self) -> str:
        if not self.available:
            return "network-offline-symbolic"
        if self.wired:
            return "network-wired-symbolic"
        if not self.wifi_enabled:
            return "network-wireless-disabled-symbolic"
        if self.connecting:
            return "network-wireless-acquiring-symbolic"
        if not self.connected or not self.ssid:
            return "network-wireless-offline-symbolic"
        return self.strength_icon(self.strength)

    @staticmethod
    def strength_icon(strength: int) -> str:
        if strength > 80:
            return "network-wireless-signal-excellent-symbolic"
        if strength > 55:
            return "network-wireless-signal-good-symbolic"
        if strength > 30:
            return "network-wireless-signal-ok-symbolic"
        if strength > 5:
            return "network-wireless-signal-weak-symbolic"
        return "network-wireless-signal-none-symbolic"

    def status_text(self) -> str:
        if not self.available:
            return "Unavailable"
        if self.wired:
            return "Wired connection"
        if not self.wifi_enabled:
            return "Wi-Fi off"
        if self.connecting:
            return "Connecting…"
        if self.connected and self.ssid:
            return self.ssid
        return "Not connected"
