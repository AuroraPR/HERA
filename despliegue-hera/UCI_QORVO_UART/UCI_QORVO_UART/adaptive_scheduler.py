"""Política de selección UWB basada en vecindad, historial y equidad.

La matriz M se guarda como ``history[segundo][reloj][anchor]``. Cada celda es
la mediana de distancia de ese segundo, -1 si el intento falló y 0 si todavía
no hubo un intento para esa pareja en ese segundo. El 0 es neutral: evita que
la ausencia inevitable de medidas en un protocolo 1-a-1 parezca un fallo.
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
    ) -> None:
        if window_seconds < 1:
            raise ValueError("window_seconds debe ser mayor o igual que 1")
        if not 0 <= minimum_probability < 1:
            raise ValueError("minimum_probability debe estar entre 0 y 1")
        if not 0 < temporal_decay <= 1:
            raise ValueError("temporal_decay debe estar en (0, 1]")
        self.temporal_decay = temporal_decay
        self.anchors = [canonical_anchor(anchor) for anchor in anchors]
        self.positions = {canonical_anchor(key): value for key, value in positions.items()}
        self.window_seconds = window_seconds
        self.neighbor_scale = max(neighbor_scale, 0.001)
        self.temperature = max(temperature, 0.05)
        self.minimum_probability = minimum_probability
        # La slice en curso también forma parte de M, por eso el histórico
        # conserva como máximo W-1 slices anteriores.
        self.history: deque[dict[str, dict[str, float]]] = deque(maxlen=max(window_seconds - 1, 1))
        self._current_second: int | None = None
        self._current_slice: dict[str, dict[str, float]] = {}
        self._samples: dict[tuple[int, str, str], list[float]] = {}
        self._last_selected: dict[str, dict[str, int]] = {}
        self._rng = random.Random(random_seed)

    def _empty_slice(self, watches: Iterable[str] = ()) -> dict[str, dict[str, float]]:
        return {watch_id: {anchor: 0.0 for anchor in self.anchors} for watch_id in watches}

    def _ensure_watch(self, watch_id: str) -> None:
        if watch_id not in self._current_slice:
            self._current_slice[watch_id] = {anchor: 0.0 for anchor in self.anchors}
        for slice_ in self.history:
            slice_.setdefault(watch_id, {anchor: 0.0 for anchor in self.anchors})
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

    def record(self, watch_id: str, anchor: str, values: Iterable[float], now: float | None = None) -> float:
        """Guarda la mediana válida de un intento, o -1 si no hubo medida válida."""
        now = time.time() if now is None else now
        self._advance(now)
        self._ensure_watch(watch_id)
        anchor = canonical_anchor(anchor)
        samples = [float(value) for value in values if float(value) >= 0 and math.isfinite(float(value))]
        distance = median(samples) if samples else -1.0
        second = int(now)
        age = self._current_second - second
        if anchor in self.anchors and 0 <= age < self.window_seconds:
            slice_ = self._current_slice if age == 0 else self.history[-age]
            key = (second, watch_id, anchor)
            accumulated = self._samples.setdefault(key, [])
            accumulated.extend(samples)
            # Mediana de TODAS las muestras del segundo, sin mezclar -1 con
            # distancias válidas ni calcular una mediana de medianas.
            distance = median(accumulated) if accumulated else -1.0
            slice_[watch_id][anchor] = distance
        return distance

    def _kernel(self, source: str, target: str) -> float:
        if source == target:
            return 1.0
        first, second = self.positions.get(source), self.positions.get(target)
        if first is None or second is None:
            return 0.0
        distance = math.dist(first, second)
        return math.exp(-distance / self.neighbor_scale)

    def _scores(self, watch_id: str, now: float) -> dict[str, float]:
        self._advance(now)
        self._ensure_watch(watch_id)
        slices = list(self.history) + [self._current_slice]
        evidence = {anchor: 0.0 for anchor in self.anchors}
        # Las muestras recientes pesan más; éxito y fallo se propagan localmente.
        for age, slice_ in enumerate(reversed(slices)):
            decay = self.temporal_decay ** age
            for source, value in slice_.get(watch_id, {}).items():
                if value == 0:
                    continue
                impact = 1.0 if value > 0 else -1.0
                for target in self.anchors:
                    evidence[target] += decay * impact * self._kernel(source, target)

        now_second = int(now)
        scores: dict[str, float] = {}
        for anchor in self.anchors:
            last = self._last_selected[watch_id].get(anchor)
            waited = self.window_seconds if last is None else min(now_second - last, self.window_seconds)
            # El crédito de espera evita inanición aun cuando otro anchor tenga mucha evidencia.
            fairness = waited / self.window_seconds
            scores[anchor] = evidence[anchor] + 0.65 * fairness
        return scores

    def probabilities(self, watch_id: str, candidates: Iterable[str], now: float | None = None) -> dict[str, float]:
        now = time.time() if now is None else now
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
        probabilities = self.probabilities(watch_id, candidates, now)
        if not probabilities:
            return None, {}
        anchors, weights = zip(*probabilities.items())
        anchor = self._rng.choices(anchors, weights=weights, k=1)[0]
        self._last_selected.setdefault(watch_id, {})[anchor] = int(now)
        return anchor, probabilities

    def snapshot(self) -> dict:
        """Estado JSON seguro para observación y depuración del planificador."""
        return {
            "window_seconds": self.window_seconds,
            "temporal_decay": self.temporal_decay,
            "timestamps": list(range(self._current_second - self.window_seconds + 1, self._current_second + 1)) if self._current_second is not None else [],
            "temporal_weights": [self.temporal_decay ** age for age in reversed(range(self.window_seconds))],
            "anchors": self.anchors,
            "history": copy.deepcopy(list(self.history) + ([self._current_slice] if self._current_second is not None else [])),
        }
