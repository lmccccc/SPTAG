"""Bounded controller contracts; never load a production ANN index."""

import configparser
import copy
import csv
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np


HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import run_sift1b_threeway as runner


class ThreewayControllerTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / "benchmark.ini"
        self.parser = configparser.ConfigParser(interpolation=None)
        self.parser.read(runner.DEFAULT_CONFIG)
        self.parser.remove_option("DiskANN", "LoaderPolicy")
        self.parser["Run"]["OutputDirectory"] = str(self.directory / "campaign")
        self.parser["Run"]["PreparedDirectory"] = str(self.directory / "campaign/inputs")
        self.parser["Run"]["BuildDirectory"] = str(self.directory / "index-build")
        for name in runner.SCENARIOS[1:]:
            self.parser[f"Scenario.{name}"]["FilterConfig"] = str(
                HERE / "configs/sift1b_pipeann_curves/filters" / f"{name}.json")
        self.save()

    def save(self):
        with self.path.open("w") as stream:
            self.parser.write(stream)
        self.config = runner.Profile(self.path)

    def test_profile_and_original_single_thread_placement(self):
        config = runner.load_profile(self.path)
        self.assertEqual(["numactl", "--cpunodebind=3", "--membind=3"], config.affinity("single"))
        self.assertEqual(["numactl", "--cpunodebind=0,1,2,3", "--interleave=0,1,2,3"],
                         config.affinity("throughput"))
        self.assertEqual([0.9, 0.95], config.targets())

    def test_reject_changed_search_policy_and_too_short_duration(self):
        self.parser["SearchSSDIndex"]["MaxCheck"] = "1024"
        self.save()
        with self.assertRaisesRegex(ValueError, "measured adaptive"):
            runner.load_profile(self.path)
        self.parser["SearchSSDIndex"]["MaxCheck"] = "2048"
        self.parser["Throughput"]["MinimumSeconds"] = "29.99"
        self.save()
        with self.assertRaisesRegex(ValueError, "30 seconds"):
            runner.load_profile(self.path)

    def test_reject_duplicate_ini_and_unauthorized_resource_change(self):
        with self.path.open("a") as stream:
            stream.write("\n[Dataset]\nDimension=128\n")
        with self.assertRaises(configparser.DuplicateSectionError):
            runner.load_profile(self.path)
        self.parser["Throughput"]["ResourcePolicy"] = "raise-sysctl"
        self.save()
        with self.assertRaisesRegex(ValueError, "host-wide"):
            runner.load_profile(self.path)

    def test_unknown_profile_keys_and_hidden_runtime_controls_fail(self):
        self.parser["DiskANN"]["Threads"] = "8"
        self.save()
        with self.assertRaisesRegex(ValueError, "Unknown or missing INI key"):
            runner.load_profile(self.path)
        for key in ("OMP_NUM_THREADS", "SPANN_CACHE_SIZE", "DISKANN_L", "MKL_NUM_THREADS", "LD_PRELOAD"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "runtime overrides"):
                runner.reject_benchmark_environment({key: "1"})
        runner.reject_benchmark_environment({"LD_LIBRARY_PATH": "/library/location"})

    def test_aio_cap_is_actual_headroom_not_core_count(self):
        capacity = runner.aio_capacity(self.config, maximum=65536, used=13440)
        self.assertEqual(48, capacity["safe_threads"])
        self.assertEqual(0, runner.aio_capacity(self.config, maximum=65536, used=65536)["safe_threads"])

    def test_single_plan_uses_ordinals_and_preserves_repeated_payloads(self):
        plan = runner.plan_single(self.config)
        self.assertEqual(4 * 13 * 2, len(plan))
        keys = [runner.resolved_key(self.config, "Filtered_DiskANN", "single", row) for row in plan]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertFalse(any(row["scenario"] == "mixed_dnf" for row in plan))
        self.assertEqual(("unfilter", 10, 1, 1), keys[0])
        self.assertEqual(("unfilter", 10, 1, 2), keys[-1])
        self.assertTrue(all(set(row) == set(runner.PLAN_FIELDS) for row in plan))
        (self.config.root / "plans").mkdir(parents=True)
        runner.write_plan(self.config, "Filtered_DiskANN", "single", plan)
        with self.assertRaises(FileExistsError):
            runner.write_plan(self.config, "Filtered_DiskANN", "single", plan)

    def curves(self):
        rows = []
        for engine in runner.ENGINES:
            for name in runner.SCENARIOS:
                if engine == "Filtered_DiskANN" and name == "mixed_dnf":
                    continue
                controls = self.config.controls(engine)
                for index, recall in ((0, 0.8), (3, 0.925), (-1, 0.975)):
                    rows.append({"engine": engine, "scenario": name, "L": controls[index],
                                 "recall_min": recall, "qps": 1000 / controls[index]})
        return rows

    def test_operating_points_use_minimum_recall_not_interpolated_qps(self):
        selected, missing = runner.select_operating_points(self.config, self.curves())
        self.assertEqual(28, len(selected))
        self.assertEqual(2, len(missing))
        self.assertTrue(all(row["status"] == "unsupported_predicate" for row in missing))
        for row in selected:
            controls = self.config.controls(row["engine"])
            self.assertEqual(controls[3] if row["recall_target"] == 0.9 else controls[-1], row["L"])
        curves = self.curves()
        for row in curves:
            if row["engine"] == "PipeANN" and row["scenario"] == "medium_tag":
                row["recall_min"] = min(row["recall_min"], 0.9016)
        _, missing = runner.select_operating_points(self.config, curves)
        self.assertEqual(1, len([row for row in missing if row["engine"] == "PipeANN"]))
        self.assertEqual(0.95, next(row for row in missing if row["engine"] == "PipeANN")["recall_target"])

    def test_throughput_caps_only_native_aio_and_deduplicates_shared_controls(self):
        selected, _ = runner.select_operating_points(self.config, self.curves())
        capacity = runner.aio_capacity(self.config, maximum=65536, used=13440)
        plan, missing = runner.plan_throughput(self.config, "Filtered_DiskANN", selected, capacity)
        self.assertEqual(8 * 9 * 3, len(plan))
        self.assertEqual(4 * 4, len(missing))
        self.assertTrue(all(self.config.threads("throughput")[row["thread_index"]] <= 48 for row in plan))
        other, missing_other = runner.plan_throughput(self.config, "SPTAG_adaptive", selected, capacity)
        self.assertEqual(10 * 13 * 3, len(other))
        self.assertEqual([], missing_other)
        same = [dict(selected[0], recall_target=0.9), dict(selected[0], recall_target=0.95)]
        plan, _ = runner.plan_throughput(self.config, "SPTAG_adaptive", same, capacity)
        self.assertEqual(13 * 3, len(plan))

    def result(self, phase="throughput", repeat=1):
        elapsed = 30.25 if phase == "throughput" else 0.5
        passes = 8 if phase == "throughput" else 1
        return {
            "schema_version": 1, "engine": "Filtered_DiskANN", "phase": phase,
            "scenario": "unfilter", "L": 10, "threads": 1, "repeat": repeat,
            "queries": 1000 * passes, "cohort_queries": 1000, "passes": passes, "warmup_queries": 1000,
            "elapsed_seconds": elapsed, "qps": 1000 * passes / elapsed, "recall": 0.96,
            "first_recall": 0.96, "last_recall": 0.96, "recall_min_batch": 0.96,
            "mean_latency_us": 20, "p50_latency_us": 18, "p99_latency_us": 60, "mean_ios": None,
            "invalid_queries": 0, "latency_quantile_method": "bounded histogram",
            "invalid_ids": 0, "duplicate_ids": 0, "nonmatching_ids": 0, "nonfinite_distances": 0,
            "underfilled_queries": 0, "query_exceptions": 0,
            "first_ids": "/first.ids", "first_distances": "/first.distances",
            "last_ids": "/last.ids", "last_distances": "/last.distances",
        }

    def parse(self, result, plan=None):
        path = self.directory / "native.log"
        path.write_text("native index load\nTHREEWAY_RESULT " + json.dumps(result) + "\n")
        if plan is None:
            plan = [dict(scenario="unfilter", control_index=0, thread_index=0, repeat=1)]
        return runner.parse_results(self.config, "Filtered_DiskANN", result["phase"], path, plan)

    def test_native_parser_checks_actual_duration_and_wall_clock_qps(self):
        self.assertEqual(1, len(self.parse(self.result())))
        for field, value, message in (
            ("elapsed_seconds", 29.99, "shorter"),
            ("qps", 100000, "wall-clock"),
            ("queries", 1001, "accounting"),
            ("threads", True, "integers"),
            ("invalid_queries", 1, "accounting"),
            ("recall", float("nan"), "numeric"),
            ("mean_latency_us", None, "JSON number"),
            ("p99_latency_us", 1, "quantiles"),
            ("latency_quantile_method", "", "documented method"),
        ):
            result = self.result()
            result[field] = value
            if field == "elapsed_seconds":
                result["qps"] = result["queries"] / value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                self.parse(result)

    def test_native_parser_no_missing_jobs_or_success_shaped_failure(self):
        result = self.result("single")
        self.assertEqual(1, len(self.parse(result)))
        second = dict(scenario="unfilter", control_index=1, thread_index=0, repeat=1)
        with self.assertRaisesRegex(ValueError, "Missing native"):
            self.parse(result, [second, dict(second, repeat=2)])
        result["passes"] = 2
        result["queries"] = 2000
        result["qps"] = 2000 / result["elapsed_seconds"]
        with self.assertRaisesRegex(ValueError, "exactly one cohort"):
            self.parse(result)

    def test_every_native_error_counter_is_required_and_zero(self):
        for field in runner.NATIVE_ERROR_COUNTS:
            for value, message in ((1, "accounting"), (-1, "accounting"), (False, "integers"),
                                   (0.0, "integers")):
                result = self.result()
                result[field] = value
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, message):
                    self.parse(result)
            result = self.result()
            del result[field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Missing native error"):
                self.parse(result)

    def test_short_result_parser_keeps_fixed_k_denominator_and_other_errors_fatal(self):
        self.parser["PipeANN"]["AllowShortResults"] = "true"
        self.save()
        result = self.result("single")
        result.update(engine="PipeANN", schema_version=2, result_policy=runner.NATIVE_SHORT_RESULT_POLICY,
                      returned_neighbors=3000, missing_neighbors=7000, underfilled_queries=1000,
                      warmup_underfilled_queries=1000, warmup_missing_neighbors=7000,
                      recall=0.3, first_recall=0.3, last_recall=0.3, recall_min_batch=0.3,
                      first_counts="/first.counts.u64bin", last_counts="/last.counts.u64bin")
        path = self.directory / "short.log"
        plan = [dict(scenario="unfilter", control_index=0, thread_index=0, repeat=1)]

        def parse(value):
            path.write_text("THREEWAY_RESULT " + json.dumps(value) + "\n")
            return runner.parse_results(self.config, "PipeANN", "single", path, plan)

        self.assertEqual([result], parse(result))
        for key, value in (("recall", 1), ("missing_neighbors", 6999), ("returned_neighbors", True),
                           ("underfilled_queries", -1), ("first_counts", ""),
                           ("invalid_ids", 1), ("nonmatching_ids", 1), ("duplicate_ids", 1),
                           ("query_exceptions", 1), ("schema_version", 1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                parse(dict(result, **{key: value}))
        self.parser.remove_option("PipeANN", "AllowShortResults")
        self.save()
        with self.assertRaisesRegex(ValueError, "order/controls"):
            parse(result)

    def admission_fixture(self, phase, pid, scenarios=("unfilter",)):
        self.parser["Dataset"]["VectorCount"] = "32"
        self.parser["Scenario.broad_tag"]["CandidateCount"] = "32"
        prefix = self.directory / "index/sift1b"
        prefix.parent.mkdir(exist_ok=True)
        self.parser["DiskANN"]["IndexPrefix"] = str(prefix)
        self.save()
        sources = []
        for suffix in runner.DISKANN_REQUIRED_SOURCES:
            source = Path(str(prefix) + suffix)
            source.write_bytes(b"1\n" * 32 if suffix == "_disk.index_labels.txt" else b"contract fixture")
            stat = source.stat()
            sources.append({"path": str(source), "resolved": str(source.resolve()), "bytes": stat.st_size,
                            "device": stat.st_dev, "inode": stat.st_ino,
                            "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns})
        filtered = "broad_tag" in scenarios
        certificate = {
            "schema_version": 1, "engine": "Filtered_DiskANN", "guard": "bounded-all-native-starts-v1",
            "source_revision": self.config.section("DiskANN")["SourceRevision"], "phase": phase,
            "status": "admitted", "error": "", "pid": pid, "top_k": 10, "native_points": 32,
            "assumptions_verified": True, "required_all_planned_L_at_least_K": True,
            "required_cache_nodes": 0, "required_frozen_points": 0, "required_reorder": False,
            "required_universal_labels": False, "required_nonempty_dummy_map": False,
            "native_io_limit": 2**32 - 1, "native_search_invocations": 0, "warmup_queries": 0,
            "measured_queries": 0, "native_index_loaded": True,
            "label_source": str(prefix) + "_disk.index_labels.txt", "extra_label_read_passes": 1,
            "label_rows": 32, "label_values": 32, "label_bytes": 64, "label_read_seconds": 0.001,
            "label_fnv1a64": "0123456789abcdef", "temporary_membership_bytes": 8 if filtered else 0,
            "temporary_membership_released": True, "graph_records_read": 10 * len(scenarios),
            "graph_bytes_read": 40 * len(scenarios), "witness_seconds": 0.001,
            "native_load_seconds": 0.01, "pq_distance_upper_bound": 1000,
            "medoid_distance_upper_bound": 1000, "finite_start_selection_verified": True,
            "witness_io": "bounded untimed read-only IO", "elapsed_seconds": 0.05,
            "planned_native_labels": [{"label": 1, "rows": 32}] if filtered else [],
            "sources": sources,
            "absent_sources": [str(prefix) + suffix for suffix in runner.DISKANN_OPTIONAL_SOURCES],
            "witnesses": [
                {"scenario": name, "start": 0, "native_label": None if name == "unfilter" else 1,
                 "required": 10, "reachable_witness_nodes": 10, "graph_records_read": 10,
                 "status": "admitted", "error": "", "nodes": list(range(10)), "parent_nodes": [0] * 10}
                for name in scenarios
            ],
        }
        path = self.config.root / "native/Filtered_DiskANN" / phase / "diskann-admission.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        runner.write_json(path, certificate)
        return path, certificate

    def test_native_completion_requires_full_plan_and_shared_index_capacity(self):
        plan = [dict(scenario="unfilter", control_index=0, thread_index=1, repeat=1)]
        directory = self.config.root / "native/Filtered_DiskANN/throughput"
        directory.mkdir(parents=True)
        ready = {"schema_version": 1, "engine": "Filtered_DiskANN", "phase": "throughput",
                 "job_count": 1, "max_threads": 2, "pid": 1, "load_seconds": 0.1}
        complete = {key: ready[key] for key in ("schema_version", "engine", "phase", "job_count")}
        complete.update(status="completed", completed_jobs=1)
        runner.write_json(directory / "ready.json", ready)
        runner.write_json(directory / "completion.json", complete)
        self.admission_fixture("throughput", 1)
        self.assertEqual(3, len(runner.validate_native_completion(
            self.config, "Filtered_DiskANN", "throughput", plan, 1)))
        ready["max_threads"] = 1
        runner.write_json(directory / "ready.json", ready)
        with self.assertRaisesRegex(ValueError, "resident index"):
            runner.validate_native_completion(self.config, "Filtered_DiskANN", "throughput", plan, 1)
        ready["max_threads"] = 2
        runner.write_json(directory / "ready.json", ready)
        complete["completed_jobs"] = 0
        runner.write_json(directory / "completion.json", complete)
        with self.assertRaisesRegex(ValueError, "exact execution plan"):
            runner.validate_native_completion(self.config, "Filtered_DiskANN", "throughput", plan, 1)
        runner.write_json(directory / "failure.json", {"status": "failed"})
        with self.assertRaisesRegex(ValueError, "failure marker"):
            runner.validate_native_completion(self.config, "Filtered_DiskANN", "throughput", plan, 1)

    def test_native_ready_requires_actual_child_and_numeric_load_time(self):
        plan = [dict(scenario="unfilter", control_index=0, thread_index=0, repeat=1)]
        directory = self.config.root / "native/Filtered_DiskANN/single"
        directory.mkdir(parents=True)
        ready = {"schema_version": 1, "engine": "Filtered_DiskANN", "phase": "single",
                 "job_count": 1, "max_threads": 1, "pid": 1234, "load_seconds": 0.1}
        runner.write_json(directory / "completion.json", {
            "schema_version": 1, "engine": "Filtered_DiskANN", "phase": "single",
            "job_count": 1, "completed_jobs": 1, "status": "completed",
        })
        runner.write_json(directory / "ready.json", ready)
        self.admission_fixture("single", 1234)
        runner.validate_native_completion(self.config, "Filtered_DiskANN", "single", plan, 1234)
        for field, value, message in (
            ("pid", 5678, "launched child"), ("pid", True, "launched child"),
            ("schema_version", True, "resident index"), ("job_count", True, "resident index"),
            ("max_threads", 1.0, "resident index"), ("load_seconds", "0.1", "JSON number"),
            ("load_seconds", False, "JSON number"), ("load_seconds", -1, "numeric"),
        ):
            runner.write_json(directory / "ready.json", {**ready, field: value})
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, message):
                runner.validate_native_completion(self.config, "Filtered_DiskANN", "single", plan, 1234)

    def test_diskann_admission_is_required_and_bound_to_original_load(self):
        path, original = self.admission_fixture("single", 1234)
        plan = [dict(scenario="unfilter", control_index=0, thread_index=0, repeat=1)]
        record = runner.validate_diskann_admission(self.config, "single", plan, 1234, 0.1)
        self.assertEqual(runner.sha256_file(path), record["sha256"])
        for field, value in (
            ("status", "rejected"), ("error", "underfilled"), ("pid", 5678), ("phase", "throughput"),
            ("schema_version", True), ("source_revision", "different"), ("top_k", 9),
            ("native_points", 31), ("assumptions_verified", False), ("native_index_loaded", False),
            ("required_all_planned_L_at_least_K", False), ("required_cache_nodes", 1),
            ("required_frozen_points", True), ("required_reorder", True), ("required_universal_labels", True),
            ("required_nonempty_dummy_map", True), ("native_io_limit", 100), ("native_search_invocations", 1),
            ("warmup_queries", 1), ("measured_queries", 1), ("extra_label_read_passes", 2),
            ("label_rows", 31), ("temporary_membership_released", False),
            ("finite_start_selection_verified", False), ("label_read_seconds", "0.1"),
            ("label_read_seconds", float("inf")), ("elapsed_seconds", 0.2), ("label_values", 31),
            ("label_fnv1a64", "not a hash"), ("label_bytes", 1), ("label_source", "/different_labels.txt"),
            ("temporary_membership_bytes", 8), ("graph_records_read", 11), ("graph_bytes_read", 1),
            ("pq_distance_upper_bound", 1e39),
        ):
            with self.subTest(field=field, value=value):
                path.write_text(json.dumps({**original, field: value}))
                with self.assertRaises(ValueError):
                    runner.validate_diskann_admission(self.config, "single", plan, 1234, 0.1)
        path.unlink()
        with self.assertRaisesRegex(ValueError, "Missing regular"):
            runner.validate_diskann_admission(self.config, "single", plan, 1234, 0.1)
        external = self.directory / "external-admission.json"
        runner.write_json(external, original)
        path.symlink_to(external)
        with self.assertRaisesRegex(ValueError, "Missing regular"):
            runner.validate_diskann_admission(self.config, "single", plan, 1234, 0.1)

    def test_diskann_admission_requires_each_planned_scenario_and_distinct_witnesses(self):
        path, original = self.admission_fixture("single", 1234, ("unfilter", "broad_tag"))
        plan = [dict(scenario=name, control_index=0, thread_index=0, repeat=1)
                for name in ("unfilter", "broad_tag")]
        runner.validate_diskann_admission(self.config, "single", plan, 1234, 0.1)
        first, second = original["witnesses"]
        for witnesses in (
            [], [first], [second], [first, first, second],
            [first, {**second, "status": "rejected"}],
            [first, {**second, "reachable_witness_nodes": 9}],
            [first, {**second, "nodes": list(range(9)) + [8]}],
            [first, {**second, "nodes": [False] + list(range(1, 10))}],
            [first, {**second, "parent_nodes": [0, 5] + [0] * 8}],
            [first, {**second, "native_label": None}],
        ):
            with self.subTest(witnesses=witnesses):
                runner.write_json(path, {**original, "witnesses": witnesses})
                with self.assertRaises(ValueError):
                    runner.validate_diskann_admission(self.config, "single", plan, 1234, 0.1)
        runner.write_json(path, {**original, "planned_native_labels": [{"label": 1, "rows": 31}]})
        with self.assertRaisesRegex(ValueError, "membership counts"):
            runner.validate_diskann_admission(self.config, "single", plan, 1234, 0.1)

    def test_diskann_admission_authenticates_present_and_absent_source_inventory(self):
        path, original = self.admission_fixture("single", 1234)
        plan = [dict(scenario="unfilter", control_index=0, thread_index=0, repeat=1)]
        for field, value in (
            ("sources", original["sources"][:-1]),
            ("sources", original["sources"] + [original["sources"][0]]),
            ("sources", [{**original["sources"][0], "path": "/another-index"}] + original["sources"][1:]),
            ("sources", [{**original["sources"][0], "inode": 0}] + original["sources"][1:]),
            ("sources", [{**original["sources"][0], "ctime_ns": 0}] + original["sources"][1:]),
            ("absent_sources", original["absent_sources"][:-1]),
        ):
            with self.subTest(field=field):
                runner.write_json(path, {**original, field: value})
                with self.assertRaises(ValueError):
                    runner.validate_diskann_admission(self.config, "single", plan, 1234, 0.1)
        runner.write_json(path, original)
        Path(original["absent_sources"][0]).write_bytes(b"unexpected disk PQ")
        with self.assertRaisesRegex(ValueError, "incomplete or changed"):
            runner.validate_diskann_admission(self.config, "single", plan, 1234, 0.1)

    def test_concurrency_recall_failure_is_reported_not_retuned(self):
        native = [self.result(repeat=repeat) for repeat in (1, 2, 3)]
        native[1]["recall_min_batch"] = 0.94
        selected = [{"engine": "Filtered_DiskANN", "scenario": "unfilter", "L": 10,
                     "recall_target": target} for target in (0.9, 0.95)]
        rows, missing = runner.aggregate_throughput(self.config, native, selected)
        self.assertEqual([0.9], [row["recall_target"] for row in rows])
        self.assertEqual([0.95], [row["recall_target"] for row in missing])
        self.assertEqual("recall_target_unmet", missing[0]["status"])
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            runner.aggregate_throughput(self.config, native[:2], selected)

    def test_historical_values_are_not_recomputed(self):
        original = {field: "" for field in runner.SINGLE_FIELDS[:16]}
        original.update(scenario="unfilter", engine="PipeANN", L="60", queries="1000", repeats="2",
                        threads="1", cpu_nodes="3", qps="1001.4123456700", recall="0.951500000")
        merged = runner.aggregate_single(self.config, [original], [])
        self.assertEqual(original, {key: merged[0][key] for key in original})
        self.assertEqual("true", merged[0]["measurement_reused"])

    def small_capture_fixture(self, topk=10):
        count = max(32, topk + 8)
        self.parser["Dataset"]["VectorCount"] = str(count)
        self.parser["Benchmark"]["TopK"] = str(topk)
        self.parser["Dataset"]["Dimension"] = "4"
        self.parser["Dataset"]["Vectors"] = str(self.directory / "base.u8bin")
        self.parser["Dataset"]["Attributes"] = str(self.directory / "attrs.u32")
        self.save()
        self.config.prepared.mkdir(parents=True)
        base = np.arange(count * 4, dtype="u1").reshape(count, 4)
        queries = base[:3]
        attrs = np.zeros((count, 2), dtype="<u4")
        attrs[:, 1] = np.arange(count)
        attrs.tofile(self.config.path_value("Dataset", "Attributes"))
        runner.write_bin(self.config.path_value("Dataset", "Vectors"), base, "u1")
        runner.write_bin(self.config.prepared / "query.u8bin", queries, "u1")
        all_distances = np.sum((base[None].astype(np.int32) - queries[:, None].astype(np.int32)) ** 2, axis=2)
        ids = np.argsort(all_distances, axis=1, kind="stable")[:, :topk].astype("<u4")
        distances = np.take_along_axis(all_distances, ids, axis=1).astype("<f4")
        runner.write_bin(self.config.prepared / "gt_unfilter.u32bin", ids, "<u4")
        result = self.result("single")
        result.update(recall=1.0, first_recall=1.0, last_recall=1.0, recall_min_batch=1.0)
        output = self.config.root / "native/Filtered_DiskANN/single"
        output.mkdir(parents=True)
        for capture in ("first", "last"):
            ids_path, distances_path = output / f"{capture}.ids.bin", output / f"{capture}.distances.bin"
            runner.write_bin(ids_path, ids, "<u4")
            runner.write_bin(distances_path, distances, "<f4")
            result[capture + "_ids"] = str(ids_path)
            result[capture + "_distances"] = str(distances_path)
        return result

    def test_captured_ids_are_checked_against_original_vectors(self):
        result = self.small_capture_fixture()
        reports = runner.validate_captures(self.config, [result])
        self.assertEqual(2, len(reports))
        with Path(result["last_distances"]).open("r+b") as stream:
            stream.seek(8)
            stream.write(np.float32(999).tobytes())
        with self.assertRaisesRegex(ValueError, "original-vector"):
            runner.validate_captures(self.config, [result])

    def test_captures_follow_configured_top100_width(self):
        result = self.small_capture_fixture(topk=100)
        self.assertEqual(2, len(runner.validate_captures(self.config, [result])))
        self.config.section("Benchmark")["TopK"] = "10"
        with self.assertRaisesRegex(ValueError, "Invalid result shape"):
            runner.validate_captures(self.config, [result])

    def test_reused_payloads_and_nonmatching_ids_fail(self):
        result = self.small_capture_fixture()
        result["last_ids"] = result["first_ids"]
        with self.assertRaisesRegex(ValueError, "overwritten/reused"):
            runner.validate_captures(self.config, [result])
        attrs = np.ones((32, 2), dtype="<u4")
        with self.assertRaisesRegex(ValueError, "Nonmatching"):
            runner.validate_ids(self.config, "broad_tag", np.arange(10, dtype="<u4")[None], attrs)

    def test_distinct_capture_names_cannot_alias_one_inode(self):
        result = self.small_capture_fixture()
        last = Path(result["last_ids"])
        last.unlink()
        last.hardlink_to(result["first_ids"])
        with self.assertRaisesRegex(ValueError, "overwritten/reused"):
            runner.validate_captures(self.config, [result])

    def short_capture_fixture(self, topk=10):
        original = self.small_capture_fixture(topk=topk)
        self.parser["PipeANN"]["AllowShortResults"] = "true"
        self.parser["Benchmark"]["QueryCount"] = "3"
        self.parser["Benchmark"]["WarmupQueries"] = "3"
        self.save()
        ids = np.array(runner.matrix(original["first_ids"], "<u4"))
        distances = np.array(runner.matrix(original["first_distances"], "<f4"))
        counts = np.array([0, 3, topk], dtype="<u8")
        for q, count in enumerate(counts):
            ids[q, int(count):] = np.iinfo(np.uint32).max
            distances[q, int(count):] = np.nan
        returned, missing = topk + 3, 2 * topk - 3
        recall = returned / (3 * topk)
        result = dict(original, engine="PipeANN", schema_version=2, queries=3, cohort_queries=3, warmup_queries=3,
                      qps=6, recall=recall, first_recall=recall, last_recall=recall, recall_min_batch=recall,
                      result_policy=runner.NATIVE_SHORT_RESULT_POLICY, returned_neighbors=returned,
                      missing_neighbors=missing, underfilled_queries=2, warmup_underfilled_queries=2,
                      warmup_missing_neighbors=missing)
        output = self.config.root / "native/PipeANN/single"
        output.mkdir(parents=True)
        for capture in ("first", "last"):
            for key, array, dtype in (("ids", ids, "<u4"), ("distances", distances, "<f4"),
                                      ("counts", counts[:, None], "<u8")):
                path = output / f"{capture}.{key}.bin"
                runner.write_bin(path, array, dtype)
                result[capture + "_" + key] = str(path)
        return result

    def test_top100_prefix_counts_use_fixed_100_denominator(self):
        result = self.short_capture_fixture(topk=100)
        checked = runner.validate_captures(self.config, [result])
        self.assertEqual([103, 103], [row["returned_neighbors"] for row in checked])
        self.assertEqual([197, 197], [row["missing_neighbors"] for row in checked])
        self.assertEqual([103 / 300, 103 / 300], [row["recall"] for row in checked])
        with Path(result["last_counts"]).open("r+b") as stream:
            stream.seek(8)
            stream.write(np.uint64(101).tobytes())
        with self.assertRaisesRegex(ValueError, "count above K"):
            runner.validate_captures(self.config, [result])

    def test_native_prefix_counts_accept_zero_short_full_and_preserve_unused_tails(self):
        result = self.short_capture_fixture()
        before = {key: Path(result[key]).read_bytes() for key in
                  ("first_ids", "last_ids", "first_distances", "last_distances", "first_counts", "last_counts")}
        checked = runner.validate_captures(self.config, [result])
        self.assertEqual([13, 13], [row["returned_neighbors"] for row in checked])
        self.assertEqual([17, 17], [row["missing_neighbors"] for row in checked])
        self.assertEqual([13 / 30, 13 / 30], [row["recall"] for row in checked])
        self.assertEqual(before, {key: Path(result[key]).read_bytes() for key in before})
        with Path(result["last_counts"]).open("r+b") as stream:
            stream.seek(8)
            stream.write(np.uint64(11).tobytes())
        with self.assertRaisesRegex(ValueError, "count above K"):
            runner.validate_captures(self.config, [result])

    def test_short_result_prefix_still_rejects_invalid_ids_and_distances(self):
        result = self.short_capture_fixture()
        path = Path(result["last_ids"])
        with path.open("r+b") as stream:
            stream.seek(8 + 10 * 4)
            stream.write(np.uint32(1000).tobytes())
        with self.assertRaisesRegex(ValueError, "Invalid original-row"):
            runner.validate_captures(self.config, [result])

    def test_count_sidecars_cannot_alias_or_hide_nonmatching_returned_ids(self):
        result = self.short_capture_fixture()
        result["last_counts"] = result["first_counts"]
        with self.assertRaisesRegex(ValueError, "overwritten/reused"):
            runner.validate_captures(self.config, [result])
        result["last_counts"] = str(Path(result["last_ids"]).with_name("last.counts.bin"))
        result["scenario"] = "broad_tag"
        shutil.copy2(self.config.prepared / "gt_unfilter.u32bin",
                     self.config.prepared / "gt_broad_tag.u32bin")
        attrs = np.zeros((32, 2), dtype="<u4")
        attrs[1, 0] = 1
        attrs.tofile(self.config.path_value("Dataset", "Attributes"))
        with self.assertRaisesRegex(ValueError, "Nonmatching"):
            runner.validate_captures(self.config, [result])

    def test_legacy_native_client_cannot_silently_enable_short_results(self):
        root, binary, path, _, _ = self.native_build_fixture("PipeANN")
        self.parser["PipeANN"]["AllowShortResults"] = "true"
        self.save()
        with mock.patch.object(runner, "HERE", root), self.assertRaisesRegex(ValueError, "Undeclared"):
            runner.validate_native_build(self.config, "PipeANN", binary, path)

    def test_protected_inventory_detects_symlink_change(self):
        first, second = self.directory / "first", self.directory / "second"
        first.write_bytes(b"old")
        second.write_bytes(b"new")
        link = self.directory / "link"
        link.symlink_to(first)
        record = runner.file_record(link)
        runner.verify_records([record])
        link.unlink()
        link.symlink_to(second)
        with self.assertRaisesRegex(ValueError, "Protected input"):
            runner.verify_records([record])

    def test_native_dependency_closure_requires_resolved_libraries(self):
        output = ("linux-vdso.so.1 (0x001)\n"
                  "libgomp.so.1 => /lib/libgomp.so.1 (0x002)\n"
                  "/lib64/ld-linux-x86-64.so.2 (0x003)\n")
        self.assertEqual({"libgomp.so.1": "/lib/libgomp.so.1",
                          "ld-linux-x86-64.so.2": "/lib64/ld-linux-x86-64.so.2"},
                         runner.parse_native_dependencies(output))
        for bad in ("libaio.so.1 => not found", "not a dynamic executable", ""):
            with self.subTest(output=bad), self.assertRaises(ValueError):
                runner.parse_native_dependencies(bad)

    def test_copied_native_loader_is_inspected_in_its_execution_directory(self):
        resolved = mock.Mock(stdout="libgomp.so.1 => /lib/libgomp.so.1 (0x002)\n")
        with mock.patch.object(runner.subprocess, "run", return_value=resolved) as invoke:
            runner.native_dependencies("/snapshot/client", cwd=self.directory)
        invoke.assert_called_once_with(["ldd", "/snapshot/client"], text=True, capture_output=True,
                                       check=True, cwd=self.directory)

    def native_build_fixture(self, engine):
        source_root = self.directory / "sources"
        native = source_root / "threeway_native"
        native.mkdir(parents=True, exist_ok=True)
        stem = runner.SECTIONS[engine].lower()

        def artifact(path, payload):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            return {"path": str(path.resolve()), "bytes": len(payload), "sha256": runner.sha256_file(path)}

        source_names = (f"{stem}_bench.cpp", "benchmark.h", f"build_{stem}_client.py")
        if engine == "Filtered_DiskANN":
            source_names += ("diskann_admission.h",)
        sources = [artifact(native / name, ("fixture " + name).encode()) for name in source_names]
        binary = self.directory / engine / "client"
        binary_entry = artifact(binary, b"unit-test client provenance, not an executable measurement")
        compiler = Path(sys.executable).resolve()
        manifest = {
            "schema_version": 1, "engine": engine, "binary": binary_entry,
            "compiler": {"path": str(compiler), "sha256": runner.sha256_file(compiler)},
            "compiler_version": "unit-test compiler identity",
            "command": [str(compiler), sources[0]["path"]],
        }
        if engine == "Filtered_DiskANN":
            root = self.directory / "diskann-original"
            library = artifact(root / "build/install/lib/libdiskann.a", b"original diskann fixture")
            self.parser["DiskANN"]["SourceDirectory"] = str(root)
            self.parser["DiskANN"]["Library"] = library["path"]
            manifest.update(source_directory=str(root), source_clean=True,
                            source_revision=self.parser["DiskANN"]["SourceRevision"],
                            sources=sources, library=library)
            libraries = [library]
        elif engine == "SPTAG_adaptive":
            root = self.directory / "spann-original/normal"
            reference = artifact(root / "bin/nativeBench", b"measured fixture reference")
            libraries = [artifact(root / "bin" / name, name.encode()) for name in
                         ("libSPTAGLibStatic.a", "libDistanceUtils.a", "libRaBitQ2Lib.a", "libzstd.a")]
            libraries.append(artifact(
                root / "AnnService/CMakeFiles/nativeBench.dir/__/Wrappers/src/CoreInterface.cpp.o",
                b"original wrapper fixture"))
            manifest.update(status="complete", adapter_source=sources[0], benchmark_header=sources[1],
                            build_entry=sources[2], source_dependencies=sources,
                            frozen_artifacts=[reference, *libraries],
                            measured_nativeBench_sha256=reference["sha256"])
        else:
            root = self.directory / "pipeann-original"
            (root / "source").mkdir(parents=True, exist_ok=True)
            self.parser["PipeANN"]["SourceDirectory"] = str(root / "source")
            libraries = [artifact(root / "build/src/libpipeann.a", b"original pipeann fixture"),
                         artifact(root / "deps/liburing.a", b"original uring fixture")]
            manifest.update(core_recompiled=False, inputs={Path(entry["path"]).name: entry for entry in sources},
                            libraries=dict(zip(("pipeann", "uring"), libraries)))
            manifest["command"] += ["-DREAD_ONLY_TESTS", "-DNO_MAPPING", "-DUSE_URING", "-DUSE_TCMALLOC"]
        manifest["command"] += [entry["path"] for entry in libraries]
        manifest_path = binary.with_name("client.build.json")
        runner.write_json(manifest_path, manifest)
        self.save()
        return source_root, binary, manifest_path, manifest, libraries

    def test_all_native_build_manifests_authenticate_original_core_and_sources(self):
        for engine in runner.ENGINES:
            root, binary, path, manifest, libraries = self.native_build_fixture(engine)
            with self.subTest(engine=engine), mock.patch.object(runner, "HERE", root):
                verified = runner.validate_native_build(self.config, engine, binary, path)
                self.assertEqual(4 if engine == "Filtered_DiskANN" else 3, len(verified["sources"]))
                self.assertEqual(manifest["binary"]["sha256"], verified["binary"]["sha256"])
                manifest["command"].remove(libraries[0]["path"])
                runner.write_json(path, manifest)
                with self.assertRaisesRegex(ValueError, "link its authenticated original core"):
                    runner.validate_native_build(self.config, engine, binary, path)

    def test_diskann_build_requires_the_shared_admission_source(self):
        root, binary, path, manifest, _ = self.native_build_fixture("Filtered_DiskANN")
        with mock.patch.object(runner, "HERE", root):
            header = root / "threeway_native/diskann_admission.h"
            payload = header.read_bytes()
            header.write_bytes(payload + b"changed after compilation")
            with self.assertRaisesRegex(ValueError, "artifact hash changed"):
                runner.validate_native_build(self.config, "Filtered_DiskANN", binary, path)
            header.write_bytes(payload)
            manifest["sources"] = manifest["sources"][:-1]
            runner.write_json(path, manifest)
            with self.assertRaisesRegex(ValueError, "Missing compiled native source identity"):
                runner.validate_native_build(self.config, "Filtered_DiskANN", binary, path)
        self.assertIn("diskann_admission.h", runner.NATIVE_CODE_FILES)
        self.assertIn("diskann_build_admission.cpp", runner.NATIVE_CODE_FILES)
        self.assertIn("build_diskann_admission.py", runner.NATIVE_CODE_FILES)

    def test_loader_build_keeps_original_library_and_authenticates_helpers(self):
        root, binary, path, manifest, libraries = self.native_build_fixture("Filtered_DiskANN")
        corrected = self.directory / "corrected-libdiskann.a"
        corrected.write_bytes(b"fixture approved corrected library")
        policy = self.directory / "loader-policy.ini"
        policy.write_text("fixture policy validated by the mocked proof reader\n")
        self.parser["DiskANN"]["LoaderPolicy"] = str(policy)
        self.save()
        binding = {"library": str(corrected), "path": str(policy)}
        original = libraries[0]
        manifest.update(
            original_library=original, library=runner.identity(corrected, True), loader_policy=binding,
            status="complete", inputs_captured_before_compile=True, inputs_verified_unchanged_after_compile=True)
        manifest["command"][-1] = str(corrected)
        for name in ("diskann_loader_policy.h", "loader_policy.py"):
            source = root / "threeway_native" / name
            source.write_text("fixture helper " + name)
            manifest["sources"].append(runner.identity(source, True))
            self.assertIn(name, runner.NATIVE_CODE_FILES)
        runner.write_json(path, manifest)
        validated = {"binding": binding, "proof": {"original_library": original}, "identities": []}
        with mock.patch.object(runner, "HERE", root), mock.patch.object(
                runner.native_loader_provenance, "validate", return_value=validated) as verify:
            build = runner.validate_native_build(self.config, "Filtered_DiskANN", binary, path)
            self.assertEqual([str(corrected), original["path"]],
                             [entry["path"] for entry in build["native_artifacts"]])
            self.assertEqual(6, len(build["sources"]))
            self.assertEqual(Path(original["path"]), verify.call_args.kwargs["original_library"])
            manifest["sources"][-1]["sha256"] = "0" * 64
            runner.write_json(path, manifest)
            with self.assertRaisesRegex(ValueError, "artifact hash changed"):
                runner.validate_native_build(self.config, "Filtered_DiskANN", binary, path)
            manifest["sources"].pop()
            runner.write_json(path, manifest)
            with self.assertRaisesRegex(ValueError, "Missing compiled native source identity"):
                runner.validate_native_build(self.config, "Filtered_DiskANN", binary, path)

    def test_pipeann_correction_requires_exact_proof_recipe_and_helper_closure(self):
        from threeway_native import pipeann_page_lifetime as repair

        root, binary, path, manifest, libraries = self.native_build_fixture("PipeANN")
        original = self.directory / "original-pipeann.a"
        original.write_bytes(b"preserved original archive")
        source = self.config.path_value("PipeANN", "SourceDirectory")
        binding = {"mode": repair.MODE, "source": str(source), "library": libraries[0]["path"]}
        manifest.update(native_correctness=binding, original_library=runner.identity(original, True),
                        status="complete", inputs_captured_before_compile=True,
                        inputs_verified_unchanged_after_compile=True, build_binary=manifest["binary"])
        common = manifest["command"][:1] + manifest["command"][2:6]
        link = [entry["path"] for entry in libraries]
        manifest["command"] = [
            *common, '-DTHREEWAY_PIPEANN_SOURCE="' + str(source) + '"',
            '-DTHREEWAY_PIPEANN_LIBRARY_SHA256="' + libraries[0]["sha256"] + '"',
            str(root / "threeway_native/pipeann_bench.cpp"), *link, "-o", str(binary),
        ]
        for name in ("build_pipeann_page_lifetime.py", "pipeann_page_lifetime.py", "pipeann_page_lifetime.patch"):
            helper = root / "threeway_native" / name
            helper.write_text("bounded proof helper fixture\n")
            manifest["inputs"][name] = runner.identity(helper, True)
            self.assertIn(name, runner.NATIVE_CODE_FILES)
        runner.write_json(path, manifest)
        correction = {"binding": binding, "proof": {"original_library": manifest["original_library"]},
                      "protected": []}
        with mock.patch.object(runner, "HERE", root), mock.patch.object(
                repair, "validate", return_value=correction), mock.patch.object(
                repair, "native_recipe", return_value=(common, link, {})):
            checked = runner.validate_native_build(self.config, "PipeANN", binary, path)
            self.assertEqual(6, len(checked["sources"]))
            self.assertEqual(original.resolve(), Path(checked["native_artifacts"][-1]["path"]))
            manifest["command"].insert(1, "-DUNREGISTERED_SEARCH_CHANGE")
            runner.write_json(path, manifest)
            with self.assertRaisesRegex(ValueError, "compiler/linker recipe changed"):
                runner.validate_native_build(self.config, "PipeANN", binary, path)
            manifest["command"].pop(1)
            manifest["inputs"].pop("pipeann_page_lifetime.patch")
            runner.write_json(path, manifest)
            with self.assertRaisesRegex(ValueError, "Missing compiled native source identity"):
                runner.validate_native_build(self.config, "PipeANN", binary, path)

    def build_validation_fixture(self):
        _, nested = self.admission_fixture("build-validation", 1234, ("broad_tag",))
        root = self.config.path_value("DiskANN", "IndexPrefix").parent
        inputs = self.directory / "build-inputs"
        inputs.mkdir()
        runner.write_bin(inputs / "base.u8bin", np.zeros((32, 4), dtype="u1"), "u1")
        runner.write_bin(inputs / "query.u8bin", np.zeros((2, 4), dtype="u1"), "u1")
        attributes = np.zeros((32, 2), dtype="<u4")
        attributes[12:24, 0], attributes[24:, 0] = 9, 200
        (inputs / "attributes.u32").write_bytes(attributes.tobytes())
        (inputs / "counts.tsv").write_text("attribute_id\tcount\n0\t12\n9\t12\n200\t8\n")
        runner.write_json(inputs / "attributes.json", {
            "vector_count": 32, "dimension": 4, "attribute_columns": 2, "limited_tag_column": 0,
            "files": {"counts": {"sha256": runner.sha256_file(inputs / "counts.tsv")}},
        })
        for name in ("prepared_labels.txt", "prepared_labels.json", "preflight.json",
                     "reuse_pq_pivots.bin", "reuse_pq_compressed.bin", "truth.npy", "libdiskann.a"):
            (inputs / name).write_bytes(b"bounded schema fixture")
        (inputs / "bin").mkdir()
        for name in ("build_memory_index", "create_disk_layout", "search_disk_index"):
            (inputs / "bin" / name).write_bytes(("fixture " + name).encode())
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_dict({
            "Inputs": {"Vectors": str(inputs / "base.u8bin"), "Queries": str(inputs / "query.u8bin"),
                       "Attributes": str(inputs / "attributes.u32"), "AttributeColumns": "2",
                       "CategoricalColumn": "0", "Counts": str(inputs / "counts.tsv"),
                       "AttributeManifest": str(inputs / "attributes.json"),
                       "PreparedLabels": str(inputs / "prepared_labels.txt"),
                       "PreparedLabelsManifest": str(inputs / "prepared_labels.json"),
                       "PQPrefix": str(inputs / "reuse")},
            "DiskANN": {"SourceDirectory": str(inputs), "BinaryDirectory": str(inputs / "bin"),
                        "SourceRevision": nested["source_revision"], "LabelType": "uint"},
            "Run": {"OutputDirectory": str(root), "ValidationK": "10", "ValidationL": "32",
                    "ValidationThreads": "1", "ValidationQueriesPerLabel": "2", "ValidationLabels": "0",
                    "PreflightDiagnostics": str(inputs / "preflight.json")},
            "Truth": {"0": str(inputs / "truth.npy")},
        })
        with (root / "config.ini").open("w") as stream:
            parser.write(stream)
        source = root / "build_categorical_from_pq.py"
        source.write_text("# Bounded provenance fixture; not an executable builder.\n")
        originals = [path for path in inputs.iterdir() if path.is_file() and path.name != "libdiskann.a"]
        runner.write_json(root / "manifest.json", {
            "native_source_revision": nested["source_revision"],
            "config_sha256": runner.sha256_file(root / "config.ini"), "runner_sha256": runner.sha256_file(source),
            "vectors": 32, "dimension": 4, "categorical_only": True, "disk_pq": False,
            "universal_label": None, "graph_reused": False, "search_pq_reused": True,
            "inputs": {str(path): runner.original_build_identity(path) for path in originals},
            "binaries": {path.name: {"identity": runner.original_build_identity(path),
                                     "sha256": runner.sha256_file(path)} for path in (inputs / "bin").iterdir()},
        })
        context = runner.DiskANNBuildValidation(root / "config.ini")
        nested["temporary_membership_bytes"] = 16
        nested["planned_native_labels"] = [{"label": 1, "rows": 12}, {"label": 2, "rows": 12}]
        first = nested["witnesses"][0]
        first["scenario"] = "label_0"
        nested["witnesses"].append({
            **first, "scenario": "label_9", "native_label": 2, "start": 12,
            "nodes": list(range(12, 22)), "parent_nodes": [12] * 10})
        nested["graph_records_read"], nested["graph_bytes_read"] = 20, 80
        certificate = {
            "schema_version": 1, "engine": "Filtered_DiskANN",
            "purpose": "original-all-label-build-validation-admission", "status": "admitted", "error": "",
            "pid": 1234, "native_search_invocations": 0, "warmup_queries": 0, "measured_queries": 0,
            "config": str(context.config.path), "source_revision": context.revision,
            "linked_library": str(inputs / "libdiskann.a"), "index_prefix": str(context.prefix),
            "stock_search_binary": str(inputs / "bin/search_disk_index"),
            "vector_count": 32, "dimension": 4, "attribute_columns": 2, "categorical_column": 0,
            "validation_k": 10, "validation_l": 32, "validation_threads": 1,
            "beam_width": 2, "cache_nodes": 0, "native_io_limit": 2**32 - 1,
            "stock_warmup_enabled": False, "automatic_beam_tuning": False, "reorder": False,
            "validation_queries_per_configured_label": 2, "planned_stock_query_calls": 3,
            "selection_policy": "configured labels in INI order, then other count>=K labels in numeric order",
            "native_index_loaded": True, "converted_labels_verified": True,
            "aio": {"used": 0, "maximum": 65536, "required": 1024, "events_per_thread": 1024},
            "elapsed_seconds": 0.1, "configured_labels": [0],
            "selected_labels": [
                {"external_label": label, "source_count": 12, "configured": label == 0,
                 "planned_queries": queries, "scenario": f"label_{label}", "native_label": native, "native_count": 12}
                for label, queries, native in ((0, 2, 1), (9, 1, 2))],
            "skipped_below_k": [{"external_label": 200, "source_count": 8}],
            "input_identities": [runner.build_input_record(path, hashed)
                                 for path, hashed in context.input_hashes.items()],
            "admission": nested,
        }
        path = root / "build-admission.json"
        runner.write_json(path, certificate)
        return context, path, certificate, inputs / "libdiskann.a"

    def test_all_label_admission_binds_original_ini_and_every_selected_label(self):
        context, path, _, library = self.build_validation_fixture()
        runner.validate_build_admission(context, path, 1234, library)
        self.assertEqual([(0, 2), (9, 1)], context.groups)
        command = context.stock_command(self.directory)
        for flag, value in (("-K", "10"), ("-L", "32"), ("-T", "1"), ("-W", "2"), ("--num_nodes_to_cache", "0")):
            self.assertEqual(value, command[command.index(flag) + 1])

    def test_all_label_admission_rejects_unsafe_or_incomplete_certificates(self):
        context, path, original, library = self.build_validation_fixture()
        mutations = [
            ("status", "rejected"), ("pid", 1235), ("native_search_invocations", 1),
            ("validation_k", True), ("validation_l", 400), ("validation_threads", 2),
            ("stock_warmup_enabled", True), ("automatic_beam_tuning", True), ("cache_nodes", 1),
            ("native_io_limit", 256), ("planned_stock_query_calls", 2), ("converted_labels_verified", False),
            ("configured_labels", [9]), ("skipped_below_k", []), ("elapsed_seconds", float("inf")),
            ("selected_labels", original["selected_labels"][:1]),
            ("input_identities", original["input_identities"][:-1]),
        ]
        for field, value in mutations:
            changed = copy.deepcopy(original)
            changed[field] = value
            path.write_text(json.dumps(changed))
            with self.subTest(field=field), self.assertRaises(ValueError):
                runner.validate_build_admission(context, path, 1234, library)
        for mutation in ("mapping", "missing-witness", "underfill", "input-identity"):
            changed = copy.deepcopy(original)
            if mutation == "mapping":
                changed["selected_labels"][1]["native_label"] = 3
            elif mutation == "missing-witness":
                changed["admission"]["witnesses"].pop()
            elif mutation == "underfill":
                changed["admission"]["witnesses"][1]["reachable_witness_nodes"] = 9
            else:
                changed["input_identities"][0]["ctime_ns"] = 0
            runner.write_json(path, changed)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                runner.validate_build_admission(context, path, 1234, library)

    def test_all_label_admission_rechecks_original_inputs_and_config(self):
        context, path, _, library = self.build_validation_fixture()
        query = context.config.path_value("Inputs", "Queries")
        query.write_bytes(query.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "admission input changed"):
            runner.validate_build_admission(context, path, 1234, library)
        context.config.path.write_text(context.config.path.read_text() + "\n; changed\n")
        with self.assertRaisesRegex(ValueError, "INI/controller authentication"):
            runner.DiskANNBuildValidation(context.config.path)

    def test_native_build_rejects_newer_header_and_modified_core(self):
        for engine in runner.ENGINES:
            root, binary, path, manifest, libraries = self.native_build_fixture(engine)
            with self.subTest(engine=engine), mock.patch.object(runner, "HERE", root):
                header = root / "threeway_native/benchmark.h"
                original = header.read_bytes()
                header.write_bytes(original + b"changed after compilation")
                with self.assertRaisesRegex(ValueError, "artifact hash changed"):
                    runner.validate_native_build(self.config, engine, binary, path)
                header.write_bytes(original)
                Path(libraries[0]["path"]).write_bytes(b"replaced core")
                with self.assertRaisesRegex(ValueError, "artifact hash changed"):
                    runner.validate_native_build(self.config, engine, binary, path)

    def publication_fixture(self, stage):
        source = self.config.root / f"plot_inputs_{stage}"
        output = self.config.root / f"plots_{stage}"
        source.mkdir(parents=True)
        output.mkdir()
        registration = {"dataset": "SIFT1B", "scenarios": list(runner.SCENARIOS), "engines": list(runner.ENGINES)}
        runner.write_json(source / "registration.json", registration)
        inputs = runner.plot_input_files(stage)
        for filename in inputs.values():
            if filename.endswith(".csv"):
                runner.write_csv(source / filename, ("scenario",), [{"scenario": "unfilter"}])
        stems = ["single_thread_recall_qps"]
        if stage == "complete":
            stems += ["throughput_scaling", "throughput_peak"]
        outputs = []
        for stem in stems:
            for extension, magic in (("png", b"\x89PNG\r\n\x1a\n"), ("pdf", b"%PDF-")):
                path = output / f"{stem}.{extension}"
                path.write_bytes(magic + b"contract fixture, not a rendered experimental figure")
                outputs.append(path)
        if stage == "complete":
            path = output / "peak_points.csv"
            runner.write_csv(path, ("scenario",), [{"scenario": "unfilter"}])
            outputs.append(path)
        metadata = {
            "schema_version": 1, "status": "completed", "stage": stage, "dataset": "SIFT1B",
            "registration": registration,
            "scenarios": list(runner.SCENARIOS), "engine_order": list(runner.ENGINES),
            "all_measured_points_retained": True, "interpolated": False, "smoothed": False, "extrapolated": False,
            "hash_algorithm": "md5", "input_files": {name: str(source / filename) for name, filename in inputs.items()},
            "input_hashes": {name: runner.hashlib.md5((source / filename).read_bytes()).hexdigest()
                             for name, filename in inputs.items()},
            "output_hashes": {path.name: runner.hashlib.md5(path.read_bytes()).hexdigest() for path in outputs},
            "single_points": 1, "throughput_points": 1 if stage == "complete" else None,
            "peak_points": 1 if stage == "complete" else None,
        }
        runner.write_json(output / "plot_metadata.json", metadata)
        return source, output, metadata

    def test_plot_publications_require_successful_renderer_status(self):
        for stage in ("single", "complete"):
            _, output, metadata = self.publication_fixture(stage)
            for status in ("failed", "running", True, None):
                with self.subTest(stage=stage, status=status):
                    if status is None:
                        metadata.pop("status")
                    else:
                        metadata["status"] = status
                    runner.write_json(output / "plot_metadata.json", metadata)
                    with self.assertRaisesRegex(ValueError, "renderer did not complete"):
                        runner.validate_plot_publication(self.config, stage)
            metadata["status"] = "completed"
            runner.write_json(output / "plot_metadata.json", metadata)
            runner.validate_plot_publication(self.config, stage)

    def test_plot_publications_keep_stage_specific_input_snapshots(self):
        for stage in ("single", "complete"):
            source, output, _ = self.publication_fixture(stage)
            with self.subTest(stage=stage):
                report = runner.validate_plot_publication(self.config, stage)
                self.assertEqual(3 if stage == "single" else 8, len(report["outputs"]))
                (self.config.root / "availability.csv").write_text("later campaign availability\n")
                runner.verify_identities(report["inputs"] + report["outputs"])
                runner.validate_plot_publication(self.config, stage)
                (source / "availability.csv").write_text("altered publication input\n")
                with self.assertRaisesRegex(ValueError, "input snapshot differs"):
                    runner.validate_plot_publication(self.config, stage)

    def test_plot_publications_require_outputs_and_matching_counts(self):
        _, output, metadata = self.publication_fixture("complete")
        metadata["throughput_points"] = 2
        runner.write_json(output / "plot_metadata.json", metadata)
        with self.assertRaisesRegex(ValueError, "count mismatch"):
            runner.validate_plot_publication(self.config, "complete")
        metadata["throughput_points"] = 1
        runner.write_json(output / "plot_metadata.json", metadata)
        (output / "throughput_peak.pdf").unlink()
        with self.assertRaisesRegex(ValueError, "Missing plot publication"):
            runner.validate_plot_publication(self.config, "complete")

    def test_frozen_python_snapshot_has_complete_local_imports(self):
        directory = self.directory / "frozen"
        directory.mkdir()
        for name in runner.CODE_FILES:
            shutil.copy2(HERE / name, directory / name)
        runner.verify_snapshot_imports(directory)

    def test_large_dependency_content_is_protected_beyond_its_header(self):
        path = self.directory / "library.so"
        path.write_bytes(b"x" * ((1 << 20) + 8))
        record = runner.file_record(path, True)
        self.assertIn("sha256", record)
        runner.verify_records([record])
        before = path.stat()
        with path.open("r+b") as stream:
            stream.seek(-1, 2)
            stream.write(b"y")
        runner.os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "Protected input"):
            runner.verify_records([record])

    def campaign(self):
        self.config.root.mkdir()
        runner.write_json(self.config.root / "manifest.json", {"controller": {
            "pid": 123, "start_ticks": 45, "command_sha256": "abc"}})
        return runner.Campaign(self.config)

    def test_build_failure_or_pid_reuse_stops_before_native_execution(self):
        campaign = self.campaign()
        build = self.config.path_value("Run", "BuildDirectory")
        build.mkdir()
        runner.write_json(build / "status.json", {"state": "running"})
        with mock.patch.object(runner, "process_identity", return_value={"pid": 123, "start_ticks": 46}), (
            self.assertRaisesRegex(ValueError, "PID was reused")
        ):
            campaign.wait_for_build()
        runner.write_json(build / "failure.json", {"error": "builder failed"})
        with self.assertRaisesRegex(ValueError, "construction failed"):
            campaign.wait_for_build()

    def test_partial_build_is_not_mistaken_for_completion(self):
        campaign = self.campaign()
        build = self.config.path_value("Run", "BuildDirectory")
        build.mkdir()
        runner.write_json(build / "completion.json", {
            "state": "completed", "vectors": 16783, "original_inputs_unchanged": True, "smoke_only": True,
            "index_prefix": str(build / "sift1b"),
        })
        with self.assertRaisesRegex(ValueError, "registered production"):
            campaign.wait_for_build()

    def test_production_build_completion_and_original_method_are_required(self):
        campaign = self.campaign()
        build = self.config.path_value("Run", "BuildDirectory")
        build.mkdir()
        completion = {
            "state": "completed", "vectors": 10**9, "original_inputs_unchanged": True, "smoke_only": False,
            "index_prefix": str(self.config.path_value("DiskANN", "IndexPrefix")),
            "validation": {"exact_result_distances": True, "all_labels_match": True,
                           "no_duplicate_ids": True, "distinct_labels_checked": 201},
        }
        source = {
            "native_source_revision": self.config.section("DiskANN")["SourceRevision"],
            "categorical_only": True, "universal_label": None, "disk_pq": False,
            "graph_reused": False, "search_pq_reused": True,
            "graph_parameters": {"R": "64", "L": "1", "FilteredL": "100"},
        }
        runner.write_json(build / "completion.json", completion)
        runner.write_json(build / "manifest.json", source)
        with mock.patch.object(runner, "index_files", return_value=[]), \
                mock.patch.object(runner, "validate_guarded_build_completion",
                                  return_value={"protected": []}) as guard:
            campaign.wait_for_build()
        guard.assert_called_once_with(self.config, build, completion)
        saved = runner.read_json(self.config.root / "index_completion.json")
        self.assertEqual(completion, saved["completion"])
        source["graph_reused"] = True
        runner.write_json(build / "manifest.json", source)
        with self.assertRaisesRegex(ValueError, "build method changed"):
            campaign.wait_for_build()

    def test_stock_validation_claim_without_admission_is_not_completion(self):
        with self.assertRaisesRegex(ValueError, "lacks guarded all-label"):
            runner.validate_guarded_build_completion(
                self.config, self.config.path_value("Run", "BuildDirectory"), {"state": "completed"})

    def test_fresh_output_refusal_precedes_any_input_or_native_work(self):
        self.config.root.mkdir()
        sentinel = self.config.root / "keep"
        sentinel.write_bytes(b"existing results")
        with self.assertRaisesRegex(ValueError, "existing campaign"):
            runner.prepare(self.config)
        self.assertEqual(b"existing results", sentinel.read_bytes())


if __name__ == "__main__":
    unittest.main()
