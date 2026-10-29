"""Battery state through UPower (hidden entirely on devices without a battery)."""

from __future__ import annotations

import logging

import gi

from . import Service

log = logging.getLogger("genesis.battery")

try:
    gi.require_version("UPowerGlib", "1.0")
    from gi.repository import UPowerGlib  # type: ignore
except (ValueError, ImportError):  # pragma: no cover
    UPowerGlib = None


class BatteryService(Service):
    def __init__(self) -> None:
        super().__init__()
        self.percentage = 0.0
        self.charging = False
        self.icon = "battery-missing-symbolic"
        self._device = None
        if UPowerGlib is None:
            return
        try:
            client = UPowerGlib.Client.new()
            self._device = client.get_display_device()
        except Exception as exc:  # noqa: BLE001
            log.info("UPower unavailable: %s", exc)
            return
        if self._device is None:
            return
        self._device.connect("notify", self._changed)
        self.refresh()

    def _changed(self, *_args) -> None:
        self.refresh()

    def refresh(self) -> None:
        dev = self._device
        try:
            kind = dev.props.kind
            present = bool(dev.props.is_present)
            # UPowerGlib.DeviceKind: BATTERY = 2, UPS = 3
            self.available = present and kind in (2, 3)
            self.percentage = float(dev.props.percentage)
            # DeviceState: CHARGING = 1, FULLY_CHARGED = 4
            self.charging = dev.props.state in (1, 4)
            self.icon = dev.props.icon_name or "battery-symbolic"
        except Exception:  # noqa: BLE001
            self.available = False
        self.notify()

    def status_text(self) -> str:
        if not self.available:
            return ""
        return f"{int(round(self.percentage))}%{' charging' if self.charging else ''}"
