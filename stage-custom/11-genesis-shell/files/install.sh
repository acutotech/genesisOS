#!/bin/bash
# Install Genesis Shell and the Genesis labwc session into a root filesystem.
#
#   install.sh --root DIR [--skel DIR] [--user-home DIR] [--wallpaper NAME]
#              [--launchers "ids"] [--position top|bottom] [--accent "#rrggbb"]
#              [--blank-timeout SECONDS] [--branding DIR]
#   install.sh --postinstall
#
# The first form copies files (run from the build host with DIR = the image
# root, or on a live system with --root /). The second form runs inside the
# target (chroot or live system): it generates the Wayland protocol bindings,
# byte-compiles the shell and refreshes icon/schema caches.
#
# Used by stage-custom/11-genesis-shell/00-run.sh (image build) and by the
# CI preview job (debian:trixie container), so both install exactly the same
# way. --branding installs the Genesis icons and wallpapers from
# stage-custom/10-branding/files for environments without that stage.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT=""
ROOT_SET=0
SKEL=""
USER_HOME=""
WALLPAPER="genesis-horizon"
LAUNCHERS="chromium code pcmanfm lxterminal genesis-settings"
POSITION="top"
ACCENT="#4f8cff"
BLANK_TIMEOUT="600"
BRANDING=""
MODE="install"

usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
	case "$1" in
		--root) ROOT="${2%/}"; ROOT_SET=1; shift 2 ;;  # "/" becomes "" so "$ROOT/usr" is "/usr"
		--skel) SKEL="$2"; shift 2 ;;
		--user-home) USER_HOME="$2"; shift 2 ;;
		--wallpaper) WALLPAPER="$2"; shift 2 ;;
		--launchers) LAUNCHERS="$2"; shift 2 ;;
		--position) POSITION="$2"; shift 2 ;;
		--accent) ACCENT="$2"; shift 2 ;;
		--blank-timeout) BLANK_TIMEOUT="$2"; shift 2 ;;
		--branding) BRANDING="$2"; shift 2 ;;
		--postinstall) MODE="postinstall"; shift ;;
		-h | --help) usage; exit 0 ;;
		*) echo "install.sh: unknown argument $1" >&2; usage >&2; exit 64 ;;
	esac
done

log() { echo "genesis-shell install: $*"; }

# ---------------------------------------------------------------------------------
postinstall() {
	local xml=/usr/share/wayland/wayland.xml
	local proto=/usr/share/genesis-shell/protocols/wlr-foreign-toplevel-management-unstable-v1.xml
	local out=/usr/lib/genesis-shell/genesis_shell/protocols
	local scanner=""
	if [ ! -f "$xml" ]; then
		echo "WARNING: $xml missing (install libwayland-dev); the window list will be disabled" >&2
	else
		if command -v pywayland-scanner > /dev/null 2>&1; then
			scanner="pywayland-scanner"
		elif python3 -c 'import pywayland.scanner' 2> /tmp/genesis-scanner.err; then
			scanner="python3 -m pywayland.scanner"
		else
			echo "WARNING: pywayland scanner cannot be imported; the window list will be disabled:" >&2
			cat /tmp/genesis-scanner.err >&2 || true
		fi
		rm -f /tmp/genesis-scanner.err
	fi
	if [ -n "$scanner" ]; then
		rm -rf "$out"
		mkdir -p "$out"
		# shellcheck disable=SC2086  # $scanner may be two words
		if $scanner -i "$xml" "$proto" -o "$out"; then
			: > "$out/__init__.py"
			log "generated Wayland protocol bindings in $out: $(find "$out" -mindepth 1 -maxdepth 1 -type d -printf '%f ')"
		else
			echo "WARNING: $scanner failed; the window list will be disabled" >&2
			rm -rf "$out"
		fi
	fi
	python3 -m compileall -q /usr/lib/genesis-shell || true
	if command -v glib-compile-schemas > /dev/null 2>&1 && [ -d /usr/share/glib-2.0/schemas ]; then
		glib-compile-schemas /usr/share/glib-2.0/schemas || true
	fi
	if command -v gtk-update-icon-cache > /dev/null 2>&1; then
		for theme in hicolor Genesis Papirus-Dark; do
			if [ -f "/usr/share/icons/$theme/index.theme" ]; then
				gtk-update-icon-cache -q -f -t "/usr/share/icons/$theme" || true
			fi
		done
	fi
	if command -v update-desktop-database > /dev/null 2>&1; then
		update-desktop-database -q /usr/share/applications || true
	fi
	if command -v fc-cache > /dev/null 2>&1; then
		fc-cache -f > /dev/null 2>&1 || true
	fi
	log "postinstall done"
}

