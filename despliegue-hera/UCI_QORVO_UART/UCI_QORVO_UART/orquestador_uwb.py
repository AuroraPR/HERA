import argparse
import json
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime

import paho.mqtt.client as mqtt


class UwbOrchestrator:
    def __init__(
        self,
        broker: str,
        port: int,
        serial_port: str,
        my_mac: str,
        watch_id: str,
        anchor: str,
        ranging_time: int = 10,
    ) -> None:
        self.broker = broker
        self.port = port
        self.serial_port = serial_port
        self.my_mac = my_mac.upper()
        self.watch_id = watch_id
        self.anchor = anchor.upper()
        self.ranging_time = ranging_time

        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        self.lock = threading.Lock()

        self.watches: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.active_session_id: str | None = None
        self.ranging_thread: threading.Thread | None = None

    def key_file_time(self) -> str:
        return str(int(time.time() / (60 * 60 * 24)))

    def connect(self) -> None:
        print(f"[MQTT] Conectando a {self.broker}:{self.port}...")
        self.client.connect(self.broker, self.port, 60)

    def loop_forever(self) -> None:
        self.client.loop_forever()

    def on_connect(self, client, userdata, flags, rc):
        print(f"[MQTT] Conectado con código {rc}")
        client.subscribe("uwb/presence/+")
        client.subscribe("uwb/session/+")
        print("[MQTT] Suscrito a uwb/presence/+")
        print("[MQTT] Suscrito a uwb/session/+")
        time.sleep(1)
        self.start_session(self.watch_id, self.anchor)

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        raw_payload = msg.payload.decode(errors="replace").strip()

        print(f"[MQTT] Mensaje en {topic}: {raw_payload}")

        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            print("[WARN] Payload no es JSON válido, ignorando")
            return

        if topic.startswith("uwb/presence/"):
            self.handle_presence(topic, payload)
        elif topic.startswith("uwb/session/"):
            self.handle_session_update(topic, payload)

    def handle_presence(self, topic: str, payload: dict) -> None:
        watch_id = payload.get("watch_id") or topic.split("/")[-1]
        state = payload.get("state", "unknown")

        with self.lock:
            watch = self.watches.setdefault(watch_id, {})
            watch["online"] = state == "online"
            watch["presence_state"] = state
            watch["last_presence"] = time.time()

        print(f"[STATE] {watch_id}: online={state == 'online'} state={state}")

    def handle_session_update(self, topic: str, payload: dict) -> None:
        watch_id = payload.get("watch_id") or topic.split("/")[-1]
        state = payload.get("state")
        uwb_mac = payload.get("uwb_mac")
        anchor = payload.get("anchor")
        session_id = payload.get("session_id")

        with self.lock:
            watch = self.watches.setdefault(watch_id, {})
            watch["last_session_update"] = time.time()
            if uwb_mac:
                watch["uwb_mac"] = uwb_mac.upper()
            if anchor:
                watch["current_anchor"] = anchor.upper()
            if state:
                watch["session_state"] = state
            if session_id:
                watch["session_id"] = session_id

            active_session_id = self.active_session_id
            active_session = self.sessions.get(active_session_id) if active_session_id else None

        if not active_session:
            print("[INFO] No hay sesión activa esperando preparación")
            return

        # Intentamos correlacionar primero por session_id
        matches_active_session = False
        if session_id and session_id == active_session["session_id"]:
            matches_active_session = True
        elif (
            not session_id
            and watch_id == active_session["watch_id"]
            and (anchor or "").upper() == active_session["anchor"]
        ):
            # Fallback por si el reloj aún no publica session_id
            matches_active_session = True
            print("[WARN] Mensaje sin session_id: correlación por watch_id + anchor")

        if not matches_active_session:
            print("[INFO] Update de sesión ignorado: no corresponde a la sesión activa")
            return

        if state != "prepared":
            print(f"[INFO] Estado de sesión recibido, pero no es prepared: {state}")
            return

        if not uwb_mac:
            print("[WARN] Sesión preparada sin uwb_mac")
            return

        with self.lock:
            self.sessions[active_session["session_id"]]["watch_mac"] = uwb_mac.upper()
            self.sessions[active_session["session_id"]]["status"] = "prepared"
            self.sessions[active_session["session_id"]]["updated_at"] = time.time()

        print(
            f"[SESSION] {active_session['session_id']} preparada: "
            f"watch={watch_id} anchor={active_session['anchor']} uwb_mac={uwb_mac.upper()}"
        )

        self.launch_ranging_if_needed(active_session["session_id"])

    def start_session(self, watch_id: str, anchor: str) -> str | None:
        with self.lock:
            if self.active_session_id is not None:
                print(f"[WARN] Ya hay una sesión activa: {self.active_session_id}")
                return None

            session_id = f"sess_{uuid.uuid4().hex[:8]}"
            session = {
                "session_id": session_id,
                "watch_id": watch_id,
                "anchor": anchor.upper(),
                "status": "waiting_watch_prepare",
                "watch_mac": None,
                "created_at": time.time(),
                "updated_at": time.time(),
            }
            self.sessions[session_id] = session
            self.active_session_id = session_id

        command_topic = f"uwb/commands/{watch_id}"
        payload = {
            "action": "start",
            "anchor": anchor.upper(),
            "session_id": session_id,
        }

        self.client.publish(command_topic, json.dumps(payload))
        print(f"[SESSION] Creada {session_id}")
        print(f"[MQTT] Publicado en {command_topic}: {payload}")
        return session_id

    def launch_ranging_if_needed(self, session_id: str) -> None:
        with self.lock:
            session = self.sessions.get(session_id)
            if not session:
                print(f"[WARN] No existe la sesión {session_id}")
                return

            if session["status"] != "prepared":
                print(f"[INFO] La sesión {session_id} todavía no está preparada")
                return

            if self.ranging_thread and self.ranging_thread.is_alive():
                print("[WARN] Ya hay un ranging en ejecución")
                return

            session["status"] = "ranging_started"
            watch_mac = session["watch_mac"]
            anchor = session["anchor"]

        self.ranging_thread = threading.Thread(
            target=self.run_ranging_session,
            args=(session_id, watch_mac, anchor),
            daemon=True,
        )
        self.ranging_thread.start()

    def run_ranging_session(self, session_id: str, dest_mac: str, anchor: str) -> None:
        print(f"[RANGING] Lanzando sesión {session_id} hacia {dest_mac} con anchor {anchor}")

        cmd = [
            "python",
            "run_fira_twr_backup.py",
            "-p",
            self.serial_port,
            "-t",
            str(self.ranging_time),
            "--mac",
            self.my_mac,
            "--dest-mac",
            dest_mac,
            "--controlee",
        ]

        try:
            os.makedirs("data", exist_ok=True)
            filename = f"data/{self.key_file_time()}.1.tsv"

            result = subprocess.run(cmd, capture_output=True, text=True)

            output = result.stdout.splitlines()
            if result.stderr:
                print("[WARN] STDERR:", result.stderr.strip())

            blocks = []
            current_block = []

            for line in output:
                line = line.strip()

                if line.startswith("# Ranging Data:"):
                    if current_block:
                        blocks.append(current_block)
                    current_block = [line]
                elif current_block:
                    current_block.append(line)

            if current_block:
                blocks.append(current_block)

            ok_count = 0
            not_ok_count = 0

            with open(filename, "a", encoding="utf-8") as datafile:
                if os.stat(filename).st_size == 0:
                    datafile.write(
                        "timestamp\tsession_id\twatch_id\tanchor\twatch_mac\tdistance_cm\trssi_dbm\n"
                    )

                for block in blocks:
                    status_line = next((l for l in block if "status:" in l), "")
                    if "Ok" in status_line:
                        dist_line = next((l for l in block if "distance:" in l), "")
                        rssi_line = next((l for l in block if "rssi:" in l), "")

                        try:
                            distance = float(
                                dist_line.split(":")[1].replace("cm", "").strip()
                            )
                            rssi = float(
                                rssi_line.split(":")[1].replace("dBm", "").strip()
                            )
                            timestamp = datetime.now().isoformat()

                            datafile.write(
                                f"{timestamp}\t{session_id}\t{self.watch_id}\t{anchor}\t"
                                f"{dest_mac}\t{distance}\t{rssi}\n"
                            )
                            ok_count += 1
                        except Exception as exc:
                            print(f"[WARN] Error al procesar bloque válido: {exc}")
                    else:
                        not_ok_count += 1

            print(f"\n[END] Sesión completada: {session_id}")
            print(f"[INFO] Archivo: {filename}")
            print(f"[INFO] Mediciones OK guardadas: {ok_count}")
            print(f"[INFO] Mediciones no OK detectadas: {not_ok_count}\n")

            stop_topic = f"uwb/commands/{self.watch_id}"
            stop_payload = {"action": "stop", "session_id": session_id}
            self.client.publish(stop_topic, json.dumps(stop_payload))
            print(f"[MQTT] Publicado STOP en {stop_topic}: {stop_payload}")

            with self.lock:
                if session_id in self.sessions:
                    self.sessions[session_id]["status"] = "completed"
                    self.sessions[session_id]["updated_at"] = time.time()
                if self.active_session_id == session_id:
                    self.active_session_id = None

        except Exception as exc:
            print(f"[ERROR] Error ejecutando ranging para {session_id}: {exc}")
            with self.lock:
                if session_id in self.sessions:
                    self.sessions[session_id]["status"] = "error"
                    self.sessions[session_id]["updated_at"] = time.time()
                if self.active_session_id == session_id:
                    self.active_session_id = None


