"""org.freedesktop.Notifications server, toasts, and the notification center."""

from __future__ import annotations

import logging
import re
import time

from gi.repository import GdkPixbuf, Gio, GLib, Gtk, GtkLayerShell, Pango

from .services import Service
from .util import add_class, desktop_app_info, flat_button, gicon_image, icon_image, label, launch_app, make_layer_window
from .widgets import CARD_WIDTH, Glass, OverlayWindow, empty_state

log = logging.getLogger("genesis.notifications")

INTERFACE_XML = """
<node>
  <interface name="org.freedesktop.Notifications">
    <method name="GetCapabilities"><arg direction="out" name="capabilities" type="as"/></method>
    <method name="Notify">
      <arg direction="in" name="app_name" type="s"/>
      <arg direction="in" name="replaces_id" type="u"/>
      <arg direction="in" name="app_icon" type="s"/>
      <arg direction="in" name="summary" type="s"/>
      <arg direction="in" name="body" type="s"/>
      <arg direction="in" name="actions" type="as"/>
      <arg direction="in" name="hints" type="a{sv}"/>
      <arg direction="in" name="expire_timeout" type="i"/>
      <arg direction="out" name="id" type="u"/>
    </method>
    <method name="CloseNotification"><arg direction="in" name="id" type="u"/></method>
    <method name="GetServerInformation">
      <arg direction="out" name="name" type="s"/>
      <arg direction="out" name="vendor" type="s"/>
      <arg direction="out" name="version" type="s"/>
      <arg direction="out" name="spec_version" type="s"/>
    </method>
    <signal name="NotificationClosed"><arg name="id" type="u"/><arg name="reason" type="u"/></signal>
    <signal name="ActionInvoked"><arg name="id" type="u"/><arg name="action_key" type="s"/></signal>
  </interface>
</node>
"""

REASON_EXPIRED, REASON_DISMISSED, REASON_CLOSED = 1, 2, 3
MAX_STORED = 60
MAX_TOASTS = 4
TOAST_MARGIN = (4, 10, 14, 10)  # top, right, bottom, left: room inside the layer window for the .toast drop shadow

_IMG_TAG = re.compile(r"<img[^>]*>", re.I)
_BR_TAG = re.compile(r"<br\s*/?>", re.I)
_OTHER_TAG = re.compile(r"</?(?!b\b|i\b|u\b|a\b)[a-zA-Z][^>]*>")
_ANY_TAG = re.compile(r"<[^>]+>")


def body_markup(body: str) -> str:
    """Notification bodies may carry a tiny HTML subset; keep what Pango accepts."""
    text = _IMG_TAG.sub("", body or "")
    text = _BR_TAG.sub("\n", text)
    text = _OTHER_TAG.sub("", text)
    try:
        Pango.parse_markup(text, -1, "\x00")
        return text
    except GLib.Error:
        return GLib.markup_escape_text(_ANY_TAG.sub("", text))


