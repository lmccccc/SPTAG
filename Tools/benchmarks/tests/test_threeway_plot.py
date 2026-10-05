"""Tiny, disposable project-local fixtures; no native runs or production data."""

import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "plot_sift1b_threeway.R"
RSCRIPT = os.environ.get("RSCRIPT", "/home/baotonglu/.local/bin/Rscript")
SCENARIOS = ["unfilter", "broad_tag", "medium_tag", "sel_01pct", "mixed_dnf"]
ENGINES = ["SPTAG_adaptive", "PipeANN", "Filtered_DiskANN"]
RECALL_TARGETS = [0.90, 0.95]
THREAD_GRID = [1, 2, 4, 8, 16, 24, 32, 40, 48, 64, 96, 128, 192]
SINGLE_FIELDS = [
    "scenario", "engine", "L", "queries", "repeats", "threads", "cpu_nodes",
    "recall", "recall_min", "recall_max", "qps", "qps_min", "qps_max",
    "candidate_count", "selectivity", "predicate",
]
THROUGHPUT_FIELDS = [
    "scenario", "engine", "threads", "L", "repeats", "recall_target",
    "recall", "recall_min", "recall_max", "qps", "qps_min", "qps_max",
    "seconds_min", "queries_min", "mean_latency_us", "p50_latency_us",
    "p99_latency_us", "mean_ios", "cpu_nodes", "memory_nodes",
    "measured_queries_total", "returned_neighbors_total", "missing_neighbors_total",
    "underfilled_queries_total", "returned_per_query", "underfilled_fraction", "native_result_schemas",
]
AVAILABILITY_FIELDS = ["phase", "scenario", "engine", "status", "reason"]


