"""Sensor bar: the NodoAI I2C sensor boxes, on the right edge of the screen.

The bar is a glass column of sensor tiles, vertically centred on the right
edge of the primary monitor. It exists only while at least one box is on the
bus: it fades in when the first box connects and fades out when the last one
leaves. A tile opens a card with the box's I2C address and status (one card at
a time; the same tile closes it again). Every change posts a notification.
"""

from __future__ import annotations

import logging
import os
import time

import cairo
from gi.repository import GdkPixbuf, GLib, Gtk, GtkLayerShell

from .services.sensors import LETTERS, SensorState, describe_events
from .util import add_class, flat_button, icon_image, label, make_layer_window, primary_monitor, set_class
from .widgets import Glass

log = logging.getLogger("genesis.sensorbar")

ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "sensors")
SHADOW = 12          # transparent room around a glass surface for its drop shadow
TILE_ICON = 38
CARD_ICON = 72
CARD_WIDTH = 248
CARD_GAP = 6         # between the bar's glass and the card's glass
FADE_MS = 220

_pixbufs: dict[tuple[str, int], GdkPixbuf.Pixbuf | None] = {}


def sensor_icon_path(letter: str) -> str:
    return os.path.join(ICON_DIR, f"sensor-{letter.lower()}.svg")


def sensor_image(letter: str, size: int) -> Gtk.Image:
    key = (letter, size)
    if key not in _pixbufs:
        try:
            _pixbufs[key] = GdkPixbuf.Pixbuf.new_from_file_at_scale(sensor_icon_path(letter), size, size, True)
        except GLib.Error as exc:
            log.warning("cannot load the icon of sensor %s: %s", letter, exc)
            _pixbufs[key] = None
    pix = _pixbufs[key]
    return Gtk.Image.new_from_pixbuf(pix) if pix is not None else icon_image("genesis-sensors-symbolic", size)


def hhmm(stamp: float) -> str:
    return time.strftime("%H:%M", time.localtime(stamp)) if stamp else "—"


class SensorTile(Gtk.Button):
    def __init__(self, bar: "SensorBar", letter: str) -> None:
        super().__init__()
        self.letter = letter
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.set_can_focus(False)
        add_class(self, "sensor-tile")
        overlay = Gtk.Overlay()
        self.image = sensor_image(letter, TILE_ICON)
        overlay.add(self.image)
        self.dot = Gtk.Box()
        add_class(self.dot, "sensor-dot")
        self.dot.set_halign(Gtk.Align.END)
        self.dot.set_valign(Gtk.Align.END)
        overlay.add_overlay(self.dot)
        self.add(overlay)
        self.connect("clicked", lambda _b: bar.toggle_card(letter))

    def update(self, st: SensorState | None, selected: bool) -> None:
        on = st is not None and st.connected
        set_class(self, "connected", on)
        set_class(self, "absent", not on)
        set_class(self, "selected", selected)
        self.set_tooltip_text(f"Sensor {self.letter}  ·  I²C 0x{st.address:02X}" if on else f"Sensor {self.letter}  ·  not connected")


