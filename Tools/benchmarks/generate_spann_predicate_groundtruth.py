#!/usr/bin/env python3
"""Generate every configured predicate's exact L2 truth from native input files."""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct

import numpy as np

import native_input_io
import predicate_groundtruth
from native_input_io import column_types, open_attributes, open_vectors, read_config
from predicate_groundtruth import matching_rows, native_predicate, parse_predicate, stable_topk


def positive_integer(section, key, default=None):
    value = section.get(key, default)
    if value is None or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError(f"{key} must be an explicit positive integer")
    result = int(value)
    if not 0 < result <= 2147483647:
        raise ValueError(f"{key} must be in [1, 2147483647]")
    return result


def required_path(section, key):
    if not section.get(key):
        raise ValueError(f"Missing native INI path: {key}")
    return Path(section[key]).resolve()


def identity(path):
    path = Path(path)
    stat = path.stat()
    return dict(path=str(path.resolve()), device=stat.st_dev, inode=stat.st_ino,
                size=stat.st_size, mtime_ns=stat.st_mtime_ns, ctime_ns=stat.st_ctime_ns)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def save_array(path, array):
    with Path(path).open("xb") as stream:
        np.save(stream, array, allow_pickle=False)
        stream.flush()
        os.fsync(stream.fileno())


