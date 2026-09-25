#!/usr/bin/env python3
"""Fixed-file hierarchy kernel/beam experiments in one native index process."""

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import uuid

import numpy as np

from official_benchmark_config import (
    Config, identity, load_config, reject_environment_overrides, require,
    sha256_file, verify_identities, write_json,
)
from run_sift1b_official import build_spann_job, check_spann, copy_config_snapshot, parse_results
from run_spann_memory_diagnostics import (
    mean_hierarchy_rows, mean_phase_rows, measured_log_lines, parse_hierarchy_line,
    parse_phase_line, run_sampled_process, set_thp_policy,
)
from sift1b_official_inputs import validate_inputs


HERE = Path(__file__).resolve().parent
DEFAULT_PLAN = HERE / "configs/sift1b_spann_latency/experiment.ini"


def load_plan(path):
    plan = Config(path)
    section = plan.section("Experiment")
    require(set(section) == {"benchmarkconfig", "binary", "binarysha256", "cmakeprofile",
                             "variants", "sampleseconds"}, "Invalid latency experiment controls")
    require(re.fullmatch(r"[0-9a-f]{64}", section["BinarySHA256"]) is not None
            and section.getfloat("SampleSeconds") > 0, "Invalid binary identity or sampling interval")
    config = load_config(plan.path_value("Experiment", "BenchmarkConfig"))
    names = plan.csv("Experiment", "Variants")
    require(names and len(names) == len(set(names))
            and all(re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in names),
            "Require distinct fixed experiment variants")
    require(set(plan.parser.sections()) == {"Experiment", *("Variant." + name for name in names)},
            "Unexpected latency experiment sections")
    canonical = {config.relative_path(value).resolve() for value in config.csv("SPANN", "SearchConfigs")}
    variants = {}
    files = [plan.path, plan.path_value("Experiment", "CMakeProfile")]
    for name in names:
        entry = plan.section("Variant." + name)
        require(set(entry) == {"searchconfig", "reference", "kind", "baseline"},
                "Invalid variant controls")
        require(entry["Kind"] in ("baseline", "implementation", "routing", "throughput"),
                "Unknown variant kind")
        reference = plan.path_value("Variant." + name, "Reference")
        require(reference.resolve() in canonical, "Reference is not a canonical native search point")
        native_path = plan.path_value("Variant." + name, "SearchConfig")
        require(native_path.is_relative_to(plan.path.parent), "Native variants must be repository-owned siblings")
        native = Config(native_path)
        require(native.parser.sections() == ["SearchSSDIndex"], "Require one native search section")
        parameters = dict(native.section("SearchSSDIndex"))
        profiled = entry["Kind"] != "throughput"
        logging_value = "true" if profiled else "false"
        require(parameters.pop("logphasetime", None) == logging_value
                and parameters.pop("logpathstats", None) == logging_value,
                "Native logging policy must match the declared variant kind")
        prefetch = parameters.pop("hierarchyprefetchmode", None)
        dedup = parameters.pop("hierarchydedupmode", None)
        budgets = parameters.pop("hierarchyroutingbudgets", None)
        require(prefetch in ("Rolling16", "Batch64") and dedup in ("Bitmap", "LocalHash", "Auto"),
                "Invalid fixed implementation mode")
        require(parameters == dict(Config(reference).section("SearchSSDIndex")),
                "Variant changes an undeclared canonical search parameter")
        if entry["Kind"] == "routing":
            require(isinstance(budgets, str) and re.fullmatch(r"[1-9]\d*(,[1-9]\d*){3}", budgets) is not None
                    and all(int(value) <= 2147483647 for value in budgets.split(",")),
                    "Routing variants require four explicit positive parent budgets")
        else:
            require(budgets == "Auto", "Implementation comparisons cannot change parent beams")
        if entry["Kind"] == "baseline":
            require(prefetch == "Rolling16" and dedup == "Bitmap" and entry["Baseline"] == name,
                    "Baseline must use the original kernel modes")
        variants[name] = {"name": name, "path": str(native_path), "L": int(parameters["internalresultnum"]),
                          "kind": entry["Kind"], "baseline": entry["Baseline"],
                          "prefetch": prefetch, "dedup": dedup, "routing": budgets,
                          "profiled": profiled}
        files.append(native_path)
    for variant in variants.values():
        baseline = variants.get(variant["baseline"])
        require(baseline is not None and baseline["kind"] == "baseline" and baseline["L"] == variant["L"],
                "Each variant needs a baseline with the same posting budget")
    require(len(files) == len(set(files)), "Do not reuse an ambiguous native variant file")
    return plan, config, variants, files


