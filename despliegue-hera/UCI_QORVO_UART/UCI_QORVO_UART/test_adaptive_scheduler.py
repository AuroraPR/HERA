import unittest

from adaptive_scheduler import AdaptiveAnchorScheduler


class SlidingWindowTests(unittest.TestCase):
    def scheduler(self, window=15):
        return AdaptiveAnchorScheduler(["A", "B"], {}, window_seconds=window)

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
        self.assertTrue(all(slice_["watch"]["A"] == 0 for slice_ in scheduler.snapshot()["history"]))

    def test_temporal_weights_and_late_samples(self):
        scheduler = self.scheduler()
        scheduler.record("watch", "A", [10], now=100)
        scheduler.record("watch", "B", [20], now=101)
        # Igual evidencia, distinto segundo: diferencia exacta 1 - 0.9.
        scores = scheduler._scores("watch", 101)
        self.assertAlmostEqual(scores["B"] - scores["A"], 0.1)
        scheduler.record("watch", "A", [20, 30, 40], now=100.8)
        snapshot = scheduler.snapshot()
        self.assertEqual(snapshot["history"][-2]["watch"]["A"], 25)
        self.assertEqual(snapshot["temporal_weights"][-3:], [0.81, 0.9, 1])
        scheduler.record("watch", "A", [], now=100.9)
        self.assertEqual(scheduler.snapshot()["history"][-2]["watch"]["A"], 25)

    def test_long_gap_and_old_data(self):
        scheduler = self.scheduler()
        scheduler.record("watch", "A", [10], now=100)
        scheduler.record("watch", "B", [], now=200)
        scheduler.record("watch", "A", [999], now=100)
        snapshot = scheduler.snapshot()
        self.assertEqual(len(snapshot["history"]), 15)
        self.assertEqual(snapshot["timestamps"], list(range(186, 201)))
        self.assertTrue(all(slice_["watch"]["A"] == 0 for slice_ in snapshot["history"]))
        self.assertEqual(snapshot["history"][-1]["watch"]["B"], -1)

    def test_single_second_window(self):
        scheduler = self.scheduler(window=1)
        scheduler.record("watch", "A", [10], now=100)
        scheduler.record("watch", "B", [20], now=101)
        self.assertEqual(len(scheduler.snapshot()["history"]), 1)
        self.assertEqual(scheduler.snapshot()["history"][0]["watch"]["A"], 0)


if __name__ == "__main__":
    unittest.main()
