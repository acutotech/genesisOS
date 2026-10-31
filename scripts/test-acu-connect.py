#!/usr/bin/env python3
"""Tests for acu-connect-service (stage-custom/16-acu-connect/files/acu-connect-service).

    python3 scripts/test-acu-connect.py

The supervisor runs against a stand-in client that records each start and does
what the test asks (exit with a code, or run until it is stopped). Checked:
it waits quietly while the device is not linked, starts the client once it is,
restarts a failing client with growing pauses, sets a revoked link aside and
waits for a new signin instead of stopping for good, stops promptly on SIGTERM,
and hands the client the desktop session's Wayland socket. Also the wheel rule of
scripts/acu-connect-lock.py. The real client and its Python packages are checked
inside the built image (scripts/image-smoke.sh). Used by `make test`.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SERVICE = os.path.join(HERE, "..", "stage-custom", "16-acu-connect", "files", "acu-connect-service")
LOCK_TOOL = os.path.join(HERE, "acu-connect-lock.py")

STUB = r'''#!/usr/bin/env python3
import json, os, signal, sys, time
work = os.environ["STUB_DIR"]
with open(os.path.join(work, "calls.jsonl"), "a") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "wayland": os.environ.get("WAYLAND_DISPLAY"),
                         "config": os.environ.get("ACU_CONFIG_DIR")}) + "\n")
plan = os.path.join(work, "plan")
steps = open(plan).read().split() if os.path.exists(plan) else []
step = steps.pop(0) if steps else "serve"
with open(plan, "w") as fh:
    fh.write(" ".join(steps))
if step == "serve":
    signal.signal(signal.SIGTERM, lambda *_: (open(os.path.join(work, "terminated"), "w").close(), sys.exit(0)))
    while True:
        time.sleep(0.05)
sys.exit(int(step))
'''


def wait_for(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class Supervisor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="acu-connect-test.")
        self.config = os.path.join(self.tmp, "state")
        os.makedirs(self.config)
        self.runtime = os.path.join(self.tmp, "runtime")
        os.makedirs(self.runtime)
        self.stub = os.path.join(self.tmp, "client")
        with open(self.stub, "w") as fh:
            fh.write(STUB)
        os.chmod(self.stub, 0o755)
        self.proc = None
        self.out = None

    def tearDown(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        if self.out:
            self.out.close()
        shutil.rmtree(self.tmp)

    def start(self, plan="", **env):
        with open(os.path.join(self.tmp, "plan"), "w") as fh:
            fh.write(plan)
        self.out = open(os.path.join(self.tmp, "out.log"), "w")
        self.proc = subprocess.Popen(
            [sys.executable, "-I", SERVICE], stdout=self.out, stderr=subprocess.STDOUT,
            env={**os.environ, "STUB_DIR": self.tmp, "ACU_CONNECT_CLIENT": f"{sys.executable} {self.stub}",
                 "ACU_CONFIG_DIR": self.config, "XDG_RUNTIME_DIR": self.runtime, "WAYLAND_DISPLAY": "wayland-0",
                 "ACU_CONNECT_POLL": "0.1", "ACU_CONNECT_PAUSE": "0.2", **env})

    def link(self):
        with open(os.path.join(self.config, "agent.json"), "w") as fh:
            json.dump({"server": "https://example.test", "deviceToken": "t"}, fh)

    def calls(self):
        try:
            with open(os.path.join(self.tmp, "calls.jsonl")) as fh:
                return [json.loads(line) for line in fh]
        except FileNotFoundError:
            return []

    def output(self):
        self.out.flush()
        with open(os.path.join(self.tmp, "out.log")) as fh:
            return fh.read()

    def stop(self):
        self.proc.send_signal(signal.SIGTERM)
        self.assertEqual(self.proc.wait(timeout=10), 0)

    def test_waits_quietly_until_linked_then_runs_the_client(self):
        self.start()
        time.sleep(0.8)
        self.assertEqual(self.calls(), [])                       # no failing client restarted over and over
        self.assertEqual(self.output().count("not linked yet"), 1)
        self.link()
        self.assertTrue(wait_for(lambda: len(self.calls()) == 1))
        self.assertEqual(self.calls()[0]["argv"], ["run"])
        self.assertEqual(self.calls()[0]["config"], self.config)
        self.stop()
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "terminated")))   # the client was stopped too

    def test_a_failing_client_is_restarted_with_growing_pauses(self):
        self.link()
        self.start("1 1 1")
        self.assertTrue(wait_for(lambda: len(self.calls()) == 4))
        out = self.output()
        for pause in ("0.2 s", "0.4 s", "0.8 s"):
            self.assertIn(f"starting it again in {pause}", out)
        self.assertEqual(self.calls()[-1]["argv"], ["run"])
        self.stop()

    def test_a_revoked_device_waits_for_a_new_signin(self):
        self.link()
        self.start("2")
        self.assertTrue(wait_for(lambda: os.path.exists(os.path.join(self.config, "agent.json.revoked"))))
        self.assertFalse(os.path.exists(os.path.join(self.config, "agent.json")))
        time.sleep(0.6)
        self.assertEqual(len(self.calls()), 1)                   # not started again with a dead link
        self.assertIn("not linked yet", self.output())
        self.assertIsNone(self.proc.poll())                      # and the service keeps running
        self.link()                                              # acu-connect signin
        self.assertTrue(wait_for(lambda: len(self.calls()) == 2))
        self.stop()

    def test_stops_promptly_while_waiting(self):
        self.start()
        time.sleep(0.3)
        started = time.monotonic()
        self.stop()
        self.assertLess(time.monotonic() - started, 3)

    def test_the_desktop_sessions_wayland_socket_is_used(self):
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(os.path.join(self.runtime, "wayland-1"))
        open(os.path.join(self.runtime, "wayland-1.lock"), "w").close()
        self.link()
        self.start()
        self.assertTrue(wait_for(lambda: len(self.calls()) == 1))
        self.assertEqual(self.calls()[0]["wayland"], "wayland-1")
        self.stop()
        sock.close()

    def test_without_a_session_the_default_display_is_kept(self):
        self.link()
        self.start()
        self.assertTrue(wait_for(lambda: len(self.calls()) == 1))
        self.assertEqual(self.calls()[0]["wayland"], "wayland-0")
        self.stop()


class LockRule(unittest.TestCase):
    def setUp(self):
        loader = importlib.machinery.SourceFileLoader("acu_connect_lock", LOCK_TOOL)
        spec = importlib.util.spec_from_loader("acu_connect_lock", loader)
        self.lock = importlib.util.module_from_spec(spec)
        loader.exec_module(self.lock)

    def test_wheels_the_image_can_install(self):
        usable = self.lock.usable
        self.assertTrue(usable("aiortc-1.15.0-py3-none-any.whl", "cp314"))
        self.assertTrue(usable("av-17.1.0-cp311-abi3-manylinux_2_28_aarch64.whl", "cp314"))
        self.assertTrue(usable("cffi-2.1.1-cp314-cp314-manylinux2014_aarch64.manylinux_2_17_aarch64.whl", "cp314"))
        self.assertFalse(usable("cffi-2.1.1-cp313-cp313-manylinux2014_aarch64.manylinux_2_17_aarch64.whl", "cp314"))
        self.assertFalse(usable("cffi-2.1.1-cp314-cp314-manylinux2014_x86_64.whl", "cp314"))
        self.assertFalse(usable("cffi-2.1.1-cp314-cp314-musllinux_1_2_aarch64.whl", "cp314"))
        self.assertFalse(usable("av-17.1.0-cp315-abi3-manylinux_2_28_aarch64.whl", "cp314"))
        self.assertFalse(usable("av-17.1.0.tar.gz", "cp314"))

    def test_the_lock_pins_every_requirement_with_hashes(self):
        stage = os.path.join(HERE, "..", "stage-custom", "16-acu-connect", "files")
        with open(os.path.join(stage, "acu-connect-raspberry-pi", "requirements.txt")) as fh:
            wanted = {line.split("#")[0].strip() for line in fh if line.split("#")[0].strip()}
        with open(os.path.join(stage, "requirements.lock")) as fh:
            text = fh.read()
        pinned = {line.split(" ")[0] for line in text.splitlines() if line and not line.startswith((" ", "#"))}
        self.assertEqual(pinned, wanted, "regenerate with scripts/acu-connect-lock.py")
        for block in text.split("\n")[4:]:
            if block and not block.startswith((" ", "#")):
                self.assertTrue(block.endswith("\\"), block)    # every pin carries at least one hash


if __name__ == "__main__":
    unittest.main(verbosity=2)
