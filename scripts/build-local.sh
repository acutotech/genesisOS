#!/usr/bin/env bash
# Build the custom Raspberry Pi OS image on this machine.
#
# Default mode runs pi-gen inside Docker (pi-gen's build-docker.sh). It needs
# Docker, about 20 GB of free disk and roughly 30-90 minutes. On an x86-64 host
# the arm64 chroot runs under QEMU (binfmt_misc + qemu-user-static), which is
# several times slower than an arm64 host.
#
# --native runs pi-gen's build.sh directly: Debian-based host (Raspberry Pi OS
# 64-bit works), sudo, and the packages from pi-gen/depends installed.
#
# Options
#   --native            build without Docker
#   --continue          resume a failed Docker build (CONTINUE=1)
#   --pi-gen-ref REF    pi-gen branch or tag (default: $PI_GEN_REF or "arm64")
#   -h, --help          this text
#
# Environment
#   PI_GEN_REPO   pi-gen git URL      (default https://github.com/RPi-Distro/pi-gen.git)
#   PI_GEN_REF    branch or tag       (default arm64; pin e.g. 2026-10-06-raspios-trixie-arm64)
#   PI_GEN_DIR    checkout location   (default ./pi-gen, git-ignored)
#
# Secrets are read from the environment or from stage-custom/secrets.env:
#   FIRST_USER_PASS          password of FIRST_USER_NAME (enables direct login)
#   PUBKEY_SSH_FIRST_USER    authorized_keys content for FIRST_USER_NAME
#   WIFI_SSID, WIFI_PASSWORD Wi-Fi network baked into network-config
# Any knob from stage-custom/custom.conf (ENABLE_I2C=1, ...) can be set in the
# environment as well; it is forwarded into the build container.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE_DIR="${REPO_DIR}/stage-custom"
BUILD_DIR="${REPO_DIR}/build"
PI_GEN_DIR="${PI_GEN_DIR:-${REPO_DIR}/pi-gen}"
PI_GEN_REPO="${PI_GEN_REPO:-https://github.com/RPi-Distro/pi-gen.git}"
PI_GEN_REF="${PI_GEN_REF:-arm64}"
CONTINUE="${CONTINUE:-0}"
NATIVE=0

# Every knob of stage-custom/custom.conf (the VAR="${VAR:-default}" lines) may be
# overridden from the environment; the list is derived so it cannot go stale.
mapfile -t FORWARDED_VARS < <(sed -nE 's/^([A-Z_]+)="\$\{\1:-.*/\1/p' "${STAGE_DIR}/custom.conf")

usage() {
	sed -n '2,/^set -euo pipefail/{/^set -euo pipefail/d;s/^# \{0,1\}//;p}' "${BASH_SOURCE[0]}"
}

die() {
	echo "error: $*" >&2
	exit 1
}

while [ $# -gt 0 ]; do
	case "$1" in
		--native) NATIVE=1 ;;
		--continue) CONTINUE=1 ;;
		--pi-gen-ref)
			[ $# -ge 2 ] || die "--pi-gen-ref needs a value"
			PI_GEN_REF="$2"
			shift
			;;
		-h|--help)
			usage
			exit 0
			;;
		*)
			usage >&2
			die "unknown option: $1"
			;;
	esac
	shift
done

case "${REPO_DIR}" in
	*" "*) die "the repository path contains a space; debootstrap cannot work with that" ;;
esac
[ -d "${STAGE_DIR}" ] || die "stage directory ${STAGE_DIR} not found"
command -v git >/dev/null 2>&1 || die "git is required"
if [ "${NATIVE}" = "0" ]; then
	command -v docker >/dev/null 2>&1 || die "docker not found; install Docker or use --native on a Debian-based host"
fi

# Secrets file: load it, then put back anything that was already set in the
# environment so that the environment wins over the file.
if [ -f "${STAGE_DIR}/secrets.env" ]; then
	declare -A env_backup=()
	for var in FIRST_USER_PASS PUBKEY_SSH_FIRST_USER "${FORWARDED_VARS[@]}"; do
		if [ -n "${!var+set}" ]; then
			env_backup["${var}"]="${!var}"
		fi
	done
	# shellcheck source=/dev/null
	source "${STAGE_DIR}/secrets.env"
	for var in "${!env_backup[@]}"; do
		printf -v "${var}" '%s' "${env_backup[${var}]}"
	done
	unset env_backup
fi

