#!/usr/bin/env python3
"""Generate native synthetic attributes using only a copied build INI.

Required inputs are Base.VectorPath/ValueType/VectorType=DEFAULT,
Tags.TagFile/ColumnTypes=categorical,numeric, SelectHead.Ratio,
BuildSSDIndex.EnableLimitedTagPosting and SearchSSDIndex.InternalResultNum.
Base.Dim and VectorSize are optional. The native support defaults are
LimitedTagSlotsPerHead=2 and LimitedTagColumn=0.

[AttributeGeneration] must explicitly provide Cardinality, ZipfExponent, Seed,
NumericSeed and ChunkRows. There are no data/model CLI or environment overrides.
Paths retain native working-directory semantics, without variable expansion.
Raw TagFile and its .npy, .counts.tsv and .manifest.json siblings must be new.
"""

import argparse
import configparser
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys

import numpy as np

from extreme_sparse_policy import ExtremeSparsePolicy, coverage_boundary_count, parse_ratio
from gen_sift1b_attrs import (
    attribute_chunks,
    categorical_counts,
    permutation_multiplier,
    sha256,
)
from native_input_io import column_types, open_vectors, read_config


def _required(config, section, key):
    value = config.get(section, {}).get(key, "")
    if not value:
        raise ValueError(f"Missing explicit [{section}] {key}")
    return value


@dataclass(frozen=True)
class GenerationSettings:
    cardinality: int
    zipf_exponent: float
    seed: int
    numeric_seed: int
    chunk_rows: int

    @classmethod
    def from_config(cls, config):
        fields = {"cardinality", "zipfexponent", "seed", "numericseed", "chunkrows"}
        unknown = set(config.get("attributegeneration", {})) - fields
        if unknown:
            raise ValueError(f"Unknown [AttributeGeneration] fields: {', '.join(sorted(unknown))}")
        values = {key: _required(config, "attributegeneration", key) for key in fields}
        result = cls(
            int(values["cardinality"]), float(values["zipfexponent"]),
            int(values["seed"]), int(values["numericseed"]), int(values["chunkrows"]),
        )
        if result.cardinality <= 0:
            raise ValueError("[AttributeGeneration] Cardinality must be positive")
        if not math.isfinite(result.zipf_exponent) or result.zipf_exponent <= 0:
            raise ValueError("[AttributeGeneration] ZipfExponent must be finite and positive")
        if result.chunk_rows <= 0:
            raise ValueError("[AttributeGeneration] ChunkRows must be positive")
        if not all(0 <= seed < 2**64 for seed in (result.seed, result.numeric_seed)):
            raise ValueError("[AttributeGeneration] Seed and NumericSeed must be uint64 integers")
        return result


def _coverage_policy(config, count, tag_file):
    build = config.get("buildssdindex", {})
    if build.get("enablelimitedtagposting", "").lower() not in {"true", "1"}:
        raise ValueError("Synthetic attributes require BuildSSDIndex.EnableLimitedTagPosting=true")
    if int(build.get("limitedtagcolumn", "0")) != 0:
        raise ValueError("Synthetic attributes require LimitedTagColumn=0")
    if int(config.get("selecthead", {}).get("count", "0")) != 0:
        raise ValueError("Synthetic coverage requires SelectHead.Ratio, not an explicit Count")
    return ExtremeSparsePolicy(
        vector_count=count,
        head_ratio=parse_ratio(_required(config, "selecthead", "ratio")),
        slots_per_head=int(build.get("limitedtagslotsperhead", "2")),
        coverage_target=int(_required(config, "searchssdindex", "internalresultnum")),
        tag_file=str(tag_file),
    )


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _identity(path):
    info = path.lstat()
    return info.st_dev, info.st_ino


def _remove_owned(path, identity):
    try:
        current = _identity(path)
    except FileNotFoundError:
        return
    if current == identity:
        path.unlink()


