#!/usr/bin/env python3
"""Fixed native diagnostics for placement, huge pages and hierarchy phase costs."""

import argparse
import configparser
import ctypes
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import resource
import shutil
import statistics
import subprocess
import sys
import time
import uuid

from official_benchmark_config import (
    Config, identity, load_config, reject_environment_overrides, require,
    sha256_file, verify_identities, write_json,
)
from run_sift1b_official import build_spann_job, check_spann, copy_config_snapshot, parse_results
from sift1b_official_inputs import validate_inputs


HERE = Path(__file__).resolve().parent
DEFAULT_PLAN = HERE / "configs/sift1b_spann_memory/diagnostic.ini"


def placement(plan, case):
    section = plan.section("Case." + case)
    require(set(section) == {"cpupolicy", "cpuids", "memorypolicy", "memorynodes", "thp"},
            "Unknown or missing placement key")
    require(section["CPUPolicy"] in ("node", "cpu") and section["MemoryPolicy"] in ("bind", "interleave"),
            "Unsupported diagnostic placement policy")
    require(section["THP"] in ("default", "disabled"), "Unsupported process-local THP policy")
    for key in ("CPUIds", "MemoryNodes"):
        require(re.fullmatch(r"\d+(,\d+)*", section[key]) is not None, f"Invalid diagnostic {key}")
    cpu = "--cpunodebind=" if section["CPUPolicy"] == "node" else "--physcpubind="
    memory = "--membind=" if section["MemoryPolicy"] == "bind" else "--interleave="
    return ["numactl", cpu + section["CPUIds"], memory + section["MemoryNodes"]]


def load_plan(path):
    plan = Config(path)
    required = {"benchmarkconfig", "searchconfig", "scenario", "cases", "sampleseconds"}
    optional = {"referencesearchconfig", "benchmarkbinary", "benchmarksha256"}
    section = plan.section("Diagnostic")
    require(required <= set(section) <= required | optional,
            "Unknown or missing diagnostic control")
    require(("BenchmarkBinary" in section) == ("BenchmarkSHA256" in section),
            "An instrumented binary requires both a fixed path and SHA256")
    if "BenchmarkSHA256" in section:
        require(re.fullmatch(r"[0-9a-f]{64}", section["BenchmarkSHA256"]) is not None,
                "Require a fixed instrumented-binary SHA256")
    cases = plan.csv("Diagnostic", "Cases")
    require(set(plan.parser.sections()) == {"Diagnostic", *("Case." + name for name in cases)},
            "Unexpected diagnostic case sections")
    require(plan.section("Diagnostic")["Scenario"] == "unfilter"
            and plan.section("Diagnostic").getfloat("SampleSeconds") > 0,
            "This diagnostic is for unfiltered search with bounded resource sampling")
    config = load_config(plan.path_value("Diagnostic", "BenchmarkConfig"))
    diagnostic = configparser.ConfigParser(interpolation=None)
    diagnostic.read(plan.path_value("Diagnostic", "SearchConfig"))
    reference_path = (plan.path_value("Diagnostic", "ReferenceSearchConfig")
                      if "ReferenceSearchConfig" in plan.section("Diagnostic")
                      else config.path_value("SPANN", "InitialSearchConfig"))
    require(reference_path.resolve() in {
        config.relative_path(value).resolve() for value in config.csv("SPANN", "SearchConfigs")},
        "The diagnostic reference must be a canonical native search file")
    reference = configparser.ConfigParser(interpolation=None)
    reference.read(reference_path)
    require(diagnostic.sections() == ["SearchSSDIndex"], "Require one fixed native search section")
    parameters = dict(diagnostic["SearchSSDIndex"])
    require(parameters.pop("logpathstats", None) in (None, "true"),
            "Hierarchy logging may only be explicitly enabled")
    require(parameters.pop("logphasetime", None) == "true"
            and parameters == dict(reference["SearchSSDIndex"]),
            "Diagnostics must preserve the declared native search budget; only diagnostic logging may differ")
    for case in cases:
        placement(plan, case)
    return plan, config


