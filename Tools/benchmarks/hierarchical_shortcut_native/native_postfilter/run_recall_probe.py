#!/usr/bin/env python3
"""Read-only causal probes of budget, head capacity and posting prefix length."""
import argparse
import configparser
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import numpy as np

from run_full import quality, read_ini, require, sha
from run_postgraph import COUNTERS, SCHEMA_VERSION, read_counters, save, validate_navigation
from run_selectivity import load_registered, native_identity, terminate_owned
from selectivity_common import SelectedPredicate, inventory


def write_ini(path, config):
    with path.open("x") as stream:
        config.write(stream, space_around_delimiters=False)


def prepare(path):
    config = read_ini(path)
    plan = config["Probe"]
    accepted = Path(plan["AcceptedCampaign"])
    _, _, original = load_registered(accepted / "campaign.ini")
    workload = json.loads(Path(original["workload"]).read_text())
    output = Path(plan["OutputDirectory"])
    require(not output.exists(), "Probe output already exists")
    scenarios = [s.strip() for s in plan["Scenarios"].split(",")]
    variants = [v.strip() for v in plan["Variants"].split(",")]
    require(scenarios == ["sel_01pct", "mixed_dnf"] and plan.getint("QueryCount") == 1000,
            "Expected full-cohort sparse and mixed causal probes")
    probes = json.loads(config["SearchSweep"]["NProbe"])
    require(probes == [96, 384, 768] and len(variants) == len(set(variants)), "Unexpected intervention grid")
    output.mkdir(parents=True)
    (output / "configs").mkdir()
    protected = {str(path.resolve()): sha(path)}
    settings = {}
    for variant in variants:
        options = dict(config["SearchSSDIndex"])
        options.update(dict(config[f"Variant.{variant}"]))
        require(set(config[f"Variant.{variant}"]) ==
                {"PostingAdditionalMaxCheck", "SearchPostingPageLimit"}, "Undeclared intervention")
        settings[variant] = options
    cases = []
    for scenario in scenarios:
        for variant in variants:
            key = f"{scenario}_{variant}"
            native = configparser.ConfigParser(interpolation=None)
            native.optionxform = str
            native["SearchSSDIndex"] = settings[variant]
            native["SearchSweep"] = dict(config["SearchSweep"])
            predicate = workload["native_predicates"][scenario]
            native["Benchmark"] = dict(Index=original["index"], Queries=workload["queries"],
                ValueType="UInt8", Predicate=predicate["kind"], PredicateFile=predicate["file"],
                MaxQueries="1000", Warmup="1000")
            filename = output / "configs" / f"{key}.ini"
            write_ini(filename, native)
            protected[str(filename)] = sha(filename)
            cases.append(dict(case=key, config=str(filename), scenario=scenario, variant=variant,
                probes=probes, queries=1000, max_check=int(settings[variant]["MaxCheck"]),
                posting_enabled=True, posting_anchor_count=int(settings[variant]["PostingAnchorCount"]),
                posting_additional_max_check=int(settings[variant]["PostingAdditionalMaxCheck"]),
                posting_page_limit=int(settings[variant]["SearchPostingPageLimit"])))
    for kind in ("diagnostic", "plain"):
        target = output / kind
        target.mkdir()
        batch = configparser.ConfigParser(interpolation=None)
        batch.optionxform = str
        batch["Batch"] = dict(CaseCount=str(len(cases)))
        for i, case in enumerate(cases, start=1):
            batch[f"Case{i}"] = dict(Config=case["config"], OutputDirectory=str(target / case["case"]))
        filename = output / "configs" / f"{kind}-batch.ini"
        write_ini(filename, batch)
        protected[str(filename)] = sha(filename)
    protected[str(Path(__file__).resolve())] = sha(__file__)
    save(output / "registration.json", dict(accepted_campaign=str(accepted), workload=original["workload"],
        index=original["index"], index_files=original["index_files"], binaries=original["binaries"],
        cpu_node=plan.getint("CPUNode"), memory_node=plan.getint("MemoryNode"),
        cases=cases, query_count=1000, points=len(cases) * len(probes), probes=probes,
        protected=protected, initial_index_unchanged=True,
        purpose="Single-variable recall diagnosis, not automatic tuning or a new performance acceptance",
        ordinary_repetitions=1, timing_scope="Diagnostic timings excluded; ordinary run is a single contextual cost measurement",
        hypotheses=["Supplemental budget stops hierarchy before enough matching H1 candidates",
                    "Filled head count is insufficient without a larger candidate set",
                    "SearchPostingPageLimit truncates relevant persisted records"],
        invariants=["No core/index changes", "Same exact1000-query cohort and truth",
                    "Graph MaxCheck2048 and anchors8 unchanged", "Same NUMA3 and one query thread"]))
    save(output / "status.json", dict(state="prepared", points_per_instrumentation=len(cases) * len(probes)))


def check(path):
    plan = read_ini(path)["Probe"]
    output = Path(plan["OutputDirectory"])
    registration = json.loads((output / "registration.json").read_text())
    load_registered(Path(registration["accepted_campaign"]) / "campaign.ini")
    for name, digest in registration["protected"].items():
        require(sha(name) == digest, f"Probe input changed: {name}")
    require(inventory(registration["index"]) == registration["index_files"], "Index identity changed")
    return output, registration


