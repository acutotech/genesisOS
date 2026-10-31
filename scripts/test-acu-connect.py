#!/usr/bin/env python3
"""Tests for acu-connect-service (stage-custom/16-acu-connect/files/acu-connect-service).

    python3 scripts/test-acu-connect.py

The supervisor runs against a stand-in client that records each start and does
what the test asks (exit with a code, run until it is stopped, ignore SIGTERM,
or re-link and report a revoke). Checked: it waits quietly while the device is
not linked, starts the client once it is, restarts a failing client with growing
pauses, sets a revoked link aside and waits for a new signin instead of stopping
for good, never sets aside a link newer than the client's, restarts or stops the
client when the link is replaced or removed, holds the client back while a
first-boot rename of the desktop user is pending, kills a client that ignores
SIGTERM, keeps systemd's variables away from the client, hands it the desktop
session's Wayland socket, and (as root) runs it as the desktop user. Also the
wheel rule of scripts/acu-connect-lock.py. The real client and its Python
packages are checked inside the built image (scripts/image-smoke.sh). Used by
`make test`.
"""
import importlib.machinery
import importlib.util
import json
import os
import pwd
import re
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
WRAPPER = os.path.join(HERE, "..", "stage-custom", "16-acu-connect", "files", "acu-connect")
LOCK_TOOL = os.path.join(HERE, "acu-connect-lock.py")

STUB = r'''#!/usr/bin/env python3
import json, os, signal, sys, time
work = os.environ["STUB_DIR"]
config = os.path.join(os.environ.get("ACU_CONFIG_DIR", ""), "agent.json")
try:
    token = json.load(open(config))["deviceToken"]
except (OSError, ValueError, KeyError):
    token = None
plan = os.path.join(work, "plan")
steps = open(plan).read().split() if os.path.exists(plan) else []
step = steps.pop(0) if steps else "serve"
with open(plan, "w") as fh:
    fh.write(" ".join(steps))
def ended(*_):
    with open(os.path.join(work, "terminated"), "a") as fh:
        fh.write(f"{token}\n")
    sys.exit(0)
# signal handling first, so a test that sees the start below can stop the client at once
if step == "serve":
    signal.signal(signal.SIGTERM, ended)
elif step == "stubborn":          # ignores SIGTERM
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(os.path.join(work, "calls.jsonl"), "a") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "wayland": os.environ.get("WAYLAND_DISPLAY"),
                         "config": os.environ.get("ACU_CONFIG_DIR"), "token": token, "pid": os.getpid(),
                         "uid": os.getuid(), "home": os.environ.get("HOME"), "env": sorted(os.environ)}) + "\n")
if step in ("serve", "stubborn"):
    while True:
        time.sleep(0.05)
if step == "signout2":            # signout removed the link, and the server dropped the client
    os.remove(config)
    sys.exit(2)
if step == "relink2":             # signin --force landed just as the old link was revoked
    tmp = config + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"server": "https://example.test", "deviceToken": "new"}, fh)
    os.replace(tmp, config)
    sys.exit(2)
sys.exit(int(step))
'''


def reachable_by_others(path):
    """Whether another user (the root tests switch to nobody) can run this file."""
    path = os.path.realpath(path)
    parent = os.path.dirname(path)
    while parent != os.path.dirname(parent):
        if not os.stat(parent).st_mode & 0o001:
            return False
        parent = os.path.dirname(parent)
    return os.stat(path).st_mode & 0o005 == 0o005


ROOT_ONLY = "needs root, as acu-connect.service runs"
NOT_REACHABLE = "this Python is not reachable by nobody"


