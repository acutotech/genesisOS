import asyncio

from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp

from .recorder import Recorder
from .relay import Flow, RelayChannel
from .screen import Screen
from .shell import Shell

CONNECT_TIMEOUT_SECONDS = 30


def to_ice_servers(servers):
    """The server's list, without the kinds of address aiortc can't use."""
    out = []
    for server in servers or []:
        urls = server.get("urls")
        urls = urls if isinstance(urls, list) else [urls]
        usable = [u for u in urls if isinstance(u, str) and u.split(":", 1)[0] in ("stun", "turn", "turns")]
        if usable:
            out.append(RTCIceServer(urls=usable, username=server.get("username"), credential=server.get("credential")))
    return out


def parse_candidate(line, mid):
    """A browser's 'candidate:...' line, with or without a leading 'a='."""
    text = line[2:] if line.startswith("a=") else line
    candidate = candidate_from_sdp(text.split(":", 1)[1])
    candidate.sdpMid = mid if mid is not None else "0"
    candidate.sdpMLineIndex = 0
    return candidate


class Session:
    """One remote session: the client is the WebRTC offerer and owns the data channels.
    If the browser can't reach this device directly, it asks for the same channels
    to go through the server instead (see relay.py)."""

    def __init__(self, id, kind, ice_servers, user, control, record, signal, on_close, log, send=None):
        self.id = id
        self.kind = kind
        self.signal = signal
        self.on_close = on_close
        self.log = lambda m: log(f"[{id[:8]}] {m}")
        self.closed = False
        self.handlers = []
        self.channels = []
        self.control = control
        self.recorder = Recorder(record) if record else None
        self.signals = asyncio.Queue()
        self.send = send
        self.relayed = False
        self.relay_channels = {}
        self.flow = None

        email = user.get("email") if user else "unknown"
        mode = (" (control)" if control else " (view only)") if kind == "screen" else ""
        self.log(f"{kind} session requested by {email}{mode}{', recorded' if self.recorder else ''}")

        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=to_ice_servers(ice_servers)))
        self.pc.on("connectionstatechange", self.state_changed)
        loop = asyncio.get_running_loop()
        self.connect_timer = loop.call_later(CONNECT_TIMEOUT_SECONDS, self.close, "connect timeout")

        if kind == "shell":
            self.open_shell()
        else:
            self.open_screen()
        self.worker = asyncio.create_task(self.negotiate())

    def state_changed(self):
        if self.relayed:
            return
        state = self.pc.connectionState
        self.log(f"peer connection {state}")
        if state == "connected":
            self.connect_timer.cancel()
        if state in ("failed", "closed"):
            self.close(f"peer {state}")

    def channel(self, label):
        if self.relayed:
            dc = RelayChannel(label, self.id, self.send, self.flow)
            self.relay_channels[label] = dc
        else:
            dc = self.pc.createDataChannel(label)
        self.channels.append(dc)
        relayed = self.relayed
        dc.on("close", lambda: self.relayed == relayed and self.close(f"{label} channel closed"))
        return dc

    def start_relay(self):
        """Switches to channels carried by the server, as long as nothing has started
        over the direct connection."""
        if self.closed or self.relayed or self.handlers or not self.send:
            return
        self.relayed = True
        self.connect_timer.cancel()
        self.worker.cancel()
        self.channels = []
        asyncio.create_task(self.pc.close())
        self.flow = Flow()
        self.log("no direct connection: relaying through the server")
        # Tell the browser first: everything the channels send follows this message.
        self.send({"type": "relay.ready", "sessionId": self.id})
        if self.kind == "shell":
            self.open_shell()
        else:
            self.open_screen()
        for channel in list(self.relay_channels.values()):
            channel.open()

    def relay_message(self, msg):
        kind = msg.get("type")
        if kind == "session.relay":
            self.start_relay()
        elif kind == "relay" and self.relayed:
            channel = self.relay_channels.get(msg.get("label"))
            if channel:
                channel.deliver(msg)
        elif kind == "relay.ack" and self.flow:
            self.flow.ack(msg.get("bytes"))

    def open_shell(self):
        dc = self.channel("shell")

        def opened():
            record = self.recorder.add if self.recorder else None
            on_exit = lambda code: asyncio.get_running_loop().call_later(0.2, self.close, "shell exited")  # noqa: E731
            try:
                self.handlers.append(Shell(dc, on_exit, self.log, record))
            except OSError as e:
                self.close(f"could not start the shell: {e}")

        dc.on("open", opened)

    def open_screen(self):
        screen = self.channel("screen")
        input_dc = self.channel("input")
        ready = []

        def opened():
            ready.append(True)
            if len(ready) == 2:
                self.handlers.append(Screen(screen, input_dc, self.log, self.control))

        screen.on("open", opened)
        input_dc.on("open", opened)

    async def negotiate(self):
        """Offer first, then answers and candidates strictly in the order they arrive."""
        try:
            await self.pc.setLocalDescription(await self.pc.createOffer())
            local = self.pc.localDescription
            self.signal({"type": local.type, "sdp": local.sdp})
            while not self.closed:
                data = await self.signals.get()
                try:
                    if data.get("type") == "answer" and data.get("sdp"):
                        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type="answer"))
                    elif data.get("candidate"):
                        await self.pc.addIceCandidate(parse_candidate(data["candidate"], data.get("mid")))
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.log(f"bad signal: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.close(f"could not start the connection: {e}")

    def handle_signal(self, data):
        if not self.closed and isinstance(data, dict):
            self.signals.put_nowait(data)

    def close(self, reason):
        if self.closed:
            return
        self.closed = True
        self.connect_timer.cancel()
        self.log(f"closing: {reason}")
        for handler in self.handlers:
            handler.close()
        for channel in list(self.relay_channels.values()):
            channel.close()
        # Send the last recorded output before the session-end message.
        if self.recorder:
            self.recorder.flush()
        self.worker.cancel()
        self.on_close(reason)
        asyncio.create_task(self.pc.close())
