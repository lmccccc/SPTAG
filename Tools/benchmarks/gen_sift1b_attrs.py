#!/usr/bin/env python3
"""Generate chunked one-tag plus one-numeric attributes for SIFT1B."""

import argparse
import hashlib
import json
import math
import os
import struct
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np

from generate_tenant_tag_scenario import zipf_counts


DEFAULT_ROOT = Path("/mnt/nvme/baotonglu/mocheng/datasets/sift1b")
DEFAULT_EXTREME_TAG_RATIO = Decimal("0.00001")
DEFAULT_ATTRIBUTE_CARDINALITY = 200
MAX_CATEGORICAL_VALUES = 256
NUMERIC_MULTIPLIER = 2654435761


def parse_ratio(value: str) -> Decimal:
    try:
        ratio = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError(f"invalid ratio: {value}") from error
    if not ratio.is_finite():
        raise argparse.ArgumentTypeError("ratio must be finite")
    return ratio


def count_from_ratio(vector_count: int, ratio: Decimal) -> int:
    if ratio < 0 or ratio >= 1:
        raise ValueError("extreme-tag-ratio must be in [0,1)")
    count = int(Decimal(vector_count) * ratio)
    if ratio and count == 0:
        raise ValueError("extreme-tag-ratio selects fewer than one vector")
    return count


