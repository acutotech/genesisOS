"""The floating top panel: launcher button, dock, clock, status pill."""

from __future__ import annotations

import logging
import time

import cairo
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

from .util import add_class, brand_image, flat_button, gicon_image, icon_image, label, launch_app, make_layer_window, set_class
from .widgets import Glass

log = logging.getLogger("genesis.panel")


class DockButton(Gtk.Button):
    def __init__(self, dock: "Dock", key: str, entry, app_id: str) -> None:
        super().__init__()
        self.dock = dock
        self.key = key
        self.entry = entry
        self.app_id = app_id
        self.windows = []
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.set_can_focus(False)
        add_class(self, "panel-button", "dock-button")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_valign(Gtk.Align.CENTER)
        self.image = gicon_image(entry.icon if entry else None, 24)
        self.indicator = Gtk.Box()
        add_class(self.indicator, "dock-indicator")
        self.indicator.set_halign(Gtk.Align.CENTER)
        box.pack_start(self.image, False, False, 0)
        box.pack_start(self.indicator, False, False, 0)
        self.add(box)
        self.set_tooltip_text(entry.name if entry else app_id)
        self.connect("clicked", self._clicked)
        self.connect("button-press-event", self._pressed)

    def update(self, windows, pinned: bool) -> None:
        self.windows = windows
        self.pinned = pinned
        set_class(self, "running", bool(windows))
        set_class(self, "active", any(w.activated for w in windows))
        if windows:
            titles = [w.title for w in windows if w.title]
            self.set_tooltip_text(titles[0] if len(titles) == 1 else (self.entry.name if self.entry else self.app_id) + (f" ({len(windows)})" if len(windows) > 1 else ""))

    def _clicked(self, _button) -> None:
        tops = self.dock.shell.toplevels
        if not self.windows:
            if self.entry is not None:
                launch_app(self.entry.info)
            return
        if len(self.windows) == 1:
            win = self.windows[0]
            if win.activated and not win.minimized:
                tops.minimize(win)
            else:
                tops.activate(win)
            return
        # several windows: cycle through them
        for idx, win in enumerate(self.windows):
            if win.activated:
                tops.activate(self.windows[(idx + 1) % len(self.windows)])
                return
        tops.activate(self.windows[0])

    def _pressed(self, _button, event) -> bool:
        if event.button != 3:
            return False
        menu = Gtk.Menu()
        tops = self.dock.shell.toplevels
        for win in self.windows:
            mi = Gtk.MenuItem.new_with_label((win.title or self.app_id)[:60])
            mi.connect("activate", lambda _m, w=win: tops.activate(w))
            menu.append(mi)
        if self.windows:
            menu.append(Gtk.SeparatorMenuItem())
        if self.entry is not None:
            mi = Gtk.MenuItem.new_with_label("New Window")
            mi.connect("activate", lambda _m: launch_app(self.entry.info))
            menu.append(mi)
            mi = Gtk.MenuItem.new_with_label("Unpin from Panel" if self.pinned else "Pin to Panel")
            mi.connect("activate", lambda _m: self.dock.toggle_pin(self.entry))
            menu.append(mi)
        if self.windows:
            mi = Gtk.MenuItem.new_with_label("Close All" if len(self.windows) > 1 else "Close")
            mi.connect("activate", lambda _m: [tops.close(w) for w in list(self.windows)])
            menu.append(mi)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True


