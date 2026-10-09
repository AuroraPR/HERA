"""Suscriptor MQTT de distancias UWB con posicionamiento en vivo."""
import argparse, json, threading, time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
import joblib
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import paho.mqtt.client as mqtt

PAST, FUTURE, WINDOW, PERIOD = 10, 5, 16, 5.0

class LivePosition:
    def __init__(self, model_path, metadata_path, positions_path, image=None, mirror=True):
        self.model = joblib.load(model_path)
        meta = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
        self.anchors = meta["anchors"]
        self.positions = self.read_positions(positions_path)
        self.mirror = mirror
        self.samples = defaultdict(deque)
        self.lock = threading.Lock(); self.last_prediction = None; self.last_time = None
        self.image = image

    @staticmethod
    def read_positions(path):
        import csv
        out = {}
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                a = r["anchor"].replace("_", ":")
                try: out[a] = (float(r["x"]), float(r["y"]))
                except (ValueError, KeyError): pass
        return out

    def on_distance(self, anchor, distance, timestamp=None):
        if anchor not in self.anchors or distance is None or distance < 0: return
        t = timestamp or time.time()
        with self.lock:
            self.samples[anchor].append((t, float(distance)))
            cutoff = t - 30
            while self.samples[anchor] and self.samples[anchor][0][0] < cutoff: self.samples[anchor].popleft()

    def predict(self):
        with self.lock:
            now = time.time(); rows = []
            for k in range(WINDOW):
                t = now - (WINDOW - 1 - k)
                row = []
                for a in self.anchors:
                    vals = [v for ts, v in self.samples[a] if ts <= t and ts >= t - 10]
                    row.append(vals[-1] if vals else -1.0)
                rows.append(row)
            latest = np.full(len(self.anchors), -1.0)
            for j in range(len(self.anchors)):
                vals = [row[j] for row in rows if row[j] != -1]
                if vals: latest[j] = vals[-1]
            if np.all(latest == -1): return None
            pred = self.model.predict(latest.reshape(1, -1))[0]
            self.last_prediction = tuple(map(float, pred)); self.last_time = datetime.now().strftime("%H:%M:%S")
            return self.last_prediction

def main():
    base = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", default="192.168.2.168"); ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--model", type=Path, default=base / "data_casa_javi/resultados_10past_5future/xgboost_sliding_5past_5future.joblib")
    ap.add_argument("--metadata", type=Path, default=base / "data_casa_javi/resultados_10past_5future/model_metadata.json")
    ap.add_argument("--positions", type=Path, default=base / "data/sit_placas.csv")
    ap.add_argument("--image", type=Path, default=base / "plano2.jpg")
    ap.add_argument("--no-mirror", action="store_true", help="No invertir verticalmente las coordenadas")
    args = ap.parse_args()
    live = LivePosition(args.model, args.metadata, args.positions, args.image, mirror=not args.no_mirror)
    client = mqtt.Client(client_id="hera_live_position")
    def connected(c, u, f, rc):
        print(f"MQTT conectado rc={rc} broker={args.broker}:{args.port}")
        c.subscribe("uwb/pico/+/events", qos=1); print("Suscrito a uwb/pico/+/events")
    def message(c, u, msg):
        try:
            body=json.loads(msg.payload); event=body.get("event")
            if event != "result": return
            anchor=msg.topic.split("/")[2]; d=float(body.get("distance_cm", -1))
            live.on_distance(anchor, d, time.time()); print(f"{anchor}: {d:.1f} cm")
        except (ValueError, TypeError, json.JSONDecodeError, IndexError) as e: print(f"Mensaje ignorado: {e}")
    client.on_connect=connected; client.on_message=message; client.connect(args.broker,args.port,60); client.loop_start()
    fig, ax = plt.subplots(figsize=(8,6)); last=[0.0]
    def draw(_):
        if time.time()-last[0] >= PERIOD:
            live.predict(); last[0]=time.time()
        ax.clear();
        if args.image and args.image.exists(): ax.imshow(plt.imread(args.image), extent=(0,1,0,1), alpha=.25, zorder=0)
        for a,(x,y) in live.positions.items():
            yd = 1.0 - y if live.mirror else y; ax.scatter(x,yd,c="black",s=35); ax.text(x,yd,a,fontsize=8)
        if live.last_prediction:
            px, py = live.last_prediction; py = 1.0 - py if live.mirror else py
            ax.scatter(px,py,c="lime",s=130,marker="*",label=f"Última posición {live.last_prediction[0]:.3f}, {live.last_prediction[1]:.3f}")
        ax.set_title(f"Posición UWB en vivo | última predicción: {live.last_time or '--'}"); ax.set_xlabel("x"); ax.set_ylabel("y"); ax.grid(True); ax.legend(loc="best")
    ani=FuncAnimation(fig, draw, interval=500, cache_frame_data=False)
    try: plt.show()
    finally: client.loop_stop(); client.disconnect()

if __name__ == "__main__": main()
