import asyncio
import json
import os
import shutil
import struct
import tempfile
import unittest
from unittest import mock

from acu_connect import DEFAULT_SERVER, config
from acu_connect.__main__ import parse_args, server_url
from acu_connect.recorder import Recorder
from acu_connect.relay import Flow, RelayChannel
from acu_connect.transport import MAX_BATCH_CHARS, Outbox
from acu_connect.screen import Input, Screen, Shared, Wlroots, Xdotool, Ydotool, is_printable, jpeg_size, pick_virtual_output
from acu_connect.session import parse_candidate, to_ice_servers
from acu_connect.shell import Shell
from acu_connect.sysinfo import collect_info, collect_metrics, key_values, parse_network


def jpeg(width, height):
    sof = b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", height, width) + b"\x03" + b"\x00" * 9
    return b"\xff\xd8\xff\xe0\x00\x04\x00\x00" + sof + b"\x00" * 16


class FakeChannel:
    readyState = "open"
    bufferedAmount = 0
    bufferedAmountLowThreshold = 0

    def __init__(self):
        self.sent = []
        self.handlers = {}

    def on(self, name, handler):
        self.handlers[name] = handler

    def send(self, data):
        self.sent.append(data)

    def texts(self):
        return [json.loads(s) for s in self.sent if isinstance(s, str)]


class FakeCapture:
    name = "fake"
    scale = 1

    def __init__(self, frames):
        self.frames = frames
        self.count = 0

    async def capture(self):
        self.count += 1
        frame = self.frames[min(self.count - 1, len(self.frames) - 1)]
        if isinstance(frame, Exception):
            raise frame
        return frame


