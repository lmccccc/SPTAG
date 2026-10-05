import configparser
import copy
import gzip
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_sift1b_top100 as shared
import run_sift1b_top100_unfiltered as runner


def write_ini(path, data):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_dict(data)
    with Path(path).open("w") as stream:
        parser.write(stream, space_around_delimiters=False)


class UnfilteredTruthTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_official_vecs_headers_shape_and_float_payload(self):
        ids = np.array([[7, 0, 1, 2], [7, 3, 4, 5]], dtype="<i4")
        path = self.root / "ids.ivecs"
        ids.tofile(path)
        with self.assertRaisesRegex(ValueError, "row width"):
            runner.read_official(path, "<i4", 2, 3)
        ids[:, 0] = 3
        ids.tofile(path)
        np.testing.assert_array_equal(runner.read_official(path, "<i4", 2, 3), ids[:, 1:])
        with self.assertRaisesRegex(ValueError, "file size"):
            runner.read_official(path, "<i4", 3, 3)
        distances = ids.view("<f4")
        distances[:, 1:] = [[11, 12, 13], [14, 15, 16]]
        distances.tofile(path)
        np.testing.assert_array_equal(runner.read_official(path, "<f4", 2, 3), distances[:, 1:])

    def test_original_query_bytes_and_order_are_required(self):
        queries = np.arange(12, dtype="u1").reshape(3, 4)
        headers = np.full((3, 1), 4, dtype="<i4").view("u1")
        path = self.root / "query.bvecs.gz"
        with gzip.open(path, "wb") as stream:
            stream.write(np.concatenate((headers, queries), axis=1).tobytes())
        runner.check_bvecs_prefix(path, queries)
        with self.assertRaisesRegex(ValueError, "order or payload"):
            runner.check_bvecs_prefix(path, queries[::-1])
        with gzip.open(path, "wb") as stream:
            stream.write(b"short")
        with self.assertRaisesRegex(ValueError, "Truncated"):
            runner.check_bvecs_prefix(path, queries)

    def test_unfiltered_skips_only_label_admission(self):
        base = np.arange(110, dtype="u1").reshape(110, 1)
        queries = np.zeros((2, 1), dtype="u1")
        ids = np.tile(np.arange(100, dtype="<i4"), (2, 1))
        distances = ids.astype("<f4") ** 2
        attrs = np.ones((110, 2), dtype="<u4")
        self.assertTrue(shared.validate_exact(ids, distances, base, queries, None, None, 100).all())
        with self.assertRaisesRegex(ValueError, "label"):
            shared.validate_exact(ids, distances, base, queries, attrs, 0, 100)
        bad = distances.copy()
        bad[0, 2] += 1
        with self.assertRaisesRegex(ValueError, "squared L2"):
            shared.validate_exact(ids, bad, base, queries, None, None, 100)
        bad_ids = ids.copy()
        bad_ids[0, 2] = bad_ids[0, 1]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            shared.validate_exact(bad_ids, distances, base, queries, None, None, 100)
        bad_ids[0, 2] = 110
        with self.assertRaisesRegex(ValueError, "Invalid result"):
            shared.validate_exact(bad_ids, distances, base, queries, None, None, 100)
        ids[0, 20:] = -1
        distances[0, 20:] = np.finfo("f4").max / np.float32(10)
        self.assertEqual([20, 100],
                         shared.validate_exact(ids, distances, base, queries, None, None, 100).sum(1).tolist())


class UnfilteredBatchTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = SimpleNamespace(root=self.root)
        self.batch_path = self.root / "batch.ini"
        self.points, self.sources = [], {}
        for setting, budget in (("base", "2048"), ("wide", "8192")):
            template = dict(
                Benchmark=dict(Index="/unchanged-index", Queries="/unchanged-queries", ValueType="UInt8",
                               Predicate="categorical", PredicateFile="/unchanged-tag", MaxQueries="1000", Warmup="1000"),
                SearchSSDIndex=dict(MaxCheck=budget, ResultNum="100", NumberOfThreads="1"),
                SearchSweep=dict(NProbe="[100,192,384,768]"))
            path = self.root / (setting + ".original.ini")
            write_ini(path, template)
            self.points.append(dict(engine="SPTAG", scenario="broad_tag", setting=setting, config=str(path)))
            template["Benchmark"].update(Predicate="empty", PredicateFile="")
            template["SearchSweep"]["NProbe"] = "[100,192,384,768,3072]"
            self.sources[setting] = template
            write_ini(self.root / (setting + ".ini"), template)
        self.batch = {"Batch": {"CaseCount": "4", "WarmupPolicy": "once"}}
        for number, (repeat, setting) in enumerate(((1, "base"), (1, "wide"), (2, "wide"), (2, "base")), 1):
            self.batch[f"Case{number}"] = dict(
                Config=str(self.root / (setting + ".ini")),
                OutputDirectory=str(self.root / "native/SPTAG" / f"r{repeat}_{setting}_unfilter"))
        write_ini(self.batch_path, self.batch)

    def read(self):
        return runner.read_cases(self.batch_path, self.config, self.points)

    def test_full_grid_two_reversed_repetitions_and_once_warmup(self):
        cases = self.read()
        self.assertEqual(20, sum(len(c["probes"]) for c in cases))
        self.assertEqual(["base", "wide", "wide", "base"], [c["setting"] for c in cases])
        self.assertEqual([1, 1, 2, 2], [c["repeat"] for c in cases])
        self.batch["Batch"]["WarmupPolicy"] = "per_point"
        write_ini(self.batch_path, self.batch)
        with self.assertRaisesRegex(ValueError, "once-warmup"):
            self.read()

    def test_no_predicate_or_query_or_search_override(self):
        for section, key, value in (("Benchmark", "Predicate", "categorical"),
                                    ("Benchmark", "PredicateFile", "/wrong-tag"),
                                    ("Benchmark", "Queries", "/different-queries"),
                                    ("Benchmark", "MaxQueries", "128"),
                                    ("SearchSSDIndex", "MaxCheck", "32768")):
            changed = copy.deepcopy(self.sources["base"])
            changed[section][key] = value
            write_ini(self.root / "base.ini", changed)
            with self.assertRaisesRegex(ValueError, "Only the predicate"):
                self.read()
        write_ini(self.root / "base.ini", self.sources["base"])
        self.read()

    def test_original_grid_cannot_be_truncated(self):
        self.sources["base"]["SearchSweep"]["NProbe"] = "[100,192,3072]"
        write_ini(self.root / "base.ini", self.sources["base"])
        with self.assertRaisesRegex(ValueError, "truncated"):
            self.read()

    def test_repetition_order_and_foreign_outputs_rejected(self):
        self.batch["Case3"], self.batch["Case4"] = self.batch["Case4"], self.batch["Case3"]
        write_ini(self.batch_path, self.batch)
        with self.assertRaisesRegex(ValueError, "reverse"):
            self.read()
        self.batch["Case1"]["OutputDirectory"] = str(self.root / "foreign/r1_base_unfilter")
        write_ini(self.batch_path, self.batch)
        with self.assertRaisesRegex(ValueError, "native output"):
            self.read()

    def test_existing_native_outputs_are_preserved(self):
        output = Path(self.batch["Case1"]["OutputDirectory"])
        output.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "existing native output"):
            self.read()


class UnfilteredContinuationTest(unittest.TestCase):
    def point(self, engine, scenario, repeat):
        return dict(engine=engine, scenario=scenario, repeat=repeat, setting="base" if engine == "SPTAG" else "L_sweep",
                    search_value=100, recall=.9, qps=100 if repeat == 1 else 50, underfilled_queries=0,
                    max_check=2048, posting_additional_max_check=2048, posting_navigation_width=8)

    def test_append_preserves_parent_and_both_repetitions(self):
        original = [self.point("SPTAG", "broad_tag", repeat) for repeat in (1, 2)]
        fresh = [self.point(engine, "unfilter", repeat) for engine in shared.VERSIONS for repeat in (1, 2)]
        before = copy.deepcopy(original)
        points, rows = runner.combine(original, fresh)
        self.assertEqual(before, original)
        self.assertEqual(before, points[:2])
        self.assertEqual(4, len(rows))
        self.assertTrue(all(r["qps"] == 75 and r["qps_min"] == 50 and r["qps_max"] == 100 for r in rows))
        self.assertTrue(rows[0]["joint_frontier"])
        fresh[-1]["scenario"] = "broad_tag"
        with self.assertRaisesRegex(ValueError, "only the three unfiltered"):
            runner.combine(original, fresh)

    def test_original_source_snapshot_and_parent_artifacts_authenticated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "parent"
            parent.mkdir()
            (root / "provenance").mkdir()
            source = str(Path(shared.__file__).resolve())
            snapshot = root / "provenance/before.py"
            snapshot.write_text("original frozen evaluator\n")
            record = dict(identity=shared.identity(source), sha256=sha256_file(snapshot))
            shared.save(parent / "registration.json", dict(files={source: record}, input_identity={}, indexes={}))
            shared.save(parent / "completion.json", dict(
                exit_code=0, aggregate_points=108, combined_measurements=216,
                artifacts={"registration.json": sha256_file(parent / "registration.json")}))
            shared.save(root / "provenance/source-transition.json", dict(
                source=source, original=record, snapshot=shared.identity(snapshot),
                snapshot_sha256=sha256_file(snapshot)))
            proof = runner.transition_parent(root, parent)
            self.assertEqual(sha256_file(source), proof["unfiltered_source_transition"]["current_sha256"])
            self.assertIn(str(snapshot), proof["files"])
            snapshot.write_text("modified snapshot\n")
            with self.assertRaisesRegex(ValueError, "unauthenticated"):
                runner.transition_parent(root, parent)
            (parent / "registration.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                runner.transition_parent(root, parent)


sha256_file = shared.sha256_file


if __name__ == "__main__":
    unittest.main()
