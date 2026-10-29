#!/bin/bash -e
# System defaults applied inside the image: hardware interfaces (raspi-config),
# VNC, default browser, boot-to-desktop with autologin, the Genesis session and
# the boot splash. Runs after the desktop and the glass theme.
#
# The knobs are evaluated here, on the host, so the chroot only runs what
# applies to this build (and the dry-run log shows exactly that).

# shellcheck source=../lib.sh
source "${STAGE_DIR}/lib.sh"

onoff() { [ "$1" = "1" ] && echo 0 || echo 1; }   # raspi-config: 0 = enable, 1 = disable
has_unit() { [ -f "${ROOTFS_DIR}/lib/systemd/system/$1" ] || [ -f "${ROOTFS_DIR}/usr/lib/systemd/system/$1" ]; }

I2C=$(onoff "${ENABLE_I2C}")
SPI=$(onoff "${ENABLE_SPI}")
SERIAL_HW=$(onoff "${ENABLE_SERIAL_PORT}")
SERIAL_CONS=$(onoff "${ENABLE_SERIAL_CONSOLE}")
ONEWIRE=$(onoff "${ENABLE_ONEWIRE}")
SESSION="$(cat "${ROOTFS_DIR}/etc/genesis/session" 2> /dev/null || echo rpd)"

# --- Hardware interfaces: raspi-config edits config.txt, /etc/modules, cmdline.txt -----
on_chroot << EOF
set -e
export SUDO_USER="${FIRST_USER_NAME}"
rc() { raspi-config nonint "\$@"; }
# raspi-config also pokes the live hardware (dtparam, modprobe), which fails in
# a chroot; the config files are what matter, so verify those.
rc do_i2c ${I2C} || true;         [ "\$(rc get_i2c)" = "${I2C}" ] || { echo "I2C setting not applied"; exit 1; }
rc do_spi ${SPI} || true;         [ "\$(rc get_spi)" = "${SPI}" ] || { echo "SPI setting not applied"; exit 1; }
rc do_serial_hw ${SERIAL_HW} || true
rc do_serial_cons ${SERIAL_CONS} || true; [ "\$(rc get_serial_cons)" = "${SERIAL_CONS}" ] || { echo "serial console setting not applied"; exit 1; }
rc do_onewire ${ONEWIRE} || true; [ "\$(rc get_onewire)" = "${ONEWIRE}" ] || { echo "1-Wire setting not applied"; exit 1; }
EOF

# --- VNC: wayvnc as a service (raspi-config's own toggle needs a running labwc) --------
if [ "${ENABLE_VNC}" = "1" ]; then
	if has_unit wayvnc.service; then
		on_chroot << EOF
systemctl enable wayvnc.service
EOF
	else
		log "WARNING: wayvnc.service not found; VNC not enabled"
	fi
fi

# --- Default browser ------------------------------------------------------------------
if [ -x "${ROOTFS_DIR}/usr/bin/${DEFAULT_BROWSER}" ]; then
	on_chroot << EOF
update-alternatives --set x-www-browser "/usr/bin/${DEFAULT_BROWSER}" > /dev/null || true
EOF
else
	log "WARNING: /usr/bin/${DEFAULT_BROWSER} not found; x-www-browser alternative left unchanged"
fi

# --- Desktop: boot to the greeter, autologin, Genesis session ----------------------------
# B4 = desktop with autologin, B3 = desktop with login prompt. Same call the
# first-boot user rename (userconf-pi) makes, so a renamed user keeps the setting.
if has_unit lightdm.service; then
	if [ "${DESKTOP_AUTOLOGIN}" = "1" ]; then
		BOOT_BEHAVIOUR=B4
		AUTOLOGIN_FIX="sed -i -e 's/^#\\?autologin-user=.*/autologin-user=${FIRST_USER_NAME}/' /etc/lightdm/lightdm.conf; grep -q '^autologin-user=' /etc/lightdm/lightdm.conf || echo 'autologin-user=${FIRST_USER_NAME}' >> /etc/lightdm/lightdm.conf"
		AUTOLOGIN_CHECK="grep -q '^autologin-user=${FIRST_USER_NAME}\$' /etc/lightdm/lightdm.conf"
	else
		BOOT_BEHAVIOUR=B3
		AUTOLOGIN_FIX="sed -i -e 's/^autologin-user=.*/#autologin-user=/' /etc/lightdm/lightdm.conf"
		AUTOLOGIN_CHECK="! grep -q '^autologin-user=' /etc/lightdm/lightdm.conf"
	fi
	on_chroot << EOF
set -e
export SUDO_USER="${FIRST_USER_NAME}"
rc() { raspi-config nonint "\$@"; }
if ! rc do_boot_behaviour ${BOOT_BEHAVIOUR}; then
	echo "raspi-config could not set boot behaviour ${BOOT_BEHAVIOUR}; applying it directly"
	systemctl set-default graphical.target
	${AUTOLOGIN_FIX}
fi
[ "\$(rc get_boot_cli)" = "1" ] || { echo "boot to desktop not applied"; exit 1; }
${AUTOLOGIN_CHECK} || { echo "desktop autologin setting (${BOOT_BEHAVIOUR}) not applied"; exit 1; }
EOF
	if [ "${SESSION}" = "genesis" ] && [ -f "${ROOTFS_DIR}/usr/share/wayland-sessions/genesis.desktop" ]; then
		on_chroot << EOF
/usr/lib/genesis-settings/genesis-helper session genesis
EOF
	fi
else
	log "WARNING: lightdm not installed; boot behaviour left as is (Lite build?)"
fi

# --- Boot splash ------------------------------------------------------------------------
CMDLINE="${ROOTFS_DIR}/boot/firmware/cmdline.txt"
if [ "${ENABLE_BOOT_SPLASH}" = "1" ] && [ -f "${ROOTFS_DIR}/etc/plymouth/plymouthd.conf" ] && [ -f "${CMDLINE}" ]; then
	for opt in quiet splash plymouth.ignore-serial-consoles logo.nologo; do
		grep -qw -- "${opt}" "${CMDLINE}" || sed -i -e "s/\$/ ${opt}/" "${CMDLINE}"
	done
	ensure_line "${ROOTFS_DIR}/boot/firmware/config.txt" "disable_splash=1"
fi

# --- Default browser for the user (and new users): mime associations --------------------
for base in "${ROOTFS_DIR}/etc/skel" "${ROOTFS_DIR}/home/${FIRST_USER_NAME}"; do
	install -d -m 755 "${base}/.config"
	cat > "${base}/.config/mimeapps.list" << EOF
[Default Applications]
text/html=${DEFAULT_BROWSER}.desktop
x-scheme-handler/http=${DEFAULT_BROWSER}.desktop
x-scheme-handler/https=${DEFAULT_BROWSER}.desktop
x-scheme-handler/about=${DEFAULT_BROWSER}.desktop
x-scheme-handler/unknown=${DEFAULT_BROWSER}.desktop
application/pdf=${DEFAULT_BROWSER}.desktop
EOF
	chmod 644 "${base}/.config/mimeapps.list"
done
chown -R 1000:1000 "${ROOTFS_DIR}/home/${FIRST_USER_NAME}/.config"
log "Interfaces: i2c=${ENABLE_I2C} spi=${ENABLE_SPI} serial=${ENABLE_SERIAL_PORT} console=${ENABLE_SERIAL_CONSOLE} onewire=${ENABLE_ONEWIRE} vnc=${ENABLE_VNC}; session=${SESSION}; browser=${DEFAULT_BROWSER}"