def parse_args():
    parser = argparse.ArgumentParser(description="Orquestador MQTT-UWB para Raspberry")
    parser.add_argument("--broker", default="192.168.18.22", help="Broker MQTT")
    parser.add_argument("--port", type=int, default=1883, help="Puerto MQTT")
    parser.add_argument("--serial-port", default="/dev/ttyACM0", help="Puerto serie de la placa")
    parser.add_argument("--my-mac", default="00:01", help="MAC local de la placa")
    parser.add_argument("--watch-id", default="watch_01", help="ID lógico del reloj")
    parser.add_argument("--anchor", default="00:01", help="Anchor/placa que ejecuta ranging")
    parser.add_argument("--time", type=int, default=10, help="Tiempo de ranging en segundos")
    return parser.parse_args()


def main():
    args = parse_args()

    orchestrator = UwbOrchestrator(
        broker=args.broker,
        port=args.port,
        serial_port=args.serial_port,
        my_mac=args.my_mac,
        watch_id=args.watch_id,
        anchor=args.anchor,
        ranging_time=args.time,
    )

    try:
        orchestrator.connect()
        orchestrator.loop_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Saliendo por teclado")
    except Exception as exc:
        print(f"[ERROR] No se pudo arrancar el orquestador: {exc}")


if __name__ == "__main__":
    main()