import csv
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


class SupplierPlot(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.script = Path(__file__).resolve().parents[1] / "plot_sift1m_h1_h2_curve.R"
        self.historical = self.root / "historical.jsonl"
        self.historical.write_text(json.dumps({
            "mode": "H1Only", "workload": "unfilter", "nprobe": 16,
            "recall": 0.8, "qps": 1000,
        }) + "\n")
        self.paired = self.root / "paired.csv"
        metrics = ["qps_median", "qps_min", "qps_max", "recall_median"]
        with self.paired.open("w") as stream:
            writer = csv.writer(stream)
            writer.writerow(["workload", "nprobe"] + [
                f"{side}_{metric}" for side in ("old", "new") for metric in metrics
            ])
            for workload in ("unfilter", "broad_tag", "medium_tag", "sparse_tag", "mixed_dnf"):
                for probe in (32, 64, 128, 256):
                    writer.writerow([workload, probe] + [1000, 900, 1100, 0.8] * 2)
        self.rows = [
            {
                "scenario": scenario, "case": case, "nprobe": probe,
                "recall_at_10": 0.8, "ordinary_ms": 1.0,
                "ordinary_ms_runs": [0.9, 1.1], "qps": 1000,
            }
            for scenario in (
                "unfilter", "broad_tag", "medium_tag", "extreme_tag", "numeric", "mixed_dnf"
            )
            for case in ("h1", "h3", "supplier")
            for probe in (16, 24)
        ]

    def render(self, *extra_args):
        summary = self.root / "synthetic-fixture.json"
        summary.write_text(json.dumps(self.rows))
        return subprocess.run(
            ["Rscript", str(self.script), str(self.historical),
             str(self.root / "plot"), str(self.paired), str(summary), *extra_args],
            capture_output=True, text=True, check=False,
        )

    def declare_corrected(self):
        for row in self.rows:
            if row["case"] == "supplier":
                row["supplier_degree_semantics"] = "predicate_valid_neighbors"

    def check_render(self, semantics):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        provenance = json.loads((self.root / "plot.plot-provenance.json").read_text())
        self.assertEqual(provenance["supplier_degree_semantics"], semantics)
        self.assertEqual(provenance["current_points"], 36)
        self.assertEqual(provenance["current_nprobe"], [16, 24])
        self.assertTrue(provenance["current_is_measured_sweep"])
        self.assertTrue((self.root / "plot.png").is_file())
        self.assertTrue((self.root / "plot.pdf").is_file())
        return provenance

    def test_legacy_retains_defect_warning(self):
        provenance = self.check_render("fresh_unvisited_neighbors")
        self.assertIn("known defect", provenance["supplier_trigger_warning"])
        self.assertEqual(provenance["sweep_execution"], "per_point_process")

    def test_corrected_semantics_are_explicit(self):
        self.declare_corrected()
        provenance = self.check_render("predicate_valid_neighbors")
        self.assertIsNone(provenance["supplier_trigger_warning"])

    def test_inconsistent_semantics_are_rejected(self):
        for value in (None, "fresh_unvisited_neighbors", "unknown"):
            with self.subTest(value=value):
                self.declare_corrected()
                self.rows[-1]["supplier_degree_semantics"] = value
                result = self.render()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("one consistent, recognized degree semantics", result.stderr)
                self.assertFalse((self.root / "plot.png").exists())

    def test_single_load_execution_is_explicit(self):
        self.declare_corrected()
        for row in self.rows:
            row["sweep_execution"] = "single_load_nprobe_array"
        provenance = self.check_render("predicate_valid_neighbors")
        self.assertEqual(provenance["sweep_execution"], "single_load_nprobe_array")

    def declare_ratio(self):
        for row in self.rows:
            if row["case"] == "supplier":
                row["supplier_degree_semantics"] = "retained_eligible_ratio"
                row["retained_ratio"] = 0.5
                row["minimum_physical_degree"] = 16

    def test_ratio_semantics_are_explicit(self):
        self.declare_ratio()
        provenance = self.check_render("retained_eligible_ratio")
        self.assertEqual(provenance["retained_ratio"], 0.5)
        self.assertEqual(provenance["minimum_physical_degree"], 16)
        self.assertIsNone(provenance["supplier_trigger_warning"])

    def test_ratio_parameters_are_required_and_constant(self):
        self.declare_ratio()
        for row in self.rows:
            row.pop("retained_ratio", None)
        result = self.render()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("require retained_ratio", result.stderr)
        for field, value in (
            ("retained_ratio", None), ("retained_ratio", 0.4),
            ("minimum_physical_degree", 15.5),
        ):
            with self.subTest(field=field, value=value):
                self.declare_ratio()
                self.rows[-1][field] = value
                result = self.render()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("consistent valid ratio", result.stderr)
                self.assertFalse((self.root / "plot.png").exists())

    def test_inconsistent_execution_is_rejected(self):
        for value in (None, "per_point_process", "unknown"):
            with self.subTest(value=value):
                for row in self.rows:
                    row["sweep_execution"] = "single_load_nprobe_array"
                self.rows[-1]["sweep_execution"] = value
                result = self.render()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("one consistent, recognized sweep execution", result.stderr)
                self.assertFalse((self.root / "plot.png").exists())

    def select_partial_stage(self):
        self.declare_ratio()
        self.rows = [
            row for row in self.rows if row["scenario"] in ("unfilter", "broad_tag")
        ]

    def test_partial_stage_requires_explicit_flag(self):
        self.select_partial_stage()
        result = self.render()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("all six scenarios", result.stderr)
        self.assertFalse((self.root / "plot.png").exists())

    def test_partial_stage_does_not_fill_missing_scenarios(self):
        self.select_partial_stage()
        result = self.render("--partial-scenarios")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        provenance = json.loads((self.root / "plot.plot-provenance.json").read_text())
        self.assertTrue(provenance["partial_scenarios"])
        self.assertEqual(provenance["current_scenarios"], ["unfilter", "broad_tag"])
        self.assertEqual(set(provenance["missing_current_scenarios"]), {
            "medium_tag", "extreme_tag", "numeric", "mixed_dnf",
        })
        self.assertEqual(provenance["current_points"], 12)
        with (self.root / "plot.plot-data.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        modes = set(provenance["current_case_modes"].values())
        current = [row for row in rows if row["mode"] in modes]
        self.assertEqual(len(current), 12)
        self.assertEqual({row["workload"] for row in current}, {"unfilter", "broad_tag"})
        self.assertTrue((self.root / "plot.pdf").is_file())

    def test_partial_stage_rejects_missing_probe_points(self):
        self.select_partial_stage()
        self.rows.pop()
        result = self.render("--partial-scenarios")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("at every nprobe", result.stderr)

    def test_partial_stage_rejects_single_points(self):
        self.select_partial_stage()
        self.rows = [row for row in self.rows if row["nprobe"] == 24]
        result = self.render("--partial-scenarios")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not single points", result.stderr)


if __name__ == "__main__":
    unittest.main()
