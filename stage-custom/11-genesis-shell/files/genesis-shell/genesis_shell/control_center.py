"""Control center: quick toggles, sliders, Wi-Fi / Bluetooth / sound pages."""

from __future__ import annotations

import logging
import socket

from gi.repository import GLib, Gtk, GtkLayerShell

from .util import add_class, flat_button, icon_button, icon_image, label, spawn, user_display_name, which
from .widgets import CARD_WIDTH, Glass, ListRow, OverlayWindow, SliderRow, Tile, avatar_image, empty_state, page_header

log = logging.getLogger("genesis.control")


class ControlCenter(OverlayWindow):
    layer = GtkLayerShell.Layer.OVERLAY
    keyboard = GtkLayerShell.KeyboardMode.ON_DEMAND

    def __init__(self, shell) -> None:
        self._night = False
        self._wifi_rows: list = []
        self._password_row = None
        super().__init__(shell, "genesis-control-center")
        self._place_near_panel()
        for svc in (shell.audio, shell.network, shell.bluetooth, shell.battery, shell.brightness):
            svc.connect(self.refresh)
        shell.notifications.connect(self.refresh)
        self.refresh()

    def _place_near_panel(self) -> None:
        if self.shell.panel_position() == "bottom":
            self.place(Gtk.Align.END, Gtk.Align.END, bottom=self.shell.panel_thickness() + 10, end=12)
        else:
            self.place(Gtk.Align.END, Gtk.Align.START, top=self.shell.panel_thickness() + 10, end=12)

    # -- construction ------------------------------------------------------------------
    def build(self) -> Gtk.Widget:
        self.glass = Glass(self.blur, self.origin, radius=20, orientation=Gtk.Orientation.VERTICAL)
        add_class(self.glass, "card")
        self.glass.set_size_request(CARD_WIDTH, -1)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(160)
        self.stack.set_homogeneous(False)
        self.stack.add_named(self._build_main(), "main")
        self.stack.add_named(self._build_wifi(), "wifi")
        self.stack.add_named(self._build_bluetooth(), "bluetooth")
        self.stack.add_named(self._build_audio(), "audio")
        self.glass.pack_start(self.stack, False, False, 0)
        return self.glass

    def _build_main(self) -> Gtk.Widget:
        s = self.shell
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        header = Gtk.Box(spacing=12)
        add_class(header, "card-header")
        header.pack_start(avatar_image(40), False, False, 0)
        who = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        who.set_valign(Gtk.Align.CENTER)
        who.pack_start(label(user_display_name(), css=("title",), xalign=0, ellipsize=True), False, False, 0)
        who.pack_start(label(socket.gethostname(), css=("subtitle",), xalign=0, ellipsize=True), False, False, 0)
        header.pack_start(who, True, True, 0)
        for icon, tip, cb, extra in (  # pack_end reverses: Settings … Lock, Power with Power rightmost
            ("system-shutdown-symbolic", "Power", s.show_power, ("danger",)),
            ("system-lock-screen-symbolic", "Lock screen", s.lock, ()),
            ("preferences-system-symbolic", "Settings", lambda: s.open_settings(), ()),
        ):
            btn = icon_button(icon, 16, css=("round-button",) + extra, tooltip=tip)
            btn.connect("clicked", lambda _b, c=cb: (self.hide(), c()))
            header.pack_end(btn, False, False, 0)
        page.pack_start(header, False, False, 0)

        self.volume = SliderRow(s.audio.icon_name(), s.audio.toggle_mute, s.audio.set_volume)
        page.pack_start(self.volume, False, False, 0)
        self.sink_button = flat_button(None, css=("link-button",))
        sink_box = Gtk.Box(spacing=6)
        self.sink_label = label("", css=("subtitle",), xalign=0, ellipsize=True, max_chars=34)
        sink_box.pack_start(self.sink_label, True, True, 0)
        sink_box.pack_end(icon_image("go-next-symbolic", 16), False, False, 0)
        self.sink_button.add(sink_box)
        self.sink_button.connect("clicked", lambda _b: self._show_page("audio"))
        self.sink_button.set_halign(Gtk.Align.FILL)
        page.pack_start(self.sink_button, False, False, 0)

        self.brightness = SliderRow("display-brightness-symbolic", None, s.brightness.set_level)
        page.pack_start(self.brightness, False, False, 0)

        grid = Gtk.Grid()
        grid.set_row_spacing(8)
        grid.set_column_spacing(8)
        grid.set_column_homogeneous(True)
        self.wifi_tile = Tile("network-wireless-symbolic", "Wi-Fi", "", self._toggle_wifi, lambda: self._show_page("wifi"))
        self.bt_tile = Tile("bluetooth-active-symbolic", "Bluetooth", "", self._toggle_bluetooth, lambda: self._show_page("bluetooth"))
        theme = Gtk.IconTheme.get_default()
        dnd_off = "preferences-system-notifications-symbolic" if theme.has_icon("preferences-system-notifications-symbolic") else "notifications-disabled-symbolic"
        self._dnd_icons = (dnd_off, "notifications-disabled-symbolic")
        self.dnd_tile = Tile(dnd_off, "Do Not Disturb", "", self._toggle_dnd)
        self.night_tile = Tile("weather-clear-night-symbolic", "Night Light", "", self._toggle_night)
        self.shot_tile = Tile("camera-photo-symbolic", "Screenshot", "Whole screen", lambda: (self.hide(), s.screenshot("screen")))
        self.display_tile = Tile("video-display-symbolic", "Displays", "Arrange screens", lambda: (self.hide(), spawn([s.config.get("shortcuts", "display_settings")])))
        tiles = [self.wifi_tile, self.bt_tile, self.dnd_tile]
        tiles.append(self.night_tile if which("wlsunset") else self.shot_tile)
        if which(s.config.get("shortcuts", "display_settings")):
            tiles.append(self.display_tile)
            if self.night_tile in tiles:
                tiles.append(self.shot_tile)
        for idx, tile in enumerate(tiles):
            grid.attach(tile, idx % 2, idx // 2, 1, 1)
        page.pack_start(grid, False, False, 0)
        return page

    def _build_wifi(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.wifi_switch = Gtk.Switch()
        self.wifi_switch.connect("state-set", self._on_wifi_switch)
        page.pack_start(page_header("Wi-Fi", lambda: self._show_page("main"), self.wifi_switch), False, False, 0)
        self.wifi_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_max_content_height(380)
        scrolled.add(self.wifi_list)
        page.pack_start(scrolled, True, True, 0)
        self.wifi_status = label("", css=("subtitle",), xalign=0)
        self.wifi_status.set_line_wrap(True)
        page.pack_start(self.wifi_status, False, False, 0)
        if which("nm-connection-editor") or which("lp-connection-editor"):
            more = flat_button(label("Advanced network settings…", xalign=0), css=("link-button",))
            tool = "lp-connection-editor" if which("lp-connection-editor") else "nm-connection-editor"
            more.connect("clicked", lambda _b: (self.hide(), spawn([tool])))
            page.pack_end(more, False, False, 0)
        return page

    def _build_bluetooth(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.bt_switch = Gtk.Switch()
        self.bt_switch.connect("state-set", self._on_bt_switch)
        page.pack_start(page_header("Bluetooth", lambda: self._show_page("main"), self.bt_switch), False, False, 0)
        self.bt_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_max_content_height(380)
        scrolled.add(self.bt_list)
        page.pack_start(scrolled, True, True, 0)
        self.bt_status = label("", css=("subtitle",), xalign=0)
        page.pack_start(self.bt_status, False, False, 0)
        manager = next((t for t in ("blueman-manager", "bluetooth-wizard") if which(t)), None)
        if manager:
            more = flat_button(label("Pair a new device…", xalign=0), css=("link-button",))
            more.connect("clicked", lambda _b: (self.hide(), spawn([manager])))
            page.pack_end(more, False, False, 0)
        return page

    def _build_audio(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.pack_start(page_header("Sound output", lambda: self._show_page("main")), False, False, 0)
        self.audio_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        page.pack_start(self.audio_list, True, True, 0)
        if which("pavucontrol"):
            more = flat_button(label("Sound settings…", xalign=0), css=("link-button",))
            more.connect("clicked", lambda _b: (self.hide(), spawn(["pavucontrol"])))
            page.pack_end(more, False, False, 0)
        return page

    # -- navigation -----------------------------------------------------------------
    def _show_page(self, name: str) -> None:
        self.stack.set_visible_child_name(name)
        if name == "wifi":
            self.shell.network.scan()
            self._fill_wifi()
        elif name == "bluetooth":
            self._fill_bluetooth()
        elif name == "audio":
            self._fill_audio()

    def on_shown(self) -> None:
        self._place_near_panel()
        self.stack.set_visible_child_name("main")
        self.refresh()

    # -- actions --------------------------------------------------------------------
    def _sync_switch(self, switch: Gtk.Switch, state: bool) -> None:
        """Reflect service state without triggering the state-set handler."""
        self._syncing = True
        try:
            switch.set_state(bool(state))
            switch.set_active(bool(state))
        finally:
            self._syncing = False

    def _on_wifi_switch(self, _switch, state) -> bool:
        if not getattr(self, "_syncing", False):
            self.shell.network.set_wifi_enabled(bool(state))
        return False

    def _on_bt_switch(self, _switch, state) -> bool:
        if not getattr(self, "_syncing", False):
            self.shell.bluetooth.set_powered(bool(state))
        return False

    def _toggle_wifi(self) -> None:
        self.shell.network.set_wifi_enabled(not self.shell.network.wifi_enabled)

    def _toggle_bluetooth(self) -> None:
        self.shell.bluetooth.set_powered(not self.shell.bluetooth.powered)

    def _toggle_dnd(self) -> None:
        n = self.shell.notifications
        n.set_dnd(not n.dnd)

    def _toggle_night(self) -> None:
        if self._night:
            spawn(["pkill", "-x", "wlsunset"])
        else:
            spawn(["wlsunset", "-t", "3600", "-T", "6500"])
        self._night = not self._night
        self.refresh()

    # -- refresh --------------------------------------------------------------------
    def refresh(self) -> None:
        s = self.shell
        self.volume.set_visible(s.audio.available)
        self.sink_button.set_visible(s.audio.available)
        if s.audio.available:
            self.volume.set_fraction(0.0 if s.audio.muted else s.audio.volume, s.audio.icon_name())
            self.sink_label.set_text(s.audio.sink_description)
        if s.brightness.available:
            self.brightness.set_visible(True)
            self.brightness.show_all()
            self.brightness.set_fraction(s.brightness.level)
        else:
            self.brightness.set_visible(False)

        self.wifi_tile.set_visible(s.network.available)
        self.wifi_tile.set_active(s.network.wifi_enabled)
        self.wifi_tile.set_subtitle(s.network.status_text())
        self.wifi_tile.set_icon(s.network.icon_name() if s.network.wifi_enabled else "network-wireless-disabled-symbolic")

        self.bt_tile.set_visible(s.bluetooth.available)
        self.bt_tile.set_active(s.bluetooth.powered)
        self.bt_tile.set_subtitle(s.bluetooth.status_text())
        self.bt_tile.set_icon(s.bluetooth.icon_name())

        self.dnd_tile.set_active(s.notifications.dnd)
        self.dnd_tile.set_subtitle("On" if s.notifications.dnd else "Off")
        self.dnd_tile.set_icon(self._dnd_icons[1] if s.notifications.dnd else self._dnd_icons[0])
        self.night_tile.set_active(self._night)
        self.night_tile.set_subtitle("On" if self._night else "Off")

        if self.visible:
            name = self.stack.get_visible_child_name()
            if name == "wifi":
                self._fill_wifi()
            elif name == "bluetooth":
                self._fill_bluetooth()
            elif name == "audio":
                self._fill_audio()

    @staticmethod
    def _clear(box: Gtk.Box) -> None:
        for child in box.get_children():
            box.remove(child)

    def _fill_wifi(self) -> None:
        net = self.shell.network
        self._sync_switch(self.wifi_switch, net.wifi_enabled)
        self._clear(self.wifi_list)
        self._password_row = None
        if not net.wifi_enabled:
            self.wifi_list.pack_start(empty_state("Wi-Fi is off", "network-wireless-disabled-symbolic"), True, True, 0)
        else:
            aps = net.access_points()
            if not aps:
                self.wifi_list.pack_start(empty_state("Searching for networks…", "network-wireless-symbolic"), True, True, 0)
            for ap in aps:
                trailing = icon_image("object-select-symbolic", 16) if ap["active"] else (icon_image("channel-secure-symbolic", 14) if ap["secure"] else None)
                row = ListRow(net.strength_icon(ap["strength"]), ap["ssid"], "Connected" if ap["active"] else ("Saved" if ap["known"] else ("Secured" if ap["secure"] else "Open")), trailing)
                if ap["active"]:
                    add_class(row, "selected")
                row.connect("clicked", self._wifi_row_clicked, ap)
                self.wifi_list.pack_start(row, False, False, 0)
        self.wifi_list.show_all()

    def _wifi_row_clicked(self, row, ap) -> None:
        net = self.shell.network
        if ap["active"]:
            self.wifi_status.set_text(f"Disconnecting from {ap['ssid']}…")
            net.disconnect_wifi(lambda ok, out: self._wifi_done(ok, out, "Disconnected"))
            return
        if ap["secure"] and not ap["known"]:
            self._ask_password(row, ap)
            return
        self.wifi_status.set_text(f"Connecting to {ap['ssid']}…")
        net.connect_ssid(ap["ssid"], None, lambda ok, out: self._wifi_done(ok, out, f"Connected to {ap['ssid']}"))

    def _ask_password(self, row, ap) -> None:
        if self._password_row is not None:
            self._password_row.destroy()
        entry = Gtk.Entry()
        add_class(entry, "inline-entry")
        entry.set_visibility(False)
        entry.set_placeholder_text(f"Password for {ap['ssid']}")
        entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "go-next-symbolic")
        entry.set_icon_activatable(Gtk.EntryIconPosition.SECONDARY, True)

        def go(*_a):
            pw = entry.get_text()
            if len(pw) < 8 and pw:
                self.wifi_status.set_text("Wi-Fi passwords have at least 8 characters.")
                return
            self.wifi_status.set_text(f"Connecting to {ap['ssid']}…")
            self.shell.network.connect_ssid(ap["ssid"], pw or None, lambda ok, out: self._wifi_done(ok, out, f"Connected to {ap['ssid']}"))
            entry.set_sensitive(False)

        entry.connect("activate", go)
        entry.connect("icon-press", go)
        self._password_row = entry
        children = self.wifi_list.get_children()
        position = children.index(row) + 1 if row in children else len(children)
        self.wifi_list.pack_start(entry, False, False, 0)
        self.wifi_list.reorder_child(entry, position)
        entry.show()
        entry.grab_focus()

    def _wifi_done(self, ok: bool, output: str, success: str) -> None:
        if ok:
            self.wifi_status.set_text(success)
        else:
            msg = output.strip().splitlines()[-1] if output.strip() else "failed"
            self.wifi_status.set_text(f"Could not connect: {msg[:120]}")
        GLib.timeout_add(1200, lambda: (self._fill_wifi(), False)[1])

    def _fill_bluetooth(self) -> None:
        bt = self.shell.bluetooth
        self._sync_switch(self.bt_switch, bt.powered)
        self._clear(self.bt_list)
        if not bt.powered:
            self.bt_list.pack_start(empty_state("Bluetooth is off", "bluetooth-disabled-symbolic"), True, True, 0)
        elif not bt.devices:
            self.bt_list.pack_start(empty_state("No paired devices", "bluetooth-active-symbolic"), True, True, 0)
        for dev in bt.devices:
            sub = "Connected" if dev["connected"] else "Paired"
            if dev.get("battery") is not None:
                sub += f"  ·  {dev['battery']}%"
            row = ListRow(dev["icon"] if dev["icon"].endswith("-symbolic") else dev["icon"] + "-symbolic", dev["name"], sub,
                          icon_image("object-select-symbolic", 16) if dev["connected"] else None)
            if dev["connected"]:
                add_class(row, "selected")
            row.connect("clicked", lambda _r, d=dev: self._bt_row_clicked(d))
            self.bt_list.pack_start(row, False, False, 0)
        self.bt_list.show_all()

    def _bt_row_clicked(self, dev) -> None:
        verb = "Disconnecting" if dev["connected"] else "Connecting"
        self.bt_status.set_text(f"{verb} {dev['name']}…")

        def done(ok):
            self.bt_status.set_text("" if ok else f"Could not {verb.lower()[:-3]} {dev['name']}")

        self.shell.bluetooth.set_connected(dev["path"], not dev["connected"], done)

    def _fill_audio(self) -> None:
        audio = self.shell.audio
        self._clear(self.audio_list)
        if not audio.sinks:
            self.audio_list.pack_start(empty_state("No output devices", "audio-speakers-symbolic"), True, True, 0)
        for name, desc in audio.sinks:
            current = name == audio.sink_name
            row = ListRow("audio-speakers-symbolic" if "hdmi" not in name.lower() else "video-display-symbolic", desc, "", icon_image("object-select-symbolic", 16) if current else None)
            if current:
                add_class(row, "selected")
            row.connect("clicked", lambda _r, n=name: (audio.set_default_sink(n), GLib.timeout_add(400, lambda: (self._fill_audio(), False)[1])))
            self.audio_list.pack_start(row, False, False, 0)
        self.audio_list.show_all()
