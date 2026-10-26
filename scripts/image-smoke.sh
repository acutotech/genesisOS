#!/bin/bash
# Smoke-test a built image without a Raspberry Pi, the way a user meets it:
#
#  1. Genesis Imager writes the image (to a file instead of a card) with a test
#     user, a random password, a host name, Wi-Fi, time zone, keyboard and an
#     SSH key in the setup area;
#  2. the image is mounted and its genesis-firstboot runs inside it (chroot),
#     then the account, password hash, autologin, wizard, network and locale
#     settings are checked;
#  3. the Genesis headless preview (labwc on a virtual output + Genesis Shell +
#     screenshots) runs as the renamed first user.
#
# --plain skips 1 and 2 (an image built before the setup area existed). Needs
# root and a host of the same architecture as the image (or binfmt +
# qemu-user-static), plus python3 and openssl on the host.
#
#   sudo scripts/image-smoke.sh [--plain] pi-gen/deploy/image_*.img.xz OUTPUT_DIR
set -euo pipefail

FIRSTBOOT=1
if [ "${1:-}" = "--plain" ]; then
	FIRSTBOOT=0
	shift
fi
IMAGE="${1:?usage: image-smoke.sh [--plain] IMAGE OUTPUT_DIR}"
OUT="${2:?usage: image-smoke.sh [--plain] IMAGE OUTPUT_DIR}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "${OUT}"
OUT="$(cd "${OUT}" && pwd)"

if [ "$(id -u)" != "0" ]; then
	echo "image-smoke: run as root (loop devices, mounts, chroot)" >&2
	exit 1
fi

WORK="$(mktemp -d)"
LOOP=""
MOUNTED=()

# shellcheck disable=SC2317  # invoked through the EXIT trap
cleanup() {
	set +e
	for m in "${MOUNTED[@]}"; do
		umount -l "$m" 2> /dev/null
	done
	[ -n "${LOOP}" ] && losetup -d "${LOOP}" 2> /dev/null
	rm -rf "${WORK}"
}
trap cleanup EXIT

TEST_USER=genesis-test
TEST_HOST=genesis-smoke
TEST_PASS="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20)"
TEST_PSK="smoke-test-psk-$(head -c 6 /dev/urandom | od -An -tx1 | tr -d ' \n')"
TEST_KEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGenesisSmokeTestKey00000000000000000000000 smoke@ci"

if [ "${FIRSTBOOT}" = 1 ]; then
	echo "image-smoke: writing $(basename "${IMAGE}") through Genesis Imager (user ${TEST_USER})"
	printf '%s\n' "${TEST_KEY}" > "${WORK}/test.pub"
	# every setting differs from the image's defaults, so each check proves first boot applied it;
	# the secrets go in on standard input, never on the command line
	printf '%s\n%s\n' "${TEST_PASS}" "${TEST_PSK}" | python3 "${REPO_DIR}/imager/genesis_imager.py" \
		--image "${IMAGE}" --output "${WORK}/image.img" --password-file - --wifi-password-file - \
		--user "${TEST_USER}" --hostname "${TEST_HOST}" --wifi-ssid "Genesis Smoke" \
		--wifi-country JP --timezone Asia/Tokyo --keyboard gb --ssh-key-file "${WORK}/test.pub" \
		| tr '\r' '\n' | grep -v '%$' || { echo "image-smoke: FAIL Genesis Imager could not write the image" >&2; exit 1; }
else
echo "image-smoke: unpacking $(basename "${IMAGE}")"
case "${IMAGE}" in
	*.xz) xz -dkc "${IMAGE}" > "${WORK}/image.img" ;;
	*.gz) gzip -dc "${IMAGE}" > "${WORK}/image.img" ;;
	*.zst) zstd -dc "${IMAGE}" > "${WORK}/image.img" ;;
	*.zip) unzip -p "${IMAGE}" > "${WORK}/image.img" ;;
	*.img) cp --reflink=auto "${IMAGE}" "${WORK}/image.img" ;;
	*) echo "image-smoke: unsupported image format: ${IMAGE}" >&2; exit 65 ;;
esac
fi

LOOP="$(losetup --show -f -P "${WORK}/image.img")"
ROOT="${WORK}/root"
mkdir -p "${ROOT}"
mount "${LOOP}p2" "${ROOT}"
MOUNTED+=("${ROOT}")
mount "${LOOP}p1" "${ROOT}/boot/firmware"
MOUNTED+=("${ROOT}/boot/firmware")
for d in dev dev/pts proc sys; do
	mount --bind "/$d" "${ROOT}/$d"
	MOUNTED+=("${ROOT}/$d")