class Pure(unittest.TestCase):
    def test_default_server_and_priority(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(server_url({}, None), DEFAULT_SERVER)
            self.assertEqual(DEFAULT_SERVER, "https://connect.acutrotech.com")
            self.assertEqual(server_url({}, {"server": "https://a.example/"}), "https://a.example")
            self.assertEqual(server_url({"server": "https://b.example"}, {"server": "https://a.example"}), "https://b.example")

    def test_parse_args(self):
        self.assertEqual(parse_args(["signin", "--name", "pi", "--force"]), {"_": ["signin"], "name": "pi", "force": True})

    def test_jpeg_size(self):
        self.assertEqual(jpeg_size(jpeg(1920, 1080)), (1920, 1080))
        self.assertIsNone(jpeg_size(b"not a jpeg, not at all"))

    def test_virtual_output_only_for_one_headless_display(self):
        mode = [{"width": 1920, "height": 1080, "current": True}]
        self.assertEqual(pick_virtual_output([{"name": "NOOP-1", "enabled": True, "modes": mode}]), {"name": "NOOP-1", "width": 1920, "height": 1080})
        self.assertIsNone(pick_virtual_output([{"name": "HDMI-A-1", "enabled": True, "modes": mode}]))
        self.assertIsNone(pick_virtual_output([{"name": "NOOP-1", "enabled": True, "modes": mode}, {"name": "NOOP-2", "enabled": True, "modes": mode}]))

    def test_input_commands(self):
        self.assertEqual(Wlroots().button(0, False), [])
        self.assertEqual(Wlroots().button(2, True), [["wlrctl", "pointer", "click", "right"]])
        self.assertEqual(Wlroots().type("-x"), [["wtype", "-k", "minus", "x"]])
        self.assertEqual(Wlroots().key({"key": "c", "ctrl": True}), [["wtype", "-M", "ctrl", "-k", "c", "-m", "ctrl"]])
        self.assertEqual(Xdotool().key({"key": "Enter", "ctrl": True}), [["xdotool", "key", "ctrl+Return"]])
        self.assertEqual(Ydotool().button(0, True), [["ydotool", "click", "0x40"]])
        self.assertTrue(is_printable({"key": "a"}))
        self.assertFalse(is_printable({"key": "a", "ctrl": True}))
        self.assertFalse(is_printable({"key": "Enter"}))

    def test_key_values_first_wins(self):
        self.assertEqual(key_values("a : 1\na : 2\nnope\nb: x:y"), {"a": "1", "b": "x:y"})

    def test_network_leaves_out_loopback_and_link_local(self):
        data = json.dumps([
            {"ifname": "lo", "address": "00:00:00:00:00:00", "addr_info": [{"local": "127.0.0.1", "scope": "host"}]},
            {"ifname": "eth0", "address": "d8:3a:dd:00:00:01", "addr_info": [{"local": "192.168.1.2", "scope": "global"}, {"local": "fe80::1", "scope": "link"}]},
            {"ifname": "tun0", "address": "aa:aa:aa:aa:aa:aa", "addr_info": [{"local": "fe80::2", "scope": "link"}]},
        ])
        self.assertEqual(parse_network(data), [{"name": "eth0", "mac": "d8:3a:dd:00:00:01", "addresses": ["192.168.1.2"]}])

    @unittest.skipUnless(os.path.exists("/proc/meminfo"), "needs Linux")
    def test_system_reports(self):
        info = collect_info()
        self.assertTrue(info["os"] and info["kernel"] and info["kernelArch"] and info["cpuCores"] > 0 and info["memoryTotal"] > 0)
        metrics = collect_metrics()
        self.assertGreater(metrics["uptimeSec"], 0)
        self.assertGreater(metrics["memTotal"], 0)

    def test_ice_servers(self):
        servers = to_ice_servers([{"urls": ["stun:a:3478", "ftp:nope"]}, {"urls": "turn:b:3478?transport=udp", "username": "u", "credential": "c"}, {"urls": []}])
        self.assertEqual([s.urls for s in servers], [["stun:a:3478"], ["turn:b:3478?transport=udp"]])
        self.assertEqual(servers[1].username, "u")
        self.assertEqual(to_ice_servers(None), [])

    def test_browser_candidate(self):
        line = "candidate:842163049 1 udp 1677729535 203.0.113.5 46154 typ srflx raddr 0.0.0.0 rport 0 generation 0 ufrag abcd network-cost 999"
        for text in (line, f"a={line}"):
            c = parse_candidate(text, "0")
            self.assertEqual((c.ip, c.port, c.type, c.sdpMid), ("203.0.113.5", 46154, "srflx", "0"))

    def test_config_is_private(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"ACU_CONFIG_DIR": d}):
            self.assertIsNone(config.load_config())
            config.save_config({"server": "x", "deviceToken": "secret"})
            self.assertEqual(config.load_config()["deviceToken"], "secret")
            self.assertEqual(os.stat(config.config_file()).st_mode & 0o777, 0o600)
            with mock.patch.object(config.os, "fsync", wraps=os.fsync) as synced:
                config.save_config({"server": "x", "deviceToken": "secret2"})
            self.assertEqual(synced.call_count, 2, "the file and its folder reach the disk")
            self.assertFalse(config.config_file().with_suffix(".json.tmp").exists())
            config.delete_config()
            self.assertIsNone(config.load_config())


