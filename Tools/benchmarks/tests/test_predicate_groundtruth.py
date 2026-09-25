"""Fresh native-input truth generation checked against independent exhaustive fixtures."""

import contextlib
import configparser
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import faiss
import numpy as np


BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS))

import generate_spann_predicate_groundtruth as generator
from predicate_groundtruth import (
    knn_l2, matching_rows, native_predicate, parse_predicate, stable_topk,
)


CURRENT_PREDICATES = {
    "unfilter": None,
    "broad_tag": {"categorical_eq": [0, 0]},
    "medium_tag": {"categorical_eq": [0, 9]},
    "sel_01pct": {"categorical_eq": [0, 169]},
    "extreme_tag": {"categorical_eq": [0, 200]},
    "numeric": {"numeric_le": [1, 42949675]},
    "mixed_dnf": {"or": [
        {"categorical_eq": [0, 200]},
        {"and": [{"categorical_eq": [0, 199]}, {"numeric_le": [1, 2147483647]}]},
    ]},
}


def scalar_predicate(expression, row):
    if expression is None:
        return True
    name, value = next(iter(expression.items()))
    if name == "and":
        return all(scalar_predicate(child, row) for child in value)
    if name == "or":
        return any(scalar_predicate(child, row) for child in value)
    column, threshold = value
    actual = int(row[column])
    operation = name.split("_")[1]
    return {"eq": actual == threshold, "lt": actual < threshold,
            "le": actual <= threshold, "gt": actual > threshold,
            "ge": actual >= threshold}[operation]


def write_native(path, array):
    with Path(path).open("wb") as stream:
        stream.write(struct.pack("<ii", *array.shape))
        stream.write(array.tobytes())


