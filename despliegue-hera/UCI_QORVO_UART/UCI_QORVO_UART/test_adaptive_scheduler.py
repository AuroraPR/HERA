import unittest
import math
import json
from collections import Counter

from adaptive_scheduler import AdaptiveAnchorScheduler


class SlidingWindowTests(unittest.TestCase):
    def scheduler(self, window=15):
        return AdaptiveAnchorScheduler(["A", "B"], {}, window_seconds=window, evidence_transform="power")

    def test_window_rotates_and_expires_by_second(self):
        scheduler = self.scheduler()
        scheduler.record("watch", "A", [10], now=100.1)
        self.assertEqual(len(scheduler.snapshot()["history"]), 15)
        scheduler.record("watch", "B", [20], now=114.9)
        snapshot = scheduler.snapshot()
        self.assertEqual(snapshot["timestamps"], list(range(100, 115)))
        self.assertEqual(snapshot["history"][0]["watch"]["A"], 10)
        scheduler.record("watch", "B", [30], now=115)
        self.assertEqual(scheduler.snapshot()["timestamps"], list(range(101, 116)))
        self.assertTrue(all(slice_["watch"]["A"] is None for slice_ in scheduler.snapshot()["history"]))

    def test_temporal_weights_and_late_samples(self):
        scheduler = self.scheduler()
        scheduler.record("watch", "A", [10], now=100)
        scheduler.record("watch", "B", [10], now=101)
        # Igual distancia: el segundo reciente pesa más por el factor 0.9.
        scores = scheduler._scores("watch", 101)
        self.assertAlmostEqual(scores["B"] - scores["A"], (0.98 ** .5 - (0.98 * .9) ** .5) / 2)
        scheduler.record("watch", "A", [20, 30, 40], now=100.8)
        snapshot = scheduler.snapshot()
        self.assertEqual(snapshot["history"][-2]["watch"]["A"], 25)
        self.assertEqual(snapshot["temporal_weights"][-3:], [0.81, 0.9, 1])
        scheduler.record("watch", "A", [], now=100.9)
        self.assertEqual(scheduler.snapshot()["history"][-2]["watch"]["A"], 30)

    def test_long_gap_and_old_data(self):
        scheduler = self.scheduler()
        scheduler.record("watch", "A", [10], now=100)
        scheduler.record("watch", "B", [], now=200)
        scheduler.record("watch", "A", [999], now=100)
        snapshot = scheduler.snapshot()
        self.assertEqual(len(snapshot["history"]), 15)
        self.assertEqual(snapshot["timestamps"], list(range(186, 201)))
        self.assertTrue(all(slice_["watch"]["A"] is None for slice_ in snapshot["history"]))
        self.assertEqual(snapshot["history"][-1]["watch"]["B"], 1000)

    def test_single_second_window(self):
        scheduler = self.scheduler(window=1)
        scheduler.record("watch", "A", [10], now=100)
        scheduler.record("watch", "B", [20], now=101)
        self.assertEqual(len(scheduler.snapshot()["history"]), 1)
        self.assertIsNone(scheduler.snapshot()["history"][0]["watch"]["A"])

    def test_unknown_is_nan_and_zero_is_a_valid_measurement(self):
        scheduler = self.scheduler()
        scheduler.record("watch", "A", [0], now=100)
        self.assertTrue(math.isnan(scheduler._current_slice["watch"]["B"]))
        self.assertEqual(scheduler._pair_estimates("watch")["A"]["normalized_distance"], 0)
        self.assertNotIn("B", scheduler._pair_estimates("watch"))
        scheduler.record("watch", "B", [-1], now=101)
        self.assertEqual(scheduler._current_slice["watch"]["B"], 1000)
        self.assertTrue(math.isnan(scheduler._current_slice["watch"]["A"]))
        self.assertEqual(scheduler._pair_estimates("watch")["A"]["observed_slices"], 1)
        json.dumps(scheduler.snapshot(), allow_nan=False)

    def test_normalized_distance_and_priority(self):
        scheduler = self.scheduler(window=1)
        for distance, expected in ((0, 0), (200, 0.2), (1000, 1), (2000, 1)):
            self.assertAlmostEqual(scheduler.normalized_distance(distance), expected)
        scheduler.record("watch", "A", [200], now=100)
        scheduler.record("watch", "B", [800], now=100)
        scores = scheduler._scores("watch", 100)
        self.assertGreater(scores["A"], scores["B"])
        self.assertTrue(all(0 <= value <= 1 for value in scores.values()))
        probabilities = scheduler.probabilities("watch", ["A", "B"], now=100)
        self.assertGreater(probabilities["A"], probabilities["B"])
        self.assertAlmostEqual(sum(probabilities.values()), 1)
        scheduler.record("watch", "B", [], now=101)
        self.assertLess(scheduler._scores("watch", 101)["B"], scheduler._scores("watch", 101)["A"])
        with self.assertRaises(ValueError):
            scheduler.normalized_distance(-1)

    def test_root_amplifies_evidence_and_keeps_neutral(self):
        linear = AdaptiveAnchorScheduler(["A", "B"], {}, evidence_power=1, evidence_transform="power")
        root = AdaptiveAnchorScheduler(["A", "B"], {}, evidence_power=.5, evidence_transform="power")
        for scheduler in (linear, root):
            scheduler.record("watch", "A", [200], now=100)
        self.assertGreater(root._scores("watch", 100)["A"], linear._scores("watch", 100)["A"])
        self.assertEqual(root._scores("watch", 100)["B"], linear._scores("watch", 100)["B"])
        for scheduler in (linear, root):
            scheduler.record("watch", "A", [], now=200)
        self.assertLess(root._scores("watch", 201)["A"], linear._scores("watch", 201)["A"])

    def test_acceptance_rejection_distribution(self):
        scheduler = AdaptiveAnchorScheduler(["A", "B"], {}, random_seed=123)
        scheduler._scores = lambda watch, now: {"A": .2, "B": .8}
        self.assertEqual(scheduler.acceptance_weights("watch", ["A", "B"], 100), {"A": .1, "B": 1.0})
        probabilities = scheduler.probabilities("watch", ["A", "B"], 100)
        self.assertAlmostEqual(probabilities["B"], 1 / 1.1)
        counts = Counter(scheduler.choose("watch", ["A", "B"], 100)[0] for _ in range(10000))
        self.assertAlmostEqual(counts["B"] / 10000, 1 / 1.1, delta=.02)
        self.assertEqual(scheduler.choose("watch", [], 100), (None, {}))
        self.assertEqual(scheduler.choose("watch", ["A"], 100), ("A", {"A": 1.0}))
        scheduler._scores = lambda watch, now: {"A": .2, "B": .2}
        self.assertEqual(scheduler.probabilities("watch", ["A", "B"], 100), {"A": .5, "B": .5})

    def test_quarter_power_and_sigmoid_are_bounded(self):
        for transform in ("power", "sigmoid"):
            scheduler = AdaptiveAnchorScheduler(["A", "B"], {}, evidence_power=.25,
                                                evidence_transform=transform, window_seconds=1)
            for timestamp, distance in enumerate((0, 100, 999, 1000, 2000)):
                scheduler.record("watch", "A", [distance], now=100 + timestamp)
                scores = scheduler._scores("watch", 100 + timestamp)
                self.assertTrue(all(0 <= score <= 1 for score in scores.values()))
                if distance >= 1000:
                    # D_max es lejanía máxima, no desconocido.
                    self.assertLess(scores["A"], scores["B"])
            scheduler.record("watch", "A", [], now=200)
            self.assertLess(scheduler._scores("watch", 200)["A"], scheduler._scores("watch", 200)["B"])

    def test_proximity_weight_overcomes_recent_visit(self):
        scheduler = AdaptiveAnchorScheduler(["A", "B"], {}, proximity_weight=4,
                                            fairness_weight=1, window_seconds=1)
        scheduler.record("watch", "A", [100], now=100)
        scheduler._last_selected["watch"]["A"] = 100
        scores = scheduler._scores("watch", 100)
        self.assertGreater(scores["A"], scores["B"])
        self.assertTrue(all(0 <= score <= 1 for score in scores.values()))


    def test_soft_alternation_preserves_good_repeats_and_recovers(self):
        scheduler = AdaptiveAnchorScheduler(["A", "B", "C"], {}, proximity_weight=2, fairness_weight=1)
        scheduler.record("watch", "A", [100], now=100)
        scheduler.record("watch", "B", [100], now=100)
        scheduler.record("watch", "C", [], now=100)
        scheduler.choose("watch", ["A"], now=100)
        first = scheduler._scores("watch", 110)
        for _ in range(12):
            scheduler.choose("watch", ["A"], now=100)
        repeated = scheduler._scores("watch", 110)
        self.assertLess(repeated["A"], first["A"])
        self.assertGreater(repeated["A"], repeated["C"])
        self.assertGreater(repeated["B"], repeated["A"])
        self.assertGreater(scheduler.probabilities("watch", ["A", "B", "C"], 110)["A"], 0)
        for _ in range(12):
            scheduler.choose("watch", ["B"], now=100)
        self.assertGreater(scheduler._scores("watch", 110)["A"], repeated["A"])
        scheduler.record("other", "A", [100], now=110)
        self.assertNotIn("other", scheduler._selection_load)

    def test_batch_never_shares_even_when_every_watch_prefers_same_anchor(self):
        scheduler = AdaptiveAnchorScheduler(["A", "B", "C"], {}, random_seed=3)
        scheduler._scores = lambda watch, now: {"A": 1, "B": .1, "C": 0}
        for step in range(1000):
            result = scheduler.choose_batch(["w1", "w2"], ["A", "A", "B", "C"],
                                            now=step, occupied=["C"], first_index=step)
            chosen = [a for a, _ in result.values()]
            self.assertEqual(set(chosen), {"A", "B"})
            first = next(iter(result))
            self.assertEqual(first, "w1" if step % 2 == 0 else "w2")
            second = list(result)[1]
            self.assertNotIn(result[first][0], result[second][1])
        scarce = scheduler.choose_batch(["w1", "w2"], ["A"], now=1001)
        self.assertEqual(scarce["w2"], (None, {}))
        self.assertEqual(scheduler.choose_batch(["w1"], ["A"], occupied=["A"])["w1"], (None, {}))

    def test_full_matrix_weighted_estimate_includes_failure(self):
        scheduler = self.scheduler()
        scheduler.record("watch", "A", [100], now=100)
        scheduler.record("watch", "A", [], now=101)
        estimate = scheduler._pair_estimates("watch")["A"]
        self.assertAlmostEqual(estimate["normalized_distance"], (.9 * .1 + 1) / 1.9)
        self.assertEqual(estimate["observed_slices"], 2)
        self.assertNotIn("B", scheduler._pair_estimates("watch"))
        scheduler.record("other", "A", [20], now=101)
        self.assertEqual(scheduler._pair_estimates("watch")["A"], estimate)

    def test_each_simulated_second_pushes_one_circular_slice(self):
        scheduler = self.scheduler(window=3)
        for second, distance in enumerate((100, 200, 300, 400), start=100):
            scheduler.record("watch", "A", [distance], now=second)
        snapshot = scheduler.snapshot()
        self.assertEqual(snapshot["timestamps"], [101, 102, 103])
        self.assertEqual([s["watch"]["A"] for s in snapshot["history"]], [200, 300, 400])
        expected = (.81 * .2 + .9 * .3 + .4) / (.81 + .9 + 1)
        self.assertAlmostEqual(scheduler._pair_estimates("watch")["A"]["normalized_distance"], expected)

    def test_idle_seconds_do_not_dilute_distance_but_reduce_confidence(self):
        scheduler = self.scheduler()
        scheduler.record("watch", "A", [100], now=100)
        scheduler._scores("watch", 104)
        estimate = scheduler._pair_estimates("watch")["A"]
        self.assertAlmostEqual(estimate["normalized_distance"], .1)
        self.assertAlmostEqual(estimate["confidence"], .9 ** 4)


if __name__ == "__main__":
    unittest.main()