def diagnostic_binary(plan, config):
    if "BenchmarkBinary" in plan.section("Diagnostic"):
        return (plan.path_value("Diagnostic", "BenchmarkBinary"),
                plan.section("Diagnostic")["BenchmarkSHA256"])
    return config.path_value("SPANN", "BenchmarkBinary"), config.section("SPANN")["BenchmarkSHA256"]


def make_job(plan, config, inputs, case):
    scenario = next(item for item in inputs["scenarios"]
                    if item["name"] == plan.section("Diagnostic")["Scenario"])
    job = build_spann_job(config, inputs, scenario, diagnostic_binary(plan, config)[0],
                          [plan.path_value("Diagnostic", "SearchConfig")])
    job["command"] = placement(plan, case) + job["command"][len(config.affinity()):]
    job["case"] = case
    return job


def set_thp_policy(policy):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    libc.prctl.restype = ctypes.c_int
    disabled = int(policy == "disabled")
    require(policy in ("default", "disabled"), "Invalid process-local THP policy")
    if libc.prctl(41, disabled, 0, 0, 0) != 0:  # PR_SET_THP_DISABLE survives exec.
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    require(libc.prctl(42, 0, 0, 0, 0) == disabled, "Process-local THP control did not take effect")


def process_snapshot(pid):
    root = Path("/proc") / str(pid)
    status = {}
    for line in (root / "status").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in ("Name", "State", "Cpus_allowed_list", "Mems_allowed_list", "THP_enabled",
                   "VmRSS", "VmSize", "voluntary_ctxt_switches", "nonvoluntary_ctxt_switches"):
            status[key] = value.strip()
    stat = (root / "stat").read_text().rsplit(")", 1)[1].split()
    memory = {match[1]: int(match[2]) for match in re.finditer(
        r"^(\w+):\s+(\d+)\s+kB", (root / "smaps_rollup").read_text(), re.MULTILINE)}
    numa = {}
    for node, pages in re.findall(r"\bN(\d+)=(\d+)", (root / "numa_maps").read_text()):
        numa[node] = numa.get(node, 0) + int(pages)
    io = {}
    for line in (root / "io").read_text().splitlines():
        key, value = line.split(":", 1)
        io[key] = int(value)
    return {"time": datetime.now(timezone.utc).isoformat(), "pid": pid, "status": status,
            "minor_faults": int(stat[7]), "major_faults": int(stat[9]),
            "user_ticks": int(stat[11]), "system_ticks": int(stat[12]), "cpu": int(stat[36]),
            "memory_kb": memory, "numa_page_counts": numa, "io": io}


def run_sampled_process(command, cwd, prefix, sample_seconds, on_start=None):
    stdout_path = Path(str(prefix) + ".stdout.log")
    stderr_path = Path(str(prefix) + ".stderr.log")
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr, (
        Path(str(prefix) + ".resources.jsonl")).open("x") as samples:
        child = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
        try:
            if on_start is not None:
                on_start(child.pid)
            while child.poll() is None:
                try:
                    sample = process_snapshot(child.pid)
                except (FileNotFoundError, ProcessLookupError) as error:
                    # procfs can lose the mm before the owned child becomes waitable.
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        raise error
                    break
                sample["stdout_bytes"] = stdout_path.stat().st_size
                sample["stderr_bytes"] = stderr_path.stat().st_size
                samples.write(json.dumps(sample) + "\n")
                samples.flush()
                time.sleep(sample_seconds)
            require(child.returncode == 0, f"Native diagnostic failed; see {stderr_path}")
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {key: getattr(after, key) - getattr(before, key)
            for key in ("ru_utime", "ru_stime", "ru_minflt", "ru_majflt", "ru_nvcsw", "ru_nivcsw",
                        "ru_inblock", "ru_oublock")}


def measured_log_lines(paths, marker, query_count, repeats):
    groups = []
    for path in paths:
        with path.open() as stream:
            rows = [line.split(marker, 1)[1].strip() for line in stream if marker in line]
        if rows:
            groups.append(rows)
    require(len(groups) == 1 and len(groups[0]) == 2 * query_count * repeats,
            f"Missing or ambiguous native {marker} rows")
    rows = groups[0]
    return [row for repeat in range(repeats)
            for row in rows[(2 * repeat + 1) * query_count:(2 * repeat + 2) * query_count]]


def parse_phase_line(line):
    return {key: float(value) for key, value in re.findall(
        r"(\w+)=([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", line)}


