"""System services the shell observes and controls.

Every service exposes `available` (False when the backing daemon or binding is
missing), a `listeners` list of zero-argument callbacks, and never raises out
of its constructor: a missing service hides a feature, it never kills the shell.
"""

from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger("genesis.services")


class Service:
    available: bool = False

    def __init__(self) -> None:
        self.listeners: list[Callable[[], None]] = []

    def connect(self, callback: Callable[[], None]) -> None:
        self.listeners.append(callback)

    def notify(self) -> None:
        for callback in list(self.listeners):
            try:
                callback()
            except Exception:  # noqa: BLE001
                log.exception("%s listener failed", type(self).__name__)
