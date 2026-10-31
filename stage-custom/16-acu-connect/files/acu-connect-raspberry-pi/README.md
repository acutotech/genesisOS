# Acu Connect client for Raspberry Pi (Python)

Works on Raspberry Pi OS Bookworm or newer, and other 64-bit Debian-based Linux
with systemd and Python 3.11 or newer. Devices link to
**https://connect.acutrotech.com**.

## Install

1. Copy this zip to the Pi and extract it:

   ```bash
   unzip acu-connect-raspberry-pi.zip
   cd acu-connect-raspberry-pi
   ```

2. Install, as your normal desktop user, not with `sudo`. It needs internet access.

   ```bash
   bash install.sh
   ```

3. It prints a link and an eight-character code. Open the link, sign in to your
   main account, and approve the device. The code expires after 10 minutes.

The client then starts by itself whenever the Pi turns on. A Pi that is already
linked stays linked, so you can run `install.sh` again to upgrade.

## Useful commands

```bash
systemctl --user status acu-connect     # is it running?
journalctl --user -u acu-connect -n 50  # recent messages
acu-connect status                      # which server, and is it reachable?
acu-connect signin --force              # link this device again
acu-connect signout                     # unlink this device
```

If `acu-connect` isn't found, open a new terminal, or use `~/.local/bin/acu-connect`.

## Settings

Add `Environment=NAME=value` lines to `~/.config/systemd/user/acu-connect.service`,
then run `systemctl --user daemon-reload` and `systemctl --user restart acu-connect`.

| Setting | Default | Meaning |
|---|---|---|
| `ACU_NETWORK_LOCATION=0` | on | don't look up the approximate location (ipinfo.io) |
| `ACU_SCREEN_FPS` | 5 | desktop frames per second, 1 to 30 |
| `ACU_SCREEN_QUALITY` | 60 | desktop image quality, 1 to 100 |
| `ACU_SCREEN_SCALE` | 1 | desktop image scale, such as 0.5 |
| `ACU_DISABLE_INPUT=1` | off | never accept mouse and keyboard input |
| `ACU_TRANSPORT=poll` | off | skip WebSockets and use ordinary web requests |

## Tests

```bash
PYTHONPATH=. ~/.local/share/acu-connect-py/venv/bin/python -m unittest discover tests
```
