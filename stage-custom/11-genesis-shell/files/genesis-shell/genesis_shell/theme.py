"""Loads shell.css with the configured accent colour and glass opacity."""

from __future__ import annotations

import logging
from pathlib import Path

from gi.repository import Gdk, Gtk

from .util import lighten, parse_hex

log = logging.getLogger("genesis.theme")

CSS_FILE = Path(__file__).with_name("shell.css")


class Theme:
    def __init__(self, config) -> None:
        self.config = config
        self.provider = Gtk.CssProvider()
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(screen, self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 50)
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.set_property("gtk-application-prefer-dark-theme", True)
        self.apply()

    def colours(self) -> dict[str, str]:
        accent = self.config.get("appearance", "accent") or "#4f8cff"
        r, g, b = parse_hex(accent)
        accent = "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))  # normalised, always valid CSS
        opacity = max(0.3, min(0.98, self.config.getfloat("appearance", "glass_opacity")))
        # every value here must be a valid GTK CSS colour: one bad @define-color discards the whole stylesheet
        return {
            "accent": accent,
            "accent_hover": lighten(accent, 0.18),
            "glass": f"rgba(18, 26, 38, {opacity:.2f})",
            "glass_strong": f"rgba(16, 23, 34, {min(0.98, opacity + 0.16):.2f})",
            "glass_light": "rgba(255, 255, 255, 0.06)",
            "edge": "rgba(255, 255, 255, 0.09)",
            "edge_outer": "rgba(0, 0, 0, 0.38)",
            "highlight": "rgba(255, 255, 255, 0.16)",
            "text": "#e9eff6",
            "text_dim": "rgba(233, 239, 246, 0.62)",
            "danger": "#ff5d5d",
            "success": "#3ddc97",
            "warning": "#ffb347",
        }

    def apply(self) -> None:
        colours = self.colours()
        defines = "\n".join(f"@define-color {name} {value};" for name, value in colours.items())
        try:
            css = CSS_FILE.read_text(encoding="utf-8")
        except OSError as exc:
            log.error("cannot read %s: %s", CSS_FILE, exc)
            css = ""
        try:
            self.provider.load_from_data((defines + "\n" + css).encode("utf-8"))
        except Exception as exc:  # GLib.Error on CSS syntax errors
            log.error("shell.css failed to load: %s", exc)
