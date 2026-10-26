"""Desktop icons: the files, folders and launchers of ~/Desktop, on the wallpaper.

Icons sit in columns from the top-left of the primary monitor, clear of the
panel. Every label is black or white, whichever has the higher contrast with
the part of the wallpaper right behind it (WCAG relative luminance, measured
on the scaled wallpaper), with a soft shadow in the opposite tone; the choice
is made again whenever the wallpaper or the layout changes.

Click selects, double-click opens, right-click offers Open, Show in Files and
Move to Trash. A launcher (.desktop file) runs only when it is executable, the
same rule other desktops use so a downloaded file cannot run on a double-click.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from .util import add_class, gicon_image, launch_app, set_class, spawn

log = logging.getLogger("genesis.desktop")

CELL_W, CELL_H = 104, 108
ICON = 48
EDGE = 14            # left margin and the gap below a top panel
LABEL_TOP = ICON + 10  # where the label starts inside a cell
# the luminance at which black and white text have equal contrast (1.05/(L+0.05) == (L+0.05)/0.05)
EQUAL_CONTRAST = 0.179


# -- colour --------------------------------------------------------------------------
def _linear(c: int) -> float:
    v = c / 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def relative_luminance(r: int, g: int, b: int) -> float:
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def label_tone(luminance: float) -> str:
    """"dark-text" on a light background, "light-text" on a dark one."""
    return "dark-text" if luminance > EQUAL_CONTRAST else "light-text"


def region_luminance(pixbuf: GdkPixbuf.Pixbuf | None, x: int, y: int, w: int, h: int, fallback: float = 0.01) -> float:
    """Mean relative luminance of a rectangle of `pixbuf` (sampled on a small grid)."""
    if pixbuf is None:
        return fallback
    pw, ph = pixbuf.get_width(), pixbuf.get_height()
    x0, y0 = max(0, min(x, pw - 1)), max(0, min(y, ph - 1))
    x1, y1 = max(x0 + 1, min(x + w, pw)), max(y0 + 1, min(y + h, ph))
    try:
        sub = pixbuf.new_subpixbuf(x0, y0, x1 - x0, y1 - y0)
        small = sub.scale_simple(12, 6, GdkPixbuf.InterpType.BILINEAR)
    except (GLib.Error, TypeError):
        return fallback
    if small is None:
        return fallback
    data = small.get_pixels()
    stride, n = small.get_rowstride(), small.get_n_channels()
    total, count = 0.0, 0
    for row in range(small.get_height()):
        for col in range(small.get_width()):
            i = row * stride + col * n
            total += relative_luminance(data[i], data[i + 1], data[i + 2])
            count += 1
    return total / count if count else fallback


# -- items ------------------------------------------------------------------------------
@dataclass
class DesktopItem:
    key: str
    name: str
    kind: str                 # home | trash | folder | launcher | file
    gicon: Gio.Icon | None
    path: str | None = None
    app_info: Gio.DesktopAppInfo | None = None


def desktop_dir() -> str:
    override = os.environ.get("GENESIS_DESKTOP_DIR")
    if override:
        return override
    home = GLib.get_home_dir()
    path = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP)
    if not path or os.path.realpath(path) == os.path.realpath(home):
        path = os.path.join(home, "Desktop")   # never put the whole home folder on the desktop
    return path


def trash_icon() -> Gio.Icon:
    try:
        info = Gio.File.new_for_uri("trash:///").query_info("trash::item-count", Gio.FileQueryInfoFlags.NONE, None)
        full = info.get_attribute_uint32("trash::item-count") > 0
    except (GLib.Error, TypeError):
        full = False
    return Gio.ThemedIcon.new("user-trash-full" if full else "user-trash")


def list_items(folder: str, show_home: bool = True, show_trash: bool = True) -> list[DesktopItem]:
    items: list[DesktopItem] = []
    if show_home:
        items.append(DesktopItem("::home", "Home", "home", Gio.ThemedIcon.new("user-home"), GLib.get_home_dir()))
    if show_trash:
        items.append(DesktopItem("::trash", "Trash", "trash", trash_icon()))
    entries: list[DesktopItem] = []
    try:
        enum = Gio.File.new_for_path(folder).enumerate_children(
            "standard::name,standard::display-name,standard::icon,standard::type,standard::is-hidden,standard::is-backup",
            Gio.FileQueryInfoFlags.NONE, None)
    except GLib.Error as exc:
        log.debug("cannot list %s: %s", folder, exc)
        return items
    for info in enum:
        name = info.get_name()
        if info.get_is_hidden() or info.get_is_backup() or name.startswith("."):
            continue
        path = os.path.join(folder, name)
        if info.get_file_type() == Gio.FileType.DIRECTORY:
            entries.append(DesktopItem(path, info.get_display_name(), "folder", info.get_icon(), path))
        elif name.endswith(".desktop"):
            try:
                app = Gio.DesktopAppInfo.new_from_filename(path)
            except (TypeError, GLib.Error):
                app = None
            if app is None:
                entries.append(DesktopItem(path, info.get_display_name(), "file", info.get_icon(), path))
            else:
                entries.append(DesktopItem(path, app.get_name() or name[:-8], "launcher",
                                           app.get_icon() or Gio.ThemedIcon.new("application-x-executable"), path, app))
        else:
            entries.append(DesktopItem(path, info.get_display_name(), "file", info.get_icon(), path))
    entries.sort(key=lambda it: (it.kind != "folder", it.name.casefold()))
    return items + entries


def grid_positions(count: int, width: int, height: int, top: int, bottom: int) -> list[tuple[int, int]]:
    """Cell origins, column by column from the top-left; extra icons wrap into new columns."""
    rows = max(1, (height - top - bottom) // CELL_H)
    return [(EDGE + (i // rows) * CELL_W, top + (i % rows) * CELL_H) for i in range(count)]


# -- widgets ----------------------------------------------------------------------------
class DesktopIcon(Gtk.EventBox):
    def __init__(self, desktop: "DesktopIcons", item: DesktopItem) -> None:
        super().__init__()
        self.desktop = desktop
        self.item = item
        self.set_visible_window(False)
        self.set_size_request(CELL_W - 6, CELL_H - 6)
        add_class(self, "desktop-icon")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_valign(Gtk.Align.START)
        add_class(box, "desktop-icon-box")
        image = gicon_image(item.gicon, ICON, fallback="text-x-generic")
        image.set_halign(Gtk.Align.CENTER)
        box.pack_start(image, False, False, 0)
        self.label = Gtk.Label(label=item.name)
        self.label.set_line_wrap(True)
        self.label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label.set_lines(2)
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        self.label.set_justify(Gtk.Justification.CENTER)
        self.label.set_max_width_chars(12)
        self.label.set_size_request(CELL_W - 16, -1)
        add_class(self.label, "desktop-label")
        box.pack_start(self.label, False, False, 0)
        self.add(box)
        self.set_tooltip_text(item.name)
        self.tone = ""
        self.connect("button-press-event", self._pressed)

    def set_tone(self, tone: str) -> None:
        self.tone = tone
        set_class(self.label, "dark-text", tone == "dark-text")
        set_class(self.label, "light-text", tone == "light-text")
        set_class(self, "on-light", tone == "dark-text")

    def _pressed(self, _w, event) -> bool:
        if event.type == Gdk.EventType._2BUTTON_PRESS and event.button == 1:
            self.desktop.open_item(self.item)
            return True
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return True
        self.desktop.select(self)
        if event.button == 3:
            self.desktop.context_menu(self.item, event)
        return True


class DesktopIcons:
    def __init__(self, shell) -> None:
        self.shell = shell
        self.config = shell.config
        self.folder = desktop_dir()
        self.fixed = Gtk.Fixed()
        add_class(self.fixed, "desktop-icons")
        self.icons: list[DesktopIcon] = []
        self.host = None
        self._rebuild_source = 0
        self._file_monitor: Gio.FileMonitor | None = None
        if self.enabled():
            try:
                os.makedirs(self.folder, exist_ok=True)
            except OSError as exc:
                log.warning("cannot create %s: %s", self.folder, exc)
        self._watch()
        shell.wallpaper.listeners.append(self.recolour)
        self.config.connect(self._on_config)
        display = Gdk.Display.get_default()
        if display is not None:
            display.connect("monitor-added", lambda *_a: GLib.idle_add(self._reattach))
            display.connect("monitor-removed", lambda *_a: GLib.idle_add(self._reattach))
        self._reattach()

    def enabled(self) -> bool:
        return self.config.getbool("desktop", "icons")

    # -- hosting --------------------------------------------------------------------------
    def _reattach(self) -> bool:
        win = self.shell.wallpaper.window_for_primary()
        if win is not self.host:
            parent = self.fixed.get_parent()
            if parent is not None:
                parent.remove(self.fixed)
            self.host = win
            if win is not None:
                win.overlay.add_overlay(self.fixed)
                win.overlay.set_overlay_pass_through(self.fixed, True)
                self.fixed.show_all()
        self.rebuild()
        return False

    def _watch(self) -> None:
        try:
            self._file_monitor = Gio.File.new_for_path(self.folder).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            self._file_monitor.connect("changed", lambda *_a: self._queue_rebuild())
        except GLib.Error as exc:
            log.warning("cannot watch %s: %s", self.folder, exc)

    def _queue_rebuild(self) -> None:
        if self._rebuild_source:
            GLib.source_remove(self._rebuild_source)
        self._rebuild_source = GLib.timeout_add(250, self._rebuild_now)

    def _rebuild_now(self) -> bool:
        self._rebuild_source = 0
        self.rebuild()
        return False

    def _on_config(self) -> None:
        changed = getattr(self.config, "changed", {"desktop", "panel"})
        if "desktop" in changed or "panel" in changed:
            self.rebuild()

    # -- layout ------------------------------------------------------------------------------
    def margins(self) -> tuple[int, int]:
        thickness = self.shell.panel_thickness()
        if self.shell.panel_position() == "top":
            return thickness + EDGE, EDGE
        return EDGE, thickness + EDGE

    def rebuild(self) -> None:
        for icon in self.icons:
            icon.destroy()
        self.icons = []
        if self.host is None or not self.enabled():
            return
        items = list_items(self.folder, self.config.getbool("desktop", "show_home"), self.config.getbool("desktop", "show_trash"))
        geo = self.host.monitor.get_geometry()
        top, bottom = self.margins()
        for item, (x, y) in zip(items, grid_positions(len(items), geo.width, geo.height, top, bottom)):
            icon = DesktopIcon(self, item)
            icon.cell = (x, y)
            self.fixed.put(icon, x, y)
            self.icons.append(icon)
        self.fixed.show_all()
        self.recolour()

    def recolour(self) -> None:
        """Pick black or white for every label from the wallpaper behind it."""
        if self.host is None or not self.icons:
            return
        geo = self.host.monitor.get_geometry()
        pixbuf = self.shell.wallpaper.pixbuf_for(geo.width, geo.height)
        for icon in self.icons:
            x, y = icon.cell
            lum = region_luminance(pixbuf, x, y + LABEL_TOP, CELL_W, CELL_H - LABEL_TOP)
            icon.set_tone(label_tone(lum))

    # -- interaction ---------------------------------------------------------------------------
    def select(self, chosen: DesktopIcon | None) -> None:
        for icon in self.icons:
            set_class(icon, "selected", icon is chosen)

    def clear_selection(self) -> None:
        self.select(None)

    def open_item(self, item: DesktopItem) -> None:
        files = self.config.get("shortcuts", "files") or "pcmanfm"
        notify = self.shell.notifications.notify_local
        if item.kind == "home":
            spawn([files, item.path or GLib.get_home_dir()])
        elif item.kind == "trash":
            spawn([files, "trash:///"])
        elif item.kind == "folder":
            spawn([files, item.path])
        elif item.kind == "launcher":
            if os.access(item.path, os.X_OK):
                if not launch_app(item.app_info):
                    notify("Cannot start", item.name)
            else:
                notify("Launcher not trusted", f"{item.name} is not marked executable. To allow it, run: chmod +x \"{item.path}\"")
        else:
            display = Gdk.Display.get_default()
            ctx = display.get_app_launch_context() if display else None
            try:
                Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(item.path).get_uri(), ctx)
            except GLib.Error as exc:
                notify("Cannot open", f"{item.name}: {exc.message}")

    def context_menu(self, item: DesktopItem, event) -> None:
        menu = Gtk.Menu()

        def entry(text: str, callback) -> None:
            mi = Gtk.MenuItem.new_with_label(text)
            mi.connect("activate", lambda _m: callback())
            menu.append(mi)

        entry("Open", lambda: self.open_item(item))
        if item.path and item.kind not in ("home", "trash"):
            files = self.config.get("shortcuts", "files") or "pcmanfm"
            entry("Show in Files", lambda: spawn([files, os.path.dirname(item.path)]))
            menu.append(Gtk.SeparatorMenuItem())
            entry("Move to Trash", lambda: self._trash(item))
        menu.show_all()
        menu.popup_at_pointer(event)

    def _trash(self, item: DesktopItem) -> None:
        try:
            Gio.File.new_for_path(item.path).trash(None)
        except GLib.Error as exc:
            self.shell.notifications.notify_local("Cannot move to Trash", f"{item.name}: {exc.message}")
        self._queue_rebuild()

    def describe(self) -> dict:
        tones = [icon.tone for icon in self.icons]
        return {
            "folder": self.folder,
            "icons": len(self.icons),
            "items": [icon.item.name for icon in self.icons],
            "dark_text": tones.count("dark-text"),
            "light_text": tones.count("light-text"),
        }
