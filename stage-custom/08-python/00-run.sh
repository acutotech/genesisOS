#!/bin/bash -e
# Python layout:
#   python3  -> the OS interpreter (3.13 on trixie). Untouched: apt, raspi-config
#               and the desktop depend on it.
#   python   -> CPython ${PYTHON_PRIMARY} (3.10 by default) from python-build-standalone,
#               installed under /opt/python, the "default" for user work and VS Code.
#   uv       -> fast installer that can add further Python versions later
#               (uv python install 3.12).
#
# Only aarch64 builds are pinned here; other architectures skip this step.

# shellcheck source=../lib.sh
source "${STAGE_DIR}/lib.sh"

PY_VERSION="${PYTHON_PRIMARY}"
PY_PBS_TAG="${PYTHON_PBS_TAG:-20261009}"
PY_SHA256="${PYTHON_PBS_SHA256:-ecbe978a7ecbeb3a9aed67bb5d161180701513b4fa8101037b2a3f1ac11e96be}"
UV_VERSION="${UV_VERSION:-0.13.1}"
UV_SHA256="${UV_SHA256:-9041c73c99fdc5892cf128140beb354eca9231d502068cc36e6cb53a8e59ad14}"

if [ "${ARCH:-arm64}" != "arm64" ]; then
	log "Python ${PY_VERSION} is only pinned for arm64 (ARCH=${ARCH}); skipping"
	exit 0
fi
if [ "${PY_VERSION}" != "3.10.22" ] && [ -z "${PYTHON_PBS_SHA256:-}" ]; then
	echo "PYTHON_PRIMARY=${PY_VERSION} needs PYTHON_PBS_TAG and PYTHON_PBS_SHA256 for its tarball" >&2
	exit 1
fi
PY_SERIES="${PY_VERSION%.*}"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_PBS_TAG}/cpython-${PY_VERSION}+${PY_PBS_TAG}-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz"
UV_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-aarch64-unknown-linux-gnu.tar.gz"

on_chroot << EOF
set -e
tmp="\$(mktemp -d)"
curl -fsSL --retry 3 -o "\$tmp/python.tgz" "${PY_URL}"
echo "${PY_SHA256}  \$tmp/python.tgz" | sha256sum -c -
rm -rf "/opt/python/${PY_SERIES}"
mkdir -p "/opt/python/${PY_SERIES}"
tar -xzf "\$tmp/python.tgz" -C "/opt/python/${PY_SERIES}" --strip-components=1
ln -sfn "/opt/python/${PY_SERIES}/bin/python${PY_SERIES}" "/usr/local/bin/python${PY_SERIES}"
ln -sfn "/opt/python/${PY_SERIES}/bin/pip${PY_SERIES}" "/usr/local/bin/pip${PY_SERIES}"
ln -sfn "/opt/python/${PY_SERIES}/bin/python${PY_SERIES}" /usr/local/bin/python
ln -sfn "/opt/python/${PY_SERIES}/bin/pip${PY_SERIES}" /usr/local/bin/pip
curl -fsSL --retry 3 -o "\$tmp/uv.tgz" "${UV_URL}"
echo "${UV_SHA256}  \$tmp/uv.tgz" | sha256sum -c -
tar -xzf "\$tmp/uv.tgz" -C "\$tmp"
install -m 755 "\$tmp"/uv-aarch64-unknown-linux-gnu/uv /usr/local/bin/uv
install -m 755 "\$tmp"/uv-aarch64-unknown-linux-gnu/uvx /usr/local/bin/uvx
rm -rf "\$tmp"
/usr/local/bin/python --version
/usr/bin/python3 --version
/usr/local/bin/uv --version
EOF

# uv keeps the interpreters it installs under the user's ~/.local/share/uv (its
# default); /opt/python is root-owned and only holds the image's own CPython.
rm -f "${ROOTFS_DIR}/etc/profile.d/genesis-python.sh"
log "Python ${PY_VERSION} will be installed as /usr/local/bin/python (python3 stays the OS interpreter)"
