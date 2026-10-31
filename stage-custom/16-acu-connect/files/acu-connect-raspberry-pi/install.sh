#!/usr/bin/env bash
# Installs the Acu Connect client on a Raspberry Pi or other Debian-based Linux,
# links it to your account and starts it. A device that is already linked stays linked.
#
#   bash install.sh
#
# Links to https://connect.acutrotech.com, unless you give another address.
# Run it from this folder, as the desktop user (not root).
set -euo pipefail

SERVER="${1:-https://connect.acutrotech.com}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.local/share/acu-connect-py"
BIN_DIR="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user (it uses sudo only for apt)." >&2
  exit 1
fi

echo "==> Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv
# screen capture + input: grim + wlrctl/wtype (or ydotool) for Wayland, ffmpeg/xdotool for X11.
# One at a time: not every release packages all of them (trixie has no ydotool).
for pkg in grim ffmpeg wlopm wlr-randr wlrctl wtype ydotool xdotool; do
  sudo apt-get install -y -qq "$pkg" >/dev/null 2>&1 || echo "note: $pkg is not available, skipping"
done

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo "Python 3.11 or newer is required (Raspberry Pi OS Bookworm or newer)." >&2
  exit 1
fi

echo "==> Installing the client to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$UNIT_DIR"
python3 -m venv --clear "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --disable-pip-version-check --only-binary=:all: -r "$SOURCE_DIR/requirements.txt"
rm -rf "${INSTALL_DIR:?}/acu_connect"
cp -R "$SOURCE_DIR/acu_connect" "$INSTALL_DIR/acu_connect"

rm -f "${BIN_DIR:?}/acu-connect"
cat > "$BIN_DIR/acu-connect" <<WRAPPER
#!/bin/sh
PYTHONPATH="$INSTALL_DIR" exec "$INSTALL_DIR/venv/bin/python" -m acu_connect "\$@"
WRAPPER
chmod +x "$BIN_DIR/acu-connect"

echo "==> Linking this device to your account"
"$BIN_DIR/acu-connect" signin --server "$SERVER"

echo "==> Starting the service"
cp "$SOURCE_DIR/acu-connect.service" "$UNIT_DIR/acu-connect.service"
sudo loginctl enable-linger "$USER" # keep running when you are logged out
systemctl --user daemon-reload
systemctl --user enable acu-connect.service >/dev/null
systemctl --user restart acu-connect.service

echo
echo "Done. Check it with:  systemctl --user status acu-connect   or   acu-connect status"
