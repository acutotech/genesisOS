import base64


class Flow:
    """What a relayed session has sent that the browser hasn't confirmed yet. The
    channels report it as their buffered amount, so the shell and the desktop slow
    down to whatever the route through the server can carry."""

    def __init__(self):
        self.in_flight = 0
        self.channels = []

    def ack(self, n):
        try:
            self.in_flight = max(0, self.in_flight - max(0, int(n)))
        except (TypeError, ValueError):
            return
        for channel in self.channels:
            if self.in_flight <= channel.bufferedAmountLowThreshold:
                channel.emit("bufferedamountlow")


class RelayChannel:
    """Stands in for a WebRTC data channel when browser and device can't reach each
    other directly, for example on two networks behind strict routers. Messages go
    through the Acu Connect server instead, over the client's normal connection.
    Same interface as the aiortc channel the shell and desktop code use."""

    def __init__(self, label, session_id, send, flow):
        self.label = label
        self.session_id = session_id
        self.send_message = send
        self.flow = flow
        self.readyState = "connecting"
        self.bufferedAmountLowThreshold = 0
        self.listeners = {}
        flow.channels.append(self)

    @property
    def bufferedAmount(self):
        return self.flow.in_flight

    def on(self, name, handler):
        self.listeners.setdefault(name, []).append(handler)

    def emit(self, name, *args):
        for handler in list(self.listeners.get(name, [])):
            handler(*args)

    def send(self, data):
        if self.readyState != "open":
            return
        if isinstance(data, (bytes, bytearray, memoryview)):
            payload = {"bin": base64.b64encode(bytes(data)).decode("ascii")}
        else:
            payload = {"text": data}
        n = len(data)
        self.flow.in_flight += n
        self.send_message({"type": "relay", "sessionId": self.session_id, "label": self.label, "n": n, **payload})

    def deliver(self, msg):
        if self.readyState == "open" and isinstance(msg.get("text"), str):
            self.emit("message", msg["text"])

    def open(self):
        self.readyState = "open"
        self.emit("open")

    def close(self):
        if self.readyState == "closed":
            return
        self.readyState = "closed"
        self.emit("close")
