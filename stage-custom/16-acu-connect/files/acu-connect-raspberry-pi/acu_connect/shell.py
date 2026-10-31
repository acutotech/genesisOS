import asyncio
import codecs
import fcntl
import json
import os
import pty
import shutil
import signal
import struct
import subprocess
import termios
from pathlib import Path

CHUNK_CHARS = 16 * 1024
PAUSE_ABOVE = 1024 * 1024
RESUME_BELOW = 256 * 1024
EXIT_CHECK_SECONDS = 0.25
KILL_AFTER_SECONDS = 2


def default_shell(env=os.environ):
    return env.get("ACU_SHELL") or env.get("SHELL") or ("/bin/bash" if shutil.which("bash") else "/bin/sh")


class Shell:
    """Bridges a WebRTC data channel to a login shell running in a PTY.
    Channel protocol (JSON text messages):
      browser -> client  {t:'i', d:'<keystrokes>'}   {t:'r', c:<cols>, r:<rows>}
      client -> browser  {t:'o', d:'<output>'}       {t:'x', code:<exit code>}"""

    def __init__(self, dc, on_exit, log, record=None):
        self.dc = dc
        self.on_exit = on_exit
        self.log = log
        self.record = record or (lambda kind, data: None)
        self.loop = asyncio.get_running_loop()
        self.closed = False
        self.paused = False
        self.pending = b""
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")

        shell = default_shell()
        self.master, slave = pty.openpty()
        self.set_size(24, 80)
        env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor", "ACU_CONNECT_SESSION": "1"}
        self.proc = subprocess.Popen(
            [shell, "-l"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=str(Path.home()),
            env=env,
            start_new_session=True,
            preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0),
        )
        os.close(slave)
        os.set_blocking(self.master, False)
        log(f"shell started: {shell} (pid {self.proc.pid})")

        self.loop.add_reader(self.master, self.readable)
        dc.bufferedAmountLowThreshold = RESUME_BELOW
        dc.on("bufferedamountlow", self.resume)
        dc.on("message", self.message)
        self.watcher = asyncio.create_task(self.watch_exit())

    def set_size(self, rows, cols):
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def send(self, msg):
        if not self.closed and self.dc.readyState == "open":
            self.dc.send(json.dumps(msg))

    def resume(self):
        if self.paused and not self.closed:
            self.paused = False
            self.loop.add_reader(self.master, self.readable)

    def readable(self):
        try:
            raw = os.read(self.master, 64 * 1024)
        except BlockingIOError:
            return
        except OSError:
            raw = b""  # EIO: the shell and everything it started have gone
        if not raw:
            self.loop.remove_reader(self.master)
            return
        data = self.decoder.decode(raw)
        if not data:
            return
        self.record("o", data)
        for i in range(0, len(data), CHUNK_CHARS):
            self.send({"t": "o", "d": data[i : i + CHUNK_CHARS]})
        # Back-pressure: stop reading from the PTY while the channel is congested.
        if not self.paused and self.dc.readyState == "open" and self.dc.bufferedAmount > PAUSE_ABOVE:
            self.paused = True
            self.loop.remove_reader(self.master)

    def message(self, raw):
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(msg, dict):
            return
        if msg.get("t") == "i" and isinstance(msg.get("d"), str):
            self.record("i", msg["d"])
            self.write(msg["d"].encode("utf-8", "replace"))
        elif msg.get("t") == "r":
            try:
                cols = max(2, min(500, int(msg.get("c") or 0)))
                rows = max(2, min(300, int(msg.get("r") or 0)))
                self.record("r", f"{cols}x{rows}")
                self.set_size(rows, cols)
            except (TypeError, ValueError, OSError):
                pass

    def write(self, data):
        """A paste can be bigger than the PTY accepts at once; the rest waits, without blocking."""
        self.pending += data
        self.flush_writes()

    def flush_writes(self):
        if self.closed:
            return
        try:
            written = os.write(self.master, self.pending) if self.pending else 0
            self.pending = self.pending[written:]
        except BlockingIOError:
            pass
        except OSError:
            self.pending = b""
        if self.pending:
            self.loop.add_writer(self.master, self.flush_writes)
        else:
            self.loop.remove_writer(self.master)

    async def watch_exit(self):
        while self.proc.poll() is None:
            await asyncio.sleep(EXIT_CHECK_SECONDS)
        if self.closed:
            return
        self.readable()  # the last output
        code = self.proc.returncode
        self.log(f"shell exited with code {code}")
        self.send({"t": "x", "code": code})
        self.finish()
        self.on_exit(code)

    def finish(self):
        self.closed = True
        self.loop.remove_reader(self.master)
        self.loop.remove_writer(self.master)
        try:
            os.close(self.master)
        except OSError:
            pass

    def close(self):
        if self.closed:
            return
        self.finish()
        self.signal(signal.SIGHUP)
        self.loop.call_later(KILL_AFTER_SECONDS, self.signal, signal.SIGKILL)

    def signal(self, sig):
        # The whole session, so programs started from the shell end with it.
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, sig)
            except OSError:
                pass
