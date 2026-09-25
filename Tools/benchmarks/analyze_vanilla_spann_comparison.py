#!/usr/bin/env python3
"""Summarize completed original/H3 native runs without changing their measurements."""

import argparse
import csv
import json
import math
from pathlib import Path
import shutil
import statistics

from official_benchmark_config import identity, require, write_json
from run_vanilla_spann_build import read_ini


def load_json(path):
    return json.loads(path.read_text())


def completed(path):
    require(load_json(path / "status.json")["state"] == "completed",
            f"Refuse an incomplete native comparison: {path}")


def resources(path):
    samples = []
    for file in path.glob("*.resources.jsonl"):
        samples.extend(json.loads(line) for line in file.open())
    require(samples, "Missing process resource observations")
    require(all(row["status"]["Cpus_allowed_list"] == "48-71" for row in samples),
            "Native process placement differs from NUMA node2")
    commands = [load_json(file)["command"] for file in path.glob("*.command.json")]
    require(commands and all("--cpunodebind=2" in command and "--membind=2" in command
                             for command in commands), "Missing NUMA binding command")
    io = [load_json(file) for file in path.glob("*.io.json")]
    require(io and all(row["direct_io"] for row in io), "Missing direct-IO evidence")
    return {
        "peak_rss_mib": max(row["memory_kb"]["Rss"] for row in samples) / 1024,
        "peak_anon_hugepages_mib": max(row["memory_kb"]["AnonHugePages"] for row in samples) / 1024,
        "storage_read_bytes_max_sample": max(row["io"]["read_bytes"] for row in samples),
        "processes_with_observed_direct_io": len(io),
        "allowed_memory_nodes": sorted({row["status"]["Mems_allowed_list"] for row in samples}),
        "notes": "RSS and read_bytes include loading/warmup; read_bytes is per-process. Mems_allowed_list is a cpuset allowance, not the numactl memory policy; successful commands carry --membind=2.",
    }


def latency_fields(plain, profile):
    require(len(plain) == len(profile) == 3, "Require three ordinary/profile pairs")
    require(len({row["recall"] for row in plain + profile}) == 1, "Recall changed between runs")
    require(all(0 <= row["recall"] <= 1 and
                math.isfinite(row["mean_ms"]) and row["mean_ms"] > 0 and
                math.isfinite(row["qps"]) and row["qps"] > 0 and
                math.isclose(row["qps"] * row["mean_ms"], 1000, rel_tol=1e-5)
                for row in plain + profile), "Invalid or inconsistently paired latency/QPS")
    values = [row["mean_ms"] for row in plain]
    qps = [row["qps"] for row in plain]
    return {
        "recall": plain[0]["recall"], "mean_ms": statistics.mean(values),
        "mean_ms_min": min(values), "mean_ms_max": max(values),
        "median_qps": statistics.median(qps), "qps_min": min(qps), "qps_max": max(qps),
        "profile_mean_ms": statistics.mean(row["mean_ms"] for row in profile),
    }


