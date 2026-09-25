#!/usr/bin/env python3
"""Small, read-only native typed-client fixtures; never load the billion-row index."""
import argparse
import configparser
import hashlib
import json
from pathlib import Path
import struct
import subprocess

import numpy as np


def require(value, message):
    if not value:
        raise RuntimeError(message)


def read_ini(path):
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    with path.open() as stream:
        config.read_file(stream)
    return config


def identities(root):
    return {str(p): (p.stat().st_dev, p.stat().st_ino, p.stat().st_size,
                      p.stat().st_mtime_ns, p.stat().st_ctime_ns)
            for p in root.rglob("*") if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("normal", "diagnostic", "old-normal", "old-diagnostic", "float-index",
                 "uint8-index", "float-queries", "byte-source-queries", "mixed-dnf", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir()
    for root, value_type in ((args.float_index, "Float"), (args.uint8_index, "UInt8")):
        with (root / "tenant_0/HeadIndex/vectors.bin").open("rb") as stream:
            rows, dimension = struct.unpack("<ii", stream.read(8))
        require(0 < rows <= 200000 and dimension == 128, "Only small existing indexes are allowed")
        require(read_ini(root / "tenant_0/indexloader.ini")["Index"]["ValueType"] == value_type,
                "Wrong fixture index type")
    before = {str(p): identities(p) for p in (args.float_index, args.uint8_index)}
    floats = np.load(args.float_queries, mmap_mode="r")[:32].copy()
    original = np.load(args.byte_source_queries, mmap_mode="r")[:32]
    require(floats.dtype == np.float32 and floats.shape == original.shape == (32, 128),
            "Expected exact small Float128 query fixtures")
    require(np.isfinite(original).all() and (original >= 0).all() and (original <= 255).all()
            and np.equal(original, np.floor(original)).all(), "Nonintegral fixture source")
    bytes_ = original.astype(np.uint8)
    mixed = np.load(args.mixed_dnf, mmap_mode="r")[:32].copy()
    with (args.uint8_index / "tenant_0/HeadIndex/vectors.bin").open("rb") as stream:
        stream.seek(8)
        self_queries = np.frombuffer(stream.read(32 * 128), dtype=np.uint8).reshape(32, 128).copy()
    for name, data in (("float", floats), ("uint8", bytes_), ("mixed", mixed),
                       ("self", self_queries), ("categorical", np.full(32, 7, dtype=np.uint32)),
                       ("float64", floats.astype(np.float64)), ("int8", bytes_.view(np.int8)),
                       ("rank3", floats.reshape(32, 128, 1)),
                       ("fortran", np.asfortranarray(floats)),
                       ("big_endian", floats.astype(">f4")), ("empty", floats[:0])):
        np.save(output / (name + ".npy"), data)
    with (output / "uint8_v2.npy").open("wb") as stream:
        np.lib.format.write_array(stream, bytes_, version=(2, 0), allow_pickle=False)
    source = (output / "float.npy").read_bytes()
    (output / "truncated.npy").write_bytes(source[:-1])
    (output / "trailing.npy").write_bytes(source + b"x")
    (output / "minor.npy").write_bytes(source[:7] + b"\x01" + source[8:])
    (output / "bad-header.npy").write_bytes(b"\x93NUMPY\x02\x00\xff\xff\xff\x7f")
    runs = {}

    def run(name, binary, value_type="Float", pages="15", query="float", predicate="empty",
            explicit=True, expected_error=None, index=None):
        directory = output / name
        directory.mkdir()
        config = configparser.ConfigParser(interpolation=None)
        config.optionxform = str
        config["SearchSSDIndex"] = dict(isExecute="true", BuildSsdIndex="false",
            InternalResultNum="24", NumberOfThreads="1", HashTableExponent="4", ResultNum="10",
            MaxCheck="2048", MaxDistRatio="8", SearchPostingPageLimit=pages, DisableCrossEdges="true",
            LogPhaseTime="false", LogPathStats="false", DumpHeads="0", EnableHybridDistance="false",
            EnablePostingNavigation="true", PostingAnchorCount="8", PostingAdditionalMaxCheck="2048")
        config["SearchSweep"] = dict(NProbe="[16,24]")
        selected_index = index or (args.float_index if value_type == "Float" else args.uint8_index)
        config["Benchmark"] = dict(Index=str(selected_index.resolve()),
            Queries=str(output / (query + ".npy")), Predicate=predicate,
            PredicateFile=str(output / ("mixed.npy" if predicate == "dnf" else "categorical.npy"))
                if predicate != "empty" else "",
            MaxQueries="32", Warmup="32")
        if explicit:
            config["Benchmark"]["ValueType"] = value_type
        path = directory / "native.ini"
        with path.open("w") as stream:
            config.write(stream, space_around_delimiters=False)
        command = ["numactl", "--cpunodebind=0", "--membind=0", str(binary.resolve()), str(path)]
        points, progress = [], []
        with (directory / "native.log").open("w") as log:
            process = subprocess.Popen(command, cwd=directory, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True)
            for line in process.stdout:
                log.write(line)
                if line.startswith('{"mode":'):
                    point = json.loads(line)
                    points.append(point)
                    folder = directory / f"nprobe_{point['nprobe']}"
                    for filename, size in (("ids.i32", 32 * 10 * 4), ("dist.f32", 32 * 10 * 4),
                                           ("work.u64", 32 * 8 * 8), ("latency_us.f64", 32 * 8)):
                        require((folder / filename).stat().st_size == size,
                                "Summary emitted before complete native payload")
                    progress.append(dict(nprobe=point["nprobe"], child_live=process.poll() is None,
                                         payloads_complete=True))
            code = process.wait()
        text = (directory / "native.log").read_text()
        if expected_error:
            require(code != 0 and expected_error in text and not points, f"Expected rejection: {name}")
            require("Load Vector (" not in text, "Invalid input reached native index loading")
        else:
            require(code == 0 and [p["nprobe"] for p in points] == [16, 24], f"Native case failed: {name}")
            for point in points:
                require(point["queries"] == 32 and point["navigation_schema_version"] == 6
                        and point["navigation_columns"] == 57, "Native cohort/schema changed")
                if binary in (args.normal, args.diagnostic):
                    require(point["value_type"] == value_type and
                            point["search_posting_page_limit"] == int(pages), "Missing native provenance")
        runs[name] = dict(command=command, returncode=code, points=points, progress=progress)

    run("old-float", args.old_normal, explicit=False)
    run("old-float-diag", args.old_diagnostic, explicit=False)
    run("float-default", args.normal, explicit=False)
    run("float-explicit", args.normal)
    run("float-diag", args.diagnostic)
    run("float-page3", args.normal, pages="3")
    run("old-float-categorical", args.old_normal, predicate="categorical", explicit=False)
    run("float-categorical", args.normal, predicate="categorical")
    run("uint8", args.normal, "UInt8", "3", "uint8")
    run("uint8-diag", args.diagnostic, "UInt8", "3", "uint8")
    run("uint8-v2", args.normal, "UInt8", "3", "uint8_v2")
    run("uint8-mixed", args.normal, "UInt8", "3", "uint8", "dnf")
    run("uint8-mixed-diag", args.diagnostic, "UInt8", "3", "uint8", "dnf")
    run("uint8-categorical", args.normal, "UInt8", "3", "uint8", "categorical")
    run("uint8-categorical-diag", args.diagnostic, "UInt8", "3", "uint8", "categorical")
    run("uint8-self", args.normal, "UInt8", "3", "self")
    self_distances = np.fromfile(output / "uint8-self/nprobe_24/dist.f32", dtype="<f4").reshape(32, 10)
    require(np.all(self_distances[:, 0] == 0), "UInt8 native self queries did not preserve exact byte distances")
    run("uint8-as-float", args.normal, query="uint8", expected_error="NPY type/order mismatch")
    run("float-as-uint8", args.normal, "UInt8", "3", "float", expected_error="NPY type/order mismatch")
    run("signed-byte", args.normal, "UInt8", "3", "int8", expected_error="NPY type/order mismatch")
    run("wrong-index", args.normal, "UInt8", "3", "uint8", index=args.float_index,
        expected_error="does not match saved index ValueType")
    run("uint8-default-float", args.normal, "UInt8", "3", "uint8", explicit=False,
        expected_error="does not match saved index ValueType")
    run("unsupported-type", args.normal, "Int8", "3", "uint8", expected_error="must be Float or UInt8")
    fake = output / "mismatched-saved-base"
    (fake / "tenant_0").mkdir(parents=True)
    saved = read_ini(args.uint8_index / "tenant_0/indexloader.ini")
    saved["Base"]["ValueType"] = "Float"
    with (fake / "tenant_0/indexloader.ini").open("w") as stream:
        saved.write(stream, space_around_delimiters=False)
    run("mismatched-base", args.normal, "UInt8", "3", "uint8", index=fake,
        expected_error="does not match saved index ValueType")
    for name in ("float64", "fortran", "big_endian"):
        run("reject-" + name, args.normal, query=name, expected_error="NPY type/order mismatch")
    for name, message in (("rank3", "one- or two-dimensional"), ("empty", "Invalid NPY shape"),
                          ("truncated", "payload size mismatch"), ("trailing", "payload size mismatch"),
                          ("minor", "Unsupported NPY version"), ("bad-header", "Invalid NPY header length")):
        run("reject-" + name, args.normal, query=name, expected_error=message)
    for i, pages in enumerate(("0", "-1", "1.5", "3garbage", "2147483648")):
        run(f"reject-page-{i}", args.normal, pages=pages, expected_error="must be a positive integer")

    comparisons = [("old-float", "float-default"), ("old-float-diag", "float-diag"),
                   ("float-default", "float-explicit"), ("float-default", "float-diag"),
                   ("old-float-categorical", "float-categorical"),
                   ("uint8", "uint8-diag"), ("uint8", "uint8-v2"),
                   ("uint8-mixed", "uint8-mixed-diag"),
                   ("uint8-categorical", "uint8-categorical-diag")]
    parity = []
    for left, right in comparisons:
        for probe in (16, 24):
            for filename in ("ids.i32", "dist.f32", "work.u64"):
                a, b = (output / case / f"nprobe_{probe}" / filename for case in (left, right))
                require(a.read_bytes() == b.read_bytes(), f"Native payload mismatch: {left}/{right}/{filename}")
        parity.append(dict(left=left, right=right, exact_ids_distances_work=True))
    require(any(p["child_live"] for name in ("float-default", "uint8", "uint8-mixed")
                for p in runs[name]["progress"] if p["nprobe"] == 16), "No observed live point flush")
    for root in (args.float_index, args.uint8_index):
        require(before[str(root)] == identities(root), "Existing fixture index modified")
    report = dict(passed=True, runs=runs, parity=parity, existing_indexes_unchanged=True,
        scope="32-query fixtures only; no 1B index load, acceptance campaign or throughput claim",
        binary_sha256={str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (args.normal, args.diagnostic, args.old_normal, args.old_diagnostic)})
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"PASS {len(runs)} native client cases and {len(parity)} exact payload comparisons")


if __name__ == "__main__":
    main()
