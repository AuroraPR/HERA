import copy
from collections import deque
import json
import math
import struct
import sys
import unittest
from pathlib import Path

from adaptive_scheduler import AdaptiveAnchorScheduler
from mqtt_orchestrator import MqttCoordinator, StepModel

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "despliegue-hera/Server_TCP/Server_TCP"))
from anchor_uci import AnchorWorker, app_config, first_twr, packet

CONFIG_PATH = Path(__file__).with_name("orquestador_config.json")


def response(gid, oid, payload=b"\x00"):
    return bytes((0x40 | gid, oid, 0, len(payload))) + payload


def twr(handle, peer="94:FC", distance=123, status=0):
    payload = bytearray(56)
    struct.pack_into("<I", payload, 4, handle)
    payload[13], payload[15], payload[24] = 1, 0, 1
    payload[25:27] = bytes.fromhex(peer.replace(":", ""))
    payload[27] = status
    struct.pack_into("<H", payload, 29, distance)
    payload[44] = 161  # -80.5 dBm, unsigned Q7.1
    return bytes(payload)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.events, self.writes, self.resets = [], [], []
        self.worker = AnchorWorker("00:01", self.writes.append, self.events.append,
                                   lambda: self.now, lambda: self.resets.append(self.now))

    def start(self, sid="s1", uwb=123):
        msg = {"action": "start", "session_id": sid, "watch_id": "watch_01",
               "watch_mac": "94:FC", "uwb_session_id": uwb, "remaining_ms": 5000}
        self.worker.command(msg)
        self.worker.feed(response(1, 0, b"\x00" + struct.pack("<I", 987)))
        self.worker.feed(response(1, 3))
        self.worker.feed(response(2, 0))
        return msg

    def test_first_distance_closes_locally_and_exactly_once(self):
        msg = self.start()
        payload = twr(987)
        ntf = bytes((0x62, 0, 0, len(payload))) + payload
        self.worker.feed(ntf[:9])
        self.worker.feed(ntf[9:] + ntf)
        self.assertEqual(sum(e["event"] == "result" for e in self.events), 1)
        self.assertEqual(self.events[0]["distance_cm"], 123)
        self.assertEqual(self.events[0]["rssi_dbm"], -80.5)
        self.assertEqual(self.writes[-1][:2], b"\x22\x01")
        self.worker.feed(response(2, 1))
        self.worker.feed(response(1, 1))
        self.assertIsNone(self.worker.active)
        self.assertEqual(self.events[-1]["event"], "closed")
        self.assertEqual(self.events[-1]["distance_cm"], 123)
        writes = len(self.writes)
        self.worker.command(msg)
        self.assertEqual(len(self.writes), writes)
        self.assertTrue(self.events[-1]["already_closed"])

    def test_wrong_peer_handle_and_failed_measurement_do_not_end_early(self):
        self.start()
        for payload in (twr(988), twr(987, peer="00:00"), twr(987, status=0x21)):
            self.worker.feed(bytes((0x62, 0, 0, len(payload))) + payload)
        self.assertFalse(self.events)
        self.now = 5000
        self.worker.poll()
        self.assertEqual([e["event"] for e in self.events], ["result", "closed"])
        self.assertEqual(self.events[0]["distance_cm"], -1)
        self.assertEqual(self.resets, [5000])

    def test_uci_timeout_resets_and_no_late_response_reopens(self):
        self.worker.command({"action": "start", "session_id": "s1", "watch_id": "w",
                             "watch_mac": "94:FC", "uwb_session_id": 123})
        self.now = 500
        self.worker.poll()
        self.worker.feed(response(1, 0))
        self.assertIsNone(self.worker.active)
        self.assertEqual(len(self.resets), 1)

    def test_stop_before_start_cannot_reopen_closed_attempt(self):
        self.worker.command({"action": "stop", "session_id": "delayed", "watch_id": "w"})
        self.assertEqual(self.events[-1]["event"], "closed")
        self.worker.command({"action": "start", "session_id": "delayed", "watch_id": "w",
                             "watch_mac": "94:FC", "uwb_session_id": 123})
        self.assertIsNone(self.worker.active)
        self.assertFalse(self.writes)
        self.assertTrue(self.events[-1]["already_closed"])
        self.start()
        self.worker.command({"action": "stop", "session_id": "another_delayed", "watch_id": "w"})
        self.assertEqual(self.worker.active["session_id"], "s1")
        self.assertFalse(self.worker.active["closing"])
        self.assertIn("another_delayed", self.worker.closed)

    def test_stale_stop_does_not_stop_current_and_reset_is_idempotent(self):
        self.start()
        self.worker.command({"action": "stop", "session_id": "old"})
        self.assertFalse(self.worker.active["closing"])
        reset = {"action": "reset", "session_id": "boot"}
        self.worker.command(reset)
        self.worker.command(reset)
        self.assertEqual(len(self.resets), 1)
        self.assertEqual(self.events[-1]["event"], "ready")

    def test_config_encoding_matches_bundled_profile_and_parser_bounds(self):
        config = app_config("00:01", "94:FC")
        params = {}
        offset = 1
        for _ in range(config[0]):
            key, size = config[offset:offset + 2]
            params[key] = config[offset + 2:offset + 2 + size]
            offset += size + 2
        self.assertEqual(offset, len(config))
        self.assertEqual(params[0x06], b"\x00\x01")
        self.assertEqual(params[0x07], b"\x94\xfc")
        self.assertEqual(params[0x27] + params[0x28], bytes(range(8, 6, -1)) + bytes(range(1, 7)))
        self.assertEqual(params[0x09], struct.pack("<I", 120))
        self.assertLess(len(config) + 4, 250)
        self.assertIsNone(first_twr(b"", 123, "94:FC"))

    def coordinator(self):
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        config["cycle"]["watches"] = ["watch_01", "watch_02"]
        sent, rows = [], []
        clock = [0.0]
        coordinator = MqttCoordinator(config, CONFIG_PATH, lambda t, b: sent.append((t, b)),
                                      clock=lambda: clock[0], log=lambda *row: rows.append(row))
        coordinator.connected = True
        coordinator.ready_anchors = set(coordinator.boards)
        coordinator.ready_watches = set(coordinator.watches)
        return coordinator, sent, rows, clock

    def test_real_config_positions_and_hardware_normalization(self):
        c, _, rows, _ = self.coordinator()
        self.assertEqual(c.model.positions["00:01"], (.053571, .482684))
        self.assertEqual(c.model.positions["00:02"], (.349838, .166126))
        self.assertEqual(c.model.positions["00:03"], (.055601, .147186))
        c.tick()
        session = next(iter(c.sessions.values()))
        c.handle("uwb/pico/" + session["anchor"] + "/events",
                 {"event": "result", "session_id": session["session_id"],
                  "watch_id": session["watch_id"], "distance_cm": 687, "rssi_dbm": -90.5})
        model = c.model.get(session["watch_id"])
        self.assertAlmostEqual(model._current_slice[session["watch_id"]][session["anchor"]], .687)
        self.assertEqual(rows[0][1], 687)  # Original centimetres remain in the log.
        a, b = c.model.positions["00:01"], c.model.positions["00:02"]
        self.assertAlmostEqual(model._kernel("00:01", "00:02"), math.exp(-math.dist(a, b) / .30))

    def test_close_timeout_survives_stop_retries_and_requires_recovery(self):
        c, sent, rows, clock = self.coordinator()
        c.tick()
        session = next(iter(c.sessions.values()))
        watch, anchor, sid = session["watch_id"], session["anchor"], session["session_id"]
        c.config["cycle"]["enabled"] = False
        c.record(session, -1)
        c.close(session)
        for step in range(1, 10):
            clock[0] = step * .26
            c.tick()
        self.assertNotIn(sid, c.sessions)
        self.assertNotIn(watch, c.by_watch)
        self.assertNotIn(anchor, c.by_anchor)
        self.assertNotIn(watch, c.ready_watches)
        self.assertNotIn(anchor, c.ready_anchors)
        recoveries = list(c.recovery.values())
        for recovery in recoveries:
            if recovery["kind"] == "pico":
                c.handle("uwb/pico/" + anchor + "/events", {"event": "ready", "session_id": recovery["sid"]})
            else:
                c.handle("uwb/session/" + watch, {"state": "stopped", "session_id": recovery["sid"]})
        self.assertIn(watch, c.ready_watches)
        self.assertIn(anchor, c.ready_anchors)

    def test_missing_real_anchor_position_fails_before_any_assignment(self):
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        config["boards"].append({"anchor": "FF:FF"})
        with self.assertRaisesRegex(ValueError, "Faltan posiciones.*FF:FF"):
            MqttCoordinator(config, CONFIG_PATH, lambda *args: None)

    def test_two_watches_reserve_before_publish_and_wait_both_acks(self):
        c, sent, rows, clock = self.coordinator()
        c.tick()
        self.assertEqual(len(c.by_anchor), 2)
        first = next(iter(c.sessions.values()))
        sid, watch, anchor = first["session_id"], first["watch_id"], first["anchor"]
        c.handle("uwb/session/" + watch, {"state": "prepared", "session_id": sid,
                                         "anchor": anchor, "uwb_mac": "94:FC"})
        self.assertTrue(first["started"])
        event = {"event": "result", "session_id": sid, "watch_id": watch,
                 "anchor": anchor, "distance_cm": 123}
        c.handle("uwb/pico/" + anchor + "/events", event)
        c.handle("uwb/pico/" + anchor + "/events", event)
        self.assertEqual(len(rows), 1)
        self.assertEqual(c.model.steps[watch], 1)
        c.handle("uwb/session/" + watch, {"state": "stopped", "session_id": sid})
        self.assertIn(anchor, c.by_anchor)
        c.handle("uwb/pico/" + anchor + "/events", dict(event, event="closed"))
        self.assertNotIn(sid, c.sessions)
        self.assertNotIn(anchor, c.by_anchor)
        c.handle("uwb/pico/" + anchor + "/events", event)
        self.assertEqual(len(rows), 1)

    def test_watch_error_does_not_prove_session_closed(self):
        c, *_ = self.coordinator()
        c.tick()
        session = next(iter(c.sessions.values()))
        sid, watch, anchor = session["session_id"], session["watch_id"], session["anchor"]
        c.handle("uwb/session/" + watch, {"state": "error", "session_id": sid})
        c.handle("uwb/pico/" + anchor + "/events",
                 {"event": "closed", "session_id": sid, "watch_id": watch})
        self.assertIn(sid, c.sessions)
        self.assertFalse(session["watch_closed"])
        c.handle("uwb/session/" + watch, {"state": "stopped", "session_id": sid})
        self.assertNotIn(sid, c.sessions)

    def test_deadline_and_late_measurement_do_not_overwrite_failure(self):
        c, sent, rows, clock = self.coordinator()
        c.tick()
        session = next(iter(c.sessions.values()))
        clock[0] = 5
        c.tick()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row[1] == -1 for row in rows))
        c.handle("uwb/pico/" + session["anchor"] + "/events",
                 {"event": "result", "session_id": session["session_id"],
                  "watch_id": session["watch_id"], "distance_cm": 10})
        self.assertEqual(len(rows), 2)
        c.tick()
        self.assertEqual(len(c.sessions), 2)  # Still reserved, no ACK.

    def test_step_clock_is_independent_of_wall_time_and_other_watch(self):
        c, *_ = self.coordinator()
        for i in range(25):
            c.model.record("watch_01", "00:01", i)
        c.model.record("watch_02", "00:02", -1)
        snapshot = c.model.snapshot()
        self.assertEqual(snapshot["steps"], {"watch_01": 25, "watch_02": 1})
        row1 = [s["watch_01"]["00:01"] for s in snapshot["matrix"]]
        self.assertEqual(row1, [v / 1000 for v in range(10, 25)])
        self.assertEqual(snapshot["matrix"][-1]["watch_02"]["00:02"], 1)
        json.dumps(snapshot, allow_nan=False)

    def test_recovery_does_not_trust_retained_online_status(self):
        c, sent, rows, clock = self.coordinator()
        c.ready_anchors.clear()
        status = {"state": "online", "power": "ON", "protocol": "mqtt_uci_v1"}
        c.handle("uwb/pico/00:01/status", status, retained=True)
        self.assertNotIn("00:01", c.ready_anchors)
        token = sent[-1][1]["session_id"]
        c.handle("uwb/pico/00:01/events", {"event": "ready", "session_id": token})
        self.assertIn("00:01", c.ready_anchors)
        c.ready_anchors.clear()
        c.handle("uwb/pico/00:01/status", status)
        self.assertNotEqual(token, sent[-1][1]["session_id"])

    def test_validated_model_scores_identical_under_event_clock(self):
        c, *_ = self.coordinator()
        actual = c.model.get("w")
        reference = AdaptiveAnchorScheduler(actual.anchors, actual.positions, **c.model.config)
        for step in range(40):
            anchor = actual.anchors[step % len(actual.anchors)]
            distance = -1 if step % 4 == 0 else 100 + step
            c.model.choose("w", [anchor])
            reference.choose("w", [anchor], now=1000 + step)
            c.model.record("w", anchor, distance)
            reference.record("w", anchor, [distance], now=1000 + step)
            self.assertEqual(actual._scores("w", 1000 + step), reference._scores("w", 1000 + step))

    def test_end_to_end_two_watches_real_worker_and_coordinator(self):
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        config["cycle"]["watches"] = ["watch_01", "watch_02"]
        broker, rows, controllers, workers, uart = deque(), [], {}, {}, deque()
        now = [0.0]
        coordinator = MqttCoordinator(config, CONFIG_PATH, lambda t, b: broker.append((t, b)),
                                      clock=lambda: now[0], log=lambda *r: rows.append(r))
        coordinator.connected = True
        def publish_watch(watch, state, sid, **extra):
            body = {"state": state, "session_id": sid, "watch_id": watch}
            body.update(extra)
            broker.append(("uwb/session/" + watch, body))
        for anchor in coordinator.boards:
            workers[anchor] = AnchorWorker(anchor, lambda p, a=anchor: uart.append((a, p)),
                lambda event, a=anchor: broker.append(("uwb/pico/" + a + "/events", event)),
                lambda: int(now[0] * 1000), lambda: None)
            coordinator.handle("uwb/pico/" + anchor + "/status",
                               {"state": "online", "protocol": "mqtt_uci_v1"})
        for watch in coordinator.watches:
            coordinator.handle("uwb/presence/" + watch, {"state": "online"})
        for _ in range(10000):
            now[0] += .005
            coordinator.tick()
            while broker:
                topic, body = broker.popleft()
                parts = topic.split("/")
                sid = body.get("session_id")
                if topic.startswith("uwb/commands/"):
                    watch = parts[2]
                    if body["action"] == "start":
                        self.assertNotIn(watch, controllers)
                        controllers[watch] = sid
                        publish_watch(watch, "prepared", sid, anchor=body["anchor"], uwb_mac="94:FC")
                    elif body["action"] == "reset":
                        controllers.pop(watch, None)
                        publish_watch(watch, "stopped", sid)
                    else:
                        if controllers.get(watch) == sid:
                            controllers.pop(watch)
                        publish_watch(watch, "stopped", sid)
                elif len(parts) == 4 and parts[3] == "commands":
                    workers[parts[2]].command(body)
                else:
                    coordinator.handle(topic, body)
                owners = [s["anchor"] for s in coordinator.sessions.values()]
                self.assertEqual(len(owners), len(set(owners)))
            while uart:
                anchor, command = uart.popleft()
                gid, oid = command[0] & 15, command[1]
                payload = b"\x00"
                if (gid, oid) == (1, 0):
                    payload += command[4:8]  # v2 handle == requested ID in fake DWM.
                workers[anchor].feed(response(gid, oid, payload))
                if (gid, oid) == (2, 0):
                    handle = workers[anchor].active["handle"]
                    data = twr(handle, distance=100 + len(rows))
                    workers[anchor].feed(bytes((0x62, 0, 0, len(data))) + data)
            for worker in workers.values():
                worker.poll()
            if len(rows) >= 200:
                break
        self.assertGreaterEqual(len(rows), 200)
        self.assertEqual(len({row[0]["session_id"] for row in rows}), len(rows))
        self.assertTrue(all(row[1] >= 0 for row in rows))
        self.assertEqual(sum(coordinator.model.steps.values()), len(rows))
        self.assertEqual(set(coordinator.model.steps), {"watch_01", "watch_02"})


if __name__ == "__main__":
    unittest.main()
