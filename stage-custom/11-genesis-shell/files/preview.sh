#!/bin/bash
# Headless smoke test of the Genesis session: starts labwc on a virtual output
# (no GPU, no display, no seat), waits for Genesis Shell, drives it over D-Bus
# and saves screenshots plus the shell state. Exit status 0 means the desktop
# came up and every screenshot shows content.
#
#   preview.sh OUTPUT_DIR
#
# Runs on a Raspberry Pi over SSH, in the CI container, or inside a chroot of
# the built image. Needs: labwc, grim, wlr-randr, dbus-run-session, python3.
set -u

OUT="${1:-$PWD/genesis-preview}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

if [ -z "${GENESIS_PREVIEW_INNER:-}" ]; then
	export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/genesis-preview-$(id -u)}"
	mkdir -p "$XDG_RUNTIME_DIR"
	chmod 700 "$XDG_RUNTIME_DIR"
	export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1 WLR_HEADLESS_OUTPUTS=1
	export XDG_CURRENT_DESKTOP="Genesis:LXDE-pi" XDG_SESSION_TYPE=wayland XDG_SESSION_DESKTOP=genesis
	export GENESIS_PREVIEW_INNER=1 GENESIS_SHELL_DEBUG=1
	# no I2C here: the sensor bar reads its "bus" from this file (hex addresses)
	export GENESIS_SENSORS_FAKE="$OUT/sensors.fake"
	: > "$GENESIS_SENSORS_FAKE"
	# desktop icons from a sample folder, not the user's own ~/Desktop
	export GENESIS_DESKTOP_DIR="$OUT/Desktop"
	mkdir -p "$GENESIS_DESKTOP_DIR/Projects" "$GENESIS_DESKTOP_DIR/Sensor Logs"
	printf 'time,sensor,value\n' > "$GENESIS_DESKTOP_DIR/readings.csv"
	printf 'Genesis OS notes\n' > "$GENESIS_DESKTOP_DIR/Notes.txt"
	if [ -f /usr/share/applications/lxterminal.desktop ]; then
		install -m 755 /usr/share/applications/lxterminal.desktop "$GENESIS_DESKTOP_DIR/Terminal.desktop"
	fi
	export HOME="${HOME:-/tmp}"
	# a private copy of the user's settings: the preview switches the wallpaper, and over
	# SSH the user's live desktop watches the real shell.conf. -L: a linked folder
	# (dotfile managers) is copied, never written through.
	user_config="${XDG_CONFIG_HOME:-$HOME/.config}"
	XDG_CONFIG_HOME="$(mktemp -d "$OUT/config.XXXXXX")" || exit 1
	export XDG_CONFIG_HOME
	for sub in genesis gtk-3.0 lxterminal; do
		if [ -d "$user_config/$sub" ]; then cp -RL "$user_config/$sub" "$XDG_CONFIG_HOME/" 2> /dev/null || true; fi
	done
	# what the checks below rely on, whatever the user turned off
	python3 - "$XDG_CONFIG_HOME/genesis/shell.conf" << 'CONF'
import configparser, os, sys
path = sys.argv[1]
conf = configparser.RawConfigParser(strict=False)
conf.optionxform = str
try:
    conf.read(path, encoding="utf-8")
except (configparser.Error, UnicodeDecodeError):
    conf = configparser.RawConfigParser()
    conf.optionxform = str
wanted = {"sensors": {"enabled": "true", "notify": "true", "show_disconnected": "false"},
          "desktop": {"icons": "true", "show_home": "true", "show_trash": "true"}}
for section, values in wanted.items():
    if not conf.has_section(section):
        conf.add_section(section)
    for key, value in values.items():
        conf.set(section, key, value)
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as fh:
    conf.write(fh)
CONF
	unset WAYLAND_DISPLAY DISPLAY
	echo "preview: starting labwc headless, output in $OUT"
	# a private session bus, the compositor, and this script as the session client
	# (-S: session command, labwc >= 0.8; older releases only have -s, a startup
	# command, and are told to exit by the inner script instead)
	session_flag=-S
	labwc --help 2>&1 | grep -q -- '-S' || session_flag=-s
	dbus-run-session -- labwc -C /etc/xdg/genesis/labwc "$session_flag" "$0 $OUT" > "$OUT/compositor.log" 2>&1
	status=$?
	if [ -f "$OUT/result" ]; then
		status=$(cat "$OUT/result")
	else
		echo "preview: FAIL the session client never reported a result (compositor exit status $status)"
		status=1
	fi
	rm -rf "$XDG_CONFIG_HOME"   # the private copy made above (mktemp), nothing of the user's
	echo "preview: finished with status $status"
	echo "===== compositor.log (labwc + session client, without the picture) ====="
	sed -e '/BEGIN CONTACT SHEET BASE64/,/END CONTACT SHEET BASE64/d' "$OUT/compositor.log" || true
	echo "===== shell session.log ====="
	tail -n 120 "$OUT/session.log" 2> /dev/null || echo "(no session.log)"
	echo "===== contact sheet ====="
	sed -n '/BEGIN CONTACT SHEET BASE64/,/END CONTACT SHEET BASE64/p' "$OUT/compositor.log" || true
	exit "$status"
