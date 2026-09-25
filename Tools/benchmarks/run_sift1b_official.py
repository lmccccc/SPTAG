#!/usr/bin/env python3
"""Fixed-file entrypoint for the official-profile PipeANN / SPANN comparison."""

import argparse
import configparser
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil
import statistics
import subprocess
import uuid

from official_benchmark_config import (
    DEFAULT_CONFIG, identity, load_config, read_json, reject_environment_overrides,
    require, sha256_file, verify_identities, write_json,
)
from sift1b_official_inputs import prepare_inputs, validate_inputs


HERE = Path(__file__).resolve().parent


def sequence(values, repeats, warmup_pair=False):
    points = []
    for repeat in range(1, repeats + 1):
        for value in (values if repeat % 2 else list(reversed(values))):
            if warmup_pair:
                points.append({"value": value, "repeat": repeat, "stage": "warmup"})
            points.append({"value": value, "repeat": repeat, "stage": "measured"})
    return points


def search_budget(path):
    parser = configparser.ConfigParser(interpolation=None)
    with Path(path).open() as stream:
        parser.read_file(stream)
    return parser["SearchSSDIndex"].getint("InternalResultNum")


def build_spann_job(config, inputs, scenario, binary, search_files=None):
    benchmark = config.section("Benchmark")
    files = ([str(config.relative_path(value)) for value in config.csv("SPANN", "SearchConfigs")]
             if search_files is None else [str(path) for path in search_files])
    require(files, "SPANN requires fixed native search files")
    points = sequence(files, benchmark.getint("Repeats"))
    command = config.affinity() + [
        str(binary), "--index", str(config.path_value("SPANN", "IndexDirectory")),
        "--queries", inputs["query_npy"], "--truth", scenario["truth_npy"],
        "--value-type", config.section("Dataset")["ValueType"], "--tenant", "0",
        "--topk", benchmark["ResultNum"], "--warmup", benchmark["WarmupQueries"],
        "--measure-offset", "0", "--max-queries", benchmark["QueryCount"],
        "--search-ini", str(config.path_value("SPANN", "InitialSearchConfig")),
    ]
    if scenario["name"] != "unfilter":
        source = config.path_value("Dataset", "WorkloadDirectory") / config.section(
            f"Scenario.{scenario['name']}")["SourcePredicate"]
        command += (["--query-tags", str(source), "--tag-column", "0"]
                    if scenario["kind"] == "categorical" else ["--query-dnf", str(source)])
    for point in points:
        point["L"] = search_budget(point["value"])
        command += ["--search-sweep-ini", point["value"]]
    return {"engine": "SPANN", "scenario": scenario["name"], "command": command,
            "cwd": str(config.path_value("Dataset", "PreparedDirectory")), "points": points}


def build_jobs(config, inputs, binaries):
    benchmark = config.section("Benchmark")
    pipeann = config.section("PipeANN")
    input_root = config.path_value("Dataset", "PreparedDirectory")
    jobs = []
    for position, scenario in enumerate(inputs["scenarios"]):
        name = scenario["name"]
        order = ("SPANN", "PipeANN") if position % 2 == 0 else ("PipeANN", "SPANN")
        for engine in order:
            if engine == "SPANN":
                jobs.append(build_spann_job(config, inputs, scenario, binaries["spannaclbench"]))
                continue
            else:
                points = sequence(config.integer_list("PipeANN", "LSweep"),
                                  benchmark.getint("Repeats"), warmup_pair=True)
                unfiltered = name == "unfilter"
                binary = binaries["search_disk_index" if unfiltered else "search_disk_index_filtered"]
                command = config.affinity() + [
                    str(binary), config.section("Dataset")["ValueType"].lower(),
                    str(config.path_value("PipeANN", "IndexPrefix")), benchmark["NumberOfThreads"],
                    pipeann["PipelineWidth"], str(input_root / inputs["query_bin"]),
                    str(input_root / scenario["truth_bin"]), benchmark["ResultNum"],
                    config.section("Dataset")["Metric"].lower(), pipeann["NeighborType"],
                    pipeann["SearchMode"] if unfiltered else str(config.path_value(f"Scenario.{name}", "FilterConfig")),
                    pipeann["UnfilteredMemoryL"] if unfiltered else pipeann["FilteredMemoryL"],
                ]
                for point in points:
                    point["L"] = point["value"]
                    command.append(str(point["value"]))
                if not unfiltered:
                    command.append("--filter-mode=" + pipeann["FilterMode"])
            jobs.append({"engine": engine, "scenario": name, "command": command,
                         "cwd": str(input_root), "points": points})
    return jobs


