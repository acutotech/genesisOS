#!/bin/bash -e
# Nice-to-have packages whose absence must not fail the build (fonts for the
# glass theme). Everything mandatory is in 00-packages.

# shellcheck source=../lib.sh
source "${STAGE_DIR}/lib.sh"

on_chroot << EOF
for pkg in fonts-inter fonts-jetbrains-mono blueman; do
	if ! apt-get -o Acquire::Retries=3 install -y "\$pkg"; then
		echo "WARNING: optional package \$pkg is not installable; continuing without it"
	fi
done
EOF
