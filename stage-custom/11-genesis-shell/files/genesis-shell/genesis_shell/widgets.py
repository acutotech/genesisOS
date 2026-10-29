"""Reusable shell widgets: glass surfaces, full-screen overlays, tiles, rows."""

from __future__ import annotations

import logging
import os
from typing import Callable

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, GtkLayerShell

from .util import ALL_EDGES, add_class, flat_button, icon_image, label, make_layer_window, primary_monitor, rounded_rect, set_class

log = logging.getLogger("genesis.widgets")

CARD_WIDTH = 384  # control center card and toasts share one width


class Glass(Gtk.Box):
    """A box whose background is the blurred wallpaper under it, tinted by CSS.

    `origin` returns the top-left of the toplevel window relative to the monitor;
    `blur` returns a cairo surface of the blurred wallpaper for that monitor.
    """

    def __init__(self, blur: Callable, origin: Callable[[], tuple[int, int]], radius: int = 20, **kwargs) -> None:
        super().__init__(**kwargs)
        self._blur = blur
        self._origin = origin
        self.radius = radius
        self.connect("draw", self._on_draw)

    def _on_draw(self, _widget, cr) -> bool:
        try:
            surface = self._blur()
        except Exception:  # noqa: BLE001
            log.exception("blur backdrop failed")
            surface = None
        if surface is None:
            return False
        alloc = self.get_allocation()
        ox, oy = self._origin()
        # position within the toplevel (allocation is relative to the parent GdkWindow,
        # which differs from the toplevel inside Gtk.Overlay)
        wx, wy = alloc.x, alloc.y
        toplevel = self.get_toplevel()
        if toplevel is not None and toplevel is not self:
            res = self.translate_coordinates(toplevel, 0, 0)
            if isinstance(res, tuple):
                if len(res) == 3 and res[0]:
                    wx, wy = res[1], res[2]
                elif len(res) == 2:
                    wx, wy = res
        cr.save()
        rounded_rect(cr, 0, 0, alloc.width, alloc.height, self.radius)
        cr.clip()
        cr.set_source_surface(surface, -(ox + wx), -(oy + wy))
        cr.paint()
        cr.restore()
        return False  # let CSS background (the tint) and children draw on top


