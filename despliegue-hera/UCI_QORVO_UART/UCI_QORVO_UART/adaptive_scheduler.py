"""Política de selección UWB basada en vecindad, historial y equidad.

La matriz M se guarda como ``history[segundo][reloj][anchor]``. Cada celda es
la mediana de distancia normalizada en [0, 1], 1 si el intento falló y NaN si
no hubo un intento para esa pareja en ese segundo. Las celdas desconocidas
no entran en las medias. En el snapshot JSON se exportan como null.
"""
from __future__ import annotations

import csv
import copy
import math
import random
import re
import time
from collections import deque
from pathlib import Path
from statistics import median
from typing import Iterable


def canonical_anchor(anchor: str) -> str:
    """Convierte formatos como ``00:_01`` o ``0001`` en ``00:01``."""
    digits = re.sub(r"[^0-9A-Fa-f]", "", str(anchor)).upper()
    if len(digits) == 4:
        return f"{digits[:2]}:{digits[2:]}"
    return str(anchor).upper().replace("_", "")


def load_anchor_positions(path: str | Path) -> dict[str, tuple[float, float]]:
    positions: dict[str, tuple[float, float]] = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            try:
                positions[canonical_anchor(row["anchor"])] = (float(row["x"]), float(row["y"]))
            except (KeyError, TypeError, ValueError):
                continue
    return positions