@contextmanager
def _new_outputs(paths):
    """Publish complete files without replacing even a concurrently made target."""
    scratch = [path.with_name(path.name + ".tmp") for path in paths]
    if len(set(paths + scratch)) != len(paths + scratch):
        raise ValueError("TagFile and its generated sibling/scratch paths must be distinct")
    protected = paths + scratch + [
        path.with_name(path.name + suffix)
        for path in paths for suffix in (".partial", ".backup")
    ]
    existing = [str(path) for path in protected if os.path.lexists(path)]
    if existing:
        raise FileExistsError("Refusing existing attribute outputs or recovery files: " + ", ".join(existing))
    paths[0].parent.mkdir(parents=True, exist_ok=True)
    owned = {}
    try:
        with ExitStack() as stack:
            streams = []
            for path in scratch:
                stream = stack.enter_context(path.open("xb"))
                info = os.fstat(stream.fileno())
                owned[path] = info.st_dev, info.st_ino
                streams.append(stream)
            yield streams
            for stream in streams:
                stream.flush()
                os.fsync(stream.fileno())
        for temporary, final in zip(scratch, paths):
            if _identity(temporary) != owned[temporary]:
                raise RuntimeError(f"Attribute scratch file was replaced: {temporary}")
            os.link(temporary, final)
            owned[final] = owned[temporary]
        _sync_directory(paths[0].parent)
    except BaseException:
        for path, identity in reversed(list(owned.items())):
            _remove_owned(path, identity)
        raise
    for path in scratch:
        _remove_owned(path, owned[path])
    _sync_directory(paths[0].parent)


def _counts_payload(counts, count, cardinality):
    rows = ["attribute_id\trank\tcount\tselectivity\tclass\n"]
    for attribute_id, frequency in enumerate(counts):
        label = "extreme" if attribute_id == cardinality else "zipf"
        rows.append(
            f"{attribute_id}\t{attribute_id + 1}\t{int(frequency)}\t"
            f"{frequency / count:.12f}\t{label}\n"
        )
    return "".join(rows).encode("utf-8")