def mean_phase_rows(measured):
    require(measured, "Missing measured phase rows")
    require(all(row.keys() == measured[0].keys() for row in measured), "Inconsistent native phase schema")
    return {key: statistics.mean(row[key] for row in measured) for key in measured[0]}


def phase_summary(paths, query_count, repeats):
    return mean_phase_rows([parse_phase_line(line)
                            for line in measured_log_lines(paths, "PhaseTime:", query_count, repeats)])


def parse_hierarchy_line(line):
    levels = {f"H{level}" for level in range(1, 6)}
    common = {"budget", "candidates", "eligible", "retained", "retained_eligible"}
    timings = {"graph_ms", "merge_ms", "tag_ms", "vec_ms", "sort_ms"}
    entries = re.findall(r"\blevel=(H\d+),(\S+)", line)
    require(len(entries) == len(levels) and {level for level, _ in entries} == levels,
            "Require exactly five distinct native hierarchy levels")
    row = {}
    for level, fields in entries:
        pairs = [field.split("=", 1) for field in fields.split(",")]
        values = {key: float(value) for key, value in pairs}
        counters = common | ({"graph_checked"} if level == "H5" else {"assignments", "distances"})
        if level != "H5" and "dedup_hash" in values:
            counters.add("dedup_hash")
        access = {"distance_calls", "graph_rows", "visited_checks", "tree_node_visits"}
        if level == "H5" and access & values.keys():
            counters |= access
        require(len(values) == len(pairs) and set(values) == counters | timings
                and all(math.isfinite(value) and value >= 0 for value in values.values())
                and all(values[key].is_integer() for key in counters),
                "Invalid native hierarchy work/timing schema")
        require("dedup_hash" not in values or values["dedup_hash"] in (0, 1),
                "Invalid native hierarchy deduplication mode")
        row[level] = values
    return row


def mean_hierarchy_rows(measured):
    require(measured, "Missing measured hierarchy rows")
    levels = set(measured[0])
    require(all(set(row) == levels and all(row[level].keys() == measured[0][level].keys()
                                        for level in levels) for row in measured),
            "Inconsistent native hierarchy schema")
    return {level: {key: statistics.mean(row[level][key] for row in measured)
                    for key in measured[0][level]} for level in sorted(levels, reverse=True)}


def hierarchy_summary(paths, query_count, repeats):
    return mean_hierarchy_rows([parse_hierarchy_line(line)
                                for line in measured_log_lines(paths, "HierarchyWork:", query_count, repeats)])


