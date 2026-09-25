"""Synthetic renderer fixtures only; never reads or writes benchmark campaigns."""

import copy
import csv
import hashlib
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "plot_posting_min_sweep.R"
HISTORICAL_SCENARIOS = [
    "unfilter", "broad_tag", "medium_tag", "extreme_tag", "numeric", "mixed_dnf",
]
SELECTIVITY_SCENARIOS = [
    "unfilter", "broad_tag", "medium_tag", "sel_01pct", "mixed_dnf",
]
POSTGRAPH_VARIANTS = ["graph", "postgraph_shared", "postgraph_extra", "graph_total"]
SELECTIVITY_GRID = [16, 24, 48, 96, 192, 384]
OUTPUT_FILES = {
    "recall_qps.png", "recall_qps.pdf", "plotted_points.csv",
    "nprobe24.csv", "source_points.json", "plot_metadata.json",
}


class PostingMinSweepPlot(unittest.TestCase):
    def setUp(self):
        self.root = Path(f".synthetic-posting-plot-{uuid.uuid4().hex}")
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.environment = {
            **os.environ,
            **{key: str(self.root.resolve()) for key in ("TMPDIR", "TMP", "TEMP")},
        }
        self.render_count = 0
        self.fixture("--selectivity")

    def fixture(self, mode):
        variants = {
            "": ["graph", "min1", "min3", "min5", "min10"],
            "--member-postfilter": ["graph", "row_min1", "all_members_min1", "all_members_min10"],
            "--postgraph": POSTGRAPH_VARIANTS,
            "--selectivity": POSTGRAPH_VARIANTS,
            "--compact-storage": ["original_layout", "compact_layout"],
        }[mode]
        scenarios = (SELECTIVITY_SCENARIOS if mode == "--selectivity"
                     else ["medium_tag"] if mode == "--member-postfilter"
                     else HISTORICAL_SCENARIOS)
        self.protocol = {
            "fixture_provenance": "SYNTHETIC PLOTTING TEST ONLY; not benchmark measurements",
            "variants": list(variants), "scenarios": list(scenarios),
            "nprobe": SELECTIVITY_GRID if mode == "--selectivity" else [16, 24],
            "query_count": 1000,
            "repetitions": 4 if mode == "--compact-storage" else 2,
        }
        if mode in ("--postgraph", "--selectivity"):
            self.protocol.update(
                posting_anchor_count=8,
                budgets={
                    variant: {
                        "graph_maxcheck": 4096 if variant == "graph_total" else 2048,
                        "posting_additional_maxcheck": 2048 if variant == "postgraph_extra" else 0,
                    }
                    for variant in variants
                },
            )
        if mode == "--compact-storage":
            self.protocol["search_settings"] = {
                "graph_maxcheck": 2048, "posting_additional_maxcheck": 2048,
                "posting_anchor_count": 8,
            }
        if mode == "--selectivity":
            eligible = [10000, 1234, 113, 11, 4]
            self.protocol["scenario_metadata"] = {
                scenario: {
                    "title": f"Synthetic {scenario}",
                    "selectivity": count / 10000,
                    "eligible_count": count, "corpus_count": 10000,
                }
                for scenario, count in zip(scenarios, eligible)
            }
        self.make_rows()

    def make_rows(self):
        self.rows = []
        for scenario_index, scenario in enumerate(self.protocol["scenarios"]):
            for variant_index, variant in enumerate(self.protocol["variants"]):
                for probe_index, probe in enumerate(self.protocol["nprobe"]):
                    recalls = ([0.5, 0.68, 0.65, 0.74, 0.8, 0.78] if variant_index == 0
                               else [0.85, 0.86, 0.84, 0.90, 0.92, 0.94])
                    for repetition in range(1, self.protocol["repetitions"] + 1):
                        qps = 3000 - 350 * probe_index - 50 * variant_index - 10 * scenario_index
                        self.rows.append({
                            "scenario": scenario, "variant": variant, "nprobe": probe,
                            "repetition": repetition, "queries": 1000,
                            "recall": recalls[probe_index],
                            "qps": qps * [0.5, 1.5, 1.0, 0.25][repetition - 1],
                            "diagnostic": False,
                            "work": {"synthetic": True, "counters": [1, 2]},
                        })

    def render(self, flags=("--selectivity",), output=None, expression=None):
        (self.root / "registration.json").write_text(json.dumps(self.protocol) + "\n")
        (self.root / "plain-results.json").write_text(json.dumps(self.rows) + "\n")
        self.render_count += 1
        output = output or self.root / f"render-{self.render_count}"
        command = (["Rscript", "-e", expression] if expression is not None
                   else ["Rscript", str(SCRIPT)])
        result = subprocess.run(
            command + [str(self.root), str(output), *flags],
            env=self.environment, capture_output=True, text=True, check=False,
        )
        return result, output

    def assert_rejected(self, message, flags=("--selectivity",)):
        result, output = self.render(flags)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)
        self.assertFalse(output.exists(), result.stdout + result.stderr)

    def read_output(self, output):
        metadata = json.loads((output / "plot_metadata.json").read_text())
        with (output / "plotted_points.csv").open() as stream:
            points = list(csv.DictReader(stream))
        return metadata, points

    def assert_rendered(self, flags=("--selectivity",)):
        result, output = self.render(flags)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual({path.name for path in output.iterdir()}, OUTPUT_FILES)
        self.assertTrue((output / "recall_qps.png").read_bytes().startswith(b"\x89PNG"))
        self.assertTrue((output / "recall_qps.pdf").read_bytes().startswith(b"%PDF"))
        self.assertEqual(
            (output / "source_points.json").read_bytes(),
            (self.root / "plain-results.json").read_bytes(),
        )
        metadata, points = self.read_output(output)
        self.assertEqual(metadata["registration_md5"],
                         hashlib.md5((self.root / "registration.json").read_bytes()).hexdigest())
        self.assertEqual(metadata["data_snapshot_md5"],
                         hashlib.md5((output / "source_points.json").read_bytes()).hexdigest())
        self.assertFalse(metadata["interpolated"])
        self.assertFalse(metadata["diagnostic_timings_used"])
        for point in points:
            records = [
                row for row in self.rows
                if row["scenario"] == point["scenario"] and row["variant"] == point["variant"]
                and row["nprobe"] == int(point["nprobe"])
            ]
            qps = [row["qps"] for row in records]
            self.assertAlmostEqual(float(point["qps"]), sum(qps) / len(qps))
            self.assertEqual(float(point["qps_min"]), min(qps))
            self.assertEqual(float(point["qps_max"]), max(qps))
            self.assertEqual(int(point["runs"]), len(records))
        return output, metadata, points

    def test_five_scenarios_use_actual_densities_and_all_repetitions(self):
        output, metadata, points = self.assert_rendered()
        self.assertEqual(metadata["comparison"], "posting_selectivity")
        self.assertEqual(metadata["scenarios"], SELECTIVITY_SCENARIOS)
        self.assertEqual(metadata["scenario_metadata"], self.protocol["scenario_metadata"])
        self.assertEqual(metadata["raw_points_used"], 240)
        self.assertEqual(metadata["plotted_means"], 120)
        self.assertEqual(metadata["pending_scenarios"], [])
        self.assertEqual(metadata["unmeasured_scenarios"], [])
        self.assertEqual(list(dict.fromkeys(point["scenario"] for point in points)),
                         SELECTIVITY_SCENARIOS)
        with (output / "nprobe24.csv").open() as stream:
            probe24 = list(csv.DictReader(stream))
        self.assertEqual(len(probe24), 20)
        self.assertTrue(all(point["nprobe"] == "24" for point in probe24))
        graph = [point for point in points
                 if point["scenario"] == "unfilter" and point["variant"] == "graph"]
        self.assertEqual([int(point["nprobe"]) for point in graph], SELECTIVITY_GRID)
        self.assertLess(float(graph[2]["recall"]), float(graph[1]["recall"]))
        if shutil.which("pdftotext"):
            text = subprocess.check_output(
                ["pdftotext", "-layout", str(output / "recall_qps.pdf"), "-"], text=True,
            )
            for label in ("100% (no filter)", "12.34%", "1.13%", "0.11%", "0.04%"):
                self.assertIn(f"Actual selectivity: {label}", text)
            self.assertIn("No measured recall overlap with H1-only", text)
            self.assertIn("not confidence intervals", text)
            for forbidden in ("Numeric", "Extreme categorical", "not measured", "pending"):
                self.assertNotIn(forbidden, text)

    def test_refined_density_labels_do_not_assume_nominal_percentages(self):
        fractions = {
            "broad_tag": (0.170124930, "17.012493%"),
            "medium_tag": (0.017012493, "1.7012493%"),
            "mixed_dnf": (0.000425710, "0.042571%"),
        }
        for scenario, (fraction, _) in fractions.items():
            metadata = self.protocol["scenario_metadata"][scenario]
            metadata["selectivity"] = fraction
            del metadata["eligible_count"]
            del metadata["corpus_count"]
        self.protocol["scenario_metadata"]["mixed_dnf"]["title"] = (
            "Synthetic mixed DNF (separate workload)"
        )
        output, metadata, _ = self.assert_rendered()
        self.assertEqual(metadata["scenarios"], SELECTIVITY_SCENARIOS)
        for scenario, (fraction, _) in fractions.items():
            self.assertEqual(metadata["scenario_metadata"][scenario]["selectivity"], fraction)
        if shutil.which("pdftotext"):
            text = subprocess.check_output(
                ["pdftotext", "-layout", str(output / "recall_qps.pdf"), "-"], text=True,
            )
            for _, label in fractions.values():
                self.assertIn(f"Actual selectivity: {label}", text)
            self.assertIn("Synthetic mixed DNF (separate workload)", text)
            for forbidden in ("Actual selectivity: 10%", "Actual selectivity: 1%",
                              "Numeric", "Extreme categorical"):
                self.assertNotIn(forbidden, text)

    def test_arbitrary_registered_order_and_scalar_metadata_without_counts(self):
        self.protocol["scenarios"] = ["mixed_dnf", "custom_static_predicate"]
        self.protocol["scenario_metadata"] = {
            "custom_static_predicate": {"title": "Synthetic empty predicate", "selectivity": 0},
            "mixed_dnf": {"title": "Synthetic mixed predicate", "selectivity": 0.0061234569},
        }
        self.make_rows()
        _, metadata, points = self.assert_rendered()
        self.assertEqual(metadata["scenarios"], self.protocol["scenarios"])
        self.assertEqual(metadata["scenario_metadata"], self.protocol["scenario_metadata"])
        self.assertEqual(list(dict.fromkeys(point["scenario"] for point in points)),
                         self.protocol["scenarios"])
        self.assertEqual(len(points), 48)

    def test_layout_accepts_one_scenario_and_more_than_six(self):
        for count in (1, 7):
            with self.subTest(count=count):
                scenarios = [f"synthetic_predicate_{index}" for index in range(count)]
                self.protocol["scenarios"] = scenarios
                self.protocol["scenario_metadata"] = {
                    scenario: {"title": f"Synthetic predicate {index}", "selectivity": 0.1}
                    for index, scenario in enumerate(scenarios)
                }
                self.make_rows()
                output, metadata, points = self.assert_rendered()
                self.assertEqual(metadata["scenarios"], scenarios[0] if count == 1 else scenarios)
                self.assertEqual(len(points), count * 24)
                self.assertEqual(metadata["unmeasured_scenarios"], [])
                if shutil.which("pdftotext"):
                    text = subprocess.check_output(
                        ["pdftotext", "-layout", str(output / "recall_qps.pdf"), "-"], text=True,
                    )
                    self.assertEqual(text.count("Actual selectivity: 10%"), count)
                    self.assertEqual(text.count("\f"), 1)

    def test_missing_or_malformed_metadata_is_rejected(self):
        original = copy.deepcopy(self.protocol)
        for value in (None, [], {}, "not an object"):
            with self.subTest(metadata=value):
                self.protocol = copy.deepcopy(original)
                self.protocol["scenario_metadata"] = value
                self.assert_rejected("scenario_metadata")
        self.protocol = copy.deepcopy(original)
        del self.protocol["scenario_metadata"]
        self.assert_rejected("scenario_metadata")
        for scenario in ("mixed_dnf", "unregistered"):
            with self.subTest(scenario=scenario):
                self.protocol = copy.deepcopy(original)
                metadata = self.protocol["scenario_metadata"]
                if scenario == "mixed_dnf":
                    del metadata[scenario]
                else:
                    metadata[scenario] = dict(metadata["mixed_dnf"])
                self.assert_rejected("scenario_metadata")

    def test_metadata_fields_must_be_valid_scalars(self):
        original = copy.deepcopy(self.protocol)
        bad_fields = [
            ("title", None), ("title", ""), ("title", "  "), ("title", 12),
            ("title", ["Synthetic"]), ("selectivity", None),
            ("selectivity", -0.1), ("selectivity", 1.001),
            ("selectivity", "0.1234"), ("selectivity", True),
            ("selectivity", [0.1234]), ("selectivity", {"fraction": 0.1234}),
            ("eligible_count", -1), ("eligible_count", 1.5),
            ("eligible_count", 10001), ("eligible_count", 1235),
            ("corpus_count", 0), ("corpus_count", "10000"),
        ]
        for field, value in bad_fields:
            with self.subTest(field=field, value=value):
                self.protocol = copy.deepcopy(original)
                self.protocol["scenario_metadata"]["broad_tag"][field] = value
                self.assert_rejected("scenario_metadata")
        for field in ("title", "selectivity", "eligible_count", "corpus_count"):
            with self.subTest(missing=field):
                self.protocol = copy.deepcopy(original)
                del self.protocol["scenario_metadata"]["broad_tag"][field]
                self.assert_rejected("scenario_metadata")
        self.protocol = copy.deepcopy(original)
        self.protocol["scenario_metadata"]["unfilter"].update(
            selectivity=0.99, eligible_count=9900,
        )
        self.assert_rejected("unfilter")

    def test_selectivity_requires_the_frozen_campaign_grid(self):
        original = copy.deepcopy(self.protocol)
        for field, value in (
            ("nprobe", [16, 24]), ("nprobe", list(reversed(SELECTIVITY_GRID))),
            ("repetitions", 4), ("query_count", 999),
            ("scenarios", SELECTIVITY_SCENARIOS + ["unfilter"]),
            ("scenarios", []), ("scenarios", [""]),
            ("variants", POSTGRAPH_VARIANTS + ["graph"]),
        ):
            with self.subTest(field=field, value=value):
                self.protocol = copy.deepcopy(original)
                self.protocol[field] = value
                self.assert_rejected("Unexpected")

    def test_selectivity_preserves_postgraph_budget_checks(self):
        original = copy.deepcopy(self.protocol)
        for variant, field, value in (
            ("graph", "posting_additional_maxcheck", 1),
            ("postgraph_shared", "graph_maxcheck", 2047),
            ("postgraph_shared", "posting_additional_maxcheck", 1),
            ("postgraph_extra", "posting_additional_maxcheck", 0),
            ("graph_total", "graph_maxcheck", 2048),
        ):
            with self.subTest(variant=variant, field=field):
                self.protocol = copy.deepcopy(original)
                self.protocol["budgets"][variant][field] = value
                self.assert_rejected("postgraph budget declaration")
        self.protocol = copy.deepcopy(original)
        self.protocol["posting_anchor_count"] = 0
        self.assert_rejected("postgraph budget declaration")

    def test_missing_point_or_scenario_is_rejected(self):
        original = copy.deepcopy(self.rows)
        self.rows.pop()
        self.assert_rejected("Incomplete registered-method curve matrix")
        self.rows = [row for row in original if row["scenario"] != "sel_01pct"]
        self.assert_rejected("Incomplete registered-method curve matrix")

    def test_unregistered_diagnostic_or_duplicate_points_never_enter_partial_output(self):
        original = copy.deepcopy(self.rows)
        for flags in (("--selectivity",), ("--selectivity", "--partial")):
            for field, value in (
                ("scenario", "numeric"), ("scenario", "extreme_tag"),
                ("variant", "min1"), ("nprobe", 32), ("repetition", 3),
                ("queries", 999), ("diagnostic", True),
                ("diagnostic", None), ("diagnostic", "false"), ("diagnostic", 0),
            ):
                with self.subTest(flags=flags, field=field, value=value):
                    self.rows = copy.deepcopy(original)
                    self.rows[0][field] = value
                    self.assert_rejected("Invalid or mixed measurement", flags)
            self.rows = copy.deepcopy(original) + [dict(original[0])]
            self.assert_rejected("Duplicate measurement points", flags)

    def test_partial_uses_only_whole_method_probe_grids_per_repetition(self):
        self.rows = [
            row for row in self.rows
            if row["scenario"] == "unfilter"
            or (row["scenario"] == "broad_tag"
                and not (row["repetition"] == 2 and row["nprobe"] == 384))
            or (row["scenario"] == "medium_tag" and row["nprobe"] != 384)
        ]
        result, output = self.render(("--selectivity", "--partial"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        metadata, points = self.read_output(output)
        self.assertTrue(metadata["partial"])
        self.assertEqual(metadata["scenarios"], SELECTIVITY_SCENARIOS)
        self.assertEqual(metadata["complete_repetitions"], {"unfilter": [1, 2], "broad_tag": 1})
        self.assertEqual(metadata["pending_scenarios"], SELECTIVITY_SCENARIOS[2:])
        self.assertEqual(metadata["unmeasured_scenarios"], [])
        self.assertEqual(metadata["raw_points_used"], 72)
        self.assertEqual(len(points), 48)
        for point in points:
            self.assertEqual(int(point["runs"]), 2 if point["scenario"] == "unfilter" else 1)
            if point["scenario"] == "broad_tag":
                self.assertEqual(point["qps"], point["qps_min"])
                self.assertEqual(point["qps"], point["qps_max"])

    def test_partial_without_any_complete_repetition_is_rejected(self):
        self.rows = [self.rows[0]]
        self.assert_rejected("No scenario has a complete", ("--selectivity", "--partial"))

    def test_recall_changes_between_repetitions_are_not_averaged(self):
        self.rows[0]["recall"] += 0.01
        self.assert_rejected("Recall differs between deterministic repetitions")

    def test_existing_outputs_are_never_overwritten(self):
        output, _, _ = self.assert_rendered()
        before = {path.name: path.read_bytes() for path in output.iterdir()}
        result, _ = self.render(output=output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to overwrite", result.stderr)
        self.assertEqual(before, {path.name: path.read_bytes() for path in output.iterdir()})

    def test_registration_change_during_rendering_is_rejected(self):
        expression = (
            'png <- function(...) {'
            'cat("\\n", file = file.path(commandArgs(trailingOnly = TRUE)[[1]], '
            '"registration.json"), append = TRUE); grDevices::png(...) }; '
            f"source({json.dumps(str(SCRIPT))})"
        )
        result, output = self.render(expression=expression)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Registration changed while plotting", result.stderr)
        self.assertFalse(any(output.iterdir()))

    def test_comparison_flags_are_mutually_exclusive(self):
        modes = ("--member-postfilter", "--postgraph", "--compact-storage", "--selectivity")
        for flags in itertools.combinations(modes, 2):
            with self.subTest(flags=flags):
                self.assert_rejected("Usage:", flags)
        self.assert_rejected("Usage:", ("--selectivity", "--selectivity"))

    def test_historical_modes_keep_six_panels_and_their_original_grids(self):
        comparisons = {
            "": "posting_minimum_sweep",
            "--member-postfilter": "posting_member_postfilter",
            "--postgraph": "posting_postgraph",
            "--compact-storage": "compact_storage",
        }
        for mode, comparison in comparisons.items():
            with self.subTest(mode=mode):
                self.fixture(mode)
                _, metadata, points = self.assert_rendered((mode,) if mode else ())
                self.assertEqual(metadata["comparison"], comparison)
                self.assertEqual(metadata["scenarios"], HISTORICAL_SCENARIOS)
                self.assertEqual(metadata["nprobe"], [16, 24])
                self.assertNotIn("scenario_metadata", metadata)
                if mode == "--member-postfilter":
                    self.assertEqual(metadata["registered_scenarios"], "medium_tag")
                    self.assertEqual(metadata["unmeasured_scenarios"],
                                     [s for s in HISTORICAL_SCENARIOS if s != "medium_tag"])
                else:
                    self.assertEqual(metadata["unmeasured_scenarios"], [])
                self.assertEqual(len(points),
                                 len(self.protocol["variants"]) * len(self.protocol["scenarios"]) * 2)


if __name__ == "__main__":
    unittest.main()
