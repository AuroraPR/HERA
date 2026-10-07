"""MicroPython/CPython UCI worker: one result, local close, MQTT only.

Wire layout follows the bundled Qorvo fira.py/fira_app.py/qorvo_msg.py.
No network, threads or board imports here; hardware is injected by main.py.
"""
import struct
try:
    import ubinascii as binascii
except ImportError:
    import binascii


def packet(gid, oid, payload=b""):
    if len(payload) > 250:
        raise ValueError("UCI command too large")
    return bytes((0x20 | gid, oid, 0, len(payload))) + payload


def mac_bytes(mac):
    raw = binascii.unhexlify(mac.replace(":", ""))
    if len(raw) != 2:
        raise ValueError("Only short UWB addresses are supported")
    # Matches the existing CLI Android-style MAC conversion.
    return raw


def app_config(local_mac, peer_mac):
    # Same static STS / DS-TWR responder profile as run_fira_twr_backup.py.
    params = [(0x00, b"\x00"), (0x11, b"\x00"), (0x03, b"\x00"),
              (0x01, b"\x02"), (0x06, mac_bytes(local_mac)), (0x04, b"\x09"),
              (0x22, b"\x01"), (0x02, b"\x00"), (0x12, b"\x03"),
              (0x2e, b"\x0b"), (0x27, b"\x08\x07"),
              (0x28, b"\x01\x02\x03\x04\x05\x06"), (0x0d, b"\x01"),
              (0x2b, b"\x00" * 8), (0x14, b"\x09"), (0x15, b"\x02"),
              (0x08, struct.pack("<H", 2400)), (0x09, struct.pack("<I", 120)),
              (0x1b, b"\x06"), (0x32, b"\x00\x00"), (0x2c, b"\x01"),
              (0x13, b"\x01"), (0x2d, b"\x00"), (0x05, b"\x01"),
              (0x07, mac_bytes(peer_mac)), (0x24, b"\x00"), (0x35, b"\x01")]
    return bytes((len(params),)) + b"".join(bytes((key, len(value))) + value for key, value in params)


def first_twr(payload, handle, peer_mac):
    if len(payload) < 25 or struct.unpack_from("<I", payload, 4)[0] != handle:
        return None
    if payload[13] != 1 or payload[15] != 0:
        return None  # Only the configured short-address TWR profile.
    for i in range(payload[24]):
        start = 25 + i * 31
        if len(payload) < start + 31:
            return None
        if payload[start:start + 2] != mac_bytes(peer_mac) or payload[start + 2] != 0:
            continue
        distance = struct.unpack_from("<H", payload, start + 4)[0]
        if distance == 65535:
            continue
        return distance, -payload[start + 19] / 2
    return None


