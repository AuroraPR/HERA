# main.py -- Pico 2W
#
# - MQTT commands/results only. UCI runs locally through anchor_uci.py.
# - No TCP listener. First valid distance triggers local stop/deinit.
#
# IMPORTANTE: necesitas subir tambien la libreria umqtt a la Pico.
# La forma mas facil (con la Pico ya conectada a WiFi, desde el REPL o
# Thonny):
#
#     import mip
#     mip.install("umqtt.simple")
#
# Esto la instala en /lib, donde este script ya la puede importar.

import network, machine, time, gc, ujson
from umqtt.simple import MQTTClient
from anchor_uci import AnchorWorker

SSID = "Adetem"
PASSWORD = "JaviAurora"

UART_ID = 0
UART_TX_PIN = 0   # GP0 -> RXD_RPI (pin 10 de J10 del DWM)
UART_RX_PIN = 1   # GP1 <- TXD (pin 8 de J10 del DWM)
BAUDRATE = 115200

POWER_PIN = 2     # GP2 -> Gate del MOSFET que corta/restaura GND del DWM
BATTERY_ADC_PIN = 26   # GP26 (ADC0) -> divisor de voltaje desde B+ de la bateria

# Relacion del divisor de voltaje: con R1=R2=10k, el voltaje en el pin es
# la MITAD del real de la bateria, asi que multiplicamos por 2 al leerlo.
# Si usas otros valores de resistencia, ajusta este factor:
#   factor = (R1 + R2) / R2
DIVIDER_FACTOR = 2.0
ADC_REFERENCE_VOLTAGE = 3.3

# --- Identidad de esta Pico / anchor ---
ANCHOR_ID = "00:01"          # CAMBIAR por placa: "00:01", "00:02", etc.
MQTT_BROKER = "192.168.18.3"
MQTT_PORT = 1883
MQTT_CLIENT_ID = "pico_" + ANCHOR_ID.replace(":", "")

STATUS_TOPIC = f"uwb/pico/{ANCHOR_ID}/status".encode()
COMMANDS_TOPIC = f"uwb/pico/{ANCHOR_ID}/commands".encode()
EVENTS_TOPIC = f"uwb/pico/{ANCHOR_ID}/events".encode()
worker = None
outbox = {}

STATUS_PUBLISH_INTERVAL_S = 10

led = machine.Pin("LED", machine.Pin.OUT)
power_pin = machine.Pin(POWER_PIN, machine.Pin.OUT)
power_pin.value(1)  # arrancamos con el DWM encendido

battery_adc = machine.ADC(BATTERY_ADC_PIN)

# Historial de las ultimas lecturas de voltaje, para detectar si esta
# subiendo de forma sostenida (indicio de que se esta cargando) en vez
# de fiarse de una unica lectura ruidosa.
_battery_history = []
_BATTERY_HISTORY_SIZE = 4
# Si la diferencia entre la lectura mas antigua y la mas reciente del
# historial supera esto, consideramos que esta cargando.
_CHARGING_RISE_THRESHOLD_V = 0.03

boot_time = time.time()

uart = machine.UART(
    UART_ID,
    baudrate=BAUDRATE,
    tx=machine.Pin(UART_TX_PIN),
    rx=machine.Pin(UART_RX_PIN),
)


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(SSID, PASSWORD)
    timeout = 20
    while not wlan.isconnected() and timeout > 0:
        led.toggle()
        time.sleep(0.5)
        timeout -= 1
    if not wlan.isconnected():
        raise RuntimeError("No se pudo conectar al WiFi")
    led.value(1)
    ip = wlan.ifconfig()[0]
    print("Conectado. IP:", ip)
    return ip, wlan


def hard_reset_dwm():
    power_pin.value(0)
    time.sleep_ms(20)
    if uart.any():
        uart.read()
    power_pin.value(1)
    time.sleep_ms(150)


# Tabla de referencia voltaje->porcentaje para una celda Li-ion 18650
# (curva de descarga tipica, no lineal). Se usa interpolacion entre puntos.
_BATTERY_CURVE = [
    (4.20, 100),
    (4.10, 90),
    (4.00, 80),
    (3.90, 70),
    (3.80, 60),
    (3.70, 50),
    (3.60, 40),
    (3.50, 30),
    (3.40, 20),
    (3.30, 10),
    (3.00, 0),
]


def read_battery_voltage() -> float:
    """Lee el ADC y devuelve el voltaje REAL de la bateria (ya
    compensando el divisor de voltaje)."""
    raw = battery_adc.read_u16()  # 0-65535 sobre 0-3.3V
    pin_voltage = (raw / 65535) * ADC_REFERENCE_VOLTAGE
    return pin_voltage * DIVIDER_FACTOR


def voltage_to_percent(voltage: float) -> int:
    """Convierte un voltaje de bateria a un porcentaje aproximado,
    interpolando en la curva de descarga tipica de una 18650."""
    if voltage >= _BATTERY_CURVE[0][0]:
        return 100
    if voltage <= _BATTERY_CURVE[-1][0]:
        return 0

    for i in range(len(_BATTERY_CURVE) - 1):
        v_high, p_high = _BATTERY_CURVE[i]
        v_low, p_low = _BATTERY_CURVE[i + 1]
        if v_low <= voltage <= v_high:
            # Interpolacion lineal entre los dos puntos mas cercanos
            ratio = (voltage - v_low) / (v_high - v_low)
            return int(p_low + ratio * (p_high - p_low))

    return 0


