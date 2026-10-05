import copy
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_sift1b_top100 as runner


class Top100ValidationTest(unittest.TestCase):
    def setUp(self):
        self.base = np.arange(120, dtype=np.uint8).reshape(120, 1)
        self.queries = np.zeros((3, 1), dtype=np.uint8)
        self.attrs = np.zeros((120, 2), dtype=np.uint32)
        self.ids = np.tile(np.arange(100, dtype=np.int32), (3, 1))
        self.distances = self.ids.astype(np.float32) ** 2

    def validate(self, ids=None, distances=None):
        return runner.validate_exact(self.ids if ids is None else ids,
                                     self.distances if distances is None else distances,
                                     self.base, self.queries, self.attrs, 0, 100)

    def test_exact_top100_and_explicit_underfill(self):
        self.assertTrue(self.validate().all())
        self.ids[1, 50:] = -1
        self.distances[1, 50:] = np.finfo(np.float32).max / np.float32(10)
        self.assertEqual([100, 50, 100], self.validate().sum(axis=1).tolist())

    def test_top10_payload_is_not_top100(self):
        with self.assertRaisesRegex(ValueError, "width"):
            self.validate(self.ids[:, :10], self.distances[:, :10])

    def test_duplicates_wrong_label_and_non_suffix_underfill(self):
        wrong = self.ids.copy()
        wrong[0, 2] = wrong[0, 1]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.validate(wrong)
        self.attrs[10, 0] = 1
        with self.assertRaisesRegex(ValueError, "label"):
            self.validate()
        self.attrs[10, 0] = 0
        wrong = self.ids.copy()
        wrong[0, 1] = -1
        with self.assertRaisesRegex(ValueError, "suffix"):
            self.validate(wrong)

    def test_exact_distance_mismatch_rejected(self):
        wrong = self.distances.copy()
        wrong[1, 20] += 1
        with self.assertRaisesRegex(ValueError, "squared L2"):
            self.validate(distances=wrong)

    def test_pareto_frontiers_pool_profiles_not_scenarios(self):
        points = []
        for engine, scenario, setting, recall, qps in (
                ("SPTAG", "sel_01pct", "base", .90, 200),
                ("SPTAG", "sel_01pct", "wide", .91, 220),
                ("Filtered_DiskANN", "sel_01pct", "L_sweep", .95, 150),
                ("PipeANN", "broad_tag", "L_sweep", .99, 1000)):
            for repeat in (1, 2):
                points.append(dict(engine=engine, scenario=scenario, setting=setting, search_value=192,
                                   recall=recall, qps=qps, repeat=repeat, underfilled_queries=0))
        rows = runner.aggregate(points)
        self.assertFalse(rows[0]["algorithm_frontier"])
        self.assertFalse(rows[0]["joint_frontier"])
        self.assertTrue(all(row["joint_frontier"] for row in rows[1:]))
        bad = copy.deepcopy(points)
        bad[-1]["repeat"] = 1
        with self.assertRaisesRegex(ValueError, "repetition"):
            runner.aggregate(bad)


