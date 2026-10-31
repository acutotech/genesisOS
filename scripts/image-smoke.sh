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

# One polkit authentication agent per session: a second one shows "An authentication
# agent already exists for the given subject" at every login.
polkit_agents() {
	local plan agents line f
	for f in "${ROOT}"/etc/xdg/autostart/*.desktop; do
		if grep -qiE 'polkit|policykit' "${f}"; then
			echo "  info  autostart $(basename "${f}"): $(grep -E '^(Exec|OnlyShowIn|NotShowIn|Hidden)=' "${f}" | tr '\n' ' ')"
		fi
	done
	find "${ROOT}/usr/lib/systemd/user" "${ROOT}/etc/systemd/user" -iname '*polkit*' 2> /dev/null | sed "s|^${ROOT}|  info  user unit |"
	plan="$(chroot "${ROOT}" env XDG_CURRENT_DESKTOP=Genesis:LXDE-pi /usr/bin/genesis-xdg-autostart --dry-run 2>&1 || true)"
	while IFS= read -r line; do echo "  | ${line}"; done <<< "${plan}"
	agents="$(grep -ciE '^autostart: would start .*(polkit|policykit)' <<< "${plan}" || true)"
	if [ "${agents}" -le 1 ]; then
		echo "  ok    the Genesis session starts ${agents} polkit agent(s)"
	else
		echo "  FAIL  the Genesis session would start ${agents} polkit agents"
		return 1
	fi
}
if ! polkit_agents; then
	echo "image-smoke: FAIL more than one polkit agent" >&2
	echo 1 > "${OUT}/result"
	exit 1
fi

# Acu Connect client: its own Python must carry it whatever happens to the system's
acu_connect() {
	local failures=0 py=/opt/acu-connect/python/bin/python3 out
	acheck() { # description, then a command that must succeed
		local what="$1"
		shift
		if "$@"; then echo "  ok    ${what}"; else echo "  FAIL  ${what}"; failures=$((failures + 1)); fi
	}
	if [ ! -d "${ROOT}/opt/acu-connect" ]; then
		echo "  info  no Acu Connect client in this image: skipped"
		return 0
	fi
	acheck "its own Python is installed" test -x "${ROOT}${py}"
	acheck "acu-connect.service is enabled" test -L "${ROOT}/etc/systemd/system/multi-user.target.wants/acu-connect.service"
	acheck "it runs as uid 1000 (the desktop user, renamed or not)" grep -qx 'User=1000' "${ROOT}/usr/lib/systemd/system/acu-connect.service"
	acheck "the link directory belongs to uid 1000" test "$(stat -c '%u %a' "${ROOT}/var/lib/acu-connect" 2> /dev/null)" = "1000 700"
	echo "  info  $(chroot "${ROOT}" "${py}" -I -c 'import sys, acu_connect; print("acu-connect", acu_connect.VERSION, "on Python", sys.version.split()[0])' 2>&1)"
	# What an apt upgrade to another Python version, or removing python3, leaves: no system Python.
	# Hidden in a private mount namespace, so nothing is changed outside it.
	out="$(unshare --mount --propagation private bash -s "${ROOT}" "${py}" << 'HIDE' 2>&1
root="$1"; py="$2"
for f in "${root}"/usr/bin/python3*; do [ -f "${f}" ] && [ ! -L "${f}" ] && mount --bind /dev/null "${f}"; done
for d in "${root}"/usr/lib/python3*; do [ -d "${d}" ] && mount -t tmpfs none "${d}"; done
if chroot "${root}" /usr/bin/python3 -c pass 2> /dev/null; then echo "FAIL the system Python is still there"; fi
chroot "${root}" "${py}" -I -c 'import aiortc, av, cryptography, websockets, acu_connect.daemon' && echo "OK imports"
chroot "${root}" env ACU_CONFIG_DIR=/tmp/acu-smoke "${py}" -I -m acu_connect status | grep -q 'Not linked' && echo "OK status"
timeout 4 chroot "${root}" env ACU_CONFIG_DIR=/tmp/acu-smoke /usr/lib/acu-connect/acu-connect-service | grep -q 'not linked yet' && echo "OK supervisor"
exit 0
HIDE
)" || true
	acheck "the system Python is really hidden for this check" test -z "$(grep '^FAIL' <<< "${out}")"
	acheck "its packages import without the system Python" grep -q '^OK imports$' <<< "${out}"
	acheck "acu-connect status works without the system Python" grep -q '^OK status$' <<< "${out}"
	acheck "the service waits for a signin instead of failing" grep -q '^OK supervisor$' <<< "${out}"
	# the package's own tests, on this Python and these packages
	rm -rf "${ROOT}/tmp/acu-tests"
	mkdir -p "${ROOT}/tmp/acu-tests"
	cp -R "${REPO_DIR}/stage-custom/16-acu-connect/files/acu-connect-raspberry-pi/tests" "${ROOT}/tmp/acu-tests/tests"
	if out="$(chroot "${ROOT}" sh -c "cd /tmp/acu-tests && ACU_CONFIG_DIR=/tmp/acu-tests/config ${py} -I -m unittest discover -s tests -t ." 2>&1)"; then
		acheck "the package's own tests pass ($(grep -o 'Ran [0-9]* tests' <<< "${out}"))" true
	else
		echo "${out}" | tail -n 30 | sed 's/^/  | /'
		acheck "the package's own tests pass" false
	fi
	echo "image-smoke: acu-connect: ${failures} failure(s)"
	[ "${failures}" -eq 0 ]
}
if ! acu_connect; then
	echo "image-smoke: FAIL Acu Connect client" >&2
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
