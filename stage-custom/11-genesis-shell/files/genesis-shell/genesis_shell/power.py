"""Power dialog: lock, log out, restart, shut down."""

from __future__ import annotations

import socket

from gi.repository import Gtk, GtkLayerShell

from .util import add_class, flat_button, icon_image, label, user_display_name
from .widgets import Glass, OverlayWindow


class PowerDialog(OverlayWindow):
    layer = GtkLayerShell.Layer.OVERLAY
    keyboard = GtkLayerShell.KeyboardMode.EXCLUSIVE
    backdrop_css = "dim-backdrop"
    blur_backdrop = True

    def __init__(self, shell) -> None:
        super().__init__(shell, "genesis-power")
        self.place(Gtk.Align.CENTER, Gtk.Align.CENTER)

    def build(self) -> Gtk.Widget:
        s = self.shell
        glass = Glass(self.blur, self.origin, radius=20, orientation=Gtk.Orientation.VERTICAL, spacing=20)
        add_class(glass, "card", "power-card")
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        head.pack_start(label("Power", css=("power-title",)), False, False, 0)
        head.pack_start(label(f"{user_display_name()} on {socket.gethostname()}", css=("subtitle",)), False, False, 0)
        glass.pack_start(head, False, False, 0)
        row = Gtk.Box(spacing=12)
        row.set_halign(Gtk.Align.CENTER)
        self.first_button = None
        for icon, text, cb, danger in (
            ("system-lock-screen-symbolic", "Lock", s.lock, False),
            ("system-log-out-symbolic", "Log Out", s.logout, False),
            ("system-reboot-symbolic", "Restart", s.reboot, False),
            ("system-shutdown-symbolic", "Shut Down", s.poweroff, True),
        ):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.pack_start(icon_image(icon, 32), False, False, 0)
            box.pack_start(label(text), False, False, 0)
            btn = flat_button(box, css=("power-action",) + (("danger",) if danger else ()))
            btn.set_can_focus(True)
            btn.connect("clicked", lambda _b, c=cb: (self.hide(), c()))
            row.pack_start(btn, False, False, 0)
            if self.first_button is None:
                self.first_button = btn
        glass.pack_start(row, False, False, 0)
        cancel = flat_button(label("Cancel"), css=("power-cancel",))
        cancel.set_halign(Gtk.Align.CENTER)
        cancel.connect("clicked", lambda _b: self.hide())
        glass.pack_start(cancel, False, False, 0)
        return glass

    def on_shown(self) -> None:
        if self.first_button is not None:
            self.first_button.grab_focus()
