"""Prepare query data, never experiment configuration, for the fixed comparison."""

import hashlib
import json
from pathlib import Path
import struct

import numpy as np

from official_benchmark_config import (
    identity, read_json, require, sha256_file, verify_identities, write_json,
)


def matrix(path, dtype, shape):
    result = np.load(path, mmap_mode="r", allow_pickle=False)
    require(result.dtype == np.dtype(dtype) and result.shape == shape, f"Invalid matrix: {path}")
    return result


def native_header(path, dtype_size):
    path = Path(path)
    with path.open("rb") as stream:
        header = stream.read(8)
    require(len(header) == 8, f"Truncated native header: {path}")
    rows, columns = struct.unpack("<II", header)
    require(rows > 0 and columns > 0 and path.stat().st_size == 8 + rows * columns * dtype_size,
            f"Invalid native shape or file size: {path}")
    return rows, columns


def write_bin(path, data, dtype):
    data = np.ascontiguousarray(data, dtype=dtype)
    require(data.ndim == 2, f"Native input must be a matrix: {path}")
    with path.open("xb") as stream:
        stream.write(struct.pack("<II", *data.shape))
        stream.write(data.tobytes())


def write_binding(path, values, query_count, kind):
    values = np.asarray(values, dtype="<u4")
    width = len(values)
    if kind == "label":
        columns = 201
        indices = np.tile(values.astype("<i4"), query_count)
        payload = np.ones(query_count * width, dtype="<f4")
    else:
        columns = width
        indices = np.tile(np.arange(width, dtype="<i4"), query_count)
        converted = values.astype("<f4")
        require(np.array_equal(converted.astype(np.float64), values.astype(np.float64)),
                f"Native range endpoints lose precision: {values}")
        # The range decoder needs both endpoints, including the explicit zero.
        payload = np.tile(converted, query_count)
    with path.open("xb") as stream:
        stream.write(struct.pack("<qqq", query_count, columns, query_count * width))
        stream.write(np.arange(0, (query_count + 1) * width, width, dtype="<i8").tobytes())
        stream.write(indices.tobytes())
        stream.write(payload.tobytes())


def dnf_clauses(path, query_count):
    rows = np.load(path, mmap_mode="r", allow_pickle=False)
    require(rows.dtype == np.dtype("<u4") and rows.ndim == 2
            and rows.shape[0] == query_count and rows.shape[1] >= 4, f"Invalid DNF input: {path}")
    require(np.all(rows == rows[0]), f"Expected fixed-selectivity query predicates: {path}")
    row = rows[0].tolist()
    require(row[0] == len(row) - 1 and row[1] == 0x444E4633, f"Invalid DNF3 record: {path}")
    position = 3
    clauses = []
    for _ in range(row[2]):
        require(position < len(row), f"Truncated DNF3 clause: {path}")
        size = row[position]
        position += 1
        require(size > 0 and position + 4 * size <= len(row), f"Truncated DNF3 terms: {path}")
        clauses.append([tuple(row[offset:offset + 4])
                        for offset in range(position, position + 4 * size, 4)])
        position += 4 * size
    require(position == len(row), f"Trailing DNF3 data: {path}")
    return clauses