done
# a bind of /dev does not carry the host's /dev/shm mount; wlroots and grim need shared memory
mkdir -p "${ROOT}/dev/shm" "${ROOT}/run"
mount -t tmpfs -o mode=1777 tmpfs "${ROOT}/dev/shm"
MOUNTED+=("${ROOT}/dev/shm")
mount -t tmpfs -o mode=755 tmpfs "${ROOT}/run"
MOUNTED+=("${ROOT}/run")
mount -t tmpfs -o mode=1777 tmpfs "${ROOT}/tmp"
MOUNTED+=("${ROOT}/tmp")

image_arch="$(chroot "${ROOT}" /usr/bin/dpkg --print-architecture 2> /dev/null || echo unknown)"
host_arch="$(uname -m)"
if [ "${image_arch}" = "arm64" ] && [ "${host_arch}" != "aarch64" ] && [ ! -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]; then
	echo "image-smoke: SKIP (image is arm64, host is ${host_arch} without qemu-user-static binfmt)"
	echo "skipped" > "${OUT}/result"
	exit 0
fi

# ------------------------------------------------------------------ first boot
first_boot() {
	local failures=0 fw="${ROOT}/boot/firmware" shadow salt want got nm
	check() { # description, then a command that must succeed
		local what="$1"
		shift
		if "$@"; then echo "  ok    ${what}"; else echo "  FAIL  ${what}"; failures=$((failures + 1)); fi
	}
	# the imager found the file through the partition table, not the FAT: prove it is the file
	check "the setup area in the boot partition holds the settings" test "$(head -c 15 "${fw}/genesis-setup.txt")" = "GENESIS_SETUP=1"
	echo "image-smoke: running genesis-firstboot inside the image"
	# its own UTS namespace: the script sets the host name, which must not be the runner's
	if ! unshare --uts chroot "${ROOT}" /usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
		LANG=C.UTF-8 /usr/lib/genesis/genesis-firstboot; then
		echo "  FAIL  genesis-firstboot exited non-zero"
		failures=$((failures + 1))
	fi
	cp "${fw}/genesis-setup-result.txt" "${ROOT}/var/log/genesis-firstboot.log" "${OUT}/" 2> /dev/null || true
	echo "--- genesis-setup-result.txt ---"
	cat "${fw}/genesis-setup-result.txt" 2> /dev/null || echo "(missing)"
	echo "---"
	check "uid 1000 is ${TEST_USER}" test "$(awk -F: '$3 == 1000 { print $1 }' "${ROOT}/etc/passwd")" = "${TEST_USER}"
	check "home moved to /home/${TEST_USER}" test -d "${ROOT}/home/${TEST_USER}" -a ! -e "${ROOT}/home/nodoai"
	check "login shell is bash" test "$(awk -F: '$3 == 1000 { print $7 }' "${ROOT}/etc/passwd")" = /bin/bash
	check "group renamed" grep -q "^${TEST_USER}:x:1000:" "${ROOT}/etc/group"
	check "${TEST_USER} is in sudo" grep -qE "^sudo:.*[:,]${TEST_USER}(,|$)" "${ROOT}/etc/group"
	check "${TEST_USER} is in i2c (sensor bar)" grep -qE "^i2c:.*[:,]${TEST_USER}(,|$)" "${ROOT}/etc/group"
	shadow="$(awk -F: -v u="${TEST_USER}" '$1 == u { print $2 }' "${ROOT}/etc/shadow")"
	salt="$(printf '%s' "${shadow}" | cut -d'$' -f4)"
	want="$(openssl passwd -6 -salt "rounds=100000\$${salt}" "${TEST_PASS}")"
	check "the password typed into the imager opens the account" test -n "${salt}" -a "${shadow}" = "${want}"
	check "desktop autologin as ${TEST_USER}" grep -q "^autologin-user=${TEST_USER}$" "${ROOT}/etc/lightdm/lightdm.conf"
	check "Genesis stays the session" grep -q "^autologin-session=genesis$" "${ROOT}/etc/lightdm/lightdm.conf"
	check "AccountsService knows ${TEST_USER}" grep -q '^Session=genesis$' "${ROOT}/var/lib/AccountsService/users/${TEST_USER}"
	check "first-boot wizard removed" test ! -e "${ROOT}/etc/xdg/autostart/piwiz.desktop"
	got="$(systemctl --root="${ROOT}" is-enabled userconfig.service 2> /dev/null || true)"
	check "userconfig.service disabled (got '${got}')" test "${got}" != enabled
	check "genesis-firstboot.service still enabled" test "$(systemctl --root="${ROOT}" is-enabled genesis-firstboot.service 2> /dev/null)" = enabled
	check "host name ${TEST_HOST}" test "$(cat "${ROOT}/etc/hostname")" = "${TEST_HOST}"
	check "/etc/hosts follows" grep -qE "^127\.0\.1\.1[[:space:]]+${TEST_HOST}$" "${ROOT}/etc/hosts"
	nm="${ROOT}/etc/NetworkManager/system-connections/genesis-wifi.nmconnection"
	check "Wi-Fi connection written" grep -q "^psk=${TEST_PSK}$" "${nm}"
	check "Wi-Fi connection is private" test "$(stat -c %a "${nm}" 2> /dev/null)" = 600
	check "Wi-Fi country JP in cmdline.txt" grep -q 'cfg80211.ieee80211_regdom=JP' "${fw}/cmdline.txt"
	check "build-time Wi-Fi country replaced" test -z "$(grep -o 'cfg80211.ieee80211_regdom=IN' "${fw}/cmdline.txt")"
	check "result file reports the country" grep -qx 'wifi country: JP' "${fw}/genesis-setup-result.txt"
	check "time zone Asia/Tokyo" test "$(readlink "${ROOT}/etc/localtime")" = /usr/share/zoneinfo/Asia/Tokyo
	check "keyboard gb" grep -q '^XKBLAYOUT="gb"$' "${ROOT}/etc/default/keyboard"
	check "SSH key authorised" grep -qF "${TEST_KEY}" "${ROOT}/home/${TEST_USER}/.ssh/authorized_keys"
	check "settings wiped from the card" cmp -s "${fw}/genesis-setup.txt" "${ROOT}/usr/share/genesis/genesis-setup-placeholder.txt"
	check "no errors reported" test -z "$(grep -iE 'error|fail|not ' "${fw}/genesis-setup-result.txt")"
	check "password not in the result or the log" test -z "$(grep -lF -e "${TEST_PASS}" -e "${TEST_PSK}" "${fw}/genesis-setup-result.txt" "${ROOT}/var/log/genesis-firstboot.log")"
	echo "image-smoke: first boot: ${failures} failure(s)"
	echo "${failures}" > "${OUT}/firstboot-failures"
	[ "${failures}" -eq 0 ]
}
if [ "${FIRSTBOOT}" = 1 ]; then
	if ! first_boot; then
		echo "image-smoke: FAIL first-boot setup" >&2
		echo 1 > "${OUT}/result"
		exit 1
	fi