class SensorCard:
    """The detail card beside the bar: address, bus, status, since when."""

    def __init__(self, bar: "SensorBar") -> None:
        self.bar = bar
        self.shell = bar.shell
        self.letter: str | None = None
        self.visible = False
        self.window = make_layer_window(
            namespace="genesis-sensor-card", layer=GtkLayerShell.Layer.TOP,
            anchors=(GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.RIGHT), exclusive=False,
            keyboard=GtkLayerShell.KeyboardMode.NONE, monitor=bar.monitor,
        )
        holder = Gtk.Box()
        holder.set_margin_top(SHADOW)
        holder.set_margin_bottom(SHADOW + 4)
        holder.set_margin_start(SHADOW)
        holder.set_margin_end(SHADOW)
        self.glass = Glass(self._blur, self._origin, radius=20, orientation=Gtk.Orientation.VERTICAL, spacing=0)
        add_class(self.glass, "card", "sensor-card")
        self.glass.set_size_request(CARD_WIDTH, -1)
        holder.add(self.glass)
        self.window.add(holder)
        self.glass.connect("size-allocate", self._limit_input)

        head = Gtk.Box(spacing=8)
        self.caption = label("", css=("sensor-card-caption",), xalign=0)
        head.pack_start(self.caption, True, True, 0)
        close = flat_button(icon_image("window-close-symbolic", 14), css=("round-button", "sensor-card-close"), tooltip="Close")
        close.connect("clicked", lambda _b: self.hide())
        head.pack_end(close, False, False, 0)
        self.glass.pack_start(head, False, False, 0)

        self.icon_holder = Gtk.Box()
        self.icon_holder.set_halign(Gtk.Align.CENTER)
        add_class(self.icon_holder, "sensor-card-icon")
        self.glass.pack_start(self.icon_holder, False, False, 0)
        self.title = label("", css=("sensor-card-title",))
        self.glass.pack_start(self.title, False, False, 0)

        self.status = Gtk.Box(spacing=6)
        self.status.set_halign(Gtk.Align.CENTER)
        add_class(self.status, "sensor-status")
        self.status_dot = Gtk.Box()
        add_class(self.status_dot, "sensor-dot")
        self.status_dot.set_valign(Gtk.Align.CENTER)
        self.status_text = label("")
        self.status.pack_start(self.status_dot, False, False, 0)
        self.status.pack_start(self.status_text, False, False, 0)
        self.glass.pack_start(self.status, False, False, 0)

        grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        add_class(grid, "sensor-details")
        self.values: dict[str, Gtk.Label] = {}
        for row, (key, text) in enumerate((("address", "I²C address"), ("bus", "Bus"), ("since", "Connected at"))):
            name = label(text, css=("dim",), xalign=0)
            name.set_hexpand(True)
            value = label("", css=("sensor-value",), xalign=1)
            grid.attach(name, 0, row, 1, 1)
            grid.attach(value, 1, row, 1, 1)
            self.values[key] = value
        self.since_name = grid.get_child_at(0, 2)
        self.glass.pack_start(grid, False, False, 0)

    # -- geometry ---------------------------------------------------------------------
    def _blur(self):
        return None if self.bar.monitor is None else self.shell.wallpaper.blurred_surface(self.bar.monitor)

    def _origin(self) -> tuple[int, int]:
        geo = self.bar.monitor.get_geometry()
        return (geo.width - self._right_margin() - self.window.get_allocated_width(), self._top_margin)

    def _right_margin(self) -> int:
        # the card's glass ends CARD_GAP left of the bar's glass; both windows carry SHADOW room on each side
        return max(0, self.bar.window.get_allocated_width() + CARD_GAP - 2 * SHADOW)

    def _limit_input(self, _widget, alloc) -> None:
        try:
            self.window.input_shape_combine_region(cairo.Region(cairo.RectangleInt(alloc.x, alloc.y, alloc.width, alloc.height)))
        except (AttributeError, TypeError):
            pass

    # -- content --------------------------------------------------------------------------
    def _fill(self) -> None:
        st = self.bar.service.state(self.letter) if self.letter else None
        if st is None:
            return
        for child in self.icon_holder.get_children():
            child.destroy()
        img = sensor_image(self.letter, CARD_ICON)
        self.icon_holder.add(img)
        self.caption.set_text(f"SENSOR {self.letter}")
        self.title.set_text(f"Sensor box {self.letter}")
        on = st.connected
        set_class(self.glass, "connected", on)
        set_class(self.glass, "absent", not on)
        self.status_text.set_text("Connected" if on else "Disconnected")
        self.values["address"].set_text(f"0x{st.address:02X}" if on else "—")
        self.values["bus"].set_text(f"i2c-{self.bar.service.bus}")
        self.since_name.set_text("Connected at" if on else "Last seen")
        self.values["since"].set_text(hhmm(st.since if on else st.last_seen))
        self.glass.show_all()

    def refresh(self) -> None:
        if self.visible:
            self._fill()

    def show_for(self, letter: str, tile: Gtk.Widget | None) -> None:
        self.letter = letter
        self._fill()
        mon = self.bar.monitor
        geo = mon.get_geometry() if mon is not None else None
        self.window.get_child().show_all()
        # line the card up with its tile, kept clear of the panel and the screen edges
        _min, natural = self.window.get_preferred_height()
        top = (geo.height - natural) // 2 if geo is not None else 0
        if tile is not None and geo is not None:
            res = tile.translate_coordinates(self.bar.window, 0, 0)
            if isinstance(res, tuple) and len(res) == 3 and res[0]:
                tile_y = self.bar.top() + res[2] + tile.get_allocated_height() // 2
                top = tile_y - natural // 2
        if geo is not None:
            low = self.shell.panel_thickness() - SHADOW + 6 if self.shell.panel_position() == "top" else -SHADOW + 6
            high = geo.height - natural - (self.shell.panel_thickness() if self.shell.panel_position() == "bottom" else 0) + SHADOW - 6
            top = max(low, min(top, high))
        self._top_margin = int(top)
        GtkLayerShell.set_margin(self.window, GtkLayerShell.Edge.TOP, self._top_margin)
        GtkLayerShell.set_margin(self.window, GtkLayerShell.Edge.RIGHT, self._right_margin())
        self.window.show_all()
        self.visible = True
        self.bar.refresh_tiles()

    _top_margin = 0

    def hide(self) -> None:
        if not self.visible:
            return
        self.visible = False
        self.letter = None
        self.window.hide()
        self.bar.refresh_tiles()

    def redraw(self) -> None:
        if self.visible:
            self.glass.queue_draw()


