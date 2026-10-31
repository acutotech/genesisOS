import asyncio
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import time

CHUNK_BYTES = 60 * 1024
MAX_BUFFERED = 512 * 1024


def has(cmd):
    return shutil.which(cmd) is not None


async def run(*argv, timeout=5):
    proc = await asyncio.create_subprocess_exec(*argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        raise RuntimeError(f"{argv[0]} exited with {proc.returncode}: {err.decode(errors='replace').strip()}")
    return out


def jpeg_size(buf):
    """Reads (width, height) from a JPEG's SOFn header."""
    if len(buf) < 4 or buf[0] != 0xFF or buf[1] != 0xD8:
        return None
    i = 2
    while i + 9 < len(buf):
        if buf[i] != 0xFF:
            return None
        marker = buf[i + 1]
        length = struct.unpack_from(">H", buf, i + 2)[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack_from(">HH", buf, i + 5)
            return width, height
        i += 2 + length
    return None


class DisplayPower:
    """Display power on wlroots compositors (labwc, wayfire, sway). Raspberry Pi OS
    runs `swayidle ... wlopm --off '*'`, which powers outputs off after a few idle
    minutes; screencopy then fails with "failed to copy output"."""

    async def off_outputs(self):
        out = (await run("wlopm")).decode()
        parts = (line.split() for line in out.split("\n"))
        return [p[0] for p in parts if len(p) >= 2 and p[1] == "off"]

    async def on(self, names):
        await run("wlopm", *[a for n in names for a in ("--on", n)])

    async def off(self, names):
        await run("wlopm", *[a for n in names for a in ("--off", n)])


def display_power(env=os.environ):
    return DisplayPower() if env.get("WAYLAND_DISPLAY") and has("wlopm") else None


def pick_virtual_output(outputs):
    """The one enabled output, if it is a virtual (headless) display. Never a real
    monitor: its resolution is the hardware's and shouldn't follow a browser window."""
    enabled = [o for o in outputs if o.get("enabled")]
    if len(enabled) != 1 or not re.match(r"^(NOOP|HEADLESS)-", enabled[0].get("name", "")):
        return None
    current = next((m for m in enabled[0].get("modes") or [] if m.get("current")), None)
    return {"name": enabled[0]["name"], "width": current["width"], "height": current["height"]} if current else None


class DisplayMode:
    """Virtual displays on wlroots can take any resolution, so the remote desktop can
    match the viewer's window exactly instead of being letterboxed or stretched."""

    async def virtual_output(self):
        return pick_virtual_output(json.loads(await run("wlr-randr", "--json")))

    async def set(self, name, width, height):
        await run("wlr-randr", "--output", name, "--custom-mode", f"{width}x{height}")


def display_mode(env=os.environ):
    return DisplayMode() if env.get("WAYLAND_DISPLAY") and has("wlr-randr") else None


class Capture:
    def __init__(self, name, scale, grab):
        self.name = name
        self.scale = scale
        self.capture = grab


def capture_backend(env=os.environ):
    """Picks how to grab a JPEG of the screen on this machine."""
    scale = float(env.get("ACU_SCREEN_SCALE") or 1)
    quality = int(float(env.get("ACU_SCREEN_QUALITY") or 60))
    # ffmpeg's mjpeg quantiser: 2 (best) .. 31 (worst)
    q = str(max(2, min(31, round((100 - quality) / 3))))
    scale_text = f"{scale:g}"

    if env.get("ACU_CAPTURE_CMD"):
        return Capture("custom", 1, lambda: run("sh", "-c", env["ACU_CAPTURE_CMD"]))
    if env.get("WAYLAND_DISPLAY") and has("grim"):
        # Some grim builds (e.g. Debian trixie) are compiled without JPEG support.
        try:
            grim_jpeg = subprocess.run(["grim", "-t", "jpeg", "-s", "0.05", "-"], capture_output=True, timeout=5).returncode == 0
        except subprocess.SubprocessError:
            grim_jpeg = False
        if grim_jpeg:
            return Capture("grim", scale, lambda: run("grim", "-t", "jpeg", "-q", str(quality), "-s", scale_text, "-"))
        if has("ffmpeg"):
            pipeline = f"grim -t png -l 0 -s {scale_text} - | ffmpeg -loglevel error -f png_pipe -i - -q:v {q} -f mjpeg -"
            return Capture("grim+ffmpeg", scale, lambda: run("sh", "-c", pipeline))
    if env.get("DISPLAY") and has("ffmpeg"):
        args = ("-loglevel", "error", "-f", "x11grab", "-i", env["DISPLAY"], "-frames:v", "1")
        args += ("-vf", f"scale=iw*{scale_text}:-2", "-q:v", q, "-f", "image2pipe", "-vcodec", "mjpeg", "-")
        return Capture("ffmpeg-x11grab", scale, lambda: run("ffmpeg", *args))
    return None


# Linux keycodes for ydotool (input-event-codes.h).
LINUX_KEYS = {
    "Escape": 1, "Backspace": 14, "Tab": 15, "Enter": 28, "Control": 29, "Shift": 42, "Alt": 56, " ": 57, "CapsLock": 58,
    "F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64, "F7": 65, "F8": 66, "F9": 67, "F10": 68, "F11": 87, "F12": 88,
    "Home": 102, "ArrowUp": 103, "PageUp": 104, "ArrowLeft": 105, "ArrowRight": 106, "End": 107, "ArrowDown": 108,
    "PageDown": 109, "Insert": 110, "Delete": 111, "Meta": 125,
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34, "h": 35, "i": 23, "j": 36, "k": 37, "l": 38, "m": 50,
    "n": 49, "o": 24, "p": 25, "q": 16, "r": 19, "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45, "y": 21, "z": 44,
}

# X keysym names for xdotool and wtype.
X_KEYS = {
    "Enter": "Return", "Backspace": "BackSpace", "Tab": "Tab", "Escape": "Escape", " ": "space", "Delete": "Delete",
    "ArrowLeft": "Left", "ArrowRight": "Right", "ArrowUp": "Up", "ArrowDown": "Down", "Home": "Home", "End": "End",
    "PageUp": "Prior", "PageDown": "Next", "Insert": "Insert", "CapsLock": "Caps_Lock",
}
MODIFIERS = {"Shift", "Control", "Alt", "Meta", "AltGraph", "CapsLock"}


def is_printable(ev):
    key = ev.get("key")
    return bool(key) and len(key) == 1 and not ev.get("ctrl") and not ev.get("alt") and not ev.get("meta")


def x_keysym(key):
    if key in X_KEYS:
        return X_KEYS[key]
    if re.match(r"^F\d{1,2}$", key):
        return key
    return key.lower() if len(key) == 1 else None


class Xdotool:
    def move(self, x, y):
        return [["xdotool", "mousemove", str(x), str(y)]]

    def button(self, b, down):
        return [["xdotool", "mousedown" if down else "mouseup", str({0: 1, 1: 2, 2: 3}.get(b, 1))]]

    def wheel(self, dy):
        return [["xdotool", "click", "5" if dy > 0 else "4"]]

    def type(self, text):
        return [["xdotool", "type", "--", text]]

    def key(self, ev):
        base = x_keysym(ev["key"])
        if not base:
            return []
        mods = [ev.get("ctrl") and "ctrl", ev.get("alt") and "alt", ev.get("shift") and len(ev["key"]) > 1 and "shift", ev.get("meta") and "super"]
        return [["xdotool", "key", "+".join([m for m in mods if m] + [base])]]


class Ydotool:
    def move(self, x, y):
        return [["ydotool", "mousemove", "--absolute", "-x", str(x), "-y", str(y)]]

    def button(self, b, down):
        code = {0: 0x00, 1: 0x02, 2: 0x01}.get(b, 0) | (0x40 if down else 0x80)
        return [["ydotool", "click", hex(code)]]

    def wheel(self, dy):
        return [["ydotool", "mousemove", "-w", "-x", "0", "-y", "-1" if dy > 0 else "1"]]

    def type(self, text):
        return [["ydotool", "type", "--", text]]

    def key(self, ev):
        code = LINUX_KEYS.get(ev["key"]) or LINUX_KEYS.get(str(ev["key"]).lower())
        if not code:
            return []
        mods = [LINUX_KEYS[name] for flag, name in (("ctrl", "Control"), ("alt", "Alt"), ("shift", "Shift"), ("meta", "Meta")) if ev.get(flag)]
        seq = [f"{m}:1" for m in mods] + [f"{code}:1", f"{code}:0"] + [f"{m}:0" for m in reversed(mods)]
        return [["ydotool", "key", *seq]]


class Wlroots:
    """wlroots compositors (labwc, wayfire, sway) via the virtual pointer/keyboard
    protocols. wlrctl only moves relatively, so home to the top-left corner first;
    the compositor clamps the pointer to the screen."""

    def move(self, x, y):
        return [["wlrctl", "pointer", "move", "-100000", "-100000"], ["wlrctl", "pointer", "move", str(x), str(y)]]

    def button(self, b, down):
        # wlrctl can only click (press + release), so act on press and ignore release.
        return [["wlrctl", "pointer", "click", {0: "left", 1: "middle", 2: "right"}.get(b, "left")]] if down else []

    def wheel(self, dy):
        return [["wlrctl", "pointer", "scroll", "15" if dy > 0 else "-15", "0"]]

    def type(self, text):
        if text.startswith("-"):
            return [["wtype", "-k", "minus", *([text[1:]] if len(text) > 1 else [])]]
        return [["wtype", text]]

    def key(self, ev):
        base = x_keysym(ev["key"])
        if not base:
            return []
        mods = [name for flag, name in (("ctrl", "ctrl"), ("alt", "alt"), ("shift", "shift"), ("meta", "logo")) if ev.get(flag)]
        return [["wtype", *[a for m in mods for a in ("-M", m)], "-k", base, *[a for m in mods for a in ("-m", m)]]]


class Input:
    """Turns browser input events into OS input. Events run one at a time in order;
    pointer moves are coalesced so a slow injector never builds up a backlog."""

    def __init__(self, name, commands, execute=None):
        self.name = name
        self.commands = commands
        self.execute = execute or (lambda argv: run(*argv, timeout=2))
        self.screen = None  # (width, height) in real pixels
        self.pending_move = None
        self.queue = asyncio.Queue()
        self.worker = asyncio.create_task(self.work())

    async def work(self):
        while True:
            job = await self.queue.get()
            argv_list = job() if callable(job) else job
            for argv in argv_list:
                try:
                    await self.execute(argv)
                except Exception:
                    pass  # a single failed event should not kill the session

    def set_screen(self, width, height):
        self.screen = (width, height)

    def px(self, ev):
        clamp = lambda v: max(0.0, min(1.0, float(v)))  # noqa: E731
        return round(clamp(ev.get("x", 0)) * (self.screen[0] - 1)), round(clamp(ev.get("y", 0)) * (self.screen[1] - 1))

    def take_move(self):
        x, y = self.pending_move
        self.pending_move = None
        return self.commands.move(x, y)

    def handle(self, ev):
        if not self.screen:
            return
        kind = ev.get("t")
        if kind == "move":
            first = self.pending_move is None
            self.pending_move = self.px(ev)
            if first:
                self.queue.put_nowait(self.take_move)
        elif kind == "button":
            x, y = self.px(ev)
            self.queue.put_nowait(self.commands.move(x, y) + self.commands.button(int(ev.get("button") or 0), bool(ev.get("down"))))
        elif kind == "wheel":
            self.queue.put_nowait(self.commands.wheel(float(ev.get("dy") or 0)))
        elif kind == "key" and ev.get("down") and ev.get("key") and ev["key"] not in MODIFIERS:
            self.queue.put_nowait(self.commands.type(ev["key"]) if is_printable(ev) else self.commands.key(ev))

    def close(self):
        self.worker.cancel()


def input_backend(env=os.environ):
    if env.get("ACU_DISABLE_INPUT") == "1":
        return None
    if env.get("WAYLAND_DISPLAY") and has("ydotool"):
        return Input("ydotool", Ydotool())
    if env.get("WAYLAND_DISPLAY") and has("wlrctl") and has("wtype"):
        return Input("wlroots", Wlroots())
    if env.get("DISPLAY") and has("xdotool"):
        return Input("xdotool", Xdotool())
    return None


class Shared:
    """State across concurrent screen sessions, so the last one to close puts things back."""

    original_mode = None  # the virtual display's size before anyone resized it
    woken_outputs = set()
    active_screens = 0
    cleanups = set()


class Screen:
    """Streams JPEG frames over the "screen" channel and applies events from "input".
    Screen channel protocol:
      {t:'info', width, height, input:bool, viewOnly:bool, resizable:bool, backend}   when the frame size changes
      {t:'frame', size:<bytes>} followed by binary chunks totalling <size> bytes
      {t:'error', message}"""

    def __init__(self, screen_dc, input_dc, log, control=False, env=os.environ, capture="auto", power="auto", mode="auto"):
        self.dc = screen_dc
        self.log = log
        self.control = control
        self.env = env
        self.capture = capture
        self.power = display_power(env) if power == "auto" else power
        self.mode = display_mode(env) if mode == "auto" else mode
        # View-only sessions never get an input backend, whatever the browser sends.
        self.input = input_backend(env) if control else None
        self.fps = max(1, min(30, float(env.get("ACU_SCREEN_FPS") or 5)))
        self.closed = False
        self.last_hash = None
        self.last_size = None
        self.virtual = None  # set when this session may resize the display
        self.wanted_size = None
        self.resizing = False
        self.resize_task = None
        input_dc.on("message", self.input_message)
        Shared.active_screens += 1
        self.task = asyncio.create_task(self.stream())

    def send_text(self, msg):
        if not self.closed and self.dc.readyState == "open":
            self.dc.send(json.dumps(msg))

    def input_message(self, raw):
        if not self.control:
            return
        try:
            ev = json.loads(raw)
            if ev.get("t") == "viewport":
                self.request_resize(float(ev.get("w") or 0), float(ev.get("h") or 0))
            elif self.input:
                self.input.handle(ev)
        except (TypeError, ValueError, AttributeError):
            pass  # malformed event

    def request_resize(self, w, h):
        # Only the latest requested size matters while a resize is in progress.
        even = lambda v: int(v / 2 + 0.5) * 2  # noqa: E731
        self.wanted_size = (max(640, min(3840, even(w))), max(480, min(2160, even(h))))
        if not self.resizing:
            self.resizing = True
            self.resize_task = asyncio.create_task(self.resize())

    async def resize(self):
        while self.wanted_size and not self.closed:
            width, height = self.wanted_size
            self.wanted_size = None
            virtual = self.virtual
            if not virtual or (virtual["width"], virtual["height"]) == (width, height):
                continue
            try:
                await self.mode.set(virtual["name"], width, height)
                if not Shared.original_mode:
                    Shared.original_mode = dict(virtual)
                virtual.update(width=width, height=height)
                self.log(f"display {virtual['name']} resized to {width}x{height}")
            except Exception as e:
                self.log(f"could not resize display: {e}")
        self.resizing = False

    async def wake(self):
        """Switches powered-off outputs on; True if anything was woken."""
        if not self.power:
            return False
        try:
            off = await self.power.off_outputs()
            if not off:
                return False
            await self.power.on(off)
            Shared.woken_outputs.update(off)
            self.log(f"display {', '.join(off)} was powered off (idle); switched it on for screen sharing")
            return True
        except Exception as e:
            self.log(f"could not switch the display on: {e}")
            return False

    async def stream(self):
        if self.capture == "auto":
            self.capture = await asyncio.to_thread(capture_backend, self.env)
        if not self.capture:
            self.send_text({"t": "error", "message": "No screen capture tool found on the device (install grim for Wayland or ffmpeg for X11)."})
            return
        how = self.input.name if self.input else "none (no input tool)" if self.control else "none (view only)"
        self.log(f"screen sharing via {self.capture.name}, input via {how}")
        await self.wake()
        if self.control and self.mode:
            try:
                self.virtual = await self.mode.virtual_output()
            except Exception:
                pass  # can't tell: leave the display size alone
        failures = 0
        while not self.closed:
            started = time.monotonic()
            if self.dc.readyState == "open" and self.dc.bufferedAmount < MAX_BUFFERED:
                try:
                    frame = await self.capture.capture()
                    failures = 0
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if self.closed:
                        break
                    # The idle timer can power the display off mid-session (nobody is
                    # touching it, especially in view-only sessions): wake it and retry.
                    if await self.wake():
                        continue
                    failures += 1
                    if failures < 3:
                        await asyncio.sleep(0.5)
                        continue
                    self.log(f"screen capture failed: {e}")
                    self.send_text({"t": "error", "message": f"Screen capture failed: {str(e).splitlines()[0] if str(e) else 'unknown error'}"})
                    break
                if self.closed:
                    break
                self.send_frame(frame)
            await asyncio.sleep(max(0.01, 1 / self.fps - (time.monotonic() - started)))

    def send_frame(self, frame):
        digest = hashlib.sha1(frame).digest()
        if not frame or digest == self.last_hash:
            return
        self.last_hash = digest
        size = jpeg_size(frame)
        if size and size != self.last_size:
            self.last_size = size
            if self.input:
                self.input.set_screen(round(size[0] / self.capture.scale), round(size[1] / self.capture.scale))
            self.send_text({
                "t": "info", "width": size[0], "height": size[1], "input": bool(self.input),
                "viewOnly": not self.control, "resizable": bool(self.virtual), "backend": self.capture.name,
            })
        self.send_text({"t": "frame", "size": len(frame)})
        for i in range(0, len(frame), CHUNK_BYTES):
            if self.dc.readyState != "open":
                return
            self.dc.send(frame[i : i + CHUNK_BYTES])

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.task.cancel()
        if self.input:
            self.input.close()
        Shared.active_screens -= 1
        if Shared.active_screens == 0:
            task = asyncio.create_task(self.restore())
            Shared.cleanups.add(task)
            task.add_done_callback(Shared.cleanups.discard)

    async def restore(self):
        """Nobody is watching: put the display's size back, then back to sleep if we woke it."""
        original, Shared.original_mode = Shared.original_mode, None
        if original and self.mode:
            try:
                await self.mode.set(original["name"], original["width"], original["height"])
            except Exception:
                pass
        if Shared.woken_outputs and self.power:
            names = sorted(Shared.woken_outputs)
            Shared.woken_outputs.clear()
            try:
                await self.power.off(names)
                self.log(f"display {', '.join(names)} powered off again")
            except Exception:
                pass
