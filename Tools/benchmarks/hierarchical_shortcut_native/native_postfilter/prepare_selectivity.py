#!/usr/bin/env python3
"""Prepare a native UInt8 cohort and exact categorical truth from a fixed INI."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

import numpy as np

from run_full import predicate_mask, read_ini, require, sha
from run_postgraph import save
from selectivity_common import SelectedPredicate, identity, validate_identities, verify_truth

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from gen_sift1b_attrs import u8bin_shape
from generate_sift1m_sparse_numeric_workloads import (
    DNF_CATEGORICAL, DNF_EQ, DNF_LE, DNF_NUMERIC, encode_dnf3, repeat_length_prefixed,
)


def save_array(path, array):
    with Path(path).open("xb") as stream:
        np.save(stream, array, allow_pickle=False)
        stream.flush()
        os.fsync(stream.fileno())


def scalar_top_distances(vectors, query, topk=10, chunk=65536):
    best = np.empty(0, dtype=np.int64)
    for start in range(0, len(vectors), chunk):
        delta = vectors[start:start + chunk].astype(np.int32) - query.astype(np.int32)
        distance = np.einsum("ij,ij->i", delta, delta, dtype=np.int64)
        keep = min(topk, len(distance))
        best = np.concatenate((best, np.partition(distance, keep - 1)[:keep]))
        best = np.sort(best)[:topk]
    return best


def prepare(path):
    config = read_ini(path)
    settings = config["Inputs"]
    require(settings["ValueType"] == "UInt8", "This preparation requires native UInt8 data")
    count, topk = settings.getint("QueryCount"), settings.getint("TopK")
    require(count == 1000 and topk == 10, "Expected registered1000-query top10 cohort")
    source = json.loads(Path(settings["SourceWorkloads"]).read_text())
    attributes_manifest = json.loads(Path(settings["AttributeManifest"]).read_text())
    base_path, query_path = Path(source["base_file"]), Path(source["query_file"])
    rows, dimension = u8bin_shape(base_path)
    query_rows, query_dimension = u8bin_shape(query_path)
    require(rows == settings.getint("CorpusCount") and dimension == query_dimension == 128 and
            query_rows >= count, "Native corpus/query shape mismatch")
    require(source["query_count"] == count and source["topk"] == topk, "Original cohort differs")
    attributes_path = Path(source["attributes"])
    raw_attributes_path = Path(attributes_manifest["files"]["sptag_attributes"]["path"])
    require(attributes_path == Path(attributes_manifest["files"]["numpy_attributes"]["path"]),
            "Attribute manifest refers to a different NPY")
    require(attributes_manifest["vector_count"] == rows and
            attributes_manifest["source_base_file"] == str(base_path), "Attribute corpus differs")
    require(raw_attributes_path.stat().st_size == rows * 2 * 4, "Native attribute extent differs")
    protected_large = {str(p): identity(p) for p in
                       (base_path, attributes_path, raw_attributes_path)}
    original_queries_path = Path(settings["OriginalQueries"])
    original_queries = np.load(original_queries_path, mmap_mode="r", allow_pickle=False)
    queries = np.fromfile(query_path, dtype=np.uint8, count=count * dimension, offset=8).reshape(count, dimension)
    require(original_queries.shape == queries.shape and original_queries.dtype == np.dtype("<f4") and
            np.array_equal(original_queries, queries.astype(np.float32)),
            "Native UInt8 queries differ from the original exact-truth cohort")
    attributes = np.load(attributes_path, mmap_mode="r", allow_pickle=False)
    require(attributes.dtype == np.dtype("<u4") and attributes.shape == (rows, 2) and
            attributes.flags.c_contiguous, "Expected original categorical,numeric UInt32 attributes")
    raw_attributes = np.memmap(raw_attributes_path, mode="r", dtype="<u4", shape=attributes.shape)
    base = np.memmap(base_path, mode="r", dtype=np.uint8, offset=8, shape=(rows, dimension))
    names = [name.strip() for name in settings["Scenarios"].split(",")]
    require(len(names) == len(set(names)) and names[0] == "unfilter" and "mixed_dnf" in names and
            not set(names) & {"numeric", "extreme_tag"}, "Invalid requested scenario scope")
    sections = {name: config[f"Scenario.{name}"] for name in names}
    generated = [name for name in names if sections[name].get("TruthKey", "") == ""]
    require(generated == ["sel_01pct"], "Only the new0.1-percent categorical truth may be generated")
    predicates, native_predicates, files = {}, {}, set()
    for name, section in sections.items():
        kind = section["Predicate"]
        if kind == "empty":
            require(name == "unfilter", "Only unfilter may use an empty predicate")
            predicates[name], native_predicates[name] = None, dict(kind=kind, file="")
        elif kind == "categorical":
            value = section.getint("Tag")
            predicates[name] = dict(categorical_eq=[0, value])
            native_predicates[name] = dict(kind=kind, file=section.get("PredicateFile", ""))
            if native_predicates[name]["file"]:
                tags = np.load(native_predicates[name]["file"], mmap_mode="r", allow_pickle=False)
                require(tags.dtype == np.dtype("<u4") and tags.shape == (count, 1) and
                        np.all(tags == value), f"Native categorical predicate differs: {name}")
                files.add(Path(native_predicates[name]["file"]))
        else:
            require(kind == "dnf" and name == "mixed_dnf", "Unsupported scenario predicate")
            rare, tag, upper = (section.getint(key) for key in ("RareTag", "Tag", "UpperInclusive"))
            require(upper == source["mixed_threshold"], "Original mixed threshold differs")
            predicates[name] = {"or": [{"categorical_eq": [0, rare]},
                {"and": [{"categorical_eq": [0, tag]}, {"numeric_le": [1, upper]}]}]}
            expected = repeat_length_prefixed(encode_dnf3([
                [(DNF_CATEGORICAL, 0, DNF_EQ, rare)],
                [(DNF_CATEGORICAL, 0, DNF_EQ, tag), (DNF_NUMERIC, 1, DNF_LE, upper)],
            ]), count)
            predicate_path = Path(section["PredicateFile"])
            actual = np.load(predicate_path, mmap_mode="r", allow_pickle=False)
            require(actual.dtype == expected.dtype and np.array_equal(actual, expected),
                    "Original native DNF blob differs from exact predicate")
            native_predicates[name] = dict(kind=kind, file=str(predicate_path))
            files.add(predicate_path)
    output = Path(settings["OutputDirectory"])
    require(not output.exists(), "Preparation output already exists; retain it and choose a new output")
    output.mkdir(parents=True)
    save(output / "status.json", dict(state="preparing", stage="authenticating_attributes"))
    counts = {name: 0 for name in names}
    counts["unfilter"] = rows
    candidates = []
    chunk = settings.getint("AttributeChunkRows")
    require(chunk > 0, "AttributeChunkRows must be positive")
    npy_hash, raw_hash = hashlib.sha256(), hashlib.sha256()
    with attributes_path.open("rb") as stream:
        npy_hash.update(stream.read(attributes.offset))
    for start in range(0, rows, chunk):
        end = min(rows, start + chunk)
        block = attributes[start:end]
        require(np.array_equal(block, raw_attributes[start:end]),
                f"Original NPY/native attribute payload differs at row{start}")
        npy_hash.update(memoryview(block))
        raw_hash.update(memoryview(block))
        for name in names:
            if name == "unfilter":
                continue
            matches = predicate_mask(predicates[name], block)
            counts[name] += int(np.count_nonzero(matches))
            if name in generated:
                candidates.append(np.flatnonzero(matches).astype(np.int64) + start)
        if end == rows or (end // chunk) % 25 == 0:
            print(json.dumps(dict(event="attribute_progress", rows=end, corpus=rows)), flush=True)
    require(npy_hash.hexdigest() == attributes_manifest["files"]["numpy_attributes"]["sha256"],
            "Original attribute NPY authentication failed")
    require(raw_hash.hexdigest() == attributes_manifest["files"]["sptag_attributes"]["sha256"],
            "Original native attribute authentication failed")
    candidate_ids = np.concatenate(candidates)
    require(len(candidate_ids) == counts["sel_01pct"] and np.all(np.diff(candidate_ids) > 0),
            "Incomplete or duplicate generated candidate IDs")
    minimum = settings.getfloat("MinimumCategoricalSelectivity")
    tolerance = settings.getfloat("NarrowSelectivityRelativeTolerance")
    actual_selectivity = len(candidate_ids) / rows
    require(minimum > 0 and tolerance > 0 and
            minimum <= actual_selectivity <= minimum * (1 + tolerance),
            "New categorical case is not the requested0.1-percent boundary")
    queries_output = output / "query_vectors.u8.npy"
    tags_output = output / "query_tags_sel_01pct.npy"
    save_array(queries_output, queries)
    save_array(tags_output, np.full((count, 1), sections["sel_01pct"].getint("Tag"), dtype="<u4"))
    native_predicates["sel_01pct"]["file"] = str(tags_output)
    files.update((queries_output, tags_output, Path(settings["SourceWorkloads"]),
                  Path(settings["AttributeManifest"]), original_queries_path, query_path, Path(path)))
    truth = {}
    for name in names:
        if name in generated:
            continue
        original = source["truth"][sections[name]["TruthKey"]]
        require(counts[name] == original["candidate_count"] and
                counts[name] / rows == original["selectivity"], f"Original selectivity differs: {name}")
        ids_path = Path(original["ids"])
        require(sha(ids_path) == original["ids_sha256"], f"Original exact truth changed: {name}")
        ids = np.load(ids_path, mmap_mode="r", allow_pickle=False)
        distances = None
        if "distances" in original:
            distance_path = Path(original["distances"])
            require(sha(distance_path) == original["distances_sha256"], f"Truth distances changed: {name}")
            distances = np.load(distance_path, mmap_mode="r", allow_pickle=False)
            files.add(distance_path)
        verify_truth(ids, distances, base, queries, SelectedPredicate(attributes, predicates[name]))
        truth[name] = dict(original, sha256=original["ids_sha256"], provenance="original authenticated exact truth")
        files.add(ids_path)
    save(output / "status.json", dict(state="preparing", stage="exact_categorical_truth",
                                     candidates=len(candidate_ids)))
    import faiss
    from generate_sift1b_acl_groundtruth import knn_l2
    threads = settings.getint("TruthThreads")
    require(threads > 0, "TruthThreads must be positive")
    faiss.omp_set_num_threads(threads)
    candidate_vectors = np.ascontiguousarray(base[candidate_ids], dtype=np.float32)
    positions, distances = knn_l2(queries.astype(np.float32), candidate_vectors, topk + 1)
    ids = candidate_ids[positions[:, :topk]]
    verify_truth(ids, distances[:, :topk], base, queries,
                 SelectedPredicate(attributes, predicates["sel_01pct"]))
    audit_queries = np.linspace(0, count - 1, settings.getint("TruthAuditQueries"), dtype=int)
    require(0 < len(audit_queries) <= count, "Invalid exact-truth audit cohort")
    for query in audit_queries:
        expected = scalar_top_distances(candidate_vectors, queries[query])
        require(np.array_equal(expected, distances[query, :topk]),
                f"Independent integer exhaustive truth differs at query{query}")
    ids_output = output / "groundtruth_sel_01pct_local_ids.npy"
    distances_output = output / "groundtruth_sel_01pct_dists.npy"
    save_array(ids_output, ids.astype("<i8"))
    save_array(distances_output, distances[:, :topk].astype("<f4"))
    files.update((ids_output, distances_output))
    truth["sel_01pct"] = dict(ids=str(ids_output), sha256=sha(ids_output),
        distances=str(distances_output), distances_sha256=sha(distances_output),
        candidate_count=len(candidate_ids), selectivity=actual_selectivity,
        provenance="Exhaustive FAISS L2 over every matching vector; exact integer top-distance cross-check",
        boundary_tie_queries=int(np.count_nonzero(distances[:, 9] == distances[:, 10])))
    metadata = {name: dict(title=sections[name]["Title"], selectivity=counts[name] / rows,
                          eligible_count=counts[name], corpus_count=rows) for name in names}
    files.add(Path(attributes_manifest["files"]["counts"]["path"]))
    require(sha(attributes_manifest["files"]["counts"]["path"]) ==
            attributes_manifest["files"]["counts"]["sha256"], "Original count table changed")
    protected = {str(p.resolve()): sha(p) for p in sorted(files)}
    validate_identities(protected_large)
    manifest = dict(schema_version=1, prepared_at=datetime.now(timezone.utc).isoformat(),
        corpus_count=rows, dimension=128, query_count=count, topk=topk, value_type="UInt8",
        queries=str(queries_output), original_queries=str(original_queries_path),
        query_cohort="Exact first1000 native query rows, byte-equivalent to original Float32 truth cohort",
        attributes=str(attributes_path), base_file=str(base_path), scenarios=names,
        predicates=predicates, native_predicates=native_predicates, truth=truth,
        scenario_metadata=metadata, protected=protected, protected_large=protected_large,
        attributes_npy_sha256=npy_hash.hexdigest(), native_attributes_sha256=raw_hash.hexdigest(),
        attribute_validation="All NPY/native rows match and both authenticated original digests match",
        groundtruth=dict(engine="faiss.knn exact METRIC_L2, no approximate index",
                         faiss_version=faiss.__version__, threads=threads,
                         audit_queries=audit_queries.tolist(), uint8_distance_integer_exact=True),
        exclusions=["numeric-only", "extreme399-point categorical"],
        mixed_scope="Original mixed DNF is separate from the categorical0.1-percent lower boundary",
        data_io_scope="One bounded attribute scan and matching raw-vector reads; no SSD posting-store scan")
    save(output / "workloads.json", manifest)
    save(output / "status.json", dict(state="complete", workloads_sha256=sha(output / "workloads.json")))
    print(json.dumps(dict(event="prepared", output=str(output), scenarios=metadata)), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ini", type=Path)
    args = parser.parse_args()
    try:
        prepare(args.ini.resolve())
    except Exception as error:
        output = Path(read_ini(args.ini)["Inputs"]["OutputDirectory"])
        if output.exists() and not (output / "workloads.json").exists():
            save(output / "failure.json", dict(error=str(error)))
        raise