fi

# ---------------------------------------------------------------- inside the compositor
ctl() { genesis-shell-ctl "$@"; }
shot() {
	sleep "${2:-1.2}"
	if ctl shot "$OUT/$1"; then echo "preview: $1"; else echo "preview: screenshot $1 FAILED"; fi
	ctl state 2> /dev/null | python3 -c 'import json, sys
s = json.load(sys.stdin)
print("preview: state overlays=%s panels=%s" % (json.dumps(s.get("overlays")), json.dumps(s.get("panels"))))' 2> /dev/null || true
}

wlr-randr --output HEADLESS-1 --custom-mode 1920x1080 > /dev/null 2>&1 || true

ready=0
for _ in $(seq 1 60); do
	if ctl state > "$OUT/state.json" 2> /dev/null; then ready=1; break; fi
	sleep 1
done
if [ "$ready" != 1 ]; then
	echo "preview: FAIL genesis-shell did not come up"
	tail -n 60 "${XDG_CACHE_HOME:-$HOME/.cache}/genesis/session.log" 2> /dev/null
	echo 1 > "$OUT/result"
	exit 1
fi
sleep 4 # blurred wallpaper and icon caches

shot 01-desktop.png 0.5
ctl launcher;        shot 02-launcher.png 2;             ctl hide
ctl control-center;  shot 03-control-center.png 1.5;     ctl hide
ctl notifications;   shot 04-notification-center.png 1.5; ctl hide
ctl notify "Genesis OS" "The desktop shell is running headless. Everything in this picture is Genesis Shell."
shot 05-toast.png 1.5
ctl power;           shot 06-power.png 1.5;              ctl hide
ctl osd audio-volume-high-symbolic 0.65 "65%"; shot 07-osd.png 0.5
if command -v lxterminal > /dev/null 2>&1; then
	lxterminal > /dev/null 2>&1 &
	term=$!
	shot 08-window.png 3
	kill "$term" 2> /dev/null || true
	sleep 0.5
fi

# sensor boxes A (0x10), C (0x19) and H (0x2c) arrive: the bar fades in, one notification
echo "0x10 0x19 0x2c" > "$GENESIS_SENSORS_FAKE"
shot 09-sensors.png 2.5
ctl state > "$OUT/state-sensors.json" 2> /dev/null
ctl sensor C
shot 10-sensor-card.png 1.2
ctl state > "$OUT/state-card.json" 2> /dev/null
ctl sensor-close
# a light wallpaper: the desktop labels turn black
wallpaper_was=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("wallpaper") or "")' "$OUT/state.json" 2> /dev/null)
python3 - "$OUT/light-wallpaper.png" << 'LIGHTPNG'
import struct, sys, zlib
w, h = 1920, 1080
rows = []
for y in range(h):
    t = y / (h - 1)
    rows.append(b"\x00" + bytes((int(236 - 30 * t), int(232 - 24 * t), int(222 - 12 * t))) * w)
def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"".join(rows), 6)) + chunk(b"IEND", b"")
open(sys.argv[1], "wb").write(png)
LIGHTPNG
ctl wallpaper "$OUT/light-wallpaper.png" && shot 11-desktop-light.png 2.5
ctl state > "$OUT/state-light.json" 2> /dev/null
[ -n "$wallpaper_was" ] && ctl wallpaper "$wallpaper_was" 2> /dev/null
# the boxes leave: after two missed scans the bar fades out again
: > "$GENESIS_SENSORS_FAKE"
sleep 2.5
ctl state > "$OUT/state-final.json" 2> /dev/null
cp "${XDG_CACHE_HOME:-$HOME/.cache}/genesis/session.log" "$OUT/session.log" 2> /dev/null || true
echo "--- session.log (tail) ---"
tail -n 40 "$OUT/session.log" 2> /dev/null || true
echo "--- end session.log ---"

# One small picture of all screenshots, also dumped as base64 when asked (CI logs
# can be read without downloading artifacts).
python3 - "$OUT" << 'PY'
import glob, os, sys
out = sys.argv[1]
try:
    from PIL import Image
except ImportError:
    sys.exit(0)
files = sorted(glob.glob(os.path.join(out, "[0-9][0-9]-*.png")))
if not files:
    sys.exit(0)
thumbs = []
for f in files:
    try:
        thumbs.append(Image.open(f).convert("RGB").resize((640, 360)))
    except Exception:  # noqa: BLE001
        pass
