#!/usr/bin/python3
"""Genesis Settings: interfaces, system, appearance, connectivity, display,
software and about pages for Genesis OS on Raspberry Pi.

Privileged changes go through /usr/lib/genesis-settings/genesis-helper via
sudo (passwordless, or with the Raspberry Pi askpass prompt) or pkexec.
"""
import configparser
import os
import shutil
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk, Pango  # noqa: E402

HELPER = "/usr/lib/genesis-settings/genesis-helper"
WALLPAPER_DIRS = ("/usr/share/genesis/wallpapers", "/usr/share/rpd-wallpaper")
SHELL_CONF_SYSTEM = "/etc/genesis/shell.conf"
SHELL_CONF_USER = os.path.expanduser("~/.config/genesis/shell.conf")
ACCENTS = [("Azure", "#4f8cff"), ("Violet", "#7c5cff"), ("Teal", "#2fb8a6"), ("Coral", "#ff7a59"), ("Amber", "#ffb347"), ("Mint", "#3ddc97")]
RELEASE_FILE = "/etc/custom-image-release"

CSS = b"""
window.genesis { background-color: #0e1622; color: #dfe8f0; }
.genesis-sidebar { background-color: rgba(255,255,255,0.03); border-right: 1px solid rgba(255,255,255,0.06); }
.genesis-sidebar row { padding: 10px 14px; border-radius: 10px; margin: 2px 8px; }
.genesis-sidebar row:selected { background-color: #5f7d9a; color: #ffffff; }
.card { background-color: rgba(255,255,255,0.045); border: 1px solid rgba(255,255,255,0.08);
        border-radius: 14px; padding: 6px; }
.card row { padding: 8px 10px; border-radius: 10px; }
.page-title { font-size: 20px; font-weight: 600; }
.row-title { font-weight: 600; }
.row-subtitle { opacity: 0.65; font-size: 12px; }
.banner { background-color: rgba(95,125,154,0.30); border: 1px solid rgba(138,164,189,0.4);
          border-radius: 10px; padding: 8px 12px; }
.pill { border-radius: 999px; padding: 6px 16px; }
.wallpaper { border-radius: 10px; border: 2px solid transparent; }
.wallpaper-selected { border: 2px solid #8aa4bd; }
.swatch { border-radius: 999px; min-width: 26px; min-height: 26px; padding: 0; border: 2px solid transparent; }
.swatch-selected { border: 2px solid #ffffff; }
"""

INTERFACES = [
    ("i2c", "I2C", "Inter-Integrated Circuit bus on GPIO 2/3", True),
    ("spi", "SPI", "Serial Peripheral Interface on GPIO 7-11", True),
    ("serial_hw", "Serial port", "UART on GPIO 14/15 for devices (Pi 5: uart0)", True),
    ("serial_cons", "Serial console", "Login shell on the serial port (usually off)", True),
    ("onewire", "1-Wire", "Dallas 1-Wire bus (w1-gpio overlay)", True),
    ("camera", "Camera auto-detect", "Detect CSI cameras at boot", True),
    ("ssh", "SSH server", "Remote shell access (port 22)", False),
    ("vnc", "VNC server", "Remote desktop with wayvnc (port 5900)", False),
]


def sh(cmd, timeout=20, env=None):
    """Run a command, never raise; return CompletedProcess."""
    try:
        full_env = dict(os.environ, **env) if env else None
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=full_env)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))


def spawn(cmd):
    """Launch a desktop program detached from this process."""
    try:
        subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def helper_get(item):
    out = sh([HELPER, "get", item]).stdout.strip()
    return out


def root_command(args):
    if sh(["sudo", "-n", "true"]).returncode == 0:
        return ["sudo", "-n", HELPER] + args, None
    if os.path.exists("/usr/bin/sudopwd"):
        return ["sudo", "-A", HELPER] + args, {"SUDO_ASKPASS": "/usr/bin/sudopwd"}
    return ["pkexec", HELPER] + args, None


def run_root_async(args, callback):
    """Run a helper command as root in a thread; callback(result) on the main loop."""
    cmd, env = root_command(args)

    def work():
        result = sh(cmd, timeout=180, env=env)
        GLib.idle_add(callback, result)

    threading.Thread(target=work, daemon=True).start()


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def release_info():
    info = {}
    for line in read_text(RELEASE_FILE).splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            info[key.strip()] = value.strip()
    return info


