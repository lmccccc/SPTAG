import copy
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

import ssd_label_distribution as distribution
import update_results as store
from test_update_results import catalog, measurement


HERE = Path(__file__).resolve().parent


class DistributionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="sptag-label-build-")
        cls.addClassCleanup(cls.build.cleanup)
        cls.binary = Path(cls.build.name) / "scanner"
        subprocess.run(["g++", "-O3", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        str(HERE / "ssd_label_distribution_scan.cpp"), "-o", str(cls.binary)], check=True)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sptag-label-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.tenant = self.root / "tenant_0"
        self.tenant.mkdir()
        self.ssd, self.support = (self.tenant / name for name in ("SPTAGFullList.bin", "limited_tag_support.bin"))
        self.scan = self.root / "scan"
        self.report = self.root / "report"
        self.data = self.root / "registry"
        self.write_fixture()
        (self.tenant / "indexloader.ini").write_text(
            "[Base]\nValueType=UInt8\nDim=128\nSSDIndex=SPTAGFullList.bin\nSSDIndexFileNum=1\n"
            "[BuildSSDIndex]\nStorage=STATIC\nEnableDataCompression=false\nEnableDeltaEncoding=false\n"
            "EnablePostingListRearrange=false\nEnableHybridDistance=false\nEnableLimitedTagPosting=true\n"
            "LimitedTagColumn=0\nNumTagsPerVec=2\nColumnTypes=categorical,numeric\n"
            "LimitedTagSupportFile=limited_tag_support.bin\nLimitedTagGenerationFingerprint=123\n")

    def write_fixture(self, groups=None):
        groups = groups if groups is not None else [
            ([0, 0, 1], [0, 1, 2]), ([1], [1, 2]), ([], [2, 2]), ([], [])]
        contents = bytearray(4096)
        contents[:44] = struct.pack("<8IQI", 0x314D5453, 3, len(groups), 10, 128, 140, 2, 0, 123, 1)
        for i in reversed(range(len(groups))):
            h, o = groups[i]
            count = len(h) + len(o)
            page = len(contents) // 4096 - 1 if count else 0
            pages = (count * 140 + 4095) // 4096
            contents[44 + 16 * i:60 + 16 * i] = struct.pack("<IHIHI", page, 0, count, pages, len(h))
            start = len(contents)
            contents.extend(bytes(pages * 4096))
            for j, label in enumerate(h + o):
                pos = start + j * 140
                contents[pos:pos + 12] = struct.pack("<3I", j % 10, label, 1234)
        self.ssd.write_bytes(contents)
        header = struct.pack("<10I5Q", 0x3153544C, 5, 80, len(groups), 2, 0, 16, 4, 0, 2, 10, 123, 0, 0, 0)
        labels = b"".join(struct.pack("<IIQ", label, 0, count) for label, count in enumerate((3, 3, 3, 1)))
        self.support.write_bytes(header + bytes(len(groups) * 4 * 4) + labels + bytes(8 * (len(groups) + 1)))

    def invoke(self, reserve="0"):
        return subprocess.run([str(self.binary), str(self.ssd), str(self.support), reserve],
                              capture_output=True, text=True)

    def make_report(self):
        distribution.scan(self.tenant, self.binary, self.scan, "fixture-index", 0)
        return distribution.export_report(self.scan, self.report, "SIFT1B")

    def test_exact_conditional_means_and_histograms(self):
        run = self.invoke()
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        distribution.validate(result)
        self.assertEqual(result["directory_entries"], 4)
        self.assertEqual(result["empty_directory_entries"], 1)
        physical = result["groups"]["physical_H_plus_O"]
        self.assertEqual(physical["nonempty_postings"], 3)
        self.assertEqual(physical["stored_records"], 11)
        self.assertEqual([r["postings_with_label"] for r in physical["labels"]], [1, 2, 3, 0])
        for label in physical["labels"][:3]:
            self.assertAlmostEqual(label["mean_proportion"], .5)
        self.assertIsNone(physical["labels"][3]["mean_proportion"])
        self.assertEqual(physical["labels"][0]["histogram_counts"][50], 1)
        self.assertEqual(physical["labels"][2]["histogram_counts"][99], 1)
        self.assertAlmostEqual(result["groups"]["H_prefix"]["labels"][1]["mean_proportion"], 2 / 3)
        self.assertAlmostEqual(result["groups"]["O_suffix"]["labels"][1]["mean_proportion"], 5 / 12)

    def test_exact_bin_edges_and_empty_region(self):
        self.write_fixture([([0] * 99 + [1], []), ([2], [])])
        run = self.invoke()
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        distribution.validate(result)
        labels = result["groups"]["physical_H_plus_O"]["labels"]
        self.assertEqual(labels[0]["histogram_counts"][99], 1)
        self.assertEqual(labels[0]["pure_postings"], 0)
        self.assertEqual(labels[1]["histogram_counts"][1], 1)
        self.assertEqual(labels[2]["pure_postings"], 1)
        self.assertEqual(result["groups"]["O_suffix"]["stored_records"], 0)

    def test_scanner_rejects_invalid_format_and_extents(self):
        for fault in ("generation", "label", "overlap", "h-count", "truncated", "schema"):
            with self.subTest(fault=fault):
                self.write_fixture()
                if fault == "generation":
                    data = bytearray(self.support.read_bytes())
                    struct.pack_into("<Q", data, 48, 124)
                    self.support.write_bytes(data)
                else:
                    data = bytearray(self.ssd.read_bytes())
                    if fault == "label":
                        struct.pack_into("<I", data, 4096 + 4, 999)
                    elif fault == "overlap":
                        data[44:48] = data[60:64]
                    elif fault == "h-count":
                        struct.pack_into("<I", data, 44 + 12, 999)
                    elif fault == "truncated":
                        data = data[:-4096]
                    elif fault == "schema":
                        struct.pack_into("<I", data, 16, 64)
                    self.ssd.write_bytes(data)
                run = self.invoke()
                self.assertNotEqual(run.returncode, 0)
                self.assertEqual(run.stdout, "")

    def test_resource_reserve_is_explicit(self):
        for value in ("-1", "invalid", "17592186044415"):
            run = self.invoke(value)
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual(run.stdout, "")

    def test_failed_scan_has_failure_not_completion(self):
        data = bytearray(self.support.read_bytes())
        struct.pack_into("<Q", data, 48, 124)
        self.support.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "Scanner exited"):
            distribution.scan(self.tenant, self.binary, self.scan, "fixture-index", 0)
        self.assertTrue((self.scan / "failure.json").exists())
        self.assertFalse((self.scan / "completion.json").exists())

    def test_unsupported_saved_mode_is_rejected(self):
        ini = self.tenant / "indexloader.ini"
        ini.write_text(ini.read_text().replace("EnableDataCompression=false", "EnableDataCompression=true"))
        with self.assertRaisesRegex(ValueError, "unsupported"):
            distribution.scan(self.tenant, self.binary, self.scan, "fixture-index", 0)
        self.assertFalse(self.scan.exists())

    def test_export_requires_unchanged_sources(self):
        distribution.scan(self.tenant, self.binary, self.scan, "fixture-index", 0)
        ini = self.tenant / "indexloader.ini"
        ini.write_text(ini.read_text() + "; changed after scan\n")
        with self.assertRaisesRegex(ValueError, "input changed"):
            distribution.export_report(self.scan, self.report, "SIFT1B")
        self.assertFalse(self.report.exists())

    def test_transfer_needs_no_original_paths_and_preserves_results(self):
        result = self.make_report()
        self.assertNotIn(str(self.root), (self.report / distribution.OUTPUT).read_text())
        self.tenant.rename(self.root / "hidden-tenant")
        self.scan.rename(self.root / "hidden-scan")
        moved = self.root / "transferred"
        self.report.rename(moved)
        before = store.update(self.data, [catalog(), measurement()])
        prefix = (self.data / "history.jsonl").read_bytes()
        receipt = distribution.publish(moved, self.data, 1)
        after = store.update(self.data, check=True)
        self.assertEqual(before["points"], after["points"])
        self.assertEqual(before["coverage"], after["coverage"])
        self.assertTrue((self.data / "history.jsonl").read_bytes().startswith(prefix))
        table = next(t for t in after["tables"] if t["event_id"] == receipt["event_id"])
        self.assertEqual(len(table["rows"]), 12)
        self.assertEqual(table["source"]["distribution"], result["distribution"])
        for name in distribution.GROUPS:
            rows = [{k: v for k, v in row.items() if k != "group"} for row in table["rows"] if row["group"] == name]
            self.assertEqual(rows, result["groups"][name]["labels"])
        history, current = ((self.data / name).read_bytes() for name in ("history.jsonl", "current.json"))
        distribution.publish(moved, self.data, 1)
        self.assertEqual(history, (self.data / "history.jsonl").read_bytes())
        self.assertEqual(current, (self.data / "current.json").read_bytes())

    def test_new_registry_and_cli_check(self):
        script = str(HERE / "ssd_label_distribution.py")
        subprocess.run([sys.executable, "-B", script, "scan", "--tenant-dir", str(self.tenant),
                        "--binary", str(self.binary), "--output-dir", str(self.scan),
                        "--index-version", "fixture-index", "--min-available-mib", "0"],
                       check=True, capture_output=True, text=True, cwd=self.root)
        for arguments in (
            ["export", "--scan-dir", str(self.scan), "--dataset", "SIFT1B", "--output-dir", str(self.report)],
            ["check", "--report-dir", str(self.report)],
        ):
            subprocess.run([sys.executable, "-B", "-S", script, *arguments],
                           check=True, capture_output=True, text=True, cwd=self.root)
        run = subprocess.run([
            sys.executable, "-B", "-S", script, "publish", "--report-dir", str(self.report),
            "--data-dir", str(self.data), "--version-order", "1"],
            check=True, capture_output=True, text=True, cwd=self.root)
        receipt = json.loads(run.stdout)
        self.assertEqual(receipt["rows"], 12)
        self.assertEqual(store.update(self.data, check=True)["points"], [])
        with self.assertRaises(FileExistsError):
            distribution.scan(self.tenant, self.binary, self.scan, "fixture-index", 0)
        with self.assertRaises(FileExistsError):
            distribution.export_report(self.scan, self.report, "SIFT1B")

    def test_corrupt_seal_or_histogram_never_publishes(self):
        original = self.make_report()
        seal = distribution.read_json(self.report / "completion.json")
        for fault in ("digest", "bins", "mean", "generation", "absent", "failure"):
            with self.subTest(fault=fault):
                result = copy.deepcopy(original)
                if fault == "bins":
                    result["groups"]["physical_H_plus_O"]["labels"][0]["histogram_counts"].append(0)
                elif fault == "mean":
                    result["groups"]["physical_H_plus_O"]["labels"][0]["mean_proportion"] = 2
                elif fault == "generation":
                    result["generation_fingerprint"] = "0"
                elif fault == "absent":
                    result["groups"]["physical_H_plus_O"]["labels"][3]["min_proportion"] = 0
                elif fault == "failure":
                    store.atomic_json(self.report / "failure.json", {"state": "failed"})
                store.atomic_json(self.report / distribution.OUTPUT, result)
                store.atomic_json(self.report / "completion.json", {
                    **seal, "sha256": "0" * 64 if fault == "digest" else store.file_sha(self.report / distribution.OUTPUT)})
                with self.assertRaises(ValueError):
                    distribution.publish(self.report, self.data, 1)
                self.assertFalse((self.data / "history.jsonl").exists())

    def test_selected_index_mismatch_fails_before_append(self):
        self.make_report()
        event = catalog()
        event["catalog"]["selected_indexes"] = [{"dataset": "SIFT1B", "algorithm": "SPTAG", "index": "another-index"}]
        store.update(self.data, [event])
        history = (self.data / "history.jsonl").read_bytes()
        with self.assertRaisesRegex(ValueError, "selected physical index"):
            distribution.publish(self.report, self.data, 1)
        self.assertEqual(history, (self.data / "history.jsonl").read_bytes())

    def test_version_replacement_preserves_unrelated_table(self):
        result = self.make_report()
        distribution.publish(self.report, self.data, 1)
        other = distribution.table_event(result, distribution.read_json(self.report / "completion.json"), 1)
        other.update(variant="unrelated", table_name="unrelated")
        before = store.update(self.data, [other])
        unrelated = next(t for t in before["tables"] if t["table_name"] == "unrelated")
        result["index_version"] = "fixture-index-v2"
        store.atomic_json(self.report / distribution.OUTPUT, result)
        seal = distribution.read_json(self.report / "completion.json")
        store.atomic_json(self.report / "completion.json", {
            **seal, "sha256": store.file_sha(self.report / distribution.OUTPUT)})
        history = (self.data / "history.jsonl").read_bytes()
        with self.assertRaisesRegex(ValueError, "Conflicting version"):
            distribution.publish(self.report, self.data, 1)
        self.assertEqual(history, (self.data / "history.jsonl").read_bytes())
        distribution.publish(self.report, self.data, 2)
        after = store.update(self.data, check=True)
        self.assertIn(unrelated, after["tables"])
        self.assertEqual(next(t for t in after["tables"] if t["table_name"] == distribution.TABLE)
                         ["version"]["index"], "fixture-index-v2")


if __name__ == "__main__":
    unittest.main()
