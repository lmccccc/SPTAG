import configparser
import copy
import mmap
import os
from pathlib import Path
import resource
import sys
import tempfile
import unittest
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run_yfcc_packed_curves as packed


class PackedCurveTests(unittest.TestCase):
    def setUp(self):
        self.selection = packed.load_selection(packed.SELECTION)
        self.ready = dict(rss_after_load_bytes=1024, peak_rss_bytes=1024, peak_rss_scope=packed.MEMORY_SCOPE)

    def rows(self, scenario, threads):
        return [dict(scenario=scenario, threads=threads, L=item["value"], maxcheck=item["maxcheck"],
                     repeat=repeat, recall=.89 + ordinal / 100, qps=100000 * threads / item["value"],
                     underfilled_queries=0, rss_before_queries_bytes=1500, rss_after_queries_bytes=1600,
                     peak_rss_bytes=2048 + ordinal * 64, peak_rss_scope=packed.MEMORY_SCOPE)
                for ordinal, item in enumerate(self.selection[scenario]) for repeat in (1, 2, 3)]

    def points(self):
        return [point for threads in packed.THREADS for scenario in packed.SCENARIOS
                for point in packed.aggregate(scenario, threads, self.selection[scenario],
                                              self.rows(scenario, threads), Path("/fixture"), self.ready, 4096)]

    def test_fixed_grid_and_memory_summary(self):
        points = self.points()
        report = packed.summarize(points, self.selection, {})
        self.assertEqual(len(points), 324)
        self.assertEqual(sum(len(point["measurements"]) for point in points), 972)
        self.assertEqual(report["newly_measured_points"], 324)
        self.assertEqual(report["reused_points"], 0)
        self.assertEqual(len(report["paired"]), 270)
        self.assertEqual(report["memory"]["peak_rss_bytes"], 4096)
        self.assertEqual(report["memory"]["native_processes"], 36)
        self.assertEqual(set(report["memory"]["by_threads"]), {"1", "4", "8", "16", "32", "48"})
        self.assertTrue(all(value["peak_rss_bytes"] == 4096
                            for value in report["memory"]["by_threads"].values()))
        for row in report["targets"]:
            if row["target"] == .99:
                self.assertTrue(all(point is None for point in row["by_threads"].values()))

    def test_missing_duplicate_reused_and_96_thread_points_fail(self):
        points = self.points()
        mutations = [points[:-1], [*points, points[0]]]
        reused = copy.deepcopy(points)
        reused[0]["reused"] = True
        mutations.append(reused)
        wrong_threads = copy.deepcopy(points)
        wrong_threads[0]["threads"] = 96
        mutations.append(wrong_threads)
        for invalid in mutations:
            with self.subTest(count=len(invalid)), self.assertRaises(ValueError):
                packed.summarize(invalid, self.selection, {})

    def test_memory_is_mandatory_and_cumulative(self):
        rows = self.rows(packed.SCENARIOS[0], 1)
        packed.validate_memory(self.ready, rows, 4096)
        for field, value in (("peak_rss_bytes", 0), ("peak_rss_bytes", True),
                             ("rss_before_queries_bytes", -1), ("peak_rss_scope", "per_query")):
            invalid = copy.deepcopy(rows)
            invalid[0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                packed.validate_memory(self.ready, invalid, 4096)
        rows[-1]["peak_rss_bytes"] = 1024
        with self.assertRaisesRegex(ValueError, "cumulative"):
            packed.validate_memory(self.ready, rows, 4096)
        with self.assertRaises(ValueError):
            packed.validate_memory(self.ready, self.rows(packed.SCENARIOS[0], 1), 1024)

    def test_partial_repeats_and_below_floor_are_not_complete(self):
        name = packed.SCENARIOS[0]
        for rows in (self.rows(name, 1)[:-1], self.rows(name, 1)):
            if len(rows) == 27:
                rows[0]["qps"] = 9.99
            with self.assertRaises(ValueError):
                packed.aggregate(name, 1, self.selection[name], rows, Path("/fixture"), self.ready, 4096)

    def test_lion_only_profile_has_three_repeats_and_checked_packed_flag(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            name = "sel_20pct"
            campaign = SimpleNamespace(
                prepared=root / "prepared",
                c=dict(cpu_nodes=[0, 1, 2, 3], memory_nodes=[0, 1, 2, 3], indexes=dict(lion="/immutable/index")),
                w=dict(base_file="/base", query_file="/query", attribute_source="/attrs",
                       truth={name: dict(candidate_count=2000000, native_ids="/truth")}),
                tags={name: [1, 2]})
            for threads in (1, 48):
                directory = root / str(threads)
                directory.mkdir()
                plan = packed.profile(campaign, directory, name, self.selection[name], threads)
                self.assertEqual(len(plan), 27)
                self.assertEqual({row[3] for row in plan}, {1, 2, 3})
                self.assertEqual(len(set(plan)), 27)
                ini = configparser.ConfigParser()
                ini.read(directory / "profile.ini")
                self.assertTrue(ini.getboolean("SearchSSDIndex", "EnablePackedHeadLayout"))
                self.assertEqual(ini.getint("Benchmark", "SingleRepeats"), 3)
                self.assertEqual(ini.getint("Benchmark", "MaximumSeconds"), 100)
                self.assertEqual(ini.getint("Benchmark", "MinimumQPS"), 10)
                self.assertEqual(ini.getint("Throughput", "Threads"), threads)
                self.assertEqual(ini.getint("Throughput", "MinimumSeconds"), 0)
                self.assertFalse(ini.has_option("Scenario." + name, "FilterConfig"))
                self.assertNotIn(" = ", (directory / "profile.ini").read_text())
                self.assertEqual([row[1] for row in plan[:6]], [100] * 6)
                self.assertEqual({row[4] for row in plan[:6]}, {1024, 4096})

    def test_input_preparation_needs_no_baseline_index_or_bindings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign = packed.LionInputs.__new__(packed.LionInputs)
            campaign.prepared = root / "prepared"
            campaign.names = ["sel_01pct"]
            campaign.queries = np.arange(1000 * 192, dtype=np.uint8).reshape(1000, 192)
            campaign.w = dict(truth={"sel_01pct": dict(native_ids=str(root / "truth"))})
            campaign.prepare()
            np.testing.assert_array_equal(packed.matrix(campaign.prepared / "query.u8bin", "<u1"), campaign.queries)
            self.assertEqual({path.name for path in campaign.prepared.iterdir()},
                             {"query.u8bin", "gt_sel_01pct.u32bin"})
            with self.assertRaises(FileExistsError):
                campaign.prepare()

    def test_native_exit_peak_does_not_inherit_controller_rss(self):
        with mmap.mmap(-1, 128 * 1024**2) as allocation, tempfile.TemporaryDirectory() as temporary:
            for offset in range(0, len(allocation), 4096):
                allocation[offset] = 1
            parent_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
            result = packed.run_native(
                Path(temporary), [sys.executable, "-c", "import resource; "
                                  "print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024)"],
                dict(os.environ), [])
            peak = result["peak_rss_bytes"]
            self.assertLess(peak, 64 * 1024**2)
            self.assertGreater(parent_peak - peak, 96 * 1024**2)
            self.assertGreaterEqual(peak, int((Path(temporary) / "native.log").read_text()))
            self.assertEqual(result["argv"][0], str(packed.TIME))


if __name__ == "__main__":
    unittest.main()
