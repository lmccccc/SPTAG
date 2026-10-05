#!/usr/bin/env python3
"""Retained genuine tiny-index tests; never start a builder or a native search."""

from __future__ import annotations

import argparse
from collections import Counter
import configparser
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess

import numpy as np


HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[3]
TOOLCHAIN = ROOT / "datasets/sift1b/toolchains/threeway_native_20260929"
SOURCE = ROOT / "DiskANN/build/categorical-reuse-smoke-filtered-only"
ORIGINAL_RUN = ROOT / "datasets/sift1b/filtered_diskann/categorical_r64_l1_lf100_pq32_20260928"


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def identity(path):
    path = Path(path).resolve(strict=True)
    stat = path.stat()
    with path.open("rb") as stream:
        header_hash = hashlib.sha256(stream.read(4096)).hexdigest()
    return dict(path=str(path), device=stat.st_dev, inode=stat.st_ino, bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns, first_4096_sha256=header_hash)


def save(path, value):
    with path.open("x") as out:
        json.dump(value, out, indent=2)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())


def copy(source, target):
    with source.open("rb") as src, target.open("xb") as dst:
        shutil.copyfileobj(src, dst)


def launch(command, cwd, log, env=None):
    with log.open("x") as stream:
        process = subprocess.Popen(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, env=env)
        try:
            status = process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
    return {"pid": process.pid, "argv": list(map(str, command)), "cwd": str(cwd),
            "exit_code": status, "log": str(log)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=HERE / "build/diskann_build_admission")
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    binary, output = args.binary.resolve(), args.output_directory.resolve()
    assert output.is_relative_to(TOOLCHAIN / "provenance")
    output.mkdir(parents=True, exist_ok=False)
    runtime = output / "runtime"
    runtime.mkdir()
    executable = runtime / "diskannBuildAdmission"
    copy(binary, executable)
    executable.chmod(0o755)
    copy(binary.with_name(binary.name + ".build.json"), runtime / "diskannBuildAdmission.build.json")
    assert sha(binary) == sha(executable) and binary.stat().st_ino != executable.stat().st_ino
    source_prefix = SOURCE / "sift1b"
    protected = {path: sha(path) for path in SOURCE.glob("sift1b*") if path.is_file()}
    protected.update({SOURCE / name: sha(SOURCE / name)
                      for name in ("fixture.u8bin", "fixture_attrs.u32", "validation_queries.u8bin")})
    with (SOURCE / "fixture.u8bin").open("rb") as stream:
        rows, dimension = struct.unpack("<II", stream.read(8))
    assert rows <= 20000 and dimension == 128, "Never use a production corpus in this test"
    vectors = np.memmap(SOURCE / "fixture.u8bin", mode="r", dtype="u1", offset=8, shape=(rows, dimension))
    attributes = np.memmap(SOURCE / "fixture_attrs.u32", mode="r", dtype="<u4", shape=(rows, 2))
    with (SOURCE / "validation_queries.u8bin").open("rb") as stream:
        query_rows, query_dim = struct.unpack("<II", stream.read(8))
    assert query_dim == dimension and query_rows >= 16
    queries = np.memmap(SOURCE / "validation_queries.u8bin", mode="r", dtype="u1", offset=8,
                       shape=(query_rows, dimension))
    counts = Counter(map(int, attributes[:, 0]))
    assert len(counts) == 201 and min(counts.values()) >= 10
    mapping = {key: int(value) for key, value in
               (line.split("\t") for line in Path(str(source_prefix) + "_disk.index_labels_map.txt").read_text().splitlines())}
    medoids = {int(parts[0]): [int(value) for value in parts[1:] if value.strip()] for parts in
               (line.split(",") for line in Path(str(source_prefix) + "_disk.index_labels_to_medoids.txt").read_text().splitlines())}
    start = medoids[mapping["0"]][0]
    with Path(str(source_prefix) + "_disk.index").open("rb") as stream:
        metadata = struct.unpack("<9Q", stream.read(80)[8:])
    assert metadata[0] == rows and metadata[1] == dimension
    node_bytes, nodes_per_sector = metadata[3:5]
    start_offset = (1 + start // nodes_per_sector) * 4096 + (start % nodes_per_sector) * node_bytes
    native_bin = ROOT / "DiskANN/build/install/bin"
    native_binaries = {name: {"identity": identity(native_bin / name), "sha256": sha(native_bin / name)}
                       for name in ("build_memory_index", "create_disk_layout", "search_disk_index")}
    cases = []

    def prepare(name, *, changes=None, mutable=(), extra_zero_count=False):
        directory = output / "cases" / name
        directory.mkdir(parents=True)
        for source in SOURCE.glob("sift1b*"):
            if not source.is_file():
                continue
            target = directory / source.name
            suffix = source.name.removeprefix("sift1b")
            if suffix in mutable:
                copy(source, target)
            else:
                target.symlink_to(source.resolve())
        copy(ORIGINAL_RUN / "build_categorical_from_pq.py", directory / "build_categorical_from_pq.py")
        local_counts = dict(counts)
        if extra_zero_count:
            local_counts[201] = 0
        counts_path = directory / "counts.tsv"
        with counts_path.open("x") as stream:
            stream.write("attribute_id\trank\tcount\tselectivity\tclass\n")
            for label, count in sorted(local_counts.items()):
                stream.write(f"{label}\t{label + 1}\t{count}\t{count / rows:.12f}\tfixture\n")
        attr_manifest = directory / "attributes.json"
        save(attr_manifest, {"vector_count": rows, "dimension": dimension, "attribute_columns": 2,
                             "limited_tag_column": 0, "files": {"counts": {"sha256": sha(counts_path)}}})
        external_labels = directory / "prepared_labels.txt"
        with external_labels.open("x") as stream:
            stream.writelines(f"{int(label)}\n" for label in attributes[:, 0])
        external_manifest = directory / "prepared_labels.json"
        save(external_manifest, {"state": "prepared", "rows": rows, "source_column": 0,
                                 "source_identity": identity(SOURCE / "fixture_attrs.u32"),
                                 "counts": [count for _, count in sorted(local_counts.items())],
                                 "attributes_sha256": sha(SOURCE / "fixture_attrs.u32"),
                                 "labels_sha256": sha(external_labels), "label_file_bytes": external_labels.stat().st_size,
                                 "label_file": str(external_labels)})
        cfg = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
        cfg.optionxform = str
        cfg.read(ORIGINAL_RUN / "config.ini")
        replacements = {
            ("Inputs", "Vectors"): SOURCE / "fixture.u8bin",
            ("Inputs", "Queries"): SOURCE / "validation_queries.u8bin",
            ("Inputs", "Attributes"): SOURCE / "fixture_attrs.u32",
            ("Inputs", "Counts"): counts_path, ("Inputs", "AttributeManifest"): attr_manifest,
            ("Inputs", "PreparedLabels"): external_labels, ("Inputs", "PreparedLabelsManifest"): external_manifest,
            ("Inputs", "PQPrefix"): source_prefix, ("Run", "OutputDirectory"): directory,
            ("Run", "PreflightDiagnostics"): SOURCE / "all-label-validation/all-label-diagnostics.json",
        }
        replacements.update(changes or {})
        for (section, key), value in replacements.items():
            cfg[section][key] = str(value)
        for label in (0, 9, 169, 200):
            ids = np.flatnonzero(attributes[:, 0] == label)
            truth = []
            for query in queries[:16]:
                distances = np.sum((np.asarray(vectors[ids], dtype=np.int32) - query.astype(np.int32)) ** 2,
                                   axis=1, dtype=np.int64)
                truth.append(ids[np.lexsort((ids, distances))[:10]])
            truth_path = directory / f"truth_{label}.npy"
            with truth_path.open("xb") as stream:
                np.save(stream, np.asarray(truth, dtype=np.uint32), allow_pickle=False)
            cfg["Truth"][str(label)] = str(truth_path)
        config_path = directory / "config.ini"
        with config_path.open("x") as stream:
            cfg.write(stream)
        input_paths = [Path(cfg["Inputs"][key]) for key in
                       ("Vectors", "Queries", "Attributes", "Counts", "AttributeManifest",
                        "PreparedLabels", "PreparedLabelsManifest")]
        input_paths += [Path(str(source_prefix) + suffix) for suffix in ("_pq_pivots.bin", "_pq_compressed.bin")]
        input_paths += [Path(cfg["Run"]["PreflightDiagnostics"])]
        input_paths += list(map(Path, cfg["Truth"].values()))
        manifest = {"native_source_revision": cfg["DiskANN"]["SourceRevision"],
                    "config_sha256": sha(config_path),
                    "runner_sha256": sha(directory / "build_categorical_from_pq.py"),
                    "inputs": {str(path): identity(path) for path in input_paths}, "binaries": native_binaries,
                    "graph_reused": False, "search_pq_reused": True, "categorical_only": True,
                    "disk_pq": False, "universal_label": None, "value_type": "uint8", "metric": "l2",
                    "vectors": rows, "dimension": dimension, "index_prefix": str(directory / "sift1b"),
                    "graph_parameters": {key: cfg["DiskANN"][key] for key in ("R", "L", "FilteredL", "Alpha", "Threads")}}
        save(directory / "manifest.json", manifest)
        return directory, cfg

    def run_case(name, *, expected=None, changes=None, mutable=(), mutate=None,
                 extra_zero_count=False, env=None):
        directory, cfg = prepare(name, changes=changes, mutable=mutable, extra_zero_count=extra_zero_count)
        if mutate:
            mutate(directory)
        certificate = directory / "admission.json"
        command = [str(executable), "--config", str(directory / "config.ini"), "--certificate", str(certificate)]
        execution = launch(command, directory, directory / "admission.log", env)
        record = json.loads(certificate.read_text())
        assert record["pid"] == execution["pid"]
        assert record["native_search_invocations"] == record["warmup_queries"] == record["measured_queries"] == 0
        assert certificate.stat().st_mode & 0o222 == 0
        assert len([line for line in Path(execution["log"]).read_text().splitlines()
                    if line.startswith("DISKANN_BUILD_ADMISSION ")]) == 1
        if expected is None:
            assert execution["exit_code"] == 0 and record["status"] == "admitted", record["error"]
            configured = list(map(int, cfg["Run"]["ValidationLabels"].split(",")))
            k = int(cfg["Run"]["ValidationK"])
            selected = configured + [label for label in sorted(counts) if label not in configured and counts[label] >= k]
            assert record["configured_labels"] == configured
            assert [row["external_label"] for row in record["selected_labels"]] == selected
            assert len(selected) == 201 and len(record["admission"]["witnesses"]) == 201
            for row in record["selected_labels"]:
                assert row["source_count"] == row["native_count"] == counts[row["external_label"]]
                assert row["native_label"] == mapping[str(row["external_label"])]
            assert record["converted_labels_verified"] and record["native_index_loaded"]
            assert record["validation_k"] == k and record["validation_l"] == int(cfg["Run"]["ValidationL"])
            assert record["validation_threads"] == int(cfg["Run"]["ValidationThreads"])
            assert record["planned_stock_query_calls"] == len(configured) * int(cfg["Run"]["ValidationQueriesPerLabel"]) + len(selected) - len(configured)
            guard = record["admission"]
            assert guard["status"] == "admitted" and guard["temporary_membership_released"]
            assert guard["extra_label_read_passes"] == 1 and guard["label_rows"] == rows
            assert guard["temporary_membership_bytes"] == ((rows + 63) // 64) * 8 * len(selected)
            assert guard["graph_records_read"] <= len(selected) * k
            assert all(row["reachable_witness_nodes"] == k and row["status"] == "admitted"
                       for row in guard["witnesses"])
            assert len({row["path"] for row in record["input_identities"]}) == len(record["input_identities"])
            if extra_zero_count:
                assert record["skipped_below_k"] == [{"external_label": 201, "source_count": 0}]
        else:
            assert execution["exit_code"] != 0 and record["status"] == "rejected", record
            assert expected in record["error"], record["error"]
            assert not record["native_index_loaded"]
            assert "Loaded PQ centroids" not in Path(execution["log"]).read_text()
            if record["admission"] is not None:
                assert record["admission"]["temporary_membership_released"]
                assert not record["admission"]["native_index_loaded"]
        cases.append({"name": name, "expected": "admitted" if expected is None else "rejected",
                      **execution, "config": str(directory / "config.ini"), "certificate": str(certificate),
                      "status": record["status"], "error": record["error"]})
        return directory, command, record

    positive, positive_command, _ = run_case("all201-full-k")
    run_case("ini-derived-L-equals-K-T2", changes={("Run", "ValidationL"): 10, ("Run", "ValidationThreads"): 2,
                                                  ("Run", "ValidationQueriesPerLabel"): 3,
                                                  ("Run", "ValidationLabels"): "200,9,0"}, extra_zero_count=True)

    def disconnect(directory):
        with (directory / "sift1b_disk.index").open("r+b") as stream:
            stream.seek(start_offset + dimension)
            stream.write(struct.pack("<I", 0))
    disconnected, _, _ = run_case("disconnected", mutable=("_disk.index",), mutate=disconnect,
                                  expected="fewer than TopK")

    def underfill(directory):
        path = directory / "sift1b_disk.index_labels.txt"
        values = list(map(int, path.read_text().splitlines()))
        retained = {start, *[i for i, value in enumerate(values) if value == mapping["0"] and i != start][:8]}
        with path.open("w") as stream:
            stream.writelines(f"{mapping['1'] if value == mapping['0'] and i not in retained else value}\n"
                              for i, value in enumerate(values))
    run_case("native-cardinality-nine", mutable=("_disk.index_labels.txt",), mutate=underfill,
             expected="fewer than TopK")

    def missing_row(directory):
        path = directory / "sift1b_disk.index_labels.txt"
        path.write_bytes(path.read_bytes().rsplit(b"\n", 2)[0] + b"\n")
    run_case("missing-native-label-row", mutable=("_disk.index_labels.txt",), mutate=missing_row,
             expected="label rows")

    def bad_medoid(directory):
        path = directory / "sift1b_disk.index_labels_to_medoids.txt"
        lines = path.read_text().splitlines()
        path.write_text("\n".join(f"{mapping['0']},{rows}" if line.startswith(f"{mapping['0']},") else line
                                 for line in lines) + "\n")
    run_case("out-of-range-native-medoid", mutable=("_disk.index_labels_to_medoids.txt",), mutate=bad_medoid,
             expected="outside the index")
    run_case("L-below-K", changes={("Run", "ValidationL"): 9}, expected="L>=K")
    run_case("configured-label-below-K", changes={("Run", "ValidationK"): 400, ("Run", "ValidationL"): 512},
             expected="fewer than K source vectors")
    run_case("configured-label-absent", changes={("Run", "ValidationLabels"): "999"},
             expected="fewer than K source vectors")
    run_case("wrong-attribute-schema", changes={("Inputs", "AttributeColumns"): 1},
             expected="headerless row-major uint32")
    run_case("environment-override-rejected", env=dict(os.environ, OMP_NUM_THREADS="1"),
             expected="Remove benchmark environment overrides")

    def bad_config_digest(directory):
        path = directory / "config.ini"
        path.write_text(path.read_text() + "; changed after immutable original manifest\n")
    run_case("changed-original-config", mutate=bad_config_digest, expected="manifest/configuration")

    def duplicate_ini(directory):
        path = directory / "config.ini"
        path.write_text(path.read_text().replace("ValidationL = 256", "ValidationL = 256\nValidationL = 256"))
    run_case("duplicate-INI-key", mutate=duplicate_ini, expected="duplicate key")

    def bad_counts_hash(directory):
        path = directory / "counts.tsv"
        path.write_text(path.read_text().replace("\tfixture\n", "\tfixture-edited\n", 1))
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["inputs"][str(path)] = identity(path)
        manifest_path.write_text(json.dumps(manifest))
    run_case("counts-manifest-hash-mismatch", mutate=bad_counts_hash, expected="manifest SHA256")

    prior = identity(positive / "admission.json"), sha(positive / "admission.json")
    repeated = launch(positive_command, positive, positive / "existing-output-refusal.log")
    assert repeated["exit_code"] != 0 and "fresh" in Path(repeated["log"]).read_text()
    assert prior == (identity(positive / "admission.json"), sha(positive / "admission.json"))
    save(output / "existing-output-refusal.json", repeated)

    manifest = json.loads(binary.with_name(binary.name + ".build.json").read_text())
    probe = runtime / "diskannLoadOnly"
    command = list(manifest["command"])
    command[command.index(str(HERE / "diskann_build_admission.cpp"))] = str(HERE / "tests/diskann_load_only.cpp")
    command[command.index("-o") + 1] = str(probe)
    save(output / "load-only.compile.json", command)
    subprocess.run(command, check=True, cwd=HERE, env=dict(os.environ, TMPDIR=str(runtime)),
                   stdout=subprocess.DEVNULL)
    loaded = launch([str(probe), str(disconnected / "sift1b"), str(start), "0"],
                    disconnected, output / "disconnected-load-only.log")
    assert loaded["exit_code"] == 0
    marker = next(json.loads(line.removeprefix("THREEWAY_NATIVE_LOAD_ONLY "))
                  for line in Path(loaded["log"]).read_text().splitlines()
                  if line.startswith("THREEWAY_NATIVE_LOAD_ONLY "))
    assert marker["pid"] == loaded["pid"] and marker["native_search_invocations"] == 0
    assert all(sha(path) == digest for path, digest in protected.items())
    save(output / "tests.json", {"status": "passed", "binary": str(binary), "binary_sha256": sha(binary),
                                "runtime_copy": str(executable), "source_fixture": str(SOURCE),
                                "original_fixture_unchanged": True, "fixture_shape": [rows, dimension],
                                "selected_labels": 201, "native_search_invocations": 0,
                                "production_index_loaded": False, "cases": cases,
                                "load_only_probe": loaded, "immutable_output_refusal": repeated})
    print(json.dumps({"report": str(output / "tests.json"), "cases": len(cases),
                      "all201_admitted_pid": cases[0]["pid"], "native_search_invocations": 0}, indent=2))


if __name__ == "__main__":
    main()
