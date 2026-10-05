#!/usr/bin/env python3
"""Opt-in genuine frozen-core SPANN smoke; never loads production data.

    python3 Tools/benchmarks/tests/test_threeway_spann.py \
        --binary /path/to/spannBench --output-directory /project/spann-smoke

Builds just 320 synthetic UInt8 vectors through the frozen native public APIs.
The fresh persistent evidence directory contains the tiny index, build/link
provenance, exhaustive truths, single/T=1/T=2/T=192 captures and verification.json.
The original four-context AIO pool is independent of query-worker concurrency.
Queries execute a distinct-inode runtime copy with an unchanged profile and
build sidecar; BenchmarkBinary continues to identify the original build artifact.
No core source, production profile, existing index or executable is modified.
"""

from __future__ import annotations

import argparse
import configparser
import importlib.util
import json
import os
from pathlib import Path
import random
import shutil
import struct
import subprocess


HERE = Path(__file__).resolve().parent
BUILD_ENTRY = HERE.parent / "threeway_native/build_spann_client.py"
SPEC = importlib.util.spec_from_file_location("threeway_spann_build", BUILD_ENTRY)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)
COUNT, DIMENSION, QUERY_COUNT, TOP_K = 320, 128, 192, 10
UPPER = 2147483647
SCENARIOS = {
    "unfilter": {"Kind": "unfilter"},
    "broad_tag": {"Kind": "categorical", "Tag": 0},
    "medium_tag": {"Kind": "categorical", "Tag": 9},
    "sel_01pct": {"Kind": "categorical", "Tag": 169},
    "mixed_dnf": {"Kind": "mixed_dnf", "RareTag": 200, "RegularTag": 199, "UpperInclusive": UPPER},
}

FIXTURE_SOURCE = r'''
#include "inc/CoreInterface.h"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>
#include <cstdlib>
void check(bool ok, const char* message) { if (!ok) throw std::runtime_error(message); }
int main(int argc, char** argv) {
    try {
        check(argc == 2, "Expected fresh fixture root");
        const auto root = std::filesystem::absolute(argv[1]);
        const auto index = root / "index";
        check(std::filesystem::create_directory(index), "Refuse existing fixture index");
        constexpr int n = 320, d = 128;
        std::vector<uint8_t> vectors(n * d);
        std::vector<uint32_t> attributes(n * 2);
        std::ifstream input(root / "vectors.u8bin", std::ios::binary);
        uint32_t rows = 0, columns = 0;
        input.read(reinterpret_cast<char*>(&rows), 4);
        input.read(reinterpret_cast<char*>(&columns), 4);
        input.read(reinterpret_cast<char*>(vectors.data()), vectors.size());
        check(bool(input) && rows == n && columns == d, "Synthetic vector extent");
        std::ifstream tags(root / "attributes.u32", std::ios::binary);
        tags.read(reinterpret_cast<char*>(attributes.data()), attributes.size() * sizeof(uint32_t));
        check(bool(tags), "Synthetic attribute extent");
        // These are construction storage locations, only in this fixture child.
        check(setenv("SPTAG_SPANN_INPLACE_DIR", index.c_str(), 1) == 0, "Fixture in-place path");
        check(setenv("SPTAG_SPANN_WORK_DIR", root.c_str(), 1) == 0, "Fixture work path");
        TenantIndexManager manager(d, "SPANN", "UInt8");
        manager.SetStorageBackend("STATIC");
        manager.SetBuildParam("DistCalcMethod", "L2", "Base");
        manager.SetBuildParam("IndexAlgoType", "BKT", "Base");
        for (const auto& p : std::vector<std::pair<const char*, const char*>>{
                 {"SelectHeadType","BKT"}, {"Ratio","0.25"}, {"SelectThreshold","4"},
                 {"SplitFactor","2"}, {"SplitThreshold","8"}, {"BKTLambdaFactor","1"},
                 {"BKTKmeansK","8"}, {"BKTLeafSize","4"}, {"SamplesNumber","128"},
                 {"NumberOfThreads","1"}, {"HierarchyEnabled","true"},
                 {"HierarchyLevels","2"}, {"HierarchyReplicaCount","2"}})
            manager.SetBuildParam(p.first, p.second, "SelectHead");
        for (const auto& p : std::vector<std::pair<const char*, const char*>>{
                 {"NumberOfThreads","1"}, {"NeighborhoodSize","16"}, {"RefineIterations","1"},
                 {"TPTNumber","1"}, {"TPTLeafSize","32"}, {"MaxCheckForRefineGraph","128"},
                 {"CEF","64"}, {"BKTNumber","1"}, {"BKTKmeansK","8"}, {"BKTLeafSize","4"},
                 {"Samples","128"}, {"BKTLambdaFactor","1"}})
            manager.SetBuildParam(p.first, p.second, "BuildHead");
        for (const auto& p : std::vector<std::pair<const char*, const char*>>{
                 {"NumberOfThreads","1"}, {"InternalResultNum","64"}, {"SearchInternalResultNum","384"},
                 {"ReplicaCount","2"}, {"PostingPageLimit","3"}, {"SearchPostingPageLimit","3"},
                 {"TailReplicaCount","0"}, {"UnfilterTailBufferLength","0"}, {"CrossEdges","0"},
                 {"ExcludeHead","true"}, {"ColumnTypes","categorical,numeric"},
                 {"EnableLimitedTagPosting","true"}, {"LimitedTagSlotsPerHead","5"},
                 {"LimitedTagMinHeadCount","1"}, {"EnableLimitedTagSupportExpansion","true"},
                 {"UseDirectIO","false"}})
            manager.SetSSDBuildParam(p.first, p.second);
        manager.SetSSDBuildParam("TmpDir", root.c_str());
        const ByteArray vector_bytes(vectors.data(), vectors.size(), false);
        const ByteArray attribute_bytes(reinterpret_cast<uint8_t*>(attributes.data()),
                                        attributes.size() * sizeof(uint32_t), false);
        check(manager.BuildFromDataWithTagsSingleTenant(vector_bytes, 0, n, attribute_bytes, 2, false, true),
              "Native tiny UInt8 SPANN build");
        check(manager.BuildSignatures(0, attribute_bytes, n, 2), "Native categorical/numeric signatures");
        check(manager.SaveAll(index.c_str()), "Native tiny fixture persistence");
        std::cout << "TINY_NATIVE_FIXTURE count=320 dimension=128 tenant_count=1\n";
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
'''


