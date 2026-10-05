#!/usr/bin/env python3
"""Bounded header/mock tests and a read-only ORIGINAL DiskANN fixture smoke.

    python3 Tools/benchmarks/threeway_native/build_diskann_client.py --with-tests
    python3 Tools/benchmarks/threeway_native/tests/validate_clients.py

All new artifacts live below threeway_native/build/validation-*. Existing
fixtures, original libraries, Release binaries and production inputs are never
written. No native builds, billion-scale loads or dependency installs occur.
"""

from __future__ import annotations

import argparse
from array import array
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time
import unittest


HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[3]
ENGINE = "Filtered_DiskANN"
PLAN_HEADER = "scenario\tcontrol_index\tthread_index\trepeat\n"


def matrix(path: Path, rows: int, cols: int, values: bytes) -> None:
    with path.open("xb") as output:
        output.write(struct.pack("<II", rows, cols))
        output.write(values)


def read_matrix(path: Path, dtype: str) -> tuple[int, int, array]:
    payload = path.read_bytes()
    rows, cols = struct.unpack_from("<II", payload)
    data = array(dtype)
    data.frombytes(payload[8:])
    if sys.byteorder != "little":
        data.byteswap()
    if len(data) != rows * cols:
        raise AssertionError(f"Invalid native output shape: {path}")
    return rows, cols, data


def records(output: str, prefix: str) -> list[dict]:
    return [json.loads(line[len(prefix):]) for line in output.splitlines() if line.startswith(prefix)]


def distances(query: bytes, base: list[bytes]) -> list[int]:
    return [sum((a - b) ** 2 for a, b in zip(query, row)) for row in base]