def wait_for(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class Supervisor(unittest.TestCase):
    def setUp(self):
        # /tmp: short enough for an AF_UNIX path, and reachable by the uid the root test switches to
        self.tmp = tempfile.mkdtemp(prefix="acu-connect-test.", dir="/tmp" if os.path.isdir("/tmp") else None)
        self.proc_dir = os.path.join(self.tmp, "proc")         # a stand-in /proc for the wizard check
        os.makedirs(self.proc_dir)
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
        for call in self.calls():   # any client a failed test (or a dead supervisor) left behind
            try:
                os.kill(call["pid"], signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
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
                 "ACU_CONNECT_POLL": "0.1", "ACU_CONNECT_PAUSE": "0.2", "ACU_CONNECT_GRACE": "1",
                 "ACU_CONNECT_UID": str(os.getuid()), "ACU_CONNECT_HOLD": self.hold,
                 # the wizard's user is this user, its processes are what proc_dir lists
                 "ACU_CONNECT_WIZARD_USER": pwd.getpwuid(os.getuid()).pw_name, "ACU_CONNECT_PROC": self.proc_dir,
                 **env})

    @property
    def hold(self):
        return os.path.join(self.tmp, "rename_user.conf")

    def wizard(self, running=True):
        """The first-boot wizard's session starts (a process of its user appears) or ends."""
        path = os.path.join(self.proc_dir, "4242")
        if running:
            os.makedirs(path, exist_ok=True)
        else:
            os.rmdir(path)

    def link(self, token="t"):
        """What acu-connect signin does: the new agent.json replaces the old one in one step."""
        tmp = os.path.join(self.config, "agent.json.tmp")
        with open(tmp, "w") as fh:
            json.dump({"server": "https://example.test", "deviceToken": token}, fh)
        os.replace(tmp, os.path.join(self.config, "agent.json"))

    def terminated(self):
        try:
            with open(os.path.join(self.tmp, "terminated")) as fh:
                return fh.read().split()
        except FileNotFoundError:
            return []

    @staticmethod
    def alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        try:   # a zombie is gone for this purpose
            with open(f"/proc/{pid}/stat") as fh:
                return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
        except OSError:
            return True

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


    def test_a_relink_while_running_restarts_the_client_on_the_new_link(self):
        self.link("old")
        self.start()
        self.assertTrue(wait_for(lambda: len(self.calls()) == 1))
        self.link("new")                                         # acu-connect signin --force
        self.assertTrue(wait_for(lambda: len(self.calls()) == 2))
        self.assertEqual([c["token"] for c in self.calls()], ["old", "new"])
        self.assertEqual(self.terminated(), ["old"])             # the old link stopped serving
        self.assertIn("the link changed", self.output())
        self.stop()

    def test_a_signout_while_running_stops_the_client(self):
        self.link("old")
        self.start()
        self.assertTrue(wait_for(lambda: len(self.calls()) == 1))
        os.remove(os.path.join(self.config, "agent.json"))       # signout, even with the server unreachable
        self.assertTrue(wait_for(lambda: self.terminated() == ["old"]))
        self.assertTrue(wait_for(lambda: "not linked yet" in self.output()))
        time.sleep(0.5)
        self.assertEqual(len(self.calls()), 1)
        self.assertIsNone(self.proc.poll())
        self.stop()

    def test_a_revoke_of_the_old_link_never_sets_aside_a_new_one(self):
        self.link("old")
        self.start("relink2")
        self.assertTrue(wait_for(lambda: len(self.calls()) == 2))
        self.assertEqual(self.calls()[1]["token"], "new")       # started again at once on the new link
        self.assertFalse(os.path.exists(os.path.join(self.config, "agent.json.revoked")))
        with open(os.path.join(self.config, "agent.json")) as fh:
            self.assertEqual(json.load(fh)["deviceToken"], "new")
        self.stop()

    def test_no_client_while_a_rename_is_pending(self):
        open(self.hold, "w").close()                             # rename-user ran (first-boot wizard armed)
        self.link()
        self.start()
        time.sleep(0.8)
        self.assertEqual(self.calls(), [])                       # nothing runs as the desktop user
        self.assertEqual(self.output().count("waiting for the rename of the desktop user"), 1)
        os.remove(self.hold)                                     # done or cancelled (cancel-rename)
        self.assertTrue(wait_for(lambda: len(self.calls()) == 1))
        self.stop()

    def test_rename_user_armed_from_a_remote_shell_does_not_cut_the_client(self):
        self.link()
        self.start()
        self.assertTrue(wait_for(lambda: len(self.calls()) == 1))
        open(self.hold, "w").close()                             # sudo rename-user, typed in a remote shell
        time.sleep(0.8)
        self.assertEqual(self.terminated(), [])                  # rename-user can finish and reboot
        self.assertEqual(len(self.calls()), 1)
        self.stop()

    def test_the_wizard_session_stops_the_client_until_it_is_done(self):
        self.link()
        self.start()
        self.assertTrue(wait_for(lambda: len(self.calls()) == 1))
        self.wizard()                                            # logged out: lightdm runs the wizard session
        self.assertTrue(wait_for(lambda: self.terminated() == ["t"]))
        self.assertIn("first-boot wizard is renaming", self.output())
        time.sleep(0.5)
        self.assertEqual(len(self.calls()), 1)
        self.wizard(running=False)                               # the wizard finished
        self.assertTrue(wait_for(lambda: len(self.calls()) == 2))
        self.stop()

    def test_a_revoked_link_that_cannot_be_set_aside_is_not_restarted_in_a_loop(self):
        os.makedirs(os.path.join(self.config, "agent.json.revoked", "x"))   # os.replace onto it fails
        self.link()
        self.start("2 2 2 2 2 2 2 2 2 2")
        time.sleep(1.0)
        self.assertLessEqual(len(self.calls()), 4)              # 0.2, 0.4, 0.8 s pauses, not a tight loop
        self.assertIn("could not set the old link aside", self.output())
        self.assertIn("starting it again in", self.output())
        self.stop()

    def test_a_revoke_after_a_signout_is_not_called_a_new_link(self):
        self.link()
        self.start("signout2")
        self.assertTrue(wait_for(lambda: "not linked yet" in self.output()))
        self.assertNotIn("a new one is in place", self.output())
        self.assertFalse(os.path.exists(os.path.join(self.config, "agent.json.revoked")))
        self.stop()

    def test_a_client_that_ignores_sigterm_is_killed(self):
        self.link()
        self.start("stubborn")
        self.assertTrue(wait_for(lambda: len(self.calls()) == 1))
        pid = self.calls()[0]["pid"]
        started = time.monotonic()
        self.stop()                                              # GRACE is 1 s in these tests
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(wait_for(lambda: not self.alive(pid), timeout=3))
        self.assertIn("did not stop within", self.output())

    def test_a_client_that_cannot_start_is_retried(self):
        self.link()
        self.start(ACU_CONNECT_CLIENT=os.path.join(self.tmp, "missing"))
        self.assertTrue(wait_for(lambda: self.output().count("cannot start the client") >= 2))
        self.assertIn("starting it again in", self.output())
        self.assertIsNone(self.proc.poll())
        self.stop()

    def test_the_client_gets_no_systemd_or_supervisor_variables(self):
        self.link()
        self.start(INVOCATION_ID="abc", JOURNAL_STREAM="1:2", PYTHONUNBUFFERED="1", ACU_SERVER="https://s.test")
        self.assertTrue(wait_for(lambda: len(self.calls()) == 1))
        env = self.calls()[0]["env"]
        for name in ("INVOCATION_ID", "JOURNAL_STREAM", "PYTHONUNBUFFERED", "ACU_CONNECT_POLL", "ACU_CONNECT_CLIENT"):
            self.assertNotIn(name, env)
        self.assertIn("ACU_SERVER", env)                         # the client's own settings pass through
        self.stop()

    def test_either_rename_marker_holds_the_client(self):
        enabled = os.path.join(self.tmp, "userconfig.service")   # rename-user enabled userconfig
        open(enabled, "w").close()
        self.link()
        self.start(ACU_CONNECT_HOLD=f"{self.hold}:{enabled}")
        time.sleep(0.6)
        self.assertEqual(self.calls(), [])
        self.assertIn(f"({enabled} exists)", self.output())
        os.remove(enabled)
        self.assertTrue(wait_for(lambda: len(self.calls()) == 1))
        self.stop()

    def test_after_cancel_rename_the_wizard_still_on_screen_holds_until_a_reboot(self):
        self.wizard()                                            # cancel-rename does not end its session
        self.link()
        self.start()
        time.sleep(0.6)
        self.assertEqual(self.calls(), [])
        self.assertIn("reboot (or restart lightdm) to end it", self.output())
        self.wizard(running=False)                               # rebooted
        self.assertTrue(wait_for(lambda: len(self.calls()) == 1))
        self.stop()

    @unittest.skipUnless(os.geteuid() == 0, ROOT_ONLY)
    def test_as_root_a_directory_problem_is_logged_once(self):
        missing = os.path.join(self.tmp, "no-such-parent", "state")
        self.start(ACU_CONFIG_DIR=missing)
        time.sleep(0.8)                                          # several POLLs
        self.assertEqual(self.output().count(f"cannot create {missing}"), 1)
        self.stop()

    @unittest.skipUnless(os.geteuid() == 0, ROOT_ONLY)
    def test_as_root_the_client_runs_as_the_desktop_user(self):
        if not reachable_by_others(sys.executable):
            self.skipTest(NOT_REACHABLE)
        nobody = pwd.getpwnam("nobody")
        os.chmod(self.tmp, 0o777)
        os.chmod(self.stub, 0o755)
        for name in ("calls.jsonl", "plan"):
            open(os.path.join(self.tmp, name), "w").close()
            os.chmod(os.path.join(self.tmp, name), 0o666)
        os.rmdir(self.config)                                    # the supervisor creates it for the user
        self.start(ACU_CONNECT_UID=str(nobody.pw_uid))
        # created, then handed to the user (two steps: wait for the second)
        self.assertTrue(wait_for(lambda: os.path.isdir(self.config) and os.stat(self.config).st_uid == nobody.pw_uid))
        self.assertEqual(oct(os.stat(self.config).st_mode & 0o777), "0o700")
        self.link()
        os.chmod(os.path.join(self.config, "agent.json"), 0o644)
        self.assertTrue(wait_for(lambda: len(self.calls()) == 1))
        call = self.calls()[0]
        self.assertEqual(call["uid"], nobody.pw_uid)
        self.assertEqual(call["home"], nobody.pw_dir)
        self.stop()

    @unittest.skipUnless(os.geteuid() == 0, ROOT_ONLY)
    def test_as_root_an_existing_directory_it_did_not_create_is_left_alone(self):
        nobody = pwd.getpwnam("nobody")
        os.chmod(self.config, 0o755)                             # say ACU_CONFIG_DIR=/opt/acu-connect
        self.start(ACU_CONNECT_UID=str(nobody.pw_uid))
        time.sleep(0.5)
        st = os.stat(self.config)
        self.assertEqual((st.st_uid, oct(st.st_mode & 0o777)), (0, "0o755"))
        self.stop()


