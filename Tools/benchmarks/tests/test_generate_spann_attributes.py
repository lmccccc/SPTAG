"""INI-only attribute preparation, using tiny disposable repository fixtures."""

from contextlib import redirect_stderr, redirect_stdout
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import unittest
from unittest import mock
import uuid

import numpy as np


BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS))
import gen_sift1b_attrs as legacy
import generate_spann_attributes as generator
from extreme_sparse_policy import coverage_boundary_count
from native_input_io import open_attributes


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def historical_payload(count, cardinality=5, exponent=1.0, rare=3,
                       seed=20260817, numeric_seed=20260821):
    weights = np.arange(1, cardinality + 1, dtype=np.float64) ** -exponent
    ideal = weights / weights.sum() * (count - rare)
    regular = np.floor(ideal).astype(np.int64)
    for index in np.argsort(-(ideal - regular))[:count - rare - int(regular.sum())]:
        regular[index] += 1
    counts = np.concatenate((regular, np.asarray([rare], dtype=np.int64)))
    multiplier = (2654435761 ^ seed) % count or 1
    if multiplier % 2 == 0:
        multiplier += 1
    while math.gcd(multiplier, count) != 1:
        multiplier += 2
        if multiplier >= count:
            multiplier = 1
    ids = np.arange(count, dtype=np.uint64)
    ranks = (ids * np.uint64(multiplier) + np.uint64(seed % count)) % np.uint64(count)
    attributes = np.empty((count, 2), dtype="<u4")
    attributes[:, 0] = np.searchsorted(np.cumsum(counts), ranks, side="right")
    attributes[:, 1] = (ids * np.uint64(2654435761) + np.uint64(numeric_seed)).astype(np.uint32)
    return attributes, counts