class SensorBar:
    def __init__(self, shell) -> None:
        self.shell = shell
        self.service = shell.sensors
        self.monitor = primary_monitor()
        self.visible = False
        self._hide_source = 0
        self._nids: dict[str, int] = {}
        self.window = make_layer_window(
            namespace="genesis-sensors", layer=GtkLayerShell.Layer.TOP, anchors=(GtkLayerShell.Edge.RIGHT,),
            exclusive=False, keyboard=GtkLayerShell.KeyboardMode.NONE, monitor=self.monitor,
        )
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        self.revealer.set_transition_duration(FADE_MS)
        holder = Gtk.Box()
        holder.set_margin_top(SHADOW)
        holder.set_margin_bottom(SHADOW + 4)
        holder.set_margin_start(SHADOW)
        holder.set_margin_end(SHADOW)
        self.glass = Glass(self._blur, self._origin, radius=18, orientation=Gtk.Orientation.VERTICAL, spacing=6)
        add_class(self.glass, "sensor-bar")
        self.glass.connect("size-allocate", self._limit_input)
        self.header = icon_image("genesis-sensors-symbolic", 16)
        add_class(self.header, "sensor-bar-header")
        self.glass.pack_start(self.header, False, False, 0)
        sep = Gtk.Box()
        add_class(sep, "sensor-bar-separator")
        self.glass.pack_start(sep, False, False, 0)
        self.tiles_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.glass.pack_start(self.tiles_box, False, False, 0)
        holder.add(self.glass)
        self.revealer.add(holder)
        self.window.add(self.revealer)
        self.tiles: dict[str, SensorTile] = {}
        self.card = SensorCard(self)
        self.service.connect(self.refresh)
        self.service.on_events(self._on_events)
        shell.config.connect(self._on_config)
        self.refresh()

    # -- geometry -------------------------------------------------------------------------
    def top(self) -> int:
        geo = self.monitor.get_geometry()
        return (geo.height - self.window.get_allocated_height()) // 2

    def _origin(self) -> tuple[int, int]:
        geo = self.monitor.get_geometry()
        return (geo.width - self.window.get_allocated_width(), self.top())

    def _blur(self):
        return None if self.monitor is None else self.shell.wallpaper.blurred_surface(self.monitor)

    def _limit_input(self, _widget, alloc) -> None:
        # the shadow room around the glass must not swallow clicks meant for windows below
        try:
            self.window.input_shape_combine_region(cairo.Region(cairo.RectangleInt(alloc.x, alloc.y, alloc.width, alloc.height)))
        except (AttributeError, TypeError):
            pass

    # -- content ---------------------------------------------------------------------------
    def _on_config(self) -> None:
        if "sensors" in getattr(self.shell.config, "changed", {"sensors"}):
            self.refresh()

    def letters_shown(self) -> list[str]:
        if self.shell.config.getbool("sensors", "show_disconnected"):
            return list(LETTERS)
        return [st.letter for st in self.service.connected()]

    def refresh(self) -> None:
        letters = self.letters_shown()
        for letter in list(self.tiles):
            if letter not in letters:
                self.tiles.pop(letter).destroy()
        for position, letter in enumerate(letters):
            tile = self.tiles.get(letter)
            if tile is None:
                tile = SensorTile(self, letter)
                self.tiles[letter] = tile
                self.tiles_box.pack_start(tile, False, False, 0)
            self.tiles_box.reorder_child(tile, position)
        self.refresh_tiles()
        count = len(self.service.connected())
        self.header.set_tooltip_text(f"{count} sensor box{'es' if count != 1 else ''} on I²C bus {self.service.bus}")
        self.tiles_box.show_all()
        self._set_visible(count > 0)
        self.card.refresh()

    def refresh_tiles(self) -> None:
        for letter, tile in self.tiles.items():
            tile.update(self.service.state(letter), self.card.visible and self.card.letter == letter)

    def _set_visible(self, show: bool) -> None:
        if show and not self.visible:
            self.visible = True
            if self._hide_source:
                GLib.source_remove(self._hide_source)
                self._hide_source = 0
            self.window.show_all()
            self.revealer.set_reveal_child(True)
        elif not show and self.visible:
            self.visible = False
            self.card.hide()
            self.revealer.set_reveal_child(False)
            self._hide_source = GLib.timeout_add(FADE_MS + 60, self._finish_hide)

    def _finish_hide(self) -> bool:
        self._hide_source = 0
        if not self.visible:
            self.window.hide()
        return False

    # -- actions -----------------------------------------------------------------------------
    def toggle_card(self, letter: str) -> None:
        if self.card.visible and self.card.letter == letter:
            self.card.hide()
            return
        self.show_card(letter)

    def show_card(self, letter: str) -> bool:
        letter = letter.upper()
        if letter not in LETTERS or self.service.state(letter) is None:
            return False
        tile = self.tiles.get(letter)
        self.card.show_for(letter, tile if self.visible else None)
        return True

    def _on_events(self, connected: list, disconnected: list) -> None:
        if not self.shell.config.getbool("sensors", "notify"):
            return
        for event in describe_events(connected, disconnected, self.service.bus):
            letters = event["letters"]
            single = len(letters) == 1
            icon = sensor_icon_path(letters[0]) if single else "genesis-sensors-symbolic"
            # a box that comes and goes replaces its own previous notification instead of stacking
            replaces = self._nids.get(letters[0], 0) if single else 0
            on_action = (lambda _key, ltr=letters[0]: self.show_card(ltr)) if single and event["kind"] == "connected" else None
            nid = self.shell.notifications.post(
                "Sensors", icon, event["summary"], event["body"], ["default", "Show"] if on_action else [],
                {}, replaces_id=replaces, on_action=on_action,
            )
            for letter in letters:
                if single:
                    self._nids[letter] = nid
                else:
                    self._nids.pop(letter, None)

    def redraw(self) -> None:
        if self.visible:
            self.glass.queue_draw()
        self.card.redraw()

    def describe(self) -> dict:
        return {
            "available": self.service.available,
            "enabled": self.service.enabled,
            "bar_visible": self.visible,
            "bar_mapped": bool(self.window.get_mapped()),
            "tiles": list(self.tiles),
            "connected": {st.letter: f"0x{st.address:02X}" for st in self.service.connected()},
            "card": self.card.letter if self.card.visible else None,
            "error": self.service.error,
        }
