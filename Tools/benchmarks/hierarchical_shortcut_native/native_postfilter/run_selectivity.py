#!/usr/bin/env python3
"""Registered large-index comparison: native batch search, bounded offline checks."""
import argparse
import configparser
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import signal
import statistics
import subprocess
import sys
import time

import numpy as np

from run_full import quality, read_ini, require, sha
from run_postgraph import (
    COUNTERS, CURRENT_SOURCES, GRID, SCHEMA_VERSION, VARIANTS, event, save,
    validate_navigation, validate_pairs,
)
from selectivity_common import (
    SelectedPredicate, inventory, validate_identities,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def stamp():
    return datetime.now(timezone.utc).isoformat()


def write_ini(path, config):
    with Path(path).open("x") as stream:
        config.write(stream, space_around_delimiters=False)
        stream.flush()
        os.fsync(stream.fileno())


def plan_schedule(scenarios, budgets):
    cases = []
    for kind in ("plain", "diagnostic"):
        for repetition in ((1, 2) if kind == "plain" else (1,)):
            scenario_order = scenarios if repetition == 1 else list(reversed(scenarios))
            variant_order = VARIANTS if repetition == 1 else tuple(reversed(VARIANTS))
            for scenario in scenario_order:
                for variant in variant_order:
                    cases.append(dict(case=f"{kind}_{scenario}_{variant}_r{repetition}",
                        kind=kind, scenario=scenario, variant=variant, repetition=repetition,
                        queries=1000, probes=GRID, **budgets[variant]))
    return cases


def read_budgets(config):
    result = {}
    for variant in VARIANTS:
        section = config[f"Variant.{variant}"]
        base, extra = section.getint("MaxCheck"), section.getint("PostingAdditionalMaxCheck")
        enabled = section.getboolean("EnablePostingNavigation")
        require(base in (2048, 4096) and extra >= 0, f"Invalid native budget: {variant}")
        result[variant] = dict(max_check=base, posting_enabled=enabled,
            posting_additional_max_check=extra,
            posting_anchor_count=config["SearchSSDIndex"].getint("PostingAnchorCount"),
            total_max_check=base + extra)
    require(result["graph"]["max_check"] == result["postgraph_shared"]["max_check"] ==
            result["postgraph_extra"]["max_check"] and
            result["graph_total"]["max_check"] == result["postgraph_extra"]["total_max_check"],
            "Graph and same-total budget controls do not match")
    require(not result["graph"]["posting_enabled"] and not result["graph_total"]["posting_enabled"] and
            result["postgraph_shared"]["posting_enabled"] and result["postgraph_extra"]["posting_enabled"],
            "Posting controls differ from declared variants")
    require(all(result[v]["posting_additional_max_check"] == 0
                for v in ("graph", "graph_total", "postgraph_shared")) and
            result["postgraph_extra"]["posting_additional_max_check"] > 0,
            "Shared/extra controls are not distinct")
    return result


def prepare(path):
    config = read_ini(path)
    plan = config["Campaign"]
    output = Path(plan["OutputDirectory"])
    require(not output.exists(), "Campaign output already exists; preserve registered campaigns")
    workload_path = Path(plan["Workloads"])
    workload = json.loads(workload_path.read_text())
    require(workload["query_count"] == 1000 and workload["topk"] == 10 and
            workload["value_type"] == "UInt8", "Wrong prepared cohort")
    scenarios = workload["scenarios"]
    require(scenarios == [s.strip() for s in plan["Scenarios"].split(",")] and
            scenarios[0] == "unfilter" and not set(scenarios) & {"numeric", "extreme_tag"},
            "Requested scenario scope differs")
    require(json.loads(config["SearchSweep"]["NProbe"]) == GRID and plan.getint("Repeats") == 2,
            "Expected established ascending grid and reversed pair")
    require(config["SearchSSDIndex"].getint("NumberOfThreads") == 1 and
            config["SearchSSDIndex"].getint("ResultNum") == 10 and
            config["SearchSSDIndex"].getint("SearchPostingPageLimit") == 3 and
            config["SearchSSDIndex"].getint("PostingAnchorCount") == 8 and
            config["SearchSSDIndex"].getint("HashTableExponent") == 4 and
            config["SearchSSDIndex"].getfloat("MaxDistRatio") == 8 and
            config["SearchSSDIndex"].getboolean("DisableCrossEdges"),
            "Fixed UInt8 search protocol differs")
    require(not any(key.lower() == "postingmincandidates" for section in config
                    for key in config[section]), "Retired posting control")
    validate_identities(workload["protected_large"])
    for source, expected in workload["protected"].items():
        require(sha(source) == expected, f"Prepared input changed: {source}")
    index = Path(plan["Index"])
    completion = json.loads(Path(plan["IndexCompletion"]).read_text())
    require(completion["state"] == "complete" and completion["head_count"] == 120040156 and
            completion["native_conversion"]["metadata_version"] == 9 and
            completion["native_conversion"]["head_stride"] == 176, "Unexpected native index completion")
    old_registration = json.loads(Path(plan["CorrectedRuntimeRegistration"]).read_text())
    runtime_evidence = json.loads(Path(plan["RuntimeEvidence"]).read_text())
    linked = json.loads(Path(plan["RuntimeLinkInputs"]).read_text())
    parent_validation = json.loads(Path(plan["RuntimeValidation"]).read_text())
    require(runtime_evidence["status"] == "complete_harness_only" and
            runtime_evidence["exact_ids_distances_ssd_graph_traversal"] and
            parent_validation["passed"], "Typed batch runtime validation is incomplete")
    require(sha(HERE / "Bench.cpp") == runtime_evidence["bench_source_sha256"],
            "Harness source differs from validated frozen client")
    core_sources = {}
    for name in CURRENT_SOURCES:
        source = ROOT / name
        expected = old_registration["protected"][str(source)]
        require(sha(source) == expected, f"Search core differs from corrected runtime: {name}")
        core_sources[name] = expected
    output.mkdir(parents=True)
    controls, runtime, snapshot = (output / name for name in ("configs", "runtime", "source"))
    for directory in (controls, runtime, snapshot):
        directory.mkdir()
    protected = dict(workload["protected"])
    protected[str(workload_path)] = sha(workload_path)
    for key in ("IndexCompletion", "CorrectedRuntimeRegistration", "RuntimeEvidence",
                "RuntimeLinkInputs", "RuntimeValidation"):
        protected[plan[key]] = sha(plan[key])
    binaries = {}
    for key, name, mode in (("Binary", "nativeBench", "normal"),
                            ("DiagnosticBinary", "nativeBench_diagnostic", "diagnostic")):
        source, destination = Path(plan[key]), runtime / name
        require(str(source) == linked[mode]["binary"] and sha(source) == linked[mode]["binary_sha256"],
                f"Client differs from validated binary: {mode}")
        for library, expected in linked[mode]["frozen_link_inputs"].items():
            require(sha(library) == expected, f"Corrected link input changed: {library}")
        shutil.copy2(source, destination)
        expected = sha(source)
        require(sha(destination) == expected, "Frozen runtime copy differs")
        binaries[key] = dict(path=str(destination), sha256=expected, original_path=str(source))
        protected[str(destination)] = expected
    sources = {ROOT / name for name in CURRENT_SOURCES}
    sources.update(HERE / name for name in
                   ("Bench.cpp", "run_selectivity.py", "selectivity_common.py",
                    "prepare_selectivity.py", "run_postgraph.py", "run_full.py"))
    sources.add(ROOT / "Tools/benchmarks/plot_posting_min_sweep.R")
    source_origins = {}
    for source in sorted(sources):
        destination = snapshot / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        source_origins[str(source)] = sha(source)
        protected[str(destination)] = sha(destination)
        require(protected[str(destination)] == source_origins[str(source)], "Frozen source copy differs")
    shutil.copy2(path, output / "campaign.ini")
    protected[str(output / "campaign.ini")] = sha(output / "campaign.ini")
    budgets = read_budgets(config)
    cases = plan_schedule(scenarios, budgets)
    for case in cases:
        native = configparser.ConfigParser(interpolation=None)
        native.optionxform = str
        native["SearchSSDIndex"] = dict(config["SearchSSDIndex"])
        native["SearchSSDIndex"].update(
            MaxCheck=str(case["max_check"]), EnablePostingNavigation=str(case["posting_enabled"]).lower(),
            PostingAdditionalMaxCheck=str(case["posting_additional_max_check"]))
        native["SearchSweep"] = dict(NProbe=json.dumps(case["probes"]))
        predicate = workload["native_predicates"][case["scenario"]]
        native["Benchmark"] = dict(Index=str(index), Queries=workload["queries"], ValueType="UInt8",
            Predicate=predicate["kind"], PredicateFile=predicate["file"], MaxQueries="1000", Warmup="1000")
        case["config"] = str(controls / f"{case['case']}.ini")
        write_ini(case["config"], native)
        protected[case["config"]] = sha(case["config"])
    for kind in ("plain", "diagnostic"):
        selected = [case for case in cases if case["kind"] == kind]
        native = configparser.ConfigParser(interpolation=None)
        native.optionxform = str
        native["Batch"] = dict(CaseCount=str(len(selected)))
        for position, case in enumerate(selected, start=1):
            case["native_case_id"] = f"Case{position}"
            native[case["native_case_id"]] = dict(
                Config=case["config"], OutputDirectory=str(output / case["case"]))
        batch = controls / f"{kind}-batch.ini"
        write_ini(batch, native)
        protected[str(batch)] = sha(batch)
    index_files = inventory(index)
    require(index_files and (index / "manifest.txt").is_file(), "Missing immutable wrapper index")
    registration = dict(dataset="SIFT1B", corpus_count=workload["corpus_count"],
        query_count=1000, warmup=1000, repetitions=2, nprobe=GRID,
        scenarios=scenarios, scenario_metadata=workload["scenario_metadata"], variants=VARIANTS,
        budget_metadata=budgets, posting_anchor_count=config["SearchSSDIndex"].getint("PostingAnchorCount"),
        budgets={variant: dict(graph_maxcheck=value["max_check"],
                               posting_additional_maxcheck=value["posting_additional_max_check"])
                 for variant, value in budgets.items()},
        schedule=cases, normal_processes=1, diagnostic_processes=1,
        normal_cases=len(scenarios) * len(VARIANTS) * 2, normal_points=len(scenarios) * len(VARIANTS) * 2 * len(GRID),
        diagnostic_points=len(scenarios) * len(VARIANTS) * len(GRID),
        cpu_node=plan.getint("CPUNode"), memory_node=plan.getint("MemoryNode"),
        binaries=binaries, protected=protected, protected_large=workload["protected_large"],
        index=str(index), index_files=index_files, workload=str(workload_path),
        counter_columns=COUNTERS, navigation_schema_version=SCHEMA_VERSION,
        source_origins=source_origins, corrected_core_sources=core_sources,
        runtime_link_inputs=linked, runtime_evidence=plan["RuntimeEvidence"],
        parent_runtime_validation=plan["RuntimeValidation"],
        search_settings=dict(value_type="UInt8", query_threads=1, posting_page_limit=3,
                             disable_cross_edges=True, max_dist_ratio=8, hash_table_exponent=4),
        timing="One native load per instrumentation class;1000 warmup+1000 measured+1000 replay per case/nprobe",
        batch_scope="One manager per instrumentation class; full warmup/measurement/replay for every point",
        workspace_history=dict(initial_case="unfilter/graph2048/nprobe16",
            fresh_batch_parity="IDs,distances,SSD work,graph heads and traversal; not all raw memory counters",
            difference="Native hash-clear capacity retains manager workspace history after budget changes",
            treatment="Retain raw counters; no core reset/hack; no fresh-process timing equivalence claim"),
        acceptance_targets=json.loads(plan["RecallTargets"]),
        protection_scope="Sealed code/config/query/truth/binaries plus complete index and large-input identities; no SSD hash scan",
        caveats=["Same-project H1-only control, not pristine Microsoft SPTAG",
                 "Unfiltered upstream soft budget; filtered graph hard checked-leaf cap; complete auxiliary rows can overshoot",
                 "Upper routing work is separately measured, not bounded by the nominal checked-leaf sum",
                 "Mixed DNF is separate from the categorical0.1-percent boundary",
                 "Two reversed repetitions; ranges are not confidence intervals",
                 "Load-once workspace history affects native hash-clear bytes; not a fresh-process allocation comparison",
                 "No historic timings or16-query smoke results substituted; no interpolated QPS"])
    save(output / "registration.json", registration)
    save(output / "status.json", dict(state="prepared", updated_at=stamp(), normal_points=0, diagnostic_points=0))
    event(output, "prepared", scenarios=scenarios, normal_points=registration["normal_points"],
          diagnostic_points=registration["diagnostic_points"])


def load_registered(path):
    plan = read_ini(path)["Campaign"]
    output = Path(plan["OutputDirectory"])
    registration = json.loads((output / "registration.json").read_text())
    require(sha(path) == registration["protected"][str(output / "campaign.ini")],
            "Authoritative campaign INI changed")
    for source, expected in registration["protected"].items():
        require(sha(source) == expected, f"Registered input changed: {source}")
    validate_identities(registration["protected_large"])
    require(inventory(registration["index"]) == registration["index_files"], "Immutable index identity changed")
    return plan, output, registration


def validate_point(output, case, row, workload, attributes, diagnostic):
    require(row["nprobe"] in case["probes"] and row["queries"] == case["queries"] and
            row["diagnostic"] == diagnostic and row["navigation_schema_version"] == SCHEMA_VERSION and
            row["navigation_columns"] == len(COUNTERS), "Unexpected native cohort/schema")
    require(row["mode"] == ("posting" if case["posting_enabled"] else "graph"), "Native posting switch differs")
    require(row["value_type"] == "UInt8" and row["search_posting_page_limit"] == 3,
            "Native type/page protocol differs")
    require(row["event"] == "point" and row["warmup_queries"] == row["measured_queries"] ==
            row["replay_queries"] == case["queries"], "Incomplete native query windows")
    require(row["case_id"] == case["native_case_id"] and
            Path(row["config"]).resolve() == Path(case["config"]).resolve() and
            Path(row["output_directory"]).resolve() == (output / case["case"]).resolve(),
            "Native case identity differs from registration")
    for key in ("max_check", "posting_anchor_count", "posting_additional_max_check"):
        require(row[key] == case[key], f"Native budget differs: {key}")
    require(math.isfinite(row["qps"]) and row["qps"] > 0 and
            math.isclose(row["qps"] * row["mean_ms"], 1000, rel_tol=1e-8), "Invalid native timing")
    folder = output / case["case"] / f"nprobe_{row['nprobe']}"
    ids = np.fromfile(folder / "ids.i32", dtype="<i4")
    require(ids.size == case["queries"] * 10 and np.all(ids >= -1), "Invalid native result payload")
    latency = np.fromfile(folder / "latency_us.f64", dtype="<f8")
    require(latency.shape == (case["queries"],) and np.all(np.isfinite(latency)) and np.all(latency > 0),
            "Invalid per-query latency payload")
    row.update(quality(folder, workload["truth"][case["scenario"]],
                       SelectedPredicate(attributes, workload["predicates"][case["scenario"]]), case["queries"]))
    row.update(case)
    row["timing_accepted"] = not diagnostic
    row["latency_payload_sha256"] = sha(folder / "latency_us.f64")
    if diagnostic:
        validate_navigation(output, row)
        row["diagnostic_qps_not_for_throughput"] = row.pop("qps")
        data = np.fromfile(folder / "navigation.u64", dtype="<u8").reshape(case["queries"], len(COUNTERS))
        if case["scenario"] != "unfilter":
            require(np.all(data[:, COUNTERS.index("graph_checked_leaves")] <= case["max_check"]),
                    "Filtered graph exceeded its hard checked-leaf budget")
    return row


def native_identity(wrapper_pid, binary):
    children = Path(f"/proc/{wrapper_pid}/task/{wrapper_pid}/children")
    if not children.exists():
        return None
    for text in children.read_text().split():
        pid = int(text)
        try:
            if Path(f"/proc/{pid}/exe").resolve() != Path(binary).resolve():
                continue
            stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        except FileNotFoundError:
            continue
        return dict(pid=pid, start_ticks=int(stat[19]))
    return None


def terminate_owned(process, native):
    if native is not None:
        path = Path(f"/proc/{native['pid']}/stat")
        if path.exists():
            fields = path.read_text().rsplit(")", 1)[1].split()
            require(int(fields[19]) == native["start_ticks"], "Native PID identity changed; refusing signal")
            os.kill(native["pid"], signal.SIGTERM)
    if process.poll() is None:
        process.terminate()
    process.wait()


def run(path, kind):
    require(not [key for key in os.environ if key.startswith(("SPTAG_", "SPANN_", "OMP_")) or key == "LD_PRELOAD"],
            "Search environment overrides forbidden")
    _, output, registration = load_registered(path)
    if kind == "diagnostic":
        require((output / "normal-completion.json").exists(), "Finish ordinary curves before diagnostics")
    cases = [case for case in registration["schedule"] if case["kind"] == kind]
    require(not (output / f"{kind}-native.log").exists() and
            not any((output / case["case"]).exists() for case in cases),
            "Existing or partial native batch retained; never silently retry/discard timings")
    workload = json.loads(Path(registration["workload"]).read_text())
    attributes = np.load(workload["attributes"], mmap_mode="r", allow_pickle=False)
    binary = registration["binaries"]["DiagnosticBinary" if kind == "diagnostic" else "Binary"]["path"]
    resources = output / f"{kind}-resources.txt"
    command = ["/usr/bin/time", "-v", "-o", str(resources), "numactl",
               f"--cpunodebind={registration['cpu_node']}", f"--membind={registration['memory_node']}",
               sys.executable, "-B", str(output / "source" / HERE.relative_to(ROOT) / "selectivity_common.py"),
               str(output), binary, str(output / "configs" / f"{kind}-batch.ini")]
    rows, native, peak, last_heartbeat = [], None, 0, 0.0
    log_path = output / f"{kind}-native.log"
    started = time.monotonic()
    event(output, "batch_started", phase=kind, command=command)
    with log_path.open("x") as log:
        process = subprocess.Popen(command, cwd=output, stdout=log, stderr=subprocess.STDOUT)
        with log_path.open() as reader:
            try:
                while True:
                    if native is None:
                        native = native_identity(process.pid, binary)
                    position = reader.tell()
                    line = reader.readline()
                    if line and not line.endswith("\n"):
                        reader.seek(position)
                        line = ""
                    if line:
                        if line.startswith("{"):
                            payload = json.loads(line)
                            if {"qps", "queries", "mode", "nprobe"} <= payload.keys():
                                index = len(rows)
                                require(index < len(cases) * len(GRID), "Unexpected extra native point")
                                case = cases[index // len(GRID)]
                                require(payload["nprobe"] == GRID[index % len(GRID)],
                                        "Native batch point order differs from registration")
                                rows.append(validate_point(output, case, payload, workload, attributes,
                                                           kind == "diagnostic"))
                                validate_pairs(output, kind, rows)
                                save(output / f"{kind}-results.json", rows)
                                if len(rows) % len(GRID) == 0:
                                    save(output / case["case"] / "validated.json", rows[-len(GRID):])
                                event(output, "point_complete", phase=kind, case=case["case"],
                                      nprobe=payload["nprobe"], points=len(rows), recall=payload["recall"])
                                last_heartbeat = 0
                    elif process.poll() is not None:
                        break
                    now = time.monotonic()
                    if now - last_heartbeat >= 15:
                        validate_identities(registration["protected_large"])
                        require(inventory(registration["index"]) == registration["index_files"],
                                "Immutable index changed during native batch")
                        resource_status = {}
                        if native is not None:
                            proc_status = Path(f"/proc/{native['pid']}/status")
                            if proc_status.exists():
                                for value in proc_status.read_text().splitlines():
                                    if value.startswith(("VmRSS:", "VmHWM:", "Threads:", "Cpus_allowed_list:", "Mems_allowed_list:")):
                                        key, data = value.split(":", 1)
                                        resource_status[key] = data.strip()
                                peak = max(peak, int(resource_status.get("VmHWM", "0 kB").split()[0]) * 1024)
                        next_case = cases[min(len(cases) - 1, len(rows) // len(GRID))]
                        save(output / "status.json", dict(state="running", kind=kind, updated_at=stamp(),
                            controller_pid=os.getpid(), wrapper_pid=process.pid, native=native,
                            points_complete=len(rows), points_total=len(cases) * len(GRID),
                            active_case=next_case["case"], elapsed_seconds=now - started,
                            sampled_peak_rss_bytes=peak, native_status=resource_status))
                        last_heartbeat = now
                    if not line:
                        time.sleep(.5)
                require(process.returncode == 0, f"Native batch exited{process.returncode}: {log_path}")
            except BaseException:
                terminate_owned(process, native)
                raise
    require(len(rows) == len(cases) * len(GRID), "Incomplete native batch")
    log_text = log_path.read_text()
    require("numeric lanes=1" in log_text and "conservative unknown" not in log_text,
            "Authenticated numeric routing metadata unavailable")
    peak_lines = [line for line in resources.read_text().splitlines()
                  if "Maximum resident set size (kbytes):" in line]
    require(len(peak_lines) == 1, "Missing post-exit peak RSS")
    external_peak = int(peak_lines[0].split(":")[-1]) * 1024
    save(output / f"{kind}-stage.json", dict(state="complete", finished_at=stamp(), returncode=0,
        command=command, cases=len(cases), points=len(rows), elapsed_seconds=time.monotonic() - started,
        sampled_peak_rss_bytes=peak, endprocess_peak_rss_bytes=external_peak))
    load_registered(path)
    save(output / "status.json", dict(state=f"{kind}_complete", updated_at=stamp(), points_complete=len(rows)))
    event(output, "batch_complete", phase=kind, points=len(rows), endprocess_peak_rss_bytes=external_peak)


def publish(path):
    _, output, registration = load_registered(path)
    rows = json.loads((output / "plain-results.json").read_text())
    require(len(rows) == registration["normal_points"], "Incomplete ordinary paired grid")
    validate_pairs(output, "plain", rows)
    points = []
    for scenario in registration["scenarios"]:
        for variant in VARIANTS:
            for probe in GRID:
                pair = [row for row in rows if (row["scenario"], row["variant"], row["nprobe"]) ==
                        (scenario, variant, probe)]
                require(len(pair) == 2 and {r["repetition"] for r in pair} == {1, 2} and
                        pair[0]["payload_hashes"] == pair[1]["payload_hashes"], "Ordinary pair mismatch")
                points.append(dict(scenario=scenario, variant=variant, nprobe=probe,
                    recall=pair[0]["recall"], qps=statistics.mean(r["qps"] for r in pair),
                    qps_min=min(r["qps"] for r in pair), qps_max=max(r["qps"] for r in pair),
                    underfilled_queries=pair[0]["underfilled_queries"], empty_queries=pair[0]["empty_queries"]))
    measured_targets = {}
    for scenario in registration["scenarios"]:
        measured_targets[scenario] = {}
        for target in registration["acceptance_targets"]:
            measured_targets[scenario][str(target)] = {}
            for variant in VARIANTS:
                eligible = [p for p in points if p["scenario"] == scenario and
                            p["variant"] == variant and p["recall"] >= target]
                measured_targets[scenario][str(target)][variant] = (
                    max(eligible, key=lambda p: p["qps"]) if eligible else dict(status="not_reached"))
    save(output / "normal-summary.json", dict(state="complete", points=points,
        best_measured_points_at_recall=measured_targets, interpolation=False,
        performance_verdict="Read measured per-scenario tradeoffs; completeness is not a claim of superiority",
        caveats=registration["caveats"]))
    save(output / "normal-completion.json", dict(state="complete", points=len(rows),
        plain_results_sha256=sha(output / "plain-results.json"), publish_before_diagnostics=True))
    plots = output.parent / "plots_selectivity"
    subprocess.run(["Rscript", str(output / "source/Tools/benchmarks/plot_posting_min_sweep.R"),
                    str(output), str(plots), "--selectivity"], check=True)
    save(output / "status.json", dict(state="ordinary_complete", updated_at=stamp(),
        normal_points=len(rows), plot=str(plots / "recall_qps.png"), diagnostics_pending=True))
    event(output, "ordinary_plots_ready", plot=str(plots / "recall_qps.png"))


def finish(path):
    _, output, registration = load_registered(path)
    require((output / "normal-completion.json").exists(), "Ordinary curves not complete")
    rows = json.loads((output / "diagnostic-results.json").read_text())
    require(len(rows) == registration["diagnostic_points"], "Incomplete separate diagnostics")
    validate_pairs(output, "diagnostic", rows)
    save(output / "completion.json", dict(state="complete", completed_at=stamp(),
        normal_points=registration["normal_points"], diagnostic_points=len(rows),
        ordinary_diagnostic_payload_parity=True, index_unchanged=True,
        registration_sha256=sha(output / "registration.json"),
        plot=str(output.parent / "plots_selectivity/recall_qps.png"),
        performance_verdict="Measured curves and diagnostics, not an automatic performance pass"))
    save(output / "status.json", dict(state="complete", updated_at=stamp(),
        normal_points=registration["normal_points"], diagnostic_points=len(rows)))
    event(output, "completed", normal_points=registration["normal_points"], diagnostic_points=len(rows))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ini", type=Path)
    parser.add_argument("--stage", required=True,
                        choices=("prepare", "plain", "publish", "diagnostic", "finish", "all"))
    args = parser.parse_args()
    try:
        if args.stage == "all":
            for kind in ("plain", "diagnostic"):
                run(args.ini.resolve(), kind)
                if kind == "plain":
                    publish(args.ini.resolve())
            finish(args.ini.resolve())
        elif args.stage in ("plain", "diagnostic"):
            run(args.ini.resolve(), args.stage)
        else:
            globals()[args.stage](args.ini.resolve())
    except Exception as error:
        output = Path(read_ini(args.ini)["Campaign"]["OutputDirectory"])
        if output.exists():
            event(output, "failed", stage=args.stage, error=str(error))
            save(output / "failure.json", dict(state="failed", stage=args.stage, error=str(error), observed_at=stamp()))
            save(output / "status.json", dict(state="failed", stage=args.stage, error=str(error), updated_at=stamp()))
        raise
