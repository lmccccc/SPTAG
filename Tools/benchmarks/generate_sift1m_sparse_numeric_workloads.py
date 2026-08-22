#!/usr/bin/env python3
"""Generate exact SIFT1M workloads for sparse-tag and numeric DNF routing."""

import argparse
import hashlib
import json
import struct
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


DNF3_MAGIC = 0x444E4633
DNF_CATEGORICAL = 0
DNF_NUMERIC = 1
DNF_EQ = 0
DNF_LE = 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-file",
        type=Path,
        default=Path(
            "/home/v-mochengli/datasets/sift1m/sift/"
            "sift_base.fvecs"
        ),
    )
    parser.add_argument(
        "--query-file",
        type=Path,
        default=Path(
            "/home/v-mochengli/datasets/sift1m/sift/"
            "sift_query.fvecs"
        ),
    )
    parser.add_argument(
        "--unfiltered-truth",
        type=Path,
        default=Path(
            "/home/v-mochengli/datasets/sift1m/sift/"
            "sift_groundtruth.ivecs"
        ),
    )
    parser.add_argument("--attributes", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--query-count", type=int, default=1000)
    parser.add_argument("--topk", type=int, default=10)
    parser.add_argument("--query-batch", type=int, default=64)
    parser.add_argument("--extreme-tag-id", type=int, default=200)
    parser.add_argument("--medium-tag-id", type=int, default=9)
    parser.add_argument("--broad-tag-id", type=int, default=0)
    parser.add_argument("--mixed-tag-id", type=int, default=199)
    parser.add_argument(
        "--numeric-selectivity",
        type=float,
        default=0.01,
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def read_xvecs(path: Path, dtype: np.dtype, limit: int = 0) -> np.ndarray:
    with path.open("rb") as stream:
        header = stream.read(4)
    if len(header) != 4:
        raise ValueError(f"{path}: missing xvec dimension")
    dimension = struct.unpack("<i", header)[0]
    item = np.dtype(dtype)
    stride = dimension + 1
    raw = np.fromfile(
        path,
        dtype=item,
        count=limit * stride if limit else -1,
    )
    if dimension <= 0 or raw.size % stride:
        raise ValueError(f"{path}: invalid xvec payload")
    rows = raw.reshape(-1, stride)
    dimension_words = raw.view("<i4").reshape(
        -1, stride
    )[:, 0]
    if np.any(dimension_words != dimension):
        raise ValueError(f"{path}: inconsistent xvec dimensions")
    return np.ascontiguousarray(rows[:, 1:])


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def exact_topk(
    base_records: np.ndarray,
    queries: np.ndarray,
    candidate_ids: np.ndarray,
    topk: int,
    query_batch: int,
) -> tuple[np.ndarray, np.ndarray]:
    output_ids = np.full(
        (queries.shape[0], topk), -1, dtype=np.int64
    )
    output_distances = np.full(
        (queries.shape[0], topk), np.inf, dtype=np.float32
    )
    if candidate_ids.size == 0:
        return output_ids, output_distances

    candidate_vectors = np.ascontiguousarray(
        base_records[candidate_ids, 1:], dtype=np.float32
    )
    candidate_norms = np.einsum(
        "ij,ij->i", candidate_vectors, candidate_vectors
    ).astype(np.float32)
    k = min(topk, candidate_ids.size)
    for start in range(0, queries.shape[0], query_batch):
        end = min(start + query_batch, queries.shape[0])
        query_block = queries[start:end]
        query_norms = np.einsum(
            "ij,ij->i", query_block, query_block
        ).astype(np.float32)
        distances = (
            candidate_norms[:, None]
            - 2.0 * (candidate_vectors @ query_block.T)
            + query_norms[None, :]
        )
        np.maximum(distances, 0.0, out=distances)
        for column in range(end - start):
            values = distances[:, column]
            if k < values.size:
                selected = np.argpartition(
                    values, k - 1
                )[:k]
            else:
                selected = np.arange(values.size)
            order = np.lexsort(
                (
                    candidate_ids[selected],
                    values[selected],
                )
            )
            selected = selected[order]
            output_ids[start + column, :k] = (
                candidate_ids[selected]
            )
            output_distances[start + column, :k] = (
                values[selected]
            )
        print(
            f"  exact truth {end}/{queries.shape[0]} "
            f"(candidates={candidate_ids.size})",
            flush=True,
        )
    return output_ids, output_distances


def encode_dnf3(
    clauses: list[list[tuple[int, int, int, int]]]
) -> np.ndarray:
    words = [DNF3_MAGIC, len(clauses)]
    for clause in clauses:
        words.append(len(clause))
        for kind, column, operation, value in clause:
            words.extend((kind, column, operation, value))
    return np.asarray(words, dtype="<u4")


def repeat_length_prefixed(
    words: np.ndarray, query_count: int
) -> np.ndarray:
    rows = np.zeros(
        (query_count, words.size + 1), dtype="<u4"
    )
    rows[:, 0] = words.size
    rows[:, 1:] = words
    return rows


def write_truth(
    output_dir: Path,
    name: str,
    ids: np.ndarray,
    distances: np.ndarray | None,
) -> dict[str, object]:
    ids_path = output_dir / f"groundtruth_{name}_local_ids.npy"
    np.save(ids_path, np.ascontiguousarray(ids, dtype="<i8"))
    result: dict[str, object] = {
        "ids": str(ids_path),
        "candidate_count": int(np.count_nonzero(ids[0] >= 0)),
        "sha256": sha256(ids_path),
    }
    if distances is not None:
        distances_path = (
            output_dir / f"groundtruth_{name}_dists.npy"
        )
        np.save(
            distances_path,
            np.ascontiguousarray(distances, dtype="<f4"),
        )
        result["distances"] = str(distances_path)
        result["distance_sha256"] = sha256(distances_path)
    return result


def main() -> None:
    args = parse_args()
    if args.query_count <= 0 or args.topk <= 0:
        raise ValueError("query-count and topk must be positive")
    if args.query_batch <= 0:
        raise ValueError("query-batch must be positive")
    if not 0.0 < args.numeric_selectivity <= 1.0:
        raise ValueError(
            "numeric-selectivity must be in (0,1]"
        )
    for path in (
        args.base_file,
        args.query_file,
        args.unfiltered_truth,
        args.attributes,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    attributes = np.load(
        args.attributes, allow_pickle=False, mmap_mode="r"
    )
    if (
        attributes.dtype != np.dtype("<u4")
        or attributes.shape != (1_000_000, 2)
    ):
        raise ValueError(
            "attributes must be a uint32 [1000000,2] matrix"
        )
    key_tags = np.asarray(attributes[:, 0], dtype=np.uint32)
    numeric = np.asarray(attributes[:, 1], dtype=np.uint32)
    queries = read_xvecs(
        args.query_file, np.dtype("<f4"), args.query_count
    )
    if queries.shape != (args.query_count, 128):
        raise ValueError("unexpected SIFT query shape")

    base_file_bytes = args.base_file.stat().st_size
    expected_base_bytes = (
        1_000_000 * 129 * np.dtype("<f4").itemsize
    )
    if base_file_bytes != expected_base_bytes:
        raise ValueError("unexpected SIFT base size")
    base_records = np.memmap(
        args.base_file,
        dtype="<f4",
        mode="r",
        shape=(1_000_000, 129),
    )
    if np.any(base_records[:, 0].view("<i4") != 128):
        raise ValueError("inconsistent SIFT base dimensions")

    output_dir = args.output_dir.resolve()
    outputs = [
        output_dir / "query_vectors.npy",
        output_dir / "query_tags_extreme.npy",
        output_dir / "query_tags_medium.npy",
        output_dir / "query_tags_broad.npy",
        output_dir / "query_dnf_numeric.npy",
        output_dir / "query_dnf_mixed.npy",
        output_dir / "workloads.json",
    ]
    truth_names = (
        "unfilter",
        "extreme_tag",
        "medium_tag",
        "broad_tag",
        "numeric",
        "mixed_dnf",
    )
    outputs.extend(
        output_dir / f"groundtruth_{name}_local_ids.npy"
        for name in truth_names
    )
    outputs.extend(
        output_dir / f"groundtruth_{name}_dists.npy"
        for name in truth_names
        if name != "unfilter"
    )
    existing = [path for path in outputs if path.exists()]
    if existing and not args.overwrite:
        raise FileExistsError(
            "Refusing to overwrite generated workloads:\n  "
            + "\n  ".join(str(path) for path in existing)
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    np.save(
        output_dir / "query_vectors.npy",
        np.ascontiguousarray(queries, dtype="<f4"),
    )

    tag_ids = {
        "extreme_tag": args.extreme_tag_id,
        "medium_tag": args.medium_tag_id,
        "broad_tag": args.broad_tag_id,
    }
    query_tag_files: dict[str, str] = {}
    for name, tag in tag_ids.items():
        candidates = np.flatnonzero(key_tags == tag)
        if candidates.size < args.topk:
            raise ValueError(
                f"{name} has only {candidates.size} candidates"
            )
        query_tags = np.full(
            (args.query_count, 1), tag, dtype="<u4"
        )
        path = output_dir / f"query_tags_{name[:-4]}.npy"
        np.save(path, query_tags)
        query_tag_files[name] = str(path)

    numeric_count = max(
        args.topk,
        int(round(
            numeric.size * args.numeric_selectivity
        )),
    )
    numeric_threshold = int(
        np.partition(numeric, numeric_count - 1)[
            numeric_count - 1
        ]
    )
    numeric_mask = numeric <= numeric_threshold
    if int(np.count_nonzero(numeric_mask)) != numeric_count:
        raise RuntimeError(
            "numeric values are not unique at the threshold"
        )

    mixed_threshold = 0x7FFFFFFF
    mixed_mask = (
        (key_tags == args.extreme_tag_id)
        | (
            (key_tags == args.mixed_tag_id)
            & (numeric <= mixed_threshold)
        )
    )
    if np.count_nonzero(mixed_mask) < args.topk:
        raise ValueError("mixed DNF has fewer than topk matches")

    numeric_dnf = encode_dnf3(
        [[(DNF_NUMERIC, 1, DNF_LE, numeric_threshold)]]
    )
    mixed_dnf = encode_dnf3(
        [
            [
                (
                    DNF_CATEGORICAL,
                    0,
                    DNF_EQ,
                    args.extreme_tag_id,
                )
            ],
            [
                (
                    DNF_CATEGORICAL,
                    0,
                    DNF_EQ,
                    args.mixed_tag_id,
                ),
                (
                    DNF_NUMERIC,
                    1,
                    DNF_LE,
                    mixed_threshold,
                ),
            ],
        ]
    )
    numeric_dnf_path = output_dir / "query_dnf_numeric.npy"
    mixed_dnf_path = output_dir / "query_dnf_mixed.npy"
    np.save(
        numeric_dnf_path,
        repeat_length_prefixed(
            numeric_dnf, args.query_count
        ),
    )
    np.save(
        mixed_dnf_path,
        repeat_length_prefixed(
            mixed_dnf, args.query_count
        ),
    )

    official_truth = read_xvecs(
        args.unfiltered_truth, np.dtype("<i4"),
        args.query_count
    )
    if official_truth.shape[1] < args.topk:
        raise ValueError(
            "unfiltered truth has fewer than topk columns"
        )
    truth_manifest = {
        "unfilter": write_truth(
            output_dir,
            "unfilter",
            official_truth[:, : args.topk],
            None,
        )
    }
    truth_manifest["unfilter"]["candidate_count"] = (
        key_tags.size
    )
    truth_manifest["unfilter"]["selectivity"] = 1.0
    workload_masks = {
        "extreme_tag": key_tags == args.extreme_tag_id,
        "medium_tag": key_tags == args.medium_tag_id,
        "broad_tag": key_tags == args.broad_tag_id,
        "numeric": numeric_mask,
        "mixed_dnf": mixed_mask,
    }
    for name, mask in workload_masks.items():
        candidates = np.flatnonzero(mask).astype(np.int64)
        print(
            f"{name}: {candidates.size} candidates",
            flush=True,
        )
        ids, distances = exact_topk(
            base_records,
            queries,
            candidates,
            args.topk,
            args.query_batch,
        )
        truth_manifest[name] = write_truth(
            output_dir, name, ids, distances
        )
        truth_manifest[name]["candidate_count"] = int(
            candidates.size
        )
        truth_manifest[name]["selectivity"] = float(
            candidates.size / key_tags.size
        )

    manifest = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(
            timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dataset": "SIFT1M",
        "metric": "squared L2",
        "query_count": args.query_count,
        "topk": args.topk,
        "base_file": str(args.base_file.resolve()),
        "query_file": str(args.query_file.resolve()),
        "attributes": str(args.attributes.resolve()),
        "query_vectors": str(
            output_dir / "query_vectors.npy"
        ),
        "flat_query_tags": query_tag_files,
        "dnf_encoding": (
            "uint32 NPY rows: [word_count, DNF3 words..., "
            "zero padding]"
        ),
        "query_dnf": {
            "numeric": str(numeric_dnf_path),
            "mixed": str(mixed_dnf_path),
        },
        "predicates": {
            "extreme_tag": {
                "categorical_eq": [0, args.extreme_tag_id]
            },
            "medium_tag": {
                "categorical_eq": [0, args.medium_tag_id]
            },
            "broad_tag": {
                "categorical_eq": [0, args.broad_tag_id]
            },
            "numeric": {
                "numeric_le": [1, numeric_threshold]
            },
            "mixed_dnf": {
                "or": [
                    {
                        "categorical_eq": [
                            0,
                            args.extreme_tag_id,
                        ]
                    },
                    {
                        "and": [
                            {
                                "categorical_eq": [
                                    0,
                                    args.mixed_tag_id,
                                ]
                            },
                            {
                                "numeric_le": [
                                    1,
                                    mixed_threshold,
                                ]
                            },
                        ]
                    },
                ]
            },
        },
        "truth": truth_manifest,
    }
    manifest_path = output_dir / "workloads.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"workloads: {manifest_path}")


if __name__ == "__main__":
    main()
