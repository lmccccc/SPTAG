"""Pure-Python preparation/accounting tests. No native measurement is launched."""
import configparser
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

import numpy as np

import run_frontier_repair as runner


def ini(**sections):
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    for section, entries in sections.items():
        config[section] = entries
    return config


class FrontierRepairTest(unittest.TestCase):
    def fixture(self, root):
        index = root / "index"
        (index / "tenant_0/HeadIndex").mkdir(parents=True)
        (index / "tenant_0/HeadIndex/vectors.bin").write_bytes(struct.pack("<ii", 20, 128))
        historical = root / "historical"
        historical.mkdir()
        queries, attrs, truth = (root / name for name in ("queries.npy", "attrs.npy", "truth.npy"))
        np.save(queries, np.zeros((1000, 128), dtype="<f4"))
        np.save(attrs, np.zeros((20, 2), dtype="<u4"))
        np.save(truth, np.tile(np.arange(10, dtype="<i8"), (1000, 1)))
        base = root / "base.fvecs"
        with base.open("wb") as stream:
            for _ in range(20):
                stream.write(struct.pack("<i", 128) + bytes(512))
        predicate = root / "pred.npy"
        np.save(predicate, np.zeros((1000, 8), dtype="<u4"))
        scenarios = runner.SCENARIOS["SIFT1M"]
        workload = dict(query_vectors=str(queries), attributes=str(attrs), base_file=str(base),
            flat_query_tags={s: str(predicate) for s in ("broad_tag", "medium_tag", "extreme_tag")},
            query_dnf=dict(numeric=str(predicate), mixed=str(predicate)), predicates={},
            truth={s: dict(ids=str(truth), sha256=runner.sha(truth)) for s in scenarios})
        workload_path = root / "workloads.json"
        workload_path.write_text(json.dumps(workload))
        protected = {str(p): runner.sha(p) for p in (queries, attrs, truth, base, predicate, workload_path)}
        schedule = []
        search = dict(isExecute="true", BuildSsdIndex="false", InternalResultNum="24",
            NumberOfThreads="1", HashTableExponent="4", ResultNum="10", MaxCheck="2048",
            MaxDistRatio="8", SearchPostingPageLimit="15", DisableCrossEdges="true",
            LogPhaseTime="false", LogPathStats="false", DumpHeads="0", EnableHybridDistance="false",
            EnablePostingNavigation="false", PostingAnchorCount="8", PostingAdditionalMaxCheck="0")
        for scenario in scenarios:
            for variant in runner.VARIANTS:
                kind, file = runner.input_predicate(workload, scenario)
                native = ini(SearchSSDIndex=search, SearchSweep=dict(NProbe="[16,24,48,96,192,384]"),
                    Benchmark=dict(Index=str(index), Queries=str(queries), Predicate=kind,
                        PredicateFile=file, MaxQueries="1000", Warmup="1000"))
                native["SearchSSDIndex"].update(EnablePostingNavigation=str(variant != "graph").lower(),
                    PostingAdditionalMaxCheck="0" if variant == "graph" else "2048")
                path = historical / f"{scenario}-{variant}.ini"
                runner.write_ini(path, native)
                protected[str(path)] = runner.sha(path)
                schedule.append(dict(kind="plain", repetition=1, scenario=scenario, variant=variant,
                                     config=str(path)))
        (historical / "registration.json").write_text(json.dumps(dict(protected=protected, schedule=schedule)))
        output = root / "main_posting_frontier_20260924"
        plan = ini(Validation=dict(Dataset="SIFT1M", HistoricalCampaign=str(historical),
            Workloads=str(workload_path), OutputDirectory=str(output), Scenarios=",".join(scenarios),
            CPUNode="3", MemoryNode="3"), SearchSweep=dict(NProbe="[96,384]"))
        for runtime in runner.RUNTIMES:
            plan[f"Runtime.{runtime}"] = dict(Normal=str(root / f"missing-{runtime}"),
                                            Diagnostic=str(root / f"missing-{runtime}-diag"))
        path = root / "plan.ini"
        runner.write_ini(path, plan)
        return path, output

    def test_prepare_is_nonexecuting_and_uses_historical_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan, output = self.fixture(Path(tmp))
            with mock.patch.object(runner.subprocess, "Popen", side_effect=AssertionError("native forbidden")):
                runner.prepare(plan)
                _, registration = runner.checked(plan)
                self.assertEqual(len(registration["cases"]), 12)
                self.assertEqual(registration["points_per_process"], 24)
                self.assertEqual(registration["ordinary_repetitions"], 1)
                self.assertFalse((output / "authorization.json").exists())
                for runtime in runner.RUNTIMES:
                    for kind in runner.KINDS:
                        batch = runner.read_ini(output / "configs" / f"{runtime}-{kind}.ini")
                        self.assertEqual(batch["Batch"]["CaseCount"], "12")
                        self.assertTrue(batch["Case1"]["Config"].endswith("unfilter_graph.ini"))
                for case in registration["cases"]:
                    before = runner.read_ini(case["original_config"])
                    after = runner.read_ini(case["config"])
                    self.assertEqual(dict(before["SearchSSDIndex"]), dict(after["SearchSSDIndex"]))
                    self.assertEqual(after["Benchmark"]["ValueType"], "Float")
                    self.assertEqual(after["SearchSweep"]["NProbe"], "[96, 384]")
                with self.assertRaisesRegex(RuntimeError, "Explicit parent authorization"):
                    runner.run(plan, "before", "normal")
                with self.assertRaisesRegex(RuntimeError, "not authorized"):
                    runner.authorize(plan, output / "authorization.v2.template.ini")

    def test_authentication_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan, output = self.fixture(Path(tmp))
            runner.prepare(plan)
            with self.assertRaisesRegex(RuntimeError, "fresh"):
                runner.prepare(plan)
            (output / "configs/unfilter_graph.ini").write_text("modified")
            with self.assertRaisesRegex(RuntimeError, "Registered input changed"):
                runner.checked(plan)

    def test_wrong_query_dtype_fails_without_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, output = self.fixture(root)
            np.save(root / "queries.npy", np.zeros((1000, 128), dtype="u1"))
            with self.assertRaisesRegex(RuntimeError, "Strict query dtype"):
                runner.prepare(plan)
            self.assertFalse(output.exists())

    def test_bounded_hash_cannot_scan_ssd(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sparse-ssd"
            with path.open("wb") as stream:
                stream.truncate((128 << 20) + 1)
            with mock.patch.object(runner, "sha", side_effect=AssertionError("must not read")):
                with self.assertRaisesRegex(RuntimeError, "Refusing large"):
                    runner.bounded_hash(path)

    def test_authorization_requires_parent_labels_and_pins_copies_without_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, output = self.fixture(root)
            runner.prepare(plan)
            approval = runner.read_ini(output / "authorization.v2.template.ini")
            approval["Authorization"]["AllowNativeRuns"] = "true"
            core = root / "libSPTAGLibStatic.a"
            core.write_bytes(b"synthetic core; never linked or executed")
            for runtime in runner.RUNTIMES:
                section = approval[f"Runtime.{runtime}"]
                section["NavigationSchemaVersion"] = "99"
                for kind, suffix in (("Normal", ""), ("Diagnostic", "-diag")):
                    source = root / f"missing-{runtime}{suffix}"
                    source.write_bytes(b"synthetic non-executable unit fixture " + runtime.encode() + kind.encode())
                    section[kind + "SHA256"] = runner.sha(source)
                provenance = root / f"{runtime}-provenance.json"
                provenance.write_text(json.dumps(dict(policy_label=runner.POLICIES[runtime],
                    navigation_schema_version=6, navigation_columns=57,
                    binary_hashes={k: section[k.title() + "SHA256"] for k in runner.KINDS},
                    core_hashes={str(core): runner.sha(core)},
                    source_hashes={"synthetic.cpp": runner.sha(core)})))
                section.update(Provenance=str(provenance), ProvenanceSHA256=runner.sha(provenance))
            path = root / "approval.ini"
            runner.write_ini(path, approval)
            with self.assertRaisesRegex(RuntimeError, "schema/policy mismatch"):
                runner.authorize(plan, path)
            self.assertFalse((output / "authorization.json").exists())
            for runtime in runner.RUNTIMES:
                approval[f"Runtime.{runtime}"]["NavigationSchemaVersion"] = "6"
            path.unlink()
            runner.write_ini(path, approval)
            with mock.patch.object(runner.subprocess, "Popen", side_effect=AssertionError("native forbidden")):
                runner.authorize(plan, path)
            sealed = json.loads((output / "authorization.json").read_text())
            for entry in sealed["runtimes"].values():
                for binary in entry["binaries"].values():
                    self.assertEqual(runner.sha(binary["path"]), binary["sha256"])

    def accounting_fixture(self, root):
        folder = root / "case/nprobe_4"
        folder.mkdir(parents=True)
        values = dict(auxiliary_members=5, auxiliary_first_visits=2,
            auxiliary_negative_first_visits=1, posting_new_candidates=1,
            auxiliary_visited_skips=1, auxiliary_unvisited_negative_skips=2,
            posting_activations=1, head_before=1, head_after=2, head_target=4,
            preserved_heads=1, graph_checked_leaves=8, supplement_checked_leaves=2,
            supplement_distances=2, h2_completed_rows=1, h2_expand_attempts=1, supplement_reason=5)
        np.array([[values.get(n, 0) for n in runner.COUNTERS]], dtype="<u8").tofile(folder / "navigation.u64")
        np.array([1, -1, -1, -1], dtype="<i4").tofile(folder / "graph_ids.i32")
        np.array([3, 0, 0, 0], dtype="<f4").tofile(folder / "graph_dist.f32")
        row = dict(case="case", nprobe=4, queries=1, scenario="mixed_dnf", variant="postgraph_extra",
                   max_check=8, posting_additional_max_check=1)
        return folder, row

    def test_negative_skips_alias_score_and_complete_row_overrun(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, row = self.accounting_fixture(Path(tmp))
            runner.check_accounting(folder, row, 20)
            self.assertEqual(row["budget_accounting"]["max_complete_row_overrun"], 1)
            self.assertFalse(row["budget_accounting"]["strict_last_row_bound_available"])
            self.assertEqual(row["mean_navigation"]["auxiliary_unvisited_negative_skips"], 2)

    def test_bad_partitions_protection_and_distance_charges_fail(self):
        for field, value in (("auxiliary_members", 6), ("preserved_heads", 0),
                             ("supplement_distances", 4), ("h2_completed_rows", 0),
                             ("h2_expand_attempts", 0), ("graph_checked_leaves", 9)):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                folder, row = self.accounting_fixture(Path(tmp))
                data = np.fromfile(folder / "navigation.u64", dtype="<u8").reshape(1, 57)
                data[0, runner.COUNTERS.index(field)] = value
                data.tofile(folder / "navigation.u64")
                with self.assertRaises(RuntimeError):
                    runner.check_accounting(folder, row, 20)

    def test_filled_frontier_can_stop_by_budget_or_reachability(self):
        for reason in (5, 6):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as tmp:
                folder, row = self.accounting_fixture(Path(tmp))
                row["runtime"] = "after"
                if reason == 5:
                    row["posting_additional_max_check"] = 4
                data = np.fromfile(folder / "navigation.u64", dtype="<u8").reshape(1, 57)
                for field, value in (("head_after", 4), ("posting_target_met", 1),
                                     ("supplement_reason", reason)):
                    data[0, runner.COUNTERS.index(field)] = value
                data.tofile(folder / "navigation.u64")
                runner.check_accounting(folder, row, 20)
                self.assertEqual(row["filled_termination_reasons"][str(reason)], 1)

    def test_after_rejects_first_fill_and_incorrect_result_status(self):
        for reason, met in ((4, 1), (6, 0)):
            with self.subTest(reason=reason, met=met), tempfile.TemporaryDirectory() as tmp:
                folder, row = self.accounting_fixture(Path(tmp))
                row["runtime"] = "after"
                data = np.fromfile(folder / "navigation.u64", dtype="<u8").reshape(1, 57)
                for field, value in (("head_after", 4), ("posting_target_met", met),
                                     ("supplement_reason", reason)):
                    data[0, runner.COUNTERS.index(field)] = value
                data.tofile(folder / "navigation.u64")
                with self.assertRaises(RuntimeError):
                    runner.check_accounting(folder, row, 20)

    def test_auxiliary_owner_member_changes_are_not_graph_parity_failures(self):
        before = {field: np.zeros(1, dtype="<u8") for field in runner.COUNTERS}
        after = {field: value.copy() for field, value in before.items()}
        for field in ("owner_references", "upper_members", "h2_member_references",
                      "h3plus_owner_references", "visited_checks"):
            after[field][:] = 42
        runner.compare_graph_work(before, after)
        after["graph_distances"][:] = 1
        with self.assertRaisesRegex(RuntimeError, "Graph work differs"):
            runner.compare_graph_work(before, after)

    def test_original_vector_distance_verification_is_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, _ = self.fixture(root)
            ids = np.tile(np.arange(10, dtype="<i4"), (1000, 1))
            ids.tofile(root / "ids.i32")
            np.zeros((1000, 10), dtype="<f4").tofile(root / "dist.f32")
            workload = json.loads((root / "workloads.json").read_text())
            registration = dict(value_type="Float", queries=str(root / "queries.npy"))
            runner.exact_returned_distances(root, workload, registration)
            distances = np.zeros((1000, 10), dtype="<f4")
            distances[:, -1] = 1
            distances.tofile(root / "dist.f32")
            with self.assertRaisesRegex(RuntimeError, "exact original-vector"):
                runner.exact_returned_distances(root, workload, registration)


if __name__ == "__main__":
    unittest.main()
