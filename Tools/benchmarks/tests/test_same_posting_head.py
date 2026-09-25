"""The navigation ablation must preserve the ordered heads and every posting byte."""

import configparser
import json
from pathlib import Path
import sys
import tempfile
import unittest

BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS))
import build_same_posting_head as build
import run_same_posting_head as comparison


class SamePostingHeadTest(unittest.TestCase):
    def test_native_build_is_head_only(self):
        path = BENCHMARKS / "configs/sift1m_same_posting/head_build.ini"
        plan = build.read_ini(path)
        command = build.command_from_ini(plan, path)
        self.assertEqual("BKT", command[command.index("-a") + 1])
        self.assertEqual(plan["Input"]["VectorPath"], command[command.index("-i") + 1])
        self.assertNotIn("SPANN", command)
        self.assertFalse(plan.has_section("BuildSSDIndex"))

    def test_paired_native_controls_change_only_head_mode(self):
        path = BENCHMARKS / "configs/sift1m_same_posting/experiment.ini"
        cases, diagnostics, controls = comparison.validate_controls(
            path, build.read_ini(path)["Experiment"])
        self.assertEqual(6, len(cases))
        self.assertEqual(2, len(diagnostics))
        for probe in (24, 32, 48):
            flat = dict(controls[f"flat{probe}"])
            h3 = dict(controls[f"h3_{probe}"])
            self.assertEqual("H1Only", flat.pop("headnavigationmode"))
            self.assertEqual("H2Only", h3.pop("headnavigationmode"))
            self.assertEqual(flat, h3)

    def fixture(self, root):
        source = root / "original"
        tenant = source / "tenant_0"
        head = tenant / "HeadIndex"
        head.mkdir(parents=True)
        (source / "manifest.txt").write_text("unchanged mapping")
        (tenant / "SPTAGFullList.bin").write_bytes(b"unchanged posting payload")
        (tenant / "SPTAGHeadVectors.bin").write_bytes(b"ordered H1 vectors")
        (tenant / "SPTAGHeadVectorIDs.bin").write_bytes(b"ordered canonical IDs")
        (head / "head_node_meta.bin").write_bytes(b"unchanged head metadata")
        (tenant / "SecondLevelHeadIndex").mkdir()
        (tenant / "SecondLevelHeadIndex/vectors.bin").write_bytes(b"unchanged top vectors")
        flat = root / "build/FlatHeadIndex"
        flat.mkdir(parents=True)
        (flat.parent / "status.json").write_text(json.dumps({"state": "completed"}))
        for name in ("tree.bin", "graph.bin", "deletes.bin", "indexloader.ini"):
            (flat / name).write_text("new native graph")
        (flat / "vectors.bin").write_bytes((tenant / "SPTAGHeadVectors.bin").read_bytes())
        view = root / "build/search_view"
        template = ("[Base]\nIndexDirectory={}\n[SelectHead]\nBuildH1Graph={}\n"
                    "[BuildSSDIndex]\nUseDirectIO={}\nReplicaCount=8\n")
        (tenant / "indexloader.ini").write_text(template.format(tenant, "false", "false"))
        loader = root / "loader.ini"
        loader.write_text(template.format(view / "tenant_0", "true", "true"))
        plan = configparser.ConfigParser(interpolation=None)
        plan["Experiment"] = {"SourceIndex": str(source), "FlatHeadIndex": str(flat),
                              "Index": str(view), "IndexLoader": loader.name}
        return plan["Experiment"], root / "experiment.ini"

    def test_view_keeps_same_posting_file_and_reuses_graph_for_both_modes(self):
        with tempfile.TemporaryDirectory() as temporary:
            plan, config = self.fixture(Path(temporary))
            before = comparison.source_inventory(Path(plan["SourceIndex"]))
            view = comparison.prepare_view(plan, config)
            original = Path(plan["SourceIndex"]) / "tenant_0/SPTAGFullList.bin"
            self.assertEqual(original, (view / "tenant_0/SPTAGFullList.bin").resolve())
            self.assertEqual(Path(plan["FlatHeadIndex"]) / "graph.bin",
                             (view / "tenant_0/HeadIndex/graph.bin").resolve())
            self.assertFalse((view / "tenant_0/HeadIndex/head_metaonly.bin").exists())
            self.assertEqual(view, comparison.prepare_view(plan, config))
            self.assertEqual(before, comparison.source_inventory(Path(plan["SourceIndex"])))

    def test_changed_vectors_or_loader_cannot_be_called_same_posting(self):
        for change in ("vectors", "loader"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                plan, config = self.fixture(Path(temporary))
                if change == "vectors":
                    (Path(plan["FlatHeadIndex"]) / "vectors.bin").write_bytes(b"reordered H1")
                else:
                    loader = config.parent / plan["IndexLoader"]
                    loader.write_text(loader.read_text().replace("ReplicaCount=8", "ReplicaCount=4"))
                with self.assertRaises(ValueError):
                    comparison.prepare_view(plan, config)
                self.assertFalse(Path(plan["Index"]).exists())


if __name__ == "__main__":
    unittest.main()
