"""Bounded portable orchestration, real capture acceptance and R publication fixtures."""
from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import struct
import subprocess
import unittest
from unittest.mock import patch

import numpy as np

import posting_ablation as campaign
import test_native_results as native_fixtures
import test_update_results as fixtures

native, store = campaign.native, campaign.store
write_ini = native_fixtures.write_ini


def reference_events(plan):
    events = []
    for template in plan["templates"]:
        for mode in campaign.MODES:
            for probe in template["probes"]:
                parameters = dict(template["parameters"], GlobalPostingSearch=str(mode == "global").lower(),
                    EnablePostingNavigation=str(mode == "full").lower(), WarmupPolicy="once_per_mode_predicate")
                events.append(dict(point=dict(scenario=template["scenario"], search_value=probe,
                                              parameters=json.dumps(parameters))))
    return events


class PostingAblationTest(unittest.TestCase):
    def setUp(self):
        fixture = native_fixtures.NativeResultsTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.root = fixture.root / "ablation"
        self.config = fixture.root / "ablation.ini"
        self.source = fixture.root / "source.cpp"
        self.source.write_text("immutable fixture source\n")
        queries = np.zeros((1000, 128), dtype=np.uint8)
        query_source = Path(fixture.workload["query_file"])
        query_source.write_bytes(struct.pack("<ii", *queries.shape) + queries.tobytes())
        np.save(fixture.workload["queries"], queries)
        workload = fixture.workload
        originals = copy.deepcopy(workload)
        workload.update(query_count=1000, topk=100, scenarios=list(campaign.SCENARIOS),
                        predicates={}, native_predicates={}, truth={}, scenario_metadata={})
        workload["protected_large"][str(query_source)] = native.identity(query_source)
        workload["protected"][workload["queries"]] = store.file_sha(workload["queries"])
        attrs = np.fromfile(workload["attribute_source"], dtype="<u4").reshape(12, 2)
        batch = {"Batch": dict(CaseCount="6", WarmupPolicy="once_per_predicate")}
        sections = {"Experiment": dict(fixture.sections["Experiment"], Repeats="1")}
        self.labels = {}
        for ordinal, scenario in enumerate(campaign.SCENARIOS, 1):
            source = originals["scenarios"][(ordinal - 1) % 3]
            expression = originals["predicates"][source]
            _, clauses = native.parse_predicate(json.dumps(expression), tuple(workload["column_types"]))
            eligible = np.flatnonzero(native.matching_rows(clauses, attrs))
            ids = np.full((1000, 100), -1, dtype="<i4")
            ids[:, :len(eligible)] = eligible
            distances = np.where(ids >= 0, ids.astype("<f4") ** 2 * 128, np.inf).astype("<f4")
            truth = dict(candidate_count=len(eligible), selectivity=len(eligible) / 12)
            for field, data in (("ids", ids), ("distances", distances)):
                path = fixture.truth / f"{scenario}-{field}.npy"
                np.save(path, data, allow_pickle=False)
                truth[field], truth[field + "_sha256"] = str(path), store.file_sha(path)
            kind, payload = native.native_predicate(clauses, tuple(workload["column_types"]), 1000)
            predicate = fixture.truth / f"{scenario}-predicate.npy"
            np.save(predicate, payload, allow_pickle=False)
            workload["protected"][str(predicate)] = store.file_sha(predicate)
            workload["predicates"][scenario] = expression
            workload["native_predicates"][scenario] = dict(kind=kind, file=str(predicate))
            workload["truth"][scenario] = truth
            workload["scenario_metadata"][scenario] = dict(
                eligible_count=len(eligible), corpus_count=12, selectivity=len(eligible) / 12)
            self.labels[scenario] = f"{100 * len(eligible) / 12:.3f}"
            profile = native.read_config(fixture.root / f"{source}.ini")
            profile["benchmark"].update(maxqueries="1000", warmup="1000",
                                        predicate=kind, predicatefile=str(predicate))
            profile["searchssdindex"].update(internalresultnum="100", resultnum="100")
            profile["searchsweep"] = dict(nprobe="100,128")
            path = fixture.root / f"{scenario}.ini"
            write_ini(path, profile)
            name = f"Case{ordinal}"
            batch[name] = dict(Config=str(path), OutputDirectory=str(fixture.run_root / name))
            sections[name] = dict(Scenario=scenario, Repetition="1")
        fixture.refresh_truth()
        write_ini(fixture.experiment, sections)
        write_ini(fixture.batch, batch)
        self.policy = {"Ablation": dict(Directory=str(self.root), DataDirectory=str(fixture.registry),
            ReferenceExperiment=str(fixture.experiment), MinimumAvailableMemoryGiB="1",
            MinimumFreeDiskGiB="1", MinimumAIOEvents="1")}
        write_ini(self.config, self.policy)
        self.fail_phase = None
        self.real_subprocess = subprocess.run

    def add_reference_profile(self):
        profile = native.read_config(self.fixture.root / "sel50.ini")
        profile["searchssdindex"]["maxcheck"] = "2048"
        profile["searchsweep"]["nprobe"] = "448"
        path = self.fixture.root / "sel50-other.ini"
        write_ini(path, profile)
        batch = native.read_config(self.fixture.batch)
        batch["batch"]["casecount"] = "7"
        batch["case7"] = dict(config=str(path), outputdirectory=str(self.fixture.run_root / "Case7"))
        write_ini(self.fixture.batch, batch)
        reference = native.read_config(self.fixture.experiment)
        reference["case7"] = dict(scenario="sel50", repetition="1")
        write_ini(self.fixture.experiment, reference)

    def prepare(self):
        with patch.object(campaign, "PLOT_SELECTIVITY", self.labels), \
                patch.object(campaign, "sources", return_value={str(self.source): store.file_sha(self.source)}), \
                redirect_stdout(io.StringIO()):
            campaign.prepare(self.config)
        return campaign.verify(self.root)

    def process(self, command, **kwargs):
        if command[0] == "Rscript":
            return self.real_subprocess(command, **kwargs)
        if command[0] == "ldd":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        plan = native.load_plan(Path(command[-1]).parent / "experiment.ini")
        if plan["root"].name == self.fail_phase:
            kwargs["stdout"].write("simulated native failure\n")
            return subprocess.CompletedProcess(command, 7)
        workload = native.read_json(plan["workloads"])
        count, topk = workload["query_count"], workload["topk"]
        events = [dict(event="batch_begin", cases=len(plan["cases"]), warmup_policy=plan["warmup_policy"],
                       phase_timing=False, value_type="UInt8", index=str(plan["index"]), queries=workload["queries"]),
                  dict(event="batch_loaded", index_load_count=1, query_corpus_load_count=1)]
        warmed = set()
        for case in plan["cases"]:
            search, bench = case["profile"]["searchssdindex"], case["profile"]["benchmark"]
            global_posting = search["globalpostingsearch"] == "true"
            mode = "global" if global_posting else "posting" if search["enablepostingnavigation"] == "true" else "graph"
            binding = dict(case_id=case["case_id"], config=str(case["config"]), output_directory=str(case["output"]))
            group = mode + "\n" + bench["predicate"] + "\n" + bench["predicatefile"]
            if group not in warmed:
                events.extend([dict(event="scenario_warmup_begin", **binding, queries=count, warmup_group=group),
                               dict(event="scenario_warmup_end", completed_queries=count)])
                warmed.add(group)
            events.append(dict(event="case_begin", **binding, predicate=bench["predicate"],
                predicate_file=bench["predicatefile"], phase_timing=False, measured_queries=count,
                replay_queries=count, warmup_queries=0, search_settings=search))
            for probe in case["probes"]:
                directory = case["output"] / f"nprobe_{probe}"
                directory.mkdir(parents=True)
                truth = workload["truth"][case["scenario"]]
                np.load(truth["ids"]).astype("<i4").tofile(directory / "ids.i32")
                np.load(truth["distances"]).astype("<f4").tofile(directory / "dist.f32")
                np.zeros((count, 8), dtype="<u8").tofile(directory / "work.u64")
                np.full(count, 10000, dtype="<f8").tofile(directory / "latency_us.f64")
                events.append(dict(event="point", **binding, mode=mode, global_posting_search=global_posting,
                    nprobe=probe, queries=count, topk=topk, value_type="UInt8", diagnostic=False, phase_timing=False,
                    measured_queries=count, replay_queries=count, warmup_policy=plan["warmup_policy"], warmup_queries=0,
                    max_check=int(search["maxcheck"]), posting_anchor_count=0, posting_additional_max_check=8,
                    posting_navigation_width=int(search["postingnavigationwidth"]),
                    posting_match_window=1024, search_posting_page_limit=3, posting_match_rate_percent=12.5,
                    qps=100, mean_ms=10, p50_us=10000, p95_us=10000, p99_us=10000,
                    graph_stop_policy="local-match-union-v2" if mode == "posting" else "upstream-adaptive-v1",
                    posting_budget_policy="unused-plus-preserved-extra-v2", navigation_schema_version=7,
                    navigation_columns=58, dispatch_schema_version=2, dispatch_columns=6))
            events.append(dict(event="case_end", **binding, completed_points=len(case["probes"])))
        events.append(dict(event="batch_end", completed_cases=len(plan["cases"]), completed_warmup_queries=count * len(warmed)))
        kwargs["stdout"].write("".join(json.dumps(event) + "\n" for event in events))
        return subprocess.CompletedProcess(command, 0)

    def execute(self):
        with patch.object(campaign, "preflight", return_value={}), \
                patch.object(campaign.subprocess, "run", side_effect=self.process), redirect_stdout(io.StringIO()):
            campaign.run(self.root)

    def test_prepare_changes_only_mode_flags_and_keeps_every_original_probe(self):
        self.add_reference_profile()
        original = native.read_config(self.fixture.root / "sel50.ini")
        original["searchsweep"]["nprobe"] = "160,100"
        write_ini(self.fixture.root / "sel50.ini", original)
        plan = self.prepare()
        self.assertEqual(plan["reference_points"], 13)
        self.assertEqual(plan["full_points"], 39)
        self.assertFalse((self.root / "pilot-truth").exists())
        self.assertFalse((self.root / "pilot-configs").exists())
        self.assertFalse((self.root / "grid-selection.json").exists())
        for template in plan["templates"]:
            for mode in campaign.MODES:
                profile = native.read_config(self.root / "measurement-configs" / f"{template['id']}-{mode}.ini")
                expected = native.read_config(template["config"])
                for key in campaign.MODE_KEYS:
                    profile["searchssdindex"].pop(key)
                    expected["searchssdindex"].pop(key, None)
                self.assertEqual(profile, expected)
        config = self.root / "measurement-configs/experiment.ini"
        formal = native.load_plan(config)
        self.assertEqual(len(formal["cases"]), 63)
        self.assertEqual(formal["warmup_policy"], "once_per_mode_predicate")
        self.assertTrue(all(case["profile"]["benchmark"]["warmup"] == "1000" for case in formal["cases"]))
        self.assertEqual(formal["cases"][0]["probes"], [160, 100])
        forward = [case["config"] for case in formal["cases"][:21]]
        self.assertEqual(list(reversed(forward)), [case["config"] for case in formal["cases"][21:42]])
        self.assertEqual(native.load_inputs(formal)["queries"].shape, (1000, 128))
        with self.assertRaisesRegex(ValueError, "fresh"):
            self.prepare()

    def test_reference_grid_rejects_added_omitted_duplicate_and_changed_points(self):
        plan = self.prepare()
        events = reference_events(plan)
        campaign.verify_reference_points(plan, events)
        for changed in (events[:-1], events + [events[0]]):
            with self.assertRaisesRegex(ValueError, "every reference parameter point"):
                campaign.verify_reference_points(plan, changed)
        events[0]["point"]["search_value"] = 16384
        with self.assertRaisesRegex(ValueError, "every reference parameter point"):
            campaign.verify_reference_points(plan, events)
        events = reference_events(plan)
        parameters = json.loads(events[0]["point"]["parameters"])
        parameters["MaxCheck"] = "8192"
        events[0]["point"]["parameters"] = json.dumps(parameters)
        with self.assertRaisesRegex(ValueError, "every reference parameter point"):
            campaign.verify_reference_points(plan, events)

    def test_input_selectivity_removed_tuning_keys_and_output_overlap_guards(self):
        with self.assertRaisesRegex(ValueError, "selectivities"):
            campaign.load_config(self.config)
        with patch.object(campaign, "PLOT_SELECTIVITY", self.labels):
            for key, value in (("PilotNProbe", "100,16384"), ("NProbeGrid", "100,16384"),
                               ("PilotTargetRecall", ".97"), ("MinimumMaxNProbe", "512")):
                invalid = copy.deepcopy(self.policy)
                invalid["Ablation"][key] = value
                write_ini(self.config, invalid)
                with self.assertRaisesRegex(ValueError, "declare exactly"):
                    campaign.load_config(self.config)
            self.policy["Ablation"]["Directory"] = str(self.fixture.index / "bad")
            write_ini(self.config, self.policy)
            with self.assertRaisesRegex(ValueError, "overlap"):
                campaign.prepare(self.config)
            self.assertFalse(self.root.exists())
            self.policy["Ablation"].update(Directory=str(self.root),
                                           DataDirectory=str(self.fixture.index / "bad-registry"))
            write_ini(self.config, self.policy)
            with self.assertRaisesRegex(ValueError, "Registry output overlaps"):
                campaign.prepare(self.config)
            self.assertFalse((self.fixture.index / "bad-registry").exists())
            path = self.fixture.root / "sel50.ini"
            profile = native.read_config(path)
            profile["searchssdindex"]["enablepostingnavigation"] = "false"
            write_ini(path, profile)
            with self.assertRaisesRegex(ValueError, "full hierarchical"):
                campaign.load_config(self.config)

    def test_frozen_native_sources_and_reference_inputs(self):
        self.prepare()
        self.source.write_text("changed source\n")
        with self.assertRaisesRegex(ValueError, "native sources"):
            campaign.verify(self.root)
        path = self.fixture.root / "sel50.ini"
        with path.open("a") as stream:
            stream.write("\n")
        with self.assertRaisesRegex(ValueError, "Frozen artifact"):
            campaign.verify(self.root)

    def test_failed_native_batch_never_publishes_or_restarts(self):
        self.prepare()
        self.fail_phase = "measurement"
        with self.assertRaisesRegex(ValueError, "exit 7"):
            self.execute()
        self.assertTrue((self.root / "failure.json").exists())
        self.assertFalse((self.fixture.registry / "history.jsonl").exists())
        with self.assertRaisesRegex(ValueError, "Fresh-only"):
            self.execute()
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "failed experiment"):
            campaign.publish(self.root)
        self.assertTrue((self.root / "publication-failure.json").exists())

    def test_real_acceptance_plots_and_idempotent_main_preservation(self):
        self.add_reference_profile()
        self.prepare()
        catalog = fixtures.catalog()
        catalog["catalog"]["experiment_views"] = dict(main_selection="observed-pareto-envelope",
            main_studies=["main"], main_protocol=dict(topk=100, cohort_queries=1000, phase="single", threads=1),
            minimum_qps=3, disclosures=[])
        original = fixtures.measurement()
        original["version"]["index"] = self.fixture.sections["Experiment"]["IndexVersion"]
        original["point"]["topk"] = 100
        before = store.update(self.fixture.registry, [catalog, original])
        self.execute()
        after = native.read_json(self.fixture.registry / "current.json")
        self.assertEqual(before["experiments"]["main"], after["experiments"]["main"])
        self.assertTrue(all(point in after["points"] for point in before["points"]))
        self.assertEqual(len(after["points"]), 40)
        metadata = native.read_json(self.root / "measurement/plots/metadata.json")
        self.assertEqual(metadata["points"], 39)
        self.assertEqual(metadata["control_groups"], 2)
        events = native.accepted_events(self.root / "measurement-configs/experiment.ini")
        self.assertEqual({event["point"]["repeats"] for event in events}, {3})
        history = (self.fixture.registry / "history.jsonl").read_bytes()
        with redirect_stdout(io.StringIO()):
            campaign.publish(self.root)
        self.assertEqual(history, (self.fixture.registry / "history.jsonl").read_bytes())
        with (self.root / "measurement-configs/p000-sel50-global.ini").open("a") as stream:
            stream.write("\n")
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "Frozen artifact"):
            campaign.publish(self.root)
        self.assertEqual(history, (self.fixture.registry / "history.jsonl").read_bytes())

    def test_new_registry_catalog_and_selected_index_guard(self):
        plan = self.prepare()
        event = fixtures.measurement(scenario="sel50")
        event.update(study=campaign.STUDY, variant=campaign.VARIANT)
        event["point"].update(repeats=3, expected_repeats=3)
        event["measurements"].append(dict(event["measurements"][-1], repeat=3))
        event["version"]["index"] = plan["reference_policy"]["indexversion"]
        additions = campaign.publication_events(plan, None, [], [event])
        after = campaign.prospective([], additions)
        self.assertEqual(len(after["catalog"]["expected_cells"]), 6)
        self.assertEqual(after["points"][0]["study"], campaign.STUDY)
        before = copy.deepcopy(after)
        before["catalog"]["experiment_views"] = dict(main_studies=[campaign.STUDY])
        with self.assertRaisesRegex(ValueError, "main curve"):
            campaign.publication_events(plan, before, [], [event])


if __name__ == "__main__":
    unittest.main()
