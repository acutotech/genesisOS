#!/bin/bash
# The rpi-eeprom-update.service drop-in under a real systemd.
#
#   sudo GENESIS_CI=1 scripts/test-dropin-systemd.sh
#
# scripts/test-eeprom.py runs `genesis-eeprom watch` by hand; this checks what only
# systemd can show: that it runs `watch before` and `watch after` around the service
# (rpi-eeprom-update.service is Type=oneshot, RemainAfterExit=true), after a failed
# run too (ExecStopPost), and that a later stop or restart changes nothing. It
# installs genesis-eeprom, a stand-in rpi-eeprom-update.service and the drop-in into
# the running system, so it refuses to run outside a throwaway machine (CI).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES="${HERE}/../stage-custom/15-bootloader/files"
[ "$(id -u)" = 0 ] || { echo "test-dropin-systemd: run as root" >&2; exit 2; }
[ -d /run/systemd/system ] || { echo "test-dropin-systemd: needs a running systemd" >&2; exit 2; }
[ "${GENESIS_CI:-}" = 1 ] || {
	echo "test-dropin-systemd: set GENESIS_CI=1 (this installs into /usr/lib/genesis, /var/lib/genesis and /etc/systemd)" >&2
	exit 2
}

UNIT=rpi-eeprom-update.service
BOOTFS=/var/tmp/genesis-test-bootfs
STATE=/var/lib/genesis/eeprom
PENDING=/run/rpi-eeprom-update-pending
MARKER=/run/genesis-eeprom-watch
pass=0
fail=0
ok() { echo "  ok    $*"; pass=$((pass + 1)); }
bad() { echo "  FAIL  $*"; fail=$((fail + 1)); }

install -D -m 755 "${FILES}/genesis-eeprom" /usr/lib/genesis/genesis-eeprom
install -D -m 644 "${FILES}/rpi-eeprom-update-genesis.conf" "/usr/lib/systemd/system/${UNIT}.d/genesis-eeprom.conf"
# a stand-in for `rpi-eeprom-update -s -a`: removePreviousUpdates, then what /run/fake-rpi-eeprom says
cat > /usr/local/sbin/fake-rpi-eeprom-update << EOF
#!/bin/sh
rm -f ${BOOTFS}/pieeprom.upd ${BOOTFS}/pieeprom.sig ${BOOTFS}/recovery.bin
[ -f /run/fake-rpi-eeprom-take-back ] && rm -f ${PENDING}   # a user's -r during the run
exit "\$(cat /run/fake-rpi-eeprom-rc 2> /dev/null || echo 0)"
EOF
chmod 755 /usr/local/sbin/fake-rpi-eeprom-update
cat > "/etc/systemd/system/${UNIT}" << 'EOF'
[Unit]
Description=stand-in for rpi-eeprom-update.service (genesis test)
[Service]
Type=oneshot
RemainAfterExit=true
ExecStart=/usr/local/sbin/fake-rpi-eeprom-update
EOF
systemctl daemon-reload
if systemctl cat "${UNIT}" | grep -q '^ExecStopPost=-/usr/lib/genesis/genesis-eeprom watch after$'; then
	ok "systemd loads the drop-in onto ${UNIT}"
else
	bad "systemd does not show the drop-in on ${UNIT}"
fi

# Genesis's update staged in this boot: the file, the pending flag, the state that names both
stage() {
	systemctl stop "${UNIT}" 2> /dev/null || true
	systemctl reset-failed "${UNIT}" 2> /dev/null || true
	rm -f "${MARKER}" /run/fake-rpi-eeprom-rc /run/fake-rpi-eeprom-take-back
	mkdir -p "${BOOTFS}" "${STATE}"
	head -c 4096 /dev/urandom > "${BOOTFS}/pieeprom.upd"
	echo "recovery $(date -u +%s%N)" > "${PENDING}"
	python3 -I - "${BOOTFS}/pieeprom.upd" "${PENDING}" "${STATE}/state.json" << 'EOF'
import hashlib, json, sys
upd, pending, out = sys.argv[1:]
state = {"status": "staged", "attempts": 1, "upd": upd, "path": "recovery",
         "upd_sha256": hashlib.sha256(open(upd, "rb").read()).hexdigest(),
         "pending_line": open(pending).readline().strip(),
         "boot_id": open("/proc/sys/kernel/random/boot_id").read().strip()}
json.dump(state, open(out, "w"))
EOF
	rm -f "${STATE}/done"
}
status() { python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["status"])' "${STATE}/state.json"; }
settle() { # wait until the unit is not activating or deactivating (ExecStopPost runs while it deactivates)
	local state
	for _ in $(seq 1 50); do
		state="$(systemctl show -p ActiveState --value "${UNIT}")"
		case "${state}" in activating | deactivating) sleep 0.2 ;; *) return 0 ;; esac
	done
	echo "  (still ${state})"
}
check() { # description, expected status
	local got
	got="$(status)"
	if [ "${got}" = "$2" ]; then ok "$1 (${got})"; else bad "$1: ${got}, expected $2"; fi
}

echo "== the service removes Genesis's update (an rpi-eeprom upgrade restarts it)"
stage
systemctl start "${UNIT}"
settle
check "watch before/after around a successful run" removed
if [ -e "${MARKER}" ]; then bad "the marker is left after the run"; else ok "the marker is gone after the run"; fi
reason="$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["reason"])' "${STATE}/state.json")"
systemctl restart "${UNIT}"
settle
systemctl stop "${UNIT}"
settle
check "a later restart and stop change nothing" removed
if [ "$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["reason"])' "${STATE}/state.json")" = "${reason}" ]; then
	ok "the recorded reason is untouched"
else
	bad "a later restart or stop rewrote the record"
fi

echo "== the service fails after removing it (ExecStartPost is skipped)"
stage
echo 2 > /run/fake-rpi-eeprom-rc
if systemctl start "${UNIT}" 2> /dev/null; then bad "the stand-in did not fail"; else ok "the run failed, as -s -a can"; fi
settle
check "ExecStopPost still runs watch after" removed

echo "== a user's -r during the run"
stage
touch /run/fake-rpi-eeprom-take-back
systemctl start "${UNIT}"
settle
check "a take-back during the run is not the service's removal" staged

echo "== nothing of Genesis's staged when the service runs"
stage
rm -f "${BOOTFS}/pieeprom.upd"
systemctl start "${UNIT}"
settle
check "an update already gone before the run is not the service's removal" staged

systemctl stop "${UNIT}" 2> /dev/null || true
echo "test-dropin-systemd: ${pass} passed, ${fail} failed"
[ "${fail}" -eq 0 ]
