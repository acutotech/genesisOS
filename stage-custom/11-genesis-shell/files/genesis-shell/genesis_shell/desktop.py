"""Desktop icons: the files, folders and launchers of ~/Desktop, on the wallpaper.

Icons sit in columns from the top-left of the primary monitor, clear of the
panel. Every label is black or white, whichever has the higher contrast with
the part of the wallpaper right behind its text (WCAG relative luminance,
measured on the scaled wallpaper), with a soft shadow in the opposite tone; the
choice is made again whenever the wallpaper, the screen size or the layout
changes.

Click selects, double-click opens, right-click offers Open, Show in Files and
Move to Trash. A launcher (.desktop file) runs only when it is executable, the
same rule other desktops use so a downloaded file cannot run on a double-click;
a link to an application the system installed counts as trusted.
"""

from __future__ import annotations

import logging
import os
import shlex
from dataclasses import dataclass

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from .util import add_class, gicon_image, launch_app, set_class, spawn

log = logging.getLogger("genesis.desktop")

CELL_W, CELL_H = 104, 108
ICON = 48
EDGE = 14            # left margin and the gap below a top panel
LABEL_TOP = 6 + ICON + 6   # where the label starts inside a cell: box padding, icon, spacing
LABEL_W, LABEL_H = CELL_W - 16, 34   # the label's box before it is laid out: two lines
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


def esc(text: str) -> str:
    """Notification bodies are markup: a file called "<b>x</b>" must show as typed."""
    return GLib.markup_escape_text(text)


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


def trash_icon(full: bool) -> Gio.Icon:
    return Gio.ThemedIcon.new("user-trash-full" if full else "user-trash")


def launcher_trusted(path: str) -> bool:
    """Executable launchers run; so do links to an application the system installed."""
    if os.access(path, os.X_OK):
        return True
    real = os.path.realpath(path)
    try:
        owner = os.stat(real).st_uid
    except OSError:
        return False
    for base in GLib.get_system_data_dirs():
        apps = os.path.join(os.path.realpath(base), "applications", "")
        if real.startswith(apps) and owner == 0:
            return True
    return False


