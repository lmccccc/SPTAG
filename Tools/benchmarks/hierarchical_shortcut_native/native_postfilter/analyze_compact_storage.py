#!/usr/bin/env python3
"""Measured compact payload/capacity accounting and paired timing, not a plotter."""
import json
from pathlib import Path
import re
import struct

from run_full import require, sha
from run_postgraph import save
from run_compact_storage import DATA, OUTPUT, VARIANTS


def dependencies(root):
    files = {}
    seen_directories = set()

    def visit(folder):
        resolved = folder.resolve()
        if resolved in seen_directories:
            return
        seen_directories.add(resolved)
        for path in folder.iterdir():
            if path.is_dir():
                visit(path)
            elif path.is_file():
                stat = path.stat()
                files[(stat.st_dev, stat.st_ino)] = stat.st_size
            else:
                raise RuntimeError(f"Unexpected index artifact: {path}")
    visit(root)
    return sum(files.values()), len(files)


def main():
    original = DATA / "toolchains/matched_baseline_20260917/buffered_view/tenant_0"
    compact = OUTPUT / "index_streaming/tenant_0"
    conversion = json.loads((compact / "compact-conversion.json").read_text())
    old_meta = original / "HeadIndex/head_node_meta.bin"
    new_meta = compact / "HeadIndex/head_node_meta.bin"
    with old_meta.open("rb") as old, new_meta.open("rb") as new:
        old_header, new_header = old.read(64), new.read(68)
        old_version, count, numeric, old_stride = struct.unpack_from("<4i", old_header)
        new_version, new_count, new_numeric, new_stride = struct.unpack_from("<4i", new_header)
        schema_length = struct.unpack_from("<I", new_header, 64)[0]
        schema = new.read(schema_length).decode()
        require((old_version, new_version, old_stride, new_stride, schema) ==
                (8, 9, 320, 176, "categorical,numeric"), "Unexpected measured SIFT layout")
        require(count == new_count and numeric == new_numeric == 1, "Metadata shape differs")
        require(old_header[16:56] == new_header[16:56], "Widths/regions/domain/generation changed")
        for head in range(count):
            a, b = old.read(old_stride), new.read(new_stride)
            require(len(a) == 320 and len(b) == 176, "Truncated metadata row")
            require(a[:72] == b[:72] and a[88:120] == b[72:104] and
                    a[248:255] == b[104:111] and a[256:320] == b[112:176],
                    f"Head logical metadata changed at {head}")
        require(not old.read(1) and not new.read(1), "Trailing metadata bytes")
    require(new_meta.stat().st_size == 68 + schema_length + count * new_stride,
            "Compact persisted stride formula differs from actual size")
    upper_count = 0
    old_vectors = new_vectors = 0
    for name in ("SPTAGSecondLevelHeadVectors.bin", "SPTAGSecondLevelHeadVectors.bin.level2"):
        with (compact / name).open("rb") as stream:
            magic, version, heads, dimension, value_type, rows = struct.unpack("<6I", stream.read(24))
        require(magic == 0x39435648 and version == 1 and heads == count and dimension == 128,
                "Unexpected canonical map header")
        require((compact / name).stat().st_size == 32 + 4 * rows, "Canonical map length differs")
        upper_count += rows
        old_vectors += (original / name).stat().st_size
        new_vectors += (compact / name).stat().st_size
    logs = [p.read_text() for p in sorted((OUTPUT / "loader_measurements").glob("compact_*.log"))]
    require(logs, "Missing actual compact native loader accounting")
    pattern = r"upper shared payload=(\d+) bytes, upper allocated capacity=(\d+) bytes"
    signature = {tuple(map(int, match)) for log in logs for match in re.findall(pattern, log)}
    maps = {tuple(map(int, match)) for log in logs for match in re.findall(
        r"map payload=(\d+) capacity=(\d+) bytes, owned vectors=0 bytes", log)}
    require(len(signature) == 1 and len(maps) == 1, "Inconsistent actual allocation accounting")
    sig_payload, sig_capacity = signature.pop()
    map_payload, map_capacity = maps.pop()
    require(map_payload == map_capacity == upper_count * 4, "Hidden canonical map capacity")
    require(sig_payload == sig_capacity == upper_count * 96, "Unexpected retained upper signature payload")
    loader = []
    for path in sorted((OUTPUT / "loader_measurements").glob("*.log")):
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.startswith('{"scope":')]
        require(len(rows) == 1, "Missing native loader measurement")
        loader.append(dict(case=path.stem, **rows[0]))
    records = json.loads((OUTPUT / "plain-results.json").read_text())
    require(len(records) == 144, "Incomplete normal campaign")
    indexed = {(r["scenario"], r["variant"], r["nprobe"], r["repetition"]): r for r in records}
    timing = []
    for scenario, variant, probe, repetition in indexed:
        if repetition != 1 or variant != VARIANTS[0]:
            continue
        original_pair = [indexed[(scenario, VARIANTS[0], probe, r)]["qps"] for r in (1, 2)]
        compact_pair = [indexed[(scenario, VARIANTS[1], probe, r)]["qps"] for r in (1, 2)]
        timing.append(dict(scenario=scenario, nprobe=probe,
            mean_qps_ratio=sum(compact_pair) / sum(original_pair),
            paired_qps_ratios=[b / a for a, b in zip(original_pair, compact_pair)],
            compact_lower_in_both_pairs=all(b < a for a, b in zip(original_pair, compact_pair)),
            compact_range_below_original=max(compact_pair) < min(original_pair),
            original_qps=original_pair, compact_qps=compact_pair))
    for filename in ("protected-file-hashes.json", "source-geometry-hashes.json"):
        for path, entry in json.loads((OUTPUT / filename).read_text()).items():
            expected = entry if isinstance(entry, str) else entry["sha256"]
            require(sha(path) == expected, f"Protected artifact changed: {path}")
    original_dependencies, original_files = dependencies(original)
    compact_dependencies, compact_files = dependencies(compact)
    new_owned = sum(p.stat().st_size for p in compact.rglob("*") if p.is_file() and not p.is_symlink())
    save(OUTPUT / "compact-layout.json", dict(
        schema=schema, numeric_granularity_bits=256, categorical_granularity_bits=256,
        head_count=count, upper_count=upper_count, old_stride=old_stride, new_stride=new_stride,
        head_logical_record_parity=count,
        components={
            "h1_metadata": dict(old_payload=count * old_stride, new_payload=count * new_stride,
                old_persisted=old_meta.stat().st_size, new_persisted=new_meta.stat().st_size,
                new_capacity=count * new_stride),
            "upper_vectors": dict(old_owned_payload=upper_count * 512, new_owned_payload=0,
                new_direct_map_payload=map_payload, new_direct_map_capacity=map_capacity,
                old_persisted=old_vectors, new_persisted=new_vectors),
            "upper_routing_signatures": dict(old_owned_payload=upper_count * 128,
                new_owned_payload=sig_payload, new_owned_capacity=sig_capacity,
                borrowed_csr_payload=upper_count * 32, persisted_csr_unchanged=True),
            "h1_vectors_graph_tree_deletes_ssd_memberships": "unchanged, immutable referenced payloads",
            "sampling_ids": dict(persisted_u64_payload=upper_count * 8,
                load_time_only_u64_arrays=True, retained_direct_maps_counted_above=True)},
        changed_resident_payload_saving=count * (old_stride - new_stride) +
            upper_count * (512 - 4) + upper_count * 32,
        logical_persisted_dependencies=dict(original_bytes=original_dependencies,
            compact_bytes=compact_dependencies, original_unique_files=original_files,
            compact_unique_files=compact_files, newly_owned_output_bytes=new_owned,
            scope="Unique inode dependencies; omitted unused selection/upper-ANN artifacts are not RAM savings"),
        conversion=conversion, loader_measurements=loader,
        normal_timing=timing, diagnostic_parity=json.loads((OUTPUT / "parity.json").read_text()),
        acceptance=dict(all_configured_consumers="BLOCKED: pre-existing FileIOInterface::Merge API mismatch",
            timing="Raw paired ratios and separated ranges reported; no automatic regression waiver",
            one_billion_writes="NOT AUTHORIZED; STOP for parent SIFT1M acceptance"),
        caveats=[
            "176 retains semantically distinct H coarse and H column masks; 144 was a candidate, not assumed lossless.",
            "Loader measurements use the native core loader with saved INIs; normal client peaks include query work.",
            "Resident component payload/capacity is not total process RSS or allocator overhead.",
            "Source indexes remain on disk; reduced logical dependencies are not reclaimed source disk space.",
            "UInt8-8D fixture hit unchanged native distance kernel SIGILL; Float128/UInt8-128 fixtures passed."],
        conditional_1b={
            "baseline": "Current filtered V8 hierarchy, not bare unfiltered SPANN",
            "formula_bytes": "144*H1 + (dimension*value_bytes-4)*previously_owned_upper + 32*proven_equal_upper",
            "applicability": "Current 1cat+1numeric H/O schema only; no saving credited for already-shared vectors",
            "unfiltered_spann_increment": "Must additionally count all compact H1 filtering metadata, support, signatures and relations",
            "actual_1b_inventory": "Parent-owned; no 1B files modified"}))
    print(json.dumps(dict(points=len(records), heads=count, upper=upper_count,
        changed_resident_payload_saving=count * 144 + upper_count * 540,
        separated_slower_points=sum(x["compact_range_below_original"] for x in timing))))


if __name__ == "__main__":
    main()
