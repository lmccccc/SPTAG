#!/usr/bin/env python3
"""Single-load native nprobe sweeps using the frozen min10 trial executables."""
import argparse
import configparser
import json
import os
from pathlib import Path
import statistics
import subprocess
import time

import numpy as np
from run_full import execute, predicate_mask, quality, read_ini, require, sha, write
from run_posting_collection import NAVIGATION_V2

VARIANTS = ("graph", "min1", "min3", "min5", "min10")
GRID = [16, 24, 48, 96, 192, 384]
SCENARIOS = ("medium_tag", "extreme_tag", "mixed_dnf", "numeric", "broad_tag", "unfilter")


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    write(temporary, value)
    temporary.replace(path)


def event(output, phase, **details):
    record = dict(time_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  runner_pid=os.getpid(), phase=phase, **details)
    with (output / "events.jsonl").open("a") as stream:
        stream.write(json.dumps(record) + "\n")
    save(output / "status.json", record)
    print(json.dumps(record), flush=True)


def prepare(path):
    started = time.monotonic()
    config = read_ini(path)
    plan = config["Campaign"]
    output = Path(plan["OutputDirectory"])
    output.mkdir(parents=True, exist_ok=True)
    require(not (output / "registration.json").exists(), "Already registered; resume, do not prepare again")
    require(plan.getint("QueriesToMeasure") == 1000 and plan.getint("Warmup") == 1000 and
            plan.getint("Repeats") == 2, "Fixed bounded cohort/repetitions")
    require(plan["Scenarios"].split(",") == list(SCENARIOS) and
            json.loads(config["SearchSweep"]["NProbe"]) == GRID, "Unregistered scenario/probe order")
    workload = json.loads(Path(plan["Workloads"]).read_text())
    require(len(np.load(plan["Queries"], mmap_mode="r")) >= 1000, "Insufficient query cohort")
    controls = output / "configs"
    controls.mkdir()
    inputs = {path, Path(__file__).resolve(), Path(__file__).with_name("run_full.py"),
              Path(__file__).with_name("run_posting_collection.py"), Path(plan["Queries"]),
              Path(plan["Workloads"]), Path(workload["attributes"]),
              Path(plan["ReferenceResults"]), Path(plan["ReferenceManifest"])}
    for key in ("Binary", "DiagnosticBinary"):
        binary = Path(plan[key])
        inputs.add(binary)
        linked = subprocess.check_output(["ldd", str(binary)], text=True)
        (output / (key + "-ldd.txt")).write_text(linked)
        for line in linked.splitlines():
            for word in line.split():
                if word.startswith("/") and Path(word).is_file():
                    inputs.add(Path(word))
    inputs.update(p for p in Path(plan["Index"]).rglob("*") if p.is_file())
    schedule = []
    for repetition in (1, 2):
        for scenario in SCENARIOS if repetition == 1 else SCENARIOS[::-1]:
            predicate, predicate_file = "empty", ""
            if scenario in workload["flat_query_tags"]:
                predicate, predicate_file = "categorical", workload["flat_query_tags"][scenario]
            elif scenario in ("numeric", "mixed_dnf"):
                predicate = "dnf"
                predicate_file = workload["query_dnf"]["numeric" if scenario == "numeric" else "mixed"]
            inputs.add(Path(workload["truth"][scenario]["ids"]))
            if predicate_file:
                inputs.add(Path(predicate_file))
            for variant in VARIANTS if repetition == 1 else VARIANTS[::-1]:
                case = f"{scenario}_{variant}_r{repetition}"
                record = dict(case=case, scenario=scenario, variant=variant, repetition=repetition)
                for kind in ("plain", "diagnostic") if repetition == 1 else ("plain",):
                    native = configparser.ConfigParser(interpolation=None)
                    native.optionxform = str
                    native["SearchSSDIndex"] = dict(config["SearchSSDIndex"])
                    native["SearchSSDIndex"]["EnablePostingNavigation"] = str(variant != "graph").lower()
                    native["SearchSSDIndex"]["PostingMinCandidates"] = "10" if variant == "graph" else variant[3:]
                    native["SearchSweep"] = dict(NProbe=json.dumps(GRID if kind == "plain" else [24]))
                    native["Benchmark"] = dict(Index=plan["Index"], Queries=plan["Queries"],
                        Predicate=predicate, PredicateFile=predicate_file, MaxQueries="1000", Warmup="1000")
                    filename = controls / f"{kind}_{case}.ini"
                    with filename.open("w") as stream:
                        native.write(stream, space_around_delimiters=False)
                    record[kind + "_config"] = str(filename)
                    inputs.add(filename)
                schedule.append(record)
    protected = {str(p): sha(p) for p in sorted(inputs)}
    for scenario in SCENARIOS:
        truth = workload["truth"][scenario]
        require(protected[truth["ids"]] == truth["sha256"], f"Truth changed: {scenario}")
    save(output / "registration.json", dict(
        query_count=1000, warmup=1000, repetitions=2, nprobe=GRID, scenarios=SCENARIOS,
        variants=VARIANTS, schedule=schedule, protected=protected, diagnostic_nprobe=[24],
        expected_plain_processes=60, expected_plain_points=360, expected_diagnostic_processes=30,
        expected_diagnostic_points=30, cpu_node=2, memory_node=2,
        reference_manifest_sha256=protected[plan["ReferenceManifest"]],
        navigation_schema_version=2, counter_columns=NAVIGATION_V2,
        timing="Native full query, no diagnostic timing; warmup/measure/deterministic replay at every probe",
        order="Reverse cases and variants in repetition2; both repetitions use ascending probes",
        scope="First1000 only; no equal-recall interpolation, universal or 1B acceptance claim"))
    event(output, "prepared", unique_fingerprinted_files=len(protected),
          wall_seconds=time.monotonic() - started)


