#!/usr/bin/env python3
"""Focused observed-density gate validation; frozen sweeps are read-only references."""
import argparse
import configparser
import json
import os
from pathlib import Path
import statistics
import time

import numpy as np
from run_full import execute, predicate_mask, quality, read_ini, require, sha
from run_posting_min_sweep import GRID, SCENARIOS, event, save
from run_posting_collection import NAVIGATION_V2

NAVIGATION_V3 = NAVIGATION_V2 + (
    "ordinary_degree", "ordinary_eligible", "local_sparse_rows", "density_suppressed_rows")
VARIANTS = ("graph", "min1", "min10", "oldmin1")
HERE = Path(__file__).resolve().parent


def prepare(path):
    config = read_ini(path)
    plan = config["Campaign"]
    output = Path(plan["OutputDirectory"])
    require(not (output / "registration.json").exists(), "Already registered")
    output.mkdir(parents=True, exist_ok=True)
    require(json.loads(Path(plan["PriorCompletion"]).read_text())["status"] == "completed",
            "Frozen NUMA2 sweep must finish first")
    workload = json.loads(Path(plan["Workloads"]).read_text())
    controls = output / "configs"
    controls.mkdir()
    schedule = []
    for kind in ("functional_plain", "functional_diagnostic", "plain", "diagnostic"):
        scenarios = SCENARIOS if kind.startswith("functional") else ("medium_tag",)
        repetitions = (1, 2) if kind == "plain" else (1,)
        for repetition in repetitions:
            for scenario in scenarios:
                variants = ("min10",) if kind.startswith("functional") else VARIANTS
                if kind == "diagnostic":
                    variants = VARIANTS[:-1]
                if repetition == 2:
                    variants = variants[::-1]
                for variant in variants:
                    case = f"{kind}_{scenario}_{variant}_r{repetition}"
                    native = configparser.ConfigParser(interpolation=None)
                    native.optionxform = str
                    native["SearchSSDIndex"] = dict(config["SearchSSDIndex"])
                    native["SearchSSDIndex"]["EnablePostingNavigation"] = str(variant != "graph").lower()
                    native["SearchSSDIndex"]["PostingMinCandidates"] = "10" if variant in ("graph", "min10") else "1"
                    grid = GRID if kind == "plain" else [24]
                    native["SearchSweep"] = dict(NProbe=json.dumps(grid))
                    predicate, predicate_file = "empty", ""
                    if scenario in workload["flat_query_tags"]:
                        predicate, predicate_file = "categorical", workload["flat_query_tags"][scenario]
                    elif scenario in ("numeric", "mixed_dnf"):
                        predicate = "dnf"
                        predicate_file = workload["query_dnf"]["numeric" if scenario == "numeric" else "mixed"]
                    count = 32 if kind.startswith("functional") else 1000
                    native["Benchmark"] = dict(Index=plan["Index"], Queries=plan["Queries"],
                        Predicate=predicate, PredicateFile=predicate_file, MaxQueries=str(count), Warmup=str(count))
                    filename = controls / f"{case}.ini"
                    with filename.open("w") as stream:
                        native.write(stream, space_around_delimiters=False)
                    schedule.append(dict(case=case, scenario=scenario, variant=variant, repetition=repetition,
                        kind=kind, config=str(filename), queries=count, probes=grid,
                        node=0 if kind.startswith("functional") else 2))
    protected = {Path(plan[k]) for k in ("Binary", "DiagnosticBinary", "FrozenBinary", "Queries",
                 "Workloads", "PriorCompletion", "PriorResults", "PriorDiagnostic")}
    protected.update(p for p in Path(plan["Index"]).rglob("*") if p.is_file())
    protected.update(controls.glob("*.ini"))
    protected.update((path, Path(__file__).resolve(), HERE / "Bench.cpp", Path(workload["attributes"])))
    for s in SCENARIOS:
        protected.add(Path(workload["truth"][s]["ids"]))
    protected.update(Path(p) for p in workload["flat_query_tags"].values())
    protected.update(Path(p) for p in workload["query_dnf"].values())
    root = HERE.parents[3]
    for name in ("AnnService/inc/Core/Common/PostingNavigation.h",
                 "AnnService/inc/Core/Common/GraphAccessStats.h", "AnnService/src/Core/BKT/BKTIndex.cpp",
                 "Test/PostingEvidenceTest.cpp", "Test/NativePostingTest.cpp"):
        protected.add(root / name)
    save(output / "registration.json", dict(query_count=1000, warmup=1000, repetitions=2,
        nprobe=GRID, scenarios=SCENARIOS, measured_scenarios=["medium_tag"], variants=VARIANTS,
        schedule=schedule, protected={str(p): sha(p) for p in sorted(protected)},
        diagnostic_schema_version=3, diagnostic_columns=NAVIGATION_V3,
        oldmin1="Frozen row-local gate binary freshly timed, not the current cumulative gate",
        scope="Medium-only timed correction; other five scenes only functional32, no current-version QPS"))
    event(output, "prepared", timed_processes=8, timed_points=48, functional_processes=12,
          medium_diagnostic_processes=3)


