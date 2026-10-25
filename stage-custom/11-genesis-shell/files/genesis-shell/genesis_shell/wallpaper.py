"""Wallpaper layer per monitor, plus the blurred copies the glass surfaces use."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import threading
from pathlib import Path

import cairo
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, GtkLayerShell

from .config import CACHE_DIR, WALLPAPER_DIRS
from .util import ALL_EDGES, make_layer_window, monitors, spawn, which

log = logging.getLogger("genesis.wallpaper")

FALLBACK_RGB = (0x0B / 255, 0x14 / 255, 0x20 / 255)
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


class WallpaperWindow:
    def __init__(self, manager: "WallpaperManager", monitor: Gdk.Monitor) -> None:
        self.manager = manager
        self.monitor = monitor
        self.window = make_layer_window(
            namespace="genesis-wallpaper", layer=GtkLayerShell.Layer.BACKGROUND, anchors=ALL_EDGES,
            exclusive=False, keyboard=GtkLayerShell.KeyboardMode.NONE, monitor=monitor,
        )
        self.area = Gtk.DrawingArea()
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.area.connect("draw", self._draw)
        self.area.connect("button-press-event", self._on_press)
        self.window.add(self.area)
        self.window.show_all()

    def _draw(self, _widget, cr) -> bool:
        geo = self.monitor.get_geometry()
        pixbuf = self.manager.pixbuf_for(geo.width, geo.height)
        cr.set_source_rgb(*FALLBACK_RGB)
        cr.paint()
        if pixbuf is not None:
            Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
            cr.paint()
        return True

    def _on_press(self, _widget, event) -> bool:
        if event.button == 3:
            self.manager.context_menu(event)
            return True
        if event.button == 1 and event.type == Gdk.EventType._2BUTTON_PRESS:
            return True
        return False

    def redraw(self) -> None:
        self.area.queue_draw()

    def destroy(self) -> None:
        self.window.destroy()


class WallpaperManager:
    def __init__(self, shell) -> None:
        self.shell = shell
        self.config = shell.config
        self.windows: dict[Gdk.Monitor, WallpaperWindow] = {}
        self._original: tuple[str, GdkPixbuf.Pixbuf] | None = None
        self._scaled: dict[tuple, GdkPixbuf.Pixbuf] = {}
        self._blur: dict[tuple, cairo.ImageSurface] = {}
        self._pending_blur: set[tuple] = set()
        self._failed_blur: set[tuple] = set()
        self._current = self.path()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for mon in monitors():
            self.windows[mon] = WallpaperWindow(self, mon)
            self.ensure_blur_async(mon)
        display = Gdk.Display.get_default()
        if display is not None:
            display.connect("monitor-added", self._monitor_added)
            display.connect("monitor-removed", self._monitor_removed)
        self.config.connect(self._on_config)

    # -- monitors ------------------------------------------------------------------
    def _monitor_added(self, _display, monitor) -> None:
        if monitor not in self.windows:
            self.windows[monitor] = WallpaperWindow(self, monitor)
            self.ensure_blur_async(monitor)

    def _monitor_removed(self, _display, monitor) -> None:
        win = self.windows.pop(monitor, None)
        if win is not None:
            win.destroy()

    # -- configuration ---------------------------------------------------------------
    def path(self) -> str | None:
        configured = self.config.get("wallpaper", "file")
        if configured and os.path.isfile(configured):
            return configured
        for candidate in self.available():
            return candidate
        return None

    def mode(self) -> str:
        mode = self.config.get("wallpaper", "mode").lower()
        return mode if mode in ("zoom", "fit", "stretch", "center") else "zoom"

    def available(self) -> list[str]:
        found: list[str] = []
        for base in WALLPAPER_DIRS:
            try:
                for name in sorted(os.listdir(base)):
                    if name.lower().endswith(IMAGE_SUFFIXES):
                        found.append(os.path.join(base, name))
            except OSError:
                continue
        return found

    def set_wallpaper(self, path: str, mode: str | None = None) -> bool:
        if not os.path.isfile(path):
            log.warning("wallpaper not found: %s", path)
            return False
        self.config.set("wallpaper", "file", path)
        if mode:
            self.config.set("wallpaper", "mode", mode)
        try:
            self.config.save()
        except OSError as exc:
            log.warning("cannot save wallpaper setting: %s", exc)
        self._apply()
        return True

    def _on_config(self) -> None:
        self._apply()

    def _apply(self) -> None:
        new = self.path()
        self._current = new
        self._original = None
        self._scaled.clear()
        self._blur.clear()
        self._failed_blur.clear()
        for win in self.windows.values():
            win.redraw()
        for mon in list(self.windows):
            self.ensure_blur_async(mon)
        self.shell.redraw_glass()

    # -- pixel data --------------------------------------------------------------------
    def _load_original(self) -> GdkPixbuf.Pixbuf | None:
        path = self._current
        if not path:
            return None
        if self._original is not None and self._original[0] == path:
            return self._original[1]
        try:
            pix = GdkPixbuf.Pixbuf.new_from_file(path)
            pix = pix.apply_embedded_orientation() or pix
        except GLib.Error as exc:
            log.warning("cannot load wallpaper %s: %s", path, exc)
            self._original = (path, None)  # remembered: the draw handler asks on every frame
            return None
        self._original = (path, pix)
        return pix

    def pixbuf_for(self, width: int, height: int) -> GdkPixbuf.Pixbuf | None:
        if width <= 0 or height <= 0:
            return None
        key = (self._current, self.mode(), width, height)
        if key in self._scaled:
            return self._scaled[key]
        src = self._load_original()
        if src is None:
            return None
        mode = self.mode()
        sw, sh = src.get_width(), src.get_height()
        try:
            if mode == "stretch":
                out = src.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
            elif mode == "center":
                out = self._blank(width, height, src.get_has_alpha())
                w, h = min(sw, width), min(sh, height)
                src.copy_area((sw - w) // 2, (sh - h) // 2, w, h, out, (width - w) // 2, (height - h) // 2)
            elif mode == "fit":
                scale = min(width / sw, height / sh)
                w, h = max(1, int(sw * scale)), max(1, int(sh * scale))
                scaled = src.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
                out = self._blank(width, height, scaled.get_has_alpha())
                scaled.copy_area(0, 0, w, h, out, (width - w) // 2, (height - h) // 2)
            else:  # zoom / cover
                scale = max(width / sw, height / sh)
                w, h = max(width, math.ceil(sw * scale)), max(height, math.ceil(sh * scale))
                scaled = src.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
                out = scaled.new_subpixbuf((w - width) // 2, (h - height) // 2, width, height)
        except GLib.Error as exc:
            log.warning("cannot scale wallpaper: %s", exc)
            return None
        self._scaled[key] = out
        return out

    @staticmethod
    def _blank(width: int, height: int, has_alpha: bool = False) -> GdkPixbuf.Pixbuf:
        pix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, bool(has_alpha), 8, width, height)
        pix.fill(0x0B1420FF)
        return pix

    # -- blurred copies ----------------------------------------------------------------
    def _blur_key(self, monitor: Gdk.Monitor) -> tuple | None:
        geo = monitor.get_geometry()
        if not self._current or geo.width <= 0:
            return None
        return (self._current, self.mode(), geo.width, geo.height)

    def _blur_file(self, key: tuple, deep: bool = False) -> Path:
        path, mode, w, h = key
        try:
            stamp = str(int(os.stat(path).st_mtime))
        except OSError:
            stamp = "0"
        digest = hashlib.sha1(f"{path}|{stamp}|{mode}|{w}x{h}|v4".encode()).hexdigest()[:16]
        return CACHE_DIR / f"blur-{digest}{'-deep' if deep else ''}.png"

    def blurred_surface(self, monitor: Gdk.Monitor, deep: bool = False) -> cairo.ImageSurface | None:
        """Blurred, darkened wallpaper for `monitor`.

        The light copy sits under glass surfaces (detail and colour show through);
        the deep copy backs full-screen overlays (launcher, power dialog), where
        no shape of the wallpaper should survive. Falls back to the light copy.
        """
        key = self._blur_key(monitor)
        if key is None:
            return None
        cache_key = (key, deep)
        if cache_key in self._blur:
            return self._blur[cache_key]
        file = self._blur_file(key, deep)
        if file.is_file():
            try:
                surface = cairo.ImageSurface.create_from_png(str(file))
                self._blur[cache_key] = surface
                return surface
            except Exception as exc:  # noqa: BLE001
                log.warning("bad blur cache %s: %s", file, exc)
        if deep and self._blur_file(key).is_file():
            return self.blurred_surface(monitor)
        self.ensure_blur_async(monitor)
        return None

    def ensure_blur_async(self, monitor: Gdk.Monitor) -> None:
        key = self._blur_key(monitor)
        if key is None or (key, False) in self._blur or key in self._pending_blur or key in self._failed_blur:
            return
        file = self._blur_file(key)
        if file.is_file():
            return
        self._pending_blur.add(key)
        pixbuf = self.pixbuf_for(key[2], key[3])  # GdkPixbuf work stays on the main thread

        def worker() -> None:
            try:
                self._generate_blur(key, file, pixbuf)
            except Exception:  # noqa: BLE001
                log.exception("blur generation failed; surfaces stay plain for this wallpaper")
            if not file.is_file():
                # remembered until the wallpaper changes; otherwise every redraw would retry
                self._failed_blur.add(key)
                log.warning("no blurred copy for %s; glass surfaces stay plain", key[0])
            GLib.idle_add(self._blur_ready, key)

        threading.Thread(target=worker, name="wallpaper-blur", daemon=True).start()

    def _blur_ready(self, key) -> bool:
        self._pending_blur.discard(key)
        self.shell.redraw_glass()
        return False

    @staticmethod
    def _generate_blur(key: tuple, file: Path, pixbuf: GdkPixbuf.Pixbuf | None) -> None:
        path, _mode, w, h = key
        tmp = file.with_suffix(".tmp.png")
        try:
            from PIL import Image, ImageEnhance, ImageFilter  # type: ignore

            img = Image.open(path).convert("RGB")
            sw, sh = img.size
            scale = max(w / sw, h / sh)
            cw, ch = max(w, math.ceil(sw * scale)), max(h, math.ceil(sh * scale))
            img = img.resize((cw, ch), Image.BILINEAR).crop(((cw - w) // 2, (ch - h) // 2, (cw - w) // 2 + w, (ch - h) // 2 + h))
            # deep copy first: the light file is the "done" marker ensure_blur_async checks
            deep = img.resize((max(1, w // 8), max(1, h // 8)), Image.BILINEAR).filter(ImageFilter.GaussianBlur(6))
            deep = deep.resize((w, h), Image.BICUBIC)
            deep = ImageEnhance.Brightness(ImageEnhance.Color(deep).enhance(1.3)).enhance(0.7)
            deep_file = file.with_name(file.stem + "-deep.png")
            deep_tmp = deep_file.with_suffix(".tmp.png")
            deep.save(deep_tmp, format="PNG", compress_level=3)
            deep_tmp.replace(deep_file)
            small = img.resize((max(1, w // 4), max(1, h // 4)), Image.BILINEAR).filter(ImageFilter.GaussianBlur(5))
            big = small.resize((w, h), Image.BICUBIC)
            big = ImageEnhance.Color(big).enhance(1.25)
            big = ImageEnhance.Brightness(big).enhance(0.8)
            big.save(tmp, format="PNG", compress_level=3)
        except Exception as exc:  # noqa: BLE001 - PIL missing: pixbuf pseudo-blur
            log.info("PIL blur unavailable (%s); using pixbuf downscale", exc)
            if pixbuf is None:
                return
            tiny = pixbuf.scale_simple(max(1, w // 10), max(1, h // 10), GdkPixbuf.InterpType.BILINEAR)
            big = tiny.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
            dark = big.composite_color_simple(w, h, GdkPixbuf.InterpType.NEAREST, 204, 1, 0x000000, 0x000000)
            (dark or big).savev(str(tmp), "png", [], [])
        tmp.replace(file)

    # -- desktop context menu -------------------------------------------------------------
    def context_menu(self, event) -> None:
        menu = Gtk.Menu()
        shortcuts = self.config

        def item(text: str, callback) -> None:
            mi = Gtk.MenuItem.new_with_label(text)
            mi.connect("activate", lambda _m: callback())
            menu.append(mi)

        item("Change Wallpaper…", lambda: self.shell.open_settings("appearance"))
        item("Open Terminal", lambda: spawn([shortcuts.get("shortcuts", "terminal")]))
        item("Open Files", lambda: spawn([shortcuts.get("shortcuts", "files")]))
        menu.append(Gtk.SeparatorMenuItem())
        if which(shortcuts.get("shortcuts", "display_settings")):
            item("Display Settings", lambda: spawn([shortcuts.get("shortcuts", "display_settings")]))
        item("Genesis Settings", lambda: self.shell.open_settings())
        menu.show_all()
        menu.popup_at_pointer(event)
