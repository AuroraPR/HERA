"""Dos relojes, una medida por reloj y paso, y asignaciones exclusivas."""
import json
import math
import random
from statistics import mean

from simular_asignacion import BASE, AdaptiveAnchorScheduler, double_coverage_positions, load_anchor_positions


def routes(seed, steps):
    cores = [(.25, .25), (.75, .25), (.25, .75), (.75, .75)]
    rng = random.Random(seed)
    permutation = list(range(4))
    rng.shuffle(permutation)
    # Dos bloques compartidos y dos separados, repetidos sin teletransporte.
    plans = ((0, 1, 2, 3), (0, 2, 2, 1))
    positions = [list(cores[permutation[0]]) for _ in range(2)]
    noises = [random.Random(seed * 100 + i + 500) for i in range(2)]
    for step in range(steps):
        frame = []
        for i in range(2):
            target = permutation[plans[i][(step // 150) % 4]]
            cx, cy = cores[target]
            x, y = positions[i]
            distance = math.dist((x, y), (cx, cy))
            if distance > .025:
                x += .02 * (cx - x) / distance + noises[i].uniform(-.001, .001)
                y += .02 * (cy - y) / distance + noises[i].uniform(-.001, .001)
                phase = "travel"
            else:
                x += .25 * (cx - x) + noises[i].uniform(-.006, .006)
                y += .25 * (cy - y) + noises[i].uniform(-.006, .006)
                phase = "dwell"
            assert 0 <= x <= 1 and 0 <= y <= 1
            assert math.dist((x, y), positions[i]) < .025
            positions[i] = [x, y]
            frame.append({"x": x, "y": y, "phase": phase, "target": target})
        yield frame


def simulate(seed, config, positions, steps=900, policy="adaptive"):
    settings = dict(config)
    settings["proximity_weight"] = settings.pop("w_cercania")
    settings["fairness_weight"] = settings.pop("w_equidad")
    scheduler = AdaptiveAnchorScheduler(positions, positions, random_seed=seed + 10000, **settings)
    watches = ["watch_01", "watch_02"]
    anchors = sorted(positions)
    rng = random.Random(seed + 20000)
    frames = []
    first_counts = {w: 0 for w in watches}
    for step, people in enumerate(routes(seed, steps)):
        first_counts[watches[step % 2]] += 1
        if policy == "adaptive":
            assignments = scheduler.choose_batch(watches, anchors, now=1000 + step, first_index=step)
        else:
            free = list(anchors)
            assignments = {}
            for i in (step % 2, 1 - step % 2):
                chosen = rng.choice(free)
                assignments[watches[i]] = (chosen, {a: 1 / len(free) for a in free})
                free.remove(chosen)
        selected = [assignments[w][0] for w in watches]
        assert None not in selected and len(set(selected)) == len(watches), "Colisión de placas"
        coincident = people[0]["phase"] == people[1]["phase"] == "dwell" and people[0]["target"] == people[1]["target"]
        for i, watch in enumerate(watches):
            person = people[i]
            anchor, probabilities = assignments[watch]
            distance = min(1, math.dist((person["x"], person["y"]), positions[anchor]))
            ok = distance <= 4.11 / config["max_distance_m"]
            noise = random.Random(seed * 1000000 + step * 100 + i * 10 + anchors.index(anchor))
            measurement = min(1, max(0, distance + noise.gauss(0, .08 / config["max_distance_m"]))) if ok else 1
            scheduler.record(watch, anchor, [measurement], now=1000 + step, normalized=True)
            assert sum(math.isfinite(v) for v in scheduler._current_slice[watch].values()) == 1
            assert math.isclose(sum(probabilities.values()), 1)
            person.update(anchor=anchor, ok=ok, distance=round(distance, 4), measurement=round(measurement, 4))
            person["x"], person["y"] = round(person["x"], 5), round(person["y"], 5)
        assert len(scheduler.history) + 1 == 15
        frames.append({"t": step, "shared": coincident, "people": people})
    shared = [f for f in frames if f["shared"]]
    separate = [f for f in frames if not f["shared"]]
    assert shared and separate
    collisions = sum(len({p["anchor"] for p in f["people"]}) != len(watches) for f in frames)
    metrics = {"collisions": collisions, "shared_steps": len(shared),
               "shared_percent": 100 * len(shared) / steps,
               "both_data_percent": 100 * mean(all(p["ok"] for p in f["people"]) for f in frames),
               "both_data_when_shared_percent": 100 * mean(all(p["ok"] for p in f["people"]) for f in shared)}
    for i, watch in enumerate(watches):
        records = [f["people"][i] for f in frames]
        metrics[watch] = {"data_percent": 100 * mean(p["ok"] for p in records),
                          "no_data_count": sum(not p["ok"] for p in records),
                          "alternation_percent": 100 * mean(a["anchor"] != b["anchor"] for a, b in zip(records, records[1:])),
                          "shared_data_percent": 100 * mean(f["people"][i]["ok"] for f in shared),
                          "separate_data_percent": 100 * mean(f["people"][i]["ok"] for f in separate),
                          "first_count": first_counts[watch]}
    return frames, metrics


def main():
    config = json.loads((BASE.parent / "despliegue-hera/UCI_QORVO_UART/UCI_QORVO_UART/orquestador_config.json").read_text(encoding="utf-8"))["orchestrator"]["adaptive_scheduler"]
    positions = double_coverage_positions(load_anchor_positions(BASE / "data/sit_placas.csv"), 1.5, config["max_distance_m"])
    results, examples = {}, {}
    for policy in ("adaptive", "uniform"):
        runs = []
        for seed in range(20):
            frames, metrics = simulate(seed, config, positions, policy=policy)
            runs.append(metrics)
            if seed == 0:
                examples[policy] = frames
        results[policy] = {k: mean(r[k] for r in runs) for k in runs[0] if not isinstance(runs[0][k], dict)}
        for watch in ("watch_01", "watch_02"):
            results[policy][watch] = {k: mean(r[watch][k] for r in runs) for k in runs[0][watch]}
    output = {"config": config, "positions": positions, "results": results, "examples": examples,
              "assumptions": {"seeds": 20, "steps": 900, "watches": 2, "radius_m": 4.11,
                              "distance_units": "normalized_by_D_max", "normalized_max_distance": 1,
                              "coordinate_units": "one axis unit = D_max metres",
                              "pair_spacing_m": 1.5, "window": 15, "measurements_per_watch_step": 1,
                              "allocation": "Exclusive batch; alternate first watch; release after each step"}}
    folder = BASE / "simulacion"
    data = json.dumps(output, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    (folder / "resultados_multi.json").write_text(data, encoding="utf-8")
    template = (folder / "multi.template.html").read_text(encoding="utf-8")
    (folder / "multi.html").write_text(template.replace("__MULTI_JSON__", data), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