class Command(unittest.TestCase):
    """The acu-connect command (a sh script), with its paths pointed at stand-ins."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="acu-command-test.", dir="/tmp" if os.path.isdir("/tmp") else None)
        self.defaults = os.path.join(self.tmp, "default")
        self.pending = os.path.join(self.tmp, "rename_user.conf")
        self.enabled = os.path.join(self.tmp, "userconfig.service")
        python = os.path.join(self.tmp, "python3")
        with open(python, "w") as fh:            # records what the client would get
            fh.write(f'#!/bin/sh\nenv > "{self.tmp}/env"\nprintf "%s\\n" "$@" > "{self.tmp}/args"\n')
        runuser = os.path.join(self.tmp, "runuser")
        with open(runuser, "w") as fh:           # as root: runuser -u NAME -- COMMAND...
            fh.write(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{self.tmp}/runuser.args"\n'
                     'while [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n')
        os.chmod(python, 0o755)
        os.chmod(runuser, 0o755)
        self.paths = {"PYTHON": python, "DEFAULTS": self.defaults, "RENAME_PENDING": f'"{self.pending} {self.enabled}"',
                      "RUNUSER": runuser}
        self.command = os.path.join(self.tmp, "acu-connect")
        self.build(os.getuid())

    def build(self, desktop_uid):
        with open(WRAPPER) as fh:
            text = fh.read()
        for name, value in (*self.paths.items(), ("DESKTOP_UID", str(desktop_uid))):
            text, n = re.subn(rf"^{name}=.*$", lambda _m: f"{name}={value}", text, flags=re.M)
            self.assertEqual(n, 1, name)
        with open(self.command, "w") as fh:
            fh.write(text)
        os.chmod(self.command, 0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_command(self, *args, defaults=None, **env):
        if defaults is not None:
            with open(self.defaults, "w", newline="") as fh:
                fh.write(defaults)
        return subprocess.run(["sh", self.command, *args], capture_output=True, text=True, timeout=30,
                              env={"PATH": "/usr/bin:/bin", "ACU_CONFIG_DIR": self.tmp, **env})

    def client_env(self):
        with open(os.path.join(self.tmp, "env")) as fh:
            return dict(line.rstrip("\n").split("=", 1) for line in fh if "=" in line)

    def client_args(self):
        with open(os.path.join(self.tmp, "args")) as fh:
            return fh.read().split("\n")[:-1]

    def test_settings_are_read_as_systemd_reads_them_not_run(self):
        result = self.run_command("status", defaults=(
            "# a comment\n"
            "#ACU_SERVER=https://commented.example\n"
            "ACU_A=https://a.example  \n"              # trailing blanks
            "ACU_B=https://b.example\r\n"             # CRLF
            "ACU_C = https://c.example\n"              # blanks around =
            "   ACU_D=https://d.example\n"             # indented
            "ACU_E=\"https://e.example\" \n"           # quoted, then a blank
            "ACU_F='grim -t jpeg -'\n"
            "ACU_G=grim -t jpeg -\n"                   # inner blanks kept, as systemd does
            "HTTPS_PROXY=http://proxy.example:3128\n"  # the command needs the proxy too
            "PATH=/evil\n"
            "ACU_BAD-NAME=x\n"
            "touch /tmp/acu-should-not-exist\n"
            "ACU_LAST=no-newline"))
        self.assertEqual(result.returncode, 0, result.stderr)
        env = self.client_env()
        for name, want in (("ACU_A", "https://a.example"), ("ACU_B", "https://b.example"),
                           ("ACU_C", "https://c.example"), ("ACU_D", "https://d.example"),
                           ("ACU_E", "https://e.example"), ("ACU_F", "grim -t jpeg -"),
                           ("ACU_G", "grim -t jpeg -"), ("HTTPS_PROXY", "http://proxy.example:3128"),
                           ("ACU_LAST", "no-newline")):
            self.assertEqual(env.get(name), want, name)
        self.assertNotIn("ACU_SERVER", env)
        self.assertNotIn("ACU_BAD-NAME", env)
        self.assertNotIn("/evil", env["PATH"])
        self.assertEqual(self.client_args(), ["-I", "-m", "acu_connect", "status"])

    def test_run_is_refused_the_service_runs_the_client(self):
        result = self.run_command("run")
        self.assertEqual(result.returncode, 1)
        self.assertIn("already runs as acu-connect.service", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "args")))
        self.assertEqual(self.run_command("run", ACU_CONNECT_MANUAL="1").returncode, 0)   # for debugging

    def test_no_signin_while_a_rename_is_pending(self):
        open(self.pending, "w").close()
        for args in (["signin"], ["signin", "--force"], ["--name", "box", "signin"]):
            result = self.run_command(*args)
            self.assertEqual(result.returncode, 1, args)
            self.assertIn("rename of the desktop user is pending", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "args")))
        self.assertEqual(self.run_command("status").returncode, 0)        # looking is fine
        self.assertEqual(self.run_command("signin", "--name", "run").returncode, 1)

    def test_no_signin_while_userconfig_is_enabled_for_a_rename(self):
        open(self.enabled, "w").close()                          # rename-user without openssh-server
        result = self.run_command("signin")
        self.assertEqual(result.returncode, 1)
        self.assertIn("then reboot", result.stderr)

    @unittest.skipUnless(os.geteuid() == 0, ROOT_ONLY)
    def test_as_root_the_command_runs_as_the_desktop_user(self):
        nobody = pwd.getpwnam("nobody")
        self.build(nobody.pw_uid)
        self.assertEqual(self.run_command("status").returncode, 0)
        with open(os.path.join(self.tmp, "runuser.args")) as fh:
            self.assertEqual(fh.read().split("\n")[:3], ["-u", "nobody", "--"])
        self.assertEqual(self.client_args()[-1], "status")

    @unittest.skipUnless(os.geteuid() == 0, ROOT_ONLY)
    def test_as_root_without_a_desktop_user_the_link_can_be_seen_and_removed_not_made(self):
        uid = 4242424
        while True:
            try:
                pwd.getpwuid(uid)
                uid += 1
            except KeyError:
                break
        self.build(uid)
        result = self.run_command("signin")
        self.assertEqual(result.returncode, 1)
        self.assertIn("there is no desktop user", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "args")))
        for command in ("status", "signout"):
            self.assertEqual(self.run_command(command).returncode, 0, command)
            self.assertEqual(self.client_args()[-1], command)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "runuser.args")))   # ran as root

    def test_an_option_value_is_not_taken_for_the_command(self):
        self.assertEqual(self.run_command("--name", "run", "status").returncode, 0)
        self.assertEqual(self.client_args()[-3:], ["--name", "run", "status"])


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
