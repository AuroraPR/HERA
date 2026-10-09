"""Event-driven MQTT coordinator. No serial sockets or ranging subprocesses."""
from __future__ import annotations

import json
import math
import queue
import time
import uuid
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from pathlib import Path

from adaptive_scheduler import AdaptiveAnchorScheduler, canonical_anchor, load_anchor_positions


class StepModel:
    """The validated scheduler, with a separate logical clock per watch.

    A completed attempt advances that watch exactly one slice, regardless of
    wall time or how fast the other watches finish. No extra failure memory.
    """
    def __init__(self, anchors, positions, config):
        self.anchors, self.positions = list(anchors), positions
        self.config = dict(config)
        self.config["proximity_weight"] = self.config.pop("w_cercania", 1.0)
        self.config["fairness_weight"] = self.config.pop("w_equidad", 1.0)
        self.models, self.steps = {}, {}

    def get(self, watch):
        if watch not in self.models:
            self.models[watch] = AdaptiveAnchorScheduler(self.anchors, self.positions, **self.config)
            self.steps[watch] = 0
        return self.models[watch]

    def choose(self, watch, free):
        model = self.get(watch)
        return model.choose(watch, free, now=1000 + self.steps[watch])

    def record(self, watch, anchor, distance):
        model = self.get(watch)
        model.record(watch, anchor, [distance], now=1000 + self.steps[watch])
        self.steps[watch] += 1

    def snapshot(self):
        states = {w: m.snapshot() for w, m in self.models.items()}
        width = int(self.config.get("window_seconds", 15))
        matrix = [{} for _ in range(width)]
        for watch, state in states.items():
            for i, slice_ in enumerate(state["history"]):
                matrix[i][watch] = slice_.get(watch, {})
        return {"steps": dict(self.steps), "matrix": matrix, "watches": states}


