#!/usr/bin/env python3
"""Fresh paired member-postfilter experiment, using the unchanged row-local entry gate."""
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

HERE = Path(__file__).resolve().parent
VARIANTS = ("graph", "row_min1", "all_members_min1", "all_members_min10")
COUNTERS = NAVIGATION_V2 + (
    "auxiliary_first_visits", "auxiliary_negative_first_visits", "auxiliary_visited_skips",
    "auxiliary_unvisited_negative_skips", "selected_h2_zero_eligible_rows",
    "selected_h2_zero_fresh_rows", "auxiliary_prefetch_lines")


def prepare(path):
    plan = read_ini(path)["Campaign"]
    config = read_ini(path)
    output = Path(plan["OutputDirectory"])
    output.mkdir(parents=True, exist_ok=True)
    require(not (output / "registration.json").exists(), "Already registered; resume instead")
    require(json.loads(Path(plan["PriorCompletion"]).read_text())["status"] == "completed",
            "Frozen NUMA2 timing must finish first")
    workload = json.loads(Path(plan["Workloads"]).read_text())
    controls = output / "configs"
    controls.mkdir()
    schedule = []
    for kind in ("functional_plain", "functional_diagnostic", "plain", "diagnostic"):
        for repetition in ((1, 2) if kind == "plain" else (1,)):
            for scenario in SCENARIOS if kind.startswith("functional") else ("medium_tag",):
                variants = ("all_members_min10",) if kind.startswith("functional") else VARIANTS
                if kind == "diagnostic":
                    variants = ("graph", "all_members_min1", "all_members_min10")
                for variant in variants if repetition == 1 else variants[::-1]:
                    case = f"{kind}_{scenario}_{variant}_r{repetition}"
                    native = configparser.ConfigParser(interpolation=None)
                    native.optionxform = str
                    native["SearchSSDIndex"] = dict(config["SearchSSDIndex"])
                    native["SearchSSDIndex"]["EnablePostingNavigation"] = str(variant != "graph").lower()
                    native["SearchSSDIndex"]["PostingMinCandidates"] = "10" if variant in ("graph", "all_members_min10") else "1"
                    probes = GRID if kind == "plain" else [24]
                    native["SearchSweep"] = dict(NProbe=json.dumps(probes))
                    predicate, predicate_file = "empty", ""
                    if scenario in workload["flat_query_tags"]:
                        predicate, predicate_file = "categorical", workload["flat_query_tags"][scenario]
                    elif scenario in ("numeric", "mixed_dnf"):
                        predicate = "dnf"
                        predicate_file = workload["query_dnf"]["numeric" if scenario == "numeric" else "mixed"]
                    count = 32 if kind.startswith("functional") else 1000
                    native["Benchmark"] = dict(Index=plan["Index"], Queries=plan["Queries"], Predicate=predicate,
                        PredicateFile=predicate_file, MaxQueries=str(count), Warmup=str(count))
                    filename = controls / f"{case}.ini"
                    with filename.open("w") as stream:
                        native.write(stream, space_around_delimiters=False)
                    schedule.append(dict(case=case, kind=kind, scenario=scenario, variant=variant,
                        repetition=repetition, config=str(filename), queries=count, probes=probes,
                        numa_node=0 if kind.startswith("functional") else 2))
    inputs = {path, Path(__file__).resolve(), HERE / "Bench.cpp", Path(workload["attributes"])}
    inputs.update(Path(plan[k]) for k in ("Binary", "DiagnosticBinary", "FrozenBinary", "Queries", "Workloads",
                  "PriorCompletion", "PriorResults", "PriorDiagnostic", "PriorFunctional"))
    inputs.update(p for p in Path(plan["Index"]).rglob("*") if p.is_file())
    inputs.update(controls.glob("*.ini"))
    inputs.update(Path(workload["truth"][s]["ids"]) for s in SCENARIOS)
    inputs.update(Path(p) for p in workload["flat_query_tags"].values())
    inputs.update(Path(p) for p in workload["query_dnf"].values())
    repo = HERE.parents[3]
    inputs.update(repo / p for p in ("AnnService/src/Core/BKT/BKTIndex.cpp",
        "AnnService/inc/Core/Common/NavigationVisited.h", "AnnService/inc/Core/Common/PostingNavigation.h",
        "AnnService/inc/Core/Common/GraphAccessStats.h", "AnnService/inc/Core/SPANN/PostingNavigation.h",
        "Test/NativePostingTest.cpp", "Test/PostingCollectionTest.cpp"))
    save(output / "registration.json", dict(query_count=1000, warmup=1000, repetitions=2, nprobe=GRID,
        scenarios=["medium_tag"], functional_scenarios=SCENARIOS, variants=VARIANTS, schedule=schedule,
        protected={str(p): sha(p) for p in sorted(inputs)}, diagnostic_schema_version=4,
        counter_columns=COUNTERS, entry_gate="Unchanged per-row physical1%; NO cumulative-density gate",
        row_min1="Frozen member-filter min1, freshly paired on the same NUMA2 cohort",
        other_scenarios="32-query functional only; NOT MEASURED for current-version QPS",
        source_scope="All fresh selected auxiliary H1 members are scored; min counts only fresh matches"))
    event(output, "prepared", normal_points=48, functional_processes=12, diagnostic_processes=3)


