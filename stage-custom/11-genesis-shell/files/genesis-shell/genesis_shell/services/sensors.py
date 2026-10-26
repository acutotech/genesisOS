"""I2C sensor boxes A-K: which are on the bus, and when they come and go.

The NodoAI sensor boxes answer on fixed I2C address ranges (three addresses
per box, A at 0x10-0x12 up to K at 0x38-0x3A). The service probes only that
range with `i2cdetect -y BUS 0x10 0x3a` every poll interval, without blocking
the shell, and reports two things to its listeners:

* `notify()` whenever the set of connected boxes (or an address) changed;
* `on_events(connected, disconnected)` with what changed since the last scan.

The first scan is the baseline and raises no events (no "connected" storm at
login). A box counts as gone only after it is missed twice in a row, so one
probe lost to bus traffic does not flash the bar or post a notification.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from typing import Callable

from gi.repository import GLib

from . import Service

log = logging.getLogger("genesis.sensors")

# box letter -> the I2C addresses it may answer on (from the NodoAI sensor tray)
SENSOR_ADDRS: dict[str, tuple[int, ...]] = {
    "A": (0x10, 0x11, 0x12),
    "B": (0x14, 0x15, 0x16),
    "C": (0x18, 0x19, 0x1A),
    "D": (0x1C, 0x1D, 0x1E),
    "E": (0x20, 0x21, 0x22),
    "F": (0x24, 0x25, 0x26),
    "G": (0x28, 0x29, 0x2A),
    "H": (0x2C, 0x2D, 0x2E),
    "I": (0x30, 0x31, 0x32),
    "J": (0x34, 0x35, 0x36),
    "K": (0x38, 0x39, 0x3A),
}
LETTERS = tuple(SENSOR_ADDRS)
FIRST_ADDR = min(min(a) for a in SENSOR_ADDRS.values())
LAST_ADDR = max(max(a) for a in SENSOR_ADDRS.values())
MISSES_TO_DISCONNECT = 2
SCAN_TIMEOUT_S = 15      # a scan still running after this counts as a missed scan
MAX_POLL_MS = 3_600_000

_ROW = re.compile(r"^\s*([0-9a-fA-F]{2}):(.*)$")


def parse_i2cdetect(text: str) -> set[int]:
    """Addresses that answered in `i2cdetect -y` output.

    A number is a device that answered; "UU" is an address a kernel driver has
    claimed, so a device is there too; "--" is nothing.
    """
    found: set[int] = set()
    for line in text.splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        base = int(m.group(1), 16)
        cells = m.group(2)
        # each cell is 3 characters wide (" 10", " --", " UU", or blanks outside the scanned range)
        for i in range(16):
            cell = cells[i * 3:i * 3 + 3].strip()
            if not cell or cell == "--":
                continue
            if cell == "UU":
                found.add(base + i)
                continue
            try:
                found.add(int(cell, 16))
            except ValueError:
                continue
    return found


def sensor_addresses(found: set[int]) -> dict[str, int | None]:
    """For every box, the first of its addresses that answered (None: not on the bus)."""
    return {letter: next((a for a in addrs if a in found), None) for letter, addrs in SENSOR_ADDRS.items()}


@dataclass
class SensorState:
    letter: str
    address: int | None = None
    since: float = 0.0        # when it connected (time.time())
    last_seen: float = 0.0
    misses: int = 0

    @property
    def connected(self) -> bool:
        return self.address is not None


class SensorTracker:
    """Debounced state of all boxes. Pure logic: fed scan results, returns events."""

    def __init__(self, misses_to_disconnect: int = MISSES_TO_DISCONNECT) -> None:
        self.limit = max(1, misses_to_disconnect)
        self.states = {letter: SensorState(letter) for letter in LETTERS}
        self.baseline = False

    def update(self, found: set[int], now: float | None = None) -> tuple[list[SensorState], list[tuple[SensorState, int]], bool]:
        """Apply one scan. Returns (connected, disconnected with their last address, anything changed)."""
        now = time.time() if now is None else now
        connected: list[SensorState] = []
        disconnected: list[tuple[SensorState, int]] = []
        changed = False
        for letter, addr in sensor_addresses(found).items():
            st = self.states[letter]
            if addr is not None:
                st.misses = 0
                st.last_seen = now
                if st.address is None:
                    st.address = addr
                    st.since = now
                    connected.append(st)
                    changed = True
                elif st.address != addr:
                    st.address = addr          # moved within its range: same box, no event
                    changed = True
            elif st.address is not None:
                st.misses += 1
                if st.misses >= self.limit:
                    disconnected.append((st, st.address))
                    st.address = None
                    st.misses = 0
                    changed = True
        if not self.baseline:
            # the first scan only establishes what is there at login
            self.baseline = True
            return [], [], changed
        return connected, disconnected, changed

    def connected(self) -> list[SensorState]:
        return [st for st in self.states.values() if st.connected]


def describe_events(connected: list[SensorState], disconnected: list[tuple[SensorState, int]], bus: int) -> list[dict]:
    """Notification texts for one scan's events: one per box, or one summary per kind."""
    out: list[dict] = []
    if len(connected) == 1:
        st = connected[0]
        out.append({"letters": [st.letter], "kind": "connected", "summary": f"Sensor {st.letter} connected",
                    "body": f"Sensor box {st.letter} is on I²C address 0x{st.address:02X} (bus {bus})."})
    elif connected:
        letters = [st.letter for st in connected]
        out.append({"letters": letters, "kind": "connected", "summary": f"{len(letters)} sensors connected",
                    "body": f"Sensor boxes {join_letters(letters)} are on I²C bus {bus}."})
    if len(disconnected) == 1:
        st, addr = disconnected[0]
        out.append({"letters": [st.letter], "kind": "disconnected", "summary": f"Sensor {st.letter} disconnected",
                    "body": f"Sensor box {st.letter} (0x{addr:02X}) is no longer on I²C bus {bus}."})
    elif disconnected:
        letters = [st.letter for st, _a in disconnected]
        out.append({"letters": letters, "kind": "disconnected", "summary": f"{len(letters)} sensors disconnected",
                    "body": f"Sensor boxes {join_letters(letters)} are no longer on I²C bus {bus}."})
    return out