class Async(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Shared.original_mode = None
        Shared.woken_outputs = set()
        Shared.active_screens = 0

    async def test_recorder_batches_and_splits(self):
        batches = []
        r = Recorder(batches.append)
        r.add("o", "x" * 20000)
        self.assertEqual(len(batches), 1, "a big output is sent straight away")
        self.assertEqual([len(e[2]) for e in batches[0]], [8192, 8192, 3616])
        r.add("i", "ls")
        self.assertEqual(len(batches), 1)
        r.flush()
        self.assertEqual(batches[1][0][1:], ["i", "ls"])

    async def test_input_coalesces_moves_and_runs_in_order(self):
        done = []

        async def execute(argv):
            done.append(argv)

        i = Input("wlroots", Wlroots(), execute)
        i.handle({"t": "move", "x": 0.5, "y": 0.5})  # no screen yet: ignored
        i.set_screen(101, 101)
        i.handle({"t": "move", "x": 0.1, "y": 0.1})
        i.handle({"t": "move", "x": 0.5, "y": 0.5})
        i.handle({"t": "key", "key": "a", "down": True})
        i.handle({"t": "key", "key": "a", "down": False})
        i.handle({"t": "key", "key": "Shift", "down": True})
        await asyncio.sleep(0.05)
        i.close()
        self.assertEqual(done, [["wlrctl", "pointer", "move", "-100000", "-100000"], ["wlrctl", "pointer", "move", "50", "50"], ["wtype", "a"]])

    async def test_screen_sends_info_then_frames_and_skips_duplicates(self):
        dc, input_dc = FakeChannel(), FakeChannel()
        frame = jpeg(800, 600)
        s = Screen(dc, input_dc, lambda m: None, control=False, env={"ACU_SCREEN_FPS": "30"}, capture=FakeCapture([frame]), power=None, mode=None)
        await asyncio.sleep(0.15)
        s.close()
        texts = dc.texts()
        self.assertEqual(texts[0]["t"], "info")
        self.assertEqual((texts[0]["width"], texts[0]["height"], texts[0]["viewOnly"], texts[0]["input"]), (800, 600, True, False))
        self.assertEqual(texts[1:], [{"t": "frame", "size": len(frame)}], "the same frame is sent once")
        self.assertEqual(b"".join(x for x in dc.sent if isinstance(x, bytes)), frame)

    async def test_viewers_cannot_control_or_resize(self):
        calls = []

        class Mode:
            async def virtual_output(self):
                return {"name": "NOOP-1", "width": 1920, "height": 1080}

            async def set(self, *a):
                calls.append(a)

        dc, input_dc = FakeChannel(), FakeChannel()
        s = Screen(dc, input_dc, lambda m: None, control=False, env={}, capture=FakeCapture([jpeg(800, 600)]), power=None, mode=Mode())
        await asyncio.sleep(0.02)
        input_dc.handlers["message"](json.dumps({"t": "viewport", "w": 1000, "h": 700}))
        await asyncio.sleep(0.02)
        s.close()
        await asyncio.sleep(0.02)
        self.assertEqual(calls, [])
        self.assertIsNone(s.input)

    async def test_control_session_resizes_and_last_one_restores(self):
        calls = []

        class Mode:
            async def virtual_output(self):
                return {"name": "NOOP-1", "width": 1920, "height": 1080}

            async def set(self, *a):
                calls.append(a)

        dc, input_dc = FakeChannel(), FakeChannel()
        s = Screen(dc, input_dc, lambda m: None, control=True, env={"ACU_DISABLE_INPUT": "1", "ACU_SCREEN_FPS": "30"}, capture=FakeCapture([jpeg(800, 600)]), power=None, mode=Mode())
        await asyncio.sleep(0.05)
        self.assertTrue(dc.texts()[0]["resizable"])
        input_dc.handlers["message"](json.dumps({"t": "viewport", "w": 1513, "h": 945}))
        await asyncio.sleep(0.05)
        self.assertEqual(calls, [("NOOP-1", 1514, 946)], "sizes round to even numbers")
        s.close()
        await asyncio.sleep(0.05)
        self.assertEqual(calls[-1], ("NOOP-1", 1920, 1080))

    async def test_wakes_an_idle_display_and_puts_it_back(self):
        events = []

        class Power:
            state = ["off"]

            async def off_outputs(self):
                return ["NOOP-1"] if self.state[0] == "off" else []

            async def on(self, names):
                events.append(("on", names))
                self.state[0] = "on"

            async def off(self, names):
                events.append(("off", names))

        dc = FakeChannel()
        capture = FakeCapture([jpeg(800, 600)])
        s = Screen(dc, FakeChannel(), lambda m: None, env={"ACU_SCREEN_FPS": "30"}, capture=capture, power=Power(), mode=None)
        await asyncio.sleep(0.05)
        s.close()
        await asyncio.sleep(0.05)
        self.assertEqual(events, [("on", ["NOOP-1"]), ("off", ["NOOP-1"])])

    async def test_capture_failure_reports_an_error(self):
        dc = FakeChannel()
        s = Screen(dc, FakeChannel(), lambda m: None, env={}, capture=FakeCapture([RuntimeError("grim failed\ndetails")]), power=None, mode=None)
        await asyncio.sleep(1.3)
        s.close()
        self.assertEqual(dc.texts()[-1], {"t": "error", "message": "Screen capture failed: grim failed"})

    @unittest.skipUnless(shutil.which("sh"), "needs a shell")
    async def test_shell_runs_commands_records_and_reports_exit(self):
        dc = FakeChannel()
        recorded, exits = [], []
        with mock.patch.dict(os.environ, {"ACU_SHELL": "/bin/sh"}):
            shell = Shell(dc, exits.append, lambda m: None, lambda kind, data: recorded.append((kind, data)))
        send = dc.handlers["message"]
        send(json.dumps({"t": "r", "c": 100, "r": 30}))
        send(json.dumps({"t": "i", "d": "stty size; echo pty-$((40+2))-é\n"}))
        for _ in range(100):
            await asyncio.sleep(0.05)
            output = "".join(m["d"] for m in dc.texts() if m["t"] == "o")
            if "pty-42-é" in output and "30 100" in output:
                break
        self.assertIn("pty-42-é", output)
        self.assertIn("30 100", output, "the window size reaches the shell")
        send(json.dumps({"t": "i", "d": "exit 7\n"}))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if exits:
                break
        self.assertEqual(exits, [7])
        self.assertEqual(dc.texts()[-1], {"t": "x", "code": 7})
        self.assertIn(("r", "100x30"), recorded)
        self.assertTrue(any(k == "i" for k, _ in recorded))
        shell.close()

    @unittest.skipUnless(shutil.which("sh"), "needs a shell")
    async def test_closing_ends_the_shell_and_a_big_paste_does_not_block(self):
        dc = FakeChannel()
        with mock.patch.dict(os.environ, {"ACU_SHELL": "/bin/sh"}):
            shell = Shell(dc, lambda code: None, lambda m: None)
        dc.handlers["message"](json.dumps({"t": "i", "d": "cat > /dev/null\n" + "x" * 200000}))
        await asyncio.sleep(0.1)
        shell.close()
        for _ in range(80):
            await asyncio.sleep(0.05)
            if shell.proc.poll() is not None:
                break
        self.assertIsNotNone(shell.proc.poll())


class Noise(unittest.TestCase):
    def test_only_the_closed_socket_retry_is_silenced(self):
        from acu_connect.__main__ import quiet_closed_socket_retries

        loop = mock.Mock()

        class Handle:
            def __repr__(self):
                return "<TimerHandle when=1 Transaction.__retry()>"

        quiet_closed_socket_retries(loop, {"exception": AttributeError("x"), "handle": Handle()})
        loop.default_exception_handler.assert_not_called()
        quiet_closed_socket_retries(loop, {"exception": ValueError("real problem"), "handle": Handle()})
        quiet_closed_socket_retries(loop, {"exception": AttributeError("elsewhere"), "handle": "<Handle other()>"})
        self.assertEqual(loop.default_exception_handler.call_count, 2)


class Relay(unittest.IsolatedAsyncioTestCase):
    async def test_channel_sends_text_and_binary_and_counts_what_is_in_flight(self):
        sent, flow = [], Flow()
        ch = RelayChannel("screen", "s1", sent.append, flow)
        ch.send("dropped: not open yet")
        opened = []
        ch.on("open", lambda: opened.append(True))
        ch.open()
        ch.send('{"t":"frame","size":3}')
        ch.send(b"\x00\xff\x10")
        self.assertEqual(opened, [True])
        self.assertEqual(sent[0], {"type": "relay", "sessionId": "s1", "label": "screen", "n": 22, "text": '{"t":"frame","size":3}'})
        self.assertEqual(sent[1], {"type": "relay", "sessionId": "s1", "label": "screen", "n": 3, "bin": "AP8Q"})
        self.assertEqual(ch.bufferedAmount, 25)

    async def test_acks_free_the_channel_and_wake_a_paused_sender(self):
        flow = Flow()
        ch = RelayChannel("shell", "s1", lambda m: None, flow)
        ch.bufferedAmountLowThreshold = 10
        woken = []
        ch.on("bufferedamountlow", lambda: woken.append(True))
        ch.open()
        ch.send("x" * 30)
        flow.ack(15)
        self.assertEqual((ch.bufferedAmount, woken), (15, []))
        flow.ack(15)
        self.assertEqual((ch.bufferedAmount, woken), (0, [True]))
        flow.ack("junk")
        flow.ack(-50)
        self.assertEqual(ch.bufferedAmount, 0)

    async def test_incoming_text_reaches_the_handler_and_close_is_reported_once(self):
        ch = RelayChannel("input", "s1", lambda m: None, Flow())
        got, closed = [], []
        ch.on("message", got.append)
        ch.on("close", lambda: closed.append(True))
        ch.deliver({"text": "before open"})
        ch.open()
        ch.deliver({"text": '{"t":"move"}'})
        ch.deliver({"bin": "AAAA"})
        ch.close()
        ch.close()
        ch.deliver({"text": "after close"})
        self.assertEqual((got, closed), (['{"t":"move"}'], [True]))

    async def test_session_switches_to_the_relay_and_runs_the_desktop_over_it(self):
        from acu_connect import session as session_module

        sent, closed = [], []
        with mock.patch.object(session_module, "Screen") as screen:
            s = session_module.Session("abc12345", "screen", [], {"email": "a@example.com"}, True, None, lambda d: None, closed.append, lambda m: None, sent.append)
            s.relay_message({"type": "relay", "label": "input", "text": "{}"})  # ignored: not relaying yet
            s.relay_message({"type": "session.relay"})
            s.relay_message({"type": "session.relay"})  # a repeat changes nothing
            await asyncio.sleep(0.05)
            self.assertTrue(s.relayed)
            self.assertEqual(sent[0], {"type": "relay.ready", "sessionId": "abc12345"})
            self.assertEqual(screen.call_count, 1, "the desktop starts once, over the relay")
            screen_dc, input_dc = screen.call_args.args[:2]
            self.assertIsInstance(screen_dc, RelayChannel)
            self.assertEqual((screen_dc.label, input_dc.label, screen_dc.readyState), ("screen", "input", "open"))
            s.state_changed()  # the closed WebRTC connection doesn't end a relayed session
            self.assertEqual(closed, [])
            s.close("ended by user")
            self.assertEqual(closed, ["ended by user"])
            self.assertEqual(screen_dc.readyState, "closed")

    async def test_a_session_already_running_directly_is_not_switched(self):
        from acu_connect import session as session_module

        sent = []
        s = session_module.Session("abc12345", "shell", [], None, False, None, lambda d: None, lambda r: None, lambda m: None, sent.append)
        s.handlers.append(mock.Mock())
        s.relay_message({"type": "session.relay"})
        self.assertFalse(s.relayed)
        self.assertEqual(sent, [])
        s.close("done")

    async def test_long_poll_batches_stay_under_the_server_limit(self):
        batches = []

        async def deliver(batch):
            batches.append(batch)

        out = Outbox(deliver)
        chunk = "x" * (100 * 1024)
        for _ in range(15):
            out.send(chunk)
        await asyncio.sleep(0.05)
        self.assertEqual(sum(len(b) for b in batches), 15)
        self.assertTrue(all(sum(map(len, b)) <= MAX_BATCH_CHARS for b in batches))
        self.assertGreater(len(batches), 2)


if __name__ == "__main__":
    unittest.main()