class AnchorWorker:
    def __init__(self, anchor, write, emit, clock_ms, reset, diff=None):
        self.anchor, self.write, self.emit = anchor, write, emit
        self.clock, self.reset = clock_ms, reset
        self.diff = diff or (lambda a, b: a - b)
        self.active = None
        self.buffer = bytearray()
        self.fragments = {}
        self.closed = []
        self.reset_ids = []
        self.pending = None
        self.state = "idle"

    def event(self, event, **fields):
        body = {"event": event, "anchor": self.anchor,
                "session_id": self.active["session_id"], "watch_id": self.active["watch_id"]}
        body.update(fields)
        self.emit(body)

    def command(self, message):
        sid = message.get("session_id", "")
        if not sid:
            return
        action = message.get("action")
        if action == "reset":
            if sid not in self.reset_ids:
                if self.active:
                    self.close("coordinator_recovery")
                    self.finish(reset=True)
                else:
                    self.reset()
                self.reset_ids = (self.reset_ids + [sid])[-16:]
            self.emit({"event": "ready", "session_id": sid, "anchor": self.anchor})
            return
        if action == "stop":
            if self.active and self.active["session_id"] == sid:
                self.close("requested")
            elif not self.active or sid in self.closed:
                self.emit({"event": "closed", "session_id": sid, "anchor": self.anchor,
                           "watch_id": message.get("watch_id"), "already_closed": True})
            return
        if action != "start":
            return
        if sid in self.closed:
            self.emit({"event": "closed", "session_id": sid, "anchor": self.anchor,
                       "watch_id": message.get("watch_id"), "already_closed": True})
            return
        if self.active:
            if self.active["session_id"] != sid:
                self.emit({"event": "busy", "session_id": sid, "anchor": self.anchor,
                           "watch_id": message.get("watch_id")})
            return
        # Validate before claiming ownership or emitting UART commands.
        config = app_config(self.anchor, message["watch_mac"])
        uwb_id = int(message["uwb_session_id"])
        if not 0 < uwb_id < 0x80000000:
            raise ValueError("Invalid UWB session ID")
        self.buffer = bytearray()
        self.fragments = {}
        self.active = dict(message)
        self.active.update(start_ms=self.clock(), limit_ms=min(5000, max(1, int(message.get("remaining_ms", 5000)))),
                           handle=uwb_id, result=False, config=config, closing=False)
        self.send("init", 1, 0, struct.pack("<I", uwb_id) + b"\x00")

    def send(self, state, gid, oid, payload):
        self.state = state
        self.pending = (gid, oid, self.clock())
        self.write(packet(gid, oid, payload))

    def close(self, reason):
        if not self.active or self.active["closing"]:
            return
        self.active["closing"] = True
        if not self.active["result"]:
            self.active["result"] = True
            self.event("result", distance_cm=-1, rssi_dbm=-1, reason=reason)
        # An in-flight init may allocate a v2 handle: wait for its response.
        if self.pending and self.state == "init":
            return
        self.send("stop", 2, 1, struct.pack("<I", self.active["handle"]))

    def finish(self, reset=False):
        if reset:
            # Hard power cycle confirms no old UWB session can remain active.
            self.reset()
        self.event("closed", reset=reset,
                   distance_cm=self.active.get("distance_cm", -1),
                   rssi_dbm=self.active.get("rssi_dbm", -1))
        self.closed.append(self.active["session_id"])
        self.closed = self.closed[-32:]
        self.active = None
        self.pending = None
        self.state = "idle"
        self.buffer = bytearray()
        self.fragments = {}

    def feed(self, data):
        self.buffer.extend(data)
        if len(self.buffer) > 4096:
            self.buffer = bytearray()
            self.close("uart_overflow")
            return
        while len(self.buffer) >= 4:
            mt = self.buffer[0] >> 5
            if mt not in (1, 2, 3):
                del self.buffer[0]
                continue
            size = self.buffer[3]
            if len(self.buffer) < 4 + size:
                return
            header = self.buffer[0]
            gid, oid = header & 15, self.buffer[1] & 63
            payload = bytes(self.buffer[4:4 + size])
            del self.buffer[:4 + size]
            key = (mt, gid, oid)
            payload = self.fragments.pop(key, b"") + payload
            if len(payload) > 2048:
                self.close("fragment_overflow")
                return
            if header & 16:
                self.fragments[key] = payload
                continue
            self.message(mt, gid, oid, payload)

    def message(self, mt, gid, oid, payload):
        if not self.active:
            return
        if mt == 3 and (gid, oid) == (2, 0) and not self.active["closing"]:
            measurement = first_twr(payload, self.active["handle"], self.active["watch_mac"])
            if measurement is not None and not self.active["result"]:
                self.active["result"] = True
                self.active["distance_cm"], self.active["rssi_dbm"] = measurement
                self.event("result", distance_cm=measurement[0], rssi_dbm=measurement[1],
                           elapsed_ms=self.diff(self.clock(), self.active["start_ms"]))
                self.close("first_distance")
            return
        if mt != 2 or not self.pending or (gid, oid) != self.pending[:2] or not payload:
            return
        state = self.state
        self.pending = None
        if state == "deinit":
            self.finish(reset=payload[0] not in (0, 0x11))
            return
        if state == "stop":
            self.send("deinit", 1, 1, struct.pack("<I", self.active["handle"]))
            return
        if payload[0] != 0:
            self.close("uci_error_%02x" % payload[0])
            return
        if state == "init":
            if len(payload) >= 5:
                self.active["handle"] = struct.unpack_from("<I", payload, 1)[0]
            if self.active["closing"]:
                self.send("stop", 2, 1, struct.pack("<I", self.active["handle"]))
            else:
                self.send("config", 1, 3, struct.pack("<I", self.active["handle"]) + self.active["config"])
        elif state == "config":
            self.send("ranging", 2, 0, struct.pack("<I", self.active["handle"]))

    def poll(self):
        if not self.active:
            return
        if self.diff(self.clock(), self.active["start_ms"]) >= self.active["limit_ms"]:
            if not self.active["result"]:
                self.active["result"] = True
                self.event("result", distance_cm=-1, rssi_dbm=-1, reason="timeout")
            self.finish(reset=True)
        elif self.pending and self.diff(self.clock(), self.pending[2]) >= 500:
            self.close("uci_timeout")
            if self.active and self.active["closing"]:
                self.finish(reset=True)
