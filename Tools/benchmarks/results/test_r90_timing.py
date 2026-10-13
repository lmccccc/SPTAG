"""Bounded acceptance fixtures; these do not run a dataset-scale experiment."""
from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import native_results as native
import r90_timing as timing
import test_native_results
import test_update_results as fixtures
import update_results as store


class TimingTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "diagnostics"
        self.root.mkdir()
        self.registry = self.base / "registry"
        events = [fixtures.catalog()]
        for scenario in timing.SCENARIOS:
            event = fixtures.measurement(scenario=scenario)
            event["point"]["topk"] = 100
            events.append(event)
        retained = fixtures.measurement()
        del retained["point"], retained["measurements"]
        retained.update(kind="table", study="ablation", variant="retained", table_name="other",
                        status="validated", rows=[dict(value=7)])
        events.append(retained)
        self.before = store.update(self.registry, events)
        self.selected = timing.selected_points(self.before, "SIFT1B", "index-1")
        native.save(self.root / "selected.json", self.selected)
        self.group = dict(name="client1", directory=str(self.root), reference=str(self.base / "reference.ini"),
                          build=str(self.base / "build.json"), binary=sys.executable,
                          batch=str(self.root / "batch.ini"), cpu_nodes="0", memory_nodes="0",
                          timeout_seconds=60, cases=[])
        native.save(self.base / "build.json", {})
        for point in self.selected:
            scenario = point["scenario"]
            original = self.base / scenario / "nprobe_96"
            original.mkdir(parents=True)
            np.tile(np.arange(100, dtype="<i4"), (1000, 1)).tofile(original / "ids.i32")
            np.zeros((1000, 100), dtype="<f4").tofile(original / "dist.f32")
            np.zeros((1000, 8), dtype="<u8").tofile(original / "work.u64")
            for repeat in (1, 2, 3):
                for mode in (("control", "timing") if repeat % 2 else ("timing", "control")):
                    case_id = "Case" + str(len(self.group["cases"]) + 1)
                    config = self.root / f"{case_id}.ini"
                    timing.write_ini(config, dict(Benchmark=dict(
                        Predicate="categorical", PredicateFile=str(self.base / f"{scenario}.npy")),
                        SearchSSDIndex=dict(MaxCheck="2048")))
                    self.group["cases"].append(dict(case_id=case_id, scenario=scenario, mode=mode, repeat=repeat,
                        config=str(config), output=str(self.root / case_id), nprobe=96,
                        reference_output=str(original.parent), source_event_id=point["event_id"]))
        self.plan = dict(groups=[self.group], dataset="SIFT1B", index_version="index-1",
                         registry=str(self.registry))
        native.save(self.root / "plan.json", self.plan)
        native.save(self.root / "registration.json", dict(files={
            str(path): native.frozen(path) for path in self.root.iterdir()}))
        self.reference = dict(index=self.base / "index")
        self.inputs = dict(workload=dict(queries=str(self.base / "queries.npy")))
        self.reference_mock = patch.object(timing, "verify_reference",
            return_value=(self.reference, self.inputs, []))
        self.reference_mock.start()
        self.addCleanup(self.reference_mock.stop)
        self.corrupt = None

    def execute_native(self, command, **kwargs):
        events = [dict(event="batch_begin", cases=36, warmup_policy="once_per_predicate",
                       index=str(self.reference["index"]), queries=self.inputs["workload"]["queries"]),
                  dict(event="batch_loaded", index_load_count=1, query_corpus_load_count=1)]
        warmed = set()
        for case in self.group["cases"]:
            binding = {key: case[key] for key in ("case_id", "config")}
            binding["output_directory"] = case["output"]
            cfg = native.read_config(case["config"])
            if case["scenario"] not in warmed:
                events.extend([dict(event="scenario_warmup_begin", **binding, queries=1000,
                    warmup_group="categorical\n" + cfg["benchmark"]["predicatefile"]),
                    dict(event="scenario_warmup_end", completed_queries=1000)])
                warmed.add(case["scenario"])
            enabled = case["mode"] == "timing"
            common = dict(**binding, warmup_queries=0, measured_queries=1000, replay_queries=1000,
                          step_timing=enabled, phase_timing=enabled)
            events.append(dict(event="case_begin", **common, search_settings=cfg["searchssdindex"]))
            events.append(dict(event="point", **common, queries=1000, topk=100, nprobe=96,
                diagnostic=True, step_timing_schema_version=1, step_timing_columns=15,
                warmup_policy="once_per_predicate", value_type="UInt8"))
            folder = Path(case["output"]) / "nprobe_96"
            folder.mkdir(parents=True)
            original = Path(case["reference_output"]) / "nprobe_96"
            for name in ("ids.i32", "dist.f32", "work.u64"):
                (folder / name).write_bytes((original / name).read_bytes())
            np.full(1000, 2000 if enabled else 1000, dtype="<f8").tofile(folder / "latency_us.f64")
            if enabled:
                np.full((1000, 15), 100000, dtype="<u8").tofile(folder / "steps.ns.u64")
            events.append(dict(event="case_end", **binding, completed_points=1))
        events.append(dict(event="batch_end", completed_cases=36, completed_warmup_queries=6000))
        if self.corrupt:
            self.corrupt(events)
        kwargs["stdout"].write("".join(json.dumps(event) + "\n" for event in events))
        return subprocess.CompletedProcess(command, 0)

    def execute(self):
        with patch.object(timing.subprocess, "run", side_effect=self.execute_native), redirect_stdout(io.StringIO()):
            timing.run(self.root)

    def publish(self):
        with redirect_stdout(io.StringIO()):
            timing.publish(self.root, self.registry, 1)

    def test_full_flow_preserves_performance_and_other_tables_and_is_idempotent(self):
        self.execute()
        report = native.read_json(self.root / "report.json")
        self.assertEqual(len(report["rows"]), 6)
        for row in report["rows"]:
            self.assertAlmostEqual(sum(row["stage_mean_ms"].values()), row["total_mean_ms"])
            self.assertAlmostEqual(sum(row["stage_share_percent"].values()), 100)
            self.assertEqual(row["paired_clock_on_over_off_ratio"], [2, 2, 2])
        self.publish()
        after = native.read_json(self.registry / "current.json")
        for key in ("points", "coverage"):
            self.assertEqual(self.before[key], after[key])
        self.assertTrue(all(table in after["tables"] for table in self.before["tables"]))
        history = (self.registry / "history.jsonl").read_bytes()
        self.publish()
        self.assertEqual(history, (self.registry / "history.jsonl").read_bytes())

    def test_selection_is_fastest_observed_and_index_isolated(self):
        snapshot = copy.deepcopy(self.before)
        fastest = copy.deepcopy(snapshot["points"][0])
        fastest.update(qps=200, event_id="fast")
        under = copy.deepcopy(fastest)
        under.update(recall=.899, qps=1000, event_id="under")
        other = copy.deepcopy(fastest)
        other.update(qps=2000, event_id="other")
        other["version"]["index"] = "other-index"
        snapshot["points"].extend([under, other, fastest])
        picked = timing.selected_points(snapshot, "SIFT1B", "index-1")
        self.assertIn("fast", [point["event_id"] for point in picked])
        snapshot["points"] = [point for point in snapshot["points"] if point["scenario"] != "sel01"]
        with self.assertRaisesRegex(ValueError, "No accepted observed R90"):
            timing.selected_points(snapshot, "SIFT1B", "index-1")

    def test_prepare_binds_selected_sources_and_generates_full_paired_schedule(self):
        reference_root = self.base / "accepted-reference"
        reference_root.mkdir()
        native.save(reference_root / "completion.json", dict(state="complete"))
        reference_path = self.base / "accepted.ini"
        reference_path.write_text("Fixture reference\n")
        cases, events = [], []
        for scenario in timing.SCENARIOS:
            source = next(case for case in self.group["cases"] if case["scenario"] == scenario)
            profile = native.read_config(source["config"])
            profile["benchmark"].update(maxqueries="1000", warmup="1000")
            cases.append(dict(config=Path(source["config"]), profile=profile))
            event = fixtures.measurement(scenario=scenario)
            event["point"]["topk"] = 100
            for measurement in event["measurements"]:
                measurement.update(config=source["config"], output_directory=source["reference_output"])
            events.append(event)
        registry = self.base / "prepare-registry"
        store.update(registry, [fixtures.catalog(), *events])
        reference = dict(self.reference, root=reference_root, path=reference_path, cases=cases,
            policy=dict(dataset="SIFT1B", indexversion="index-1", cpunodes="0",
                        memorynodes="0", timeoutseconds="60"))
        build = self.base / "prepare-build.json"
        native.save(build, dict(clients=dict(timing=dict(binary=sys.executable))))
        root = self.base / "prepared"
        config = self.base / "r90.ini"
        timing.write_ini(config, dict(R90=dict(Directory=str(root), DataDirectory=str(registry),
            Dataset="SIFT1B", IndexVersion="index-1", Clients="Client1"),
            Client1=dict(Experiment=str(reference_path), Build=str(build))))
        inputs = dict(workload=dict(self.inputs["workload"], base_file=str(self.base / "base.bin"),
                                    attribute_source=str(self.base / "attributes.bin")))
        for name in ("queries", "base_file", "attribute_source"):
            Path(inputs["workload"][name]).write_bytes(b"immutable input binding fixture")
        with patch.object(timing, "verify_reference", return_value=(reference, inputs, events)), \
                patch.object(timing.subprocess, "check_output", return_value=""), redirect_stdout(io.StringIO()):
            timing.prepare(config)
        plan = native.read_json(root / "plan.json")
        self.assertEqual(len(plan["groups"]), 1)
        group = plan["groups"][0]
        self.assertEqual(len(group["cases"]), 36)
        self.assertEqual(native.read_config(group["batch"])["batch"]["warmuppolicy"], "once_per_predicate")
        for case in group["cases"]:
            cfg = native.read_config(case["config"])
            self.assertEqual(cfg["searchsweep"]["nprobe"], "96")
            self.assertEqual(cfg["benchmark"]["warmup"], "1000")
            self.assertEqual(cfg["benchmark"]["steptiming"], str(case["mode"] == "timing").lower())
        native.verify_files(native.read_json(root / "registration.json")["files"])
        with self.assertRaisesRegex(ValueError, "fresh"):
            timing.prepare(config)

    def test_missing_full_scenario_warmup_fails_and_is_not_publishable(self):
        self.corrupt = lambda events: events.pop(2)
        with self.assertRaisesRegex(ValueError, "scenario_warmup_begin"):
            self.execute()
        self.assertTrue((self.root / "failure.json").exists())
        with self.assertRaisesRegex(ValueError, "Failed report"):
            self.publish()

    def test_incomplete_or_modified_seals_are_rejected(self):
        self.execute()
        seal = native.read_json(self.root / "completion.json")
        del seal["artifacts"]["report.json"]
        (self.root / "completion.json").write_text(json.dumps(seal))
        with self.assertRaisesRegex(ValueError, "Incomplete completion seal"):
            self.publish()

    def test_changed_outputs_are_rejected(self):
        self.execute()
        path = Path(self.group["cases"][0]["output"]) / "nprobe_96" / "work.u64"
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "differs from accepted ordinary"):
            timing.collect(self.root)

    def test_cas_conflict_does_not_append_diagnostics(self):
        self.execute()
        update = store.update
        def concurrent_update(*args, **kwargs):
            racer = fixtures.measurement(minute=2)
            racer.update(study="ablation", variant="racer")
            update(self.registry, [racer])
            return update(*args, **kwargs)
        with patch.object(store, "update", side_effect=concurrent_update), self.assertRaisesRegex(ValueError, "History changed"):
            self.publish()
        current = native.read_json(self.registry / "current.json")
        self.assertFalse(any(table["study"] == timing.STUDY for table in current["tables"]))

    def test_stage_accounting_rejects_bad_extent_negative_and_nonfinite_time(self):
        steps = np.full((3, 15), 100000, dtype="<u8")
        for latency in (np.ones(2), np.full(3, 1000), np.full(3, np.nan)):
            with self.assertRaises(ValueError):
                timing.stage_summary(steps, latency)
        with self.assertRaises(ValueError):
            timing.stage_summary(steps.astype("<f8"), np.full(3, 2000))


class ReferenceTest(unittest.TestCase):
    def test_reference_seal_and_exact_ordinary_client_binding(self):
        fixture = test_native_results.NativeResultsTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.execute()
        client = dict(binary=sys.executable, sha256=store.file_sha(sys.executable))
        build = dict(schema_version=1, status="complete", sources={
            str(fixture.experiment): store.file_sha(fixture.experiment)},
            clients=dict(ordinary=client, timing=client))
        plan, _, events = timing.verify_reference(fixture.experiment, build)
        self.assertEqual(len(events), 6)
        build["clients"]["ordinary"] = dict(binary="/bin/true", sha256=store.file_sha("/bin/true"))
        with self.assertRaisesRegex(ValueError, "ordinary client"):
            timing.verify_reference(fixture.experiment, build)
        seal = native.read_json(plan["root"] / "completion.json")
        del seal["artifacts"]["events.json"]
        (plan["root"] / "completion.json").write_text(json.dumps(seal))
        with self.assertRaisesRegex(ValueError, "Incomplete completion seal"):
            timing.verify_reference(fixture.experiment, build)


if __name__ == "__main__":
    unittest.main()
