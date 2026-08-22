#!/usr/bin/env python3
"""Generate reproducible categorical/numeric attributes for SIFT1M."""

import argparse
import hashlib
import json
import os
import struct
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np

from generate_tenant_tag_scenario import zipf_counts


DEFAULT_BASE = Path("/home/v-mochengli/datasets/sift1m/sift/sift_base.fvecs")
DEFAULT_OUTPUT = Path("/datadisk/yfcc_fast/sptag_sift1m_zipf200")
MAX_ATTRIBUTE_CARDINALITY = 200


def parse_ratio(value: str) -> Decimal:
    try:
        ratio = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError(
            f"invalid ratio: {value}"
        ) from error
    if not ratio.is_finite():
        raise argparse.ArgumentTypeError(
            "ratio must be finite"
        )
    return ratio


def count_from_ratio(
    vector_count: int, ratio: Decimal
) -> int:
    if ratio < 0 or ratio >= 1:
        raise ValueError(
            "extreme-tag-ratio must be in [0,1)"
        )
    if ratio == 0:
        return 0
    count = int(Decimal(vector_count) * ratio)
    if count == 0:
        raise ValueError(
            "extreme-tag-ratio selects fewer than one vector"
        )
    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-file", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--attribute-cardinality", type=int, default=200)
    parser.add_argument("--zipf-exponent", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=20260817)
    parser.add_argument(
        "--extreme-tag-ratio",
        type=parse_ratio,
        default=Decimal("0"),
        help=(
            "Append one categorical value with "
            "floor(vector_count * ratio) members."
        ),
    )
    parser.add_argument(
        "--numeric-column",
        action="store_true",
        help="Append one deterministic uint32 numeric column.",
    )
    parser.add_argument("--numeric-seed", type=int, default=20260821)
    parser.add_argument(
        "--output-prefix",
        default="",
        help="Override the generated file prefix.",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def fvecs_info(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(4)
    if len(header) != 4:
        raise ValueError(f"{path}: missing fvecs dimension header")
    dimension = struct.unpack("<i", header)[0]
    record_bytes = (dimension + 1) * np.dtype("<f4").itemsize
    file_bytes = path.stat().st_size
    if dimension <= 0 or file_bytes % record_bytes:
        raise ValueError(f"{path}: invalid fvecs size {file_bytes} for dimension {dimension}")
    return dimension, file_bytes // record_bytes


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_outputs_available(paths: list[Path], overwrite: bool) -> None:
    existing = [path for path in paths if path.exists()]
    if existing and not overwrite:
        joined = "\n  ".join(str(path) for path in existing)
        raise FileExistsError(f"Refusing to overwrite generated inputs:\n  {joined}")


def main() -> None:
    args = parse_args()
    if not args.base_file.is_file():
        raise FileNotFoundError(args.base_file)
    if not 1 <= args.attribute_cardinality <= MAX_ATTRIBUTE_CARDINALITY:
        raise ValueError(
            f"attribute-cardinality must be in [1, {MAX_ATTRIBUTE_CARDINALITY}]"
        )
    if not np.isfinite(args.zipf_exponent) or args.zipf_exponent <= 0:
        raise ValueError("zipf-exponent must be finite and positive")
    dimension, vector_count = fvecs_info(args.base_file)
    if (vector_count, dimension) != (1_000_000, 128):
        raise ValueError(
            f"Expected canonical SIFT1M shape (1000000, 128), got "
            f"({vector_count}, {dimension})"
        )

    output_dir = args.output_dir.resolve()
    extreme_tag_count = count_from_ratio(
        vector_count, args.extreme_tag_ratio
    )
    if args.output_prefix:
        prefix = args.output_prefix
    else:
        prefix = f"sift1m_zipf{args.attribute_cardinality}"
        if extreme_tag_count:
            prefix += f"_sparse{extreme_tag_count}"
        if args.numeric_column:
            prefix += "_numeric"
    payload_name = "attrs" if args.numeric_column else "tags"
    raw_tags_path = output_dir / f"{prefix}_{payload_name}.u32"
    npy_tags_path = output_dir / f"{prefix}_{payload_name}.npy"
    key_tags_path = output_dir / f"{prefix}_key_tags.npy"
    numeric_path = output_dir / f"{prefix}_numeric.npy"
    group_tags_path = output_dir / f"{prefix}_group_tags.txt"
    counts_path = output_dir / f"{prefix}_counts.tsv"
    manifest_path = output_dir / "manifest.json"
    outputs = [
        raw_tags_path,
        npy_tags_path,
        key_tags_path,
        group_tags_path,
        counts_path,
        manifest_path,
    ]
    if args.numeric_column:
        outputs.append(numeric_path)
    ensure_outputs_available(outputs, args.overwrite)
    output_dir.mkdir(parents=True, exist_ok=True)

    regular_vector_count = (
        vector_count - extreme_tag_count
    )
    counts = zipf_counts(
        args.attribute_cardinality,
        regular_vector_count,
        args.zipf_exponent,
    )
    if extreme_tag_count:
        counts = np.concatenate(
            (
                counts,
                np.asarray(
                    [extreme_tag_count],
                    dtype=np.int64,
                ),
            )
        )
    if int(counts.sum()) != vector_count or np.any(counts <= 0):
        raise RuntimeError("Zipf rounding did not produce positive counts summing to SIFT1M")

    tags = np.repeat(
        np.arange(counts.size, dtype=np.uint32), counts
    )
    np.random.default_rng(args.seed).shuffle(tags)
    observed = np.bincount(tags, minlength=counts.size)
    if not np.array_equal(observed, counts):
        raise RuntimeError("Shuffled attribute counts do not match the Zipf allocation")

    little_endian_tags = np.ascontiguousarray(tags, dtype="<u4")
    np.save(key_tags_path, little_endian_tags.reshape(vector_count, 1))
    attributes = little_endian_tags.reshape(vector_count, 1)
    numeric_values = None
    if args.numeric_column:
        vector_ids = np.arange(vector_count, dtype=np.uint64)
        numeric_values = np.asarray(
            (
                vector_ids * np.uint64(2654435761) +
                np.uint64(args.numeric_seed)
            )
            & np.uint64(0xFFFFFFFF),
            dtype="<u4",
        )
        if np.unique(numeric_values).size != vector_count:
            raise RuntimeError(
                "Numeric construction is not unique over SIFT1M"
            )
        attributes = np.ascontiguousarray(
            np.column_stack(
                (little_endian_tags, numeric_values)
            ),
            dtype="<u4",
        )
        np.save(numeric_path, numeric_values.reshape(vector_count, 1))
    attributes.tofile(raw_tags_path)
    np.save(npy_tags_path, attributes)
    np.savetxt(group_tags_path, little_endian_tags, fmt="%u")

    with counts_path.open("w", encoding="utf-8") as stream:
        stream.write(
            "attribute_id\trank\tcount\tselectivity\tclass\n"
        )
        for attribute_id, count in enumerate(counts):
            value_class = (
                "extreme"
                if extreme_tag_count and
                attribute_id == args.attribute_cardinality
                else "zipf"
            )
            stream.write(
                f"{attribute_id}\t{attribute_id + 1}\t{int(count)}\t"
                f"{count / vector_count:.12f}\t{value_class}\n"
            )

    manifest = {
        "schema_version": 3,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dataset": "SIFT1M",
        "source_base_file": str(args.base_file.resolve()),
        "source_base_size": os.path.getsize(args.base_file),
        "vector_count": vector_count,
        "dimension": dimension,
        "attribute_columns": int(attributes.shape[1]),
        "categorical_columns": 1,
        "numeric_columns": int(args.numeric_column),
        "limited_tag_column": 0,
        "attribute_cardinality": args.attribute_cardinality,
        "total_categorical_cardinality": int(counts.size),
        "attribute_id_range": {
            "min": 0,
            "max": int(counts.size - 1),
        },
        "distribution": "Zipf",
        "zipf_exponent": args.zipf_exponent,
        "rounding": "largest-remainder",
        "assignment": "random-permutation-of-exact-counts",
        "seed": args.seed,
        "extreme_tag_id": (
            args.attribute_cardinality
            if extreme_tag_count
            else None
        ),
        "extreme_tag_ratio_requested": float(
            args.extreme_tag_ratio
        ),
        "extreme_tag_count": extreme_tag_count,
        "extreme_tag_selectivity": (
            extreme_tag_count / vector_count
            if extreme_tag_count
            else 0.0
        ),
        "extreme_tag_rounding": (
            "floor(vector_count * ratio)"
        ),
        "numeric_generation": (
            {
                "formula": "(vid * 2654435761 + seed) mod 2^32",
                "seed": args.numeric_seed,
                "unique": True,
            }
            if args.numeric_column
            else None
        ),
        "largest_count": int(counts[0]),
        "largest_selectivity": float(counts[0] / vector_count),
        "smallest_count": int(counts[-1]),
        "smallest_selectivity": float(counts[-1] / vector_count),
        "files": {
            "sptag_attributes": {
                "path": str(raw_tags_path),
                "format": (
                    "headerless row-major little-endian uint32"
                ),
                "shape": [
                    vector_count,
                    int(attributes.shape[1]),
                ],
                "bytes": raw_tags_path.stat().st_size,
                "sha256": sha256(raw_tags_path),
            },
            "numpy_attributes": {
                "path": str(npy_tags_path),
                "shape": [
                    vector_count,
                    int(attributes.shape[1]),
                ],
                "dtype": "uint32",
                "sha256": sha256(npy_tags_path),
            },
            "numpy_key_tags": {
                "path": str(key_tags_path),
                "shape": [vector_count, 1],
                "dtype": "uint32",
                "sha256": sha256(key_tags_path),
            },
            "per_tag_bkt_tags": {
                "path": str(group_tags_path),
                "format": "one decimal attribute id per line",
                "bytes": group_tags_path.stat().st_size,
                "sha256": sha256(group_tags_path),
            },
            "counts": {
                "path": str(counts_path),
                "sha256": sha256(counts_path),
            },
        },
    }
    if numeric_values is not None:
        manifest["files"]["numpy_numeric"] = {
            "path": str(numeric_path),
            "shape": [vector_count, 1],
            "dtype": "uint32",
            "sha256": sha256(numeric_path),
        }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"output_dir          : {output_dir}")
    print(f"vector_count        : {vector_count}")
    print(f"attribute_values    : {args.attribute_cardinality}")
    print(f"total tag values    : {counts.size}")
    print(f"attribute columns   : {attributes.shape[1]}")
    print(f"zipf_exponent       : {args.zipf_exponent}")
    print(f"largest bucket      : {counts[0]} ({counts[0] / vector_count:.6%})")
    print(f"smallest bucket     : {counts[-1]} ({counts[-1] / vector_count:.6%})")
    if extreme_tag_count:
        print(
            f"extreme tag         : {args.attribute_cardinality} "
            f"{extreme_tag_count} "
            f"({extreme_tag_count / vector_count:.6%}, "
            f"requested={args.extreme_tag_ratio})"
        )
    if numeric_values is not None:
        print(
            f"numeric range       : "
            f"[{int(numeric_values.min())}, "
            f"{int(numeric_values.max())}]"
        )
    print(f"SPTAG TagFile       : {raw_tags_path}")
    print(f"PerVectorTagsFile   : {group_tags_path}")
    print(f"distribution        : {counts_path}")
    print(f"manifest            : {manifest_path}")


if __name__ == "__main__":
    main()
