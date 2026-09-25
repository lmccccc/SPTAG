#!/usr/bin/env python3
"""Run fixed vanilla/H3 controls without rewriting an index or native search INI."""

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import time
import uuid

from official_benchmark_config import identity, reject_environment_overrides, require, write_json
from run_spann_memory_diagnostics import mean_phase_rows, parse_phase_line, run_sampled_process
from run_vanilla_spann_build import read_ini


def loader_differences(source, replacement):
    before, after = read_ini(source), read_ini(replacement)
    require(before.sections() == after.sections(), "H3 native loader sections changed")
    differences = {}
    for section in before.sections():
        require(set(before[section]) == set(after[section]), "H3 native loader keys changed")
        for key, value in before[section].items():
            if value != after[section][key]:
                differences[f"{section}.{key}"] = [value, after[section][key]]
    return differences


def payload_identity(source):
    return [identity(path, path.stat().st_size < 16 * 1024 * 1024)
            for path in sorted(source.rglob("*")) if path.is_file()]


def prepare_h3(plan):
    source = Path(plan["SourceIndex"]).resolve(strict=True)
    index = Path(plan["Index"])
    loader = Path(plan["IndexLoader"]).resolve(strict=True)
    require(index.is_absolute() and not index.is_symlink(), "Require a separate absolute H3 view")
    differences = loader_differences(source / "tenant_0/indexloader.ini", loader)
    require(set(differences) == {"Base.indexdirectory", "BuildSSDIndex.usedirectio"},
            f"Unexpected H3 loader changes: {differences}")
    require(differences["Base.indexdirectory"][1] == str(index / "tenant_0"),
            "Fixed H3 loader directory differs from the control")
    require(differences["BuildSSDIndex.usedirectio"] == ["false", "true"],
            "The H3 view may only change buffered to direct IO")
    if not index.exists():
        require(not index.parent.exists(), "Refusing to repair or overwrite an existing H3 view")
        index.mkdir(parents=True)
        for entry in source.iterdir():
            target = index / entry.name
            if entry.name == "tenant_0":
                target.mkdir()
                for payload in entry.iterdir():
                    if payload.name == "indexloader.ini":
                        shutil.copy2(loader, target / payload.name)
                    else:
                        (target / payload.name).symlink_to(payload, target_is_directory=payload.is_dir())
            else:
                target.symlink_to(entry, target_is_directory=entry.is_dir())
        write_json(index.parent / "provenance.json", {
            "source": str(source), "native_loader": identity(loader, True),
            "changes": differences, "source_payloads": payload_identity(source),
            "payload_policy": "Absolute symlinks; no data/index copy, rebuild, or save.",
        })
    require((index / "tenant_0/indexloader.ini").read_bytes() == loader.read_bytes(),
            "Staged native loader is not the fixed loader")
    require({p.name for p in source.iterdir()} == {p.name for p in index.iterdir()},
            "H3 root payload inventory changed")
    for entry in source.iterdir():
        if entry.name == "tenant_0":
            require({p.name for p in entry.iterdir()} ==
                    {p.name for p in (index / entry.name).iterdir()}, "H3 tenant inventory changed")
            for payload in entry.iterdir():
                target = index / entry.name / payload.name
                if payload.name != "indexloader.ini":
                    require(target.is_symlink() and target.resolve(strict=True) == payload.resolve(),
                            f"H3 payload is not an unchanged source link: {target}")
        else:
            target = index / entry.name
            require(target.is_symlink() and target.resolve(strict=True) == entry.resolve(),
                    f"H3 root payload is not a source link: {target}")
    return index


def observe_posting_io(pid, output, expected_direct):
    root = Path("/proc") / str(pid)
    deadline = time.monotonic() + 60
    observations = []
    while time.monotonic() < deadline:
        require(root.exists(), "Native process exited before its posting IO mode was observed")
        state = (root / "stat").read_text().rsplit(")", 1)[1].split()[0]
        if state == "Z":
            write_json(output, {"expected_direct_io": expected_direct, "observations": observations})
            raise ValueError("Native process completed without the expected posting IO mode")
        for fd in (root / "fd").iterdir():
            try:
                target = os.readlink(fd)
                if not target.endswith("/SPTAGFullList.bin"):
                    continue
                fields = dict(line.split(":", 1)
                              for line in (root / "fdinfo" / fd.name).read_text().splitlines()
                              if ":" in line)
                flags = int(fields["flags"].strip(), 8)
            except (FileNotFoundError, ProcessLookupError):
                continue
            observation = {"pid": pid, "fd": int(fd.name), "target": target,
                           "flags_octal": oct(flags), "direct_io": bool(flags & os.O_DIRECT),
                           "observed_at": datetime.now(timezone.utc).isoformat()}
            observations.append(observation)
            # The buffered metadata/header handle can precede the real data handle.
            if observation["direct_io"] == expected_direct:
                write_json(output, observation)
                return
        time.sleep(0.01)
    write_json(output, {"expected_direct_io": expected_direct, "observations": observations})
    raise ValueError("Did not observe the configured posting IO mode; comparison is invalid")