class Top100RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = SimpleNamespace(root=self.root)
        self.proof = dict(files={}, input_identity={}, indexes={}, warmup_policy="once")
        for source in (Path(runner.__file__).resolve(), runner.POSTFILTER / "selectivity_common.py"):
            self.proof["files"][str(source)] = dict(identity=runner.identity(source),
                                                   sha256=runner.sha256_file(source))
        runner.save(self.root / "registration.json", self.proof)
        runner.save(self.root / "failure.json", {"error": "baseline startup"})
        runner.save(self.root / "status.json", {"state": "failed"})
        (self.root / "logs").mkdir()
        (self.root / "logs/Filtered_DiskANN.log").write_text("OMP startup failure\n")
        runner.save(self.root / "Filtered_DiskANN.execution.json", {"pid": 123})
        runner.save(self.root / "SPTAG.raw.json", [])

    def test_resume_preserves_registration_and_completed_results(self):
        paths = [self.root / name for name in ("registration.json", "SPTAG.raw.json")]
        before = {path: runner.sha256_file(path) for path in paths}
        proof = runner.prepare_resume(self.config)
        self.assertEqual(["SPTAG"], proof["continuation"]["completed_engines"])
        runner.verify(proof)
        self.assertEqual(before, {path: runner.sha256_file(path) for path in paths})
        self.assertFalse((self.root / "failure.json").exists())
        self.assertTrue((self.root / "recovery/initial/failure.json").is_file())
        self.assertTrue((self.root / "recovery/initial/Filtered_DiskANN.log").is_file())

    def test_partial_baseline_outputs_are_not_overwritten(self):
        (self.root / "native/Filtered_DiskANN/single").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "partial native output"):
            runner.prepare_resume(self.config)
        self.assertTrue((self.root / "failure.json").is_file())

    def test_unarchived_launcher_change_is_rejected(self):
        self.proof["files"][str(Path(runner.__file__).resolve())]["sha256"] = "changed"
        runner.write_json(self.root / "registration.json", self.proof)
        with self.assertRaisesRegex(ValueError, "Missing original launcher snapshot"):
            runner.prepare_resume(self.config)

    def test_original_launcher_snapshot_is_authenticated(self):
        source = str(Path(runner.__file__).resolve())
        snapshot = self.root / "old-launcher.py"
        snapshot.write_text("original launcher\n")
        old_hash = runner.sha256_file(snapshot)
        self.proof["files"][source]["sha256"] = old_hash
        runner.write_json(self.root / "registration.json", self.proof)
        (self.root / "recovery").mkdir()
        runner.save(self.root / "recovery/source-snapshots.json", dict(
            original_registration_sha256=runner.sha256_file(self.root / "registration.json"),
            sources={source: dict(snapshot=str(snapshot), snapshot_identity=runner.identity(snapshot),
                                  sha256=old_hash)}))
        proof = runner.prepare_resume(self.config)
        runner.verify(proof)
        self.assertEqual(old_hash, proof["continuation"]["launcher_updates"][source]["original_sha256"])
        snapshot.write_text("changed snapshot\n")
        with self.assertRaisesRegex(ValueError, "Frozen file"):
            runner.verify(proof)

    def test_other_frozen_file_changes_are_rejected(self):
        protected = self.root / "index.bin"
        protected.write_bytes(b"index")
        self.proof["files"][str(protected)] = dict(identity=runner.identity(protected),
                                                  sha256=runner.sha256_file(protected))
        runner.write_json(self.root / "registration.json", self.proof)
        protected.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Frozen file"):
            runner.prepare_resume(self.config)
        self.assertTrue((self.root / "failure.json").is_file())

    def test_completed_spann_is_not_invoked_again(self):
        points = {
            engine: [dict(engine=engine, scenario="broad_tag", setting="base", search_value=100,
                          recall=.9, qps=100, repeat=repeat, underfilled_queries=0)
                     for repeat in (1, 2)]
            for engine in runner.VERSIONS
        }
        runner.write_json(self.root / "SPTAG.raw.json", points["SPTAG"])
        runner.save(self.root / "SPTAG.execution.json", {"pid": 456})
        (self.root / "plots").mkdir()
        (self.root / "recovery").mkdir()
        runner.save(self.root / "recovery/registration.json", self.proof)
        with mock.patch.object(runner, "prepare_resume", return_value=self.proof), \
                mock.patch.object(runner, "invoke", return_value=789) as invoke, \
                mock.patch.object(runner, "analyze_engine",
                                  side_effect=lambda config, proof, engine, pid: points[engine]), \
                mock.patch.object(runner, "verify"), mock.patch.object(runner.subprocess, "run"):
            runner.run(self.config, resume=True)
        self.assertEqual(["Filtered_DiskANN", "PipeANN"], [call.args[2] for call in invoke.call_args_list])
        self.assertEqual(points["SPTAG"], runner.read_json(self.root / "SPTAG.raw.json"))

    def test_failure_status_is_explicit_and_prior_failure_is_preserved(self):
        old = (self.root / "failure.json").read_bytes()
        with mock.patch.object(sys, "argv", ["runner", "--config", "unused"]), \
                mock.patch.object(runner.shared, "Profile", return_value=self.config), \
                mock.patch.object(runner, "run", side_effect=ValueError("new failure")):
            with self.assertRaisesRegex(ValueError, "new failure"):
                runner.main()
        self.assertEqual(old, (self.root / "failure.json").read_bytes())
        status = runner.read_json(self.root / "status.json")
        self.assertEqual("failed", status["state"])
        self.assertEqual("new failure", runner.read_json(status["failure_report"])["error"])


class RuntimeConfinementTest(unittest.TestCase):
    def test_baseline_shm_exception_preserves_dataset_write_protection(self):
        code = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from selectivity_common import confine_outputs
root, protected, shm = map(Path, sys.argv[2:5])
enabled = sys.argv[5] == 'true'
confine_outputs(root, allow_shared_memory=enabled)
(root / 'allowed').write_bytes(b'output')
assert protected.read_bytes() == b'original'
try:
    protected.write_bytes(b'changed')
except PermissionError:
    pass
else:
    raise AssertionError('protected data became writable')
try:
    fd = os.open(shm, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
except PermissionError:
    assert not enabled
else:
    assert enabled
    os.ftruncate(fd, 4096)
    os.write(fd, b'runtime')
    os.close(fd)
    shm.unlink()
try:
    shm.mkdir()
except PermissionError:
    pass
else:
    raise AssertionError('shared-memory directory creation became allowed')
print('confinement preserved')
"""
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "output"
            root.mkdir()
            protected = parent / "index"
            protected.write_bytes(b"original")
            shm = Path("/dev/shm") / ("sptag-runtime-test-" + uuid.uuid4().hex)
            try:
                for enabled in ("false", "true"):
                    result = subprocess.run(
                        [sys.executable, "-B", "-c", code, str(runner.POSTFILTER), str(root),
                         str(protected), str(shm), enabled], text=True, capture_output=True)
                    self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                    self.assertIn("confinement preserved", result.stdout)
                    self.assertEqual(b"original", protected.read_bytes())
            finally:
                if shm.is_file():
                    shm.unlink()
                elif shm.is_dir():
                    shm.rmdir()


if __name__ == "__main__":
    unittest.main()