def run(path, stage):
    require(not [k for k in os.environ if k.startswith(("SPTAG_", "SPANN_", "OMP_")) or k == "LD_PRELOAD"],
            "Search environment overrides are forbidden")
    config = read_ini(path)
    plan = config["Campaign"]
    output = Path(plan["OutputDirectory"])
    registration = json.loads((output / "registration.json").read_text())
    workload = json.loads(Path(plan["Workloads"]).read_text())
    attrs = np.load(workload["attributes"], mmap_mode="r")
    masks = {s: np.ones(len(attrs), dtype=bool) if s == "unfilter" else
             predicate_mask(workload["predicates"][s], attrs) for s in SCENARIOS}
    diagnostic = stage == "diagnostic"
    kind = "diagnostic" if diagnostic else "plain"
    grid = [24] if diagnostic else GRID
    binary = plan["DiagnosticBinary" if diagnostic else "Binary"]
    old = json.loads(Path(plan["ReferenceResults"]).read_text())
    references = {(p["scenario"], "graph" if p["variant"] == "graph" else "min10"): p["payload_hashes"]
                  for p in old if p["variant"] in ("graph", "posting") and p["repetition"] == 1}
    schedule = [c for c in registration["schedule"] if not diagnostic or c["repetition"] == 1]
    if stage == "early":
        schedule = [c for c in schedule if c["scenario"] == "medium_tag" and c["repetition"] == 1]
    result_file = output / f"{kind}-results.json"
    records = json.loads(result_file.read_text()) if result_file.exists() else []
    by_key = {(p["scenario"], p["variant"], p["nprobe"], p["repetition"]): p for p in records}
    plain = json.loads((output / "plain-results.json").read_text()) if diagnostic else []
    parity = {(p["scenario"], p["variant"], p["nprobe"]): p["payload_hashes"] for p in plain}
    started, processes = time.monotonic(), 0
    event(output, stage + "_started", completed_points=len(records))
    for case in schedule:
        folder = output / f"{kind}_{case['case']}"
        validated = folder / "validated.json"
        if validated.exists():
            rows = json.loads(validated.read_text())
        else:
            require(not folder.exists(), f"Incomplete run preserved; inspect before retry: {folder}")
            command = ["numactl", "--cpunodebind=" + plan["CPUNode"], "--membind=" + plan["MemoryNode"],
                       binary, case[kind + "_config"]]
            event(output, kind + "_process_started", case=case["case"], completed_points=len(by_key),
                  native_log=str(folder / "native.log"))
            run_started = time.monotonic()
            peak = execute(command, folder)
            processes += 1
            elapsed = time.monotonic() - run_started
            log = (folder / "native.log").read_text()
            require("numeric lanes=1" in log and "conservative unknown" not in log,
                    f"Missing authenticated signatures: {folder}")
            rows = [json.loads(line) for line in log.splitlines() if line.startswith('{"mode":')]
            require([p["nprobe"] for p in rows] == grid, f"Incomplete/reordered native sweep: {folder}")
            for row in rows:
                require(row["queries"] == 1000 and row["diagnostic"] == diagnostic and
                        row.get("navigation_schema_version") == 2 and row.get("navigation_columns") == 22,
                        "Unexpected cohort/mode/counter schema")
                point = folder / f"nprobe_{row['nprobe']}"
                row.update(quality(point, workload["truth"][case["scenario"]], masks[case["scenario"]], 1000))
                row.update(case)
                row.update(command=command, peak_process_rss_bytes=peak, process_wall_seconds=elapsed,
                           output_directory=str(point))
                if row["nprobe"] == 24 and case["variant"] in ("graph", "min10"):
                    require(row["payload_hashes"] == references[case["scenario"], case["variant"]],
                            f"Frozen nprobe24 payload regression: {case['case']}")
                if diagnostic:
                    counters = np.fromfile(point / "navigation.u64", dtype="<u8").reshape(1000, 22)
                    row["mean_navigation"] = dict(zip(NAVIGATION_V2, counters.mean(0).tolist()))
                    row["max_navigation"] = dict(zip(NAVIGATION_V2, counters.max(0).tolist()))
                    require(np.all(counters[:, 17] == counters[:, 19] + counters[:, 20]) and
                            np.all(counters[:, 21] <= counters[:, 20]), "Invalid activation accounting")
                    target = 10 if case["variant"] == "graph" else int(case["variant"][3:])
                    require(np.all(counters[:, 18] >= target * counters[:, 19]), "Invalid fresh candidate count")
                    if case["variant"] == "graph":
                        require(not np.any(counters[:, 17:]), "Graph unexpectedly used auxiliary collection")
            save(validated, rows)
        for row in rows:
            key = row["scenario"], row["variant"], row["nprobe"]
            if diagnostic:
                require(row["payload_hashes"] == parity[key], "Normal/diagnostic payload mismatch")
            elif row["repetition"] == 2:
                require(row["payload_hashes"] == by_key[(*key, 1)]["payload_hashes"],
                        "Reversed repetition payload mismatch")
            by_key[(*key, row["repetition"])] = row
        save(result_file, list(by_key.values()))
        event(output, kind + "_process_complete", case=case["case"], completed_points=len(by_key),
              new_processes=processes, phase_wall_seconds=time.monotonic() - started)
    event(output, stage + "_complete", completed_points=len(by_key), new_processes=processes,
          wall_seconds=time.monotonic() - started)
    if not diagnostic and len(by_key) == 360:
        summarize(output, False)