class Row(Gtk.ListBoxRow):
    def __init__(self, title, subtitle=None, widget=None):
        super().__init__(activatable=False)
        box = Gtk.Box(spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        label = Gtk.Label(label=title, xalign=0)
        label.get_style_context().add_class("row-title")
        text.pack_start(label, False, False, 0)
        if subtitle:
            sub = Gtk.Label(label=subtitle, xalign=0, wrap=True)
            sub.get_style_context().add_class("row-subtitle")
            text.pack_start(sub, False, False, 0)
        box.pack_start(text, True, True, 0)
        if widget is not None:
            widget.set_valign(Gtk.Align.CENTER)
            box.pack_end(widget, False, False, 0)
        self.add(box)


def card(rows):
    frame = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    frame.get_style_context().add_class("card")
    for row in rows:
        frame.add(row)
    return frame


def page(title, *sections):
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=24)
    heading = Gtk.Label(label=title, xalign=0)
    heading.get_style_context().add_class("page-title")
    box.pack_start(heading, False, False, 0)
    for section in sections:
        box.pack_start(section, False, False, 0)
    scroller.add(box)
    return scroller


class GenesisSettings(Gtk.Window):
    def __init__(self):
        super().__init__(title="Genesis Settings")
        self.set_default_size(960, 640)
        self.set_icon_name("genesis-settings")
        self.get_style_context().add_class("genesis")
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        header = Gtk.HeaderBar(title="Genesis Settings", show_close_button=True)
        self.set_titlebar(header)

        self.banner = Gtk.Label(label="", xalign=0, no_show_all=True)
        self.banner.get_style_context().add_class("banner")

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        sidebar = Gtk.StackSidebar(stack=self.stack)
        sidebar.get_style_context().add_class("genesis-sidebar")
        sidebar.set_size_request(200, -1)

        self.stack.add_titled(self.build_interfaces(), "interfaces", "Interfaces")
        self.stack.add_titled(self.build_system(), "system", "System")
        self.stack.add_titled(self.build_appearance(), "appearance", "Appearance")
        self.stack.add_titled(self.build_connectivity(), "connectivity", "Connectivity")
        self.stack.add_titled(self.build_display(), "display", "Display & Sound")
        self.stack.add_titled(self.build_software(), "software", "Software")
        self.stack.add_titled(self.build_about(), "about", "About")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        banner_box = Gtk.Box(margin_start=24, margin_end=24, margin_top=12)
        banner_box.pack_start(self.banner, True, True, 0)
        content.pack_start(banner_box, False, False, 0)
        content.pack_start(self.stack, True, True, 0)

        root = Gtk.Box()
        root.pack_start(sidebar, False, False, 0)
        root.pack_start(content, True, True, 0)
        self.add(root)
        self.show_all()

    # ---------------------------------------------------------------- helpers
    def show_banner(self, text):
        self.banner.set_text(text)
        self.banner.show()

    def error(self, text, detail=""):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.ERROR,
                                   buttons=Gtk.ButtonsType.CLOSE, text=text)
        if detail:
            dialog.format_secondary_text(detail.strip()[-800:])
        dialog.run()
        dialog.destroy()

    def launch(self, *candidates):
        for cmd in candidates:
            if shutil.which(cmd[0]):
                if spawn(cmd):
                    return
        self.error("Not installed", "None of these programs is available: " + ", ".join(c[0] for c in candidates))

    def toggle_switch(self, key, needs_reboot, switch, state):
        switch.set_sensitive(False)

        def done(result):
            switch.set_sensitive(True)
            if result.returncode != 0:
                switch.handler_block_by_func(self.on_interface_toggled)
                switch.set_active(not state)
                switch.handler_unblock_by_func(self.on_interface_toggled)
                self.error("Could not change %s" % key, result.stderr or result.stdout)
            elif needs_reboot:
                self.show_banner("Reboot to apply the hardware interface changes.")

        run_root_async(["set", key, "0" if state else "1"], done)

    def on_interface_toggled(self, switch, _param):
        key, needs_reboot = switch.genesis_key, switch.genesis_reboot
        self.toggle_switch(key, needs_reboot, switch, switch.get_active())

    def make_switch(self, key, needs_reboot):
        switch = Gtk.Switch()
        switch.genesis_key = key
        switch.genesis_reboot = needs_reboot
        switch.set_active(helper_get(key) == "0")
        switch.connect("notify::active", self.on_interface_toggled)
        return switch

    # ---------------------------------------------------------------- pages
    def build_interfaces(self):
        rows = [Row(title, subtitle, self.make_switch(key, reboot)) for key, title, subtitle, reboot in INTERFACES]
        hint = Gtk.Label(label="VNC is served by wayvnc and works with any VNC viewer on port 5900. "
                               "SSH and VNC accept the account password; change it under System.",
                         xalign=0, wrap=True)
        hint.get_style_context().add_class("row-subtitle")
        return page("Interfaces", card(rows), hint)

    def build_system(self):
        self.hostname_entry = Gtk.Entry(text=read_text("/etc/hostname") or "nodobox", width_chars=22)
        apply_host = Gtk.Button(label="Apply")
        apply_host.get_style_context().add_class("pill")
        apply_host.connect("clicked", self.on_hostname_apply)
        host_box = Gtk.Box(spacing=8)
        host_box.pack_start(self.hostname_entry, False, False, 0)
        host_box.pack_start(apply_host, False, False, 0)

        self.tz_entry = Gtk.Entry(text=read_text("/etc/timezone") or "Asia/Kolkata", width_chars=22)
        completion = Gtk.EntryCompletion()
        store = Gtk.ListStore(str)
        for zone in sh(["timedatectl", "list-timezones"]).stdout.split():
            store.append([zone])
        completion.set_model(store)
        completion.set_text_column(0)
        completion.set_minimum_key_length(1)
        self.tz_entry.set_completion(completion)
        apply_tz = Gtk.Button(label="Apply")
        apply_tz.get_style_context().add_class("pill")
        apply_tz.connect("clicked", self.on_timezone_apply)
        tz_box = Gtk.Box(spacing=8)
        tz_box.pack_start(self.tz_entry, False, False, 0)
        tz_box.pack_start(apply_tz, False, False, 0)

        self.locale_combo = Gtk.ComboBoxText.new_with_entry()
        current_locale = ""
        for line in read_text("/etc/default/locale").splitlines():
            if line.startswith("LANG="):
                current_locale = line.split("=", 1)[1].strip().strip('"')
        for line in read_text("/usr/share/i18n/SUPPORTED").splitlines():
            name = line.split()[0] if line.split() else ""
            if name.endswith(".UTF-8") and name.startswith(("en_", "hi_", "de_", "fr_", "es_", "ja_", "zh_", "pt_", "it_", "nl_", "ru_", "ko_", "ar_")):
                self.locale_combo.append_text(name)
        self.locale_combo.get_child().set_text(current_locale or "en_US.UTF-8")
        apply_locale = Gtk.Button(label="Apply")
        apply_locale.get_style_context().add_class("pill")
        apply_locale.connect("clicked", self.on_locale_apply)
        locale_box = Gtk.Box(spacing=8)
        locale_box.pack_start(self.locale_combo, False, False, 0)
        locale_box.pack_start(apply_locale, False, False, 0)

        identity = card([
            Row("Hostname", "Also reachable as <hostname>.local on the local network", host_box),
            Row("Time zone", "Area/City, for example Asia/Kolkata", tz_box),
            Row("Language", "System locale (log out to apply)", locale_box),
        ])

        passwd = Gtk.Button(label="Change password")
        passwd.get_style_context().add_class("pill")
        passwd.connect("clicked", lambda _b: self.launch(["lxterminal", "-e", "sh -c 'passwd; echo; echo Done. Press Enter.; read x'"],
                                                        ["x-terminal-emulator", "-e", "passwd"]))
        boot = card([
            Row("Desktop auto-login", "Log straight into the desktop at boot", self.make_switch("autologin", False)),
            Row("Boot splash", "Show the Genesis logo while booting", self.make_switch("splash", True)),
            Row("Password-less sudo", "Do not ask for the password for admin commands (less secure)",
                self.make_switch("sudo_nopass", False)),
            Row("Account password", "Change the password of the current user", passwd),
        ])

        reboot = Gtk.Button(label="Reboot")
        reboot.get_style_context().add_class("pill")
        reboot.connect("clicked", lambda _b: spawn(["systemctl", "reboot"]))
        shutdown = Gtk.Button(label="Shut down")
        shutdown.get_style_context().add_class("pill")
        shutdown.connect("clicked", lambda _b: spawn(["systemctl", "poweroff"]))
        power_box = Gtk.Box(spacing=8)
        power_box.pack_start(reboot, False, False, 0)
        power_box.pack_start(shutdown, False, False, 0)
        power = card([Row("Power", "Restart or switch off the device", power_box)])
        return page("System", identity, boot, power)

    def on_hostname_apply(self, _button):
        name = self.hostname_entry.get_text().strip()
        if not name or not all(c.isalnum() or c == "-" for c in name) or name[0] == "-" or name[-1] == "-":
            self.error("Invalid hostname", "Use letters, digits and hyphens only.")
            return
        run_root_async(["hostname", name], lambda r: self.after_change(r, "Hostname changed; reboot to apply everywhere."))

    def on_timezone_apply(self, _button):
        zone = self.tz_entry.get_text().strip()
        if not os.path.exists(os.path.join("/usr/share/zoneinfo", zone)):
            self.error("Unknown time zone", zone)
            return
        run_root_async(["timezone", zone], lambda r: self.after_change(r, "Time zone set to %s." % zone))

    def on_locale_apply(self, _button):
        loc = self.locale_combo.get_child().get_text().strip()
        run_root_async(["locale", loc], lambda r: self.after_change(r, "Locale set to %s; log out and back in." % loc))

    def after_change(self, result, success_text):
        if result.returncode != 0:
            self.error("Change failed", result.stderr or result.stdout)
        else:
            self.show_banner(success_text)

    # ---- shell configuration helpers --------------------------------------------
    @staticmethod
    def shell_conf():
        parser = configparser.ConfigParser(interpolation=None)
        parser.read([SHELL_CONF_SYSTEM, SHELL_CONF_USER], encoding="utf-8")
        return parser

    def shell_conf_set(self, section, key, value):
        """Write one key to the user shell.conf; Genesis Shell applies it live."""
        parser = self.shell_conf()
        if not parser.has_section(section):
            parser.add_section(section)
        parser.set(section, key, str(value))
        try:
            os.makedirs(os.path.dirname(SHELL_CONF_USER), exist_ok=True)
            with open(SHELL_CONF_USER, "w", encoding="utf-8") as fh:
                fh.write("# Genesis Shell settings (written by Genesis Settings).\n")
                parser.write(fh)
            return True
        except OSError as exc:
            self.error("Could not write %s" % SHELL_CONF_USER, str(exc))
            return False

    @staticmethod
    def shell_ctl(*args):
        return sh(["genesis-shell-ctl", *args], timeout=10).returncode == 0

    def build_appearance(self):
        conf = self.shell_conf()
        self.wall_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                                     homogeneous=True, row_spacing=10, column_spacing=10)
        current = self.current_wallpaper()
        seen = set()
        for directory in WALLPAPER_DIRS:
            if not os.path.isdir(directory):
                continue
            for name in sorted(os.listdir(directory)):
                path = os.path.realpath(os.path.join(directory, name))
                if path in seen or not name.lower().endswith((".jpg", ".jpeg", ".png")) or "-2560" in name:
                    continue
                seen.add(path)
                try:
                    pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 200, 112, True)
                except GLib.Error:
                    continue
                image = Gtk.Image.new_from_pixbuf(pix)
                image.get_style_context().add_class("wallpaper")
                button = Gtk.Button(relief=Gtk.ReliefStyle.NONE, tooltip_text=name)
                button.add(image)
                button.genesis_path = path
                button.connect("clicked", self.on_wallpaper_clicked)
                if path == current:
                    image.get_style_context().add_class("wallpaper-selected")
                self.wall_flow.add(button)
        browse = Gtk.Button(label="Browse…")
        browse.get_style_context().add_class("pill")
        browse.connect("clicked", self.on_wallpaper_browse)
        greeter = Gtk.Button(label="Use on login screen")
        greeter.get_style_context().add_class("pill")
        greeter.connect("clicked", self.on_greeter_wallpaper)
        wall_buttons = Gtk.Box(spacing=8)
        wall_buttons.pack_start(browse, False, False, 0)
        wall_buttons.pack_start(greeter, False, False, 0)
        wall_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        wall_box.pack_start(self.wall_flow, False, False, 0)
        wall_box.pack_start(wall_buttons, False, False, 0)
        wallpapers = card([Row("Wallpaper", "Click a wallpaper to use it on the desktop", None)])
        wallpapers.add(Gtk.ListBoxRow(activatable=False, child=wall_box))

        rows = []
        accent_now = conf.get("appearance", "accent", fallback="#4f8cff").lower()
        swatches = Gtk.Box(spacing=8)
        self.swatch_buttons = []
        for name, colour in ACCENTS:
            btn = Gtk.Button(relief=Gtk.ReliefStyle.NONE, tooltip_text=name)
            btn.get_style_context().add_class("swatch")
            provider = Gtk.CssProvider()
            provider.load_from_data(("button { background-color: %s; }" % colour).encode())
            btn.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            btn.genesis_colour = colour
            if colour == accent_now:
                btn.get_style_context().add_class("swatch-selected")
            btn.connect("clicked", self.on_accent_clicked)
            swatches.pack_start(btn, False, False, 0)
            self.swatch_buttons.append(btn)
        rows.append(Row("Accent colour", "Used by the panel, launcher, controls and applications", swatches))

        position_now = conf.get("panel", "position", fallback="top")
        pos_top = Gtk.RadioButton.new_with_label(None, "Top")
        pos_bottom = Gtk.RadioButton.new_with_label_from_widget(pos_top, "Bottom")
        (pos_bottom if position_now == "bottom" else pos_top).set_active(True)
        pos_top.connect("toggled", lambda r: r.get_active() and self.shell_conf_set("panel", "position", "top"))
        pos_bottom.connect("toggled", lambda r: r.get_active() and self.shell_conf_set("panel", "position", "bottom"))
        pos_box = Gtk.Box(spacing=12)
        pos_box.pack_start(pos_top, False, False, 0)
        pos_box.pack_start(pos_bottom, False, False, 0)
        rows.append(Row("Panel position", "Where the floating panel lives", pos_box))

        opacity = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 40, 95, 5)
        opacity.set_draw_value(False)
        opacity.set_size_request(180, -1)
        opacity.set_value(float(conf.get("appearance", "glass_opacity", fallback="0.72")) * 100)
        opacity.connect("value-changed", lambda sc: self.shell_conf_set("appearance", "glass_opacity", "%.2f" % (sc.get_value() / 100)))
        rows.append(Row("Glass opacity", "How solid the panel and cards look over the wallpaper", opacity))

        blank = Gtk.ComboBoxText()
        choices = [("0", "Never"), ("300", "5 minutes"), ("600", "10 minutes"), ("1200", "20 minutes"), ("1800", "30 minutes")]
        for value, text in choices:
            blank.append(value, text)
        blank.set_active_id(conf.get("idle", "blank_timeout", fallback="600"))
        if blank.get_active_id() is None:
            blank.set_active_id("600")
        blank.connect("changed", self.on_blank_changed)
        rows.append(Row("Blank screen after", "Turns the display off when idle", blank))

        layout = Gtk.Button(label="Edit")
        layout.get_style_context().add_class("pill")
        layout.connect("clicked", lambda _b: self.launch(["xdg-open", SHELL_CONF_USER if os.path.exists(SHELL_CONF_USER) else SHELL_CONF_SYSTEM]))
        rows.append(Row("Advanced", "Pinned apps, clock format and more in shell.conf", layout))
        return page("Appearance", wallpapers, card(rows))

    def current_wallpaper(self):
        path = self.shell_conf().get("wallpaper", "file", fallback="")
        return os.path.realpath(path) if path else ""

    def set_wallpaper(self, path):
        if not self.shell_ctl("wallpaper", path):
            # shell not running (other session): store it for the next login
            self.shell_conf_set("wallpaper", "file", path)
        for child in self.wall_flow.get_children():
            button = child.get_child()
            image = button.get_child()
            ctx = image.get_style_context()
            if getattr(button, "genesis_path", "") == path:
                ctx.add_class("wallpaper-selected")
            else:
                ctx.remove_class("wallpaper-selected")
        self.show_banner("Wallpaper set to %s" % os.path.basename(path))

    def on_wallpaper_clicked(self, button):
        self.set_wallpaper(button.genesis_path)

    def on_wallpaper_browse(self, _button):
        dialog = Gtk.FileChooserDialog(title="Choose a wallpaper", transient_for=self, action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        images = Gtk.FileFilter()
        images.set_name("Images")
        images.add_mime_type("image/jpeg")
        images.add_mime_type("image/png")
        dialog.add_filter(images)
        if dialog.run() == Gtk.ResponseType.OK:
            self.set_wallpaper(dialog.get_filename())
        dialog.destroy()

    def on_greeter_wallpaper(self, _button):
        path = self.current_wallpaper()
        if not path:
            self.error("No wallpaper configured yet")
            return
        run_root_async(["greeter-wallpaper", path], lambda r: self.after_change(r, "Login screen wallpaper updated."))

    def on_accent_clicked(self, button):
        colour = button.genesis_colour
        if not self.shell_conf_set("appearance", "accent", colour):
            return
        # applications pick the accent up from the user GTK stylesheet
        css_dir = os.path.expanduser("~/.config/gtk-3.0")
        try:
            os.makedirs(css_dir, exist_ok=True)
            with open(os.path.join(css_dir, "gtk.css"), "w", encoding="utf-8") as fh:
                fh.write("/* written by Genesis Settings */\n")
                fh.write("@define-color genesis_accent %s;\n@define-color theme_selected_bg_color %s;\n@define-color accent_bg_color %s;\n@define-color accent_color %s;\n" % (colour, colour, colour, colour))
        except OSError as exc:
            self.error("Could not write the GTK stylesheet", str(exc))
        for btn in self.swatch_buttons:
            ctx = btn.get_style_context()
            if btn is button:
                ctx.add_class("swatch-selected")
            else:
                ctx.remove_class("swatch-selected")
        self.show_banner("Accent colour updated; newly opened applications use it too.")

    def on_blank_changed(self, combo):
        value = combo.get_active_id()
        if value is None or not self.shell_conf_set("idle", "blank_timeout", value):
            return
        sh(["pkill", "-x", "swayidle"], timeout=5)
        spawn(["genesis-idle"])
        self.show_banner("Screen blanking updated.")

    def build_connectivity(self):
        wifi = Gtk.Button(label="Wi-Fi networks")
        wifi.get_style_context().add_class("pill")
        wifi.connect("clicked", lambda _b: self.launch(["genesis-shell-ctl", "control-center"], ["lp-connection-editor"], ["nm-connection-editor"]))
        edit = Gtk.Button(label="Edit connections")
        edit.get_style_context().add_class("pill")
        edit.connect("clicked", lambda _b: self.launch(["lp-connection-editor"], ["nm-connection-editor"]))
        bt = Gtk.Button(label="Bluetooth")
        bt.get_style_context().add_class("pill")
        bt.connect("clicked", lambda _b: self.launch(["genesis-shell-ctl", "control-center"], ["blueman-manager"]))

        self.country_combo = Gtk.ComboBoxText()
        current = sh(["raspi-config", "nonint", "get_wifi_country"]).stdout.strip()
        index = 0
        for i, line in enumerate(l for l in read_text("/usr/share/zoneinfo/iso3166.tab").splitlines() if l and not l.startswith("#")):
            code, _, name = line.partition("\t")
            self.country_combo.append(code, "%s  %s" % (code, name))
            if code == current:
                index = i
        self.country_combo.set_active(index)
        apply_country = Gtk.Button(label="Apply")
        apply_country.get_style_context().add_class("pill")
        apply_country.connect("clicked", self.on_country_apply)
        country_box = Gtk.Box(spacing=8)
        country_box.pack_start(self.country_combo, False, False, 0)
        country_box.pack_start(apply_country, False, False, 0)

        ips = sh(["hostname", "-I"]).stdout.strip() or "no address yet"
        return page("Connectivity", card([
            Row("Wi-Fi", "Join or forget wireless networks", wifi),
            Row("Wired and advanced", "Static addresses, VPN, hotspots", edit),
            Row("Bluetooth", "Pair audio devices, keyboards and phones", bt),
            Row("Wi-Fi country", "Sets the allowed channels; required before Wi-Fi works", country_box),
            Row("IP addresses", ips, None),
        ]))

    def on_country_apply(self, _button):
        code = self.country_combo.get_active_id()
        if code:
            run_root_async(["wifi-country", code], lambda r: self.after_change(r, "Wi-Fi country set to %s." % code))

    def build_display(self):
        screens = Gtk.Button(label="Screen layout")
        screens.get_style_context().add_class("pill")
        screens.connect("clicked", lambda _b: self.launch(["raindrop"], ["wdisplays"], ["arandr"]))
        sound = Gtk.Button(label="Sound")
        sound.get_style_context().add_class("pill")
        sound.connect("clicked", lambda _b: self.launch(["pavucontrol"]))
        return page("Display & Sound", card([
            Row("Displays", "Resolution, rotation and arrangement", screens),
            Row("Audio", "Output device, volume and input levels", sound),
        ]))

    def build_software(self):
        def button(label, *cmds):
            b = Gtk.Button(label=label)
            b.get_style_context().add_class("pill")
            b.connect("clicked", lambda _b: self.launch(*cmds))
            return b

        update_cmd = "sh -c 'sudo apt update && sudo apt full-upgrade; echo; echo Done. Press Enter.; read x'"
        rows = [
            Row("Raspberry Pi Configuration", "The full Raspberry Pi system settings tool", button("Open", ["rc_gui"])),
            Row("Recommended software", "Install curated applications", button("Open", ["rp-prefapps"])),
            Row("Update the system", "Fetch and install updates in a terminal", button("Update", ["lxterminal", "-e", update_cmd])),
            Row("Visual Studio Code", "Editor with the Python extension; python is %s" % self.python_version("python"),
                button("Open", ["code"])),
        ]
        return page("Software", card(rows))

    @staticmethod
    def python_version(binary):
        out = sh([binary, "--version"]).stdout.strip() or sh([binary, "--version"]).stderr.strip()
        return out.replace("Python ", "") or "not found"

    def build_about(self):
        info = release_info()
        model = read_text("/proc/device-tree/model").replace("\x00", "") or "unknown"
        pretty = ""
        for line in read_text("/etc/os-release").splitlines():
            if line.startswith("PRETTY_NAME="):
                pretty = line.split("=", 1)[1].strip('"')
        mem_kb = 0
        for line in read_text("/proc/meminfo").splitlines():
            if line.startswith("MemTotal:"):
                mem_kb = int(line.split()[1])
        try:
            usage = shutil.disk_usage("/")
            disk = "%.1f GB free of %.1f GB" % (usage.free / 1e9, usage.total / 1e9)
        except OSError:
            disk = "unknown"
        code_version = sh(["code", "--version"]).stdout.split("\n")[0] if shutil.which("code") else "not installed"
        rows = [
            Row("Genesis OS", "Image %s built %s" % (info.get("IMAGE_NAME", "?"), info.get("IMAGE_DATE", "?")), None),
            Row("Base", pretty or "Debian", None),
            Row("Device", model, None),
            Row("Kernel", sh(["uname", "-r"]).stdout.strip(), None),
            Row("Memory", "%.1f GB" % (mem_kb / 1e6), None),
            Row("Storage", disk, None),
            Row("Python", "python %s (default), python3 %s (system)" % (self.python_version("python"), self.python_version("python3")), None),
            Row("Visual Studio Code", code_version, None),
            Row("Session", helper_get("session") or "genesis", None),
            Row("Source", info.get("SOURCE_COMMIT", "unknown"), None),
        ]
        return page("About", card(rows))


def main():
    win = GenesisSettings()
    win.connect("destroy", Gtk.main_quit)
    if len(sys.argv) > 1 and win.stack.get_child_by_name(sys.argv[1]) is not None:
        win.stack.set_visible_child_name(sys.argv[1])
    Gtk.main()


if __name__ == "__main__":
    main()