def write_matrix(path: Path, rows: list, width: int, code: str) -> None:
    with path.open("xb") as stream:
        stream.write(struct.pack("<II", len(rows), width))
        for row in rows:
            stream.write(struct.pack("<" + code * width, *row))


def read_matrix(path: Path, code: str) -> list[tuple]:
    data = path.read_bytes()
    rows, width = struct.unpack_from("<II", data)
    assert (rows, width) == (QUERY_COUNT, TOP_K), path
    assert len(data) == 8 + rows * width * 4, path
    return list(struct.iter_unpack("<" + code * width, data[8:]))


def matches(row: tuple[int, int], scenario: dict) -> bool:
    if scenario["Kind"] == "unfilter":
        return True
    if scenario["Kind"] == "categorical":
        return row[0] == scenario["Tag"]
    return row[0] == scenario["RareTag"] or (
        row[0] == scenario["RegularTag"] and row[1] <= scenario["UpperInclusive"]
    )


def distance(left: list[int], right: list[int]) -> int:
    return sum((a - b) ** 2 for a, b in zip(left, right))


def run(command: list[str], root: Path, name: str) -> str:
    with (root / name).open("xb") as log:
        process = subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                   env=dict(os.environ, TMPDIR=str(root)))
        try:
            returncode = process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
    (root / (name + ".launch.json")).write_text(json.dumps({
        "command": command, "cwd": str(root), "pid": process.pid, "returncode": returncode,
    }, indent=2) + "\n")
    output = (root / name).read_text()
    if returncode:
        raise AssertionError(f"{command} failed ({returncode}); {root / name}\n{output[-10000:]}")
    return output