def generate_attributes(config_path):
    """Generate a new attribute family and return its persisted manifest."""
    config_path = Path(config_path).resolve()
    config = read_config(config_path)
    config_sha256 = sha256(config_path)
    removed = {"vectoroffset", "vectorcount", "tagoffset", "staticacltagcols"}
    for section, values in config.items():
        for key in removed.intersection(values):
            raise ValueError(f"[{section}] {key} was removed; use native input fields")
    schema = column_types(config)
    if schema != ("categorical", "numeric"):
        raise ValueError(
            "Synthetic generation supports only ColumnTypes=categorical,numeric in that order; "
            "use existing real attributes directly for other schemas"
        )
    settings = GenerationSettings.from_config(config)
    base = config.get("base", {})
    if _required(config, "base", "vectortype").upper() != "DEFAULT":
        raise ValueError("Attribute generation requires native Base.VectorType=DEFAULT")
    vectors = open_vectors(
        _required(config, "base", "vectorpath"),
        _required(config, "base", "valuetype"),
        dimension=int(base["dim"]) if "dim" in base else None,
        limit=int(base.get("vectorsize", "-1")),
    )
    count, source_rows = len(vectors.data), vectors.source_rows
    base_path, dimension, value_type = vectors.path, vectors.dimension, vectors.value_type
    del vectors
    configured_path = Path(_required(config, "tags", "tagfile"))
    # Resolve parents only: resolving a final symlink would hide an existing target.
    raw_path = configured_path.parent.resolve() / configured_path.name
    npy_path = raw_path.with_suffix(".npy")
    counts_path = raw_path.with_suffix(".counts.tsv")
    manifest_path = raw_path.with_suffix(".manifest.json")
    paths = [raw_path, npy_path, counts_path, manifest_path]
    policy = _coverage_policy(config, count, raw_path)
    rare_count = coverage_boundary_count(count, policy)
    counts = categorical_counts(count, settings.cardinality, settings.zipf_exponent, rare_count)
    multiplier = permutation_multiplier(count, settings.seed)
    offset = settings.seed % count

    header = io.BytesIO()
    np.lib.format.write_array_header_1_0(
        header, {"descr": "<u4", "fortran_order": False, "shape": (count, len(schema))}
    )
    npy_header = header.getvalue()
    raw_hash, npy_hash = hashlib.sha256(), hashlib.sha256(npy_header)
    observed = np.zeros(len(counts), dtype=np.int64)
    numeric_min, numeric_max = 2**32 - 1, 0
    counts_payload = _counts_payload(counts, count, settings.cardinality)
    with _new_outputs(paths) as (raw, npy, counts_stream, manifest_stream):
        npy.write(npy_header)
        for start, block in attribute_chunks(
            count, counts, settings.seed, settings.numeric_seed, settings.chunk_rows
        ):
            payload = memoryview(block)
            raw.write(payload)
            npy.write(payload)
            raw_hash.update(payload)
            npy_hash.update(payload)
            observed += np.bincount(block[:, 0], minlength=len(counts))
            numeric_min = min(numeric_min, int(block[:, 1].min()))
            numeric_max = max(numeric_max, int(block[:, 1].max()))
            print(f"attributes {start + len(block):,}/{count:,}", flush=True)
        if not np.array_equal(observed, counts):
            raise RuntimeError("Generated categorical counts do not match the exact allocation")
        counts_stream.write(counts_payload)
        manifest = {
            "schema_version": 4,
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "dataset": "native",
            "source_base_file": str(base_path),
            "source_vector_count": source_rows,
            "vector_count": count,
            "dimension": dimension,
            "value_type": value_type,
            "vector_type": "DEFAULT",
            "native_config": {
                "path": str(config_path),
                "sha256": config_sha256,
                "configured_vector_count": count,
                "values": config,
            },
            "column_types": list(schema),
            "attribute_columns": len(schema),
            "categorical_columns": 1,
            "numeric_columns": 1,
            "limited_tag_column": 0,
            "numeric_column": 1,
            "attribute_cardinality": settings.cardinality,
            "total_categorical_cardinality": len(counts),
            "distribution": "Zipf",
            "zipf_exponent": settings.zipf_exponent,
            "rounding": "largest-remainder",
            "assignment": "affine-permutation-of-exact-counts",
            "attribute_generation": {
                "cardinality": settings.cardinality,
                "zipf_exponent": settings.zipf_exponent,
                "seed": settings.seed,
                "numeric_seed": settings.numeric_seed,
                "chunk_rows": settings.chunk_rows,
            },
            "assignment_permutation": {
                "formula": "(vid * multiplier + offset) mod vector_count",
                "multiplier": multiplier,
                "offset": offset,
                "gcd_multiplier_vector_count": math.gcd(multiplier, count),
                "seed": settings.seed,
            },
            "extreme_tag_id": settings.cardinality,
            "extreme_tag_count": rare_count,
            "extreme_tag_selectivity": rare_count / count,
            "extreme_tag_policy": {
                "formula": "ceil(coverage_target / (expected_head_ratio * slots_per_head)) - 1",
                "expected_head_ratio": str(policy.head_ratio),
                "expected_head_count": str(count * policy.head_ratio),
                "slots_per_head": policy.slots_per_head,
                "coverage_target": policy.coverage_target,
                "derived_max_tag_count": rare_count,
            },
            "numeric_generation": {
                "formula": "(vid * 2654435761 + seed) mod 2^32",
                "seed": settings.numeric_seed,
                "unique_for_vector_count": count <= 2**32,
                "min": numeric_min,
                "max": numeric_max,
            },
            "files": {
                "sptag_attributes": {
                    "path": str(raw_path),
                    "format": "headerless row-major little-endian uint32",
                    "shape": [count, len(schema)],
                    "bytes": count * len(schema) * 4,
                    "sha256": raw_hash.hexdigest(),
                },
                "numpy_attributes": {
                    "path": str(npy_path),
                    "shape": [count, len(schema)],
                    "dtype": "uint32",
                    "bytes": len(npy_header) + count * len(schema) * 4,
                    "sha256": npy_hash.hexdigest(),
                },
                "counts": {
                    "path": str(counts_path),
                    "bytes": len(counts_payload),
                    "sha256": hashlib.sha256(counts_payload).hexdigest(),
                },
            },
        }
        manifest_stream.write((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
    print(f"SPTAG TagFile: {raw_path}\nmanifest: {manifest_path}", flush=True)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--config", type=Path, required=True, help="Native build INI (the only input)")
    args = parser.parse_args(argv)
    generate_attributes(args.config)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, configparser.Error) as error:
        sys.exit(f"Attribute preparation failed: {error}")