def vanilla_rows(path):
    completed(path)
    runs = load_json(path / "runs.json")
    ready, equivalent, done = [], [], []
    with (path / "native.stdout.log").open() as stream:
        for line in stream:
            for marker, collection in (("VANILLA_READY ", ready),
                                       ("VANILLA_EQUIVALENT ", equivalent), ("VANILLA_DONE ", done)):
                if line.startswith(marker):
                    collection.append(json.loads(line[len(marker):]))
    probes = sorted({row["nprobe"] for row in runs})
    require(len(ready) == len(done) == 1 and done[0]["all_comparisons_passed"],
            "Missing native completion/equivalence records")
    require({row["nprobe"] for row in equivalent} == set(probes) and
            all(row["exact_ids"] and row["exact_float_bits"] and row["profile_work_equal"]
                for row in equivalent), "Native ordinary/profile search equivalence failed")
    require(all(row["count"] == row["warmup"] == 1000 and row["query_offset"] == 0 and
                row["number_of_threads"] == 1 and row["max_check"] == 2048 and
                row["memory_max_check"] == 2048 and row["search_posting_page_limit"] == 15 and
                row["actual_direct_io"] for row in runs), "Unexpected native vanilla controls")
    rows = []
    for probe in probes:
        plain = [row for row in runs if row["nprobe"] == probe and row["mode"] == "plain"]
        profile = [row for row in runs if row["nprobe"] == probe and row["mode"] == "profile"]
        require(all(row["head_ms"] is None and row["posting_ms"] is None for row in plain),
                "Ordinary throughput unexpectedly contains phase instrumentation")
        rows.append({
            "engine": "vanilla", "nprobe": probe, **latency_fields(plain, profile),
            "head_ms": statistics.mean(row["head_ms"] for row in profile),
            "head_stage_ms": statistics.mean(row["head_ms"] for row in profile),
            "posting_ms": statistics.mean(row["posting_ms"] for row in profile),
            "posting_io_scan_ms": None,
            "head_reported_scanned": profile[0]["native_head_get_scanned_mean"],
            "posting_distance_computations": profile[0]["native_m_totalListElementsCount_mean"],
            "posting_requests": profile[0]["native_m_diskIOCount_mean"],
            "requested_pages": profile[0]["native_m_diskAccessCount_mean"],
            "requested_bytes": profile[0]["native_m_diskAccessCount_mean"] * 4096,
            "parent_beam": None, "source": str(path),
        })
    return rows, ready[0]


def h3_rows(path):
    completed(path)
    runs = load_json(path / "runs.json")
    summaries = load_json(path / "summary.json")
    rows = []
    for summary in summaries:
        probe = summary["nprobe"]
        search = read_ini(path / "config" / f"h3_plain_L{probe:04d}.ini")["SearchSSDIndex"]
        require(search.getint("InternalResultNum") == probe and
                search.getint("NumberOfThreads") == 1 and
                search.getint("SearchPostingPageLimit") == 15, "Unexpected saved H3 search controls")
        selected = [row for row in runs if row["nprobe"] == probe]
        require(summary["native_core_work_identical"], "H3 core search work changed")
        plain = [{**row["native"], "mean_ms": row["native"]["mean_latency_ms"]}
                 for row in selected if row["mode"] == "plain"]
        profile = [{**row["native"], "mean_ms": row["native"]["mean_latency_ms"]}
                   for row in selected if row["mode"] == "profile"]
        phases = summary["phases"]
        require(all(row["queries"] == 1000 and row["measure_offset"] == 0 and
                    row["failed_queries"] == 0 for row in plain + profile), "Unexpected H3 cohort")
        require(phases["h2MaxCheck"] == 512 and phases["h2Iter"] == 1,
                "Unexpected H3 navigation budget or retry")
        rows.append({
            "engine": "h3", "nprobe": probe, **latency_fields(plain, profile),
            "head_ms": phases["h2"], "head_stage_ms": phases["graphOther"],
            "posting_ms": phases["post"], "posting_io_scan_ms": phases["io"] + phases["scan"],
            "head_reported_scanned": phases["headScanned"],
            "posting_distance_computations": plain[0]["distance_computations_per_query"],
            "posting_requests": plain[0]["postings_per_query"],
            "requested_pages": plain[0]["posting_page_reads_per_query"],
            "requested_bytes": plain[0]["posting_physical_bytes_per_query"],
            "parent_beam": math.ceil(search.getfloat("HierarchyInitialProbeRatio") * probe),
            "source": str(path),
        })
    return rows