def summarize(path, kind):
    output, registration = check(path)
    workload = json.loads(Path(registration["workload"]).read_text())
    attributes = np.load(workload["attributes"], mmap_mode="r", allow_pickle=False)
    native_rows = [json.loads(line) for line in (output / f"{kind}.log").read_text().splitlines()
                   if line.startswith('{"mode":')]
    expected = [(case, probe) for case in registration["cases"] for probe in case["probes"]]
    require(len(native_rows) == len(expected), "Incomplete native causal-probe grid")
    records = []
    for row, (case, probe) in zip(native_rows, expected):
        require(row["event"] == "point" and row["nprobe"] == probe and row["queries"] == 1000 and
                row["warmup_queries"] == row["measured_queries"] == row["replay_queries"] == 1000 and
                row["diagnostic"] == (kind == "diagnostic") and row["value_type"] == "UInt8" and
                row["navigation_schema_version"] == SCHEMA_VERSION and
                row["navigation_columns"] == len(COUNTERS), "Native probe/window mismatch")
        require(row["config"] == case["config"] and row["output_directory"] ==
                str(output / kind / case["case"]), "Native case path mismatch")
        for key in ("max_check", "posting_anchor_count", "posting_additional_max_check"):
            require(row[key] == case[key], f"Native intervention differs: {key}")
        require(row["search_posting_page_limit"] == case["posting_page_limit"], "Native page limit differs")
        folder = output / kind / case["case"] / f"nprobe_{probe}"
        row.update(quality(folder, workload["truth"][case["scenario"]],
                           SelectedPredicate(attributes, workload["predicates"][case["scenario"]]), 1000))
        row.update(case)
        row["timing_accepted"] = kind == "plain"
        if kind == "diagnostic":
            validate_navigation(output / kind, row)
            counters = read_counters(output / kind, row)
            require(np.all(counters["graph_checked_leaves"] <= case["max_check"]), "Graph budget violation")
            row["head_target_met_queries"] = int(np.count_nonzero(counters["head_after"] >= probe))
            row["supplement_budget_underfilled_queries"] = int(counters["posting_budget_underfilled"].sum())
            row["diagnostic_qps_not_for_throughput"] = row.pop("qps")
        records.append(row)
    if kind == "plain":
        diagnostic = json.loads((output / "diagnostic-results.json").read_text())
        for plain, observed in zip(records, diagnostic):
            require(plain["payload_hashes"] == observed["payload_hashes"], "Probe instrumentation changed results/work")
    save(output / f"{kind}-results.json", records)
    save(output / "status.json", dict(state=f"{kind}_complete", points=len(records)))
    print(json.dumps(dict(completed=kind, points=len(records))), flush=True)


def run(path, kind):
    output, registration = check(path)
    require(not [key for key in os.environ if key.startswith(("SPTAG_", "SPANN_", "OMP_")) or key == "LD_PRELOAD"],
            "Search environment overrides forbidden")
    require(not (output / f"{kind}.log").exists(), "Partial probe batch retained; never overwrite")
    binary = registration["binaries"]["DiagnosticBinary" if kind == "diagnostic" else "Binary"]["path"]
    helper = Path(registration["accepted_campaign"]) / "source/Tools/benchmarks/hierarchical_shortcut_native/native_postfilter/selectivity_common.py"
    command = ["/usr/bin/time", "-v", "-o", str(output / f"{kind}.resources.txt"),
               "numactl", f"--cpunodebind={registration['cpu_node']}",
               f"--membind={registration['memory_node']}", sys.executable, "-B",
               str(helper), str(output), binary, str(output / "configs" / f"{kind}-batch.ini")]
    with (output / f"{kind}.log").open("x") as stream:
        process = subprocess.Popen(command, cwd=output, stdout=stream, stderr=subprocess.STDOUT)
        started = time.monotonic()
        native = None
        try:
            while process.poll() is None:
                if native is None:
                    native = native_identity(process.pid, binary)
                save(output / "status.json", dict(state="running", kind=kind, native=native,
                    wrapper_pid=process.pid, controller_pid=os.getpid(),
                    elapsed_seconds=time.monotonic() - started))
                time.sleep(15)
            require(process.returncode == 0, f"Native probe failed with exit{process.returncode}")
        except BaseException:
            terminate_owned(process, native)
            raise
    summarize(path, kind)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ini", type=Path)
    parser.add_argument("--stage", choices=("prepare", "diagnostic", "plain"), required=True)
    args = parser.parse_args()
    try:
        if args.stage == "prepare":
            prepare(args.ini.resolve())
        else:
            run(args.ini.resolve(), args.stage)
    except Exception as error:
        output = Path(read_ini(args.ini)["Probe"]["OutputDirectory"])
        if output.exists():
            save(output / "failure.json", dict(state="failed", stage=args.stage, error=str(error)))
            save(output / "status.json", dict(state="failed", stage=args.stage, error=str(error)))
        raise