class Fixture:
    def __init__(self, directory: Path, *, native: bool = False, native_binary: Path | None = None):
        self.directory = directory
        self.directory.mkdir()
        self.prepared = directory / "prepared"
        self.prepared.mkdir()
        self.output = directory / "results"
        (self.output / "plans").mkdir(parents=True)
        self.query_count = 8
        self.warmup = 8
        if native:
            source = ROOT / "DiskANN/build/categorical-reuse-smoke-filtered-only"
            self.vectors = source / "fixture.u8bin"
            self.attributes = source / "fixture_attrs.u32"
            self.queries = source / "validation_queries.u8bin"
            if self.vectors.stat().st_size > 32 * 1024 * 1024:
                raise AssertionError("Refusing anything except the bounded existing tiny DiskANN fixture")
            rows, dim = struct.unpack("<II", self.vectors.read_bytes()[:8])
            if rows > 20000 or dim > 256:
                raise AssertionError("Refusing large native fixture")
            payload = self.vectors.read_bytes()[8:]
            self.base = [payload[offset:offset + dim] for offset in range(0, len(payload), dim)]
            self.attrs = list(struct.iter_unpack("<II", self.attributes.read_bytes()))
            payload = self.queries.read_bytes()[8:8 + self.query_count * dim]
            self.query_data = [payload[offset:offset + dim] for offset in range(0, len(payload), dim)]
            self.index = source / "sift1b"
            self.top_k = 10
            self.controls = "64,256"
            self.scenarios = {
                "unfilter": ("unfilter", "", lambda tag, num: True),
                "broad_tag": ("categorical", "Tag=0\n", lambda tag, num: tag == 0),
                "medium_tag": ("categorical", "Tag=9\n", lambda tag, num: tag == 9),
                "sel_01pct": ("categorical", "Tag=169\n", lambda tag, num: tag == 169),
                "mixed_dnf": ("mixed_dnf", "RareTag=200\nRegularTag=199\nUpperInclusive=2147483647\n",
                              lambda tag, num: tag == 200 or (tag == 199 and num <= 2147483647)),
            }
        else:
            dim = 8
            self.base = [bytes((row * 7 + column * 3) % 256 for column in range(dim)) for row in range(32)]
            self.attrs = [(row % 4, row * 10) for row in range(len(self.base))]
            self.query_data = [self.base[row] for row in range(self.query_count)]
            self.vectors = directory / "vectors.u8bin"
            self.queries = directory / "queries.u8bin"
            self.attributes = directory / "attributes.u32"
            matrix(self.vectors, len(self.base), dim, b"".join(self.base))
            matrix(self.queries, self.query_count + 2, dim, b"".join(self.query_data + self.base[8:10]))
            self.attributes.write_bytes(b"".join(struct.pack("<II", *row) for row in self.attrs))
            self.index = directory / "unused"
            self.top_k = 2
            self.controls = "2,4"
            self.scenarios = {
                "unfilter": ("unfilter", "", lambda tag, num: True),
                "broad_tag": ("categorical", "Tag=0\n", lambda tag, num: tag == 0),
                "mixed_dnf": ("mixed_dnf", "RareTag=1\nRegularTag=2\nUpperInclusive=100\n",
                              lambda tag, num: tag == 1 or (tag == 2 and num <= 100)),
            }
        self.dimension = dim
        matrix(self.prepared / "query.u8bin", self.query_count, dim, b"".join(self.query_data))
        self.truths = {}
        all_distances = [distances(query, self.base) for query in self.query_data]
        scenario_sections = ""
        for name, (kind, fields, predicate) in self.scenarios.items():
            candidates = [row for row, attrs in enumerate(self.attrs) if predicate(*attrs)]
            truth = [row for query in all_distances
                     for row in sorted(candidates, key=lambda row: (query[row], row))[:self.top_k]]
            assert len(truth) == self.query_count * self.top_k
            self.truths[name] = truth
            truth_path = self.prepared / f"gt_{name}.u32bin"
            matrix(truth_path, self.query_count, self.top_k, struct.pack("<" + "I" * len(truth), *truth))
            scenario_sections += (
                f"\n[Scenario.{name}]\nKind={kind}\n{fields}CandidateCount={len(candidates)}\nTruth={truth_path}\n" +
                (f"FilterConfig={directory / ('filter_' + name + '.json')}\n" if kind != "unfilter" else "")
            )
        self.profile = directory / "profile.ini"
        self.config = f"""; Strict profile, no environment or command-line search overrides.
[Dataset]
Vectors={self.vectors}
Queries={self.queries}
Attributes={self.attributes}
VectorCount={len(self.base)}
Dimension={dim}
AttributeColumns=2
CategoricalColumn=0
NumericColumn=1
QueryPayloadSHA256={hashlib.sha256(b''.join(self.query_data)).hexdigest()}
[Run]
OutputDirectory={self.output}
PreparedDirectory={self.prepared}
BuildDirectory={directory / 'unused-build'}
BuildControllerPID=1
PollSeconds=30
ResourceIntervalSeconds=15
MinimumFreeDiskGiB=32
MinimumFreeMemoryGiB=192
MaxChildRSSGiB=512
RScript=/unused/controller-only/Rscript
[History]
Summary={directory / 'unused-summary.csv'}
SummarySHA256={'0' * 64}
Registration={directory / 'unused-registration.json'}
RegistrationSHA256={'0' * 64}
AssemblyManifest={directory / 'unused-manifest.json'}
AssemblyManifestSHA256={'0' * 64}
[Benchmark]
QueryCount={self.query_count}
WarmupQueries={self.warmup}
TopK={self.top_k}
SingleRepeats=2
Scenarios={','.join(self.scenarios)}
[Throughput]
Threads=1,2,4
Repeats=2
MinimumSeconds=0.05
RecallTargets=0.9,0.95
AIOReserve=2048
ResourcePolicy=current-host-limit
CPUNodes=0
MemoryNodes=0
[Single]
CPUNodes=0
MemoryNodes=0
[DiskANN]
IndexPrefix={self.index}
LSweep={self.controls}
BeamWidth=2
CacheNodes=0
AIOEventsPerThread=1024
Library={ROOT / 'DiskANN/build/install/lib/libdiskann.a'}
SourceDirectory={ROOT / 'DiskANN'}
SourceRevision=78256bbab4685e1774e78d331e081a153be26823
BenchmarkBinary={native_binary or HERE / 'build/diskann_bench'}
[Mock]
Mode=exact
DelayMicroseconds=500
FailAfterCalls={self.warmup}
NativeIO=true
{scenario_sections}"""
        self.profile.write_text(self.config)

    def plan(self, phase: str, rows: str) -> None:
        (self.output / "plans" / f"{ENGINE}.{phase}.tsv").write_text(PLAN_HEADER + rows)

    def change(self, before: str, after: str) -> None:
        assert before in self.config
        self.config = self.config.replace(before, after)
        self.profile.write_text(self.config)

    def run(self, binary: Path, phase: str, *, env: dict | None = None) -> subprocess.CompletedProcess:
        frozen_profile = self.directory / "config" / "benchmark.ini"
        frozen_profile.parent.mkdir(exist_ok=True)
        if not frozen_profile.exists():
            with frozen_profile.open("xb") as output:
                output.write(self.profile.read_bytes())
        if frozen_profile.read_bytes() != self.profile.read_bytes():
            raise AssertionError("Fixture profile changed after its unchanged snapshot was copied")
        run = subprocess.run([str(binary), str(frozen_profile), phase], text=True, capture_output=True,
                             timeout=30, env=env, cwd=self.prepared)
        (self.directory / f"{phase}.{time.time_ns()}.log").write_text(run.stdout + run.stderr)
        return run

    def native_dir(self, phase: str) -> Path:
        return self.output / "native" / ENGINE / phase