def run_native(command, output, name, interval, expected_direct):
    prefix = output / name
    write_json(output / f"{name}.command.json", {"command": command, "cwd": str(output)})
    try:
        usage = run_sampled_process(
            command, output, prefix, interval,
            lambda pid: observe_posting_io(pid, output / f"{name}.io.json", expected_direct))
    except (OSError, ValueError) as error:
        write_json(output / "status.json", {"state": "failed", "native_run": name, "error": str(error)})
        raise
    write_json(output / f"{name}.usage.json", usage)
    return [Path(str(prefix) + suffix) for suffix in (".stdout.log", ".stderr.log")]


def h3_records(logs, count, warmup, nprobe, profile):
    results, phases = [], []
    for path in logs:
        with path.open() as stream:
            for line in stream:
                if line.startswith("{") and '"engine":' in line:
                    results.append(json.loads(line))
                if "PhaseTime:" in line:
                    phases.append(parse_phase_line(line.split("PhaseTime:", 1)[1]))
    require(len(results) == 1, "Require one native H3 result per process")
    result = results[0]
    require(result["queries"] == count and result["failed_queries"] == 0,
            "H3 query count or success status differs from control")
    require(math.isfinite(result["mean_latency_ms"]) and result["mean_latency_ms"] > 0,
            "Invalid H3 latency")
    if profile:
        require(len(phases) == warmup + count, "H3 phase count includes missing/extra queries")
        phases = phases[warmup:]
        require(all(row["nprobe"] == nprobe for row in phases), "Unexpected H3 native beam")
        require(all(math.isfinite(value) and value >= 0
                    for row in phases for value in row.values()), "Invalid H3 phase values")
        return result, mean_phase_rows(phases)
    require(not phases, "Ordinary H3 run unexpectedly enabled timing instrumentation")
    return result, None


def paired_search_configs(config, case):
    require(case.startswith("L") and case[1:].isdigit(), "Invalid fixed H3 search case name")
    plain_path = config.parent / f"h3_plain_{case}.ini"
    profile_path = config.parent / f"h3_profile_{case}.ini"
    plain, profile = read_ini(plain_path), read_ini(profile_path)
    require(plain.sections() == profile.sections() == ["SearchSSDIndex"],
            "H3 search controls must contain one native section")
    after = dict(profile["SearchSSDIndex"])
    require(after.pop("logphasetime", None) == after.pop("logpathstats", None) == "true"
            and after == dict(plain["SearchSSDIndex"]),
            "H3 profile must differ only by the two diagnostic flags")
    native = plain["SearchSSDIndex"]
    require(native.getint("InternalResultNum") == int(case[1:]), "Case and native nprobe differ")
    require(native.getint("NumberOfThreads") == 1, "Require one query thread")
    return plain_path, profile_path, native


def h3_core_work(row):
    # First-visit ownership of a duplicated vector depends on IO completion order.
    # That can change the number of contributing postings without changing work.
    excluded = {"search_ini", "qps", "mean_latency_ms", "contributing_postings_per_query"}
    return {key: value for key, value in row.items() if key not in excluded}