class Notification:
    def __init__(self, nid: int, app_name: str, app_icon: str, summary: str, body: str, actions: list, hints: dict, timeout: int) -> None:
        self.id = nid
        self.app_name = app_name or "Notification"
        self.app_icon = app_icon or ""
        self.summary = summary or ""
        self.body = body or ""
        self.actions = [(actions[i], actions[i + 1]) for i in range(0, len(actions) - 1, 2)]
        self.urgency = int(hints.get("urgency", 1) or 1)
        self.desktop_entry = str(hints.get("desktop-entry", "") or "")
        self.transient = bool(hints.get("transient", False))
        self.resident = bool(hints.get("resident", False))
        self.image_path = str(hints.get("image-path", hints.get("image_path", "")) or "")
        self.pixbuf = self._pixbuf(hints)
        self.timeout = timeout
        self.time = time.time()
        self.read = False

    @staticmethod
    def _pixbuf(hints: dict) -> GdkPixbuf.Pixbuf | None:
        data = hints.get("image-data", hints.get("image_data", hints.get("icon_data")))
        if not data:
            return None
        try:
            width, height, rowstride, has_alpha, bits, _channels, raw = data
            pix = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(raw)), GdkPixbuf.Colorspace.RGB, bool(has_alpha), int(bits), int(width), int(height), int(rowstride))
            return pix.scale_simple(48, 48, GdkPixbuf.InterpType.BILINEAR)
        except Exception as exc:  # noqa: BLE001
            log.debug("bad image-data: %s", exc)
            return None

    def icon_widget(self, size: int) -> Gtk.Widget:
        if self.pixbuf is not None:
            pix = self.pixbuf if size == 48 else self.pixbuf.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
            return Gtk.Image.new_from_pixbuf(pix)
        for candidate in (self.image_path, self.app_icon):
            if candidate == "dialog-information-symbolic":
                continue  # the shell's own default icon: draw the accent roundel below instead of a bare glyph
            if candidate.startswith("file://"):
                candidate = candidate[7:]
            if candidate.startswith("/"):
                cache = self.__dict__.setdefault("_file_icons", {})
                if (candidate, size) not in cache:  # a screenshot is a full-size PNG: decode it once
                    try:
                        cache[(candidate, size)] = GdkPixbuf.Pixbuf.new_from_file_at_scale(candidate, size, size, True)
                    except GLib.Error:
                        cache[(candidate, size)] = None
                if cache[(candidate, size)] is None:
                    continue
                return Gtk.Image.new_from_pixbuf(cache[(candidate, size)])
            if candidate:
                return icon_image(candidate, size)
        if self.desktop_entry:
            info = desktop_app_info(self.desktop_entry)
            if info is not None and info.get_icon() is not None:
                return gicon_image(info.get_icon(), size)
        img = icon_image("dialog-information-symbolic", size // 2 + 4)  # glyph in an accent roundel
        img.set_size_request(size + 4, size + 4)
        add_class(img, "generic-icon")
        return img

    def age_text(self) -> str:
        delta = int(time.time() - self.time)
        if delta < 60:
            return "now"
        if delta < 3600:
            return f"{delta // 60} min"
        if delta < 86400:
            return f"{delta // 3600} h"
        return time.strftime("%d %b", time.localtime(self.time))


class NotificationService(Service):
    def __init__(self, shell) -> None:
        super().__init__()
        self.shell = shell
        self.items: list[Notification] = []
        self._next_id = 1
        self._connection: Gio.DBusConnection | None = None
        self._registration = 0
        self.dnd = shell.config.getbool("notifications", "do_not_disturb")
        self.toasts = ToastStack(shell, self)
        self._node = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML)
        self._owner = Gio.bus_own_name(
            Gio.BusType.SESSION, "org.freedesktop.Notifications", Gio.BusNameOwnerFlags.REPLACE | Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT,
            self._bus_acquired, self._name_acquired, self._name_lost,
        )
        shell.config.connect(self._on_config)

    def _on_config(self) -> None:
        dnd = self.shell.config.getbool("notifications", "do_not_disturb")
        if dnd != self.dnd:
            self.dnd = dnd
            self.notify()

    # -- D-Bus --------------------------------------------------------------------------
    def _bus_acquired(self, connection, _name) -> None:
        self._connection = connection
        try:
            self._registration = connection.register_object("/org/freedesktop/Notifications", self._node.interfaces[0], self._method_call, None, None)
        except GLib.Error as exc:
            log.error("cannot register notification object: %s", exc)

    def _name_acquired(self, _connection, _name) -> None:
        self.available = True
        log.info("notification server active")

    def _name_lost(self, _connection, _name) -> None:
        self.available = False
        log.warning("lost org.freedesktop.Notifications (another daemon running?)")

    def _method_call(self, connection, _sender, _path, _iface, method, params, invocation) -> None:
        try:
            if method == "GetCapabilities":
                invocation.return_value(GLib.Variant("(as)", (["actions", "body", "body-markup", "icon-static", "persistence"],)))
            elif method == "GetServerInformation":
                invocation.return_value(GLib.Variant("(ssss)", ("Genesis Shell", "Genesis OS", "1.0", "1.2")))
            elif method == "Notify":
                app_name, replaces_id, app_icon, summary, body, actions, hints, timeout = params.unpack()
                nid = self.post(app_name, app_icon, summary, body, list(actions), dict(hints), int(timeout), replaces_id=int(replaces_id))
                invocation.return_value(GLib.Variant("(u)", (nid,)))
            elif method == "CloseNotification":
                (nid,) = params.unpack()
                self.close(self.find(int(nid)), REASON_CLOSED)
                invocation.return_value(None)
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
        except Exception as exc:  # noqa: BLE001
            log.exception("notification method %s failed", method)
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(exc))

    def _emit(self, signal: str, variant: GLib.Variant) -> None:
        if self._connection is None:
            return
        try:
            self._connection.emit_signal(None, "/org/freedesktop/Notifications", "org.freedesktop.Notifications", signal, variant)
        except GLib.Error as exc:
            log.debug("emit %s failed: %s", signal, exc)

    # -- store ------------------------------------------------------------------------------
    def find(self, nid: int) -> Notification | None:
        return next((n for n in self.items if n.id == nid), None) or self.toasts.find(nid)

    def post(self, app_name: str, app_icon: str, summary: str, body: str, actions: list, hints: dict, timeout: int = -1, replaces_id: int = 0) -> int:
        existing = self.find(replaces_id) if replaces_id else None
        nid = existing.id if existing is not None else self._next_id
        if existing is None:
            self._next_id = self._next_id % 0xFFFFFFF + 1
        n = Notification(nid, app_name, app_icon, summary, body, actions, hints, timeout)
        if existing is not None:
            if existing in self.items:
                self.items.remove(existing)
            self.toasts.remove(existing)
        if not n.transient:
            self.items.insert(0, n)
            del self.items[MAX_STORED:]
        if not self.dnd or n.urgency >= 2:
            self.toasts.show(n)
        self.notify()
        return nid

    def notify_local(self, summary: str, body: str = "", icon: str = "dialog-information-symbolic") -> int:
        return self.post("Genesis", icon, summary, body, [], {"desktop-entry": "genesis-settings"})

    def close(self, n: Notification | None, reason: int) -> None:
        if n is None:
            return
        self.toasts.remove(n)
        if n in self.items:
            self.items.remove(n)
        self._emit("NotificationClosed", GLib.Variant("(uu)", (n.id, reason)))
        self.notify()

    def invoke(self, n: Notification, key: str) -> None:
        self._emit("ActionInvoked", GLib.Variant("(us)", (n.id, key)))
        if not n.resident:
            self.close(n, REASON_DISMISSED)

    def mark_read(self) -> None:
        for n in self.items:
            n.read = True
        self.notify()

    def clear_all(self) -> None:
        for n in list(self.items):
            self.close(n, REASON_DISMISSED)

    def unread_count(self) -> int:
        return sum(1 for n in self.items if not n.read)

    def set_dnd(self, enabled: bool) -> None:
        self.dnd = enabled
        self.shell.config.set("notifications", "do_not_disturb", "true" if enabled else "false")
        try:
            self.shell.config.save()
        except OSError as exc:
            log.warning("cannot save notification setting: %s", exc)
        self.notify()