class MqttCoordinator:
    def __init__(self, config, config_path, publish, clock=time.monotonic, log=None):
        self.config, self.publish, self.clock = config, publish, clock
        options = config["orchestrator"]
        self.verbose = bool(options.get("verbose", False))
        self.limit = float(options.get("timelimit", 5))
        if not 0 < self.limit <= 5:
            raise ValueError("timelimit must be in (0,5]")
        self.boards = {canonical_anchor(b["anchor"]): b for b in config["boards"]}
        self.watches = list(dict.fromkeys(config["cycle"]["watches"]))
        path = Path(config_path).resolve().parent / options["anchor_positions_file"]
        positions = load_anchor_positions(path)
        missing = sorted(set(self.boards) - set(positions))
        if missing:
            raise ValueError("Faltan posiciones en " + str(path.resolve()) + ": " + ", ".join(missing))
        self.model = StepModel(self.boards, positions, options["adaptive_scheduler"])
        self.sessions, self.by_watch, self.by_anchor = {}, {}, {}
        self.ready_anchors, self.ready_watches = set(), set()
        self.board_health = {a: {"total_ok": 0, "total_not_ok": 0, "consecutive_failures": 0,
                                  "session_history": []} for a in self.boards}
        self.watch_health = {}
        self.cursor = 0
        self.connected = False
        self.boot_id = "boot_" + uuid.uuid4().hex
        self.recovery = {}
        self.log = log or self.write_measurement
        self.data_dir = Path(config_path).resolve().parent / options.get("data_dir", "data")

    def vlog(self, message):
        if self.verbose:
            print("[VERBOSE] " + message, flush=True)

    def send(self, topic, body):
        self.publish(topic, body)

    def watch_command(self, session, action):
        self.send("uwb/commands/" + session["watch_id"],
                  {"action": action, "session_id": session["session_id"],
                   "anchor": session["anchor"], "uwb_session_id": session["uwb_session_id"],
                   "timelimit_ms": max(1, int((session["deadline"] - self.clock()) * 1000)) if action == "start" else 0})

    def pico_command(self, session, action):
        body = {"action": action, "session_id": session["session_id"],
                "watch_id": session["watch_id"], "uwb_session_id": session["uwb_session_id"],
                "watch_mac": session.get("watch_mac", ""),
                "remaining_ms": max(1, int((session["deadline"] - self.clock()) * 1000))}
        self.send("uwb/pico/" + session["anchor"] + "/commands", body)

    def close(self, session):
        if not session["closing"]:
            session["close_started"] = self.clock()
        session["closing"] = True
        session["last_stop"] = self.clock()
        self.watch_command(session, "stop")
        self.pico_command(session, "stop")

    def record(self, session, distance, rssi=-1):
        if session["recorded"]:
            return
        if not isinstance(distance, (int, float)) or not math.isfinite(distance) or distance < 0:
            distance, rssi = -1, -1
        if not isinstance(rssi, (int, float)) or not math.isfinite(rssi):
            rssi = -1
        session["recorded"] = True
        session["distance_cm"] = distance
        self.model.record(session["watch_id"], session["anchor"], distance)
        health = self.board_health[session["anchor"]]
        health["last_attempt_at"] = time.time()
        health["last_ok_count"], health["last_not_ok_count"] = int(distance >= 0), int(distance < 0)
        health["total_ok"] += int(distance >= 0)
        health["total_not_ok"] += int(distance < 0)
        health["consecutive_failures"] = health["consecutive_failures"] + 1 if distance < 0 else 0
        if distance >= 0:
            health["last_success_at"] = time.time()
        health["session_history"] = (health["session_history"] + [{"timestamp": time.time(),
             "success": distance >= 0, "ok_count": int(distance >= 0), "not_ok_count": int(distance < 0)}])[-20:]
        self.log(session, distance, rssi)

    def handle(self, topic, body, retained=False):
        self.vlog(f"procesando topic={topic!r} retained={retained} body={body}")
        parts = topic.split("/")
        if parts[:2] == ["uwb", "presence"] and len(parts) == 3:
            watch = parts[2]
            self.watch_health[watch] = dict(self.watch_health.get(watch, {}), **body,
                                            online=body.get("state") == "online", last_presence=time.time())
            # Retained presence is allowed; all command/result events are live.
            if body.get("state", body.get("status")) == "online":
                if watch in self.watches and watch not in self.ready_watches:
                    self.vlog(f"reloj {watch}: online y configurado; solicito reset/presentación")
                    self.request_recovery("watch", watch)
                else:
                    self.vlog(f"reloj {watch}: online pero no listo; configurados={self.watches}")
            else:
                self.ready_watches.discard(watch)
            return
        if len(parts) == 4 and parts[:2] == ["uwb", "pico"] and parts[3] == "status":
            anchor = canonical_anchor(parts[2])
            if anchor in self.board_health:
                health = self.board_health[anchor]
                health.update(pico_reachable=body.get("state") == "online", pico_last_seen=time.time())
                for source, target in (("ip", "pico_ip"), ("uptime_s", "pico_uptime_s"),
                                       ("battery_voltage", "pico_battery_voltage"), ("battery_percent", "pico_battery_percent"),
                                       ("battery_charging", "pico_battery_charging"), ("power", "pico_power")):
                    health[target] = body.get(source)
            if body.get("state") == "online" and body.get("power", "ON") == "ON" and body.get("protocol") == "mqtt_uci_v1":
                if anchor in self.boards and anchor not in self.ready_anchors:
                    self.vlog(f"placa {anchor}: online, power ON y protocolo válido; solicito ready")
                    self.request_recovery("pico", anchor)
            else:
                self.vlog(f"placa {anchor}: NO lista; state={body.get('state')} power={body.get('power')} protocol={body.get('protocol')}")
                self.ready_anchors.discard(anchor)
                if body.get("state") == "offline" and anchor in self.boards:
                    # Misma recuperación que POST /action/reset/<anchor>.
                    # Se reintenta hasta recibir ready, incluso si la Pico
                    # vuelve a conectarse después del mensaje offline.
                    sid = self.by_anchor.get(anchor)
                    if sid:
                        self.record(self.sessions[sid], -1)
                        self.close(self.sessions[sid])
                    self.request_recovery("pico", anchor)
            return
        if retained:
            return
        sid = body.get("session_id")
        session = self.sessions.get(sid)
        pico = len(parts) == 4 and parts[:2] == ["uwb", "pico"] and parts[3] == "events"
        event = body.get("event")
        if pico and event in ("result", "closed", "ready", "busy"):
            if session and canonical_anchor(parts[2]) != session["anchor"]:
                return
            self.send("uwb/pico/" + parts[2] + "/commands",
                      {"action": "ack", "session_id": sid, "event": body["event"]})
        if sid in self.recovery:
            recovery = self.recovery[sid]
            if recovery["kind"] == "pico" and pico and canonical_anchor(parts[2]) == recovery["id"] and event == "ready":
                self.ready_anchors.add(recovery["id"])
                del self.recovery[sid]
            elif recovery["kind"] == "watch" and topic == "uwb/session/" + recovery["id"] and body.get("state") == "stopped":
                self.ready_watches.add(recovery["id"])
                del self.recovery[sid]
            return
        if not session:
            return  # Duplicate/stale/unknown attempt: never update M twice.
        if pico:
            if body.get("watch_id") != session["watch_id"]:
                return
            event = body.get("event")
            if event in ("result", "closed"):
                if self.clock() >= session["deadline"] and not session["recorded"]:
                    self.record(session, -1)
                else:
                    self.record(session, body.get("distance_cm", -1), body.get("rssi_dbm", -1))
                if event == "closed":
                    session["pico_closed"] = True
                self.close(session)
            elif event == "busy":
                self.record(session, -1)
                self.close(session)
        elif len(parts) == 3 and parts[:2] == ["uwb", "session"] and parts[2] == session["watch_id"]:
            event = body.get("state", body.get("status", body.get("event")))
            self.watch_health.setdefault(session["watch_id"], {}).update(last_session_update=time.time(),
                    current_anchor=session["anchor"], uwb_mac=body.get("uwb_mac", session.get("watch_mac")))
            if event == "prepared" and not session["closing"] and not session["started"]:
                if canonical_anchor(body.get("anchor", "")) != session["anchor"]:
                    return
                session["watch_mac"] = body.get("uwb_mac", "")
                if self.clock() >= session["deadline"]:
                    self.record(session, -1)
                    self.close(session)
                else:
                    session["started"] = True
                    self.pico_command(session, "start")
            elif event in ("stopped", "closed"):
                session["watch_closed"] = True
                if not session["closing"]:
                    self.record(session, -1)
                    self.close(session)
            elif event == "error":
                self.record(session, -1)
                self.close(session)
        if session["pico_closed"] and session["watch_closed"]:
            self.by_watch.pop(session["watch_id"], None)
            self.by_anchor.pop(session["anchor"], None)
            self.sessions.pop(sid, None)

    def tick(self):
        now = self.clock()
        for recovery in self.recovery.values():
            if self.connected and now - recovery["sent"] >= .5:
                self.send(recovery["topic"], {"action": "reset", "session_id": recovery["sid"]})
                recovery["sent"] = now
        for session in list(self.sessions.values()):
            if now >= session["deadline"] and not session["closing"]:
                self.record(session, -1)
                self.close(session)
            elif session["closing"] and now - session["last_stop"] >= .25:
                # Reenvío corto para dar margen a un ACK perdido.
                if now - session["close_started"] < 2.0:
                    self.close(session)
                else:
                    # No bloquear indefinidamente el reloj ni la placa si uno
                    # de los dos ACK de cierre nunca llega.
                    self.vlog(f"liberación forzada session={session['session_id']} "
                              f"watch_closed={session['watch_closed']} pico_closed={session['pico_closed']}")
                    self.by_watch.pop(session["watch_id"], None)
                    self.by_anchor.pop(session["anchor"], None)
                    self.sessions.pop(session["session_id"], None)
                    if not session["watch_closed"]:
                        self.ready_watches.discard(session["watch_id"])
                        self.request_recovery("watch", session["watch_id"])
                    if not session["pico_closed"]:
                        self.ready_anchors.discard(session["anchor"])
                        self.request_recovery("pico", session["anchor"])
        if not self.connected or not self.config.get("cycle", {}).get("enabled", True):
            return
        order = self.watches[self.cursor:] + self.watches[:self.cursor]
        self.cursor = (self.cursor + 1) % len(self.watches) if self.watches else 0
        for watch in order:
            if watch in self.by_watch or watch not in self.ready_watches:
                self.vlog(f"no emparejo reloj={watch}: activo={watch in self.by_watch} ready_watch={watch in self.ready_watches} ready_watches={self.ready_watches}")
                continue
            free = [a for a in self.boards if a in self.ready_anchors and a not in self.by_anchor]
            self.vlog(f"buscando placa para reloj={watch}: ready_anchors={self.ready_anchors} libres={free}")
            anchor, probabilities = self.model.choose(watch, free)
            if anchor is None:
                self.vlog(f"sin placa elegible para reloj={watch}")
                continue
            sid = "sess_" + uuid.uuid4().hex
            session = {"session_id": sid, "watch_id": watch, "anchor": anchor,
                       "uwb_session_id": (uuid.uuid4().int & 0x7fffffff) or 1,
                       "deadline": now + self.limit, "closing": False, "started": False,
                       "recorded": False, "watch_closed": False, "pico_closed": False,
                       "probabilities": probabilities}
            # Reserve BEFORE publishing any command; this loop owns all state.
            self.sessions[sid] = session
            self.by_watch[watch], self.by_anchor[anchor] = sid, sid
            self.watch_command(session, "start")
            self.vlog(f"EMPAREJAMIENTO creado watch={watch} anchor={anchor} session={sid}")
        assert len(self.by_anchor) == len(self.by_watch) == len(self.sessions)

    def request_recovery(self, kind, identity):
        if any(r["kind"] == kind and r["id"] == identity for r in self.recovery.values()):
            return
        sid = self.boot_id + "_" + uuid.uuid4().hex[:8]
        if sid not in self.recovery:
            topic = "uwb/pico/" + identity + "/commands" if kind == "pico" else "uwb/commands/" + identity
            self.recovery[sid] = {"kind": kind, "id": identity, "sid": sid, "topic": topic, "sent": self.clock()}
            self.send(topic, {"action": "reset", "session_id": sid})

    def write_measurement(self, session, distance, rssi):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / (datetime.now().strftime("%Y%m%d") + "." + session["watch_id"] + ".tsv")
        new = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8") as out:
            if new:
                out.write("timestamp\tsession_id\twatch_id\tanchor\tboard_mac\tserial_port\twatch_mac\tdistance_cm\trssi_dbm\n")
            out.write("\t".join(map(str, [datetime.now().isoformat(), session["session_id"], session["watch_id"],
                                        session["anchor"], self.boards[session["anchor"]].get("my_mac", session["anchor"]),
                                        "mqtt:" + session["anchor"], session.get("watch_mac", ""), distance, rssi])) + "\n")
        path = self.data_dir / "M_latest.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.model.snapshot(), allow_nan=False), encoding="utf-8")
        temporary.replace(path)

    def snapshot(self):
        return {"boards": self.boards, "watches": self.watch_health,
                "active_sessions_by_watch": self.by_watch, "active_sessions_by_anchor": self.by_anchor,
                "sessions": self.sessions, "board_health": self.board_health, "transport": "mqtt",
                "round_robin_mode": "adaptive", "adaptive_scheduler": self.model.snapshot(),
                "server_time": time.time()}


