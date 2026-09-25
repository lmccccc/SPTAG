#!/usr/bin/env python3
"""Attribute existing native phase logs; never publish their QPS as throughput."""
import argparse
import configparser
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import numpy as np

from run_full import quality, read_ini, require, sha
from run_postgraph import save
from run_selectivity import load_registered, native_identity, terminate_owned
from selectivity_common import SelectedPredicate
from run_recall_probe import write_ini


def measured_phases(rows, count, nprobe):
    require(len(rows) == count * 3, "Phase rows must cover full warmup,measurement,replay")
    require(all(int(row["nprobe"]) == nprobe for row in rows), "Phase nprobe order differs")
    selected = rows[count:count * 2]
    fields = ("bkt", "pq", "graphOther", "post", "io", "scan", "postOther", "total")
    result = {field: np.asarray([row[field] for row in selected], dtype=float) for field in fields}
    require(all(np.all(np.isfinite(values)) for values in result.values()), "Non-finite phase timer")
    require(np.all(np.abs(result["bkt"] + result["pq"] + result["graphOther"] +
                          result["post"] - result["total"]) <= .004),
            "Rounded native phase totals disagree")
    require(np.all(np.abs(result["io"] + result["scan"] + result["postOther"] -
                          result["post"]) <= .004), "Rounded posting phase totals disagree")
    result["ram_navigation"] = result["bkt"] + result["pq"] + result["graphOther"]
    return dict(measured_rows=count, all_phase_rows=len(rows),
                mean_ms={name: float(values.mean()) for name, values in result.items()},
                p50_ms={name: float(np.median(values)) for name, values in result.items()},
                p95_ms={name: float(np.percentile(values, 95)) for name, values in result.items()})


def prepare(path):
    config = read_ini(path)
    plan = config["PhaseProbe"]
    output = Path(plan["OutputDirectory"])
    require(not output.exists(), "Phase output already exists")
    accepted = Path(plan["AcceptedCampaign"])
    _, _, original = load_registered(accepted / "campaign.ini")
    evidence = json.loads(Path(plan["RuntimeEvidence"]).read_text())
    require(evidence["status"] == "complete_harness_only" and not evidence["core_rebuilt"] and
            sha(plan["Binary"]) == evidence["binary_sha256"], "Phase client provenance differs")
    output.mkdir()
    (output / "configs").mkdir()
    protected = {str(path): sha(path), plan["Binary"]: sha(plan["Binary"]),
                 plan["RuntimeEvidence"]: sha(plan["RuntimeEvidence"]),
                 str(Path(__file__).resolve()): sha(__file__)}
    cases = []
    for case_id in [value.strip() for value in plan["Cases"].split(",")]:
        section = config[f"Case.{case_id}"]
        source = Path(section["Config"])
        native = read_ini(source)
        require(native["Benchmark"]["MaxQueries"] == native["Benchmark"]["Warmup"] == "1000",
                "Full1000-query windows required")
        native["Benchmark"]["PhaseTiming"] = "true"
        native["SearchSSDIndex"]["LogPhaseTime"] = "true"
        native["SearchSweep"]["NProbe"] = section["NProbe"]
        destination = output / "configs" / f"{case_id}.ini"
        write_ini(destination, native)
        protected[str(source)] = sha(source)
        protected[str(destination)] = sha(destination)
        cases.append(dict(case=case_id, config=str(destination), scenario=section["Scenario"],
            variant=section["Variant"], probes=json.loads(section["NProbe"])))
    batch = configparser.ConfigParser(interpolation=None)
    batch.optionxform = str
    batch["Batch"] = dict(CaseCount=str(len(cases)))
    for i, case in enumerate(cases, start=1):
        batch[f"Case{i}"] = dict(Config=case["config"], OutputDirectory=str(output / case["case"]))
    filename = output / "batch.ini"
    write_ini(filename, batch)
    protected[str(filename)] = sha(filename)
    save(output / "registration.json", dict(cases=cases, binary=plan["Binary"],
        accepted_campaign=str(accepted), budget_probe=plan["BudgetProbe"], workload=original["workload"],
        cpu_node=plan.getint("CPUNode"), memory_node=plan.getint("MemoryNode"),
        protected=protected, phase_timing=True, timing_accepted=False, query_count=1000,
        attribution="Per point:warmup[0:1000],measured[1000:2000],replay[2000:3000]",
        scope="Native rounded-ms phase logs; RAM navigation includes H1 and supplementary postings; final path includes buffered SSD IO and scans",
        exclusions="Never substitute logged QPS for ordinary throughput; before-head setup and logging are outside the native phase sum"))
    save(output / "status.json", dict(state="prepared", points=sum(len(c["probes"]) for c in cases)))


def check(path, allow_updated_analyzer=False):
    output = Path(read_ini(path)["PhaseProbe"]["OutputDirectory"])
    registration = json.loads((output / "registration.json").read_text())
    load_registered(Path(registration["accepted_campaign"]) / "campaign.ini")
    for source, expected in registration["protected"].items():
        actual = sha(source)
        if actual != expected and allow_updated_analyzer and Path(source) == Path(__file__).resolve():
            original = output / "original_analyzer" / Path(source).name
            require(original.is_file() and sha(original) == expected,
                    "Original analyzer must be preserved before analysis-only recovery")
            save(output / "analysis-recovery.json", dict(
                scope="Postprocessing only; no native measurement rerun or parameter change",
                original_analyzer=str(original), original_sha256=expected,
                corrected_analyzer=source, corrected_sha256=actual))
        else:
            require(actual == expected, f"Registered phase input changed: {source}")
    return output, registration


