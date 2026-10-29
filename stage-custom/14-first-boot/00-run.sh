#!/bin/bash -e
# First-boot setup for Genesis Imager.
#
# The boot partition gets a setup area (genesis-setup.txt, an inert placeholder)
# that Genesis Imager fills in while flashing: user name, password hash, host
# name, Wi-Fi, time zone, keyboard, SSH. genesis-firstboot.service applies it on
# the first boot, before Raspberry Pi's first-boot wizard and the login screen,
# then restores the placeholder. The image itself carries no credentials.

# shellcheck source=../lib.sh
source "${STAGE_DIR}/lib.sh"

install -D -m 755 files/genesis-firstboot "${ROOTFS_DIR}/usr/lib/genesis/genesis-firstboot"
install -D -m 644 files/genesis-firstboot.service "${ROOTFS_DIR}/usr/lib/systemd/system/genesis-firstboot.service"
install -D -m 644 files/genesis-setup.txt "${ROOTFS_DIR}/usr/share/genesis/genesis-setup-placeholder.txt"
install -D -m 644 files/genesis-setup.txt "${ROOTFS_DIR}/boot/firmware/genesis-setup.txt"

on_chroot << EOF
systemctl enable genesis-firstboot.service
EOF
log "First-boot setup area installed (boot partition: genesis-setup.txt)"
