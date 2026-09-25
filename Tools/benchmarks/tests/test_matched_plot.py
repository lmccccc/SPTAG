import csv
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


class MatchedPlot(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.script = Path(__file__).resolve().parents[1] / "plot_sift1m_matched_curve.R"
        self.protocol = {
            "protocol_id": "a" * 64, "index_fingerprint": "b" * 64,
            "harness_fingerprint": "c" * 64,
            "original_core_fingerprint": "d" * 64, "current_core_fingerprint": "e" * 64,
            "io_mode": "buffered", "topk": 10, "maxcheck": 2048,
            "hierarchy_maxcheck": 512, "hierarchy_initial_probe_ratio": 0.666666,
            "page_limit": 15, "query_count": 1000, "measure_offset": 0, "warmup": 1000,
            "query_threads": 1, "numa_cpu_node": 2, "numa_memory_node": 2,
            "nprobe": [16, 24], "repetitions": 2, "retained_ratio": 0.5,
            "minimum_physical_degree": 16, "original_reference_verified": True,
            "timed_body_shared": True,
        }
        self.rows = [
            {
                "scenario": scenario, "case": case, "nprobe": probe,
                "recall_at_10": 0.8, "ordinary_ms": 1.0,
                "ordinary_ms_runs": [0.9, 1.1], "qps": 1000,
                "protocol_id": self.protocol["protocol_id"],
                "index_fingerprint": self.protocol["index_fingerprint"],
                "harness_fingerprint": self.protocol["harness_fingerprint"],
                "core_fingerprint": self.protocol[
                    "original_core_fingerprint" if case == "h1_original" else "current_core_fingerprint"
                ],
                "sweep_execution": "single_load_nprobe_array",
                "common_timed_body_fingerprint": "f" * 64,
            }
            for scenario in (
                "unfilter", "broad_tag", "medium_tag", "extreme_tag", "numeric", "mixed_dnf"
            )
            for case in ("h1_original", "h1", "h3", "supplier")
            for probe in (16, 24)
        ]

    def render(self, *extra_args):
        summary = self.root / "synthetic-fixture.json"
        protocol = self.root / "protocol.json"
        summary.write_text(json.dumps(self.rows))
        protocol.write_text(json.dumps(self.protocol))
        return subprocess.run(
            ["Rscript", str(self.script), str(summary), str(protocol),
             str(self.root / "plot"), *extra_args],
            capture_output=True, text=True, check=False,
        )

    def assert_rejected(self, message):
        result = self.render()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)
        self.assertFalse((self.root / "plot.png").exists())

    def test_complete_matched_output_contains_no_historical_points(self):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        provenance = json.loads((self.root / "plot.plot-provenance.json").read_text())
        self.assertEqual(provenance["historical_points"], 0)
        self.assertEqual(provenance["total_points"], 48)
        self.assertFalse(provenance["partial_scenarios"])
        with (self.root / "plot.plot-data.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 48)
        self.assertEqual({row["case"] for row in rows}, {"h1_original", "h1", "h3", "supplier"})
        self.assertEqual({row["protocol_id"] for row in rows}, {self.protocol["protocol_id"]})
        self.assertTrue((self.root / "plot.pdf").is_file())

    def test_partial_requires_explicit_flag_and_never_substitutes_history(self):
        self.rows = [row for row in self.rows if row["scenario"] in ("unfilter", "broad_tag")]
        self.assert_rejected("all six scenarios")
        result = self.render("--partial-scenarios")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        provenance = json.loads((self.root / "plot.plot-provenance.json").read_text())
        self.assertEqual(provenance["historical_points"], 0)
        self.assertEqual(provenance["total_points"], 16)
        self.assertEqual(len(provenance["missing_scenarios"]), 4)

    def test_rejects_missing_case_or_probe(self):
        self.rows.pop()
        self.assert_rejected("all four cases at every declared nprobe")

    def test_rejects_undeclared_probe_grid(self):
        self.protocol["nprobe"].append(32)
        self.assert_rejected("all four cases at every declared nprobe")

    def test_rejects_single_point_publication(self):
        self.protocol["nprobe"] = [24]
        self.rows = [row for row in self.rows if row["nprobe"] == 24]
        self.assert_rejected("not single points")

    def test_rejects_mixed_common_inputs(self):
        for field in ("protocol_id", "index_fingerprint", "harness_fingerprint"):
            with self.subTest(field=field):
                original = self.rows[-1][field]
                self.rows[-1][field] = "f" * 64
                self.assert_rejected(f"mixed or mismatched {field}")
                self.rows[-1][field] = original

    def test_rejects_modified_core_masquerading_as_original(self):
        self.rows[0]["core_fingerprint"] = self.protocol["current_core_fingerprint"]
        self.assert_rejected("wrong per-case core identity")

    def test_rejects_unverified_reference_or_different_timed_bodies(self):
        for field in ("original_reference_verified", "timed_body_shared"):
            with self.subTest(field=field):
                self.protocol[field] = False
                self.assert_rejected("distinct authenticated cores and a shared timed body")
                self.protocol[field] = True
        self.protocol["original_core_fingerprint"] = self.protocol["current_core_fingerprint"]
        self.assert_rejected("distinct authenticated cores")

    def test_rejects_direct_io_or_process_per_point(self):
        self.protocol["io_mode"] = "direct"
        self.assert_rejected("invalid matched IO")
        self.protocol["io_mode"] = "buffered"
        self.rows[-1]["sweep_execution"] = "per_point_process"
        self.assert_rejected("non-native array execution")

    def test_rejects_false_aggregation_and_incomplete_repetitions(self):
        self.rows[-1]["qps"] = 999
        self.assert_rejected("latency-derived QPS")
        self.rows[-1]["qps"] = 1000
        self.rows[-1]["ordinary_ms_runs"] = [1.0]
        self.assert_rejected("repetitions disagree")

    def test_rejects_duplicate_rows(self):
        self.rows.append(dict(self.rows[0]))
        self.assert_rejected("all four cases at every declared nprobe")

    def test_rejects_missing_identity(self):
        self.rows[-1]["index_fingerprint"] = None
        self.assert_rejected("missing matched measurement fields")

    def test_nested_measurement_evidence_does_not_break_coordinate_export(self):
        for row in self.rows:
            row["work"] = {"head": 100, "upper": [0, 0]}
            row["operations"] = [{"load": 1.0, "measured": 2.0}]
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with (self.root / "plot.plot-data.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 48)
        self.assertNotIn("work", rows[0])
        self.assertNotIn("operations", rows[0])

    def point_overlay(self):
        return {
            "label": "Cost v2",
            "common": {**self.protocol, "timed_body_fingerprint": "f" * 64},
            "provenance": {"fixture": "synthetic, not benchmark data"},
            "rows": [
                {
                    "scenario": "broad_tag", "case": case, "nprobe": 24,
                    "recall_at_10": 0.91, "qps": 1250, "ordinary_ms": 0.8,
                    "ordinary_ms_runs": [0.7, 0.9],
                }
                for case in ("auto", "graph", "estimate_only", "original", "h3")
            ],
        }

    def render_points(self, overlay):
        path = self.root / "points.json"
        path.write_text(json.dumps(overlay))
        return self.render("--points", str(path))

    def test_single_points_are_separate_from_complete_curves(self):
        result = self.render_points(self.point_overlay())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        provenance = json.loads((self.root / "plot.plot-provenance.json").read_text())
        self.assertEqual(provenance["curve_points"], 48)
        self.assertEqual(provenance["total_points"], 53)
        self.assertEqual(provenance["single_points"]["count"], 5)
        self.assertFalse(provenance["single_points"]["connected"])
        with (self.root / "plot.plot-data.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(sum(r["measurement_kind"] == "curve" for r in rows), 48)
        points = [r for r in rows if r["measurement_kind"] == "single_point"]
        self.assertEqual(len(points), 5)
        self.assertTrue(all(r["case"].startswith("cost_") for r in points))
        self.assertTrue(all(r["core_fingerprint"] == "NA" for r in points))

    def test_points_cannot_bypass_missing_curve_rows(self):
        self.rows.pop()
        result = self.render_points(self.point_overlay())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("all four cases", result.stderr)

    def test_points_reject_mixed_settings_or_timed_body(self):
        for field, value in (("io_mode", "direct"), ("page_limit", 12),
                             ("index_fingerprint", "0" * 64),
                             ("timed_body_fingerprint", "0" * 64)):
            with self.subTest(field=field):
                overlay = self.point_overlay()
                overlay["common"][field] = value
                result = self.render_points(overlay)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "plot.png").exists())

    def test_points_reject_missing_control_or_wrong_metrics(self):
        for defect in ("control", "qps", "repetitions", "duplicate"):
            with self.subTest(defect=defect):
                overlay = self.point_overlay()
                if defect == "control":
                    overlay["rows"] = [r for r in overlay["rows"] if r["case"] != "graph"]
                elif defect == "qps":
                    overlay["rows"][0]["qps"] = 999
                elif defect == "repetitions":
                    overlay["rows"][0]["ordinary_ms_runs"] = [0.8]
                else:
                    overlay["rows"].append(dict(overlay["rows"][0]))
                result = self.render_points(overlay)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "plot.png").exists())


if __name__ == "__main__":
    unittest.main()