def run(config, config_path):
    import paho.mqtt.client as mqtt
    verbose = bool(config.get("orchestrator", {}).get("verbose", False))
    def vprint(message):
        if verbose:
            print("[VERBOSE] " + message, flush=True)

    events = queue.Queue()
    client = mqtt.Client(client_id="hera_orchestrator_" + uuid.uuid4().hex[:8])
    def publish(topic, body):
        client.publish(topic, json.dumps(body, allow_nan=False), qos=1, retain=False)
    coordinator = MqttCoordinator(config, config_path, publish)
    cached = {"status": json.dumps(coordinator.snapshot(), allow_nan=False).encode()}
    server = None
    dashboard = config.get("dashboard", {})
    if dashboard.get("enabled", False):
        from orquestador_multi import DASHBOARD_HTML
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                if self.path not in ("/", "/status", "/index.html"):
                    self.send_error(404)
                    return
                status = self.path == "/status"
                payload = cached["status"] if status else DASHBOARD_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json" if status else "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def do_POST(self):
                prefix = "/action/reset/"
                anchor = canonical_anchor(self.path[len(prefix):]) if self.path.startswith(prefix) else ""
                if anchor not in coordinator.boards:
                    self.send_error(404)
                    return
                events.put(("__reset", {"anchor": anchor}, False))
                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
        server = ThreadingHTTPServer((dashboard.get("host", "0.0.0.0"), int(dashboard.get("port", 8080))), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    def connected(client, userdata, flags, rc):
        vprint(f"on_connect rc={rc} broker={config['mqtt']['broker']}:{config['mqtt'].get('port', 1883)}")
        if rc == 0:
            for topic in ("uwb/presence/+", "uwb/session/+", "uwb/pico/+/status", "uwb/pico/+/events"):
                result, mid = client.subscribe(topic, qos=1)
                vprint(f"suscripción topic={topic!r} result={result} mid={mid}")
            events.put(("__connected", {}, False))
        else:
            vprint(f"conexión MQTT rechazada rc={rc}")
    client.on_connect = connected
    client.on_disconnect = lambda *args: events.put(("__disconnected", {}, False))
    def message(client, userdata, msg):
        vprint(f"mensaje recibido topic={msg.topic!r} retain={msg.retain} payload={msg.payload!r}")
        try:
            body = json.loads(msg.payload)
            if isinstance(body, dict):
                events.put((msg.topic, body, msg.retain))
        except (ValueError, UnicodeError):
            pass
    client.on_message = message
    client.connect(config["mqtt"]["broker"], int(config["mqtt"].get("port", 1883)), keepalive=15)
    vprint(f"cliente MQTT creado; conectando a {config['mqtt']['broker']}:{config['mqtt'].get('port', 1883)}")
    client.loop_start()
    last_status = -1.0
    try:
        while True:
            try:
                topic, body, retained = events.get(timeout=.01)
                if topic == "__connected":
                    coordinator.connected = True
                    for session in list(coordinator.sessions.values()):
                        coordinator.record(session, -1)
                        coordinator.close(session)
                elif topic == "__disconnected":
                    coordinator.connected = False
                    coordinator.ready_anchors.clear()
                    coordinator.ready_watches.clear()
                elif topic == "__reset":
                    anchor = body["anchor"]
                    coordinator.ready_anchors.discard(anchor)
                    sid = coordinator.by_anchor.get(anchor)
                    if sid:
                        coordinator.record(coordinator.sessions[sid], -1)
                        coordinator.close(coordinator.sessions[sid])
                    coordinator.request_recovery("pico", anchor)
                else:
                    coordinator.handle(topic, body, retained)
            except queue.Empty:
                pass
            coordinator.tick()
            if time.monotonic() - last_status >= 1:
                cached["status"] = json.dumps(coordinator.snapshot(), allow_nan=False).encode()
                if coordinator.connected:
                    client.publish("uwb/orchestrator/status", cached["status"], qos=0, retain=True)
                last_status = time.monotonic()
    except KeyboardInterrupt:
        coordinator.connected = False
        for session in list(coordinator.sessions.values()):
            coordinator.close(session)
    finally:
        client.disconnect()
        client.loop_stop()
        if server:
            server.shutdown()
            server.server_close()
