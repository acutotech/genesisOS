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
    print(bootfs)
elif args[:2] == ["-d", "-f"]:
    if os.environ.get("RPI_EEPROM_IMMEDIATE_UPDATE") != "0":
        sys.exit(9)   # an immediate write is never what genesis-eeprom may ask for
    rc = int(os.environ.get("STUB_UPDATE_RC", "0"))
    if rc and not os.environ.get("STUB_WRITE_FIRST"):
        sys.exit(rc)
    upd = os.path.join(bootfs, "pieeprom.upd")
    shutil.copyfile(args[2], upd)
    shutil.copyfile(args[2] + ".conf", upd + ".conf")
    if os.environ.get("STUB_STAGE_OTHER"):
        with open(upd + ".conf", "w") as fh:
            fh.write("[all]\nBOOT_ORDER=0xf21\n")
    with open(upd, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    with open(os.path.join(bootfs, "pieeprom.sig"), "w") as fh:
        fh.write(digest + "\nts: 1\n")
    os.makedirs(os.path.dirname(pending), exist_ok=True)
    with open(pending, "w") as fh:
        fh.write("recovery %d\n" % time.time())
    if rc:
        sys.exit(rc)   # e.g. copying recovery.bin failed after the update was written
elif args == ["-r"]:
    for name in ("pieeprom.upd", "pieeprom.upd.conf", "pieeprom.sig"):
        if os.path.exists(os.path.join(bootfs, name)):
            os.remove(os.path.join(bootfs, name))
    if os.path.exists(pending):
        os.remove(pending)
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
        result = subprocess.run([sys.executable, "-I", TOOL, *args], capture_output=True, text=True,
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

    def test_an_update_removed_before_the_reboot_is_staged_once_more(self):
        self.run_tool("apply")
        self.reboot(FACTORY)                                     # never reached the bootloader
        out = self.run_tool("apply")
        self.assertIn("staging it again", out)
        self.assertEqual((self.state()["status"], self.state()["attempts"]), ("staged", 2))
        self.reboot(FACTORY, boot="boot-3")                      # and again: give up
        self.run_tool("apply")
        self.assertEqual(self.state()["status"], "failed")
        self.assertTrue(self.done())

    def test_original_conf_keeps_the_config_from_before_genesis(self):
        self.run_tool("apply")
        self.reboot(self.staged_conf())                          # applied: the running config is Genesis's now
        self.run_tool("apply")
        self.run_tool("first", "usb")                            # a second change, on top of the first
        with open(os.path.join(self.root, "var", "lib", "genesis", "eeprom", "previous.conf")) as fh:
            self.assertIn("BOOT_ORDER=0xf416", fh.read())
        with open(os.path.join(self.root, "var", "lib", "genesis", "eeprom", "original.conf")) as fh:
            self.assertEqual(fh.read(), FACTORY)

    def test_moved_to_another_pi_before_the_reboot(self):
        self.run_tool("apply")
        self.reboot("[all]\nBOOT_UART=1\nBOOT_ORDER=0xf416\nNET_INSTALL_AT_POWER_ON=0\nPCIE_PROBE=1\n")
        self.write(os.path.join(self.root, "proc", "device-tree", "serial-number"), b"ffff0000\x00", binary=True)
        out = self.run_tool("apply")
        self.assertIn("another Raspberry Pi", out)
        self.assertEqual(self.state()["status"], "unchanged")    # planned again for this board: it already has it
        self.assertEqual(self.state()["serial"], "ffff0000")

    def test_a_leftover_update_file_without_a_pending_flag_does_not_block(self):
        self.write(os.path.join(self.root, "boot", "firmware", "pieeprom.upd"), "old")
        self.run_tool("apply")
        self.assertEqual(self.state()["status"], "staged")

    def test_deferrals_count_boots_not_runs(self):
        self.write(os.path.join(self.root, "run", "rpi-eeprom-update-pending"), "recovery 1\n")
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