class ThreewayPlot(unittest.TestCase):
    def setUp(self):
        self.root = Path(f".synthetic-threeway-{uuid.uuid4().hex}")
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.environment = {
            **os.environ,
            **{key: str(self.root.resolve()) for key in ("TMPDIR", "TMP", "TEMP")},
        }
        self.calls = 0
        counts = [1000000000, 170124930, 17012493, 1000735, 425710]
        titles = ["Unfiltered", "Broad categorical", "Medium categorical",
                  "Categorical near 0.1%", "Mixed DNF"]
        self.registration = {
            "schema_version": 1, "dataset": "SIFT1B", "corpus_count": 1000000000,
            "query_count": 1000, "metric": "squared L2", "recall_target": 0.95,
            "recall_targets": list(RECALL_TARGETS),
            "throughput_min_seconds": 30, "throughput_core_budget": 96,
            "throughput_thread_grid": list(THREAD_GRID),
            "scenarios": list(SCENARIOS), "engines": list(ENGINES),
            "caption_note": "SYNTHETIC FIXTURE - NOT BENCHMARK MEASUREMENTS",
            "aio_limit_note": "SYNTHETIC retained AIO settings; higher concurrency remains blocked",
            "scenario_metadata": {
                scenario: {
                    "title": title, "predicate": f"SYNTHETIC predicate: {scenario}",
                    "candidate_count": count, "selectivity": count / 1000000000,
                }
                for scenario, count, title in zip(SCENARIOS, counts, titles)
            },
        }
        self.single = []
        self.throughput = []
        self.availability = []
        self.throughput_availability = []
        for scene_index, scenario in enumerate(SCENARIOS):
            entry = self.registration["scenario_metadata"][scenario]
            for engine_index, engine in enumerate(ENGINES):
                unsupported = engine == "Filtered_DiskANN" and scenario == "mixed_dnf"
                medium_pipeann = engine == "PipeANN" and scenario == "medium_tag"
                for phase, rows in (("single", self.availability),
                                    ("throughput", self.throughput_availability)):
                    rows.append({
                        "phase": phase, "scenario": scenario, "engine": engine,
                        "status": "unsupported_predicate" if unsupported else "available",
                        "reason": "Mixed DNF / numeric predicates are not native" if unsupported else "",
                    })
                if medium_pipeann:
                    self.throughput_availability[-1]["recall_target"] = 0.90
                    self.throughput_availability.append({
                        "phase": "throughput", "scenario": scenario, "engine": engine,
                        "recall_target": 0.95, "status": "recall_target_unmet",
                        "reason": "SYNTHETIC grid ceiling 0.9016; R90 remains valid",
                    })
                if unsupported:
                    continue
                grid = ([16, 24, 48], [10, 20, 40], [20, 40, 80])[engine_index]
                for point_index, control in enumerate(grid):
                    recall = [0.75, 0.72, 0.98][point_index]
                    if medium_pipeann and point_index == 2:
                        recall = 0.9016
                    qps = 3200 / (1 + point_index + engine_index * 0.3 + scene_index * 0.1)
                    self.single.append({
                        "scenario": scenario, "engine": engine, "L": control,
                        "queries": 1000, "repeats": 2, "threads": 1, "cpu_nodes": "3",
                        "recall": recall,
                        "recall_min": 0.901 if medium_pipeann and point_index == 2 else recall - 0.01,
                        "recall_max": 0.902 if medium_pipeann and point_index == 2 else recall + 0.01,
                        "qps": qps, "qps_min": qps * 0.9, "qps_max": qps * 1.1,
                        "candidate_count": entry["candidate_count"], "selectivity": entry["selectivity"],
                        "predicate": entry["predicate"],
                    })
                threads = [1, 24, 48] if engine_index == 2 else [1, 32, 96, 192]
                rates = ([1000, 12000, 10000, 8000], [900, 10000, 9000, 7000],
                         [800, 6000, 9000])[engine_index]
                for thread, rate in zip(threads, rates):
                    qps = rate / (1 + scene_index * 0.1)
                    for target in RECALL_TARGETS:
                        if medium_pipeann and target == 0.95:
                            continue
                        self.throughput.append({
                            "scenario": scenario, "engine": engine, "threads": thread,
                            "L": [192, 160, 100][engine_index], "repeats": 3, "recall_target": target,
                            "recall": 0.9016 if medium_pipeann else 0.97,
                            "recall_min": 0.901 if medium_pipeann else 0.96,
                            "recall_max": 0.902 if medium_pipeann else 0.98,
                            "qps": qps, "qps_min": qps * 0.9, "qps_max": qps * 1.1,
                            "seconds_min": 30, "queries_min": 30000,
                            "mean_latency_us": "" if engine_index == 0 else 350,
                            "p50_latency_us": "" if engine_index == 0 else 100,
                            "p99_latency_us": "" if engine_index == 0 else 2000,
                            "mean_ios": "" if engine_index == 0 else 8,
                            "cpu_nodes": "3", "memory_nodes": "3",
                            "measured_queries_total": 90000, "returned_neighbors_total": 900000,
                            "missing_neighbors_total": 0, "underfilled_queries_total": 0,
                            "returned_per_query": 10, "underfilled_fraction": 0, "native_result_schemas": "1",
                        })
                if engine_index == 2:
                    for blocked_threads in (64, 96, 128, 192):
                        self.throughput_availability.append({
                            "phase": "throughput", "scenario": scenario, "engine": engine,
                            "status": "aio_limit", "threads": blocked_threads,
                            "reason": f"{blocked_threads} query threads blocked by unchanged OS/AIO limit 65536",
                        })
        random.Random(19).shuffle(self.single)
        random.Random(23).shuffle(self.throughput)

    @staticmethod
    def csv_rows(path):
        with path.open(newline="") as stream:
            return list(csv.DictReader(stream))

    def write_csv(self, name, rows, fields):
        fields = list(dict.fromkeys(fields + [key for row in rows for key in row]))
        with (self.root / name).open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def render(self, stage="single", output=None, flags=None, expression=None, input_directory=None, script=SCRIPT):
        self.calls += 1
        (self.root / "registration.json").write_text(json.dumps(self.registration) + "\n")
        self.write_csv("single_summary.csv", self.single, SINGLE_FIELDS)
        availability = self.availability + (self.throughput_availability if stage == "complete" else [])
        self.write_csv("availability.csv", availability, AVAILABILITY_FIELDS)
        inputs = ["registration.json", "single_summary.csv", "availability.csv"]
        if stage == "complete":
            self.write_csv("throughput_summary.csv", self.throughput, THROUGHPUT_FIELDS)
            inputs.append("throughput_summary.csv")
        self.input_bytes = {name: (self.root / name).read_bytes() for name in inputs}
        campaign = self.root
        if input_directory is not None:
            input_directory.mkdir(parents=True)
            for name in inputs:
                shutil.copyfile(self.root / name, input_directory / name)
            campaign = input_directory
        output = output or self.root / f"render-{self.calls}"
        command = [RSCRIPT, "-e", expression] if expression else [RSCRIPT, str(script)]
        result = subprocess.run(
            command + [str(campaign), str(output), *(flags if flags is not None else ["--stage", stage])],
            env=self.environment, capture_output=True, text=True, check=False, timeout=90,
        )
        return result, output

    def assert_rejected(self, message, stage="single", **kwargs):
        result, output = self.render(stage, **kwargs)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)
        self.assertFalse(output.exists(), result.stdout + result.stderr)

    def assert_rendered(self, stage="single", **kwargs):
        result, output = self.render(stage, **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Warning", result.stderr)
        stems = ["single_thread_recall_qps"]
        if stage == "complete":
            stems += ["throughput_scaling", "throughput_peak"]
        figures = {f"{stem}.{extension}" for stem in stems for extension in ("png", "pdf")}
        expected = figures | {"plot_metadata.json"}
        if stage == "complete":
            expected.add("peak_points.csv")
        self.assertEqual({path.name for path in output.iterdir()}, expected)
        for name in figures:
            signature = b"\x89PNG" if name.endswith(".png") else b"%PDF"
            self.assertTrue((output / name).read_bytes().startswith(signature), name)
        metadata = json.loads((output / "plot_metadata.json").read_text())
        self.assertEqual(metadata["schema_version"], 1)
        self.assertEqual(metadata["status"], "completed")
        self.assertEqual(metadata["stage"], stage)
        self.assertEqual(metadata["registration"], self.registration)
        self.assertEqual(metadata["scenarios"], SCENARIOS)
        self.assertEqual(metadata["engine_order"], ENGINES)
        self.assertEqual(list(metadata["engine_colors"].values()), ["#009E73", "#D55E00", "#0072B2"])
        self.assertEqual(metadata["single_points"], len(self.single))
        self.assertEqual(metadata["throughput_points"], len(self.throughput) if stage == "complete" else None)
        self.assertEqual(metadata["recall_target_views"]["primary"], 0.95)
        self.assertEqual(metadata["recall_target_views"]["targets"], RECALL_TARGETS)
        self.assertFalse(metadata["recall_target_views"]["independent_repeats"])
        self.assertEqual(metadata["throughput_facet_count"], 10 if stage == "complete" else None)
        if stage == "complete":
            fields = [field for field in THROUGHPUT_FIELDS if field != "recall_target"]
            distinct = {tuple(str(row[field]) for field in fields) for row in self.throughput}
            self.assertEqual(metadata["throughput_unique_measurement_records"], len(distinct))
        self.assertTrue(metadata["all_measured_points_retained"])
        for key in ("interpolated", "smoothed", "extrapolated", "matched_io_comparison",
                    "global_page_cache_drops", "saturation_proven", "system_settings_changed"):
            self.assertFalse(metadata[key], key)
        self.assertFalse(metadata["historical_reuse"]["fresh_paired_rerun"])
        self.assertFalse(metadata["historical_reuse"]["measurements_modified"])
        self.assertEqual(metadata["hash_algorithm"], "md5")
        self.assertEqual(set(metadata["input_files"]), set(metadata["input_hashes"]))
        self.assertEqual(len(metadata["input_hashes"]), len(self.input_bytes))
        for key, path in metadata["input_files"].items():
            self.assertEqual(Path(path).read_bytes(), self.input_bytes[Path(path).name])
            self.assertEqual(metadata["input_hashes"][key], hashlib.md5(Path(path).read_bytes()).hexdigest())
        self.assertEqual(set(metadata["output_hashes"]), expected - {"plot_metadata.json"})
        for name, digest in metadata["output_hashes"].items():
            self.assertEqual(hashlib.md5((output / name).read_bytes()).hexdigest(), digest)
        return output, metadata

    def pdf_text(self, output, stem):
        if not shutil.which("pdftotext"):
            self.skipTest("PDF text assertions require pdftotext")
        text = subprocess.check_output(
            ["pdftotext", "-layout", str(output / f"{stem}.pdf"), "-"],
            text=True, env=self.environment,
        )
        return " ".join(text.split())

    @staticmethod
    def source_expression(body):
        return body + f"\nsource({json.dumps(str(SCRIPT))})"

    def test_single_only_preserves_native_l_geometry_and_historical_measurements(self):
        self.single[0].update(
            qps="1234.567890123456789", qps_min="1000.00000000000000001",
            qps_max="1400.00000000000000001",
        )
        expression = self.source_expression("""
            ggsave <- function(filename, plot, ...) {
              b <- ggplot2::ggplot_build(plot)
              expected <- read.csv(file.path(commandArgs(trailingOnly = TRUE)[[1]], "single_summary.csv"))
              scenarios <- c("unfilter", "broad_tag", "medium_tag", "sel_01pct", "mixed_dnf")
              engines <- c("SPTAG_adaptive", "PipeANN", "Filtered_DiskANN")
              expected <- expected[order(match(expected$scenario, scenarios),
                                         match(expected$engine, engines), expected$L), ]
              stopifnot(nrow(b$layout$layout) == 5L, nrow(b$data[[4]]) == nrow(expected),
                        isTRUE(all.equal(plot$data$recall, expected$recall)),
                        isTRUE(all.equal(plot$data$qps, expected$qps)),
                        isTRUE(all.equal(plot$data$L, as.numeric(expected$L))))
              for (p in seq_along(scenarios)) for (g in seq_along(engines)) {
                wanted <- expected[expected$scenario == scenarios[[p]] &
                                   expected$engine == engines[[g]], ]
                actual <- b$data[[3]][b$data[[3]]$PANEL == p & b$data[[3]]$group == g, ]
                stopifnot(isTRUE(all.equal(actual$x, wanted$recall)),
                          isTRUE(all.equal(actual$y, log10(wanted$qps))))
              }
              stopifnot(!any(plot$data$scenario == "mixed_dnf" & plot$data$engine == "Filtered_DiskANN"))
              ggplot2::ggsave(filename, plot = plot, ...)
            }
        """)
        output, metadata = self.assert_rendered(expression=expression)
        self.assertEqual(metadata["single_points"], 42)
        self.assertEqual({row["phase"] for row in metadata["availability"]}, {"single"})
        self.assertIn("unsupported mixed DNF", metadata["facet_annotations"]["single"]["mixed_dnf"])
        text = self.pdf_text(output, "single_thread_recall_qps")
        for label in ("SPTAG adaptive", "PipeANN", "Filtered-DiskANN", "Sep24",
                      "not a fresh paired rerun", "SYNTHETIC FIXTURE", "Actual selectivity:",
                      "17.012493%", "0.1000735%", "0.042571%", "unavailable:",
                      "unsupported mixed DNF / numeric", "nprobe", "searchL", "not equal work",
                      "R64, L1, FilteredL100, PQ32", "no disk vector quantization",
                      "SPANN buffered", "Filtered-DiskANN direct", "NOT a matched-I/O",
                      "Recall 0.9016", "R95 unavailable", "R90 remains a valid comparison",
                      "fixed 1000-query hot working set", "no global page-cache drops",
                      "1024 AIO slots per worker", "recorded live headroom",
                      "resource-limited maximum observed throughput", "saturated theoretical maximum"):
            self.assertIn(label, text)
        self.assertFalse((self.root / "throughput_summary.csv").exists())
        self.assertNotIn("13440", text)
        self.assertNotIn("48-thread ceiling", text)

    def test_complete_outputs_select_observed_median_peaks_and_retain_raw_fields(self):
        selected = next(row for row in self.throughput if row["scenario"] == "unfilter" and
                        row["engine"] == "Filtered_DiskANN" and row["threads"] == 48)
        selected["qps"] = "9000.123456789012345678"
        for row in self.throughput:
            if row["engine"] == "SPTAG_adaptive" and row["threads"] == 96:
                row["qps_max"] = 999999
        expression = self.source_expression("""
            ggsave <- function(filename, plot, ...) {
              b <- ggplot2::ggplot_build(plot)
              if (!grepl("single_thread", basename(filename), fixed = TRUE)) {
                stopifnot(nrow(b$layout$layout) == 10L,
                          all(c("scenario", "recall_target") %in% names(b$layout$layout)),
                          setequal(b$layout$layout$recall_target, c(0.90, 0.95)),
                          !any(plot$data$scenario == "medium_tag" & plot$data$engine == "PipeANN" &
                               plot$data$recall_target == 0.95))
                layer <- if (grepl("scaling", basename(filename), fixed = TRUE)) 4L else 2L
                stopifnot(nrow(b$data[[layer]]) == nrow(plot$data))
                for (p in seq_len(nrow(b$layout$layout))) {
                  panel <- b$layout$layout[p, ]
                  expected <- plot$data[plot$data$scenario == panel$scenario &
                                       plot$data$recall_target == panel$recall_target, ]
                  actual <- b$data[[layer]][b$data[[layer]]$PANEL == panel$PANEL, ]
                  stopifnot(isTRUE(all.equal(actual$y, log10(expected$qps))))
                }
              }
              ggplot2::ggsave(filename, plot = plot, ...)
            }
        """)
        output, metadata = self.assert_rendered("complete", expression=expression)
        peaks = self.csv_rows(output / "peak_points.csv")
        self.assertEqual(len(peaks), 27)
        self.assertEqual(metadata["peak_points"], 27)
        source = self.csv_rows(self.root / "throughput_summary.csv")
        for peak in peaks:
            original = next(row for row in source if all(
                row[key] == peak[key] for key in ("scenario", "engine", "recall_target", "threads", "L")
            ))
            self.assertEqual({key: peak[key] for key in THROUGHPUT_FIELDS}, original)
            group = [row for row in source if row["scenario"] == peak["scenario"] and
                     row["engine"] == peak["engine"] and row["recall_target"] == peak["recall_target"]]
            self.assertEqual(float(peak["qps"]), max(float(row["qps"]) for row in group))
            diskann = peak["engine"] == "Filtered_DiskANN"
            self.assertEqual(peak["threads"], "48" if diskann else "32")
            self.assertEqual(peak["largest_tested_threads"], "48" if diskann else "192")
            self.assertEqual(peak["boundary_censored"], "TRUE" if diskann else "FALSE")
            self.assertEqual(peak["resource_limited"], "TRUE" if diskann else "FALSE")
            self.assertEqual(peak["saturation_proven"], "FALSE")
        for target in RECALL_TARGETS:
            mixed = [row for row in peaks if row["scenario"] == "mixed_dnf" and
                     float(row["recall_target"]) == target]
            self.assertEqual([row["engine"] for row in mixed], ENGINES[:2])
        medium_pipeann = [row for row in peaks if row["scenario"] == "medium_tag" and row["engine"] == "PipeANN"]
        self.assertEqual([float(row["recall_target"]) for row in medium_pipeann], [0.90])
        self.assertIn("R95 unavailable", metadata["facet_annotations"]["throughput"]["0.95"]["medium_tag"])
        self.assertNotIn("PipeANN", metadata["facet_annotations"]["throughput"]["0.9"]["medium_tag"])
        self.assertTrue(any(row["status"] == "aio_limit" for row in metadata["availability"]))
        scaling = self.pdf_text(output, "throughput_scaling")
        peak_text = self.pdf_text(output, "throughput_peak")
        for text in (scaling, peak_text):
            for phrase in ("recorded live headroom", "existing", "unconstrained algorithm maximum",
                           "0.95", "30", "96", "SYNTHETIC retained AIO settings", "Recall >= 90%",
                           "Recall >= 95% (primary)", "NOT independent repeats", "never pooled",
                           "R95 unavailable", "R90 remains a valid comparison"):
                self.assertIn(phrase.lower(), text.lower())
        self.assertIn("boundary-censored", peak_text)
        self.assertIn("t=48; L=100", peak_text)
        self.assertIn("Lines keep native L fixed", scaling)

    def test_corrected_pipeann_is_labeled_only_on_throughput_figures(self):
        self.registration["native_pipeann_correctness"] = {
            "mode": "retain-unexpanded-pages-before-ring-reuse-v1",
            "proof": "/synthetic/non-production-proof.json",
        }
        self.registration["caption_note"] += (
            " SYNTHETIC timed page retention correction; historical single-thread binary unchanged.")
        output, metadata = self.assert_rendered("complete")
        self.assertEqual("PipeANN", metadata["engine_labels"]["PipeANN"])
        self.assertEqual("PipeANN (page-lifetime fix)", metadata["throughput_engine_labels"]["PipeANN"])
        label = "PipeANN (page-lifetime fix)"
        self.assertNotIn(label, self.pdf_text(output, "single_thread_recall_qps"))
        for name in ("throughput_scaling", "throughput_peak"):
            self.assertIn(label, self.pdf_text(output, name))

    def test_unknown_pipeann_correctness_variant_is_rejected(self):
        self.registration["native_pipeann_correctness"] = {"mode": "unapproved-search-retuning"}
        self.assert_rejected("Invalid PipeANN native correctness declaration")

    def test_native_short_result_counts_are_disclosed_without_changing_measured_recall(self):
        self.registration["native_pipeann_result_policy"] = {
            "mode": "native-count-prefix-v1", "authorization": "allow-native-short-results", "schema_version": 2,
        }
        for row in self.throughput:
            if row["engine"] == "PipeANN":
                row.update(returned_neighbors_total=899000, missing_neighbors_total=1000,
                           underfilled_queries_total=500, returned_per_query=899000 / 90000,
                           underfilled_fraction=500 / 90000, native_result_schemas="1,2")
        output, _ = self.assert_rendered("complete")
        text = self.pdf_text(output, "throughput_scaling")
        self.assertIn("denominator stays queries times 10", text)
        self.assertIn("No padding, output repair or output-dependent budget widening", text)
        for row in self.csv_rows(output / "peak_points.csv"):
            if row["engine"] == "PipeANN":
                self.assertEqual(row["missing_neighbors_total"], "1000")
                self.assertEqual(row["native_result_schemas"], "1,2")

    def test_inconsistent_native_short_result_counts_are_rejected(self):
        self.registration["native_pipeann_result_policy"] = {
            "mode": "native-count-prefix-v1", "authorization": "allow-native-short-results", "schema_version": 2,
        }
        self.throughput[0]["missing_neighbors_total"] = 1
        self.assert_rejected("Invalid native returned/missing-neighbor accounting", stage="complete")

    def add_unfiltered_supplement(self):
        self.registration["native_pipeann_correctness"] = {
            "mode": "retain-unexpanded-pages-before-ring-reuse-v1"}
        self.registration["native_pipeann_result_policy"] = {
            "mode": "native-count-prefix-v1", "authorization": "allow-native-short-results", "schema_version": 2}
        self.registration["unfiltered_supplement"] = {
            "mode": "fixed-grid-unfilter-only-v1", "previous_directory": "/synthetic/completed",
            "search_grids": {"PipeANN": [40, 80], "Filtered_DiskANN": [800, 1600, 3200]}}
        for row in self.single:
            row.update(measurement_series="baseline",
                       measurement_reused="false" if row["engine"] == "Filtered_DiskANN" else "true")
        original = next(row for row in self.single
                        if row["engine"] == "PipeANN" and row["scenario"] == "unfilter" and row["L"] == 40)
        self.single.extend(dict(original, L=control, measurement_series="pipeann_current",
                                measurement_reused="false") for control in (40, 80))
        for row in self.throughput:
            row["source_campaign"] = "/synthetic/completed"

    def test_supplement_retains_overlap_but_never_connects_historical_and_current_clients(self):
        self.add_unfiltered_supplement()
        expression = self.source_expression("""
            ggsave <- function(filename, plot, ...) {
              if (grepl("single_thread", filename)) {
                paths <- plot$layers[[3]]$data
                keys <- paste(paths$scenario, paths$series_key)
                stopifnot(all(vapply(split(paths$measurement_series, keys),
                                     function(x) length(unique(x)) == 1L, logical(1))))
                current <- paths[paths$series_key == "PipeANN_current", ]
                stopifnot(nrow(current) == 2L, all(current$scenario == "unfilter"),
                          identical(current$L, c(40, 80)))
              }
              ggplot2::ggsave(filename, plot = plot, ...)
            }
        """)
        output, metadata = self.assert_rendered("complete", expression=expression)
        self.assertIn("PipeANN_current", metadata["single_series_labels"])
        text = self.pdf_text(output, "single_thread_recall_qps")
        self.assertIn("PipeANN (historical)", text)
        self.assertIn("PipeANN (current corrected client)", text)
        self.assertIn("never joined or pooled", text)
        for row in self.csv_rows(output / "peak_points.csv"):
            self.assertEqual("/synthetic/completed", row["source_campaign"])

    def test_supplement_rejects_undeclared_or_mis_scoped_current_series(self):
        self.add_unfiltered_supplement()
        self.single[-1]["scenario"] = "broad_tag"
        self.assert_rejected("Invalid single-thread measurement_series")
        self.single[-1]["scenario"] = "unfilter"
        self.single[-1]["L"] = 120
        self.assert_rejected("Current PipeANN points differ")
        self.single[-1]["L"] = 80
        del self.registration["unfiltered_supplement"]
        self.assert_rejected("Invalid single-thread measurement_series")

    def test_supplement_cannot_merge_duplicate_current_points_or_call_them_reused(self):
        self.add_unfiltered_supplement()
        self.single.append(dict(self.single[-1]))
        self.assert_rejected("Duplicate measured points")
        self.single.pop()
        self.single[-1]["measurement_reused"] = "true"
        self.assert_rejected("Invalid measurement_reused")

    def add_spann_refresh(self):
        self.add_unfiltered_supplement()
        self.registration["spann_single_refresh"] = {
            "mode": "optimized-main-single-v1", "previous_directory": "/synthetic/completed",
            "native_binary": "/synthetic/optimized-nativeBench", "native_binary_sha256": "a" * 64,
            "nprobe": [16, 24, 48]}
        for row in self.single:
            if row["engine"] == "SPTAG_adaptive":
                row.update(measurement_series="spann_optimized", measurement_reused="false")
            else:
                row["measurement_reused"] = "true"

    def test_spann_refresh_labels_fresh_core_and_retains_peer_series(self):
        self.add_spann_refresh()
        output, metadata = self.assert_rendered()
        self.assertEqual(metadata["single_series_labels"]["SPTAG_adaptive"], "SPTAG (optimized)")
        self.assertEqual(metadata["historical_reuse"]["engines"], ["PipeANN", "Filtered_DiskANN"])
        self.assertEqual(metadata["spann_single_refresh"], self.registration["spann_single_refresh"])
        text = self.pdf_text(output, "single_thread_recall_qps")
        self.assertIn("SPTAG (optimized)", text)
        self.assertIn("SPANN remeasured; DiskANN/PipeANN retained", text)
        self.assertIn("not rescaled", text)
        self.assertIn("never joined or pooled", text)
        self.assertNotIn("All original single-thread points are retained unchanged", text)

    def test_spann_refresh_rejects_missing_points_bad_provenance_and_stale_throughput(self):
        self.add_spann_refresh()
        refresh = self.registration["spann_single_refresh"]
        refresh["native_binary_sha256"] = "unverified"
        self.assert_rejected("Invalid SPANN single-thread refresh")
        refresh["native_binary_sha256"] = "a" * 64
        peer = next(row for row in self.single if row["engine"] == "PipeANN")
        peer["measurement_reused"] = "false"
        self.assert_rejected("Invalid measurement_reused")
        peer["measurement_reused"] = "true"
        spann = next(row for row in self.single if row["engine"] == "SPTAG_adaptive")
        spann["measurement_reused"] = "true"
        self.assert_rejected("Invalid measurement_reused")
        spann["measurement_reused"] = "false"
        control = spann["L"]
        spann["L"] = 64
        self.assert_rejected("SPANN refresh requires the complete declared native grid")
        spann["L"] = control
        spann["measurement_series"] = "baseline"
        self.assert_rejected("SPANN refresh requires the complete declared native grid")
        spann["measurement_series"] = "spann_optimized"
        self.assert_rejected("Invalid SPANN single-thread refresh", stage="complete")

    def test_optional_provenance_and_ignored_future_phase_file(self):
        for row in self.single:
            row.update(
                measurement_reused=row["engine"] != "Filtered_DiskANN",
                io_mode="buffered" if row["engine"] == "SPTAG_adaptive" else "direct",
                source="SYNTHETIC source, preserved",
            )
        future = self.root / "throughput_summary.csv"
        future.write_text("intentionally not a throughput table\n")
        _, metadata = self.assert_rendered()
        self.assertNotIn("throughput_summary", metadata["input_hashes"])
        self.assertEqual(future.read_text(), "intentionally not a throughput table\n")

    def test_missing_diskann_curves_remain_explicit_without_losing_historical_series(self):
        self.single = [row for row in self.single if row["engine"] != "Filtered_DiskANN"]
        for row in self.availability:
            if row["engine"] == "Filtered_DiskANN" and row["scenario"] != "mixed_dnf":
                row.update(status="blocked", reason="SYNTHETIC native client unavailable")
        _, metadata = self.assert_rendered()
        self.assertEqual(metadata["single_points"], 30)
        self.assertIn("Filtered-DiskANN unavailable", metadata["facet_annotations"]["single"]["unfilter"])

    def test_complete_all_unavailable_throughput_has_no_invented_peak_rows(self):
        self.throughput = []
        self.throughput_availability = [
            row for row in self.throughput_availability if row["status"] != "aio_limit"
        ]
        for row in self.throughput_availability:
            if row["status"] == "available":
                row.update(status="recall_target_unmet", reason="SYNTHETIC no qualifying measurement")
        output, metadata = self.assert_rendered("complete")
        self.assertEqual(metadata["throughput_points"], 0)
        self.assertEqual(metadata["peak_points"], 0)
        self.assertEqual(self.csv_rows(output / "peak_points.csv"), [])

    def test_tied_boundary_peak_chooses_lower_threads_but_stays_censored(self):
        row = next(row for row in self.throughput if row["scenario"] == "unfilter" and
                   row["engine"] == "Filtered_DiskANN" and row["threads"] == 24 and row["recall_target"] == 0.90)
        row.update(qps=9000, qps_min=8100, qps_max=9900)
        duplicate_thread = dict(row, L=120)
        self.throughput.append(duplicate_thread)
        output, _ = self.assert_rendered("complete")
        peak = next(row for row in self.csv_rows(output / "peak_points.csv")
                    if row["scenario"] == "unfilter" and row["engine"] == "Filtered_DiskANN" and
                    float(row["recall_target"]) == 0.90)
        self.assertEqual((peak["threads"], peak["L"]), ("24", "100"))
        self.assertEqual(peak["peak_at_largest_tested_threads"], "FALSE")
        self.assertEqual(peak["maximum_at_largest_tested_threads"], "TRUE")
        self.assertEqual(peak["boundary_censored"], "TRUE")

    def test_shared_target_views_allow_legacy_availability_without_pooling_repeats(self):
        self.throughput_availability = [
            {key: value for key, value in row.items() if key != "recall_target"}
            for row in self.throughput_availability if row["status"] not in ("recall_target_unmet", "aio_limit")
        ]
        output, metadata = self.assert_rendered("complete")
        self.assertEqual(metadata["throughput_points"], 100)
        self.assertEqual(metadata["throughput_unique_measurement_records"], 52)
        self.assertFalse(metadata["recall_target_views"]["independent_repeats"])
        peaks = self.csv_rows(output / "peak_points.csv")
        shared = [row for row in peaks if row["scenario"] == "unfilter" and row["engine"] == "SPTAG_adaptive"]
        self.assertEqual([float(row["recall_target"]) for row in shared], RECALL_TARGETS)
        for field in THROUGHPUT_FIELDS:
            if field != "recall_target":
                self.assertEqual(shared[0][field], shared[1][field], field)
        self.assertTrue(all(row["repeats"] == "3" for row in peaks))
        self.assertTrue(all("recall_target" not in row for row in metadata["availability"]))
        self.assertIn("R95 unavailable", metadata["facet_annotations"]["throughput"]["0.95"]["medium_tag"])

    def test_sparse_targets_and_scoped_concurrency_do_not_require_a_full_engine_matrix(self):
        self.throughput = [
            row for row in self.throughput
            if (row["scenario"] == "unfilter" and row["engine"] == "SPTAG_adaptive" and
                (row["recall_target"] == 0.90 or row["threads"] in (1, 32))) or
               (row["scenario"] == "medium_tag" and row["engine"] == "PipeANN")
        ]
        for row in self.throughput:
            if row["scenario"] == "medium_tag":
                row["recall_min"] = 0.90
        self.throughput_availability = [
            {
                "phase": "throughput", "scenario": "unfilter", "engine": "SPTAG_adaptive",
                "status": "available", "reason": "", "recall_target": 0.90, "threads": "",
            },
            *[
                {
                    "phase": "throughput", "scenario": "unfilter", "engine": "SPTAG_adaptive",
                    "status": "available", "reason": "", "recall_target": 0.95, "threads": thread,
                }
                for thread in (1, 32)
            ],
            {
                "phase": "throughput", "scenario": "unfilter", "engine": "SPTAG_adaptive",
                "status": "blocked", "reason": "SYNTHETIC higher-target concurrency unavailable",
                "recall_target": 0.95, "threads": 96,
            },
            {
                "phase": "throughput", "scenario": "medium_tag", "engine": "PipeANN",
                "status": "available", "reason": "", "recall_target": 0.90, "threads": "",
            },
        ]
        output, metadata = self.assert_rendered("complete")
        peaks = self.csv_rows(output / "peak_points.csv")
        self.assertEqual(len(peaks), 3)
        spann = {float(row["recall_target"]): row for row in peaks if row["engine"] == "SPTAG_adaptive"}
        self.assertEqual(spann[0.90]["largest_tested_threads"], "192")
        self.assertEqual(spann[0.95]["largest_tested_threads"], "32")
        self.assertEqual(spann[0.90]["boundary_censored"], "FALSE")
        self.assertEqual(spann[0.95]["boundary_censored"], "TRUE")
        self.assertEqual(spann[0.90]["resource_limited"], "FALSE")
        self.assertEqual(spann[0.95]["resource_limited"], "TRUE")
        notes = metadata["facet_annotations"]["throughput"]
        self.assertNotIn("SPTAG adaptive: concurrency", notes["0.9"]["unfilter"])
        self.assertIn("concurrency", notes["0.95"]["unfilter"])
        self.assertIn("no qualifying", notes["0.95"]["broad_tag"])
        self.assertIn("R95 unavailable", notes["0.95"]["medium_tag"])

    def test_availability_scope_rejects_invalid_targets_threads_and_unmatched_claims(self):
        original = copy.deepcopy(self.throughput_availability)
        for field, value, message in (
            ("recall_target", 0.92, "availability.csv recall_target"),
            ("recall_target", "NaN", "Invalid numeric measurement"),
            ("threads", 0, "availability.csv threads"),
            ("threads", 1.5, "availability.csv threads"),
            ("threads", 3, "availability.csv threads"),
            ("threads", "Inf", "Invalid numeric measurement"),
        ):
            with self.subTest(field=field, value=value):
                self.throughput_availability = copy.deepcopy(original)
                self.throughput_availability[0][field] = value
                self.assert_rejected(message, "complete")
        self.throughput_availability = copy.deepcopy(original)
        record = next(row for row in self.throughput_availability if row["scenario"] == "medium_tag" and
                      row["engine"] == "PipeANN" and row["status"] == "available")
        record["recall_target"] = 0.95
        self.assert_rejected("Availability disagrees", "complete")
        self.throughput_availability = copy.deepcopy(original)
        self.throughput_availability[0]["threads"] = 64
        self.assert_rejected("Availability disagrees", "complete")

    def test_frozen_plot_with_controller_exception_logs_passes_publication_verification(self):
        with patch.object(sys, "path", [str(SCRIPT.parent), *sys.path]):
            import run_sift1b_threeway as controller

        self.availability = [row for row in self.availability if row["status"] != "available"]
        self.throughput_availability = [
            row for row in self.throughput_availability if row["status"] != "available"
        ]
        for row in self.availability + self.throughput_availability:
            row.setdefault("recall_target", "")
            row.setdefault("threads", "")
        for row in self.single:
            row.update(
                measurement_reused="false" if row["engine"] == "Filtered_DiskANN" else "true",
                io_mode="buffered" if row["engine"] == "SPTAG_adaptive" else "direct",
                source="SYNTHETIC archived measurement source",
            )
        self.registration.update(
            query_payload_sha256="0" * 64,
            throughput_memory_policy="SYNTHETIC interleave across the same four NUMA nodes",
            throughput_validation_policy="SYNTHETIC all-cohort accounting included in wall time",
            native_io_policy={"SPTAG_adaptive": "buffered", "PipeANN": "direct", "Filtered_DiskANN": "direct"},
            selection_policy="SYNTHETIC shared controls measured once for both target views",
        )
        frozen = self.root / "frozen"
        frozen.mkdir()
        script = frozen / SCRIPT.name
        shutil.copyfile(SCRIPT, script)
        campaign = self.root / "controller-campaign"
        for stage in ("single", "complete"):
            with self.subTest(stage=stage):
                inputs = campaign / f"plot_inputs_{stage}"
                output, metadata = self.assert_rendered(
                    stage, input_directory=inputs, script=script,
                    output=campaign / ("plots_single" if stage == "single" else "plots_complete"),
                )
                for name, fields in (("single_summary.csv", controller.SINGLE_FIELDS),
                                     ("availability.csv", controller.AVAILABILITY_FIELDS)):
                    with (inputs / name).open(newline="") as stream:
                        self.assertEqual(next(csv.reader(stream)), list(fields))
                publication = controller.validate_plot_publication(SimpleNamespace(root=campaign), stage)
                self.assertEqual(publication["state"], "completed")
                self.assertEqual(publication["stage"], stage)
                for field in ("single_points", "throughput_points", "peak_points"):
                    if field in publication:
                        self.assertEqual(publication[field], metadata[field])
                self.assertEqual(len(publication["outputs"]), len(list(output.iterdir())))
                self.assertTrue(all(row["status"] != "available" for row in metadata["availability"]))
        self.assertEqual(script.read_bytes(), SCRIPT.read_bytes())

    def test_unavailable_scopes_are_authoritative_over_explicit_available_claims(self):
        original = copy.deepcopy(self.throughput_availability)
        for status in ("recall_target_unmet", "aio_limit", "invalid_output", "failed", "blocked",
                       "unsupported_predicate"):
            with self.subTest(status=status):
                self.throughput_availability = copy.deepcopy(original)
                self.throughput_availability.append({
                    "phase": "throughput", "scenario": "unfilter", "engine": "SPTAG_adaptive",
                    "status": status, "reason": "SYNTHETIC authoritative unavailable scope",
                    "recall_target": 0.95, "threads": 32,
                })
                self.assert_rejected("authoritative unavailable scope", "complete")
        for field in ("recall_target", "threads"):
            self.throughput_availability[-1][field] = ""
            self.assert_rejected("authoritative unavailable scope", "complete")

    def test_header_only_exception_log_does_not_require_available_records(self):
        self.availability = []
        _, metadata = self.assert_rendered()
        self.assertEqual(metadata["availability"], [])
        self.assertIn("unsupported mixed DNF", metadata["facet_annotations"]["single"]["mixed_dnf"])

    def test_invalid_single_recall_and_qps_fail_instead_of_dropping_rows(self):
        original = copy.deepcopy(self.single)
        for field, value, message in (
            ("recall", "NaN", "Invalid numeric measurement"),
            ("recall_min", "-Inf", "Invalid numeric measurement"),
            ("recall", 1.01, "Invalid recall ranges"),
            ("recall_min", -0.01, "Invalid recall ranges"),
            ("recall_max", 1.01, "Invalid recall ranges"),
            ("qps", "Inf", "Invalid numeric measurement"),
            ("qps", "not a number", "Invalid numeric measurement"),
            ("qps", "", "missing measurement fields"),
            ("qps", 0, "Invalid QPS ranges"),
            ("qps_min", -1, "Invalid QPS ranges"),
            ("qps_max", 1, "Invalid QPS ranges"),
        ):
            with self.subTest(field=field, value=value):
                self.single = copy.deepcopy(original)
                self.single[0][field] = value
                self.assert_rejected(message)

    def test_invalid_throughput_core_and_optional_numeric_fields_fail(self):
        original = copy.deepcopy(self.throughput)
        for field, value, message in (
            ("qps", "NaN", "Invalid numeric measurement"),
            ("recall", 1.1, "Invalid recall ranges"),
            ("qps_min", 0, "Invalid QPS ranges"),
            ("seconds_min", "Inf", "Invalid numeric measurement"),
            ("queries_min", "", "missing measurement fields"),
            ("queries_min", 999, "Invalid integer measurement"),
            ("repeats", 1.5, "Invalid integer measurement"),
            ("mean_latency_us", "NaN", "Invalid numeric measurement"),
            ("mean_ios", -1, "Invalid nonnegative metric"),
            ("cpu_nodes", "", "missing measurement fields"),
        ):
            with self.subTest(field=field, value=value):
                self.throughput = copy.deepcopy(original)
                self.throughput[0][field] = value
                self.assert_rejected(message, "complete")
        self.throughput = copy.deepcopy(original)
        self.throughput[0].update(p50_latency_us=20, p99_latency_us=10)
        self.assert_rejected("Invalid latency percentiles", "complete")

    def test_insufficient_duration_and_recall_are_not_silently_filtered(self):
        original = copy.deepcopy(self.throughput)
        index = next(i for i, row in enumerate(self.throughput) if row["recall_target"] == 0.95)
        for field, value, message in (
            ("seconds_min", 29.999, "insufficient duration"),
            ("recall_min", 0.949999, "below recall_target"),
            ("recall_target", 0.94, "recall_target disagrees"),
            ("threads", 3, "outside the registered grid"),
        ):
            with self.subTest(field=field):
                self.throughput = copy.deepcopy(original)
                self.throughput[index][field] = value
                self.assert_rejected(message, "complete")
        self.throughput = copy.deepcopy(original)
        row = next(row for row in self.throughput if row["recall_target"] == 0.90)
        row["recall_min"] = 0.899999
        self.assert_rejected("below recall_target", "complete")

    def test_duplicate_native_points_include_equivalent_numeric_encodings(self):
        self.single.append(dict(self.single[0], L=f"{self.single[0]['L']}.0"))
        self.assert_rejected("Duplicate measured points in single_summary.csv")
        self.single.pop()
        self.throughput.append(dict(self.throughput[0], threads=f"{self.throughput[0]['threads']}.0",
                                    recall_target=f"{self.throughput[0]['recall_target']:.3f}"))
        self.assert_rejected("Duplicate measured points in throughput_summary.csv", "complete")

    def test_diskann_mixed_and_unregistered_numeric_curves_cannot_be_fabricated(self):
        original = copy.deepcopy(self.single)
        mixed = next(row for row in self.single if row["scenario"] == "mixed_dnf")
        self.single.append(dict(mixed, engine="Filtered_DiskANN"))
        self.assert_rejected("no fabricated measurements")
        self.single = copy.deepcopy(original)
        self.single[0]["scenario"] = "numeric"
        self.assert_rejected("Unregistered scenario or engine")
        self.single = copy.deepcopy(original)
        mixed = next(row for row in self.throughput if row["scenario"] == "mixed_dnf")
        self.throughput.append(dict(mixed, engine="Filtered_DiskANN"))
        self.assert_rejected("no fabricated measurements", "complete")

    def test_single_metadata_and_native_protocol_must_agree(self):
        original = copy.deepcopy(self.single)
        for field, value, message in (
            ("threads", 2, "queries=1000 and threads=1"),
            ("queries", 999, "queries=1000 and threads=1"),
            ("L", 1.5, "Invalid integer measurement"),
            ("predicate", "SYNTHETIC other predicate", "disagrees with scenario_metadata"),
            ("candidate_count", 12, "disagrees with scenario_metadata"),
            ("selectivity", 0.5, "disagrees with scenario_metadata"),
        ):
            with self.subTest(field=field):
                self.single = copy.deepcopy(original)
                self.single[0][field] = value
                self.assert_rejected(message)

    def test_optional_provenance_cannot_mislabel_io_or_historical_reuse(self):
        historical = next(row for row in self.single if row["engine"] == "SPTAG_adaptive")
        historical["measurement_reused"] = False
        self.assert_rejected("Invalid measurement_reused")
        historical["measurement_reused"] = True
        historical["io_mode"] = "direct"
        self.assert_rejected("Invalid native io_mode")

    def test_registration_requires_the_exact_comparison_contract(self):
        original = copy.deepcopy(self.registration)
        for field, value in (
            ("dataset", "SIFT1M"), ("query_count", 999), ("metric", "L2"),
            ("recall_target", 0.94), ("throughput_min_seconds", 1),
            ("recall_targets", [0.95]), ("recall_targets", [0.90, 0.90]), ("recall_targets", [0.85, 0.95]),
            ("throughput_core_budget", 48), ("throughput_thread_grid", [1, 2, 48]),
            ("engines", ["SPTAG_adaptive", "PipeANN", "DiskANN"]),
            ("scenarios", SCENARIOS[:-1] + ["numeric"]),
        ):
            with self.subTest(field=field):
                self.registration = copy.deepcopy(original)
                self.registration[field] = value
                self.assert_rejected("Invalid registration")
        self.registration = copy.deepcopy(original)
        self.registration["scenario_metadata"]["broad_tag"]["selectivity"] = 0.5
        self.assert_rejected("Invalid scenario_metadata")

    def test_availability_requires_known_statuses_reasons_and_consistent_rows(self):
        original = copy.deepcopy(self.availability)
        for field, value in (("phase", "diagnostic"), ("status", "unknown"), ("engine", "SPANN")):
            with self.subTest(field=field):
                self.availability = copy.deepcopy(original)
                self.availability[0][field] = value
                self.assert_rejected("Invalid availability.csv")
        self.availability = copy.deepcopy(original)
        self.availability[0].update(status="blocked", reason="")
        self.assert_rejected("Invalid availability.csv")
        self.availability[0]["reason"] = "SYNTHETIC blocked"
        self.assert_rejected("Availability disagrees")
        self.availability = copy.deepcopy(original)
        self.availability.append(dict(self.availability[0]))
        self.assert_rejected("Invalid availability.csv")
        self.availability = copy.deepcopy(original)
        mixed = next(row for row in self.availability if row["scenario"] == "mixed_dnf" and
                     row["engine"] == "Filtered_DiskANN")
        mixed.update(status="available", reason="")
        self.assert_rejected("mixed_dnf availability must be unsupported_predicate")

    def test_missing_measurements_cannot_be_declared_available(self):
        self.single = [row for row in self.single
                       if not (row["scenario"] == "unfilter" and row["engine"] == "Filtered_DiskANN")]
        self.assert_rejected("Availability disagrees")

    def test_missing_historical_curves_cannot_be_hidden_by_availability(self):
        self.single = [row for row in self.single
                       if not (row["scenario"] == "mixed_dnf" and row["engine"] == "PipeANN")]
        record = next(row for row in self.availability
                      if row["scenario"] == "mixed_dnf" and row["engine"] == "PipeANN")
        record.update(status="blocked", reason="SYNTHETIC omitted historical curve")
        self.assert_rejected("Missing reused historical single-thread curve")

    def test_existing_directory_file_and_dangling_symlink_are_never_overwritten(self):
        output = self.root / "existing"
        output.mkdir()
        sentinel = output / "single_thread_recall_qps.png"
        sentinel.write_bytes(b"SYNTHETIC protected output")
        result, _ = self.render(output=output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to overwrite", result.stderr)
        self.assertEqual(sentinel.read_bytes(), b"SYNTHETIC protected output")
        self.assertEqual(list(output.iterdir()), [sentinel])
        existing_file = self.root / "existing-file"
        existing_file.write_text("SYNTHETIC protected file")
        dangling = self.root / "dangling"
        dangling.symlink_to("nonexistent-synthetic-target")
        for target in (existing_file, dangling):
            with self.subTest(target=target.name):
                result, _ = self.render(output=target)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Refusing to overwrite", result.stderr)
        self.assertEqual(existing_file.read_text(), "SYNTHETIC protected file")
        self.assertTrue(dangling.is_symlink())

    def test_cli_requires_an_explicit_known_stage(self):
        for flags in ([], ["--stage", "all"], ["--complete"], ["--stage", "single", "extra"]):
            with self.subTest(flags=flags):
                self.assert_rejected("usage:", flags=flags)

    def test_input_changes_during_rendering_cannot_publish_outputs(self):
        expression = self.source_expression("""
            ggsave <- function(...) {
              cat("\\n", file = file.path(commandArgs(trailingOnly = TRUE)[[1]], "single_summary.csv"),
                  append = TRUE)
            }
        """)
        self.assert_rejected("Frozen plotting inputs changed", expression=expression)

    def test_failed_render_cleans_up_only_new_plot_outputs(self):
        expression = self.source_expression("""
            ggsave <- function(filename, ...) {
              writeLines("SYNTHETIC partial figure", filename)
              stop("SYNTHETIC rendering failure")
            }
        """)
        self.assert_rejected("SYNTHETIC rendering failure", expression=expression)
        for name, content in self.input_bytes.items():
            self.assertEqual((self.root / name).read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
