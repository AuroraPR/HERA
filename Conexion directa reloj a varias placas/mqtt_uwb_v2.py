import os
import time
from datetime import datetime
import subprocess

import paho.mqtt.client as mqtt

# --- CONFIGURACIÓN ---
MQTT_BROKER = "192.168.18.22"
MQTT_PORT = 1883
MQTT_TOPIC = "uwb/target_mac"
SERIAL_PORT = "/dev/ttyACM0"
MY_MAC = "00:01"

last_mac = None

def key_file_time():
    return str(int(time.time() / (60 * 60 * 24)))  # días desde Epoch

def on_connect(client, userdata, flags, rc):
    print(f"[MQTT] Conectado al broker con código {rc}")
    client.subscribe(MQTT_TOPIC)

def on_message(client, userdata, msg):
    global last_mac
    dest_mac = msg.payload.decode().strip().upper()
    print(f"[MQTT] MAC recibida: {dest_mac}")

    if dest_mac and dest_mac != last_mac:
        last_mac = dest_mac
        print(f"[MQTT] Cambió la MAC, ejecutando ranging...\n")

        cmd = [
            "python", "run_fira_twr.py",
            "-p", SERIAL_PORT,
            "--mac", MY_MAC,
            "--dest-mac", dest_mac,
            "--controlee"
        ]

        try:
            os.makedirs("data", exist_ok=True)
            filename = f"data/{key_file_time()}.1.tsv"

            result = subprocess.run(cmd, capture_output=True, text=True)

            output = result.stdout.splitlines()
            if result.stderr:
                print("[WARN] STDERR:", result.stderr.strip())

            # Procesar bloques una vez termina el script
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

            with open(filename, "a") as datafile:
                if os.stat(filename).st_size == 0:
                    datafile.write("timestamp\tdistance_cm\trssi_dbm\n")

                for block in blocks:
                    status_line = next((l for l in block if "status:" in l), "")
                    if "Ok" in status_line:
                        dist_line = next((l for l in block if "distance:" in l), "")
                        rssi_line = next((l for l in block if "rssi:" in l), "")
                        try:
                            distance = float(dist_line.split(":")[1].replace("cm", "").strip())
                            rssi = float(rssi_line.split(":")[1].replace("dBm", "").strip())
                            timestamp = datetime.now().isoformat()

                            datafile.write(f"{timestamp}\t{distance}\t{rssi}\n")
                            ok_count += 1
                        except Exception as e:
                            print(f"[WARN] Error al procesar bloque válido: {e}")
                    else:
                        not_ok_count += 1

            print(f"\n[END] Sesión completada.")
            print(f"[INFO] Archivo: {filename}")
            print(f"[INFO] Mediciones OK guardadas: {ok_count}")
            print(f"[INFO] Mediciones no OK detectadas: {not_ok_count}\n")

        except Exception as e:
            print(f"[ERROR] Error ejecutando el script: {e}")

# --- MAIN ---
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

try:
    print(f"[MQTT] Conectando a {MQTT_BROKER}:{MQTT_PORT}...")
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_forever()
except Exception as e:
    print(f"[ERROR] No se pudo conectar al broker MQTT: {e}")
