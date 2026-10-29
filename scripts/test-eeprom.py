#!/usr/bin/env python3
"""Tests for genesis-eeprom (stage-custom/15-bootloader/files/genesis-eeprom).

    python3 scripts/test-eeprom.py

Two parts:

* the rules, called directly: BOOT_ORDER for each boot medium, the
  section-aware merge, validation, the settings file;
* the whole program against a fake root: a Pi 5 device tree, packaged
  bootloader images, and stand-ins for rpi-eeprom-config and rpi-eeprom-update
  that record their calls (the update stand-in refuses anything but a staged
  update). This walks the state machine: staged, applied at the next boot,
  never again; cancelled; changed by the user; deferred; skipped.

What this cannot prove is that a real Pi 5 bootloader takes the result. The
image build checks the config with the image's own rpi-eeprom-config
(scripts/image-smoke.sh); the rest needs a Pi. Used by `make test`.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "stage-custom", "15-bootloader", "files", "genesis-eeprom")

loader = importlib.machinery.SourceFileLoader("genesis_eeprom", TOOL)
spec = importlib.util.spec_from_loader("genesis_eeprom", loader)
ge = importlib.util.module_from_spec(spec)
loader.exec_module(ge)

FACTORY = "[all]\nBOOT_UART=1\nBOOT_ORDER=0xf461\nNET_INSTALL_AT_POWER_ON=1\n"
DEFAULTS = {"enabled": "1", "boot_first": "auto", "pcie_probe": "1", "net_install_at_power_on": "0", "power_off_on_halt": "keep"}
RUNNING_TS = 1790351949


class BootOrder(unittest.TestCase):
    def test_factory_order_for_each_medium(self):
        self.assertEqual(ge.boot_order_first("0xf461", 1), "0xf461")   # SD: nothing to change
        self.assertEqual(ge.boot_order_first("0xf461", 6), "0xf416")   # NVMe, SD, USB
        self.assertEqual(ge.boot_order_first("0xf461", 4), "0xf614")   # USB, SD, NVMe

    def test_every_local_medium_stays_bootable(self):
        for start in ("0xf461", "0xf146", "0xf41", "0xf1", "0xf21", "0x1"):
            for first in (1, 4, 6):
                modes = ge.parse_order(ge.boot_order_first(start, first))
                self.assertEqual(modes[0], first, (start, first))
                self.assertTrue({1, 4, 6} <= set(modes), (start, first, modes))
                self.assertEqual(modes[-1], 0xF, (start, first))

    def test_missing_or_partial_orders(self):
        self.assertEqual(ge.boot_order_first(None, 1), "0xf461")       # an empty BOOT_ORDER means 0xf41
        self.assertEqual(ge.boot_order_first(None, 6), "0xf416")
        self.assertEqual(ge.boot_order_first("0xf146", 1), "0xf461")   # raspi-config's "NVMe/USB" order
        self.assertEqual(ge.boot_order_first("0xf146", 4), "0xf164")
        self.assertEqual(ge.boot_order_first("0x1", 6), "0xf416")      # no restart: one is added

    def test_a_users_network_boot_stays(self):
        order = ge.boot_order_first("0xf21", 1)
        self.assertEqual(order, "0xf4621")
        self.assertIn(2, ge.parse_order(order))

    def test_rpiboot_stays_last(self):
        self.assertEqual(ge.boot_order_first("0x3461", 6), "0x3416")

    def test_orders_left_alone(self):
        for bad in ("0xf4651", "0xf3461", "0xe461", "0x123456789", "f461", "0xzz", "0x12416121", "0xf4f1", "0xf4e1"):
            with self.assertRaises(ge.Skip, msg=bad):
                ge.boot_order_first(bad, 6)

    def test_describe(self):
        self.assertEqual(ge.describe_order("0xf416"), "NVMe → SD card → USB drive → repeat")
        self.assertEqual(ge.describe_order(None), "SD card → USB drive → repeat")
        self.assertEqual(ge.describe_order("0xf4f1"), "SD card → restart → USB drive → repeat")   # a mid-order f is shown


class Merge(unittest.TestCase):
    def test_factory_plan(self):
        desired, notes = ge.plan(FACTORY, dict(DEFAULTS), 6)
        self.assertEqual(desired, "[all]\nBOOT_UART=1\nBOOT_ORDER=0xf416\nNET_INSTALL_AT_POWER_ON=0\nPCIE_PROBE=1\n")
        self.assertEqual(notes, [])

    def test_keep_everything_changes_nothing(self):
        settings = {**DEFAULTS, "boot_first": "keep", "pcie_probe": "keep", "net_install_at_power_on": "keep"}
        self.assertEqual(ge.plan(FACTORY, settings, 6)[0], FACTORY)

    def test_idempotent(self):
        for mode in (1, 4, 6):
            once = ge.plan(FACTORY, dict(DEFAULTS), mode)[0]
            self.assertEqual(ge.plan(once, dict(DEFAULTS), mode)[0], once)

    def test_conditional_sections_are_the_users(self):
        text = "[all]\nBOOT_UART=1\nBOOT_ORDER=0xf461\n[gpio4=0]\nBOOT_ORDER=0xf21\n"
        desired, notes = ge.plan(text, dict(DEFAULTS), 6)
        self.assertIn("[gpio4=0]\nBOOT_ORDER=0xf21\n", desired)
        self.assertIn("BOOT_ORDER=0xf461\n", desired)              # left alone: a filter overrides it
        self.assertTrue(any("conditional" in n for n in notes), notes)
        # the other keys still go in, into an [all] block after the filter
        self.assertTrue(desired.endswith("[all]\nNET_INSTALL_AT_POWER_ON=0\nPCIE_PROBE=1\n"), desired)

    def test_new_keys_go_before_a_config_txt_section(self):
        text = "[all]\nBOOT_ORDER=0xf461\n[config.txt]\n[all]\nkernel=kernel_2712.img\n"
        desired, _notes = ge.plan(text, dict(DEFAULTS), 1)
        before, after = desired.split("[config.txt]")
        self.assertIn("PCIE_PROBE=1", before)
        self.assertNotIn("PCIE_PROBE", after)
        self.assertIn("kernel=kernel_2712.img", after)

    def test_keys_in_a_config_txt_section_are_not_bootloader_keys(self):
        text = "[all]\nBOOT_ORDER=0xf461\n[config.txt]\n[all]\nPCIE_PROBE=0\n"
        desired, notes = ge.plan(text, dict(DEFAULTS), 1)
        self.assertEqual(notes, [])
        self.assertIn("PCIE_PROBE=1\n[config.txt]\n[all]\nPCIE_PROBE=0\n", desired)

    def test_comments_duplicates_and_spacing(self):
        text = "#BOOT_ORDER=0xf1\n[all]\nBOOT_ORDER = 0xf461\nPCIE_PROBE=0\nPCIE_PROBE=0\n"
        desired, _notes = ge.plan(text, dict(DEFAULTS), 1)
        self.assertIn("#BOOT_ORDER=0xf1\n", desired)               # comments untouched
        self.assertIn("BOOT_ORDER = 0xf461\n", desired)            # same value: line not rewritten
        self.assertEqual(desired.count("PCIE_PROBE=1"), 2)         # every unconditional duplicate
        self.assertNotIn("PCIE_PROBE=0", desired)

    def test_nul_padding_and_crlf(self):
        text = "[all]\r\nBOOT_UART=1\r\nBOOT_ORDER=0xf461\r\n\x00\x00\x00"
        desired, _notes = ge.plan(text, dict(DEFAULTS), 6)
        self.assertNotIn("\r", desired)
        self.assertNotIn("\x00", desired)
        self.assertIn("BOOT_ORDER=0xf416", desired)

    def test_no_boot_order_set_yet(self):
        desired, _notes = ge.plan("[all]\nBOOT_UART=1\n", dict(DEFAULTS), 4)
        self.assertIn("BOOT_ORDER=0xf614", desired)

    def test_network_boot_keeps_the_order(self):
        desired, notes = ge.plan(FACTORY, dict(DEFAULTS), 2)
        self.assertIn("BOOT_ORDER=0xf461", desired)
        self.assertTrue(any("network" in n for n in notes), notes)

    def test_an_order_it_must_not_touch(self):
        text = "[all]\nBOOT_ORDER=0xf3461\n"
        with self.assertRaises(ge.Skip):   # the result would not validate either
            ge.plan(text, dict(DEFAULTS), 6)

    def test_too_big(self):
        text = "[all]\nBOOT_ORDER=0xf461\n" + "".join(f"# {'x' * 70}\n" for _ in range(55))
        self.assertLessEqual(len(ge.normalise(text)), ge.MAX_CONFIG)
        with self.assertRaises(ge.Skip):
            ge.plan(text, dict(DEFAULTS), 6)

    def test_explicit_medium(self):
        settings = {**DEFAULTS, "boot_first": "nvme"}
        self.assertIn("BOOT_ORDER=0xf416", ge.plan(FACTORY, settings, 1)[0])   # booted from SD, NVMe first anyway


class Chroot(unittest.TestCase):
    def test_a_normal_user_is_not_told_chroot(self):
        real_stat = os.stat

        def stat(p, *a, **k):
            if str(p).startswith("/proc/1/root"):
                raise PermissionError(13, "Permission denied", p)
            return real_stat(p, *a, **k)

        old_root = ge.ROOT
        try:
            ge.ROOT = ""
            ge.os.stat = stat
            self.assertFalse(ge.in_chroot())
        finally:
            ge.os.stat = real_stat
            ge.ROOT = old_root


class Settings(unittest.TestCase):
    def test_parse(self):
        s = ge.read_settings("# x\nenabled=1\nboot_first = NVMe  # comment\npcie_probe=keep\n")
        self.assertEqual(s["boot_first"], "nvme")
        self.assertEqual(s["pcie_probe"], "keep")
        self.assertEqual(s["power_off_on_halt"], "keep")

    def test_rejects(self):
        for bad in ("boot_first=floppy\n", "pcie_probe=2\n", "psu_max_current=5000\n", "BOOT_ORDER=0xf461\n", "enabled=keep\n"):
            with self.assertRaises(ValueError, msg=bad):
                ge.read_settings(bad)


# -- the whole program against a fake root -----------------------------------------------------
STUB_CONFIG = r'''#!/usr/bin/env python3
import os, shutil, sys
root = os.environ["GENESIS_EEPROM_ROOT"]
with open(os.path.join(root, "calls.log"), "a") as fh:
    fh.write("rpi-eeprom-config " + " ".join(sys.argv[1:]) + "\n")
args = sys.argv[1:]
if not args:
    sys.stdout.write(open(os.path.join(root, "fake", "running.conf")).read())
elif args[0] == "--config":
    conf, out, image = args[1], args[3], args[4]
    shutil.copyfile(image, out)
    shutil.copyfile(conf, out + ".conf")
else:
    sys.stdout.write(open(args[0] + ".conf").read())
'''

STUB_UPDATE = r'''#!/usr/bin/env python3
import hashlib, os, shutil, sys, time
root = os.environ["GENESIS_EEPROM_ROOT"]
with open(os.path.join(root, "calls.log"), "a") as fh:
    fh.write("rpi-eeprom-update " + " ".join(sys.argv[1:]) + " immediate=" + os.environ.get("RPI_EEPROM_IMMEDIATE_UPDATE", "unset") + "\n")
bootfs = os.path.join(root, "boot", "firmware")
pending = os.path.join(root, "run", "rpi-eeprom-update-pending")
args = sys.argv[1:]
if args == ["-b"]:
    # a NOOBS-labelled SD card is mounted on a temporary directory that is gone when -b returns
    print(os.path.join(root, "gone-mount") if os.environ.get("STUB_BOOTFS_GONE") else bootfs)
elif args[:2] == ["-d", "-f"]:
    if os.environ.get("RPI_EEPROM_IMMEDIATE_UPDATE") != "0":
        sys.exit(9)   # an immediate write is never what genesis-eeprom may ask for
    rc = int(os.environ.get("STUB_UPDATE_RC", "0"))
    if rc and not os.environ.get("STUB_WRITE_FIRST"):
        sys.exit(rc)
    upd = os.path.join(bootfs, "pieeprom.upd")
    shutil.copyfile(args[2], upd)
    shutil.copyfile(args[2] + ".conf", upd + ".conf")
    with open(upd, "r+b") as fh:                 # every image written is stamped: no two are the same
        fh.seek(-32, 2)
        fh.write(("%032d" % time.time_ns()).encode())
    if os.environ.get("STUB_STAGE_OTHER"):
        with open(upd + ".conf", "w") as fh:
            fh.write("[all]\nBOOT_ORDER=0xf21\n")
    with open(upd, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    with open(os.path.join(bootfs, "pieeprom.sig"), "w") as fh:
        fh.write(digest + "\nts: 1\n")
    os.makedirs(os.path.dirname(pending), exist_ok=True)
    with open(pending, "w") as fh:
        fh.write("recovery %d\n" % time.time_ns())
    if rc:
        sys.exit(rc)   # e.g. copying recovery.bin failed after the update was written
elif args in (["-r"], ["-s", "-a"]):
    # removePreviousUpdates; only -r also clears a "recovery" flag, as upstream does
    for name in ("pieeprom.upd", "pieeprom.upd.conf", "pieeprom.sig", "recovery.bin"):
        if os.path.exists(os.path.join(bootfs, name)):
            os.remove(os.path.join(bootfs, name))
    if args == ["-r"] and os.path.exists(pending) and open(pending).read().startswith("recovery"):
        os.remove(pending)
    if args == ["-s", "-a"] and os.environ.get("STUB_AUTO_NEW"):
        # the upgrade brought a newer bootloader: staged with the running config
        with open(os.path.join(bootfs, "pieeprom.upd"), "wb") as fh:
            fh.write(b"newer bootloader")
        with open(pending, "w") as fh:
            fh.write("recovery %d\n" % time.time_ns())
else:
    sys.exit(64)
'''


def be32(value):
    return value.to_bytes(4, "big")


class Program(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="genesis-eeprom-test.")
        self.root = os.path.join(self.tmp, "root")
        self.tools = os.path.join(self.tmp, "tools")
        os.makedirs(self.tools)
        for name, body in (("rpi-eeprom-config", STUB_CONFIG), ("rpi-eeprom-update", STUB_UPDATE)):
            with open(os.path.join(self.tools, name), "w") as fh:
                fh.write(body)
            os.chmod(os.path.join(self.tools, name), 0o755)
        dt = os.path.join(self.root, "proc", "device-tree")
        self.write(os.path.join(dt, "system", "linux,revision"), be32(0xD04170), binary=True)     # Pi 5 4GB
        self.write(os.path.join(dt, "chosen", "bootloader", "boot-mode"), be32(6), binary=True)   # NVMe
        self.write(os.path.join(dt, "chosen", "bootloader", "build-timestamp"), be32(RUNNING_TS), binary=True)
        self.write(os.path.join(dt, "serial-number"), b"a1b2c3d4\x00", binary=True)
        self.write(os.path.join(dt, "chosen", "bootloader", "update-timestamp"), be32(1700000000), binary=True)
        self.boot_id("boot-1")
        os.makedirs(os.path.join(self.root, "boot", "firmware"))
        os.makedirs(os.path.join(self.root, "run"))
        fw = os.path.join(self.root, "usr", "lib", "firmware", "raspberrypi", "bootloader-2712")
        self.image(os.path.join(fw, "default", "pieeprom-2026-09-25.bin"), RUNNING_TS, 2)
        self.image(os.path.join(fw, "latest", "pieeprom-2026-10-23.bin"), 1792764809, 3)
        self.settings("enabled=1\nboot_first=auto\npcie_probe=1\nnet_install_at_power_on=0\npower_off_on_halt=keep\n")
        self.running(FACTORY)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, file, data, binary=False):
        os.makedirs(os.path.dirname(file), exist_ok=True)
        with open(file, "wb" if binary else "w") as fh:
            fh.write(data)

    def image(self, file, ts, mfg):
        body = b"\xff" * 1000 + f"BUILD_TIMESTAMP={ts}\x00MFG_VER: {mfg}\n".encode()
        self.write(file, body + b"\xff" * (ge.IMAGE_SIZE - len(body)), binary=True)
        self.write(file + ".conf", FACTORY)

    def boot_id(self, value):
        self.write(os.path.join(self.root, "proc", "sys", "kernel", "random", "boot_id"), value + "\n")

    def settings(self, text):
        self.write(os.path.join(self.root, "etc", "genesis", "bootloader.conf"), text)

    def running(self, text):
        self.write(os.path.join(self.root, "fake", "running.conf"), text)

    def run_tool(self, *args, rc=0, **env):
        result = subprocess.run([sys.executable, "-I", TOOL, *args], capture_output=True, text=True, timeout=60,
                                env={**os.environ, "GENESIS_EEPROM_ROOT": self.root, "GENESIS_EEPROM_TOOLS": self.tools, **env})
        self.assertEqual(result.returncode, rc, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def state(self):
        with open(os.path.join(self.root, "var", "lib", "genesis", "eeprom", "state.json")) as fh:
            return json.load(fh)

    def done(self):
        return os.path.exists(os.path.join(self.root, "var", "lib", "genesis", "eeprom", "done"))

    def calls(self):
        try:
            with open(os.path.join(self.root, "calls.log")) as fh:
                return fh.read()
        except FileNotFoundError:
            return ""

    def staged_conf(self):
        with open(os.path.join(self.root, "boot", "firmware", "pieeprom.upd.conf")) as fh:
            return fh.read()

    def service(self, **env):
        """rpi-eeprom-update.service with the Genesis drop-in (an rpi-eeprom upgrade restarts it)."""
        self.run_tool("watch", "before")
        subprocess.run([os.path.join(self.tools, "rpi-eeprom-update"), "-s", "-a"], check=True,
                       env={**os.environ, "GENESIS_EEPROM_ROOT": self.root, **env})
        self.run_tool("watch", "after")

    def user_update(self, *args):
        subprocess.run([os.path.join(self.tools, "rpi-eeprom-update"), *args], check=True,
                       env={**os.environ, "GENESIS_EEPROM_ROOT": self.root, "RPI_EEPROM_IMMEDIATE_UPDATE": "0"})

    def eeprom_file(self, name):
        return os.path.join(self.root, "var", "lib", "genesis", "eeprom", name)

    def status_state(self):
        return json.loads(self.run_tool("status", "--json"))["state"]

    def serial(self, value):
        self.write(os.path.join(self.root, "proc", "device-tree", "serial-number"), value.encode() + b"\x00", binary=True)

    def dt(self, name, value):
        self.write(os.path.join(self.root, "proc", "device-tree", "chosen", "bootloader", name), be32(value), binary=True)

    def read(self, name):
        with open(self.eeprom_file(name)) as fh:
            return fh.read()

    def reboot(self, running, boot="boot-2"):
        for name in ("pieeprom.upd", "pieeprom.upd.conf", "pieeprom.sig"):   # what rpi-eeprom-update.service cleans
            p = os.path.join(self.root, "boot", "firmware", name)
            if os.path.exists(p):
                os.remove(p)
        p = os.path.join(self.root, "run", "rpi-eeprom-update-pending")
        if os.path.exists(p):
            os.remove(p)
        self.running(running)
        self.boot_id(boot)

    def test_stage_then_applied_then_never_again(self):
        out = self.run_tool("apply")
        self.assertIn("staged", out)
        desired = "[all]\nBOOT_UART=1\nBOOT_ORDER=0xf416\nNET_INSTALL_AT_POWER_ON=0\nPCIE_PROBE=1\n"
        self.assertEqual(self.staged_conf(), desired)
        st = self.state()
        self.assertEqual((st["status"], st["path"], st["image"]), ("staged", "recovery", "pieeprom-2026-09-25.bin"))
        self.assertIn("-d -f", self.calls())
        self.assertIn("immediate=0", self.calls())
        self.assertNotIn("pieeprom-2026-10-23", self.calls())      # never a newer bootloader version
        with open(os.path.join(self.root, "var", "lib", "genesis", "eeprom", "original.conf")) as fh:
            self.assertEqual(fh.read(), FACTORY)
        with open(self.eeprom_file("original.serial")) as fh:
            self.assertEqual(fh.read().strip(), "a1b2c3d4")
        # the same boot again (service restarted): nothing new is staged
        before = self.calls()
        self.assertIn("applied at the next reboot", self.run_tool("apply"))
        self.assertEqual(self.calls().count("-d -f"), before.count("-d -f"))
        # next boot: the bootloader runs the new config
        self.reboot(desired)
        self.assertIn("runs the Genesis settings", self.run_tool("apply"))
        self.assertEqual(self.state()["status"], "applied")
        self.assertTrue(self.done())
        # later boots: never again, even after a user's change
        self.reboot("[all]\nBOOT_ORDER=0xf461\n", boot="boot-3")
        before = self.calls()
        self.assertIn("nothing to do", self.run_tool("apply"))
        self.assertEqual(self.calls(), before)

    def test_a_cancel_with_genesis_eeprom_is_final(self):
        self.run_tool("apply")
        self.run_tool("cancel")
        self.reboot(FACTORY)
        before = self.calls()
        self.assertIn("nothing to do", self.run_tool("apply"))
        self.assertEqual(self.calls(), before)
        self.assertEqual(self.state()["status"], "cancelled")

    def test_user_change_is_respected(self):
        self.run_tool("apply")
        self.reboot("[all]\nBOOT_ORDER=0xf21\n")
        self.run_tool("apply")
        self.assertEqual(self.state()["status"], "user-changed")

    def test_sd_boot_with_nothing_to_change(self):
        self.write(os.path.join(self.root, "proc", "device-tree", "chosen", "bootloader", "boot-mode"), be32(1), binary=True)
        self.settings("boot_first=auto\npcie_probe=keep\nnet_install_at_power_on=keep\n")
        self.assertIn("already has", self.run_tool("apply"))
        self.assertEqual(self.state()["status"], "unchanged")
        self.assertNotIn("rpi-eeprom-update -d", self.calls())

    def test_another_pending_update_defers_then_gives_up(self):
        self.write(os.path.join(self.root, "boot", "firmware", "pieeprom.upd"), "someone else's update")
        for attempt in range(1, 4):
            self.write(os.path.join(self.root, "run", "rpi-eeprom-update-pending"), "recovery 1\n")
            self.boot_id(f"boot-{attempt}")
            self.run_tool("apply", rc=0 if attempt < 3 else 1)   # giving up shows as a failed unit
            self.assertEqual(self.state()["status"], "deferred" if attempt < 3 else "failed")
        self.assertTrue(self.done())
        self.assertNotIn("rpi-eeprom-update -d", self.calls())

    def test_no_image_of_the_running_version(self):
        self.write(os.path.join(self.root, "proc", "device-tree", "chosen", "bootloader", "build-timestamp"), be32(1800000000), binary=True)
        self.run_tool("apply")
        self.assertEqual(self.state()["status"], "deferred")
        self.assertNotIn("rpi-eeprom-update -d", self.calls())

    def test_update_tool_fails(self):
        self.run_tool("apply", rc=1, STUB_UPDATE_RC="2")
        self.assertEqual(self.state()["status"], "failed")
        self.assertFalse(os.path.exists(os.path.join(self.root, "boot", "firmware", "pieeprom.upd")))

    def test_not_a_pi5(self):
        for rev in (0xD04180, 0xD03140, 0x00000011):            # CM5, Pi 4, an old-style code
            self.write(os.path.join(self.root, "proc", "device-tree", "system", "linux,revision"), be32(rev), binary=True)
            self.assertIn("not a Raspberry Pi 5", self.run_tool("apply"))
            self.assertFalse(os.path.exists(os.path.join(self.root, "var", "lib", "genesis", "eeprom", "state.json")))
        self.assertEqual(self.calls(), "")

    def test_chroot(self):
        self.write(os.path.join(self.root, "fake-chroot"), "")
        self.assertIn("skip: chroot", self.run_tool("apply"))
        self.assertEqual(self.calls(), "")

    def test_frozen_in_any_section(self):
        for frozen in (FACTORY + "FREEZE_VERSION=1\n", FACTORY + "[pi5]\nFREEZE_VERSION=1\n",
                       FACTORY + "[0x1234abcd]\nFREEZE_VERSION=1\n[all]\n", FACTORY + "FREEZE_VERSION=1\nFREEZE_VERSION=0\n"):
            self.running(frozen)
            self.run_tool("apply", "--again")
            self.assertEqual(self.state()["status"], "skipped", frozen)
            self.assertIn("frozen", self.state()["reason"])
        self.assertNotIn("-d -f", self.calls())

    def test_frozen_only_in_config_txt_does_not_count(self):
        self.running(FACTORY + "[config.txt]\nFREEZE_VERSION=1\n")
        self.run_tool("apply")
        self.assertEqual(self.state()["status"], "staged")

    def test_self_update_off_in_a_section_on_nvme(self):
        self.running(FACTORY + "[pi5]\nENABLE_SELF_UPDATE=0\n")
        self.run_tool("apply")
        self.assertEqual(self.state()["status"], "skipped")
        self.assertNotIn("-d -f", self.calls())

    def test_an_admins_immediate_writes_are_never_used(self):
        self.write(os.path.join(self.root, "etc", "default", "rpi-eeprom-update"),
                   'FIRMWARE_RELEASE_STATUS="default"\nRPI_EEPROM_IMMEDIATE_UPDATE=1\n')
        self.run_tool("apply", RPI_EEPROM_IMMEDIATE_UPDATE="0")
        self.assertEqual(self.state()["status"], "skipped")
        self.assertIn("RPI_EEPROM_IMMEDIATE_UPDATE=1", self.state()["reason"])
        self.assertNotIn("-d -f", self.calls())
        self.write(os.path.join(self.root, "etc", "default", "rpi-eeprom-update"),
                   'FIRMWARE_RELEASE_STATUS="default"\n#RPI_EEPROM_IMMEDIATE_UPDATE=1\n')
        self.run_tool("apply", "--again")
        self.assertEqual(self.state()["status"], "staged")

    def test_clock_behind_the_last_update_defers(self):
        self.write(os.path.join(self.root, "proc", "device-tree", "chosen", "bootloader", "update-timestamp"),
                   be32(0xFFFFFFF0), binary=True)
        self.run_tool("apply")
        self.assertEqual(self.state()["status"], "deferred")
        self.assertIn("clock", self.state()["reason"])
        self.assertNotIn("-d -f", self.calls())

    def test_failure_after_writing_takes_the_update_back(self):
        out = self.run_tool("apply", rc=1, STUB_UPDATE_RC="2", STUB_WRITE_FIRST="1")
        self.assertIn("taken back", out)
        self.assertEqual(self.state()["status"], "failed")
        self.assertIn("rpi-eeprom-update -r", self.calls())
        self.assertFalse(os.path.exists(os.path.join(self.root, "boot", "firmware", "pieeprom.upd")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "run", "rpi-eeprom-update-pending")))

    def test_wrong_staged_config_is_taken_back(self):
        self.run_tool("apply", rc=1, STUB_STAGE_OTHER="1")
        self.assertIn("does not hold the planned config", self.state()["reason"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "boot", "firmware", "pieeprom.upd")))

    def test_an_update_the_service_removed_is_staged_once_more(self):
        self.run_tool("apply")
        self.service()                                           # an rpi-eeprom upgrade, in the staging boot
        self.assertEqual(self.state()["status"], "removed")
        self.assertEqual(self.status_state()["status"], "removed")   # said in the same boot, not "staged"
        self.assertIn("removed", self.run_tool("apply"))         # nothing more this boot
        self.reboot(FACTORY)                                     # never reached the bootloader
        out = self.run_tool("apply")
        self.assertIn("staging it again", out)
        self.assertEqual((self.state()["status"], self.state()["attempts"]), ("staged", 2))
        self.service()
        self.reboot(FACTORY, boot="boot-3")                      # and again: give up
        self.run_tool("apply")
        self.assertEqual(self.state()["status"], "failed")
        self.assertTrue(self.done())

    def test_a_users_r_in_the_staging_boot_stands(self):         # REG1
        self.run_tool("apply")
        self.user_update("-r")                                   # upstream's documented cancel
        self.assertFalse(os.path.exists(os.path.join(self.root, "run", "rpi-eeprom-update-pending")))
        self.assertEqual(self.status_state()["status"], "gone")
        self.service()                                           # an upgrade later in the same boot
        self.assertEqual(self.state()["status"], "staged")       # not mistaken for the service's removal
        self.reboot(FACTORY)
        self.run_tool("apply")
        self.assertEqual(self.state()["status"], "not-applied")
        self.assertTrue(self.done())
        self.assertEqual(self.calls().count("-d -f"), 1)         # never staged again

    def test_a_users_revert_in_the_staging_boot_stands(self):    # REG1: rpi-eeprom-config --apply, raspi-config
        self.run_tool("apply")
        self.write(os.path.join(self.root, "boot", "firmware", "pieeprom.upd"), "the user's own update")
        self.write(os.path.join(self.root, "run", "rpi-eeprom-update-pending"), "immediate 1\n")
        self.service()
        self.assertEqual(self.state()["status"], "staged")
        self.reboot(FACTORY)                                     # the user's config is the one from before
        self.run_tool("apply")
        self.assertEqual(self.state()["status"], "not-applied")
        self.assertEqual(self.calls().count("-d -f"), 1)

    def test_after_the_service_removed_it_settings_can_stage_it_again_at_once(self):
        self.run_tool("apply")
        self.service()                                           # leaves a stale "recovery" flag behind
        self.run_tool("first", "usb")
        self.assertEqual(self.state()["status"], "staged")
        self.assertIn("BOOT_ORDER=0xf614", self.staged_conf())

    def test_another_update_the_service_staged_is_never_taken_back(self):
        self.run_tool("apply")
        self.service(STUB_AUTO_NEW="1")                          # the upgrade staged a newer bootloader
        self.assertEqual(self.state()["status"], "staged")       # it wrote: not a plain removal, no retry
        self.assertEqual(self.status_state()["status"], "gone")
        self.run_tool("first", "usb")
        self.assertEqual(self.state()["status"], "deferred")
        with open(os.path.join(self.root, "boot", "firmware", "pieeprom.upd"), "rb") as fh:
            self.assertEqual(fh.read(), b"newer bootloader")
        self.assertEqual(self.calls().count(" -r "), 0)

    def test_an_own_update_applied_after_the_removal_stands(self):     # SM1/XB6
        self.run_tool("apply")
        self.service()
        self.assertEqual(self.state()["status"], "removed")
        self.reboot(FACTORY)
        self.dt("update-timestamp", int(time.time()) + 100)      # the user's rpi-eeprom-config --apply, stamped later
        self.run_tool("apply")
        st = self.state()
        self.assertEqual(st["status"], "not-applied")
        self.assertIn("it stands", st["reason"])
        self.assertTrue(self.done())
        self.assertEqual(self.calls().count("-d -f"), 1)

    def test_rpi_eeprom_update_a_after_the_removal_stands(self):     # R5-SM4
        self.run_tool("apply")
        self.service()
        self.reboot(FACTORY)
        self.dt("build-timestamp", 1792764809)                   # the user ran rpi-eeprom-update -a
        self.dt("update-timestamp", 1792764900)
        self.run_tool("apply")
        st = self.state()
        self.assertEqual(st["status"], "not-applied")
        self.assertIn("rpi-eeprom-update -a", st["reason"])

    def test_cancel_leaves_another_update_alone(self):            # SM2
        self.run_tool("apply")
        self.service(STUB_AUTO_NEW="1")                          # the upgrade staged a newer bootloader
        self.assertIn("left alone", self.run_tool("cancel"))
        with open(os.path.join(self.root, "boot", "firmware", "pieeprom.upd"), "rb") as fh:
            self.assertEqual(fh.read(), b"newer bootloader")
        self.assertEqual((self.state()["status"], self.done()), ("cancelled", True))
        self.reboot(FACTORY)
        self.assertIn("nothing to do", self.run_tool("apply"))  # the retry is stopped too

    def test_a_choice_made_while_the_service_runs_is_watched(self):   # SM3
        self.run_tool("apply")
        self.run_tool("watch", "before")
        self.run_tool("first", "usb")                            # Settings, while the upgrade runs the service
        subprocess.run([os.path.join(self.tools, "rpi-eeprom-update"), "-s", "-a"], check=True,
                       env={**os.environ, "GENESIS_EEPROM_ROOT": self.root})
        self.run_tool("watch", "after")
        st = self.state()
        self.assertEqual(st["status"], "removed")                # the USB choice is staged again next boot
        self.reboot(FACTORY)
        self.run_tool("apply")
        self.assertIn("BOOT_ORDER=0xf614", self.staged_conf())

    def test_apply_after_a_take_back_says_so(self):              # SM4
        self.run_tool("apply")
        self.user_update("-r")
        out = self.run_tool("apply")
        self.assertIn("no longer on the boot partition", out)
        self.assertNotIn("applied at the next reboot", out)

    def test_watch_after_runs_once(self):                        # SM5: ExecStopPost after ExecStartPost
        self.run_tool("apply")
        self.service()
        before = self.state()
        self.run_tool("watch", "after")                          # the stop at shutdown
        self.assertEqual(self.state()["reason"], before["reason"])

    def test_a_failed_service_run_is_still_seen(self):           # SM5: only ExecStopPost runs
        self.run_tool("apply")
        self.run_tool("watch", "before")
        subprocess.run([os.path.join(self.tools, "rpi-eeprom-update"), "-s", "-a"], check=True,
                       env={**os.environ, "GENESIS_EEPROM_ROOT": self.root})
        self.run_tool("watch", "after")                          # as ExecStopPost
        self.assertEqual(self.state()["status"], "removed")

    def test_a_pending_flag_on_a_boot_partition_that_is_gone_defers(self):   # INT-2
        self.write(os.path.join(self.root, "run", "rpi-eeprom-update-pending"), "recovery 1\n")
        self.run_tool("apply", STUB_BOOTFS_GONE="1")
        self.assertEqual(self.state()["status"], "deferred")
        self.assertNotIn("-d -f", self.calls())

    def test_each_pi_keeps_its_own_original_across_moves(self):  # XB1
        self.run_tool("apply")                                   # Pi A: factory config, NVMe first
        a_genesis = self.staged_conf()
        self.reboot(a_genesis)
        self.run_tool("apply")
        b_own = "[all]\nBOOT_UART=1\nBOOT_ORDER=0xf41\n"
        self.serial("bbbb0000")                                  # to Pi B
        self.reboot(b_own, boot="boot-3")
        self.run_tool("first", "usb")
        self.assertEqual((self.read("original.conf"), self.read("original.serial").strip()), (b_own, "bbbb0000"))
        self.assertEqual(self.read("original-a1b2c3d4.conf"), FACTORY)
        self.serial("a1b2c3d4")                                  # back to A, which runs Genesis's config
        self.reboot(a_genesis, boot="boot-4")
        self.run_tool("first", "sd")
        self.assertEqual((self.read("original.conf"), self.read("original.serial").strip()), (FACTORY, "a1b2c3d4"))
        self.assertEqual(self.read("original-bbbb0000.conf"), b_own)
        self.serial("bbbb0000")                                  # and to B again
        self.reboot(b_own, boot="boot-5")
        self.run_tool("first", "nvme")
        self.assertEqual(self.read("original.conf"), b_own)
        self.assertEqual(self.read("original-a1b2c3d4.conf"), FACTORY)

    def test_a_pi_that_took_another_ones_update_keeps_its_known_original(self):   # XB1/S6
        b_own = "[all]\nBOOT_UART=1\nBOOT_ORDER=0xf41\n"
        self.serial("bbbb0000")
        self.running(b_own)
        self.run_tool("apply")                                   # B first: its original is known
        self.reboot(self.staged_conf())
        self.run_tool("apply")
        self.serial("a1b2c3d4")                                  # A stages, then the medium goes to B unrebooted
        self.reboot(FACTORY, boot="boot-3")
        self.run_tool("first", "nvme")
        a_desired = self.staged_conf()
        self.serial("bbbb0000")
        self.reboot(a_desired, boot="boot-4")
        self.run_tool("apply")
        self.assertEqual(self.read("original.conf"), b_own)
        self.assertFalse(os.path.exists(self.eeprom_file("original.unknown")))
        self.assertTrue(any("took an update staged on Raspberry Pi a1b2c3d4" in n for n in self.state()["notes"]))

    def test_moved_to_a_pi500_sets_the_pi5s_original_aside(self):    # XB2
        self.run_tool("apply")
        self.reboot(FACTORY)
        self.serial("c5c5c5c5")
        self.write(os.path.join(self.root, "proc", "device-tree", "system", "linux,revision"), be32(0xD04190), binary=True)
        self.run_tool("apply")
        self.assertFalse(os.path.exists(self.eeprom_file("original.conf")))
        self.assertEqual(self.read("original-a1b2c3d4.conf"), FACTORY)
        info = json.loads(self.run_tool("status", "--json"))
        self.assertEqual((info["available"], info["serial"], info["state"]["status"]), (False, "c5c5c5c5", "moved"))
        self.assertIn("Serial c5c5c5c5", self.run_tool("status"))

    def test_status_says_when_a_result_is_another_pis(self):       # XB3
        self.run_tool("apply")
        self.reboot(self.staged_conf())
        self.run_tool("apply")
        self.serial("bbbb0000")
        self.reboot("[all]\nBOOT_ORDER=0xf41\n", boot="boot-3")
        st = self.status_state()
        self.assertEqual(st["status"], "other-pi")
        self.assertIn("a1b2c3d4", st["reason"])

    def test_the_move_shows_in_status_text(self):               # XB4
        self.run_tool("apply")
        self.reboot(self.staged_conf())
        self.serial("bbbb0000")
        self.run_tool("apply")
        out = self.run_tool("status")
        self.assertIn("took an update staged on Raspberry Pi a1b2c3d4", out)

    def test_a_second_removal_does_not_promise_a_retry(self):    # XB5
        self.run_tool("apply")
        self.service()
        self.reboot(FACTORY)
        self.run_tool("apply")                                   # staged again (attempt 2)
        self.service()
        reason = self.state()["reason"]
        self.assertIn("not staged again automatically", reason)
        self.assertNotIn("stages it again at the next boot", reason)

    def test_a_move_survives_a_deferral(self):                    # XB7
        self.run_tool("apply")
        self.reboot("[all]\nBOOT_ORDER=0xf41\n")
        self.serial("bbbb0000")
        self.write(os.path.join(self.root, "run", "rpi-eeprom-update-pending"), "immediate 1\n")
        self.run_tool("apply")
        self.assertEqual((self.state()["status"], self.state()["moved_from"]), ("deferred", "a1b2c3d4"))
        self.reboot("[all]\nBOOT_ORDER=0xf41\n", boot="boot-3")
        self.run_tool("apply")
        self.assertEqual((self.state()["status"], self.state()["moved_from"]), ("staged", "a1b2c3d4"))

    def test_an_own_go_back_on_a_newer_bootloader_stands(self):          # R1
        self.run_tool("apply")
        self.service()                                           # the upgrade added a newer default image
        self.reboot(FACTORY)
        self.dt("build-timestamp", 1792764809)                   # the go-back was built from it (rpi-eeprom-update -l)
        self.dt("update-timestamp", int(time.time()) + 100)
        self.run_tool("apply")
        self.assertEqual(self.state()["status"], "not-applied")
        self.assertEqual(self.calls().count("-d -f"), 1)

    def test_a_go_back_during_the_service_run_stands(self):        # R5-SM2
        self.run_tool("apply")
        self.run_tool("watch", "before")
        image = os.path.join(self.tmp, "go-back.bin")
        self.write(image, "the user's image " * 8)
        self.write(image + ".conf", FACTORY)
        self.user_update("-d", "-f", image)                      # rpi-eeprom-config --apply original.conf
        subprocess.run([os.path.join(self.tools, "rpi-eeprom-update"), "-s", "-a"], check=True,
                       env={**os.environ, "GENESIS_EEPROM_ROOT": self.root})
        self.run_tool("watch", "after")
        self.assertEqual(self.state()["status"], "staged")       # not the service's removal
        self.reboot(FACTORY)
        self.run_tool("apply")
        self.assertEqual(self.state()["status"], "not-applied")
        self.assertEqual(self.calls().count("-d -f"), 2)               # Genesis's staging and the user's go-back

    def test_a_take_back_during_the_service_run_stands(self):
        self.run_tool("apply")
        self.run_tool("watch", "before")
        self.user_update("-r")
        subprocess.run([os.path.join(self.tools, "rpi-eeprom-update"), "-s", "-a"], check=True,
                       env={**os.environ, "GENESIS_EEPROM_ROOT": self.root})
        self.run_tool("watch", "after")
        self.assertEqual(self.state()["status"], "staged")
        self.reboot(FACTORY)
        self.run_tool("apply")
        self.assertEqual(self.state()["status"], "not-applied")

    def test_a_watch_that_timed_out_leaves_nothing_for_a_later_stop(self):   # R5-SM1
        import fcntl
        self.run_tool("apply")
        self.run_tool("watch", "before")
        subprocess.run([os.path.join(self.tools, "rpi-eeprom-update"), "-s", "-a"], check=True,
                       env={**os.environ, "GENESIS_EEPROM_ROOT": self.root})
        with open(os.path.join(self.root, "run", "genesis-eeprom.lock"), "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)                       # Settings holds the lock past the wait
            self.assertIn("is held", self.run_tool("watch", "after", GENESIS_EEPROM_LOCK_WAIT="0.5"))
        self.assertFalse(os.path.exists(os.path.join(self.root, "run", "genesis-eeprom-watch")))
        self.run_tool("watch", "after")                          # a later stop (shutdown): nothing to misread
        self.assertEqual(self.state()["status"], "staged")

    def test_a_choice_made_while_the_service_runs_with_nothing_staged_is_watched(self):   # R2
        self.run_tool("apply")
        self.reboot(self.staged_conf())
        self.run_tool("apply")                                   # applied, done
        self.run_tool("watch", "before")                         # an upgrade restarts the service: nothing staged
        self.run_tool("first", "usb")
        subprocess.run([os.path.join(self.tools, "rpi-eeprom-update"), "-s", "-a"], check=True,
                       env={**os.environ, "GENESIS_EEPROM_ROOT": self.root})
        self.run_tool("watch", "after")
        self.assertEqual(self.state()["status"], "removed")

    def test_only_root_can_take_the_lock(self):                   # R4-INT-1
        self.run_tool("apply")
        self.assertEqual(os.stat(os.path.join(self.root, "run", "genesis-eeprom.lock")).st_mode & 0o777, 0o600)

    def test_watch_after_never_waits_for_the_lock_when_it_has_nothing_to_do(self):
        import fcntl
        self.run_tool("apply")
        lock = os.path.join(self.root, "run", "genesis-eeprom.lock")
        with open(lock, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)                       # someone holds it for good
            started = time.monotonic()
            self.run_tool("watch", "after")                      # a stop or shutdown: no marker
            self.assertLess(time.monotonic() - started, 5)

    def test_a_move_record_stays_with_the_pi_that_recorded_it(self):     # R3
        self.run_tool("apply")
        a_desired = self.staged_conf()
        self.serial("bbbb0000")
        self.reboot(a_desired)                                   # B takes A's update
        self.write(os.path.join(self.root, "run", "rpi-eeprom-update-pending"), "immediate 1\n")
        self.run_tool("apply")
        self.assertEqual((self.state()["status"], self.state()["moved_from"]), ("deferred", "a1b2c3d4"))
        self.serial("a1b2c3d4")                                  # back to A before B rebooted
        self.reboot(FACTORY, boot="boot-3")
        self.run_tool("apply")
        st = self.state()
        self.assertEqual(st["status"], "staged")
        self.assertNotIn("moved_from", st)
        self.assertNotIn("took an update", st["reason"])

    def test_every_result_names_its_pi(self):                     # XB3
        self.running(FACTORY + "FREEZE_VERSION=1\n")
        self.run_tool("apply")
        self.assertEqual((self.state()["status"], self.state()["serial"]), ("skipped", "a1b2c3d4"))
        self.serial("bbbb0000")
        self.assertEqual(self.status_state()["status"], "other-pi")
        self.run_tool("cancel")
        self.assertEqual(self.state()["serial"], "bbbb0000")

    def test_status_names_this_pis_original(self):                # docs: comes back only when Genesis runs again
        self.run_tool("apply")
        self.reboot(self.staged_conf())
        self.run_tool("apply")
        self.serial("bbbb0000")
        self.reboot("[all]\nBOOT_ORDER=0xf41\n", boot="boot-3")
        self.run_tool("first", "usb")
        self.reboot(self.staged_conf(), boot="boot-4")
        self.run_tool("apply")                                   # B applied and done
        self.serial("a1b2c3d4")                                  # back on A: nothing runs
        info = json.loads(self.run_tool("status", "--json"))
        self.assertEqual(info["original"], self.eeprom_file("original-a1b2c3d4.conf"))
        self.assertEqual(info["state"]["notes"], [])
        self.assertIn("Config before Genesis OS " + self.eeprom_file("original-a1b2c3d4.conf"), self.run_tool("status"))

    def test_another_pis_result_on_a_pi500_promises_nothing(self):   # R4
        self.run_tool("apply")
        self.reboot(self.staged_conf())
        self.run_tool("apply")
        self.serial("c5c5c5c5")
        self.write(os.path.join(self.root, "proc", "device-tree", "system", "linux,revision"), be32(0xD04190), binary=True)
        st = self.status_state()
        self.assertEqual(st["status"], "other-pi")
        self.assertIn("changes nothing on this board", st["reason"])

    def test_status_tells_unknown_from_none(self):               # R5-XB-1
        info = json.loads(self.run_tool("status", "--json"))
        self.assertEqual((info["original"], info["original_unknown"]), (None, False))
        self.assertIn("none recorded", self.run_tool("status"))
        self.run_tool("apply")
        self.reboot(self.staged_conf())
        self.serial("bbbb0000")
        self.run_tool("apply")                                   # B took A's update
        info = json.loads(self.run_tool("status", "--json"))
        self.assertEqual((info["original"], info["original_unknown"]), (None, True))
        self.assertIn("unknown (this board took, or may have taken, another Pi's update", self.run_tool("status"))

    def test_cancel_on_another_pis_record_starts_this_pis_own(self):   # R5-XB-2
        self.run_tool("apply")
        self.reboot(self.staged_conf())
        self.serial("cccc0000")
        self.run_tool("apply")                                   # C took A's update: its note
        self.serial("a1b2c3d4")
        self.run_tool("cancel")
        st = self.state()
        self.assertEqual((st["status"], st["serial"], st.get("notes")), ("cancelled", "a1b2c3d4", None))
        self.assertNotIn("took an update", self.run_tool("status"))

    def test_a_moved_record_is_shown_only_on_its_board(self):     # R5-XB-3
        self.run_tool("apply")
        self.reboot(FACTORY)
        self.serial("c5c5c5c5")
        self.write(os.path.join(self.root, "proc", "device-tree", "system", "linux,revision"), be32(0xD04190), binary=True)
        self.run_tool("apply")                                   # a Pi 500 records "moved"
        self.assertEqual(self.status_state()["status"], "moved")
        self.serial("d4d4d4d4")                                  # then a CM5
        self.write(os.path.join(self.root, "proc", "device-tree", "system", "linux,revision"), be32(0xD04180), binary=True)
        self.assertEqual(self.status_state()["status"], "other-pi")

    def test_off_keeps_its_move_record_on_its_own_board_only(self):   # R6-DS-3
        self.run_tool("apply")
        self.reboot(self.staged_conf())
        self.serial("bbbb0000")
        self.settings("enabled=0\n")
        self.run_tool("apply")
        self.assertEqual(self.state()["status"], "off")
        self.assertIn("took an update staged on Raspberry Pi a1b2c3d4", self.run_tool("status"))
        self.serial("a1b2c3d4")                                  # on A: still off, but not A's move
        st = self.status_state()
        self.assertEqual(st["status"], "off")
        self.assertNotIn("notes", st)
        self.assertNotIn("took an update", self.run_tool("status"))

    def test_a_before_that_cannot_lock_still_marks_the_run(self):    # R5-SM3
        import fcntl
        self.run_tool("apply")
        self.reboot(self.staged_conf())
        self.run_tool("apply")                                   # applied, done
        lock = os.path.join(self.root, "run", "genesis-eeprom.lock")
        with open(lock, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)                       # Settings is staging and holds the lock
            self.run_tool("watch", "before", GENESIS_EEPROM_LOCK_WAIT="0.5")
        with open(os.path.join(self.root, "run", "genesis-eeprom-watch")) as fh:
            self.assertEqual(fh.read().strip(), "-")
        self.run_tool("first", "usb")                            # its staging finishes and puts its sha there
        subprocess.run([os.path.join(self.tools, "rpi-eeprom-update"), "-s", "-a"], check=True,
                       env={**os.environ, "GENESIS_EEPROM_ROOT": self.root})
        self.run_tool("watch", "after")
        self.assertEqual(self.state()["status"], "removed")

    def test_an_apply_never_creates_the_marker(self):            # R6-SM1
        self.run_tool("first", "usb")
        self.assertFalse(os.path.exists(os.path.join(self.root, "run", "genesis-eeprom-watch")))

    def test_an_update_made_after_the_removal_stops_the_retry(self):   # R6-SM2
        self.run_tool("apply")
        self.service()
        self.assertEqual(self.state()["status"], "removed")
        self.write(os.path.join(self.root, "run", "rpi-eeprom-update-pending"), "recovery 42\n")   # the user's go-back
        self.service()                                           # a second upgrade removes it again
        self.assertTrue(self.state()["superseded"])
        self.reboot(FACTORY)
        self.run_tool("apply")
        self.assertEqual(self.state()["status"], "not-applied")
        self.assertEqual(self.calls().count("-d -f"), 1)

    def test_a_pi500_that_may_have_taken_the_update_is_unknown(self):   # R6-DS-1
        self.run_tool("apply")
        self.reboot(FACTORY)
        self.serial("c5c5c5c5")
        self.write(os.path.join(self.root, "proc", "device-tree", "system", "linux,revision"), be32(0xD04190), binary=True)
        self.run_tool("apply")
        info = json.loads(self.run_tool("status", "--json"))
        self.assertEqual((info["original"], info["original_unknown"]), (None, True))

    def test_cancel_on_another_board_takes_back_the_update_on_the_medium(self):   # R6-DS-2
        self.run_tool("apply")                                   # A stages and does not reboot
        self.serial("bbbb0000")                                  # B: its own run did not get to save anything
        self.boot_id("boot-2")
        self.run_tool("cancel")
        self.assertFalse(os.path.exists(os.path.join(self.root, "boot", "firmware", "pieeprom.upd")))
        self.assertEqual((self.state()["status"], self.state()["serial"]), ("cancelled", "bbbb0000"))

    def test_watch_without_a_staged_update_does_nothing(self):
        self.service()
        self.assertFalse(os.path.exists(self.eeprom_file("state.json")))

    def test_original_conf_keeps_the_config_from_before_genesis(self):
        self.run_tool("apply")
        self.reboot(self.staged_conf())                          # applied: the running config is Genesis's now
        self.run_tool("apply")
        self.run_tool("first", "usb")                            # a second change, on top of the first
        with open(os.path.join(self.root, "var", "lib", "genesis", "eeprom", "previous.conf")) as fh:
            self.assertIn("BOOT_ORDER=0xf416", fh.read())
        with open(os.path.join(self.root, "var", "lib", "genesis", "eeprom", "original.conf")) as fh:
            self.assertEqual(fh.read(), FACTORY)

    def test_moved_to_another_pi_that_took_the_update(self):
        self.run_tool("apply")
        self.reboot("[all]\nBOOT_UART=1\nBOOT_ORDER=0xf416\nNET_INSTALL_AT_POWER_ON=0\nPCIE_PROBE=1\n")
        self.write(os.path.join(self.root, "proc", "device-tree", "serial-number"), b"ffff0000\x00", binary=True)
        out = self.run_tool("apply")
        self.assertIn("staged on Raspberry Pi a1b2c3d4", out)
        st = self.state()
        self.assertEqual((st["status"], st["serial"], st["moved_from"]), ("unchanged", "ffff0000", "a1b2c3d4"))
        self.assertTrue(any("took an update staged on Raspberry Pi a1b2c3d4" in n for n in st["notes"]), st)
        # the first Pi's config is set aside, and none is claimed for this one
        self.assertFalse(os.path.exists(self.eeprom_file("original.conf")))
        with open(self.eeprom_file("original-a1b2c3d4.conf")) as fh:
            self.assertEqual(fh.read(), FACTORY)
        self.run_tool("first", "usb")
        self.assertEqual(self.state()["status"], "staged")
        self.assertFalse(os.path.exists(self.eeprom_file("original.conf")))

    def test_moved_to_another_pi_that_did_not_take_it(self):
        self.run_tool("apply")
        own = "[all]\nBOOT_UART=1\nBOOT_ORDER=0xf41\n"
        self.reboot(own)
        self.write(os.path.join(self.root, "proc", "device-tree", "serial-number"), b"ffff0000\x00", binary=True)
        self.run_tool("apply")
        st = self.state()
        self.assertEqual((st["status"], st["moved_from"]), ("staged", "a1b2c3d4"))
        with open(self.eeprom_file("original.conf")) as fh:
            self.assertEqual(fh.read(), own)                     # this Pi's own
        with open(self.eeprom_file("original.serial")) as fh:
            self.assertEqual(fh.read().strip(), "ffff0000")
        self.assertTrue(os.path.exists(self.eeprom_file("original-a1b2c3d4.conf")))

    def test_moved_to_a_board_that_is_not_a_pi5(self):
        self.run_tool("apply")
        self.reboot(FACTORY)
        self.write(os.path.join(self.root, "proc", "device-tree", "system", "linux,revision"), be32(0xD04190), binary=True)
        self.assertIn("not a Raspberry Pi 5", self.run_tool("apply"))   # a Pi 500
        st = self.state()
        self.assertEqual((st["status"], st["moved_from"]), ("moved", "a1b2c3d4"))
        self.assertNotEqual(self.status_state()["status"], "staged")
        self.assertEqual(self.calls().count("-d -f"), 1)

    def test_turned_off_after_staging_is_not_staged_forever(self):   # state/F5
        self.run_tool("apply")
        self.settings("enabled=0\n")
        self.run_tool("apply", "--again")                       # takes Genesis's own update back
        self.assertFalse(os.path.exists(os.path.join(self.root, "boot", "firmware", "pieeprom.upd")))
        self.assertEqual(self.state()["status"], "off")
        self.reboot(FACTORY)
        self.run_tool("apply")
        self.assertEqual(self.status_state()["status"], "off")
        self.assertEqual(self.calls().count("-d -f"), 1)

    def test_turned_off_before_a_retry(self):                      # state/F5, the retry path
        self.run_tool("apply")
        self.service()
        self.settings("enabled=0\n")
        self.reboot(FACTORY)
        self.run_tool("apply")
        self.assertEqual(self.state()["status"], "off")
        self.assertEqual(self.calls().count("-d -f"), 1)

    def test_a_leftover_update_file_without_a_pending_flag_does_not_block(self):
        self.write(os.path.join(self.root, "boot", "firmware", "pieeprom.upd"), "old")
        self.run_tool("apply")
        self.assertEqual(self.state()["status"], "staged")

    def test_deferrals_count_boots_not_runs(self):
        self.write(os.path.join(self.root, "run", "rpi-eeprom-update-pending"), "immediate 1\n")
        for _ in range(4):
            self.run_tool("apply")                               # the same boot, four times
        self.assertEqual((self.state()["status"], self.state()["deferrals"]), ("deferred", 1))

    def test_cancel(self):
        self.run_tool("apply")
        self.assertIn("cancelled", self.run_tool("cancel"))
        self.assertEqual(self.state()["status"], "cancelled")
        self.assertTrue(self.done())
        self.assertFalse(os.path.exists(os.path.join(self.root, "boot", "firmware", "pieeprom.upd")))

    def test_a_choice_made_with_customisation_off_is_still_checked(self):
        self.settings("enabled=0\n")
        self.run_tool("first", "nvme")                            # Settings turns it on for this choice
        self.assertEqual(self.state()["status"], "staged")
        self.settings("enabled=0\n")                              # even if turned off again before the reboot
        self.reboot(self.staged_conf())
        self.run_tool("apply")
        self.assertEqual(self.state()["status"], "applied")

    def test_first_keep_applies_only_the_flags(self):
        self.run_tool("first", "keep")
        conf = self.staged_conf()
        self.assertIn("BOOT_ORDER=0xf461", conf)
        self.assertIn("PCIE_PROBE=1", conf)

    def test_a_broken_settings_file_is_reported_not_crashed_or_final(self):
        self.settings("boot_first=floppy\n")
        out = self.run_tool("apply", rc=1)
        self.assertIn("boot_first", out)
        self.assertNotIn("Traceback", out)
        self.assertFalse(self.done())

    def test_secure_boot(self):
        self.write(os.path.join(self.root, "proc", "device-tree", "chosen", "bootloader", "signed"), be32(1), binary=True)
        self.assertIn("secure boot", self.run_tool("apply"))
        self.assertNotIn("-d -f", self.calls())

    def test_off(self):
        self.settings("enabled=0\n")
        self.assertIn("off", self.run_tool("apply"))
        self.assertEqual(self.calls(), "")

    def test_first_replaces_its_own_staged_update(self):
        self.run_tool("apply")                                    # NVMe first, staged
        out = self.run_tool("first", "usb")
        self.assertIn("staged", out)
        self.assertIn("rpi-eeprom-update -r", self.calls())
        self.assertIn("BOOT_ORDER=0xf614", self.staged_conf())
        with open(os.path.join(self.root, "etc", "genesis", "bootloader.conf")) as fh:
            self.assertIn("boot_first=usb", fh.read())

    def test_status(self):
        info = json.loads(self.run_tool("status", "--json"))
        self.assertTrue(info["available"])
        self.assertEqual((info["booted_from"], info["boot_order"], info["version"]), ("NVMe", "0xf461", "2026-09-25"))

    def test_plan_cli(self):
        src = os.path.join(self.tmp, "running.conf")
        self.write(src, FACTORY)
        out = self.run_tool("plan", "--running", src, "--boot-mode", "4")
        self.assertIn("BOOT_ORDER=0xf614", out)

    def test_a_fake_root_never_reaches_the_real_tools(self):
        result = subprocess.run([sys.executable, "-I", TOOL, "apply"], capture_output=True, text=True,
                                env={**os.environ, "GENESIS_EEPROM_ROOT": self.root})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GENESIS_EEPROM_TOOLS", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