class Dock(Gtk.Box):
    def __init__(self, shell) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.shell = shell
        self.buttons: dict[str, DockButton] = {}
        self.order: list[str] = []
        self.refresh()

    def pinned_ids(self) -> list[str]:
        ids = []
        for desktop_id in self.shell.config.getlist("panel", "launchers"):
            if not desktop_id.endswith(".desktop"):
                desktop_id += ".desktop"
            ids.append(desktop_id)
        return ids

    def toggle_pin(self, entry) -> None:
        ids = self.pinned_ids()
        if entry.id in ids:
            ids.remove(entry.id)
        else:
            ids.append(entry.id)
        self.shell.config.set("panel", "launchers", " ".join(i[:-8] if i.endswith(".desktop") else i for i in ids))
        try:
            self.shell.config.save()
        except OSError as exc:
            log.warning("cannot save pins: %s", exc)
        self.refresh()

    def refresh(self) -> None:
        apps = self.shell.apps
        tops = self.shell.toplevels
        groups = tops.by_app() if tops.available else {}
        wanted: list[tuple[str, object, str, list]] = []  # key, entry, app_id, windows
        used_app_ids: set[str] = set()
        pinned = self.pinned_ids()
        for desktop_id in pinned:
            entry = apps.by_desktop_id(desktop_id)
            if entry is None:
                continue
            windows = []
            for app_id, wins in groups.items():
                match = apps.for_app_id(app_id)
                if match is not None and match.id == entry.id:
                    windows.extend(wins)
                    used_app_ids.add(app_id)
            wanted.append((entry.id, entry, entry.stem, windows))
        for app_id, wins in groups.items():
            if app_id in used_app_ids:
                continue
            entry = apps.for_app_id(app_id)
            key = entry.id if entry is not None else f"appid:{app_id}"
            existing = next((w for w in wanted if w[0] == key), None)
            if existing is not None:
                existing[3].extend(wins)
                continue
            wanted.append((key, entry, app_id, wins))

        keys = [w[0] for w in wanted]
        for key in list(self.buttons):
            if key not in keys:
                self.buttons.pop(key).destroy()
        for position, (key, entry, app_id, windows) in enumerate(wanted):
            btn = self.buttons.get(key)
            if btn is None:
                btn = DockButton(self, key, entry, app_id)
                self.buttons[key] = btn
                self.pack_start(btn, False, False, 0)
            self.reorder_child(btn, position)
            btn.update(windows, key in pinned)
        self.show_all()


