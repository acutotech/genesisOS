#!/bin/bash -e
# First-boot configuration through cloud-init (NoCloud datasource; Raspberry Pi
# OS reads /boot/firmware/{meta-data,user-data,network-config} on first boot).
#
#   user-data       documented template, every setting commented out
#   network-config  Wi-Fi credentials, written only when WIFI_SSID is set
#
# Both files stay editable on the boot partition after flashing.

# shellcheck source=../lib.sh
source "${STAGE_DIR}/lib.sh"

if [ "${ENABLE_CLOUD_INIT}" != "1" ]; then
	log "ENABLE_CLOUD_INIT is not 1: skipping cloud-init seed files"
	exit 0
fi

BOOT="${ROOTFS_DIR}/boot/firmware"
install -d "${BOOT}"

install -m 644 files/user-data "${BOOT}/user-data"

if [ -z "${WIFI_SSID}" ]; then
	log "WIFI_SSID is empty: keeping pi-gen's default network-config"
	exit 0
fi

if [ -z "${WPA_COUNTRY:-}" ]; then
	echo "WIFI_SSID is set but WPA_COUNTRY is empty. Set WPA_COUNTRY in config;" >&2
	echo "without it the Wi-Fi radio stays blocked on first boot." >&2
	exit 1
fi
if [ -n "${WIFI_PASSWORD}" ] && { [ "${#WIFI_PASSWORD}" -lt 8 ] || [ "${#WIFI_PASSWORD}" -gt 63 ]; }; then
	echo "WIFI_PASSWORD must be 8 to 63 characters, or empty for an open network." >&2
	exit 1
fi
if [[ ! "${WIFI_INTERFACE}" =~ ^[A-Za-z0-9_-]+$ ]]; then
	echo "WIFI_INTERFACE '${WIFI_INTERFACE}' is not a valid interface name." >&2
	exit 1
fi

# Layout follows the Raspberry Pi example for Trixie:
# https://www.raspberrypi.com/news/cloud-init-on-raspberry-pi-os/
{
	cat files/network-config.header
	printf 'network:\n'
	printf '  version: 2\n'
	printf '  wifis:\n'
	printf '    renderer: NetworkManager\n'
	printf '    %s:\n' "${WIFI_INTERFACE}"
	printf '      dhcp4: true\n'
	printf '      optional: true\n'
	printf '      regulatory-domain: %s\n' "$(yaml_dq "${WPA_COUNTRY}")"
	printf '      access-points:\n'
	printf '        %s:\n' "$(yaml_dq "${WIFI_SSID}")"
	if [ -n "${WIFI_PASSWORD}" ]; then
		printf '          password: %s\n' "$(yaml_dq "${WIFI_PASSWORD}")"
	fi
	if [ "${WIFI_HIDDEN}" = "1" ]; then
		printf '          hidden: true\n'
	fi
} > "${BOOT}/network-config"
chmod 644 "${BOOT}/network-config"

log "Wrote Wi-Fi configuration (interface ${WIFI_INTERFACE}) to network-config"
