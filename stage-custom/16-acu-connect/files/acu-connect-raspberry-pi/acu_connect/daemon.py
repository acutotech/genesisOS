import asyncio
import json
import os

from .netlocation import lookup_network_location
from .session import Session
from .sysinfo import collect_info, collect_metrics
from .transport import Closed, PollTransport, Rejected, WebSocketTransport

METRICS_SECONDS = 60
LOCATION_SECONDS = 60 * 60
MAX_BACKOFF_SECONDS = 30
MAX_SESSIONS = 5
EXIT_REVOKED = 2


class Agent:
    """Keeps the outbound connection to the hub alive and starts a Session for
    every request that arrives on it. WebSockets first; if they never get through,
    as on shared hosting, ordinary web requests."""

    def __init__(self, server, device_token, log=print, polling=None):
        self.server = server.rstrip("/")
        self.token = device_token
        self.log = log
        self.sessions = {}
        self.backoff = 1
        self.polling = os.environ.get("ACU_TRANSPORT") == "poll" if polling is None else polling
        self.transport = None
        self.greeted = False
        self.reports = []

    async def run(self):
        """Runs until the device is removed from the account; returns the exit code."""
        while True:
            self.greeted = False
            self.transport = (PollTransport if self.polling else WebSocketTransport)(self.server, self.token)
            code, reason = 1006, ""
            try:
                await self.transport.open()
                self.backoff = 1
                while True:
                    self.on_raw(await self.transport.receive())
            except Rejected as e:
                if e.status == 401:
                    return self.revoke("device token rejected by the server")
                self.log(f"server answered HTTP {e.status}")
            except Closed as e:
                code, reason = e.code, e.reason
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log(f"connection error: {e}")
            finally:
                self.connection_ended()
            if code == 4001:
                return self.revoke(f"device was {reason or 'revoked'}")
            # Never got through this way, so the host may not allow it: try the other way.
            if not self.greeted:
                self.polling = not self.polling
                if self.polling:
                    self.log("WebSocket not available, connecting over HTTP requests instead")
                    continue
            self.log(f"disconnected ({code}{f' {reason}' if reason else ''}), retrying in {self.backoff}s")
            await asyncio.sleep(self.backoff)
            self.backoff = min(MAX_BACKOFF_SECONDS, self.backoff * 2)

    def revoke(self, reason):
        self.log(f'stopping: {reason}. Run "acu-connect signin" to link this device again.')
        return EXIT_REVOKED

    def connection_ended(self):
        for task in self.reports:
            task.cancel()
        self.reports = []
        for session in list(self.sessions.values()):
            session.close("signalling connection lost")
        transport = self.transport
        if transport:
            asyncio.create_task(self.quietly_close(transport))

    @staticmethod
    async def quietly_close(transport):
        try:
            await transport.close()
        except Exception:
            pass

    async def stop(self):
        for session in list(self.sessions.values()):
            session.close("agent stopping")
        await asyncio.sleep(0.2)  # lets the last messages out
        if self.transport:
            await self.quietly_close(self.transport)

    def send(self, msg):
        if self.transport:
            self.transport.send(json.dumps(msg))

    def on_raw(self, raw):
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return
        if isinstance(msg, dict):
            self.on_message(msg)

    def on_message(self, msg):
        kind = msg.get("type")
        if kind == "hello":
            self.greeted = True
            self.log(f'connected to {self.server} as "{msg.get("name")}"')
            self.reports = [asyncio.create_task(self.report_system()), asyncio.create_task(self.report_location())]
        elif kind == "session.request":
            self.start_session(msg)
        elif kind == "signal":
            session = self.sessions.get(msg.get("sessionId"))
            if session:
                session.handle_signal(msg.get("data"))
        elif kind == "session.end":
            session = self.sessions.get(msg.get("sessionId"))
            if session:
                session.close("ended by user")

    def start_session(self, msg):
        session_id = msg.get("sessionId")
        if len(self.sessions) >= MAX_SESSIONS:
            return self.send({"type": "session.error", "sessionId": session_id, "message": "Device is busy"})
        if msg.get("kind") not in ("shell", "screen"):
            return self.send({"type": "session.error", "sessionId": session_id, "message": "Unknown session kind"})

        def on_close(reason):
            self.sessions.pop(session_id, None)
            self.send({"type": "session.end", "sessionId": session_id, "reason": reason})

        record = (lambda events: self.send({"type": "session.record", "sessionId": session_id, "events": events})) if msg.get("record") else None
        signal = lambda data: self.send({"type": "signal", "sessionId": session_id, "data": data})  # noqa: E731
        try:
            self.sessions[session_id] = Session(
                session_id, msg["kind"], msg.get("iceServers"), msg.get("user"), msg.get("control") is True, record, signal, on_close, self.log
            )
        except Exception as e:
            self.send({"type": "session.error", "sessionId": session_id, "message": str(e)})

    async def report_system(self):
        """Device specifications once per connection, then live metrics every minute."""
        try:
            self.send({"type": "device.info", "info": await asyncio.to_thread(collect_info)})
        except Exception as e:
            self.log(f"could not collect system info: {e}")
        while True:
            self.send({"type": "device.metrics", "metrics": await asyncio.to_thread(collect_metrics)})
            await asyncio.sleep(METRICS_SECONDS)

    async def report_location(self):
        """Checked hourly, so a device moved to another network shows up there."""
        while True:
            try:
                location = await asyncio.to_thread(lookup_network_location)
                if location:
                    self.send({"type": "device.location", "location": location})
            except Exception as e:
                self.log(f"could not look up network location: {e}")
            await asyncio.sleep(LOCATION_SECONDS)
