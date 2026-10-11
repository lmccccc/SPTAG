"""Bounded fixtures for the full execution/acceptance/publication contract."""

from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import native_results as runner
import select_index
import test_update_results as fixtures
import update_results as store


def write_json(path, value):
    path.write_text(json.dumps(value))


def write_ini(path, sections):
    path.write_text("".join(f"[{name}]\n" + "".join(f"{k}={v}\n" for k, v in values.items()) + "\n"
                            for name, values in sections.items()))


class NativeResultsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.index = self.root / "index"
        self.index.mkdir()
        (self.index / "fixture.dat").write_bytes(b"fixture index identity; not a real ANN index")
        self.truth = self.root / "truth"
        self.truth.mkdir()
        base = np.repeat(np.arange(12, dtype=np.uint8)[:, None], 128, axis=1)
        queries = np.zeros((32, 128), dtype=np.uint8)
        attrs = np.zeros((12, 2), dtype="<u4")
        attrs[:, 0] = np.arange(12) % 2
        for name, values in (("base.bin", base), ("queries.bin", queries)):
            (self.root / name).write_bytes(struct.pack("<ii", *values.shape) + values.tobytes())
        attrs.tofile(self.root / "attrs.bin")
        np.save(self.truth / "queries.npy", queries)
        schema = ["categorical", "numeric"]
        self.workload = dict(schema_version=2, value_type="UInt8", dimension=128, metric="squared L2",
                             dataset="SIFT1B", corpus_count=12, source_vector_count=12, query_count=32,
                             topk=3, base_file=str(self.root / "base.bin"),
                             query_file=str(self.root / "queries.bin"), attribute_source=str(self.root / "attrs.bin"),
                             queries=str(self.truth / "queries.npy"), column_types=schema,
                             scenarios=["scalar", "union", "empty"], predicates={}, native_predicates={},
                             truth={}, protected={}, protected_large={}, scenario_metadata={})
        for name in ("base_file", "query_file", "attribute_source"):
            path = self.workload[name]
            self.workload["protected_large"][path] = runner.identity(path)
        self.workload["protected"][self.workload["queries"]] = store.file_sha(self.workload["queries"])
        expressions = {"scalar": {"categorical_eq": [0, 0]}, "union": {"or": [
            {"categorical_eq": [0, 0]}, {"categorical_eq": [0, 1]}]}, "empty": {"categorical_eq": [0, 9]}}
        for scenario, expression in expressions.items():
            _, clauses = runner.parse_predicate(json.dumps(expression), tuple(schema))
            eligible = np.flatnonzero(runner.matching_rows(clauses, attrs))
            ids = np.full((32, 3), -1, dtype="<i4")
            ids[:, :min(3, len(eligible))] = eligible[:3]
            distances = np.where(ids >= 0, ids.astype("<f4") ** 2 * 128, np.inf).astype("<f4")
            ip, dp = self.truth / f"{scenario}-ids.npy", self.truth / f"{scenario}-distances.npy"
            np.save(ip, ids)
            np.save(dp, distances)
            kind, payload = runner.native_predicate(clauses, tuple(schema), 32)
            predicate = self.truth / f"{scenario}-predicate.npy"
            np.save(predicate, payload)
            self.workload["predicates"][scenario] = expression
            self.workload["native_predicates"][scenario] = dict(kind=kind, file=str(predicate))
            self.workload["protected"][str(predicate)] = store.file_sha(predicate)
            self.workload["truth"][scenario] = dict(ids=str(ip), distances=str(dp),
                ids_sha256=store.file_sha(ip), distances_sha256=store.file_sha(dp),
                candidate_count=len(eligible), selectivity=len(eligible) / 12)
            self.workload["scenario_metadata"][scenario] = dict(
                eligible_count=len(eligible), corpus_count=12, selectivity=len(eligible) / 12)
        self.refresh_truth()
        self.experiment = self.root / "experiment.ini"
        self.batch = self.root / "batch.ini"
        self.run_root = self.root / "run"
        self.registry = self.root / "results"
        self.sections = {"Experiment": dict(Directory=str(self.run_root), Batch=str(self.batch),
            Binary=sys.executable, Workloads=str(self.truth / "workloads.json"),
            Dataset="SIFT1B", IndexVersion="fixture-v5-512", ClientVersion="fixture-native",
            VersionOrder="1", Study="main", Variant="default", Repeats="2", TimeoutSeconds="30",
            CPUNodes="0", MemoryNodes="0", MinimumQPS="3")}
        batch = {"Batch": dict(CaseCount="6", WarmupPolicy="once")}
        for n, (repetition, scenario) in enumerate(
                [(r, s) for r in (1, 2) for s in expressions], 1):
            profile = self.root / f"{scenario}.ini"
            search = dict(isExecute="true", BuildSsdIndex="false", InternalResultNum="4", ResultNum="3",
                NumberOfThreads="1", HashTableExponent="2", MaxCheck="32", MaxDistRatio="8",
                SearchPostingPageLimit="3", DisableCrossEdges="true", LogPhaseTime="false",
                LogPathStats="false", DumpHeads="0", EnableHybridDistance="false",
                EnablePostingNavigation="true", PostingAnchorCount="0", PostingAdditionalMaxCheck="8",
                PostingNavigationWidth="8", PostingMatchRatePercent="12.5", PostingMatchWindow="1024")
            pred = self.workload["native_predicates"][scenario]
            write_ini(profile, {"Benchmark": dict(Index=str(self.index), ValueType="UInt8",
                Queries=self.workload["queries"], Predicate=pred["kind"], PredicateFile=pred["file"],
                MaxQueries="32", Warmup="32"), "SearchSSDIndex": search, "SearchSweep": dict(NProbe="4,8")})
            name = f"Case{n}"
            self.sections[name] = dict(Scenario=scenario, Repetition=str(repetition))
            batch[name] = dict(Config=str(profile), OutputDirectory=str(self.run_root / name))
        write_ini(self.experiment, self.sections)
        write_ini(self.batch, batch)
        self.corrupt = None

    def refresh_truth(self):
        write_json(self.truth / "workloads.json", self.workload)
        write_json(self.truth / "completion.json", dict(state="complete",
            workloads_sha256=store.file_sha(self.truth / "workloads.json")))

    def native_fixture(self, command, **kwargs):
        if command[0] == "ldd":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        plan = runner.load_plan(self.experiment)
        events = [
            dict(event="batch_begin", cases=6, warmup_policy="once", phase_timing=False,
                 value_type="UInt8", index=str(self.index), queries=self.workload["queries"]),
            dict(event="batch_loaded", index_load_count=1, query_corpus_load_count=1),
            dict(event="batch_warmup_begin", queries=32, nprobe=4),
            dict(event="batch_warmup_end", completed_queries=32)]
        for case in plan["cases"]:
            binding = dict(case_id=case["case_id"], config=str(case["config"]), output_directory=str(case["output"]))
            bench = case["profile"]["benchmark"]
            events.append(dict(event="case_begin", **binding, predicate=bench["predicate"],
                predicate_file=bench["predicatefile"], phase_timing=False, measured_queries=32,
                replay_queries=32, warmup_queries=0, search_settings=case["profile"]["searchssdindex"]))
            for probe in (4, 8):
                directory = case["output"] / f"nprobe_{probe}"
                directory.mkdir(parents=True)
                truth = self.workload["truth"][case["scenario"]]
                ids, distances = np.load(truth["ids"]), np.load(truth["distances"])
                ids.astype("<i4").tofile(directory / "ids.i32")
                distances.astype("<f4").tofile(directory / "dist.f32")
                np.zeros((32, 8), dtype="<u8").tofile(directory / "work.u64")
                np.full(32, 10000, dtype="<f8").tofile(directory / "latency_us.f64")
                events.append(dict(event="point", **binding, mode="posting", nprobe=probe,
                    queries=32, topk=3, value_type="UInt8", diagnostic=False, phase_timing=False,
                    measured_queries=32, replay_queries=32, warmup_policy="once", warmup_queries=0,
                    max_check=32, posting_anchor_count=0, posting_additional_max_check=8,
                    posting_navigation_width=8, posting_match_window=1024,
                    search_posting_page_limit=3, posting_match_rate_percent=12.5,
                    qps=100, mean_ms=10, p50_us=10000, p95_us=10000, p99_us=10000,
                    graph_stop_policy="local-match-union-v2", posting_budget_policy="unused-plus-preserved-extra-v2",
                    navigation_schema_version=7, navigation_columns=58, dispatch_schema_version=2, dispatch_columns=6))
            events.append(dict(event="case_end", **binding, completed_points=2))
        events.append(dict(event="batch_end", completed_cases=6, completed_warmup_queries=32))
        if self.corrupt:
            self.corrupt(events)
        log = kwargs["stdout"]
        log.write("Native non-JSON load log\n")
        log.write("".join(json.dumps(e) + "\n" for e in events))
        return subprocess.CompletedProcess(command, 0)

    def execute(self):
        with patch.object(runner.subprocess, "run", side_effect=self.native_fixture), redirect_stdout(io.StringIO()):
            runner.run(self.experiment)

    def test_complete_flow_stable_shapes_and_idempotent_publication(self):
        self.execute()
        complete = runner.read_json(self.run_root / "completion.json")
        self.assertEqual(complete["schema_version"], 1)
        events = runner.read_json(self.run_root / "events.json")
        self.assertEqual(len(events), 6)
        self.assertEqual({e["point"]["topk"] for e in events}, {3})
        self.assertEqual({e["point"]["repeats"] for e in events}, {2})
        self.assertEqual([e["point"]["recall"] for e in events if e["point"]["scenario"] == "empty"], [0, 0])
        store.update(self.registry, [fixtures.catalog()])
        prefix = (self.registry / "history.jsonl").read_bytes()
        with redirect_stdout(io.StringIO()):
            current = runner.publish(self.experiment, self.registry)
            history, materialized = (self.registry / "history.jsonl").read_bytes(), (self.registry / "current.json").read_bytes()
            self.assertEqual(current, runner.publish(self.experiment, self.registry))
        self.assertEqual(history, (self.registry / "history.jsonl").read_bytes())
        self.assertEqual(materialized, (self.registry / "current.json").read_bytes())
        self.assertTrue(history.startswith(prefix))
        self.assertEqual(set(current), {"schema_version", "policy", "history", "catalog", "latest_versions",
                                        "points", "tables", "coverage"})
        self.assertEqual(len(current["points"]), 6)
        with self.assertRaisesRegex(ValueError, "fresh"):
            self.execute()

    def test_catalog_initialization_pins_index_and_emits_views(self):
        runner.initialize(self.experiment, self.registry)
        with self.assertRaisesRegex(ValueError, "reinitialize"):
            runner.initialize(self.experiment, self.registry)
        self.execute()
        with redirect_stdout(io.StringIO()):
            current = runner.publish(self.experiment, self.registry)
        self.assertEqual(current["schema_version"], 1)
        self.assertEqual(len(current["experiments"]["main"]["curves"]), 3)
        self.assertEqual(len(current["experiments"]["ablations"]["curves"]), 3)
        self.assertEqual(current["catalog"]["selected_indexes"][0]["index"], "fixture-v5-512")

    def test_index_label_cannot_hide_a_different_physical_index(self):
        self.execute()
        events = runner.read_json(self.run_root / "events.json")
        store.update(self.registry, [fixtures.catalog()] + events)
        changed = copy.deepcopy(events[0])
        changed["study"] = "ablation"
        changed["version"]["index_identity"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "physical index"):
            store.update(self.registry, [changed])

    def test_bad_lifecycle_never_creates_completion_or_publishes(self):
        self.corrupt = lambda events: events.pop()
        with self.assertRaisesRegex(ValueError, "batch_end"):
            self.execute()
        self.assertTrue((self.run_root / "failure.json").is_file())
        self.assertFalse((self.run_root / "completion.json").exists())
        self.assertFalse(self.registry.exists())
        with self.assertRaisesRegex(ValueError, "failed"):
            runner.publish(self.experiment, self.registry)

    def test_diagnostic_qps_is_rejected(self):
        def corrupt(events):
            next(e for e in events if e["event"] == "point")["diagnostic"] = True
        self.corrupt = corrupt
        with self.assertRaisesRegex(ValueError, "diagnostic"):
            self.execute()

    def test_wrong_distance_rejected(self):
        def corrupt(events):
            path = self.run_root / "Case1/nprobe_4/dist.f32"
            values = np.fromfile(path, dtype="<f4")
            values[1] += 1
            values.tofile(path)
        self.corrupt = corrupt
        with self.assertRaisesRegex(ValueError, "squared L2"):
            self.execute()

    def test_config_change_blocks_publication_without_registry_mutation(self):
        self.execute()
        store.update(self.registry, [fixtures.catalog()])
        previous = (self.registry / "history.jsonl").read_bytes()
        with (self.root / "scalar.ini").open("a") as stream:
            stream.write("; changed after execution\n")
        with self.assertRaisesRegex(ValueError, "Frozen"):
            runner.publish(self.experiment, self.registry)
        self.assertEqual(previous, (self.registry / "history.jsonl").read_bytes())

    def test_capture_tampering_blocks_publication(self):
        self.execute()
        (self.run_root / "Case1/nprobe_4/ids.i32").write_bytes(b"wrong")
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            runner.publish(self.experiment, self.registry)
        self.assertFalse(self.registry.exists())

    def test_wrong_predicate_and_mixed_index_fail_preflight(self):
        self.workload["predicates"]["scalar"] = {"categorical_eq": [0, 1]}
        self.refresh_truth()
        with self.assertRaisesRegex(ValueError, "payload"):
            runner.load_inputs(runner.load_plan(self.experiment))
        profile = self.root / "scalar.ini"
        profile.write_text(profile.read_text().replace(str(self.index), str(self.root / "another-index")))
        with self.assertRaisesRegex(ValueError, "physical index"):
            runner.load_plan(self.experiment)

    def test_incomplete_registration_fails_preflight(self):
        self.sections["Case6"]["Repetition"] = "1"
        write_ini(self.experiment, self.sections)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            runner.load_plan(self.experiment)


