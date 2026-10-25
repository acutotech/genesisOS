#!/usr/bin/env bash
# genesis-firstboot against a throw-away root: no root rights, no device.
#
# Genesis Imager builds the setup area (its own code, so the format is the one
# the imager really writes); genesis-firstboot runs with GENESIS_ROOT pointing at
# a fake root, and the system tools it calls (getent, raspi-config, systemctl,
# userconf-pi, ...) are stubs that record their arguments.
#
# What this does NOT prove: that userconf-pi, NetworkManager and systemd on the
# Pi accept the result. The image build's first-boot test runs the script in
# the real image for that.
# shellcheck disable=SC2016  # stub bodies and crypt hashes are literal text on purpose
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIRSTBOOT="${REPO_DIR}/stage-custom/14-first-boot/files/genesis-firstboot"
PLACEHOLDER_SRC="${REPO_DIR}/stage-custom/14-first-boot/files/genesis-setup.txt"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); echo "  ok    $*"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL  $*" >&2; }
assert_file()    { if [ -f "$1" ]; then ok "exists ${1#"${R}"}"; else fail "missing ${1#"${R}"}"; fi; }
assert_no_file() { if [ ! -e "$1" ]; then ok "absent ${1#"${R}"}"; else fail "should be absent: ${1#"${R}"}"; fi; }
assert_grep()    { if grep -qE -- "$1" "$2"; then ok "/$1/ in ${2#"${R}"}"; else fail "/$1/ not in ${2#"${R}"}"; fi; }
assert_no_grep() { if ! grep -qF -- "$1" "$2"; then ok "'$1' absent from ${2#"${R}"}"; else fail "'$1' unexpectedly in ${2#"${R}"}"; fi; }
assert_eq()      { if [ "$1" = "$2" ]; then ok "$3"; else fail "$3 (got '$1', expected '$2')"; fi; }

# --- stubs ------------------------------------------------------------------------------
STUBS="${WORK}/stubs"
CALLS="${WORK}/calls.log"
mkdir -p "${STUBS}"
stub() { printf '#!/bin/bash\n%s\n' "$2" > "${STUBS}/$1"; chmod +x "${STUBS}/$1"; }
stub getent 'db=$1; key=$2
[ "$db" = passwd ] || exit 2
awk -F: -v k="$key" '"'"'($1 == k || $3 == k) { print; found = 1 } END { exit !found }'"'"' "$GENESIS_ROOT/etc/passwd"'
stub raspi-config 'echo "raspi-config $*" >> "$CALLS"
case "$2" in get_autologin) echo 0 ;; do_wifi_country) [ -n "$3" ] ;; esac'
stub systemctl 'echo "systemctl $*" >> "$CALLS"'
stub hostname 'echo "hostname $*" >> "$CALLS"'
stub nmcli 'echo "nmcli $*" >> "$CALLS"'
stub chown 'echo "chown $*" >> "$CALLS"'
stub usermod 'echo "usermod $*" >> "$CALLS"'
stub groupmod 'echo "groupmod $*" >> "$CALLS"'
stub chpasswd 'echo "chpasswd $* <$(cat)>" >> "$CALLS"'
export CALLS PATH="${STUBS}:${PATH}"

