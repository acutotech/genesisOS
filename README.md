# Genesis OS

A custom Raspberry Pi OS image for the **NodoAI box**: 64-bit Raspberry Pi OS
(Debian trixie) running **Genesis Shell**, a desktop written for this image:
floating glass panel, full-screen app launcher, control center, notifications,
on-screen display, power dialog and wallpaper, all drawn by our own code on top
of labwc, the compositor Raspberry Pi ships and tests on the Pi 5. Plus the
Genesis logo on the boot splash and login screen, Visual Studio Code, Python
3.10 and 3.15 for your code (the OS keeps its own 3.13), Chromium, a Genesis
Settings app, and the hardware interfaces
switched on. Built with [pi-gen](https://github.com/RPi-Distro/pi-gen), the
tool that produces the official images, in **GitHub Actions** or **locally
with Docker**.

Output: `image_<date>-genesis-os-desktop.img.xz`, flashable with Raspberry
Pi Imager, plus a package list (`.info`), `SHA256SUMS`, and the screenshots
of the desktop that CI took inside the image before publishing it.

## What the image contains

| Area | Default | Where to change it |
| --- | --- | --- |
| Base | Raspberry Pi OS Desktop, trixie, arm64 (Pi 5 first; also Pi 4, 400, 500, CM4/CM5, Zero 2 W, Pi 3) | `BASE_STAGES` in `config` |
| Hostname | `nodobox`, reachable as `nodobox.local` | `config` |
| User | `nodoai`; the password is typed into **Genesis Imager** while flashing (the image itself has none) | Genesis Imager; `config` for the built-in name |
| Wi-Fi | `NodoAI`, passphrase typed into Genesis Imager; country `IN` | Genesis Imager; `config` |
| Locale, keyboard, time zone | `en_US.UTF-8`, `us`, `Asia/Kolkata` | `config` |
| Desktop | **Genesis** session: labwc + Genesis Shell (see below). Raspberry Pi's stock labwc desktop stays installed as a fallback, selectable on the login screen | `stage-custom/11-genesis-shell` |
| Look | dark glass: translucent panel and cards over a blurred copy of the wallpaper, 16px rounded corners, steel-blue accent, Inter + JetBrains Mono, Papirus icons, five wallpapers | `GENESIS_ACCENT`, `GENESIS_WALLPAPER` in `custom.conf`; Genesis Settings |
| Boot | Plymouth splash with the Genesis logo, straight into the desktop as `nodoai` | `ENABLE_BOOT_SPLASH`, `DESKTOP_AUTOLOGIN` |
| Apps | Visual Studio Code (Microsoft repository) with the Python extensions, Chromium as default browser, the Raspberry Pi desktop application set | `INSTALL_VSCODE`, `VSCODE_EXTENSIONS`, `DEFAULT_BROWSER` |
| Python | for you: `python` = CPython 3.10.22 (default, VS Code interpreter) and `python3.15` = 3.15.1, the newest stable release, both under `/opt/python`; for the system: `python3` = the OS's 3.13, left alone; `uv` for more versions; `genesis-python` lists them all | `PYTHON_PRIMARY`, `PYTHON_LATEST` |
| Interfaces | I2C, SPI, serial port (UART on GPIO 14/15, Pi 5 `uart0`), SSH, VNC (wayvnc) on; serial console and 1-Wire off | `ENABLE_*` in `custom.conf` |
| Settings | **Genesis Settings** (interfaces, system, appearance, connectivity, display and sound, software, about) plus Raspberry Pi Configuration | `stage-custom/12-genesis-settings` |
| Behaviour | persistent journal, automatic security updates, cloud-init seed files on the boot partition, screen blanks after 10 minutes | `custom.conf` |
| Remote access | **Acu Connect** client (shell and desktop from connect.acutrotech.com), on its own Python; starts at boot and waits until the box is linked with `acu-connect signin` | `ACU_CONNECT`, `ACU_CONNECT_SERVER` in `custom.conf`; `stage-custom/16-acu-connect` |

Why these choices, in two sentences each:

* **Our own shell, not our own compositor.** What a user sees and touches is
  the shell (panel, launcher, menus, notifications), so that is what we wrote;
  a Wayland compositor is years of work and would be less finished than
  labwc, which Raspberry Pi maintains and tunes for the Pi 5's GPU. The first
  attempt (Wayfire plus Raspberry Pi's panel) booted to a white screen and
  was replaced.
* **Glass without compositor blur.** labwc does not blur what is behind a
  window, so the shell draws each surface over a pre-blurred, darkened copy
  of the wallpaper; the panel, launcher, cards and dialogs look frosted at
  zero GPU cost, and windows under the panel are never there anyway because
  the panel reserves its space.
* **Two Pythons for you, one for the system, kept apart.** The system's
  Python is Debian's 3.13 at `/usr/bin/python3`: apt, raspi-config, the
  desktop shell and Genesis Settings run on it, and their GTK and apt modules
  are compiled for it, so it is the right version and stays untouched
  (`pip` refuses to install into it). Your Pythons are self-contained builds
  under `/opt/python/<version>` that nothing in the OS uses:
  3.10.22 as `python` / `pip` (also `python3.10`, `pip3.10`) and 3.15.1 as
  `python3.15` / `pip3.15`. `pip install` without a virtual environment goes
  to `~/.local`. A user Python of the system's own series (3.13) is refused
  at build time: it would share `~/.local/lib/python3.13` with the desktop.
  Two things to know: 3.10.22 is the last 3.10 release and the series is
  end-of-life, so plan to move your code on; and 3.15 is new, so some
  packages have no aarch64 wheels for it yet (`uv python install 3.14` is one
  command away). The Raspberry Pi libraries the OS ships (`gpiozero`,
  `rpi-lgpio`, `picamera2`) live in the system 3.13; in your Pythons install
  what you need with `pip install gpiozero rpi-lgpio`, or run `python3`.
* **Credentials come from the imager, not the image.** A password baked into
  the image is the same on every box and readable by anyone who has the
  image (this repository is public). Genesis Imager writes a salted hash of
  the password you type, plus Wi-Fi and the other settings, onto the card
  while flashing; the box applies them on first boot and erases them.

## Quick start (GitHub Actions)

1. Push this repository to GitHub. Public repositories get the native arm64
   runner (`ubuntu-24.04-arm`) for free; private ones can use it too, billed
   against the plan's minutes. **In a public repository the built image is
   public as well** (artifact and release), so the workflow refuses to bake
   `FIRST_USER_PASS` or `WIFI_PASSWORD` into it unless you set the repository
   variable `ALLOW_SECRETS_IN_PUBLIC_IMAGE` to `true`. For a product image with
   a known default password, make the repository private.
2. Nothing else is needed: the user's password and the Wi-Fi passphrase are
   entered in Genesis Imager when flashing. Only if you want them baked into
   the image instead (a private repository; every box then shares them), add
   repository secrets under *Settings → Secrets and variables → Actions*:

   | Secret | Value for the NodoAI box |
   | --- | --- |
   | `FIRST_USER_PASS` | the login password for `nodoai` (keep it out of the repository; it must not contain `"`, `$`, `` ` `` or `\`, a pi-gen limitation the workflow checks) |
   | `WIFI_SSID` | the network name the box joins (`NodoAI` for the product network) |
   | `WIFI_PASSWORD` | that network's passphrase |
   | `SSH_AUTHORIZED_KEYS` | optional: your public key(s) |

3. *Actions → Build image → Run workflow*, or push to `main`. The workflow
   first lints, dry-runs the stage and renders the shell headless on an arm64
   container (about 5 minutes; screenshots in the `genesis-shell-preview`
   artifact), then builds the image (about 20 minutes on the arm64 runner),
   then writes the image through Genesis Imager with a test user, runs the
   first boot inside it and checks the account, password, Wi-Fi and locale,
   starts the Genesis session as that user and screenshots it
   (`genesis-image-smoke` artifact). Any failure, or a blank desktop, fails
   the build.
4. Download the `genesis-os` artifact and unzip it, and the Genesis Imager app
   for your computer (`genesis-imager-windows-x64`, `-macos-arm64` or
   `-linux-x86_64` artifact; tagged releases carry both).
5. Flash it with **Genesis Imager** (next section): pick the image, the card,
   type the password and the Wi-Fi passphrase, *Write*.
6. Tag a commit `v1.2.3` and push the tag: the workflow attaches the image and
   the Genesis Imager apps to a GitHub release.

## Genesis Imager

The tool that writes Genesis OS onto a card **with your user, password and
Wi-Fi**, so the box boots straight into the desktop, on the network, with
nothing to type on it. One Python file using only the standard library
([`imager/genesis_imager.py`](imager/genesis_imager.py)); CI also builds it
into apps for Windows, macOS (Apple silicon) and Linux.

| Field | Default |
| --- | --- |
| Image | *Download latest* (the newest GitHub release, checksum-verified) or *Choose file* (`.img`, `.img.xz`, `.img.gz`, or the `.zip` artifact) |
| Storage | removable cards and USB drives only; the disk your computer runs from is never listed |
| User name, password | `nodoai`, password required (the image has none) |
| Host name | `nodobox` (`nodobox.local`) |
| Wi-Fi | `NodoAI`, passphrase, country `IN`, hidden network option |
| Time zone, keyboard | `Asia/Kolkata`, `us` |
| SSH | on; optional public keys; optional "key logins only" |

It writes the whole card, partition table last, reads it back to verify, and
ejects it. Your choices except the passwords are remembered for next time.
Before erasing anything it lists the disks again and stops if the selected
one changed (a card swapped since the list was made); the disk your computer
runs from is never offered, not even with *Show fixed disks*. It checks the
settings the way the box will: a user name that is already a system account
or group (`games`, `video`, ...), a Wi-Fi passphrase over 63 bytes, a
country that is not an ISO code (`UK` is `GB`), a time zone written in the
wrong letter case (`europe/london`) or in an area that does not exist, or "key
logins only" without a key are refused before writing. A zone your computer
does not know (its time zone data may be older than the box's) is passed on,
and the box reports it in `genesis-setup-result.txt` if it does not know it
either.

* **Windows:** run `genesis-imager-windows-x64.exe`; it asks for administrator
  rights (needed to write a disk). SmartScreen may warn about an unsigned app:
  *More info → Run anyway*.
* **macOS:** unzip, then right-click *Genesis Imager* → *Open* the first time
  (the app is not notarised). It asks for your password when writing.
* **Linux:** `chmod +x genesis-imager-linux-x86_64 && ./genesis-imager-linux-x86_64`
  (asks for your password through polkit; needs glibc 2.35 or newer: Ubuntu
  22.04, Debian 12 or later), or on any distribution `python3 imager/genesis_imager.py`
  (with `python3-tk` for the window).

Command line (any OS with Python 3.9+, no window needed):

```sh
python3 imager/genesis_imager.py --list-devices
sudo python3 imager/genesis_imager.py --image genesis-os.img.xz --device /dev/sdX --user nodoai
# or write a customised image file and flash it with any tool you like:
python3 imager/genesis_imager.py --image genesis-os.img.xz --output nodobox.img --user nodoai
```

It prompts for the password and the Wi-Fi passphrase; `--help` lists every
option (`--wifi-ssid ""` for no Wi-Fi, `--ssh-key-file`, `--ssh-keys-only`,
`--timezone`, `--keyboard`, `--hostname`, `--no-ssh`, `--image latest`). For
scripts, `--password-file` and `--wifi-password-file` read the secrets from
files or, with `-`, from standard input (password first): `--password` and
`--wifi-password` work too, but everything on a command line is visible to
other users in the process list and lands in sudo's log. A file written with
`--output` contains the Wi-Fi passphrase and the password hash, so it is
created readable only by you; delete it once the card is written.

**How it works.** The image's boot partition holds `genesis-setup.txt`, a
placeholder of 16 blocks of 512 bytes, each starting with a marker. While
streaming the image to the card the imager finds those blocks (inside the
boot partition only, at sector boundaries, each exactly once) and replaces
them with your settings: `KEY=VALUE` lines with the password as a SHA-512
crypt hash (100 000 rounds, random salt) and free text base64-encoded. Nothing
in the file system changes size or place, so no FAT code is involved. On the
first boot `genesis-firstboot.service` runs before Raspberry Pi's first-boot
wizard, the login screen, NetworkManager and SSH, applies the settings (the
account through Raspberry Pi's own `userconf-pi`, which renames the user,
sets the password, cancels the wizard and keeps desktop autologin), writes a
summary without secrets to `genesis-setup-result.txt` on the boot partition,
and overwrites the settings with the placeholder again.

Flashing with Raspberry Pi Imager, balenaEtcher or `dd` still works: the
placeholder is left alone and the standard Raspberry Pi wizard asks for a
user name and password on the first boot.

## First boot

Flashed with Genesis Imager, the box boots to the Genesis splash, logs your
user into the Genesis desktop, joins the Wi-Fi, and answers on
`nodobox.local` (or the host name you chose) for SSH and VNC with the password
you typed. `genesis-setup-result.txt` on the boot partition says what was
applied; `/var/log/genesis-firstboot.log` on the box has the details.

Flashed with any other tool, the image behaves like the official one: the
first-boot wizard asks for a user name and password. The build-time secrets
below are the older alternative for private repositories; a password baked
in at build time is shared by every box flashed from that image.

| At build time you set | What happens on first boot (without Genesis Imager) |
| --- | --- |
| `FIRST_USER_PASS` (optionally `SSH_AUTHORIZED_KEYS`) | the user exists with that password, SSH and VNC are up: `ssh nodoai@nodobox.local`, any VNC viewer on `nodobox.local:5900`. |
| only `SSH_AUTHORIZED_KEYS` | **not usable headless.** pi-gen creates the user with `adduser --disabled-login`, which on trixie sets the shell to `/usr/sbin/nologin` until a password is set, so SSH refuses the key login, and the first-boot wizard runs. Set `FIRST_USER_PASS` as well; the workflow prints a warning if you don't. |
| nothing | identical to the official image: the wizard asks for a username and password. Headless alternatives: Raspberry Pi Imager 2.0 *OS customisation*, or a `userconf.txt` on the boot partition containing `nodoai:<hash>` with the hash from `openssl passwd -6`. |

`WPA_COUNTRY` in `config` must be set, or pi-gen ships the radio
soft-blocked; set it to the country the device is used in so the right
channels are allowed.

`cloud-init` runs on the first boot only. To re-run it after editing
`user-data`, run `sudo cloud-init clean --logs` and reboot.

## The desktop

Genesis Shell is one Python/GTK process per session (about 70 MB, idle at 0%
CPU) drawing on wlr-layer-shell surfaces; labwc places and decorates the
application windows.

* **Panel** (floating, top): Genesis logo opens the launcher; the dock shows
  pinned apps (Chromium, VS Code, Files, Terminal, Genesis Settings) and
  every running app with an indicator, click to focus or minimise,
  right-click for windows, pin/unpin, close; the clock opens the calendar and
  notification center; the status pill (network, Bluetooth, volume, battery
  when present) opens the control center.
* **Launcher**: full-screen over the blurred wallpaper, search box with
  type-ahead, category chips, keyboard navigation, user and power buttons.
  Super, Super+Space or Super+A.
* **Control center**: volume and output device, brightness when a backlight
  exists, tiles for Wi-Fi (with a network list and password entry),
  Bluetooth (paired devices, connect/disconnect, pairing through Blueman),
  Do Not Disturb, Night Light (when `wlsunset` is installed), screenshot and
  display settings; lock, settings and power buttons. Super+C.
* **Notifications**: a real `org.freedesktop.Notifications` server with
  toasts (actions, images, urgency), a notification center with Do Not
  Disturb behind the clock, and an unread badge. Super+N.
* **Sensor bar** for the NodoAI sensor boxes A-K on the I2C bus (GPIO 2/3,
  `/dev/i2c-1`). It is hidden while no box is connected and fades in on the
  right edge as soon as one answers, with one tile per connected box (the
  column scrolls if more boxes are connected than fit the screen). A click
  on a tile opens its card (I2C address, bus, connected since); click it
  again or the × to close. Every box that connects or disconnects posts a
  notification (several in the same scan share one), and clicking the
  "connected" notification of a single box, as a toast or in the notification
  center, opens its card. The bus is scanned with
  `i2cdetect -y 1 0x10 0x3a` every 2 s without blocking the shell; a box
  counts as gone after two missed scans (about 4 s), so a lost probe does not
  flash the bar. Boxes already connected at login show in the bar without a
  notification. `genesis-shell-ctl sensor C` opens a card, `sensor-close`
  closes it. Box addresses: A 0x10-0x12, B 0x14-0x16, then every 4 up to
  K 0x38-0x3A.
* **Desktop icons** for the files, folders and launchers in `~/Desktop`, plus
  Home and Trash, in columns from the top-left. Each label is black or white,
  whichever contrasts more with the part of the wallpaper right behind its
  text (WCAG relative luminance), and is chosen again when the wallpaper or
  the screen resolution changes. Double-click opens, right-click offers Open,
  Show in Files and Move to Trash; a launcher (`.desktop` file) runs only if
  it is executable or is a link to an application the system installed.
* **On-screen display** for volume and brightness keys; **power dialog**
  (lock with swaylock, log out, restart, shut down) on Ctrl+Alt+Del or the
  power key; **screenshots** with Print (screen) and Shift+Print (area) into
  `~/Pictures/Screenshots`, copied to the clipboard.
* **Windows**: rounded server-side decorations with shadows, Super+arrows to
  snap and maximise, Super+Q to close, Alt+Tab switcher with previews, four
  workspaces (Super+1..4, Super+Shift+1..4 to move), Ctrl+Alt+M magnifier.
  Super+Return or Super+T opens a terminal, Super+E the file manager,
  Super+B the browser, Super+I Genesis Settings, Super+L locks.
* **Genesis Settings** (menu, dock, Super+I) drives `raspi-config` for
  interfaces, hostname, time zone, locale, boot options and Wi-Fi country,
  controls wayvnc, and on its Appearance page sets the wallpaper, accent
  colour, panel position, glass opacity and screen blanking, which the shell
  applies live. Privileged changes prompt for the password through the Pi's
  askpass dialog, or run silently if `PASSWORDLESS_SUDO="1"`.
* **Configuration**: `/etc/genesis/shell.conf` (image defaults, rendered
  from `custom.conf`) overlaid by `~/.config/genesis/shell.conf`; labwc
  config in `/etc/xdg/genesis/labwc/` (copy to `~/.config/genesis/labwc/`
  to customise keybindings or the theme); `genesis-shell-ctl --help` lists
  the commands keybindings use. `[sensors]` sets `enabled`, `bus`, `poll_ms`,
  `notify` and `show_disconnected` (all eleven tiles with absent boxes dimmed;
  the bar still hides when no box is connected); `enabled` and `bus` apply at
  the next login. `[desktop]` sets `icons`, `show_home` and `show_trash`.
* **Testing without a Pi**: `/usr/share/genesis-shell/preview.sh DIR` starts
  the whole session on a virtual output and writes screenshots to DIR. CI
  runs it on a trixie container before building and inside the finished
  image after building.

## Acu Connect client

Acutro Technologies' client for remote shell and desktop access from
[connect.acutrotech.com](https://connect.acutrotech.com) is built into the
image (`stage-custom/16-acu-connect`; the package itself is kept unchanged in
`files/acu-connect-raspberry-pi`). It is proprietary: keep this repository
and its images private, or have Acutro's written permission.

**It keeps running whatever the system does.** It runs on its own CPython
(python-build-standalone 3.14, `/opt/acu-connect/python`) with its packages
installed from a hash-pinned lock file, never on the system `python3` or
Debian's `python3-*` packages. So an `apt upgrade`, a new Debian Python
version, or removing `python3` altogether does not touch it; the image build
checks exactly that by hiding the system Python and starting the client.
`acu-connect.service` starts it at every boot as the desktop user (by uid
1000, which Genesis Imager's first-boot rename keeps) and restarts it forever,
with growing pauses if it keeps stopping. The screen tools it uses (`grim`,
`ffmpeg`, `wtype`, `wlrctl`, `xdotool`, ...) are optional: if an upgrade
removes one, desktop sharing loses that ability and the rest keeps working.

**Linking a box.** A new box is not linked. The service waits (it logs that
once) until someone runs, as the desktop user or with `sudo`:

```sh
acu-connect signin      # prints a link and an 8-character code; approve it in the dashboard
acu-connect status      # linked? server reachable?
acu-connect signout     # unlink
journalctl -u acu-connect -f
```

When a box is removed from the account, the service sets the old link aside
(`/var/lib/acu-connect/agent.json.revoked`) and waits for a new `signin`
instead of stopping for good. Settings (server, frame rate, location lookup,
input) go in `/etc/default/acu-connect`; then `sudo systemctl restart
acu-connect`.

**Updating the package.** Replace `files/acu-connect-raspberry-pi` with the
new version, run `python3 scripts/acu-connect-lock.py` (it pins every
requirement by hash and checks each has an arm64 wheel for the bundled
Python), then `make test`.

## Customising the image

Everything you change lives in `config` and `stage-custom/`. The dry-run
(`make test`) checks most edits in seconds; a full build is only needed to
verify package installation and boot behaviour.

### Packages

Edit `stage-custom/00-packages/00-packages`: one Debian or Raspberry Pi
package per line, `#` comments allowed. Append `-` to remove a package the
Lite image ships, for example `build-essential-`. Packages that need
`--no-install-recommends` go in a `00-packages-nr` file next to it.

### Identity, locale, SSH, base stages (`config`)

Hostname, first user name, locale, keyboard, time zone, Wi-Fi country, SSH
policy, output compression and the upstream stages to build (`BASE_STAGES`).
The same file drives local and CI builds: the workflow forwards exactly the
variables defined there to pi-gen-action and fails if you add one it does not
know, so nothing can apply to one build path only. Keep secrets out of it.

### Switches (`stage-custom/custom.conf`)

| Knob | Does |
| --- | --- |
| `WIFI_SSID`, `WIFI_PASSWORD`, `WIFI_HIDDEN`, `WIFI_INTERFACE` | Wi-Fi written to `/boot/firmware/network-config` |
| `ENABLE_I2C`, `ENABLE_SPI`, `ENABLE_SERIAL_PORT` | `dtparam=i2c_arm=on`, `dtparam=spi=on`, `enable_uart=1` in `config.txt` |
| `CONFIG_TXT_EXTRA` | any further `config.txt` lines, appended under `[all]` |
| `PERSISTENT_JOURNAL`, `JOURNAL_MAX_USE` | journald storage |
| `ENABLE_UNATTENDED_UPGRADES` | automatic updates from Debian security and the Raspberry Pi archive |
| `ENABLE_SERVICES`, `DISABLE_SERVICES` | systemd units to `enable` / `disable` in the image |
| `ACU_CONNECT`, `ACU_CONNECT_SERVER` | Acu Connect client installed (`1`) or not; the server `acu-connect signin` links to |

Every knob can also be set as an environment variable for a local build
(`ENABLE_I2C=1 make build`). Secrets go in `stage-custom/secrets.env`
(git-ignored, `VAR=value` per line):

```sh
FIRST_USER_PASS='change-me'
PUBKEY_SSH_FIRST_USER='ssh-ed25519 AAAA... you@laptop'
WIFI_SSID='My Network'
WIFI_PASSWORD='wifi-passphrase'
```

### Your own files, services and scripts

pi-gen runs the sub-directories of `stage-custom/` in alphabetical order. Inside
each, files are picked up by name: `NN-packages`, `NN-packages-nr`,
`NN-debconf`, `NN-patches/`, `NN-run.sh` (runs on the build host with the image
mounted at `${ROOTFS_DIR}`, must be executable) and `NN-run-chroot.sh` (runs
inside the image). `stage-custom/05-hello-service` shows the full pattern:

```sh
source "${STAGE_DIR}/lib.sh"                      # knobs + helpers
install -m 755 files/my-app  "${ROOTFS_DIR}/usr/local/bin/my-app"
install -m 644 files/my-app.service "${ROOTFS_DIR}/etc/systemd/system/my-app.service"
on_chroot << EOF
systemctl enable my-app.service
EOF
```

Copy that directory, rename it (keep the two-digit prefix), replace the files.
Sub-stages run in name order; `90-services` runs last on purpose so
`ENABLE_SERVICES` can refer to units your own sub-stages install. `make test`
fails if a `*-run.sh` is not executable: pi-gen only logs a `Skip` line for
it and the build succeeds without your changes.

### Boot firmware (`config.txt`, `cmdline.txt`)

`stage-custom/01-system/00-run.sh` appends to `/boot/firmware/config.txt`
under pi-gen's `[all]` section and is idempotent. Use the knobs above or
`CONFIG_TXT_EXTRA`; for anything structural (per-model sections, overlays
with parameters) edit that script.

### First-boot automation (cloud-init)

Raspberry Pi OS trixie ships cloud-init with the NoCloud datasource reading
`/boot/firmware/{meta-data,user-data,network-config}`. The image installs a
fully commented `user-data` template
(`stage-custom/02-cloud-init/files/user-data`) and, when Wi-Fi is configured,
a generated `network-config`. Both stay editable on the SD card after
flashing, which is how one image can serve several devices. See Raspberry
Pi's [cloud-init announcement](https://www.raspberrypi.com/news/cloud-init-on-raspberry-pi-os/)
and the [cloud-init docs](https://cloudinit.readthedocs.io/).

### Changing the base

* **Lite instead of Desktop:** set `BASE_STAGES="stage0 stage1 stage2"` in
  `config`, `IMG_SUFFIX="-lite"` in `stage-custom/EXPORT_IMAGE`, and
  `INSTALL_VSCODE="0"`, `ENABLE_BOOT_SPLASH="0"` in `custom.conf`. The desktop
  sub-stages notice the missing desktop and skip what does not apply.
* **32-bit (Pi 1, Pi 2 v1.1, Zero, Zero W):** pi-gen builds armhf from its
  `master` branch. Set `pi-gen-ref` / `PI_GEN_REF` to `master`. 32-bit trixie
  images have had teething problems upstream; check pi-gen's issue tracker
  first.
* **Reproducible builds:** pin `pi-gen-ref` / `PI_GEN_REF` to a pi-gen tag
  such as `2026-10-06-raspios-trixie-arm64` instead of the moving `arm64`
  branch. Package versions still come from the archives at build time.
* **Other Debian release:** `RELEASE` in `config` must match the pi-gen
  branch you build from (the `arm64` branch is trixie).

## Building locally

Requirements: Linux with Docker, ~20 GB free disk, a path without spaces.
On x86-64 the build runs the arm64 chroot under QEMU and takes 1-3 hours; on
an arm64 host (Raspberry Pi 4/5 with 64-bit OS, Apple Silicon Linux VM,
Graviton) well under an hour.

```sh
cp stage-custom/secrets.env.example stage-custom/secrets.env   # optional, see above
make build                       # or: scripts/build-local.sh --help
ls pi-gen/deploy/
```

`scripts/build-local.sh` clones pi-gen into `./pi-gen` (git-ignored),
composes `build/config` from `config` plus your secrets, mounts
`stage-custom/` into the build container and runs pi-gen's `build-docker.sh`.
Resume a failed build with `make build CONTINUE=1`
(`scripts/build-local.sh --continue`), or start over with `make clean`, which
also removes pi-gen's `pigen_work` Docker container.

Native build without Docker, on a Debian-based host with the packages from
`pi-gen/depends` installed: `make build-native` (uses `sudo`).

## Checking changes without a full build

```sh
make lint    # shellcheck, yamllint, actionlint (if installed)
make test    # dry-run of stage-custom against a fake root filesystem, plus unit tests
```

The dry-run executes every `*-run.sh` with pi-gen's variables set, captures
and syntax-checks the `on_chroot` scripts instead of running them, and
asserts on the produced files in seven scenarios (defaults, Wi-Fi with awkward
characters, idempotence, invalid input, cloud-init disabled, a refused user
Python, Acu Connect off). It cannot tell
you whether a package name exists in trixie or whether the image boots; the
CI build and a real device do that. `make test` also runs
`scripts/test-shell-logic.py` (I2C scan parsing, sensor debounce and
notification texts, desktop label contrast), `scripts/test-acu-connect.py`
(the Acu Connect service: waiting for a link, restarts, a removed device) and
the Genesis Imager tests.

## Repository layout

```
config                          pi-gen settings (shared by CI and local builds)
Makefile                        lint / test / build shortcuts
scripts/build-local.sh          local build (Docker or native)
scripts/test-stage.sh           dry-run harness
stage-custom/
  custom.conf                   non-secret knobs
  lib.sh                        helpers sourced by every sub-stage
  prerun.sh, EXPORT_IMAGE       pi-gen plumbing (copy previous rootfs, export this stage)
  00-packages/00-packages       extra packages
  01-system/                    config.txt, journald, unattended-upgrades, motd
  02-cloud-init/                user-data template, Wi-Fi network-config
  04-release-info/              /etc/custom-image-release (what was built from what)
  05-hello-service/             example: ship a script + systemd unit
  06-apt-repos/                 Microsoft repository for VS Code (key vendored)
  07-desktop-packages/          settings-app runtime, wayvnc, plymouth, fonts
  08-python/                    your Pythons: 3.10 ("python") and 3.15 under /opt/python, uv, genesis-python
  09-vscode/                    VS Code, default settings, Python extensions
  10-branding/                  icons, Genesis icon theme, wallpapers, Plymouth splash, login screen
  11-genesis-shell/             Genesis Shell (files/genesis-shell), labwc session config, GTK theme,
                                install.sh (shared with CI) and preview.sh (headless smoke test)
  12-genesis-settings/          Genesis Settings app and its privileged helper
  13-system-defaults/           interfaces, VNC, browser, autologin, default session, splash flags
  14-first-boot/                genesis-firstboot.service and the setup-area placeholder on the boot partition
  16-acu-connect/               Acu Connect client (files/acu-connect-raspberry-pi, unchanged) on its own
                                Python, its service, requirements.lock (scripts/acu-connect-lock.py)
  90-services/                  enable/disable systemd units (runs last)
  secrets.env.example           template for secrets.env
branding/src/                   the logo and wallpaper the assets are generated from
scripts/make-assets.py          regenerates icons, splash and wallpapers (needs Pillow)
scripts/make-sensor-icons.py    regenerates the sensor box icons A-K (SVG, standard library only)
scripts/test-shell-logic.py     unit tests for Genesis Shell logic that needs no display
scripts/image-smoke.sh          writes a built image through Genesis Imager, runs first boot and the
                                headless desktop test inside it
scripts/test-firstboot.sh       genesis-firstboot against a fake root (no root rights needed)
imager/                         Genesis Imager (genesis_imager.py) and its tests
  secrets.env                   git-ignored, your secrets (local builds)
  build-info.env                git-ignored, generated per build
.github/workflows/build-image.yml
```

## Security notes

* **The image carries no credentials** when the `FIRST_USER_PASS` and
  `WIFI_*` secrets are unset (the default, and the right choice for this
  public repository). Genesis Imager puts the password, as a salted SHA-512
  crypt hash, and the Wi-Fi passphrase on each card while flashing; the first
  boot applies them and overwrites them in place on the card, so they do not
  stay on the boot partition. The plain password never leaves your computer.
  On a Windows, macOS or Linux machine the imager stores your last settings
  without either password.
* Secrets never enter the repository. CI writes `stage-custom/secrets.env`
  from GitHub secrets at build time; locally you create it yourself. Neither
  file is copied into the image.
* The image itself is not secret-free: it carries the password hash and the
  Wi-Fi passphrase you configured. Artifacts and releases of a **public**
  repository can be downloaded by anyone, so the workflow stops when those
  secrets are set in a public repository unless the repository variable
  `ALLOW_SECRETS_IN_PUBLIC_IMAGE=true` says you accept that. Keep the
  repository private or distribute the image another way.
* Wi-Fi credentials end up in clear text in `network-config` on the FAT boot
  partition. This is how Raspberry Pi Imager stores them too. Anyone with the
  SD card can read them.
* `FIRST_USER_PASS` is stored as a hash in `/etc/shadow` of the image. Every
  device flashed from the image shares it, and SSH plus VNC accept it, so
  change it on each device (Genesis Settings → System) or ship SSH keys with
  `PUBKEY_ONLY_SSH="1"`.
* `root` has no password (pi-gen default). SSH host keys are generated on the
  device at first boot, not shared between devices.
* Automatic security updates are on by default (`ENABLE_UNATTENDED_UPGRADES`).

## Troubleshooting

* **Build fails in stage0 (debootstrap) or with apt errors:** usually a mirror
  hiccup; re-run. Locally, `--continue` resumes; "Container pigen_work already
  exists" means run `make clean` (or `--continue`) first. In CI the full pi-gen output
  is in the job log (grouped per stage) and `build.log` is uploaded as the
  `build-log` artifact.
* **"No space left on device" in CI:** the workflow already removes unneeded
  runner tooling before the build (its log prints free space before and
  after). Drop packages, or do not build the Desktop stages.
* **pi-gen-action fails before pi-gen starts with `apt purge` errors:** its
  `increase-runner-disk-size` option is broken on arm64 runners in v1.11.0;
  the workflow keeps it off and does its own cleanup.
* **Wi-Fi does not connect:** check `WPA_COUNTRY`, that the SSID is 2.4 GHz
  capable for Zero 2 W / Pi 3, and `WIFI_HIDDEN` for hidden networks. On the
  device: `cloud-init status --long`, `nmcli device`, `/var/log/cloud-init.log`.
* **Edited `user-data` has no effect:** cloud-init ran already; `sudo cloud-init
  clean --logs && sudo reboot`.
* **`*-run.sh` did nothing:** it is not executable (`chmod +x`); `make test`
  reports this.
* **SSH says "This account is currently not available" or closes at once:**
  the first user has no password, so its shell is `nologin`. Set
  `FIRST_USER_PASS` and rebuild, or finish the console wizard once.
* **x86-64 runner is slow or fails in QEMU:** use `ubuntu-24.04-arm`.
* **Runner runs out of disk:** the stage deletes the earlier stages' root
  filesystem copies as soon as it has its own (`GENESIS_PRUNE_WORK`, on by
  default). Set it to `0` only when you debug an earlier stage with
  `CONTINUE=1`.
* **Desktop is blank or the panel is missing:** look at
  `~/.cache/genesis/session.log` (the shell logs there and is restarted
  automatically if it crashes), then run
  `/usr/share/genesis-shell/preview.sh /tmp/p` over SSH to reproduce headless.
  The stock Raspberry Pi desktop is still available from the login screen's
  session menu.
* **The sensor bar never appears although a box is plugged in:** run
  `/usr/sbin/i2cdetect -y 1 0x10 0x3a` as your user (`/usr/sbin` is not on a
  normal user's `PATH`). "No such file" means I2C is off
  (Genesis Settings → Interfaces, then reboot); "Permission denied" means
  your user is not in the `i2c` group (`sudo adduser $USER i2c`, log out and
  in); an empty grid means the box does not answer (wiring, power, or an
  address outside 0x10-0x3A). `genesis-shell-ctl state` shows what the shell
  sees under `sensors`, including the last scan error.
* **An app is missing from the dock or shows a generic icon:** its Wayland
  `app_id` does not match a desktop file; add it to `APP_ID_ALIASES` in
  `genesis_shell/services/apps.py`.
* **`python` is 3.10 but a tool wants 3.13:** call `python3`, or run the tool
  with `uv run --python 3.13`. `genesis-python` shows which interpreter is
  which.
* **`pip3.15 install` builds from source or fails:** the package has no wheel
  for Python 3.15 on aarch64 yet. Use `python` (3.10), or
  `uv python install 3.14` and a virtual environment on 3.14.
* **The first boot asked for a user name anyway:** the card was not written by
  Genesis Imager, or the settings were rejected. Read
  `genesis-setup-result.txt` on the boot partition (any computer can open it).
* **Genesis Imager says the image has no setup area:** the image was built
  before Genesis Imager existed, or it is not a Genesis OS image. Build or
  download a current one. If it says the image was *already customised*, you
  picked a file made with `--output`: choose the original image to change the
  settings, or write that file with any image writer.
* **Genesis Imager says the image file is incomplete:** the `.img` is shorter
  than its own partition table says (still being copied or unzipped, or an
  interrupted download). Nothing was made bootable; copy it again.
* **Genesis Imager lists no card:** check *Show fixed disks* (some USB card
  readers report as fixed disks), then *Refresh*. Built-in SD slots on Macs are
  listed as they are.
* **The first boot kept the login prompt although I wanted autologin (or the
  other way round):** the image's `DESKTOP_AUTOLOGIN` decides; Genesis Imager
  keeps the build's choice for the renamed user.

## Alternatives

* [rpi-image-gen](https://github.com/raspberrypi/rpi-image-gen) is Raspberry
  Pi's newer tool for lean product images (minutes to build, SBOM built in,
  profile-based). It does not reproduce Raspberry Pi OS itself. If you want a
  small appliance image rather than "Raspberry Pi OS plus my changes", look
  there first.
* [usimd/pi-gen-action](https://github.com/usimd/pi-gen-action) is the GitHub
  Action this workflow uses; its README documents every input.