def run_h3(config, plan, resume=False):
    index = prepare_h3(plan)
    source = Path(plan["SourceIndex"]).resolve(strict=True)
    before = payload_identity(source)
    binary = identity(Path(plan["Binary"]), True)
    require(binary["sha256"] == plan["BinarySHA256"], "Unexpected frozen H3 executable")
    count, warmup, repeats = (plan.getint(key) for key in ("QueryCount", "Warmup", "Repeats"))
    require(count > 0 and warmup == count and repeats > 0 and plan.getint("MeasureOffset") == 0,
            "Require matched full warmup and measured cohorts")
    cases = [item.strip() for item in plan["SearchCases"].split(",")]
    require(cases and len(set(cases)) == len(cases), "Empty or duplicate H3 cases")
    controls = [(case, *paired_search_configs(config, case)) for case in cases]
    output = Path(plan["OutputDirectory"])
    snapshot = output / "config"
    provenance = {
        "binary": binary, "control": identity(config, True), "index_payloads": before,
        "queries": identity(Path(plan["Queries"]), True), "truth": identity(Path(plan["Truth"]), True),
        "thp_policy": "Unchanged host/process defaults.", "engine": "frozen_h3",
    }
    if resume:
        require(json.loads((output / "provenance.json").read_text()) == provenance,
                "Cannot resume changed controls, inputs, payloads, or executable")
        require((snapshot / config.name).read_bytes() == config.read_bytes(),
                "Saved H3 control differs from the fixed control")
        require((snapshot / Path(plan["IndexLoader"]).name).read_bytes() ==
                Path(plan["IndexLoader"]).read_bytes(), "Saved H3 loader differs")
        if (output / "runs.json").exists() and not (output / "initial_runs.json").exists():
            shutil.copy2(output / "runs.json", output / "initial_runs.json")
        script = output / f"resume_{uuid.uuid4().hex[:8]}.py"
        shutil.copy2(Path(__file__), script)
        write_json(script.with_suffix(".json"), {
            "runner": identity(script, True), "raw_native_logs_reused": True,
            "previous_analysis": "All native processes succeeded; first-visit posting attribution was incorrectly treated as order-invariant.",
        })
    else:
        output.mkdir(parents=True, exist_ok=False)
        snapshot.mkdir()
        shutil.copy2(config, snapshot / config.name)
        shutil.copy2(plan["IndexLoader"], snapshot / Path(plan["IndexLoader"]).name)
        shutil.copy2(Path(__file__), output / Path(__file__).name)
        write_json(output / "provenance.json", provenance)
    write_json(output / "status.json", {"state": "running"})
    all_runs, summaries = [], []
    for case, plain_path, profile_path, native in controls:
        nprobe = native.getint("InternalResultNum")
        for path in (plain_path, profile_path):
            if (snapshot / path.name).exists():
                require((snapshot / path.name).read_bytes() == path.read_bytes(),
                        "Native search controls changed")
            else:
                shutil.copy2(path, snapshot / path.name)
        case_runs, first_work = [], None
        for repeat in range(1, repeats + 1):
            order = ("plain", "profile") if repeat % 2 else ("profile", "plain")
            for mode in order:
                search = snapshot / (plain_path if mode == "plain" else profile_path).name
                command = [
                    "numactl", f"--cpunodebind={plan.getint('CPUNode')}",
                    f"--membind={plan.getint('MemoryNode')}", plan["Binary"],
                    "--index", str(index), "--queries", plan["Queries"], "--truth", plan["Truth"],
                    "--search-sweep-ini", str(search), "--value-type", plan["ValueType"],
                    "--topk", str(native.getint("ResultNum")), "--warmup", str(warmup),
                    "--measure-offset", str(plan.getint("MeasureOffset")), "--max-queries", str(count),
                ]
                name = f"{case}_r{repeat}_{mode}"
                if resume and (output / f"{name}.usage.json").exists():
                    require(json.loads((output / f"{name}.command.json").read_text()) ==
                            {"command": command, "cwd": str(output)}, "Native command changed")
                    require(json.loads((output / f"{name}.io.json").read_text())["direct_io"] ==
                            plan.getboolean("ExpectedDirectIO"), "Native IO mode changed")
                    logs = [output / f"{name}.{stream}.log" for stream in ("stdout", "stderr")]
                else:
                    require(not list(output.glob(f"{name}.*")),
                            "Cannot overwrite incomplete native evidence; use a new fixed output path")
                    logs = run_native(command, output, name, plan.getfloat("SampleSeconds"),
                                      plan.getboolean("ExpectedDirectIO"))
                row, phases = h3_records(logs, count, warmup, nprobe, mode == "profile")
                work = h3_core_work(row)
                if first_work is None:
                    first_work = work
                if work != first_work:
                    write_json(output / f"{name}.work_mismatch.json",
                               {"reference": first_work, "actual": work})
                    write_json(output / "status.json",
                               {"state": "failed", "stage": "work_comparison", "run": name})
                    raise ValueError(f"H3 instrumentation/repetition changed native work: {name}")
                result = {"engine": "h3", "nprobe": nprobe, "repeat": repeat, "mode": mode,
                          "native": row, "phases": phases, "logs": [str(p) for p in logs]}
                case_runs.append(result)
                all_runs.append(result)
                write_json(output / "runs.json", all_runs)
                print(f"H3 {name}: recall={row['recall']:.6f}, qps={row['qps']:.3f}", flush=True)
        plain = [item["native"] for item in case_runs if item["mode"] == "plain"]
        profiled = [item for item in case_runs if item["mode"] == "profile"]
        phases = mean_phase_rows([item["phases"] for item in profiled])
        summaries.append({
            "engine": "h3", "nprobe": nprobe, "recall": plain[0]["recall"],
            "mean_ms": statistics.mean(row["mean_latency_ms"] for row in plain),
            "median_qps": statistics.median(row["qps"] for row in plain),
            "profile_mean_ms": statistics.mean(item["native"]["mean_latency_ms"] for item in profiled),
            "head_ms": phases["h2"], "posting_ms": phases["io"] + phases["scan"],
            "phases": phases, "exact_result_ids_available": False,
            "native_core_work_identical": True,
            "contributing_postings_range": [
                min(item["native"]["contributing_postings_per_query"] for item in case_runs),
                max(item["native"]["contributing_postings_per_query"] for item in case_runs),
            ],
            "posting_attribution_note": "First-visit posting ownership can change with async completion order; total reads, scans, distances and recall must remain identical.",
        })
    require(payload_identity(source) == before, "Preserved H3 payloads changed")
    require(identity(Path(plan["Binary"]), True) == binary, "Frozen H3 executable changed")
    write_json(output / "summary.json", summaries)
    write_json(output / "status.json", {"state": "completed", "runs": len(all_runs)})


