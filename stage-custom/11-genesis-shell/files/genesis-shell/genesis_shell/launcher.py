"""Full-screen application launcher with search and category chips."""

from __future__ import annotations

import logging
import re

from gi.repository import Gdk, Gtk, GtkLayerShell, Pango

from .services.apps import GROUP_NAMES
from .util import add_class, flat_button, gicon_image, icon_button, label, launch_app, user_display_name
from .widgets import OverlayWindow, avatar_image

log = logging.getLogger("genesis.launcher")


class AppCell(Gtk.Button):
    def __init__(self, entry, icon_size: int) -> None:
        super().__init__()
        self.entry = entry
        self.score = 0
        self.set_relief(Gtk.ReliefStyle.NONE)
        add_class(self, "app-cell")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.pack_start(gicon_image(entry.icon, icon_size), False, False, 0)
        # long names drop a trailing parenthetical ("ImageMagick (color depth=q16)"); the full name stays in the tooltip
        shown = re.sub(r"\s*\([^()]*\)\s*$", "", entry.name).strip() if len(entry.name) > 18 else entry.name
        name = label(shown or entry.name, css=("app-label",))
        name.set_justify(Gtk.Justification.CENTER)
        name.set_line_wrap(True)
        name.set_line_wrap_mode(Pango.WrapMode.WORD)
        name.set_lines(2)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(14)
        box.pack_start(name, False, False, 0)
        self.add(box)
        self.set_size_request(max(120, icon_size + 64), -1)
        self.set_tooltip_text(entry.name if shown != entry.name else (entry.generic or entry.name))


