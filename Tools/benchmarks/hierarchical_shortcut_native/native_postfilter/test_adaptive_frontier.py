"""Synthetic adaptive stop/observer checks only; no native process or index load."""
import configparser
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

import run_adaptive_frontier as runner


class AdaptiveFrontierTest(unittest.TestCase):
    def controlled_profile(self):
        frozen = runner.HERE / "sift1b_controlled_ascent.ini"
        return runner.read_ini(frozen if frozen.exists() else runner.HERE.parents[1] /
                               "configs/posting_frontier_repair/sift1b_controlled_ascent.ini")

    def test_controlled_profile_is_only_seventy_adaptive_and_six_counter_points(self):
        config = self.controlled_profile()
        _, _, scenarios, grid, repeats, output = runner.read_design(config)
        self.assertEqual(output, "main_posting_controlled_ascent_curves_20260924")
        self.assertEqual(runner.selected_variants(config), ("postgraph_extra",))
        cases = runner.repeat_cases([dict(case=name) for name in scenarios], repeats)
        self.assertEqual(len(cases) * len(grid), 70)
        self.assertEqual([c["case"] for c in cases[5:]], ["r2_" + s for s in scenarios[::-1]])
        names, probes, count = runner.counter_design(config)
        self.assertEqual((len(names) * len(probes), count), (6, 32))
        self.assertNotIn("Runtime.before", config)
        for key, value in (("Variants", "graph,postgraph_extra"), ("CPUNode", "2"),
                           ("ImplementationRevision", "unknown")):
            with self.subTest(key=key):
                bad = self.controlled_profile()
                bad["Validation"][key] = value
                with self.assertRaises(RuntimeError):
                    runner.read_design(bad)
        for section, key, value in (("CounterProbe", "Warmup", "0"),
                                    ("CounterProbe", "MaxQueries", "1000"),
                                    ("SearchSweep", "NProbe", "[16,96]")):
            bad = self.controlled_profile()
            bad[section][key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                runner.read_design(bad)

    def test_variant_defaults_and_invalid_selections(self):
        config = configparser.ConfigParser(interpolation=None)
        config["Validation"] = {}
        self.assertEqual(runner.selected_variants(config), runner.VARIANTS)
        for variant in ("", "postgraph_extra", "graph,graph", "other"):
            config["Validation"]["Variants"] = variant
            with self.subTest(variant=variant), self.assertRaises(RuntimeError):
                runner.selected_variants(config)
        config["Validation"]["Mode"] = "selectivity_curve"
        config["Validation"]["Variants"] = "postgraph_extra"
        self.assertEqual(runner.selected_variants(config), ("postgraph_extra",))

    def test_counter_summary_uses_32_typed_rows_not_normal_cases(self):
        for value_type in ("UInt8", "Float"):
            with self.subTest(value_type=value_type), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                dtype = "u1" if value_type == "UInt8" else "<f4"
                np.save(root / "queries.npy", np.zeros((1000, 128), dtype=dtype))
                vectors = np.repeat(np.arange(10, dtype=dtype)[:, None], 128, axis=1)
                with (root / "base.bin").open("wb") as stream:
                    if value_type == "UInt8":
                        np.array([10, 128], dtype="<i4").tofile(stream)
                        vectors.tofile(stream)
                    else:
                        for vector in vectors:
                            np.array([128], dtype="<i4").tofile(stream)
                            vector.tofile(stream)
                np.save(root / "attributes.npy", np.zeros((10, 2), dtype="<u4"))
                ids = np.tile(np.arange(10, dtype="<i4"), (32, 1))
                distances = np.tile((128 * np.arange(10) ** 2).astype("<f4"), (32, 1))
                np.save(root / "truth.npy", np.tile(np.arange(10, dtype="<i8"), (1000, 1)))
                workload = dict(attributes=str(root / "attributes.npy"), base_file=str(root / "base.bin"),
                    predicates={"sel_01pct": None}, truth={"sel_01pct": {"ids": str(root / "truth.npy")}})
                (root / "workloads.json").write_text(json.dumps(workload))
                case = dict(case="counter_sel_01pct_postgraph_extra", scenario="sel_01pct",
                    variant="postgraph_extra", queries=32, config="/counter.ini", probes=[16],
                    max_check=2048, posting_anchor_count=8, posting_additional_max_check=2048,
                    posting_page_limit=3, counter_only=True)
                registration = dict(index="/immutable", queries=str(root / "queries.npy"),
                    value_type=value_type, cases=[], cases_by_kind={"diagnostic": [case]},
                    workload=str(root / "workloads.json"), ordinary_repetitions=2, head_count=1000)
                folder = root / "after/diagnostic" / case["case"] / "nprobe_16"
                folder.mkdir(parents=True)
                ids.tofile(folder / "ids.i32")
                distances.tofile(folder / "dist.f32")
                np.zeros((32, 8), dtype="<u8").tofile(folder / "work.u64")
                np.ones(32, dtype="<f8").tofile(folder / "latency_us.f64")
                seed, _ = self.fixture(root / "seed")
                nav = np.tile(np.fromfile(seed / "navigation.u64", dtype="<u8"), (32, 1))
                nav[:, runner.COUNTERS.index("head_target")] = 16
                nav.tofile(folder / "navigation.u64")
                graph = np.full((32, 16), -1, dtype="<i4")
                graph[:, 0] = 1
                graph.tofile(folder / "graph_ids.i32")
                np.zeros((32, 16), dtype="<f4").tofile(folder / "graph_dist.f32")
                identity = dict(case_id="Case1", config=case["config"], output_directory=str(folder.parent))
                point = dict(event="point", mode="posting", nprobe=16, queries=32, warmup_queries=32,
                    measured_queries=32, replay_queries=32, value_type=value_type,
                    navigation_schema_version=6, navigation_columns=57, diagnostic=True,
                    phase_timing=False, max_check=2048, posting_anchor_count=8,
                    posting_additional_max_check=2048, search_posting_page_limit=3, qps=1000, **identity)
                events = [dict(event="batch_begin", cases=1, index=registration["index"],
                    queries=registration["queries"], value_type=value_type),
                    dict(event="batch_loaded", index_load_count=1, query_corpus_load_count=1),
                    dict(event="case_begin", **identity), point,
                    dict(event="case_end", completed_points=1, **identity),
                    dict(event="batch_end", completed_cases=1)]
                log = root / "after-diagnostic.log"
                log.write_text("".join(json.dumps(e) + "\n" for e in events))
                authorization = dict(runtimes={"after": dict(schema=6, policy=runner.POLICIES["after"])})
                runner.summarize(root, registration, authorization, "after", "diagnostic")
                rows = json.loads((root / "after-diagnostic-results.json").read_text())
                self.assertEqual((len(rows), rows[0]["recall"], rows[0]["queries"]), (1, 1, 32))
                self.assertNotIn("qps", rows[0])
                self.assertEqual(rows[0]["convergence_fraction"], 1)
                point["warmup_queries"] = 0
                log.write_text("".join(json.dumps(e) + "\n" for e in events))
                with self.assertRaisesRegex(RuntimeError, "window protocol"):
                    runner.summarize(root, registration, authorization, "after", "diagnostic")

    def test_preserved_comparison_retains_regressions_and_rejects_unfiltered_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_root = root / "old"
            old_root.mkdir()
            base = [dict(case=s + "_postgraph_extra", scenario=s, probes=runner.CURVE_GRID)
                    for s in runner.CURVE_SCENARIOS]
            cases = runner.repeat_cases(base, 2)
            registration = dict(cases=cases, preserved_references={"PriorAdaptiveCampaign": str(old_root)})
            rows = []
            for case in cases:
                for probe in case["probes"]:
                    folder = root / "after/normal" / case["case"] / f"nprobe_{probe}"
                    folder.mkdir(parents=True)
                    for filename in (*runner.PAYLOADS, "latency_us.f64"):
                        (folder / filename).write_bytes(filename.encode())
                    rows.append(dict(case=case["case"], scenario=case["scenario"], variant="postgraph_extra",
                        nprobe=probe, repetition=case["repetition"], queries=1000, kind="normal",
                        payload_hashes={name: runner.sha(folder / name) for name in runner.PAYLOADS},
                        latency_sha256=runner.sha(folder / "latency_us.f64"),
                        recall=0.8, qps=80, mean_ssd_work={}))
            old_rows = [dict(row, recall=0.9, qps=100) for row in rows]
            (old_root / "after-normal-results.json").write_text(json.dumps(old_rows))
            (root / "after-normal-results.json").write_text(json.dumps(rows))
            (root / "after-normal.log").write_text("synthetic point log")
            runner.compare_preserved_curves(root, registration, rows, "normal")
            report = json.loads((root / "ordinary-comparison.json").read_text())
            self.assertEqual(len(report["points"]), 70)
            self.assertTrue(all(p["qps_regressed"] and p["recall_regressed"] for p in report["points"]))
            self.assertFalse(report["performance_acceptance"])
            old_rows[0]["payload_hashes"] = {"ids.i32": "changed"}
            (old_root / "after-normal-results.json").write_text(json.dumps(old_rows))
            with self.assertRaisesRegex(RuntimeError, "exact unfiltered"):
                runner.compare_preserved_curves(root, registration, rows, "normal")

    def test_declared_curve_design_and_balanced_repetitions(self):
        config = configparser.ConfigParser(interpolation=None)
        config["Validation"] = dict(Dataset="SIFT1B", Mode="selectivity_curve",
            Scenarios=",".join(runner.CURVE_SCENARIOS), Repeats="2")
        config["SearchSweep"] = dict(NProbe="[16,24,48,96,192,384,768]")
        dataset, mode, scenarios, grid, repeats, output = runner.read_design(config)
        self.assertEqual((dataset, mode, repeats), ("SIFT1B", "selectivity_curve", 2))
        self.assertEqual(len(scenarios), 5)
        self.assertEqual(grid[-1], 768)
        self.assertEqual(output, "main_posting_adaptive_curves_20260924")
        base = [dict(case=name, config=f"/{name}.ini", probes=grid) for name in ("a", "b")]
        cases = runner.repeat_cases(base, repeats)
        self.assertEqual([case["case"] for case in cases], ["r1_a", "r1_b", "r2_b", "r2_a"])
        self.assertEqual([case["config"] for case in cases], ["/a.ini", "/b.ini", "/b.ini", "/a.ini"])
        for bad in ("[96]", "[96,16]", "[16,16]", "[true,96]", "[0,96]"):
            config["SearchSweep"]["NProbe"] = bad
            with self.subTest(grid=bad), self.assertRaises(RuntimeError):
                runner.read_design(config)

    def test_summary_handles_extended_repeated_native_grid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queries = np.zeros((1000, 128), dtype="u1")
            np.save(root / "queries.npy", queries)
            vectors = np.repeat(np.arange(10, dtype="u1")[:, None], 128, axis=1)
            with (root / "base.bin").open("wb") as stream:
                np.array([10, 128], dtype="<i4").tofile(stream)
                vectors.tofile(stream)
            np.save(root / "attributes.npy", np.zeros((10, 2), dtype="<u4"))
            ids = np.tile(np.arange(10, dtype="<i4"), (1000, 1))
            distances = np.tile((128 * np.arange(10) ** 2).astype("<f4"), (1000, 1))
            np.save(root / "truth.npy", ids.astype("<i8"))
            workload = dict(attributes=str(root / "attributes.npy"), base_file=str(root / "base.bin"),
                predicates={"unfilter": None}, truth={"unfilter": {"ids": str(root / "truth.npy")}})
            (root / "workloads.json").write_text(json.dumps(workload))
            probes = [16, 24, 48, 96, 192, 384, 768]
            cases = runner.repeat_cases([dict(case="unfilter_graph", scenario="unfilter",
                variant="graph", config=str(root / "native.ini"), queries=1000, probes=probes,
                max_check=2048, posting_anchor_count=8, posting_additional_max_check=0,
                posting_page_limit=3)], 2)
            registration = dict(index="/immutable/index", queries=str(root / "queries.npy"),
                value_type="UInt8", cases=cases, grid=probes, workload=str(root / "workloads.json"),
                ordinary_repetitions=2)
            events = [dict(event="batch_begin", cases=2, index=registration["index"],
                queries=registration["queries"], value_type="UInt8"),
                dict(event="batch_loaded", index_load_count=1, query_corpus_load_count=1)]
            for i, case in enumerate(cases, 1):
                folder = root / "after/normal" / case["case"]
                identity = dict(case_id=f"Case{i}", config=case["config"], output_directory=str(folder))
                events.append(dict(event="case_begin", **identity))
                for probe in probes:
                    point = folder / f"nprobe_{probe}"
                    point.mkdir(parents=True)
                    ids.tofile(point / "ids.i32")
                    distances.tofile(point / "dist.f32")
                    np.zeros((1000, 8), dtype="<u8").tofile(point / "work.u64")
                    np.ones(1000, dtype="<f8").tofile(point / "latency_us.f64")
                    events.append(dict(event="point", mode="graph", nprobe=probe,
                        queries=1000, warmup_queries=1000, measured_queries=1000, replay_queries=1000,
                        value_type="UInt8", navigation_schema_version=6, navigation_columns=57,
                        diagnostic=False, phase_timing=False, max_check=2048, posting_anchor_count=8,
                        posting_additional_max_check=0, search_posting_page_limit=3, qps=1000, **identity))
                events.append(dict(event="case_end", completed_points=len(probes), **identity))
            events.append(dict(event="batch_end", completed_cases=2))
            (root / "after-normal.log").write_text("".join(json.dumps(row) + "\n" for row in events))
            authorization = dict(runtimes={"after": dict(schema=6, policy=runner.POLICIES["after"])})
            runner.summarize(root, registration, authorization, "after", "normal")
            rows = json.loads((root / "after-normal-results.json").read_text())
            self.assertEqual(len(rows), 14)
            self.assertEqual([row["repetition"] for row in rows], [1] * 7 + [2] * 7)
            self.assertTrue(all(row["recall"] == 1 and row["timing_scope"] == "repeated ordinary curve"
                                for row in rows))

    def fixture(self, root, reason=7, filled=False, runtime="after", policy=None):
        folder = root / "case/nprobe_4"
        folder.mkdir(parents=True)
        fields = dict(auxiliary_members=5, auxiliary_first_visits=2,
            auxiliary_negative_first_visits=1, posting_new_candidates=1,
            auxiliary_visited_skips=1, auxiliary_unvisited_negative_skips=2,
            posting_activations=1, head_before=1, head_after=4 if filled else 2,
            head_target=4, preserved_heads=1, graph_checked_leaves=8,
            supplement_checked_leaves=2, supplement_distances=2, h2_completed_rows=1,
            h2_expand_attempts=1, supplement_reason=reason, posting_target_met=int(filled))
        np.array([[fields.get(name, 0) for name in runner.COUNTERS]], dtype="<u8").tofile(
            folder / "navigation.u64")
        np.array([1, -1, -1, -1], dtype="<i4").tofile(folder / "graph_ids.i32")
        np.array([3, 0, 0, 0], dtype="<f4").tofile(folder / "graph_dist.f32")
        row = dict(case="case", nprobe=4, queries=1, scenario="mixed_dnf", variant="postgraph_extra",
            max_check=8, posting_additional_max_check=8, runtime=runtime,
            policy=policy or runner.POLICIES[runtime])
        return folder, row

    def test_convergence_allows_full_and_underfilled_results(self):
        for filled in (False, True):
            with self.subTest(filled=filled), tempfile.TemporaryDirectory() as tmp:
                folder, row = self.fixture(Path(tmp), filled=filled)
                runner.check_accounting(folder, row, 20)
                self.assertEqual(row["supplement_reasons"], {"7": 1})
                self.assertEqual(row["convergence_fraction"], 1)
                self.assertEqual(row["head_underfill_fraction"], int(not filled))
                self.assertEqual(row["convergence_underfilled_fraction"], int(not filled))
                self.assertEqual(row["convergence_filled_fraction"], int(filled))
                self.assertEqual(row["underfill_given_convergence"], int(not filled))

    def test_reason7_is_not_accepted_for_baseline_or_full_budget_label(self):
        for runtime, policy in (("before", runner.POLICIES["before"]),
                                ("after", "predicate_first_global_distance_frontier")):
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as tmp:
                folder, row = self.fixture(Path(tmp), runtime=runtime, policy=policy)
                with self.assertRaises(RuntimeError):
                    runner.check_accounting(folder, row, 20)

    def test_unknown_reason_and_adaptive_first_fill_are_rejected(self):
        for reason in (4, 8):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as tmp:
                folder, row = self.fixture(Path(tmp), reason=reason, filled=True)
                with self.assertRaises(RuntimeError):
                    runner.check_accounting(folder, row, 20)

    def test_exhaustion_and_budget_remain_distinct_from_convergence(self):
        for reason in (5, 6):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as tmp:
                folder, row = self.fixture(Path(tmp), reason=reason, filled=True)
                if reason == 6:
                    row["posting_additional_max_check"] = 1
                runner.check_accounting(folder, row, 20)
                self.assertEqual(row["convergence_fraction"], 0)
                self.assertIsNone(row["underfill_given_convergence"])

    def test_convergence_does_not_relax_charges_protection_or_result_status(self):
        for field, value in (("supplement_distances", 4), ("preserved_heads", 0),
                             ("posting_target_met", 0), ("posting_activations", 0)):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                folder, row = self.fixture(Path(tmp), filled=True)
                data = np.fromfile(folder / "navigation.u64", dtype="<u8").reshape(1, 57)
                data[0, runner.COUNTERS.index(field)] = value
                data.tofile(folder / "navigation.u64")
                with self.assertRaises(RuntimeError):
                    runner.check_accounting(folder, row, 20)

    def test_observer_scope_retains_real_work_parity(self):
        disabled = {name: np.zeros(2, dtype="<u8") for name in runner.COUNTERS}
        enabled = {name: value.copy() for name, value in disabled.items()}
        for name in runner.OBSERVERS:
            enabled[name][:] = [20, 30]
        runner.compare_graph_work(disabled, enabled, False, True)
        changed = {name: value.copy() for name, value in enabled.items()}
        changed["graph_unique_h1"][0] += 1
        with self.assertRaises(RuntimeError):
            runner.compare_graph_work(enabled, changed, True, True)
        enabled["graph_distances"][0] += 1
        with self.assertRaises(RuntimeError):
            runner.compare_graph_work(disabled, enabled, False, True)
        disabled["graph_may_matches"][0] = 1
        with self.assertRaises(RuntimeError):
            runner.compare_graph_work(disabled, disabled, False, False)


if __name__ == "__main__":
    unittest.main()
