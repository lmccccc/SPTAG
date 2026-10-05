#!/usr/bin/env python3
"""Focused immutable-input publication checks; removes its own fixture tree."""

import importlib.util
import os
from pathlib import Path
import shutil
import time
import unittest


HERE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("diskann_client_builder", HERE / "build_diskann_client.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class BuildInputGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = HERE / "build" / f"diskann-input-guard-{os.getpid()}-{time.time_ns()}"
        cls.workspace.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.workspace)

    def setUp(self):
        self.directory = self.workspace / self._testMethodName
        self.directory.mkdir()
        self.paths = [self.directory / name for name in
                      ("adapter.cpp", "benchmark.h", "builder.py", "core.a", "compiler")]
        for path in self.paths:
            with path.open("xb") as output:
                output.write(b"original bounded test artifact\n")

    def snapshot(self):
        return [BUILDER.artifact(path) for path in self.paths]

    def test_unchanged_inputs_pass(self):
        inputs = self.snapshot()
        BUILDER.verify_inputs_unchanged(inputs)
        self.assertEqual(inputs, self.snapshot())

    def test_each_changed_input_fails(self):
        for path in self.paths:
            with self.subTest(path=path.name):
                inputs = self.snapshot()
                path.write_bytes(b"changed while compiler was reading\n")
                with self.assertRaisesRegex(RuntimeError, "changed during compilation"):
                    BUILDER.verify_inputs_unchanged(inputs)

    def test_missing_input_fails(self):
        inputs = self.snapshot()
        self.paths[1].unlink()
        with self.assertRaisesRegex(RuntimeError, "disappeared or became unreadable"):
            BUILDER.verify_inputs_unchanged(inputs)

    def test_edit_then_restore_bytes_still_fails(self):
        path = self.paths[1]
        original = path.read_bytes()
        inputs = self.snapshot()
        path.write_bytes(b"intermediate header contents\n")
        path.write_bytes(original)
        os.utime(path, ns=(path.stat().st_atime_ns, inputs[1]["mtime_ns"] + 1))
        self.assertEqual(BUILDER.sha256(path), inputs[1]["sha256"])
        with self.assertRaisesRegex(RuntimeError, "changed during compilation"):
            BUILDER.verify_inputs_unchanged(inputs)

    def test_replaced_same_content_file_fails(self):
        path = self.paths[0]
        original = path.read_bytes()
        inputs = self.snapshot()
        path.rename(path.with_suffix(".previous"))
        with path.open("xb") as output:
            output.write(original)
        self.assertEqual(BUILDER.sha256(path), inputs[0]["sha256"])
        with self.assertRaisesRegex(RuntimeError, "changed during compilation"):
            BUILDER.verify_inputs_unchanged(inputs)

    def test_prior_generations_preserve_all_existing_records(self):
        published = self.directory / "diskannBench"
        suffixes = ("", ".build.json", ".link-provenance.json")
        for suffix in suffixes:
            published.with_name(published.name + suffix).write_bytes(b"published" + suffix.encode())
        digest = BUILDER.sha256(published)
        first = BUILDER.prior_artifacts(published, digest)
        self.assertEqual(len(first), 3)
        for old, saved in first:
            BUILDER.copy_exclusive(old, saved)
        existing = {saved: saved.read_bytes() for _, saved in first}
        second = BUILDER.prior_artifacts(published, digest)
        self.assertEqual(second[0][1].name, published.name + ".prior." + digest + ".2")
        for old, saved in second:
            BUILDER.move_exclusive(old, saved)
        self.assertTrue(all(path.read_bytes() == data for path, data in existing.items()))
        self.assertTrue(all(not old.exists() and saved.is_file() for old, saved in second))

    def test_orphan_prior_sidecar_reserves_generation(self):
        published = self.directory / "diskannBench"
        published.write_bytes(b"published")
        digest = BUILDER.sha256(published)
        prior_record = published.with_name(published.name + ".prior." + digest + ".build.json")
        prior_record.write_bytes(b"retained sidecar")
        entries = BUILDER.prior_artifacts(published, digest)
        self.assertEqual(entries[0][1].name, published.name + ".prior." + digest + ".2")
        self.assertEqual(prior_record.read_bytes(), b"retained sidecar")


if __name__ == "__main__":
    unittest.main(verbosity=2)