def list_items(folder: str, show_home: bool = True, show_trash: bool = True, trash_full: bool = False) -> list[DesktopItem]:
    items: list[DesktopItem] = []
    if show_home:
        items.append(DesktopItem("::home", "Home", "home", Gio.ThemedIcon.new("user-home"), GLib.get_home_dir()))
    if show_trash:
        items.append(DesktopItem("::trash", "Trash", "trash", trash_icon(trash_full)))
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
        self.cell = (0, 0)
        self.measured: tuple[int, int, int, int] | None = None   # the label box the tone was measured on
        self.label.connect("size-allocate", lambda *_a: desktop.queue_recolour())
        self.connect("button-press-event", self._pressed)

    def label_box(self, fixed: Gtk.Widget) -> tuple[int, int, int, int]:
        """Where the label's text is, in desktop coordinates (an estimate until it is laid out)."""
        x, y = self.cell
        width, height = self.label.get_allocated_width(), self.label.get_allocated_height()
        if self.label.get_mapped() and width > 1 and height > 1:
            res = self.label.translate_coordinates(fixed, 0, 0)
            if res is not None:
                return res[-2], res[-1], width, height
        return x + (CELL_W - 6 - LABEL_W) // 2, y + LABEL_TOP, LABEL_W, LABEL_H

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
        self._recolour_source = 0
        self._file_monitor: Gio.FileMonitor | None = None
        self._trash_monitor: Gio.FileMonitor | None = None
        self.trash_full = False
        self.selected_key: str | None = None
        self.laid_out_for = (0, 0)
        self.fixed.connect("size-allocate", self._on_resized)
        if self.enabled():
            try:
                os.makedirs(self.folder, exist_ok=True)
            except OSError as exc:
                log.warning("cannot create %s: %s", self.folder, exc)
        self._watch()
        self._watch_trash()
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
            self._file_monitor.connect("changed", self._on_folder_changed)
        except GLib.Error as exc:
            log.warning("cannot watch %s: %s", self.folder, exc)

    def _on_folder_changed(self, _monitor, file, _other, event) -> None:
        # files coming, going or renamed change the icons; a file being written does not
        # (a logger appending to ~/Desktop/readings.csv must not rebuild the desktop twice a second),
        # except a launcher, whose name, icon and trust live in the file
        E = Gio.FileMonitorEvent
        if event in (E.CREATED, E.DELETED, E.MOVED_IN, E.MOVED_OUT, E.RENAMED, E.MOVED):
            self._queue_rebuild()
        elif event in (E.CHANGES_DONE_HINT, E.ATTRIBUTE_CHANGED) and (file.get_basename() or "").endswith(".desktop"):
            self._queue_rebuild()

    def _watch_trash(self) -> None:
        """Follow the Trash (through gvfs) so its icon shows whether it is empty."""
        try:
            self._trash_monitor = Gio.File.new_for_uri("trash:///").monitor_directory(Gio.FileMonitorFlags.NONE, None)
            self._trash_monitor.connect("changed", lambda *_a: self._count_trash())
        except GLib.Error as exc:
            log.debug("cannot watch the Trash: %s", exc)
        self._count_trash()

    def _count_trash(self) -> None:
        try:
            Gio.File.new_for_uri("trash:///").query_info_async(
                "trash::item-count", Gio.FileQueryInfoFlags.NONE, GLib.PRIORITY_DEFAULT, None, self._trash_counted)
        except (GLib.Error, TypeError) as exc:
            log.debug("cannot count the Trash: %s", exc)

    def _trash_counted(self, file, result) -> None:
        try:
            full = file.query_info_finish(result).get_attribute_uint32("trash::item-count") > 0
        except GLib.Error:
            full = False
        if full != self.trash_full:
            self.trash_full = full
            self._queue_rebuild()

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

    def _on_resized(self, _widget, alloc) -> None:
        # a new mode or scale on the same monitor: lay the icons out again for the new size
        if self.icons and alloc.width > 1 and (alloc.width, alloc.height) != self.laid_out_for:
            self._queue_rebuild()

    def rebuild(self) -> None:
        for icon in self.icons:
            icon.destroy()
        self.icons = []
        if self.host is None or not self.enabled():
            return
        items = list_items(self.folder, self.config.getbool("desktop", "show_home"), self.config.getbool("desktop", "show_trash"),
                           self.trash_full)
        width, height = self.fixed.get_allocated_width(), self.fixed.get_allocated_height()
        if width <= 1 or height <= 1:
            geo = self.host.monitor.get_geometry()
            width, height = geo.width, geo.height
        self.laid_out_for = (width, height)
        top, bottom = self.margins()
        for item, (x, y) in zip(items, grid_positions(len(items), width, height, top, bottom)):
            icon = DesktopIcon(self, item)
            icon.cell = (x, y)
            self.fixed.put(icon, x, y)
            self.icons.append(icon)
            if item.key == self.selected_key:
                set_class(icon, "selected", True)
        if not any(icon.item.key == self.selected_key for icon in self.icons):
            self.selected_key = None
        self.fixed.show_all()
        self.recolour()

    def queue_recolour(self) -> None:
        # labels get their final size after layout: measure again then, once per batch
        if not self._recolour_source:
            self._recolour_source = GLib.idle_add(self._recolour_changed)

    def _recolour_changed(self) -> bool:
        self._recolour_source = 0
        if any(icon.measured != icon.label_box(self.fixed) for icon in self.icons):
            self.recolour()
        return False

    def recolour(self) -> None:
        """Pick black or white for every label from the wallpaper behind its text."""
        if self.host is None or not self.icons:
            return
        geo = self.host.monitor.get_geometry()   # the wallpaper is drawn at the monitor's size
        pixbuf = self.shell.wallpaper.pixbuf_for(geo.width, geo.height)
        for icon in self.icons:
            box = icon.label_box(self.fixed)
            icon.measured = box
            icon.set_tone(label_tone(region_luminance(pixbuf, *box)))

    # -- interaction ---------------------------------------------------------------------------
    def select(self, chosen: DesktopIcon | None) -> None:
        self.selected_key = chosen.item.key if chosen is not None else None
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
            if launcher_trusted(item.path):
                if not launch_app(item.app_info):
                    notify("Cannot start", esc(item.name))
            elif os.path.islink(item.path):
                notify("Launcher not trusted", esc(f"{item.name} links to a launcher that is not marked executable. "
                                                   "Copy the launcher itself to the Desktop and mark the copy executable."))
            else:
                notify("Launcher not trusted", esc(f"{item.name} is not marked executable. To allow it, run: chmod +x {shlex.quote(item.path)}"))
        else:
            display = Gdk.Display.get_default()
            ctx = display.get_app_launch_context() if display else None
            try:
                Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(item.path).get_uri(), ctx)
            except GLib.Error as exc:
                notify("Cannot open", esc(f"{item.name}: {exc.message}"))

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
            self.shell.notifications.notify_local("Cannot move to Trash", esc(f"{item.name}: {exc.message}"))
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
