"""
watch_bridge_call_backup.py

Escucha "uwb/target_mac" por MQTT y lanza run_fira_twr_backup.py.
El parseo de las mediciones replica la lógica de mqtt_uwb_v2.py.
"""

import argparse
import json
import os
import subprocess
import threading
import time
from datetime import datetime

import paho.mqtt.client as mqtt


def key_file_time() -> str:
    return str(int(time.time() / (60 * 60 * 24)))


def parse_ranging_output(output_text: str):
    """Parsea los bloques igual que mqtt_uwb_v2.py."""
    output = output_text.splitlines()
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
    measurements = []

    for block in blocks:
        status_line = next((line for line in block if "status:" in line), "")

        if "Ok" in status_line:
            dist_line = next((line for line in block if "distance:" in line), "")
            rssi_line = next((line for line in block if "rssi:" in line), "")

            try:
                distance = float(
                    dist_line.split(":")[1].replace("cm", "").strip()
                )
                rssi = float(
                    rssi_line.split(":")[1].replace("dBm", "").strip()
                )
                measurements.append((distance, rssi))
                ok_count += 1
            except Exception as exc:
                print(f"[WARN] Error al procesar bloque válido: {exc}")
        else:
            not_ok_count += 1

    return ok_count, not_ok_count, measurements


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, help="uart:socket://ip:puerto o COMx")
    parser.add_argument(
        "--anchor-hex",
        default="00:01",
        help="El responder que identifica a esta placa en uwb/target_mac",
    )
    parser.add_argument("--my-mac", default="00:01")
    parser.add_argument("--broker", default="192.168.18.3")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--script", default="run_fira_twr_backup.py")
    parser.add_argument("--python-cmd", default="python")
    parser.add_argument(
        "--ranging-time",
        type=int,
        default=3,
        help="Segundos de ranging por cada invocación (-t)",
    )
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()

    os.makedirs(args.data_dir, exist_ok=True)

    lock = threading.Lock()
    busy = {"value": False}
    totals = {"ok": 0, "not_ok": 0}

    def launch_ranging(dest_mac_hex: str):
        with lock:
            if busy["value"]:
                print("[SKIP] Ya hay una invocación en curso, se ignora esta")
                return
            busy["value"] = True

        try:
            cmd = [
                args.python_cmd,
                args.script,
                "-p",
                args.port,
                "-t",
                str(args.ranging_time),
                "--mac",
                args.my_mac,
                "--dest-mac",
                dest_mac_hex,
                "--controlee",
            ]

            print(f"[LANZANDO] {' '.join(cmd)}")
            start_time = time.time()
            result = subprocess.run(cmd, capture_output=True, text=True)
            elapsed = time.time() - start_time

            if result.stderr:
                print("[WARN] STDERR:", result.stderr.strip())

            ok_count, not_ok_count, measurements = parse_ranging_output(
                result.stdout
            )

            totals["ok"] += ok_count
            totals["not_ok"] += not_ok_count

            filename = os.path.join(
                args.data_dir,
                f"{key_file_time()}.1.tsv",
            )

            with open(filename, "a", encoding="utf-8") as datafile:
                if os.stat(filename).st_size == 0:
                    datafile.write(
                        "timestamp\tdest_mac\tdistance_cm\trssi_dbm\n"
                    )

                for distance, rssi in measurements:
                    timestamp = datetime.now().isoformat()
                    datafile.write(
                        f"{timestamp}\t{dest_mac_hex}\t{distance}\t{rssi}\n"
                    )

            print("\n[END] Sesión completada.")
            print(f"[INFO] Archivo: {filename}")
            print(f"[INFO] Destino: {dest_mac_hex}")
            print(f"[INFO] Tiempo: {elapsed:.2f}s")
            print(f"[INFO] Mediciones OK guardadas: {ok_count}")
            print(f"[INFO] Mediciones no OK detectadas: {not_ok_count}")
            print(
                f"[ACUMULADO] OK={totals['ok']}  "
                f"NO_OK={totals['not_ok']}\n"
            )

        except Exception as exc:
            print(f"[ERROR] Fallo al lanzar o procesar el script: {exc}")
        finally:
            with lock:
                busy["value"] = False

    def on_connect(client, userdata, flags, rc):
        print(f"[MQTT] Conectado (rc={rc})")
        client.subscribe("uwb/target_mac")
        print("[MQTT] Suscrito a uwb/target_mac, esperando al reloj...")

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            print("[WARN] Mensaje MQTT no válido")
            return

        responder = payload.get("responder")
        mac = payload.get("mac")

        if responder != args.anchor_hex:
            return

        if not mac or mac in ("--:--", "XX:XX"):
            print(
                f"[AVISO] MAC no válida todavía para "
                f"responder={responder}: {mac}"
            )
            return

        print(f"\n[TARGET] responder={responder} mac={mac}")
        threading.Thread(
            target=launch_ranging,
            args=(mac,),
            daemon=True,
        ).start()

    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.broker, args.mqtt_port, 60)

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print(
            f"\n[INFO] Saliendo. Total OK={totals['ok']}  "
            f"NO_OK={totals['not_ok']}"
        )
        client.disconnect()


if __name__ == "__main__":
    main()