def make_job(plan, config, variants, inputs):
    scenario = next(item for item in inputs["scenarios"] if item["name"] == "unfilter")
    job = build_spann_job(config, inputs, scenario, plan.path_value("Experiment", "Binary"),
                          [Path(variant["path"]) for variant in variants.values()])
    job["command"].append("--dump-results")
    by_path = {variant["path"]: name for name, variant in variants.items()}
    for point in job["points"]:
        point["variant"] = by_path[point["value"]]
    return job


def native_work(row):
    return {key: value for key, value in row["native"].items()
            if key not in {"search_ini", "qps", "mean_latency_ms"}}


def per_query_hit_sets(ids, truth, topk):
    return [set(ids[query * topk:(query + 1) * topk]) & expected
            for query, expected in enumerate(truth)]


def summarize_run(config, inputs, variants, job, output):
    count = inputs["query_count"]
    topk = config.section("Benchmark").getint("ResultNum")
    paths = (output / "native.stdout.log", output / "native.stderr.log")
    rows = parse_results(paths[0], job, count)
    profiled_count = sum(variants[point["variant"]]["profiled"] for point in job["points"])
    phase_lines = measured_log_lines(paths, "PhaseTime:", count, profiled_count)
    hierarchy_lines = measured_log_lines(paths, "HierarchyWork:", count, profiled_count)
    groups = {name: [] for name in variants}
    profile_position = 0
    for row in rows:
        ids = row["native"].get("result_ids")
        require(isinstance(ids, list) and len(ids) == count * topk
                and all(type(value) is int and -1 <= value < config.section("Dataset").getint("VectorCount")
                        for value in ids), "Missing or invalid exact native result IDs")
        entry = {"result": row}
        if variants[row["variant"]]["profiled"]:
            span = slice(profile_position * count, (profile_position + 1) * count)
            entry["phase"] = mean_phase_rows([parse_phase_line(line) for line in phase_lines[span]])
            entry["hierarchy"] = mean_hierarchy_rows([
                parse_hierarchy_line(line) for line in hierarchy_lines[span]])
            profile_position += 1
        groups[row["variant"]].append(entry)
    require(profile_position == profiled_count, "Incomplete profiled-point accounting")
    scenario = next(item for item in inputs["scenarios"] if item["name"] == "unfilter")
    truth_array = np.load(scenario["truth_npy"], allow_pickle=False)
    require(truth_array.shape == (count, topk), "Unexpected native groundtruth shape")
    truth = [{int(value) for value in row if value >= 0} for row in truth_array]
    summaries = []
    for name, variant in variants.items():
        group = groups[name]
        require(len(group) == config.section("Benchmark").getint("Repeats"), "Incomplete variant repetitions")
        first = group[0]["result"]
        require(all(native_work(entry["result"]) == native_work(first) for entry in group),
                "Repeated variant searches changed results or work")
        baseline = groups[variant["baseline"]][0]["result"]
        same_work = native_work(first) == native_work(baseline)
        if variant["kind"] != "routing":
            require(same_work, f"Implementation changed native results or work: {name}")
        phase = None
        hierarchy = None
        if variant["profiled"]:
            phase = mean_phase_rows([entry["phase"] for entry in group])
            hierarchy = mean_hierarchy_rows([entry["hierarchy"] for entry in group])
        if variant["profiled"] and variant["kind"] != "routing":
            base_hierarchy = mean_hierarchy_rows([
                entry["hierarchy"] for entry in groups[variant["baseline"]]])
            for level, fields in hierarchy.items():
                for key, value in fields.items():
                    if not key.endswith("_ms") and key != "dedup_hash":
                        require(value == base_hierarchy[level][key], f"Layer work changed: {name}/{level}/{key}")
        base_ids = baseline["native"]["result_ids"]
        ids = first["native"]["result_ids"]
        base_hits = per_query_hit_sets(base_ids, truth, topk)
        hits = per_query_hit_sets(ids, truth, topk)
        require(abs(sum(map(len, hits)) / (count * topk) - first["recall"]) < 1e-8,
                "Native recall does not match the exact returned IDs")
        summaries.append({
            **variant, "qps": statistics.median(entry["result"]["qps"] for entry in group),
            "recall": first["recall"], "same_work": same_work, "identical_result_ids": ids == base_ids,
            "lost_gt_hits": sum(len(before - after) for before, after in zip(base_hits, hits)),
            "gained_gt_hits": sum(len(after - before) for before, after in zip(base_hits, hits)),
            "queries_losing_gt": sum(bool(before - after) for before, after in zip(base_hits, hits)),
            "phase_mean": phase, "hierarchy_mean": hierarchy,
            "native_results": [entry["result"] for entry in group],
        })
    write_json(output / "results.json", summaries)
    columns = ["name", "kind", "L", "dedup", "prefetch", "routing", "qps", "recall",
               "navigation_ms", "merge_ms", "vector_ms", "posting_ms", "lower_distances",
               "lost_gt_hits", "gained_gt_hits", "queries_losing_gt", "identical_result_ids"]
    with (output / "summary.csv").open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in summaries:
            values = {key: row[key] for key in columns if key in row}
            if row["profiled"]:
                values.update({label: row["phase_mean"][key] for label, key in (
                    ("navigation_ms", "h2"), ("merge_ms", "h2Merge"), ("vector_ms", "h2Vec"),
                    ("posting_ms", "post"), ("lower_distances", "h2Unique"))})
            writer.writerow(values)
            detail = (f"navigation={row['phase_mean']['h2']:.3f} ms" if row["profiled"]
                      else "phase/path logging disabled")
            print(f"{row['name']}: {row['qps']:.3f} QPS, recall={row['recall']:.4%}, "
                  f"{detail}, lost/gained GT="
                  f"{row['lost_gt_hits']}/{row['gained_gt_hits']}", flush=True)
    return summaries