fi

# ------------------------------------------------------------------ Pi 5 bootloader settings
# No Pi here, so nothing is flashed: the image's own rpi-eeprom-config embeds what
# genesis-eeprom plans into the packaged bootloader image and reads it back, and the
# service itself must refuse to run in this chroot.
bootloader() {
	local failures=0 fwdir=/usr/lib/firmware/raspberrypi/bootloader-2712 image mode out own
	bcheck() { # description, then a command that must succeed
		local what="$1"
		shift
		if "$@"; then echo "  ok    ${what}"; else echo "  FAIL  ${what}"; failures=$((failures + 1)); fi
	}
	if [ ! -x "${ROOT}/usr/lib/genesis/genesis-eeprom" ]; then
		echo "  info  no genesis-eeprom in this image (built before the bootloader settings): skipped"
		return 0
	fi
	bcheck "rpi-eeprom tools are installed" test -x "${ROOT}/usr/bin/rpi-eeprom-config" -a -x "${ROOT}/usr/bin/rpi-eeprom-update"
	image="$(find "${ROOT}${fwdir}/default" -maxdepth 1 -name 'pieeprom-*.bin' -size 2048k -printf '%f\n' 2> /dev/null | sort | tail -n 1)"
	bcheck "a Pi 5 bootloader image is packaged (${image:-none})" test -n "${image}"
	bcheck "genesis-eeprom.service is enabled" test -L "${ROOT}/etc/systemd/system/multi-user.target.wants/genesis-eeprom.service"
	if [ -e "${ROOT}/etc/systemd/system/multi-user.target.wants/rpi-eeprom-update.service" ]; then
		echo "  info  rpi-eeprom-update.service is enabled (genesis-eeprom runs after it)"
	fi
	# shellcheck disable=SC2016  # a dpkg-query format, not a shell expansion
	echo "  info  $(chroot "${ROOT}" dpkg-query -W -f '${Package} ${Version}  ' rpi-eeprom flashrom rpieepromab 2> /dev/null)"
	# fixed settings, so the checks below do not depend on this build's EEPROM_* knobs
	printf 'enabled=1\nboot_first=auto\npcie_probe=1\nnet_install_at_power_on=0\npower_off_on_halt=keep\n' > "${ROOT}/tmp/smoke-bootloader.conf"
	if [ -n "${image}" ]; then
		own=0
		chroot "${ROOT}" /usr/lib/genesis/genesis-eeprom plan --image "${fwdir}/default/${image}" --boot-mode 1 \
			> "${ROOT}/tmp/plan-own.conf" 2>&1 || own=$?
		bcheck "the image's own bootloader.conf is valid (plan exit ${own})" test "${own}" = 0
		echo "--- factory config of ${image} ---"
		chroot "${ROOT}" /usr/bin/rpi-eeprom-config "${fwdir}/default/${image}" | sed 's/^/  /'
		for mode in 1 6 4; do
			chroot "${ROOT}" /usr/lib/genesis/genesis-eeprom plan --settings /tmp/smoke-bootloader.conf \
				--image "${fwdir}/default/${image}" --boot-mode "${mode}" > "${ROOT}/tmp/plan-${mode}.conf" 2> /dev/null
			bcheck "boot mode ${mode}: genesis-eeprom plans a config" test -s "${ROOT}/tmp/plan-${mode}.conf"
			chroot "${ROOT}" /usr/bin/rpi-eeprom-config --config "/tmp/plan-${mode}.conf" --out "/tmp/built-${mode}.bin" "${fwdir}/default/${image}" > /dev/null
			bcheck "boot mode ${mode}: the image's rpi-eeprom-config embeds it" test "$(stat -c %s "${ROOT}/tmp/built-${mode}.bin" 2> /dev/null)" = 2097152
			chroot "${ROOT}" /usr/bin/rpi-eeprom-config "/tmp/built-${mode}.bin" > "${ROOT}/tmp/readback-${mode}.conf"
			bcheck "boot mode ${mode}: the embedded config reads back unchanged" cmp -s "${ROOT}/tmp/plan-${mode}.conf" "${ROOT}/tmp/readback-${mode}.conf"
		done
		echo "--- planned for an NVMe boot ---"
		sed 's/^/  /' "${ROOT}/tmp/plan-6.conf"
		bcheck "SD boot: SD card first" grep -qx 'BOOT_ORDER=0xf461' "${ROOT}/tmp/plan-1.conf"
		bcheck "NVMe boot: NVMe first, SD and USB after" grep -qx 'BOOT_ORDER=0xf416' "${ROOT}/tmp/plan-6.conf"
		bcheck "USB boot: USB first, SD and NVMe after" grep -qx 'BOOT_ORDER=0xf614' "${ROOT}/tmp/plan-4.conf"
	fi
	out="$(chroot "${ROOT}" /usr/lib/genesis/genesis-eeprom status --json 2> /dev/null | python3 -c 'import json, sys; print(json.load(sys.stdin).get("reason", ""))')"
	bcheck "it knows it runs in a chroot (${out:-no answer})" test "${out}" = chroot
	out="$(chroot "${ROOT}" /usr/lib/genesis/genesis-eeprom apply 2>&1)"
	bcheck "in a chroot the service touches nothing (${out})" grep -qE 'skip: chroot|customisation is off' <<< "${out}"
	bcheck "no bootloader update was staged on the card" test ! -e "${ROOT}/boot/firmware/pieeprom.upd" -a ! -e "${ROOT}/boot/firmware/recovery.bin"
	bcheck "no bootloader state was recorded in the image" test ! -e "${ROOT}/var/lib/genesis/eeprom/state.json"
	echo "image-smoke: bootloader: ${failures} failure(s)"
	[ "${failures}" -eq 0 ]
}
if ! bootloader; then
	echo "image-smoke: FAIL bootloader settings" >&2
	echo 1 > "${OUT}/result"
	exit 1
