"""Fixed-profile contracts; no native index build or benchmark is run here."""

import configparser
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np


BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS))
import official_benchmark_config as common
import run_sift1b_official as runner
import sift1b_official_inputs as inputs


class OfficialConfigTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.profile = self.root / "profile"
        shutil.copytree(common.DEFAULT_CONFIG.parent, self.profile)
        self.path = self.profile / "benchmark.ini"

    def change(self, section, key, value):
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(self.path)
        parser[section][key] = value
        with self.path.open("w") as stream:
            parser.write(stream)

    def test_canonical_profile(self):
        config = common.load_config()
        self.assertEqual(14, len(config.config_files()))
        self.assertEqual([10, 15, 20, 25, 30, 35, 40, 50, 60, 80, 120, 200, 400],
                         config.integer_list("PipeANN", "LSweep"))
        self.assertEqual(["numactl", "--cpunodebind=2", "--membind=2"], config.affinity())
        for name in common.SCENARIOS[1:]:
            inputs.validate_filter_config(config, name, inputs.scenario_contract(config, name))

    def test_missing_memory_never_changes_unfiltered_memory_l(self):
        self.change("PipeANN", "UnfilteredMemoryL", "0")
        with self.assertRaisesRegex(ValueError, "no missing-file fallback"):
            common.load_config(self.path)

    def test_filtered_memory_entry_is_not_enabled(self):
        self.change("PipeANN", "FilteredMemoryL", "10")
        with self.assertRaisesRegex(ValueError, "Filtered search"):
            common.load_config(self.path)

    def test_reject_unknown_whole_process_single_cpu_setting(self):
        self.change("Execution", "PhysicalCPU", "48")
        with self.assertRaisesRegex(ValueError, "Unknown or missing key"):
            common.load_config(self.path)

    def test_missing_required_key_is_a_configuration_error(self):
        self.path.write_text(self.path.read_text().replace("ResultNum=10\n", ""))
        with self.assertRaisesRegex(ValueError, "Unknown or missing key"):
            common.load_config(self.path)

    def test_reject_environment_overrides_even_when_empty_or_zero(self):
        for name in ("PIPEANN_WARMUP_QUERIES", "SPTAG_NPROBE", "ADDITIONAL_DEFINITIONS", "CXXFLAGS"):
            for value in ("", "0"):
                with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, name):
                    common.reject_environment_overrides({name: value})
        common.reject_environment_overrides({"PATH": "/usr/bin", "HOME": "/tmp", "LD_LIBRARY_PATH": "/usr/lib"})

    def test_snapshots_copy_configuration_bytes_without_rendering(self):
        config = common.load_config(self.path)
        original = {path.relative_to(self.profile): path.read_bytes() for path in config.config_files()}
        with mock.patch.object(configparser.ConfigParser, "write", side_effect=AssertionError("no INI generation")):
            snapshot = runner.copy_config_snapshot(config, self.root / "snapshot")
        self.assertEqual(config.digest(), snapshot.digest())
        self.assertEqual(original, {path.relative_to(snapshot.path.parent): path.read_bytes()
                                    for path in snapshot.config_files()})

    def test_only_l_may_differ_across_spann_points(self):
        point = self.profile / "spann/L0032.ini"
        point.write_text(point.read_text().replace("MaxCheck=1024", "MaxCheck=2048"))
        with self.assertRaisesRegex(ValueError, "may differ only"):
            common.load_config(self.path)

    def test_fixed_filter_change_is_not_silently_regenerated(self):
        config = common.load_config(self.path)
        path = config.path_value("Scenario.numeric", "FilterConfig")
        native = json.loads(path.read_text())
        native["filter"] = "num = 42"
        path.write_text(json.dumps(native))
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "disagrees"):
            inputs.validate_filter_config(config, "numeric", inputs.scenario_contract(config, "numeric"))
        self.assertEqual(before, path.read_bytes())

    def test_partial_inputs_fail_before_writing(self):
        directory = self.root / "partial"
        directory.mkdir()
        sentinel = directory / "keep"
        sentinel.write_bytes(b"existing data")
        self.change("Dataset", "PreparedDirectory", str(directory))
        with self.assertRaisesRegex(ValueError, "partial or foreign"):
            inputs.prepare_inputs(common.load_config(self.path))
        self.assertEqual(b"existing data", sentinel.read_bytes())

    def test_native_range_binding_preserves_zero_and_exact_upper(self):
        path = self.root / "range.spmat"
        inputs.write_binding(path, [0, 2147483648], 3, "range")
        raw = path.read_bytes()
        self.assertEqual((3, 2, 6), struct.unpack("<qqq", raw[:24]))
        payload = np.frombuffer(raw, dtype="<f4", offset=24 + 4 * 8 + 6 * 4).reshape(3, 2)
        np.testing.assert_array_equal(payload, [[0, 2147483648]] * 3)
        with self.assertRaisesRegex(ValueError, "lose precision"):
            inputs.write_binding(self.root / "bad.spmat", [0, 42949675], 3, "range")

    def test_length_prefixed_dnf3_preserves_inclusive_operator(self):
        path = self.root / "predicate.npy"
        row = [7, 0x444E4633, 1, 1, 1, 1, 2, 42949675]
        np.save(path, np.array([row, row], dtype="<u4"))
        self.assertEqual([[(1, 1, 2, 42949675)]], inputs.dnf_clauses(path, 2))
        np.save(path, np.array([row, row[:-1] + [42]], dtype="<u4"))
        with self.assertRaisesRegex(ValueError, "fixed-selectivity"):
            inputs.dnf_clauses(path, 2)

    def test_groundtruth_source_and_thresholds_must_match(self):
        config = common.load_config(self.path)
        workload = {
            "query_count": 1000, "topk": 10,
            "base_file": str(config.path_value("Dataset", "VectorFile")),
            "query_file": str(config.path_value("Dataset", "QueryFile")),
            "numeric_threshold": 42949675, "mixed_threshold": 2147483647,
        }
        with mock.patch.object(inputs, "read_json", return_value=workload):
            self.assertEqual(workload, inputs.validate_workload(config))
        for key, value, message in (
            ("base_file", "/different/base.bin", "different base/query"),
            ("numeric_threshold", 42, "numeric thresholds"),
        ):
            with self.subTest(key=key), mock.patch.object(inputs, "read_json", return_value={**workload, key: value}):
                with self.assertRaisesRegex(ValueError, message):
                    inputs.validate_workload(config)

    def test_jobs_read_fixed_native_files_and_keep_one_query_thread(self):
        config = common.load_config(self.path)
        data = {
            "query_npy": "/fixture/query.npy", "query_bin": "query.u8bin",
            "scenarios": [{"name": name, "kind": config.section(f"Scenario.{name}")["Kind"],
                           "truth_npy": f"/fixture/{name}.npy", "truth_bin": f"{name}.ibin"}
                          for name in common.SCENARIOS],
        }
        binaries = {name: Path("/binaries") / name for name in
                    ("spannaclbench", "search_disk_index", "search_disk_index_filtered")}
        with mock.patch.object(configparser.ConfigParser, "write", side_effect=AssertionError("no generation")):
            jobs = runner.build_jobs(config, data, binaries)
        self.assertEqual(12, len(jobs))
        self.assertEqual(360, sum(point["stage"] == "measured" for job in jobs for point in job["points"]))
        for job in jobs:
            command = job["command"]
            self.assertEqual(["numactl", "--cpunodebind=2", "--membind=2"], command[:3])
            self.assertFalse(any("--physcpubind" in value for value in command))
            if job["engine"] == "PipeANN":
                self.assertEqual("1", command[6])
                self.assertEqual("32", command[7])
                self.assertEqual("10", command[10])
                self.assertEqual("10" if job["scenario"] == "unfilter" else "0", command[14])
                for first, second in zip(job["points"][::2], job["points"][1::2]):
                    self.assertEqual(first["L"], second["L"])
                    self.assertEqual(("warmup", "measured"), (first["stage"], second["stage"]))
            else:
                for point in job["points"]:
                    self.assertTrue(Path(point["value"]).is_file())

    def test_native_errors_and_missing_rows_are_not_success(self):
        path = self.root / "native.log"
        job = {"engine": "SPANN", "scenario": "unfilter",
               "points": [{"L": 96, "value": "fixed.ini", "stage": "measured", "repeat": 1}]}
        path.write_text("")
        with self.assertRaisesRegex(ValueError, "Missing native"):
            runner.parse_results(path, job, 1000)
        result = {"engine": "legacy_label", "failed_queries": 1, "queries": 1000,
                  "search_ini": "fixed.ini", "measure_offset": 0, "search_api": "SearchWithPredicate",
                  "value_type": "UInt8", "recall": 0.0, "qps": 100.0}
        path.write_text(json.dumps(result, separators=(",", ":")) + "\n")
        with self.assertRaisesRegex(ValueError, "query contract failed"):
            runner.parse_results(path, job, 1000)
        result["failed_queries"] = 0
        path.write_text(json.dumps(result, separators=(",", ":")) + "\n")
        self.assertEqual(0.0, runner.parse_results(path, job, 1000)[0]["recall"])
        del result["qps"]
        path.write_text(json.dumps(result, separators=(",", ":")) + "\n")
        with self.assertRaisesRegex(ValueError, "Missing native measurement fields"):
            runner.parse_results(path, job, 1000)

    def test_pipeann_zero_exit_without_valid_recall_rows_is_not_success(self):
        path = self.root / "pipeann.log"
        job = {"engine": "PipeANN", "scenario": "unfilter",
               "points": [{"L": 10, "value": 10, "stage": "measured", "repeat": 1}]}
        for value in (
            {"has_recall": False, "L": 10, "recall_percent": 50, "qps": 100},
            {"has_recall": True, "L": 20, "recall_percent": 50, "qps": 100},
            {"has_recall": True, "L": 10, "recall_percent": 50, "qps": 0},
        ):
            path.write_text("RESULT " + json.dumps(value) + "\n")
            with self.assertRaises(ValueError):
                runner.parse_results(path, job, 1000)

    @unittest.skipUnless(shutil.which("Rscript"), "R renderer requires Rscript")
    def test_summary_and_six_figures_accept_unequal_engine_grids(self):
        config = common.load_config(self.path)
        definitions, rows = [], []
        for name in common.SCENARIOS:
            definitions.append({"name": name, "predicate": "TEST FIXTURE - NOT BENCHMARK DATA",
                                "candidate_count": 1000, "selectivity": 0.000001})
            for engine, budgets in (
                ("PipeANN", config.integer_list("PipeANN", "LSweep")),
                ("SPANN", [runner.search_budget(config.relative_path(path))
                           for path in config.csv("SPANN", "SearchConfigs")]),
            ):
                for point in runner.sequence(budgets, 3, warmup_pair=engine == "PipeANN"):
                    rows.append({"scenario": name, "engine": engine, "L": point["value"],
                                 "queries": 1000, "recall": 0.5, "qps": 100, **point})
        count, points = runner.summarize(config, {"scenarios": definitions}, rows, self.root)
        self.assertEqual((360, 120), (count, points))
        (self.root / "plots").mkdir()
        subprocess.run(["Rscript", str(BENCHMARKS / "plot_sift1b_official.R"), str(self.root)],
                       check=True, capture_output=True, text=True, timeout=90)
        outputs = list((self.root / "plots").iterdir())
        self.assertEqual(12, len(outputs))
        for path in outputs:
            with path.open("rb") as stream:
                prefix = stream.read(8)
            self.assertTrue(prefix.startswith(b"\x89PNG\r\n\x1a\n" if path.suffix == ".png" else b"%PDF-"))


if __name__ == "__main__":
    unittest.main()