R="${WORK}/root"
make_root() {
	rm -rf "${R}" "${CALLS}"
	mkdir -p "${R}/boot/firmware" "${R}/etc/default" "${R}/etc/lightdm" "${R}/etc/genesis" \
		"${R}/home/nodoai" "${R}/usr/share/genesis" "${R}/usr/share/zoneinfo/Asia" \
		"${R}/var/lib/AccountsService/users" "${R}/usr/lib/userconf-pi" "${R}/usr/lib/genesis-settings"
	printf 'root:x:0:0:root:/root:/bin/bash\nnodoai:x:1000:1000:,,,:/home/nodoai:/usr/sbin/nologin\n' > "${R}/etc/passwd"
	printf '127.0.0.1\tlocalhost\n127.0.1.1\tnodobox\n' > "${R}/etc/hosts"
	printf 'nodobox\n' > "${R}/etc/hostname"
	printf 'XKBMODEL="pc105"\nXKBLAYOUT="us"\nXKBVARIANT=""\nXKBOPTIONS=""\n' > "${R}/etc/default/keyboard"
	printf 'TZif2 fake\n' > "${R}/usr/share/zoneinfo/Asia/Tokyo"
	printf '[User]\nSession=genesis\n' > "${R}/var/lib/AccountsService/users/nodoai"
	printf 'genesis\n' > "${R}/etc/genesis/session"
	cp "${PLACEHOLDER_SRC}" "${R}/usr/share/genesis/genesis-setup-placeholder.txt"
	cp "${PLACEHOLDER_SRC}" "${R}/boot/firmware/genesis-setup.txt"
	# userconf-pi: record the call and rename uid 1000 the way usermod -l -m would
	cat > "${R}/usr/lib/userconf-pi/userconf" << 'EOF'
#!/bin/bash
echo "userconf $*" >> "$CALLS"
old="$(awk -F: '$3 == 1000 { print $1 }' "$GENESIS_ROOT/etc/passwd")"
if [ "$old" != "$1" ]; then
	sed -i "s#^$old:x:1000:1000:\([^:]*\):/home/$old:[^:]*#$1:x:1000:1000:\1:/home/$1:/bin/bash#" "$GENESIS_ROOT/etc/passwd"
	mv "$GENESIS_ROOT/home/$old" "$GENESIS_ROOT/home/$1"
fi
EOF
	printf '#!/bin/bash\necho "genesis-helper SUDO_USER=$SUDO_USER $*" >> "$CALLS"\n' > "${R}/usr/lib/genesis-settings/genesis-helper"
	chmod +x "${R}/usr/lib/userconf-pi/userconf" "${R}/usr/lib/genesis-settings/genesis-helper"
	: > "${CALLS}"
}

# Write the setup area exactly as Genesis Imager does; arguments are Settings fields as JSON.
imager_payload() {
	python3 - "${REPO_DIR}/imager" "$1" "${R}/boot/firmware/genesis-setup.txt" << 'PY'
import json, sys
sys.path.insert(0, sys.argv[1])
import genesis_imager as g
settings = g.Settings(**json.loads(sys.argv[2]))
with open(sys.argv[3], "r+b") as fh:          # in place, as the imager writes the card
    fh.write(settings.payload())
print(settings.password_hash or "")
PY
}

run_firstboot() {
	GENESIS_ROOT="${R}" GENESIS_FW=/boot/firmware "${FIRSTBOOT}" > "${WORK}/out.log" 2>&1
}

HASH='$6$rounds=100000$testsalttestsalt$abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789./abcdefghijklmnopqrstuv'
KEY1='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGenesisTestKeyOne genesis@test'
KEY2='ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQGenesisTestKeyTwo other@test'

# =========================================================================================
echo "Scenario 1: everything set, as Genesis Imager writes it"
make_root
SETTINGS="$(python3 -c 'import json, sys; print(json.dumps({
    "user": "tester", "password_hash": sys.argv[1], "hostname": "picard",
    "wifi_ssid": " Lab \"5G\" \\ Café", "wifi_password": "pa ss\\word;#1", "wifi_hidden": True,
    "wifi_country": "JP", "timezone": "Asia/Tokyo", "keyboard": "gb", "keyboard_variant": "extd",
    "ssh": True, "ssh_password_auth": False, "ssh_keys": [sys.argv[2], sys.argv[3]]}))' "${HASH}" "${KEY1}" "${KEY2}")"
imager_payload "${SETTINGS}" > /dev/null
assert_eq "$(head -c 15 "${R}/boot/firmware/genesis-setup.txt")" "GENESIS_SETUP=1" "setup area written"
if run_firstboot; then ok "genesis-firstboot exits 0"; else fail "genesis-firstboot failed: $(cat "${WORK}/out.log")"; fi

