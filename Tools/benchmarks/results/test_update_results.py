import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import update_results as store


def catalog():
    return {"schema_version": 1, "kind": "catalog", "revision": 1,
            "completed_at": "2026-10-03T00:00:00Z", "source": {"description": "Test catalog"},
            "catalog": {"datasets": ["SIFT1B"], "studies": [{"id": "main"}, {"id": "ablation"}],
                        "expected_cells": [{"study": "main", "variant": "default", "dataset": "SIFT1B",
                                            "algorithm": "SPTAG", "scenario": name, "phase": "single", "threads": 1}
                                           for name in ("label", "other")]}}


def measurement(minute=1, generation=1, qps=100, scenario="label", complete=True):
    client = f"client-{generation}"
    repetitions = [{"repeat": i, "threads": 1, "cohort_queries": 1000, "search_value": 96,
                    "client_version": client, "recall": .9, "qps": qps}
                   for i in range(1, 3 if complete else 2)]
    return {"schema_version": 1, "kind": "measurement", "study": "main", "variant": "default",
            "dataset": "SIFT1B", "algorithm": "SPTAG",
            "version": {"id": client, "client": client, "index": f"index-{generation}", "order": generation},
            "completed_at": f"2026-10-03T12:{minute:02d}:00Z",
            "source": {"description": "Test accepted experiment"},
            "point": {"scenario": scenario, "predicate": "tag = 1", "phase": "single", "threads": 1,
                      "cohort_queries": 1000, "parameters": '{"MaxCheck":2048}', "curve_id": "test",
                      "search_parameter": "nprobe", "search_value": 96, "selectivity": .1,
                      "candidate_count": 100000000, "recall": .9, "recall_min_observed": .9,
                      "recall_max_repeat": .9, "qps": qps, "qps_min": qps, "qps_max": qps,
                      "repeats": len(repetitions), "expected_repeats": 2, "complete_repeats_same_client": complete},
            "measurements": repetitions}


class ResultsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        store.update(self.directory, [catalog()])

    def tearDown(self):
        self.temp.cleanup()

    def test_latest_time_not_best_qps(self):
        current = store.update(self.directory, [measurement(qps=300), measurement(minute=2, qps=50)])
        self.assertEqual(current["points"][0]["qps"], 50)

    def test_late_old_version_never_restores_old_cells(self):
        store.update(self.directory, [measurement(scenario="other")])
        current = store.update(self.directory, [measurement(generation=2),
                                                 measurement(minute=9, scenario="other")])
        self.assertEqual([p["scenario"] for p in current["points"]], ["label"])
        self.assertEqual(next(c["status"] for c in current["coverage"] if c["scenario"] == "other"), "not_measured")

    def test_incomplete_latest_version_does_not_fall_back(self):
        store.update(self.directory, [measurement()])
        current = store.update(self.directory, [measurement(generation=2, complete=False)])
        self.assertEqual(current["points"], [])
        self.assertTrue(any(c["status"] == "incomplete_repetitions" for c in current["coverage"]))

    def test_incomplete_repeat_preserves_latest_complete_same_version(self):
        store.update(self.directory, [measurement(qps=120)])
        current = store.update(self.directory, [measurement(minute=2, qps=40, complete=False)])
        self.assertEqual(current["points"][0]["qps"], 120)

    def test_append_only_deduplication_and_stable_refresh(self):
        event = measurement()
        store.update(self.directory, [event])
        history = (self.directory / "history.jsonl").read_bytes()
        current = (self.directory / "current.json").read_bytes()
        store.update(self.directory, [copy.deepcopy(event)])
        store.update(self.directory)
        self.assertEqual(history, (self.directory / "history.jsonl").read_bytes())
        self.assertEqual(current, (self.directory / "current.json").read_bytes())
        store.update(self.directory, [measurement(minute=2)])
        self.assertTrue((self.directory / "history.jsonl").read_bytes().startswith(history))

    def test_identical_measurement_reimport_deduplicates_changed_source_description(self):
        event = measurement()
        store.update(self.directory, [event])
        event["source"]["description"] = "Same experiment through another accepted importer"
        current = store.update(self.directory, [event])
        self.assertEqual(current["history"]["records"], 2)

    def test_ambiguous_same_time_rejected_before_append(self):
        store.update(self.directory, [measurement()])
        previous = (self.directory / "history.jsonl").read_bytes()
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            store.update(self.directory, [measurement(qps=200)])
        self.assertEqual(previous, (self.directory / "history.jsonl").read_bytes())

    def test_ablation_variants_have_independent_latest_versions(self):
        reference, optimized = measurement(), measurement(generation=2)
        reference.update(study="ablation", variant="reference")
        optimized.update(study="ablation", variant="optimized")
        current = store.update(self.directory, [reference, optimized])
        self.assertEqual({p["variant"] for p in current["points"]}, {"reference", "optimized"})

    def test_changed_version_order_and_duplicate_order_rejected(self):
        store.update(self.directory, [measurement()])
        event = measurement(minute=2)
        event["version"]["order"] = 2
        with self.assertRaisesRegex(ValueError, "cannot change its order"):
            store.update(self.directory, [event])
        event = measurement(generation=2)
        event["version"]["order"] = 1
        with self.assertRaisesRegex(ValueError, "Conflicting version"):
            store.update(self.directory, [event])

    def test_invalid_measurements_do_not_write_history(self):
        changes = [
            lambda e: e["point"].update(qps=0),
            lambda e: e["point"].update(recall=1.1),
            lambda e: e["measurements"][0].update(client_version="wrong-client"),
            lambda e: e["measurements"][0].update(duplicate_ids=1),
            lambda e: e["measurements"][0].update(diagnostic=True),
            lambda e: e["measurements"][0].update(phase_timing=True),
            lambda e: e["measurements"][0].update(qps=500),
            lambda e: e["measurements"][0].update(repeat=2),
            lambda e: e.update(completed_at="2026-10-03"),
        ]
        previous = (self.directory / "history.jsonl").read_bytes()
        for edit in changes:
            event = measurement()
            edit(event)
            with self.assertRaises(ValueError):
                store.update(self.directory, [event])
        self.assertEqual(previous, (self.directory / "history.jsonl").read_bytes())

    def test_truncated_history_fails_without_changing_current(self):
        previous = (self.directory / "current.json").read_bytes()
        with (self.directory / "history.jsonl").open("ab") as stream:
            stream.write(b'{"unfinished":')
        with self.assertRaisesRegex(ValueError, "incomplete"):
            store.update(self.directory)
        self.assertEqual(previous, (self.directory / "current.json").read_bytes())

    def test_atomic_failure_is_recoverable_from_history(self):
        previous = (self.directory / "current.json").read_bytes()
        with patch.object(store.os, "replace", side_effect=OSError("simulated atomic publication failure")):
            with self.assertRaises(OSError):
                store.update(self.directory, [measurement()])
        self.assertEqual(previous, (self.directory / "current.json").read_bytes())
        self.assertFalse(list(self.directory.glob(".current-*")))
        self.assertEqual(len(store.update(self.directory)["points"]), 1)

    def test_concurrent_writers_do_not_lose_events(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: store.update(self.directory, [measurement(minute=i, qps=100+i)]), range(1, 9)))
        current = store.update(self.directory)
        self.assertEqual(current["history"]["records"], 9)
        self.assertEqual(current["points"][0]["qps"], 108)

    def test_failed_experiment_command_is_not_published(self):
        previous = (self.directory / "history.jsonl").read_bytes()
        result = subprocess.run([sys.executable, "-B", str(Path(store.__file__)), "--data-dir", str(self.directory),
                                 "run", "--event", str(self.directory / "new-event.json"), "--",
                                 sys.executable, "-c", "raise SystemExit(7)"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(previous, (self.directory / "history.jsonl").read_bytes())

    def test_successful_experiment_command_publishes_and_refreshes(self):
        output = self.directory / "new-event.json"
        result = subprocess.run(
            [sys.executable, "-B", str(Path(store.__file__)), "--data-dir", str(self.directory),
             "run", "--event", str(output), "--", sys.executable, "-c",
             "from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2])",
             str(output), json.dumps(measurement())], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        current = store.update(self.directory, check=True)
        self.assertEqual(current["history"]["records"], 2)
        self.assertEqual(current["points"][0]["qps"], 100)

    def test_existing_output_is_rejected_before_running_command(self):
        output = self.directory / "existing.json"
        output.write_text(json.dumps(measurement()))
        marker = self.directory / "ran"
        result = subprocess.run(
            [sys.executable, "-B", str(Path(store.__file__)), "--data-dir", str(self.directory),
             "run", "--event", str(output), "--", sys.executable, "-c",
             "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker)],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing stale-success", result.stderr)
        self.assertFalse(marker.exists())

    def test_accounting_tables_validate_rows_and_update_coverage(self):
        registration = catalog()
        registration["revision"] = 2
        registration["catalog"]["studies"].append({"id": "overhead"})
        registration["catalog"]["expected_cells"].append({"study": "overhead", "dataset": "SIFT1B"})
        event = measurement()
        del event["point"], event["measurements"]
        event.update(kind="table", study="overhead", table_name="stage-times", status="validated",
                     rows=[{"category": "Graph", "unit": "ms/query", "value": 1},
                           {"category": "SSD", "unit": "ms/query", "value": 2}])
        current = store.update(self.directory, [registration, event])
        coverage = next(c for c in current["coverage"] if c["study"] == "overhead")
        self.assertEqual((coverage["status"], coverage["points"]), ("measured", 2))
        history = (self.directory / "history.jsonl").read_bytes()
        for change in ({"unit": "seconds"}, {"category": "unknown"}, {"value": -1}):
            bad = copy.deepcopy(event)
            bad["rows"][0].update(change)
            with self.assertRaises(ValueError):
                store.update(self.directory, [bad])
        bad = copy.deepcopy(event)
        bad["rows"].append(bad["rows"][0])
        with self.assertRaisesRegex(ValueError, "Duplicate accounting"):
            store.update(self.directory, [bad])
        self.assertEqual(history, (self.directory / "history.jsonl").read_bytes())

    def test_latest_table_replaces_older_values_without_losing_history(self):
        event = measurement()
        del event["point"], event["measurements"]
        event.update(kind="table", table_name="example", status="validated", rows=[{"value": 1}])
        store.update(self.directory, [event])
        event = copy.deepcopy(event)
        event.update(completed_at="2026-10-03T12:02:00Z", rows=[{"value": 2}])
        current = store.update(self.directory, [event])
        self.assertEqual(current["tables"][0]["rows"], [{"value": 2}])
        self.assertEqual(current["history"]["records"], 3)

    def test_check_detects_modified_current_json(self):
        (self.directory / "current.json").write_text("{}\n")
        with self.assertRaisesRegex(ValueError, "stale"):
            store.update(self.directory, check=True)
        store.update(self.directory)
        store.update(self.directory, check=True)

    def presentation_catalog(self):
        event = catalog()
        event["revision"] = 2
        event["catalog"]["experiment_views"] = {
            "main_selection": "observed-pareto-envelope",
            "main_protocol": {"topk": 100, "cohort_queries": 1000, "phase": "single", "threads": 1},
            "minimum_qps": 3, "main_studies": ["main", "ablation"],
            "disclosures": ["Observed tuning envelope, not a single fixed configuration."]}
        return event

    def presentation_measurement(self, variant, recall, qps, **changes):
        event = measurement(qps=qps)
        event["variant"] = variant
        event["point"].update(topk=100, recall=recall, recall_min_observed=recall,
                              recall_max_repeat=recall, **changes)
        for row in event["measurements"]:
            row["recall"] = recall
        return event

    def test_pareto_view_preserves_all_fixed_curves_and_exact_sources(self):
        events = [self.presentation_measurement(name, recall, qps) for name, recall, qps in (
            ("fast", .8, 100), ("dominated", .8, 90), ("middle", .9, 80),
            ("same-qps", .85, 80), ("accurate", .99, 30), ("tie", .9, 80),
            ("below-floor", 1, 2))]
        current = store.update(self.directory, [self.presentation_catalog(), *events])
        views = current["experiments"]
        self.assertEqual(len(current["points"]), 7)
        main = views["main"]["curves"]
        self.assertEqual(len(main), 1)
        self.assertEqual([(p["recall"], p["qps"]) for p in main[0]["points"]],
                         [(.8, 100), (.9, 80), (.99, 30)])
        self.assertEqual(main[0]["candidate_points"], 6)
        by_id = {p["event_id"]: p for p in current["points"]}
        for point in main[0]["points"]:
            original = by_id[point["event_id"]]
            self.assertEqual(point["parameters"], json.loads(original["parameters"]))
            self.assertEqual(point["version"], original["version"])
            self.assertEqual(point["source_curve_id"], original["curve_id"])
        tied = [p["event_id"] for p in current["points"] if p["recall"] == .9]
        self.assertEqual(main[0]["points"][1]["event_id"], min(tied))
        self.assertEqual({p["event_id"] for c in views["ablations"]["curves"] for p in c["points"]},
                         set(by_id))
        self.assertEqual(store.experiment_views(list(reversed(current["points"])),
                                               current["catalog"]["experiment_views"]), views)
        snapshot = (self.directory / "current.json").read_bytes()
        store.update(self.directory, list(reversed(events)))
        self.assertEqual(snapshot, (self.directory / "current.json").read_bytes())
        store.update(self.directory, check=True)

    def test_pareto_never_mixes_predicates_algorithms_or_recall_depths(self):
        ordinary = self.presentation_measurement("ordinary", .9, 10)
        predicate = self.presentation_measurement("predicate", .99, 100, predicate="tag = 2")
        algorithm = self.presentation_measurement("algorithm", .99, 100)
        algorithm["algorithm"] = "Other"
        top10 = self.presentation_measurement("top10", 1, 1000)
        top10["point"]["topk"] = 10
        current = store.update(self.directory, [self.presentation_catalog(), ordinary, predicate, algorithm, top10])
        self.assertEqual(len(current["experiments"]["main"]["curves"]), 3)
        self.assertEqual(len(current["experiments"]["ablations"]["curves"]), 4)
        selected = {p["variant"] for c in current["experiments"]["main"]["curves"] for p in c["points"]}
        self.assertNotIn("top10", selected)

    def test_presentation_does_not_restore_superseded_or_incomplete_measurements(self):
        old = self.presentation_measurement("same", .9, 1000)
        newer = measurement(generation=2, qps=10)
        newer["variant"] = "same"
        newer["point"]["topk"] = 100
        incomplete = measurement(generation=3, complete=False)
        incomplete["point"]["topk"] = 100
        current = store.update(self.directory, [self.presentation_catalog(), old, newer, incomplete])
        selected = current["experiments"]["main"]["curves"][0]["points"]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["qps"], 10)
        self.assertEqual(len(current["experiments"]["ablations"]["curves"]), 1)

    def test_bad_presentation_depth_fails_before_history_append(self):
        bad = self.presentation_measurement("bad", .9, 100)
        bad["version"]["topk"] = 10
        previous = (self.directory / "history.jsonl").read_bytes()
        with self.assertRaisesRegex(ValueError, "recall depths"):
            store.update(self.directory, [self.presentation_catalog(), bad])
        self.assertEqual(previous, (self.directory / "history.jsonl").read_bytes())

    def test_main_protocol_filter_preserves_other_cohorts_in_ablations(self):
        ordinary = self.presentation_measurement("ordinary", .9, 10)
        other_cohort = self.presentation_measurement("cohort", 1, 1000, cohort_queries=32)
        for row in other_cohort["measurements"]:
            row["cohort_queries"] = 32
        throughput = self.presentation_measurement("throughput", 1, 1000, phase="throughput", threads=4)
        for row in throughput["measurements"]:
            row["threads"] = 4
        current = store.update(self.directory, [self.presentation_catalog(), ordinary, other_cohort, throughput])
        curves = current["experiments"]["main"]["curves"]
        self.assertEqual(len(curves), 1)
        self.assertEqual([p["variant"] for p in curves[0]["points"]], ["ordinary"])
        self.assertEqual(len(current["experiments"]["ablations"]["curves"]), 3)

    def test_legacy_throughput_does_not_fill_latest_main_or_mix_clients(self):
        main = self.presentation_measurement("default", .9, 10)
        main["algorithm"] = "PipeANN"
        main["version"].update(id="top100-client", order=4)
        for row in main["measurements"]:
            row["client_version"] = main["version"]["client"]
        registered = self.presentation_catalog()
        planned = dict(study="main", variant="default", dataset="SIFT1B",
                       algorithm="PipeANN", scenario="label", phase="throughput", threads=48)
        registered["catalog"]["expected_cells"].append(planned)
        before = store.update(self.directory, [registered, main])
        updated = copy.deepcopy(registered)
        updated["revision"] = 3
        updated["catalog"]["studies"].append({"id": "legacy-throughput"})
        historical = self.presentation_measurement("top10", .95, 1000, phase="throughput", threads=48)
        historical.update(study="legacy-throughput", algorithm="PipeANN")
        historical["point"]["topk"] = 10
        for row in historical["measurements"]:
            row["threads"] = 48
        incomplete = copy.deepcopy(historical)
        incomplete["point"].update(scenario="partial", repeats=1, complete_repeats_same_client=False)
        incomplete["measurements"] = incomplete["measurements"][:1]
        legacy = copy.deepcopy(incomplete)
        legacy["version"].update(id="legacy-return", client="legacy-return", order=0)
        legacy["measurements"][0].update(repeat=2, client_version="legacy-return")
        result = store.update(self.directory, [updated, historical, incomplete, legacy])
        self.assertEqual(result["experiments"]["main"], before["experiments"]["main"])
        self.assertEqual(len(result["points"]), 2)
        self.assertEqual([p for p in result["points"] if p["study"] == "main"], before["points"])
        self.assertEqual(next(c["status"] for c in result["coverage"]
                              if all(c.get(k) == v for k, v in planned.items())), "not_measured")
        self.assertTrue(any(c["status"] == "incomplete_repetitions" for c in result["coverage"]))
        snapshot = (self.directory / "current.json").read_bytes()
        store.update(self.directory, [updated, historical, incomplete, legacy])
        self.assertEqual((self.directory / "current.json").read_bytes(), snapshot)


if __name__ == "__main__":
    unittest.main()