def summarize(path, allow_updated_analyzer=False):
    output, registration = check(path, allow_updated_analyzer)
    workload = json.loads(Path(registration["workload"]).read_text())
    attributes = np.load(workload["attributes"], mmap_mode="r", allow_pickle=False)
    references = json.loads((Path(registration["accepted_campaign"]) / "plain-results.json").read_text())
    references += json.loads((Path(registration["budget_probe"]) / "plain-results.json").read_text())
    cases = {case["config"]: case for case in registration["cases"]}
    group, records = [], []
    for line in (output / "native.log").read_text().splitlines():
        if "PhaseTime:" in line:
            group.append(dict(re.findall(r"([A-Za-z0-9]+)=([-+0-9.eE]+)", line.split("PhaseTime:", 1)[1])))
        elif line.startswith('{"mode":'):
            row = json.loads(line)
            require(row["phase_timing"] and not row["diagnostic"] and
                    row["warmup_queries"] == row["measured_queries"] == row["replay_queries"] == 1000,
                    "Wrong phase mode or windows")
            case = cases[row["config"]]
            probe = row["nprobe"]
            attribution = measured_phases(group, 1000, probe)
            group = []
            folder = Path(row["output_directory"]) / f"nprobe_{probe}"
            checked = quality(folder, workload["truth"][case["scenario"]],
                              SelectedPredicate(attributes, workload["predicates"][case["scenario"]]), 1000)
            matched = [reference for reference in references
                       if (reference["scenario"], reference["variant"], reference["nprobe"]) ==
                       (case["scenario"], case["variant"], probe)]
            require(matched and all(value["payload_hashes"] == checked["payload_hashes"] for value in matched),
                    f"Native phase instrumentation changed results/work: {case['case']}")
            records.append(dict(case=case["case"], scenario=case["scenario"], variant=case["variant"],
                nprobe=probe, phase_timing=True, timing_accepted=False, ordinary_payload_parity=True,
                ordinary_reference_qps=[value["qps"] for value in matched], **checked, **attribution))
    require(not group and len(records) == sum(len(case["probes"]) for case in cases.values()),
            "Incomplete native phase grid")
    save(output / "results.json", records)
    save(output / "status.json", dict(state="complete", points=len(records), timing_accepted=False))
    print(json.dumps(dict(state="complete", points=len(records), phase_rows=len(records) * 3000)), flush=True)


def run(path):
    output, registration = check(path)
    require((Path(registration["budget_probe"]) / "plain-results.json").exists(),
            "Complete ordinary budget probes before starting competing phase diagnostics")
    require(not [key for key in os.environ if key.startswith(("SPTAG_", "SPANN_", "OMP_")) or key == "LD_PRELOAD"],
            "Search environment overrides forbidden")
    require(not (output / "native.log").exists(), "Existing phase run retained")
    helper = Path(registration["accepted_campaign"]) / "source/Tools/benchmarks/hierarchical_shortcut_native/native_postfilter/selectivity_common.py"
    command = ["/usr/bin/time", "-v", "-o", str(output / "resources.txt"), "numactl",
               f"--cpunodebind={registration['cpu_node']}", f"--membind={registration['memory_node']}",
               sys.executable, "-B", str(helper), str(output), registration["binary"], str(output / "batch.ini")]
    with (output / "native.log").open("x") as stream:
        process = subprocess.Popen(command, cwd=output, stdout=stream, stderr=subprocess.STDOUT)
        started = time.monotonic()
        native = None
        try:
            while process.poll() is None:
                if native is None:
                    native = native_identity(process.pid, registration["binary"])
                save(output / "status.json", dict(state="running", native=native, controller_pid=os.getpid(),
                    wrapper_pid=process.pid, elapsed_seconds=time.monotonic() - started))
                time.sleep(15)
            require(process.returncode == 0, f"Native phase process failed:{process.returncode}")
        except BaseException:
            terminate_owned(process, native)
            raise
    summarize(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ini", type=Path)
    parser.add_argument("--stage", choices=("prepare", "run", "summarize"), required=True)
    parser.add_argument("--allow-analyzer-update", action="store_true")
    args = parser.parse_args()
    try:
        require(not args.allow_analyzer_update or args.stage == "summarize",
                "An analyzer update cannot authorize new or modified native measurements")
        if args.stage == "summarize":
            summarize(args.ini.resolve(), args.allow_analyzer_update)
        else:
            globals()[args.stage](args.ini.resolve())
    except Exception as error:
        output = Path(read_ini(args.ini)["PhaseProbe"]["OutputDirectory"])
        if output.exists():
            save(output / "failure.json", dict(state="failed", stage=args.stage, error=str(error)))
            save(output / "status.json", dict(state="failed", stage=args.stage, error=str(error)))
        raise
