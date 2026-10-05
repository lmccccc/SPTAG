"""Bounded contracts for new parameter measurements and immutable result reuse."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import run_sift1b_threeway as shared
import supplement_sift1b_threeway as supplement


class SupplementTest(unittest.TestCase):
    def setUp(self):
        self.config = shared.load_profile(HERE / "configs/sift1b_threeway/benchmark_unfilter_supplement.ini")
        self.previous = shared.load_profile(HERE / "configs/sift1b_threeway/benchmark_native_short_results.ini")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config.section("Run")["OutputDirectory"] = str(Path(temporary.name) / "supplement")
        self.config.section("Run")["PreparedDirectory"] = str(Path(temporary.name) / "supplement/inputs")

    def test_only_registered_search_grids_and_output_paths_change(self):
        self.assertEqual(4, len(supplement.config_delta(self.previous, self.config)))
        for section, key, value in (
            ("Dataset", "Queries", "/different"), ("DiskANN", "BeamWidth", "4"),
            ("DiskANN", "IndexPrefix", "/rebuilt"), ("PipeANN", "UnfilteredMemoryL", "0"),
            ("PipeANN", "BenchmarkBinary", "/different"), ("Throughput", "Threads", "1,2"),
            ("Throughput", "MinimumSeconds", "5"), ("Benchmark", "QueryCount", "500"),
        ):
            with self.subTest(section=section, key=key):
                original = self.config.section(section)[key]
                self.config.section(section)[key] = value
                with self.assertRaisesRegex(ValueError, "only output paths"):
                    supplement.config_delta(self.previous, self.config)
                self.config.section(section)[key] = original

    def test_single_plan_has_exactly_ten_unfiltered_jobs_and_reversed_repeats(self):
        sizes = []
        for engine, grid in (("PipeANN", [80, 120]), ("Filtered_DiskANN", [800, 1600, 3200])):
            plan = supplement.plan_single(self.config, engine)
            sizes.append(len(plan))
            keys = [shared.resolved_key(self.config, engine, "single", job) for job in plan]
            self.assertEqual([*grid, *reversed(grid)], [key[1] for key in keys])
            self.assertTrue(all(key[0] == "unfilter" and key[2] == 1 for key in keys))
            self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(10, sum(sizes))

    def test_missing_scopes_preserve_valid_pipeann_r90(self):
        previous = [{"engine": "PipeANN", "scenario": "unfilter", "recall_target": "0.9"},
                    {"engine": "SPTAG_adaptive", "scenario": "unfilter", "recall_target": "0.95"}]
        missing = supplement.missing_scopes(self.config, previous)
        self.assertEqual({("PipeANN", "unfilter", 0.95), ("Filtered_DiskANN", "unfilter", 0.9),
                          ("Filtered_DiskANN", "unfilter", 0.95)}, {supplement.scope(row) for row in missing})

    def fresh_points(self):
        return [
            {"engine": engine, "scenario": "unfilter", "L": control,
             "recall_min": recall, "measurement_reused": "false"}
            for engine, control, recall in (
                ("PipeANN", 80, .968), ("PipeANN", 120, .980),
                ("Filtered_DiskANN", 800, .80), ("Filtered_DiskANN", 1600, .92),
                ("Filtered_DiskANN", 3200, .96))]

    def test_only_missing_scopes_get_current_client_operating_points(self):
        missing = supplement.missing_scopes(self.config, [
            {"engine": "PipeANN", "scenario": "unfilter", "recall_target": .9}])
        selected, unavailable = supplement.select_points(self.config, self.fresh_points(), missing)
        self.assertFalse(unavailable)
        self.assertEqual([80, 1600, 3200], [row["L"] for row in selected])
        pipe, _ = shared.plan_throughput(self.config, "PipeANN", selected, {"safe_threads": 48})
        disk, _ = shared.plan_throughput(self.config, "Filtered_DiskANN", selected, {
            "safe_threads": 48, "maximum": 65536, "used": 0, "reserve": 2048, "events_per_thread": 1024})
        self.assertEqual(39, len(pipe))
        self.assertEqual(54, len(disk))
        self.assertTrue(all(row["scenario"] == "unfilter" for row in pipe + disk))

    def test_failed_single_threshold_does_not_widen_grid_or_invent_throughput(self):
        points = self.fresh_points()
        for row in points:
            row["recall_min"] = .7
        selected, unavailable = supplement.select_points(
            self.config, points, supplement.missing_scopes(self.config, []))
        self.assertFalse(selected)
        self.assertEqual(4, len(unavailable))
        self.assertEqual([800, 1600, 3200], self.config.controls("Filtered_DiskANN"))
        points[0]["measurement_reused"] = "true"
        with self.assertRaisesRegex(ValueError, "Only fresh"):
            supplement.select_points(self.config, points, [])

    def test_shared_r90_r95_operating_control_is_measured_once(self):
        points = self.fresh_points()
        points[3]["recall_min"] = .96
        selected, _ = supplement.select_points(
            self.config, points, supplement.missing_scopes(self.config, []))
        plan, _ = shared.plan_throughput(self.config, "Filtered_DiskANN", selected, {
            "safe_threads": 48, "maximum": 65536, "used": 0, "reserve": 2048, "events_per_thread": 1024})
        self.assertEqual(27, len(plan))

    def test_only_superseded_missing_target_annotations_are_removed(self):
        template = dict(phase="throughput", scenario="unfilter", engine="PipeANN",
                        status="recall_target_unmet", reason="prior L60", recall_target="0.95", threads="1")
        old = [template, dict(template, recall_target="0.9"), dict(template, scenario="medium_tag"),
               dict(template, status="aio_limit")]
        keep, superseded = supplement.retain_availability(
            old, [{"engine": "PipeANN", "scenario": "unfilter", "recall_target": .95}])
        self.assertEqual(old[1:], keep)
        self.assertEqual([template], superseded)
        self.assertEqual(4, len(old))

    def test_aggregate_does_not_mix_engines_or_single_client_series(self):
        raw = [dict(engine=engine, scenario="unfilter", phase="single", L=80,
                    repeat=repeat, recall=.96, qps=100 + repeat)
               for engine in supplement.ENGINES for repeat in (1, 2)]
        rows = supplement.aggregate_single(self.config, raw)
        self.assertEqual(2, len(rows))
        self.assertEqual(["pipeann_current", "baseline"], [row["measurement_series"] for row in rows])
        self.assertEqual([101.5, 101.5], [row["qps"] for row in rows])
        with self.assertRaisesRegex(ValueError, "Incomplete supplemental"):
            supplement.aggregate_single(self.config, raw[:-1])
        raw[0]["scenario"] = "broad_tag"
        with self.assertRaisesRegex(ValueError, "escaped"):
            supplement.aggregate_single(self.config, raw)

    def test_history_is_validated_against_its_own_immutable_ini_not_the_new_grid(self):
        with patch.object(shared, "read_history", side_effect=RuntimeError("historical lookup")) as read:
            with self.assertRaisesRegex(RuntimeError, "historical lookup"):
                shared.prepare(self.config, historical_config=self.previous)
            read.assert_called_once_with(self.previous)
        self.previous.section("Dataset")["Queries"] = "/another-cohort"
        with self.assertRaisesRegex(ValueError, "Historical registration changed"):
            shared.prepare(self.config, historical_config=self.previous)

    def test_execution_requires_frozen_code(self):
        with self.assertRaisesRegex(ValueError, "frozen supplemental"):
            supplement.run(Path("/not-a-publication"))


if __name__ == "__main__":
    unittest.main()