def scenario_contract(config, name):
    section = config.section(f"Scenario.{name}")
    kind = section["Kind"]
    if name == "unfilter":
        require(kind == "unfilter", "Unfiltered scenario must not have a predicate")
        return {"clauses": [], "bindings": {}, "expression": None, "stores": [], "predicate": "TRUE"}
    stores = [{"name": "tag", "key": 0, "type": "label", "file": "base.label.0"}]
    if kind == "categorical":
        tag = section.getint("Tag")
        require(0 <= tag <= 200, f"Invalid categorical query: {name}")
        return {
            "clauses": [[(0, 0, 0, tag)]],
            "bindings": {"tag": (f"{name}.spmat", [tag], "label")},
            "expression": "tag = $$tag", "stores": stores, "predicate": f"tag = {tag}",
        }
    upper = section.getint("UpperInclusive")
    require(0 <= upper < 2**32 - 1, f"Unsupported inclusive range endpoint: {name}")
    range_store = {"name": "num", "key": 1, "type": "range", "file": "base.label.1"}
    if kind == "numeric":
        return {
            "clauses": [[(1, 1, 2, upper)]],
            "bindings": {"range": ("numeric.spmat", [0, upper + 1], "range")},
            "expression": "num = $$range", "stores": [range_store], "predicate": f"num <= {upper}",
        }
    require(kind == "mixed_dnf", f"Unsupported scenario kind: {kind}")
    rare, regular = section.getint("RareTag"), section.getint("RegularTag")
    require(0 <= rare <= 200 and 0 <= regular <= 200 and rare != regular, "Invalid mixed DNF tags")
    return {
        "clauses": [[(0, 0, 0, rare)], [(0, 0, 0, regular), (1, 1, 2, upper)]],
        "bindings": {
            "rare": ("mixed_dnf_rare.spmat", [rare], "label"),
            "regular": ("mixed_dnf_regular.spmat", [regular], "label"),
            "range": ("mixed_dnf_range.spmat", [0, upper + 1], "range"),
        },
        "expression": "tag = $$rare OR (tag = $$regular AND num = $$range)",
        "stores": stores + [range_store],
        "predicate": f"tag = {rare} OR (tag = {regular} AND num <= {upper})",
    }


def validate_filter_config(config, name, contract):
    path = config.path_value(f"Scenario.{name}", "FilterConfig")
    actual = read_json(path)
    expected = {
        "attr_indexes": contract["stores"], "filter": contract["expression"],
        "bindings": {variable: binding[0] for variable, binding in contract["bindings"].items()},
    }
    require(actual == expected, f"Fixed native filter config disagrees with its INI scenario: {path}")


def input_contract(config):
    relevant = {section: dict(config.section(section))
                for section in ("Dataset",) + tuple(f"Scenario.{name}"
                                                    for name in config.csv("Benchmark", "Scenarios"))}
    relevant["query_count"] = config.section("Benchmark").getint("QueryCount")
    relevant["topk"] = config.section("Benchmark").getint("ResultNum")
    relevant["source_index_prefix"] = str(config.path_value("PipeANN", "SourceIndexPrefix"))
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()


def validate_workload(config):
    workload = read_json(config.path_value("Dataset", "WorkloadDirectory") / "workloads.json")
    benchmark = config.section("Benchmark")
    require(workload["query_count"] == benchmark.getint("QueryCount")
            and workload["topk"] == benchmark.getint("ResultNum"),
            "Prepared groundtruth does not cover the configured query count/top-k")
    require(Path(workload["base_file"]).resolve() == config.path_value("Dataset", "VectorFile")
            and Path(workload["query_file"]).resolve() == config.path_value("Dataset", "QueryFile"),
            "Groundtruth belongs to different base/query sources")
    require(workload["numeric_threshold"] == config.section("Scenario.numeric").getint("UpperInclusive")
            and workload["mixed_threshold"] == config.section("Scenario.mixed_dnf").getint("UpperInclusive"),
            "Groundtruth numeric thresholds differ from the fixed predicates")
    return workload


def validate_inputs(config):
    root = config.path_value("Dataset", "PreparedDirectory")
    manifest_path = root / "inputs.json"
    require(manifest_path.is_file(), f"Missing prepared inputs: run prepare-inputs for {config.path}")
    manifest = read_json(manifest_path)
    require(manifest["contract"] == input_contract(config), "Prepared data belongs to a different INI")
    verify_identities(manifest["source_files"])
    validate_workload(config)
    for entry in manifest["files"]:
        path = root / entry["name"]
        require(path.is_file() and path.stat().st_size == entry["bytes"]
                and sha256_file(path) == entry["sha256"], f"Prepared input changed: {path}")
    for name, target in manifest["aliases"].items():
        path = root / name
        require(path.is_symlink() and path.resolve() == Path(target),
                f"Incorrect attribute alias: {path}")
    for name in config.csv("Benchmark", "Scenarios"):
        if name != "unfilter":
            validate_filter_config(config, name, scenario_contract(config, name))
    return manifest


