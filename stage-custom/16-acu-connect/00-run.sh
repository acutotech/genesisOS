#!/bin/bash -e
# Acu Connect client: remote shell and desktop access to the box from
# https://connect.acutrotech.com (Acutro Technologies' package, kept unchanged in
# files/acu-connect-raspberry-pi; its own install.sh and user unit are not used).
#
# Built so that apt upgrades and a new system Python cannot break it:
#   /opt/acu-connect/python   its own CPython (python-build-standalone, pinned by
#                             SHA-256), never /usr/bin/python3 or Debian's python3-*
#   its packages              pip --require-hashes --only-binary=:all: from
#                             files/requirements.lock, into that Python only
#   /opt/acu-connect/app      the client (acu_connect), found through a .pth file
# acu-connect.service runs it at every boot as the desktop user (uid 1000, which
# survives Genesis Imager's first-boot rename) and restarts it forever; until the
# box is linked with "acu-connect signin" it waits instead of failing. The screen
# tools it uses (grim, ffmpeg, wtype, ...) are optional: without one, desktop
# sharing loses that ability and the rest keeps working.

# shellcheck source=../lib.sh
source "${STAGE_DIR}/lib.sh"

PKG=files/acu-connect-raspberry-pi
case "${ACU_CONNECT}" in
	0) log "Acu Connect: not installed (ACU_CONNECT=0)"; exit 0 ;;
	1) ;;
	*) echo "16-acu-connect: ACU_CONNECT=${ACU_CONNECT} is not 0 or 1" >&2; exit 1 ;;
esac
if [ ! -f "${PKG}/acu_connect/__main__.py" ] || [ ! -f files/requirements.lock ]; then
	echo "16-acu-connect: ${PKG} or files/requirements.lock is missing" >&2
	exit 1
fi
if [ "${ARCH:-arm64}" != "arm64" ]; then
	log "Acu Connect: its Python is only pinned for arm64 (ARCH=${ARCH}); not installed"
	exit 0
fi
if [ -n "${ACU_CONNECT_SERVER}" ] && ! [[ "${ACU_CONNECT_SERVER}" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~/-]*)?$ ]]; then
	echo "16-acu-connect: ACU_CONNECT_SERVER=${ACU_CONNECT_SERVER} is not a plain https:// URL" >&2
	exit 1
fi

# Its own CPython: a series every pinned package has an arm64 wheel for
# (scripts/acu-connect-lock.py checks), and a python-build-standalone build of it.
ACU_PYTHON="${ACU_PYTHON:-3.14.9}"
ACU_PYTHON_PBS_TAG="${ACU_PYTHON_PBS_TAG:-20261021}"
ACU_PYTHON_PBS_SHA256="${ACU_PYTHON_PBS_SHA256:-6f77731d85a14ed848dd4041478cd3ed511523caa3d896b84b436a7edf13cf62}"
PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${ACU_PYTHON_PBS_TAG}/cpython-${ACU_PYTHON}+${ACU_PYTHON_PBS_TAG}-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz"

install -d -m 755 "${ROOTFS_DIR}/opt/acu-connect/app"
rm -rf "${ROOTFS_DIR}/opt/acu-connect/app/acu_connect"
cp -R "${PKG}/acu_connect" "${ROOTFS_DIR}/opt/acu-connect/app/acu_connect"
find "${ROOTFS_DIR}/opt/acu-connect/app" -name __pycache__ -prune -exec rm -rf {} +
chmod -R u=rwX,go=rX "${ROOTFS_DIR}/opt/acu-connect/app"
install -m 644 "${PKG}/LICENSE" "${PKG}/README.md" files/requirements.lock "${ROOTFS_DIR}/opt/acu-connect/"
install -D -m 755 files/acu-connect-service "${ROOTFS_DIR}/usr/lib/acu-connect/acu-connect-service"
install -D -m 755 files/acu-connect "${ROOTFS_DIR}/usr/bin/acu-connect"
install -D -m 644 files/acu-connect.service "${ROOTFS_DIR}/usr/lib/systemd/system/acu-connect.service"
install -D -m 644 files/acu-connect.default "${ROOTFS_DIR}/etc/default/acu-connect"
if [ -n "${ACU_CONNECT_SERVER}" ]; then
	sed -i "s|^#ACU_SERVER=.*|ACU_SERVER=${ACU_CONNECT_SERVER}|" "${ROOTFS_DIR}/etc/default/acu-connect"
fi

on_chroot << EOF
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::Retries=3 update
# screen capture and input for desktop sharing; optional, and not every release has each
for pkg in grim ffmpeg wlopm wlr-randr wlrctl wtype xdotool; do
	if ! apt-get install -y --no-install-recommends "\$pkg" > /dev/null; then
		echo "acu-connect: \$pkg is not available, skipping"
	fi
done
tmp="\$(mktemp -d)"
curl -fsSL --retry 3 -o "\$tmp/python.tgz" "${PYTHON_URL}"
echo "${ACU_PYTHON_PBS_SHA256}  \$tmp/python.tgz" | sha256sum -c -
rm -rf /opt/acu-connect/python
mkdir -p /opt/acu-connect/python
tar -xzf "\$tmp/python.tgz" -C /opt/acu-connect/python --strip-components=1
rm -rf "\$tmp"
py=/opt/acu-connect/python/bin/python3
"\$py" -I -m pip install --no-cache-dir --disable-pip-version-check --no-warn-script-location \
	--require-hashes --only-binary=:all: --no-deps -r /opt/acu-connect/requirements.lock
echo /opt/acu-connect/app > "\$("\$py" -I -c 'import sysconfig; print(sysconfig.get_path("purelib"))')/acu-connect.pth"
# compiled now: the service user cannot write next to these files
"\$py" -I -m compileall -q -j 0 /opt/acu-connect/python/lib /opt/acu-connect/app
"\$py" -I -c 'import aiortc, av, cryptography, websockets, acu_connect.daemon, acu_connect; print("acu-connect", acu_connect.VERSION, "on Python", __import__("sys").version.split()[0])'
install -d -m 700 -o 1000 -g 1000 /var/lib/acu-connect
systemctl enable acu-connect.service
EOF
log "Acu Connect client: /opt/acu-connect on its own Python ${ACU_PYTHON}; acu-connect.service waits for \"acu-connect signin\""
