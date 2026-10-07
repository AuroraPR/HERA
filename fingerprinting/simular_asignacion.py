"""Simulación reproducible del planificador real; no conecta con hardware."""
import argparse
import json
import math
import random
import sys
from collections import Counter
from pathlib import Path
from statistics import mean

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent / "despliegue-hera/UCI_QORVO_UART/UCI_QORVO_UART"))
from adaptive_scheduler import AdaptiveAnchorScheduler, load_anchor_positions


def reflect(value):
    value %= 2
    return value if value <= 1 else 2 - value


def double_coverage_positions(anchors, pair_spacing_m=1.5, scale_m=10):
    """Dos placas por cuadrante y una central; separación configurable."""
    if len(anchors) != 9:
        raise ValueError("La distribución de doble cobertura requiere 9 anchors")
    offset = pair_spacing_m / (2 * scale_m)
    if not 0 < offset < .25:
        raise ValueError("La separación debe ser positiva y menor que medio lado del plano")
    points = [(x + delta, y) for y in (.25, .75) for x in (.25, .75) for delta in (-offset, offset)]
    return dict(zip(sorted(anchors), points + [(.5, .5)]))


def generate_route(seed, seconds, mode):
    rng = random.Random(seed)
    if mode == "random":
        x, y, vx, vy = rng.random(), rng.random(), 0., 0.
        for _ in range(seconds):
            vx = max(-.025, min(.025, .85 * vx + rng.uniform(-.012, .012)))
            vy = max(-.025, min(.025, .85 * vy + rng.uniform(-.012, .012)))
            x, y = reflect(x + vx), reflect(y + vy)
            yield x, y, "random", None
        return
    cores = [(x, y) for y in (.25, .75) for x in (.25, .75)]
    target = rng.randrange(len(cores))
    x, y = cores[target]
    remaining = rng.randint(60, 120)
    phase = "dwell"
    for _ in range(seconds):
        cx, cy = cores[target]
        if phase == "dwell":
            x += .25 * (cx - x) + rng.uniform(-.006, .006)
            y += .25 * (cy - y) + rng.uniform(-.006, .006)
            remaining -= 1
            yield x, y, "dwell", target
            if remaining == 0:
                target = rng.choice([i for i in range(len(cores)) if i != target])
                phase = "travel"
        else:
            distance = math.dist((x, y), (cx, cy))
            if distance <= .02:
                x, y = cx, cy
                phase = "dwell"
                remaining = rng.randint(60, 120)
            else:
                x += .02 * (cx - x) / distance + rng.uniform(-.001, .001)
                y += .02 * (cy - y) / distance + rng.uniform(-.001, .001)
            yield x, y, "travel", target


