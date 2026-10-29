#!/bin/bash -e
# Genesis Shell and the Genesis session (labwc + our own panel, launcher,
# control center, notifications, wallpaper). The heavy lifting is in
# files/install.sh, which the CI preview job runs the same way.

# shellcheck source=../lib.sh
source "${STAGE_DIR}/lib.sh"

USER_HOME="${ROOTFS_DIR}/home/${FIRST_USER_NAME}"
SKEL="${ROOTFS_DIR}/etc/skel"

if [ ! -x "${ROOTFS_DIR}/usr/bin/labwc" ]; then
	log "WARNING: labwc is not installed; the Genesis session cannot run (Lite build?)"
fi

files/install.sh \
	--root "${ROOTFS_DIR}" --skel "${SKEL}" --user-home "${USER_HOME}" \
	--wallpaper "${GENESIS_WALLPAPER}" --launchers "${GENESIS_PANEL_LAUNCHERS}" \
	--position "${GENESIS_PANEL_POSITION}" --accent "${GENESIS_ACCENT}" --blank-timeout "${GENESIS_BLANK_TIMEOUT}"

on_chroot << EOF
set -e
/usr/share/genesis-shell/install.sh --postinstall
EOF

chown -R 1000:1000 "${USER_HOME}"
log "Genesis session installed (wallpaper ${GENESIS_WALLPAPER}, panel ${GENESIS_PANEL_POSITION}, accent ${GENESIS_ACCENT})"