def update_battery_history_and_check_charging(voltage: float) -> bool:
    """Añade la lectura actual al historial y determina si la bateria
    parece estar subiendo de voltaje de forma sostenida (cargando).

    No es 100% preciso (es una heuristica, no una lectura directa del
    pin CHRG del TP4056), pero no requiere tocar el hardware: si el
    voltaje sube de forma consistente en las ultimas lecturas, lo
    tomamos como señal de que hay carga entrando."""
    _battery_history.append(voltage)
    if len(_battery_history) > _BATTERY_HISTORY_SIZE:
        _battery_history.pop(0)

    if len(_battery_history) < _BATTERY_HISTORY_SIZE:
        return False  # todavia no tenemos suficiente historial para decidir

    rise = _battery_history[-1] - _battery_history[0]
    return rise > _CHARGING_RISE_THRESHOLD_V


def build_status_payload(wlan) -> bytes:
    uptime_s = time.time() - boot_time
    try:
        # Se lee en cada publicacion por si el DHCP cambia la IP
        ip = wlan.ifconfig()[0]
    except Exception:
        ip = None
    try:
        rssi = wlan.status("rssi")
    except Exception:
        rssi = None
    gc.collect()
    free_mem = gc.mem_free()
    power_state = "ON" if power_pin.value() == 1 else "OFF"

    try:
        battery_voltage = read_battery_voltage()
        battery_percent = voltage_to_percent(battery_voltage)
        battery_charging = update_battery_history_and_check_charging(battery_voltage)
    except Exception as e:
        print("[BATT] Error leyendo bateria:", e)
        battery_voltage = None
        battery_percent = None
        battery_charging = None

    payload = {
        "anchor": ANCHOR_ID,
        "protocol": "mqtt_uci_v1",
        "state": "online",
        "ip": ip,
        "uptime_s": uptime_s,
        "rssi": rssi,
        "free_mem": free_mem,
        "power": power_state,
        "battery_voltage": round(battery_voltage, 2) if battery_voltage is not None else None,
        "battery_percent": battery_percent,
        "battery_charging": battery_charging,
    }
    return ujson.dumps(payload).encode()


def on_mqtt_message(topic, msg):
    print("[MQTT] Comando recibido:", topic, msg)
    try:
        payload = ujson.loads(msg)
    except Exception:
        print("[MQTT] Payload no es JSON valido, ignorando")
        return

    action = str(payload.get("action", "")).upper()

    if action == "ACK":
        outbox.pop((payload.get("session_id"), payload.get("event")), None)
        return
    if action in ("START", "STOP", "RESET"):
        payload["action"] = action.lower()
        try:
            worker.command(payload)
        except (ValueError, KeyError) as exc:
            print("[UCI] Comando invalido:", exc)
        return

    if worker.active:
        worker.close("power_command")
        worker.finish(reset=True)

    if action == "POWER_OFF":
        power_pin.value(0)
    elif action == "POWER_ON":
        power_pin.value(1)
    elif action == "POWER_CYCLE":
        power_pin.value(0)
        time.sleep(1.0)
        power_pin.value(1)
    else:
        print("[MQTT] Accion desconocida:", action)


def mqtt_control_loop(wlan):
    """Hilo de control: conecta a MQTT, se suscribe a comandos, publica
    estado periodicamente, y registra un Last Will para que el broker
    marque esta Pico como offline si se cae de forma anomala."""
    global worker
    def emit(payload):
        outbox[(payload["session_id"], payload["event"])] = ujson.dumps(payload).encode()
        if len(outbox) > 64:
            raise RuntimeError("MQTT outbox full")
    worker = AnchorWorker(ANCHOR_ID, uart.write, emit, time.ticks_ms, hard_reset_dwm, time.ticks_diff)
    hard_reset_dwm()
    while True:
        try:
            client = MQTTClient(
                MQTT_CLIENT_ID,
                MQTT_BROKER,
                port=MQTT_PORT,
                keepalive=30,
            )

            try:
                last_ip = wlan.ifconfig()[0]
            except Exception:
                last_ip = None
            offline_payload = ujson.dumps({"anchor": ANCHOR_ID, "state": "offline", "ip": last_ip}).encode()
            client.set_last_will(STATUS_TOPIC, offline_payload, retain=True, qos=0)

            client.set_callback(on_mqtt_message)
            client.connect()
            client.subscribe(COMMANDS_TOPIC)
            client.io_timeout = 0.1
            print(f"[MQTT] Conectado a {MQTT_BROKER}:{MQTT_PORT}, suscrito a {COMMANDS_TOPIC}")

            # Publicamos estado "online" inmediatamente al conectar
            client.publish(STATUS_TOPIC, build_status_payload(wlan), retain=True)

            last_status_publish = time.time()
            last_events = time.ticks_ms() - 250

            while True:
                worker.poll()
                client.check_msg()  # no bloqueante, procesa comandos entrantes
                if uart.any():
                    worker.feed(uart.read())
                worker.poll()
                if time.ticks_diff(time.ticks_ms(), last_events) >= 20:
                    for payload in list(outbox.values()):
                        client.publish(EVENTS_TOPIC, payload, retain=False, qos=0)
                    last_events = time.ticks_ms()

                if time.time() - last_status_publish >= STATUS_PUBLISH_INTERVAL_S:
                    client.publish(STATUS_TOPIC, build_status_payload(wlan), retain=True)
                    last_status_publish = time.time()

                time.sleep_ms(2)

        except Exception as e:
            if worker.active:
                worker.close("mqtt_disconnected")
                worker.finish(reset=True)
            print("[MQTT] Error, reconectando en 3s:", e)
            time.sleep(3)


def main():
    ip, wlan = connect_wifi()

    mqtt_control_loop(wlan)


while True:
    try:
        main()
    except Exception as e:
        print("Error, reintentando en 2s:", e)
        time.sleep(2)