class AdaptiveAnchorScheduler:
    """Aprende qué anchors son plausibles, sin convertirlo en una regla rígida."""

    def __init__(
        self,
        anchors: Iterable[str],
        positions: dict[str, tuple[float, float]],
        window_seconds: int = 15,
        neighbor_scale: float = 0.30,
        temperature: float = 0.65,
        minimum_probability: float = 0.04,
        random_seed: int | None = None,
        temporal_decay: float = 0.9,
        max_distance_m: float = 10.0,
        evidence_power: float = 0.5,
        selection_mode: str = "rejection",
        acceptance_floor: float = 0.1,
        evidence_transform: str = "sigmoid",
        sigmoid_slope: float = 4.0,
        proximity_weight: float = 1.0,
        fairness_weight: float = 1.0,
        history_aggregation: str = "per_anchor",
    ) -> None:
        if window_seconds < 1:
            raise ValueError("window_seconds debe ser mayor o igual que 1")
        if not 0 <= minimum_probability < 1:
            raise ValueError("minimum_probability debe estar entre 0 y 1")
        if not 0 < temporal_decay <= 1:
            raise ValueError("temporal_decay debe estar en (0, 1]")
        self.temporal_decay = temporal_decay
        if not math.isfinite(max_distance_m) or max_distance_m <= 0:
            raise ValueError("max_distance_m debe ser finito y mayor que 0")
        self.max_distance_m = max_distance_m
        if not math.isfinite(evidence_power) or evidence_power <= 0:
            raise ValueError("evidence_power debe ser finito y mayor que 0")
        self.evidence_power = evidence_power
        if selection_mode not in {"softmax", "rejection"}:
            raise ValueError("selection_mode debe ser 'softmax' o 'rejection'")
        if not 0 < acceptance_floor <= 1:
            raise ValueError("acceptance_floor debe estar en (0, 1]")
        self.selection_mode = selection_mode
        self.acceptance_floor = acceptance_floor
        if evidence_transform not in {"power", "sigmoid"}:
            raise ValueError("evidence_transform debe ser 'power' o 'sigmoid'")
        if not math.isfinite(sigmoid_slope) or sigmoid_slope <= 0:
            raise ValueError("sigmoid_slope debe ser finito y mayor que 0")
        self.evidence_transform = evidence_transform
        self.sigmoid_slope = sigmoid_slope
        if any(not math.isfinite(w) or w < 0 for w in (proximity_weight, fairness_weight)) or proximity_weight + fairness_weight <= 0:
            raise ValueError("Los pesos deben ser finitos, no negativos y sumar más que 0")
        self.proximity_weight = proximity_weight
        self.fairness_weight = fairness_weight
        if history_aggregation not in {"per_anchor", "legacy"}:
            raise ValueError("history_aggregation debe ser 'per_anchor' o 'legacy'")
        self.history_aggregation = history_aggregation
        self.anchors = [canonical_anchor(anchor) for anchor in anchors]
        self.positions = {canonical_anchor(key): value for key, value in positions.items()}
        if any(len(p) != 2 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in p)
               for p in self.positions.values()):
            raise ValueError("Las posiciones x,y deben estar normalizadas entre 0 y 1")
        self.window_seconds = window_seconds
        if not math.isfinite(neighbor_scale) or neighbor_scale <= 0:
            raise ValueError("neighbor_scale debe ser finito y mayor que 0")
        self.neighbor_scale = neighbor_scale
        self.temperature = max(temperature, 0.05)
        self.minimum_probability = minimum_probability
        # La slice en curso también forma parte de M, por eso el histórico
        # conserva como máximo W-1 slices anteriores.
        self.history: deque[dict[str, dict[str, float]]] = deque(maxlen=max(window_seconds - 1, 1))
        self._current_second: int | None = None
        self._current_slice: dict[str, dict[str, float]] = {}
        self._samples: dict[tuple[int, str, str], list[float]] = {}
        self._last_selected: dict[str, dict[str, int]] = {}
        self._selection_load: dict[str, dict[str, float]] = {}
        self._rng = random.Random(random_seed)

    def _empty_slice(self, watches: Iterable[str] = ()) -> dict[str, dict[str, float]]:
        return {watch_id: {anchor: math.nan for anchor in self.anchors} for watch_id in watches}

    def _ensure_watch(self, watch_id: str) -> None:
        if watch_id not in self._current_slice:
            self._current_slice[watch_id] = {anchor: math.nan for anchor in self.anchors}
        for slice_ in self.history:
            slice_.setdefault(watch_id, {anchor: math.nan for anchor in self.anchors})
        self._last_selected.setdefault(watch_id, {})

    def _advance(self, now: float) -> None:
        second = int(now)
        if self._current_second is None:
            self._current_second = second
            self.history.extend(self._empty_slice() for _ in range(self.window_seconds - 1))
            return
        if second - self._current_second >= self.window_seconds:
            # No hace falta materializar miles de segundos neutros: ya no caben
            # en la ventana M. Conservamos los relojes conocidos y reiniciamos M.
            self.history.clear()
            self._current_second = second
            self._current_slice = self._empty_slice(self._current_slice)
            self.history.extend(self._empty_slice(self._current_slice) for _ in range(self.window_seconds - 1))
        while self._current_second < second:
            if self.window_seconds > 1:
                self.history.append(self._current_slice)
            self._current_second += 1
            self._current_slice = self._empty_slice(self._current_slice)
        cutoff = self._current_second - self.window_seconds + 1
        self._samples = {key: values for key, values in self._samples.items() if key[0] >= cutoff}

    def record(self, watch_id: str, anchor: str, values: Iterable[float], now: float | None = None,
               normalized: bool = False) -> float:
        """Entrada HW en cm o simulación normalizada; devuelve mediana en [0,1]."""
        now = time.time() if now is None else now
        self._advance(now)
        self._ensure_watch(watch_id)
        anchor = canonical_anchor(anchor)
        samples = [float(value) for value in values if float(value) >= 0 and math.isfinite(float(value))]
        samples = [min(value, 1.0) if normalized else self.normalized_distance(value) for value in samples]
        if not samples:
            samples = [1.0]
        distance = median(samples)
        second = int(now)
        age = self._current_second - second
        if anchor in self.anchors and 0 <= age < self.window_seconds:
            slice_ = self._current_slice if age == 0 else self.history[-age]
            key = (second, watch_id, anchor)
            accumulated = self._samples.setdefault(key, [])
            accumulated.extend(samples)
            # Mediana de todas las observaciones, incluidos los intentos
            # fallidos representados por D_max. Nunca mediana de medianas.
            distance = median(accumulated)
            slice_[watch_id][anchor] = distance
        return distance

    def _kernel(self, source: str, target: str) -> float:
        if source == target:
            return 1.0
        first, second = self.positions.get(source), self.positions.get(target)
        if first is None or second is None:
            return 0.0
        # Coordinates and scale use D_max as their unit. Do not clip the
        # diagonal before applying the kernel: that would create a plateau.
        distance = math.dist(first, second)
        return math.exp(-distance / self.neighbor_scale)

    def normalized_distance(self, distance_cm: float) -> float:
        """Distancia válida en [0, 1]; los marcadores -1 no son distancias."""
        if not math.isfinite(distance_cm) or distance_cm < 0:
            raise ValueError("Se necesita una distancia válida en centímetros")
        return min(distance_cm / (100 * self.max_distance_m), 1.0)

    def _scores(self, watch_id: str, now: float) -> dict[str, float]:
        self._advance(now)
        self._ensure_watch(watch_id)
        slices = list(self.history) + [self._current_slice]
        evidence = {anchor: 0.0 for anchor in self.anchors}
        temporal_total = sum(self.temporal_decay ** age for age in range(self.window_seconds))
        # Las muestras recientes pesan más; éxito y fallo se propagan localmente.
        for age, slice_ in enumerate(reversed(slices)):
            decay = self.temporal_decay ** age
            spatial_total = {anchor: 0.0 for anchor in self.anchors}
            spatial_evidence = {anchor: 0.0 for anchor in self.anchors}
            for source, value in slice_.get(watch_id, {}).items():
                # Desconocida no es fallo; una distancia cero sí es válida.
                if not math.isfinite(value):
                    continue
                impact = 1.0 - 2.0 * value
                for target in self.anchors:
                    kernel = self._kernel(source, target)
                    spatial_evidence[target] += impact * kernel
                    spatial_total[target] += kernel
            for target in self.anchors:
                # Dividir al menos por 1 conserva la atenuación de los vecinos
                # y acota cada slice incluso con varios relojes/anchors medidos.
                evidence[target] += decay * spatial_evidence[target] / max(1.0, spatial_total[target])

        if self.history_aggregation == "per_anchor":
            # Una estimación por pareja a partir de TODAS sus observaciones en
            # M. Los segundos sin intento no entran en el denominador; los
            # intentos fallidos sí, con D_max. La antigüedad reduce confianza.
            estimates = self._pair_estimates(watch_id)
            evidence = {anchor: 0.0 for anchor in self.anchors}
            for target in self.anchors:
                total = 0.0
                for source, estimate in estimates.items():
                    kernel = self._kernel(source, target)
                    evidence[target] += kernel * estimate["confidence"] * (1 - 2 * estimate["normalized_distance"])
                    total += kernel
                evidence[target] /= max(1.0, total)
            temporal_total = 1.0

        now_second = int(now)
        scores: dict[str, float] = {}
        for anchor in self.anchors:
            last = self._last_selected[watch_id].get(anchor)
            waited = self.window_seconds if last is None else min(now_second - last, self.window_seconds)
            # El crédito de espera evita inanición aun cuando otro anchor tenga mucha evidencia.
            fairness = max(0.0, waited / self.window_seconds)
            # Memoria suave por decisión: repetir acumula carga, alternar la
            # disipa. Nunca excluye una placa y es independiente por reloj.
            fairness *= 1 - self._selection_load.get(watch_id, {}).get(anchor, 0.0)
            signed_evidence = evidence[anchor] / temporal_total
            # La raíz amplifica la magnitud de señales débiles, conservando
            # el signo. No aplicar la raíz a probabilidades: las uniformaría.
            if self.evidence_transform == "sigmoid":
                # Sigmoide centrada y normalizada: -1 -> -1, 0 -> 0, 1 -> 1.
                boosted = math.tanh(self.sigmoid_slope * signed_evidence) / math.tanh(self.sigmoid_slope)
            else:
                boosted = math.copysign(abs(signed_evidence) ** self.evidence_power, signed_evidence)
            proximity = (1.0 + boosted) / 2.0
            # Equidad condicionada por cercanía: esperar no compensa por sí
            # solo una mala distancia. Una placa buena conserva su término
            # principal aunque acabe de seleccionarse; alternar es un bonus.
            scores[anchor] = proximity * (self.proximity_weight + self.fairness_weight * fairness) / (self.proximity_weight + self.fairness_weight)
        return scores

    def _pair_estimates(self, watch_id: str) -> dict[str, dict[str, float]]:
        """Distancia ponderada y confianza por anchor en la ventana actual."""
        estimates = {}
        slices = list(self.history) + [self._current_slice]
        for anchor in self.anchors:
            weighted_distance = total_weight = 0.0
            newest_age = None
            count = 0
            for age, slice_ in enumerate(reversed(slices)):
                timestamp = self._current_second - age
                if (timestamp, watch_id, anchor) not in self._samples:
                    continue
                value = slice_.get(watch_id, {}).get(anchor, math.nan)
                if not math.isfinite(value):
                    continue
                weight = self.temporal_decay ** age
                weighted_distance += weight * value
                total_weight += weight
                newest_age = age if newest_age is None else newest_age
                count += 1
            if total_weight:
                estimates[anchor] = {"normalized_distance": weighted_distance / total_weight,
                                     "confidence": self.temporal_decay ** newest_age,
                                     "age_seconds": newest_age, "observed_slices": count}
        return estimates

    def acceptance_weights(self, watch_id: str, candidates: Iterable[str], now: float | None = None) -> dict[str, float]:
        """Min-max entre placas libres; la peor conserva opción de aceptación."""
        now = time.time() if now is None else now
        allowed = list(dict.fromkeys(canonical_anchor(a) for a in candidates if canonical_anchor(a) in self.anchors))
        if not allowed:
            return {}
        scores = self._scores(watch_id, now)
        lower = min(scores[a] for a in allowed)
        upper = max(scores[a] for a in allowed)
        if math.isclose(lower, upper, abs_tol=1e-12):
            return {a: 1.0 for a in allowed}
        return {a: min(1.0, max(self.acceptance_floor, self.acceptance_floor + (1 - self.acceptance_floor) * ((scores[a] - lower) / (upper - lower)))) for a in allowed}

    def probabilities(self, watch_id: str, candidates: Iterable[str], now: float | None = None) -> dict[str, float]:
        now = time.time() if now is None else now
        if self.selection_mode == "rejection":
            weights = self.acceptance_weights(watch_id, candidates, now)
            total = sum(weights.values())
            return {a: weight / total for a, weight in weights.items()}
        allowed = [canonical_anchor(anchor) for anchor in candidates if canonical_anchor(anchor) in self.anchors]
        if not allowed:
            return {}
        scores = self._scores(watch_id, now)
        maximum = max(scores[anchor] for anchor in allowed)
        weights = {anchor: math.exp((scores[anchor] - maximum) / self.temperature) for anchor in allowed}
        total = sum(weights.values())
        raw = {anchor: weights[anchor] / total for anchor in allowed}
        # Suelo probabilístico: ninguna placa libre queda descartada por completo.
        floor = min(self.minimum_probability, 1 / len(allowed))
        return {anchor: floor + (1 - floor * len(allowed)) * raw[anchor] for anchor in allowed}

    def choose(self, watch_id: str, candidates: Iterable[str], now: float | None = None) -> tuple[str | None, dict[str, float]]:
        now = time.time() if now is None else now
        if self.selection_mode == "rejection":
            weights = self.acceptance_weights(watch_id, candidates, now)
            if not weights:
                return None, {}
            total = sum(weights.values())
            probabilities = {a: weight / total for a, weight in weights.items()}
            anchors = list(weights)
            while True:
                anchor = self._rng.choice(anchors)
                if self._rng.random() < weights[anchor]:
                    break
            self._remember_selection(watch_id, anchor, now)
            return anchor, probabilities
        probabilities = self.probabilities(watch_id, candidates, now)
        if not probabilities:
            return None, {}
        anchors, weights = zip(*probabilities.items())
        anchor = self._rng.choices(anchors, weights=weights, k=1)[0]
        self._remember_selection(watch_id, anchor, now)
        return anchor, probabilities

    def choose_batch(self, watch_ids: Iterable[str], candidates: Iterable[str],
                     now: float | None = None, occupied: Iterable[str] = (),
                     first_index: int = 0) -> dict[str, tuple[str | None, dict[str, float]]]:
        """Asigna un lote sin compartir placas; sin libres devuelve None.

        Reserva cada elección antes de evaluar el siguiente reloj. El llamador
        debe serializar los lotes y pasar las reservas de sesiones activas.
        first_index permite rotar quién elige primero sin alterar la heurística.
        """
        now = time.time() if now is None else now
        watches = list(dict.fromkeys(watch_ids))
        if not watches:
            return {}
        offset = first_index % len(watches)
        watches = watches[offset:] + watches[:offset]
        reserved = {canonical_anchor(a) for a in occupied}
        free = list(dict.fromkeys(canonical_anchor(a) for a in candidates
                                  if canonical_anchor(a) in self.anchors and canonical_anchor(a) not in reserved))
        result = {}
        for watch in watches:
            anchor, probabilities = self.choose(watch, free, now)
            result[watch] = (anchor, probabilities)
            if anchor is not None:
                free.remove(anchor)
        return result

    def _remember_selection(self, watch_id: str, anchor: str, now: float) -> None:
        self._last_selected.setdefault(watch_id, {})[anchor] = int(now)
        load = self._selection_load.setdefault(watch_id, {})
        rate = 2 / (self.window_seconds + 1)
        for candidate in self.anchors:
            load[candidate] = (1 - rate) * load.get(candidate, 0.0) + rate * (candidate == anchor)

    def snapshot(self) -> dict:
        """Estado JSON seguro para observación y depuración del planificador."""
        return {
            "window_seconds": self.window_seconds,
            "temporal_decay": self.temporal_decay,
            "max_distance_m": self.max_distance_m,
            "distance_units": "normalized_by_D_max",
            "normalized_max_distance": 1.0,
            "spatial_kernel": "exp(-euclidean_distance_xy / neighbor_scale)",
            "neighbor_scale": self.neighbor_scale,
            "coordinate_units": "one axis unit = max_distance_m metres",
            "evidence_power": self.evidence_power,
            "selection_mode": self.selection_mode,
            "acceptance_floor": self.acceptance_floor,
            "evidence_transform": self.evidence_transform,
            "sigmoid_slope": self.sigmoid_slope,
            "proximity_weight": self.proximity_weight,
            "fairness_weight": self.fairness_weight,
            "history_aggregation": self.history_aggregation,
            "selection_load_by_watch": copy.deepcopy(self._selection_load),
            "estimates_by_watch": {watch: self._pair_estimates(watch) for watch in self._current_slice},
            "timestamps": list(range(self._current_second - self.window_seconds + 1, self._current_second + 1)) if self._current_second is not None else [],
            "temporal_weights": [self.temporal_decay ** age for age in reversed(range(self.window_seconds))],
            "anchors": self.anchors,
            "history": [{watch: {anchor: value if math.isfinite(value) else None
                                  for anchor, value in row.items()}
                         for watch, row in slice_.items()}
                        for slice_ in list(self.history) + ([self._current_slice] if self._current_second is not None else [])],
        }