cols, rows = 2, (len(thumbs) + 1) // 2
sheet = Image.new("RGB", (cols * 640 + (cols + 1) * 8, rows * 360 + (rows + 1) * 8), (12, 18, 28))
for i, t in enumerate(thumbs):
    sheet.paste(t, (8 + (i % cols) * 648, 8 + (i // cols) * 368))
sheet.save(os.path.join(out, "contact-sheet.jpg"), quality=80)
PY
if [ "${GENESIS_PREVIEW_PRINT_B64:-0}" = 1 ] && [ -f "$OUT/contact-sheet.jpg" ]; then
	echo "----- BEGIN CONTACT SHEET BASE64 -----"
	base64 -w 76 "$OUT/contact-sheet.jpg"
	echo "----- END CONTACT SHEET BASE64 -----"
fi

python3 - "$OUT" << 'PY'
import json, os, struct, sys, zlib

out = sys.argv[1]
failures = []

def png_stats(path):
    """(width, height, distinct-sample-count) without external libraries."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, width, height, idat = 8, 0, 0, b""
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, ctype = struct.unpack(">IIBB", body[:10])
            if depth != 8 or ctype not in (2, 6):
                return (width, height, -1)
            channels = 3 if ctype == 2 else 4
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * channels + 1
    samples = set()
    prev = bytearray(width * channels)
    for y in range(height):
        ftype = raw[y * stride]
        line = bytearray(raw[y * stride + 1:(y + 1) * stride])
        bpp = channels
        for i in range(len(line)):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if ftype == 1: line[i] = (line[i] + a) & 255
            elif ftype == 2: line[i] = (line[i] + b) & 255
            elif ftype == 3: line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif ftype == 4:
                p = a + b - c; pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = line
        if y % 12 == 0:
            for x in range(0, width * channels, channels * 16):
                samples.add(bytes(line[x:x + 3]))
    return (width, height, len(samples))

for name in sorted(os.listdir(out)):
    if not name.endswith(".png"):
        continue
    path = os.path.join(out, name)
    stats = png_stats(path)
    if stats is None:
        failures.append(f"{name}: not a PNG"); continue
    w, h, distinct = stats
    verdict = "ok" if distinct >= 24 else "BLANK"
    print(f"{name}: {w}x{h}, {distinct} distinct colours sampled -> {verdict}")
    if distinct < 24:
        failures.append(f"{name}: looks blank ({distinct} colours)")
    if w < 1280:
        failures.append(f"{name}: unexpected size {w}x{h}")

for required in ("01-desktop.png", "02-launcher.png", "03-control-center.png", "04-notification-center.png", "05-toast.png", "06-power.png",
                 "09-sensors.png", "10-sensor-card.png", "11-desktop-light.png"):
    if not os.path.exists(os.path.join(out, required)):
        failures.append(f"missing {required}")

try:
    with open(os.path.join(out, "state-final.json"), encoding="utf-8") as fh:
        state = json.load(fh)
    print("shell state:", json.dumps(state["services"]), "monitors:", state["monitors"], "apps:", state["apps"])
    if state["monitors"] < 1:
        failures.append("no panel created")
    if not state["services"]["notifications"]:
        failures.append("notification server not owned")
    if state["apps"] < 1:
        failures.append("application index empty")
except Exception as exc:  # noqa: BLE001
    failures.append(f"state unreadable: {exc}")


def load(name):
    with open(os.path.join(out, name), encoding="utf-8") as fh:
        return json.load(fh)


try:
    first, sensors, card, light, final = (load(n) for n in ("state.json", "state-sensors.json", "state-card.json", "state-light.json", "state-final.json"))
    print("sensors:", json.dumps(sensors["sensors"]), "card:", card["sensors"]["card"], "final bar:", final["sensors"]["bar_visible"])
    print("desktop (dark wallpaper):", json.dumps(sensors["desktop"]), "(light wallpaper):", json.dumps(light["desktop"]))
    if first["sensors"]["bar_visible"]:
        failures.append("sensor bar shown although no box was connected")
    if not sensors["sensors"]["bar_visible"] or sorted(sensors["sensors"]["connected"]) != ["A", "C", "H"]:
        failures.append(f"sensor bar did not show boxes A, C, H: {sensors['sensors']}")
    if "3 sensors connected" not in sensors.get("notification_log", []):
        failures.append(f"no notification for the connected boxes: {sensors.get('notification_log')}")
    if "3 sensors disconnected" not in final.get("notification_log", []):
        failures.append(f"no notification for the boxes that left: {final.get('notification_log')}")
    if card["sensors"]["card"] != "C":
        failures.append(f"sensor card for C not shown: {card['sensors']}")
    if final["sensors"]["bar_visible"]:
        failures.append("sensor bar still shown after every box left")
    if sensors["desktop"]["icons"] < 4:
        failures.append(f"desktop icons missing: {sensors['desktop']}")
    if sensors["desktop"]["light_text"] != sensors["desktop"]["icons"]:
        failures.append(f"labels on the dark wallpaper are not all white: {sensors['desktop']}")
    if light["desktop"]["dark_text"] != light["desktop"]["icons"]:
        failures.append(f"labels on the light wallpaper are not all black: {light['desktop']}")
except Exception as exc:  # noqa: BLE001
    failures.append(f"sensor/desktop state unreadable: {exc}")

if failures:
    print("preview: FAIL")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("preview: PASS")
PY
status=$?
echo "$status" > "$OUT/result"
ctl quit > /dev/null 2>&1 || true
# labwc -S ends the compositor when this script exits; be explicit in case it does not
(sleep 2 && labwc -e > /dev/null 2>&1) &
exit "$status"