def u8bin_shape(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(8)
    if len(header) != 8:
        raise ValueError(f"{path}: missing u8bin header")
    vector_count, dimension = struct.unpack("<ii", header)
    if vector_count <= 0 or dimension <= 0:
        raise ValueError(
            f"{path}: invalid u8bin shape ({vector_count}, {dimension})"
        )
    expected_bytes = 8 + vector_count * dimension
    if path.stat().st_size != expected_bytes:
        raise ValueError(
            f"{path}: expected {expected_bytes} bytes for "
            f"({vector_count}, {dimension}), got {path.stat().st_size}"
        )
    return vector_count, dimension


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def permutation_multiplier(vector_count: int, seed: int) -> int:
    candidate = (NUMERIC_MULTIPLIER ^ seed) % vector_count
    if candidate == 0:
        candidate = 1
    if candidate % 2 == 0:
        candidate += 1
    while math.gcd(candidate, vector_count) != 1:
        candidate += 2
        if candidate >= vector_count:
            candidate = 1
    return candidate


def prepare_output_paths(
    final_paths: list[Path],
    temporary_paths: list[Path],
    backup_paths: list[Path],
    overwrite: bool,
) -> None:
    backups = [path for path in backup_paths if path.exists()]
    if backups:
        joined = "\n  ".join(str(path) for path in backups)
        raise FileExistsError(
            "Refusing to discard recovery backups:\n  " + joined
        )
    existing = [path for path in final_paths if path.exists()]
    temporary = [path for path in temporary_paths if path.exists()]
    if (existing or temporary) and not overwrite:
        joined = "\n  ".join(
            str(path) for path in [*existing, *temporary]
        )
        raise FileExistsError(
            f"Refusing to overwrite generated inputs:\n  {joined}"
        )
    if overwrite:
        for path in temporary:
            path.unlink()


def publish_outputs(pairs: list[tuple[Path, Path]]) -> None:
    backups: list[tuple[Path, Path]] = []
    published: list[Path] = []
    try:
        for _, final_path in pairs:
            if not final_path.exists():
                continue
            backup_path = final_path.with_name(
                final_path.name + ".backup"
            )
            os.replace(final_path, backup_path)
            backups.append((backup_path, final_path))
        for temporary_path, final_path in pairs:
            os.replace(temporary_path, final_path)
            published.append(final_path)
    except BaseException:
        for final_path in published:
            if final_path.exists():
                final_path.unlink()
        for backup_path, final_path in reversed(backups):
            if backup_path.exists():
                os.replace(backup_path, final_path)
        raise
    for backup_path, _ in backups:
        try:
            backup_path.unlink()
        except OSError as error:
            raise RuntimeError(
                "Published new attributes but could not remove recovery "
                f"backup {backup_path}"
            ) from error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dataset_root",
        nargs="?",
        type=Path,
        default=Path(os.environ.get("SIFT1B_ROOT", DEFAULT_ROOT)),
    )
    parser.add_argument(
        "--base-file",
        type=Path,
        help="Default: <dataset_root>/sift1b_base.u8bin",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Default: <dataset_root>/sift1b_build",
    )
    parser.add_argument(
        "--vector-count",
        type=int,
        default=0,
        help="Generate a prefix subset for smoke tests; 0 uses the full base.",
    )
    parser.add_argument(
        "--attribute-cardinality",
        type=int,
        default=DEFAULT_ATTRIBUTE_CARDINALITY,
    )
    parser.add_argument("--zipf-exponent", type=float, default=1.0)
    parser.add_argument(
        "--extreme-tag-ratio",
        type=parse_ratio,
        default=DEFAULT_EXTREME_TAG_RATIO,
        help="Append a tag with floor(vector_count * ratio) members.",
    )
    parser.add_argument("--seed", type=int, default=20260817)
    parser.add_argument("--numeric-seed", type=int, default=20260821)
    parser.add_argument("--chunk-size", type=int, default=5_000_000)
    parser.add_argument(
        "--output-prefix",
        default="",
        help="Override the derived sift1b_zipf<N>_sparse<M>_numeric prefix.",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.dataset_root.resolve()
    base_file = (
        args.base_file.resolve()
        if args.base_file
        else root / "sift1b_base.u8bin"
    )
    if not base_file.is_file():
        raise FileNotFoundError(base_file)
    source_count, dimension = u8bin_shape(base_file)
    if dimension != 128:
        raise ValueError(f"expected SIFT dimension 128, got {dimension}")
    vector_count = args.vector_count or source_count
    if vector_count <= 0 or vector_count > source_count:
        raise ValueError(
            f"vector-count must be in [1, {source_count}], got {vector_count}"
        )
    if args.chunk_size <= 0:
        raise ValueError("chunk-size must be positive")
    if args.attribute_cardinality <= 0:
        raise ValueError("attribute-cardinality must be positive")
    if not np.isfinite(args.zipf_exponent) or args.zipf_exponent <= 0:
        raise ValueError("zipf-exponent must be finite and positive")

    extreme_tag_count = count_from_ratio(
        vector_count, args.extreme_tag_ratio
    )
    categorical_values = (
        args.attribute_cardinality + int(extreme_tag_count > 0)
    )
    if categorical_values > MAX_CATEGORICAL_VALUES:
        raise ValueError(
            f"at most {MAX_CATEGORICAL_VALUES} categorical values are "
            "supported by posting signatures"
        )
    regular_count = vector_count - extreme_tag_count
    counts = zipf_counts(
        args.attribute_cardinality,
        regular_count,
        args.zipf_exponent,
    )
    if extreme_tag_count:
        counts = np.concatenate(
            (counts, np.asarray([extreme_tag_count], dtype=np.int64))
        )
    if int(counts.sum()) != vector_count or np.any(counts <= 0):
        raise RuntimeError(
            "Zipf rounding did not produce positive counts summing "
            "to the requested vector count"
        )

    prefix = args.output_prefix
    if not prefix:
        prefix = f"sift1b_zipf{args.attribute_cardinality}"
        if extreme_tag_count:
            prefix += f"_sparse{extreme_tag_count}"
        prefix += "_numeric"
    output_dir = (
        args.output_dir.resolve()
        if args.output_dir
        else root / "sift1b_build"
    )
    raw_path = output_dir / f"{prefix}_attrs.u32"
    npy_path = output_dir / f"{prefix}_attrs.npy"
    counts_path = output_dir / f"{prefix}_counts.tsv"
    manifest_path = output_dir / f"{prefix}_manifest.json"
    temporary_paths = [
        path.with_name(path.name + ".tmp")
        for path in (raw_path, npy_path, counts_path, manifest_path)
    ]
    final_paths = [raw_path, npy_path, counts_path, manifest_path]
    backup_paths = [
        path.with_name(path.name + ".backup")
        for path in final_paths
    ]
    prepare_output_paths(
        final_paths,
        temporary_paths,
        backup_paths,
        args.overwrite,
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    multiplier = permutation_multiplier(vector_count, args.seed)
    offset = args.seed % vector_count
    cumulative = np.cumsum(counts, dtype=np.int64)
    observed = np.zeros(counts.size, dtype=np.int64)
    numeric_min = np.iinfo(np.uint32).max
    numeric_max = 0
    raw_tmp, npy_tmp, counts_tmp, manifest_tmp = temporary_paths
    started = time.perf_counter()

    try:
        raw = np.memmap(
            raw_tmp,
            mode="w+",
            dtype="<u4",
            shape=(vector_count, 2),
        )
        attributes = np.lib.format.open_memmap(
            npy_tmp,
            mode="w+",
            dtype="<u4",
            shape=(vector_count, 2),
        )
        for start in range(0, vector_count, args.chunk_size):
            end = min(start + args.chunk_size, vector_count)
            vector_ids = np.arange(start, end, dtype=np.uint64)
            ranks = (
                vector_ids * np.uint64(multiplier) + np.uint64(offset)
            ) % np.uint64(vector_count)
            tags = np.searchsorted(
                cumulative, ranks, side="right"
            ).astype(np.uint32)
            numeric = (
                vector_ids * np.uint64(NUMERIC_MULTIPLIER)
                + np.uint64(args.numeric_seed)
            ).astype(np.uint32)

            raw[start:end, 0] = tags
            raw[start:end, 1] = numeric
            attributes[start:end, 0] = tags
            attributes[start:end, 1] = numeric
            observed += np.bincount(
                tags, minlength=counts.size
            ).astype(np.int64)
            numeric_min = min(numeric_min, int(numeric.min()))
            numeric_max = max(numeric_max, int(numeric.max()))
            print(
                f"  attributes {end:,}/{vector_count:,} "
                f"({time.perf_counter() - started:.1f}s)",
                flush=True,
            )
        raw.flush()
        attributes.flush()
        del raw, attributes
        if not np.array_equal(observed, counts):
            raise RuntimeError(
                "generated categorical counts do not match the allocation"
            )

        with counts_tmp.open("w", encoding="utf-8") as stream:
            stream.write(
                "attribute_id\trank\tcount\tselectivity\tclass\n"
            )
            for attribute_id, count in enumerate(counts):
                value_class = (
                    "extreme"
                    if extreme_tag_count
                    and attribute_id == args.attribute_cardinality
                    else "zipf"
                )
                stream.write(
                    f"{attribute_id}\t{attribute_id + 1}\t{int(count)}\t"
                    f"{count / vector_count:.12f}\t{value_class}\n"
                )

        manifest = {
            "schema_version": 3,
            "generated_at_utc": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "dataset": "SIFT1B",
            "source_base_file": str(base_file),
            "source_vector_count": source_count,
            "vector_count": vector_count,
            "dimension": dimension,
            "attribute_columns": 2,
            "categorical_columns": 1,
            "numeric_columns": 1,
            "limited_tag_column": 0,
            "numeric_column": 1,
            "attribute_cardinality": args.attribute_cardinality,
            "total_categorical_cardinality": int(counts.size),
            "distribution": "Zipf",
            "zipf_exponent": args.zipf_exponent,
            "rounding": "largest-remainder",
            "assignment": "affine-permutation-of-exact-counts",
            "assignment_permutation": {
                "formula": "(vid * multiplier + offset) mod vector_count",
                "multiplier": multiplier,
                "offset": offset,
                "gcd_multiplier_vector_count": math.gcd(
                    multiplier, vector_count
                ),
                "seed": args.seed,
            },
            "extreme_tag_id": (
                args.attribute_cardinality
                if extreme_tag_count
                else None
            ),
            "extreme_tag_ratio_requested": str(
                args.extreme_tag_ratio
            ),
            "extreme_tag_count": extreme_tag_count,
            "extreme_tag_selectivity": (
                extreme_tag_count / vector_count
                if extreme_tag_count
                else 0.0
            ),
            "extreme_tag_rounding": "floor(vector_count * ratio)",
            "numeric_generation": {
                "formula": "(vid * 2654435761 + seed) mod 2^32",
                "seed": args.numeric_seed,
                "unique_for_vector_count": vector_count <= 2**32,
                "min": numeric_min,
                "max": numeric_max,
            },
            "files": {
                "sptag_attributes": {
                    "path": str(raw_path),
                    "format": (
                        "headerless row-major little-endian uint32"
                    ),
                    "shape": [vector_count, 2],
                    "bytes": raw_tmp.stat().st_size,
                    "sha256": sha256(raw_tmp),
                },
                "numpy_attributes": {
                    "path": str(npy_path),
                    "shape": [vector_count, 2],
                    "dtype": "uint32",
                    "bytes": npy_tmp.stat().st_size,
                    "sha256": sha256(npy_tmp),
                },
                "counts": {
                    "path": str(counts_path),
                    "sha256": sha256(counts_tmp),
                },
            },
        }
        manifest_tmp.write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        publish_outputs(
            [
                (raw_tmp, raw_path),
                (npy_tmp, npy_path),
                (counts_tmp, counts_path),
                (manifest_tmp, manifest_path),
            ]
        )
    except BaseException:
        for path in temporary_paths:
            if path.exists():
                path.unlink()
        raise

    print(f"vector_count        : {vector_count}")
    print("attribute schema    : [categorical tag, numeric]")
    print(
        f"extreme tag         : id={args.attribute_cardinality} "
        f"count={extreme_tag_count} "
        f"selectivity={extreme_tag_count / vector_count:.12f}"
    )
    print(f"SPTAG TagFile       : {raw_path}")
    print(f"NumTagsPerVec       : 2")
    print(f"manifest            : {manifest_path}")


if __name__ == "__main__":
    main()