def prepare_inputs(config):
    root = config.path_value("Dataset", "PreparedDirectory")
    if (root / "inputs.json").is_file():
        return validate_inputs(config)
    require(not root.exists(), f"Refusing partial or foreign prepared-input directory: {root}")
    dataset = config.section("Dataset")
    benchmark = config.section("Benchmark")
    count, topk = benchmark.getint("QueryCount"), benchmark.getint("ResultNum")
    suite = config.path_value("Dataset", "WorkloadDirectory")
    workload = validate_workload(config)
    source_query = config.path_value("Dataset", "QueryFile")
    native_shape = native_header(source_query, 1)
    require(native_shape[1] == dataset.getint("Dimension") and native_shape[0] >= count,
            "Native query source is too small or has a different dimension")
    require(native_header(config.path_value("Dataset", "VectorFile"), 1)
            == (dataset.getint("VectorCount"), dataset.getint("Dimension")), "Unexpected base-vector source")
    queries = matrix(suite / "query_vectors.npy", "<f4", (count, dataset.getint("Dimension")))
    with source_query.open("rb") as stream:
        stream.seek(8)
        prefix = np.frombuffer(stream.read(count * native_shape[1]), dtype=np.uint8).reshape(queries.shape)
    require(np.array_equal(queries, prefix), "NPY queries do not match the native query prefix")
    sources = [identity(suite / "workloads.json", True), identity(suite / "query_vectors.npy", True),
               identity(source_query)]
    definitions = []
    source_prefix = config.path_value("PipeANN", "SourceIndexPrefix")
    aliases = {f"base{suffix}": str(Path(str(source_prefix) + suffix).resolve())
               for suffix in (".label.0", ".label.0.filter", ".label.1", ".label.1.quantize")}
    require(all(Path(path).is_file() for path in aliases.values()), "Missing persisted PipeANN attributes")
    for name in config.csv("Benchmark", "Scenarios"):
        section = config.section(f"Scenario.{name}")
        contract = scenario_contract(config, name)
        truth_path = suite / section["TruthFile"]
        entry = workload["truth"][name]
        require(truth_path.resolve() == Path(entry["ids"]).resolve()
                and sha256_file(truth_path) == entry["ids_sha256"], f"Wrong GT source: {name}")
        truth = matrix(truth_path, "<i8", (count, topk))
        require(np.all((truth >= 0) & (truth < dataset.getint("VectorCount")))
                and all(len(set(row)) == topk for row in truth.tolist()), f"Invalid GT IDs: {name}")
        sources.append(identity(truth_path, True))
        if name != "unfilter":
            validate_filter_config(config, name, contract)
            predicate_path = suite / section["SourcePredicate"]
            if section["Kind"] == "categorical":
                tags = matrix(predicate_path, "<u4", (count, 1))
                require(np.all(tags == section.getint("Tag")), f"Tag query differs from GT predicate: {name}")
            else:
                require(dnf_clauses(predicate_path, count) == contract["clauses"],
                        f"Numeric/DNF query differs from GT predicate: {name}")
            sources.append(identity(predicate_path, True))
        definitions.append({
            "name": name, "kind": section["Kind"], "truth_npy": str(truth_path),
            "truth_bin": f"gt_{name}.ibin", "predicate": contract["predicate"],
            "candidate_count": entry["candidate_count"], "selectivity": entry["selectivity"],
        })

    root.mkdir(parents=True)
    write_bin(root / "query.u8bin", queries, "u1")
    for definition in definitions:
        name = definition["name"]
        truth = np.load(definition["truth_npy"], allow_pickle=False)
        write_bin(root / definition["truth_bin"], truth, "<u4")
        for filename, values, kind in scenario_contract(config, name)["bindings"].values():
            write_binding(root / filename, values, count, kind)
    for name, target in aliases.items():
        (root / name).symlink_to(target)
    files = [{"name": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
             for path in sorted(root.iterdir()) if path.is_file() and not path.is_symlink()]
    write_json(root / "inputs.json", {
        "contract": input_contract(config), "query_count": count, "topk": topk,
        "query_npy": str(suite / "query_vectors.npy"), "query_bin": "query.u8bin",
        "scenarios": definitions, "files": files, "source_files": sources, "aliases": aliases,
    })
    return validate_inputs(config)
