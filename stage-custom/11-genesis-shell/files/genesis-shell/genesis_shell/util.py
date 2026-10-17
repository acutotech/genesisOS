"""Small helpers shared by the shell components."""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
from typing import Iterable, Sequence

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gdk, Gio, GLib, Gtk, GtkLayerShell, Pango  # noqa: E402

log = logging.getLogger("genesis.util")


# --- layer-shell windows ----------------------------------------------------------
def make_layer_window(
    *,
    namespace: str,
    layer: GtkLayerShell.Layer,
    anchors: Iterable[GtkLayerShell.Edge] = (),
    margins: dict | None = None,
    exclusive: bool | int = False,
    keyboard: GtkLayerShell.KeyboardMode = GtkLayerShell.KeyboardMode.NONE,
    monitor: Gdk.Monitor | None = None,
) -> Gtk.Window:
    """A transparent, undecorated GTK window placed with wlr-layer-shell."""
    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_decorated(False)
    # The window must stay resizable: gtk-layer-shell sizes anchored windows by
    # forcing geometry hints, and GTK computes the hints of a non-resizable
    # window from its guessed default size. For height-for-width content (flow
    # boxes, wrapping labels) that guess is 200x1 and the compositor's size is
    # never applied, so the window maps as a one-pixel strip.
    win.set_app_paintable(True)
    visual = win.get_screen().get_rgba_visual()
    if visual is not None:
        win.set_visual(visual)
    win.get_style_context().add_class("genesis")
    GtkLayerShell.init_for_window(win)
    GtkLayerShell.set_namespace(win, namespace)
    GtkLayerShell.set_layer(win, layer)
    for edge in anchors:
        GtkLayerShell.set_anchor(win, edge, True)
    for edge, value in (margins or {}).items():
        GtkLayerShell.set_margin(win, edge, int(value))
    if exclusive is True:
        GtkLayerShell.auto_exclusive_zone_enable(win)
    elif exclusive is False:
        GtkLayerShell.set_exclusive_zone(win, -1)
    else:
        GtkLayerShell.set_exclusive_zone(win, int(exclusive))
    GtkLayerShell.set_keyboard_mode(win, keyboard)
    if monitor is not None:
        GtkLayerShell.set_monitor(win, monitor)
    return win


ALL_EDGES = (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT)


def primary_monitor() -> Gdk.Monitor | None:
    display = Gdk.Display.get_default()
    if display is None:
        return None
    mon = display.get_primary_monitor()
    if mon is None and display.get_n_monitors() > 0:
        mon = display.get_monitor(0)
    return mon


def monitors() -> list[Gdk.Monitor]:
    display = Gdk.Display.get_default()
    if display is None:
        return []
    return [display.get_monitor(i) for i in range(display.get_n_monitors())]


