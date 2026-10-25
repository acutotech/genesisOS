#!/bin/bash -e
# Start from the previous stage's root filesystem (pi-gen convention).

if [ ! -d "${ROOTFS_DIR}" ]; then
	copy_previous
fi

# pi-gen keeps a full rootfs copy per stage (several GB each for the desktop
# stages) and only reads the copies of stages it exports. Once this stage has
# its own copy, drop the others to keep the build inside a CI runner's disk.
# shellcheck source=custom.conf
source "$(dirname "${BASH_SOURCE[0]}")/custom.conf"
if [ "${GENESIS_PRUNE_WORK}" = "1" ] && [ -n "${WORK_DIR:-}" ]; then
	self="$(realpath "${ROOTFS_DIR}")"
	# shellcheck disable=SC2086  # STAGE_LIST is a space-separated list (pi-gen convention)
	for stage_dir in ${STAGE_LIST:-}; do
		# build-local.sh lists the upstream stages by relative name (stage0 ...),
		# pi-gen resolves them against its own directory; this script runs from ours
		case "${stage_dir}" in
			/*) ;;
			*) stage_dir="${BASE_DIR:-.}/${stage_dir}" ;;
		esac
		[ -d "${stage_dir}" ] || continue
		rootfs="${WORK_DIR}/$(basename "${stage_dir}")/rootfs"
		[ -d "${rootfs}" ] || continue
		[ "$(realpath "${rootfs}")" = "${self}" ] && continue
		if [ -f "${stage_dir}/EXPORT_IMAGE" ] && [ ! -f "${stage_dir}/SKIP_IMAGES" ]; then
			continue   # pi-gen exports this stage too; it needs its rootfs
		fi
		if awk '{print $2}' /proc/mounts | grep -q "^$(realpath "${rootfs}")"; then
			log "Not pruning ${rootfs}: still has mount points"
			continue
		fi
		log "Pruning ${rootfs} (not exported; frees $(du -sh --apparent-size "${rootfs}" 2> /dev/null | cut -f1))"
		rm -rf "${rootfs}"
	done
fi