class ClientTests(unittest.TestCase):
    workspace: Path
    binaries: Path
    native_binary: Path
    native_executable: Path
    counter = 0

    def fixture(self, *, native: bool = False) -> Fixture:
        type(self).counter += 1
        return Fixture(self.workspace / f"{self._testMethodName}-{self.counter}",
                       native=native, native_binary=self.native_binary)

    def assert_success(self, fixture: Fixture, run: subprocess.CompletedProcess, phase: str,
                       expected_jobs: int, *, exact: bool = True) -> list[dict]:
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        ready = records(run.stdout, "THREEWAY_READY ")
        result = records(run.stdout, "THREEWAY_RESULT ")
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["job_count"], expected_jobs)
        self.assertEqual(len(result), expected_jobs)
        completion = json.loads((fixture.native_dir(phase) / "completion.json").read_text())
        self.assertEqual(completion["job_count"], expected_jobs)
        self.assertEqual(completion["completed_jobs"], expected_jobs)
        paths = []
        for row in result:
            self.assertEqual(row["queries"], row["passes"] * fixture.query_count)
            self.assertEqual(row["cohort_queries"], fixture.query_count)
            self.assertEqual(row["warmup_queries"], fixture.warmup)
            self.assertEqual(row["invalid_queries"], 0)
            self.assertTrue(math.isclose(row["qps"], row["queries"] / row["elapsed_seconds"], rel_tol=1e-14))
            self.assertGreater(row["mean_latency_us"], 0)
            self.assertGreaterEqual(row["p99_latency_us"], row["p50_latency_us"])
            self.assertIn("256 bins/octave", row["latency_quantile_method"])
            self.assertGreaterEqual(row["recall_min_batch"], 0)
            self.assertLessEqual(row["recall_min_batch"], min(row["first_recall"], row["last_recall"]))
            self.assertLessEqual(row["recall_min_batch"], row["recall"] + 1e-15)
            if exact:
                self.assertEqual(row["recall"], 1)
            else:
                self.assertGreaterEqual(row["recall"], 0)
                self.assertLessEqual(row["recall"], 1)
            if phase == "throughput":
                self.assertGreaterEqual(row["elapsed_seconds"], 0.05)
            else:
                self.assertEqual(row["passes"], 1)
                self.assertEqual(row["threads"], 1)
                self.assertEqual(row["first_recall"], row["recall"])
                self.assertEqual(row["last_recall"], row["recall"])
                self.assertEqual(row["recall_min_batch"], row["recall"])
            for cohort in ("first", "last"):
                ids_path, dists_path = Path(row[cohort + "_ids"]), Path(row[cohort + "_distances"])
                self.assertTrue(ids_path.is_absolute() and dists_path.is_absolute())
                paths.extend((ids_path, dists_path))
                rows, cols, ids = read_matrix(ids_path, "I")
                dr, dc, dists = read_matrix(dists_path, "f")
                self.assertEqual((rows, cols, dr, dc), (fixture.query_count, fixture.top_k) * 2)
                if exact:
                    self.assertEqual(list(ids), fixture.truths[row["scenario"]])
                truth = fixture.truths[row["scenario"]]
                hits = sum(len(set(ids[q * cols:(q + 1) * cols]) &
                               set(truth[q * cols:(q + 1) * cols])) for q in range(rows))
                self.assertAlmostEqual(row[cohort + "_recall"], hits / (rows * cols), places=14)
                predicate = fixture.scenarios[row["scenario"]][2]
                for q, query in enumerate(fixture.query_data):
                    query_ids = ids[q * cols:(q + 1) * cols]
                    self.assertEqual(len(set(query_ids)), cols)
                    for k, native_id in enumerate(query_ids):
                        self.assertTrue(predicate(*fixture.attrs[native_id]))
                        self.assertEqual(dists[q * cols + k],
                                         sum((a - b) ** 2 for a, b in zip(query, fixture.base[native_id])))
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(len(paths), len({(path.stat().st_dev, path.stat().st_ino) for path in paths}))
        self.assertTrue(all(not path.is_symlink() for path in paths))
        self.assertEqual(len(list(fixture.native_dir(phase).glob("*.u32bin"))), expected_jobs * 2)
        self.assertEqual(len(list(fixture.native_dir(phase).glob("*.f32bin"))), expected_jobs * 2)
        return result

    def test_header_units(self) -> None:
        run = subprocess.run([str(self.binaries / "header_unit_test")], text=True, capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_exact_parent_profile_options_without_index(self) -> None:
        parent_profile = HERE.parent / "configs/sift1b_threeway/benchmark.ini"
        original = parent_profile.read_bytes()
        directory = self.workspace / "exact-parent-profile"
        directory.mkdir()
        output, prepared = directory / "results", directory / "inputs"
        (output / "plans").mkdir(parents=True)
        prepared.mkdir()
        modified, output_count = re.subn(rb"(?m)^OutputDirectory=[^\r\n]*",
                                        lambda _: b"OutputDirectory=" + str(output).encode(), original)
        modified, prepared_count = re.subn(rb"(?m)^PreparedDirectory=[^\r\n]*",
                                          lambda _: b"PreparedDirectory=" + str(prepared).encode(), modified)
        self.assertEqual((output_count, prepared_count), (1, 1))
        for before, after in zip(original.splitlines(keepends=True), modified.splitlines(keepends=True)):
            if not before.startswith((b"OutputDirectory=", b"PreparedDirectory=")):
                self.assertEqual(before, after)
        self.assertEqual(len(original.splitlines()), len(modified.splitlines()))
        profile = directory / "benchmark.ini"
        with profile.open("xb") as stream:
            stream.write(modified)
        for engine in ("Filtered_DiskANN", "SPTAG_adaptive", "PipeANN"):
            for phase in ("single", "throughput"):
                second_thread = 0 if phase == "single" else 1
                plan = output / "plans" / f"{engine}.{phase}.tsv"
                with plan.open("x") as stream:
                    stream.write(PLAN_HEADER + f"broad_tag\t0\t0\t1\nmedium_tag\t1\t{second_thread}\t2\n")
                run = subprocess.run([str(self.binaries / "profile_options_test"), str(profile), phase, engine],
                                     cwd=prepared, text=True, capture_output=True, timeout=10)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                options = json.loads(run.stdout)
                self.assertEqual(options["engine"], engine)
                self.assertEqual(options["phase"], phase)
                self.assertEqual(options["vector_count"], 10**9)
                self.assertEqual((options["query_count"], options["warmup_queries"], options["top_k"]),
                                 (1000, 1000, 10))
                self.assertEqual((options["scenario_count"], options["job_count"]), (5, 2))
                self.assertEqual(options["max_threads"], 1 if phase == "single" else 2)
                self.assertEqual(options["first_control"], 16 if engine == "SPTAG_adaptive" else 10)
                self.assertEqual(options["output_directory"], str(output))
                self.assertEqual(options["prepared_directory"], str(prepared))
        self.assertEqual(list(prepared.iterdir()), [])
        self.assertFalse((output / "native").exists())
        self.assertEqual(parent_profile.read_bytes(), original)

    def test_single_repeat_payloads_no_clobber(self) -> None:
        fixture = self.fixture()
        fixture.plan("single", "unfilter\t0\t0\t1\nunfilter\t0\t0\t2\nunfilter\t1\t0\t1\n"
                              "broad_tag\t1\t0\t1\nmixed_dnf\t0\t0\t1\n")
        fixture.change("[Dataset]", "[dAtAsEt]")
        fixture.change("VectorCount=", "vEcToRcOuNt=")
        result = self.assert_success(fixture, fixture.run(self.binaries / "mock_bench", "single"), "single", 5)
        self.assertTrue(all(row["mean_ios"] == 7 for row in result))
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in fixture.native_dir("single").iterdir()}
        second = fixture.run(self.binaries / "mock_bench", "single")
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("must be fresh", second.stderr)
        self.assertNotIn("MOCK_SUMMARY", second.stdout)
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before})

    def test_throughput_resident_teams_full_cohorts(self) -> None:
        fixture = self.fixture()
        fixture.plan("throughput", "".join(f"unfilter\t0\t{thread}\t{repeat}\n"
                                           for thread in range(3) for repeat in (1, 2)))
        run = fixture.run(self.binaries / "mock_bench", "throughput")
        result = self.assert_success(fixture, run, "throughput", 6)
        self.assertEqual(Counter(row["threads"] for row in result), {1: 2, 2: 2, 4: 2})
        summary, = records(run.stdout, "MOCK_SUMMARY ")
        self.assertEqual(summary["loads"], 1)
        self.assertEqual(summary["max_threads"], 4)
        self.assertEqual(summary["configurations"], 6)
        expected = sum(row["queries"] + row["warmup_queries"] for row in result)
        self.assertEqual(summary["calls"], expected)
        self.assertEqual(summary["query_counts"], [expected // fixture.query_count] * fixture.query_count)
        self.assertEqual(summary["worker_mask"], 15)

    def test_nullable_native_ios(self) -> None:
        fixture = self.fixture()
        fixture.change("NativeIO=true", "NativeIO=false")
        fixture.plan("single", "unfilter\t0\t0\t1\n")
        result = self.assert_success(fixture, fixture.run(self.binaries / "mock_bench", "single"), "single", 1)
        self.assertIsNone(result[0]["mean_ios"])

    def test_minimum_recall_tracks_uncaptured_middle_cohort(self) -> None:
        fixture = self.fixture()
        fixture.change("Mode=exact", "Mode=recall_dip")
        fixture.plan("throughput", "unfilter\t0\t1\t1\n")
        run = fixture.run(self.binaries / "mock_bench", "throughput")
        result, = self.assert_success(fixture, run, "throughput", 1, exact=False)
        self.assertGreaterEqual(result["passes"], 3)
        self.assertEqual(result["first_recall"], 1)
        self.assertEqual(result["last_recall"], 1)
        self.assertEqual(result["recall_min_batch"], 0)
        self.assertAlmostEqual(result["recall"], 1 - 1 / result["passes"], places=14)

    def test_bad_ini_rejected_before_backend(self) -> None:
        changes = [
            ("Dimension=8", "Dimension=8\ndIMENSION=8"),
            ("Dimension=8", "Dimension=8tail"),
            ("Dimension=8", "Dimension=8 ; inline comments are not values"),
            ("Dimension=8", "Dimension=-8"),
            ("Dimension=8", "Dimension="),
            ("Dimension=8", "# not an INI comment\nDimension=8"),
            ("[Single]", "[Single]\nCPUNodes=0\nMemoryNodes=0\n[single]"),
            ("MinimumSeconds=0.05", "MinimumSeconds=nan"),
            ("Threads=1,2,4", "Threads=1,2,2"),
            ("AttributeColumns=2", "AttributeColumns=2\nAttributeOffset=0"),
            ("Scenarios=unfilter,broad_tag,mixed_dnf", "Scenarios=unfilter,unfilter"),
            ("ResourcePolicy=current-host-limit", "ResourcePolicy=change-kernel-limit"),
            ("PollSeconds=30", "PollSeconds=not-a-number"),
            ("SummarySHA256=" + "0" * 64, "SummarySHA256=invalid-digest"),
        ]
        for before, after in changes:
            with self.subTest(after=after):
                fixture = self.fixture()
                fixture.change(before, after)
                fixture.plan("single", "unfilter\t0\t0\t1\n")
                run = fixture.run(self.binaries / "mock_bench", "single")
                self.assertNotEqual(run.returncode, 0)
                self.assertNotIn("MOCK_SUMMARY", run.stdout)
                self.assertFalse(fixture.native_dir("single").exists())

    def test_bad_plans_rejected_before_backend(self) -> None:
        plans = [
            "unfilter\t2\t0\t1\n", "unfilter\t0\t1\t1\n", "unfilter\t0\t0\t0\n",
            "unfilter\t0\t0\t3\n", "unknown\t0\t0\t1\n", "unfilter\t-1\t0\t1\n",
            "unfilter\t0\t0\t1\textra\n", "unfilter\t0\t0\t1\nunfilter\t0\t0\t1\n", "\n", "",
        ]
        for plan in plans:
            with self.subTest(plan=plan):
                fixture = self.fixture()
                fixture.plan("single", plan)
                run = fixture.run(self.binaries / "mock_bench", "single")
                self.assertNotEqual(run.returncode, 0)
                self.assertNotIn("MOCK_SUMMARY", run.stdout)
        fixture = self.fixture()
        fixture.plan("single", "unfilter\t0\t0\t1\n")
        path = fixture.output / "plans" / f"{ENGINE}.single.tsv"
        path.write_text(path.read_text().replace("control_index", "L"))
        self.assertNotEqual(fixture.run(self.binaries / "mock_bench", "single").returncode, 0)

    def test_bad_native_input_extents_and_query_prefix(self) -> None:
        for corruption in ("attribute_header", "attribute_short", "vector_short", "wrong_query_prefix",
                           "truth_short", "truth_duplicate", "truth_nonmatching"):
            with self.subTest(corruption=corruption):
                fixture = self.fixture()
                fixture.plan("single", "broad_tag\t0\t0\t1\n")
                if corruption == "attribute_header":
                    fixture.attributes.write_bytes(struct.pack("<II", 32, 2) + fixture.attributes.read_bytes())
                elif corruption == "attribute_short":
                    fixture.attributes.write_bytes(fixture.attributes.read_bytes()[:-4])
                elif corruption == "vector_short":
                    fixture.vectors.write_bytes(fixture.vectors.read_bytes()[:-1])
                elif corruption == "wrong_query_prefix":
                    path = fixture.prepared / "query.u8bin"
                    data = bytearray(path.read_bytes())
                    data[8] ^= 1
                    path.write_bytes(data)
                else:
                    path = fixture.prepared / "gt_broad_tag.u32bin"
                    data = bytearray(path.read_bytes())
                    if corruption == "truth_short":
                        data = data[:-4]
                    elif corruption == "truth_duplicate":
                        data[12:16] = data[8:12]
                    else:
                        data[8:12] = struct.pack("<I", 1)
                    path.write_bytes(data)
                run = fixture.run(self.binaries / "mock_bench", "single")
                self.assertNotEqual(run.returncode, 0)
                self.assertNotIn("MOCK_SUMMARY", run.stdout)

    def test_native_output_symlinks_rejected(self) -> None:
        for location in ("root", "native", "phase"):
            with self.subTest(location=location):
                fixture = self.fixture()
                fixture.plan("single", "unfilter\t0\t0\t1\n")
                if location == "root":
                    destination = fixture.directory / "redirected-results"
                    fixture.output.rename(destination)
                    fixture.output.symlink_to(destination, target_is_directory=True)
                elif location == "native":
                    destination = fixture.directory / "redirected-native"
                    destination.mkdir()
                    (fixture.output / "native").symlink_to(destination, target_is_directory=True)
                else:
                    fixture.native_dir("single").parent.mkdir(parents=True)
                    fixture.native_dir("single").symlink_to(fixture.directory / "missing-phase", target_is_directory=True)
                run = fixture.run(self.binaries / "mock_bench", "single")
                self.assertNotEqual(run.returncode, 0)
                self.assertIn("Symlinks are forbidden", run.stderr)
                self.assertNotIn("MOCK_SUMMARY", run.stdout)

    def test_parent_accepts_native_build_provenance(self) -> None:
        code = (
            "import sys; from pathlib import Path; "
            "sys.path.insert(0, sys.argv[1]); "
            "import run_sift1b_threeway as controller; "
            "config=controller.load_profile(Path(sys.argv[1])/'configs/sift1b_threeway/benchmark.ini'); "
            "binary=Path(sys.argv[2]); "
            "controller.validate_native_build(config, 'Filtered_DiskANN', binary, "
            "binary.with_name(binary.name+'.build.json')); "
            "print('parent build-provenance guard passed')"
        )
        run = subprocess.run([sys.executable, "-B", "-c", code, str(HERE.parent), str(self.native_binary)],
                             text=True, capture_output=True, timeout=30, cwd=self.workspace)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_invalid_measured_output_is_explicit_failure(self) -> None:
        counters = {
            "invalid_id": "invalid_ids", "duplicate": "duplicate_ids", "nonmatch": "nonmatching_ids",
            "nonfinite": "nonfinite_distances", "underfill": "underfilled_queries", "throw": "query_exceptions",
            "throw_nonstandard": "query_exceptions",
        }
        for mode, counter in counters.items():
            with self.subTest(mode=mode):
                fixture = self.fixture()
                fixture.change("Mode=exact", f"Mode={mode}")
                fixture.plan("throughput", "broad_tag\t0\t2\t1\n")
                run = fixture.run(self.binaries / "mock_bench", "throughput")
                self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr)
                result, = records(run.stdout, "THREEWAY_RESULT ")
                self.assertEqual(result["invalid_queries"], fixture.query_count)
                self.assertEqual(result[counter], fixture.query_count)
                for field in ("first_ids", "first_distances", "last_ids", "last_distances"):
                    self.assertTrue(Path(result[field]).is_file())
                self.assertTrue((fixture.native_dir("throughput") / "failure.json").is_file())
                self.assertFalse((fixture.native_dir("throughput") / "completion.json").exists())
                if mode == "throw":
                    self.assertIn("mock native query exception", run.stderr)

    def test_invalid_warmup_preserves_failure_evidence(self) -> None:
        fixture = self.fixture()
        fixture.change("Mode=exact", "Mode=underfill")
        fixture.change("FailAfterCalls=8", "FailAfterCalls=0")
        fixture.plan("single", "unfilter\t0\t0\t1\n")
        run = fixture.run(self.binaries / "mock_bench", "single")
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(records(run.stdout, "THREEWAY_RESULT "))
        self.assertEqual(len(list(fixture.native_dir("single").glob("*.failed_warmup.ids.u32bin"))), 1)

    def test_thread_limit_not_silently_reduced(self) -> None:
        fixture = self.fixture()
        fixture.plan("throughput", "unfilter\t0\t2\t1\n")
        run = fixture.run(self.binaries / "mock_bench", "throughput", env=dict(os.environ, OMP_THREAD_LIMIT="1"))
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("Remove benchmark environment overrides: OMP_THREAD_LIMIT", run.stderr)
        self.assertNotIn("MOCK_SUMMARY", run.stdout)

    def test_tuning_environment_rejected_before_plan_or_inputs(self) -> None:
        names = ("OMP_NUM_THREADS", "OMP_DYNAMIC", "GOMP_CPU_AFFINITY", "KMP_AFFINITY", "MKL_NUM_THREADS",
                 "OPENBLAS_NUM_THREADS", "SPTAG_CACHE_SIZE", "SPANN_INDEX_PATH", "DISKANN_BEAM_WIDTH",
                 "PIPEANN_PIPELINE_WIDTH", "FILTERED_DISKANN_INDEX", "CXXFLAGS", "CPPFLAGS",
                 "ADDITIONAL_DEFINITIONS", "LD_PRELOAD")
        fixture = self.fixture()
        for name in names:
            for value in ("", "1") if name != "LD_PRELOAD" else ("",):
                with self.subTest(name=name, value=value):
                    run = fixture.run(self.binaries / "mock_bench", "single", env=dict(os.environ, **{name: value}))
                    self.assertNotEqual(run.returncode, 0)
                    self.assertIn("Remove benchmark environment overrides", run.stderr)
                    self.assertIn(name, run.stderr)
                    self.assertNotIn("Cannot open plan", run.stderr)
                    self.assertNotIn("MOCK_SUMMARY", run.stdout)
                    self.assertFalse(fixture.native_dir("single").exists())

    def test_real_diskann_tiny_smoke_and_aio_checks(self) -> None:
        fixture = self.fixture(native=True)
        fixture.plan("single", "".join(f"{scenario}\t{control}\t0\t1\n"
                                      for scenario in ("broad_tag", "medium_tag", "sel_01pct")
                                      for control in (0, 1)) + "broad_tag\t0\t0\t2\n")
        single = fixture.run(self.native_executable, "single")
        result = self.assert_success(fixture, single, "single", 7, exact=False)
        self.assertTrue(all(row["mean_ios"] is not None and row["mean_ios"] > 0 for row in result))
        self.assertEqual(single.stdout.count("allocating ctx:"), 1)
        fixture.plan("throughput", "".join(f"broad_tag\t0\t{thread}\t1\n" for thread in range(3)))
        throughput = fixture.run(self.native_executable, "throughput")
        self.assert_success(fixture, throughput, "throughput", 3, exact=False)
        self.assertEqual(records(throughput.stdout, "THREEWAY_READY ")[0]["max_threads"], 4)
        self.assertEqual(len(records(throughput.stdout, "THREEWAY_AIO ")), 1)
        self.assertEqual(throughput.stdout.count("allocating ctx:"), 4)
        for mode in ("aio_reserve", "aio_events", "mixed_dnf"):
            with self.subTest(mode=mode):
                failing = self.fixture(native=True)
                failing.plan("single", ("mixed_dnf" if mode == "mixed_dnf" else "broad_tag") + "\t0\t0\t1\n")
                if mode == "aio_reserve":
                    failing.change("AIOReserve=2048", "AIOReserve=18446744073709551615")
                elif mode == "aio_events":
                    failing.change("AIOEventsPerThread=1024", "AIOEventsPerThread=8")
                run = failing.run(self.native_executable, "single")
                self.assertNotEqual(run.returncode, 0)
                self.assertFalse(records(run.stdout, "THREEWAY_READY "))
                self.assertNotIn("Loaded PQ centroids", run.stdout)
                self.assertIn({"aio_reserve": "CURRENT HOST AIO headroom",
                               "aio_events": "AIOEventsPerThread=1024",
                               "mixed_dnf": "not mixed_dnf"}[mode], run.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary-directory", type=Path, default=HERE / "build")
    parser.add_argument("--diskann-binary", type=Path)
    args = parser.parse_args()
    binaries = args.binary_directory.resolve()
    if not binaries.is_relative_to(HERE):
        parser.error("Test binaries/artifacts must remain below threeway_native")
    native_binary = (args.diskann_binary or binaries / "diskann_bench").resolve()
    if not native_binary.is_relative_to(ROOT):
        parser.error("The native client must remain below this workspace")
    workspace = binaries / f"validation-{time.time_ns()}-{os.getpid()}"
    workspace.mkdir()
    runtime = workspace / "runtime"
    runtime.mkdir()
    native_executable = runtime / "diskannBench"
    with native_binary.open("rb") as source, native_executable.open("xb") as destination:
        shutil.copyfileobj(source, destination)
    native_executable.chmod(0o755)
    ClientTests.workspace, ClientTests.binaries, ClientTests.native_binary = workspace, binaries, native_binary
    ClientTests.native_executable = native_executable
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ClientTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = {"successful": result.wasSuccessful(), "tests": result.testsRun,
               "failures": len(result.failures), "errors": len(result.errors), "workspace": str(workspace),
               "diskann_binary": str(native_binary), "frozen_executable": str(native_executable)}
    (workspace / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