# pi-gen feeds FIRST_USER_PASS to chpasswd through an unquoted heredoc, so a few
# characters would silently change the password.
if [[ "${FIRST_USER_PASS:-}" == *[\"\$\`\\]* || "${FIRST_USER_PASS:-}" == *$'\n'* ]]; then
	die 'FIRST_USER_PASS must not contain " $ ` \ or newlines (pi-gen limitation)'
fi
if [ -n "${PUBKEY_SSH_FIRST_USER:-}" ] && [ -z "${FIRST_USER_PASS:-}" ]; then
	echo "warning: PUBKEY_SSH_FIRST_USER is set but FIRST_USER_PASS is not. pi-gen leaves the user's shell" >&2
	echo "         as nologin until a password exists, so SSH key logins are refused. Set FIRST_USER_PASS." >&2
fi

# --- pi-gen checkout ---------------------------------------------------------
if [ ! -d "${PI_GEN_DIR}/.git" ]; then
	echo "Cloning pi-gen (${PI_GEN_REF}) into ${PI_GEN_DIR}"
	git clone --quiet --depth 1 --branch "${PI_GEN_REF}" "${PI_GEN_REPO}" "${PI_GEN_DIR}"
else
	echo "Updating pi-gen checkout to ${PI_GEN_REF}"
	git -C "${PI_GEN_DIR}" fetch --quiet --depth 1 origin "${PI_GEN_REF}"
	git -C "${PI_GEN_DIR}" checkout --quiet --detach FETCH_HEAD
fi
echo "pi-gen commit: $(git -C "${PI_GEN_DIR}" rev-parse --short HEAD)"

[ -f "${PI_GEN_DIR}/build-docker.sh" ] || die "${PI_GEN_DIR} does not look like pi-gen"

# Upstream stages to build first (from config), and only stage-custom exports an image.
# shellcheck source=/dev/null
BASE_STAGES="$(. "${REPO_DIR}/config" && echo "${BASE_STAGES:-stage0 stage1 stage2}")"
for stage in ${BASE_STAGES}; do
	[ -d "${PI_GEN_DIR}/${stage}" ] || die "pi-gen has no stage '${stage}' (BASE_STAGES in config)"
	touch "${PI_GEN_DIR}/${stage}/SKIP_IMAGES"
done

# Generated files below hold secrets: create them unreadable to others from the start.
umask 077

# --- build metadata and forwarded overrides for the stage -------------------
{
	printf '# Generated by scripts/build-local.sh. Do not commit.\n'
	printf 'CUSTOM_GIT_SHA=%q\n' "$(git -C "${REPO_DIR}" rev-parse HEAD 2>/dev/null || echo unknown)"
	printf 'CUSTOM_GIT_REF=%q\n' "$(git -C "${REPO_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
	printf 'CUSTOM_BUILD_URL=%q\n' "local:$(hostname)"
	printf '\n# Environment overrides forwarded into the build container.\n'
	for var in "${FORWARDED_VARS[@]}"; do
		if [ -n "${!var+set}" ]; then
			printf '%s=%q\n' "${var}" "${!var}"
		fi
	done
} > "${STAGE_DIR}/build-info.env"
chmod 600 "${STAGE_DIR}/build-info.env"

# --- pi-gen config -------------------------------------------------------------
mkdir -p "${BUILD_DIR}"
CONFIG_FILE="${BUILD_DIR}/config"
# The composed config carries the password and SSH key; do not leave it behind.
trap 'rm -f "${CONFIG_FILE}"' EXIT
{
	cat "${REPO_DIR}/config"
	printf '\n# --- generated by scripts/build-local.sh ---\n'
	printf 'STAGE_LIST="%s %s"\n' "${BASE_STAGES}" "${STAGE_DIR}"
	if [ -n "${FIRST_USER_PASS:-}" ]; then
		printf 'FIRST_USER_PASS=%q\n' "${FIRST_USER_PASS}"
		printf 'DISABLE_FIRST_BOOT_USER_RENAME="1"\n'
	else
		printf 'DISABLE_FIRST_BOOT_USER_RENAME="0"\n'
	fi
	if [ -n "${PUBKEY_SSH_FIRST_USER:-}" ]; then
		printf 'PUBKEY_SSH_FIRST_USER=%q\n' "${PUBKEY_SSH_FIRST_USER}"
	else
		# pi-gen refuses PUBKEY_ONLY_SSH=1 without a key.
		printf 'PUBKEY_ONLY_SSH="0"\n'
	fi
} > "${CONFIG_FILE}"
chmod 600 "${CONFIG_FILE}"

# shellcheck source=/dev/null
EFFECTIVE_IMG_NAME="$(. "${CONFIG_FILE}" && echo "${IMG_NAME}")"
# shellcheck source=/dev/null
EFFECTIVE_USER="$(. "${CONFIG_FILE}" && echo "${FIRST_USER_NAME}")"
PASSWORD_STATE="NOT set: first-boot user wizard"
[ -n "${FIRST_USER_PASS:-}" ] && PASSWORD_STATE="set"
KEY_STATE="not set"
[ -n "${PUBKEY_SSH_FIRST_USER:-}" ] && KEY_STATE="set"
WIFI_STATE="not configured"
[ -n "${WIFI_SSID:-}" ] && WIFI_STATE="SSID ${WIFI_SSID}"

echo
echo "Image name     : ${EFFECTIVE_IMG_NAME}"
echo "Config         : ${CONFIG_FILE}"
echo "Stages         : ${BASE_STAGES} + stage-custom"
echo "Custom stage   : ${STAGE_DIR}"
echo "First user     : ${EFFECTIVE_USER} (password ${PASSWORD_STATE}; SSH key ${KEY_STATE})"
echo "Wi-Fi          : ${WIFI_STATE}"
echo

# --- build ---------------------------------------------------------------------
cd "${PI_GEN_DIR}"
if [ "${NATIVE}" = "1" ]; then
	sudo ./build.sh -c "${CONFIG_FILE}"
else
	# Mount the stage at the same path inside the container so STAGE_LIST resolves.
	CONTINUE="${CONTINUE}" PIGEN_DOCKER_OPTS="-v ${STAGE_DIR}:${STAGE_DIR}" \
		./build-docker.sh -c "${CONFIG_FILE}"
fi

echo
echo "Done. Output in ${PI_GEN_DIR}/deploy:"
ls -lh "${PI_GEN_DIR}/deploy"