def run_vanilla(config, parser):
    plan, execution = parser["Benchmark"], parser["Execution"]
    require(plan.getint("QueryCount") == plan.getint("Warmup") > 0,
            "Require matched full warmup and measured cohorts")
    binary = identity(Path(execution["Binary"]), True)
    require(binary["sha256"] == execution["BinarySHA256"], "Unexpected original SPANN adapter")
    before = payload_identity(Path(parser["Base"]["IndexDirectory"]))
    output = Path(execution["OutputDirectory"])
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "config"
    snapshot.mkdir()
    shutil.copy2(config, snapshot / config.name)
    for path in plan["SearchConfigs"].split(","):
        shutil.copy2(path.strip(), snapshot / Path(path.strip()).name)
    shutil.copy2(Path(__file__), output / Path(__file__).name)
    write_json(output / "provenance.json", {
        "binary": binary, "control": identity(config, True), "index_payloads": before,
        "queries": identity(Path(parser["Base"]["QueryPath"]), True),
        "truth": identity(Path(parser["Base"]["TruthPath"]), True),
        "thp_policy": "Unchanged host/process defaults.", "engine": "microsoft_spann",
    })
    write_json(output / "status.json", {"state": "running"})
    command = [
        "numactl", f"--cpunodebind={execution.getint('CPUNode')}",
        f"--membind={execution.getint('MemoryNode')}", execution["Binary"], "--config", str(config),
    ]
    logs = run_native(command, output, "native", 0.2, plan.getboolean("ExpectedDirectIO"))
    rows = []
    for path in logs:
        with path.open() as stream:
            rows.extend(json.loads(line.partition(" ")[2])
                        for line in stream if line.startswith("VANILLA_POINT "))
    expected = len(plan["SearchConfigs"].split(",")) * plan.getint("Repeats") * 2
    require(len(rows) == expected, "Missing original SPANN benchmark results")
    require(identity(Path(execution["Binary"]), True) == binary, "Vanilla adapter changed")
    require(payload_identity(Path(parser["Base"]["IndexDirectory"])) == before,
            "Original index payloads changed during search")
    write_json(output / "runs.json", rows)
    write_json(output / "status.json", {"state": "completed", "runs": len(rows)})
    print(f"Original SPANN results: {output}", flush=True)


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--config", type=Path, required=True)
    arguments.add_argument("--prepare-only", action="store_true")
    arguments.add_argument("--resume", action="store_true")
    args = arguments.parse_args()
    reject_environment_overrides()
    config = args.config.resolve(strict=True)
    parser = read_ini(config)
    if parser.sections() == ["H3"]:
        if args.prepare_only:
            require(not args.resume, "Preparation and measurement resume are separate operations")
            print(prepare_h3(parser["H3"]))
        else:
            run_h3(config, parser["H3"], args.resume)
    else:
        require(not args.prepare_only, "Preparation-only applies only to the H3 read-only view")
        require(not args.resume, "Resume applies only to successful per-process H3 logs")
        require(parser.sections() == ["Base", "Benchmark", "Execution"], "Unknown comparison control")
        run_vanilla(config, parser)


if __name__ == "__main__":
    main()