def check_spann(config):
    binary = config.path_value("SPANN", "BenchmarkBinary")
    require(sha256_file(binary) == config.section("SPANN")["BenchmarkSHA256"], "Unexpected SPANN executable")
    root = config.path_value("SPANN", "IndexDirectory")
    evidence = read_json(config.path_value("SPANN", "PreviousRunManifest"))
    index_records = [record for record in evidence["identities"] if Path(record["path"]).is_relative_to(root)]
    require(index_records, "Previous audit evidence does not identify the selected SPANN index")
    for record in index_records:
        stat = Path(record["path"]).stat()
        require(stat.st_size == record["bytes"] and stat.st_mtime_ns == record["mtime_ns"]
                and stat.st_ctime_ns == record["ctime_ns"], f"Audited SPANN file changed: {record['path']}")
    summary_path = config.path_value("SPANN", "CoverageSummary")
    require(summary_path.parent.parent == config.path_value("SPANN", "PreviousRunManifest").parent,
            "Coverage evidence comes from a different run")
    summary = read_json(summary_path)
    require(summary["documents"] == config.section("Dataset").getint("VectorCount")
            and summary["tag_count"] == 201 and summary["record_bytes"] == 140, "Wrong SPANN coverage report")
    for key in ("zero_h_nonhead_vids", "unobserved_vids", "head_records", "below_required_tags"):
        require(summary[key] == 0, f"SPANN coverage prerequisite failed: {key}")
    return [identity(record["path"]) for record in index_records]


def check_ready(config):
    from pipeann_official_prepare import validate_memory, validate_tools
    reject_environment_overrides()
    inputs = validate_inputs(config)
    tools = validate_tools(config)
    memory = validate_memory(config)
    spann_files = check_spann(config)
    return inputs, tools, memory, spann_files


def parse_results(path, job, query_count):
    native = []
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if job["engine"] == "SPANN" and line.startswith('{"engine":'):
                native.append(json.loads(line))
            elif job["engine"] == "PipeANN" and line.startswith("RESULT "):
                native.append(json.loads(line[7:]))
    require(len(native) == len(job["points"]), f"Missing native result rows: {job['scenario']}/{job['engine']}")
    rows = []
    for result, point in zip(native, job["points"]):
        fields = ({"failed_queries", "queries", "search_ini", "measure_offset", "search_api",
                   "value_type", "recall", "qps"} if job["engine"] == "SPANN" else
                  {"has_recall", "L", "recall_percent", "qps"})
        require(isinstance(result, dict) and fields <= result.keys(), "Missing native measurement fields")
        if job["engine"] == "SPANN":
            require(result["failed_queries"] == 0 and result["queries"] == query_count
                    and result["search_ini"] == point["value"] and result["measure_offset"] == 0
                    and result["search_api"] == "SearchWithPredicate" and result["value_type"] == "UInt8",
                    "SPANN native query contract failed")
            recall = result["recall"]
        else:
            require(result["has_recall"] is True and result["L"] == point["L"]
                    and isinstance(result["recall_percent"], (int, float)),
                    "PipeANN native result contract failed")
            recall = result["recall_percent"] / 100.0
        require(isinstance(recall, (int, float)) and isinstance(result["qps"], (int, float))
                and math.isfinite(recall) and 0 <= recall <= 1
                and math.isfinite(result["qps"]) and result["qps"] > 0, "Invalid native measurement")
        rows.append({"scenario": job["scenario"], "engine": job["engine"], **point,
                     "queries": query_count, "recall": recall, "qps": result["qps"], "native": result})
    return rows