class Toast(Glass):
    def __init__(self, stack: "ToastStack", n: Notification) -> None:
        super().__init__(stack.blur, stack.origin, radius=20, orientation=Gtk.Orientation.VERTICAL, spacing=8)
        add_class(self, "toast")
        self.stack = stack
        self.notification = n
        self.set_size_request(CARD_WIDTH, -1)
        top = Gtk.Box(spacing=12)
        icon = n.icon_widget(28)
        icon.set_valign(Gtk.Align.START)
        top.pack_start(icon, False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        if n.summary and n.app_name and n.app_name != n.summary:
            texts.pack_start(label(n.app_name, css=("notif-app",), xalign=0, ellipsize=True), False, False, 0)
        head = Gtk.Box(spacing=6)
        head.pack_start(label(n.summary or n.app_name, css=("toast-summary",), xalign=0, ellipsize=True), True, True, 0)
        texts.pack_start(head, False, False, 0)
        if n.body:
            body = Gtk.Label()
            body.set_markup(body_markup(n.body))
            add_class(body, "toast-body")
            body.set_xalign(0)
            body.set_line_wrap(True)
            body.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            body.set_lines(3)
            body.set_ellipsize(Pango.EllipsizeMode.END)
            body.set_max_width_chars(44)
            texts.pack_start(body, False, False, 0)
        top.pack_start(texts, True, True, 0)
        close = flat_button(icon_image("window-close-symbolic", 16), css=("toast-close",))
        close.set_valign(Gtk.Align.START)
        close.connect("clicked", lambda _b: stack.service.close(n, REASON_DISMISSED))
        top.pack_end(close, False, False, 0)
        self.pack_start(top, False, False, 0)
        visible_actions = [(k, lbl) for k, lbl in n.actions if k != "default"]
        if visible_actions:
            row = Gtk.Box(spacing=6)
            row.set_halign(Gtk.Align.END)
            for key, text in visible_actions[:3]:
                btn = flat_button(label(text), css=("toast-action",))
                btn.connect("clicked", lambda _b, k=key: stack.service.invoke(n, k))
                row.pack_start(btn, False, False, 0)
            self.pack_start(row, False, False, 0)
        self.button = Gtk.EventBox()
        self.button.set_visible_window(False)
        self.button.connect("button-press-event", self._pressed)
        self._timeout = 0

    def _pressed(self, _w, event) -> bool:
        if event.button == 1:
            n = self.notification
            if any(k == "default" for k, _ in n.actions):
                self.stack.service.invoke(n, "default")
            else:
                n.read = True
                self.stack.remove(n)
                self.stack.service.notify()
            return True
        return False


class ToastStack:
    def __init__(self, shell, service: NotificationService) -> None:
        self.shell = shell
        self.service = service
        self.toasts: list[Toast] = []
        self.window = make_layer_window(
            namespace="genesis-notifications", layer=GtkLayerShell.Layer.OVERLAY,
            anchors=(GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.RIGHT), margins={GtkLayerShell.Edge.TOP: 60, GtkLayerShell.Edge.RIGHT: 12 - TOAST_MARGIN[1]},
            exclusive=False, keyboard=GtkLayerShell.KeyboardMode.NONE,
        )
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)  # the wrapper margins provide the gap
        self.window.add(self.box)
        self.monitor = None

    def _reposition(self) -> None:
        if self.shell.panel_position() == "bottom":
            GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.TOP, False)
            GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.BOTTOM, True)
            GtkLayerShell.set_margin(self.window, GtkLayerShell.Edge.BOTTOM, self.shell.panel_thickness() + 10 - TOAST_MARGIN[2])
        else:
            GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.BOTTOM, False)
            GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.TOP, True)
            GtkLayerShell.set_margin(self.window, GtkLayerShell.Edge.TOP, self.shell.panel_thickness() + 10 - TOAST_MARGIN[0])

    def find(self, nid: int) -> Notification | None:
        return next((t.notification for t in self.toasts if t.notification.id == nid), None)

    # -- geometry for the glass backdrop of each toast -----------------------------------
    def blur(self):
        return None if self.monitor is None else self.shell.wallpaper.blurred_surface(self.monitor)

    def origin(self) -> tuple[int, int]:
        geo = self.monitor.get_geometry()
        w, h = self.window.get_allocated_width(), self.window.get_allocated_height()
        x = geo.width - (12 - TOAST_MARGIN[1]) - w
        if self.shell.panel_position() == "bottom":
            return (x, geo.height - (self.shell.panel_thickness() + 10 - TOAST_MARGIN[2]) - h)
        return (x, self.shell.panel_thickness() + 10 - TOAST_MARGIN[0])

    def show(self, n: Notification) -> None:
        self.remove(n)
        toast = Toast(self, n)
        wrapper = toast.button
        wrapper.add(toast)
        # margins lie outside the Glass allocation: the blur clips to the rounded toast and the shadow has room
        wrapper.set_margin_top(TOAST_MARGIN[0])
        wrapper.set_margin_end(TOAST_MARGIN[1])
        wrapper.set_margin_bottom(TOAST_MARGIN[2])
        wrapper.set_margin_start(TOAST_MARGIN[3])
        self.toasts.append(toast)
        self.box.pack_start(wrapper, False, False, 0)
        while len(self.toasts) > MAX_TOASTS:
            self.remove(self.toasts[0].notification)
        timeout = n.timeout if n.timeout > 0 else (0 if n.urgency >= 2 else self.shell.config.getint("notifications", "timeout") * 1000)
        if n.timeout == 0:
            timeout = 0
        if timeout > 0:
            toast._timeout = GLib.timeout_add(timeout, self._expire, n)
        if not self.window.get_visible():
            self._reposition()
            mon = self.shell.current_monitor()
            if mon is not None:
                GtkLayerShell.set_monitor(self.window, mon)
                self.monitor = mon
        self.window.show_all()

    def _expire(self, n: Notification) -> bool:
        self.remove(n)
        if n.transient and self.service.find(n.id) is None:
            self.service._emit("NotificationClosed", GLib.Variant("(uu)", (n.id, REASON_EXPIRED)))
        return False

    def remove(self, n: Notification) -> None:
        for toast in list(self.toasts):
            if toast.notification.id == n.id:
                if toast._timeout:
                    GLib.source_remove(toast._timeout)
                    toast._timeout = 0
                self.toasts.remove(toast)
                parent = toast.get_parent()
                if parent is not None:
                    self.box.remove(parent)
                    parent.destroy()
        if not self.toasts:
            self.window.hide()


