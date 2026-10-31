import asyncio
import collections

import websockets

from .http import request

RECEIVE_TIMEOUT = 60
RETRIES = 2
RETRY_SECONDS = 2


class Closed(Exception):
    def __init__(self, code=1006, reason=""):
        super().__init__(f"{code} {reason}".strip())
        self.code = code
        self.reason = reason


class Rejected(Exception):
    """The server answered, and refused the connection."""

    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status = status


class Outbox:
    """Sends one message or batch at a time, so messages arrive in order."""

    def __init__(self, deliver):
        self.deliver = deliver
        self.waiting = []
        self.task = None
        self.failed = False

    def send(self, text):
        self.waiting.append(text)
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.drain())

    async def drain(self):
        while self.waiting and not self.failed:
            batch, self.waiting = self.waiting, []
            try:
                await self.deliver(batch)
            except Exception:
                self.failed = True

    def stop(self):
        if self.task:
            self.task.cancel()


class WebSocketTransport:
    def __init__(self, server, token):
        self.url = f"{server.replace('http', 'ws', 1)}/ws/device"
        self.token = token
        self.ws = None
        self.outbox = Outbox(self.deliver)

    async def open(self):
        try:
            self.ws = await websockets.connect(
                self.url,
                additional_headers={"Authorization": f"Bearer {self.token}"},
                open_timeout=10,
                ping_interval=30,
                ping_timeout=20,
                max_size=1024 * 1024,
            )
        except websockets.exceptions.InvalidStatus as e:
            raise Rejected(e.response.status_code) from e

    async def deliver(self, batch):
        for text in batch:
            await self.ws.send(text)

    def send(self, text):
        self.outbox.send(text)

    async def receive(self):
        try:
            return await self.ws.recv()
        except websockets.exceptions.ConnectionClosed as e:
            raise Closed(e.rcvd.code if e.rcvd else 1006, e.rcvd.reason if e.rcvd else "") from e

    async def close(self):
        self.outbox.stop()
        if self.ws:
            await self.ws.close()


class PollTransport:
    """The same messages over ordinary requests, for servers on hosts that block
    WebSockets, such as shared hosting."""

    def __init__(self, server, token):
        self.server = server
        self.headers = {"Authorization": f"Bearer {token}"}
        self.id = None
        self.after = 0
        self.inbox = collections.deque()
        self.closed = None
        self.outbox = Outbox(self.deliver)

    async def post(self, action, body):
        url = f"{self.server}/api/poll/{action}"
        return await asyncio.to_thread(request, url, "POST", body, self.headers, RECEIVE_TIMEOUT)

    async def open(self):
        status, data = await self.post("open", {"role": "device"})
        if status >= 300:
            raise Rejected(status)
        self.id = data["id"]

    async def deliver(self, batch):
        status, _ = await self.post(f"{self.id}/send", {"messages": batch})
        if status >= 300:
            raise Closed()

    def send(self, text):
        self.outbox.send(text)

    async def receive(self):
        failures = 0
        while not self.inbox:
            if self.closed:
                raise self.closed
            if self.outbox.failed:
                raise Closed()
            try:
                status, data = await self.post(f"{self.id}/receive", {"after": self.after})
            except OSError:
                failures += 1
                if failures > RETRIES:
                    raise Closed() from None
                await asyncio.sleep(RETRY_SECONDS)
                continue
            failures = 0
            if status >= 300:
                raise Closed()  # the server restarted
            self.inbox.extend(data.get("messages") or [])
            closed = data.get("closed")
            if closed:
                self.closed = Closed(closed.get("code", 1006), closed.get("reason", ""))
        seq, text = self.inbox.popleft()
        self.after = seq
        return text

    async def close(self):
        self.outbox.stop()
        if self.id and not self.closed:
            try:
                await asyncio.wait_for(self.post(f"{self.id}/close", {}), 3)
            except (OSError, asyncio.TimeoutError):
                pass
