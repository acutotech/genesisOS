# Acu Connect on Genesis OS

Remote shell and desktop access from https://connect.acutrotech.com. On Genesis
OS the client is built in; the package's own `install.sh` and per-user unit are
not used, so its README (VENDOR-README.md, next to this file) does not apply
here except for what the client does.

    sudo acu-connect signin      link this box: approve the 8-character code in the dashboard
    sudo acu-connect status      linked? server reachable?
    sudo acu-connect signout     unlink (the client stops within a few seconds)
    systemctl status acu-connect
    journalctl -u acu-connect -f

- `acu-connect.service` (a system service) starts at every boot and runs the
  client as the desktop user (uid 1000, whatever it is called now) once the box
  is linked. Until then it waits and says so once in the journal. It does not
  start the client while Raspberry Pi's first-boot wizard still has to rename
  that user; it starts within seconds of the wizard finishing.
- `signin --force` (link again) and `signout` take effect at once: the service
  restarts or stops the client by itself.
- When the box is removed from the account, the link is kept as
  `/var/lib/acu-connect/agent.json.revoked` and the service waits for a new
  `signin`.
- Settings: `/etc/default/acu-connect` (NAME=value lines), then
  `sudo systemctl restart acu-connect`.
- The client runs on its own Python in `/opt/acu-connect/python`, never the
  system `python3`, so upgrades of Debian's Python do not affect it.
