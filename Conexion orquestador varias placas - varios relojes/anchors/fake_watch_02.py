import argparse
import json
import random
import signal
import sys
import threading
import time
from typing import Optional

import paho.mqtt.client as mqtt


class FakeWatch:
    def __init__(
        self,
        broker: str,
        port: int,
        watch_id: str = "watch_02",
        prepared_delay: float = 1.0,
        presence_interval: float = 5.0,
        fixed_uwb_mac: Optional[str] = None,
        cycle_macs: bool = True,
    ) -> None:
        self.broker = broker
        self.port = port
        self.watch_id = watch_id
        self.prepared_delay = prepared_delay
        self.presence_interval = presence_interval
        self.fixed_uwb_mac = fixed_uwb_mac.upper() if fixed_uwb_mac else None
        self.cycle_macs = cycle_macs

        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        self.stop_event = threading.Event()
        self.presence_thread: Optional[threading.Thread] = None

        self.mac_pool = [
            "12:21",
            "12:22",
            "12:23",
            "12:24",
            "12:25",
            "12:26",
        ]
        self.mac_index = 0

        self.active_session_id: Optional[str] = None
        self.active_anchor: Optional[str] = None

    def next_uwb_mac(self) -> str:
        if self.fixed_uwb_mac:
            return self.fixed_uwb_mac

        if self.cycle_macs:
            mac = self.mac_pool[self.mac_index % len(self.mac_pool)]
            self.mac_index += 1
            return mac

        return random.choice(self.mac_pool)

    def publish_json(self, topic: str, payload: dict) -> None:
        raw = json.dumps(payload)
        self.client.publish(topic, raw)
        print(f"[PUB] {topic} -> {raw}")

    def publish_presence(self, state: str = "online") -> None:
        topic = f"uwb/presence/{self.watch_id}"
        payload = {
            "watch_id": self.watch_id,
            "state": state,
            "ts": time.time(),
        }
        self.publish_json(topic, payload)

    def publish_session(self, payload: dict) -> None:
        topic = f"uwb/session/{self.watch_id}"
        payload = {
            "watch_id": self.watch_id,
            **payload,
            "ts": time.time(),
        }
        self.publish_json(topic, payload)

    def presence_loop(self) -> None:
        while not self.stop_event.is_set():
            self.publish_presence("online")
            self.stop_event.wait(self.presence_interval)

    def connect(self) -> None:
        print(f"[MQTT] Conectando a {self.broker}:{self.port} como {self.watch_id}...")
        self.client.connect(self.broker, self.port, 60)

    def on_connect(self, client, userdata, flags, rc):
        print(f"[MQTT] Conectado con código {rc}")
        cmd_topic = f"uwb/commands/{self.watch_id}"
        client.subscribe(cmd_topic)
        print(f"[MQTT] Suscrito a {cmd_topic}")

        self.publish_presence("online")

        if self.presence_thread is None or not self.presence_thread.is_alive():
            self.presence_thread = threading.Thread(target=self.presence_loop, daemon=True)
            self.presence_thread.start()

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        raw_payload = msg.payload.decode(errors="replace").strip()
        print(f"[MQTT] RX {topic} -> {raw_payload}")

        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            print("[WARN] Payload no válido, ignorando")
            return

        action = payload.get("action")
        session_id = payload.get("session_id")
        anchor = (payload.get("anchor") or "").upper()

        if action == "start":
            self.handle_start(session_id=session_id, anchor=anchor)
        elif action == "stop":
            self.handle_stop(session_id=session_id)
        else:
            print(f"[INFO] Acción no soportada: {action}")

    def handle_start(self, session_id: Optional[str], anchor: str) -> None:
        if not session_id:
            print("[WARN] START sin session_id, ignorando")
            return

        self.active_session_id = session_id
        self.active_anchor = anchor or None

        print(
            f"[START] session_id={session_id} anchor={anchor or 'N/A'} "
            f"(responderé prepared en {self.prepared_delay}s)"
        )

        def _delayed_prepared():
            if self.stop_event.is_set():
                return

            uwb_mac = self.next_uwb_mac()
            payload = {
                "session_id": session_id,
                "state": "prepared",
                "anchor": anchor,
                "uwb_mac": uwb_mac,
            }
            self.publish_session(payload)

        timer = threading.Timer(self.prepared_delay, _delayed_prepared)
        timer.daemon = True
        timer.start()

    def handle_stop(self, session_id: Optional[str]) -> None:
        print(f"[STOP] session_id={session_id}")

        payload = {
            "session_id": session_id,
            "state": "stopped",
            "anchor": self.active_anchor,
        }
        self.publish_session(payload)

        if session_id == self.active_session_id:
            self.active_session_id = None
            self.active_anchor = None

    def shutdown(self) -> None:
        print("[INFO] Cerrando simulador...")
        self.stop_event.set()
        try:
            self.publish_presence("offline")
        except Exception:
            pass
        try:
            self.client.disconnect()
        except Exception:
            pass

    def loop_forever(self) -> None:
        self.client.loop_start()
        try:
            while not self.stop_event.is_set():
                time.sleep(0.2)
        finally:
            self.client.loop_stop()


def parse_args():
    parser = argparse.ArgumentParser(description="Simulador MQTT de reloj Wear para pruebas de orquestación UWB")
    parser.add_argument("--broker", default="127.0.0.1", help="IP o host del broker MQTT")
    parser.add_argument("--port", type=int, default=1883, help="Puerto MQTT")
    parser.add_argument("--watch-id", default="watch_02", help="ID lógico del reloj simulado")
    parser.add_argument("--prepared-delay", type=float, default=1.0, help="Retardo antes de publicar prepared")
    parser.add_argument("--presence-interval", type=float, default=5.0, help="Intervalo de publicación de presence")
    parser.add_argument("--uwb-mac", default=None, help="MAC UWB fija del reloj simulado, por ejemplo 12:34")
    parser.add_argument(
        "--random-mac",
        action="store_true",
        help="Usa una MAC aleatoria del pool en cada sesión en lugar de ir rotando",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    sim = FakeWatch(
        broker=args.broker,
        port=args.port,
        watch_id=args.watch_id,
        prepared_delay=args.prepared_delay,
        presence_interval=args.presence_interval,
        fixed_uwb_mac=args.uwb_mac,
        cycle_macs=not args.random_mac,
    )

    def _handle_signal(signum, frame):
        sim.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        sim.connect()
        sim.loop_forever()
    except KeyboardInterrupt:
        sim.shutdown()
    except Exception as exc:
        print(f"[ERROR] {exc}")
        sim.shutdown()
        sys.exit(1)


if __name__ == "__main__":
    main()