def save_json(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def native_truth(path, ids):
    with Path(path).open("xb") as stream:
        stream.write(struct.pack("<ii", *ids.shape))
        stream.write(ids.astype("<i4").tobytes())
        stream.flush()
        os.fsync(stream.fileno())


def read_rows(stream, dtype, rows, columns):
    size = rows * columns * np.dtype(dtype).itemsize
    payload = stream.read(size)
    if len(payload) != size:
        raise ValueError(f"Native input was truncated while reading {stream.name}")
    return np.frombuffer(payload, dtype=dtype).reshape(rows, columns)


def read_scenarios(config, settings, schema):
    names = [name.strip() for name in settings.get("scenarios", "").split(",")]
    if (not names or len(set(names)) != len(names)
            or any(not re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in names)):
        raise ValueError("GroundTruth.Scenarios requires distinct lowercase scenario names")
    result = {}
    for name in names:
        section = config.get(f"predicate.{name}", {})
        if "expression" not in section or set(section) - {"expression", "title"}:
            raise ValueError(f"[Predicate.{name}] requires Expression and optional Title only")
        expression, clauses = parse_predicate(section["expression"], schema)
        result[name] = dict(expression=expression, clauses=clauses, title=section.get("title", name))
    return result


def generate(config_path):
    config_path = Path(config_path).resolve()
    config = read_config(config_path)
    base, tags = config.get("base", {}), config.get("tags", {})
    settings = config.get("groundtruth", {})
    allowed = {"outputdirectory", "dataset", "querycount", "chunkrows", "querybatch",
               "threads", "scenarios"}
    if not settings or set(settings) - allowed:
        raise ValueError(f"Missing or unknown GroundTruth settings: {set(settings) - allowed}")
    if base.get("distcalcmethod", "").lower() != "l2":
        raise ValueError("Predicate groundtruth currently supports native L2 only")
    if (base.get("vectortype", "DEFAULT").upper() != "DEFAULT"
            or base.get("querytype", "DEFAULT").upper() != "DEFAULT"):
        raise ValueError("Predicate groundtruth requires native DEFAULT vector/query containers")
    schema = tuple(column_types(config))
    if not schema:
        raise ValueError("Groundtruth requires explicit Tags.ColumnTypes")
    topk = positive_integer(config.get("searchssdindex", {}), "resultnum")
    chunk_rows = positive_integer(settings, "chunkrows", "65536")
    query_batch = positive_integer(settings, "querybatch", "64")
    threads = positive_integer(settings, "threads", "8")
    scenarios = read_scenarios(config, settings, schema)
    if not settings.get("outputdirectory"):
        raise ValueError("Missing native INI path: outputdirectory")
    requested_output = Path(settings["outputdirectory"]).absolute()
    if os.path.lexists(requested_output):
        raise FileExistsError(f"Refusing existing output directory: {requested_output}")
    output = requested_output.resolve()
    dimension = positive_integer(base, "dim") if "dim" in base else None
    vector_limit = int(base.get("vectorsize", "-1"))
    vectors = open_vectors(required_path(base, "vectorpath"), base.get("valuetype", ""),
                           dimension=dimension, limit=vector_limit)
    query_source = open_vectors(required_path(base, "querypath"), vectors.value_type,
                                dimension=vectors.dimension,
                                limit=int(base.get("querysize", "-1")))
    query_count = (len(query_source.data) if settings.get("querycount") == "all"
                   else positive_integer(settings, "querycount"))
    if query_count > len(query_source.data):
        raise ValueError("GroundTruth.QueryCount exceeds the native query prefix")
    attributes_path = required_path(tags, "tagfile")
    attributes = open_attributes(attributes_path, len(vectors.data), len(schema),
                                 source_rows=vectors.source_rows)
    queries = np.ascontiguousarray(query_source.data[:query_count])
    float_queries = np.ascontiguousarray(queries, dtype="<f4")
    if not np.all(np.isfinite(float_queries)):
        raise ValueError("Query vectors must contain finite values")
    sources = {str(path): identity(path) for path in
               (vectors.path, query_source.path, attributes_path, config_path)}

    import faiss
    from threadpoolctl import threadpool_limits

    output.mkdir(parents=True, exist_ok=False)
    save_json(output / "started.json", dict(state="running", config=str(config_path),
              started_at=datetime.now(timezone.utc).isoformat()))
    try:
        previous_threads = faiss.omp_get_max_threads()
        faiss.omp_set_num_threads(threads)
        try:
            with threadpool_limits(limits=threads, user_api="blas"):
                manifest = generate_outputs(
                    output, config_path, settings, vectors, query_source.path, attributes_path,
                    attributes, schema, queries, float_queries, topk, scenarios,
                    chunk_rows, query_batch, threads, sources, faiss,
                )
        finally:
            faiss.omp_set_num_threads(previous_threads)
        for path, expected in sources.items():
            if identity(path) != expected:
                raise RuntimeError(f"Source changed during truth generation: {path}")
        save_json(output / "workloads.json", manifest)
        save_json(output / "completion.json", dict(
            state="complete", workloads_sha256=sha256(output / "workloads.json"),
            completed_at=datetime.now(timezone.utc).isoformat()))
    except Exception as error:
        save_json(output / "failure.json", dict(
            state="failed", error=str(error), failed_at=datetime.now(timezone.utc).isoformat()))
        raise
    print(json.dumps(dict(event="complete", output=str(output), scenarios=list(scenarios))), flush=True)
    return manifest


def generate_outputs(output, config_path, settings, vectors, query_path, attribute_source,
                     attributes, schema, queries, float_queries, topk, scenarios,
                     chunk_rows, query_batch, threads, sources, faiss):
    count, query_count = len(vectors.data), len(queries)
    best_ids = {name: np.full((query_count, topk), -1, dtype="<i8") for name in scenarios}
    best_distances = {name: np.full((query_count, topk), np.inf, dtype="<f4") for name in scenarios}
    matches = dict.fromkeys(scenarios, 0)
    copied_config = output / "input.ini"
    shutil.copyfile(config_path, copied_config)
    float_query_path, native_query_path = output / "query_vectors.npy", output / "query_vectors.native.npy"
    save_array(float_query_path, float_queries)
    save_array(native_query_path, queries)
    protected = {str(path): sha256(path) for path in
                 (copied_config, float_query_path, native_query_path)}
    attribute_output = output / "attributes.npy"
    header = io.BytesIO()
    np.lib.format.write_array_header_1_0(header, dict(
        descr=np.lib.format.dtype_to_descr(np.dtype("<u4")),
        fortran_order=False, shape=(count, len(schema))))
    attribute_digest = hashlib.sha256(header.getvalue())
    raw_attribute_digest = hashlib.sha256()
    # Streaming avoids accumulating resident mmap pages over a billion-row scan.
    with attribute_output.open("xb") as attribute_copy, attribute_source.open("rb") as attribute_stream, \
            vectors.path.open("rb") as vector_stream:
        attribute_copy.write(header.getvalue())
        for start in range(0, count, chunk_rows):
            end = min(count, start + chunk_rows)
            attrs = read_rows(attribute_stream, attributes.dtype, end - start, len(schema))
            attribute_copy.write(memoryview(attrs).cast("B"))
            attribute_digest.update(memoryview(attrs).cast("B"))
            raw_attribute_digest.update(memoryview(attrs).cast("B"))
            masks = {name: matching_rows(scenario["clauses"], attrs)
                     for name, scenario in scenarios.items()}
            for name, mask in masks.items():
                matches[name] += int(np.count_nonzero(mask))
            selected = np.flatnonzero(np.logical_or.reduce(list(masks.values())))
            if len(selected):
                vector_stream.seek(8 + start * vectors.dimension * vectors.data.dtype.itemsize)
                raw_vectors = read_rows(vector_stream, vectors.data.dtype, end - start, vectors.dimension)
                block = np.ascontiguousarray(raw_vectors[selected], dtype="<f4")
                if not np.all(np.isfinite(block)):
                    raise ValueError(f"Nonfinite base vectors in rows [{start}, {end})")
                positions = {name: np.flatnonzero(mask[selected]) for name, mask in masks.items()}
                ids = selected.astype(np.int64) + start
                for first in range(0, query_count, query_batch):
                    last = min(query_count, first + query_batch)
                    distances = faiss.pairwise_distances(
                        float_queries[first:last], block, metric=faiss.METRIC_L2)
                    if not np.all(np.isfinite(distances)):
                        raise ValueError(f"L2 overflow in rows [{start}, {end})")
                    # Float norm expansion can round a near-zero squared L2 below zero.
                    np.maximum(distances, 0, out=distances)
                    for name, selected_positions in positions.items():
                        if not len(selected_positions):
                            continue
                        candidate_ids = ids[selected_positions]
                        for offset, row in enumerate(distances):
                            query = first + offset
                            local_ids, local_distances = stable_topk(
                                candidate_ids, row[selected_positions], topk)
                            merged_ids, merged_distances = stable_topk(
                                np.concatenate((best_ids[name][query], local_ids)),
                                np.concatenate((best_distances[name][query], local_distances)), topk)
                            length = len(merged_ids)
                            best_ids[name][query, :length] = merged_ids
                            best_distances[name][query, :length] = merged_distances
            print(json.dumps(dict(event="truth_progress", rows=end, corpus=count,
                                  candidate_counts=matches)), flush=True)
        attribute_copy.flush()
        os.fsync(attribute_copy.fileno())
    protected[str(attribute_output)] = attribute_digest.hexdigest()
    predicates, native_predicates, truth, flat_tags, query_dnf = {}, {}, {}, {}, {}
    for name, scenario in scenarios.items():
        ids, distances = best_ids[name], best_distances[name]
        valid = ids >= 0
        expected = min(topk, matches[name])
        if (not np.all(valid.sum(axis=1) == expected)
                or not np.all(np.isfinite(distances[valid]))
                or not np.all(np.isinf(distances[~valid]))):
            raise RuntimeError(f"Incomplete or invalid truth result: {name}")
        ids_path = output / f"groundtruth_{name}_local_ids.npy"
        distances_path = output / f"groundtruth_{name}_dists.npy"
        binary_path = output / f"groundtruth_{name}.ibin"
        save_array(ids_path, ids)
        save_array(distances_path, distances)
        native_truth(binary_path, ids)
        for path in (ids_path, distances_path, binary_path):
            protected[str(path)] = sha256(path)
        truth[name] = dict(
            ids=str(ids_path), ids_sha256=protected[str(ids_path)], sha256=protected[str(ids_path)],
            distances=str(distances_path), distances_sha256=protected[str(distances_path)],
            native_ids=str(binary_path), native_ids_sha256=protected[str(binary_path)],
            candidate_count=matches[name], selectivity=matches[name] / count,
            provenance="Exhaustive squared L2 over every predicate-matching source row",
        )
        kind, payload = native_predicate(scenario["clauses"], schema, query_count)
        predicate_file = ""
        if payload is not None:
            prefix = "query_tags" if kind == "categorical" else "query_dnf"
            path = output / f"{prefix}_{name}.npy"
            save_array(path, payload)
            predicate_file = str(path)
            protected[predicate_file] = sha256(path)
            if kind == "categorical":
                flat_tags[name] = predicate_file
            else:
                query_dnf[name] = predicate_file
        predicates[name] = scenario["expression"]
        native_predicates[name] = dict(kind=kind, file=predicate_file)
    if "mixed_dnf" in query_dnf:
        query_dnf["mixed"] = query_dnf["mixed_dnf"]
    protected_large = {path: value for path, value in sources.items() if path != str(config_path)}
    code_files = (Path(__file__), Path(native_input_io.__file__), Path(predicate_groundtruth.__file__),
                  Path(__file__).with_name("validate_spann_hierarchy_config.py"),
                  Path(__file__).with_name("generate_sift1m_sparse_numeric_workloads.py"))
    manifest = dict(
        schema_version=2, generated_at_utc=datetime.now(timezone.utc).isoformat(),
        dataset=settings.get("dataset", "SPANN"), metric="squared L2",
        value_type=vectors.value_type, dimension=vectors.dimension, vector_count=count,
        corpus_count=count, source_vector_count=vectors.source_rows, query_count=query_count, topk=topk,
        base_file=str(vectors.path), query_file=str(query_path), attributes=str(attribute_output),
        attribute_source=str(attribute_source), column_types=list(schema),
        queries=str(native_query_path), float_queries=str(float_query_path),
        scenarios=list(scenarios), predicates=predicates, native_predicates=native_predicates,
        flat_query_tags=flat_tags, query_dnf=query_dnf, truth=truth,
        scenario_metadata={name: dict(title=scenario["title"], selectivity=matches[name] / count,
                                     eligible_count=matches[name], corpus_count=count)
                           for name, scenario in scenarios.items()},
        attributes_npy_sha256=attribute_digest.hexdigest(),
        selected_native_attributes_sha256=raw_attribute_digest.hexdigest(),
        protected=protected, protected_large=protected_large,
        source_config=dict(path=str(config_path), sha256=sha256(config_path),
                           working_directory=str(Path.cwd())),
        generator_sources={str(path.resolve()): sha256(path) for path in code_files},
        groundtruth=dict(
            engine="exhaustive FAISS pairwise squared L2, no approximate index",
            faiss_version=faiss.__version__, numpy_version=np.__version__, distance_dtype="float32",
            tie_break="ascending squared L2, then ascending original vector ID",
            threads=threads, chunk_rows=chunk_rows, query_batch=query_batch,
            missing_id=-1, missing_distance="positive infinity",
            max_distance_tile_bytes=min(chunk_rows, count) * min(query_batch, query_count) * 4,
        ),
    )
    for name, field in (("numeric", "numeric_threshold"), ("mixed_dnf", "mixed_threshold")):
        if name in scenarios:
            thresholds = {value for clause in scenarios[name]["clauses"]
                          for kind, _, operation, value in clause if kind == 1 and operation == 2}
            if len(thresholds) == 1:
                manifest[field] = thresholds.pop()
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        generate(args.config)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        parser.exit(1, f"[predicate-groundtruth] {error}\n")


if __name__ == "__main__":
    main()