assert_grep "^userconf tester \\\$6\\\$rounds=100000\\\$testsalttestsalt\\\$" "${CALLS}"
assert_eq "$(grep -c '^userconf ' "${CALLS}")" 1 "userconf-pi called once"
assert_eq "$(grep '^userconf ' "${CALLS}" | cut -d' ' -f3)" "${HASH}" "password hash passed through unchanged"
assert_file "${R}/var/lib/userconf-pi/autologin"
assert_grep '^systemctl --no-block stop userconfig.service$' "${CALLS}"
assert_grep '^genesis-helper SUDO_USER=tester session genesis$' "${CALLS}"
assert_file "${R}/var/lib/AccountsService/users/tester"
assert_no_file "${R}/var/lib/AccountsService/users/nodoai"

assert_eq "$(cat "${R}/etc/hostname")" picard "hostname written"
assert_grep $'^127\\.0\\.1\\.1\tpicard$' "${R}/etc/hosts"
assert_grep '^hostname picard$' "${CALLS}"

assert_grep '^raspi-config nonint do_wifi_country JP$' "${CALLS}"
NM="${R}/etc/NetworkManager/system-connections/genesis-wifi.nmconnection"
assert_file "${NM}"
assert_eq "$(stat -c %a "${NM}")" 600 "Wi-Fi keyfile is private (600)"
# " Lab \"5G\" \ Café" as bytes; the id gets GKeyFile escaping (\s for the leading space, \\ for \)
assert_grep '^ssid=32;76;97;98;32;34;53;71;34;32;92;32;67;97;102;195;169;$' "${NM}"
assert_grep '^id=\\sLab "5G" \\\\ Café$' "${NM}"
assert_grep '^psk=pa ss\\\\word;#1$' "${NM}"
assert_grep '^hidden=true$' "${NM}"
assert_grep '^key-mgmt=wpa-psk$' "${NM}"
assert_grep '^uuid=[0-9a-f-]{36}$' "${NM}"
assert_grep '^nmcli connection reload$' "${CALLS}"

assert_eq "$(readlink "${R}/etc/localtime")" /usr/share/zoneinfo/Asia/Tokyo "localtime points at the time zone"
assert_eq "$(cat "${R}/etc/timezone")" Asia/Tokyo "/etc/timezone written"
assert_grep '^XKBLAYOUT="gb"$' "${R}/etc/default/keyboard"
assert_grep '^XKBVARIANT="extd"$' "${R}/etc/default/keyboard"
assert_grep '^XKBMODEL="pc105"$' "${R}/etc/default/keyboard"

assert_grep '^systemctl enable ssh$' "${CALLS}"
AK="${R}/home/tester/.ssh/authorized_keys"
assert_file "${AK}"
assert_eq "$(wc -l < "${AK}")" 2 "two authorized keys"
assert_grep "^${KEY1}\$" "${AK}"
assert_eq "$(stat -c %a "${AK}")" 600 "authorized_keys is 600"
assert_grep '^PasswordAuthentication no$' "${R}/etc/ssh/sshd_config.d/20-genesis-keys-only.conf"

assert_eq "$(cmp -s "${R}/boot/firmware/genesis-setup.txt" "${PLACEHOLDER_SRC}" && echo restored)" restored "placeholder restored over the settings"
RESULT="${R}/boot/firmware/genesis-setup-result.txt"
assert_file "${RESULT}"
assert_grep '^user: tester \(renamed from nodoai\), password set$' "${RESULT}"
assert_grep '^ssh: key logins only$' "${RESULT}"
for secret in 'testsalttestsalt' 'pa ss' 'word;#1'; do
	assert_no_grep "${secret}" "${RESULT}"
	assert_no_grep "${secret}" "${R}/var/log/genesis-firstboot.log"
done

echo "Scenario 1b: the next boot finds the placeholder and does nothing"
: > "${CALLS}"
rm -f "${RESULT}"
if run_firstboot; then ok "second boot exits 0"; else fail "second boot failed"; fi
assert_eq "$(wc -c < "${CALLS}")" 0 "second boot calls nothing"
assert_no_file "${RESULT}"

