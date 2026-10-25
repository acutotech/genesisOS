#!/bin/bash -e
# Python layout. Two kinds of Python, kept apart on purpose:
#
#   System Python   /usr/bin/python3 (Debian trixie's 3.13). apt, raspi-config,
#                   the desktop, Genesis Shell and Genesis Settings run on it;
#                   their compiled modules (GTK bindings, ...) are built for it,
#                   so it is neither replaced nor upgraded here. Debian marks it
#                   EXTERNALLY-MANAGED: pip refuses to install into it.
#   User Pythons    CPython builds from python-build-standalone under
#                   /opt/python/<series>, one directory per version:
#                     python3.10 / pip3.10   (PYTHON_PRIMARY, 3.10.22 = the last 3.10)
#                     python3.15 / pip3.15   (PYTHON_LATEST, the newest stable)
#                   "python" and "pip" point at PYTHON_PRIMARY, the user default
#                   and VS Code's interpreter. pip run by the user installs into
#                   ~/.local/lib/pythonX.Y, never into the system Python.
#   uv              fast installer and version manager for further versions
#                   (uv python install 3.12); they land in ~/.local/share/uv.
#
# Only aarch64 builds are pinned here; other architectures skip this step.

# shellcheck source=../lib.sh
source "${STAGE_DIR}/lib.sh"

UV_VERSION="${UV_VERSION:-0.13.1}"
UV_SHA256="${UV_SHA256:-9041c73c99fdc5892cf128140beb354eca9231d502068cc36e6cb53a8e59ad14}"

# version -> "python-build-standalone release tag:SHA-256 of the aarch64 install_only_stripped tarball"
pin_for() {
	case "$1" in
		3.10.22) echo "20261009:ecbe978a7ecbeb3a9aed67bb5d161180701513b4fa8101037b2a3f1ac11e96be" ;;
		3.14.9) echo "20261021:6f77731d85a14ed848dd4041478cd3ed511523caa3d896b84b436a7edf13cf62" ;;
		3.15.1) echo "20261021:6efe65c43ae0bdebebeab4607f91a0053dd36d5e51cfd69a791d544e9b122be7" ;;
		*) return 1 ;;
	esac
}

if [ "${ARCH:-arm64}" != "arm64" ]; then
	log "User Pythons are only pinned for arm64 (ARCH=${ARCH}); skipping"
	exit 0
fi

# "role version tag sha" per user Python. An unpinned version needs its tag and
# SHA-256 from the environment (PYTHON_PRIMARY_PBS_TAG/_SHA256, PYTHON_LATEST_PBS_TAG/_SHA256;
# PYTHON_PBS_TAG/PYTHON_PBS_SHA256 still work for the primary one).
PLAN=()
for role in PRIMARY LATEST; do
	var="PYTHON_${role}"
	version="${!var:-}"
	[ -n "${version}" ] || continue
	tag_var="PYTHON_${role}_PBS_TAG"
	sha_var="PYTHON_${role}_PBS_SHA256"
	tag="${!tag_var:-}"
	sha="${!sha_var:-}"
	if [ "${role}" = PRIMARY ]; then
		tag="${tag:-${PYTHON_PBS_TAG:-}}"
		sha="${sha:-${PYTHON_PBS_SHA256:-}}"
	fi
	if [ -z "${tag}" ] || [ -z "${sha}" ]; then
		if ! pin="$(pin_for "${version}")"; then
			echo "${var}=${version} is not pinned: set ${tag_var} and ${sha_var} (python-build-standalone release tag and tarball SHA-256)" >&2
			exit 1
		fi
		tag="${tag:-${pin%%:*}}"
		sha="${sha:-${pin#*:}}"
	fi
	# A user Python of the system's own series (3.13 on trixie) would be "python3.13"/"pip3.13" first
	# in PATH and share ~/.local/lib/python3.13 with the system Python, so a user's pip install
	# could replace packages the desktop runs on.
	if [ -e "${ROOTFS_DIR}/usr/bin/python${version%.*}" ]; then
		echo "${var}=${version}: python${version%.*} is the system Python's series; choose another version" >&2
		exit 1
	fi
	PLAN+=("${role} ${version} ${tag} ${sha}")
done
[ "${#PLAN[@]}" -gt 0 ] || { log "No user Python requested (PYTHON_PRIMARY and PYTHON_LATEST empty)"; exit 0; }

PRIMARY_SERIES="${PYTHON_PRIMARY%.*}"
INSTALL_LINES=""
for entry in "${PLAN[@]}"; do
	read -r role version tag sha <<< "${entry}"
	series="${version%.*}"
	url="https://github.com/astral-sh/python-build-standalone/releases/download/${tag}/cpython-${version}+${tag}-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz"
	INSTALL_LINES+="install_python ${series} ${url} ${sha}"$'\n'
	log "User Python ${version} (${role,,}) -> /opt/python/${series}, python${series}, pip${series}"
done
UV_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-aarch64-unknown-linux-gnu.tar.gz"

on_chroot << EOF
set -e
tmp="\$(mktemp -d)"
install_python() {
	series="\$1"; url="\$2"; sha="\$3"
	curl -fsSL --retry 3 -o "\$tmp/python-\$series.tgz" "\$url"
	echo "\$sha  \$tmp/python-\$series.tgz" | sha256sum -c -
	rm -rf "/opt/python/\$series"
	mkdir -p "/opt/python/\$series"
	tar -xzf "\$tmp/python-\$series.tgz" -C "/opt/python/\$series" --strip-components=1
	rm -f "\$tmp/python-\$series.tgz"
	ln -sfn "/opt/python/\$series/bin/python\$series" "/usr/local/bin/python\$series"
	ln -sfn "/opt/python/\$series/bin/pip\$series" "/usr/local/bin/pip\$series"
	"/usr/local/bin/python\$series" --version
}
${INSTALL_LINES}
if [ -n "${PRIMARY_SERIES}" ] && [ -x "/opt/python/${PRIMARY_SERIES}/bin/python${PRIMARY_SERIES}" ]; then
	ln -sfn "/opt/python/${PRIMARY_SERIES}/bin/python${PRIMARY_SERIES}" /usr/local/bin/python
	ln -sfn "/opt/python/${PRIMARY_SERIES}/bin/pip${PRIMARY_SERIES}" /usr/local/bin/pip
fi
curl -fsSL --retry 3 -o "\$tmp/uv.tgz" "${UV_URL}"
echo "${UV_SHA256}  \$tmp/uv.tgz" | sha256sum -c -
tar -xzf "\$tmp/uv.tgz" -C "\$tmp"
install -m 755 "\$tmp"/uv-aarch64-unknown-linux-gnu/uv /usr/local/bin/uv
install -m 755 "\$tmp"/uv-aarch64-unknown-linux-gnu/uvx /usr/local/bin/uvx
rm -rf "\$tmp"
# the system Python must stay the OS's: nothing of ours may shadow python3
if [ -e /usr/local/bin/python3 ]; then echo "/usr/local/bin/python3 would shadow the system Python" >&2; exit 1; fi
/usr/bin/python3 --version
/usr/local/bin/uv --version
EOF

install -D -m 755 files/genesis-python "${ROOTFS_DIR}/usr/local/bin/genesis-python"
# uv keeps the interpreters it installs under the user's ~/.local/share/uv (its
# default); /opt/python is root-owned and only holds the image's own builds.
rm -f "${ROOTFS_DIR}/etc/profile.d/genesis-python.sh"
log "User Pythons: ${PYTHON_PRIMARY:-none} (default python) and ${PYTHON_LATEST:-none}; python3 stays the system interpreter"
