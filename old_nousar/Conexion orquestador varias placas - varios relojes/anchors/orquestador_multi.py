import argparse
import json
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt


class UwbOrchestratorMulti:
    def __init__(
        self,
        broker: str,
        port: int,
        boards: list[dict[str, Any]],
        ranging_time: int = 10,
        prepare_timeout: int = 15,
        data_dir: str = "data",
        run_script: str = "run_fira_twr.py",
        python_cmd: str = "python",
        round_robin_mode: str = "strict",
    ) -> None:
        self.broker = broker
        self.port = port
        self.ranging_time = ranging_time
        self.prepare_timeout = prepare_timeout
        self.data_dir = Path(data_dir)
        self.run_script = run_script
        self.python_cmd = python_cmd

        self.round_robin_mode = round_robin_mode.lower().strip()
        if self.round_robin_mode not in {"strict", "flexible"}:
            raise ValueError("round_robin_mode debe ser 'strict' o 'flexible'")

        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        self.lock = threading.RLock()

        self.boards: dict[str, dict[str, Any]] = {}
        for board in boards:
            anchor = str(board["anchor"]).upper()
            self.boards[anchor] = {
                "anchor": anchor,
                "my_mac": str(board["my_mac"]).upper(),
                "serial_port": str(board["serial_port"]),
                "label": str(board.get("label", anchor)),
            }

        self.anchor_order: list[str] = list(self.boards.keys())
        self.next_anchor_index_by_watch: dict[str, int] = {}

        self.watches: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}

        self.active_sessions_by_watch: dict[str, str] = {}
        self.active_sessions_by_anchor: dict[str, str] = {}
        self.ranging_threads_by_anchor: dict[str, threading.Thread] = {}
        self.prepare_timers_by_session: dict[str, threading.Timer] = {}

        self.scheduler_thread: threading.Thread | None = None
        self.scheduler_stop_event = threading.Event()

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
        print(f"[INFO] Orquestador listo | round_robin_mode={self.round_robin_mode}")

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
        anchor = (payload.get("anchor") or "").upper()
        session_id = payload.get("session_id")

        with self.lock:
            watch = self.watches.setdefault(watch_id, {})
            watch["last_session_update"] = time.time()
            if uwb_mac:
                watch["uwb_mac"] = uwb_mac.upper()
            if anchor:
                watch["current_anchor"] = anchor
            if state:
                watch["session_state"] = state
            if session_id:
                watch["session_id"] = session_id

            active_session_id = self.active_sessions_by_watch.get(watch_id)
            active_session = self.sessions.get(active_session_id) if active_session_id else None

        if not active_session:
            print(f"[INFO] No hay sesión activa para {watch_id}")
            return

        matches_active_session = False
        if session_id and session_id == active_session["session_id"]:
            matches_active_session = True
        elif (
            not session_id
            and watch_id == active_session["watch_id"]
            and anchor == active_session["anchor"]
        ):
            matches_active_session = True
            print("[WARN] Mensaje sin session_id: correlación por watch_id + anchor")

        if not matches_active_session:
            print("[INFO] Update de sesión ignorado: no corresponde a la sesión activa del reloj")
            return

        if state != "prepared":
            print(f"[INFO] Estado de sesión recibido, pero no es prepared: {state}")
            return

        if not uwb_mac:
            print("[WARN] Sesión preparada sin uwb_mac")
            return

        self.cancel_prepare_timer(active_session["session_id"])

        with self.lock:
            active_session["watch_mac"] = uwb_mac.upper()
            active_session["status"] = "prepared"
            active_session["updated_at"] = time.time()

        print(
            f"[SESSION] {active_session['session_id']} preparada: "
            f"watch={watch_id} anchor={active_session['anchor']} uwb_mac={uwb_mac.upper()}"
        )

        self.launch_ranging_if_needed(active_session["session_id"])

    def get_free_anchor(self) -> str | None:
        with self.lock:
            busy_anchors = set(self.active_sessions_by_anchor.keys())
            for anchor in self.boards:
                if anchor not in busy_anchors:
                    return anchor
        return None

    def is_watch_free(self, watch_id: str) -> bool:
        with self.lock:
            return watch_id not in self.active_sessions_by_watch

    def get_next_anchor_for_watch_strict(self, watch_id: str) -> str | None:
        with self.lock:
            if not self.anchor_order:
                return None

            idx = self.next_anchor_index_by_watch.get(watch_id, 0)
            anchor = self.anchor_order[idx]

            if anchor in self.active_sessions_by_anchor:
                return None

            self.next_anchor_index_by_watch[watch_id] = (idx + 1) % len(self.anchor_order)
            return anchor

    def get_next_anchor_for_watch_flexible(self, watch_id: str) -> str | None:
        with self.lock:
            if not self.anchor_order:
                return None

            start_idx = self.next_anchor_index_by_watch.get(watch_id, 0)
            total = len(self.anchor_order)

            for offset in range(total):
                idx = (start_idx + offset) % total
                anchor = self.anchor_order[idx]

                if anchor not in self.active_sessions_by_anchor:
                    self.next_anchor_index_by_watch[watch_id] = (idx + 1) % total
                    return anchor

            return None

    def get_next_anchor_for_watch(self, watch_id: str) -> str | None:
        if self.round_robin_mode == "strict":
            return self.get_next_anchor_for_watch_strict(watch_id)
        return self.get_next_anchor_for_watch_flexible(watch_id)

    def start_session(self, watch_id: str, preferred_anchor: str | None = None) -> str | None:
        preferred_anchor = preferred_anchor.upper() if preferred_anchor else None

        with self.lock:
            if watch_id in self.active_sessions_by_watch:
                print(f"[WARN] El reloj {watch_id} ya tiene una sesión activa")
                return None

            anchor = preferred_anchor or self.get_free_anchor()
            if not anchor:
                print(f"[WARN] No hay placas libres para iniciar sesión con {watch_id}")
                return None

            if anchor not in self.boards:
                print(f"[WARN] Anchor desconocido: {anchor}")
                return None

            if anchor in self.active_sessions_by_anchor:
                print(f"[WARN] La placa {anchor} ya tiene una sesión activa")
                return None

            session_id = f"sess_{uuid.uuid4().hex[:8]}"
            board = self.boards[anchor]
            session = {
                "session_id": session_id,
                "watch_id": watch_id,
                "anchor": anchor,
                "board_label": board["label"],
                "board_mac": board["my_mac"],
                "serial_port": board["serial_port"],
                "status": "waiting_watch_prepare",
                "watch_mac": None,
                "created_at": time.time(),
                "updated_at": time.time(),
            }
            self.sessions[session_id] = session
            self.active_sessions_by_watch[watch_id] = session_id
            self.active_sessions_by_anchor[anchor] = session_id

        command_topic = f"uwb/commands/{watch_id}"
        payload = {
            "action": "start",
            "anchor": anchor,
            "session_id": session_id,
        }

        self.client.publish(command_topic, json.dumps(payload))
        print(f"[SESSION] Creada {session_id} para {watch_id} usando {anchor}")
        print(f"[MQTT] Publicado en {command_topic}: {payload}")

        timer = threading.Timer(self.prepare_timeout, self.handle_prepare_timeout, args=(session_id,))
        timer.daemon = True
        with self.lock:
            self.prepare_timers_by_session[session_id] = timer
        timer.start()

        return session_id

    def handle_prepare_timeout(self, session_id: str) -> None:
        with self.lock:
            session = self.sessions.get(session_id)
            if not session:
                return
            if session["status"] != "waiting_watch_prepare":
                return

            session["status"] = "prepare_timeout"
            session["updated_at"] = time.time()
            watch_id = session["watch_id"]
            anchor = session["anchor"]

        print(f"[TIMEOUT] La sesión {session_id} no llegó a prepared en {self.prepare_timeout}s")
        self.publish_stop(watch_id, session_id)
        self.release_session_resources(session_id, watch_id, anchor)

    def cancel_prepare_timer(self, session_id: str) -> None:
        with self.lock:
            timer = self.prepare_timers_by_session.pop(session_id, None)
        if timer:
            timer.cancel()

    def launch_ranging_if_needed(self, session_id: str) -> None:
        with self.lock:
            session = self.sessions.get(session_id)
            if not session:
                print(f"[WARN] No existe la sesión {session_id}")
                return

            if session["status"] != "prepared":
                print(f"[INFO] La sesión {session_id} todavía no está preparada")
                return

            anchor = session["anchor"]
            existing_thread = self.ranging_threads_by_anchor.get(anchor)
            if existing_thread and existing_thread.is_alive():
                print(f"[WARN] Ya hay un ranging en ejecución en la placa {anchor}")
                return

            session["status"] = "ranging_started"
            watch_mac = session["watch_mac"]

        thread = threading.Thread(
            target=self.run_ranging_session,
            args=(session_id, watch_mac),
            daemon=True,
        )
        with self.lock:
            self.ranging_threads_by_anchor[anchor] = thread
        thread.start()

    def run_ranging_session(self, session_id: str, dest_mac: str) -> None:
        with self.lock:
            session = self.sessions.get(session_id)
            if not session:
                print(f"[WARN] La sesión {session_id} ya no existe")
                return
            watch_id = session["watch_id"]
            anchor = session["anchor"]
            serial_port = session["serial_port"]
            board_mac = session["board_mac"]

        print(f"[RANGING] Lanzando sesión {session_id} hacia {dest_mac} con anchor {anchor}")

        cmd = [
            self.python_cmd,
            self.run_script,
            "-p",
            serial_port,
            "-t",
            str(self.ranging_time),
            "--mac",
            board_mac,
            "--dest-mac",
            dest_mac,
            "--controlee",
        ]

        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            filename = self.data_dir / f"{self.key_file_time()}.{watch_id}.tsv"

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
                if filename.stat().st_size == 0:
                    datafile.write(
                        "timestamp\tsession_id\twatch_id\tanchor\tboard_mac\tserial_port\twatch_mac\tdistance_cm\trssi_dbm\n"
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
                                f"{timestamp}\t{session_id}\t{watch_id}\t{anchor}\t{board_mac}\t{serial_port}\t"
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

            self.publish_stop(watch_id, session_id)

            with self.lock:
                if session_id in self.sessions:
                    self.sessions[session_id]["status"] = "completed"
                    self.sessions[session_id]["updated_at"] = time.time()

        except Exception as exc:
            print(f"[ERROR] Error ejecutando ranging para {session_id}: {exc}")
            with self.lock:
                if session_id in self.sessions:
                    self.sessions[session_id]["status"] = "error"
                    self.sessions[session_id]["updated_at"] = time.time()
        finally:
            self.release_session_resources(session_id, watch_id, anchor)
            with self.lock:
                self.ranging_threads_by_anchor.pop(anchor, None)

    def publish_stop(self, watch_id: str, session_id: str) -> None:
        stop_topic = f"uwb/commands/{watch_id}"
        stop_payload = {"action": "stop", "session_id": session_id}
        self.client.publish(stop_topic, json.dumps(stop_payload))
        print(f"[MQTT] Publicado STOP en {stop_topic}: {stop_payload}")

    def release_session_resources(self, session_id: str, watch_id: str, anchor: str) -> None:
        self.cancel_prepare_timer(session_id)
        with self.lock:
            if self.active_sessions_by_watch.get(watch_id) == session_id:
                self.active_sessions_by_watch.pop(watch_id, None)
            if self.active_sessions_by_anchor.get(anchor) == session_id:
                self.active_sessions_by_anchor.pop(anchor, None)

    def start_cycle_scheduler(
        self,
        watches: list[str],
        loop_interval_seconds: float = 1.0,
        launch_stagger_seconds: float = 0.3,
    ) -> None:
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            print("[WARN] El scheduler cíclico ya está en ejecución")
            return

        self.scheduler_stop_event.clear()

        def _scheduler_loop():
            print("[SCHED] Scheduler cíclico iniciado")
            while not self.scheduler_stop_event.is_set():
                for watch_id in watches:
                    if self.scheduler_stop_event.is_set():
                        break

                    with self.lock:
                        watch_busy = watch_id in self.active_sessions_by_watch

                    if watch_busy:
                        free_anchor = None
                    else:
                        free_anchor = self.get_next_anchor_for_watch(watch_id)

                    started = False
                    if not watch_busy and free_anchor:
                        session_id = self.start_session(watch_id, free_anchor)
                        if session_id:
                            started = True

                    if started and launch_stagger_seconds > 0:
                        time.sleep(launch_stagger_seconds)

                if loop_interval_seconds > 0:
                    time.sleep(loop_interval_seconds)

            print("[SCHED] Scheduler cíclico detenido")

        self.scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
        self.scheduler_thread.start()

    def stop_cycle_scheduler(self) -> None:
        self.scheduler_stop_event.set()

    def status_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "boards": self.boards,
                "watches": self.watches,
                "active_sessions_by_watch": self.active_sessions_by_watch,
                "active_sessions_by_anchor": self.active_sessions_by_anchor,
                "sessions": self.sessions,
                "round_robin_mode": self.round_robin_mode,
                "next_anchor_index_by_watch": self.next_anchor_index_by_watch,
            }


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="Orquestador MQTT-UWB multi reloj / multi placa")
    parser.add_argument(
        "--config",
        default="orquestador_config.json",
        help="Archivo JSON con toda la configuración del orquestador",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    mqtt_cfg = config.get("mqtt", {})
    broker = mqtt_cfg.get("broker", "192.168.18.22")
    port = int(mqtt_cfg.get("port", 1883))

    orchestrator_cfg = config.get("orchestrator", {})
    ranging_time = int(orchestrator_cfg.get("ranging_time", 10))
    prepare_timeout = int(orchestrator_cfg.get("prepare_timeout", 15))
    data_dir = orchestrator_cfg.get("data_dir", "data")
    run_script = orchestrator_cfg.get("run_script", "run_fira_twr.py")
    python_cmd = orchestrator_cfg.get("python_cmd", "python")
    round_robin_mode = str(orchestrator_cfg.get("round_robin_mode", "strict"))

    boards = config.get("boards", [])
    if not boards:
        raise ValueError("La configuración debe incluir al menos una placa en 'boards'")

    cycle_cfg = config.get("cycle", {})
    cycle_enabled = bool(cycle_cfg.get("enabled", False))
    cycle_watches = cycle_cfg.get("watches", [])
    loop_interval_seconds = float(cycle_cfg.get("loop_interval_seconds", 1.0))
    launch_stagger_seconds = float(cycle_cfg.get("launch_stagger_seconds", 0.3))

    orchestrator = UwbOrchestratorMulti(
        broker=broker,
        port=port,
        boards=boards,
        ranging_time=ranging_time,
        prepare_timeout=prepare_timeout,
        data_dir=data_dir,
        run_script=run_script,
        python_cmd=python_cmd,
        round_robin_mode=round_robin_mode,
    )

    try:
        orchestrator.connect()

        if cycle_enabled:
            if not cycle_watches:
                raise ValueError("Si cycle.enabled=true, debes indicar cycle.watches")
            orchestrator.start_cycle_scheduler(
                watches=cycle_watches,
                loop_interval_seconds=loop_interval_seconds,
                launch_stagger_seconds=launch_stagger_seconds,
            )

        orchestrator.loop_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Saliendo por teclado")
        orchestrator.stop_cycle_scheduler()
    except Exception as exc:
        print(f"[ERROR] No se pudo arrancar el orquestador: {exc}")


if __name__ == "__main__":
    main()