def run(plan, config):
    inputs = validate_inputs(config)
    index_files = check_spann(config)
    binary_path, expected_digest = diagnostic_binary(plan, config)
    binary = identity(binary_path, True)
    require(binary["sha256"] == expected_digest, "Unexpected diagnostic executable")
    output = config.path_value("Benchmark", "OutputDirectory") / (
        "spann_memory_diagnostic_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8])
    output.mkdir()
    (output / "config").mkdir()
    copy_config_snapshot(config, output / "config" / config.path.parent.name)
    profile = output / "config" / plan.path.parent.name
    profile.mkdir()
    for path in (plan.path, plan.path_value("Diagnostic", "SearchConfig")):
        shutil.copy2(path, profile / path.name)
    frozen_plan, frozen_config = load_plan(profile / plan.path.name)
    require(frozen_config.digest() == config.digest(), "Canonical profile changed during diagnostic snapshot")
    plan, config = frozen_plan, frozen_config
    (output / "code").mkdir()
    for name in ("run_spann_memory_diagnostics.py", "run_sift1b_official.py",
                 "official_benchmark_config.py", "sift1b_official_inputs.py"):
        shutil.copy2(HERE / name, output / "code" / name)
    code_records = [identity(path, True) for path in sorted((output / "code").iterdir())]
    records = []
    jobs = [make_job(plan, config, inputs, case) for case in plan.csv("Diagnostic", "Cases")]
    controls = [identity(path, True) for path in
                (plan.path, plan.path_value("Diagnostic", "SearchConfig"), *config.config_files())]
    write_json(output / "manifest.json", {
        "jobs": jobs, "binary": binary, "controls": controls, "code": code_records, "index_files": index_files,
        "inputs": inputs, "thp_enabled": Path("/sys/kernel/mm/transparent_hugepage/enabled").read_text().strip(),
        "thp_defrag": Path("/sys/kernel/mm/transparent_hugepage/defrag").read_text().strip(),
        "purpose": "Diagnostic only: identical search work; no host policy changes or benchmark result replacement",
    })
    print(f"Diagnostic directory: {output}", flush=True)
    try:
        for job in jobs:
            case = job["case"]
            print("Running " + case, flush=True)
            write_json(output / "status.json", {"state": "running", "case": case})
            stdout_path, stderr_path = output / (case + ".stdout.log"), output / (case + ".stderr.log")
            command = [sys.executable, "-B", str(output / "code" / "run_spann_memory_diagnostics.py"),
                       "execute", "--config", str(plan.path), "--case", case]
            usage = run_sampled_process(command, job["cwd"], output / case,
                                        plan.section("Diagnostic").getfloat("SampleSeconds"))
            rows = parse_results(stdout_path, job, inputs["query_count"])
            phase = phase_summary((stdout_path, stderr_path), inputs["query_count"],
                                  config.section("Benchmark").getint("Repeats"))
            record = {
                "case": case, "native_results": rows, "phase_mean": phase,
                "qps_median": statistics.median(row["qps"] for row in rows),
                "recall": [row["recall"] for row in rows],
                "usage": usage,
            }
            search = Config(plan.path_value("Diagnostic", "SearchConfig")).section("SearchSSDIndex")
            if search.getboolean("LogPathStats", fallback=False):
                hierarchy = hierarchy_summary((stdout_path, stderr_path), inputs["query_count"],
                                              config.section("Benchmark").getint("Repeats"))
                for counter, phase_counter in (("distances", "h2Unique"), ("assignments", "h2Assign")):
                    require(math.isclose(sum(hierarchy[f"H{level}"][counter] for level in range(1, 5)),
                                         phase[phase_counter], rel_tol=1e-12, abs_tol=1e-9),
                            "Per-layer work does not match aggregate native counters")
                require(hierarchy["H5"]["graph_checked"] == phase["h2Upper"],
                        "Top-layer checked count does not match aggregate native counters")
                if "BenchmarkBinary" in plan.section("Diagnostic"):
                    require(all(hierarchy["H5"].get(key, 0) > 0 for key in
                                ("distance_calls", "graph_rows", "visited_checks", "tree_node_visits")),
                            "Instrumented H5 access counters are missing or empty")
                record["hierarchy_mean"] = hierarchy
            records.append(record)
            write_json(output / "results.json", records)
            print(f"{case}: {record['qps_median']:.3f} QPS", flush=True)
        verify_identities([binary, *index_files, *controls, *code_records])
        validate_inputs(config)
        reference = records[0]["native_results"][0]["native"]
        ignored = {"search_ini", "qps", "mean_latency_ms"}
        for record in records:
            for row in record["native_results"]:
                require({key: value for key, value in row["native"].items() if key not in ignored} ==
                        {key: value for key, value in reference.items() if key not in ignored},
                        "Diagnostic changed non-timing search results")
        write_json(output / "status.json", {"state": "completed", "cases": len(records)})
    except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        write_json(output / "status.json", {"state": "failed", "error": str(error)})
        raise
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("check-config", "run", "execute"))
    parser.add_argument("--config", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--case")
    arguments = parser.parse_args()
    reject_environment_overrides()
    plan, config = load_plan(arguments.config)
    if arguments.stage == "check-config":
        print(f"Fixed diagnostic: {plan.path}")
    elif arguments.stage == "execute":
        require(arguments.case in plan.csv("Diagnostic", "Cases"), "Select a declared diagnostic case")
        inputs = validate_inputs(config)
        binary_path, expected_digest = diagnostic_binary(plan, config)
        require(sha256_file(binary_path) == expected_digest, "Diagnostic binary changed")
        job = make_job(plan, config, inputs, arguments.case)
        set_thp_policy(plan.section("Case." + arguments.case)["THP"])
        os.execvp(job["command"][0], job["command"])
    else:
        require(arguments.case is None, "Run the complete fixed diagnostic order")
        run(plan, config)


if __name__ == "__main__":
    main()