class Launcher(OverlayWindow):
    layer = GtkLayerShell.Layer.OVERLAY
    keyboard = GtkLayerShell.KeyboardMode.EXCLUSIVE
    backdrop_css = "launcher-backdrop"
    blur_backdrop = True

    def __init__(self, shell) -> None:
        self.group = "All"
        self.query = ""
        self.cells: list[AppCell] = []
        super().__init__(shell, "genesis-launcher")
        self.place(Gtk.Align.FILL, Gtk.Align.FILL)
        shell.apps.connect(self.rebuild)
        self.rebuild()

    def build(self) -> Gtk.Widget:
        cfg = self.shell.config
        columns = max(4, min(10, cfg.getint("launcher", "columns")))
        self.columns = columns
        icon_size = max(32, min(96, cfg.getint("launcher", "icon_size")))
        self.cell_w = max(120, icon_size + 64)
        col_w = columns * self.cell_w + (columns - 1) * 12  # width of a full grid row; search and footer align to it
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        root.set_margin_top(88)
        root.set_margin_bottom(28)
        root.set_margin_start(40)
        root.set_margin_end(40)

        self.search = Gtk.Entry()
        add_class(self.search, "search-entry")
        self.search.set_placeholder_text("Search apps")
        self.search.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "system-search-symbolic")
        self.search.set_size_request(min(640, col_w), -1)
        self.search.set_halign(Gtk.Align.CENTER)
        self.search.connect("changed", self._on_search_changed)
        self.search.connect("activate", lambda _e: self._activate_first())
        self.search.connect("key-press-event", self._on_search_key)
        root.pack_start(self.search, False, False, 0)

        chips = Gtk.Box(spacing=6)
        chips.set_halign(Gtk.Align.CENTER)
        self.chip_buttons: dict[str, Gtk.ToggleButton] = {}
        for name in GROUP_NAMES:
            btn = Gtk.ToggleButton(label=name)
            add_class(btn, "chip")
            btn.set_no_show_all(True)  # rebuild() shows only chips whose category has apps
            btn.get_child().show()
            btn.set_can_focus(False)
            btn.set_active(name == "All")
            btn.connect("toggled", self._on_chip, name)
            chips.pack_start(btn, False, False, 0)
            self.chip_buttons[name] = btn
        root.pack_start(chips, False, False, 0)

        self.flow = Gtk.FlowBox()
        self.flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flow.set_homogeneous(True)
        self.flow.set_max_children_per_line(columns)
        self.flow.set_min_children_per_line(min(4, columns))
        self.flow.set_row_spacing(12)
        self.flow.set_column_spacing(12)
        self.flow.set_halign(Gtk.Align.CENTER)
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_filter_func(self._filter, None)
        self.flow.set_sort_func(self._sort, None)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        scrolled.add(self.flow)
        self.scrolled = scrolled
        root.pack_start(scrolled, True, True, 0)

        self.empty = label("No matching apps", css=("empty-state",))
        self.empty.set_no_show_all(True)
        root.pack_start(self.empty, False, False, 0)

        footer = Gtk.Box(spacing=8)
        add_class(footer, "launcher-footer")
        footer.set_halign(Gtk.Align.CENTER)
        footer.set_size_request(col_w, -1)
        who = Gtk.Box(spacing=10)
        who.pack_start(avatar_image(32), False, False, 0)
        who.pack_start(label(user_display_name(), css=("title",)), False, False, 0)
        footer.pack_start(who, False, False, 0)
        actions = Gtk.Box(spacing=8)
        for icon, tip, cb, extra in (
            ("preferences-system-symbolic", "Settings", lambda: self.shell.open_settings(), ()),
            ("system-lock-screen-symbolic", "Lock", self.shell.lock, ()),
            ("system-shutdown-symbolic", "Power", self.shell.show_power, ("danger",)),
        ):
            btn = icon_button(icon, 16, css=("round-button",) + extra, tooltip=tip)
            btn.connect("clicked", lambda _b, c=cb: (self.hide(), c()))
            actions.pack_start(btn, False, False, 0)
        footer.pack_end(actions, False, False, 0)
        root.pack_end(footer, False, False, 0)
        return root

    # -- data -------------------------------------------------------------------------
    def rebuild(self) -> None:
        for child in self.flow.get_children():
            self.flow.remove(child)
        self.cells = []
        icon_size = max(32, min(96, self.shell.config.getint("launcher", "icon_size")))
        for entry in self.shell.apps.entries:
            cell = AppCell(entry, icon_size)
            cell.connect("clicked", self._cell_clicked)
            self.cells.append(cell)
            self.flow.add(cell)
        groups = {e.group for e in self.shell.apps.entries}
        for name, btn in self.chip_buttons.items():
            btn.set_visible(name == "All" or name in groups)
        if self.group != "All" and self.group not in groups:
            self.chip_buttons["All"].set_active(True)
        self.flow.show_all()
        self._rescore()

    def _rescore(self) -> None:
        q = self.query.strip()
        for cell in self.cells:
            cell.score = cell.entry.score(q) if q else 1
        self.flow.invalidate_filter()
        self.flow.invalidate_sort()
        # short result sets centre under the search box instead of hugging the left column
        n = sum(1 for c in self.cells if c.score > 0 and (self.group == "All" or c.entry.group == self.group))
        per_line = max(1, min(self.columns, n))
        self.flow.set_min_children_per_line(per_line)
        self.flow.set_max_children_per_line(per_line)
        self.empty.set_visible(self._first_visible() is None)

    def _filter(self, child, _data) -> bool:
        cell = child.get_child()
        if self.group != "All" and cell.entry.group != self.group:
            return False
        return cell.score > 0

    def _sort(self, a, b, _data) -> int:
        ca, cb = a.get_child(), b.get_child()
        if self.query.strip() and ca.score != cb.score:
            return -1 if ca.score > cb.score else 1
        na, nb = ca.entry.name.casefold(), cb.entry.name.casefold()
        return -1 if na < nb else (1 if na > nb else 0)

    def _first_visible(self) -> AppCell | None:
        for child in self.flow.get_children():
            if child.get_child_visible() and child.get_child().score > 0:
                cell = child.get_child()
                if self.group == "All" or cell.entry.group == self.group:
                    return cell
        return None

    # -- interaction -------------------------------------------------------------------
    def _on_search_changed(self, entry) -> None:
        self.query = entry.get_text()
        self._rescore()
        self.scrolled.get_vadjustment().set_value(0)

    def _on_chip(self, button, name) -> None:
        if not button.get_active():
            if name == self.group:  # do not allow deselecting the current chip
                button.set_active(True)
            return
        self.group = name
        for other, btn in self.chip_buttons.items():
            if other != name and btn.get_active():
                btn.set_active(False)
        self._rescore()

    def _on_search_key(self, _entry, event) -> bool:
        if event.keyval in (Gdk.KEY_Down, Gdk.KEY_Tab):
            first = self._first_visible()
            if first is not None:
                first.grab_focus()
            return True
        return False

    def on_key(self, event) -> bool:
        # typing anywhere goes to the search box
        if self.window.get_focus() is not self.search:
            uni = Gdk.keyval_to_unicode(event.keyval)
            if uni and chr(uni).isprintable() and not event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK):
                self.search.grab_focus()
                self.search.set_position(-1)
                return self.search.event(event)
            if event.keyval == Gdk.KEY_BackSpace:
                self.search.grab_focus()
                self.search.set_position(-1)
                return self.search.event(event)
        return False

    def _activate_first(self) -> None:
        first = self._first_visible()
        if first is not None:
            self._launch(first.entry)

    def _cell_clicked(self, cell) -> None:
        self._launch(cell.entry)

    def _launch(self, entry) -> None:
        self.hide()
        launch_app(entry.info)

    def on_shown(self) -> None:
        self.search.set_text("")
        if self.group != "All":
            self.chip_buttons["All"].set_active(True)
        self.search.grab_focus()
        self.scrolled.get_vadjustment().set_value(0)
