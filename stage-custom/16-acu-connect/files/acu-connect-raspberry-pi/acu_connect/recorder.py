import asyncio
import time

MAX_EVENT_CHARS = 8 * 1024
FLUSH_CHARS = 16 * 1024
FLUSH_SECONDS = 0.5


class Recorder:
    """Batches shell activity ([msSinceStart, 'i' | 'o' | 'r', data]) and sends it
    to the server for the activity log. Recording happens on the device, so a
    user cannot opt out of it from the browser."""

    def __init__(self, send):
        self.send = send
        self.started_at = time.monotonic()
        self.batch = []
        self.chars = 0
        self.timer = None

    def add(self, kind, data):
        t = int((time.monotonic() - self.started_at) * 1000)
        for i in range(0, len(data), MAX_EVENT_CHARS):
            chunk = data[i : i + MAX_EVENT_CHARS]
            self.batch.append([t, kind, chunk])
            self.chars += len(chunk)
        if self.chars >= FLUSH_CHARS:
            self.flush()
        elif self.timer is None:
            self.timer = asyncio.get_running_loop().call_later(FLUSH_SECONDS, self.flush)

    def flush(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        if not self.batch:
            return
        events, self.batch, self.chars = self.batch, [], 0
        self.send(events)
