#!/usr/bin/env python3
"""Prepare and run staged limited-tag support-expansion experiments."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import struct
import subprocess
import sys
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from generate_sift1m_sparse_numeric_workloads import exact_topk, read_xvecs, sha256


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
DATASETS_ROOT = WORKSPACE_ROOT / "datasets"
CANONICAL_TEMPLATE = SCRIPT_DIR / "build_spann_attr_sift1m_zipf200_limited_tag.ini"
DEFAULT_SOURCE_FIXTURE = DATASETS_ROOT / "sift1m_zipf200_sparse193_numeric"
DEFAULT_MAIN_FIXTURE = DATASETS_ROOT / "sift1m_zipf8192_numeric_support_expansion"

DEFAULT_QUERY_COUNT = 1000
DEFAULT_TOPK = 10
DEFAULT_WARMUP = 100
DEFAULT_MEASURE_OFFSET = 100
DEFAULT_MEASURED_QUERIES = 900
DEFAULT_QUERY_BATCH = 64
DEFAULT_SEARCH_GRID = (16, 32, 64, 128)
DEFAULT_TRIALS = 3
DEFAULT_EXTRA_SUPPORT_BUDGET = 262_144
DEFAULT_EXPANDED_PAGE_BUDGET = 32
MAIN_PROFILE = "zipf8192"
CONTROL_PROFILE = "current201"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def json_dump(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def parse_int_list(text: str) -> list[int]:
    values: list[int] = []
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        values.append(int(token))
    ensure(bool(values) and all(value > 0 for value in values),
           f"expected positive integers in {text!r}")
    ensure(len(values) == len(set(values)), "duplicate probe values are not allowed")
    return values


def stable_seed(*parts: Any) -> int:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\0")
    return int.from_bytes(digest.digest()[:8], "little") & 0xFFFFFFFF


def parse_native_bool(value: str | int | bool) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {
        "1", "true", "yes", "on",
    }


def coerce_number(value: Any) -> Any:
    if isinstance(value, (int, float)) or value is None:
        return value
    text = str(value).strip()
    if text == "":
        return text
    try:
        if any(marker in text.lower() for marker in (".", "e")):
            return float(text)
        return int(text)
    except ValueError:
        return value


def link_into_fixture(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        target = destination.resolve(strict=True)
        ensure(
            target == source.resolve(),
            f"{destination} already points to {target}, expected {source}",
        )
        return
    destination.symlink_to(source.resolve())


def canonicalize_path(path: Path) -> Path:
    return path.resolve(strict=False)


def guard_output_path(path: Path, run_dir: Path) -> None:
    resolved = canonicalize_path(path)
    owner = canonicalize_path(run_dir)
    ensure(
        resolved != owner and resolved.is_relative_to(owner),
        f"output must be a child of this experiment run: {path}",
    )
    ensure(not path.exists() and not path.is_symlink(),
           f"refusing to replace an existing build output: {path}")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_ini(path: Path) -> OrderedDict[str, OrderedDict[str, str]]:
    sections: OrderedDict[str, OrderedDict[str, str]] = OrderedDict()
    current: OrderedDict[str, str] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            current = sections.setdefault(section, OrderedDict())
            continue
        ensure(current is not None, f"{path}: key before section: {raw}")
        ensure("=" in raw, f"{path}: invalid INI line: {raw}")
        key, value = raw.split("=", 1)
        current[key.strip()] = value.strip()
    return sections


def write_ini(path: Path, sections: OrderedDict[str, OrderedDict[str, str]]) -> None:
    lines: list[str] = []
    for section, values in sections.items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"{key}={value}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def set_ini_value(
    sections: OrderedDict[str, OrderedDict[str, str]],
    section: str,
    key: str,
    value: str | int | float | bool | Path,
) -> None:
    bucket = sections.setdefault(section, OrderedDict())
    if isinstance(value, bool):
        bucket[key] = "true" if value else "false"
    else:
        bucket[key] = str(value)


def render_ini_variant(
    template: OrderedDict[str, OrderedDict[str, str]],
    fixture_root: Path,
    attribute_cardinality: int,
    attrs_path: Path,
    group_tags_path: Path,
    index_dir: Path,
    tmp_dir: Path,
    variant: "VariantSpec",
    profile: str,
) -> OrderedDict[str, OrderedDict[str, str]]:
    rendered = OrderedDict(
        (section, OrderedDict(values))
        for section, values in template.items()
    )
    source_root = fixture_root / "source"
    set_ini_value(rendered, "Base", "VectorPath", source_root / "sift_base.bin")
    set_ini_value(rendered, "Base", "QueryPath", source_root / "sift_query.fvecs")
    set_ini_value(rendered, "Base", "WarmupPath", source_root / "sift_query.fvecs")
    set_ini_value(rendered, "Base", "TruthPath", source_root / "sift_groundtruth.ivecs")
    set_ini_value(rendered, "Base", "IndexDirectory", index_dir)
    set_ini_value(rendered, "Tags", "TagFile", attrs_path)
    set_ini_value(rendered, "Tags", "NumTagsPerVec", 2)
    set_ini_value(rendered, "Build", "BuildSignatures", True)
    set_ini_value(rendered, "SelectHead", "MinHeadsPerTag", variant.min_heads_per_tag)
    if profile == MAIN_PROFILE:
        set_ini_value(rendered, "BuildHead", "NumberOfThreads", 1)
        set_ini_value(rendered, "BuildHead", "BKTSeed", 0)
        set_ini_value(rendered, "BuildHead", "TPTSeed", 0)
    set_ini_value(rendered, "BuildSSDIndex", "TmpDir", tmp_dir)
    set_ini_value(rendered, "BuildSSDIndex", "EnableLimitedTagPosting", True)
    set_ini_value(rendered, "BuildSSDIndex", "LimitedTagSlotsPerHead", 2)
    set_ini_value(rendered, "BuildSSDIndex", "LimitedTagVoteHeadCount", 2)
    set_ini_value(rendered, "BuildSSDIndex", "LimitedTagMinHeadCount", variant.support_floor)
    set_ini_value(rendered, "BuildSSDIndex", "LimitedTagColumn", 0)
    set_ini_value(
        rendered,
        "BuildSSDIndex",
        "EnableLimitedTagSupportExpansion",
        variant.enable_support_expansion,
    )
    set_ini_value(
        rendered,
        "BuildSSDIndex",
        "LimitedTagMaxExtraSupports",
        variant.max_extra_supports,
    )
    set_ini_value(
        rendered,
        "BuildSSDIndex",
        "LimitedTagMaxExpandedPostingPages",
        variant.max_expanded_posting_pages,
    )
    set_ini_value(rendered, "BuildSSDIndex", "SparseFallbackMaxHeads", 64)
    set_ini_value(rendered, "BuildSSDIndex", "SparseFallbackMaxPostingPages", 256)
    set_ini_value(rendered, "BuildSSDIndex", "EnableExtremeSparseTag", False)
    set_ini_value(rendered, "BuildSSDIndex", "LogExtremeSparseTagRoute", False)
    set_ini_value(rendered, "SearchSSDIndex", "InternalResultNum", 64)
    set_ini_value(rendered, "SearchSSDIndex", "MaxCheck", 2048)
    set_ini_value(rendered, "SearchSSDIndex", "SecondLevelMaxCheck", 128)
    set_ini_value(rendered, "SearchSSDIndex", "SecondLevelGraphSignaturePruning", False)
    set_ini_value(rendered, "SearchSSDIndex", "NumberOfThreads", 1)
    set_ini_value(rendered, "MultiTenant", "PerVectorTagsFile", group_tags_path)
    set_ini_value(rendered, "MultiTenant", "NumericCols", 1)
    set_ini_value(rendered, "MultiTenant", "ACLCols", 0)
    set_ini_value(rendered, "MultiTenant", "HierLevelWidths", attribute_cardinality)
    set_ini_value(rendered, "MultiTenant", "InPlaceBuild", True)
    set_ini_value(rendered, "MultiTenant", "PersistSelectHead", 0)
    set_ini_value(rendered, "MultiTenant", "ResumeBuild", 0)
    return rendered


def render_primary_build_ini(
    final_ini: OrderedDict[str, OrderedDict[str, str]],
) -> OrderedDict[str, OrderedDict[str, str]]:
    primary = OrderedDict(
        (section, OrderedDict(values))
        for section, values in final_ini.items()
    )
    set_ini_value(primary, "Build", "BuildSignatures", False)
    return primary


def render_search_ini(
    template: OrderedDict[str, OrderedDict[str, str]],
    nprobe: int,
) -> OrderedDict[str, OrderedDict[str, str]]:
    rendered = OrderedDict()
    search_section = OrderedDict(template.get("SearchSSDIndex", OrderedDict()))
    rendered["SearchSSDIndex"] = search_section
    set_ini_value(rendered, "SearchSSDIndex", "InternalResultNum", nprobe)
    set_ini_value(rendered, "SearchSSDIndex", "MaxCheck", 2048)
    set_ini_value(
        rendered,
        "SearchSSDIndex",
        "SecondLevelMaxCheck",
        max(128, 2 * nprobe),
    )
    set_ini_value(rendered, "SearchSSDIndex", "NumberOfThreads", 1)
    set_ini_value(
        rendered,
        "SearchSSDIndex",
        "SecondLevelGraphSignaturePruning",
        False,
    )
    return rendered


def parse_time_output(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not path.is_file():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        if ": " not in line:
            continue
        key, value = line.rsplit(": ", 1)
        key = key.strip()
        value = value.strip()
        if key == "Elapsed (wall clock) time (h:mm:ss or m:ss)":
            result["elapsed_wall"] = value
            result["elapsed_seconds"] = sum(
                float(part) * 60 ** place
                for place, part in enumerate(reversed(value.split(":")))
            )
        elif key == "Maximum resident set size (kbytes)":
            result["max_rss_kb"] = int(value)
        elif key == "Exit status":
            result["exit_status"] = int(value)
    return result


def tool_hashes(paths: Iterable[Path]) -> dict[str, str]:
    output: dict[str, str] = {}
    for path in paths:
        if path.is_file():
            output[str(path)] = sha256(path)
    return output


def binary_hash(path: Path) -> dict[str, Any]:
    ensure(path.is_file(), f"missing binary: {path}")
    return {
        "path": str(path),
        "sha256": sha256(path),
    }


def run_command(
    command: list[str],
    stdout_path: Path,
    stderr_path: Path,
    time_path: Path,
    cwd: Path,
) -> dict[str, Any]:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    time_path.parent.mkdir(parents=True, exist_ok=True)
    time_binary = Path("/usr/bin/time")
    ensure(time_binary.is_file(), "GNU /usr/bin/time is required for experiment provenance")
    full_command = [str(time_binary), "-v", "-o", str(time_path)] + command
    with stdout_path.open("wb") as stdout_stream, stderr_path.open("wb") as stderr_stream:
        completed = subprocess.run(
            full_command,
            cwd=str(cwd),
            stdout=stdout_stream,
            stderr=stderr_stream,
            check=False,
        )
    metadata = {
        "command": full_command,
        "cwd": str(cwd),
        "returncode": completed.returncode,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "time": str(time_path),
        "timing": parse_time_output(time_path),
    }
    return metadata


def load_fixture_manifest(fixture_root: Path) -> dict[str, Any]:
    manifest_path = fixture_root / "manifest.json"
    query_manifest_path = fixture_root / "query" / "workloads.json"
    ensure(manifest_path.is_file(), f"missing fixture manifest: {manifest_path}")
    ensure(query_manifest_path.is_file(), f"missing query manifest: {query_manifest_path}")
    manifest = read_json(manifest_path)
    manifest["_path"] = str(manifest_path)
    manifest["_query_manifest_path"] = str(query_manifest_path)
    manifest["_query_manifest"] = read_json(query_manifest_path)
    return manifest


def workload_entries(query_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    truth = query_manifest["truth"]
    if "workloads" in query_manifest:
        for name, payload in query_manifest["workloads"].items():
            entry = dict(payload)
            entry["truth"] = truth[name]
            entries[name] = entry
        return entries

    flat_query_tags = query_manifest.get("flat_query_tags", {})
    query_dnf = query_manifest.get("query_dnf", {})
    for name, truth_entry in truth.items():
        if name == "unfilter":
            entries[name] = {
                "mode": "unfiltered",
                "truth": truth_entry,
            }
        elif name in flat_query_tags:
            entries[name] = {
                "mode": "categorical",
                "tag_column": 0,
                "query_tags": flat_query_tags[name],
                "truth": truth_entry,
            }
        elif name in query_dnf:
            entries[name] = {
                "mode": "dnf",
                "query_dnf": query_dnf[name],
                "truth": truth_entry,
            }
    return entries


def truth_ids_path(truth_entry: dict[str, Any]) -> Path:
    path = truth_entry.get("path") or truth_entry.get("ids")
    ensure(path, f"missing groundtruth ids path in {truth_entry}")
    return Path(path)


def read_workload_query_vectors(query_manifest: dict[str, Any]) -> np.ndarray:
    path = Path(query_manifest["query_vectors"])
    return np.load(path, allow_pickle=False)


def validate_truth_rows(
    ids: np.ndarray,
    topk: int,
    candidate_counts: np.ndarray,
) -> None:
    ensure(ids.dtype == np.int64, "groundtruth ids must be int64")
    ensure(ids.ndim == 2 and ids.shape[1] == topk, "groundtruth ids shape mismatch")
    ensure(candidate_counts.shape == (ids.shape[0], 1), "eligible-count matrix shape mismatch")
    for row_index in range(ids.shape[0]):
        row = ids[row_index]
        ensure(np.all(row >= 0), f"query {row_index}: missing groundtruth ids")
        unique = np.unique(row)
        ensure(
            unique.size >= topk,
            f"query {row_index}: fewer than {topk} distinct ids",
        )
        ensure(
            int(candidate_counts[row_index, 0]) >= topk,
            f"query {row_index}: candidate count below topk",
        )


def validate_tagged_truth_membership(
    ids: np.ndarray,
    key_tags: np.ndarray,
    per_query_tags: np.ndarray,
) -> None:
    row_tags = np.asarray(per_query_tags[:, 0], dtype=np.uint32)
    for row_index in range(ids.shape[0]):
        row_ids = ids[row_index]
        observed = key_tags[row_ids]
        ensure(
            np.all(observed == row_tags[row_index]),
            f"query {row_index}: groundtruth ids do not all satisfy tag {row_tags[row_index]}",
        )


def write_array(path: Path, array: np.ndarray) -> dict[str, Any]:
    np.save(path, array)
    return {
        "path": str(path),
        "shape": list(array.shape),
        "dtype": str(array.dtype),
        "sha256": sha256(path),
    }


def grouped_exact_topk(
    base_records: np.memmap,
    queries: np.ndarray,
    key_tags: np.ndarray,
    per_query_tags: np.ndarray,
    topk: int,
    query_batch: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    tag_vector = np.asarray(per_query_tags[:, 0], dtype=np.uint32)
    output_ids = np.full((queries.shape[0], topk), -1, dtype=np.int64)
    output_distances = np.full((queries.shape[0], topk), np.inf, dtype=np.float32)
    eligible_counts = np.zeros((queries.shape[0], 1), dtype=np.int64)
    for tag in np.unique(tag_vector):
        query_rows = np.flatnonzero(tag_vector == tag)
        candidate_ids = np.flatnonzero(key_tags == tag).astype(np.int64)
        ensure(candidate_ids.size >= topk, f"tag {tag} has only {candidate_ids.size} vectors")
        ids, distances = exact_topk(
            base_records,
            queries[query_rows],
            candidate_ids,
            topk,
            query_batch,
        )
        output_ids[query_rows] = ids
        output_distances[query_rows] = distances
        eligible_counts[query_rows, 0] = candidate_ids.size
    return output_ids, output_distances, eligible_counts


def benchmark_profile_for_fixture(fixture_root: Path) -> str:
    name = fixture_root.name
    if name == DEFAULT_MAIN_FIXTURE.name:
        return MAIN_PROFILE
    if name == DEFAULT_SOURCE_FIXTURE.name:
        return CONTROL_PROFILE
    raise RuntimeError(f"unknown fixture profile for {fixture_root}")


@dataclass(frozen=True)
class VariantSpec:
    name: str
    label: str
    enable_support_expansion: bool
    support_floor: int
    min_heads_per_tag: int = 0
    max_extra_supports: int = 0
    max_expanded_posting_pages: int = 0
    reference_index: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "enable_support_expansion": self.enable_support_expansion,
            "support_floor": self.support_floor,
            "min_heads_per_tag": self.min_heads_per_tag,
            "max_extra_supports": self.max_extra_supports,
            "max_expanded_posting_pages": self.max_expanded_posting_pages,
            "reference_index": self.reference_index,
        }


def profile_variants(profile: str) -> list[VariantSpec]:
    if profile == MAIN_PROFILE:
        return [
            VariantSpec(
                name="legacy_fixed_f1",
                label="legacy_fixed_f1",
                enable_support_expansion=False,
                support_floor=1,
            ),
            VariantSpec(
                name="o_source_f1",
                label="o_source_f1",
                enable_support_expansion=True,
                support_floor=1,
                max_extra_supports=DEFAULT_EXTRA_SUPPORT_BUDGET,
                max_expanded_posting_pages=DEFAULT_EXPANDED_PAGE_BUDGET,
            ),
            VariantSpec(
                name="o_source_f16",
                label="o_source_f16",
                enable_support_expansion=True,
                support_floor=16,
                max_extra_supports=DEFAULT_EXTRA_SUPPORT_BUDGET,
                max_expanded_posting_pages=DEFAULT_EXPANDED_PAGE_BUDGET,
            ),
        ]
    if profile == CONTROL_PROFILE:
        return [
            VariantSpec(
                name="legacy_active_baseline",
                label="legacy_active_baseline",
                enable_support_expansion=False,
                support_floor=1,
                reference_index=True,
            ),
            VariantSpec(
                name="o_source_f16",
                label="o_source_f16",
                enable_support_expansion=True,
                support_floor=16,
                max_extra_supports=DEFAULT_EXTRA_SUPPORT_BUDGET,
                max_expanded_posting_pages=DEFAULT_EXPANDED_PAGE_BUDGET,
            ),
        ]
    raise RuntimeError(f"unsupported profile {profile}")


def resolve_variants(profile: str, names: list[str] | None) -> list[VariantSpec]:
    variants = {variant.name: variant for variant in profile_variants(profile)}
    if not names:
        return list(variants.values())
    resolved: list[VariantSpec] = []
    for name in names:
        ensure(name in variants, f"unknown variant {name!r} for profile {profile}")
        resolved.append(variants[name])
    return resolved


def build_default_workloads(profile: str) -> list[str]:
    if profile == MAIN_PROFILE:
        return ["unfilter", "broad_tag", "bin13_20", "bin21_50", "bin51_200"]
    return ["unfilter", "broad_tag", "extreme_tag"]


def profile_notes(profile: str) -> list[str]:
    if profile == MAIN_PROFILE:
        return [
            "Dedicated extreme-sparse route is compile-time disabled in current wrappers; copied INI EST flags do not imply an active separate EST route.",
            "SparseFallbackMaxHeads and SparseFallbackMaxPostingPages remain the matched ordinary sparse fallback controls.",
            "CPU head construction uses native BKTSeed=0 and TPTSeed=0 with one BuildHead thread, isolating tree initialization from legacy process-global clock reseeding; SelectHead and SSD assignment retain 24 threads.",
            "Within the main 8192-tag fixture, O-source floor=1 vs floor=16 keeps the same unified fallback path and isolates the support-floor effect.",
        ]
    return [
        "Dedicated extreme-sparse route is compile-time disabled in current wrappers; copied INI EST flags do not imply an active separate EST route.",
        "SparseFallbackMaxHeads and SparseFallbackMaxPostingPages remain the matched ordinary sparse fallback controls.",
        "Current201 is an independent-rebuild no-overflow control; do not claim exact O identity unless H-source hashes and original-membership fingerprints both match.",
        "Current201 with E=0 is still not an algorithmic no-op: the unified fallback / RNG replica path can change scanned occurrences, dedup, page reads, recall, and QPS even without realized overflow expansion.",
    ]


def prepare_fixture(args: argparse.Namespace) -> None:
    fixture_root = args.fixture_root.resolve()
    reference_fixture = args.reference_fixture.resolve()
    source_root = reference_fixture / "source"
    query_source_root = reference_fixture / "query"
    ensure(source_root.is_dir(), f"missing source dir: {source_root}")
    ensure(query_source_root.is_dir(), f"missing query dir: {query_source_root}")
    ensure(args.query_count > 0, "query-count must be positive")
    ensure(args.topk > 0, "topk must be positive")
    ensure(args.query_batch > 0, "query-batch must be positive")
    ensure(args.warmup_count >= 0, "warmup-count must be non-negative")
    ensure(args.measure_offset >= 0, "measure-offset must be non-negative")
    ensure(args.measured_query_count > 0, "measured-query-count must be positive")
    ensure(
        args.measure_offset + args.measured_query_count <= args.query_count,
        "measure-offset + measured-query-count exceeds query-count",
    )

    if fixture_root.exists():
        raise FileExistsError(f"{fixture_root} already exists; use a new fixture directory")

    fixture_root.mkdir(parents=True, exist_ok=True)
    for name in ("sift_base.fvecs", "sift_base.bin", "sift_query.fvecs", "sift_groundtruth.ivecs"):
        link_into_fixture(source_root / name, fixture_root / "source" / name)

    prefix = fixture_root.name
    generator = SCRIPT_DIR / "generate_sift1m_zipf_attribute.py"
    generator_log = fixture_root / "prepare_generate_attributes.log"
    with generator_log.open("w", encoding="utf-8") as log_stream:
        subprocess.run(
            [
                sys.executable,
                str(generator),
                "--base-file",
                str(fixture_root / "source" / "sift_base.fvecs"),
                "--output-dir",
                str(fixture_root),
                "--attribute-cardinality",
                str(args.attribute_cardinality),
                "--zipf-exponent",
                str(args.zipf_exponent),
                "--seed",
                str(args.seed),
                "--numeric-column",
                "--numeric-seed",
                str(args.numeric_seed),
                "--output-prefix",
                prefix,
            ],
            cwd=str(REPO_ROOT),
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            check=True,
        )

    attrs_path = fixture_root / f"{prefix}_attrs.npy"
    attrs_u32_path = fixture_root / f"{prefix}_attrs.u32"
    group_tags_path = fixture_root / f"{prefix}_group_tags.txt"
    ensure(attrs_path.is_file(), f"missing generated attrs: {attrs_path}")
    ensure(attrs_u32_path.is_file(), f"missing generated tag payload: {attrs_u32_path}")
    ensure(group_tags_path.is_file(), f"missing generated group tags: {group_tags_path}")

    query_dir = fixture_root / "query"
    query_dir.mkdir(parents=True, exist_ok=True)
    queries = read_xvecs(
        fixture_root / "source" / "sift_query.fvecs",
        np.dtype("<f4"),
        args.query_count,
    )
    ensure(
        queries.shape == (args.query_count, 128),
        f"unexpected query shape {queries.shape}",
    )
    query_vectors_path = query_dir / "query_vectors.npy"
    query_vectors_meta = write_array(
        query_vectors_path,
        np.ascontiguousarray(queries, dtype="<f4"),
    )

    official_truth = read_xvecs(
        fixture_root / "source" / "sift_groundtruth.ivecs",
        np.dtype("<i4"),
        args.query_count,
    )
    ensure(
        official_truth.shape[1] >= args.topk,
        "official truth has fewer than requested topk columns",
    )
    unfilter_ids = np.ascontiguousarray(
        official_truth[:, : args.topk],
        dtype=np.int64,
    )
    unfilter_eligible = np.full((args.query_count, 1), 1_000_000, dtype=np.int64)
    validate_truth_rows(unfilter_ids, args.topk, unfilter_eligible)
    ensure(
        np.all((0 <= unfilter_ids) & (unfilter_ids < 1_000_000)),
        "unfiltered truth contains out-of-range ids",
    )
    unfilter_ids_meta = write_array(
        query_dir / "groundtruth_unfilter_local_ids.npy",
        unfilter_ids,
    )
    unfilter_eligible_meta = write_array(
        query_dir / "eligible_counts_unfilter.npy",
        unfilter_eligible,
    )

    attributes = np.load(attrs_path, allow_pickle=False, mmap_mode="r")
    ensure(
        attributes.shape == (1_000_000, 2) and attributes.dtype == np.dtype("<u4"),
        f"unexpected attr matrix {attributes.shape} {attributes.dtype}",
    )
    key_tags = np.asarray(attributes[:, 0], dtype=np.uint32)
    counts = np.bincount(key_tags, minlength=args.attribute_cardinality).astype(np.int64)
    ensure(
        counts.size == args.attribute_cardinality,
        "attribute cardinality mismatch",
    )

    base_records = np.memmap(
        fixture_root / "source" / "sift_base.fvecs",
        dtype="<f4",
        mode="r",
        shape=(1_000_000, 129),
    )
    ensure(
        np.all(base_records[:, 0].view("<i4") == 128),
        "base fvecs dimension header mismatch",
    )

    workload_specs = [
        ("broad_tag", None, 0),
        ("bin13_20", (13, 20), None),
        ("bin21_50", (21, 50), None),
        ("bin51_200", (51, 200), None),
    ]
    flat_query_tags: dict[str, str] = {}
    truth_manifest: dict[str, Any] = {
        "unfilter": {
            **unfilter_ids_meta,
            "eligible_counts": unfilter_eligible_meta["path"],
            "eligible_counts_sha256": unfilter_eligible_meta["sha256"],
            "eligible_count_summary": {
                "min": int(unfilter_eligible.min()),
                "max": int(unfilter_eligible.max()),
                "mean": float(unfilter_eligible.mean()),
            },
            "selectivity": 1.0,
        }
    }
    workload_manifest: dict[str, Any] = {
        "unfilter": {
            "mode": "unfiltered",
            "label": "unfiltered",
            "eligible_counts": unfilter_eligible_meta["path"],
        }
    }
    predicates: dict[str, Any] = {"unfilter": {"kind": "unfiltered"}}
    tag_histories: list[dict[str, Any]] = []

    for workload_name, bucket, fixed_tag in workload_specs:
        if bucket is None:
            selected_tags = np.full((args.query_count, 1), fixed_tag, dtype="<u4")
        else:
            low, high = bucket
            eligible_tags = np.flatnonzero((counts >= low) & (counts <= high))
            ensure(
                eligible_tags.size > 0,
                f"no tags found in bucket [{low}, {high}]",
            )
            rng = np.random.default_rng(stable_seed(prefix, workload_name, args.seed))
            if eligible_tags.size >= args.query_count:
                chosen = rng.choice(
                    eligible_tags,
                    size=args.query_count,
                    replace=False,
                )
            else:
                chosen = np.resize(rng.permutation(eligible_tags), args.query_count)
            selected_tags = np.ascontiguousarray(chosen.reshape(args.query_count, 1), dtype="<u4")

        ids, distances, eligible_counts = grouped_exact_topk(
            base_records,
            queries,
            key_tags,
            selected_tags,
            args.topk,
            args.query_batch,
        )
        validate_truth_rows(ids, args.topk, eligible_counts)
        validate_tagged_truth_membership(ids, key_tags, selected_tags)
        row_tags = np.asarray(selected_tags[:, 0], dtype=np.uint32)
        selected_count_values = counts[row_tags.astype(np.int64)]
        ensure(
            int(selected_count_values.min()) >= args.topk,
            f"{workload_name}: selected tags below topk",
        )

        query_tags_meta = write_array(
            query_dir / f"query_tags_{workload_name}.npy",
            selected_tags,
        )
        gt_ids_meta = write_array(
            query_dir / f"groundtruth_{workload_name}_local_ids.npy",
            np.ascontiguousarray(ids, dtype=np.int64),
        )
        gt_dist_meta = write_array(
            query_dir / f"groundtruth_{workload_name}_dists.npy",
            np.ascontiguousarray(distances, dtype="<f4"),
        )
        eligible_meta = write_array(
            query_dir / f"eligible_counts_{workload_name}.npy",
            np.ascontiguousarray(eligible_counts, dtype=np.int64),
        )
        flat_query_tags[workload_name] = query_tags_meta["path"]
        truth_manifest[workload_name] = {
            **gt_ids_meta,
            "distances": gt_dist_meta["path"],
            "distance_sha256": gt_dist_meta["sha256"],
            "eligible_counts": eligible_meta["path"],
            "eligible_counts_sha256": eligible_meta["sha256"],
            "eligible_count_summary": {
                "min": int(eligible_counts.min()),
                "max": int(eligible_counts.max()),
                "mean": float(eligible_counts.mean()),
            },
            "selectivity": float(np.mean(eligible_counts[:, 0] / 1_000_000.0)),
        }
        predicates[workload_name] = {
            "kind": "categorical_eq_per_query" if bucket is not None else "categorical_eq",
            "column": 0,
            "tag": int(fixed_tag) if fixed_tag is not None else None,
            "tag_count_bucket": list(bucket) if bucket is not None else None,
        }
        workload_manifest[workload_name] = {
            "mode": "categorical",
            "label": workload_name,
            "tag_column": 0,
            "query_tags": query_tags_meta["path"],
            "eligible_counts": eligible_meta["path"],
        }
        unique_tags, multiplicities = np.unique(row_tags, return_counts=True)
        tag_histories.append(
            {
                "workload": workload_name,
                "selected_tags": int(unique_tags.size),
                "tag_ids_sha256": query_tags_meta["sha256"],
                "query_allocation_min": int(multiplicities.min()),
                "query_allocation_max": int(multiplicities.max()),
            }
        )

    query_manifest = {
        "schema_version": 2,
        "generated_at_utc": utc_now(),
        "dataset": "SIFT1M",
        "fixture_name": prefix,
        "metric": "squared L2",
        "query_count": args.query_count,
        "topk": args.topk,
        "warmup_count": args.warmup_count,
        "measure_offset": args.measure_offset,
        "measured_query_count": args.measured_query_count,
        "query_selection": f"first {args.query_count} rows from sift_query.fvecs",
        "base_file": str((fixture_root / "source" / "sift_base.fvecs").resolve()),
        "query_file": str((fixture_root / "source" / "sift_query.fvecs").resolve()),
        "attributes": str(attrs_path.resolve()),
        "query_vectors": query_vectors_meta["path"],
        "flat_query_tags": flat_query_tags,
        "predicates": predicates,
        "truth": truth_manifest,
        "workloads": workload_manifest,
        "selected_tag_workloads": tag_histories,
    }
    json_dump(query_dir / "workloads.json", query_manifest)

    plan = {
        "schema_version": 1,
        "generated_at_utc": utc_now(),
        "profile": MAIN_PROFILE,
        "fixture_root": str(fixture_root),
        "canonical_template_ini": str(CANONICAL_TEMPLATE),
        "canonical_template_sha256": sha256(CANONICAL_TEMPLATE),
        "default_search_grid": list(DEFAULT_SEARCH_GRID),
        "default_trials": DEFAULT_TRIALS,
        "default_workloads": build_default_workloads(MAIN_PROFILE),
        "extreme_sparse_dedicated_route_compiled": False,
        "main8192_build_controls": {
            "BuildHead.NumberOfThreads": 1,
            "BuildHead.BKTSeed": 0,
            "BuildHead.TPTSeed": 0,
            "SelectHead.NumberOfThreads": 24,
            "BuildSSDIndex.NumberOfThreads": 24,
        },
        "notes": [
            "Dedicated extreme-sparse routing is compile-time disabled in current wrappers; copied INI EST flags do not imply an active separate sparse route.",
            "Ordinary sparse fallback remains governed by SparseFallbackMaxHeads=64 and SparseFallbackMaxPostingPages=256.",
            "The explicit extra-support budget 262144 is safely above the structural 8192*16 upper bound 131072 for the main fixture.",
            "Main 8192-tag runs use native BuildHead.BKTSeed=0, TPTSeed=0, and NumberOfThreads=1 for repeatable CPU head construction; SelectHead and BuildSSDIndex remain at 24 threads.",
        ],
        "variants": [variant.to_json() for variant in profile_variants(MAIN_PROFILE)],
        "source_fixture": str(reference_fixture),
        "source_hashes": tool_hashes(
            [
                fixture_root / "source" / "sift_base.fvecs",
                fixture_root / "source" / "sift_base.bin",
                fixture_root / "source" / "sift_query.fvecs",
                fixture_root / "source" / "sift_groundtruth.ivecs",
                attrs_u32_path,
                attrs_path,
                group_tags_path,
                query_dir / "workloads.json",
            ]
        ),
    }
    json_dump(fixture_root / "limited_tag_support_expansion_plan.json", plan)
    print(f"prepared fixture: {fixture_root}")


def create_run_manifest(
    fixture_root: Path,
    run_dir: Path,
    profile: str,
    variants: list[VariantSpec],
    nprobes: list[int],
    workloads: list[str],
) -> dict[str, Any]:
    fixture_manifest = load_fixture_manifest(fixture_root)
    return {
        "schema_version": 1,
        "created_at_utc": utc_now(),
        "repo_root": str(REPO_ROOT),
        "fixture_root": str(fixture_root),
        "fixture_name": fixture_root.name,
        "profile": profile,
        "canonical_template_ini": str(CANONICAL_TEMPLATE),
        "canonical_template_sha256": sha256(CANONICAL_TEMPLATE),
        "run_dir": str(run_dir),
        "query_manifest": fixture_manifest["_query_manifest_path"],
        "dataset_manifest": fixture_manifest["_path"],
        "nprobes": nprobes,
        "workloads": workloads,
        "extreme_sparse_dedicated_route_compiled": False,
        "comparison_classification": (
            "same_fixture_strict_o_identity_required"
            if profile == MAIN_PROFILE
            else "independent_rebuild_no_overflow_control"
        ),
        "build_controls": (
            {
                "BuildHead.NumberOfThreads": 1,
                "BuildHead.BKTSeed": 0,
                "BuildHead.TPTSeed": 0,
                "SelectHead.NumberOfThreads": 24,
                "BuildSSDIndex.NumberOfThreads": 24,
            }
            if profile == MAIN_PROFILE
            else {
                "BuildHead.NumberOfThreads": 24,
                "SelectHead.NumberOfThreads": 24,
                "BuildSSDIndex.NumberOfThreads": 24,
            }
        ),
        "notes": profile_notes(profile),
        "driver_sha256": sha256(Path(__file__)),
        "launcher_sha256": sha256(SCRIPT_DIR / "run_spann_attr_build.sh"),
        "native_source_hashes": tool_hashes(
            REPO_ROOT / path for path in [
                "AnnService/inc/Core/BKT/ParameterDefinitionList.h",
                "AnnService/inc/Core/KDT/ParameterDefinitionList.h",
                "AnnService/inc/Core/Common/BKTree.h",
                "AnnService/inc/Core/Common/NeighborhoodGraph.h",
                "AnnService/inc/Core/SPANN/LimitedTagSupport.h",
                "AnnService/inc/Core/SPANN/LimitedTagSupportExpansion.h",
                "AnnService/inc/Core/SPANN/ExtraStaticSearcher.h",
                "AnnService/inc/Core/SPANN/HeadNodeMetadata.h",
                "AnnService/inc/Core/SPANN/Index.h",
                "AnnService/src/Core/SPANN/SPANNIndex.cpp",
                "Tools/benchmarks/InspectLimitedTagCoverage.cpp",
            ]
        ),
        "variants": {
            variant.name: variant.to_json()
            for variant in variants
        },
    }


def initialize_build_run(args: argparse.Namespace) -> tuple[Path, Path, dict[str, Any], str, list[VariantSpec]]:
    fixture_root = args.fixture_root.resolve()
    profile = args.profile or benchmark_profile_for_fixture(fixture_root)
    selected = resolve_variants(
        profile,
        [name.strip() for name in args.variants.split(",")] if args.variants else None,
    )
    ensure(args.run_label or args.run_dir, "pass --run-label or --run-dir")
    if args.run_label:
        ensure(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.run_label) is not None,
               "run-label must not contain path separators or traversal")
    run_dir = (
        args.run_dir.resolve()
        if args.run_dir
        else (
            (args.bench_root.resolve() if args.bench_root else fixture_root / "limited_tag_support_expansion_runs")
            / f"{fixture_root.name}_{args.run_label}"
        )
    )
    if run_dir.exists():
        raise FileExistsError(f"run dir already exists: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=False)
    fixture_manifest = load_fixture_manifest(fixture_root)
    manifest = create_run_manifest(
        fixture_root,
        run_dir,
        profile,
        selected,
        parse_int_list(args.nprobes),
        [name.strip() for name in args.workloads.split(",")] if args.workloads else build_default_workloads(profile),
    )
    for binary_name in ("spannbuilder", "spannaclbench", "spannsupportaudit"):
        binary_path = REPO_ROOT / "Release" / binary_name
        if binary_path.is_file():
            manifest.setdefault("binary_hashes", {})[binary_name] = binary_hash(binary_path)
    json_dump(run_dir / "run_manifest.json", manifest)
    return fixture_root, run_dir, fixture_manifest, profile, selected


def build_variants(args: argparse.Namespace) -> None:
    fixture_root, run_dir, fixture_manifest, profile, variants = initialize_build_run(args)
    if any(not variant.reference_index for variant in variants) and not args.emit_only:
        ensure(
            (REPO_ROOT / "Release" / "spannbuilder").is_file(),
            f"missing builder binary: {REPO_ROOT / 'Release' / 'spannbuilder'}",
        )
    template = parse_ini(CANONICAL_TEMPLATE)
    attribute_cardinality = int(fixture_manifest["attribute_cardinality"])
    attrs_u32_path = fixture_root / f"{fixture_root.name}_attrs.u32"
    group_tags_path = fixture_root / f"{fixture_root.name}_group_tags.txt"
    canonical_index_dir = Path(template["Base"]["IndexDirectory"])

    run_manifest = read_json(run_dir / "run_manifest.json")
    for variant in variants:
        variant_dir = run_dir / "variants" / variant.name
        variant_dir.mkdir(parents=True, exist_ok=True)
        build_dir = variant_dir / "build"
        build_dir.mkdir(parents=True, exist_ok=True)
        index_dir = (
            canonical_index_dir
            if variant.reference_index
            else build_dir / "index"
        )
        tmp_dir = build_dir / "tmp"
        if not variant.reference_index:
            guard_output_path(index_dir, run_dir)
            guard_output_path(tmp_dir, run_dir)
        final_ini = render_ini_variant(
            template,
            fixture_root,
            attribute_cardinality,
            attrs_u32_path,
            group_tags_path,
            index_dir,
            tmp_dir,
            variant,
            profile,
        )
        primary_ini = render_primary_build_ini(final_ini)
        final_ini_path = build_dir / "build_final.ini"
        primary_ini_path = build_dir / "build_primary.ini"
        write_ini(final_ini_path, final_ini)
        write_ini(primary_ini_path, primary_ini)

        build_meta = {
            "variant": variant.to_json(),
            "reference_index": variant.reference_index,
            "emit_only": bool(args.emit_only),
            "index_dir": str(index_dir),
            "tmp_dir": str(tmp_dir),
            "final_ini": str(final_ini_path),
            "primary_ini": str(primary_ini_path),
            "build_controls": run_manifest["build_controls"],
            "artifact_hashes": tool_hashes(
                [
                    final_ini_path,
                    primary_ini_path,
                    CANONICAL_TEMPLATE,
                    attrs_u32_path,
                    group_tags_path,
                    fixture_root / "source" / "sift_base.bin",
                ]
            ),
        }
        builder_path = REPO_ROOT / "Release" / "spannbuilder"
        if builder_path.is_file():
            build_meta["binary_hashes"] = {
                "spannbuilder": binary_hash(builder_path)
            }
        if variant.reference_index:
            ensure(
                (index_dir / "tenant_0" / "indexloader.ini").is_file(),
                f"missing prebuilt reference index: {index_dir / 'tenant_0'}",
            )
            build_meta["status"] = "prebuilt_reference"
            tenant_dir = index_dir / "tenant_0"
            build_meta["tenant_dir"] = str(tenant_dir)
            build_meta["h1_source_vid_sha256"] = sha256(tenant_dir / "SPTAGHeadVectorIDs.bin")
            json_dump(build_dir / "build_metadata.json", build_meta)
            run_manifest["variants"][variant.name]["index_dir"] = str(index_dir)
            run_manifest["variants"][variant.name]["tenant_dir"] = str(tenant_dir)
            continue
        if args.emit_only:
            build_meta["status"] = "emitted_only"
            build_meta["tenant_dir"] = str(index_dir / "tenant_0")
            json_dump(build_dir / "build_metadata.json", build_meta)
            run_manifest["variants"][variant.name]["index_dir"] = str(index_dir)
            run_manifest["variants"][variant.name]["tenant_dir"] = str(index_dir / "tenant_0")
            continue

        build_meta["status"] = "building"
        build_meta["tenant_dir"] = str(index_dir / "tenant_0")
        run_manifest["variants"][variant.name]["index_dir"] = str(index_dir)
        run_manifest["variants"][variant.name]["tenant_dir"] = str(index_dir / "tenant_0")
        json_dump(build_dir / "build_metadata.json", build_meta)
        json_dump(run_dir / "run_manifest.json", run_manifest)
        print(f"building {variant.name}: {final_ini_path}", flush=True)
        build_command = [
            "bash", str(SCRIPT_DIR / "run_spann_attr_build.sh"), str(final_ini_path)
        ]
        if args.cpu_affinity:
            build_command = ["taskset", "-c", args.cpu_affinity] + build_command
        launcher_meta = run_command(
            build_command,
            build_dir / "launcher.stdout.log",
            build_dir / "launcher.stderr.log",
            build_dir / "launcher.time.txt",
            REPO_ROOT,
        )
        build_meta["launcher"] = launcher_meta
        build_meta["status"] = "built" if launcher_meta["returncode"] == 0 else "failed"
        json_dump(build_dir / "build_metadata.json", build_meta)
        ensure(launcher_meta["returncode"] == 0,
               f"{variant.name}: build failed; see {build_dir / 'launcher.stderr.log'}")

        tenant_dir = index_dir / "tenant_0"
        required_paths = [
            tenant_dir / "indexloader.ini",
            tenant_dir / "SPTAGHeadVectorIDs.bin",
            tenant_dir / "SPTAGFullList.bin",
            tenant_dir / "limited_tag_support.bin",
            tenant_dir / "HeadIndex" / "head_node_meta.bin",
            tenant_dir / "HeadIndex" / "tag_node_index.bin",
        ]
        for required in required_paths:
            ensure(required.is_file(), f"{variant.name}: missing build artifact {required}")

        build_meta.update(
            {
                "status": "built",
                "tenant_dir": str(tenant_dir),
                "launcher": launcher_meta,
                "h1_source_vid_sha256": sha256(tenant_dir / "SPTAGHeadVectorIDs.bin"),
            }
        )
        json_dump(build_dir / "build_metadata.json", build_meta)
        run_manifest["variants"][variant.name]["index_dir"] = str(index_dir)
        run_manifest["variants"][variant.name]["tenant_dir"] = str(tenant_dir)
        json_dump(run_dir / "run_manifest.json", run_manifest)

    json_dump(run_dir / "run_manifest.json", run_manifest)
    print(f"prepared build run: {run_dir}")


def hierarchy_geometry_hashes(tenant_dir: Path) -> dict[str, str]:
    paths = [tenant_dir / "SPTAGHeadVectorIDs.bin"]
    paths.extend(sorted(tenant_dir.glob("SPTAGSecondLevelHeadVectorIDs.bin*")))
    paths.extend(tenant_dir / "SecondLevelHeadIndex" / name
                 for name in ("tree.bin", "graph.bin", "vectors.bin"))
    result = {}
    for path in paths:
        ensure(path.is_file(), f"missing hierarchy geometry artifact: {path}")
        result[str(path.relative_to(tenant_dir))] = sha256(path)
    csr_header = struct.Struct("<Q8I2d6Q")
    for path in sorted(tenant_dir.glob("second_level_head_postings.bin*")):
        with path.open("rb") as stream:
            header_bytes = stream.read(csr_header.size)
            ensure(len(header_bytes) == csr_header.size, f"truncated CSR header: {path}")
            header = csr_header.unpack(header_bytes)
            ensure(header[1:3] == (3, 104) and header[6] == 4,
                   f"unsupported CSR provenance layout: {path}")
            body_size = 8 * (header[4] + 1) + header[6] * header[11]
            body = stream.read(body_size)
            ensure(len(body) == body_size, f"truncated CSR geometry: {path}")
            result[path.name + ":offsets_members"] = hashlib.sha256(body).hexdigest()
    ensure(any(name.endswith(":offsets_members") for name in result),
           "missing hierarchy CSR geometry")
    return result


def require_matching_audits(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    variants = {}
    expanded_bases = {}
    for name, metadata in manifest["variants"].items():
        audit = read_json(run_dir / "variants" / name / "audit" / "audit_metadata.json")
        summary = audit["summary"]
        tenant_dir = Path(metadata["tenant_dir"])
        ensure(int(summary["unique_pure_vids"]) ==
               int(summary["documents"]) - int(summary["heads"]) and
               int(summary["head_records"]) == 0, f"{name}: incomplete H coverage")
        variants[name] = {
            "h1_source_vid_sha256": sha256(tenant_dir / "SPTAGHeadVectorIDs.bin"),
            "original_membership_fingerprint": summary["original_membership_fingerprint"],
            "geometry": hierarchy_geometry_hashes(tenant_dir),
        }
        if metadata["enable_support_expansion"]:
            with (tenant_dir / "limited_tag_support.bin").open("rb") as stream:
                header = struct.unpack("<5I", stream.read(20))
                ensure(header[1:3] == (4, 80), f"{name}: expected expanded V4 support")
                stream.seek(header[2])
                base_bytes = stream.read(header[3] * header[4] * 4)
                ensure(len(base_bytes) == header[3] * header[4] * 4,
                       f"{name}: truncated base support")
                expanded_bases[name] = hashlib.sha256(base_bytes).hexdigest()
        if manifest["profile"] == MAIN_PROFILE and metadata["support_floor"] == 16:
            ensure(int(summary["extra_supports"]) > 0 and int(summary["extra_h_postings"]) > 0,
                   f"{name}: fixture did not exercise nonempty O-derived support expansion")
    reference = next(iter(variants.values()))
    report = {
        "comparison_classification": manifest["comparison_classification"],
        "variants": variants,
        "consistent_h1_source_vids": all(
            row["h1_source_vid_sha256"] == reference["h1_source_vid_sha256"]
            for row in variants.values()),
        "consistent_original_membership_fingerprint": all(
            row["original_membership_fingerprint"] == reference["original_membership_fingerprint"]
            for row in variants.values()),
        "consistent_hierarchy_geometry": all(
            row["geometry"] == reference["geometry"] for row in variants.values()),
        "expanded_base_support_sha256": expanded_bases,
        "consistent_expanded_base_support": len(set(expanded_bases.values())) <= 1,
    }
    json_dump(run_dir / "audit_identity.json", report)
    if manifest["profile"] == MAIN_PROFILE:
        ensure(all(report[key] for key in (
            "consistent_h1_source_vids", "consistent_original_membership_fingerprint",
            "consistent_hierarchy_geometry", "consistent_expanded_base_support")),
            f"fixed-geometry/O/base-support comparison failed; see {run_dir / 'audit_identity.json'}")
    return report


def audit_run(args: argparse.Namespace) -> None:
    run_dir = args.run_dir.resolve()
    manifest = read_json(run_dir / "run_manifest.json")
    audit_binary = REPO_ROOT / "Release" / "spannsupportaudit"
    ensure(audit_binary.is_file(), f"missing audit binary: {audit_binary}")
    for variant_name, variant_meta in manifest["variants"].items():
        tenant_dir = Path(variant_meta["tenant_dir"])
        ensure(tenant_dir.is_dir(), f"missing tenant dir for {variant_name}: {tenant_dir}")
        audit_dir = run_dir / "variants" / variant_name / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        prefix = audit_dir / "limited_tag_support"
        command = [str(audit_binary), str(tenant_dir), str(prefix)]
        if args.cpu_affinity:
            command = ["taskset", "-c", args.cpu_affinity] + command
        audit_meta = run_command(
            command,
            audit_dir / "audit.stdout.log",
            audit_dir / "audit.stderr.log",
            audit_dir / "audit.time.txt",
            REPO_ROOT,
        )
        ensure(audit_meta["returncode"] == 0, f"{variant_name}: audit failed")
        summary_path = audit_dir / "limited_tag_support.summary.json"
        tags_path = audit_dir / "limited_tag_support.tags.csv"
        postings_path = audit_dir / "limited_tag_support.postings.csv"
        for path in (summary_path, tags_path, postings_path):
            ensure(path.is_file(), f"{variant_name}: missing audit output {path}")
        audit_meta["outputs"] = {
            "summary": str(summary_path),
            "tags": str(tags_path),
            "postings": str(postings_path),
        }
        audit_meta["binary_hashes"] = {
            "spannsupportaudit": binary_hash(audit_binary)
        }
        audit_meta["hashes"] = tool_hashes([summary_path, tags_path, postings_path, tenant_dir / "SPTAGHeadVectorIDs.bin"])
        audit_meta["summary"] = read_json(summary_path)
        json_dump(audit_dir / "audit_metadata.json", audit_meta)
    require_matching_audits(run_dir, manifest)
    print(f"audited run: {run_dir}")


def parse_search_ini_metadata(path: Path) -> dict[str, Any]:
    search = parse_ini(path)
    section = search["SearchSSDIndex"]
    return {
        "internal_result_num": int(section["InternalResultNum"]),
        "max_check": int(section["MaxCheck"]),
        "second_level_max_check": int(section["SecondLevelMaxCheck"]),
        "threads": int(section["NumberOfThreads"]),
        "graph_signature_pruning": parse_native_bool(section["SecondLevelGraphSignaturePruning"]),
    }


def benchmark_run(args: argparse.Namespace) -> None:
    run_dir = args.run_dir.resolve()
    manifest = read_json(run_dir / "run_manifest.json")
    ensure(args.trials > 0, "trials must be positive")
    require_matching_audits(run_dir, manifest)
    fixture_root = Path(manifest["fixture_root"])
    query_manifest = load_fixture_manifest(fixture_root)["_query_manifest"]
    workloads = workload_entries(query_manifest)
    workload_names = [name.strip() for name in args.workloads.split(",")] if args.workloads else manifest["workloads"]
    nprobes = parse_int_list(args.nprobes) if args.nprobes else manifest["nprobes"]
    topk = int(query_manifest.get("topk", DEFAULT_TOPK))
    warmup_count = int(query_manifest.get("warmup_count", DEFAULT_WARMUP))
    measure_offset = int(query_manifest.get("measure_offset", DEFAULT_MEASURE_OFFSET))
    measured_query_count = int(query_manifest.get("measured_query_count", DEFAULT_MEASURED_QUERIES))
    bench_binary = REPO_ROOT / "Release" / "spannaclbench"
    ensure(bench_binary.is_file(), f"missing benchmark binary: {bench_binary}")
    template = parse_ini(CANONICAL_TEMPLATE)

    benchmark_root = run_dir / "benchmarks"
    benchmark_root.mkdir(parents=True, exist_ok=True)
    trial_rows: list[dict[str, Any]] = []
    variant_names = [name.strip() for name in args.variants.split(",")] if args.variants else list(manifest["variants"].keys())
    ensure(bool(variant_names) and len(set(variant_names)) == len(variant_names) and
           all(name in manifest["variants"] for name in variant_names), "invalid benchmark variants")
    ensure(bool(workload_names) and len(set(workload_names)) == len(workload_names) and
           all(name in workloads for name in workload_names), "invalid benchmark workloads")
    reference_work: dict[tuple[str, str, int], dict[str, Any]] = {}

    search_ini_paths: dict[tuple[str, int], Path] = {}
    for variant_name in variant_names:
        search_dir = run_dir / "variants" / variant_name / "search"
        search_dir.mkdir(parents=True, exist_ok=True)
        for nprobe in nprobes:
            search_ini_path = search_dir / f"search_nprobe{nprobe:04d}.ini"
            write_ini(search_ini_path, render_search_ini(template, nprobe))
            search_ini_paths[(variant_name, nprobe)] = search_ini_path

    for trial in range(1, args.trials + 1):
        variant_order = variant_names if trial % 2 else list(reversed(variant_names))
        trial_dir = benchmark_root / f"trial_{trial:02d}"
        trial_dir.mkdir(parents=True, exist_ok=True)
        for workload_name in workload_names:
            ensure(workload_name in workloads, f"unknown workload {workload_name}")
            workload = workloads[workload_name]
            truth_path = truth_ids_path(workload["truth"])
            for variant_name in variant_order:
                tenant_index = Path(manifest["variants"][variant_name]["index_dir"])
                stdout_path = trial_dir / f"{workload_name}__{variant_name}.stdout.log"
                stderr_path = trial_dir / f"{workload_name}__{variant_name}.stderr.log"
                time_path = trial_dir / f"{workload_name}__{variant_name}.time.txt"
                command = [
                    str(bench_binary),
                    "--index",
                    str(tenant_index),
                    "--queries",
                    query_manifest["query_vectors"],
                    "--truth",
                    str(truth_path),
                    "--topk",
                    str(topk),
                    "--warmup",
                    str(warmup_count),
                    "--measure-offset",
                    str(measure_offset),
                    "--max-queries",
                    str(measured_query_count),
                    "--tenant",
                    "0",
                    "--value-type",
                    "Float",
                ]
                if workload["mode"] == "categorical":
                    command.extend([
                        "--query-tags",
                        str(workload["query_tags"]),
                        "--tag-column",
                        str(workload.get("tag_column", 0)),
                    ])
                elif workload["mode"] == "dnf":
                    command.extend([
                        "--query-dnf",
                        str(workload["query_dnf"]),
                    ])
                for nprobe in nprobes:
                    command.extend([
                        "--search-sweep-ini",
                        str(search_ini_paths[(variant_name, nprobe)]),
                    ])
                if args.cpu_affinity:
                    command = ["taskset", "-c", args.cpu_affinity] + command

                meta = run_command(command, stdout_path, stderr_path, time_path, REPO_ROOT)
                ensure(meta["returncode"] == 0, f"trial {trial} {variant_name} {workload_name} failed")

                jsonl_path = trial_dir / f"{workload_name}__{variant_name}.jsonl"
                parsed_rows: list[dict[str, Any]] = []
                with stdout_path.open("r", encoding="utf-8") as stream, jsonl_path.open("w", encoding="utf-8") as jsonl:
                    for line in stream:
                        stripped = line.strip()
                        if not stripped.startswith("{") or not stripped.endswith("}"):
                            continue
                        row = json.loads(stripped)
                        search_ini_path = Path(row["search_ini"])
                        sweep_meta = parse_search_ini_metadata(search_ini_path)
                        ensure(int(row["failed_queries"]) == 0 and
                               int(row["queries"]) == measured_query_count and
                               int(row["measure_offset"]) == measure_offset,
                               f"{variant_name}/{workload_name}: incomplete native benchmark")
                        ensure(math.isfinite(float(row["qps"])) and float(row["qps"]) > 0 and
                               math.isfinite(float(row["recall"])) and 0 <= float(row["recall"]) <= 1,
                               "invalid native QPS/recall")
                        probe = sweep_meta["internal_result_num"]
                        ensure(probe in nprobes and search_ini_path ==
                               search_ini_paths[(variant_name, probe)], "unexpected native search INI")
                        semantic = {key: value for key, value in row.items()
                                    if key.endswith("_per_query") or key in {
                                        "recall", "queries", "failed_queries", "match_rate",
                                        "unique_match_rate", "scanned_occurrence_to_unique_ratio"}}
                        identity = (variant_name, workload_name, probe)
                        ensure(identity not in reference_work or reference_work[identity] == semantic,
                               f"native work changed across trials for {identity}")
                        reference_work[identity] = semantic
                        row.update(
                            {
                                "fixture": manifest["fixture_name"],
                                "variant": variant_name,
                                "trial": trial,
                                "workload": workload_name,
                                "nprobe": sweep_meta["internal_result_num"],
                                "second_level_max_check": sweep_meta["second_level_max_check"],
                                "graph_signature_pruning": sweep_meta["graph_signature_pruning"],
                            }
                        )
                        jsonl.write(json.dumps(row) + "\n")
                        parsed_rows.append(row)
                        trial_rows.append(row)
                ensure(
                    len(parsed_rows) == len(nprobes) and
                    {row["nprobe"] for row in parsed_rows} == set(nprobes),
                    f"expected {len(nprobes)} JSON rows, got {len(parsed_rows)} for {variant_name}/{workload_name}/trial{trial}",
                )
                json_dump(
                    trial_dir / f"{workload_name}__{variant_name}.metadata.json",
                    {
                        "command": meta["command"],
                        "binary_hashes": {
                            "spannaclbench": binary_hash(bench_binary)
                        },
                        "timing": meta["timing"],
                        "stdout_sha256": sha256(stdout_path),
                        "stderr_sha256": sha256(stderr_path),
                        "jsonl_sha256": sha256(jsonl_path),
                    },
                )

    combined_path = benchmark_root / "benchmark_trials.jsonl"
    with combined_path.open("w", encoding="utf-8") as stream:
        for row in trial_rows:
            stream.write(json.dumps(row) + "\n")
    json_dump(benchmark_root / "protocol.json", {
        "trials": args.trials, "nprobes": nprobes, "variants": variant_names,
        "workloads": workload_names, "cpu_affinity": args.cpu_affinity,
        "queries": measured_query_count, "measure_offset": measure_offset,
        "warmup": warmup_count, "topk": topk, "driver_sha256": sha256(Path(__file__)),
        "binary": binary_hash(bench_binary),
    })
    print(f"benchmarked run: {run_dir}")


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure(rows, f"refusing to write empty csv: {path}")
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def finalize_run(args: argparse.Namespace) -> None:
    run_dir = args.run_dir.resolve()
    manifest = read_json(run_dir / "run_manifest.json")
    identity_checks = require_matching_audits(run_dir, manifest)
    protocol = read_json(run_dir / "benchmarks" / "protocol.json")
    final_dir = run_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    benchmark_jsonl = run_dir / "benchmarks" / "benchmark_trials.jsonl"
    ensure(benchmark_jsonl.is_file(), f"missing benchmark rows: {benchmark_jsonl}")

    trial_rows: list[dict[str, Any]] = []
    with benchmark_jsonl.open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                trial_rows.append(json.loads(line))
    ensure(trial_rows, "benchmark results are empty")

    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in trial_rows:
        grouped[(row["variant"], row["workload"], int(row["nprobe"]))].append(row)
    expected_groups = {
        (variant, workload, probe)
        for variant in protocol["variants"]
        for workload in protocol["workloads"]
        for probe in protocol["nprobes"]
    }
    ensure(set(grouped) == expected_groups, "incomplete benchmark operating-point grid")

    benchmark_summary: list[dict[str, Any]] = []
    for (variant, workload, nprobe), rows in sorted(grouped.items()):
        ensure(len(rows) == protocol["trials"] and
               {int(row["trial"]) for row in rows} == set(range(1, protocol["trials"] + 1)),
               f"incomplete or duplicated trials for {variant}/{workload}/{nprobe}")
        qps_values = [float(row["qps"]) for row in rows]
        recall_values = [float(row["recall"]) for row in rows]
        latency_values = [float(row["mean_latency_ms"]) for row in rows]
        failed_values = [int(row.get("failed_queries", 0)) for row in rows]
        ensure(max(failed_values) == 0, "cannot publish failed queries")
        exemplar = rows[0]
        benchmark_summary.append(
            {
                "fixture": manifest["fixture_name"],
                "variant": variant,
                "workload": workload,
                "nprobe": nprobe,
                "trial_count": len(rows),
                "qps_median": statistics.median(qps_values),
                "qps_min": min(qps_values),
                "qps_max": max(qps_values),
                "recall_median": statistics.median(recall_values),
                "recall_min": min(recall_values),
                "recall_max": max(recall_values),
                "latency_ms_median": statistics.median(latency_values),
                "latency_ms_min": min(latency_values),
                "latency_ms_max": max(latency_values),
                "failed_queries_max": max(failed_values),
                "queries": int(exemplar["queries"]),
                "measure_offset": int(exemplar["measure_offset"]),
                "search_api": exemplar["search_api"],
                "search_ini": exemplar["search_ini"],
                "second_level_max_check": int(exemplar["second_level_max_check"]),
                "graph_signature_pruning": bool(exemplar["graph_signature_pruning"]),
                **{key: value for key, value in exemplar.items()
                   if key.endswith("_per_query") or key in {
                       "match_rate", "unique_match_rate", "scanned_occurrence_to_unique_ratio"}},
            }
        )
    write_csv(final_dir / "benchmark_summary.csv", benchmark_summary)

    merged_tag_rows: list[dict[str, Any]] = []
    audit_summary_rows: list[dict[str, Any]] = []
    structure_summary: list[dict[str, Any]] = []
    attribution = {
        "fixture": manifest["fixture_name"],
        "comparison_classification": manifest.get("comparison_classification", ""),
        "extreme_sparse_dedicated_route_compiled": False,
        "variants": {},
        "h1_source_vid_sha256": {},
        "original_membership_fingerprint": {},
        "consistent_h1_source_vids": True,
        "consistent_original_membership_fingerprint": True,
        "consistent_hierarchy_geometry": identity_checks["consistent_hierarchy_geometry"],
        "consistent_expanded_base_support": identity_checks["consistent_expanded_base_support"],
        "notes": list(manifest.get("notes", [])),
    }
    baseline_summary: dict[str, Any] | None = None
    for variant_name in manifest["variants"].keys():
        audit_meta_path = run_dir / "variants" / variant_name / "audit" / "audit_metadata.json"
        ensure(audit_meta_path.is_file(), f"missing audit metadata for {variant_name}")
        audit_meta = read_json(audit_meta_path)
        summary = audit_meta["summary"]
        realized_extras = int(summary["extra_h_postings"])
        extra_supports = int(summary["extra_supports"])
        ensure(0 <= realized_extras <= extra_supports, "invalid realized extra-support count")
        computed_unused = extra_supports - realized_extras
        ensure(int(summary.get("unused_extra_supports", computed_unused)) == computed_unused,
               "unused extra supports disagree with actual nonempty H coverage")
        summary["unused_extra_supports"] = computed_unused
        build_meta = read_json(run_dir / "variants" / variant_name / "build" / "build_metadata.json")
        h1_hash = build_meta["h1_source_vid_sha256"]
        attribution["h1_source_vid_sha256"][variant_name] = h1_hash
        attribution["original_membership_fingerprint"][variant_name] = summary["original_membership_fingerprint"]
        attribution["variants"][variant_name] = {
            "enable_support_expansion": manifest["variants"][variant_name]["enable_support_expansion"],
            "support_floor": manifest["variants"][variant_name]["support_floor"],
            "extra_supports": int(summary["extra_supports"]),
            "extra_h_postings": int(summary.get("extra_h_postings", 0)),
            "unused_extra_supports": int(summary.get("unused_extra_supports", 0)),
            "source_capped_tags": int(summary.get("source_capped_tags", 0)),
            "below_required_tags": int(summary["below_required_tags"]),
            "below_floor_tags": int(summary["below_floor_tags"]),
            "max_expanded_pure_pages": int(summary["max_expanded_pure_pages"]),
            "support_file_bytes": int(summary["support_file_bytes"]),
            "unique_original_vids": int(summary.get("unique_original_vids", 0)),
        }
        failed_cap_checks = [
            key for key, value in summary.items()
            if isinstance(value, bool)
            and value is False
            and ("cap" in key.lower() or "replica" in key.lower())
        ]
        if failed_cap_checks:
            attribution["notes"].append(
                f"{variant_name} reported failed replica/cap checks: {', '.join(sorted(failed_cap_checks))}."
            )
        audit_summary_rows.append(
            {
                "fixture": manifest["fixture_name"],
                "variant": variant_name,
                **summary,
            }
        )
        if variant_name in {"legacy_fixed_f1", "legacy_active_baseline"}:
            baseline_summary = summary

        tag_rows = read_csv_rows(Path(audit_meta["outputs"]["tags"]))
        for row in tag_rows:
            enriched = {
                "fixture": manifest["fixture_name"],
                "variant": variant_name,
            }
            for key, value in row.items():
                enriched[key] = coerce_number(value)
            merged_tag_rows.append(enriched)

        low_count_rows = [row for row in tag_rows if int(row["vectors"]) <= 200]
        def avg(name: str) -> float:
            if not low_count_rows:
                return 0.0
            return float(statistics.mean(float(row[name]) for row in low_count_rows))

        structure_summary.append(
            {
                "fixture": manifest["fixture_name"],
                "variant": variant_name,
                "enable_support_expansion": manifest["variants"][variant_name]["enable_support_expansion"],
                "support_floor": manifest["variants"][variant_name]["support_floor"],
                "configured_extra_support_budget": int(
                    manifest["variants"][variant_name]["max_extra_supports"]
                ),
                "documents": int(summary["documents"]),
                "heads": int(summary["heads"]),
                "tag_count": int(summary["tag_count"]),
                "records": int(summary["records"]),
                "pure_records": int(summary["pure_records"]),
                "original_records": int(summary["original_records"]),
                "payload_bytes": int(summary["payload_bytes"]),
                "record_bytes": int(summary["record_bytes"]),
                "unique_pure_vids": int(summary["unique_pure_vids"]),
                "unique_original_vids": int(summary.get("unique_original_vids", 0)),
                "head_records": int(summary["head_records"]),
                "extra_supports": int(summary["extra_supports"]),
                "extra_h_postings": int(summary.get("extra_h_postings", 0)),
                "unused_extra_supports": int(summary.get("unused_extra_supports", 0)),
                "support_file_bytes": int(summary["support_file_bytes"]),
                "source_capped_tags": int(summary.get("source_capped_tags", 0)),
                "below_required_tags": int(summary["below_required_tags"]),
                "below_floor_tags": int(summary["below_floor_tags"]),
                "max_expanded_pure_pages": int(summary["max_expanded_pure_pages"]),
                "original_membership_fingerprint": summary["original_membership_fingerprint"],
                "h1_source_vid_sha256": h1_hash,
                "extreme_sparse_dedicated_route_compiled": False,
                "low_count_tags": len(low_count_rows),
                "low_count_mean_required_heads": avg("required_heads"),
                "low_count_mean_own_heads": avg("own_heads"),
                "low_count_mean_support_heads": avg("support_heads"),
                "low_count_mean_effective_heads": avg("effective_heads"),
                "low_count_mean_extra_support_heads": avg("extra_support_heads"),
                "h_replicas_per_nonhead": int(summary["pure_records"]) /
                    (int(summary["documents"]) - int(summary["heads"])),
                "build_elapsed_seconds": build_meta.get("launcher", {}).get(
                    "timing", {}).get("elapsed_seconds"),
                "build_peak_rss_mib": (
                    build_meta["launcher"]["timing"]["max_rss_kb"] / 1024
                    if "launcher" in build_meta else None
                ),
            }
        )

    ensure(merged_tag_rows, "merged audit tag rows are empty")
    ensure(audit_summary_rows, "merged audit summary rows are empty")
    write_csv(final_dir / "audit_tags.csv", merged_tag_rows)
    write_csv(final_dir / "audit_summary.csv", audit_summary_rows)
    write_csv(final_dir / "structure_summary.csv", structure_summary)

    if baseline_summary is not None:
        baseline_payload = int(baseline_summary["payload_bytes"])
        baseline_h_records = int(baseline_summary["pure_records"])
        baseline_support_file = int(baseline_summary["support_file_bytes"])
        for row in structure_summary:
            row["payload_bytes_delta_vs_legacy"] = int(row["payload_bytes"]) - baseline_payload
            row["h_records_delta_vs_legacy"] = int(row["pure_records"]) - baseline_h_records
            row["support_file_bytes_delta_vs_legacy"] = int(row["support_file_bytes"]) - baseline_support_file
    else:
        for row in structure_summary:
            row["payload_bytes_delta_vs_legacy"] = ""
            row["h_records_delta_vs_legacy"] = ""
            row["support_file_bytes_delta_vs_legacy"] = ""
    for row in structure_summary:
        explicit_unused = row.get("unused_extra_supports", "")
        if explicit_unused == "" or explicit_unused is None:
            row["unused_extra_supports"] = max(
                0,
                int(row["extra_supports"]) - int(row["extra_h_postings"]),
            )
        row["unique_vid_delta_vs_original"] = int(row["unique_pure_vids"]) - int(row["unique_original_vids"])
    write_csv(final_dir / "structure_summary.csv", structure_summary)

    h1_hashes = set(attribution["h1_source_vid_sha256"].values())
    fingerprints = set(attribution["original_membership_fingerprint"].values())
    attribution["consistent_h1_source_vids"] = len(h1_hashes) == 1
    attribution["consistent_original_membership_fingerprint"] = len(fingerprints) == 1
    if not attribution["consistent_h1_source_vids"]:
        attribution["notes"].append("H1 source VID hashes differ across variants; stop geometry attribution.")
    if not attribution["consistent_original_membership_fingerprint"]:
        attribution["notes"].append("Original membership fingerprints differ across variants; stop O-source attribution.")
    if manifest.get("profile") == CONTROL_PROFILE:
        attribution["notes"].append(
            "Current201 remains an independent-rebuild no-overflow control unless both H-source hashes and original-membership fingerprints match exactly."
        )
        attribution["notes"].append(
            "Current201 E=0 does not imply a no-op: unified fallback / RNG replica behavior can still change scan volume, dedup, pages, recall, and QPS."
        )

    if manifest["profile"] == MAIN_PROFILE:
        for variant_name, variant_meta in manifest["variants"].items():
            if variant_meta["enable_support_expansion"]:
                if int(attribution["variants"][variant_name]["extra_supports"]) <= 0:
                    attribution["notes"].append(
                        f"{variant_name} enabled support expansion but extra_supports <= 0."
                    )
                if int(attribution["variants"][variant_name]["extra_h_postings"]) <= 0:
                    attribution["notes"].append(
                        f"{variant_name} enabled support expansion but extra_h_postings <= 0."
                    )

    json_dump(final_dir / "attribution_checks.json", attribution)
    if attribution["notes"] and manifest["profile"] == MAIN_PROFILE:
        if (
            not attribution["consistent_h1_source_vids"]
            or not attribution["consistent_original_membership_fingerprint"]
        ):
            raise RuntimeError("attribution checks failed; see final/attribution_checks.json")

    if args.plot:
        plot_script = SCRIPT_DIR / "plot_limited_tag_support_expansion.R"
        output_prefix = final_dir / manifest["fixture_name"]
        subprocess.run(
            ["Rscript", str(plot_script), str(final_dir), str(output_prefix)],
            cwd=str(REPO_ROOT),
            check=True,
        )
    print(f"finalized run: {run_dir}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Generate the 8192-tag fixture and exact workloads")
    prepare.add_argument(
        "--fixture-root",
        type=Path,
        default=DEFAULT_MAIN_FIXTURE,
    )
    prepare.add_argument(
        "--reference-fixture",
        type=Path,
        default=DEFAULT_SOURCE_FIXTURE,
        help="Existing fixture that already owns the canonical SIFT source files.",
    )
    prepare.add_argument("--attribute-cardinality", type=int, default=8192)
    prepare.add_argument("--zipf-exponent", type=float, default=1.0)
    prepare.add_argument("--seed", type=int, default=20260817)
    prepare.add_argument("--numeric-seed", type=int, default=20260821)
    prepare.add_argument("--query-count", type=int, default=DEFAULT_QUERY_COUNT)
    prepare.add_argument("--topk", type=int, default=DEFAULT_TOPK)
    prepare.add_argument("--query-batch", type=int, default=DEFAULT_QUERY_BATCH)
    prepare.add_argument("--warmup-count", type=int, default=DEFAULT_WARMUP)
    prepare.add_argument("--measure-offset", type=int, default=DEFAULT_MEASURE_OFFSET)
    prepare.add_argument("--measured-query-count", type=int, default=DEFAULT_MEASURED_QUERIES)

    build = subparsers.add_parser("build", help="Emit run-specific INIs and build variants")
    build.add_argument("--fixture-root", type=Path, default=DEFAULT_MAIN_FIXTURE)
    build.add_argument("--profile", choices=[MAIN_PROFILE, CONTROL_PROFILE], default="")
    build.add_argument("--bench-root", type=Path)
    build.add_argument("--run-label", default="")
    build.add_argument("--run-dir", type=Path)
    build.add_argument("--variants", default="")
    build.add_argument("--workloads", default="")
    build.add_argument("--nprobes", default="16,32,64,128")
    build.add_argument("--cpu-affinity", default="")
    build.add_argument("--emit-only", action="store_true", help="Write run manifests/INIs only; do not invoke spannbuilder")

    audit = subparsers.add_parser("audit", help="Run spannsupportaudit for every built variant")
    audit.add_argument("--run-dir", type=Path, required=True)
    audit.add_argument("--cpu-affinity", default="")

    benchmark = subparsers.add_parser("benchmark", help="Run spannaclbench sweeps for a build run")
    benchmark.add_argument("--run-dir", type=Path, required=True)
    benchmark.add_argument("--variants", default="")
    benchmark.add_argument("--workloads", default="")
    benchmark.add_argument("--nprobes", default="")
    benchmark.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    benchmark.add_argument("--cpu-affinity", default="")

    finalize = subparsers.add_parser("finalize", help="Aggregate audit/benchmark outputs and optionally plot")
    finalize.add_argument("--run-dir", type=Path, required=True)
    finalize.add_argument("--plot", action="store_true")

    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "prepare":
        prepare_fixture(args)
    elif args.command == "build":
        build_variants(args)
    elif args.command == "audit":
        audit_run(args)
    elif args.command == "benchmark":
        benchmark_run(args)
    elif args.command == "finalize":
        finalize_run(args)
    else:
        raise RuntimeError(f"unsupported command {args.command}")


if __name__ == "__main__":
    main()
