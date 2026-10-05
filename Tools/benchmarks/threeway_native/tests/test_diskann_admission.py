#!/usr/bin/env python3
"""Genuine original-core admission tests, retained outside the source tree.

Only new writable tiny-fixture clones are changed. Disconnected native indexes
are loaded by an API-only probe which never calls cached_beam_search. The guarded
client must reject them before any index load, warmup, or measured native query.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys


HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE.parent))
import run_sift1b_threeway as parent


def sha(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def write_json(path: Path, record) -> None:
    with path.open("x") as output:
        json.dump(record, output, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())


def replace(text: str, key: str, value: str) -> str:
    text, count = re.subn(r"(?m)^" + re.escape(key) + r"=[^\r\n]*", lambda _: f"{key}={value}", text)
    assert count == 1, key
    return text


def copy_exclusive(source: Path, target: Path) -> None:
    with source.open("rb") as incoming, target.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing)


def launch(command: list[str], cwd: Path, log: Path) -> tuple[int, int]:
    with log.open("x") as output:
        child = subprocess.Popen(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT)
        pid = child.pid
        try:
            status = child.wait(timeout=45)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
            raise
    return pid, status


def markers(log: Path, prefix: str) -> list[dict]:
    return [json.loads(line.removeprefix(prefix)) for line in log.read_text().splitlines() if line.startswith(prefix)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=HERE / "build/diskann_bench")
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--positive-only", action="store_true")
    args = parser.parse_args()
    binary, output = args.binary.resolve(), args.output_directory.resolve()
    if not output.is_relative_to(ROOT / "datasets/sift1b/toolchains/threeway_native_20260929/provenance"):
        parser.error("Retained tests must remain in the dedicated NVMe toolchain provenance subtree")
    output.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location("native_fixture_helper", HERE / "tests/validate_clients.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    fixture = helper.Fixture(output / "data", native=True, native_binary=binary)
    fixture.config = replace(fixture.config, "LSweep", "10,64")
    runtime = output / "runtime"
    runtime.mkdir()
    copied_binary = runtime / "diskannBench"
    copy_exclusive(binary, copied_binary)
    copied_binary.chmod(0o755)
    copy_exclusive(binary.with_name(binary.name + ".build.json"), runtime / "diskannBench.build.json")
    assert sha(binary) == sha(copied_binary)
    assert binary.stat().st_ino != copied_binary.stat().st_ino
    original_prefix = fixture.index
    protected = {path: sha(path) for path in original_prefix.parent.glob(original_prefix.name + "*") if path.is_file()}
    label_map = {key: int(value) for key, value in
                 (line.split("\t") for line in Path(str(original_prefix) + "_disk.index_labels_map.txt").read_text().splitlines())}
    medoid_lines = Path(str(original_prefix) + "_disk.index_labels_to_medoids.txt").read_text().splitlines()
    label_medoids = {int(parts[0]): [int(item) for item in parts[1:] if item.strip()]
                     for parts in (line.split(",") for line in medoid_lines)}
    labels = [set(map(int, line.split(","))) for line in
              Path(str(original_prefix) + "_disk.index_labels.txt").read_text().splitlines()]
    broad = label_map["0"]
    start = label_medoids[broad][0]
    alternate = next(node for node, values in enumerate(labels) if broad in values and node != start)
    with Path(str(original_prefix) + "_disk.index").open("rb") as source:
        metadata = struct.unpack("<9Q", source.read(80)[8:])
    points, dimension, global_start, node_bytes, nodes_per_sector = metadata[:5]
    assert points <= 20000 and points == len(labels) and dimension == fixture.dimension
    assert metadata[5] == metadata[7] == 0

    def node_offset(node: int) -> int:
        return (1 + node // nodes_per_sector) * 4096 + (node % nodes_per_sector) * node_bytes

    def clone(name: str, mutable: set[str]) -> Path:
        directory = output / "indexes" / name
        directory.mkdir(parents=True)
        prefix = directory / original_prefix.name
        for source in original_prefix.parent.glob(original_prefix.name + "*"):
            if not source.is_file():
                continue
            suffix = source.name[len(original_prefix.name):]
            target = Path(str(prefix) + suffix)
            if suffix in mutable:
                copy_exclusive(source, target)
            else:
                target.symlink_to(source.resolve())
        return prefix

    def patch_degree(prefix: Path, node: int, degree: int) -> None:
        path = Path(str(prefix) + "_disk.index")
        assert not path.is_symlink()
        with path.open("r+b") as stream:
            stream.seek(node_offset(node) + dimension)
            stream.write(struct.pack("<I", degree))

    case_records = []

    def run_case(name: str, prefix: Path, scenarios: list[str], positive: bool,
                 phases=("single",), expected_error: str = "") -> dict:
        directory = output / "cases" / name
        directory.mkdir(parents=True)
        results = directory / "results"
        (results / "plans").mkdir(parents=True)
        text = replace(fixture.config, "OutputDirectory", str(results))
        text = replace(text, "IndexPrefix", str(prefix))
        profile = directory / "benchmark.ini"
        with profile.open("x") as stream:
            stream.write(text)
        frozen = directory / "config/benchmark.ini"
        frozen.parent.mkdir()
        copy_exclusive(profile, frozen)
        assert profile.read_bytes() == frozen.read_bytes()
        config = parent.Profile(frozen)
        if positive:
            validated_build = parent.validate_native_build(
                config, "Filtered_DiskANN", binary, binary.with_name(binary.name + ".build.json"))
            write_json(directory / "parent-build-validation.json", validated_build)
        phase_records = {}
        for phase in phases:
            plan = [{"scenario": scenario, "control_index": control, "thread_index": thread, "repeat": 1}
                    for scenario in scenarios
                    for control in (range(2) if positive else (0,))
                    for thread in (range(2) if positive and phase == "throughput" else (0,))]
            plan_path = results / "plans" / f"Filtered_DiskANN.{phase}.tsv"
            with plan_path.open("x") as stream:
                stream.write("scenario\tcontrol_index\tthread_index\trepeat\n")
                for job in plan:
                    stream.write(f"{job['scenario']}\t{job['control_index']}\t{job['thread_index']}\t{job['repeat']}\n")
            write_json(directory / f"{phase}.plan.json", plan)
            log = directory / f"{phase}.log"
            command = [str(copied_binary), str(frozen), phase]
            pid, status = launch(command, fixture.prepared, log)
            native = results / "native/Filtered_DiskANN" / phase
            admission = json.loads((native / "diskann-admission.json").read_text())
            assert admission["pid"] == pid
            assert admission["status"] == ("admitted" if positive else "rejected"), admission
            assert admission["native_search_invocations"] == admission["warmup_queries"] == admission["measured_queries"] == 0
            assert admission["temporary_membership_released"]
            assert len(markers(log, "THREEWAY_ADMISSION ")) == 1
            if positive:
                assert status == 0, log.read_text()
                assert admission["extra_label_read_passes"] == 1
                assert admission["label_rows"] == points
                assert admission["label_bytes"] == Path(str(prefix) + "_disk.index_labels.txt").stat().st_size
                assert admission["native_index_loaded"]
                assert admission["finite_start_selection_verified"]
                assert all(row["status"] == "admitted" and row["reachable_witness_nodes"] >= fixture.top_k
                           and row["graph_records_read"] <= fixture.top_k for row in admission["witnesses"])
                assert {row["scenario"] for row in admission["witnesses"]} == set(scenarios)
                stdout = log.read_text()
                assert stdout.index("THREEWAY_ADMISSION ") < stdout.index("THREEWAY_READY ")
                parsed = parent.parse_results(config, "Filtered_DiskANN", phase, log, plan)
                completion = parent.validate_native_completion(config, "Filtered_DiskANN", phase, plan, pid)
                captures = parent.validate_captures(config, parsed)
                assert any(row["L"] == fixture.top_k for row in parsed)
                write_json(directory / f"{phase}.parent-validation.json",
                           {"results": parsed, "completion": completion, "captures": captures, "expected_pid": pid})
            else:
                assert status != 0 and expected_error in admission["error"], log.read_text()
                assert not admission["native_index_loaded"]
                assert not (native / "ready.json").exists()
                assert not (native / "completion.json").exists()
                assert not markers(log, "THREEWAY_RESULT ")
                assert "Loaded PQ centroids" not in log.read_text()
                assert "allocating ctx:" not in log.read_text()
                assert not list(native.glob("*.ids.u32bin"))
            phase_records[phase] = {"pid": pid, "exit_code": status, "argv": command, "cwd": str(fixture.prepared),
                                    "profile": str(frozen), "plan": str(plan_path), "log": str(log),
                                    "admission": str(native / "diskann-admission.json"), "jobs": len(plan),
                                    "output_directory": str(native)}
        record = {"case": name, "expected": "admitted" if positive else "rejected", "index_prefix": str(prefix),
                  "phases": phase_records}
        case_records.append(record)
        return record

    run_case("full-k-L-equals-K", original_prefix, ["unfilter", "broad_tag", "medium_tag", "sel_01pct"],
             True, ("single", "throughput"))

    if not args.positive_only:
        build = json.loads(binary.with_name(binary.name + ".build.json").read_text())
        probe = runtime / "diskannLoadOnly"
        command = list(build["command"])
        source_position = command.index(str(HERE / "diskann_bench.cpp"))
        command[source_position] = str(HERE / "tests/diskann_load_only.cpp")
        command[command.index("-o") + 1] = str(probe)
        write_json(output / "load-only.compile.json", command)
        subprocess.run(command, check=True, env=dict(os.environ, TMPDIR=str(runtime)), cwd=HERE)

        def native_load_only(name: str, prefix: Path, node: int, degree: int) -> dict:
            log = output / f"{name}.native-load-only.log"
            command = [str(probe), str(prefix), str(node), str(degree)]
            pid, status = launch(command, fixture.prepared, log)
            assert status == 0, log.read_text()
            marker = markers(log, "THREEWAY_NATIVE_LOAD_ONLY ")[0]
            assert marker["pid"] == pid and marker["native_search_invocations"] == 0
            return {"pid": pid, "argv": command, "cwd": str(fixture.prepared),
                    "log": str(log), "native_search_invocations": 0}

        disconnected = clone("disconnected", {"_disk.index"})
        patch_degree(disconnected, start, 0)
        loaded = native_load_only("disconnected", disconnected, start, 0)
        record = run_case("disconnected-native", disconnected, ["broad_tag"], False,
                          expected_error="fewer than TopK")
        record["native_load_only"] = loaded

        too_few = clone("native-label-cardinality-nine", {"_disk.index_labels.txt"})
        kept = {start, *[node for node, values in enumerate(labels) if broad in values and node != start][:8]}
        other = next(value for value in label_map.values() if value != broad)
        changed = [(values if node in kept or broad not in values else (values - {broad}) | {other})
                   for node, values in enumerate(labels)]
        path = Path(str(too_few) + "_disk.index_labels.txt")
        path.write_text("".join(",".join(map(str, sorted(values))) + "\n" for values in changed))
        with Path(str(original_prefix) + "_disk.index").open("rb") as source:
            source.seek(node_offset(start) + dimension)
            original_degree = struct.unpack("<I", source.read(4))[0]
        loaded = native_load_only("native-label-cardinality-nine", too_few, start, original_degree)
        record = run_case("native-label-cardinality-nine", too_few, ["broad_tag"], False,
                          expected_error="fewer than TopK")
        record["native_load_only"] = loaded
        record["original_attribute_matches_unchanged"] = sum(broad in values for values in labels)
        guard = json.loads(Path(record["phases"]["single"]["admission"]).read_text())
        assert guard["planned_native_labels"] == [{"label": broad, "rows": 9}]

        for kind in ("categorical", "unfilter"):
            mutable = {"_disk.index", "_disk.index_labels_to_medoids.txt"}
            multiple = clone(kind + "-all-starts", mutable)
            isolated = alternate if kind == "categorical" else (global_start + 1) % points
            patch_degree(multiple, isolated, 0)
            if kind == "categorical":
                path = Path(str(multiple) + "_disk.index_labels_to_medoids.txt")
                path.write_text("\n".join(f"{broad}, {start}, {isolated}" if int(line.split(",")[0]) == broad else line
                                           for line in medoid_lines) + "\n")
                scenario = "broad_tag"
            else:
                path = Path(str(multiple) + "_disk.index_medoids.bin")
                with path.open("xb") as stream:
                    stream.write(struct.pack("<IIII", 2, 1, global_start, isolated))
                scenario = "unfilter"
            record = run_case(kind + "-every-start-checked", multiple, [scenario], False,
                              expected_error="fewer than TopK")
            guard = json.loads(Path(record["phases"]["single"]["admission"]).read_text())
            expected_starts = {start, isolated} if kind == "categorical" else {global_start, isolated}
            assert {row["start"] for row in guard["witnesses"]} == expected_starts
            assert {row["status"] for row in guard["witnesses"]} == {"admitted", "rejected"}

        for name in ("dummy", "universal"):
            modified = clone(name, set())
            suffix = "_disk.index_dummy_map.txt" if name == "dummy" else "_disk.index_universal_label.txt"
            with Path(str(modified) + suffix).open("x") as stream:
                stream.write("0,1\n" if name == "dummy" else "1\n")
            run_case(name + "-rejected", modified, ["broad_tag"], False,
                     expected_error="dummy-ID" if name == "dummy" else "Universal labels")

        malformed = clone("out-of-range-edge", {"_disk.index"})
        with Path(str(malformed) + "_disk.index").open("r+b") as stream:
            stream.seek(node_offset(start) + dimension + 4)
            stream.write(struct.pack("<I", points))
        run_case("out-of-range-edge", malformed, ["broad_tag"], False, expected_error="edge leaves")

        malformed = clone("oversized-degree", {"_disk.index"})
        patch_degree(malformed, start, (node_bytes - dimension - 4) // 4 + 1)
        run_case("oversized-degree", malformed, ["broad_tag"], False, expected_error="degree bound")

        malformed = clone("missing-label-row", {"_disk.index_labels.txt"})
        path = Path(str(malformed) + "_disk.index_labels.txt")
        path.write_bytes(path.read_bytes().rsplit(b"\n", 2)[0] + b"\n")
        run_case("missing-label-row", malformed, ["broad_tag"], False, expected_error="label rows")

        malformed = clone("nonfinite-PQ-medoid-distance", {"_pq_pivots.bin"})
        path = Path(str(malformed) + "_pq_pivots.bin")
        with path.open("r+b") as stream:
            stream.seek(8)
            offset = struct.unpack("<Q", stream.read(8))[0]
            stream.seek(offset + 8)
            stream.write(struct.pack("<f", float("nan")))
        run_case("nonfinite-PQ-medoid-distance", malformed, ["broad_tag"], False,
                 expected_error="Nonfinite PQ pivot")

    assert all(sha(path) == digest for path, digest in protected.items())
    manifest = {"schema_version": 1, "engine": "Filtered_DiskANN", "status": "passed",
                "source_binary": str(binary), "copied_binary": str(copied_binary), "binary_sha256": sha(binary),
                "shared_header_sha256": sha(HERE / "benchmark.h"), "top_k": fixture.top_k,
                "query_count": fixture.query_count, "fixture_points": points, "fixture_dimension": dimension,
                "original_fixture_files_unchanged": True,
                "production_data_loaded": False, "source_index_prefix": str(original_prefix),
                "cases": case_records}
    write_json(output / "admission-tests.json", manifest)
    print(json.dumps({"report": str(output / "admission-tests.json"), "cases": len(case_records),
                      "binary_sha256": sha(binary)}, indent=2))


if __name__ == "__main__":
    main()