if [ "$MODE" = "postinstall" ]; then
	postinstall
	exit 0
fi

[ "$ROOT_SET" = 1 ] || { echo "install.sh: --root is required" >&2; exit 64; }
[ -d "${ROOT:-/}" ] || { echo "install.sh: ${ROOT} is not a directory" >&2; exit 66; }

inst() { # mode source destination-in-root
	install -D -m "$1" "$2" "$ROOT$3"
}

# ---- the shell package -------------------------------------------------------------
rm -rf "$ROOT/usr/lib/genesis-shell/genesis_shell"
install -d -m 755 "$ROOT/usr/lib/genesis-shell" "$ROOT/usr/share/genesis-shell/protocols"
cp -a "$HERE/genesis-shell/genesis_shell" "$ROOT/usr/lib/genesis-shell/"
find "$ROOT/usr/lib/genesis-shell" -name __pycache__ -type d -prune -exec rm -rf {} +
chmod -R u=rwX,go=rX "$ROOT/usr/lib/genesis-shell"
inst 644 "$HERE/genesis-shell/protocols/wlr-foreign-toplevel-management-unstable-v1.xml" /usr/share/genesis-shell/protocols/wlr-foreign-toplevel-management-unstable-v1.xml
inst 755 "$HERE/install.sh" /usr/share/genesis-shell/install.sh
inst 755 "$HERE/preview.sh" /usr/share/genesis-shell/preview.sh

# ---- session programs ----------------------------------------------------------------
for prog in genesis-session genesis-session-start genesis-respawn genesis-fallback genesis-shell genesis-shell-ctl genesis-xdg-autostart genesis-idle; do
	inst 755 "$HERE/session/$prog" "/usr/bin/$prog"
done
inst 644 "$HERE/session/genesis.desktop" /usr/share/wayland-sessions/genesis.desktop

# ---- labwc configuration and theme ------------------------------------------------------
for f in rc.xml menu.xml autostart; do
	inst 644 "$HERE/labwc/$f" "/etc/xdg/genesis/labwc/$f"
done
cursor="Adwaita"
[ -d "$ROOT/usr/share/icons/PiXflat/cursors" ] && cursor="PiXflat"
xwayland_line=""
[ -x "$ROOT/usr/bin/xwayland-xauth" ] && xwayland_line="WLR_XWAYLAND=/usr/bin/xwayland-xauth"
sed -e "s#@CURSOR_THEME@#${cursor}#" -e "s#@XWAYLAND_LINE@#${xwayland_line}#" "$HERE/labwc/environment.in" > "$ROOT/etc/xdg/genesis/labwc/environment"
chmod 644 "$ROOT/etc/xdg/genesis/labwc/environment"
inst 644 "$HERE/genesis-portals.conf" /usr/share/xdg-desktop-portal/genesis-portals.conf

# ---- shell defaults -----------------------------------------------------------------------
install -d -m 755 "$ROOT/etc/genesis"
case "$ACCENT" in
	\#[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]) ;;
	*) echo "install.sh: --accent must be #rrggbb, got '$ACCENT'" >&2; exit 64 ;;
esac
# labwc >= 0.8 reads themes/<name>/labwc/, 0.6-0.7 only themes/<name>/openbox-3/; ship both with the accent substituted
for d in labwc openbox-3; do
	install -d -m 755 "$ROOT/usr/share/themes/GenesisGlass/$d"
	sed -e "s/#4f8cff/${ACCENT}/g" "$HERE/labwc/themerc" > "$ROOT/usr/share/themes/GenesisGlass/$d/themerc"
	chmod 644 "$ROOT/usr/share/themes/GenesisGlass/$d/themerc"
done
sed -e "s|@WALLPAPER@|${WALLPAPER}|" -e "s|@PANEL_POSITION@|${POSITION}|" -e "s|@LAUNCHERS@|${LAUNCHERS}|" \
	-e "s|@ACCENT@|${ACCENT}|" -e "s|@BLANK_TIMEOUT@|${BLANK_TIMEOUT}|" "$HERE/shell.conf.in" > "$ROOT/etc/genesis/shell.conf"