class NotificationList(Gtk.Box):
    """Rows for the notification center; rebuilt from the service on change."""

    def __init__(self, shell) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.shell = shell
        header = Gtk.Box(spacing=8)
        header.pack_start(label("Notifications", css=("title",), xalign=0), True, True, 0)
        self.dnd_switch = Gtk.Switch()
        self.dnd_switch.set_tooltip_text("Do Not Disturb")
        self._syncing = False
        self.dnd_switch.connect("state-set", self._on_dnd_switch)
        header.pack_end(self.dnd_switch, False, False, 0)
        header.pack_end(label("Do Not Disturb", css=("subtitle",)), False, False, 0)
        self.pack_start(header, False, False, 0)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.rows.set_vexpand(True)
        scrolled = Gtk.ScrolledWindow()
        add_class(scrolled, "notif-list")
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_max_content_height(420)
        scrolled.add(self.rows)
        self.pack_start(scrolled, True, True, 0)
        self.clear = flat_button(label("Clear all"), css=("link-button",))
        self.clear.set_halign(Gtk.Align.END)
        self.clear.connect("clicked", lambda _b: shell.notifications.clear_all())
        self.pack_end(self.clear, False, False, 0)
        shell.notifications.connect(self._refresh_if_shown)  # NotificationCenter.on_shown refreshes on open
        self.refresh()

    def _refresh_if_shown(self) -> None:
        if self.get_mapped():
            self.refresh()

    def _on_dnd_switch(self, _switch, state) -> bool:
        if not self._syncing and bool(state) != self.shell.notifications.dnd:
            self.shell.notifications.set_dnd(bool(state))
        return False

    def refresh(self) -> None:
        svc = self.shell.notifications
        self._syncing = True
        try:
            self.dnd_switch.set_state(svc.dnd)
            self.dnd_switch.set_active(svc.dnd)
        finally:
            self._syncing = False
        for child in self.rows.get_children():
            self.rows.remove(child)
        if not svc.items:
            self.rows.pack_start(empty_state("No notifications"), True, True, 0)
        for n in svc.items:
            row = Gtk.Box(spacing=10)
            add_class(row, "notif-row")
            row.pack_start(n.icon_widget(24), False, False, 0)
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            if n.app_name and n.app_name != n.summary:
                texts.pack_start(label(n.app_name, css=("notif-app",), xalign=0, ellipsize=True), False, False, 0)
            head = Gtk.Box(spacing=6)
            head.pack_start(label(n.summary or n.app_name, css=("toast-summary",), xalign=0, ellipsize=True), True, True, 0)
            head.pack_end(label(n.age_text(), css=("notif-time",)), False, False, 0)
            texts.pack_start(head, False, False, 0)
            if n.body:
                body = Gtk.Label()
                body.set_markup(body_markup(n.body))
                add_class(body, "toast-body")
                body.set_xalign(0)
                body.set_line_wrap(True)
                body.set_lines(2)
                body.set_ellipsize(Pango.EllipsizeMode.END)
                body.set_max_width_chars(40)
                texts.pack_start(body, False, False, 0)
            row.pack_start(texts, True, True, 0)
            close = flat_button(icon_image("window-close-symbolic", 16), css=("toast-close",))
            close.set_valign(Gtk.Align.START)
            close.connect("clicked", lambda _b, item=n: svc.close(item, REASON_DISMISSED))
            row.pack_end(close, False, False, 0)
            if n.desktop_entry:
                row_button = Gtk.EventBox()
                row_button.set_visible_window(False)
                row_button.add(row)
                row_button.connect("button-press-event", lambda _w, _e, item=n: self._open(item))
                self.rows.pack_start(row_button, False, False, 0)
            else:
                self.rows.pack_start(row, False, False, 0)
        self.clear.set_visible(bool(svc.items))
        self.rows.show_all()

    def _open(self, n: Notification) -> bool:
        if any(k == "default" for k, _ in n.actions):
            self.shell.notifications.invoke(n, "default")
            return True
        info = desktop_app_info(n.desktop_entry)
        if info is not None:
            launch_app(info)
            self.shell.notification_center.hide()
        return True