def join_letters(letters: list[str]) -> str:
    return letters[0] if len(letters) == 1 else ", ".join(letters[:-1]) + " and " + letters[-1]


def find_i2cdetect() -> str | None:
    # i2c-tools installs to /usr/sbin, which is not on a normal user's PATH
    return shutil.which("i2cdetect") or next((p for p in ("/usr/sbin/i2cdetect", "/sbin/i2cdetect") if os.access(p, os.X_OK)), None)


class SensorService(Service):
    """Polls the bus on the GLib main loop; never blocks it and never raises."""

    def __init__(self, config, run_async: Callable) -> None:
        super().__init__()
        self.config = config
        self._run_async = run_async
        self.event_listeners: list[Callable[[list, list], None]] = []
        self.tracker = SensorTracker()
        self.enabled = config.getbool("sensors", "enabled")
        self.bus = max(0, config.getint("sensors", "bus"))
        self.fake = os.environ.get("GENESIS_SENSORS_FAKE", "")   # tests: a file of hex addresses instead of the bus
        self.tool = find_i2cdetect()
        self.error = ""
        self._failures = 0
        self._busy = False
        self._source = 0
        self._watchdog = 0
        self.available = self.enabled and bool(self.fake or (self.tool and os.path.exists(self.device)))
        if not self.enabled:
            log.info("sensor bar disabled in shell.conf")
            return
        if not self.fake and not self.tool:
            self.error = "i2cdetect not found (install i2c-tools)"
            log.warning("%s; sensor boxes cannot be detected", self.error)
            return
        GLib.idle_add(self._poll)

    @property
    def device(self) -> str:
        return f"/dev/i2c-{self.bus}"

    def interval_ms(self) -> int:
        if self.fake:
            return 500
        return min(MAX_POLL_MS, max(500, self.config.getint("sensors", "poll_ms")))

    def on_events(self, callback: Callable[[list, list], None]) -> None:
        self.event_listeners.append(callback)

    def connected(self) -> list[SensorState]:
        return self.tracker.connected()

    def state(self, letter: str) -> SensorState | None:
        return self.tracker.states.get(letter)

    # -- polling ---------------------------------------------------------------------
    def _schedule(self) -> None:
        if self._source:
            GLib.source_remove(self._source)
        self._source = GLib.timeout_add(self.interval_ms(), self._poll)

    def _poll(self) -> bool:
        self._source = 0
        if self._busy:              # a slow bus: never stack scans
            self._schedule()
            return False
        if self.fake:
            try:
                with open(self.fake, encoding="utf-8") as fh:
                    found = {int(tok, 16) for tok in fh.read().split() if re.fullmatch(r"(0x)?[0-9a-fA-F]{1,2}", tok)}
            except OSError:
                found = set()
            self._apply(found)
            self._schedule()
            return False
        if not os.path.exists(self.device):
            # I2C switched off (raspi-config / Genesis Settings): check again later
            message = f"{self.device} does not exist (I2C is off)"
            if self.available or self.tracker.connected():
                self._apply_failure(message)
            self.error = message
            self.available = False
            self._source = GLib.timeout_add(10000, self._poll)
            return False
        self.available = True
        self._busy = True
        self._watchdog = GLib.timeout_add_seconds(SCAN_TIMEOUT_S, self._scan_overdue)
        self._run_async([self.tool, "-y", str(self.bus), f"0x{FIRST_ADDR:02x}", f"0x{LAST_ADDR:02x}"], self._scanned)
        return False

    def _scan_overdue(self) -> bool:
        # a scan that does not finish is a missed scan, every SCAN_TIMEOUT_S, until it does;
        # no new scan starts meanwhile, so they never pile up behind a stuck bus
        self._apply_failure(f"i2cdetect did not finish within {SCAN_TIMEOUT_S} s")
        return True

    def _scanned(self, status: int, out: str) -> None:
        self._busy = False
        if self._watchdog:
            GLib.source_remove(self._watchdog)
            self._watchdog = 0
        if status == 0:
            if self._failures:
                log.info("I2C bus %d readable again", self.bus)
            self._failures = 0
            self.error = ""
            self._apply(parse_i2cdetect(out))
        else:
            self._apply_failure(f"i2cdetect exited with status {status}: {out.strip()[:200]}")
        self._schedule()

    def _apply_failure(self, message: str) -> None:
        self._failures += 1
        if self._failures == 1:
            log.warning("cannot scan I2C bus %d: %s", self.bus, message)
        self.error = message
        # a bus that stays unreadable has no boxes on it; the misses take them off one by one
        if self._failures >= MISSES_TO_DISCONNECT or self.tracker.connected():
            self._apply(set())

    def _apply(self, found: set[int]) -> None:
        connected, disconnected, changed = self.tracker.update(found)
        if changed:
            self.notify()
        if connected or disconnected:
            log.info("sensors: +%s -%s", [s.letter for s in connected], [s.letter for s, _a in disconnected])
            for callback in list(self.event_listeners):
                try:
                    callback(connected, disconnected)
                except Exception:  # noqa: BLE001
                    log.exception("sensor event listener failed")
