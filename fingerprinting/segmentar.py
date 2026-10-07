"""Segmentación UWB por escena, sin dependencias externas."""
import argparse
import bisect
import csv
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median


def leer_escena(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        clicks = sorted((datetime.fromisoformat(r["timestamp"]), float(r["x"]), float(r["y"]))
                        for r in csv.DictReader(f))
    if len(clicks) < 2 or any(not math.isfinite(v) for _, x, y in clicks for v in (x, y)):
        raise ValueError(f"{path}: se necesitan al menos dos clicks válidos")
    if any(a[0] == b[0] for a, b in zip(clicks, clicks[1:])):
        raise ValueError(f"{path}: timestamps de clicks duplicados")
    return clicks


def interpolar(clicks, times, timestamp):
    i = min(max(bisect.bisect_right(times, timestamp) - 1, 0), len(clicks) - 2)
    a, b = clicks[i:i + 2]
    fraction = (timestamp - a[0]) / (b[0] - a[0])
    return tuple(a[j] + fraction * (b[j] - a[j]) for j in (1, 2))


def timestamp_segundo(timestamp):
    rounded = (timestamp + timedelta(microseconds=500_000)).replace(microsecond=0)
    return rounded.isoformat(timespec="seconds") + ".0000"


def segmentar(uwb, data_dir, output, patron_escenas="escena_*.csv"):
    paths = sorted(data_dir.glob(patron_escenas), key=lambda p: [int(s) if s.isdigit() else s for s in re.split(r"(\d+)", p.stem)])
    if not paths:
        raise ValueError(f"No hay ficheros {patron_escenas}")
    scenes = [(p.stem, leer_escena(p)) for p in paths]
    scenes.sort(key=lambda s: s[1][0][0])
    anchors = set()
    buckets = defaultdict(list)
    metrics = ("distance_cm", "rssi_dbm")
    with uwb.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            anchor = r["anchor"].strip()
            if not anchor:
                continue
            anchors.add(anchor)
            timestamp = datetime.fromisoformat(r["timestamp"])
            for label, clicks in scenes:
                start, end = clicks[0][0], clicks[-1][0]
                if start <= timestamp <= end:
                    count = math.ceil((end - start).total_seconds())
                    second = min(int((timestamp - start).total_seconds()), count - 1)
                    for metric in metrics:
                        try:
                            value = float(r[metric])
                        except (ValueError, TypeError):
                            continue
                        if math.isfinite(value) and value != -1 and (metric != "distance_cm" or value >= 0):
                            buckets[label, second, anchor, metric].append(value)
    anchors = sorted(anchors)
    if not anchors:
        raise ValueError("El fichero UWB no contiene anchors")
    fields = ["label", "timestamp_inicio", "timestamp_fin", "x", "y"]
    fields += [f"anchor_{anchor}_{metric}_{stat}" for anchor in anchors for metric in metrics for stat in ("median", "min", "max")]
    fields.append("data")
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(fields)
        for label, clicks in scenes:
            times = [c[0] for c in clicks]
            count = math.ceil((times[-1] - times[0]).total_seconds())
            for second in range(count):
                start = times[0] + timedelta(seconds=second)
                end = min(start + timedelta(seconds=1), times[-1])
                x, y = interpolar(clicks, times, start + (end - start) / 2)
                row = [label, timestamp_segundo(start), timestamp_segundo(end), x, y]
                for anchor in anchors:
                    for metric in metrics:
                        values = buckets[label, second, anchor, metric]
                        row.extend((median(values), min(values), max(values)) if values else (-1, -1, -1))
                status = "NO-data" if all(value == -1 for value in row[5:]) else "data"
                row[3:] = [f"{value:.4f}" for value in row[3:]]
                row.append(status)
                writer.writerow(row)
                rows += 1
    print(f"{rows} segmentos; {len(anchors)} anchors: {', '.join(anchors)}; salida: {output}")


if __name__ == "__main__":
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uwb", type=Path, default=base / "data" / "20733.watch_01 (1).tsv")
    parser.add_argument("--escenas", type=Path, default=base / "data")
    parser.add_argument("--salida", type=Path, default=base / "segmentacion.csv")
    parser.add_argument("--patron-escenas", default="escena_*.csv")
    args = parser.parse_args()
    segmentar(args.uwb, args.escenas, args.salida, args.patron_escenas)
