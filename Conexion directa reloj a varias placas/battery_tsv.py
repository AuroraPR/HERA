#!/usr/bin/env python3
import time
import os
import paho.mqtt.client as mqtt
from datetime import datetime

# Ruta al fichero TSV donde guardaremos los datos
TSV_FILE = 'battery_data.tsv'

# Asegurarnos de que el fichero existe y, si está vacío, escribir cabecera
if not os.path.isfile(TSV_FILE) or os.path.getsize(TSV_FILE) == 0:
    with open(TSV_FILE, 'w') as f:
        f.write("timestamp\tbattery_level\n")

def on_message(client, userdata, msg):
    try:
        # Decodifica el nivel de batería
        level = int(msg.payload.decode())
        # Timestamp en segundos
        ts = datetime.now().isoformat()
        # Escribe una línea nueva en el TSV
        with open(TSV_FILE, 'a') as f:
            f.write(f"{ts}\t{level}\n")
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Guardado: {level}%")
    except Exception as e:
        print("Error procesando mensaje:", e)

def main():
    # Configura cliente MQTT
    client = mqtt.Client()
    client.on_message = on_message

    # Conecta al broker (ajusta host/puerto si es necesario)
    client.connect("192.168.18.23", 1883, 60)

    # Suscríbete al topic de batería
    client.subscribe("uwb/battery_level")
    print("Suscrito a uwb/battery_level. Esperando datos...")

    # Bucle de espera
    client.loop_forever()

if __name__ == "__main__":
    main()