def summarize(config, inputs, rows, output):
    measured = [row for row in rows if row["stage"] == "measured"]
    with (output / "native_results.jsonl").open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row, allow_nan=False) + "\n")
    with (output / "results.jsonl").open("w") as stream:
        for row in measured:
            stream.write(json.dumps(row, allow_nan=False) + "\n")
    definitions = {entry["name"]: entry for entry in inputs["scenarios"]}
    groups = {}
    for row in measured:
        groups.setdefault((row["scenario"], row["engine"], row["L"]), []).append(row)
    columns = ["scenario", "engine", "L", "queries", "repeats", "threads", "cpu_nodes",
               "recall", "recall_min", "recall_max", "qps", "qps_min", "qps_max",
               "candidate_count", "selectivity", "predicate"]
    with (output / "summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for (name, engine, budget), group in sorted(groups.items()):
            require(len(group) == config.section("Benchmark").getint("Repeats")
                    and len({row["repeat"] for row in group}) == len(group), "Incomplete repetition group")
            recalls, qps = [row["recall"] for row in group], [row["qps"] for row in group]
            writer.writerow({
                "scenario": name, "engine": engine, "L": budget, "queries": group[0]["queries"],
                "repeats": len(group), "threads": config.section("Benchmark").getint("NumberOfThreads"),
                "cpu_nodes": config.section("Execution")["CPUNodes"],
                "recall": statistics.median(recalls), "recall_min": min(recalls), "recall_max": max(recalls),
                "qps": statistics.median(qps), "qps_min": min(qps), "qps_max": max(qps),
                **{key: definitions[name][key] for key in ("candidate_count", "selectivity", "predicate")},
            })
    return len(measured), len(groups)


def copy_config_snapshot(config, destination):
    destination.mkdir()
    for path in config.config_files():
        relative = path.relative_to(config.path.parent)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    snapshot = load_config(destination / config.path.name)
    require(snapshot.digest() == config.digest(), "Configuration changed during snapshot")
    return snapshot


def run(config):
    inputs, tools, memory, spann_files = check_ready(config)
    output_base = config.path_value("Benchmark", "OutputDirectory")
    output_base.mkdir(parents=True, exist_ok=True)
    name = "pipeann_spann_official_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = output_base / f"{name}_{uuid.uuid4().hex[:8]}"
    output.mkdir()
    original_digest = config.digest()
    config = copy_config_snapshot(config, output / "config")
    (output / "bin").mkdir()
    binaries = {}
    sources = {"spannaclbench": config.path_value("SPANN", "BenchmarkBinary")}
    for key in ("search_disk_index", "search_disk_index_filtered"):
        sources[key] = Path(tools["binaries"][key]["path"])
    for key, path in sources.items():
        destination = output / "bin" / key
        shutil.copy2(path, destination)
        require(sha256_file(path) == sha256_file(destination), f"Executable changed during snapshot: {path}")
        binaries[key] = destination
    for directory in ("logs", "plots", "code"):
        (output / directory).mkdir()
    code_names = ("official_benchmark_config.py", "sift1b_official_inputs.py", "pipeann_official_prepare.py",
                  "run_sift1b_official.py", "plot_sift1b_official.R")
    for filename in code_names:
        shutil.copy2(HERE / filename, output / "code" / filename)
    jobs = build_jobs(config, inputs, binaries)
    binary_records = {key: identity(path, True) for key, path in binaries.items()}
    write_json(output / "manifest.json", {
        "config_digest": original_digest, "config_files": [identity(path, True) for path in config.config_files()],
        "inputs": inputs, "toolchain": tools, "memory_entry": memory, "jobs": jobs,
        "binaries": binary_records,
        "spann_files": spann_files,
        "io_policy": {"SPANN": "buffered", "PipeANN": "direct"},
        "warmup_policy": "Full identical query set before every timed pass; first of each PipeANN equal-L pair excluded",
    })
    print(f"Run directory: {output}", flush=True)
    rows = []
    try:
        for number, job in enumerate(jobs, 1):
            label = f"{job['scenario']}__{job['engine']}"
            write_json(output / "status.json", {"state": "running", "job": label, "job_number": number})
            print(f"[{number}/{len(jobs)}] {label}", flush=True)
            stdout_path = output / "logs" / f"{label}.stdout.log"
            with stdout_path.open("xb") as stdout, (output / "logs" / f"{label}.stderr.log").open("xb") as stderr:
                subprocess.run(
                    ["/usr/bin/time", "-v", "-o", str(output / "logs" / f"{label}.time.txt")] + job["command"],
                    cwd=job["cwd"], stdout=stdout, stderr=stderr, check=True,
                )
            rows += parse_results(stdout_path, job, config.section("Benchmark").getint("QueryCount"))
            summarize(config, inputs, rows, output)
        verify_identities(spann_files)
        verify_identities(binary_records.values())
        validate_inputs(config)
        from pipeann_official_prepare import validate_memory
        require(validate_memory(config) == memory, "PipeANN toolchain/index artifacts changed during benchmark")
        require(config.digest() == original_digest, "Frozen configuration changed during benchmark")
        measurements, points = summarize(config, inputs, rows, output)
        require(measurements == sum(point["stage"] == "measured" for job in jobs for point in job["points"]),
                "Incomplete measured matrix")
        subprocess.run(["Rscript", str(output / "code" / "plot_sift1b_official.R"), str(output)], check=True)
        write_json(output / "status.json", {"state": "completed", "measurements": measurements, "curve_points": points})
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        write_json(output / "status.json", {"state": "failed", "error": str(error)})
        raise
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("check-config", "build-tools", "prepare-inputs", "prepare-memory", "check-ready", "run"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    arguments = parser.parse_args()
    reject_environment_overrides()
    config = load_config(arguments.config)
    if arguments.stage == "check-config":
        print(f"Fixed profile: {config.path}\nSHA256: {config.digest()}")
    elif arguments.stage == "prepare-inputs":
        print(json.dumps(prepare_inputs(config), indent=2))
    elif arguments.stage in ("build-tools", "prepare-memory"):
        from pipeann_official_prepare import build_tools, prepare_memory
        operation = build_tools if arguments.stage == "build-tools" else prepare_memory
        print(json.dumps(operation(config), indent=2))
    elif arguments.stage == "check-ready":
        check_ready(config)
        print("Fixed configuration and native prerequisites are ready.")
    else:
        run(config)


if __name__ == "__main__":
    main()
