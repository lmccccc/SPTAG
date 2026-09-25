#!/usr/bin/env python3
"""Full-cohort native MAIN graph/posting comparison; no replacement search code."""
import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import statistics
import struct
import subprocess
import tarfile
import time

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PAYLOADS = ("ids.i32", "dist.f32", "work.u64")
NAVIGATION = (
    "h1_distances", "graph_rows", "visited_checks", "tree_visits",
    "upper_distances", "signature_checks", "owner_references", "upper_members",
    "auxiliary_members", "head_predicate_calls", "posting_states", "state_initialized_bytes",
    "selected_posting_rows", "discovered_heap_pops", "cpp_allocations", "cpp_allocated_bytes",
    "native_hash_cleared_bytes")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def read_ini(path):
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    require(config.read(path), f"Missing INI: {path}")
    return config


def predicate_mask(predicate, attrs):
    if "categorical_eq" in predicate:
        column, value = predicate["categorical_eq"]
        return attrs[:, column] == value
    if "numeric_le" in predicate:
        column, value = predicate["numeric_le"]
        return attrs[:, column] <= value
    if "or" in predicate:
        return np.logical_or.reduce([predicate_mask(p, attrs) for p in predicate["or"]])
    if "and" in predicate:
        return np.logical_and.reduce([predicate_mask(p, attrs) for p in predicate["and"]])
    raise ValueError("Unsupported original workload predicate")