def run(path, stage):
    require(not [k for k in os.environ if k.startswith(("SPTAG_", "SPANN_", "OMP_")) or k == "LD_PRELOAD"],
            "Search environment overrides forbidden")
    plan = read_ini(path)["Campaign"]
    output = Path(plan["OutputDirectory"])
    registration = json.loads((output / "registration.json").read_text())
    workload = json.loads(Path(plan["Workloads"]).read_text())
    attrs = np.load(workload["attributes"], mmap_mode="r")
    masks = {s: np.ones(len(attrs), dtype=bool) if s == "unfilter" else
             predicate_mask(workload["predicates"][s], attrs) for s in SCENARIOS}
    old = json.loads(Path(plan["PriorResults"]).read_text())
    reference = {(p["variant"], p["nprobe"]): p["payload_hashes"] for p in old
                 if p["scenario"] == "medium_tag" and p["repetition"] == 1}
    for kind in ("functional_plain", "functional_diagnostic") if stage == "functional" else (stage,):
        diagnostic = kind.endswith("diagnostic")
        records, started = [], time.monotonic()
        for case in [c for c in registration["schedule"] if c["kind"] == kind]:
            folder = output / case["case"]
            validated = folder / "validated.json"
            if validated.exists():
                rows = json.loads(validated.read_text())
            else:
                require(not folder.exists(), f"Incomplete run retained: {folder}")
                binary = plan["FrozenBinary"] if case["variant"] == "row_min1" else \
                    plan["DiagnosticBinary" if diagnostic else "Binary"]
                command = ["numactl", "--cpunodebind=" + str(case["numa_node"]),
                    "--membind=" + str(case["numa_node"]), binary, case["config"]]
                event(output, "process_started", case=case["case"], native_log=str(folder / "native.log"))
                before = time.monotonic()
                peak = execute(command, folder)
                wall = time.monotonic() - before
                log = (folder / "native.log").read_text()
                require("numeric lanes=1" in log and "conservative unknown" not in log, "Routing metadata unavailable")
                rows = [json.loads(line) for line in log.splitlines() if line.startswith('{"mode":')]
                require([r["nprobe"] for r in rows] == case["probes"], "Incomplete native probe array")
                for row in rows:
                    version, columns = (2, 22) if case["variant"] == "row_min1" else (4, 29)
                    require(row["queries"] == case["queries"] and row["diagnostic"] == diagnostic and
                            row["navigation_schema_version"] == version and row["navigation_columns"] == columns,
                            "Unexpected native cohort/schema")
                    point = folder / f"nprobe_{row['nprobe']}"
                    row.update(quality(point, workload["truth"][case["scenario"]], masks[case["scenario"]], case["queries"]))
                    row.update(case)
                    row.update(command=command, peak_process_rss_bytes=peak, process_wall_seconds=wall)
                    if kind == "plain" and case["variant"] in ("graph", "row_min1"):
                        expected = reference["graph" if case["variant"] == "graph" else "min1", row["nprobe"]]
                        require(row["payload_hashes"] == expected, "Frozen baseline/row-min1 payload regression")
                    if diagnostic:
                        c = np.fromfile(point / "navigation.u64", dtype="<u8").reshape(case["queries"], 29)
                        require(np.all(c[:, 22] == c[:, 23] + c[:, 18]), "First visits do not equal negatives + fresh matches")
                        require(np.all(c[:, 22] + c[:, 24] == c[:, 8]), "Auxiliary member accounting mismatch")
                        require(not np.any(c[:, 25]), "Negative auxiliary members were skipped without visiting")
                        require(np.all(c[:, 26] <= c[:, 27]) and np.all(c[:, 27] <= c[:, 12]), "Invalid zero-row accounting")
                        require(np.all(c[:, 28] >= 8 * c[:, 22]) and np.all(c[:, 28] <= 9 * c[:, 22]),
                                "Float128 vector prefetch coverage mismatch")
                        require(np.all(c[:, 17] == c[:, 19] + c[:, 20]), "Activation accounting mismatch")
                        row["mean_navigation"] = dict(zip(COUNTERS, c.mean(0).tolist()))
                        row["max_navigation"] = dict(zip(COUNTERS, c.max(0).tolist()))
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
                            "Normal/diagnostic payload mismatch")
                elif row["repetition"] == 2:
                    first = next(p for p in records if p["variant"] == row["variant"] and
                                 p["nprobe"] == row["nprobe"] and p["repetition"] == 1)
                    require(row["payload_hashes"] == first["payload_hashes"], "Reversed repetition changed payload")
            records.extend(rows)
            save(output / f"{kind}-results.json", records)
            event(output, "process_complete", kind=kind, case=case["case"], points=len(records),
                  phase_wall_seconds=time.monotonic() - started)


