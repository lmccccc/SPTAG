#!/usr/bin/env python3
"""Isolated post-graph campaign: native arrays, fresh timing, durable process resume."""
import argparse
import configparser
import json
import os
from pathlib import Path
import statistics
import time

import numpy as np

from run_full import NAVIGATION, execute, predicate_mask, quality, read_ini, require, sha

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
GRID = [16, 24, 48, 96, 192, 384]
SCENARIOS = ("medium_tag", "extreme_tag", "mixed_dnf", "numeric", "broad_tag", "unfilter")
VARIANTS = ("graph", "postgraph_shared", "postgraph_extra", "graph_total")
BUDGETS = {
    "graph": dict(max_check=2048, posting_enabled=False, posting_additional_max_check=0),
    "postgraph_shared": dict(max_check=2048, posting_enabled=True, posting_additional_max_check=0),
    "postgraph_extra": dict(max_check=2048, posting_enabled=True, posting_additional_max_check=2048),
    "graph_total": dict(max_check=4096, posting_enabled=False, posting_additional_max_check=0),
}
for budget in BUDGETS.values():
    budget["posting_anchor_count"] = 8
    budget["total_max_check"] = budget["max_check"] + budget["posting_additional_max_check"]

# Schema is extended alongside the native client; historical decoders are not imported.
COUNTERS = NAVIGATION + (
    "posting_activations", "posting_new_candidates", "posting_target_met",
    "posting_underfilled", "posting_budget_underfilled", "auxiliary_first_visits",
    "auxiliary_negative_first_visits", "auxiliary_visited_skips",
    "auxiliary_unvisited_negative_skips", "selected_h2_zero_eligible_rows",
    "selected_h2_zero_fresh_rows", "auxiliary_prefetch_lines",
) + tuple(f"{level}_{field}" for level in ("h2", "h3plus") for field in (
    "owner_references", "member_references", "candidate_considerations", "unique_states",
    "expand_attempts", "expanded_skips", "completed_rows", "representative_distances"))
COUNTERS += ("head_before", "head_after", "head_target", "graph_unique_h1", "graph_may_matches",
             "anchors", "supplement_reason", "graph_checked_leaves", "supplement_checked_leaves",
             "graph_distances", "supplement_distances", "preserved_heads")
SCHEMA_VERSION = 6
CURRENT_SOURCES = (
    "AnnService/inc/Core/BKT/Index.h", "AnnService/src/Core/BKT/BKTIndex.cpp",
    "AnnService/inc/Core/Common/NavigationVisited.h", "AnnService/inc/Core/Common/PostingNavigation.h",
    "AnnService/inc/Core/Common/GraphAccessStats.h", "AnnService/inc/Core/Common/WorkSpace.h",
    "AnnService/inc/Core/Common/BKTree.h", "AnnService/inc/Core/SPANN/PostingNavigation.h",
    "AnnService/inc/Core/SPANN/Options.h", "AnnService/inc/Core/SPANN/ParameterDefinitionList.h",
    "AnnService/src/Core/SPANN/SPANNIndex.cpp", "Wrappers/src/CoreInterface.cpp",
)