def prepare(path):
    config = read_ini(path)
    plan = config["Campaign"]
    output = Path(plan["OutputDirectory"])
    output.mkdir(parents=True, exist_ok=True)
    require(not (output / "registration.json").exists(), "Campaign already registered")
    workload = json.loads(Path(plan["Workloads"]).read_text())
    queries = np.load(plan["Queries"], mmap_mode="r")
    count = len(queries)
    require(count == workload["query_count"], "Manifest/query cohort mismatch")
    raw_queries = Path(workload["query_file"])
    with raw_queries.open("rb") as stream:
        dimension = struct.unpack("<i", stream.read(4))[0]
    require(raw_queries.stat().st_size == count * (dimension + 1) * 4,
            "Campaign must include the entire raw held-out query file")
    scenarios = plan["Scenarios"].split(",")
    require(set(scenarios) == set(workload["truth"]), "Must include every original scenario")
    grid = json.loads(config["SearchSweep"]["NProbe"])
    reference = read_ini(HERE.parent / "native_ratio_curve_20260917/experiment.ini")
    require(grid == [int(p) for p in reference["Experiment"]["grid"].split(",")],
            "Grid must match the established full native sweep")
    require(plan.getint("Repeats") == reference["Experiment"].getint("repeats"), "Repeat protocol changed")
    controls = output / "configs"
    controls.mkdir()
    protected = {str(Path(plan["Queries"])): sha(plan["Queries"]),
                 str(Path(plan["Workloads"])): sha(plan["Workloads"]),
                 workload["attributes"]: sha(workload["attributes"]),
                 str(raw_queries): sha(raw_queries),
                 workload["base_file"]: sha(workload["base_file"])}
    schedule = []
    for scenario in scenarios:
        truth = workload["truth"][scenario]
        require(sha(truth["ids"]) == truth["sha256"], f"Truth changed: {scenario}")
        require(np.load(truth["ids"], mmap_mode="r").shape[0] == count, "Partial truth cohort")
        protected[truth["ids"]] = sha(truth["ids"])
        predicate, predicate_file = "empty", ""
        if scenario in workload["flat_query_tags"]:
            predicate, predicate_file = "categorical", workload["flat_query_tags"][scenario]
        elif scenario in ("numeric", "mixed_dnf"):
            predicate = "dnf"
            predicate_file = workload["query_dnf"]["numeric" if scenario == "numeric" else "mixed"]
        if predicate_file:
            require(len(np.load(predicate_file, mmap_mode="r")) == count, "Partial predicate cohort")
            protected[predicate_file] = sha(predicate_file)
        for repetition in range(1, plan.getint("Repeats") + 1):
            order = ("graph", "posting") if repetition % 2 else ("posting", "graph")
            for mode in order:
                case = f"{scenario}_{mode}_r{repetition}"
                native = read_ini(path)
                native.remove_section("Campaign")
                native["Benchmark"] = {
                    "Index": plan["Index"], "Queries": plan["Queries"],
                    "Predicate": predicate, "PredicateFile": predicate_file,
                    "MaxQueries": "all", "Warmup": "all"}
                native["SearchSSDIndex"]["EnablePostingNavigation"] = str(mode == "posting").lower()
                native["SearchSweep"]["NProbe"] = json.dumps(grid if repetition % 2 else grid[::-1])
                filename = controls / f"{case}.ini"
                with filename.open("w") as stream:
                    native.write(stream, space_around_delimiters=False)
                schedule.append({"case": case, "scenario": scenario, "mode": mode,
                                 "repetition": repetition, "config": str(filename)})
    for directory in ("AnnService", "Wrappers"):
        for source in (ROOT / directory).rglob("*"):
            if source.is_file() and source.suffix in (".h", ".cpp", ".i"):
                protected[str(source)] = sha(source)
    for source in [ROOT / "CMakeLists.txt", ROOT / "AnnService/CMakeLists.txt",
                   ROOT / "Wrappers/CMakeLists.txt", *HERE.glob("*.cpp"), *HERE.glob("*.py"),
                   *controls.glob("*.ini"), path]:
        protected[str(source)] = sha(source)
    historical = output.parent / "main_postfilter_integration_20260919"
    for name in ("release_protection.json", "verified-dataset-protection.json"):
        for source, expected in json.loads((historical / name).read_text()).items():
            require(sha(source) == expected, f"Protected historical file changed: {source}")
            protected[source] = expected
    registration = {
        "full_cohort": count, "cohort_scope": "entire raw held-out SIFT query file; same six predicates with newly generated exact truth",
        "grid": grid, "repeats": plan.getint("Repeats"), "scenarios": scenarios,
        "cpu_node": plan.getint("CPUNode"), "memory_node": plan.getint("MemoryNode"),
        "same_build_baseline": "MAIN H1 result-only postfilter, auxiliary disabled",
        "timing": "whole native query including predicates/signatures/state; per-query clocks in both modes; no diagnostics",
        "diagnostics": "separate compiled counter build, all cohort rows at each grid point, never used for QPS",
        "io": "same buffered_view snapshot for both modes; no direct/buffered timing splice",
        "schedule": schedule, "protected": protected,
        "counter_columns": NAVIGATION,
        "cpp_allocation_scope": "ordinary C++ new/new[] requests inside query only, not all malloc/aligned IO allocations",
        "state_initialized_bytes_scope": "explicit new posting State values only; not allocator internals",
        "acceptance": "strict per-scenario joint recall/throughput/underfill; show regressions and non-overlapping recall ranges; no interpolated QPS"}
    write(output / "registration.json", registration)
    with tarfile.open(output / "main-source.tar.gz", "w:gz") as archive:
        for source in protected:
            source = Path(source)
            if source.is_relative_to(ROOT):
                archive.add(source, arcname=str(source.relative_to(ROOT)), recursive=False)
    print(json.dumps({k: registration[k] for k in ("full_cohort", "grid", "repeats", "scenarios")}))


def quality(directory, workload, mask, count):
    ids = np.fromfile(directory / "ids.i32", dtype="<i4").reshape(count, 10)
    distances = np.fromfile(directory / "dist.f32", dtype="<f4").reshape(count, 10)
    truth = np.load(workload["ids"], mmap_mode="r")
    valid = ids >= 0
    require(np.all(ids[valid] < len(mask)) and np.all(mask[ids[valid]]), "Exact filter violation")
    require(np.all(np.isfinite(distances[valid])) and np.all(distances[valid] >= 0), "Invalid L2 distance")
    hits = 0
    for i, row in enumerate(ids):
        results = row[row >= 0]
        require(len(set(results)) == len(results), "Duplicate final IDs")
        hits += len(set(results) & set(truth[i, :10]))
    work = np.fromfile(directory / "work.u64", dtype="<u8").reshape(count, 8)
    return {"recall": hits / (count * 10), "underfilled_queries": int(np.count_nonzero(valid.sum(axis=1) < 10)),
            "empty_queries": int(np.count_nonzero(valid.sum(axis=1) == 0)),
            "mean_returned": float(valid.sum(axis=1).mean()), "mean_ssd_work": work.mean(axis=0).tolist(),
            "payload_hashes": {name: sha(directory / name) for name in PAYLOADS}}