# =========================================================================================
echo "Scenario 2: card flashed with another tool (placeholder untouched)"
make_root
if run_firstboot; then ok "exits 0"; else fail "failed on the placeholder"; fi
assert_eq "$(wc -c < "${CALLS}")" 0 "nothing called"
assert_eq "$(cat "${R}/etc/hostname")" nodobox "hostname untouched"
assert_no_file "${R}/boot/firmware/genesis-setup-result.txt"

# =========================================================================================
echo "Scenario 3: settings cut short (no END=1)"
make_root
imager_payload '{"user": "tester", "password_hash": "'"${HASH}"'"}' > /dev/null
python3 - "${R}/boot/firmware/genesis-setup.txt" << 'PY'
import sys
data = open(sys.argv[1], "rb").read()
open(sys.argv[1], "r+b").write(data.replace(b"END=1\n", b"\n\n\n\n\n\n"))
PY
if run_firstboot; then ok "exits 0"; else fail "failed on cut-short settings"; fi
assert_eq "$(grep -c '^userconf ' "${CALLS}" || true)" 0 "account untouched"
assert_grep '^error: the settings are incomplete' "${R}/boot/firmware/genesis-setup-result.txt"
assert_eq "$(cmp -s "${R}/boot/firmware/genesis-setup.txt" "${PLACEHOLDER_SRC}" && echo restored)" restored "placeholder restored"

# =========================================================================================
echo "Scenario 4: a bad user name is refused, the rest still applies"
make_root
printf 'GENESIS_SETUP=1\nUSER=Root;rm\nPASSWORD_HASH=%s\nHOSTNAME=lab-pi\nWIFI_SSID_B64=%s\nWIFI_PSK_B64=%s\nWIFI_COUNTRY=IN\nTIMEZONE=../../etc/passwd\nSSH=0\nEND=1\n' \
	"${HASH}" "$(printf 'Open' | base64)" "$(printf 'short' | base64)" | dd of="${R}/boot/firmware/genesis-setup.txt" conv=notrunc status=none
if run_firstboot; then ok "exits 0"; else fail "failed"; fi
assert_eq "$(grep -c '^userconf ' "${CALLS}" || true)" 0 "invalid user name: userconf-pi not called"
assert_grep "^user: 'Root;rm' is not a valid user name" "${R}/boot/firmware/genesis-setup-result.txt"
assert_eq "$(cat "${R}/etc/hostname")" lab-pi "hostname still applied"
assert_grep '^wifi: the password must be 8 to 63 characters' "${R}/boot/firmware/genesis-setup-result.txt"
assert_no_file "${R}/etc/NetworkManager/system-connections/genesis-wifi.nmconnection"
assert_grep "^timezone: '../../etc/passwd' is not a known time zone" "${R}/boot/firmware/genesis-setup-result.txt"
assert_grep '^systemctl disable ssh$' "${CALLS}"

# =========================================================================================
echo "Scenario 5: no userconf-pi in the image (fallback path), open network, same user name"
make_root
rm "${R}/usr/lib/userconf-pi/userconf"
imager_payload '{"user": "nodoai", "password_hash": "'"${HASH}"'", "wifi_ssid": "Open Cafe", "wifi_password": ""}' > /dev/null
if run_firstboot; then ok "exits 0"; else fail "failed"; fi
assert_grep '^usermod -s /bin/bash nodoai$' "${CALLS}"
assert_eq "$(grep -c '^usermod -l' "${CALLS}" || true)" 0 "same name: no rename"
assert_eq "$(grep '^chpasswd ' "${CALLS}")" "chpasswd -e <nodoai:${HASH}>" "password set with chpasswd -e"
NM="${R}/etc/NetworkManager/system-connections/genesis-wifi.nmconnection"
assert_no_grep '[wifi-security]' "${NM}"
assert_no_grep 'hidden=' "${NM}"
assert_grep '^user: nodoai, password set$' "${R}/boot/firmware/genesis-setup-result.txt"

echo
echo "${PASS} passed, ${FAIL} failed"
[ "${FAIL}" -eq 0 ]