class SelectionTest(unittest.TestCase):
    setUp = fixtures.ResultsTest.setUp
    tearDown = fixtures.ResultsTest.tearDown

    def points(self, generation):
        points = [fixtures.measurement(generation=generation, scenario="label") for _ in range(2)]
        for event, probe in zip(points, (96, 192)):
            event["point"].update(topk=100, search_value=probe)
            for row in event["measurements"]:
                row["search_value"] = probe
        return points

    def cells(self):
        return [dict(study="main", variant="default", scenario="label", topk=100,
                     cohort_queries=1000, phase="single", threads=1, expected_repeats=2)]

    def test_pin_retire_and_unknown_index_never_mix(self):
        store.update(self.directory, self.points(1) + self.points(2))
        prefix = (self.directory / "history.jsonl").read_bytes()
        current = select_index.select(self.directory, "SIFT1B", "SPTAG", "index-2", self.cells())
        self.assertTrue((self.directory / "history.jsonl").read_bytes().startswith(prefix))
        self.assertEqual(current["catalog"]["selected_indexes"],
                         [dict(dataset="SIFT1B", algorithm="SPTAG", index="index-2")])
        before = (self.directory / "history.jsonl").read_bytes()
        self.assertEqual(current, select_index.select(self.directory, "SIFT1B", "SPTAG", "index-2", self.cells()))
        self.assertEqual(before, (self.directory / "history.jsonl").read_bytes())
        current = store.update(self.directory, self.points(3))
        self.assertEqual({p["version"]["index"] for p in current["points"]}, {"index-2"})
        self.assertEqual({p["version"]["index"] for p in current["latest_versions"]}, {"index-2"})
        late = self.points(1)[0]
        late["completed_at"] = "2026-10-04T00:00:00Z"
        current = store.update(self.directory, [late])
        self.assertEqual({p["version"]["index"] for p in current["points"]}, {"index-2"})

    def test_missing_curve_rejected_without_mutation(self):
        store.update(self.directory, self.points(1) + self.points(2)[:1])
        before = (self.directory / "history.jsonl").read_bytes()
        with self.assertRaisesRegex(ValueError, "complete replacement curve"):
            select_index.select(self.directory, "SIFT1B", "SPTAG", "index-2", self.cells())
        self.assertEqual(before, (self.directory / "history.jsonl").read_bytes())


if __name__ == "__main__":
    unittest.main()