def run(path, stage):
    require(not [k for k in os.environ if k.startswith(("SPTAG_", "SPANN_", "OMP_")) or k == "LD_PRELOAD"],
            "Search environment overrides are forbidden")
    config = read_ini(path)
    plan = config["Campaign"]
    output = Path(plan["OutputDirectory"])
    reg = json.loads((output / "registration.json").read_text())
    workload = json.loads(Path(plan["Workloads"]).read_text())
    attrs = np.load(workload["attributes"], mmap_mode="r")
    masks = {s: np.ones(len(attrs), dtype=bool) if s == "unfilter" else
             predicate_mask(workload["predicates"][s], attrs) for s in SCENARIOS}
    old = json.loads(Path(plan["PriorResults"]).read_text())
    reference = {(r["variant"], r["nprobe"]): r["payload_hashes"] for r in old
                 if r["scenario"] == "medium_tag" and r["repetition"] == 1}
    kinds = ("functional_plain", "functional_diagnostic") if stage == "functional" else (stage,)
    for kind in kinds:
        diagnostic = kind.endswith("diagnostic")
        records = []
        started = time.monotonic()
        for case in [c for c in reg["schedule"] if c["kind"] == kind]:
            folder = output / case["case"]
            validated = folder / "validated.json"
            if validated.exists():
                rows = json.loads(validated.read_text())
            else:
                require(not folder.exists(), f"Incomplete process retained: {folder}")
                binary = plan["FrozenBinary"] if case["variant"] == "oldmin1" else \
                    plan["DiagnosticBinary" if diagnostic else "Binary"]
                command = ["numactl", "--cpunodebind=" + str(case["node"]), "--membind=" + str(case["node"]),
                           binary, case["config"]]
                event(output, "process_started", case=case["case"], native_log=str(folder / "native.log"))
                run_started = time.monotonic()
                peak = execute(command, folder)
                wall = time.monotonic() - run_started
                log = (folder / "native.log").read_text()
                require("numeric lanes=1" in log and "conservative unknown" not in log, "Invalid routing metadata")
                rows = [json.loads(line) for line in log.splitlines() if line.startswith('{"mode":')]
                require([r["nprobe"] for r in rows] == case["probes"], "Incomplete native probe array")
                for row in rows:
                    version, columns = (2, 22) if case["variant"] == "oldmin1" else (3, 26)
                    require(row["queries"] == case["queries"] and row["diagnostic"] == diagnostic and
                            row["navigation_schema_version"] == version and row["navigation_columns"] == columns,
                            "Unexpected counter version/cohort")
                    point = folder / f"nprobe_{row['nprobe']}"
                    row.update(quality(point, workload["truth"][case["scenario"]], masks[case["scenario"]], case["queries"]))
                    row.update(case)
                    row.update(process_wall_seconds=wall, peak_process_rss_bytes=peak, command=command)
                    if kind == "plain" and case["variant"] in ("graph", "oldmin1"):
                        baseline = "graph" if case["variant"] == "graph" else "min1"
                        require(row["payload_hashes"] == reference[baseline, row["nprobe"]], "Frozen payload regression")
                    if diagnostic:
                        counts = np.fromfile(point / "navigation.u64", dtype="<u8").reshape(case["queries"], 26)
                        row["mean_navigation"] = dict(zip(NAVIGATION_V3, counts.mean(0).tolist()))
                        row["max_navigation"] = dict(zip(NAVIGATION_V3, counts.max(0).tolist()))
                        require(np.all(counts[:, 23] <= counts[:, 22]) and
                                np.all(counts[:, 25] <= counts[:, 24]) and
                                np.all(counts[:, 17] <= counts[:, 24] - counts[:, 25]),
                                "Inconsistent observed-density accounting")
                        row["queries_with_activation"] = int(np.count_nonzero(counts[:, 17]))
                        row["queries_with_suppression"] = int(np.count_nonzero(counts[:, 25]))
                        row["pooled_observed_match_fraction"] = float(counts[:, 23].sum() / counts[:, 22].sum()) \
                            if counts[:, 22].sum() else None
                    if kind.startswith("functional"):
                        row["timing_accepted"] = False
                save(validated, rows)
            for row in rows:
                if diagnostic:
                    paired_kind = "functional_plain" if kind.startswith("functional") else "plain"
                    paired = json.loads((output / f"{paired_kind}-results.json").read_text())
                    matches = [p for p in paired if (p["scenario"], p["variant"], p["nprobe"]) ==
                               (row["scenario"], row["variant"], row["nprobe"])]
                    require(matches and all(p["payload_hashes"] == row["payload_hashes"] for p in matches),
                            "Diagnostic changes results or SSD work")
                elif row["repetition"] == 2:
                    first = next(p for p in records if p["variant"] == row["variant"] and
                                 p["nprobe"] == row["nprobe"] and p["repetition"] == 1)
                    require(first["payload_hashes"] == row["payload_hashes"], "Repetition changed payload")
            records.extend(rows)
            save(output / f"{kind}-results.json", records)
            event(output, "process_complete", kind=kind, case=case["case"], completed_points=len(records),
                  phase_wall_seconds=time.monotonic() - started)
        event(output, kind + "_complete", completed_points=len(records), wall_seconds=time.monotonic() - started)


