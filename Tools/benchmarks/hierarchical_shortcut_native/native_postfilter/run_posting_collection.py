#!/usr/bin/env python3
"""Bounded fresh-H1 floor trial against same-main H1 and frozen fraction posting."""
import argparse
import configparser
import json
from pathlib import Path
import statistics

import numpy as np
from run_full import NAVIGATION, ROOT, execute, predicate_mask, quality, read_ini, require, sha, write

NAVIGATION_V2 = NAVIGATION + (
    "posting_activations", "posting_new_candidates", "posting_target_met",
    "posting_underfilled", "posting_budget_underfilled")


def main(path, stage):
    config = read_ini(path)
    plan = config["Campaign"]
    output = Path(plan["OutputDirectory"])
    evidence = Path(plan["EvidenceRoot"])
    count = plan.getint("QueriesToMeasure")
    require(count in (32, 1000), "Only bounded cohorts are authorized")
    require(config["SearchSSDIndex"]["PostingMinCandidates"] == "10", "Trial target must remain 10")
    require(config["SearchSSDIndex"]["InternalResultNum"] == "24", "Fixed nprobe24")
    workload = json.loads(Path(plan["Workloads"]).read_text())
    scenarios = plan["Scenarios"].split(",")
    require(set(scenarios) == set(workload["truth"]), "All original scenarios required")
    binary_keys = dict(graph="Binary", posting="Binary", fraction="FractionBinary")
    diagnostic_keys = dict(graph="DiagnosticBinary", posting="DiagnosticBinary", fraction="FractionDiagnosticBinary")
    if stage == "prepare":
        output.mkdir(parents=True, exist_ok=True)
        require(not (output / "registration.json").exists(), "Already registered")
        controls = output / "configs"
        controls.mkdir()
        protected = json.loads((evidence / "protected-runtime.json").read_text())
        historical = evidence.parent / "main_postfilter_integration_20260919"
        for name in ("release_protection.json", "verified-dataset-protection.json"):
            protected.update(json.loads((historical / name).read_text()))
        for area in ("AnnService", "Wrappers"):
            for source in (ROOT / area).rglob("*"):
                if source.is_file() and source.suffix in (".h", ".cpp", ".i"):
                    protected[str(source)] = sha(source)
        for source in Path(plan["Index"]).rglob("*"):
            if source.is_file():
                protected[str(source)] = sha(source)
        for source in [path, Path(__file__), Path(__file__).with_name("Bench.cpp"),
                       Path(plan["Queries"]), Path(plan["Workloads"]), Path(workload["attributes"]),
                       *[Path(plan[key]) for key in set(binary_keys.values()) | set(diagnostic_keys.values())]]:
            protected[str(source)] = sha(source)
        schedule = []
        for repetition in (1, 2):
            for scenario in scenarios if repetition == 1 else scenarios[::-1]:
                predicate, predicate_file = "empty", ""
                if scenario in workload["flat_query_tags"]:
                    predicate, predicate_file = "categorical", workload["flat_query_tags"][scenario]
                elif scenario in ("numeric", "mixed_dnf"):
                    predicate = "dnf"
                    predicate_file = workload["query_dnf"]["numeric" if scenario == "numeric" else "mixed"]
                protected[workload["truth"][scenario]["ids"]] = workload["truth"][scenario]["sha256"]
                if predicate_file:
                    protected[predicate_file] = sha(predicate_file)
                variants = ("graph", "fraction", "posting") if repetition == 1 else ("posting", "fraction", "graph")
                for variant in variants:
                    case = f"{scenario}_{variant}_r{repetition}"
                    native = configparser.ConfigParser(interpolation=None)
                    native.optionxform = str
                    native["SearchSSDIndex"] = dict(config["SearchSSDIndex"])
                    native["SearchSSDIndex"]["EnablePostingNavigation"] = str(variant != "graph").lower()
                    if variant == "fraction":
                        del native["SearchSSDIndex"]["PostingMinCandidates"]
                    native["Benchmark"] = dict(Index=plan["Index"], Queries=plan["Queries"],
                        Predicate=predicate, PredicateFile=predicate_file,
                        MaxQueries=str(count), Warmup=str(count))
                    filename = controls / f"{case}.ini"
                    with filename.open("w") as stream:
                        native.write(stream, space_around_delimiters=False)
                    protected[str(filename)] = sha(filename)
                    schedule.append(dict(case=case, scenario=scenario, variant=variant,
                                         repetition=repetition, config=str(filename)))
        write(output / "registration.json", dict(
            queries=count, warmup=count, nprobe=24, repeats=2, target=10,
            schedule=schedule, protected=protected, counter_schemas={"1": NAVIGATION, "2": NAVIGATION_V2},
            baseline="same current MAIN H1 admission with auxiliary disabled",
            fraction="frozen corrected fraction-policy executable, freshly timed in this same protocol",
            fraction_config_difference="PostingMinCandidates omitted: frozen binary does not implement the new parameter",
            scope="functional32" if count == 32 else "bounded1000, not all-scenario/1B acceptance",
            cpu_node=plan.getint("CPUNode"), memory_node=plan.getint("MemoryNode")))
        return
    registration = json.loads((output / "registration.json").read_text())
    for source, expected in registration["protected"].items():
        require(sha(source) == expected, f"Registered bytes changed: {source}")
    if stage in ("plain", "diagnostic"):
        diagnostic = stage == "diagnostic"
        attrs = np.load(workload["attributes"], mmap_mode="r")
        records = []
        for case in registration["schedule"]:
            if diagnostic and case["repetition"] != 1:
                continue
            directory = output / f"{stage}_{case['case']}"
            if not (directory / "validated.json").exists():
                require(not directory.exists(), f"Preserved incomplete run: {directory}")
                binary = plan[(diagnostic_keys if diagnostic else binary_keys)[case["variant"]]]
                command = ["numactl", "--cpunodebind=" + plan["CPUNode"],
                           "--membind=" + plan["MemoryNode"], binary, case["config"]]
                peak = execute(command, directory)
                log = (directory / "native.log").read_text()
                require("numeric lanes=1" in log and "conservative unknown" not in log,
                        "Missing authenticated routing signatures")
                rows = [json.loads(line) for line in log.splitlines() if line.startswith('{"mode":')]
                require(len(rows) == 1 and rows[0]["queries"] == count and
                        rows[0]["diagnostic"] == diagnostic, "Incorrect query window/counter mode")
                row = rows[0]
                mask = np.ones(len(attrs), dtype=bool) if case["scenario"] == "unfilter" else \
                    predicate_mask(workload["predicates"][case["scenario"]], attrs)
                row.update(quality(directory, workload["truth"][case["scenario"]], mask, count))
                row.update(case)
                row["peak_process_rss_bytes"] = peak
                row["command"] = command
                version = 1 if case["variant"] == "fraction" else 2
                names = NAVIGATION if version == 1 else NAVIGATION_V2
                require(row.get("navigation_schema_version", 1) == version and
                        row.get("navigation_columns", 17) == len(names), "Unexpected native counter schema")
                if diagnostic:
                    counters = np.fromfile(directory / "navigation.u64", dtype="<u8").reshape(count, len(names))
                    row["counter_columns"] = names
                    row["mean_navigation"] = dict(zip(names, counters.mean(0).tolist()))
                    row["max_navigation"] = dict(zip(names, counters.max(0).tolist()))
                    if version == 2:
                        require(np.all(counters[:, 17] == counters[:, 19] + counters[:, 20]) and
                                np.all(counters[:, 21] <= counters[:, 20]), "Invalid activation accounting")
                        if case["variant"] == "graph":
                            require(not np.any(counters[:, 17:]), "H1-only ran auxiliary collection")
                write(directory / "validated.json", row)
            records.append(json.loads((directory / "validated.json").read_text()))
            write(output / f"{stage}-results.json", records)
            print(json.dumps(dict(stage=stage, completed=len(records), case=case["case"])), flush=True)
    elif stage == "summarize":
        plain = json.loads((output / "plain-results.json").read_text())
        diagnostic = json.loads((output / "diagnostic-results.json").read_text())
        require(len(plain) == 36 and len(diagnostic) == 18, "Incomplete three-way paired matrix")
        summary = {}
        for scenario in scenarios:
            summary[scenario] = {}
            for variant in ("graph", "fraction", "posting"):
                points = [p for p in plain if p["scenario"] == scenario and p["variant"] == variant]
                counter = next(p for p in diagnostic if p["scenario"] == scenario and p["variant"] == variant)
                require(len(points) == 2 and all(p["payload_hashes"] == counter["payload_hashes"] for p in points),
                        "Native repetition/diagnostic IDs, distances or SSD work differ")
                summary[scenario][variant] = dict(
                    recall=counter["recall"], underfilled_queries=counter["underfilled_queries"],
                    qps_mean=statistics.mean(p["qps"] for p in points),
                    qps_repetitions=[p["qps"] for p in points],
                    latency=[{k: p[k] for k in ("p50_us", "p95_us", "p99_us")} for p in points],
                    ssd_work=counter["mean_ssd_work"], navigation=counter["mean_navigation"],
                    navigation_max=counter["max_navigation"])
        write(output / "summary.json", dict(scenarios=summary, queries=count, target=10,
            plain_points=36, diagnostic_points=18, exact_filter_violations=0, payload_parity=True,
            interpretation="Same-budget bounded trial. No universal or 1B performance acceptance inferred."))
    for source, expected in registration["protected"].items():
        require(sha(source) == expected, f"Protected bytes changed: {source}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ini", type=Path)
    parser.add_argument("--stage", choices=("prepare", "plain", "diagnostic", "summarize"), required=True)
    arguments = parser.parse_args()
    main(arguments.ini.resolve(), arguments.stage)
