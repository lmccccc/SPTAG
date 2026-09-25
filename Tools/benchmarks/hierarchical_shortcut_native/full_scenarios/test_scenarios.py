"""Small offline fixtures for exact-result validation and underfill accounting."""
import importlib.util
from pathlib import Path
import unittest

import numpy as np

spec = importlib.util.spec_from_file_location("shortcut_scenario_runner", Path(__file__).with_name("run.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class ScenarioFixtures(unittest.TestCase):
    def test_native_predicate_truth_tables(self):
        attrs = np.array([[0, 1], [9, 30], [200, 80], [199, 5]], dtype=np.uint32)
        mask = runner.predicate_mask({"or": [
            {"categorical_eq": [0, 200]},
            {"and": [{"categorical_eq": [0, 199]}, {"numeric_le": [1, 10]}]}
        ]}, attrs)
        self.assertEqual(mask.tolist(), [False, False, True, True])
        self.assertEqual(runner.predicate_mask({"numeric_le": [1, 5]}, attrs).tolist(),
                         [True, False, False, True])

    def test_underfilled_and_empty_results(self):
        data = [{"results": [[2, 1.0]] + [[-1, 3.4e38]] * 9},
                {"results": [[-1, 3.4e38]] * 10}]
        truth = np.array([[2] + [-1] * 9, [2] + [-1] * 9])
        result = runner.query_quality(data, truth, np.array([False, False, True]))
        self.assertEqual(result["underfilled_queries"], 2)
        self.assertEqual(result["empty_queries"], 1)
        self.assertEqual(result["mean_returned"], 0.5)
        self.assertEqual(result["recall_at_10"], 0.05)

    def test_exact_filter_and_dedup_fail_closed(self):
        truth = np.array([[0] * 10])
        with self.assertRaises(ValueError):
            runner.query_quality([{"results": [[0, 0.0]]}], truth, np.array([False]))
        with self.assertRaises(ValueError):
            runner.query_quality([{"results": [[0, 0.0], [0, 0.0]]}], truth, np.array([True]))


if __name__ == "__main__":
    unittest.main()