fi

if [ ! -x "${ROOT}/usr/sbin/i2cdetect" ]; then
	echo "image-smoke: FAIL the image has no /usr/sbin/i2cdetect (i2c-tools missing: the sensor bar cannot scan)" >&2
	echo 1 > "${OUT}/result"
	exit 1
fi
if [ ! -x "${ROOT}/usr/share/genesis-shell/preview.sh" ]; then
	echo "image-smoke: FAIL the image has no /usr/share/genesis-shell/preview.sh (Genesis Shell missing?)" >&2
	exit 1
fi

user_name="$(awk -F: '$3 == 1000 { print $1 }' "${ROOT}/etc/passwd")"
[ -n "${user_name}" ] || user_name=root
home="/home/${user_name}"
[ "${user_name}" = root ] && home=/root
mkdir -p "${ROOT}/tmp/genesis-smoke"
chown 1000:1000 "${ROOT}/tmp/genesis-smoke" 2> /dev/null || true

echo "image-smoke: running the Genesis session headless as ${user_name}"
set +e
chroot --userspec="${user_name}:${user_name}" "${ROOT}" /usr/bin/env -i \
	HOME="${home}" USER="${user_name}" LOGNAME="${user_name}" LANG=C.UTF-8 \
	PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
	XDG_RUNTIME_DIR=/tmp/genesis-smoke-runtime \
	/usr/share/genesis-shell/preview.sh /tmp/genesis-smoke
status=$?
set -e
cp -a "${ROOT}/tmp/genesis-smoke/." "${OUT}/" 2> /dev/null || true
echo "image-smoke: preview exit status ${status}; results in ${OUT}"
ls -l "${OUT}" || true
exit "${status}"
