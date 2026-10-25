"""Index of installed applications (desktop entries) with search and grouping."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from gi.repository import Gio, GLib

from . import Service
from ..util import desktop_app_info

log = logging.getLogger("genesis.apps")

# (chip label, categories that belong to it); first match wins, order matters.
GROUPS: list[tuple[str, frozenset[str]]] = [
    ("Internet", frozenset({"Network", "WebBrowser", "Email", "Chat", "InstantMessaging", "P2P", "FileTransfer"})),
    ("Development", frozenset({"Development", "IDE", "Debugger", "RevisionControl", "Building", "Profiling"})),
    ("Office", frozenset({"Office", "WordProcessor", "Spreadsheet", "Presentation", "TextEditor", "Calendar", "ContactManagement", "Finance", "Viewer"})),
    ("Graphics", frozenset({"Graphics", "2DGraphics", "3DGraphics", "RasterGraphics", "VectorGraphics", "Photography", "Scanning"})),
    ("Media", frozenset({"AudioVideo", "Audio", "Video", "Player", "Recorder", "Music", "TV"})),
    ("Games", frozenset({"Game"})),
    ("Education", frozenset({"Education", "Science", "Math", "Electronics", "Engineering"})),
    ("System", frozenset({"System", "Settings", "PackageManager", "Monitor", "TerminalEmulator", "FileManager", "HardwareSettings", "Security", "Emulator"})),
    ("Accessories", frozenset({"Utility", "Accessibility", "Core", "Archiving", "Compression", "Calculator", "Clock", "TextTools", "Printing"})),
]
GROUP_NAMES = ["All"] + [name for name, _ in GROUPS] + ["Other"]

# Wayland app_id / X11 WM_CLASS values that do not match their desktop file name.
APP_ID_ALIASES = {
    "chromium-browser": "chromium",
    "chromium": "chromium",
    "code": "code",
    "code-oss": "code",
    "vscodium": "codium",
    "org.pwmt.zathura": "zathura",
    "thonny": "org.thonny.Thonny",
    "org.thonny.thonny": "org.thonny.Thonny",
    "geany": "geany",
    "vlc": "vlc",
    "libreoffice-startcenter": "libreoffice-startcenter",
    "soffice": "libreoffice-startcenter",
    "lxterminal": "lxterminal",
    "pcmanfm": "pcmanfm",
    "firefox": "firefox",
    "firefox-esr": "firefox-esr",
    "mousepad": "org.xfce.mousepad",
    "genesis-settings": "genesis-settings",
}


@dataclass
class AppEntry:
    id: str  # desktop file id, e.g. "chromium.desktop"
    name: str
    generic: str
    keywords: tuple[str, ...]
    categories: frozenset[str]
    icon: Gio.Icon | None
    exec_name: str
    wm_class: str
    info: Gio.DesktopAppInfo

    @property
    def stem(self) -> str:
        return self.id[:-8] if self.id.endswith(".desktop") else self.id

    @property
    def group(self) -> str:
        for group, cats in GROUPS:
            if self.categories & cats:
                return group
        return "Other"

    def score(self, query: str) -> int:
        """Higher is better; 0 means no match."""
        q = query.casefold()
        name = self.name.casefold()
        if name == q:
            return 100
        if name.startswith(q):
            return 90
        if any(word.startswith(q) for word in name.split()):
            return 80
        if q in name:
            return 60
        if any(k.casefold().startswith(q) for k in self.keywords):
            return 50
        if q in self.generic.casefold():
            return 40
        if q in self.exec_name.casefold():
            return 30
        return 0


class AppIndex(Service):
    def __init__(self) -> None:
        super().__init__()
        self.entries: list[AppEntry] = []
        self._refresh_source = 0
        self.refresh()
        try:
            self._monitor = Gio.AppInfoMonitor.get()
            self._monitor.connect("changed", self._schedule_refresh)
        except Exception:  # noqa: BLE001
            log.exception("AppInfoMonitor unavailable")
        self.available = True

    def _schedule_refresh(self, *_args) -> None:
        if self._refresh_source:
            GLib.source_remove(self._refresh_source)
        self._refresh_source = GLib.timeout_add(800, self._refresh_and_notify)

    def _refresh_and_notify(self) -> bool:
        self._refresh_source = 0
        self.refresh()
        self.notify()
        return False

    def refresh(self) -> None:
        entries: list[AppEntry] = []
        for info in Gio.AppInfo.get_all():
            if not isinstance(info, Gio.DesktopAppInfo):
                continue
            try:
                if not info.should_show():
                    continue
                cats = frozenset(c for c in (info.get_categories() or "").split(";") if c)
                exec_line = info.get_executable() or ""
                entries.append(
                    AppEntry(
                        id=info.get_id() or "",
                        name=info.get_name() or info.get_id() or "",
                        generic=info.get_generic_name() or "",
                        keywords=tuple(info.get_keywords() or ()),
                        categories=cats,
                        icon=info.get_icon(),
                        exec_name=os.path.basename(exec_line),
                        wm_class=(info.get_startup_wm_class() or ""),
                        info=info,
                    )
                )
            except Exception:  # noqa: BLE001
                log.exception("bad desktop entry %s", info.get_id())
        entries.sort(key=lambda e: e.name.casefold())
        self.entries = entries
        self._by_stem = {e.stem.casefold(): e for e in entries}
        self._by_class = {e.wm_class.casefold(): e for e in entries if e.wm_class}
        self._by_exec = {e.exec_name.casefold(): e for e in entries if e.exec_name}
        log.debug("indexed %d applications", len(entries))

    # -- queries --------------------------------------------------------------
    def search(self, query: str = "", group: str = "All") -> list[AppEntry]:
        pool = self.entries if group in ("All", "", None) else [e for e in self.entries if e.group == group]
        query = (query or "").strip()
        if not query:
            return pool
        scored = [(e.score(query), e) for e in pool]
        return [e for s, e in sorted(scored, key=lambda t: (-t[0], t[1].name.casefold())) if s > 0]

    def by_desktop_id(self, desktop_id: str) -> AppEntry | None:
        if not desktop_id:
            return None
        if not desktop_id.endswith(".desktop"):
            desktop_id += ".desktop"
        for e in self.entries:
            if e.id == desktop_id:
                return e
        # entries hidden from menus can still be pinned (NoDisplay=true launchers)
        info = desktop_app_info(desktop_id)
        if info is not None:
            return AppEntry(
                id=desktop_id, name=info.get_name() or desktop_id, generic=info.get_generic_name() or "",
                keywords=tuple(info.get_keywords() or ()), categories=frozenset(), icon=info.get_icon(),
                exec_name=os.path.basename(info.get_executable() or ""), wm_class=info.get_startup_wm_class() or "", info=info,
            )
        return None

    def for_app_id(self, app_id: str) -> AppEntry | None:
        """Map a Wayland app_id (or X11 class) to an application."""
        if not app_id:
            return None
        candidates = [app_id, app_id.casefold(), APP_ID_ALIASES.get(app_id.casefold(), "")]
        if "." in app_id:  # org.gnome.Nautilus -> nautilus
            candidates.append(app_id.rsplit(".", 1)[-1].casefold())
        candidates = [c for c in candidates if c]
        tables = (getattr(self, "_by_stem", {}), getattr(self, "_by_class", {}), getattr(self, "_by_exec", {}))
        for cand in candidates:
            c = cand.casefold()
            for table in tables:
                if c in table:
                    return table[c]
        # last resort: the desktop file exists but is hidden from menus
        for cand in candidates:
            entry = self.by_desktop_id(cand)
            if entry is not None:
                return entry
        return None