def summarize(output, final):
    plain = json.loads((output / "plain-results.json").read_text())
    require(len(plain) == 360, "Incomplete normal curves")
    points = []
    for scenario in SCENARIOS:
        for variant in VARIANTS:
            for probe in GRID:
                rows = [r for r in plain if (r["scenario"], r["variant"], r["nprobe"]) ==
                        (scenario, variant, probe)]
                require(len(rows) == 2 and {r["repetition"] for r in rows} == {1, 2} and
                        rows[0]["payload_hashes"] == rows[1]["payload_hashes"], "Missing or unequal pair")
                points.append(dict(scenario=scenario, variant=variant, nprobe=probe, recall=rows[0]["recall"],
                    qps=statistics.mean(r["qps"] for r in rows), qps_min=min(r["qps"] for r in rows),
                    qps_max=max(r["qps"] for r in rows), underfilled_queries=rows[0]["underfilled_queries"]))
    save(output / "normal-summary.json", dict(points=points, plain_points=360, payload_parity=True))
    if final:
        started = time.monotonic()
        diagnostic = json.loads((output / "diagnostic-results.json").read_text())
        require(len(diagnostic) == 30, "Incomplete nprobe24 diagnostics")
        require(len({(r["scenario"], r["variant"]) for r in diagnostic}) == 30, "Duplicate diagnostic points")
        registration = json.loads((output / "registration.json").read_text())
        for source, expected in registration["protected"].items():
            require(sha(source) == expected, f"Registered bytes changed: {source}")
        save(output / "completion.json", dict(status="completed", plain_points=360, diagnostic_points=30,
            normal_processes=60, diagnostic_processes=30, normal_diagnostic_payload_parity=True,
            frozen_nprobe24_payload_parity=True, protected_inputs_runtime_index="unchanged",
            fingerprinted_files=len(registration["protected"]), query_count=1000,
            scope="Bounded measured curves, not universal performance or 1B acceptance"))
        event(output, "completed", plain_points=360, diagnostic_points=30,
              final_fingerprint_wall_seconds=time.monotonic() - started)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ini", type=Path)
    parser.add_argument("--stage", choices=("prepare", "early", "plain", "diagnostic", "finish"), required=True)
    args = parser.parse_args()
    try:
        if args.stage == "prepare":
            prepare(args.ini.resolve())
        elif args.stage == "finish":
            summarize(Path(read_ini(args.ini)["Campaign"]["OutputDirectory"]), True)
        else:
            run(args.ini.resolve(), args.stage)
    except Exception as error:
        root = Path(read_ini(args.ini)["Campaign"]["OutputDirectory"])
        if root.exists():
            event(root, "failed", stage=args.stage, error=str(error))
        raise
