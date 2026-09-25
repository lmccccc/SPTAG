"""Small fixed-control tests; never loads a real ANN index."""

import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS))
import run_spann_memory_diagnostics as diagnostic


class MemoryDiagnosticTest(unittest.TestCase):
    def test_fixed_cases_and_identical_query_work(self):
        plan, config = diagnostic.load_plan(diagnostic.DEFAULT_PLAN)
        inputs = {"query_npy": "/fixture/queries.npy", "scenarios": [
            {"name": "unfilter", "kind": "unfilter", "truth_npy": "/fixture/truth.npy"}]}
        suffix = None
        for case in plan.csv("Diagnostic", "Cases"):
            job = diagnostic.make_job(plan, config, inputs, case)
            self.assertEqual([96, 96, 96], [point["L"] for point in job["points"]])
            self.assertNotIn("--direct-search", job["command"])
            if suffix is None:
                suffix = job["command"][3:]
            self.assertEqual(suffix, job["command"][3:])
        self.assertEqual(["numactl", "--physcpubind=48", "--interleave=2,3"],
                         diagnostic.placement(plan, "legacy_default"))
        self.assertEqual(["numactl", "--cpunodebind=2", "--membind=2"],
                         diagnostic.placement(plan, "local_no_thp"))

    def test_budget_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(diagnostic.DEFAULT_PLAN.parent, root / "sift1b_spann_memory")
            shutil.copytree(BENCHMARKS / "configs/sift1b_official", root / "sift1b_official")
            native = root / "sift1b_spann_memory/search_L0096.ini"
            native.write_text(native.read_text().replace("InternalResultNum=96", "InternalResultNum=128"))
            with self.assertRaisesRegex(ValueError, "preserve"):
                diagnostic.load_plan(root / "sift1b_spann_memory/diagnostic.ini")

    def test_fixed_layer_profiles_preserve_declared_canonical_points(self):
        inputs = {"query_npy": "/fixture/queries.npy", "scenarios": [
            {"name": "unfilter", "kind": "unfilter", "truth_npy": "/fixture/truth.npy"}]}
        for budget in (64, 96):
            with self.subTest(budget=budget):
                plan, config = diagnostic.load_plan(
                    BENCHMARKS / f"configs/sift1b_spann_layers/diagnostic_L{budget:04d}.ini")
                job = diagnostic.make_job(plan, config, inputs, "local_layers")
                self.assertEqual([budget] * 3, [point["L"] for point in job["points"]])
                native = diagnostic.Config(plan.path_value("Diagnostic", "SearchConfig"))
                self.assertTrue(native.section("SearchSSDIndex").getboolean("LogPathStats"))

    def test_layer_profile_cannot_change_its_reference_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("sift1b_spann_layers", "sift1b_official"):
                shutil.copytree(BENCHMARKS / "configs" / name, root / name)
            native = root / "sift1b_spann_layers/search_L0064.ini"
            native.write_text(native.read_text().replace("InternalResultNum=64", "InternalResultNum=96"))
            with self.assertRaisesRegex(ValueError, "preserve"):
                diagnostic.load_plan(root / "sift1b_spann_layers/diagnostic_L0064.ini")

    def test_instrumented_binary_is_explicit_and_hash_pinned(self):
        plan, config = diagnostic.load_plan(
            BENCHMARKS / "configs/sift1b_spann_layers/diagnostic_access_L0064.ini")
        binary, digest = diagnostic.diagnostic_binary(plan, config)
        self.assertNotEqual(config.path_value("SPANN", "BenchmarkBinary"), binary)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("sift1b_spann_layers", "sift1b_official"):
                shutil.copytree(BENCHMARKS / "configs" / name, root / name)
            path = root / "sift1b_spann_layers/diagnostic_access_L0064.ini"
            path.write_text("".join(line for line in path.read_text().splitlines(keepends=True)
                                    if not line.startswith("BenchmarkSHA256=")))
            with self.assertRaisesRegex(ValueError, "both"):
                diagnostic.load_plan(path)

    def test_phase_summary_excludes_warmup_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "native.log"
            path.write_text("".join(f"PhaseTime: post={value} h2=1.0\n"
                                    for value in (100, 100, 2, 4, 100, 100, 6, 8)))
            self.assertEqual({"post": 5.0, "h2": 1.0}, diagnostic.phase_summary([path], 2, 2))
            with self.assertRaisesRegex(ValueError, "Missing"):
                diagnostic.phase_summary([path], 2, 3)

    def test_hierarchy_summary_excludes_warmup_and_rejects_missing_layers(self):
        def row(value):
            layers = []
            for level in range(5, 0, -1):
                counters = "graph_checked=192" if level == 5 else "assignments=12,distances=10"
                layers.append(
                    f"level=H{level},budget=64,candidates=10,eligible=10,retained=10,"
                    f"retained_eligible=10,{counters},graph_ms=0,merge_ms=0,tag_ms=0,"
                    f"vec_ms={value},sort_ms=0")
            return "HierarchyWork: " + " ".join(layers) + "\n"

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "native.log"
            path.write_text("".join(row(value) for value in (100, 100, 2, 4, 100, 100, 6, 8)))
            summary = diagnostic.hierarchy_summary([path], 2, 2)
            self.assertEqual(["H5", "H4", "H3", "H2", "H1"], list(summary))
            self.assertEqual(5.0, summary["H1"]["vec_ms"])
            self.assertEqual(12, summary["H3"]["assignments"])
            self.assertEqual(192, summary["H5"]["graph_checked"])
            instrumented = path.read_text().replace(
                "graph_checked=192", "graph_checked=192,distance_calls=500,graph_rows=96,"
                "visited_checks=800,tree_node_visits=200")
            path.write_text(instrumented)
            self.assertEqual(500, diagnostic.hierarchy_summary([path], 2, 2)["H5"]["distance_calls"])
            path.write_text(instrumented.replace(",visited_checks=800", ""))
            with self.assertRaisesRegex(ValueError, "schema"):
                diagnostic.hierarchy_summary([path], 2, 2)
            path.write_text(instrumented)
            path.write_text(path.read_text().replace("level=H3", "level=H2"))
            with self.assertRaisesRegex(ValueError, "five distinct"):
                diagnostic.hierarchy_summary([path], 2, 2)

    def test_thp_control_is_process_local_and_confirmed(self):
        with mock.patch.object(diagnostic.ctypes, "CDLL") as library:
            library.return_value.prctl.side_effect = [0, 1]
            diagnostic.set_thp_policy("disabled")
            self.assertEqual([mock.call(41, 1, 0, 0, 0), mock.call(42, 0, 0, 0, 0)],
                             library.return_value.prctl.call_args_list)

    def test_resource_snapshot_reads_only_owned_process(self):
        snapshot = diagnostic.process_snapshot(os.getpid())
        self.assertEqual(os.getpid(), snapshot["pid"])
        self.assertGreater(snapshot["memory_kb"]["Rss"], 0)
        self.assertGreaterEqual(snapshot["minor_faults"], 0)
        self.assertGreaterEqual(snapshot["io"]["read_bytes"], 0)
        self.assertGreaterEqual(snapshot["io"]["rchar"], 0)

    def test_exit_sampling_race_waits_for_owned_child_status(self):
        child = mock.Mock()
        child.poll.side_effect = [None, 0]
        child.returncode = None
        def exited(timeout):
            self.assertEqual(5, timeout)
            child.returncode = 0
            return 0
        child.wait.side_effect = exited
        with tempfile.TemporaryDirectory() as temporary, (
            mock.patch.object(diagnostic.subprocess, "Popen", return_value=child)), (
            mock.patch.object(diagnostic, "process_snapshot", side_effect=ProcessLookupError("exiting"))):
            usage = diagnostic.run_sampled_process(["fixture"], temporary, Path(temporary) / "native", 2)
        self.assertIn("ru_inblock", usage)
        self.assertIn("ru_oublock", usage)
        child.terminate.assert_not_called()
        child.wait.assert_called_once_with(timeout=5)


if __name__ == "__main__":
    unittest.main()