def finish(path):
    plan = read_ini(path)["Campaign"]
    output = Path(plan["OutputDirectory"])
    reg = json.loads((output / "registration.json").read_text())
    plain = json.loads((output / "plain-results.json").read_text())
    diag = json.loads((output / "diagnostic-results.json").read_text())
    require(len(plain) == 48 and len(diag) == 3, "Incomplete Medium comparison")
    for kind in ("functional_plain", "functional_diagnostic"):
        require(len(json.loads((output / f"{kind}-results.json").read_text())) == 6, "Incomplete six-case functional check")
    points = []
    for variant in VARIANTS:
        for probe in GRID:
            rows = [r for r in plain if r["variant"] == variant and r["nprobe"] == probe]
            require(len(rows) == 2 and rows[0]["payload_hashes"] == rows[1]["payload_hashes"], "Pair mismatch")
            points.append(dict(scenario="medium_tag", variant=variant, nprobe=probe, recall=rows[0]["recall"],
                qps=statistics.mean(r["qps"] for r in rows), qps_min=min(r["qps"] for r in rows),
                qps_max=max(r["qps"] for r in rows), underfilled_queries=rows[0]["underfilled_queries"]))
    frontier = []
    for graph in [r for r in points if r["variant"] == "graph"]:
        for variant in VARIANTS[1:]:
            eligible = [p for p in points if p["variant"] == variant and p["recall"] >= graph["recall"]]
            candidate = max(eligible, key=lambda p: p["qps"]) if eligible else None
            frontier.append(dict(graph=graph, variant=variant, measured_recall_at_least=candidate,
                qps_ratio=candidate["qps"] / graph["qps"] if candidate else None))
    old = json.loads(Path(plan["PriorDiagnostic"]).read_text())
    work = {}
    for current in diag:
        variant = current["variant"]
        previous = next(r for r in old if r["scenario"] == "medium_tag" and r["variant"] == variant)
        work[variant] = dict(current=current["mean_navigation"], previous=previous["mean_navigation"],
            queries_with_activation=current["queries_with_activation"],
            queries_with_suppression=current["queries_with_suppression"],
            pooled_observed_match_fraction=current["pooled_observed_match_fraction"])
    for source, expected in reg["protected"].items():
        require(sha(source) == expected, f"Protected bytes changed: {source}")
    save(output / "summary.json", dict(status="completed", points=points, recall_at_least_frontier=frontier,
        medium_nprobe24_work=work, normal_points=48, diagnostic_points=3, functional_cases=6,
        protected_bytes="unchanged", normal_diagnostic_payload_parity=True,
        caveats=["Other scenes have only 32-query functional checks, no current-version timing",
                 "First empty ordinary row may still activate posting", "Not equal-recall interpolation or 1B acceptance"]))
    event(output, "completed", normal_points=48, medium_diagnostic_points=3, functional_cases=6)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ini", type=Path)
    parser.add_argument("--stage", choices=("prepare", "functional", "plain", "diagnostic", "finish"), required=True)
    args = parser.parse_args()
    try:
        if args.stage == "prepare":
            prepare(args.ini.resolve())
        elif args.stage == "finish":
            finish(args.ini.resolve())
        else:
            run(args.ini.resolve(), args.stage)
    except Exception as error:
        output = Path(read_ini(args.ini)["Campaign"]["OutputDirectory"])
        if output.exists():
            event(output, "failed", stage=args.stage, error=str(error))
        raise