class NotificationCenter(OverlayWindow):
    """Clock popover: calendar on the left, notifications on the right."""

    layer = GtkLayerShell.Layer.OVERLAY
    keyboard = GtkLayerShell.KeyboardMode.ON_DEMAND

    def build(self) -> Gtk.Widget:
        glass = Glass(self.blur, self.origin, radius=20, orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        add_class(glass, "card")
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.date_label = label("", css=("title", "date-title"), xalign=0)
        self.date_label.set_valign(Gtk.Align.CENTER)
        left.pack_start(self.date_label, False, False, 0)
        self.calendar = Gtk.Calendar()
        self.calendar.set_property("show-week-numbers", False)
        left.pack_start(self.calendar, False, False, 0)
        glass.pack_start(left, False, False, 0)
        self.list = NotificationList(self.shell)
        self.list.set_size_request(360, -1)
        glass.pack_start(self.list, True, True, 0)
        return glass

    def _place_near_panel(self) -> None:
        if self.shell.panel_position() == "bottom":
            self.place(Gtk.Align.CENTER, Gtk.Align.END, bottom=self.shell.panel_thickness() + 10)
        else:
            self.place(Gtk.Align.CENTER, Gtk.Align.START, top=self.shell.panel_thickness() + 10)

    def on_shown(self) -> None:
        self._place_near_panel()
        self.date_label.set_text(time.strftime("%A, %d %B %Y"))
        now = time.localtime()
        self.calendar.select_month(now.tm_mon - 1, now.tm_year)
        self.calendar.select_day(now.tm_mday)
        self.list.refresh()

    def on_hidden(self) -> None:
        self.shell.notifications.mark_read()
