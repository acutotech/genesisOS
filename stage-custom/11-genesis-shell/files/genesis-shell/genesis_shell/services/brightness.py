"""Display backlight (only present on panels such as the Raspberry Pi Touch Display)."""

from __future__ import annotations

import glob
import logging
import os

from . import Service
from ..util import run_async, which

log = logging.getLogger("genesis.brightness")

STEP = 0.05


class BrightnessService(Service):
    def __init__(self) -> None:
        super().__init__()
        self.level = 1.0
        self._path = None
        self._max = 1
        for base in sorted(glob.glob("/sys/class/backlight/*")):
            try:
                with open(os.path.join(base, "max_brightness"), encoding="ascii") as fh:
                    self._max = max(1, int(fh.read().strip()))
                self._path = base
                break
            except (OSError, ValueError):
                continue
        self._tool = which("brightnessctl")
        self.available = self._path is not None and (self._tool or os.access(os.path.join(self._path, "brightness"), os.W_OK))
        if self.available:
            self.refresh()

    def refresh(self) -> None:
        try:
            with open(os.path.join(self._path, "brightness"), encoding="ascii") as fh:
                self.level = max(0.0, min(1.0, int(fh.read().strip()) / self._max))
        except (OSError, ValueError):
            pass
        self.notify()

    def set_level(self, fraction: float) -> None:
        fraction = max(0.02, min(1.0, fraction))
        value = int(round(fraction * self._max))
        if self._tool:
            run_async(["brightnessctl", "-q", "-d", os.path.basename(self._path), "set", str(value)])
        else:
            try:
                with open(os.path.join(self._path, "brightness"), "w", encoding="ascii") as fh:
                    fh.write(str(value))
            except OSError as exc:
                log.warning("cannot set brightness: %s", exc)
        self.level = fraction
        self.notify()

    def step(self, delta: float) -> None:
        self.set_level(round((self.level + delta) / STEP) * STEP)

    def icon_name(self) -> str:
        return "display-brightness-symbolic"