def smoke(binary: Path, root: Path) -> None:
    frozen_before = [build.identity(path) for path in build.frozen_inputs()]
    rng = random.Random(29)
    vectors = [[rng.randrange(256) for _ in range(DIMENSION)] for _ in range(COUNT)]
    queries = [vectors[(i * 7) % COUNT] for i in range(QUERY_COUNT)]
    attributes = [((0, 9, 169, 200, 199)[i % 5], (0, UPPER, UPPER + 1, 0xFFFFFFFF)[i // 5 % 4])
                  for i in range(COUNT)]
    write_matrix(root / "vectors.u8bin", vectors, DIMENSION, "B")
    write_matrix(root / "query.u8bin", queries, DIMENSION, "B")
    (root / "attributes.u32").write_bytes(b"".join(struct.pack("<II", *row) for row in attributes))
    truths = {}
    candidates = {}
    for name, scenario in SCENARIOS.items():
        allowed = [i for i, row in enumerate(attributes) if matches(row, scenario)]
        candidates[name] = len(allowed)
        truths[name] = [sorted(allowed, key=lambda i: (distance(query, vectors[i]), i))[:TOP_K] for query in queries]
        write_matrix(root / ("gt_" + name + ".u32bin"), truths[name], TOP_K, "I")

    source, helper = root / "native_fixture.cpp", root / "native_fixture"
    source.write_text(FIXTURE_SOURCE)
    command = build.compile_command(source, helper, "/usr/bin/c++", root / "native_fixture.d")
    (root / "fixture_compile.json").write_text(json.dumps(command, indent=2) + "\n")
    run(command, root, "fixture_compile.log")
    run([str(helper), str(root)], root, "fixture_build.log")
    fixture_before = [build.identity(path) for path in sorted((root / "index").rglob("*")) if path.is_file()]
    assert fixture_before, "No actual native index was built"

    authority = configparser.ConfigParser(interpolation=None)
    authority.read(HERE.parent / "configs/sift1b_threeway/benchmark.ini")
    config = configparser.ConfigParser(interpolation=None)
    config["Dataset"] = dict(Vectors=str(root / "vectors.u8bin"), Queries=str(root / "query.u8bin"),
                             Attributes=str(root / "attributes.u32"),
                             VectorCount=str(COUNT), Dimension=str(DIMENSION), AttributeColumns="2",
                             CategoricalColumn="0", NumericColumn="1")
    config["Run"] = dict(OutputDirectory=str(root / "runs"), PreparedDirectory=str(root))
    config["Benchmark"] = dict(QueryCount=str(QUERY_COUNT), WarmupQueries=str(QUERY_COUNT),
                               TopK=str(TOP_K), SingleRepeats="1", Scenarios=",".join(SCENARIOS))
    config["Single"] = dict(CPUNodes="0", MemoryNodes="0")
    config["Throughput"] = dict(Threads=authority["Throughput"]["Threads"], Repeats="1", MinimumSeconds="0.005",
                                RecallTargets="0.90,0.95", AIOReserve="2048", CPUNodes="0", MemoryNodes="0")
    config["SPANN"] = dict(IndexDirectory=str(root / "index"), BenchmarkBinary=str(binary),
                           NProbe=authority["SPANN"]["NProbe"])
    config["SearchSSDIndex"] = dict(authority["SearchSSDIndex"])
    for name, scenario in SCENARIOS.items():
        config["Scenario." + name] = {key: str(value) for key, value in
                                     dict(scenario, CandidateCount=candidates[name],
                                          Truth=str(root / f"gt_{name}.u32bin")).items()}
    profile = root / "fixture.ini"
    with profile.open("x") as stream:
        stream.write("; SYNTHETIC TINY FIXTURE ONLY: not a rendered production configuration\n")
        config.write(stream)
    runtime = root / "runs/runtime"
    runtime.mkdir(parents=True)
    executable = runtime / binary.name
    profile_copy = runtime / "benchmark.ini"
    sidecar = binary.with_name(binary.name + ".build.json")
    shutil.copy2(binary, executable)
    shutil.copy2(sidecar, executable.with_name(executable.name + ".build.json"))
    shutil.copy2(profile, profile_copy)
    assert not os.path.samefile(binary, executable), "Runtime copy must exercise a distinct executable inode"
    assert build.sha256(binary) == build.sha256(executable)
    assert profile.read_bytes() == profile_copy.read_bytes()
    build_record = json.loads(sidecar.read_text())
    assert build_record["binary"]["sha256"] == build.sha256(executable)
    assert build_record["source_hashes_verified_unchanged"] is True
    before = build_record["source_snapshots"]["before_compile"]
    build.verify_source_snapshots(before, build_record["source_snapshots"]["after_compile"])
    build.verify_source_snapshots(before, {name: build_record[name] for name in build.SOURCE_KEYS})
    for name in build.SOURCE_KEYS:
        changed = dict(before, **{name: dict(before[name], sha256="0" * 64)})
        try:
            build.verify_source_snapshots(before, changed)
        except ValueError as error:
            assert name in str(error)
        else:
            raise AssertionError(f"Concurrent {name} change was not rejected")
    plans = root / "runs/plans"
    plans.mkdir(parents=True)
    reports = []
    captures = 0
    thread_grid = [int(value) for value in config["Throughput"]["Threads"].split(",")]
    largest_thread = thread_grid.index(192)
    for phase, thread_indices in (("single", (0,)), ("throughput", (0, 1, largest_thread))):
        rows = [(name, control, thread) for name in SCENARIOS for control in (5, 6) for thread in thread_indices
                if thread != largest_thread or control == 5]
        (plans / f"SPTAG_adaptive.{phase}.tsv").write_text(
            "scenario\tcontrol_index\tthread_index\trepeat\n" +
            "".join(f"{name}\t{control}\t{thread}\t1\n" for name, control, thread in rows))
        output = run([str(executable), str(profile_copy), phase], root, f"{phase}.log")
        assert output.count("THREEWAY_SPANN_INDEX ") == 1, "One physical manager/index per process, not per worker"
        event = json.loads(next(line.split(" ", 1)[1] for line in output.splitlines()
                                if line.startswith("THREEWAY_SPANN_INDEX ")))
        expected_threads = 1 if phase == "single" else 192
        assert event["index_load_count"] == 1 and event["max_threads"] == expected_threads
        assert event["aio_contexts"] == 4 and event["aio_events_per_context"] == 1024
        allocation = json.loads(next(line.split(" ", 1)[1] for line in output.splitlines()
                                     if line.startswith("THREEWAY_AIO ")))
        assert allocation["requested"] == 4096 and allocation["policy"] == "native-shared-pool"
        directory = root / "runs/native/SPTAG_adaptive" / phase
        ready = json.loads((directory / "ready.json").read_text())
        launch = json.loads((root / f"{phase}.log.launch.json").read_text())
        assert ready["pid"] == launch["pid"] and ready["max_threads"] == expected_threads
        completion = json.loads((directory / "completion.json").read_text())
        assert completion["status"] == "completed" and completion["completed_jobs"] == len(rows)
        results = sorted(directory.glob("*.result.json"))
        assert len(results) == len(rows)
        for path in results:
            result = json.loads(path.read_text())
            assert result["invalid_queries"] == 0 and result["underfilled_queries"] == 0
            assert result["recall"] == 1 and result["recall_min_batch"] == 1
            assert result["mean_ios"] is None, "Partial posting counters are not total native IO"
            assert result["threads"] in ((1,) if phase == "single" else (1, 2, 192))
            if phase == "throughput":
                assert result["elapsed_seconds"] >= 0.005
            scenario = SCENARIOS[result["scenario"]]
            for capture in ("first", "last"):
                ids = read_matrix(Path(result[capture + "_ids"]), "I")
                distances = read_matrix(Path(result[capture + "_distances"]), "f")
                for query_id, (returned, returned_distances) in enumerate(zip(ids, distances)):
                    assert len(set(returned)) == TOP_K
                    assert set(returned) == set(truths[result["scenario"]][query_id]), (path, query_id, returned)
                    for vid, reported_distance in zip(returned, returned_distances):
                        assert 0 <= vid < COUNT and matches(attributes[vid], scenario), (path, vid)
                        assert reported_distance == distance(queries[query_id], vectors[vid]), (path, vid)
                        captures += 1
            reports.append({key: result[key] for key in ("phase", "scenario", "L", "threads", "queries", "recall")})
    assert [build.identity(path) for path in build.frozen_inputs()] == frozen_before, "Frozen core changed"
    assert [build.identity(path) for path in sorted((root / "index").rglob("*")) if path.is_file()] == fixture_before, \
        "Query-only smoke modified its native index"
    assert build.sha256(binary) == build.sha256(executable) and profile.read_bytes() == profile_copy.read_bytes()
    (root / "verification.json").write_text(json.dumps({
        "status": "passed", "fixture_only": True, "binary": build.identity(binary), "vectors": COUNT,
        "query_count": QUERY_COUNT, "top_k": TOP_K, "verified_capture_entries": captures,
        "scenarios": list(SCENARIOS), "threads": [1, 2, 192], "one_resident_index_per_process": True,
        "native_aio_contexts": 4, "native_aio_events_per_context": 1024, "aio_events_total": 4096,
        "executed_binary": build.identity(executable), "distinct_executable_inode": True,
        "source_profile": build.identity(profile), "copied_profile": build.identity(profile_copy),
        "source_build_sidecar": build.identity(sidecar),
        "copied_build_sidecar": build.identity(executable.with_name(executable.name + ".build.json")),
        "frozen_artifacts": frozen_before, "fixture_files": fixture_before, "jobs": reports,
    }, indent=2) + "\n")
    print(f"PASS genuine frozen SPANN: {len(reports)} jobs, all five predicates, T=1/2/192, native AIO=4x1024, "
          f"{captures} exact original-VID/distance checks; {root / 'verification.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    binary, root = args.binary.resolve(), args.output_directory.absolute()
    if not binary.is_file():
        parser.error(f"Missing newly built client: {binary}")
    if os.path.lexists(root) or not root.parent.resolve().is_relative_to(build.WORKSPACE):
        parser.error("Smoke output must be a fresh directory inside the current project workspace")
    if root.resolve().is_relative_to(build.FROZEN):
        parser.error("Frozen measured toolchains are immutable")
    root.mkdir(parents=True, exist_ok=False)
    smoke(binary, root)


if __name__ == "__main__":
    main()