def run(config):
    config = config.resolve(strict=True)
    plan = read_ini(config)
    control = plan["Comparison"]
    vanilla = Path(control["Vanilla"])
    h3 = [Path(item.strip()) for item in control["H3"].split(",")]
    output = Path(control["OutputDirectory"])
    targets = ["curve.csv", "comparison.json", "analysis_provenance.json"]
    require(output.is_dir() and not any((output / name).exists() for name in targets),
            "Require a new analysis in the existing comparison root; never overwrite figures/results")
    rows, native_ready = vanilla_rows(vanilla)
    for path in h3:
        rows.extend(h3_rows(path))
    keyed = {(row["engine"], row["nprobe"]): row for row in rows}
    require(len(keyed) == len(rows), "Duplicate measured engine/nprobe")
    pairs = {}
    for name in plan.sections():
        if name == "Comparison":
            continue
        before = keyed["vanilla", plan[name].getint("VanillaNprobe")]
        after = keyed["h3", plan[name].getint("H3Nprobe")]
        delta = after["recall"] - before["recall"]
        require(abs(delta) <= control.getfloat("MaximumPairRecallDifference"),
                f"Pair does not meet its declared recall tolerance: {name}")
        pairs[name] = {
            "vanilla": before, "h3": after, "recall_delta_percentage_points": 100 * delta,
            "h3_over_vanilla": {
                key: after[key] / before[key] for key in
                ("mean_ms", "median_qps", "head_ms", "posting_ms", "posting_requests",
                 "posting_distance_computations", "requested_bytes")
            },
        }
    rows.sort(key=lambda row: (row["engine"], row["nprobe"]))
    report = {
        "scope": "Unfiltered SIFT1M, concrete original Microsoft SPANN versus preserved H3 indices",
        "protocol": {
            "query_count": 1000, "warmup_per_run": 1000, "measure_offset": 0, "repeats": 3,
            "query_threads": 1, "cpu_node": 2, "memory_node": 2, "direct_io": True,
            "search_page_cap": 15, "system_cache_or_thp_changes": False,
            "vanilla_head_maxcheck": 2048, "h3_top_graph_maxcheck": 512,
        },
        "pairs": pairs, "curve": rows, "vanilla_native_metadata": native_ready,
        "head_overlap": load_json(output / "head_overlap.json"),
        "resources": {str(path): resources(path) for path in [vanilla, *h3]},
        "timing_caveats": [
            "Total latency/QPS are ordinary uninstrumented measurements; head/posting phases come from separate paired runs.",
            "Vanilla times SearchIndex itself; frozen H3's outer timer also includes wrapper calls, counter collection, and returned-ID copies. Both exclude recall calculation and loading/warmup.",
            "H3 head_ms is the complete hierarchy core; head_stage_ms includes surrounding native head preparation.",
            "posting_ms includes native posting-stage setup/completion. H3 post is used, not just io+scan; vanilla exposes no separate device/scan timers.",
            "H3's profiled phase logging adds outer-timer overhead, so phase values need not sum to ordinary latency.",
            "Original build12 is natively uplifted to15 by the default118-vector floor; preserved H3 build16 and 524-byte records differ from original516-byte records.",
            "Different representative sets, posting memberships, layouts, graph builds, and binaries mean this is not a pure structural ablation.",
            "Head counters are native logical work counters with engine-specific definitions, not hardware memory-access or cache-miss counts.",
        ],
    }
    with (output / "curve.csv").open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    write_json(output / "comparison.json", report)
    shutil.copy2(Path(__file__), output / Path(__file__).name)
    shutil.copy2(config, output / "analysis.ini")
    write_json(output / "analysis_provenance.json", {
        "script": identity(Path(__file__), True), "config": identity(config, True),
        "inputs": [identity(path / "runs.json", True) for path in [vanilla, *h3]],
        "native_logs_changed": False,
    })
    for name, pair in pairs.items():
        print(name, json.dumps({key: pair[key] for key in
                                ("recall_delta_percentage_points", "h3_over_vanilla")}))
    print(output / "comparison.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    run(parser.parse_args().config)