class AttributeGenerationTest(unittest.TestCase):
    def setUp(self):
        directory = Path("build") / f"spann_attribute_test_{uuid.uuid4().hex}"
        directory.mkdir(parents=True)
        self.root = directory.resolve()
        self.addCleanup(shutil.rmtree, directory)
        self.sequence = 0

    def fixture(self, *, value_type="Float", dimension=7, rows=97, chunk=13,
                filename="arbitrary.attributes", changes=None):
        self.sequence += 1
        prefix = self.root / f"case{self.sequence}"
        prefix.mkdir()
        base_path = prefix / "arbitrary.vector-payload"
        dtype = {"Float": "<f4", "Int8": "i1", "UInt8": "u1", "Int16": "<i2"}[value_type]
        values = np.arange(rows * dimension).astype(dtype).reshape(rows, dimension)
        base_path.write_bytes(struct.pack("<ii", rows, dimension) + values.tobytes())
        tag_path = prefix / "generated" / filename
        config = {
            "Base": {
                "VectorPath": str(base_path), "ValueType": value_type,
                "VectorType": "DEFAULT", "Dim": str(dimension),
                "QueryPath": str(prefix / "not-required.query"),
            },
            "Tags": {"TagFile": str(tag_path), "ColumnTypes": "categorical,numeric"},
            "SelectHead": {"Ratio": "0.5"},
            "BuildSSDIndex": {
                "EnableLimitedTagPosting": "true",
                "LimitedTagColumn": "0", "LimitedTagSlotsPerHead": "2",
            },
            "SearchSSDIndex": {"InternalResultNum": "4"},
            "AttributeGeneration": {
                "Cardinality": "5", "ZipfExponent": "1.0", "Seed": "20260817",
                "NumericSeed": "20260821", "ChunkRows": str(chunk),
            },
        }
        for section, updates in (changes or {}).items():
            for key, value in updates.items():
                if value is None:
                    config[section].pop(key, None)
                else:
                    config.setdefault(section, {})[key] = str(value)
        config_path = prefix / "copied-build.ini"
        config_path.write_text(
            "; tiny native preparation fixture\n" + "\n".join(
                f"[{section}]\n" + "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
                for section, values in config.items()
            ), encoding="utf-8",
        )
        return config_path, base_path, tag_path

    def generate(self, config):
        with redirect_stdout(io.StringIO()):
            return generator.generate_attributes(config)

    def test_raw_npy_and_counts_match_historical_recipe_at_every_chunk_size(self):
        expected, counts = historical_payload(97)
        reference_npy = self.root / "reference.npy"
        mapped = np.lib.format.open_memmap(reference_npy, mode="w+", dtype="<u4", shape=expected.shape)
        mapped[:] = expected
        mapped.flush()
        del mapped
        for chunk in (1, 11, 97, 1000):
            with self.subTest(chunk=chunk):
                config, base, tag_path = self.fixture(chunk=chunk)
                original = digest(base)
                manifest = self.generate(config)
                self.assertEqual(tag_path.read_bytes(), expected.tobytes())
                self.assertEqual(tag_path.with_suffix(".npy").read_bytes(), reference_npy.read_bytes())
                self.assertEqual(digest(base), original)
                data = open_attributes(tag_path, 97, 2)
                np.testing.assert_array_equal(np.bincount(data[:, 0]), counts)
                del data
                self.assertEqual(manifest["extreme_tag_count"], 3)

    def test_manifest_identities_schema_and_complete_count_table(self):
        config, base, tag_path = self.fixture()
        manifest = self.generate(config)
        self.assertEqual(json.loads(tag_path.with_suffix(".manifest.json").read_text()), manifest)
        self.assertEqual(manifest["vector_count"], 97)
        self.assertEqual(manifest["source_base_file"], str(base.resolve()))
        self.assertEqual(manifest["source_vector_count"], 97)
        self.assertEqual(manifest["value_type"], "Float")
        self.assertEqual(manifest["dimension"], 7)
        self.assertEqual(manifest["column_types"], ["categorical", "numeric"])
        self.assertEqual(manifest["native_config"]["path"], str(config.resolve()))
        self.assertEqual(manifest["native_config"]["sha256"], digest(config))
        self.assertEqual(manifest["attribute_generation"]["chunk_rows"], 13)
        expected, expected_counts = historical_payload(97)
        for key, output in (
            ("sptag_attributes", tag_path),
            ("numpy_attributes", tag_path.with_suffix(".npy")),
            ("counts", tag_path.with_suffix(".counts.tsv")),
        ):
            record = manifest["files"][key]
            self.assertEqual(record["path"], str(output))
            self.assertEqual(record["sha256"], digest(output))
            self.assertEqual(record["bytes"], output.stat().st_size)
        with tag_path.with_suffix(".counts.tsv").open() as stream:
            records = list(csv.DictReader(stream, delimiter="\t"))
        self.assertEqual([int(row["attribute_id"]) for row in records], list(range(6)))
        self.assertEqual([int(row["count"]) for row in records], expected_counts.tolist())
        self.assertEqual(sum(int(row["count"]) for row in records), 97)
        self.assertEqual([row["class"] for row in records], ["zipf"] * 5 + ["extreme"])
        for row in records:
            self.assertAlmostEqual(float(row["selectivity"]), int(row["count"]) / 97, places=11)
        self.assertEqual(manifest["numeric_generation"]["min"], int(expected[:, 1].min()))
        self.assertEqual(manifest["numeric_generation"]["max"], int(expected[:, 1].max()))
        self.assertEqual(len(list(tag_path.parent.iterdir())), 4)

    def test_every_native_type_dimension_and_selected_prefix(self):
        for value_type, dimension in (("Float", 2), ("Int8", 5), ("UInt8", 1), ("Int16", 11)):
            with self.subTest(value_type=value_type):
                config, base, tag_path = self.fixture(
                    value_type=value_type, dimension=dimension, rows=101,
                    changes={"Base": {"VectorSize": "67", "Dim": None}},
                )
                original = base.read_bytes()
                manifest = self.generate(config)
                expected, _ = historical_payload(67)
                self.assertEqual(tag_path.read_bytes(), expected.tobytes())
                self.assertEqual(manifest["source_vector_count"], 101)
                self.assertEqual(manifest["vector_count"], 67)
                self.assertEqual(manifest["dimension"], dimension)
                self.assertEqual(manifest["value_type"], value_type)
                self.assertEqual(base.read_bytes(), original)

    def test_nondefault_recipe_parameters_are_taken_only_from_ini(self):
        expected, expected_counts = historical_payload(
            137, cardinality=7, exponent=0.65, seed=42, numeric_seed=99,
        )
        for chunk in (3, 71):
            with self.subTest(chunk=chunk):
                config, _, tag = self.fixture(rows=137, chunk=chunk, changes={
                    "AttributeGeneration": {
                        "Cardinality": 7, "ZipfExponent": "0.65", "Seed": 42, "NumericSeed": 99,
                    },
                })
                manifest = self.generate(config)
                self.assertEqual(tag.read_bytes(), expected.tobytes())
                np.testing.assert_array_equal(np.bincount(expected[:, 0]), expected_counts)
                self.assertEqual(manifest["extreme_tag_id"], 7)
                self.assertEqual(manifest["assignment_permutation"]["seed"], 42)
                self.assertEqual(manifest["numeric_generation"]["seed"], 99)

    def test_legacy_cli_and_payload_remain_compatible(self):
        prefix = "sift1b_zipf5_sparse3_numeric"
        config, base, tag_path = self.fixture(
            value_type="UInt8", dimension=128, filename=f"{prefix}_attrs.u32",
        )
        self.generate(config)
        old_output = self.root / "legacy-output"
        args = [
            "gen_sift1b_attrs.py", str(self.root), "--base-file", str(base),
            "--output-dir", str(old_output), "--config", str(config),
            "--attribute-cardinality", "5", "--chunk-size", "11",
        ]
        with mock.patch.object(sys, "argv", args), redirect_stdout(io.StringIO()):
            legacy.main()
        self.assertEqual(tag_path.read_bytes(), (old_output / f"{prefix}_attrs.u32").read_bytes())
        self.assertEqual(
            tag_path.with_suffix(".npy").read_bytes(), (old_output / f"{prefix}_attrs.npy").read_bytes(),
        )
        self.assertEqual(
            tag_path.with_suffix(".counts.tsv").read_bytes(), (old_output / f"{prefix}_counts.tsv").read_bytes(),
        )

    def test_schema_is_explicit_and_never_reordered_or_filled(self):
        for schema in ("numeric,categorical", "categorical", "numeric",
                       "categorical,numeric,categorical", "", None):
            with self.subTest(schema=schema):
                config, _, tag = self.fixture(changes={"Tags": {"ColumnTypes": schema}})
                with self.assertRaises(ValueError):
                    self.generate(config)
                self.assertFalse(tag.parent.exists())
        config, _, tag = self.fixture(changes={"Tags": {
            "ColumnTypes": "CaTe, NuM", "NumTagsPerVec": "2",
        }})
        self.generate(config)
        self.assertTrue(tag.is_file())
        config, _, _ = self.fixture(changes={"Tags": {"NumTagsPerVec": "3"}})
        with self.assertRaisesRegex(ValueError, "NumTagsPerVec"):
            self.generate(config)

    def test_generation_requires_explicit_settings_not_machine_or_environment_defaults(self):
        for key in ("Cardinality", "ZipfExponent", "Seed", "NumericSeed", "ChunkRows"):
            with self.subTest(missing=key):
                config, _, tag = self.fixture(changes={"AttributeGeneration": {key: None}})
                with self.assertRaisesRegex(ValueError, "Missing explicit"):
                    self.generate(config)
                self.assertFalse(tag.parent.exists())
        config, _, tag = self.fixture()
        with mock.patch.dict(os.environ, {
            "SIFT1B_ROOT": str(self.root / "must-not-be-used"),
            "SPTAG_TAG_OFFSET": "100", "SPTAG_VECTOR_COUNT": "1",
            "SPTAG_LIMITED_TAG_SLOTS_PER_HEAD": "900", "NUMERIC_SEED": "1",
        }, clear=True):
            self.generate(config)
        self.assertEqual(tag.read_bytes(), historical_payload(97)[0].tobytes())
        self.assertFalse((self.root / "must-not-be-used").exists())

    def test_invalid_settings_and_insufficient_counts_fail_before_writing(self):
        changes = (
            {"AttributeGeneration": {"Cardinality": 0}},
            {"AttributeGeneration": {"Cardinality": 256}},
            {"AttributeGeneration": {"ZipfExponent": "nan"}},
            {"AttributeGeneration": {"ZipfExponent": 0}},
            {"AttributeGeneration": {"ChunkRows": 0}},
            {"AttributeGeneration": {"Seed": -1}},
            {"AttributeGeneration": {"NumericSeed": 2**64}},
            {"AttributeGeneration": {"RareCount": 1}},
            {"SelectHead": {"Ratio": 0}},
            {"SelectHead": {"Ratio": 2}},
            {"SelectHead": {"Ratio": "nan"}},
            {"SelectHead": {"Count": 10}},
            {"BuildSSDIndex": {"EnableLimitedTagPosting": "false"}},
            {"BuildSSDIndex": {"LimitedTagColumn": 1}},
            {"BuildSSDIndex": {"LimitedTagSlotsPerHead": 0}},
            {"SearchSSDIndex": {"InternalResultNum": 0}},
            {"Base": {"VectorType": "RAW"}},
            {"Base": {"ValueType": None}},
            {"Base": {"VectorSize": 98}},
            {"Base": {"VectorCount": 97}},
            {"Tags": {"TagOffset": 0}},
        )
        for change in changes:
            with self.subTest(change=change):
                config, _, tag = self.fixture(changes=change)
                with self.assertRaises((ValueError, RuntimeError)):
                    self.generate(config)
                self.assertFalse(tag.parent.exists())
        for count in (2, 3, 8):
            config, _, tag = self.fixture(rows=count)
            with self.subTest(count=count), self.assertRaises((ValueError, RuntimeError)):
                self.generate(config)
            self.assertFalse(tag.parent.exists())

    def test_native_budget_formula_including_existing_billion_scale_boundary(self):
        config = {
            "selecthead": {"ratio": "0.12"},
            "buildssdindex": {"enablelimitedtagposting": "true", "limitedtagslotsperhead": "2"},
            "searchssdindex": {"internalresultnum": "96"},
        }
        policy = generator._coverage_policy(config, 1_000_000_000, Path("not-opened"))
        self.assertEqual(coverage_boundary_count(1_000_000_000, policy), 399)
        config, _, _ = self.fixture(changes={"BuildSSDIndex": {"LimitedTagSlotsPerHead": None}})
        manifest = self.generate(config)
        self.assertEqual(manifest["extreme_tag_count"], 3)
        self.assertEqual(manifest["extreme_tag_policy"]["slots_per_head"], 2)

    def test_every_existing_final_or_recovery_path_is_preserved(self):
        for index in range(4):
            for suffix in ("", ".tmp", ".partial", ".backup"):
                with self.subTest(index=index, suffix=suffix):
                    config, _, tag = self.fixture()
                    paths = [tag, tag.with_suffix(".npy"), tag.with_suffix(".counts.tsv"),
                             tag.with_suffix(".manifest.json")]
                    occupied = paths[index].with_name(paths[index].name + suffix)
                    occupied.parent.mkdir()
                    occupied.write_bytes(b"historical-or-partial")
                    with self.assertRaises(FileExistsError):
                        self.generate(config)
                    self.assertEqual(occupied.read_bytes(), b"historical-or-partial")
                    self.assertEqual(list(tag.parent.iterdir()), [occupied])

    def test_existing_symlinks_even_dangling_are_preserved(self):
        for index in range(4):
            for dangling in (False, True):
                with self.subTest(index=index, dangling=dangling):
                    config, base, tag = self.fixture()
                    original = base.read_bytes()
                    paths = [tag, tag.with_suffix(".npy"), tag.with_suffix(".counts.tsv"),
                             tag.with_suffix(".manifest.json")]
                    occupied = paths[index]
                    occupied.parent.mkdir()
                    target = base.with_name("absent") if dangling else base
                    occupied.symlink_to(target)
                    with self.assertRaises(FileExistsError):
                        self.generate(config)
                    self.assertTrue(occupied.is_symlink())
                    self.assertEqual(occupied.readlink(), target)
                    self.assertEqual(base.read_bytes(), original)
                    self.assertEqual(list(tag.parent.iterdir()), [occupied])

    def test_path_collision_is_explicit_and_failed_generation_cleans_only_owned_scratch(self):
        config, _, tag = self.fixture(filename="attributes.npy")
        with self.assertRaisesRegex(ValueError, "distinct"):
            self.generate(config)
        self.assertFalse(tag.parent.exists())
        config, _, tag = self.fixture()
        real_chunks = generator.attribute_chunks

        def fail_after_one(*args):
            yield next(real_chunks(*args))
            raise RuntimeError("injected generation failure")

        with mock.patch.object(generator, "attribute_chunks", fail_after_one):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.generate(config)
        self.assertEqual(list(tag.parent.iterdir()), [])

    def test_publication_race_never_overwrites_or_deletes_unknown_output(self):
        config, _, tag = self.fixture()
        competitor = tag.with_suffix(".npy")
        real_link = os.link

        def race(temporary, final):
            if final == competitor:
                competitor.write_bytes(b"another-process-output")
            return real_link(temporary, final)

        with mock.patch.object(generator.os, "link", side_effect=race):
            with self.assertRaises(FileExistsError):
                self.generate(config)
        self.assertEqual(competitor.read_bytes(), b"another-process-output")
        self.assertEqual(list(tag.parent.iterdir()), [competitor])

    def test_cli_is_repository_owned_and_accepts_only_config(self):
        config, _, tag = self.fixture()
        result = subprocess.run(
            [sys.executable, str(BENCHMARKS / "generate_spann_attributes.py"), "--config", str(config)],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(tag.is_file())
        repeated = subprocess.run(
            [sys.executable, str(BENCHMARKS / "generate_spann_attributes.py"), "--config", str(config)],
            text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(repeated.returncode, 0)
        self.assertIn("Refusing existing", repeated.stderr)
        for options in ([], ["--config", str(config), "--overwrite"],
                        ["--config", str(config), "--seed", "1"],
                        ["--config", str(config), "--base-file", "other"]):
            with self.subTest(options=options), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    generator.main(options)
                self.assertEqual(caught.exception.code, 2)

    def test_relative_input_and_output_paths_use_native_working_directory(self):
        config, base, tag = self.fixture()
        config.write_text(
            config.read_text().replace(str(base), str(base.relative_to(self.root))).replace(
                str(tag), str(tag.relative_to(self.root)),
            ), encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(BENCHMARKS / "generate_spann_attributes.py"), "--config", str(config)],
            cwd=self.root, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(tag.read_bytes(), historical_payload(97)[0].tobytes())
        manifest = json.loads(tag.with_suffix(".manifest.json").read_text())
        self.assertEqual(manifest["source_base_file"], str(base))
        self.assertEqual(manifest["files"]["sptag_attributes"]["path"], str(tag))


if __name__ == "__main__":
    unittest.main()