class PredicateHelpersTest(unittest.TestCase):
    def test_all_native_comparisons_and_original_columns(self):
        schema = ("numeric", "categorical", "numeric", "categorical")
        attributes = np.array([[0, 7, 5, 9], [1, 9, 6, 7], [2, 7, 0xFFFFFFFF, 9]], dtype="<u4")
        for operation in ("eq", "lt", "le", "gt", "ge"):
            expression = {"and": [{"categorical_eq": [3, 9]}, {f"numeric_{operation}": [2, 5]}]}
            _, clauses = parse_predicate(json.dumps(expression), schema)
            expected = [scalar_predicate(expression, row) for row in attributes]
            np.testing.assert_array_equal(matching_rows(clauses, attributes), expected)
            kind, payload = native_predicate(clauses, schema, 2)
            self.assertEqual(kind, "dnf")
            self.assertEqual(payload[0].tolist(), [11, 0x444E4633, 1, 2, 0, 3, 0, 9, 1, 2,
                                                 {"eq": 0, "lt": 1, "le": 2, "gt": 3, "ge": 4}[operation], 5])
            np.testing.assert_array_equal(payload[0], payload[1])

    def test_distributed_dnf_and_overlapping_or(self):
        expression = {"and": [
            {"or": [{"categorical_eq": [0, 1]}, {"categorical_eq": [0, 2]}]},
            {"or": [{"numeric_le": [1, 5]}, {"numeric_ge": [1, 3]}]},
        ]}
        _, clauses = parse_predicate(json.dumps(expression), ("categorical", "numeric"))
        self.assertEqual(len(clauses), 4)
        attributes = np.array([[1, 4], [2, 100], [0, 4], [1, 0]], dtype="<u4")
        np.testing.assert_array_equal(matching_rows(clauses, attributes), [True, True, False, True])

    def test_invalid_predicates_fail_explicitly(self):
        invalid = [
            "{}", "[]", "true", '{"or":[]}', '{"and":[null]}',
            '{"categorical_ge":[0,1]}', '{"numeric_le":[0,1]}',
            '{"categorical_eq":[1,1]}', '{"numeric_eq":[2,1]}',
            '{"numeric_eq":[1,-1]}', '{"numeric_eq":[1,4294967296]}',
            '{"numeric_eq":[1,1.0]}', '{"numeric_eq":[1,true]}',
            '{"numeric_eq":[true,1]}',
            '{"numeric_eq":[1,0],"numeric_eq":[1,1]}',
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_predicate(value, ("categorical", "numeric"))
        too_many = {"or": [{"categorical_eq": [0, value]} for value in range(65)]}
        with self.assertRaisesRegex(ValueError, "64"):
            parse_predicate(json.dumps(too_many), ("categorical", "numeric"))

    def test_stable_boundary_ties_and_padding(self):
        ids = np.array([20, -1, 7, 0, 10, 3])
        distances = np.array([1, np.inf, 0, 1, 1, 1], dtype=np.float32)
        result, values = stable_topk(ids, distances, 3)
        np.testing.assert_array_equal(result, [7, 0, 3])
        np.testing.assert_array_equal(values, [0, 1, 1])
        self.assertEqual(len(stable_topk(np.array([-1]), np.array([np.inf]), 2)[0]), 0)
        with self.assertRaises(ValueError):
            stable_topk(ids, distances, 0)

    def test_archived_exhaustive_helper_is_preserved(self):
        queries = np.array([[0, 0]], dtype=np.float32)
        positions, distances = knn_l2(queries, np.array([[1, 0]], dtype=np.float32), 3)
        np.testing.assert_array_equal(positions, [[0, -1, -1]])
        np.testing.assert_array_equal(distances, [[1, np.inf, np.inf]])


class PredicateGroundtruthTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.base = (np.arange(125).reshape(25, 5) % 19).astype(np.uint8)
        self.base[:9] = self.base[0]
        self.queries = np.array([self.base[0], self.base[13], self.base[24]], dtype=np.uint8)
        labels = np.resize([0, 9, 169, 200, 199], 25)
        numeric = np.resize([0, 42949675, 42949676, 2147483647, 2147483648, 0xFFFFFFFF], 25)
        self.attributes = np.column_stack((labels, numeric)).astype("<u4")
        self.schema = ("categorical", "numeric")
        self.value_type = "UInt8"
        self.write_sources()

    def write_sources(self):
        write_native(self.root / "base.bin", self.base)
        write_native(self.root / "query.bin", self.queries)
        self.attributes.tofile(self.root / "attributes.u32")

    def config(self, name="truth", *, predicates=None, chunk=5, batch=2, topk=7, extra=""):
        predicates = CURRENT_PREDICATES if predicates is None else predicates
        config = self.root / f"{name}.ini"
        text = (
            f"[Base]\nValueType={self.value_type}\nDistCalcMethod=L2\nVectorType=DEFAULT\n"
            f"VectorPath={self.root / 'base.bin'}\nQueryType=DEFAULT\n"
            f"QueryPath={self.root / 'query.bin'}\nDim={self.base.shape[1]}\n"
            f"TruthPath={self.root / 'does-not-exist-old-truth.bin'}\n"
            f"[Tags]\nTagFile={self.root / 'attributes.u32'}\nColumnTypes={','.join(self.schema)}\n"
            f"[SearchSSDIndex]\nResultNum={topk}\n"
            f"[GroundTruth]\nOutputDirectory={self.root / name}\nQueryCount=all\n"
            f"ChunkRows={chunk}\nQueryBatch={batch}\nThreads=1\n"
            f"Scenarios={','.join(predicates)}\n{extra}"
        )
        for scenario, expression in predicates.items():
            text += f"[Predicate.{scenario}]\nExpression={json.dumps(expression)}\n"
        config.write_text(text)
        return config

    def run_generator(self, config):
        with contextlib.redirect_stdout(io.StringIO()):
            return generator.generate(config)

    def assert_exact(self, manifest, predicates, base=None, queries=None, attributes=None):
        base = self.base if base is None else base
        queries = self.queries if queries is None else queries
        attributes = self.attributes if attributes is None else attributes
        for name, expression in predicates.items():
            entry = manifest["truth"][name]
            candidates = [index for index, row in enumerate(attributes)
                          if scalar_predicate(expression, row)]
            self.assertEqual(entry["candidate_count"], len(candidates))
            self.assertEqual(entry["selectivity"], len(candidates) / len(base))
            actual_ids, actual_distances = np.load(entry["ids"]), np.load(entry["distances"])
            for query_index, query in enumerate(queries):
                ordered = sorted(
                    (float(np.sum((base[index].astype(np.float64) - query.astype(np.float64)) ** 2)), index)
                    for index in candidates
                )[:manifest["topk"]]
                expected_ids = [index for _, index in ordered] + [-1] * (manifest["topk"] - len(ordered))
                expected_distances = [distance for distance, _ in ordered] + [np.inf] * (manifest["topk"] - len(ordered))
                np.testing.assert_array_equal(actual_ids[query_index], expected_ids)
                np.testing.assert_allclose(actual_distances[query_index], expected_distances, atol=1e-5)
            with Path(entry["native_ids"]).open("rb") as stream:
                self.assertEqual(struct.unpack("<ii", stream.read(8)), actual_ids.shape)
                np.testing.assert_array_equal(np.frombuffer(stream.read(), dtype="<i4").reshape(actual_ids.shape),
                                              actual_ids)
            for path_key, hash_key in (("ids", "ids_sha256"), ("distances", "distances_sha256"),
                                       ("native_ids", "native_ids_sha256")):
                self.assertEqual(generator.sha256(entry[path_key]), entry[hash_key])

    def test_all_current_scenarios_from_original_inputs_only(self):
        before = {str(path): generator.sha256(path) for path in
                  (self.root / "base.bin", self.root / "query.bin", self.root / "attributes.u32")}
        config = self.config()
        manifest = self.run_generator(config)
        self.assert_exact(manifest, CURRENT_PREDICATES)
        np.testing.assert_array_equal(np.load(manifest["queries"]), self.queries)
        np.testing.assert_array_equal(np.load(manifest["attributes"]), self.attributes)
        self.assertEqual(np.load(manifest["queries"]).dtype, np.dtype("u1"))
        self.assertEqual(generator.sha256(manifest["attributes"]), manifest["attributes_npy_sha256"])
        self.assertEqual(hashlib.sha256(self.attributes.tobytes()).hexdigest(),
                         manifest["selected_native_attributes_sha256"])
        self.assertEqual(manifest["numeric_threshold"], 42949675)
        self.assertEqual(manifest["mixed_threshold"], 2147483647)
        for path, expected in before.items():
            self.assertEqual(generator.sha256(path), expected)
        for path, expected in manifest["protected"].items():
            self.assertEqual(generator.sha256(path), expected)
        completion = json.loads((self.root / "truth/completion.json").read_text())
        self.assertEqual(completion["state"], "complete")
        self.assertEqual(completion["workloads_sha256"], generator.sha256(self.root / "truth/workloads.json"))
        self.assertFalse((self.root / "does-not-exist-old-truth.bin").exists())

    def test_chunk_and_query_batch_invariance_with_boundary_ties(self):
        first = self.run_generator(self.config("first", chunk=3, batch=1, topk=4))
        second = self.run_generator(self.config("second", chunk=11, batch=3, topk=4))
        self.assert_exact(first, CURRENT_PREDICATES)
        for name in CURRENT_PREDICATES:
            np.testing.assert_array_equal(np.load(first["truth"][name]["ids"]),
                                          np.load(second["truth"][name]["ids"]))
            np.testing.assert_array_equal(np.load(first["truth"][name]["distances"]),
                                          np.load(second["truth"][name]["distances"]))
        np.testing.assert_array_equal(np.load(first["truth"]["unfilter"]["ids"])[0], [0, 1, 2, 3])

    def test_empty_predicate_matches_emit_explicit_padding(self):
        predicates = {"none": {"categorical_eq": [0, 42]}}
        with mock.patch.object(faiss, "pairwise_distances", side_effect=AssertionError("No vectors match")):
            manifest = self.run_generator(self.config(predicates=predicates))
        self.assert_exact(manifest, predicates)
        self.assertEqual(manifest["truth"]["none"]["candidate_count"], 0)

    def test_distance_tiles_are_bounded_and_reused_for_scenarios(self):
        observed = []
        original = faiss.pairwise_distances

        def distance(queries, vectors, **kwargs):
            observed.append((len(queries), len(vectors)))
            return original(queries, vectors, **kwargs)

        with mock.patch.object(faiss, "pairwise_distances", side_effect=distance):
            self.run_generator(self.config(chunk=6, batch=2))
        self.assertEqual(len(observed), 10)
        self.assertTrue(all(query <= 2 and rows <= 6 for query, rows in observed))

    def test_base_payload_is_streamed_instead_of_retained_as_mmap_pages(self):
        original = generator.open_vectors

        class HeaderOnlyMapping:
            def __init__(self, data):
                self.dtype, self.count = data.dtype, len(data)

            def __len__(self):
                return self.count

            def __getitem__(self, key):
                raise AssertionError("Base payload must use bounded streaming reads")

        def open_vectors(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if Path(path) == self.root / "base.bin":
                return SimpleNamespace(path=result.path, value_type=result.value_type,
                                       source_rows=result.source_rows, dimension=result.dimension,
                                       data=HeaderOnlyMapping(result.data))
            return result

        with mock.patch.object(generator, "open_vectors", side_effect=open_vectors):
            manifest = self.run_generator(self.config())
        self.assert_exact(manifest, CURRENT_PREDICATES)

    def test_documented_template_generates_all_current_predicates(self):
        config = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
        config.read(BENCHMARKS.parents[1] / "docs/AdaptiveSpann.ini")
        config["Base"].update(ValueType="UInt8", Dim="5", VectorPath=str(self.root / "base.bin"),
                              QueryPath=str(self.root / "query.bin"), QuerySize="3")
        config["Tags"]["TagFile"] = str(self.root / "attributes.u32")
        config["GroundTruth"].update(OutputDirectory=str(self.root / "template"), Threads="1",
                                     ChunkRows="4", QueryBatch="2")
        path = self.root / "template.ini"
        with path.open("w") as stream:
            config.write(stream, space_around_delimiters=False)
        manifest = self.run_generator(path)
        self.assert_exact(manifest, CURRENT_PREDICATES)
        self.assertEqual(manifest["scenarios"], list(CURRENT_PREDICATES))

    def test_native_types_and_non_128_dimensions(self):
        for name, dtype in (("Float", "<f4"), ("Int8", "i1"), ("UInt8", "u1"), ("Int16", "<i2")):
            with self.subTest(value_type=name):
                self.value_type = name
                self.base = (np.arange(51).reshape(17, 3) % 23).astype(dtype)
                if name != "UInt8":
                    self.base -= 10
                if name == "Float":
                    self.base /= 8
                self.queries = self.base[[0, 8]].copy()
                self.attributes = self.attributes[:17]
                self.write_sources()
                manifest = self.run_generator(self.config(name.lower(), topk=3))
                self.assert_exact(manifest, CURRENT_PREDICATES)
                self.assertEqual(np.load(manifest["queries"]).dtype, np.dtype(dtype))

    def test_interleaved_columns_use_native_dnf_not_unbound_flat_tags(self):
        self.schema = ("numeric", "categorical", "numeric", "categorical")
        self.attributes = np.column_stack((np.arange(25), self.attributes[:, 0],
                                           self.attributes[:, 1], self.attributes[::-1, 0])).astype("<u4")
        self.write_sources()
        predicates = {
            "column_three": {"categorical_eq": [3, 199]},
            "combined": {"and": [{"categorical_eq": [1, 0]}, {"numeric_ge": [0, 10]}]},
        }
        manifest = self.run_generator(self.config(predicates=predicates, topk=3))
        self.assert_exact(manifest, predicates)
        self.assertTrue(all(entry["kind"] == "dnf" for entry in manifest["native_predicates"].values()))
        self.assertFalse(manifest["flat_query_tags"])

    def test_native_prefixes_accept_full_source_attributes(self):
        config = self.config(topk=3)
        config.write_text(config.read_text().replace("[Base]\n", "[Base]\nVectorSize=13\nQuerySize=2\n"))
        manifest = self.run_generator(config)
        self.assert_exact(manifest, CURRENT_PREDICATES, self.base[:13], self.queries[:2], self.attributes[:13])
        self.assertEqual(manifest["source_vector_count"], 25)
        self.assertEqual(manifest["vector_count"], 13)
        self.assertEqual(np.load(manifest["attributes"]).shape, (13, 2))

    def test_existing_output_and_dangling_symlinks_are_preserved(self):
        config = self.config()
        destination = self.root / "truth"
        destination.mkdir()
        marker = destination / "keep"
        marker.write_text("original")
        with self.assertRaises(FileExistsError):
            self.run_generator(config)
        self.assertEqual(marker.read_text(), "original")
        link_config = self.config("link")
        link = self.root / "link"
        link.symlink_to(self.root / "missing-target", target_is_directory=True)
        with self.assertRaises(FileExistsError):
            self.run_generator(link_config)
        self.assertTrue(link.is_symlink())
        self.assertFalse((self.root / "missing-target").exists())

    def test_invalid_configs_fail_before_creating_output(self):
        replacements = [
            ("DistCalcMethod=L2", "DistCalcMethod=Cosine"),
            ("VectorType=DEFAULT", "VectorType=RAW"),
            ("QueryCount=all", "QueryCount=100"),
            ("ChunkRows=5", "ChunkRows=0"),
            ("ResultNum=7", "ResultNum=0"),
            ('"categorical_eq": [0, 0]', '"categorical_eq": [1, 0]'),
        ]
        for index, (old, new) in enumerate(replacements):
            with self.subTest(replacement=new):
                config = self.config(f"bad{index}")
                self.assertIn(old, config.read_text())
                config.write_text(config.read_text().replace(old, new))
                with self.assertRaises(ValueError):
                    self.run_generator(config)
                self.assertFalse((self.root / f"bad{index}").exists())
        config = self.config("unknown", extra="SourceWorkloads=/tmp/old-truth.json\n")
        with self.assertRaisesRegex(ValueError, "unknown"):
            self.run_generator(config)
        self.assertFalse((self.root / "unknown").exists())

    def test_nonfinite_input_is_a_logged_failure_not_success(self):
        self.value_type = "Float"
        self.base = self.base.astype("<f4")
        self.queries = self.queries.astype("<f4")
        self.base[4, 0] = np.nan
        self.write_sources()
        with self.assertRaisesRegex(ValueError, "Nonfinite"):
            self.run_generator(self.config())
        failure = json.loads((self.root / "truth/failure.json").read_text())
        self.assertEqual(failure["state"], "failed")
        self.assertFalse((self.root / "truth/workloads.json").exists())
        self.assertFalse((self.root / "truth/completion.json").exists())

    def test_changed_source_cannot_publish_a_successful_workload(self):
        original = faiss.pairwise_distances
        changed = False

        def distance(queries, vectors, **kwargs):
            nonlocal changed
            if not changed:
                with (self.root / "base.bin").open("ab") as stream:
                    stream.write(b"x")
                changed = True
            return original(queries, vectors, **kwargs)

        with mock.patch.object(faiss, "pairwise_distances", side_effect=distance):
            with self.assertRaisesRegex(RuntimeError, "Source changed"):
                self.run_generator(self.config())
        self.assertEqual(json.loads((self.root / "truth/failure.json").read_text())["state"], "failed")
        self.assertFalse((self.root / "truth/workloads.json").exists())
        self.assertFalse((self.root / "truth/completion.json").exists())

    def test_fresh_cli_pipeline_needs_no_old_workload_or_temporary_script(self):
        (self.root / "attributes.u32").unlink()
        predicates = {"unfilter": None, "mixed": {"or": [
            {"categorical_eq": [0, 3]},
            {"and": [{"categorical_eq": [0, 2]}, {"numeric_le": [1, 2147483647]}]},
        ]}}
        config = self.config(predicates=predicates, topk=3)
        text = config.read_text().replace(
            "[SearchSSDIndex]\n", "[SearchSSDIndex]\nInternalResultNum=2\n")
        text += (
            "[SelectHead]\nRatio=0.5\n"
            "[BuildSSDIndex]\nEnableLimitedTagPosting=true\nLimitedTagSlotsPerHead=2\n"
            "[AttributeGeneration]\nCardinality=3\nZipfExponent=1.0\nSeed=7\n"
            "NumericSeed=11\nChunkRows=4\n"
        )
        config.write_text(text)
        for script in ("generate_spann_attributes.py", "generate_spann_predicate_groundtruth.py"):
            result = subprocess.run(
                [sys.executable, str(BENCHMARKS / script), "--config", str(config)],
                cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                check=False, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
        self.attributes = np.fromfile(self.root / "attributes.u32", dtype="<u4").reshape(25, 2)
        manifest = json.loads((self.root / "truth/workloads.json").read_text())
        self.assert_exact(manifest, predicates)
        self.assertGreater(manifest["truth"]["mixed"]["candidate_count"], 0)
        self.assertFalse((BENCHMARKS / "generate_sift1b_acl_groundtruth.py").exists())
        self.assertTrue((self.root / "attributes.manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