def simulate(seed, seconds, config, positions, policy="adaptive", scale_m=10, connection_radius_m=2.5, movement="random"):
    anchors = sorted(positions)
    settings = dict(config)
    settings["proximity_weight"] = settings.pop("w_cercania", 1.0)
    settings["fairness_weight"] = settings.pop("w_equidad", 1.0)
    if policy == "previous":
        settings["history_aggregation"] = "per_anchor"
        settings["evidence_transform"] = "sigmoid"
    elif policy == "power":
        settings["evidence_transform"] = "power"
        settings["evidence_power"] = 0.25
    scheduler = AdaptiveAnchorScheduler(anchors, positions, random_seed=seed + 10000, **settings)
    uniform = random.Random(seed + 10000)
    frames, attempts = [], []
    chosen = None
    distance_excess = []
    nearest_distances = []
    connected_distances = []
    instant_coverage = 0
    oracle_session_coverage = 0
    available_counts = []
    for second, (x, y, phase, target) in enumerate(generate_route(seed, seconds, movement)):
        distances = {a: scale_m * math.dist((x, y), positions[a]) for a in anchors}
        available_counts.append(sum(d <= connection_radius_m for d in distances.values()))
        ordered = sorted(anchors, key=distances.get)
        # Cada paso elige una placa y recibe exactamente una medida.
        if policy in ("adaptive", "previous", "power"):
            chosen, probabilities = scheduler.choose("watch_01", anchors, now=1000 + second)
        else:
            chosen = uniform.choice(anchors) if policy == "uniform" else anchors[second % len(anchors)]
            probabilities = {a: 1 / len(anchors) for a in anchors}
        attempts.append({"second": second, "anchor": chosen, "distance_m": distances[chosen],
                         "rank": ordered.index(chosen) + 1, "nearest_m": distances[ordered[0]]})
        # Radio duro solicitado: fuera del alcance no hay conexión y la
        # heurística recibe D_max como distancia censurada, no una medida real.
        # Mismo resultado potencial por segundo/anchor en todas las políticas.
        radio = random.Random(seed * 1000000 + second * 100 + anchors.index(chosen))
        ok = distances[chosen] <= connection_radius_m
        instant_coverage += distances[ordered[0]] <= connection_radius_m
        distance_excess.append(distances[chosen] - distances[ordered[0]])
        nearest_distances.append(distances[ordered[0]])
        if ok:
            connected_distances.append(distances[chosen])
        oracle_session_coverage += distances[ordered[0]] <= connection_radius_m
        values = [max(0, distances[chosen] * 100 + radio.gauss(0, 8))] if ok else [-1]
        scheduler.record("watch_01", chosen, values, now=1000 + second)
        assert sum(math.isfinite(v) for v in scheduler._current_slice["watch_01"].values()) == 1
        assert len(scheduler.history) + 1 == config.get("window_seconds", 15)
        assert all(0 <= value <= 1 for value in scheduler._scores("watch_01", 1000 + second).values())
        assert abs(sum(probabilities.values()) - 1) < 1e-9
        assert 0 <= x <= 1 and 0 <= y <= 1
        frames.append({"t": second, "x": round(x, 5), "y": round(y, 5), "anchor": chosen,
                       "ok": ok, "distance": round(distances[chosen], 3), "nearest": ordered[0],
                       "available_count": available_counts[-1],
                       "phase": phase, "target": target,
                       "rank": ordered.index(chosen) + 1})
    changes = sum(a["anchor"] != b["anchor"] for a, b in zip(attempts, attempts[1:]))
    transitions = len(attempts) - 1
    repeats = transitions - changes
    phase_data = {}
    for phase in ("dwell", "travel"):
        subset = [f for f in frames if f["phase"] == phase]
        phase_data[f"{phase}_data_percent"] = 100 * mean(f["ok"] for f in subset) if subset else None
    metrics = {"mean_distance_m": mean(a["distance_m"] for a in attempts),
               "min_available_anchors": min(available_counts),
               "double_coverage_percent": 100 * mean(count >= 2 for count in available_counts),
               "mean_excess_distance_m": mean(distance_excess),
               "normalized_excess_distance_percent": 100 * mean(distance_excess) / config.get("max_distance_m", 10.0),
               "mean_nearest_distance_m": mean(nearest_distances),
               "mean_connected_distance_m": mean(connected_distances) if connected_distances else None,
               "oracle_instant_data_percent": 100 * instant_coverage / seconds,
               "oracle_session_data_percent": 100 * oracle_session_coverage / seconds,
               "coverage_efficiency_percent": 100 * len(connected_distances) / oracle_session_coverage if oracle_session_coverage else None,
               "top3_percent": 100 * mean(a["rank"] <= 3 for a in attempts),
               "data_percent": 100 * mean(f["ok"] for f in frames),
               "within_radius_percent": 100 * mean(a["distance_m"] <= connection_radius_m for a in attempts),
               "any_anchor_within_radius_percent": 100 * mean(a["nearest_m"] <= connection_radius_m for a in attempts),
               "changes": changes, "decisions": len(attempts),
               "change_percent": 100 * changes / max(1, len(attempts) - 1),
               "alternation_percent": 100 * changes / transitions if transitions else None,
               "repeat_percent": 100 * repeats / transitions if transitions else None,
               "repeats": repeats, "transitions": transitions,
               "unique_anchors": len(set(a["anchor"] for a in attempts)),
               "anchor_counts": dict(Counter(a["anchor"] for a in attempts))}
    metrics.update(phase_data)
    return frames, metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=900)
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--scale-m", type=float, default=10)
    parser.add_argument("--connection-radius-m", type=float)
    parser.add_argument("--layout", choices=("real", "double"), default="real")
    parser.add_argument("--pair-spacing-m", type=float, default=1.5)
    parser.add_argument("--movement", choices=("random", "cores"), default="random")
    args = parser.parse_args()
    path = BASE.parent / "despliegue-hera/UCI_QORVO_UART/UCI_QORVO_UART/orquestador_config.json"
    config = json.loads(path.read_text(encoding="utf-8"))["orchestrator"]["adaptive_scheduler"]
    real_positions = load_anchor_positions(BASE / "data/sit_placas.csv")
    positions = double_coverage_positions(real_positions, args.pair_spacing_m, args.scale_m) if args.layout == "double" else real_positions
    # En cada cuadrante de lado 0.5, ambas placas distan como máximo
    # sqrt(0.275²+0.25²)*escala de cualquier punto. Certificado continuo.
    guaranteed_radius = args.scale_m * math.hypot(.25 + args.pair_spacing_m / (2 * args.scale_m), .25)
    if args.connection_radius_m is None:
        args.connection_radius_m = math.ceil(guaranteed_radius * 100) / 100 if args.layout == "double" else 2.5
    results, examples = {}, {}
    for policy in ("adaptive", "previous", "power", "uniform", "round_robin"):
        runs = []
        policy_positions = double_coverage_positions(real_positions, .5, args.scale_m) if policy == "previous" and args.layout == "double" else positions
        for seed in range(args.seeds):
            frames, metrics = simulate(seed, args.seconds, config, policy_positions, policy, args.scale_m, args.connection_radius_m, args.movement)
            if args.layout == "double" and policy_positions is positions and args.connection_radius_m >= guaranteed_radius:
                assert metrics["min_available_anchors"] >= 2
            runs.append(metrics)
            if seed == 0:
                examples[policy] = {"frames": frames, "metrics": metrics, "positions": policy_positions}
        results[policy] = {key: mean(r[key] for r in runs if r[key] is not None) if any(r[key] is not None for r in runs) else None
                           for key in runs[0] if key != "anchor_counts"}
        results[policy]["min_available_anchors"] = min(r["min_available_anchors"] for r in runs)
    output = {"assumptions": {"scale_m": args.scale_m, "session_seconds": 1,
              "measurement_per_step": 1, "unknown_cells": "NaN; excluded from weighted averages",
              "seconds": args.seconds, "seeds": args.seeds, "max_step_per_axis": .025,
              "connection_radius_m": args.connection_radius_m,
              "layout": args.layout,
              "movement": args.movement,
              "pair_spacing_m": args.pair_spacing_m,
              "dwell_seconds": [60, 120], "travel_speed_m_per_second": .02 * args.scale_m,
              "double_coverage_guaranteed_radius_m": guaranteed_radius if args.layout == "double" else None,
              "radio": "Conexión si distancia <= radio; fuera de radio se entrega D_max a la heurística",
              "anchors": "Todos los anchors presentes en sit_placas; no solo los de la configuración hardware"},
              "config": config, "positions": positions, "results": results, "examples": examples}
    directory = BASE / "simulacion"
    directory.mkdir(exist_ok=True)
    data = json.dumps(output, ensure_ascii=False, separators=(",", ":"))
    (directory / "resultados.json").write_text(data, encoding="utf-8")
    template = (directory / "replay.template.html").read_text(encoding="utf-8")
    (directory / "replay.html").write_text(template.replace("__SIMULATION_JSON__", data), encoding="utf-8")
    print(json.dumps({"assumptions": output["assumptions"], "results": results}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
