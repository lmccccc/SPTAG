from pathlib import Path
import configparser
import json
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from run_camera_threeway import Campaign, validate_capture, matrix, save, identity, select_curve_points
sys.path.insert(0, str(ROOT / "threeway_native/tests"))
from validate_clients import Fixture, records


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.base = np.array([[0, 0], [1, 0], [2, 0], [3, 0]], dtype=np.uint8)
        self.query = self.base[:1]
        self.attrs = np.zeros((4, 4), dtype=np.uint32)
        self.attrs[:, 2] = [1, 2, 3, 1]
        self.truth = np.array([[0, 1]], dtype=np.uint32)
        self.ids = self.truth.copy()
        self.dist = np.array([[0, 1]], dtype=np.float32)

    def check(self):
        return validate_capture(self.ids, self.dist, self.base, self.query, self.attrs, [1, 2], self.truth)

    def test_or_and_exact_distance(self):
        self.assertEqual(self.check(), 1)
        self.ids[0, 1] = 3
        self.dist[0, 1] = 9
        self.assertEqual(self.check(), .5)

    def test_short_result_keeps_fixed_k_denominator(self):
        self.ids[0, 1] = 0xffffffff
        self.dist[0, 1] = np.nan
        self.assertEqual(self.check(), .5)
        self.ids[0, 0] = 0xffffffff
        self.assertEqual(self.check(), 0)

    def test_invalid_distance_duplicate_nonmatch_and_id(self):
        for kind in ("distance", "duplicate", "nonmatch", "id", "suffix", "nan"):
            with self.subTest(kind=kind):
                self.setUp()
                if kind == "distance":
                    self.dist[0, 1] = 2
                elif kind == "duplicate":
                    self.ids[0, 1] = 0
                elif kind == "nonmatch":
                    self.ids[0, 1], self.dist[0, 1] = 2, 4
                elif kind == "id":
                    self.ids[0, 1] = 4
                elif kind == "suffix":
                    self.ids[0, 0] = 0xffffffff
                else:
                    self.dist[0, 1] = np.nan
                with self.assertRaises(ValueError):
                    self.check()

    def test_identity_detects_changes_and_json_rejects_nan(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save(path, {"state": 1})
            original = identity(path)
            save(path, {"state": 2})
            self.assertNotEqual(identity(path), original)
            with self.assertRaises(ValueError):
                save(path, {"bad": float("nan")})
            self.assertEqual(path.read_text(), '{\n  "state": 2\n}\n')

    def test_curve_selection_limits_and_recall_band(self):
        points = [{"recall": recall, "recall_min_observed": recall, "qps": 1000 * (1.1 - recall),
                   "qps_min": 1000 * (1.1 - recall), "search_value": i + 1}
                  for i, recall in enumerate(np.linspace(.60, .995, 160))]
        points.append({"recall": 1, "recall_min_observed": 1, "qps": 9, "qps_min": 9, "search_value": 1000})
        chosen = select_curve_points(points)
        self.assertEqual(len(chosen), 12)
        self.assertTrue(all(p["qps_min"] >= 10 for p in chosen))
        for target in (.89, .90, .91, .92, .93, .95, .99):
            self.assertTrue(any(target <= p["recall"] < target + .003 for p in chosen))

    def test_recall100_does_not_use_top10_denominator(self):
        base = np.arange(101, dtype=np.uint8).reshape(101, 1)
        attrs = np.zeros((101, 4), dtype=np.uint32)
        attrs[:, 2] = 1
        truth = np.arange(100, dtype=np.uint32).reshape(1, 100)
        ids = np.full((1, 100), 0xffffffff, dtype=np.uint32)
        ids[0, :10] = truth[0, :10]
        distances = np.full((1, 100), np.nan, dtype=np.float32)
        distances[0, :10] = np.arange(10) ** 2
        self.assertEqual(validate_capture(ids, distances, base, base[:1], attrs, [1], truth), .1)

    def test_recall100_profiles_set_both_native_depths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "build.ini"
            build.write_text("[SearchSSDIndex]\nResultNum=10\n")
            campaign = Campaign.__new__(Campaign)
            campaign.topk = 100
            campaign.prepared = root
            campaign.c = {"compact_protocol": True, "minimum_qps": 10, "cpu_nodes": [0],
                          "memory_nodes": [0], "lion_build_ini": str(build), "pipeann_source": str(root),
                          "indexes": dict.fromkeys(("lion", "pipeann", "diskann"), str(root))}
            campaign.w = {"base_file": str(root / "base"), "query_file": str(root / "queries"),
                          "attribute_source": str(root / "attrs"),
                          "truth": {"sel_01pct": {"candidate_count": 10000, "native_ids": str(root / "truth")}}}
            campaign.tags = {"sel_01pct": [1, 17, 18, 19]}
            for engine in ("lion", "pipeann", "diskann"):
                output = root / engine
                output.mkdir()
                campaign.profile(output, engine, ["sel_01pct"], [100, 128], 1024, "single", 1, False)
                ini = configparser.ConfigParser()
                ini.read(output / "profile.ini")
                self.assertEqual(ini.getint("Benchmark", "TopK"), 100)
                if engine == "lion":
                    self.assertEqual(ini.getint("SearchSSDIndex", "ResultNum"), 100)

    def test_recall100_rejects_prior_top10_parameter_hints(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.json"
            path.write_text(json.dumps({"measurements": [{"point": {"topk": 10}}]}))
            campaign = Campaign.__new__(Campaign)
            campaign.topk = 100
            campaign.c = {"previous_results": str(path)}
            with self.assertRaisesRegex(ValueError, "different recall depth"):
                campaign.run_compact()

    def test_baselines_run_first_and_lion_uses_only_two_budgets(self):
        with tempfile.TemporaryDirectory() as directory:
            campaign = Campaign.__new__(Campaign)
            campaign.root = Path(directory)
            (campaign.root / "registration.json").write_text('{"files":[]}')
            campaign.topk = 100
            campaign.names = ["sel_50pct"]
            campaign.tags = {"sel_50pct": [2, 3]}
            campaign.selected, campaign.events, campaign.skipped = [], [], []
            campaign.c = {"engine_order": ["pipeann", "diskann", "lion"],
                          "lion_maxchecks": [1024, 4096], "search_values": [100, 128], "threads": [4, 8]}
            campaign.batch = Mock(return_value=[])
            campaign.report = Mock()
            campaign.status = Mock()
            with patch("run_camera_threeway.registry.update"):
                campaign.run_compact()
            calls = campaign.batch.call_args_list
            self.assertEqual([call.args[0] for call in calls], ["pipeann", "diskann", "lion"])
            settings = calls[-1].kwargs["settings"]
            self.assertEqual({item["maxcheck"] for item in settings}, {1024, 4096})
            self.assertEqual(len(settings), 4)


class NativeProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temporary.name)
        cls.binary = cls.directory / "mock_bench"
        subprocess.run(["g++", "-std=c++17", "-O2", "-fopenmp",
                        str(ROOT / "threeway_native/tests/mock_bench.cpp"), "-o", str(cls.binary)], check=True)
        cls.options_binary = cls.directory / "profile_options"
        subprocess.run(["g++", "-std=c++17", "-O2", "-fopenmp",
                        str(ROOT / "threeway_native/tests/profile_options_test.cpp"),
                        "-o", str(cls.options_binary)], check=True)
        cls.memory_binary = cls.directory / "process_memory"
        subprocess.run(["g++", "-std=c++17", "-O2", "-fopenmp",
                        str(ROOT / "threeway_native/tests/process_memory_test.cpp"),
                        "-o", str(cls.memory_binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def fixture(self):
        return Fixture(self.directory / self._testMethodName)

    def test_resident_and_cumulative_peak_rss_use_bytes(self):
        run = subprocess.run([str(self.memory_binary)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_ready_and_both_query_modes_include_memory(self):
        fixture = self.fixture()
        for phase in ("single", "throughput"):
            fixture.plan(phase, "broad_tag\t0\t0\t1\nbroad_tag\t0\t0\t2\n")
            run = fixture.run(self.binary, phase)
            self.assertEqual(run.returncode, 0, run.stderr)
            ready, = records(run.stdout, "THREEWAY_READY ")
            self.assertGreater(ready["rss_after_load_bytes"], 0)
            previous = ready["peak_rss_bytes"]
            self.assertGreater(previous, 0)
            for row in [ready, *records(run.stdout, "THREEWAY_RESULT ")]:
                self.assertEqual(row["peak_rss_scope"],
                                 "native_process_lifetime_including_load_warmup_validation")
                self.assertGreaterEqual(row["peak_rss_bytes"], previous)
                previous = row["peak_rss_bytes"]
                if "L" in row:
                    self.assertGreater(row["rss_before_queries_bytes"], 0)
                    self.assertGreater(row["rss_after_queries_bytes"], 0)

    def test_or_accepts_second_label(self):
        fixture = self.fixture()
        fixture.change("Kind=categorical\nTag=0", "Kind=categorical_or\nTags=0,2")
        truth = fixture.prepared / "gt_broad_tag.u32bin"
        payload = bytearray(truth.read_bytes())
        struct.pack_into("<I", payload, 12, 2)
        truth.write_bytes(payload)
        fixture.plan("single", "broad_tag\t0\t0\t1\n")
        run = fixture.run(self.binary, "single")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(records(run.stdout, "THREEWAY_RESULT ")[0]["recall"], 1)

    def test_short_prefix_recall_and_suffix_accounting(self):
        fixture = self.fixture()
        fixture.change("Mode=exact", "Mode=short_prefix")
        fixture.change("SingleRepeats=2", "SingleRepeats=2\nAllowShortResults=true")
        fixture.plan("single", "broad_tag\t0\t0\t1\n")
        run = fixture.run(self.binary, "single")
        self.assertEqual(run.returncode, 0, run.stderr)
        result, = records(run.stdout, "THREEWAY_RESULT ")
        self.assertEqual(result["underfilled_queries"], fixture.query_count)
        self.assertEqual(result["invalid_queries"], 0)
        self.assertEqual(result["recall"], .5)

    def test_unconsumed_nonempty_suffix_rejected(self):
        fixture = self.fixture()
        fixture.change("Mode=exact", "Mode=underfill")
        fixture.change("SingleRepeats=2", "SingleRepeats=2\nAllowShortResults=true")
        fixture.plan("single", "broad_tag\t0\t0\t1\n")
        run = fixture.run(self.binary, "single")
        self.assertNotEqual(run.returncode, 0)
        result, = records(run.stdout, "THREEWAY_RESULT ")
        self.assertGreater(result["invalid_ids"], 0)

    def test_slow_cutoff_preserves_repeats_and_explicit_skips(self):
        fixture = self.fixture()
        fixture.change("SingleRepeats=2", "SingleRepeats=2\nMinimumQPS=1000000000000")
        fixture.plan("single", "broad_tag\t0\t0\t1\nbroad_tag\t0\t0\t2\n"
                     "broad_tag\t1\t0\t1\nbroad_tag\t1\t0\t2\n")
        run = fixture.run(self.binary, "single")
        self.assertEqual(run.returncode, 0, run.stderr)
        results = records(run.stdout, "THREEWAY_RESULT ")
        skips = records(run.stdout, "THREEWAY_SKIP ")
        self.assertEqual([r["repeat"] for r in results], [1, 2])
        self.assertEqual(len(skips), 2)
        self.assertTrue(all(r["L"] == 4 and r["slow_L"] == 2 for r in skips))
        self.assertEqual(records(run.stdout, "THREEWAY_COMPLETE ")[0]["completed_jobs"], 2)

    def test_once_warmup_across_parameters_and_repeats(self):
        fixture = self.fixture()
        fixture.change("SingleRepeats=2", "SingleRepeats=2\nWarmupPolicy=once_per_scenario")
        fixture.plan("single", "broad_tag\t0\t0\t1\nbroad_tag\t0\t0\t2\n"
                     "broad_tag\t1\t0\t1\nbroad_tag\t1\t0\t2\nunfilter\t0\t0\t1\n")
        run = fixture.run(self.binary, "single")
        self.assertEqual(run.returncode, 0, run.stderr)
        rows = records(run.stdout, "THREEWAY_RESULT ")
        self.assertEqual([r["warmup_queries"] for r in rows], [8, 0, 0, 0, 8])
        self.assertTrue(all(r["passes"] == 1 and r["queries"] == 8 for r in rows))

    def test_throughput_one_cohort_and_once_per_thread_warmup(self):
        fixture = self.fixture()
        fixture.change("SingleRepeats=2", "SingleRepeats=2\nWarmupPolicy=once_per_scenario")
        fixture.change("MinimumSeconds=0.05", "MinimumSeconds=0")
        fixture.plan("throughput", "broad_tag\t0\t1\t1\nbroad_tag\t0\t1\t2\n"
                     "broad_tag\t1\t1\t1\nbroad_tag\t0\t2\t1\n")
        run = fixture.run(self.binary, "throughput")
        self.assertEqual(run.returncode, 0, run.stderr)
        rows = records(run.stdout, "THREEWAY_RESULT ")
        self.assertEqual([r["warmup_queries"] for r in rows], [8, 0, 0, 8])
        self.assertTrue(all(r["passes"] == 1 and r["queries"] == 8 for r in rows))

    def test_measurement_deadline_emits_no_fabricated_qps(self):
        fixture = self.fixture()
        fixture.change("SingleRepeats=2", "SingleRepeats=2\nMaximumSeconds=0.04")
        fixture.change("WarmupQueries=8", "WarmupQueries=1")
        fixture.change("DelayMicroseconds=500", "DelayMicroseconds=10000")
        fixture.plan("single", "broad_tag\t0\t0\t1\n")
        run = fixture.run(self.binary, "single")
        self.assertEqual(run.returncode, 124, run.stdout + run.stderr)
        self.assertFalse(records(run.stdout, "THREEWAY_RESULT "))
        timeout, = records(run.stderr, "THREEWAY_TIMEOUT ")
        self.assertEqual(timeout["stage"], "measurement")
        self.assertNotIn("qps", timeout)
        self.assertTrue((fixture.native_dir("single") / "timeout.json").exists())

    def test_lion_mixed_maxcheck_plan_preserves_duplicate_nprobe(self):
        fixture = self.fixture()
        fixture.change("[DiskANN]", "[SPANN]\nProtocol=explicit-ini-v1\nNProbe=2,4\n[DiskANN]")
        plan = fixture.output / "plans/SPTAG_adaptive.single.tsv"
        plan.write_text("scenario\tcontrol_index\tthread_index\trepeat\tmaxcheck\n"
                        "broad_tag\t0\t0\t1\t256\nbroad_tag\t0\t0\t1\t1024\n")
        run = subprocess.run([str(self.options_binary), str(fixture.profile), "single", "SPTAG_adaptive"],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('"job_count":2', run.stdout)
        fixture.change("Protocol=explicit-ini-v1", "Protocol=hierarchy-ablation-v1")
        run = subprocess.run([str(self.options_binary), str(fixture.profile), "single", "SPTAG_adaptive"],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('"job_count":2', run.stdout)
        fixture.change("Protocol=hierarchy-ablation-v1", "Protocol=posting-layout-ablation-v1")
        run = subprocess.run([str(self.options_binary), str(fixture.profile), "single", "SPTAG_adaptive"],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('"job_count":2', run.stdout)
        fixture.change("Protocol=posting-layout-ablation-v1", "Protocol=unknown")
        run = subprocess.run([str(self.options_binary), str(fixture.profile), "single", "SPTAG_adaptive"],
                             capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)

    def test_pipeann_low_budget_requires_explicit_short_result_gate(self):
        fixture = self.fixture()
        fixture.change("[DiskANN]", "[PipeANN]\nLSweep=1,4\n[DiskANN]")
        plan = fixture.output / "plans/PipeANN.single.tsv"
        plan.write_text("scenario\tcontrol_index\tthread_index\trepeat\nbroad_tag\t0\t0\t1\n")

        def invoke():
            return subprocess.run([str(self.options_binary), str(fixture.profile), "single", "PipeANN"],
                                  capture_output=True, text=True)
        self.assertNotEqual(invoke().returncode, 0)
        fixture.change("SingleRepeats=2", "SingleRepeats=2\nAllowSearchBelowTopK=true")
        self.assertNotEqual(invoke().returncode, 0)
        fixture.change("AllowSearchBelowTopK=true", "AllowSearchBelowTopK=true\nAllowShortResults=true")
        run = invoke()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('"job_count":1', run.stdout)

    def test_native_top100_counts_99_results_as_point99(self):
        fixture = self.fixture()
        vectors = np.arange(256 * fixture.dimension, dtype=np.uint8).reshape(256, fixture.dimension)
        fixture.vectors.write_bytes(struct.pack("<II", 256, fixture.dimension) + vectors.tobytes())
        fixture.attributes.write_bytes(np.zeros((256, 2), dtype="<u4").tobytes())
        truth = np.tile(np.arange(100, dtype="<u4"), (fixture.query_count, 1))
        (fixture.prepared / "gt_unfilter.u32bin").write_bytes(
            struct.pack("<II", fixture.query_count, 100) + truth.tobytes())
        fixture.change("VectorCount=32", "VectorCount=256")
        fixture.change("TopK=2", "TopK=100")
        fixture.change("Scenarios=unfilter,broad_tag,mixed_dnf", "Scenarios=unfilter")
        fixture.change("CandidateCount=32", "CandidateCount=256")
        fixture.change("LSweep=2,4", "LSweep=100,128")
        fixture.change("Mode=exact", "Mode=short_prefix")
        fixture.change("SingleRepeats=2", "SingleRepeats=2\nAllowShortResults=true")
        fixture.plan("single", "unfilter\t0\t0\t1\n")
        run = fixture.run(self.binary, "single")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result, = records(run.stdout, "THREEWAY_RESULT ")
        self.assertAlmostEqual(result["recall"], .99)
        self.assertEqual(matrix(result["first_ids"], "<u4").shape, (fixture.query_count, 100))


if __name__ == "__main__":
    unittest.main()
