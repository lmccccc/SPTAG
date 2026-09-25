"""Fixed same-process experiments; these tests never load the SIFT index."""

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import numpy as np

BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS))
import run_spann_latency_ab as latency


class LatencyExperimentTest(unittest.TestCase):
    def test_fixed_matrix_uses_one_native_process_and_exact_results(self):
        plan, config, variants, _ = latency.load_plan(latency.DEFAULT_PLAN)
        inputs = {"query_npy": "/fixture/query.npy", "scenarios": [
            {"name": "unfilter", "kind": "unfilter", "truth_npy": "/fixture/truth.npy"}]}
        job = latency.make_job(plan, config, variants, inputs)
        self.assertEqual(13, len(variants))
        self.assertEqual(39, len(job["points"]))
        self.assertEqual(1, job["command"].count("--index"))
        self.assertEqual(1, job["command"].count("--dump-results"))
        self.assertEqual(39, job["command"].count("--search-sweep-ini"))
        self.assertNotIn("--direct-search", job["command"])
        names = list(variants)
        self.assertEqual(names, [point["variant"] for point in job["points"][:13]])
        self.assertEqual(list(reversed(names)), [point["variant"] for point in job["points"][13:26]])

    def test_implementation_cannot_change_beam_or_native_budget(self):
        for original, replacement, message in (
            ("HierarchyRoutingBudgets=Auto", "HierarchyRoutingBudgets=1,2,4,8", "parent beams"),
            ("MaxCheck=1024", "MaxCheck=512", "undeclared"),
        ):
            with self.subTest(parameter=original), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for name in ("sift1b_spann_latency", "sift1b_official"):
                    shutil.copytree(BENCHMARKS / "configs" / name, root / name)
                path = root / "sift1b_spann_latency/search/auto64.ini"
                path.write_text(path.read_text().replace(original, replacement))
                with self.assertRaisesRegex(ValueError, message):
                    latency.load_plan(root / "sift1b_spann_latency/experiment.ini")

    def test_work_comparison_includes_exact_ids(self):
        first = {"native": {"qps": 100, "recall": 0.5, "result_ids": [1, 2]}}
        second = {"native": {"qps": 200, "recall": 0.5, "result_ids": [1, 3]}}
        self.assertNotEqual(latency.native_work(first), latency.native_work(second))
        second["native"]["result_ids"] = [1, 2]
        self.assertEqual(latency.native_work(first), latency.native_work(second))

    def test_confirmation_keeps_beam_comparisons_on_rolling_prefetch(self):
        _, _, variants, _ = latency.load_plan(latency.DEFAULT_PLAN.parent / "confirm.ini")
        self.assertEqual(9, len(variants))
        self.assertTrue(all(variant["prefetch"] == "Rolling16" for variant in variants.values()))
        self.assertEqual("1,2,4,8", variants["narrow1_roll64"]["routing"])

    def test_recall90_plan_preserves_budget_and_separates_throughput(self):
        _, _, variants, _ = latency.load_plan(latency.DEFAULT_PLAN.parent / "recall90.ini")
        self.assertEqual(4, len(variants))
        self.assertTrue(all(variant["L"] == 128 and variant["routing"] == "Auto"
                            for variant in variants.values()))
        self.assertEqual({"bitmap128", "auto128"},
                         {name for name, variant in variants.items() if variant["profiled"]})
        self.assertEqual({"plain128", "auto_plain128"},
                         {name for name, variant in variants.items() if variant["kind"] == "throughput"})

    def test_grouping_excludes_each_warmup_and_reports_routing_losses(self):
        plan, config, variants, _ = latency.load_plan(latency.DEFAULT_PLAN)
        variants = {name: variants[name] for name in ("bitmap64", "auto64", "narrow1_64")}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plain = root / "plain64.ini"
            plain.write_text(Path(variants["bitmap64"]["path"]).read_text()
                             .replace("LogPhaseTime=true", "LogPhaseTime=false")
                             .replace("LogPathStats=true", "LogPathStats=false"))
            variants["plain64"] = {
                **variants["bitmap64"], "name": "plain64", "path": str(plain),
                "kind": "throughput", "profiled": False,
            }
            truth_path = root / "truth.npy"
            np.save(truth_path, np.arange(20, dtype=np.int64).reshape(2, 10), allow_pickle=False)
            inputs = {"query_count": 2, "query_npy": "/fixture/query.npy", "scenarios": [
                {"name": "unfilter", "kind": "unfilter", "truth_npy": str(truth_path)}]}
            job = latency.make_job(plan, config, variants, inputs)
            lines = []
            for point in job["points"]:
                for warmup in ((True, False) if variants[point["variant"]]["profiled"] else ()):
                    for _ in range(2):
                        elapsed = 100 if warmup else 0.05
                        lines.append(f"PhaseTime: h2={elapsed} h2Merge=0.01 h2Vec=0.02 post=0.05 h2Unique=8\n")
                        layers = []
                        for level in range(5, 0, -1):
                            work = ("graph_checked=2" if level == 5 else
                                    f"assignments=2,distances=2,dedup_hash={int(point['variant'] != 'bitmap64')}")
                            layers.append(f"level=H{level},budget=1,candidates=2,eligible=2,retained=1,"
                                          f"retained_eligible=1,{work},graph_ms=0.01,merge_ms=0.01,"
                                          "tag_ms=0,vec_ms=0.01,sort_ms=0")
                        lines.append("HierarchyWork: " + " ".join(layers) + "\n")
                ids = list(range(20))
                if point["variant"] == "narrow1_64":
                    ids[9] = 99
                lines.append(json.dumps({
                    "engine": "static_per_tag_bkt", "failed_queries": 0, "queries": 2,
                    "search_ini": point["value"], "measure_offset": 0,
                    "search_api": "SearchWithPredicate", "value_type": "UInt8",
                    "recall": 0.95 if ids[9] == 99 else 1.0, "qps": 100, "result_ids": ids,
                }) + "\n")
            (root / "native.stdout.log").write_text("".join(lines))
            (root / "native.stderr.log").write_text("")
            summary = latency.summarize_run(config, inputs, variants, job, root)
            self.assertEqual(4, len(summary))
            self.assertEqual(0.05, summary[0]["phase_mean"]["h2"])
            self.assertTrue(summary[1]["same_work"])
            self.assertTrue(summary[1]["identical_result_ids"])
            self.assertEqual(1, summary[2]["lost_gt_hits"])
            self.assertEqual(0, summary[2]["gained_gt_hits"])
            self.assertEqual(1, summary[2]["queries_losing_gt"])
            self.assertFalse(summary[2]["same_work"])
            self.assertTrue(summary[3]["same_work"])
            self.assertTrue(summary[3]["identical_result_ids"])
            self.assertIsNone(summary[3]["phase_mean"])
            self.assertIsNone(summary[3]["hierarchy_mean"])
            self.assertEqual(5, len((root / "summary.csv").read_text().splitlines()))


if __name__ == "__main__":
    unittest.main()
