import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from run_postgraph import COUNTERS, validate_navigation


class PostgraphNavigationTest(unittest.TestCase):
    def counters(self):
        values = dict(auxiliary_members=4, auxiliary_first_visits=1,
                      auxiliary_visited_skips=1, auxiliary_unvisited_negative_skips=2,
                      posting_new_candidates=1, posting_activations=1,
                      head_before=1, head_after=2, head_target=4, preserved_heads=1,
                      graph_checked_leaves=8, supplement_checked_leaves=1,
                      supplement_distances=1, supplement_reason=5)
        return {name: np.array([values.get(name, 0)], dtype=np.uint64) for name in COUNTERS}

    def validate(self, counters):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "case/nprobe_4"
            folder.mkdir(parents=True)
            np.array([1, -1, -1, -1], dtype="<i4").tofile(folder / "graph_ids.i32")
            np.array([1, 0, 0, 0], dtype="<f4").tofile(folder / "graph_dist.f32")
            row = dict(case="case", nprobe=4, queries=1, max_check=8, posting_additional_max_check=1)
            with mock.patch("run_postgraph.read_counters", return_value=counters):
                validate_navigation(root, row)
            return row

    def test_terminal_rejections_are_partitioned_but_not_charged(self):
        row = self.validate(self.counters())
        self.assertEqual(row["mean_navigation"]["auxiliary_unvisited_negative_skips"], 2)
        self.assertEqual(row["mean_navigation"]["supplement_checked_leaves"], 1)

    def test_collapsed_negative_representative_can_score_for_live_alias(self):
        counters = self.counters()
        counters["auxiliary_negative_first_visits"][:] = 1
        counters["posting_new_candidates"][:] = 0
        self.validate(counters)

    def test_frozen_all_scored_accounting_remains_valid(self):
        counters = self.counters()
        counters["auxiliary_unvisited_negative_skips"][:] = 0
        counters["auxiliary_first_visits"][:] = 3
        counters["auxiliary_negative_first_visits"][:] = 2
        counters["supplement_checked_leaves"][:] = 3
        counters["supplement_distances"][:] = 3
        self.validate(counters)

    def test_missing_or_double_counted_members_fail(self):
        for members in (3, 5):
            with self.subTest(members=members):
                counters = self.counters()
                counters["auxiliary_members"][:] = members
                with self.assertRaisesRegex(RuntimeError, "Auxiliary reference accounting"):
                    self.validate(counters)

    def test_skipped_negatives_cannot_consume_distance_budget(self):
        counters = self.counters()
        counters["supplement_checked_leaves"][:] = 3
        with self.assertRaisesRegex(RuntimeError, "Supplement scored-node budget"):
            self.validate(counters)

    def test_adaptive_stop_is_reported_even_when_underfilled(self):
        counters = self.counters()
        counters["supplement_reason"][:] = 7
        row = self.validate(counters)
        self.assertEqual(row["supplement_reasons"]["7"], 1)
        self.assertLess(row["mean_navigation"]["head_after"], row["nprobe"])


if __name__ == "__main__":
    unittest.main()