# --- drawing --------------------------------------------------------------------
def rounded_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    r = max(0.0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def parse_hex(color: str, fallback: str = "#4f8cff") -> tuple[float, float, float]:
    rgba = Gdk.RGBA()
    if not rgba.parse(color):
        rgba.parse(fallback)
    return rgba.red, rgba.green, rgba.blue


def lighten(color: str, amount: float) -> str:
    r, g, b = parse_hex(color)
    r, g, b = (min(1.0, c + (1.0 - c) * amount) for c in (r, g, b))
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


# --- widgets --------------------------------------------------------------------
def icon_image(name: str, size: int) -> Gtk.Image:
    img = Gtk.Image.new_from_icon_name(name, Gtk.IconSize.BUTTON)
    img.set_pixel_size(size)
    return img


def gicon_image(gicon: Gio.Icon | None, size: int, fallback: str = "application-x-executable") -> Gtk.Image:
    if gicon is None:
        return icon_image(fallback, size)
    img = Gtk.Image.new_from_gicon(gicon, Gtk.IconSize.BUTTON)
    img.set_pixel_size(size)
    return img


def label(text: str = "", *, css: Sequence[str] = (), xalign: float | None = None, ellipsize: bool = False, max_chars: int = 0) -> Gtk.Label:
    lbl = Gtk.Label(label=text)
    for cls in css:
        lbl.get_style_context().add_class(cls)
    if xalign is not None:
        lbl.set_xalign(xalign)
    if ellipsize:
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
    if max_chars:
        lbl.set_max_width_chars(max_chars)
    return lbl


def flat_button(child: Gtk.Widget | None = None, *, css: Sequence[str] = (), tooltip: str = "") -> Gtk.Button:
    btn = Gtk.Button()
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.set_can_focus(False)
    if child is not None:
        btn.add(child)
    for cls in css:
        btn.get_style_context().add_class(cls)
    if tooltip:
        btn.set_tooltip_text(tooltip)
    return btn


def icon_button(name: str, size: int = 18, *, css: Sequence[str] = ("round-button",), tooltip: str = "") -> Gtk.Button:
    return flat_button(icon_image(name, size), css=css, tooltip=tooltip)


def add_class(widget: Gtk.Widget, *classes: str) -> Gtk.Widget:
    ctx = widget.get_style_context()
    for cls in classes:
        ctx.add_class(cls)
    return widget


def set_class(widget: Gtk.Widget, cls: str, enabled: bool) -> None:
    ctx = widget.get_style_context()
    if enabled:
        ctx.add_class(cls)
    else:
        ctx.remove_class(cls)


# --- processes --------------------------------------------------------------------
def which(name: str) -> bool:
    return shutil.which(name) is not None


def spawn(argv: Sequence[str]) -> bool:
    """Start a detached process; never raises."""
    try:
        GLib.spawn_async(
            list(argv),
            flags=GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.STDOUT_TO_DEV_NULL | GLib.SpawnFlags.STDERR_TO_DEV_NULL,
        )
        return True
    except GLib.Error as exc:
        log.warning("cannot start %s: %s", argv, exc)
        return False


def spawn_shell(command: str) -> bool:
    return spawn(["/bin/sh", "-c", command])


def run(argv: Sequence[str], timeout: float = 5.0) -> str:
    """Run a short command and return its stdout ('' on any failure)."""
    try:
        res = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, check=False)
        return res.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("%s failed: %s", argv, exc)
        return ""


def run_async(argv: Sequence[str], on_done=None) -> None:
    """Run a command without blocking the UI; on_done(exit_status, stdout) on the main loop."""
    try:
        proc = Gio.Subprocess.new(list(argv), Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
    except GLib.Error as exc:
        log.warning("cannot start %s: %s", argv, exc)
        if on_done:
            on_done(127, "")
        return

    def _finished(p, result):
        try:
            _ok, out, _err = p.communicate_utf8_finish(result)
            status = p.get_exit_status() if p.get_if_exited() else 1
        except GLib.Error as exc:
            log.warning("%s: %s", argv, exc)
            out, status = "", 1
        if on_done:
            on_done(status, out or "")

    proc.communicate_utf8_async(None, None, _finished)


def launch_app(app_info: Gio.AppInfo | None) -> bool:
    if app_info is None:
        return False
    display = Gdk.Display.get_default()
    context = display.get_app_launch_context() if display else None
    if context is not None:
        context.set_timestamp(Gtk.get_current_event_time())
    try:
        return bool(app_info.launch([], context))
    except GLib.Error as exc:
        log.warning("cannot launch %s: %s", app_info.get_id(), exc)
        return False


def desktop_app_info(desktop_id: str) -> Gio.DesktopAppInfo | None:
    """Gio.DesktopAppInfo for an id, or None. PyGObject raises TypeError
    ('constructor returned NULL') for a missing file instead of returning None."""
    if not desktop_id:
        return None
    if not desktop_id.endswith(".desktop"):
        desktop_id += ".desktop"
    try:
        return Gio.DesktopAppInfo.new(desktop_id)
    except (TypeError, GLib.Error):
        return None


def launch_desktop_id(desktop_id: str) -> bool:
    info = desktop_app_info(desktop_id)
    if info is None:
        # fall back to running the bare command
        return spawn([desktop_id.removesuffix(".desktop")])
    return launch_app(info)


def user_display_name() -> str:
    name = GLib.get_real_name()
    if not name or name == "Unknown":
        name = GLib.get_user_name()
    return name.split(",")[0].strip() or os.environ.get("USER", "user")


def pictures_dir() -> str:
    path = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or os.path.join(GLib.get_home_dir(), "Pictures")
    os.makedirs(path, exist_ok=True)
    return path
