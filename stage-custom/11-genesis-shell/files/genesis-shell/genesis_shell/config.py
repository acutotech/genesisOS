"""Shell configuration: /etc/genesis/shell.conf overlaid by ~/.config/genesis/shell.conf.

Plain INI. Every key has a default here, so the shell runs with no file at all.
The user file is watched; changes apply live (Genesis Settings writes it).
"""

from __future__ import annotations

import configparser
import logging
from pathlib import Path
from typing import Callable

from gi.repository import Gio, GLib

log = logging.getLogger("genesis.config")

SYSTEM_CONF = Path("/etc/genesis/shell.conf")
USER_DIR = Path(GLib.get_user_config_dir()) / "genesis"
USER_CONF = USER_DIR / "shell.conf"
CACHE_DIR = Path(GLib.get_user_cache_dir()) / "genesis"
WALLPAPER_DIRS = ("/usr/share/genesis/wallpapers", "/usr/share/rpd-wallpaper", "/usr/share/backgrounds")

DEFAULTS: dict[str, dict[str, str]] = {
    "wallpaper": {
        "file": "/usr/share/genesis/wallpapers/genesis-horizon.jpg",
        "mode": "zoom",  # zoom (cover) | fit | stretch | center
    },
    "panel": {
        "position": "top",  # top | bottom
        "height": "44",
        "floating": "true",
        "launchers": "chromium code pcmanfm lxterminal genesis-settings",
        "clock_format": "%a %d %b   %H:%M",
        "show_seconds": "false",
    },
    "appearance": {
        "accent": "#4f8cff",
        "glass_opacity": "0.62",
    },
    "notifications": {
        "timeout": "6",
        "do_not_disturb": "false",
    },
    "launcher": {
        "columns": "7",
        "icon_size": "64",
    },
    "idle": {
        "blank_timeout": "600",  # seconds, 0 disables
        "lock_on_idle": "false",
    },
    "shortcuts": {
        "terminal": "lxterminal",
        "files": "pcmanfm",
        "settings": "genesis-settings",
        "display_settings": "raindrop",
    },
}


class Config:
    """Merged view of defaults, the system file and the user file."""

    def __init__(self) -> None:
        self._parser = configparser.ConfigParser(interpolation=None)
        self._listeners: list[Callable[[], None]] = []
        self._monitor: Gio.FileMonitor | None = None
        self._reload_source = 0
        self.reload()
        self._watch()

    # -- reading ---------------------------------------------------------------
    def reload(self) -> None:
        old = {name: dict(self._parser.items(name)) for name in self._parser.sections()}
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_dict(DEFAULTS)
        for path in (SYSTEM_CONF, USER_CONF):
            if not path.is_file():
                continue
            try:
                parser.read(path, encoding="utf-8")
            except configparser.Error as exc:
                log.warning("ignoring %s: %s", path, exc)
        self._parser = parser
        new = {name: dict(parser.items(name)) for name in parser.sections()}
        #: sections whose values differ from before the last reload; listeners use it
        #: to redo only what changed (a save made by the shell itself changes nothing)
        self.changed = {name for name in set(old) | set(new) if old.get(name) != new.get(name)}

    def get(self, section: str, key: str, fallback: str | None = None) -> str:
        return self._parser.get(section, key, fallback=fallback if fallback is not None else DEFAULTS.get(section, {}).get(key, ""))

    def getint(self, section: str, key: str) -> int:
        try:
            return self._parser.getint(section, key)
        except (ValueError, configparser.Error):
            return int(DEFAULTS[section][key])

    def getfloat(self, section: str, key: str) -> float:
        try:
            return self._parser.getfloat(section, key)
        except (ValueError, configparser.Error):
            return float(DEFAULTS[section][key])

    def getbool(self, section: str, key: str) -> bool:
        try:
            return self._parser.getboolean(section, key)
        except (ValueError, configparser.Error):
            return DEFAULTS[section][key].lower() in ("1", "true", "yes", "on")

    def getlist(self, section: str, key: str) -> list[str]:
        return self.get(section, key).split()

    # -- writing ---------------------------------------------------------------
    def set(self, section: str, key: str, value: str) -> None:
        if not self._parser.has_section(section):
            self._parser.add_section(section)
        self._parser.set(section, key, str(value))

    def save(self) -> None:
        """Write the merged configuration to the user file (atomically)."""
        USER_DIR.mkdir(parents=True, exist_ok=True)
        tmp = USER_CONF.with_suffix(".conf.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write("# Genesis Shell settings. Edited by Genesis Settings; hand edits are fine.\n")
            self._parser.write(fh)
        tmp.replace(USER_CONF)

    # -- change notification ---------------------------------------------------
    def connect(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def _watch(self) -> None:
        try:
            USER_DIR.mkdir(parents=True, exist_ok=True)
            gfile = Gio.File.new_for_path(str(USER_CONF))
            self._monitor = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
            self._monitor.connect("changed", self._on_file_changed)
        except GLib.Error as exc:
            log.warning("cannot watch %s: %s", USER_CONF, exc)

    def _on_file_changed(self, *_args) -> None:
        if self._reload_source:
            GLib.source_remove(self._reload_source)
        self._reload_source = GLib.timeout_add(300, self._reload_and_notify)

    def _reload_and_notify(self) -> bool:
        self._reload_source = 0
        self.reload()
        if not self.changed:
            return False
        log.debug("configuration changed: %s", ", ".join(sorted(self.changed)))
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:  # noqa: BLE001 - one bad listener must not stop the others
                log.exception("config listener failed")
        return False
