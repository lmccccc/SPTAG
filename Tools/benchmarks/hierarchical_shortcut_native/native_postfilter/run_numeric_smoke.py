#!/usr/bin/env python3
"""Bounded same-main numeric routing replay; never resumes the full campaign."""
import argparse
import configparser
import json
from pathlib import Path
import statistics

import numpy as np
from run_full import (NAVIGATION, PAYLOADS, ROOT, execute, predicate_mask,
                      quality, read_ini, require, sha, write)


def main(path, stage):
    config = read_ini(path)
    plan = config["Campaign"]
    output = Path(plan["OutputDirectory"])
    workload = json.loads(Path(plan["Workloads"]).read_text())
    count = plan.getint("QueriesToMeasure")
    require(count in (32, 1000), "Only the bounded numeric smoke is authorized")
    scenarios = plan["Scenarios"].split(",")
    require(set(scenarios) == set(workload["truth"]), "All six scenarios required")
    require(config["SearchSSDIndex"]["InternalResultNum"] == "24", "Fixed nprobe24")
    require(plan.getint("Repeats") == 2, "Two reversed paired repetitions required")
    if stage == "prepare":
        require(not (output / "smoke-registration.json").exists(), "Already registered")
        controls = output / "smoke-configs"
        controls.mkdir()
        protected = {}
        for area in ("AnnService", "Wrappers"):
            for source in (ROOT / area).rglob("*"):
                if source.is_file() and source.suffix in (".h", ".cpp", ".i"):
                    protected[str(source)] = sha(source)
        for source in (Path(plan["Index"])).rglob("*"):
            if source.is_file():
                protected[str(source)] = sha(source)
        for source in [path, Path(__file__), Path(plan["Binary"]), Path(plan["DiagnosticBinary"]),
                       Path(plan["Queries"]), Path(plan["Workloads"]), Path(workload["attributes"])]:
            protected[str(source)] = sha(source)
        historical = output.parent / "main_postfilter_integration_20260919"
        for name in ("release_protection.json", "verified-dataset-protection.json"):
            protected.update(json.loads((historical / name).read_text()))
        protected.update(json.loads((output / "protected-runtime.json").read_text()))
        schedule = []
        for repetition in (1, 2):
            for scenario in scenarios if repetition == 1 else scenarios[::-1]:
                predicate, file = "empty", ""
                if scenario in workload["flat_query_tags"]:
                    predicate, file = "categorical", workload["flat_query_tags"][scenario]
                elif scenario in ("numeric", "mixed_dnf"):
                    predicate = "dnf"
                    file = workload["query_dnf"]["numeric" if scenario == "numeric" else "mixed"]
                protected[workload["truth"][scenario]["ids"]] = workload["truth"][scenario]["sha256"]
                if file:
                    protected[file] = sha(file)
                for mode in ("graph", "posting") if repetition == 1 else ("posting", "graph"):
                    case = f"{scenario}_{mode}_r{repetition}"
                    native = configparser.ConfigParser(interpolation=None)
                    native.optionxform = str
                    native["SearchSSDIndex"] = dict(config["SearchSSDIndex"])
                    native["SearchSSDIndex"]["EnablePostingNavigation"] = str(mode == "posting").lower()
                    native["Benchmark"] = dict(Index=plan["Index"], Queries=plan["Queries"],
                        Predicate=predicate, PredicateFile=file, MaxQueries=str(count), Warmup=str(count))
                    filename = controls / f"{case}.ini"
                    with filename.open("w") as stream:
                        native.write(stream, space_around_delimiters=False)
                    protected[str(filename)] = sha(filename)
                    schedule.append(dict(case=case, scenario=scenario, mode=mode,
                                         repetition=repetition, config=str(filename)))
        write(output / "smoke-registration.json", dict(
            queries=count, warmup=count, nprobe=24, schedule=schedule, protected=protected,
            counters=NAVIGATION, cpu_node=plan.getint("CPUNode"), memory_node=plan.getint("MemoryNode"),
            scope="bounded numeric correctness/work smoke, not an all-scenario performance or 1B acceptance campaign"))
        return
    registration = json.loads((output / "smoke-registration.json").read_text())
    for source, expected in registration["protected"].items():
        require(sha(source) == expected, f"Registered source/input/runtime changed: {source}")
    if stage in ("plain", "diagnostic"):
        diagnostic = stage == "diagnostic"
        binary = plan["DiagnosticBinary" if diagnostic else "Binary"]
        attrs = np.load(workload["attributes"], mmap_mode="r")
        records = []
        for case in registration["schedule"]:
            if diagnostic and case["repetition"] != 1:
                continue
            directory = output / f"smoke_{stage}_{case['case']}"
            if not (directory / "validated.json").exists():
                require(not directory.exists(), f"Preserved incomplete point requires inspection: {directory}")
                peak = execute(["numactl", "--cpunodebind=" + plan["CPUNode"],
                    "--membind=" + plan["MemoryNode"], binary, case["config"]], directory)
                log = (directory / "native.log").read_text()
                require("numeric lanes=1" in log and "conservative unknown" not in log,
                        f"Numeric routing metadata was not authenticated: {directory}")
                rows = [json.loads(line) for line in log.splitlines() if line.startswith('{"mode":')]
                require(len(rows) == 1 and rows[0]["queries"] == count and
                        rows[0]["diagnostic"] == diagnostic, "Wrong native output/window")
                row = rows[0]
                scenario = case["scenario"]
                mask = np.ones(len(attrs), dtype=bool) if scenario == "unfilter" else \
                    predicate_mask(workload["predicates"][scenario], attrs)
                row.update(quality(directory, workload["truth"][scenario], mask, count))
                row.update(case)
                row["peak_process_rss_bytes"] = peak
                if diagnostic:
                    counters = np.fromfile(directory / "navigation.u64", dtype="<u8").reshape(count, len(NAVIGATION))
                    row["mean_navigation"] = dict(zip(NAVIGATION, counters.mean(axis=0).tolist()))
                    row["max_navigation"] = dict(zip(NAVIGATION, counters.max(axis=0).tolist()))
                write(directory / "validated.json", row)
            records.append(json.loads((directory / "validated.json").read_text()))
            write(output / f"smoke-{stage}-results.json", records)
            print(json.dumps(dict(stage=stage, completed=len(records), case=case["case"])), flush=True)
    elif stage == "summarize":
        plain = json.loads((output / "smoke-plain-results.json").read_text())
        diagnostic = json.loads((output / "smoke-diagnostic-results.json").read_text())
        require(len(plain) == 24 and len(diagnostic) == 12, "Incomplete bounded matrix")
        summary = {}
        for scenario in scenarios:
            summary[scenario] = {}
            for mode in ("graph", "posting"):
                points = [p for p in plain if p["scenario"] == scenario and p["mode"] == mode]
                counter = next(p for p in diagnostic if p["scenario"] == scenario and p["mode"] == mode)
                require(len(points) == 2 and all(p["payload_hashes"] == counter["payload_hashes"]
                                                for p in points), "Repetition/diagnostic result or SSD-work mismatch")
                summary[scenario][mode] = dict(
                    recall=counter["recall"], underfilled_queries=counter["underfilled_queries"],
                    qps_mean=statistics.mean(p["qps"] for p in points),
                    qps_repetitions=[p["qps"] for p in points],
                    latency_percentiles=[{k: p[k] for k in ("p50_us", "p95_us", "p99_us")} for p in points],
                    ssd_work=counter["mean_ssd_work"], navigation=counter["mean_navigation"],
                    navigation_max=counter["max_navigation"])
        write(output / "smoke-summary.json", dict(
            scenarios=summary, query_count=count, plain_points=24, diagnostic_points=12,
            exact_filter_violations=0, payload_parity=True,
            all_scenario_or_1b_acceptance="not established; bounded same-budget smoke only"))
    for source, expected in registration["protected"].items():
        require(sha(source) == expected, f"Protected bytes changed: {source}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ini", type=Path)
    parser.add_argument("--stage", choices=("prepare", "plain", "diagnostic", "summarize"), required=True)
    arguments = parser.parse_args()
    main(arguments.ini.resolve(), arguments.stage)
