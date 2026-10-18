# Genesis OS

A custom Raspberry Pi OS image for the **NodoAI box**: 64-bit Raspberry Pi OS
(Debian trixie) running **Genesis Shell**, a desktop written for this image:
floating glass panel, full-screen app launcher, control center, notifications,
on-screen display, power dialog and wallpaper, all drawn by our own code on top
of labwc, the compositor Raspberry Pi ships and tests on the Pi 5. Plus the
Genesis logo on the boot splash and login screen, Visual Studio Code, Python
3.10 and 3.13, Chromium, a Genesis Settings app, and the hardware interfaces
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
| User | `nodoai`; password from secret `FIRST_USER_PASS` | `config`, secrets |
| Wi-Fi | network from secrets `WIFI_SSID` / `WIFI_PASSWORD`, country `IN` | secrets, `config` |
| Locale, keyboard, time zone | `en_US.UTF-8`, `us`, `Asia/Kolkata` | `config` |
| Desktop | **Genesis** session: labwc + Genesis Shell (see below). Raspberry Pi's stock labwc desktop stays installed as a fallback, selectable on the login screen | `stage-custom/11-genesis-shell` |
| Look | dark glass: translucent panel and cards over a blurred copy of the wallpaper, 16px rounded corners, steel-blue accent, Inter + JetBrains Mono, Papirus icons, five wallpapers | `GENESIS_ACCENT`, `GENESIS_WALLPAPER` in `custom.conf`; Genesis Settings |
| Boot | Plymouth splash with the Genesis logo, straight into the desktop as `nodoai` | `ENABLE_BOOT_SPLASH`, `DESKTOP_AUTOLOGIN` |
| Apps | Visual Studio Code (Microsoft repository) with the Python extensions, Chromium as default browser, the Raspberry Pi desktop application set | `INSTALL_VSCODE`, `VSCODE_EXTENSIONS`, `DEFAULT_BROWSER` |
| Python | `python` = CPython 3.10.22 under `/opt/python` (user default, VS Code interpreter); `python3` = the OS's 3.13; `uv` installed for more versions | `PYTHON_PRIMARY` |
| Interfaces | I2C, SPI, serial port (UART on GPIO 14/15, Pi 5 `uart0`), SSH, VNC (wayvnc) on; serial console and 1-Wire off | `ENABLE_*` in `custom.conf` |
| Settings | **Genesis Settings** (interfaces, system, appearance, connectivity, display and sound, software, about) plus Raspberry Pi Configuration | `stage-custom/12-genesis-settings` |
| Behaviour | persistent journal, automatic security updates, cloud-init seed files on the boot partition, screen blanks after 10 minutes | `custom.conf` |

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
* **Python 3.10 as `python`, not as `python3`.** The OS, apt and raspi-config
  run on the system 3.13; replacing it breaks them. 3.10.22 is the last 3.10
  release and the series is end-of-life, so plan to move your code to 3.12 or
  3.13 (`uv python install 3.12` is one command). The Raspberry Pi libraries
  the OS ships (`gpiozero`, `rpi-lgpio`, `picamera2`) live in the system 3.13;
  in 3.10 install what you need with `pip install gpiozero rpi-lgpio` or run
  `python3`.

## Quick start (GitHub Actions)

1. Push this repository to GitHub. Public repositories get the native arm64
   runner (`ubuntu-24.04-arm`) for free; private ones can use it too, billed
   against the plan's minutes. **In a public repository the built image is
   public as well** (artifact and release), so the workflow refuses to bake
   `FIRST_USER_PASS` or `WIFI_PASSWORD` into it unless you set the repository
   variable `ALLOW_SECRETS_IN_PUBLIC_IMAGE` to `true`. For a product image with
   a known default password, make the repository private.
2. Add the repository secrets under *Settings → Secrets and variables →
   Actions*:

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
   then mounts the image, starts the Genesis session inside it and screenshots
   it (`genesis-image-smoke` artifact). A blank desktop fails the build.
4. Download the `genesis-os` artifact and unzip it.
5. Flash `image_*.img.xz`: Raspberry Pi Imager (*Use custom*), or
   `xzcat image_*.img.xz | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync`.
   If you let Imager apply its own *OS customisation*, it overwrites the
   `user-data` and `network-config` files this image ships.
6. Tag a commit `v1.2.3` and push the tag: the workflow attaches the image to a
   GitHub release.

## First boot

With the secrets above set, the box boots to the Genesis splash, logs
`nodoai` into the Genesis desktop, joins the `NodoAI` Wi-Fi, and answers on
`nodobox.local` for SSH and VNC with the `FIRST_USER_PASS` password. Without
`FIRST_USER_PASS` the image behaves like the official one: the first-boot
wizard asks for a username and password.

| At build time you set | What happens on first boot |
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
  the commands keybindings use.
* **Testing without a Pi**: `/usr/share/genesis-shell/preview.sh DIR` starts
  the whole session on a virtual output and writes screenshots to DIR. CI
  runs it on a trixie container before building and inside the finished
  image after building.

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
make test    # dry-run of stage-custom against a fake root filesystem
```

The dry-run executes every `*-run.sh` with pi-gen's variables set, captures
and syntax-checks the `on_chroot` scripts instead of running them, and
asserts on the produced files in five scenarios (defaults, Wi-Fi with awkward
characters, idempotence, invalid input, cloud-init disabled). It cannot tell
you whether a package name exists in trixie or whether the image boots; the
CI build and a real device do that.

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
  08-python/                    CPython 3.10 under /opt/python as "python", uv
  09-vscode/                    VS Code, default settings, Python extensions
  10-branding/                  icons, Genesis icon theme, wallpapers, Plymouth splash, login screen
  11-genesis-shell/             Genesis Shell (files/genesis-shell), labwc session config, GTK theme,
                                install.sh (shared with CI) and preview.sh (headless smoke test)
  12-genesis-settings/          Genesis Settings app and its privileged helper
  13-system-defaults/           interfaces, VNC, browser, autologin, default session, splash flags
  90-services/                  enable/disable systemd units (runs last)
  secrets.env.example           template for secrets.env
branding/src/                   the logo and wallpaper the assets are generated from
scripts/make-assets.py          regenerates icons, splash and wallpapers (needs Pillow)
scripts/image-smoke.sh          mounts a built image and runs the headless desktop test inside it
  secrets.env                   git-ignored, your secrets (local builds)
  build-info.env                git-ignored, generated per build
.github/workflows/build-image.yml
```

## Security notes

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
* **An app is missing from the dock or shows a generic icon:** its Wayland
  `app_id` does not match a desktop file; add it to `APP_ID_ALIASES` in
  `genesis_shell/services/apps.py`.
* **`python` is 3.10 but a tool wants 3.13:** call `python3`, or run the tool
  with `uv run --python 3.13`.

## Alternatives

* [rpi-image-gen](https://github.com/raspberrypi/rpi-image-gen) is Raspberry
  Pi's newer tool for lean product images (minutes to build, SBOM built in,
  profile-based). It does not reproduce Raspberry Pi OS itself. If you want a
  small appliance image rather than "Raspberry Pi OS plus my changes", look
  there first.
* [usimd/pi-gen-action](https://github.com/usimd/pi-gen-action) is the GitHub
  Action this workflow uses; its README documents every input.
