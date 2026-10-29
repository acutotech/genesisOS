#!/bin/bash -e
# Raspberry Pi 5 bootloader (EEPROM) settings.
#
# The EEPROM is a chip on each Pi, not part of the image, so nothing is flashed
# here: the knobs from custom.conf become /etc/genesis/bootloader.conf, and
# genesis-eeprom.service applies them once on the Pi (see files/genesis-eeprom).

# shellcheck source=../lib.sh
source "${STAGE_DIR}/lib.sh"

fail() { echo "15-bootloader: $*" >&2; exit 1; }
one_of() { # value, name, allowed...
	local value=$1 name=$2 allowed
	shift 2
	for allowed in "$@"; do [ "${value}" = "${allowed}" ] && return 0; done
	fail "${name}=${value} is not one of: $*"
}
one_of "${EEPROM_CUSTOMIZE}" EEPROM_CUSTOMIZE 0 1
one_of "${EEPROM_BOOT_FIRST}" EEPROM_BOOT_FIRST auto sd nvme usb keep
one_of "${EEPROM_PCIE_PROBE}" EEPROM_PCIE_PROBE 0 1 keep
one_of "${EEPROM_NET_INSTALL_AT_POWER_ON}" EEPROM_NET_INSTALL_AT_POWER_ON 0 1 keep
one_of "${EEPROM_POWER_OFF_ON_HALT}" EEPROM_POWER_OFF_ON_HALT 0 1 keep

install -D -m 755 files/genesis-eeprom "${ROOTFS_DIR}/usr/lib/genesis/genesis-eeprom"
install -d -m 755 "${ROOTFS_DIR}/usr/sbin" "${ROOTFS_DIR}/etc/genesis"
ln -sf /usr/lib/genesis/genesis-eeprom "${ROOTFS_DIR}/usr/sbin/genesis-eeprom"
install -D -m 644 files/genesis-eeprom.service "${ROOTFS_DIR}/usr/lib/systemd/system/genesis-eeprom.service"
install -D -m 644 files/rpi-eeprom-update-genesis.conf \
	"${ROOTFS_DIR}/usr/lib/systemd/system/rpi-eeprom-update.service.d/genesis-eeprom.conf"
install -D -m 644 files/bootloader.txt "${ROOTFS_DIR}/usr/share/doc/genesis/bootloader.txt"
install -d -m 755 "${ROOTFS_DIR}/var/lib/genesis/eeprom"

cat > "${ROOTFS_DIR}/etc/genesis/bootloader.conf" << EOF
# Genesis OS bootloader settings (Raspberry Pi 5 EEPROM), applied once by
# genesis-eeprom.service on the first boot of a Pi 5. "keep" leaves a setting as
# the bootloader has it. After editing, apply with: sudo genesis-eeprom apply --again
# Undo before the next reboot with: sudo genesis-eeprom cancel
# enabled=0: nothing happens unless a boot medium is chosen in Genesis Settings
enabled=${EEPROM_CUSTOMIZE}
# medium tried first: auto (the one this system booted from) | sd | nvme | usb | keep
boot_first=${EEPROM_BOOT_FIRST}
pcie_probe=${EEPROM_PCIE_PROBE}
net_install_at_power_on=${EEPROM_NET_INSTALL_AT_POWER_ON}
power_off_on_halt=${EEPROM_POWER_OFF_ON_HALT}
EOF
chmod 644 "${ROOTFS_DIR}/etc/genesis/bootloader.conf"

# Always enabled: with enabled=0 it does nothing, but a medium chosen later in Genesis
# Settings still gets its result checked at the next boot.
on_chroot << EOF
systemctl enable genesis-eeprom.service
EOF
if [ "${EEPROM_CUSTOMIZE}" = "1" ]; then
	log "Bootloader settings: first=${EEPROM_BOOT_FIRST} pcie_probe=${EEPROM_PCIE_PROBE} net_install_at_power_on=${EEPROM_NET_INSTALL_AT_POWER_ON} power_off_on_halt=${EEPROM_POWER_OFF_ON_HALT}"
else
	log "Bootloader settings: off (EEPROM_CUSTOMIZE=0)"
fi
