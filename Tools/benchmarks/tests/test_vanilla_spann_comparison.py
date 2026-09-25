"""Fixed SIFT1M controls and read-only views; no real index searches in these tests."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS))

import run_vanilla_spann_build as build
import run_vanilla_spann_comparison as comparison
import analyze_vanilla_spann_comparison as analysis


CONFIGS = BENCHMARKS / "configs/sift1m_vanilla_spann"


class VanillaSpannComparisonTest(unittest.TestCase):
    def test_builder_uses_native_ini_input_and_resource_controls(self):
        config = CONFIGS / "build.ini"
        plan = build.read_ini(config)
        command = build.build_command(plan, config)
        self.assertNotIn("-i", command)
        for flag, value in (("-a", "SPANN"), ("-v", "Float"), ("-d", "128"),
                            ("-f", "XVEC"), ("-t", "24")):
            self.assertEqual(value, command[command.index(flag) + 1])
        self.assertEqual(plan["Base"]["IndexDirectory"], command[command.index("-o") + 1])
        self.assertIn("--cpunodebind=2", command)
        self.assertIn("--membind=2", command)
        self.assertEqual(12, plan["BuildSSDIndex"].getint("PostingPageLimit"))
        self.assertEqual(3, plan["BuildHead"].getint("RefineIterations"))
        self.assertEqual(8192, plan["BuildHead"].getint("MaxCheck"))

    def test_conflicting_native_builder_types_or_threads_fail(self):
        for section, key, value in (("Index", "ValueType", "UInt8"),
                                    ("BuildHead", "NumberOfThreads", "1")):
            with self.subTest(section=section):
                plan = build.read_ini(CONFIGS / "build.ini")
                plan[section][key] = value
                with self.assertRaises(ValueError):
                    build.build_command(plan, CONFIGS / "build.ini")

    def test_fixed_engine_controls_match_query_and_posting_budgets(self):
        plan = build.read_ini(CONFIGS / "h3.ini")["H3"]
        for case in plan["SearchCases"].split(","):
            with self.subTest(case=case):
                _, _, h3 = comparison.paired_search_configs(CONFIGS / "h3.ini", case)
                vanilla = build.read_ini(CONFIGS / f"search_{case}.ini")["SearchSSDIndex"]
                for key in ("InternalResultNum", "NumberOfThreads", "HashTableExponent",
                            "ResultNum", "MaxCheck", "MaxDistRatio", "SearchPostingPageLimit"):
                    self.assertEqual(vanilla[key], h3[key])
                self.assertEqual(512, h3.getint("HierarchyMaxCheck"))

    def make_view_fixture(self, root):
        source = root / "source"
        tenant = source / "tenant_0"
        tenant.mkdir(parents=True)
        (source / "manifest.txt").write_text("tenant 0\n")
        (tenant / "posting.bin").write_bytes(b"unchanged payload")
        (tenant / "HeadIndex").mkdir()
        (tenant / "HeadIndex/vectors.bin").write_bytes(b"unchanged vectors")
        index = root / "isolated/index"
        common = "[Index]\nIndexAlgoType=SPANN\n[Base]\nIndexDirectory={}\n[BuildSSDIndex]\nUseDirectIO={}\nReplicaCount=8\n"
        (tenant / "indexloader.ini").write_text(common.format(tenant, "false"))
        loader = root / "direct.ini"
        loader.write_text(common.format(index / "tenant_0", "true"))
        return {"SourceIndex": str(source), "Index": str(index), "IndexLoader": str(loader)}

    def test_prepared_view_reuses_unchanged_payloads_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            plan = self.make_view_fixture(Path(temporary))
            source = Path(plan["SourceIndex"])
            before = comparison.payload_identity(source)
            index = comparison.prepare_h3(plan)
            self.assertTrue((index / "tenant_0/posting.bin").is_symlink())
            self.assertTrue((index / "tenant_0/HeadIndex").is_symlink())
            self.assertFalse((index / "tenant_0/indexloader.ini").is_symlink())
            self.assertEqual(index, comparison.prepare_h3(plan))
            self.assertEqual(before, comparison.payload_identity(source))

    def test_changed_layout_cannot_masquerade_as_io_only_view(self):
        with tempfile.TemporaryDirectory() as temporary:
            plan = self.make_view_fixture(Path(temporary))
            loader = Path(plan["IndexLoader"])
            loader.write_text(loader.read_text().replace("ReplicaCount=8", "ReplicaCount=4"))
            with self.assertRaisesRegex(ValueError, "Unexpected H3 loader changes"):
                comparison.prepare_h3(plan)
            self.assertFalse(Path(plan["Index"]).exists())

    def test_replaced_payload_is_rejected_in_existing_view(self):
        with tempfile.TemporaryDirectory() as temporary:
            plan = self.make_view_fixture(Path(temporary))
            index = comparison.prepare_h3(plan)
            payload = index / "tenant_0/posting.bin"
            payload.unlink()
            payload.write_bytes(b"replacement")
            with self.assertRaisesRegex(ValueError, "unchanged source link"):
                comparison.prepare_h3(plan)

    def test_h3_phases_exclude_warmup_and_preserve_declared_nprobe(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "native.log"
            result = {"engine": "spann", "queries": 2, "failed_queries": 0,
                      "mean_latency_ms": 1.0, "qps": 1000, "recall": 0.9}
            log.write_text(
                "PhaseTime: nprobe=16 h2=100 io=100 scan=100\n"
                "PhaseTime: nprobe=16 h2=1 io=2 scan=3\n"
                "PhaseTime: nprobe=16 h2=3 io=4 scan=5\n" + json.dumps(result) + "\n")
            row, phases = comparison.h3_records([log], 2, 1, 16, True)
            self.assertEqual(result, row)
            self.assertEqual({"nprobe": 16, "h2": 2, "io": 3, "scan": 4}, phases)
            with self.assertRaisesRegex(ValueError, "beam"):
                comparison.h3_records([log], 2, 1, 32, True)
            with self.assertRaisesRegex(ValueError, "instrumentation"):
                comparison.h3_records([log], 2, 1, 16, False)

    def test_io_order_changes_attribution_not_work_or_recall(self):
        first = {"recall": 0.95, "distance_computations_per_query": 10,
                 "posting_page_reads_per_query": 20, "contributing_postings_per_query": 2}
        second = {**first, "contributing_postings_per_query": 3}
        self.assertEqual(comparison.h3_core_work(first), comparison.h3_core_work(second))
        for key in ("recall", "distance_computations_per_query", "posting_page_reads_per_query"):
            with self.subTest(key=key):
                changed = {**second, key: second[key] + 0.001}
                self.assertNotEqual(comparison.h3_core_work(first), comparison.h3_core_work(changed))

    def test_latency_summary_keeps_ordinary_and_profile_denominators_separate(self):
        plain = [{"recall": 0.9, "mean_ms": value, "qps": 1000 / value}
                 for value in (1, 2, 3)]
        profile = [{"recall": 0.9, "mean_ms": value, "qps": 1000 / value}
                   for value in (2, 3, 4)]
        summary = analysis.latency_fields(plain, profile)
        self.assertEqual(2, summary["mean_ms"])
        self.assertEqual(3, summary["profile_mean_ms"])
        self.assertEqual(500, summary["median_qps"])

    def test_latency_summary_rejects_stale_latency_or_changed_recall(self):
        row = {"recall": 0.9, "mean_ms": 1.0, "qps": 1000.0}
        for replacement in ({"qps": 1200.0}, {"recall": 0.91}, {"mean_ms": float("nan")}):
            with self.subTest(replacement=replacement):
                with self.assertRaises(ValueError):
                    analysis.latency_fields([row, row, {**row, **replacement}], [row, row, row])


if __name__ == "__main__":
    unittest.main()