def execute(command, folder):
    folder.mkdir()
    peak = 0
    with (folder / "native.log").open("w") as log:
        process = subprocess.Popen(command, cwd=folder, stdout=log, stderr=subprocess.STDOUT)
        while process.poll() is None:
            try:
                status = Path(f"/proc/{process.pid}/status").read_text()
                for line in status.splitlines():
                    if line.startswith(("VmHWM:", "VmRSS:")):
                        peak = max(peak, int(line.split()[1]) * 1024)
            except FileNotFoundError:
                pass
            time.sleep(.2)
        require(process.returncode == 0, f"Native process failed: {folder}")
    return peak


def run(path, diagnostic=False):
    require(not [key for key in os.environ if key.startswith(("SPTAG_", "SPANN_", "OMP_")) or key == "LD_PRELOAD"],
            "Search environment overrides are forbidden")
    config = read_ini(path)
    plan = config["Campaign"]
    output = Path(plan["OutputDirectory"])
    registration = json.loads((output / "registration.json").read_text())
    for source, expected in registration["protected"].items():
        require(sha(source) == expected, f"Registered input/source changed: {source}")
    workloads = json.loads(Path(plan["Workloads"]).read_text())
    attrs = np.load(workloads["attributes"], mmap_mode="r")
    masks = {s: np.ones(len(attrs), dtype=bool) if s == "unfilter" else
             predicate_mask(workloads["predicates"][s], attrs) for s in registration["scenarios"]}
    binary = Path(plan["DiagnosticBinary" if diagnostic else "Binary"])
    require(binary.is_file(), f"Missing main build: {binary}")
    kind = "diagnostic" if diagnostic else "plain"
    write(output / f"{kind}-binary.json", {"path": str(binary), "sha256": sha(binary)})
    records = []
    for case in registration["schedule"]:
        if diagnostic and case["repetition"] != 1:
            continue
        folder = output / f"{kind}_{case['case']}"
        command = ["numactl", f"--cpunodebind={registration['cpu_node']}",
                   f"--membind={registration['memory_node']}", str(binary), case["config"]]
        if not (folder / "validated.json").exists():
            require(not folder.exists(), f"Incomplete run retained; inspect before retry: {folder}")
            peak = execute(command, folder)
            rows = [json.loads(line) for line in (folder / "native.log").read_text().splitlines()
                    if line.startswith('{"mode":')]
            require(len(rows) == len(registration["grid"]), "Incomplete native grid")
            for row in rows:
                require(row["diagnostic"] == diagnostic and row["queries"] == registration["full_cohort"],
                        "Wrong counter mode or query window")
                row.update(quality(folder / f"nprobe_{row['nprobe']}", workloads["truth"][case["scenario"]],
                                   masks[case["scenario"]], registration["full_cohort"]))
                row.update(case)
                row["peak_process_rss_bytes"] = peak
                if diagnostic:
                    counters = np.fromfile(folder / f"nprobe_{row['nprobe']}/navigation.u64",
                                           dtype="<u8").reshape(registration["full_cohort"], len(NAVIGATION))
                    row["mean_navigation"] = dict(zip(NAVIGATION, counters.mean(axis=0).tolist()))
                    row["max_navigation"] = dict(zip(NAVIGATION, counters.max(axis=0).tolist()))
                else:
                    row["command"] = command
            write(folder / "validated.json", rows)
        rows = json.loads((folder / "validated.json").read_text())
        records.extend(rows)
        write(output / f"{kind}-results.json", records)
        print(json.dumps({"completed": case["case"], "kind": kind, "points": len(records)}), flush=True)
    require(sha(binary) == json.loads((output / f"{kind}-binary.json").read_text())["sha256"],
            "Binary changed during campaign")
    for source, expected in registration["protected"].items():
        require(sha(source) == expected, f"Input changed during campaign: {source}")