class Panel:
    def __init__(self, shell, monitor: Gdk.Monitor) -> None:
        self.shell = shell
        self.monitor = monitor
        cfg = shell.config
        self.height = max(32, cfg.getint("panel", "height"))
        self.floating = cfg.getbool("panel", "floating")
        self.position = "bottom" if cfg.get("panel", "position").lower() == "bottom" else "top"
        side = GtkLayerShell.Edge.BOTTOM if self.position == "bottom" else GtkLayerShell.Edge.TOP
        self.margin = 8 if self.floating else 0
        self.side_margin = 12 if self.floating else 0
        margins = {side: self.margin, GtkLayerShell.Edge.LEFT: self.side_margin, GtkLayerShell.Edge.RIGHT: self.side_margin}
        # A floating bar gets transparent room for its drop shadow; the exclusive
        # zone stays the bar's own height and input is limited to the bar, so the
        # shadow strip never covers or eats clicks meant for windows below it.
        self.shadow_room = 14 if self.floating else 0
        self.window = make_layer_window(
            namespace="genesis-panel", layer=GtkLayerShell.Layer.TOP,
            anchors=(side, GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT),
            margins=margins, exclusive=self.height if self.floating else True,
            keyboard=GtkLayerShell.KeyboardMode.NONE, monitor=monitor,
        )
        self.box = Glass(self._blur, self._origin, radius=16 if self.floating else 0, orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        add_class(self.box, "panel")
        if not self.floating:
            add_class(self.box, "docked")
        self.box.set_size_request(-1, self.height)
        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        if self.shadow_room:
            if self.position == "bottom":
                holder.set_margin_top(self.shadow_room)
            else:
                holder.set_margin_bottom(self.shadow_room)
        holder.pack_start(self.box, False, False, 0)
        self.window.add(holder)
        self.box.connect("size-allocate", self._limit_input)

        # left: logo + dock
        left = Gtk.Box(spacing=2)
        left.set_valign(Gtk.Align.CENTER)
        logo = flat_button(brand_image("genesis-os", 26), css=("panel-button", "logo-button"), tooltip="Applications  (Super)")
        logo.connect("clicked", lambda _b: shell.toggle_launcher(monitor))
        left.pack_start(logo, False, False, 0)
        sep = Gtk.Box()
        add_class(sep, "panel-separator")
        sep.set_valign(Gtk.Align.CENTER)
        sep.set_size_request(1, 20)
        left.pack_start(sep, False, False, 0)
        self.dock = Dock(shell)
        left.pack_start(self.dock, False, False, 0)
        self.box.pack_start(left, False, False, 0)

        # center: clock
        self.clock_label = label("", css=("clock",))
        self.clock_button = flat_button(self.clock_label, css=("panel-button",), tooltip="Calendar and notifications")
        self.clock_button.set_valign(Gtk.Align.CENTER)
        self.clock_button.connect("clicked", lambda _b: shell.toggle_notification_center(monitor))
        self.box.set_center_widget(self.clock_button)

        # right: status pill
        icons = Gtk.Box(spacing=2)
        self.badge = label("", css=("notification-badge",))
        self.badge.set_valign(Gtk.Align.CENTER)
        self.badge.set_no_show_all(True)
        self.net_icon = icon_image("network-wireless-symbolic", 16)
        self.net_icon.set_no_show_all(True)
        self.bt_icon = icon_image("bluetooth-active-symbolic", 16)
        self.bt_icon.set_no_show_all(True)
        self.vol_icon = icon_image("audio-volume-high-symbolic", 16)
        self.bat_icon = icon_image("battery-symbolic", 16)
        self.bat_icon.set_no_show_all(True)
        self.menu_icon = icon_image("genesis-control-center-symbolic", 16)  # always shown: the pill opens the control center
        for w in (self.badge, self.net_icon, self.bt_icon, self.vol_icon, self.bat_icon, self.menu_icon):
            icons.pack_start(w, False, False, 0)
        self.status_button = flat_button(icons, css=("panel-button", "status-pill"), tooltip="Control center")
        self.status_button.connect("clicked", lambda _b: shell.toggle_control_center(monitor))
        right = Gtk.Box(spacing=2)
        right.set_valign(Gtk.Align.CENTER)
        right.pack_end(self.status_button, False, False, 0)
        self.box.pack_end(right, False, False, 0)

        self.window.show_all()
        self._last_clock = ""
        self._clock_source = 0
        self._tick()
        self.update_status()

    # -- geometry for the glass backdrop ------------------------------------------------
    def _origin(self) -> tuple[int, int]:
        geo = self.monitor.get_geometry()
        y = self.margin if self.position == "top" else geo.height - self.margin - self.window.get_allocated_height()
        return (self.side_margin, y)

    def _limit_input(self, _widget, alloc) -> None:
        try:
            region = cairo.Region(cairo.RectangleInt(alloc.x, alloc.y, alloc.width, alloc.height))
            self.window.input_shape_combine_region(region)
        except (AttributeError, TypeError) as exc:  # pycairo without Region support
            log.debug("cannot limit panel input region: %s", exc)

    def _blur(self):
        return self.shell.wallpaper.blurred_surface(self.monitor)

    def bottom(self) -> int:
        """Pixels from the top edge of the monitor to just below the panel (0 if at bottom)."""
        return self.margin + self.height if self.position == "top" else 0

    # -- updates ------------------------------------------------------------------------
    def _tick(self) -> bool:
        fmt = self.shell.config.get("panel", "clock_format") or "%a %d %b   %H:%M"
        if self.shell.config.getbool("panel", "show_seconds") and "%S" not in fmt:
            fmt += ":%S"
        text = time.strftime(fmt)
        if text != self._last_clock:
            self._last_clock = text
            self.clock_label.set_text(text)
        # next wake-up: the next second when seconds are shown, otherwise the next minute
        if "%S" in fmt or "%T" in fmt or "%r" in fmt:
            delay_ms = 1000 - int(time.time() * 1000) % 1000
        else:
            delay_ms = 60000 - int(time.time() * 1000) % 60000
        self._clock_source = GLib.timeout_add(delay_ms + 20, self._tick)
        return False

    def update_status(self) -> None:
        s = self.shell
        self.net_icon.set_visible(s.network.available)
        self.net_icon.set_from_icon_name(s.network.icon_name(), Gtk.IconSize.BUTTON)
        self.net_icon.set_pixel_size(16)
        self.net_icon.set_tooltip_text(s.network.status_text())
        show_bt = s.bluetooth.available and s.bluetooth.powered
        self.bt_icon.set_visible(show_bt)
        if show_bt:
            self.bt_icon.set_from_icon_name("bluetooth-active-symbolic", Gtk.IconSize.BUTTON)
            self.bt_icon.set_pixel_size(16)
        self.vol_icon.set_visible(s.audio.available)
        self.vol_icon.set_from_icon_name(s.audio.icon_name(), Gtk.IconSize.BUTTON)
        self.vol_icon.set_pixel_size(16)
        self.bat_icon.set_visible(s.battery.available)
        if s.battery.available:
            self.bat_icon.set_from_icon_name(s.battery.icon, Gtk.IconSize.BUTTON)
            self.bat_icon.set_pixel_size(16)
            self.bat_icon.set_tooltip_text(s.battery.status_text())
        unread = s.notifications.unread_count()
        self.badge.set_visible(unread > 0)
        self.badge.set_text(str(unread) if unread < 100 else "99+")
        # never an empty pill: fall back to a menu glyph when no status icon is shown

    def refresh_dock(self) -> None:
        self.dock.refresh()

    def redraw(self) -> None:
        self.box.queue_draw()

    def destroy(self) -> None:
        if self._clock_source:
            GLib.source_remove(self._clock_source)
            self._clock_source = 0
        self.window.destroy()
