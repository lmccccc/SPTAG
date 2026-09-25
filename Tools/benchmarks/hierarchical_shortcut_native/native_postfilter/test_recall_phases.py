import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

from run_full import sha
from run_recall_phases import measured_phases, summarize


class PhaseAttributionTest(unittest.TestCase):
    def test_warmup_and_replay_are_not_measurements(self):
        def row(graph, post):
            return dict(nprobe=96, bkt=0, pq=0, graphOther=graph, post=post,
                        io=post / 2, scan=post / 4, postOther=post / 4, total=graph + post)
        values = [row(999, 999)] * 2 + [row(2, 1), row(4, 3)] + [row(777, 777)] * 2
        result = measured_phases(values, 2, 96)
        self.assertEqual(result["mean_ms"]["ram_navigation"], 3)
        self.assertEqual(result["mean_ms"]["post"], 2)
        self.assertEqual(result["mean_ms"]["total"], 5)
        self.assertEqual(result["measured_rows"], 2)

    def test_incomplete_mixed_or_inconsistent_rows_fail(self):
        row = dict(nprobe=96, bkt=0, pq=0, graphOther=2, post=1, io=.5, scan=.3, postOther=.2, total=3)
        with self.assertRaises(RuntimeError):
            measured_phases([row] * 5, 2, 96)
        with self.assertRaises(RuntimeError):
            measured_phases([row] * 6, 2, 384)
        bad = dict(row, total=8)
        with self.assertRaises(RuntimeError):
            measured_phases([bad] * 6, 2, 96)

    def test_complete_phase_summary_counts_case_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "unfilter/nprobe_96"
            folder.mkdir(parents=True)
            ids = np.tile(np.arange(10, dtype="<i4"), (1000, 1))
            ids.tofile(folder / "ids.i32")
            np.ones((1000, 10), dtype="<f4").tofile(folder / "dist.f32")
            np.zeros((1000, 8), dtype="<u8").tofile(folder / "work.u64")
            np.save(root / "attributes.npy", np.zeros((10, 2), dtype=np.uint32))
            np.save(root / "truth.npy", ids.astype("<i8"))
            workload = dict(attributes=str(root / "attributes.npy"), predicates={"unfilter": None},
                            truth={"unfilter": {"ids": str(root / "truth.npy")}})
            (root / "workloads.json").write_text(json.dumps(workload))
            reference = dict(scenario="unfilter", variant="graph", nprobe=96, qps=1000,
                payload_hashes={name: sha(folder / name) for name in ("ids.i32", "dist.f32", "work.u64")})
            (root / "accepted").mkdir()
            (root / "accepted/plain-results.json").write_text(json.dumps([reference]))
            (root / "budget").mkdir()
            (root / "budget/plain-results.json").write_text("[]")
            case = dict(case="unfilter", config=str(root / "native.ini"),
                        scenario="unfilter", variant="graph", probes=[96])
            registration = dict(workload=str(root / "workloads.json"), accepted_campaign=str(root / "accepted"),
                                budget_probe=str(root / "budget"), cases=[case])
            point = dict(mode="graph", nprobe=96, phase_timing=True, diagnostic=False,
                warmup_queries=1000, measured_queries=1000, replay_queries=1000,
                config=case["config"], output_directory=str(folder.parent))
            phase = "PhaseTime: nprobe=96 bkt=0 pq=0 graphOther=2 post=1 io=.5 scan=.3 postOther=.2 total=3\n"
            (root / "native.log").write_text(phase * 3000 + json.dumps(point) + "\n")
            with mock.patch("run_recall_phases.check", return_value=(root, registration)):
                summarize(root / "unused.ini")
            result = json.loads((root / "results.json").read_text())
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["mean_ms"]["ram_navigation"], 2)
            self.assertTrue(result[0]["ordinary_payload_parity"])
            self.assertFalse(result[0]["timing_accepted"])


if __name__ == "__main__":
    unittest.main()