chmod 644 "$ROOT/etc/genesis/shell.conf"
printf 'genesis\n' > "$ROOT/etc/genesis/session"

# ---- GTK theme for applications -----------------------------------------------------------------
theme="$ROOT/usr/share/themes/GenesisGlass"
install -d -m 755 "$theme/gtk-3.0" "$theme/gtk-2.0"
{
	printf '@import url("resource:///org/gtk/libgtk/theme/Adwaita/gtk-contained-dark.css");\n\n'
	sed -e "s/#4f8cff/${ACCENT}/g" "$HERE/gtk-apps.css"
} > "$theme/gtk-3.0/gtk.css"
cp "$theme/gtk-3.0/gtk.css" "$theme/gtk-3.0/gtk-dark.css"
printf 'gtk-color-scheme = "selected_bg_color:%s"\n' "$ACCENT" > "$theme/gtk-2.0/gtkrc"
chmod 644 "$theme"/gtk-3.0/*.css "$theme/gtk-2.0/gtkrc"
inst 644 "$HERE/settings.ini" /etc/gtk-3.0/settings.ini
inst 644 "$HERE/90_genesis.gschema.override" /usr/share/glib-2.0/schemas/90_genesis.gschema.override
inst 644 "$HERE/xsettingsd.conf" /etc/xsettingsd/xsettingsd.conf

# ---- optional branding (icons, wallpapers) for environments without stage 10 ----------------------
if [ -n "$BRANDING" ]; then
	[ -d "$BRANDING" ] || { echo "install.sh: --branding $BRANDING is not a directory" >&2; exit 66; }
	if [ -d "$BRANDING/icons/hicolor" ]; then
		install -d -m 755 "$ROOT/usr/share/icons/hicolor"
		cp -a "$BRANDING/icons/hicolor/." "$ROOT/usr/share/icons/hicolor/"
	fi
	if [ -d "$BRANDING/wallpapers" ]; then
		install -d -m 755 "$ROOT/usr/share/genesis/wallpapers"
		cp -a "$BRANDING/wallpapers/." "$ROOT/usr/share/genesis/wallpapers/"
	fi
	if [ ! -f "$ROOT/usr/share/icons/Genesis/index.theme" ]; then
		install -d -m 755 "$ROOT/usr/share/icons/Genesis/48x48/places"
		if [ -f "$ROOT/usr/share/icons/hicolor/48x48/apps/genesis-os.png" ]; then
			cp "$ROOT/usr/share/icons/hicolor/48x48/apps/genesis-os.png" "$ROOT/usr/share/icons/Genesis/48x48/places/start-here.png"
		fi
		printf '[Icon Theme]\nName=Genesis\nComment=Genesis OS icons\nInherits=Papirus-Dark,Adwaita,hicolor\nDirectories=48x48/places\n\n[48x48/places]\nSize=48\nContext=Places\nType=Fixed\n' > "$ROOT/usr/share/icons/Genesis/index.theme"
	fi
fi

# Prefer Papirus-Dark icons for applications when the package is present.
index="$ROOT/usr/share/icons/Genesis/index.theme"
if [ -f "$index" ] && [ -d "$ROOT/usr/share/icons/Papirus-Dark" ] && ! grep -q '^Inherits=Papirus-Dark' "$index"; then
	sed -i -e 's/^Inherits=/Inherits=Papirus-Dark,/' "$index"
fi

# ---- per-user defaults -----------------------------------------------------------------------------
for base in "$SKEL" "$USER_HOME"; do
	[ -n "$base" ] || continue
	install -d -m 755 "$base/.config/gtk-3.0" "$base/.config/lxterminal" "$base/.config/xsettingsd" "$base/.config/genesis" "$base/Pictures"
	install -m 644 "$HERE/settings.ini" "$base/.config/gtk-3.0/settings.ini"
	install -m 644 "$HERE/lxterminal.conf" "$base/.config/lxterminal/lxterminal.conf"
	install -m 644 "$HERE/xsettingsd.conf" "$base/.config/xsettingsd/xsettingsd.conf"
	# leftovers from the stock desktop that would fight the shell
	rm -rf "$base/.config/wf-panel-pi" "$base/.config/pcmanfm/LXDE-pi/desktop-items-0.conf"
done

log "installed into ${ROOT:-/} (wallpaper=$WALLPAPER, position=$POSITION, accent=$ACCENT)"
