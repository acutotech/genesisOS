"""Open windows via wlr-foreign-toplevel-management (pywayland), on a second
Wayland connection whose fd is polled from the GLib main loop."""

from __future__ import annotations

import logging
import struct

from gi.repository import GLib

from . import Service

log = logging.getLogger("genesis.toplevels")

STATE_MAXIMIZED, STATE_MINIMIZED, STATE_ACTIVATED, STATE_FULLSCREEN = 0, 1, 2, 3


class Toplevel:
    def __init__(self, handle) -> None:
        self.handle = handle
        self.title = ""
        self.app_id = ""
        self.states: set[int] = set()
        self.ready = False

    @property
    def activated(self) -> bool:
        return STATE_ACTIVATED in self.states

    @property
    def minimized(self) -> bool:
        return STATE_MINIMIZED in self.states

    @property
    def maximized(self) -> bool:
        return STATE_MAXIMIZED in self.states


class ToplevelService(Service):
    def __init__(self) -> None:
        super().__init__()
        self.toplevels: dict[int, Toplevel] = {}
        self._display = None
        self._manager = None
        self._seat = None
        self._watch = 0
        try:
            from pywayland.client import Display  # type: ignore

            from ..protocols.wayland import WlSeat  # type: ignore
            from ..protocols.wlr_foreign_toplevel_management_unstable_v1 import ZwlrForeignToplevelManagerV1  # type: ignore
        except Exception as exc:  # noqa: BLE001 - bindings not generated / pywayland missing
            log.warning("foreign-toplevel support unavailable (%s): window list disabled", exc)
            return
        self._WlSeat = WlSeat
        self._Manager = ZwlrForeignToplevelManagerV1
        try:
            self._display = Display()
            self._display.connect()
            registry = self._display.get_registry()
            self._registry = registry  # keep the proxy alive: its dispatcher delivers later globals
            registry.dispatcher["global"] = self._on_global
            registry.dispatcher["global_remove"] = lambda *_a: None
            self._display.roundtrip()
            if self._manager is None:
                log.warning("compositor offers no zwlr_foreign_toplevel_manager_v1: window list disabled")
                self._display.disconnect()
                self._display = None
                return
            self._display.roundtrip()  # initial toplevels
            self._watch = GLib.io_add_watch(self._display.get_fd(), GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN, self._on_readable)
            self._display.flush()
            self.available = True
        except Exception as exc:  # noqa: BLE001
            log.warning("foreign-toplevel setup failed: %s", exc)
            self._display = None

    # -- protocol plumbing -------------------------------------------------------
    def _on_global(self, registry, name, interface, version) -> None:
        if interface == "zwlr_foreign_toplevel_manager_v1":
            self._manager = registry.bind(name, self._Manager, min(version, 3))
            self._manager.dispatcher["toplevel"] = self._on_toplevel
            self._manager.dispatcher["finished"] = lambda *_a: None
        elif interface == "wl_seat" and self._seat is None:
            self._seat = registry.bind(name, self._WlSeat, min(version, 7))

    def _on_toplevel(self, _manager, handle) -> None:
        top = Toplevel(handle)
        key = id(handle)
        self.toplevels[key] = top

        def on_title(_h, title):
            top.title = title or ""

        def on_app_id(_h, app_id):
            top.app_id = app_id or ""

        def on_state(_h, states):
            top.states = self._decode_states(states)

        def on_done(_h):
            top.ready = True
            self.notify()

        def on_closed(h):
            self.toplevels.pop(key, None)
            try:
                h.destroy()
            except Exception:  # noqa: BLE001
                pass
            self.notify()

        handle.dispatcher["title"] = on_title
        handle.dispatcher["app_id"] = on_app_id
        handle.dispatcher["state"] = on_state
        handle.dispatcher["done"] = on_done
        handle.dispatcher["closed"] = on_closed
        for ev in ("output_enter", "output_leave", "parent"):
            handle.dispatcher[ev] = lambda *_a: None

    @staticmethod
    def _decode_states(states) -> set[int]:
        if states is None:
            return set()
        if isinstance(states, (bytes, bytearray, memoryview)):
            raw = bytes(states)
            count = len(raw) // 4
            return set(struct.unpack(f"<{count}I", raw[: count * 4]))
        try:
            return {int(s) for s in states}
        except TypeError:
            return set()

    def _on_readable(self, _fd, _cond) -> bool:
        try:
            self._display.read()
            self._display.dispatch(block=False)
            self._display.flush()
        except Exception as exc:  # noqa: BLE001 - compositor gone
            log.warning("foreign-toplevel connection lost: %s", exc)
            self.available = False
            self.toplevels.clear()
            self.notify()
            return False
        return True

    def _flush(self) -> None:
        try:
            self._display.flush()
        except Exception as exc:  # noqa: BLE001
            log.debug("flush failed: %s", exc)

    # -- queries -------------------------------------------------------------------
    def windows(self) -> list[Toplevel]:
        return [t for t in self.toplevels.values() if t.ready]

    def by_app(self) -> dict[str, list[Toplevel]]:
        groups: dict[str, list[Toplevel]] = {}
        for t in self.windows():
            groups.setdefault(t.app_id or "?", []).append(t)
        return groups

    def active(self) -> Toplevel | None:
        for t in self.windows():
            if t.activated:
                return t
        return None

    # -- actions ----------------------------------------------------------------------
    def activate(self, top: Toplevel) -> None:
        if self._seat is None:
            return
        try:
            if top.minimized:
                top.handle.unset_minimized()
            top.handle.activate(self._seat)
        finally:
            self._flush()

    def minimize(self, top: Toplevel) -> None:
        try:
            top.handle.set_minimized()
        finally:
            self._flush()

    def toggle_maximize(self, top: Toplevel) -> None:
        try:
            if top.maximized:
                top.handle.unset_maximized()
            else:
                top.handle.set_maximized()
        finally:
            self._flush()

    def close(self, top: Toplevel) -> None:
        try:
            top.handle.close()
        finally:
            self._flush()