class OverlayWindow:
    """Full-monitor transparent layer surface hosting one card.

    Clicking outside the card or pressing Escape hides it. Subclasses build the
    card with `build()` and may override `on_shown()` / `on_hidden()` /
    `on_key(event)`.
    """

    layer = GtkLayerShell.Layer.OVERLAY
    keyboard = GtkLayerShell.KeyboardMode.EXCLUSIVE
    backdrop_css = ""
    blur_backdrop = False

    def __init__(self, shell, namespace: str) -> None:
        self.shell = shell
        self.namespace = namespace
        self.monitor: Gdk.Monitor | None = None
        self.visible = False
        self.window = make_layer_window(namespace=namespace, layer=self.layer, anchors=ALL_EDGES, exclusive=False, keyboard=self.keyboard)
        self.window.connect("key-press-event", self._on_key)
        self.window.connect("delete-event", lambda *_a: self.hide() or True)

        self.catcher = Gtk.EventBox()
        self.catcher.set_visible_window(False)
        self.catcher.set_above_child(False)
        self.catcher.connect("button-press-event", lambda *_a: self.hide() or True)
        self.window.add(self.catcher)

        self.overlay = Gtk.Overlay()
        self.catcher.add(self.overlay)

        self.backdrop = Gtk.Box()
        self.backdrop.set_hexpand(True)
        self.backdrop.set_vexpand(True)
        if self.backdrop_css:
            add_class(self.backdrop, self.backdrop_css)
        if self.blur_backdrop:
            self.backdrop.connect("draw", self._draw_blur)
        self.overlay.add(self.backdrop)

        self.card_holder = Gtk.EventBox()
        self.card_holder.set_visible_window(False)
        self.card_holder.connect("button-press-event", lambda *_a: True)  # clicks inside stay inside
        self.overlay.add_overlay(self.card_holder)
        self.card = self.build()
        self.card_holder.add(self.card)

    # -- to override -------------------------------------------------------------
    def build(self) -> Gtk.Widget:
        raise NotImplementedError

    def on_shown(self) -> None:
        pass

    def on_hidden(self) -> None:
        pass

    def on_key(self, event) -> bool:
        return False

    # -- helpers -------------------------------------------------------------------
    def place(self, halign: Gtk.Align, valign: Gtk.Align, **margins: int) -> None:
        self.card_holder.set_halign(halign)
        self.card_holder.set_valign(valign)
        for name, value in margins.items():
            getattr(self.card_holder, f"set_margin_{name}")(int(value))

    def origin(self) -> tuple[int, int]:
        return (0, 0)

    def blur(self):
        if self.monitor is None:
            return None
        return self.shell.wallpaper.blurred_surface(self.monitor)

    def _draw_blur(self, _widget, cr) -> bool:
        surface = None if self.monitor is None else self.shell.wallpaper.blurred_surface(self.monitor, deep=True)
        if surface is not None:
            cr.save()
            cr.set_source_surface(surface, 0, 0)
            cr.paint()
            cr.restore()
        return False

    def _on_key(self, _window, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return self.on_key(event)

    # -- visibility -----------------------------------------------------------------
    def show(self, monitor: Gdk.Monitor | None = None) -> None:
        if self.visible:
            return
        self.monitor = monitor or self.shell.current_monitor() or primary_monitor()
        if self.monitor is not None:
            GtkLayerShell.set_monitor(self.window, self.monitor)
        self.visible = True
        self.window.show_all()
        try:
            self.on_shown()
        except Exception:  # noqa: BLE001
            log.exception("%s on_shown failed", self.namespace)
        self.shell.on_overlay_changed()
        log.debug("%s shown (realized=%s mapped=%s)", self.namespace, self.window.get_realized(), self.window.get_mapped())
        GLib.timeout_add(600, self._log_state)

    def _log_state(self) -> bool:
        alloc = self.window.get_allocation()
        log.debug("%s after 600ms: visible=%s mapped=%s size=%dx%d", self.namespace, self.window.get_visible(), self.window.get_mapped(), alloc.width, alloc.height)
        return False

    def describe(self) -> dict:
        alloc = self.window.get_allocation()
        return {"visible": self.visible, "mapped": bool(self.window.get_mapped()), "realized": bool(self.window.get_realized()), "size": [alloc.width, alloc.height]}

    def hide(self) -> None:
        if not self.visible:
            return
        self.visible = False
        self.window.hide()
        self.on_hidden()
        self.shell.on_overlay_changed()

    def toggle(self, monitor: Gdk.Monitor | None = None) -> None:
        if self.visible:
            self.hide()
        else:
            self.show(monitor)


class Tile(Gtk.Box):
    """Quick-settings tile: icon, title, subtitle; click toggles, chevron opens details."""

    def __init__(self, icon: str, title: str, subtitle: str = "", on_toggle: Callable | None = None, on_details: Callable | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        add_class(self, "tile")
        self.set_hexpand(True)
        self.icon = icon_image(icon, 24)
        self.title = label(title, css=("tile-title",), xalign=0, ellipsize=True)
        self.subtitle = label(subtitle, css=("subtitle",), xalign=0, ellipsize=True, max_chars=18)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        texts.set_valign(Gtk.Align.CENTER)
        texts.pack_start(self.title, False, False, 0)
        texts.pack_start(self.subtitle, False, False, 0)
        inner = Gtk.Box(spacing=12)
        inner.pack_start(self.icon, False, False, 0)
        inner.pack_start(texts, True, True, 0)
        self.button = flat_button(inner, css=("tile-button",))
        self.button.set_hexpand(True)
        if on_toggle is not None:
            self.button.connect("clicked", lambda _b: on_toggle())
        self.pack_start(self.button, True, True, 0)
        if on_details is not None:
            chevron = flat_button(icon_image("go-next-symbolic", 16), css=("tile-chevron",))
            chevron.set_valign(Gtk.Align.CENTER)
            chevron.connect("clicked", lambda _b: on_details())
            self.pack_end(chevron, False, False, 0)
        self.set_subtitle(subtitle)

    def set_active(self, active: bool) -> None:
        set_class(self, "active", active)

    def set_subtitle(self, text: str) -> None:
        self.subtitle.set_text(text)
        self.subtitle.set_visible(bool(text))

    def set_icon(self, name: str) -> None:
        self.icon.set_from_icon_name(name, Gtk.IconSize.BUTTON)
        self.icon.set_pixel_size(24)


class SliderRow(Gtk.Box):
    """Icon button + horizontal scale + percentage label."""

    def __init__(self, icon: str, on_icon_clicked: Callable | None, on_value: Callable[[float], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        add_class(self, "slider-row")
        self.icon_button = flat_button(icon_image(icon, 20), css=("round-button",))
        if on_icon_clicked is not None:
            self.icon_button.connect("clicked", lambda _b: on_icon_clicked())
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.scale.set_draw_value(False)
        self.scale.set_hexpand(True)
        self.scale.set_valign(Gtk.Align.CENTER)
        self._updating = False
        self._on_value = on_value
        self.scale.connect("value-changed", self._changed)
        self.value_label = label("0%", css=("dim",))
        self.value_label.set_width_chars(4)
        self.value_label.set_xalign(1.0)
        self.pack_start(self.icon_button, False, False, 0)
        self.pack_start(self.scale, True, True, 0)
        self.pack_start(self.value_label, False, False, 0)

    def set_fraction(self, fraction: float, icon: str | None = None) -> None:
        self._updating = True
        self.scale.set_value(max(0.0, min(100.0, fraction * 100)))
        self._updating = False
        self.value_label.set_text(f"{int(round(fraction * 100))}%")
        if icon:
            img = self.icon_button.get_child()
            img.set_from_icon_name(icon, Gtk.IconSize.BUTTON)
            img.set_pixel_size(20)

    def _changed(self, scale) -> None:
        if self._updating:
            return
        self._on_value(scale.get_value() / 100.0)


class ListRow(Gtk.Button):
    """A row with icon, title, subtitle and an optional trailing widget."""

    def __init__(self, icon: str | Gtk.Widget, title: str, subtitle: str = "", trailing: Gtk.Widget | None = None) -> None:
        super().__init__()
        self.set_relief(Gtk.ReliefStyle.NONE)
        add_class(self, "list-row")
        box = Gtk.Box(spacing=12)
        self.icon = icon if isinstance(icon, Gtk.Widget) else icon_image(icon, 20)
        box.pack_start(self.icon, False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        texts.set_valign(Gtk.Align.CENTER)
        self.title = label(title, xalign=0, ellipsize=True)
        self.subtitle = label(subtitle, css=("subtitle",), xalign=0, ellipsize=True)
        self.subtitle.set_visible(bool(subtitle))
        texts.pack_start(self.title, False, False, 0)
        texts.pack_start(self.subtitle, False, False, 0)
        box.pack_start(texts, True, True, 0)
        if trailing is not None:
            trailing.set_valign(Gtk.Align.CENTER)
            box.pack_end(trailing, False, False, 0)
        self.add(box)

    def set_subtitle(self, text: str) -> None:
        self.subtitle.set_text(text)
        self.subtitle.set_visible(bool(text))


def avatar_image(size: int = 40) -> Gtk.Image:
    """~/.face if present, otherwise a generic avatar icon."""
    face = os.path.join(os.path.expanduser("~"), ".face")
    if os.path.isfile(face):
        try:
            pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(face, size, size, True)
            img = Gtk.Image.new_from_pixbuf(pix)
            add_class(img, "avatar")
            return img
        except Exception:  # noqa: BLE001
            pass
    img = icon_image("avatar-default-symbolic", size - 8)
    add_class(img, "avatar")
    return img


def empty_state(text: str, icon: str = "notifications-disabled-symbolic") -> Gtk.Box:
    """Centred placeholder for an empty list: a dim icon above a short message."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_valign(Gtk.Align.CENTER)
    box.set_vexpand(True)
    img = icon_image(icon, 28)
    img.set_opacity(0.35)
    box.pack_start(img, False, False, 0)
    box.pack_start(label(text, css=("empty-state",)), False, False, 0)
    return box


def page_header(title: str, on_back: Callable, trailing: Gtk.Widget | None = None) -> Gtk.Box:
    header = Gtk.Box(spacing=6)
    add_class(header, "card-header")
    back = flat_button(icon_image("go-previous-symbolic", 16), css=("round-button", "back-button"))
    back.connect("clicked", lambda _b: on_back())
    header.pack_start(back, False, False, 0)
    header.pack_start(label(title, css=("title",), xalign=0), True, True, 0)
    if trailing is not None:
        trailing.set_valign(Gtk.Align.CENTER)
        header.pack_end(trailing, False, False, 0)
    return header