def summarize(path):
    output = Path(read_ini(path)["Campaign"]["OutputDirectory"])
    registration = json.loads((output / "registration.json").read_text())
    plain = json.loads((output / "plain-results.json").read_text())
    diagnostic = json.loads((output / "diagnostic-results.json").read_text())
    require(len(plain) == len(registration["scenarios"]) * len(registration["grid"]) * 2 * registration["repeats"],
            "Incomplete paired campaign")
    grouped = {}
    for row in plain:
        key = row["scenario"], row["mode"], row["nprobe"]
        grouped.setdefault(key, []).append(row)
    points = {}
    for key, rows in grouped.items():
        require(len(rows) == registration["repeats"], "Missing reversed pair")
        require(all(row["payload_hashes"] == rows[0]["payload_hashes"] for row in rows), "Replay order changed results")
        profile = [r for r in diagnostic if (r["scenario"], r["mode"], r["nprobe"]) == key]
        require(len(profile) == 1 and profile[0]["payload_hashes"] == rows[0]["payload_hashes"],
                "Diagnostic changes IDs/distances/work")
        points[key] = {**rows[0], "qps": statistics.median(r["qps"] for r in rows),
                       "qps_repeats": [r["qps"] for r in rows],
                       "p50_us": statistics.median(r["p50_us"] for r in rows),
                       "p95_us": statistics.median(r["p95_us"] for r in rows),
                       "p99_us": statistics.median(r["p99_us"] for r in rows),
                       "mean_navigation": profile[0]["mean_navigation"],
                       "max_navigation": profile[0]["max_navigation"]}
    report = {}
    for scenario in registration["scenarios"]:
        graph = [points[scenario, "graph", p] for p in registration["grid"]]
        posting = [points[scenario, "posting", p] for p in registration["grid"]]
        same_budget = []
        threshold_matches = []
        overlap = [max(min(p["recall"] for p in graph), min(p["recall"] for p in posting)),
                   min(max(p["recall"] for p in graph), max(p["recall"] for p in posting))]
        for baseline, candidate in zip(graph, posting):
            same_budget.append({"nprobe": baseline["nprobe"], "graph_recall": baseline["recall"],
                "posting_recall": candidate["recall"], "qps_ratio": candidate["qps"] / baseline["qps"],
                "graph_underfill": baseline["underfilled_queries"], "posting_underfill": candidate["underfilled_queries"],
                "joint_noninferior": candidate["recall"] >= baseline["recall"] and candidate["qps"] >= baseline["qps"]
                    and candidate["underfilled_queries"] <= baseline["underfilled_queries"]})
            target = baseline["recall"]
            choices = [p for p in posting if overlap[0] <= target <= overlap[1] and p["recall"] >= target
                       and p["underfilled_queries"] <= baseline["underfilled_queries"]]
            best = max(choices, key=lambda p: p["qps"]) if choices else None
            threshold_matches.append({"graph_nprobe": baseline["nprobe"], "target_recall": target,
                "posting_nprobe": best["nprobe"] if best else None,
                "actual_posting_recall": best["recall"] if best else None,
                "qps_ratio": best["qps"] / baseline["qps"] if best else None,
                "exact_recall_match": best["recall"] == target if best else False,
                "comparison": "measured recall-at-least threshold, no interpolation" if best else "not comparable within measured recall overlap"})
        report[scenario] = {"graph": graph, "posting": posting, "same_budget": same_budget,
            "recall_overlap": overlap if overlap[0] <= overlap[1] else None,
            "threshold_matches": threshold_matches,
            "all_same_budget_points_noninferior": all(r["joint_noninferior"] for r in same_budget)}
    write(output / "summary.json", {"scenarios": report,
        "all_scenarios_noninferior": all(v["all_same_budget_points_noninferior"] for v in report.values()),
        "uncertainty": "two reversed repetitions, not a statistical equivalence proof; no 1B recall/QPS prediction"})
    print(json.dumps({s: {"all_same_budget_points_noninferior": r["all_same_budget_points_noninferior"],
                         "qps_ratio_range": [min(x["qps_ratio"] for x in r["same_budget"]),
                                             max(x["qps_ratio"] for x in r["same_budget"])],
                         "recall_overlap": r["recall_overlap"]} for s, r in report.items()}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--stage", choices=("prepare", "plain", "diagnostic", "summarize"), required=True)
    arguments = parser.parse_args()
    config = arguments.config.resolve()
    if arguments.stage == "prepare":
        prepare(config)
    elif arguments.stage == "summarize":
        summarize(config)
    else:
        run(config, arguments.stage == "diagnostic")
