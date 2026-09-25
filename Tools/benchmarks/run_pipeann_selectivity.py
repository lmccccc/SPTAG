#!/usr/bin/env python3
"""Fresh five-scenario PipeANN curves using the verified native read-only runtime."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import numpy as np

from official_benchmark_config import (
    Config, identity, load_config, read_json, reject_environment_overrides,
    require, sha256_file, verify_identities, write_json,
)
from pipeann_official_prepare import validate_memory, validate_tools
from run_sift1b_official import parse_results, sequence
from sift1b_official_inputs import (
    dnf_clauses, native_header, scenario_contract, validate_filter_config,
    validate_inputs, write_bin, write_binding,
)

HERE = Path(__file__).resolve().parent
SHARED = HERE / "hierarchical_shortcut_native/native_postfilter"
if SHARED.is_dir():
    sys.path.insert(0, str(SHARED))
from selectivity_common import confine_outputs, identity as file_identity, validate_identities
from run_selectivity import native_identity, terminate_owned

SCENARIOS = ("unfilter", "broad_tag", "medium_tag", "sel_01pct", "mixed_dnf")
LS = [10, 15, 20, 25, 30, 35, 40, 50, 60, 80, 120, 200, 400]


def config_at(path):
    config = Config(path)
    allowed = {
        "Dataset": {"ReferenceConfig", "Workloads"},
        "Benchmark": {"QueryCount", "WarmupQueries", "ResultNum", "NumberOfThreads",
                      "Repeats", "Scenarios", "OutputDirectory", "SPTAGCampaign"},
        "Execution": {"CPUNodes", "MemoryNodes"},
        "PipeANN": {"IndexPrefix", "NeighborType", "PipelineWidth", "SearchMode",
                    "UnfilteredMemoryL", "FilteredMemoryL", "FilterMode", "LSweep"},
    }
    for name in SCENARIOS:
        allowed[f"Scenario.{name}"] = ({"Kind"} if name == "unfilter" else
            {"Kind", "RareTag", "RegularTag", "UpperInclusive", "FilterConfig"} if name == "mixed_dnf"
            else {"Kind", "Tag", "FilterConfig"})
    adaptation_keys = {"ReadOnlyAdaptationManifest", "ReadOnlyAdaptationSHA256"}
    if any(key.lower() in config.section("PipeANN") for key in adaptation_keys):
        allowed["PipeANN"] |= adaptation_keys
    require(set(config.parser.sections()) == set(allowed), "Unknown/missing PipeANN curve section")
    for section, keys in allowed.items():
        require(set(config.section(section)) == {key.lower() for key in keys},
                f"Unknown/missing PipeANN curve key: {section}")
    benchmark, pipe = config.section("Benchmark"), config.section("PipeANN")
    require(tuple(config.csv("Benchmark", "Scenarios")) == SCENARIOS, "Wrong five-scenario scope")
    require(benchmark.getint("QueryCount") == benchmark.getint("WarmupQueries") == 1000 and
            benchmark.getint("ResultNum") == 10 and benchmark.getint("NumberOfThreads") == 1 and
            benchmark.getint("Repeats") == 2, "Wrong native query/repetition protocol")
    require(config.integer_list("PipeANN", "LSweep") == LS and
            pipe["NeighborType"] == "pq" and pipe.getint("PipelineWidth") == 32 and
            pipe.getint("SearchMode") == 2 and pipe.getint("UnfilteredMemoryL") == 10 and
            pipe.getint("FilteredMemoryL") == 0 and pipe["FilterMode"] == "auto",
            "PipeANN native search profile differs")
    require(config.affinity() == ["numactl", "--cpunodebind=3", "--membind=3"],
            "Comparison must use NUMA3")
    reject_environment_overrides()
    require(not [key for key in os.environ if key.startswith(("SPANN_", "OMP_")) or key == "LD_PRELOAD"],
            "Native environment overrides forbidden")
    return config


def verify_adaptation(path, digest):
    path = Path(path)
    require(small_hash(path) == digest, "Read-only descriptor adaptation manifest changed")
    verifier = path.parent / "validation/verify_toolchain.py"
    result = subprocess.run([sys.executable, "-B", str(verifier), str(path.parent), digest],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    require(result.returncode == 0, f"Read-only adaptation validation failed: {result.stderr}")
    return read_json(path), verifier


def adapted_tools(config, original):
    pipe = config.section("PipeANN")
    if "readonlyadaptationmanifest" not in pipe:
        return original["binaries"], None
    path = config.path_value("PipeANN", "ReadOnlyAdaptationManifest")
    digest = pipe["ReadOnlyAdaptationSHA256"]
    manifest, verifier = verify_adaptation(path, digest)
    require(manifest["source_revision"] == original["source"]["revision"] and
            manifest["original_config_sha256"] == original["config_sha256"],
            "Native adaptation derives from a different reference profile")
    return manifest["binaries"], dict(path=str(path), sha256=digest, verifier=str(verifier),
        description="Read-only file-descriptor opens and explicit failed-open checks; search algorithm unchanged",
        backend=manifest["effective"]["backend"], allocator=manifest["effective"]["allocator"])


def small_hash(path):
    require(Path(path).stat().st_size <= 128 << 20, f"Refusing large content hash: {path}")
    return sha256_file(path)


def native_jobs(config, definitions, binaries, root):
    benchmark, pipe = config.section("Benchmark"), config.section("PipeANN")
    jobs = []
    for scenario in definitions:
        name = scenario["name"]
        unfiltered = name == "unfilter"
        points = sequence(LS, benchmark.getint("Repeats"), warmup_pair=True)
        for point in points:
            point["L"] = point["value"]
        binary = binaries["search_disk_index" if unfiltered else "search_disk_index_filtered"]
        selector = pipe["SearchMode"] if unfiltered else str(config.path_value(f"Scenario.{name}", "FilterConfig"))
        command = [binary["path"], "uint8", str(config.path_value("PipeANN", "IndexPrefix")),
            benchmark["NumberOfThreads"], pipe["PipelineWidth"], str(root / "inputs/query.u8bin"),
            str(root / "inputs" / scenario["truth_bin"]), benchmark["ResultNum"], "l2",
            pipe["NeighborType"], selector,
            pipe["UnfilteredMemoryL"] if unfiltered else pipe["FilteredMemoryL"]]
        command += [str(point["L"]) for point in points]
        if not unfiltered:
            command.append("--filter-mode=" + pipe["FilterMode"])
        jobs.append(dict(engine="PipeANN", scenario=name, command=command, points=points,
                         cwd=str(root / "inputs"), binary=binary))
    return jobs


def prepare(path):
    config = config_at(path)
    root = config.path_value("Benchmark", "OutputDirectory")
    require(not root.exists(), "Prepared/partial campaign must be preserved")
    reference = load_config(config.path_value("Dataset", "ReferenceConfig"))
    tools = validate_tools(reference)
    memory = validate_memory(reference)
    native_binaries, adaptation = adapted_tools(config, tools)
    previous = validate_inputs(reference)
    require(config.path_value("PipeANN", "IndexPrefix") == reference.path_value("PipeANN", "IndexPrefix"),
            "Index prefix differs from the validated memory-entry layout")
    workload = read_json(config.path_value("Dataset", "Workloads"))
    require(workload["query_count"] == 1000 and workload["value_type"] == "UInt8" and
            workload["scenarios"] == list(SCENARIOS), "Prepared SPTAG workload differs")
    for name, digest in workload["protected"].items():
        require(small_hash(name) == digest, f"Authenticated workload input changed: {name}")
    validate_identities(workload["protected_large"])
    queries = np.load(workload["queries"], mmap_mode="r", allow_pickle=False)
    require(queries.dtype == np.dtype("u1") and queries.shape == (1000, 128), "Strict query type/shape")
    old_inputs = reference.path_value("Dataset", "PreparedDirectory")
    old_query = old_inputs / previous["query_bin"]
    require(native_header(old_query, 1) == queries.shape, "Archived query shape differs")
    require(np.array_equal(np.fromfile(old_query, dtype="u1", offset=8).reshape(queries.shape), queries),
            "Native query bytes differ across engines")
    definitions, contracts = [], {}
    for name in SCENARIOS:
        contract = scenario_contract(config, name)
        truth = workload["truth"][name]
        require(small_hash(truth["ids"]) == truth["sha256"], f"Groundtruth changed: {name}")
        ids = np.load(truth["ids"], mmap_mode="r", allow_pickle=False)
        require(ids.shape == (1000, 10) and ids.dtype == np.dtype("<i8") and
                np.all((ids >= 0) & (ids < 1000000000)) and
                all(len(set(row)) == 10 for row in ids), "Invalid top10 truth")
        if name != "unfilter":
            validate_filter_config(config, name, contract)
            predicate = workload["native_predicates"][name]
            if predicate["kind"] == "categorical":
                values = np.load(predicate["file"], mmap_mode="r", allow_pickle=False)
                require(values.dtype == np.dtype("<u4") and values.shape == (1000, 1) and
                        np.all(values == config.section(f"Scenario.{name}").getint("Tag")),
                        "Categorical query does not match its filter")
            else:
                require(dnf_clauses(predicate["file"], 1000) == contract["clauses"], "Mixed DNF differs")
        contracts[name] = contract
        definitions.append(dict(name=name, truth_bin=f"gt_{name}.ibin", truth_npy=truth["ids"],
            candidate_count=truth["candidate_count"], selectivity=truth["selectivity"],
            predicate=contract["predicate"], kind=config.section(f"Scenario.{name}")["Kind"]))
    root.mkdir(parents=True)
    for subdirectory in ("inputs", "runtime", "source", "logs"):
        (root / subdirectory).mkdir()
    helpers = ("run_pipeann_selectivity.py", "official_benchmark_config.py", "pipeann_official_prepare.py",
               "run_sift1b_official.py", "sift1b_official_inputs.py")
    for helper in helpers:
        shutil.copy2(HERE / helper, root / "source" / helper)
    confinement = HERE / "hierarchical_shortcut_native/native_postfilter"
    for helper in ("selectivity_common.py", "run_full.py", "run_selectivity.py", "run_postgraph.py"):
        shutil.copy2(confinement / helper, root / "source" / helper)
    write_bin(root / "inputs/query.u8bin", queries, "u1")
    require(small_hash(root / "inputs/query.u8bin") == small_hash(old_query), "Copied query differs")
    for definition in definitions:
        name = definition["name"]
        write_bin(root / "inputs" / definition["truth_bin"], np.load(definition["truth_npy"]), "<u4")
        for filename, values, kind in contracts[name]["bindings"].values():
            write_binding(root / "inputs" / filename, values, 1000, kind)
        old = next((entry for entry in previous["scenarios"] if entry["name"] == name), None)
        if old is not None:
            require(small_hash(root / "inputs" / definition["truth_bin"]) ==
                    small_hash(old_inputs / old["truth_bin"]), "Shared native truth differs")
    for alias, target in previous["aliases"].items():
        (root / "inputs" / alias).symlink_to(target)
    binaries = {}
    for name in ("search_disk_index", "search_disk_index_filtered"):
        record = native_binaries[name]
        target = root / "runtime" / name
        shutil.copy2(record["path"], target)
        require(small_hash(target) == record["sha256"], "Frozen PipeANN executable differs")
        binaries[name] = dict(path=str(target), sha256=record["sha256"], origin=record["path"])
    prefix = config.path_value("PipeANN", "IndexPrefix")
    index_files = sorted(prefix.parent.glob(prefix.name + "*"))
    require(index_files and any(p.name.endswith("_mem.index") for p in index_files), "Missing native memory entry")
    controls = [config.path, config.path_value("Dataset", "Workloads"),
                config.path_value("Dataset", "ReferenceConfig")]
    controls += [config.path_value(f"Scenario.{name}", "FilterConfig") for name in SCENARIOS[1:]]
    if adaptation:
        controls += [Path(adaptation["path"]), Path(adaptation["verifier"])]
    small = controls + [p for name in ("inputs", "runtime", "source")
                        for p in (root / name).iterdir() if p.is_file() and not p.is_symlink()]
    write_json(root / "registration.json", dict(
        query_count=1000, warmup_queries=1000, repeats=2, threads=1, cpu_nodes=3, memory_nodes=3,
        definitions=definitions, binaries=binaries, jobs=native_jobs(config, definitions, binaries, root),
        protected={str(p): small_hash(p) for p in small},
        index_files={str(p): file_identity(p) for p in index_files},
        alias_targets=previous["aliases"], protected_large=workload["protected_large"],
        reference_toolchain=identity(reference.path_value("PipeANN", "ToolchainDirectory") / "toolchain.json", True),
        reference_memory=identity(reference.path_value("MemoryIndex", "SamplePrefix").parent / "memory.json", True),
        source_revision=tools["source"]["revision"], read_only=True, no_mapping=True,
        readonly_descriptor_adaptation=adaptation,
        memory_entry_recipe=dict(rate=.01, R=32, L=64, alpha=1.2),
        io_mode="native direct IO; SPTAG uses buffered IO", input_cohort="identical native UInt8 first1000",
        corpus_count=1000000000, scenario_count=5, points_per_scenario=26,
        memory_validation=memory["status"], historical_timings_used=False))
    write_json(root / "status.json", dict(state="prepared_not_run", native_runs=0))


def checked(config):
    root = config.path_value("Benchmark", "OutputDirectory")
    registration = read_json(root / "registration.json")
    for name, digest in registration["protected"].items():
        require(small_hash(name) == digest, f"Registered PipeANN input changed: {name}")
    validate_identities(registration["index_files"])
    validate_identities(registration["protected_large"])
    verify_identities([registration["reference_toolchain"], registration["reference_memory"]])
    adaptation = registration.get("readonly_descriptor_adaptation")
    if adaptation:
        verify_adaptation(adaptation["path"], adaptation["sha256"])
    for alias, target in registration["alias_targets"].items():
        require((root / "inputs" / alias).resolve() == Path(target), "Attribute alias changed")
    return root, registration


def validated_results(log, job):
    errors = [line for line in Path(log).read_text().splitlines()
              if ":ERROR]" in line or ":FATAL]" in line or "Bad file descriptor" in line]
    require(not errors, f"PipeANN emitted native error diagnostics; results are invalid: {errors[:3]}")
    return parse_results(log, job, 1000)


def run(path):
    config = config_at(path)
    root, registration = checked(config)
    sptag = config.path_value("Benchmark", "SPTAGCampaign")
    require(read_json(sptag / "status.json") == dict(state="stage_complete", runtime="after", kind="normal")
            and (sptag / "after-normal-results.json").is_file(), "Wait for the SPTAG timing batch to finish")
    rows = []
    for job in registration["jobs"]:
        name = job["scenario"]
        stage = root / name
        require(not stage.exists(), "Existing/partial PipeANN stage retained; do not rerun")
        stage.mkdir()
        log = root / "logs" / f"{name}.log"
        command = ["/usr/bin/time", "-v", "-o", str(root / "logs" / f"{name}.resources.txt")]
        command += config.affinity() + [sys.executable, "-B", str(root / "source/run_pipeann_selectivity.py"),
                                       "native", str(stage)] + job["command"]
        write_json(stage / "command.json", command)
        with log.open("x") as stream:
            process = subprocess.Popen(command, cwd=job["cwd"], stdout=stream, stderr=subprocess.STDOUT)
            started = time.time()
            native = None
            try:
                while process.poll() is None:
                    if native is None:
                        native = native_identity(process.pid, job["binary"]["path"])
                    write_json(root / "status.json", dict(state="running", scenario=name,
                        wrapper_pid=process.pid, native=native, started_at=started,
                        elapsed_seconds=time.time() - started, log=str(log)))
                    time.sleep(10)
                returncode = process.returncode
            except BaseException:
                terminate_owned(process, native)
                raise
        require(returncode == 0, f"PipeANN {name} failed with exit {returncode}; retained log: {log}")
        parsed = validated_results(log, job)
        require(len(parsed) == 52, "Expected13L x2passes xwarmup/measurement")
        rows.extend(parsed)
        write_json(stage / "results.json", parsed)
        checked(config)
    write_json(root / "native-results.json", rows)
    measured = [row for row in rows if row["stage"] == "measured"]
    require(len(measured) == 130, "Incomplete five-scenario PipeANN curves")
    write_json(root / "results.json", measured)
    write_json(root / "status.json", dict(state="complete", native_processes=5, measured_points=130))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "native":
        require(len(sys.argv) >= 5 and Path(sys.argv[2]).is_absolute() and
                Path(sys.argv[3]).is_absolute(), "Expected output root, executable and native arguments")
        confine_outputs(sys.argv[2])
        os.execv(sys.argv[3], sys.argv[3:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "run"))
    parser.add_argument("ini", type=Path)
    args = parser.parse_args()
    config = config_at(args.ini)
    try:
        (prepare if args.stage == "prepare" else run)(args.ini.resolve())
    except Exception as error:
        root = config.path_value("Benchmark", "OutputDirectory")
        if (root / "registration.json").exists():
            failure = dict(state="failed", stage=args.stage, error=str(error))
            write_json(root / f"failure-{time.time_ns()}.json", failure)
            write_json(root / "status.json", failure)
        raise