def finish(path):
    plan = read_ini(path)["Campaign"]
    output = Path(plan["OutputDirectory"])
    reg = json.loads((output / "registration.json").read_text())
    plain = json.loads((output / "plain-results.json").read_text())
    diagnostic = json.loads((output / "diagnostic-results.json").read_text())
    functional = json.loads((output / "functional_diagnostic-results.json").read_text())
    require(len(plain) == 48 and len(diagnostic) == 3 and len(functional) == 6, "Incomplete matrix")
    points = []
    for variant in VARIANTS:
        for probe in GRID:
            rows = [p for p in plain if p["variant"] == variant and p["nprobe"] == probe]
            require(len(rows) == 2 and rows[0]["payload_hashes"] == rows[1]["payload_hashes"], "Invalid pair")
            points.append(dict(scenario="medium_tag", variant=variant, nprobe=probe, recall=rows[0]["recall"],
                qps=statistics.mean(p["qps"] for p in rows), qps_min=min(p["qps"] for p in rows),
                qps_max=max(p["qps"] for p in rows), underfilled_queries=rows[0]["underfilled_queries"]))
    previous = json.loads(Path(plan["PriorFunctional"]).read_text())
    controls = {}
    for row in functional:
        prior = next(p for p in previous if p["scenario"] == row["scenario"] and
                     p["variant"] == "posting" and p["repetition"] == 1)
        if row["scenario"] == "unfilter":
            require(row["payload_hashes"] == prior["payload_hashes"], "Unfiltered payload changed")
        controls[row["scenario"]] = dict(old_member_filter_recall=prior["recall"], all_members_recall=row["recall"],
            old_underfilled=prior["underfilled_queries"], new_underfilled=row["underfilled_queries"],
            navigation=row["mean_navigation"], scope="32 functional queries, not QPS")
    for source, expected in reg["protected"].items():
        require(sha(source) == expected, f"Protected bytes changed: {source}")
    save(output / "summary.json", dict(status="completed", points=points, normal_points=48, diagnostic_points=3,
        functional_controls=controls, medium_nprobe24_work={r["variant"]: r["mean_navigation"] for r in diagnostic},
        protected_bytes="unchanged", normal_diagnostic_payload_parity=True,
        entry_gate="row-local1%; cumulative-density gate excluded",
        caveats=["Whole selected rows may exceed remaining budget; negatives consume real budget",
                 "Functional controls are32 queries; other five scenarios NOT MEASURED for throughput",
                 "No universal or1B acceptance; no old/new timing splice"]))
    event(output, "completed", normal_points=48, diagnostic_points=3, functional_scenarios=6)


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
        root = Path(read_ini(args.ini)["Campaign"]["OutputDirectory"])
        if root.exists():
            event(root, "failed", stage=args.stage, error=str(error))
        raise
