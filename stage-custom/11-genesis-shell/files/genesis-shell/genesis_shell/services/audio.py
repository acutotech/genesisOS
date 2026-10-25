"""Output volume via PulseAudio/PipeWire (pulsectl, falling back to pactl)."""

from __future__ import annotations

import logging
import re
import threading

from gi.repository import GLib

from . import Service
from ..util import run, run_async, which

log = logging.getLogger("genesis.audio")

STEP = 0.05


class AudioService(Service):
    def __init__(self) -> None:
        super().__init__()
        self.volume = 0.0  # 0.0 .. 1.0 (pulse allows more; we clamp)
        self.muted = False
        self.sink_name = ""
        self.sink_description = "Output"
        self.sinks: list[tuple[str, str]] = []  # (name, description)
        self._pulse = None
        self._pulsectl = None
        self._poll_source = 0
        try:
            import pulsectl  # type: ignore

            self._pulsectl = pulsectl
            self._pulse = pulsectl.Pulse("genesis-shell")
            self.available = True
            self._start_listener()
        except Exception as exc:  # noqa: BLE001 - library missing or no server (yet)
            log.info("pulsectl unavailable (%s); %s", exc, "using pactl" if which("pactl") else "audio control disabled")
            self._pulse = None
            self.available = which("pactl")
        if self.available:
            self.refresh()
        if self._pulse is None and (self.available or self._pulsectl is not None):
            # the sound server is often not up yet when the session starts
            self._poll_tries = 0
            self._poll_source = GLib.timeout_add_seconds(3, self._poll)

    # -- state -------------------------------------------------------------------
    def _poll(self) -> bool:
        if self._pulsectl is not None:
            try:
                self._pulse = self._pulsectl.Pulse("genesis-shell")
            except Exception:  # noqa: BLE001 - still no server
                self._pulse = None
            if self._pulse is not None:
                log.info("connected to the sound server; live volume updates on")
                self.available = True
                self._poll_source = 0
                self._start_listener()
                self.refresh()
                return False
        self._poll_tries += 1
        if not self.available:
            return self._poll_tries < 40  # stop asking after about two minutes
        self.refresh()
        return True

    def refresh(self) -> None:
        if self._pulse is not None:
            self._refresh_pulsectl()
        else:
            self._refresh_pactl()
        self.notify()

    def _refresh_pulsectl(self) -> None:
        try:
            sinks = self._pulse.sink_list()
            default_name = self._pulse.server_info().default_sink_name
            sink = next((s for s in sinks if s.name == default_name), sinks[0] if sinks else None)
            self.sinks = [(s.name, s.description) for s in sinks]
            if sink is None:
                self.sink_name, self.sink_description = "", "No output device"
                return
            self.sink_name = sink.name
            self.sink_description = sink.description
            self.volume = max(0.0, min(1.0, self._pulse.volume_get_all_chans(sink)))
            self.muted = bool(sink.mute)
        except Exception as exc:  # noqa: BLE001 - connection dropped
            log.debug("pulse refresh failed: %s", exc)
            self._reconnect()

    def _reconnect(self) -> None:
        try:
            self._pulse.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._pulse = self._pulsectl.Pulse("genesis-shell")
        except Exception as exc:  # noqa: BLE001
            log.debug("pulse reconnect failed: %s", exc)

    def _refresh_pactl(self) -> None:
        out = run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        m = re.search(r"(\d+)%", out)
        if m:
            self.volume = max(0.0, min(1.0, int(m.group(1)) / 100.0))
        out = run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
        self.muted = "yes" in out
        self.sink_name = run(["pactl", "get-default-sink"]).strip()
        self.sink_description = self.sink_name or "Output"

    # -- actions -----------------------------------------------------------------
    def set_volume(self, fraction: float) -> None:
        fraction = max(0.0, min(1.0, fraction))
        if self._pulse is not None:
            try:
                sink = self._default_sink()
                if sink is not None:
                    self._pulse.volume_set_all_chans(sink, fraction)
                    if self.muted and fraction > 0:
                        self._pulse.mute(sink, False)
            except Exception as exc:  # noqa: BLE001
                log.debug("set volume failed: %s", exc)
                self._reconnect()
        else:
            run_async(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{int(round(fraction * 100))}%"])
            if self.muted and fraction > 0:
                run_async(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"])
        self.volume = fraction
        if fraction > 0:
            self.muted = False
        self.notify()

    def step(self, delta: float) -> None:
        self.set_volume(round((self.volume + delta) / STEP) * STEP)

    def toggle_mute(self) -> None:
        if self._pulse is not None:
            try:
                sink = self._default_sink()
                if sink is not None:
                    self._pulse.mute(sink, not self.muted)
            except Exception as exc:  # noqa: BLE001
                log.debug("toggle mute failed: %s", exc)
                self._reconnect()
        else:
            run_async(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
        self.muted = not self.muted
        self.notify()

    def set_default_sink(self, name: str) -> None:
        if self._pulse is not None:
            try:
                self._pulse.sink_default_set(name)
            except Exception as exc:  # noqa: BLE001
                log.debug("set default sink failed: %s", exc)
        else:
            run_async(["pactl", "set-default-sink", name])
        GLib.timeout_add(300, lambda: (self.refresh(), False)[1])

    def _default_sink(self):
        sinks = self._pulse.sink_list()
        default_name = self._pulse.server_info().default_sink_name
        return next((s for s in sinks if s.name == default_name), sinks[0] if sinks else None)

    # -- presentation --------------------------------------------------------------
    def icon_name(self) -> str:
        if self.muted or self.volume <= 0.001:
            return "audio-volume-muted-symbolic"
        if self.volume < 0.34:
            return "audio-volume-low-symbolic"
        if self.volume < 0.67:
            return "audio-volume-medium-symbolic"
        return "audio-volume-high-symbolic"

    # -- change events ---------------------------------------------------------------
    def _start_listener(self) -> None:
        """Second connection that blocks on server events and wakes the main loop."""

        def worker() -> None:
            try:
                listener = self._pulsectl.Pulse("genesis-shell-events")
                listener.event_mask_set("sink", "server")
                listener.event_callback_set(lambda _ev: GLib.idle_add(self._on_event))
                listener.event_listen()
            except Exception as exc:  # noqa: BLE001
                log.info("pulse event listener stopped (%s); polling instead", exc)
                GLib.idle_add(self._enable_polling)

        threading.Thread(target=worker, name="pulse-events", daemon=True).start()

    def _enable_polling(self) -> bool:
        if not self._poll_source:
            self._poll_source = GLib.timeout_add_seconds(3, self._poll)
        return False

    def _on_event(self) -> bool:
        self.refresh()
        return False