def save(path, value):
    path = Path(path)
    pending = path.with_name(path.name + ".pending")
    with pending.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def event(output, kind, **fields):
    with (output / "events.jsonl").open("a") as stream:
        stream.write(json.dumps(dict(event=kind, unix_time=time.time(), **fields)) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def schedule(diagnostic_full_grid=False):
    rows = []
    for kind in ("functional_plain", "functional_diagnostic", "plain", "diagnostic"):
        for repetition in ((1, 2) if kind == "plain" else (1,)):
            scenarios = SCENARIOS if repetition == 1 else SCENARIOS[::-1]
            for scenario in scenarios:
                variants = VARIANTS if repetition == 1 else VARIANTS[::-1]
                if kind == "functional_plain":
                    variants = ("postgraph_extra",)
                elif kind == "functional_diagnostic":
                    variants = ("graph", "postgraph_extra")
                for variant in variants:
                    rows.append(dict(case=f"{kind}_{scenario}_{variant}_r{repetition}",
                        kind=kind, scenario=scenario, variant=variant, repetition=repetition,
                        queries=32 if kind.startswith("functional") else 1000,
                        probes=GRID if kind == "plain" or
                            (kind == "diagnostic" and diagnostic_full_grid) else [24],
                        **BUDGETS[variant]))
    return rows


def prepare(path):
    config = read_ini(path)
    plan = config["Campaign"]
    require(json.loads(config["SearchSweep"]["NProbe"]) == GRID, "Fixed ascending probe grid changed")
    require(not any(key.lower() == "postingmincandidates" for section in config
                    for key in config[section]), "PostingMinCandidates is retired, including when OFF")
    output = Path(plan["OutputDirectory"])
    output.mkdir(parents=True, exist_ok=True)
    require(not (output / "registration.json").exists(), "Already registered; resume a stage")
    workload = json.loads(Path(plan["Workloads"]).read_text())
    queries = np.load(plan["Queries"], mmap_mode="r")
    require(len(queries) >= 1000 and queries.shape[1] == 128, "Missing 1000-query Float128 cohort")
    controls = output / "configs"
    controls.mkdir(exist_ok=True)
    diagnostic_full_grid = plan.getboolean("DiagnosticFullGrid", fallback=False)
    rows = schedule(diagnostic_full_grid)
    inputs = {path, HERE / "run_postgraph.py", HERE / "postgraph.ini", HERE / "Bench.cpp",
              HERE / "run_full.py", Path(plan["Queries"]), Path(plan["Workloads"]),
              Path(workload["attributes"]), Path(plan["Index"]) / "tenant_0/indexloader.ini"}
    inputs.update(ROOT / source for source in CURRENT_SOURCES)
    inputs.update(p for p in Path(plan["Index"]).rglob("*") if p.is_file())
    for scenario in SCENARIOS:
        truth = workload["truth"][scenario]
        require(sha(truth["ids"]) == truth["sha256"], f"Truth changed: {scenario}")
        require(len(np.load(truth["ids"], mmap_mode="r")) >= 1000, f"Short truth: {scenario}")
        inputs.add(Path(truth["ids"]))
    for case in rows:
        native = configparser.ConfigParser(interpolation=None)
        native.optionxform = str
        native["SearchSSDIndex"] = dict(config["SearchSSDIndex"])
        native["SearchSSDIndex"].update(
            MaxCheck=str(case["max_check"]),
            EnablePostingNavigation=str(case["posting_enabled"]).lower(),
            PostingAnchorCount="8",
            PostingAdditionalMaxCheck=str(case["posting_additional_max_check"]))
        native["SearchSweep"] = dict(NProbe=json.dumps(case["probes"]))
        scenario = case["scenario"]
        predicate, predicate_file = "empty", ""
        if scenario in workload["flat_query_tags"]:
            predicate, predicate_file = "categorical", workload["flat_query_tags"][scenario]
        elif scenario in ("numeric", "mixed_dnf"):
            predicate = "dnf"
            predicate_file = workload["query_dnf"]["numeric" if scenario == "numeric" else "mixed"]
        if predicate_file:
            require(len(np.load(predicate_file, mmap_mode="r")) == len(queries), "Predicate cohort mismatch")
            inputs.add(Path(predicate_file))
        native["Benchmark"] = dict(Index=plan["Index"], Queries=plan["Queries"],
            Predicate=predicate, PredicateFile=predicate_file,
            MaxQueries=str(case["queries"]), Warmup=str(case["queries"]))
        filename = controls / f"{case['case']}.ini"
        with filename.open("w") as stream:
            native.write(stream, space_around_delimiters=False)
        case["config"] = str(filename)
        inputs.add(filename)
    binaries = {key: dict(path=plan[key], sha256=sha(plan[key]))
                for key in ("Binary", "DiagnosticBinary")}
    save(output / "registration.json", dict(query_count=1000, warmup=1000, repetitions=2,
        nprobe=GRID, scenarios=SCENARIOS, variants=VARIANTS, budget_metadata=BUDGETS,
        schedule=rows, normal_processes=48, normal_points=288,
        diagnostic_points=144 if diagnostic_full_grid else 24,
        diagnostic_nprobe=GRID if diagnostic_full_grid else [24],
        functional_queries=32, functional_scenarios=SCENARIOS, functional_processes=18,
        functional_scope="Six postgraph_extra controls; same-build graph diagnostics preserve the native phase",
        counter_columns=COUNTERS, navigation_schema_version=SCHEMA_VERSION,
        cpu_node=plan.getint("CPUNode"), memory_node=plan.getint("MemoryNode"),
        binaries=binaries, protected={str(p): sha(p) for p in sorted(inputs)},
        protection_scope="Current relevant code, configs, query/truth metadata and binaries; no historical sealing",
        timing="1000 warmup + 1000 measured + deterministic replay per ascending probe in one native process",
        caveats=["No old timing splice or PostingMinCandidates sweep", "Shared-budget underfill is valid",
                 "No target-recall guarantee or billion-vector scalability claim"]))
    event(output, "prepared", normal_processes=48, normal_points=288,
          diagnostic_points=144 if diagnostic_full_grid else 24)


def load_registered(path):
    plan = read_ini(path)["Campaign"]
    output = Path(plan["OutputDirectory"])
    registration = json.loads((output / "registration.json").read_text())
    for source, expected in registration["protected"].items():
        require(sha(source) == expected, f"Registered input changed: {source}")
    for binary in registration["binaries"].values():
        require(sha(binary["path"]) == binary["sha256"], f"Registered binary changed: {binary['path']}")
    return plan, output, registration


def payloads_match(left, right):
    return left["payload_hashes"] == right["payload_hashes"]


def read_counters(output, row):
    folder = output / row["case"] / f"nprobe_{row['nprobe']}"
    data = np.fromfile(folder / "navigation.u64", dtype="<u8").reshape(row["queries"], len(COUNTERS))
    return {name: data[:, i] for i, name in enumerate(COUNTERS)}


def validate_navigation(output, row):
    counters = read_counters(output, row)
    require(np.all(counters["auxiliary_first_visits"] ==
                   counters["auxiliary_negative_first_visits"] + counters["posting_new_candidates"]),
            "Auxiliary fresh match accounting differs")
    require(np.all(counters["auxiliary_first_visits"] + counters["auxiliary_visited_skips"] +
                   counters["auxiliary_unvisited_negative_skips"] ==
                   counters["auxiliary_members"]), "Auxiliary reference accounting differs")
    require(np.all(counters["head_target"] == row["nprobe"]) and
            np.all(counters["head_before"] <= counters["head_after"]) and
            np.all(counters["head_after"] <= counters["head_target"]) and
            np.all(counters["preserved_heads"] == counters["head_before"]), "Head count/preservation violation")
    require(np.all(counters["posting_activations"] <= 1), "More than one supplementary phase")
    require(np.all(counters["supplement_checked_leaves"] == counters["auxiliary_first_visits"]) and
            np.all(counters["supplement_distances"] == counters["auxiliary_first_visits"]),
            "Supplement scored-node budget mismatch")
    full = counters["head_before"] >= counters["head_target"]
    for field in ("posting_activations", "owner_references", "signature_checks", "upper_distances", "auxiliary_members"):
        require(not np.any(counters[field][full]), f"Full head set performed auxiliary work: {field}")
    if row["posting_additional_max_check"] == 0:
        exhausted = counters["graph_checked_leaves"] >= row["max_check"]
        require(not np.any(counters["posting_activations"][exhausted]), "Zero-extra exhausted graph supplemented")
    folder = output / row["case"] / f"nprobe_{row['nprobe']}"
    row["graph_payload_hashes"] = {name: sha(folder / name) for name in ("graph_ids.i32", "graph_dist.f32")}
    row["supplement_reasons"] = {str(i): int(np.count_nonzero(counters["supplement_reason"] == i)) for i in range(8)}
    row["mean_navigation"] = {name: float(values.mean()) for name, values in counters.items()}
    row["max_navigation"] = {name: int(values.max()) for name, values in counters.items()}


def validate_pairs(output, kind, rows):
    if kind == "plain":
        indexed = {(r["scenario"], r["variant"], r["nprobe"], r["repetition"]): r for r in rows}
        for row in rows:
            if row["repetition"] == 2:
                first = indexed[(row["scenario"], row["variant"], row["nprobe"], 1)]
                require(payloads_match(first, row), "Reversed repetition changed native payload")
    if kind.endswith("diagnostic"):
        plain_kind = "functional_plain" if kind.startswith("functional") else "plain"
        plain = json.loads((output / f"{plain_kind}-results.json").read_text())
        for row in rows:
            matches = [p for p in plain if (p["scenario"], p["variant"], p["nprobe"]) ==
                       (row["scenario"], row["variant"], row["nprobe"])]
            if kind == "functional_diagnostic" and row["variant"] == "graph":
                continue
            require(matches and all(payloads_match(p, row) for p in matches),
                    "Normal/diagnostic native payload differs")
    indexed = {(r["scenario"], r["variant"], r["nprobe"], r["repetition"]): r for r in rows}
    for row in rows:
        if kind.endswith("diagnostic") and row["variant"] in ("postgraph_shared", "postgraph_extra"):
            graph = indexed.get((row["scenario"], "graph", row["nprobe"], row["repetition"]))
            if graph:
                require(row["graph_payload_hashes"] == graph["graph_payload_hashes"], "Original H1 head phase changed")
                original = read_counters(output, graph)
                current = read_counters(output, row)
                for field in ("graph_checked_leaves", "graph_distances", "graph_rows", "tree_visits"):
                    require(np.array_equal(original[field], current[field]), f"Native graph work changed: {field}")
        if row["scenario"] != "unfilter" or row["variant"] not in ("postgraph_shared", "postgraph_extra"):
            continue
        baseline = indexed.get(("unfilter", "graph", row["nprobe"], row["repetition"]))
        if baseline:
            require(payloads_match(baseline, row), "Unfiltered native navigation changed")


def run(path, stage, retry_incomplete=False):
    require(not [k for k in os.environ if k.startswith(("SPTAG_", "SPANN_", "OMP_")) or k == "LD_PRELOAD"],
            "Search environment overrides forbidden")
    plan, output, registration = load_registered(path)
    if stage == "plain":
        require((output / "functional-completion.json").exists(), "Run six-case32 functional controls first")
    if stage == "diagnostic":
        require((output / "normal-completion.json").exists(), "Publish all normal curves before diagnostics")
    workload = json.loads(Path(plan["Workloads"]).read_text())
    attrs = np.load(workload["attributes"], mmap_mode="r")
    masks = {s: np.ones(len(attrs), dtype=bool) if s == "unfilter" else
             predicate_mask(workload["predicates"][s], attrs) for s in SCENARIOS}
    kinds = ("functional_plain", "functional_diagnostic") if stage == "functional" else (stage,)
    for kind in kinds:
        diagnostic = kind.endswith("diagnostic")
        records = []
        for case in (c for c in registration["schedule"] if c["kind"] == kind):
            folder = output / case["case"]
            validated = folder / "validated.json"
            if validated.exists():
                rows = json.loads(validated.read_text())
                require([r["nprobe"] for r in rows] == case["probes"], "Corrupt resume record")
                for row in rows:
                    for filename, expected in row["payload_hashes"].items():
                        require(sha(folder / f"nprobe_{row['nprobe']}" / filename) == expected,
                                f"Resumed payload changed: {folder}/{filename}")
            else:
                if folder.exists():
                    require(retry_incomplete, f"Incomplete process retained: {folder}; inspect, then --retry-incomplete")
                    archived = output / f"{case['case']}.incomplete-{time.time_ns()}"
                    folder.rename(archived)
                    event(output, "incomplete_archived", case=case["case"], retained=str(archived))
                binary = registration["binaries"]["DiagnosticBinary" if diagnostic else "Binary"]["path"]
                command = ["numactl", f"--cpunodebind={registration['cpu_node']}",
                           f"--membind={registration['memory_node']}", binary, case["config"]]
                event(output, "process_started", case=case["case"], command=command)
                started = time.monotonic()
                peak = execute(command, folder)
                wall = time.monotonic() - started
                log = (folder / "native.log").read_text()
                require("numeric lanes=1" in log and "conservative unknown" not in log,
                        "Authenticated numeric routing metadata unavailable")
                rows = [json.loads(line) for line in log.splitlines() if line.startswith('{"mode":')]
                require([r["nprobe"] for r in rows] == case["probes"], "Incomplete native ascending probe array")
                for row in rows:
                    require(row["queries"] == case["queries"] and row["diagnostic"] == diagnostic and
                            row["navigation_schema_version"] == SCHEMA_VERSION and
                            row["navigation_columns"] == len(COUNTERS), "Unexpected native cohort/schema")
                    require(row["mode"] == ("posting" if case["posting_enabled"] else "graph"),
                            "Native posting switch differs")
                    for key in ("max_check", "posting_anchor_count", "posting_additional_max_check"):
                        require(row[key] == case[key], f"Native budget differs: {key}")
                    row.update(quality(folder / f"nprobe_{row['nprobe']}", workload["truth"][case["scenario"]],
                                       masks[case["scenario"]], case["queries"]))
                    row.update(case)
                    row.update(command=command, peak_process_rss_bytes=peak, process_wall_seconds=wall,
                               timing_accepted=kind == "plain")
                    if diagnostic:
                        validate_navigation(output, row)
                        row["diagnostic_qps_not_for_throughput"] = row.pop("qps")
                    elif kind.startswith("functional"):
                        row["functional_qps_not_for_throughput"] = row.pop("qps")
                save(validated, rows)
            records.extend(rows)
            validate_pairs(output, kind, records)
            save(output / f"{kind}-results.json", records)
            event(output, "process_complete", case=case["case"], points=len(records))
    load_registered(path)
    if stage == "functional":
        controls = json.loads((output / "functional_diagnostic-results.json").read_text())
        require(len(controls) == 12, "Missing functional graph/postgraph controls")
        save(output / "functional-completion.json", dict(status="completed", scenarios=SCENARIOS,
             queries=32, timing_accepted=False, native_payload_parity=True))


def publish(path):
    _, output, registration = load_registered(path)
    rows = json.loads((output / "plain-results.json").read_text())
    require(len(rows) == 288, "Need all 288 normal points before publishing")
    validate_pairs(output, "plain", rows)
    points = []
    for scenario in SCENARIOS:
        for variant in VARIANTS:
            for probe in GRID:
                pair = [r for r in rows if (r["scenario"], r["variant"], r["nprobe"]) ==
                        (scenario, variant, probe)]
                require(len(pair) == 2 and {r["repetition"] for r in pair} == {1, 2} and
                        payloads_match(*pair), "Missing deterministic normal pair")
                points.append(dict(scenario=scenario, variant=variant, nprobe=probe, queries=1000,
                    repetitions=2, recall=pair[0]["recall"], qps=statistics.mean(r["qps"] for r in pair),
                    qps_min=min(r["qps"] for r in pair), qps_max=max(r["qps"] for r in pair),
                    underfilled_queries=pair[0]["underfilled_queries"], **BUDGETS[variant]))
    save(output / "normal-summary.json", dict(status="normal_completed", query_count=1000,
        repetitions=2, nprobe=GRID, scenarios=SCENARIOS, variants=VARIANTS,
        budget_metadata=BUDGETS, points=points, normal_points=288, normal_processes=48,
        diagnostics=dict(nprobe=registration.get("diagnostic_nprobe", [24]),
                         scope="Separate from normal QPS; not a prerequisite for publishing normal curves"),
        caveats=registration["caveats"]))
    save(output / "normal-completion.json", dict(status="completed", points=288, processes=48,
        plain_results_sha256=sha(output / "plain-results.json"), publish_before_diagnostics=True))
    event(output, "normal_results_ready", points=288, plotting="parent R --postgraph")


def finish(path):
    _, output, registration = load_registered(path)
    require((output / "normal-completion.json").exists(), "Normal results not published")
    diagnostic = json.loads((output / "diagnostic-results.json").read_text())
    require(len(diagnostic) == registration["diagnostic_points"], "Missing separate diagnostic points")
    validate_pairs(output, "diagnostic", diagnostic)
    save(output / "completion.json", dict(status="completed", normal_points=288, normal_processes=48,
        diagnostic_points=len(diagnostic),
        diagnostic_nprobe=registration.get("diagnostic_nprobe", [24]),
        normal_diagnostic_payload_parity=True,
        shared_budget_underfill="Reported honestly, never repaired or hidden",
        registration_sha256=sha(output / "registration.json"), caveats=registration["caveats"]))
    event(output, "completed", normal_points=288, diagnostic_points=len(diagnostic))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ini", type=Path)
    parser.add_argument("--stage", required=True,
                        choices=("prepare", "functional", "plain", "publish", "diagnostic", "finish"))
    parser.add_argument("--retry-incomplete", action="store_true")
    args = parser.parse_args()
    try:
        if args.stage in ("prepare", "publish", "finish"):
            globals()[args.stage](args.ini.resolve())
        else:
            run(args.ini.resolve(), args.stage, args.retry_incomplete)
    except Exception as error:
        root = Path(read_ini(args.ini)["Campaign"]["OutputDirectory"])
        if root.exists():
            event(root, "failed", stage=args.stage, error=str(error))
        raise