def run(plan, config, variants, files):
    inputs = validate_inputs(config)
    index_files = check_spann(config)
    binary = identity(plan.path_value("Experiment", "Binary"), True)
    require(binary["sha256"] == plan.section("Experiment")["BinarySHA256"], "Unexpected A/B binary")
    output = config.path_value("Benchmark", "OutputDirectory") / (
        "spann_latency_ab_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8])
    output.mkdir()
    (output / "config").mkdir()
    copy_config_snapshot(config, output / "config" / config.path.parent.name)
    destination = output / "config" / plan.path.parent.name
    destination.mkdir()
    for path in files:
        relative = path.relative_to(plan.path.parent)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    frozen_plan, frozen_config, variants, files = load_plan(destination / plan.path.name)
    require(frozen_config.digest() == config.digest(), "Canonical controls changed during snapshot")
    plan, config = frozen_plan, frozen_config
    (output / "code").mkdir()
    for name in ("run_spann_latency_ab.py", "run_spann_memory_diagnostics.py",
                 "run_sift1b_official.py", "official_benchmark_config.py", "sift1b_official_inputs.py"):
        shutil.copy2(HERE / name, output / "code" / name)
    identities = [binary, *index_files, *(identity(path, True) for path in files),
                  *(identity(path, True) for path in config.config_files()),
                  *(identity(path, True) for path in (output / "code").iterdir())]
    job = make_job(plan, config, variants, inputs)
    write_json(output / "manifest.json", {"job": job, "variants": variants, "inputs": inputs,
                                         "identities": identities,
                                         "purpose": "One index process; same-work kernels and separate beam/recall experiments"})
    print(f"Experiment directory: {output}", flush=True)
    print(f"Running {len(variants)} fixed variants in one native index process.", flush=True)
    try:
        def started(pid):
            write_json(output / "status.json", {"state": "running", "pid": pid})
            print(f"Native PID: {pid}", flush=True)
        command = [sys.executable, "-B", str(output / "code" / "run_spann_latency_ab.py"),
                   "execute", "--config", str(plan.path)]
        usage = run_sampled_process(command, job["cwd"], output / "native",
                                    plan.section("Experiment").getfloat("SampleSeconds"), started)
        write_json(output / "usage.json", usage)
        summarize_run(config, inputs, variants, job, output)
        verify_identities(identities)
        validate_inputs(config)
        write_json(output / "status.json", {"state": "completed", "variants": len(variants),
                                            "measurements": len(job["points"])})
    except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        write_json(output / "status.json", {"state": "failed", "error": str(error)})
        raise
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("check-config", "run", "execute"))
    parser.add_argument("--config", type=Path, default=DEFAULT_PLAN)
    args = parser.parse_args()
    reject_environment_overrides()
    plan, config, variants, files = load_plan(args.config)
    if args.stage == "check-config":
        print(f"Fixed one-process A/B: {len(variants)} variants; {plan.path}")
    elif args.stage == "execute":
        require(sha256_file(plan.path_value("Experiment", "Binary")) ==
                plan.section("Experiment")["BinarySHA256"], "A/B executable changed")
        job = make_job(plan, config, variants, validate_inputs(config))
        set_thp_policy("default")
        os.execvp(job["command"][0], job["command"])
    else:
        run(plan, config, variants, files)


if __name__ == "__main__":
    main()
