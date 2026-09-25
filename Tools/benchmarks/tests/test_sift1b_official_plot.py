"""Synthetic R plotting fixtures only: no native runs or real campaign outputs."""

import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import unittest
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "plot_sift1b_official.R"
SCENARIOS = ["unfilter", "broad_tag", "medium_tag", "sel_01pct", "mixed_dnf"]
ENGINES = ["SPTAG_adaptive", "SPTAG_H1", "PipeANN"]
REQUIRED = [
    "scenario", "engine", "L", "queries", "repeats", "threads", "cpu_nodes",
    "recall", "recall_min", "recall_max", "qps", "qps_min", "qps_max",
    "candidate_count", "selectivity", "predicate",
]
FIGURES = [
    f"{stem}.{extension}"
    for stem in ["recall_qps"] + [f"{scenario}_recall_qps" for scenario in SCENARIOS]
    for extension in ("png", "pdf")
]
OUTPUTS = set(FIGURES + [
    "plotted_points.csv", "plot_metadata.json", "hash_manifest.csv",
    "source_summary.csv", "source_registration.json",
])


class OfficialSelectivityPlot(unittest.TestCase):
    def setUp(self):
        self.root = Path(f".synthetic-official-selectivity-{uuid.uuid4().hex}")
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.environment = {
            **os.environ,
            **{key: str(self.root.resolve()) for key in ("TMPDIR", "TMP", "TEMP")},
        }
        self.calls = 0
        counts = [1000000000, 170124930, 17012493, 1000735, 425710]
        titles = ["Unfiltered", "Broad categorical", "Medium categorical",
                  "Categorical near 0.1%", "Mixed DNF (separate workload)"]
        self.registration = {
            "schema_version": 1, "dataset": "SIFT1B", "corpus_count": 1000000000,
            "comparison": "fresh_paired", "scenarios": list(SCENARIOS),
            "caption_note": "SYNTHETIC FIXTURE - NOT BENCHMARK MEASUREMENTS",
            "scenario_metadata": {
                scenario: {
                    "title": f"Synthetic {title}", "predicate": f"SYNTHETIC predicate: {scenario}",
                    "candidate_count": count, "selectivity": count / 1000000000,
                }
                for scenario, count, title in zip(SCENARIOS, counts, titles)
            },
            "engines": {},
        }
        for engine, grid in zip(ENGINES, ([16, 24, 48, 96], [16, 32, 64], [10, 20, 40, 80, 160])):
            spann = engine != "PipeANN"
            self.registration["engines"][engine] = {
                "native_control": "nprobe" if spann else "searchL",
                "L": grid, "repeats": 2 if spann else 3,
                "queries": 1000, "threads": 1, "cpu_nodes": "2", "memory_nodes": "2",
                "query_cohort_id": "SYNTHETIC-identical-ordered-1000-query-cohort",
                "io_mode": "buffered" if spann else "direct",
                "qps_aggregation": "arithmetic_mean" if spann else "median",
                "index_id": f"SYNTHETIC-{'shared-SPTAG' if spann else 'PipeANN'}-index",
                "runtime_id": f"SYNTHETIC-frozen-{'SPTAG' if spann else 'PipeANN'}-runtime",
                "source_date": "2026-09-24",
                "search_policy": (
                    "predicate_first_adaptive_global_posting_frontier" if engine == "SPTAG_adaptive"
                    else "h1_only" if engine == "SPTAG_H1" else "official_search_disk_index"
                ),
                "controls": {
                    "graph_maxcheck": 2048,
                    "posting_additional_maxcheck": 2048 if engine == "SPTAG_adaptive" else 0,
                    "posting_anchor_count": 8, "enable_posting_navigation": engine == "SPTAG_adaptive",
                } if spann else {"pipeline": 32, "unfiltered_mem_L": 10, "filtered_mem_L": 0},
            }
        self.make_rows()

    def make_rows(self):
        self.rows = []
        for scenario_index, scenario in enumerate(SCENARIOS):
            entry = self.registration["scenario_metadata"][scenario]
            for engine_index, (engine, controls) in enumerate(self.registration["engines"].items()):
                for point_index, control in enumerate(controls["L"]):
                    recall = ([0.0, 0.65, 0.60, 0.9, 1.0, 0.99, 0.98][point_index]
                              if point_index < 7 else 0.98 + (point_index - 6) * 0.001)
                    qps = 3200 / (1 + point_index + engine_index * 0.3 + scenario_index * 0.1)
                    self.rows.append({
                        "scenario": scenario, "engine": engine, "L": control,
                        "queries": controls["queries"], "repeats": controls["repeats"],
                        "threads": controls["threads"], "cpu_nodes": controls["cpu_nodes"],
                        "recall": recall, "recall_min": max(0, recall - 0.02),
                        "recall_max": min(1, recall + 0.01),
                        "qps": qps, "qps_min": qps * 0.75, "qps_max": qps * 1.5,
                        "candidate_count": entry["candidate_count"], "selectivity": entry["selectivity"],
                        "predicate": entry["predicate"],
                        **{key: controls[key] for key in (
                            "query_cohort_id", "memory_nodes", "io_mode", "index_id",
                            "runtime_id", "search_policy", "qps_aggregation", "native_control", "source_date",
                        )},
                        "diagnostic": False, "stage": "measured",
                    })
        random.Random(19).shuffle(self.rows)

    def render(self, flags=("--selectivity",), output=None, expression=None):
        self.calls += 1
        fields = list(dict.fromkeys(key for row in self.rows for key in row))
        with (self.root / "summary.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(self.rows)
        (self.root / "plot_registration.json").write_text(json.dumps(self.registration) + "\n")
        output = output or self.root / f"render-{self.calls}"
        command = ["Rscript", "-e", expression] if expression else ["Rscript", str(SCRIPT)]
        result = subprocess.run(
            command + [str(self.root), str(output), *flags],
            env=self.environment, capture_output=True, text=True, check=False, timeout=90,
        )
        return result, output

    @staticmethod
    def csv_rows(path):
        with path.open(newline="") as stream:
            return list(csv.DictReader(stream))

    def assert_rejected(self, message, flags=("--selectivity",)):
        result, output = self.render(flags)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)
        self.assertFalse(output.exists(), result.stdout + result.stderr)

    def assert_rendered(self, expression=None):
        result, output = self.render(expression=expression)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual({path.name for path in output.iterdir()}, OUTPUTS)
        for name in FIGURES:
            prefix = b"\x89PNG" if name.endswith(".png") else b"%PDF"
            self.assertTrue((output / name).read_bytes().startswith(prefix), name)
        for original, snapshot in (("summary.csv", "source_summary.csv"),
                                   ("plot_registration.json", "source_registration.json")):
            self.assertEqual((self.root / original).read_bytes(), (output / snapshot).read_bytes())
        metadata = json.loads((output / "plot_metadata.json").read_text())
        self.assertEqual(metadata["scenarios"], SCENARIOS)
        selected = [engine for engine in ENGINES if engine in self.registration["engines"]]
        self.assertEqual(metadata["engine_order"], selected)
        self.assertEqual(metadata["engines"], self.registration["engines"])
        self.assertEqual(metadata["scenario_metadata"], self.registration["scenario_metadata"])
        self.assertEqual(metadata["source_rows"], len(self.rows))
        self.assertEqual(metadata["plotted_points"], len(self.rows))
        self.assertEqual(metadata["recall_axis"], [0, 1])
        self.assertEqual(metadata["qps_axis"], "log10")
        self.assertFalse(metadata["interpolated"])
        self.assertTrue(metadata["all_measured_points_retained"])
        self.assertFalse(metadata["diagnostic_timings_used"])
        source = self.csv_rows(self.root / "summary.csv")
        expected = sorted(source, key=lambda row: (
            SCENARIOS.index(row["scenario"]), selected.index(row["engine"]), float(row["L"]),
        ))
        self.assertEqual(self.csv_rows(output / "plotted_points.csv"), expected)
        for key, name in (("summary", "summary.csv"), ("registration", "plot_registration.json")):
            self.assertEqual(metadata["input_hashes"][key],
                             hashlib.md5((self.root / name).read_bytes()).hexdigest())
        for name, digest in metadata["output_hashes"].items():
            self.assertEqual(hashlib.md5((output / name).read_bytes()).hexdigest(), digest, name)
        manifest = self.csv_rows(output / "hash_manifest.csv")
        self.assertEqual(len(manifest), len(OUTPUTS) - 1 + 2)
        self.assertEqual({row["path"] for row in manifest if row["role"] == "output"},
                         OUTPUTS - {"hash_manifest.csv"})
        for row in manifest:
            path = Path(row["path"]) if row["role"] == "input" else output / row["path"]
            self.assertEqual(hashlib.md5(path.read_bytes()).hexdigest(), row["md5"], row["path"])
        return output, metadata

    def pdf_text(self, output):
        if not shutil.which("pdftotext"):
            self.skipTest("PDF text assertions require pdftotext")
        text = subprocess.check_output(
            ["pdftotext", "-layout", str(output / "recall_qps.pdf"), "-"], text=True,
        )
        return " ".join(text.split())

    def test_complete_three_engine_figures_preserve_exact_points_and_plot_geometry(self):
        self.rows[0].update(qps="1234.567890123456789", qps_min="1000.00000000000000001",
                            qps_max="1400.00000000000000001")
        expression = (
            "ggsave <- function(filename, plot, ...) {"
            "b <- ggplot2::ggplot_build(plot); "
            "stopifnot(nrow(b$data[[3]]) == nrow(plot$data), nrow(b$data[[4]]) == nrow(plot$data)); "
            "for (p in b$layout$panel_params) stopifnot(isTRUE(all.equal(p$x.range, c(0, 1)))); "
            "panels <- unique(as.character(plot$data$scenario)); "
            "for (p in seq_along(panels)) for (g in seq_along(levels(plot$data$engine))) {"
            "expected <- plot$data[plot$data$scenario == panels[[p]] & "
            "plot$data$engine == levels(plot$data$engine)[[g]], ]; "
            "actual <- b$data[[3]][b$data[[3]]$PANEL == p & b$data[[3]]$group == g, ]; "
            "stopifnot(isTRUE(all.equal(actual$x, expected$recall)), "
            "isTRUE(all.equal(actual$y, log10(expected$qps)))) }; "
            "ggplot2::ggsave(filename, plot = plot, ...) }; "
            f"source({json.dumps(str(SCRIPT))})"
        )
        output, metadata = self.assert_rendered(expression)
        self.assertEqual(metadata["plotted_points"], 60)
        text = self.pdf_text(output)
        for label in ("100% (no filter)", "17.012493%", "1.7012493%", "0.1000735%", "0.042571%"):
            self.assertIn(f"Actual selectivity: {label}", text)
        for label in ("SYNTHETIC FIXTURE", "Fresh paired", "SPTAG H1-only (MaxCheck 2048)",
                      "arithmetic-mean QPS; 2 complete runs", "median QPS; 3 complete runs",
                      "CPU NUMA 2 / memory NUMA 2", "SPTAG buffered versus PipeANN direct",
                      "nprobe", "searchL", "not equal work", "not confidence intervals"):
            self.assertIn(label, text)
        for label in ("Numeric-only", "Extreme categorical", "SIFT1M", "six-scenario"):
            self.assertNotIn(label, text)

    def test_two_engines_do_not_invent_h1_control(self):
        del self.registration["engines"]["SPTAG_H1"]
        self.make_rows()
        output, metadata = self.assert_rendered()
        self.assertEqual(metadata["engine_order"], ["SPTAG_adaptive", "PipeANN"])
        self.assertNotIn("H1-only", self.pdf_text(output))

    def test_explicit_adaptive_only_reused_baseline_preserves_all_100_points(self):
        del self.registration["engines"]["SPTAG_H1"]
        self.registration.update(series="adaptive_only", comparison="reused_pipeann_baseline")
        for name, engine in self.registration["engines"].items():
            engine.update(
                repeats=2, qps_aggregation="median", cpu_nodes="3", memory_nodes="3",
                measurement_reused=name == "PipeANN",
                source_date="2026-09-24" if name == "PipeANN" else "2026-09-25",
                L=([10, 15, 20, 25, 30, 35, 40, 50, 60, 80, 120, 200, 400]
                   if name == "PipeANN" else [16, 24, 48, 96, 192, 384, 768]),
            )
        self.make_rows()
        output, metadata = self.assert_rendered()
        self.assertEqual(metadata["series"], "adaptive_only")
        self.assertEqual(metadata["comparison"], "reused_pipeann_baseline")
        self.assertEqual(metadata["plotted_points"], 100)
        self.assertEqual(metadata["engine_order"], ["SPTAG_adaptive", "PipeANN"])
        text = self.pdf_text(output)
        self.assertIn("SPTAG rerun with preserved PipeANN baseline", text)
        self.assertIn("source 2026-09-24", text)
        self.assertIn("source 2026-09-25", text)
        self.assertNotIn("Fresh paired", text)
        self.assertNotIn("H1-only", text)

    def test_explicit_series_rejects_mismatched_engine_sets(self):
        self.registration["series"] = "adaptive_only"
        self.assert_rejected("Declared series")
        self.registration["series"] = "with_h1_control"
        del self.registration["engines"]["SPTAG_H1"]
        self.make_rows()
        self.assert_rejected("Declared series")
        self.registration["series"] = "unknown"
        self.assert_rejected("Declared series")

    def test_reused_baseline_requires_explicit_reuse_dates_and_matching_placement(self):
        del self.registration["engines"]["SPTAG_H1"]
        self.registration.update(series="adaptive_only", comparison="reused_pipeann_baseline")
        self.make_rows()
        self.assert_rejected("measurement_reused")
        for name, engine in self.registration["engines"].items():
            engine["measurement_reused"] = name == "PipeANN"
        self.registration["engines"]["PipeANN"]["measurement_reused"] = False
        self.assert_rejected("measurement_reused")
        self.registration["engines"]["PipeANN"]["measurement_reused"] = True
        self.registration["engines"]["PipeANN"]["cpu_nodes"] = "0"
        self.make_rows()
        self.assert_rejected("matching cpu_nodes")
        self.registration["engines"]["PipeANN"]["cpu_nodes"] = "2"
        del self.registration["engines"]["PipeANN"]["source_date"]
        self.assert_rejected("source_date")

    def test_seven_point_median_campaign_preserves_native_case_protocol(self):
        grid = [16, 24, 48, 96, 192, 384, 768]
        for engine in self.registration["engines"].values():
            engine.update(cpu_nodes="3", memory_nodes="3")
        for name in ("SPTAG_adaptive", "SPTAG_H1"):
            engine = self.registration["engines"][name]
            engine.update(L=grid, repeats=2, qps_aggregation="median", index_h1_count=120040156)
            engine["controls"]["search_posting_page_limit"] = 3
            engine["native_case_protocol"] = {
                "fixture_only": True, "warmup_queries": 1000,
                "measured_queries": 1000, "replay_queries": 1000,
                "case_order": "reversed on the second ordinary pass",
                "sweep_order": "ascending",
            }
        self.make_rows()
        output, metadata = self.assert_rendered()
        self.assertEqual(metadata["plotted_points"], 95)
        text = self.pdf_text(output)
        for label in ("median QPS; 2 complete runs", "CPU NUMA 3 / memory NUMA 3",
                      "Native nprobe = [16, 24, 48, 96, 192, 384, 768]",
                      "search_posting_page_limit=3",
                      "same-index/runtime project baseline with posting disabled",
                      "not an unmodified Microsoft SPTAG build"):
            self.assertIn(label, text)
        points = self.csv_rows(output / "plotted_points.csv")
        for name in ("SPTAG_adaptive", "SPTAG_H1"):
            self.assertEqual(metadata["engines"][name]["native_case_protocol"],
                             self.registration["engines"][name]["native_case_protocol"])
            for scenario in SCENARIOS:
                self.assertEqual([int(point["L"]) for point in points
                                  if point["engine"] == name and point["scenario"] == scenario], grid)

    def test_familiar_summary_columns_need_no_rewritten_native_records(self):
        self.rows = [{key: row[key] for key in REQUIRED} for row in self.rows]
        self.assert_rendered()

    def test_equivalent_numeric_encodings_are_valid_and_preserved(self):
        self.registration["schema_version"] = 1.0
        self.registration["corpus_count"] = 1e9
        for row in self.rows:
            row["corpus_count"] = "1.000000000e9"
        self.assert_rendered()

    def test_historical_comparison_discloses_date_cohort_and_placement_differences(self):
        self.registration["comparison"] = "historical"
        self.registration["engines"]["PipeANN"].update(
            source_date="2026-09-20", queries=2000, threads=2, cpu_nodes="0", memory_nodes="0",
            query_cohort_id="SYNTHETIC-different-historical-cohort",
        )
        self.registration["engines"]["SPTAG_H1"]["runtime_id"] = "SYNTHETIC-historical-H1-runtime"
        self.make_rows()
        output, metadata = self.assert_rendered()
        self.assertEqual(metadata["comparison"], "historical")
        text = self.pdf_text(output)
        for label in ("Historical comparison - not a fresh paired run", "source 2026-09-20",
                      "2000 queries", "2 query thread(s)", "CPU NUMA 0 / memory NUMA 0",
                      "this project's baseline with posting disabled",
                      "not an unmodified Microsoft SPTAG build"):
            self.assertIn(label, text)
        self.assertNotIn("same-index/runtime project baseline", text)

    def test_historical_sources_require_date_and_numa_labels(self):
        self.registration["comparison"] = "historical"
        original = copy.deepcopy(self.registration)
        for field in ("source_date", "cpu_nodes", "memory_nodes"):
            with self.subTest(field=field):
                self.registration = copy.deepcopy(original)
                del self.registration["engines"]["PipeANN"][field]
                self.assert_rejected("source_date" if field == "source_date" else "engine declaration")
        self.registration = copy.deepcopy(original)
        self.registration["engines"]["PipeANN"]["source_date"] = "2026-02-30"
        self.assert_rejected("source_date")

    def test_fresh_pairing_rejects_inconsistent_engine_cohorts_or_execution(self):
        original = copy.deepcopy(self.registration)
        for field, value in (("queries", 999), ("threads", 2), ("cpu_nodes", "0"),
                             ("memory_nodes", "0"), ("query_cohort_id", "SYNTHETIC-other-cohort")):
            with self.subTest(field=field):
                self.registration = copy.deepcopy(original)
                self.registration["engines"]["PipeANN"][field] = value
                self.assert_rejected(f"Fresh paired comparison requires matching {field}")
        for field in ("runtime_id", "index_id"):
            self.registration = copy.deepcopy(original)
            self.registration["engines"]["SPTAG_H1"][field] = "SYNTHETIC-unmatched-control"
            self.assert_rejected(f"Fresh SPTAG H1 control requires the same {field}")

    def test_registration_metadata_cannot_infer_or_fabricate_scenarios(self):
        original = copy.deepcopy(self.registration)
        for field, value in (("dataset", "SIFT1M"), ("corpus_count", 1000000),
                             ("scenarios", list(reversed(SCENARIOS))), ("comparison", "unspecified")):
            with self.subTest(field=field):
                self.registration = copy.deepcopy(original)
                self.registration[field] = value
                self.assert_rejected("Invalid selectivity registration")
        for field, value in (("title", ""), ("selectivity", [0.170124930]),
                             ("selectivity", 0.10), ("candidate_count", 170124931),
                             ("predicate", None)):
            with self.subTest(field=field, value=value):
                self.registration = copy.deepcopy(original)
                self.registration["scenario_metadata"]["broad_tag"][field] = value
                self.assert_rejected("scenario_metadata")
        self.registration = copy.deepcopy(original)
        del self.registration["scenario_metadata"]["mixed_dnf"]
        self.assert_rejected("scenario_metadata")

    def test_engine_grids_io_and_frozen_adaptive_policy_are_explicit(self):
        original = copy.deepcopy(self.registration)
        for field, value in (("native_control", "searchL"), ("L", [16]),
                             ("L", [24, 16]), ("L", [16, 16]), ("L", [16, 24.5]),
                             ("io_mode", "direct"), ("qps_aggregation", "best"),
                             ("query_cohort_id", None), ("controls", {})):
            with self.subTest(field=field, value=value):
                self.registration = copy.deepcopy(original)
                self.registration["engines"]["SPTAG_adaptive"][field] = value
                self.assert_rejected("engine declaration")
        self.registration = copy.deepcopy(original)
        self.registration["engines"]["SPTAG_adaptive"]["search_policy"] = "full_budget_failure"
        self.assert_rejected("frozen search policy")
        self.registration = copy.deepcopy(original)
        self.registration["engines"]["SPTAG_adaptive"]["controls"]["enable_posting_navigation"] = False
        self.assert_rejected("frozen search policy")
        self.registration = copy.deepcopy(original)
        self.registration["engines"]["PipeANN"]["controls"] = {}
        self.assert_rejected("engine declaration")
        self.registration = copy.deepcopy(original)
        del self.registration["engines"]["PipeANN"]
        self.assert_rejected("Register SPTAG_adaptive and PipeANN")

    def test_missing_native_points_or_entire_scenario_are_rejected(self):
        original = copy.deepcopy(self.rows)
        self.rows.pop()
        self.assert_rejected("Incomplete registered native-control grid")
        self.rows = [row for row in original if row["scenario"] != "mixed_dnf"]
        self.assert_rejected("Incomplete registered native-control grid")
        self.rows = copy.deepcopy(original)
        self.rows[0]["L"] = 192
        self.assert_rejected("Incomplete registered native-control grid")

    def test_duplicates_and_unregistered_curves_are_rejected(self):
        original = copy.deepcopy(self.rows)
        self.rows.append(dict(self.rows[0]))
        self.assert_rejected("Duplicate measured control points")
        self.rows[-1]["L"] = f"{self.rows[-1]['L']}.0"
        self.assert_rejected("Duplicate measured control points")
        for field, value in (("scenario", "numeric"), ("scenario", "extreme_tag"), ("engine", "SPANN")):
            self.rows = copy.deepcopy(original)
            self.rows[0][field] = value
            self.assert_rejected("Unregistered scenario or engine")

    def test_incomplete_repetitions_fail_even_when_all_grid_points_exist(self):
        self.rows[0]["repeats"] -= 1
        self.assert_rejected("Incomplete or inconsistent repetitions")

    def test_one_repetition_cannot_claim_a_multi_run_range(self):
        self.registration["engines"]["SPTAG_adaptive"]["repeats"] = 1
        for row in self.rows:
            if row["engine"] == "SPTAG_adaptive":
                row["repeats"] = 1
        self.assert_rejected("Single-repetition ranges must equal the measured point")

    def test_panel_predicate_count_and_selectivity_must_agree(self):
        original = copy.deepcopy(self.rows)
        for field, value in (("predicate", "SYNTHETIC different predicate"),
                             ("candidate_count", 23), ("selectivity", 0.5)):
            with self.subTest(field=field):
                self.rows = copy.deepcopy(original)
                self.rows[0][field] = value
                self.assert_rejected("Inconsistent panel predicate/count/selectivity")

    def test_record_provenance_cannot_disagree_with_registration(self):
        original = copy.deepcopy(self.rows)
        for field, value in (("queries", 999), ("threads", 2), ("cpu_nodes", "0"),
                             ("memory_nodes", "0"), ("query_cohort_id", "SYNTHETIC-wrong-cohort"),
                             ("runtime_id", "SYNTHETIC-wrong-runtime"), ("io_mode", "unknown"),
                             ("source_date", "2026-09-19"), ("native_control", "beam_width")):
            with self.subTest(field=field):
                self.rows = copy.deepcopy(original)
                self.rows[0][field] = value
                self.assert_rejected(f"Measurement disagrees with registered {field}")

    def test_invalid_ranges_or_nonfinite_coordinates_are_rejected(self):
        original = copy.deepcopy(self.rows)
        for field, value, message in (
            ("qps_min", 0, "Invalid performance ranges"),
            ("qps", -1, "Invalid performance ranges"),
            ("qps_max", 1, "Invalid performance ranges"),
            ("recall", 1.5, "Invalid performance ranges"),
            ("recall_min", -0.01, "Invalid performance ranges"),
            ("recall_max", 1.01, "Invalid performance ranges"),
            ("qps", "Inf", "Invalid numeric measurement"),
            ("qps", "not a number", "Invalid numeric measurement"),
            ("threads", 1.5, "Invalid integer measurement"),
            ("queries", 2147483648, "Invalid integer measurement"),
        ):
            with self.subTest(field=field, value=value):
                self.rows = copy.deepcopy(original)
                self.rows[0][field] = value
                self.assert_rejected(message)
        self.rows = [{key: value for key, value in row.items() if key != "recall_min"} for row in original]
        self.assert_rejected("summary.csv schema")

    def test_diagnostics_and_warmups_are_not_curve_points(self):
        original = copy.deepcopy(self.rows)
        for field, value in (("diagnostic", True), ("diagnostic", ""),
                             ("stage", "warmup"), ("stage", "diagnostic")):
            self.rows = copy.deepcopy(original)
            self.rows[0][field] = value
            self.assert_rejected("non-ordinary measurement")

    def test_existing_outputs_are_untouched(self):
        output = self.root / "existing-output"
        output.mkdir()
        sentinel = output / "recall_qps.png"
        sentinel.write_bytes(b"SYNTHETIC protected output")
        result, _ = self.render(output=output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to overwrite", result.stderr)
        self.assertEqual(sentinel.read_bytes(), b"SYNTHETIC protected output")
        self.assertEqual(list(output.iterdir()), [sentinel])

    def test_mutated_input_cannot_publish_partial_or_mislabelled_outputs(self):
        expression = (
            'ggsave <- function(...) {'
            'cat("\\n", file = file.path(commandArgs(trailingOnly = TRUE)[[1]], "summary.csv"), '
            'append = TRUE); ggplot2::ggsave(...) }; '
            f"source({json.dumps(str(SCRIPT))})"
        )
        result, output = self.render(expression=expression)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Frozen plotting inputs changed", result.stderr)
        self.assertFalse(output.exists())

    def test_no_implicit_partial_mode_or_ambiguous_flags(self):
        for flags in (("--selectivity", "--partial"), ("--selectivity", "--selectivity"),
                      ("--unknown",)):
            self.assert_rejected("usage:", flags)


if __name__ == "__main__":
    unittest.main()
