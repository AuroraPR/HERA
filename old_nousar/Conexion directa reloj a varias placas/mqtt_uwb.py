import paho.mqtt.client as mqtt
import subprocess

# --- CONFIGURACIÓN ---
MQTT_BROKER = "192.168.18.22"
MQTT_PORT = 1883
MQTT_TOPIC = "uwb/target_mac"
SERIAL_PORT = "/dev/ttyACM0"  # Cambia esto si tu DWM aparece con otro nombre
MY_MAC = "00:01"              # MAC fija del dispositivo que actúa como respondeur (controlee)

last_mac = None

def on_connect(client, userdata, flags, rc):
    print(f"[MQTT] Conectado al broker con código {rc}")
    client.subscribe(MQTT_TOPIC)

def on_message(client, userdata, msg):
    global last_mac
    dest_mac = msg.payload.decode().strip().upper()
    print(f"[MQTT] MAC recibida: {dest_mac}")

    if dest_mac and dest_mac != last_mac:
        last_mac = dest_mac
        print(f"[MQTT] Cambió la MAC, ejecutando script...")
        run_ranging(dest_mac)

def run_ranging(dest_mac):
    cmd = [
        "python", "run_fira_twr.py",
        "-p", SERIAL_PORT,
        "--mac", MY_MAC,
        "--dest-mac", dest_mac,
	"--node", "onetomany",
        "--controlee"  # Siempre usar controlee
    ]

    try:
        subprocess.run(cmd)
        print(f"[OK] Ranging ejecutado correctamente con {dest_mac}")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Error ejecutando el script: {e}")

def main():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        print(f"[MQTT] Conectando a {MQTT_BROKER}:{MQTT_PORT}")
        client.loop_forever()
    except Exception as e:
        print(f"[ERROR] No se pudo conectar al broker MQTT: {e}")

if __name__ == "__main__":
    main()
