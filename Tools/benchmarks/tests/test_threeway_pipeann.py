#!/usr/bin/env python3
"""Opt-in, bounded real-core smoke; ordinary test discovery never loads an index.

python3 Tools/benchmarks/tests/test_threeway_pipeann.py \
    --binary /path/to/new/pipeannBench --work-directory /workspace/new-smoke

Creates only a 4096x16 UInt8 fixture using original PipeANN builders, retains
original labels 0/9/169/199/200, and uses the original 1% memory-entry sampler.
Runs all five scenarios at T=1 and T=2 through the actual resident executable.
Uses a distinct-inode runtime copy while the unchanged INI names its source.
Every first/last capture is checked against exhaustive original-vector L2 and
the full categorical/DNF predicate, including the inclusive uint32 boundary.
"""

from __future__ import annotations

import argparse
import configparser
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import unittest

import numpy as np


BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS))
from sift1b_official_inputs import write_bin, write_binding


def build_module():
    path = BENCHMARKS / "threeway_native/build_pipeann_client.py"
    spec = importlib.util.spec_from_file_location("build_pipeann_client", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_bin(path, dtype):
    with Path(path).open("rb") as stream:
        rows, columns = struct.unpack("<II", stream.read(8))
        data = np.frombuffer(stream.read(), dtype=dtype)
    if data.size != rows * columns:
        raise ValueError(f"Invalid native capture extent: {path}")
    return data.reshape(rows, columns)


class PipeANNRealCoreTest(unittest.TestCase):
    settings = None

    @classmethod
    def setUpClass(cls):
        if cls.settings is None:
            raise unittest.SkipTest("Real PipeANN smoke requires explicit --binary and fresh --work-directory")
        cls.build = build_module()
        cls.binary = cls.settings.binary.resolve(strict=True)
        if os.path.lexists(cls.settings.work_directory):
            raise ValueError("Fixture output must be a fresh directory, not an existing path or symlink")
        cls.root = cls.settings.work_directory.resolve()
        cls.source = cls.settings.source_directory.resolve(strict=True)
        if not cls.root.parent.resolve().is_relative_to(cls.build.WORKSPACE):
            raise ValueError("Fixture must remain in the workspace, never a system temporary directory")
        if cls.root.is_relative_to(cls.source.parent):
            raise ValueError("Fixture must not modify the frozen toolchain")
        cls.root.mkdir(parents=True, exist_ok=False)
        cls.env = dict(os.environ, TMPDIR=str(cls.root), TMP=str(cls.root), TEMP=str(cls.root))
        prefixes = ("SPTAG_", "SPANN_", "PIPEANN_", "DISKANN_", "OMP_", "GOMP_", "KMP_", "MKL_", "OPENBLAS_")
        exact = {"LD_PRELOAD", "CXXFLAGS", "CPPFLAGS", "ADDITIONAL_DEFINITIONS"}
        forbidden = [name for name in cls.env if name.startswith(prefixes) or name in exact]
        if forbidden:
            raise ValueError(f"Remove runtime overrides before the native smoke: {forbidden}")
        cls.commands = []
        cls.inputs = cls.root / "inputs"
        cls.index = cls.root / "index"
        cls.inputs.mkdir()
        cls.index.mkdir()
        cls.prefix = cls.index / "tiny"
        runtime = cls.root / "runtime"
        runtime.mkdir()
        cls.runtime_binary = runtime / cls.binary.name
        with cls.binary.open("rb") as source_file, cls.runtime_binary.open("xb") as destination:
            shutil.copyfileobj(source_file, destination)
            os.fchmod(destination.fileno(), 0o755)
        source_manifest = Path(str(cls.binary) + ".build.json")
        if source_manifest.is_file():
            runtime_manifest = Path(str(cls.runtime_binary) + ".build.json")
            with source_manifest.open("rb") as source_file, runtime_manifest.open("xb") as destination:
                shutil.copyfileobj(source_file, destination)
        if os.path.samefile(cls.binary, cls.runtime_binary):
            raise ValueError("Runtime-copy smoke requires a distinct executable inode")
        if cls.build.identity(cls.binary)["sha256"] != cls.build.identity(cls.runtime_binary)["sha256"]:
            raise ValueError("Runtime-copy smoke changed executable bytes")
        common, link, provenance = cls.build.native_recipe(cls.source)
        builder = cls.root / "build_disk_index_filtered"
        # Only the fixture-writing tool uses writable attribute constructors;
        # the production client/archive retain their authenticated read-only flags.
        fixture_compile = [
            *[flag for flag in common if flag != "-DREAD_ONLY_TESTS"],
            str(cls.source / "tests/build_disk_index_filtered.cpp"), *link, "-o", str(builder),
        ]
        cls.execute(fixture_compile, "compile-fixture-builder.log", timeout=180)
        provenance["fixture_builder"] = cls.build.identity(builder)
        provenance["fixture_compile"] = fixture_compile
        frozen = json.loads((cls.source.parent / "toolchain.json").read_text())
        original_identity = cls.build.authenticate(frozen["original_toolchain"])
        original = json.loads(Path(original_identity["path"]).read_text())
        tools = {name: cls.build.authenticate(original["binaries"][name])
                 for name in ("gen_random_slice", "build_memory_index")}
        provenance["original_fixture_tools"] = tools

        cls.count, cls.dimension, cls.query_count, cls.top_k = 4096, 16, 8, 10
        rng = np.random.default_rng(20260929)
        cls.vectors = rng.integers(0, 256, (cls.count, cls.dimension), dtype=np.uint8)
        cls.tags = np.resize(np.array([0, 9, 169, 199, 200], dtype="<u4"), cls.count)
        cls.numbers = np.resize(np.array([0, 2147483647, 2147483648, 4294967295], dtype="<u4"), cls.count)
        attributes = np.column_stack([cls.tags, cls.numbers]).astype("<u4")
        (cls.inputs / "attributes.u32").write_bytes(attributes.tobytes())
        rare_high = np.flatnonzero((cls.tags == 200) & (cls.numbers > 2147483647))
        regular_boundary = np.flatnonzero((cls.tags == 199) & (cls.numbers == 2147483647))
        query_ids = np.concatenate([rare_high[:4], regular_boundary[:4]])
        cls.queries = cls.vectors[query_ids].copy()
        write_bin(cls.inputs / "vectors.u8bin", cls.vectors, "u1")
        write_bin(cls.inputs / "query.u8bin", cls.queries, "u1")
        write_bin(cls.inputs / "numbers.bin", cls.numbers[:, None], "<u4")
        with (cls.inputs / "labels.spmat").open("xb") as stream:
            stream.write(struct.pack("<qqq", cls.count, 201, cls.count))
            stream.write(np.arange(cls.count + 1, dtype="<i8").tobytes())
            stream.write(cls.tags.astype("<i4").tobytes())
            stream.write(np.ones(cls.count, dtype="<f4").tobytes())
        cls.execute([
            str(builder), "uint8", str(cls.inputs / "vectors.u8bin"), str(cls.prefix),
            "32", "64", "64", "4", "1", "2", "l2", "pq",
            "label_spmat", str(cls.inputs / "labels.spmat"), "range", str(cls.inputs / "numbers.bin"),
        ], "build-fixture.log", timeout=180)
        sample = cls.root / "sample"
        cls.execute([tools["gen_random_slice"]["path"], "uint8", str(cls.inputs / "vectors.u8bin"),
                     str(sample), "0.01"], "sample-memory.log")
        samples = read_bin(str(sample) + "_data.bin", "u1")
        sample_ids = read_bin(str(sample) + "_ids.bin", "<u4")[:, 0]
        if len(samples) < 10 or not np.array_equal(samples, cls.vectors[sample_ids]):
            raise ValueError("Original 1% memory sampler did not retain matching global IDs/vectors")
        cls.execute([
            tools["build_memory_index"]["path"], "uint8", str(sample) + "_data.bin",
            str(sample) + "_ids.bin", str(cls.prefix) + "_mem.index", "32", "64", "1.2", "2", "l2",
        ], "build-memory.log", timeout=180)
        if not np.array_equal(read_bin(str(cls.prefix) + "_mem.index.tags", "<u4")[:, 0], sample_ids):
            raise ValueError("Memory-entry tags differ from original sampled IDs")
        for suffix in (".label.0", ".label.0.filter", ".label.1", ".label.1.quantize"):
            (cls.inputs / ("base" + suffix)).symlink_to(str(cls.prefix) + suffix)
        cls.masks = {
            "unfilter": np.ones(cls.count, dtype=bool),
            "broad_tag": cls.tags == 0,
            "medium_tag": cls.tags == 9,
            "sel_01pct": cls.tags == 169,
            "mixed_dnf": (cls.tags == 200) | ((cls.tags == 199) & (cls.numbers <= 2147483647)),
        }
        for name, value in (("broad_tag", 0), ("medium_tag", 9), ("sel_01pct", 169),
                            ("mixed_dnf_rare", 200), ("mixed_dnf_regular", 199)):
            write_binding(cls.inputs / (name + ".spmat"), [value], cls.query_count, "label")
        write_binding(cls.inputs / "mixed_dnf_range.spmat", [0, 2147483648], cls.query_count, "range")
        cls.exact = np.sum(
            (cls.queries[:, None, :].astype(np.int64) - cls.vectors[None, :, :].astype(np.int64)) ** 2, axis=2)
        cls.truths = {}
        for name, mask in cls.masks.items():
            candidates = np.flatnonzero(mask)
            truth = np.array([candidates[np.lexsort((candidates, row[candidates]))[:cls.top_k]]
                              for row in cls.exact], dtype="<u4")
            cls.truths[name] = truth
            write_bin(cls.inputs / ("gt_" + name + ".u32bin"), truth, "<u4")
        cls.profile = cls.root / "benchmark.ini"
        config = configparser.ConfigParser(interpolation=None)
        config.optionxform = str
        config.read_dict({
            "Dataset": {"Vectors": str(cls.inputs / "vectors.u8bin"), "Queries": str(cls.inputs / "query.u8bin"),
                        "Attributes": str(cls.inputs / "attributes.u32"), "VectorCount": str(cls.count),
                        "Dimension": str(cls.dimension), "AttributeColumns": "2", "CategoricalColumn": "0",
                        "NumericColumn": "1"},
            "Run": {"OutputDirectory": str(cls.root / "results"), "PreparedDirectory": str(cls.inputs)},
            "Benchmark": {"QueryCount": str(cls.query_count), "WarmupQueries": str(cls.query_count), "TopK": str(cls.top_k),
                          "SingleRepeats": "1", "Scenarios": ",".join(cls.masks)},
            "Single": {"CPUNodes": "0", "MemoryNodes": "0"},
            "Throughput": {"Threads": "1,2", "Repeats": "1", "MinimumSeconds": "0.1", "RecallTargets": "0.9,0.95",
                           "AIOReserve": "0", "CPUNodes": "0", "MemoryNodes": "0"},
            "PipeANN": {"IndexPrefix": str(cls.prefix), "BenchmarkBinary": str(cls.binary),
                        "SourceDirectory": str(cls.source), "LSweep": str(cls.count), "PipelineWidth": "32",
                        "SearchMode": "2", "UnfilteredMemoryL": "10", "FilteredMemoryL": "0", "FilterMode": "auto"},
        })
        for name, mask in cls.masks.items():
            entry = {"Kind": "unfilter" if name == "unfilter" else "mixed_dnf" if name == "mixed_dnf" else "categorical",
                     "Truth": str(cls.inputs / ("gt_" + name + ".u32bin")), "CandidateCount": str(np.sum(mask))}
            if name != "unfilter":
                entry["FilterConfig"] = str(BENCHMARKS / "configs/sift1b_pipeann_curves/filters" / (name + ".json"))
            if name == "mixed_dnf":
                entry.update(RareTag="200", RegularTag="199", UpperInclusive="2147483647")
            elif name != "unfilter":
                entry["Tag"] = str({"broad_tag": 0, "medium_tag": 9, "sel_01pct": 169}[name])
            config["Scenario." + name] = entry
        with cls.profile.open("x") as stream:
            config.write(stream)
        plans = cls.root / "results/plans"
        plans.mkdir(parents=True)
        for phase, threads in (("single", range(1)), ("throughput", range(2))):
            (plans / ("PipeANN." + phase + ".tsv")).write_text(
                "scenario\tcontrol_index\tthread_index\trepeat\n" +
                "".join(f"{name}\t0\t{thread}\t1\n"
                        for name in cls.masks for thread in threads))
        cls.immutable = {str(path): cls.build.identity(path) for path in cls.index.iterdir() if path.is_file()}
        provenance.update(fixture_shape=[cls.count, cls.dimension], query_count=cls.query_count,
                          memory_sampling_rate=0.01, memory_entries=len(samples), fixture_files=cls.immutable,
                          profile=cls.build.identity(cls.profile), binary=cls.build.identity(cls.binary),
                          runtime_binary=cls.build.identity(cls.runtime_binary), runtime_distinct_inode=True)
        (cls.root / "fixture-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")

    @classmethod
    def execute(cls, command, log, timeout=90):
        cls.commands.append(command)
        (cls.root / "commands.json").write_text(json.dumps(cls.commands, indent=2) + "\n")
        with (cls.root / log).open("x") as stream:
            completed = subprocess.run(command, cwd=cls.inputs, env=cls.env, stdout=stream,
                                       stderr=subprocess.STDOUT, timeout=timeout)
        if completed.returncode:
            raise RuntimeError(f"Native command failed ({completed.returncode}); see {cls.root / log}")

    def test_all_scenarios_single_and_multiworker(self):
        evidence = []
        for phase in ("single", "throughput"):
            self.execute([str(self.runtime_binary), str(self.profile), phase], phase + ".log", timeout=180)
            native = self.root / "results/native/PipeANN" / phase
            ready = json.loads((native / "ready.json").read_text())
            complete = json.loads((native / "completion.json").read_text())
            self.assertEqual(ready["max_threads"], 1 if phase == "single" else 2)
            self.assertEqual(complete["status"], "completed")
            self.assertEqual(complete["completed_jobs"], 5 if phase == "single" else 10)
            log = (self.root / (phase + ".log")).read_text()
            self.assertEqual(log.count('"shared_index_loads":1'), 1)
            self.assertEqual(log.count("SSDIndex loaded successfully."), 1)
            for path in sorted(native.glob("*.result.json")):
                result = json.loads(path.read_text())
                self.assertEqual(result["invalid_queries"], 0)
                self.assertGreater(result["mean_ios"], 0)
                self.assertEqual(result["queries"], self.query_count * result["passes"])
                if phase == "throughput":
                    self.assertGreaterEqual(result["elapsed_seconds"], 0.1)
                name = result["scenario"]
                for capture in ("first", "last"):
                    ids = read_bin(result[capture + "_ids"], "<u4")
                    distances = read_bin(result[capture + "_distances"], "<f4")
                    self.assertEqual(ids.shape, (self.query_count, self.top_k))
                    self.assertTrue(np.all(ids < self.count))
                    self.assertTrue(np.all(self.masks[name][ids]))
                    self.assertTrue(all(len(np.unique(row)) == self.top_k for row in ids))
                    expected = np.take_along_axis(self.exact, ids.astype(np.intp), axis=1).astype("<f4")
                    np.testing.assert_array_equal(distances, expected)
                    truth = self.truths[name]
                    np.testing.assert_array_equal(
                        distances, np.take_along_axis(self.exact, truth.astype(np.intp), axis=1).astype("<f4"))
                    self.assertEqual(result[capture + "_recall"], 1)
                    if name == "mixed_dnf":
                        self.assertTrue(np.any((self.tags[ids] == 200) & (self.numbers[ids] > 2147483647)))
                        self.assertTrue(np.any((self.tags[ids] == 199) & (self.numbers[ids] == 2147483647)))
                evidence.append({
                    "phase": phase, "scenario": name, "threads": result["threads"],
                    "passes": result["passes"], "queries": result["queries"],
                    "recall": result["recall"], "mean_ios": result["mean_ios"],
                    "elapsed_seconds": result["elapsed_seconds"],
                    "sum_query_wall_seconds": result["mean_latency_us"] * result["queries"] / 1e6,
                    "capture_ids_and_distances_exact": True,
                })
        overlaps = [item["sum_query_wall_seconds"] / item["elapsed_seconds"]
                    for item in evidence if item["threads"] == 2]
        self.assertGreater(max(overlaps), 1.05, "No measured overlap between genuine native searches")
        self.assertEqual(self.immutable, {str(path): self.build.identity(path)
                                         for path in self.index.iterdir() if path.is_file()})
        (self.root / "smoke-results.json").write_text(json.dumps({
            "status": "passed", "production_index_loaded": False, "index_files_unchanged": True,
            "runtime_copy_accepted": True, "maximum_concurrent_query_wall_ratio": max(overlaps), "results": evidence,
        }, indent=2) + "\n")

    def test_mismatched_source_executable_rejected(self):
        wrong = self.root / "wrong-source-binary"
        with self.binary.open("rb") as source_file, wrong.open("xb") as destination:
            shutil.copyfileobj(source_file, destination)
        with wrong.open("r+b") as stream:
            stream.seek(-1, os.SEEK_END)
            value = stream.read(1)[0]
            stream.seek(-1, os.SEEK_END)
            stream.write(bytes([value ^ 1]))
        config = configparser.ConfigParser(interpolation=None)
        config.read(self.profile)
        output = self.root / "mismatched-source-results"
        config["Run"]["OutputDirectory"] = str(output)
        config["PipeANN"]["BenchmarkBinary"] = str(wrong)
        profile = self.root / "mismatched-source.ini"
        with profile.open("x") as stream:
            config.write(stream)
        (output / "plans").mkdir(parents=True)
        (output / "plans/PipeANN.single.tsv").write_text(
            "scenario\tcontrol_index\tthread_index\trepeat\nunfilter\t0\t0\t1\n")
        completed = subprocess.run([str(self.runtime_binary), str(profile), "single"], cwd=self.inputs,
                                   env=self.env, text=True, capture_output=True, timeout=30)
        (self.root / "mismatched-source.log").write_text(completed.stdout + completed.stderr)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("PipeANN.BenchmarkBinary does not match", completed.stderr)
        self.assertNotIn("SSDIndex loaded successfully.", completed.stdout)
        failure = json.loads((output / "native/PipeANN/single/failure.json").read_text())
        self.assertEqual(failure["status"], "failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument("--source-directory", type=Path, default=build_module().WORKSPACE /
                        "datasets/sift1b/toolchains/pipeann_query_readonly_fds_20260924/source")
    args, remaining = parser.parse_known_args()
    if bool(args.binary) != bool(args.work_directory):
        parser.error("--binary and --work-directory must be supplied together")
    if args.binary:
        PipeANNRealCoreTest.settings = args
    unittest.main(argv=[sys.argv[0], *remaining], verbosity=2)
