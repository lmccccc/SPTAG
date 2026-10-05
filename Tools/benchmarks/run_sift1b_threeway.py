"""Immutable single-thread curves and resource-limited native throughput.

The INI owns every search value. TSV plans only select ordinals in its declared
grids; they cannot introduce a new budget, predicate, or thread count.
"""

import argparse
from collections import defaultdict
import configparser
import copy
import csv
from datetime import datetime, timezone
import hashlib
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
import traceback

import numpy as np

import native_loader_provenance
from native_input_io import open_attributes, open_vectors, read_config
from official_benchmark_config import (
    Config, identity, read_json, reject_environment_overrides, require,
    sha256_file, verify_identities, write_json,
)
from sift1b_official_inputs import (
    native_header, scenario_contract, validate_filter_config, write_bin, write_binding,
)


HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "configs/sift1b_threeway/benchmark.ini"
ENGINES = ("SPTAG_adaptive", "PipeANN", "Filtered_DiskANN")
SECTIONS = dict(zip(ENGINES, ("SPANN", "PipeANN", "DiskANN")))
SCENARIOS = ("unfilter", "broad_tag", "medium_tag", "sel_01pct", "mixed_dnf")
PLAN_FIELDS = ("scenario", "control_index", "thread_index", "repeat")
SINGLE_FIELDS = (
    "scenario", "engine", "L", "queries", "repeats", "threads", "cpu_nodes",
    "recall", "recall_min", "recall_max", "qps", "qps_min", "qps_max",
    "candidate_count", "selectivity", "predicate", "measurement_reused", "io_mode", "source",
)
THROUGHPUT_FIELDS = (
    "scenario", "engine", "threads", "L", "repeats", "recall_target", "recall",
    "recall_min", "recall_max", "qps", "qps_min", "qps_max", "seconds_min",
    "queries_min", "mean_latency_us", "p50_latency_us", "p99_latency_us",
    "mean_ios", "cpu_nodes", "memory_nodes",
    "measured_queries_total", "returned_neighbors_total", "missing_neighbors_total",
    "underfilled_queries_total", "returned_per_query", "underfilled_fraction", "native_result_schemas",
)
AVAILABILITY_FIELDS = ("phase", "scenario", "engine", "status", "reason", "recall_target", "threads")
NATIVE_ERROR_COUNTS = (
    "invalid_queries", "invalid_ids", "duplicate_ids", "nonmatching_ids",
    "nonfinite_distances", "underfilled_queries", "query_exceptions",
)
CODE_FILES = (
    "run_sift1b_threeway.py", "official_benchmark_config.py", "native_input_io.py",
    "validate_spann_hierarchy_config.py", "sift1b_official_inputs.py", "plot_sift1b_threeway.R",
    "native_loader_provenance.py", "continue_sift1b_threeway.py",
)
NATIVE_CODE_FILES = (
    "benchmark.h", "diskann_bench.cpp", "spann_bench.cpp", "pipeann_bench.cpp",
    "build_diskann_client.py", "build_spann_client.py", "build_pipeann_client.py",
    "diskann_admission.h", "diskann_build_admission.cpp", "build_diskann_admission.py",
    "diskann_loader_policy.h", "loader_policy.py",
    "build_pipeann_page_lifetime.py", "pipeann_page_lifetime.py", "pipeann_page_lifetime.patch",
    "build_pipeann_short_results.py", "pipeann_short_results.patch",
)
NATIVE_SHORT_RESULT_POLICY = "native-count-prefix-v1"
DISKANN_REQUIRED_SOURCES = (
    "_disk.index", "_pq_compressed.bin", "_pq_pivots.bin", "_disk.index_labels.txt",
    "_disk.index_labels_map.txt", "_disk.index_labels_to_medoids.txt",
)
DISKANN_OPTIONAL_SOURCES = (
    "_disk.index_pq_pivots.bin", "_disk.index_universal_label.txt", "_disk.index_dummy_map.txt",
    "_disk.index_medoids.bin", "_pq_pivots.bin_rotation_matrix.bin", "_disk.index_centroids.bin",
)
GIB = 1 << 30
DISK_RESERVE_AUTHORIZATION = "authorize-64-rebuild-layout"
LAYOUT_CONTINUATION_RECORDS = (
    "continuation.ini", "resource-policy.json", "layout-continuation-registration.json",
    "layout-continuation-evidence.json", "discarded-partial-layout.json",
    "create-disk-layout.execution.json",
)


class Profile(Config):
    def __init__(self, path):
        self.path = Path(path).resolve(strict=True)
        self.parser = configparser.ConfigParser(interpolation=None)
        self.parser.read_dict(read_config(self.path))

    def section(self, name):
        return super().section(name.lower())

    @property
    def root(self):
        return self.path_value("Run", "OutputDirectory")

    @property
    def prepared(self):
        return self.path_value("Run", "PreparedDirectory")

    def controls(self, engine):
        return self.integer_list(SECTIONS[engine], "NProbe" if engine == "SPTAG_adaptive" else "LSweep")

    def threads(self, phase):
        return [1] if phase == "single" else self.integer_list("Throughput", "Threads")

    def repeats(self, phase):
        return self.section("Benchmark").getint("SingleRepeats") if phase == "single" else (
            self.section("Throughput").getint("Repeats"))

    def targets(self):
        return [float(value) for value in self.csv("Throughput", "RecallTargets")]

    def affinity(self, phase):
        section = "Single" if phase == "single" else "Throughput"
        result = super().affinity(section)
        if phase == "throughput":
            result[-1] = "--interleave=" + self.section(section)["MemoryNodes"]
        return result

    def config_files(self):
        return [self.path] + [self.path_value(f"Scenario.{name}", "FilterConfig")
                             for name in SCENARIOS if name != "unfilter"]


def load_profile(path=DEFAULT_CONFIG):
    config = Profile(path)
    keys = {
        "Dataset": "Vectors Queries Attributes VectorCount Dimension AttributeColumns CategoricalColumn "
                   "NumericColumn QueryPayloadSHA256",
        "Run": "OutputDirectory PreparedDirectory BuildDirectory BuildControllerPID PollSeconds "
               "ResourceIntervalSeconds MinimumFreeDiskGiB MinimumFreeMemoryGiB MaxChildRSSGiB RScript",
        "History": "Summary SummarySHA256 Registration RegistrationSHA256 AssemblyManifest AssemblyManifestSHA256",
        "Benchmark": "QueryCount WarmupQueries TopK SingleRepeats Scenarios",
        "Single": "CPUNodes MemoryNodes",
        "Throughput": "Threads Repeats MinimumSeconds RecallTargets AIOReserve CPUNodes MemoryNodes ResourcePolicy",
        "DiskANN": "IndexPrefix BenchmarkBinary SourceDirectory SourceRevision Library LSweep BeamWidth "
                   "CacheNodes AIOEventsPerThread",
        "SPANN": "IndexDirectory BenchmarkBinary NProbe",
        "PipeANN": "IndexPrefix BenchmarkBinary SourceDirectory LSweep PipelineWidth SearchMode "
                   "UnfilteredMemoryL FilteredMemoryL FilterMode",
    }
    for name in SCENARIOS:
        keys[f"Scenario.{name}"] = "Kind Truth CandidateCount" + (
            "" if name == "unfilter" else " FilterConfig " + (
                "RareTag RegularTag UpperInclusive" if name == "mixed_dnf" else "Tag"))
    require(set(config.parser.sections()) == {name.lower() for name in keys} | {"searchssdindex"},
            "Unknown or missing INI section")
    for section, names in keys.items():
        expected = {key.lower() for key in names.split()}
        actual = set(config.section(section))
        optional = {"DiskANN": "loaderpolicy", "PipeANN": "allowshortresults"}.get(section)
        require(actual == expected or (optional is not None and actual == expected | {optional}),
                f"Unknown or missing INI key in [{section}]")
    data, bench = config.section("Dataset"), config.section("Benchmark")
    require((data.getint("VectorCount"), data.getint("Dimension"), data.getint("AttributeColumns"),
             data.getint("CategoricalColumn"), data.getint("NumericColumn")) == (10**9, 128, 2, 0, 1),
            "This registered comparison requires the complete SIFT1B UInt8/two-column source")
    require((bench.getint("QueryCount"), bench.getint("WarmupQueries"), bench.getint("TopK"),
             bench.getint("SingleRepeats")) == (1000, 1000, 10, 2),
            "Single-thread curves must retain the historical 1000-query/top10/two-repeat protocol")
    require(tuple(config.csv("Benchmark", "Scenarios")) == SCENARIOS, "Unexpected scenario order")
    throughput = config.section("Throughput")
    require(throughput.getfloat("MinimumSeconds") >= 30 and throughput.getint("Repeats") >= 3,
            "Sustained throughput needs at least 30 seconds and three repetitions per point")
    require(throughput["ResourcePolicy"] == "current-host-limit",
            "No host-wide limit changes or hidden resource overrides are authorized")
    require(throughput.getint("AIOReserve") >= 0, "AIO reserve cannot be negative")
    require(config.targets() == sorted(set(config.targets()))
            and all(0 < value <= 1 for value in config.targets()), "Invalid recall targets")
    for engine in ENGINES:
        values = config.controls(engine)
        require(values == sorted(set(values)) and min(values) >= bench.getint("TopK"),
                f"Invalid native search grid: {engine}")
    threads = config.threads("throughput")
    require(threads == sorted(set(threads)) and threads[0] == 1 and threads[-1] <= 1024,
            "Invalid concurrency grid")
    for phase in ("single", "throughput"):
        config.affinity(phase)
    require(config.section("Single")["CPUNodes"] == "3"
            and config.section("Single")["MemoryNodes"] == "3",
            "Historical single-thread comparison requires NUMA node 3, not a single-CPU pin")
    require(throughput["CPUNodes"] == "0,1,2,3" and throughput["MemoryNodes"] == "0,1,2,3",
            "The registered throughput hardware budget is the same four NUMA nodes for every engine")
    require(config.prepared == config.root / "inputs", "Prepared data must be owned by this fresh campaign")
    diskann, pipeann = config.section("DiskANN"), config.section("PipeANN")
    require(diskann.getint("BeamWidth") == 2 and diskann.getint("CacheNodes") == 0
            and diskann.getint("AIOEventsPerThread") == 1024, "Preserve original DiskANN search/IO settings")
    require(pipeann.getint("PipelineWidth") == 32 and pipeann.getint("SearchMode") == 2
            and pipeann.getint("UnfilteredMemoryL") == 10 and pipeann.getint("FilteredMemoryL") == 0
            and pipeann["FilterMode"] == "auto", "Preserve the registered native PipeANN baseline")
    if "AllowShortResults" in pipeann:
        require(pipeann["AllowShortResults"].lower() in ("true", "1"),
                "The optional PipeANN native-count protocol must be explicitly enabled")
    expected_search = {
        "isexecute": "true", "buildssdindex": "false", "internalresultnum": "24",
        "numberofthreads": "1", "hashtableexponent": "4", "resultnum": "10",
        "maxcheck": "2048", "maxdistratio": "8", "searchpostingpagelimit": "3",
        "disablecrossedges": "true", "logphasetime": "false", "logpathstats": "false",
        "dumpheads": "0", "enablehybriddistance": "false", "enablepostingnavigation": "true",
        "postinganchorcount": "8", "postingadditionalmaxcheck": "2048",
    }
    require(dict(config.section("SearchSSDIndex")) == expected_search,
            "SearchSSDIndex must preserve the measured adaptive SPANN policy")
    for name in SCENARIOS:
        section = config.section(f"Scenario.{name}")
        require(0 < section.getint("CandidateCount") <= data.getint("VectorCount"), "Invalid candidate count")
        contract = scenario_contract(config, name)
        if name != "unfilter":
            validate_filter_config(config, name, contract)
    for key in ("PollSeconds", "ResourceIntervalSeconds", "MinimumFreeDiskGiB",
                "MinimumFreeMemoryGiB", "MaxChildRSSGiB"):
        require(config.section("Run").getfloat(key) > 0, f"[Run] {key} must be positive")
    return config


def reject_benchmark_environment(environment=None):
    environment = os.environ if environment is None else environment
    reject_environment_overrides(environment)
    forbidden = sorted(key for key in environment
                       if key.startswith(("SPANN_", "DISKANN_", "OMP_", "GOMP_", "KMP_", "MKL_", "OPENBLAS_"))
                       or key == "LD_PRELOAD")
    require(not forbidden, "Remove benchmark runtime overrides: " + ", ".join(forbidden))


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_csv(path):
    with Path(path).open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, fields, rows):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def finite(value, name, low=0, high=None, positive=False):
    require(not isinstance(value, bool), f"Invalid numeric {name}")
    number = float(value)
    require(math.isfinite(number) and (number > low if positive else number >= low)
            and (high is None or number <= high), f"Invalid numeric {name}: {value}")
    return number


def matrix(path, dtype):
    rows, columns = native_header(path, np.dtype(dtype).itemsize)
    return np.memmap(path, mode="r", dtype=dtype, offset=8, shape=(rows, columns))


def predicate_mask(config, scenario, attributes, ids):
    section = config.section(f"Scenario.{scenario}")
    kind = section["Kind"]
    if kind == "unfilter":
        return np.ones(ids.shape, dtype=bool)
    tag = attributes[ids, config.section("Dataset").getint("CategoricalColumn")]
    if kind == "categorical":
        return tag == section.getint("Tag")
    require(kind == "mixed_dnf", "Unsupported native predicate")
    numeric = attributes[ids, config.section("Dataset").getint("NumericColumn")]
    return ((tag == section.getint("RareTag")) |
            ((tag == section.getint("RegularTag")) & (numeric <= section.getint("UpperInclusive"))))


def validate_ids(config, scenario, ids, attributes):
    count = config.section("Dataset").getint("VectorCount")
    require(ids.ndim == 2 and np.issubdtype(ids.dtype, np.integer)
            and not np.any(ids < 0) and not np.any(ids >= count), "Invalid original-row result IDs")
    require(np.all(np.diff(np.sort(ids, axis=1), axis=1) != 0), "Duplicate result IDs")
    require(np.all(predicate_mask(config, scenario, attributes, ids)), "Nonmatching predicate result")


def read_history(config):
    for name in ("Summary", "Registration", "AssemblyManifest"):
        path = config.path_value("History", name)
        require(sha256_file(path) == config.section("History")[name + "SHA256"],
                f"Historical {name} changed")
    registration = read_json(config.path_value("History", "Registration"))
    assembly = read_json(config.path_value("History", "AssemblyManifest"))
    cohort = config.section("Dataset")["QueryPayloadSHA256"]
    require(assembly["parent_declared_complete"] is True
            and assembly["query_cohort"]["logical_payload_sha256"] == cohort,
            "Historical data is incomplete or belongs to another cohort")
    require(registration["dataset"] == "SIFT1B" and registration["corpus_count"] == 10**9
            and tuple(registration["scenarios"]) == SCENARIOS, "Historical registration differs")
    rows, seen = read_csv(config.path_value("History", "Summary")), set()
    for row in rows:
        engine, scenario = row["engine"], row["scenario"]
        require(engine in ENGINES[:2] and scenario in SCENARIOS, "Unexpected historical series")
        key = (engine, scenario, int(row["L"]))
        require(key not in seen and key[2] in config.controls(engine), "Duplicate/unregistered historical point")
        seen.add(key)
        require(int(row["queries"]) == 1000 and int(row["repeats"]) == 2
                and int(row["threads"]) == 1 and row["cpu_nodes"] == "3", "Historical timing protocol differs")
        for metric, limit in (("recall", 1), ("qps", None)):
            lo = finite(row[metric + "_min"], metric + "_min", high=limit, positive=metric == "qps")
            mean = finite(row[metric], metric, high=limit, positive=metric == "qps")
            hi = finite(row[metric + "_max"], metric + "_max", high=limit, positive=metric == "qps")
            require(lo <= mean <= hi, "Historical range does not contain its aggregate")
        metadata = registration["scenario_metadata"][scenario]
        require(int(row["candidate_count"]) == config.section(f"Scenario.{scenario}").getint("CandidateCount")
                and row["predicate"] == scenario_contract(config, scenario)["predicate"]
                and math.isclose(float(row["selectivity"]), int(row["candidate_count"]) / 10**9,
                                 rel_tol=0, abs_tol=1e-12)
                and metadata["candidate_count"] == int(row["candidate_count"]), "Historical predicate differs")
        native = registration["engines"][engine]
        require(native["query_cohort_id"] == "sha256:" + cohort
                and native["threads"] == 1 and str(native["cpu_nodes"]) == "3"
                and str(native["memory_nodes"]) == "3", "Historical cohort/placement differs")
    expected = {(engine, scenario, control) for engine in ENGINES[:2]
                for scenario in SCENARIOS for control in config.controls(engine)}
    require(seen == expected, "Incomplete historical curve matrix")
    return rows, registration


def file_record(path, hash_content=False):
    path = Path(path).absolute()
    stat = path.stat()
    result = {
        "path": str(path), "resolved": str(path.resolve(strict=True)), "device": stat.st_dev,
        "inode": stat.st_ino, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
        "link": os.readlink(path) if path.is_symlink() else None,
    }
    with path.open("rb") as stream:
        result["first_4096_sha256"] = hashlib.sha256(stream.read(4096)).hexdigest()
    if hash_content or stat.st_size <= 1 << 20:
        result["sha256"] = sha256_file(path)
    return result


def verify_records(records):
    for record in records:
        require(file_record(record["path"], "sha256" in record) == record,
                f"Protected input/index changed: {record['path']}")


def parse_native_dependencies(output):
    libraries = {}
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        if "=>" in line:
            name, target = line.split("=>", 1)
            name, target = name.strip(), target.strip().split(" (", 1)[0]
            require(Path(name).name == name and Path(target).is_absolute(),
                    f"Unresolved native runtime dependency: {line}")
        elif line.startswith("/"):
            target = line.split(" (", 1)[0]
            name = Path(target).name
        elif line.startswith("linux-vdso.so."):
            continue
        else:
            raise ValueError(f"Unrecognized native loader output: {line}")
        require(name not in libraries or libraries[name] == target, f"Conflicting runtime dependency: {name}")
        libraries[name] = target
    require(libraries, "Native runtime dependency inspection returned no libraries")
    return libraries


def native_dependencies(binary, cwd=None):
    result = subprocess.run(["ldd", str(binary)], text=True, capture_output=True, check=True, cwd=cwd)
    return parse_native_dependencies(result.stdout)


def authenticate_build_artifact(entry, expected_path=None):
    require(isinstance(entry, dict) and isinstance(entry.get("path"), str)
            and Path(entry["path"]).is_absolute(), "Build provenance requires an absolute artifact path")
    path = Path(entry["path"])
    if expected_path is not None:
        require(path.resolve(strict=True) == Path(expected_path).resolve(strict=True),
                f"Build provenance names the wrong artifact: {path}")
    actual = file_record(path, True)
    require(entry.get("sha256") == actual["sha256"], f"Build artifact hash changed: {path}")
    for key in ("bytes", "size"):
        if key in entry:
            require(type(entry[key]) is int and entry[key] == actual["bytes"],
                    f"Build artifact size changed: {path}")
    return actual


def validate_native_build(config, engine, binary, manifest_path, *, diskann_build_admission=False):
    require(not diskann_build_admission or engine == "Filtered_DiskANN",
            "Build admission is only defined for original DiskANN")
    manifest = read_json(manifest_path)
    require(type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1
            and manifest.get("engine") == engine, "Native build manifest schema/engine mismatch")
    command = manifest.get("command")
    require(isinstance(command, list) and command and all(isinstance(arg, str) and arg for arg in command),
            "Native build manifest is missing its compiler/linker argv")
    compiler_path = shutil.which(command[0])
    require(compiler_path is not None and isinstance(manifest.get("compiler_version"), str)
            and manifest["compiler_version"], "Native build manifest is missing compiler identity/version")
    compiler = authenticate_build_artifact(manifest["compiler"], compiler_path)
    installed = authenticate_build_artifact(manifest["binary"], binary)
    reference_binary = None
    loader = None
    correctness = None
    result_policy = None
    stem = SECTIONS[engine].lower()
    source_names = (f"{stem}_bench.cpp", "benchmark.h", f"build_{stem}_client.py")
    native_directory = HERE / "threeway_native"
    if engine == "Filtered_DiskANN":
        source_names += ("diskann_admission.h",)
        if diskann_build_admission:
            source_names = ("diskann_build_admission.cpp", "benchmark.h", "build_diskann_client.py",
                            "diskann_admission.h", "build_diskann_admission.py")
        require(manifest["source_clean"] is True
                and manifest["source_revision"] == config.section("DiskANN")["SourceRevision"]
                and Path(manifest["source_directory"]).resolve() == config.path_value("DiskANN", "SourceDirectory"),
                "DiskANN client does not use the registered original source")
        sources = manifest["sources"]
        if "LoaderPolicy" in config.section("DiskANN"):
            loader = native_loader_provenance.validate(
                config.path_value("DiskANN", "LoaderPolicy"), config.path_value("DiskANN", "SourceDirectory"),
                config.section("DiskANN")["SourceRevision"],
                original_library=config.path_value("DiskANN", "Library"))
            require(manifest.get("loader_policy") == loader["binding"],
                    "DiskANN build does not bind the approved loader-only library")
            source_names += ("diskann_loader_policy.h", "loader_policy.py")
            original = authenticate_build_artifact(
                manifest["original_library"], config.path_value("DiskANN", "Library"))
            require(original["sha256"] == loader["proof"]["original_library"]["sha256"],
                    "DiskANN manifest changed its original algorithm-base archive")
            native_artifacts = [
                authenticate_build_artifact(manifest["library"], loader["binding"]["library"]), original]
        else:
            require("loader_policy" not in manifest, "Unregistered DiskANN loader replacement")
            native_artifacts = [authenticate_build_artifact(
                manifest["library"], config.path_value("DiskANN", "Library"))]
        link_paths = [native_artifacts[0]["resolved"]]
    elif engine == "SPTAG_adaptive":
        require(manifest["status"] == "complete", "SPANN native build did not complete")
        sources = [manifest["adapter_source"], manifest["benchmark_header"], manifest["build_entry"]]
        native_artifacts = [authenticate_build_artifact(entry) for entry in manifest["frozen_artifacts"]]
        references = [entry for entry in native_artifacts if Path(entry["resolved"]).name == "nativeBench"]
        require(len(references) == 1 and references[0]["sha256"] == manifest["measured_nativeBench_sha256"],
                "SPANN build lacks its authenticated measured nativeBench reference")
        reference_binary = references[0]
        core = Path(references[0]["resolved"]).parents[1]
        link_paths = [str((core / "AnnService/CMakeFiles/nativeBench.dir/__/Wrappers/src/CoreInterface.cpp.o").resolve())]
        link_paths += [str((core / "bin" / name).resolve()) for name in
                       ("libSPTAGLibStatic.a", "libDistanceUtils.a", "libRaBitQ2Lib.a", "libzstd.a")]
        available = {entry["resolved"] for entry in native_artifacts}
        require(set(link_paths).issubset(available), "SPANN build did not preserve every frozen native core object")
        for entry in manifest["source_dependencies"]:
            authenticate_build_artifact(entry)
    else:
        from threeway_native import pipeann_page_lifetime

        require(engine == "PipeANN" and manifest["core_recompiled"] is False,
                "PipeANN client must link a separately authenticated core")
        require({"-DREAD_ONLY_TESTS", "-DNO_MAPPING", "-DUSE_URING", "-DUSE_TCMALLOC"}.issubset(command),
                "PipeANN native read-only/backend compilation flags changed")
        sources = list(manifest["inputs"].values())
        library = config.path_value("PipeANN", "SourceDirectory").parent / "build/src/libpipeann.a"
        native_artifacts = [authenticate_build_artifact(manifest["libraries"]["pipeann"], library)]
        native_artifacts += [authenticate_build_artifact(entry) for name, entry in manifest["libraries"].items()
                             if name != "pipeann"]
        link_paths = [entry["resolved"] for entry in native_artifacts]
        source = config.path_value("PipeANN", "SourceDirectory")
        if "native_correctness" in manifest:
            correctness = pipeann_page_lifetime.validate(source)
            require(manifest["native_correctness"] == correctness["binding"]
                    and manifest["status"] == "complete" and manifest["inputs_captured_before_compile"] is True
                    and manifest["inputs_verified_unchanged_after_compile"] is True,
                    "PipeANN client does not bind its completed authorized correctness build")
            original = authenticate_build_artifact(manifest["original_library"])
            require(original["sha256"] == correctness["proof"]["original_library"]["sha256"],
                    "PipeANN correction changed its original algorithm-base archive")
            native_artifacts.append(original)
            common, link, _ = pipeann_page_lifetime.native_recipe(source)
            build_binary = authenticate_build_artifact(manifest["build_binary"])
            require(build_binary["sha256"] == installed["sha256"], "PipeANN installed client differs from its build")
            if native_short_results(config, engine):
                from threeway_native import build_pipeann_short_results

                require("native_result_policy" in manifest, "Missing compiled native-count protocol")
                result_policy = build_pipeann_short_results.validate(manifest["native_result_policy"], source)
                require(result_policy["proof"]["binary"] == manifest["build_binary"],
                        "Native-count protocol proof belongs to another executable")
                expected = result_policy["command"]
                source_names = build_pipeann_short_results.HELPERS
            else:
                require("native_result_policy" not in manifest, "Unregistered native-count protocol")
                expected = [
                    *common, '-DTHREEWAY_PIPEANN_SOURCE="' + str(source) + '"',
                    '-DTHREEWAY_PIPEANN_LIBRARY_SHA256="' + native_artifacts[0]["sha256"] + '"',
                    str(native_directory / "pipeann_bench.cpp"), *link, "-o", build_binary["path"],
                ]
                source_names += ("build_pipeann_page_lifetime.py", "pipeann_page_lifetime.py",
                                 "pipeann_page_lifetime.patch")
            require(command == expected, "Corrected PipeANN client compiler/linker recipe changed")
        else:
            require(not native_short_results(config, engine)
                    and "native_result_policy" not in manifest
                    and not (source.parent / pipeann_page_lifetime.PROOF).exists(),
                    "Undeclared PipeANN native correctness replacement")
    require(set(link_paths).issubset(command), "Native build argv does not link its authenticated original core")
    indexed = {str(Path(entry["path"]).resolve(strict=True)): entry for entry in sources}
    require(len(indexed) == len(sources), "Duplicate native source provenance")
    if diskann_build_admission or loader is not None:
        require(manifest["status"] == "complete" and manifest["inputs_captured_before_compile"] is True
                and manifest["inputs_verified_unchanged_after_compile"] is True
                and str((native_directory / source_names[0]).resolve()) in command,
                "All-label admission executable lacks an authenticated completed build")
    source_records = []
    for name in source_names:
        path = (native_directory / name).resolve(strict=True)
        require(str(path) in indexed, f"Missing compiled native source identity: {name}")
        source_records.append(authenticate_build_artifact(indexed[str(path)], path))
    if engine == "Filtered_DiskANN" or correctness is not None:
        generated = ({entry["path"]: entry for entry in result_policy["sources"].values()}
                     if result_policy is not None else {})
        for path, entry in indexed.items():
            if path in generated:
                require(entry == generated[path], "Generated native-count client source changed")
                authenticate_build_artifact(entry, path)
                continue
            require(Path(path).parent == native_directory.resolve() and Path(path).name in NATIVE_CODE_FILES,
                    "Native source dependency is not included in the frozen native closure")
            if Path(path).name not in source_names:
                source_records.append(authenticate_build_artifact(entry, path))
        require(set(generated).issubset(indexed), "Missing generated native-count client source")
    return {"manifest": file_record(manifest_path, True), "binary": installed, "compiler": compiler,
            "command": command, "sources": source_records, "native_artifacts": native_artifacts,
            "reference_binary": reference_binary, "loader": loader, "correctness": correctness,
            "result_policy": result_policy}


def verify_snapshot_imports(directory):
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    subprocess.run([sys.executable, "-B", str(directory / "run_sift1b_threeway.py"), "--help"],
                   cwd=directory, env=environment, stdout=subprocess.DEVNULL, check=True)


def index_files(config, include_diskann=False):
    paths = []
    spann = config.path_value("SPANN", "IndexDirectory")
    visited = set()
    for directory, subdirs, names in os.walk(spann, followlinks=True):
        stat = Path(directory).stat()
        key = (stat.st_dev, stat.st_ino)
        if key in visited:
            subdirs.clear()
            continue
        visited.add(key)
        paths.extend(Path(directory) / name for name in names if (Path(directory) / name).is_file())
    prefix = config.path_value("PipeANN", "IndexPrefix")
    paths.extend(path for path in prefix.parent.glob(prefix.name + "*") if path.is_file())
    require((Path(str(prefix) + "_mem.index")).is_file(), "Missing required PipeANN mem_L=10 entry index")
    require(len(paths) > 10, "Missing native index inventory")
    if include_diskann:
        prefix = config.path_value("DiskANN", "IndexPrefix")
        for suffix in ("_disk.index", "_disk.index_labels.txt", "_disk.index_labels_map.txt",
                       "_disk.index_labels_to_medoids.txt", "_pq_pivots.bin", "_pq_compressed.bin"):
            path = Path(str(prefix) + suffix)
            require(path.is_file(), f"Missing completed DiskANN artifact: {path}")
            paths.append(path)
    return sorted(set(paths))


def process_identity(pid):
    directory = Path("/proc") / str(pid)
    try:
        stat = (directory / "stat").read_text()
        command = (directory / "cmdline").read_bytes()
    except FileNotFoundError:
        return None
    fields = stat[stat.rfind(")") + 2:].split()
    if fields[0] == "Z":
        return None
    return {"pid": int(pid), "start_ticks": int(fields[19]),
            "command_sha256": hashlib.sha256(command).hexdigest()}


def aio_capacity(config, maximum=None, used=None):
    maximum = int(Path("/proc/sys/fs/aio-max-nr").read_text()) if maximum is None else maximum
    used = int(Path("/proc/sys/fs/aio-nr").read_text()) if used is None else used
    reserve = config.section("Throughput").getint("AIOReserve")
    per_thread = config.section("DiskANN").getint("AIOEventsPerThread")
    capacity = max(0, (maximum - used - reserve) // per_thread)
    return {"maximum": maximum, "used": used, "reserve": reserve,
            "events_per_thread": per_thread, "safe_threads": capacity}


def resources(pid=None):
    fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    result = {"time_utc": utc_now(), "memory_available_bytes": int(fields["MemAvailable"].split()[0]) * 1024,
              "load_average": list(os.getloadavg()),
              "cpu_ticks": Path("/proc/stat").read_text().splitlines()[0],
              "diskstats": Path("/proc/diskstats").read_text(),
              "aio_max_nr": int(Path("/proc/sys/fs/aio-max-nr").read_text()),
              "aio_nr": int(Path("/proc/sys/fs/aio-nr").read_text())}
    if pid is not None:
        try:
            status = dict(line.split(":", 1) for line in (Path("/proc") / str(pid) / "status").read_text().splitlines())
            result.update(pid=pid, rss_bytes=int(status.get("VmRSS", "0 kB").split()[0]) * 1024,
                          peak_rss_bytes=int(status.get("VmHWM", "0 kB").split()[0]) * 1024,
                          native_threads=int(status.get("Threads", "0")))
            result["process_io"] = (Path("/proc") / str(pid) / "io").read_text()
        except (FileNotFoundError, ProcessLookupError):
            result["process_exited"] = True
    return result


def prepare(config, *, historical_config=None):
    reject_benchmark_environment()
    require(not config.root.exists(), f"Refusing existing campaign directory: {config.root}")
    if historical_config is not None:
        for section in ("Dataset", "History", "Benchmark", "Single",
                        *(f"Scenario.{name}" for name in SCENARIOS)):
            require(dict(config.section(section)) == dict(historical_config.section(section)),
                    f"Historical registration changed [{section}]")
    rows, historical = read_history(config if historical_config is None else historical_config)
    source_vectors = open_vectors(config.path_value("Dataset", "Vectors"), "UInt8", 128)
    queries = open_vectors(config.path_value("Dataset", "Queries"), "UInt8", 128, limit=1000)
    require(source_vectors.source_rows == 10**9, "Base file is not the full SIFT1B")
    attributes = open_attributes(config.path_value("Dataset", "Attributes"), 10**9, 2)
    payload = np.asarray(queries.data).tobytes()
    require(hashlib.sha256(payload).hexdigest() == config.section("Dataset")["QueryPayloadSHA256"],
            "First 1000 queries differ from the existing comparison")
    truths = {}
    for name in SCENARIOS:
        truth = np.load(config.path_value(f"Scenario.{name}", "Truth"), mmap_mode="r", allow_pickle=False)
        require(truth.shape == (1000, 10), f"Groundtruth cohort/top-k differs: {name}")
        validate_ids(config, name, truth, attributes)
        truths[name] = truth
    binaries = {engine: config.path_value(SECTIONS[engine], "BenchmarkBinary") for engine in ENGINES}
    code = {name: HERE / name for name in CODE_FILES}
    code.update({f"threeway_native/{name}": HERE / "threeway_native" / name for name in NATIVE_CODE_FILES})
    build_records = {engine: path.with_name(path.name + ".build.json") for engine, path in binaries.items()}
    for path in list(binaries.values()) + list(build_records.values()) + list(code.values()):
        require(path.is_file(), f"Missing campaign client/code: {path}")
    for path in binaries.values():
        require(os.access(path, os.X_OK), f"Native client is not executable: {path}")
    native_builds = {engine: validate_native_build(config, engine, path, build_records[engine])
                    for engine, path in binaries.items()}
    require("sha256:" + native_builds["SPTAG_adaptive"]["reference_binary"]["sha256"]
            == historical["engines"]["SPTAG_adaptive"]["runtime_id"],
            "SPANN client core provenance differs from the authenticated historical runtime")
    dependencies = {engine: native_dependencies(path) for engine, path in binaries.items()}
    dependency_records = {
        path: file_record(path, True) for path in sorted({path for paths in dependencies.values() for path in paths.values()})
    }
    source_inputs = [config.path_value("Dataset", key) for key in ("Vectors", "Queries", "Attributes")]
    source_inputs += [config.path_value(f"Scenario.{name}", "Truth") for name in SCENARIOS]
    source_inputs += config.config_files()
    source_inputs += [config.path_value("History", key) for key in ("Summary", "Registration", "AssemblyManifest")]
    protected = [file_record(path) for path in sorted(set(source_inputs + index_files(config)))]
    protected.extend(dependency_records.values())
    for build in native_builds.values():
        protected.extend([build["manifest"], build["binary"], *build["native_artifacts"]])
        if build["loader"] is not None:
            protected.extend(file_record(entry["path"], True) for entry in build["loader"]["identities"])
        if build["correctness"] is not None:
            protected.extend(file_record(entry["path"], True) for entry in build["correctness"]["protected"])
        if build["result_policy"] is not None:
            protected.extend(file_record(entry["path"], True) for entry in build["result_policy"]["protected"])
    binary_sources = {engine: file_record(path, True) for engine, path in binaries.items()}
    code_sources = {name: file_record(path, True) for name, path in code.items()}
    for build in native_builds.values():
        for source in build["sources"]:
            require(source == code_sources["threeway_native/" + Path(source["path"]).name],
                    "Native compilation and frozen source snapshot disagree")
    controller = process_identity(config.section("Run").getint("BuildControllerPID"))
    build_root = config.path_value("Run", "BuildDirectory")
    require(controller is not None or (build_root / "completion.json").is_file(),
            "Index is incomplete and its registered controller is not alive")
    require(len(os.sched_getaffinity(0)) == 96, "The registered common 96-CPU budget is not available")
    config.root.mkdir(parents=True)
    for directory in ("inputs", "history", "config", "code", "runtime", "logs", "plans"):
        (config.root / directory).mkdir()
    shutil.copy2(config.path, config.root / "config/benchmark.ini")
    for path in config.config_files()[1:]:
        target = config.root / "config/filters" / path.name
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(path, target)
    for name, path in code.items():
        target = config.root / "code" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        require(sha256_file(target) == code_sources[name]["sha256"], "Source code changed during snapshot")
    verify_snapshot_imports(config.root / "code")
    frozen_binaries = {}
    runtime_dependencies = {}
    for engine, path in binaries.items():
        target = config.root / "runtime" / path.name
        shutil.copy2(path, target)
        require(sha256_file(target) == binary_sources[engine]["sha256"], "Native executable changed during snapshot")
        require(native_dependencies(target, cwd=config.prepared) == dependencies[engine],
                f"Copied {engine} client resolves different libraries; use stable native RPATHs")
        frozen_binaries[engine] = identity(target, True)
        shutil.copy2(build_records[engine], target.with_name(target.name + ".build.json"))
        runtime_dependencies[engine] = []
        if native_builds[engine]["result_policy"] is not None:
            for name, entry in native_builds[engine]["result_policy"]["sources"].items():
                archived = config.root / "runtime/client-source" / engine / name
                archived.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry["path"], archived)
                require(sha256_file(archived) == entry["sha256"], "Native-count source changed during snapshot")
        for name, source in dependencies[engine].items():
            archived = config.root / "runtime/dependencies" / engine / name
            archived.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, archived)
            require(sha256_file(archived) == dependency_records[source]["sha256"],
                    f"Runtime library changed during snapshot: {source}")
            runtime_dependencies[engine].append({
                "name": name, "live": dependency_records[source], "archived": str(archived),
            })
    for key, name in (("Summary", "summary.csv"), ("Registration", "registration.json"),
                      ("AssemblyManifest", "assembly_manifest.json")):
        shutil.copy2(config.path_value("History", key), config.root / "history" / name)
    write_bin(config.prepared / "query.u8bin", queries.data, "u1")
    prefix = config.path_value("PipeANN", "IndexPrefix")
    for suffix in ("label.0", "label.0.filter", "label.1", "label.1.quantize"):
        target = Path(str(prefix) + "." + suffix).resolve(strict=True)
        (config.prepared / ("base." + suffix)).symlink_to(target)
    for name, truth in truths.items():
        write_bin(config.prepared / f"gt_{name}.u32bin", truth, "<u4")
        contract = scenario_contract(config, name)
        for filename, values, kind in contract["bindings"].values():
            write_binding(config.prepared / filename, values, 1000, kind)
    registration = {
        "schema_version": 1, "dataset": "SIFT1B", "corpus_count": 10**9, "query_count": 1000,
        "metric": "squared L2", "recall_target": max(config.targets()), "recall_targets": config.targets(),
        "throughput_min_seconds": config.section("Throughput").getfloat("MinimumSeconds"),
        "throughput_core_budget": 96, "throughput_thread_grid": config.threads("throughput"),
        "scenarios": list(SCENARIOS), "scenario_metadata": historical["scenario_metadata"], "engines": list(ENGINES),
        "query_payload_sha256": hashlib.sha256(payload).hexdigest(),
        "caption_note": "Historical SPANN/PipeANN single-thread measurements are reused, not freshly paired. "
                        "Filtered-DiskANN uses a categorical-focused R64/L1/FilteredL100/PQ32 graph. "
                        "Native I/O: SPANN buffered; PipeANN and DiskANN direct. "
                        "Throughput repeats a warm fixed 1000-query working set, not a cold random-corpus workload.",
        "aio_limit_note": "Existing host AIO settings are unchanged. DiskANN reserves 1024 slots/thread; "
                          "unavailable concurrency is reported explicitly. Peaks are maximum observed feasible "
                          "throughput, not unconstrained algorithm maxima.",
        "throughput_memory_policy": "interleave across the same four NUMA nodes for every engine",
        "throughput_validation_policy": "All-cohort ID/predicate/recall accounting is included in continuous "
                                        "wall time; exact captured-vector checks occur after the native process.",
        "native_io_policy": {"SPTAG_adaptive": "buffered", "PipeANN": "direct", "Filtered_DiskANN": "direct"},
        "selection_policy": "Smallest INI-declared L with minimum single-thread repetition recall >= target; "
                            "no interpolation or refitting. Shared controls are measured once for both target views.",
    }
    loader = native_builds["Filtered_DiskANN"]["loader"]
    if loader is not None:
        registration["native_loader_policy"] = loader["binding"]
        registration["caption_note"] += (
            " DiskANN uses an explicitly approved linear label-loader fix; graph, PQ, labels and "
            "search algorithms/parameters are unchanged, and loading is outside query timing.")
    correctness = native_builds["PipeANN"]["correctness"]
    if correctness is not None:
        registration["native_pipeann_correctness"] = correctness["binding"]
        registration["caption_note"] += (
            " PipeANN throughput uses the approved page-lifetime correctness repair in the timed query path; "
            "the graph, search budgets, candidate selection and stopping rules are unchanged. Historical "
            "PipeANN single-thread points predate this correction, so this is not same-binary scaling.")
    result_policy = native_builds["PipeANN"]["result_policy"]
    if result_policy is not None:
        registration["native_pipeann_result_policy"] = result_policy["binding"]
        registration["caption_note"] += (
            " PipeANN accepts its native 0..K returned prefix without padding or result repair. "
            "Missing neighbors contribute zero to Recall@10; the denominator remains completed queries times 10. "
            "Native return/missing counts are retained in raw results and throughput tables.")
    write_json(config.root / "registration.json", registration)
    frozen = [identity(path, True) for directory in ("code", "config", "history", "runtime")
              for path in sorted((config.root / directory).rglob("*")) if path.is_file()]
    input_records = [file_record(path) for path in sorted(config.prepared.iterdir())]
    manifest = {"schema_version": 1, "prepared_at_utc": utc_now(), "controller": controller,
                "protected": protected, "prepared_inputs": input_records, "frozen": frozen,
                "binaries": frozen_binaries, "resource_policy": "current-host-limit",
                "binary_sources": binary_sources, "code_sources": code_sources, "native_builds": native_builds,
                "runtime_dependencies": runtime_dependencies,
                "loader_environment": {"LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH")},
                "runtime_policy": "Native ELF/RPATH and loader environment unchanged; live dependencies verified "
                                  "and copies archived for provenance, not substituted into native search",
                "python": {"executable": sys.executable, "version": sys.version, "numpy": np.__version__},
                "historical_points": len(rows), "initial_resources": resources()}
    write_json(config.root / "manifest.json", manifest)
    write_json(config.root / "status.json", {"state": "prepared", "updated_at_utc": utc_now()})
    return config.root


def plan_single(config):
    result = []
    for repeat in range(1, config.repeats("single") + 1):
        scenarios = SCENARIOS if repeat % 2 else tuple(reversed(SCENARIOS))
        controls = list(range(len(config.controls("Filtered_DiskANN"))))
        if repeat % 2 == 0:
            controls.reverse()
        for name in scenarios:
            if config.section(f"Scenario.{name}")["Kind"] == "mixed_dnf":
                continue
            result += [dict(scenario=name, control_index=index, thread_index=0, repeat=repeat)
                       for index in controls]
    return result


def select_operating_points(config, rows):
    result, unavailable = [], []
    for engine in ENGINES:
        for name in SCENARIOS:
            for target in config.targets():
                matching = [row for row in rows if row["engine"] == engine and row["scenario"] == name
                            and float(row["recall_min"]) >= target]
                if not matching:
                    unsupported = engine == "Filtered_DiskANN" and name == "mixed_dnf"
                    unavailable.append({
                        "phase": "throughput", "scenario": name, "engine": engine,
                        "status": "unsupported_predicate" if unsupported else "recall_target_unmet",
                        "reason": "Original DiskANN has no mixed numeric DNF predicate" if unsupported else (
                            f"No registered single-thread budget attained Recall@10 >= {target} in every repetition"),
                        "recall_target": target, "threads": "",
                    })
                    continue
                selected = min(matching, key=lambda row: int(row["L"]))
                control = int(selected["L"])
                require(control in config.controls(engine), "Selected operating point is outside the INI")
                result.append({"engine": engine, "scenario": name, "recall_target": target, "L": control,
                               "control_index": config.controls(engine).index(control),
                               "selection_recall_min": float(selected["recall_min"])})
    return result, unavailable


def plan_throughput(config, engine, selections, capacity):
    points = sorted({(row["scenario"], row["control_index"]) for row in selections if row["engine"] == engine},
                    key=lambda pair: (SCENARIOS.index(pair[0]), pair[1]))
    allowed, unavailable = [], []
    for index, threads in enumerate(config.threads("throughput")):
        if engine == "Filtered_DiskANN" and threads > capacity["safe_threads"]:
            for name in dict.fromkeys(name for name, _ in points):
                unavailable.append({
                    "phase": "throughput", "scenario": name, "engine": engine, "status": "aio_limit",
                    "reason": f"Native allocation needs {threads * capacity['events_per_thread']} AIO slots; "
                              f"host maximum={capacity['maximum']}, occupied={capacity['used']}, "
                              f"reserve={capacity['reserve']}. No limit or source changes authorized.",
                    "recall_target": "", "threads": threads,
                })
        else:
            allowed.append(index)
    result = []
    for repeat in range(1, config.repeats("throughput") + 1):
        order = points if repeat % 2 else list(reversed(points))
        thread_order = allowed if repeat % 2 else list(reversed(allowed))
        for name, control_index in order:
            result += [dict(scenario=name, control_index=control_index, thread_index=index, repeat=repeat)
                       for index in thread_order]
    return result, unavailable


def write_plan(config, engine, phase, plan):
    path = config.root / "plans" / f"{engine}.{phase}.tsv"
    with path.open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=PLAN_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(plan)
    return identity(path, True)


def resolved_key(config, engine, phase, row):
    return (row["scenario"], config.controls(engine)[row["control_index"]],
            config.threads(phase)[row["thread_index"]], row["repeat"])


def native_short_results(config, engine):
    return engine == "PipeANN" and config.section("PipeANN").getboolean("AllowShortResults", fallback=False)


def validate_return_accounting(config, result):
    top_k = config.section("Benchmark").getint("TopK")
    for key in ("returned_neighbors", "missing_neighbors", "warmup_underfilled_queries", "warmup_missing_neighbors"):
        require(type(result.get(key)) is int and result[key] >= 0, "Native return counters must be nonnegative integers")
    require(result.get("result_policy") == NATIVE_SHORT_RESULT_POLICY,
            "Missing or unapproved native returned-prefix policy")
    require(result["returned_neighbors"] + result["missing_neighbors"] == result["queries"] * top_k
            and 0 <= result["underfilled_queries"] <= result["queries"]
            and result["underfilled_queries"] <= result["missing_neighbors"] <= result["underfilled_queries"] * top_k
            and 0 <= result["warmup_underfilled_queries"] <= result["warmup_queries"]
            and result["warmup_underfilled_queries"] <= result["warmup_missing_neighbors"]
            <= result["warmup_underfilled_queries"] * top_k, "Native short-result accounting failed")
    require(result["recall"] <= result["returned_neighbors"] / (result["queries"] * top_k) + 1e-12,
            "Recall was normalized by returned neighbors instead of fixed K")
    for key in ("first_counts", "last_counts"):
        require(isinstance(result.get(key), str) and result[key], "Missing actual native per-query counts")


def parse_results(config, engine, phase, path, plan):
    expected = [resolved_key(config, engine, phase, row) for row in plan]
    require(len(expected) == len(set(expected)), "Duplicate execution-plan job")
    results = []
    with Path(path).open(errors="strict") as stream:
        for line in stream:
            if line.startswith("THREEWAY_RESULT "):
                results.append(json.loads(line.removeprefix("THREEWAY_RESULT ")))
    require(len(results) == len(plan), f"Missing native measured jobs: {engine}/{phase}")
    for result, key in zip(results, expected):
        require(all(field in result for field in NATIVE_ERROR_COUNTS), "Missing native error counters")
        require(all(type(result[field]) is int for field in (
            "schema_version", "L", "threads", "repeat", "queries", "cohort_queries",
            "passes", "warmup_queries") + NATIVE_ERROR_COUNTS), "Native counters must be integers")
        short = native_short_results(config, engine)
        require(result["schema_version"] == (2 if short else 1)
                and result["engine"] == engine and result["phase"] == phase
                and (result["scenario"], result["L"], result["threads"], result["repeat"]) == key,
                "Native result order/controls differ from the ordinal plan")
        count = config.section("Benchmark").getint("QueryCount")
        errors = tuple(field for field in NATIVE_ERROR_COUNTS if not (short and field == "underfilled_queries"))
        require(all(result[field] == 0 for field in errors) and result["cohort_queries"] == count
                and result["warmup_queries"] == count and type(result["passes"]) is int and result["passes"] > 0
                and result["queries"] == result["passes"] * count, "Native query accounting failed")
        elapsed = finite(result["elapsed_seconds"], "elapsed_seconds", positive=True)
        qps = finite(result["qps"], "qps", positive=True)
        require(math.isclose(qps, result["queries"] / elapsed, rel_tol=1e-7),
                "QPS is not completed queries divided by continuous wall-clock elapsed")
        if phase == "single":
            require(result["passes"] == 1, "Single-thread historical protocol measures exactly one cohort")
        else:
            require(elapsed >= config.section("Throughput").getfloat("MinimumSeconds"),
                    "Native throughput pass is shorter than the required sustained duration")
        for field in ("recall", "first_recall", "last_recall", "recall_min_batch"):
            finite(result[field], field, high=1)
        require(result["recall_min_batch"] <= min(result["first_recall"], result["last_recall"], result["recall"]) + 1e-12,
                "Native minimum cohort recall is inconsistent")
        for field in ("elapsed_seconds", "qps", "recall", "first_recall", "last_recall", "recall_min_batch",
                      "mean_latency_us", "p50_latency_us", "p99_latency_us"):
            require(type(result[field]) in (int, float), f"Native {field} must be a JSON number")
            finite(result[field], field)
        if result["mean_ios"] is not None:
            require(type(result["mean_ios"]) in (int, float), "Native mean_ios must be numeric or null")
            finite(result["mean_ios"], "mean_ios")
        require(result["p50_latency_us"] <= result["p99_latency_us"], "Native latency quantiles are reversed")
        require(isinstance(result["latency_quantile_method"], str) and result["latency_quantile_method"],
                "Native approximate latency quantiles need their documented method")
        for field in ("first_ids", "first_distances", "last_ids", "last_distances"):
            require(isinstance(result[field], str) and result[field], f"Missing native capture path: {field}")
        if short:
            validate_return_accounting(config, result)
    return results


def validate_diskann_admission(config, phase, plan, expected_pid, load_seconds):
    path = config.root / "native" / "Filtered_DiskANN" / phase / "diskann-admission.json"
    require(path.is_file() and not path.is_symlink(), "Missing regular DiskANN admission certificate")
    certificate = read_json(path)
    top_k = config.section("Benchmark").getint("TopK")
    rows = config.section("Dataset").getint("VectorCount")
    require(config.section("DiskANN").getint("CacheNodes") == 0
            and all(config.controls("Filtered_DiskANN")[job["control_index"]] >= top_k for job in plan),
            "DiskANN admission assumptions disagree with the execution plan")
    scenarios = {
        name: {"kind": config.section(f"Scenario.{name}")["Kind"],
               "count": config.section(f"Scenario.{name}").getint("CandidateCount")}
        for name in {job["scenario"] for job in plan}
    }
    validate_diskann_admission_record(
        certificate, prefix=config.path_value("DiskANN", "IndexPrefix"),
        source_revision=config.section("DiskANN")["SourceRevision"], phase=phase,
        expected_pid=expected_pid, top_k=top_k, rows=rows, load_seconds=load_seconds, scenarios=scenarios)
    return identity(path, True)


def validate_diskann_admission_record(certificate, *, prefix, source_revision, phase,
                                     expected_pid, top_k, rows, load_seconds, scenarios):
    require(type(expected_pid) is int and expected_pid > 0, "Invalid DiskANN admission process PID")
    expected = {
        "schema_version": 1, "engine": "Filtered_DiskANN", "guard": "bounded-all-native-starts-v1",
        "source_revision": source_revision, "phase": phase,
        "status": "admitted", "error": "", "pid": expected_pid, "top_k": top_k, "native_points": rows,
        "assumptions_verified": True, "required_all_planned_L_at_least_K": True,
        "required_cache_nodes": 0, "required_frozen_points": 0, "required_reorder": False,
        "required_universal_labels": False, "required_nonempty_dummy_map": False,
        "native_io_limit": 2**32 - 1, "native_search_invocations": 0, "warmup_queries": 0,
        "measured_queries": 0, "native_index_loaded": True, "extra_label_read_passes": 1,
        "label_rows": rows, "temporary_membership_released": True, "finite_start_selection_verified": True,
    }
    integer_fields = ("label_values", "label_bytes", "temporary_membership_bytes",
                      "graph_records_read", "graph_bytes_read")
    number_fields = ("label_read_seconds", "witness_seconds", "native_load_seconds", "elapsed_seconds",
                     "pq_distance_upper_bound", "medoid_distance_upper_bound")
    other_fields = ("label_source", "label_fnv1a64", "witness_io", "planned_native_labels",
                    "sources", "absent_sources", "witnesses")
    require(isinstance(certificate, dict)
            and set(certificate) == set(expected) | set(integer_fields) | set(number_fields) | set(other_fields),
            "DiskANN admission certificate schema mismatch")
    for field, value in expected.items():
        require(type(certificate[field]) is type(value) and certificate[field] == value,
                f"DiskANN admission mismatch: {field}")
    for field in integer_fields:
        require(type(certificate[field]) is int and certificate[field] >= 0,
                f"DiskANN admission requires a nonnegative integer: {field}")
    for field in number_fields:
        require(type(certificate[field]) in (int, float), f"DiskANN admission requires a JSON number: {field}")
        finite(certificate[field], f"DiskANN admission {field}")
    require(certificate["label_values"] >= rows and certificate["label_bytes"] > 0
            and certificate["elapsed_seconds"] <= load_seconds + 1e-9
            and all(certificate[field] <= certificate["elapsed_seconds"] for field in
                    ("label_read_seconds", "witness_seconds", "native_load_seconds")),
            "DiskANN admission row/time accounting mismatch")
    for field in ("pq_distance_upper_bound", "medoid_distance_upper_bound"):
        require(certificate[field] < float(np.finfo(np.float32).max),
                "DiskANN admission distance bound can overflow")
    digest = certificate["label_fnv1a64"]
    require(isinstance(digest, str) and len(digest) == 16
            and all(character in "0123456789abcdef" for character in digest),
            "DiskANN admission label digest is malformed")
    require(isinstance(certificate["witness_io"], str) and certificate["witness_io"],
            "DiskANN admission must describe its untimed witness IO")

    prefix = str(prefix)
    required_sources = {prefix + suffix for suffix in DISKANN_REQUIRED_SOURCES}
    allowed_sources = required_sources | {prefix + suffix for suffix in DISKANN_OPTIONAL_SOURCES}
    sources = certificate["sources"]
    absent = certificate["absent_sources"]
    require(isinstance(sources, list) and isinstance(absent, list)
            and all(isinstance(item, str) for item in absent)
            and len(absent) == len(set(absent)), "DiskANN admission source inventory is malformed")
    recorded = {}
    for entry in sources:
        require(isinstance(entry, dict) and set(entry) ==
                {"path", "resolved", "bytes", "device", "inode", "mtime_ns", "ctime_ns"}
                and isinstance(entry["path"], str) and entry["path"] in allowed_sources
                and entry["path"] not in recorded, "DiskANN admission source does not belong to this index")
        source = Path(entry["path"])
        stat = source.stat()
        actual = {"resolved": str(source.resolve(strict=True)), "bytes": stat.st_size, "device": stat.st_dev,
                  "inode": stat.st_ino, "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns}
        require(all(type(entry[key]) is type(value) and entry[key] == value for key, value in actual.items()),
                f"DiskANN admission source changed: {source}")
        recorded[entry["path"]] = entry
    require(required_sources.issubset(recorded) and set(absent) == allowed_sources - set(recorded)
            and all(not Path(name).exists() and not Path(name).is_symlink() for name in absent),
            "DiskANN admission source inventory is incomplete or changed")
    label_source = prefix + "_disk.index_labels.txt"
    require(certificate["label_source"] == label_source
            and certificate["label_bytes"] == recorded[label_source]["bytes"],
            "DiskANN admission did not scan the registered native label source")

    witnesses = certificate["witnesses"]
    require(isinstance(witnesses, list) and witnesses, "DiskANN admission has no reachability witnesses")
    seen, witnessed_scenarios, label_counts = set(), set(), {}
    graph_records = 0
    for witness in witnesses:
        require(isinstance(witness, dict) and set(witness) == {
            "scenario", "start", "native_label", "required", "reachable_witness_nodes",
            "graph_records_read", "status", "error", "nodes", "parent_nodes",
        }, "Malformed DiskANN admission witness")
        name, start = witness["scenario"], witness["start"]
        require(isinstance(name, str) and name in scenarios
                and type(start) is int and 0 <= start < rows and (name, start) not in seen,
                "DiskANN admission witness has an unplanned or duplicate start")
        seen.add((name, start))
        witnessed_scenarios.add(name)
        require(witness["status"] == "admitted" and witness["error"] == ""
                and all(type(witness[field]) is int and witness[field] == top_k
                        for field in ("required", "reachable_witness_nodes"))
                and type(witness["graph_records_read"]) is int
                and 0 < witness["graph_records_read"] <= top_k, "DiskANN admission witness is underfilled")
        nodes, parents = witness["nodes"], witness["parent_nodes"]
        require(isinstance(nodes, list) and isinstance(parents, list) and len(nodes) == len(parents) == top_k
                and all(type(node) is int and 0 <= node < rows for node in nodes + parents)
                and len(set(nodes)) == top_k and nodes[0] == parents[0] == start
                and all(parents[index] in nodes[:index] for index in range(1, top_k)),
                "DiskANN admission witness is not a distinct rooted reachability tree")
        graph_records += witness["graph_records_read"]
        scenario = scenarios[name]
        label = witness["native_label"]
        if scenario["kind"] == "unfilter":
            require(label is None, "Unfiltered DiskANN admission must not use a label")
        else:
            require(scenario["kind"] == "categorical" and type(label) is int and 0 <= label < 2**32,
                    "DiskANN admission witness has an unsupported predicate")
            count = scenario["count"]
            if "native_label" in scenario:
                require(label == scenario["native_label"], "DiskANN admission converted label mismatch")
            require(count >= top_k and label_counts.setdefault(label, count) == count,
                    "DiskANN admission native label counts conflict")
    require(witnessed_scenarios == set(scenarios), "DiskANN admission omits a planned scenario")
    require(0 < certificate["graph_records_read"] <= graph_records
            and certificate["graph_bytes_read"] >= 4 * certificate["graph_records_read"],
            "DiskANN admission witness IO accounting mismatch")
    planned_labels = certificate["planned_native_labels"]
    require(isinstance(planned_labels, list), "DiskANN admission native labels must be a list")
    actual_counts = {}
    for entry in planned_labels:
        require(isinstance(entry, dict) and set(entry) == {"label", "rows"}
                and type(entry["label"]) is int and type(entry["rows"]) is int
                and 0 <= entry["label"] < 2**32 and top_k <= entry["rows"] <= rows
                and entry["label"] not in actual_counts, "DiskANN admission native label record is malformed")
        actual_counts[entry["label"]] = entry["rows"]
    require(actual_counts == label_counts, "DiskANN admission native membership counts disagree with the plan")
    membership_bytes = certificate["temporary_membership_bytes"]
    require((membership_bytes > 0) == bool(label_counts)
            and membership_bytes <= 8 * ((rows + 63) // 64) * len(label_counts),
            "DiskANN admission temporary membership allocation is not bounded")


def build_input_record(path, full_hash=False):
    record = file_record(path, full_hash)
    return {key: record[key] for key in
            ("path", "resolved", "bytes", "device", "inode", "mtime_ns", "first_4096_sha256")} | {
        "ctime_ns": Path(path).stat().st_ctime_ns,
        "sha256": record["sha256"] if full_hash else None,
    }


def original_build_identity(path):
    record = build_input_record(path)
    return {key: record[key] for key in
            ("device", "inode", "bytes", "mtime_ns", "first_4096_sha256")} | {"path": record["resolved"]}


class DiskANNBuildValidation:
    """Read-only binding to the original build INI, not another search profile."""

    def __init__(self, config_path, loader_policy=None):
        self.config = Config(config_path)
        cfg = self.config
        self.root = cfg.path_value("Run", "OutputDirectory")
        require(cfg.path == self.root / "config.ini", "Admission needs the original build-directory INI")
        self.manifest = read_json(self.root / "manifest.json")
        source = self.root / "build_categorical_from_pq.py"
        require(sha256_file(cfg.path) == self.manifest["config_sha256"]
                and sha256_file(source) == self.manifest["runner_sha256"],
                "Original build INI/controller authentication failed")
        self.revision = cfg.section("DiskANN")["SourceRevision"]
        require(self.revision == self.manifest["native_source_revision"]
                and cfg.section("DiskANN")["LabelType"] == "uint"
                and self.manifest["categorical_only"] is True and self.manifest["disk_pq"] is False
                and self.manifest["universal_label"] is None
                and self.manifest["graph_reused"] is False and self.manifest["search_pq_reused"] is True,
                "All-label admission requires the original categorical build")
        self.prefix = self.root / "sift1b"
        self.rows, self.dimension = native_header(cfg.path_value("Inputs", "Vectors"), 1)
        self.columns = cfg.section("Inputs").getint("AttributeColumns")
        self.column = cfg.section("Inputs").getint("CategoricalColumn")
        self.k = cfg.section("Run").getint("ValidationK")
        self.search_l = cfg.section("Run").getint("ValidationL")
        self.threads = cfg.section("Run").getint("ValidationThreads")
        self.queries_per_label = cfg.section("Run").getint("ValidationQueriesPerLabel")
        self.labels = cfg.integer_list("Run", "ValidationLabels")
        query_rows, query_dimension = native_header(cfg.path_value("Inputs", "Queries"), 1)
        require(0 < self.k <= self.search_l and self.threads > 0
                and 0 < self.queries_per_label <= query_rows and query_dimension == self.dimension
                and self.columns > 0 and 0 <= self.column < self.columns
                and len(self.labels) == len(set(self.labels))
                and all(0 <= label < 2**32 for label in self.labels)
                and (self.rows, self.dimension) == (self.manifest["vectors"], self.manifest["dimension"])
                and cfg.path_value("Inputs", "Attributes").stat().st_size == self.rows * self.columns * 4,
                "Invalid original build-validation controls or input schema")
        metadata = read_json(cfg.path_value("Inputs", "AttributeManifest"))
        counts_path = cfg.path_value("Inputs", "Counts")
        require((metadata["vector_count"], metadata["dimension"], metadata["attribute_columns"],
                 metadata["limited_tag_column"]) == (self.rows, self.dimension, self.columns, self.column)
                and sha256_file(counts_path) == metadata["files"]["counts"]["sha256"],
                "Original categorical counts/schema failed authentication")
        with counts_path.open(newline="") as stream:
            entries = list(csv.DictReader(stream, delimiter="\t"))
        self.counts = {int(row["attribute_id"]): int(row["count"]) for row in entries}
        require(len(self.counts) == len(entries) and sum(self.counts.values()) == self.rows
                and all(0 <= label < 2**32 and 0 <= count <= self.rows for label, count in self.counts.items())
                and all(self.counts.get(label, 0) >= self.k for label in self.labels),
                "Original categorical validation has duplicate, absent or underfilled configured labels")
        self.groups = [(label, self.queries_per_label) for label in self.labels]
        self.groups += [(label, 1) for label in sorted(self.counts)
                        if label not in self.labels and self.counts[label] >= self.k]
        self.query_calls = sum(count for _, count in self.groups)
        expected_inputs = {cfg.path_value("Inputs", name) for name in (
            "Vectors", "Queries", "Attributes", "Counts", "AttributeManifest",
            "PreparedLabels", "PreparedLabelsManifest")}
        expected_inputs.update(Path(cfg.section("Inputs")["PQPrefix"] + suffix).resolve(strict=True)
                               for suffix in ("_pq_pivots.bin", "_pq_compressed.bin"))
        expected_inputs.update(cfg.path_value("Truth", name) for name in cfg.section("Truth"))
        expected_inputs.add(cfg.path_value("Run", "PreflightDiagnostics"))
        require({Path(name).resolve(strict=True) for name in self.manifest["inputs"]} == expected_inputs,
                "Original build input inventory is incomplete")
        self.input_hashes = {cfg.path: True, self.root / "manifest.json": True, source: True}
        for name, expected in self.manifest["inputs"].items():
            require(original_build_identity(name) == expected, f"Original build input changed: {name}")
            self.input_hashes[Path(name)] = Path(name) in {
                counts_path, cfg.path_value("Inputs", "AttributeManifest")}
        require(set(self.manifest["binaries"]) ==
                {"build_memory_index", "create_disk_layout", "search_disk_index"},
                "Original native executable inventory is incomplete")
        for name, expected in self.manifest["binaries"].items():
            binary = cfg.path_value("DiskANN", "BinaryDirectory") / name
            require(original_build_identity(binary) == expected["identity"]
                    and sha256_file(binary) == expected["sha256"], f"Original native executable changed: {name}")
            self.input_hashes[binary] = True
        self.loader = None
        if loader_policy is not None:
            self.loader = native_loader_provenance.validate(
                loader_policy, cfg.path_value("DiskANN", "SourceDirectory"), self.revision,
                cfg.path_value("DiskANN", "BinaryDirectory") / "search_disk_index")
            require(self.loader["proof"]["original_stock_binary"]["sha256"] ==
                    self.manifest["binaries"]["search_disk_index"]["sha256"],
                    "Loader proof does not preserve the original registered stock caller")

    def stock_binary(self):
        return (Path(self.loader["binding"]["stock_binary"]) if self.loader is not None else
                self.config.path_value("DiskANN", "BinaryDirectory") / "search_disk_index")

    def admission_command(self, binary, output):
        command = list(map(str, [binary, "--config", self.config.path, "--certificate", output / "admission.json"]))
        if self.loader is not None:
            command += ["--loader-policy", self.loader["binding"]["path"]]
        return command

    def validation_parser(self):
        parser = copy.deepcopy(self.config.parser)
        if self.loader is not None:
            stock = self.stock_binary()
            require(stock.name == "search_disk_index", "Loader stock executable must preserve the original name")
            parser["DiskANN"]["BinaryDirectory"] = str(stock.parent)
        return parser

    def stock_command(self, output):
        cfg = self.config
        return list(map(str, [
            self.stock_binary(),
            "--data_type", "uint8", "--dist_fn", "l2", "--index_path_prefix", self.prefix,
            "--query_file", output / "validation_queries.u8bin",
            "--query_filters_file", output / "validation_filters.txt", "--label_type", "uint",
            "--result_path", output / "validation", "-K", self.k, "-L", self.search_l,
            "-W", 2, "-T", self.threads, "--num_nodes_to_cache", 0,
        ]))


def validate_build_admission(context, path, expected_pid, library):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "Missing regular all-label admission certificate")
    certificate = read_json(path)
    expected = {
        "schema_version": 1, "engine": "Filtered_DiskANN",
        "purpose": "original-all-label-build-validation-admission", "status": "admitted", "error": "",
        "pid": expected_pid, "native_search_invocations": 0, "warmup_queries": 0, "measured_queries": 0,
        "config": str(context.config.path), "source_revision": context.revision,
        "linked_library": str(Path(library).resolve(strict=True)), "index_prefix": str(context.prefix),
        "stock_search_binary": str(context.stock_binary()),
        "vector_count": context.rows, "dimension": context.dimension,
        "attribute_columns": context.columns, "categorical_column": context.column,
        "validation_k": context.k, "validation_l": context.search_l, "validation_threads": context.threads,
        "beam_width": 2, "cache_nodes": 0, "native_io_limit": 2**32 - 1,
        "stock_warmup_enabled": False, "automatic_beam_tuning": False, "reorder": False,
        "validation_queries_per_configured_label": context.queries_per_label,
        "planned_stock_query_calls": context.query_calls,
        "selection_policy": "configured labels in INI order, then other count>=K labels in numeric order",
        "native_index_loaded": True, "converted_labels_verified": True,
    }
    other = {"aio", "elapsed_seconds", "configured_labels", "selected_labels", "skipped_below_k",
             "input_identities", "admission"}
    if context.loader is not None:
        expected["loader_policy"] = context.loader["binding"]
        require(Path(library).resolve(strict=True) == Path(context.loader["binding"]["library"]),
                "Admission used a library outside the approved loader policy")
    require(isinstance(certificate, dict) and set(certificate) == set(expected) | other,
            "All-label admission certificate schema mismatch")
    for key, value in expected.items():
        require(type(certificate[key]) is type(value) and certificate[key] == value,
                f"All-label admission mismatch: {key}")
    elapsed = certificate["elapsed_seconds"]
    require(type(elapsed) in (int, float), "All-label admission elapsed time must be numeric")
    finite(elapsed, "all-label admission elapsed time")
    require(certificate["configured_labels"] == context.labels
            and all(type(label) is int for label in certificate["configured_labels"]),
            "All-label admission configured labels changed")
    skipped = [{"external_label": label, "source_count": count}
               for label, count in sorted(context.counts.items()) if count < context.k]
    require(certificate["skipped_below_k"] == skipped, "All-label admission silently skipped eligible labels")
    selected = certificate["selected_labels"]
    require(isinstance(selected, list) and len(selected) == len(context.groups),
            "All-label admission omits selected labels")
    scenarios, native_labels = {}, set()
    for record, (label, queries) in zip(selected, context.groups):
        fields = {"external_label": label, "source_count": context.counts[label],
                  "configured": label in context.labels, "planned_queries": queries,
                  "scenario": f"label_{label}", "native_count": context.counts[label]}
        require(isinstance(record, dict) and set(record) == set(fields) | {"native_label"}
                and all(type(record[key]) is type(value) and record[key] == value for key, value in fields.items())
                and type(record["native_label"]) is int and 0 <= record["native_label"] < 2**32
                and record["native_label"] not in native_labels, "All-label admission selection/mapping mismatch")
        native_labels.add(record["native_label"])
        scenarios[record["scenario"]] = {
            "kind": "categorical", "count": context.counts[label], "native_label": record["native_label"]}
    aio = certificate["aio"]
    require(isinstance(aio, dict) and set(aio) == {"used", "maximum", "required", "events_per_thread"}
            and all(type(value) is int and value >= 0 for value in aio.values())
            and aio["required"] == 1024 * context.threads and aio["events_per_thread"] == 1024
            and aio["used"] + aio["required"] <= aio["maximum"], "All-label admission AIO assumptions changed")
    identities = certificate["input_identities"]
    require(isinstance(identities, list) and len(identities) == len(context.input_hashes),
            "All-label admission original input inventory is incomplete")
    seen = set()
    for entry in identities:
        require(isinstance(entry, dict) and isinstance(entry.get("path"), str),
                "Malformed all-label input identity")
        name = Path(entry["path"])
        require(name in context.input_hashes and name not in seen, "Unexpected/duplicate all-label input identity")
        seen.add(name)
        actual = build_input_record(name, context.input_hashes[name])
        require(set(entry) == set(actual)
                and all(type(entry[key]) is type(value) and entry[key] == value for key, value in actual.items()),
                f"All-label admission input changed: {name}")
    validate_diskann_admission_record(
        certificate["admission"], prefix=context.prefix, source_revision=context.revision,
        phase="build-validation", expected_pid=expected_pid, top_k=context.k, rows=context.rows,
        load_seconds=elapsed, scenarios=scenarios)
    return identity(path, True)


def load_layout_continuation_config(path):
    config = Config(path)
    allowed = {
        "Continuation": {"OriginalConfig", "PreviousLayoutDirectory", "PreviousCampaignDirectory", "OutputDirectory"},
        "Run": {"MinimumFreeDiskGiB"},
        "Authorization": {"Action"},
    }
    require(set(config.parser.sections()) == set(allowed), "Unexpected layout continuation INI sections")
    for section, keys in allowed.items():
        require(set(config.section(section)) == {key.lower() for key in keys},
                f"Unexpected layout continuation keys in [{section}]")
    require(config.section("Authorization")["Action"] == DISK_RESERVE_AUTHORIZATION
            and config.section("Run").getint("MinimumFreeDiskGiB") == 64,
            "Only the explicitly approved 64-GiB post-build disk policy is authorized")
    require(all(Path(value).is_absolute() for value in config.section("Continuation").values()),
            "Continuation paths must remain absolute when frozen unchanged")
    return config


def layout_disk_reserve_policy(original_config, directory):
    path = directory / "resource-policy.json"
    if not path.exists() and not path.is_symlink():
        return None
    require(path.is_file() and not path.is_symlink(), "Layout resource policy must be a regular file")
    config = load_layout_continuation_config(directory / "continuation.ini")
    require(config.path_value("Continuation", "OriginalConfig") == original_config.path
            and config.path_value("Continuation", "OutputDirectory") == directory,
            "Resource policy belongs to another original build or continuation")
    expected = {
        "schema_version": 1, "purpose": "authorized-post-build-disk-reserve",
        "authorization": DISK_RESERVE_AUTHORIZATION,
        "configuration": identity(config.path, True),
        "original_config": identity(original_config.path, True),
        "original_minimum_free_disk_gib": original_config.section("Run").getint("MinimumFreeDiskGiB"),
        "minimum_free_disk_gib": config.section("Run").getint("MinimumFreeDiskGiB"),
    }
    policy = read_json(path)
    require(policy == expected and all(type(policy[key]) is type(value) for key, value in expected.items()),
            "Layout disk policy differs from its authorized immutable INI")
    return {**expected, "record": identity(path, True)}


def build_resource_parser(config, policy=None):
    parser = copy.deepcopy(config.parser)
    if policy is not None:
        require(policy["authorization"] == DISK_RESERVE_AUTHORIZATION
                and policy["minimum_free_disk_gib"] == 64
                and policy["original_config"] == identity(config.path, True),
                "Unregistered post-build resource override")
        verify_identities([policy["record"], policy["configuration"]])
        parser["Run"]["MinimumFreeDiskGiB"] = str(policy["minimum_free_disk_gib"])
    return parser


def validate_layout_continuation(context, directory, policy):
    if policy is None:
        return []
    registration = read_json(directory / "layout-continuation-registration.json")
    require(registration["purpose"] == "saved-original-graph-layout-continuation"
            and registration["original_config"] == identity(context.config.path, True)
            and registration["resource_policy"] == policy,
            "Layout continuation registration changed")
    verify_identities(registration["frozen"] + registration["previous_records"])
    verify_records(registration["protected"])
    discarded = read_json(directory / "discarded-partial-layout.json")
    execution = read_json(directory / "create-disk-layout.execution.json")
    evidence = read_json(directory / "layout-continuation-evidence.json")
    handoff = read_json(directory / "saved-graph-handoff.json")
    graph = next(Path(item["path"]) for item in handoff["graph_inputs"] if Path(item["path"]).name == "graph")
    argv = list(map(str, [
        context.config.path_value("DiskANN", "BinaryDirectory") / "create_disk_layout", "uint8",
        context.config.path_value("Inputs", "Vectors"), graph, str(context.prefix) + "_disk.index",
    ]))
    require(registration["argv"] == argv
            and registration["partial"]["identity"]["path"] == str(context.prefix) + "_disk.index"
            and registration["partial"]["zero_header"] is True and registration["partial"]["links"] == 1,
            "Continuation changed the original native layout command or cleanup target")
    require(discarded["action"] == "unlink-authenticated-incomplete-layout"
            and discarded["authorization"] == DISK_RESERVE_AUTHORIZATION
            and discarded["partial"] == registration["partial"]
            and discarded["removed_paths"] == [str(context.prefix) + "_disk.index"]
            and type(discarded["native_search_invocations"]) is int
            and discarded["native_search_invocations"] == 0,
            "Continuation did not restrict cleanup to its authenticated failed output")
    require(execution["status"] == "completed" and type(execution["exit_code"]) is int
            and execution["exit_code"] == 0 and type(execution["pid"]) is int and execution["pid"] > 0
            and execution["cwd"] == str(directory)
            and execution["argv"] == argv == read_json(directory / "create-disk-layout.command.json")
            and execution["minimum_free_disk_gib"] == policy["minimum_free_disk_gib"],
            "Original layout command did not complete under its authorized resource policy")
    for value in (discarded["removed_unix"], execution["started_unix"], execution["ended_unix"]):
        require(type(value) in (int, float), "Invalid layout continuation timestamp")
        finite(value, "layout continuation time", positive=True)
    require(discarded["removed_unix"] <= execution["started_unix"] <= execution["ended_unix"],
            "Incomplete layout was not discarded before the fresh native layout command")
    expected = {
        "status": "completed", "graph_rebuilt": False, "native_search_invocations": 0,
        "registration": identity(directory / "layout-continuation-registration.json", True),
        "discarded_partial": identity(directory / "discarded-partial-layout.json", True),
        "execution": identity(directory / "create-disk-layout.execution.json", True),
        "resource_policy": policy,
    }
    require(evidence == expected and evidence["graph_rebuilt"] is False
            and type(evidence["native_search_invocations"]) is int,
            "Layout continuation evidence is incomplete")
    return registration["frozen"] + registration["previous_records"]


def validate_guarded_build_completion(config, directory, completion):
    expected_path = directory / "guarded-validation.json"
    record = completion.get("guarded_validation")
    require(isinstance(record, dict) and record.get("path") == str(expected_path)
            and expected_path.is_file() and not expected_path.is_symlink(),
            "DiskANN completion lacks guarded all-label build validation")
    verify_identities([record])
    evidence = read_json(expected_path)
    require(type(evidence.get("schema_version")) is int and evidence["schema_version"] == 1
            and evidence.get("status") == "completed", "Guarded build validation did not complete")
    records = [evidence[key] for key in (
        "config", "registration", "layout", "admission", "admission_execution", "stock_execution", "validation")]
    records.extend(evidence["outputs"])
    verify_identities(records)
    registration = read_json(directory / "registration.json")
    require(evidence["registration"]["path"] == str(directory / "registration.json")
            and registration["purpose"] == "guarded-original-diskann-build-validation"
            and evidence["config"] == registration["config"], "Guarded validation registration changed")
    verify_identities(registration["frozen"])
    verify_identities(registration.get("layout_sources", []))
    verify_records(registration["protected"])
    acceptance = read_json(directory / "admission-acceptance.json")
    require(acceptance["status"] == "accepted"
            and registration["admission_binary"]["sha256"] ==
            acceptance["builds"]["diskannBuildAdmission"]["binary"]["sha256"]
            and registration["library"] == acceptance["builds"]["diskannBuildAdmission"]["native_artifacts"][0]["path"],
            "Guarded validation used an unaccepted standalone admission executable")
    loader_binding = registration.get("loader_policy")
    context = DiskANNBuildValidation(
        evidence["config"]["path"], loader_binding["path"] if loader_binding is not None else None)
    require(loader_binding == (context.loader["binding"] if context.loader is not None else None),
            "Guarded completion changed its approved native loader binding")
    declared_loader = (str(config.path_value("DiskANN", "LoaderPolicy"))
                       if "LoaderPolicy" in config.section("DiskANN") else None)
    require(declared_loader == (loader_binding["path"] if loader_binding is not None else None),
            "Campaign and guarded validation declare different native loader policies")
    verify_identities(registration.get("loader_identities", []))
    require(context.prefix == config.path_value("DiskANN", "IndexPrefix")
            and context.revision == config.section("DiskANN")["SourceRevision"]
            and context.rows == config.section("Dataset").getint("VectorCount")
            and context.dimension == config.section("Dataset").getint("Dimension")
            and all(context.config.path_value("Inputs", name) == config.path_value("Dataset", name)
                    for name in ("Vectors", "Queries", "Attributes")),
            "Guarded validation belongs to a different registered corpus/index")
    preflight = read_json(evidence["admission_execution"]["path"])
    stock = read_json(evidence["stock_execution"]["path"])
    for name, execution in (("all-label admission", preflight), ("stock validation", stock)):
        require(execution.get("status") == "completed" and type(execution.get("exit_code")) is int
                and execution["exit_code"] == 0 and type(execution.get("pid")) is int and execution["pid"] > 0,
                f"Guarded {name} did not exit successfully")
        for field in ("started_unix", "ended_unix"):
            require(type(execution[field]) in (int, float), f"Invalid guarded {name} execution time")
            finite(execution[field], f"guarded {name} {field}", positive=True)
        require(execution["started_unix"] <= execution["ended_unix"], "Guarded execution times are reversed")
    admission_path = directory / "admission.json"
    require(preflight["argv"] == context.admission_command(registration["admission_binary"]["path"], directory)
        and evidence["admission"]["path"] == str(admission_path)
        and evidence["admission_execution"]["path"] == str(directory / "admission.execution.json")
        and evidence["stock_execution"]["path"] == str(directory / "validate-search.execution.json")
        and evidence["validation"]["path"] == str(directory / "validation.json")
        and preflight["cwd"] == stock["cwd"] == str(directory)
        and stock["argv"] == context.stock_command(directory)
        and preflight["ended_unix"] <= stock["started_unix"],
        "Stock validation did not follow the exact original all-label admission")
    linked_library = (loader_binding["library"] if loader_binding is not None else
                      str(config.path_value("DiskANN", "Library")))
    require(registration["library"] == linked_library, "Guarded completion changed its active native library")
    certificate = validate_build_admission(context, admission_path, preflight["pid"], linked_library)
    require(certificate == evidence["admission"], "All-label admission certificate changed")
    output_names = {
        "admission.command.json", "admission.log", "validate-search.command.json", "validate-search.log",
        "validation_queries.u8bin", "validation_filters.txt",
        f"validation_{context.search_l}_idx_uint32.bin", f"validation_{context.search_l}_dists_float.bin",
    }
    require(len(evidence["outputs"]) == len(output_names)
            and {entry["path"] for entry in evidence["outputs"]} ==
            {str(directory / name) for name in output_names}, "Guarded validation raw outputs are incomplete")
    validation = read_json(evidence["validation"]["path"])
    require(validation == completion["validation"]
            and validation["queries"] == context.query_calls and validation["topk"] == context.k
            and validation["search_l"] == context.search_l
            and validation["distinct_labels_checked"] == len(context.groups)
            and validation["labels_absent_from_fixture"] == []
            and all(validation[field] is True for field in
                    ("exact_result_distances", "all_labels_match", "no_duplicate_ids")),
            "Original stock validation did not cover the admitted request set")
    layout = read_json(evidence["layout"]["path"])
    verify_identities(layout["records"])
    layout_directory = Path(registration["layout_directory"])
    policy = layout_disk_reserve_policy(context.config, layout_directory)
    require(registration.get("resource_policy") == policy and layout.get("resource_policy") == policy,
            "Guarded validation lost its registered layout resource policy")
    continuation_records = validate_layout_continuation(context, layout_directory, policy)
    layout_paths = {str(layout_directory / name) for name in (
        "layout_completion.json", "manifest.json", "saved-graph-handoff.json",
        "controller-retirement.command.json", "create-disk-layout.command.json", "create-disk-layout.log")}
    layout_paths.add(str(context.root / "failure.json"))
    if policy is not None:
        layout_paths.update(str(layout_directory / name) for name in LAYOUT_CONTINUATION_RECORDS)
    require(evidence["layout"]["path"] == str(directory / "layout-evidence.json")
            and len(layout["records"]) == len(layout_paths)
            and {entry["path"] for entry in layout["records"]} == layout_paths,
            "Guarded validation layout evidence is incomplete")
    require(layout["state"] == "layout_completed" and layout["native_builder_exit_status"] == 0
            and layout["graph_rebuilt"] is False and layout["production_search_invocations"] == 0
            and layout["index_prefix"] == str(context.prefix),
            "Guarded validation lacks the saved original-graph handoff")
    protected = registration["protected"] + [
        file_record(entry["path"], True) for entry in [
            record, *records, *registration["frozen"], *layout["records"],
            *registration.get("layout_sources", []), *registration.get("loader_identities", []),
            *continuation_records]
    ]
    return {"certificate": certificate, "evidence": record, "protected": protected}


def validate_native_completion(config, engine, phase, plan, expected_pid):
    directory = config.root / "native" / engine / phase
    require(not (directory / "failure.json").exists(), "Native failure marker contradicts successful exit")
    completion = read_json(directory / "completion.json")
    require(completion == {
        "schema_version": 1, "engine": engine, "phase": phase, "status": "completed",
        "job_count": len(plan), "completed_jobs": len(plan),
    } and all(type(completion[field]) is int for field in ("schema_version", "job_count", "completed_jobs")),
            "Native completion does not cover the exact execution plan")
    ready_record = validate_native_ready(config, engine, phase, plan, expected_pid)
    ready = read_json(directory / "ready.json")
    records = {"ready.json": ready_record, "completion.json": identity(directory / "completion.json", True)}
    if engine == "Filtered_DiskANN":
        records["diskann-admission.json"] = validate_diskann_admission(
            config, phase, plan, expected_pid, ready["load_seconds"])
    return records


def validate_native_ready(config, engine, phase, plan, expected_pid):
    directory = config.root / "native" / engine / phase
    ready = read_json(directory / "ready.json")
    require(set(ready) == {"schema_version", "engine", "phase", "job_count", "max_threads", "pid", "load_seconds"}
            and ready["schema_version"] == 1 and ready["engine"] == engine and ready["phase"] == phase
            and type(ready["schema_version"]) is int
            and type(ready["job_count"]) is int and ready["job_count"] == len(plan)
            and type(ready["max_threads"]) is int
            and ready["max_threads"] == max(config.threads(phase)[row["thread_index"]] for row in plan),
            "Native resident index was not initialized for the exact planned concurrency")
    require(type(expected_pid) is int and expected_pid > 0
            and type(ready["pid"]) is int and ready["pid"] == expected_pid,
            "Native ready PID does not match the launched child")
    require(type(ready["load_seconds"]) in (int, float), "Native load_seconds must be a JSON number")
    finite(ready["load_seconds"], "native index load seconds")
    return identity(directory / "ready.json", True)


def validate_captures(config, results):
    data = config.section("Dataset")
    top_k = config.section("Benchmark").getint("TopK")
    require(top_k > 0, "Captured result TopK must be positive")
    base = open_vectors(config.path_value("Dataset", "Vectors"), "UInt8", data.getint("Dimension")).data
    queries = matrix(config.prepared / "query.u8bin", "u1")
    attributes = open_attributes(config.path_value("Dataset", "Attributes"),
                                 data.getint("VectorCount"), data.getint("AttributeColumns"))
    reports, seen, inodes = [], set(), set()
    for result in results:
        truth = matrix(config.prepared / f"gt_{result['scenario']}.u32bin", "<u4")
        native_directory = config.root / "native" / result["engine"] / result["phase"]
        short = result["schema_version"] == 2
        require(result["schema_version"] in (1, 2)
                and (not short or native_short_results(config, result["engine"])),
                "Unregistered captured native-count protocol")
        if short:
            validate_return_accounting(config, result)
        saved_counts = []
        for capture in ("first", "last"):
            ids_path, distances_path = Path(result[capture + "_ids"]), Path(result[capture + "_distances"])
            paths = [ids_path, distances_path]
            if short:
                paths.append(Path(result[capture + "_counts"]))
            for path in paths:
                resolved = path.resolve(strict=True)
                require(path.is_absolute() and resolved.is_relative_to(native_directory)
                        and not path.is_symlink(), "Result capture escaped its native output directory")
                info = path.stat()
                inode = (info.st_dev, info.st_ino)
                require(str(resolved) not in seen and inode not in inodes,
                        "Result payload overwritten/reused by another capture")
                seen.add(str(resolved))
                inodes.add(inode)
            ids, distances = matrix(ids_path, "<u4"), matrix(distances_path, "<f4")
            require(ids.shape == truth.shape == distances.shape == (len(queries), top_k), "Invalid result shape")
            if short:
                counts_matrix = matrix(paths[2], "<u8")
                require(counts_matrix.shape == (len(queries), 1) and np.all(counts_matrix <= top_k),
                        "Invalid native returned-count shape or count above K")
                counts = np.asarray(counts_matrix[:, 0], dtype=np.int64)
                saved_counts.append(counts)
                for count in np.unique(counts):
                    validate_ids(config, result["scenario"], ids[counts == count, :count], attributes)
            else:
                require(result["underfilled_queries"] == 0, "Legacy full-K capture claims short results")
                counts = np.full(len(queries), top_k, dtype=np.int64)
                validate_ids(config, result["scenario"], ids, attributes)
            valid = np.arange(top_k)[None, :] < counts[:, None]
            require(np.isfinite(distances[valid]).all(), "Nonfinite native result distance")
            for first in range(0, len(ids), 128):
                group = ids[first:first + 128]
                query_rows, ranks = np.nonzero(valid[first:first + len(group)])
                delta = np.asarray(base[group[query_rows, ranks]], dtype=np.int32) - \
                    np.asarray(queries[first + query_rows], dtype=np.int32)
                exact = np.sum(delta * delta, axis=1, dtype=np.int64)
                require(np.array_equal(exact, distances[first + query_rows, ranks]),
                        "Native result distance differs from original-vector squared L2")
            recall = sum(len(set(row[:count]) & set(correct))
                         for row, count, correct in zip(ids, counts, truth)) / ids.size
            require(math.isclose(recall, result[capture + "_recall"], rel_tol=0, abs_tol=1e-10),
                    "Saved native IDs disagree with reported capture recall")
            if result["passes"] == 1:
                require(math.isclose(recall, result["recall"], rel_tol=0, abs_tol=1e-10),
                        "Single measured cohort recall differs from saved results")
            report = {"engine": result["engine"], "scenario": result["scenario"], "L": result["L"],
                      "threads": result["threads"], "repeat": result["repeat"], "capture": capture,
                      "recall": recall, "exact_distances": True, "all_predicates_match": True,
                      "ids": identity(ids_path, True), "distances": identity(distances_path, True)}
            if short:
                report.update(counts=identity(paths[2], True), returned_neighbors=int(counts.sum()),
                              missing_neighbors=int(ids.size - counts.sum()),
                              underfilled_queries=int(np.count_nonzero(counts < top_k)))
            reports.append(report)
        if short:
            if result["passes"] == 1:
                require(np.array_equal(*saved_counts), "One-cohort first/last native counts differ")
                saved_counts = saved_counts[:1]
            statistics_seen = {
                "returned_neighbors": sum(int(counts.sum()) for counts in saved_counts),
                "missing_neighbors": sum(int(top_k * len(counts) - counts.sum()) for counts in saved_counts),
                "underfilled_queries": sum(int(np.count_nonzero(counts < top_k)) for counts in saved_counts),
            }
            for name, value in statistics_seen.items():
                require(value <= result[name] and (result["passes"] > 2 or value == result[name]),
                        "Native count totals disagree with saved cohorts")
    return reports


def plot_input_files(stage):
    require(stage in ("single", "complete"), "Unknown publication stage")
    names = ["registration", "single_summary", "availability"]
    if stage == "complete":
        names.append("throughput_summary")
    return {name: name + (".json" if name == "registration" else ".csv") for name in names}


def validate_plot_publication(config, stage):
    source = config.root / f"plot_inputs_{stage}"
    destination = config.root / ("plots_single" if stage == "single" else "plots_complete")
    metadata_path = destination / "plot_metadata.json"
    metadata = read_json(metadata_path)
    require(metadata.get("status") == "completed", "Plot renderer did not complete successfully")
    require(type(metadata["schema_version"]) is int and metadata["schema_version"] == 1
            and metadata["stage"] == stage and metadata["dataset"] == "SIFT1B"
            and metadata["scenarios"] == list(SCENARIOS) and metadata["engine_order"] == list(ENGINES)
            and metadata["registration"] == read_json(source / "registration.json"),
            "Plot metadata does not describe its registered publication")
    require(metadata["all_measured_points_retained"] is True
            and all(metadata[key] is False for key in ("interpolated", "smoothed", "extrapolated")),
            "Plot publication altered or omitted measured observations")
    input_files = plot_input_files(stage)
    stems = ["single_thread_recall_qps"]
    if stage == "complete":
        stems.extend(("throughput_scaling", "throughput_peak"))
    output_files = {f"{stem}.{extension}" for stem in stems for extension in ("png", "pdf")}
    if stage == "complete":
        output_files.add("peak_points.csv")
    require(metadata["hash_algorithm"] == "md5" and set(metadata["input_files"]) == set(input_files)
            and set(metadata["input_hashes"]) == set(input_files)
            and set(metadata["output_hashes"]) == output_files, "Incomplete plot publication inventory")
    for name, filename in input_files.items():
        path = source / filename
        require(metadata["input_files"][name] == str(path.resolve())
                and hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest() == metadata["input_hashes"][name],
                f"Plot input snapshot differs from rendered data: {filename}")
    for filename in sorted(output_files):
        path = destination / filename
        require(path.is_file() and not path.is_symlink(), f"Missing plot publication artifact: {filename}")
        payload = path.read_bytes()
        require(payload and hashlib.md5(payload, usedforsecurity=False).hexdigest() == metadata["output_hashes"][filename],
                f"Plot artifact hash mismatch: {filename}")
        if path.suffix in (".png", ".pdf"):
            require(payload.startswith(b"\x89PNG\r\n\x1a\n" if path.suffix == ".png" else b"%PDF-"),
                    f"Plot artifact has the wrong image format: {filename}")
    counts = {"single_points": len(read_csv(source / "single_summary.csv"))}
    if stage == "complete":
        counts.update(throughput_points=len(read_csv(source / "throughput_summary.csv")),
                      peak_points=len(read_csv(destination / "peak_points.csv")))
    for name, count in counts.items():
        require(type(metadata[name]) is int and metadata[name] == count, f"Plot publication count mismatch: {name}")
    if stage == "single":
        require(metadata["throughput_points"] is None and metadata["peak_points"] is None,
                "Single-stage publication claims unmeasured throughput")
    return {"state": "completed", "stage": stage, **counts,
            "inputs": [identity(source / filename, True) for filename in input_files.values()],
            "outputs": [identity(destination / filename, True)
                        for filename in sorted(output_files | {"plot_metadata.json"})]}


def aggregate_single(config, history, native):
    rows = []
    for original in history:
        row = dict(original)
        row.update(measurement_reused="true", io_mode="buffered" if row["engine"] == "SPTAG_adaptive" else "direct",
                   source=str(config.path_value("History", "Summary")))
        rows.append(row)
    groups = defaultdict(list)
    for result in native:
        groups[(result["scenario"], result["L"])].append(result)
    for (name, control), values in groups.items():
        require(sorted(value["repeat"] for value in values) == list(range(1, config.repeats("single") + 1)),
                "Incomplete native single-thread repetitions")
        section = config.section(f"Scenario.{name}")
        recall, qps = [value["recall"] for value in values], [value["qps"] for value in values]
        rows.append({
            "scenario": name, "engine": "Filtered_DiskANN", "L": control, "queries": 1000,
            "repeats": len(values), "threads": 1, "cpu_nodes": config.section("Single")["CPUNodes"],
            "recall": statistics.median(recall), "recall_min": min(recall), "recall_max": max(recall),
            "qps": statistics.median(qps), "qps_min": min(qps), "qps_max": max(qps),
            "candidate_count": section.getint("CandidateCount"), "selectivity": section.getint("CandidateCount") / 10**9,
            "predicate": scenario_contract(config, name)["predicate"], "measurement_reused": "false",
            "io_mode": "direct", "source": str(config.root / "single_raw.json"),
        })
    return rows


def aggregate_throughput(config, native, selections):
    groups = defaultdict(list)
    for result in native:
        groups[(result["engine"], result["scenario"], result["L"], result["threads"])].append(result)
    rows, unavailable = [], []
    for (engine, name, control, threads), values in groups.items():
        require(sorted(value["repeat"] for value in values) == list(range(1, config.repeats("throughput") + 1)),
                "Incomplete throughput repetitions")
        selected = [row for row in selections if (row["engine"], row["scenario"], row["L"]) == (engine, name, control)]
        require(selected, "Unselected native throughput budget")
        qps = [value["qps"] for value in values]
        for selection in selected:
            target = selection["recall_target"]
            minimum = min(value["recall_min_batch"] for value in values)
            if minimum + 1e-12 < target:
                unavailable.append({
                    "phase": "throughput", "scenario": name, "engine": engine, "status": "recall_target_unmet",
                    "reason": f"Concurrent native cohort recall {minimum} fell below {target}; no retuning or imputation",
                    "recall_target": target, "threads": threads,
                })
                continue
            row = {
                "scenario": name, "engine": engine, "threads": threads, "L": control, "repeats": len(values),
                "recall_target": target, "recall": statistics.median(value["recall"] for value in values),
                "recall_min": minimum, "recall_max": max(value["recall"] for value in values),
                "qps": statistics.median(qps), "qps_min": min(qps), "qps_max": max(qps),
                "seconds_min": min(value["elapsed_seconds"] for value in values),
                "queries_min": min(value["queries"] for value in values),
                "cpu_nodes": config.section("Throughput")["CPUNodes"],
                "memory_nodes": config.section("Throughput")["MemoryNodes"],
            }
            for field in ("mean_latency_us", "p50_latency_us", "p99_latency_us", "mean_ios"):
                metrics = [value[field] for value in values]
                require(all(value is None for value in metrics) or all(value is not None for value in metrics),
                        "Inconsistent native metric availability")
                row[field] = "" if metrics[0] is None else statistics.median(metrics)
            total_queries = sum(value["queries"] for value in values)
            returned = sum(value["returned_neighbors"] if value["schema_version"] == 2
                           else value["queries"] * config.section("Benchmark").getint("TopK") for value in values)
            missing = sum(value["missing_neighbors"] if value["schema_version"] == 2 else 0 for value in values)
            underfilled = sum(value["underfilled_queries"] for value in values)
            row.update(measured_queries_total=total_queries, returned_neighbors_total=returned,
                       missing_neighbors_total=missing, underfilled_queries_total=underfilled,
                       returned_per_query=returned / total_queries, underfilled_fraction=underfilled / total_queries,
                       native_result_schemas=",".join(map(str, sorted({value["schema_version"] for value in values}))))
            rows.append(row)
    return rows, unavailable


class Campaign:
    def __init__(self, config):
        self.config = config
        self.root = config.root
        self.manifest = read_json(self.root / "manifest.json")
        self.started = utc_now()
        self.child = None

    def update(self, state, **details):
        value = {"state": state, "pid": os.getpid(), "started_at_utc": self.started,
                 "updated_at_utc": utc_now(), **details}
        write_json(self.root / "status.json", value)
        print(json.dumps(value, allow_nan=False), flush=True)

    def verify(self):
        reject_benchmark_environment()
        require(self.manifest["loader_environment"] == {"LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH")},
                "Native loader environment differs from the prepared campaign")
        verify_records(self.manifest["protected"])
        verify_records(self.manifest["prepared_inputs"])
        verify_identities(self.manifest["frozen"])

    def check_resources(self, pid=None):
        record = resources(pid)
        record["free_disk_bytes"] = shutil.disk_usage(self.root).free
        run = self.config.section("Run")
        with (self.root / "resources.jsonl").open("a") as stream:
            stream.write(json.dumps(record, allow_nan=False) + "\n")
        require(record["free_disk_bytes"] >= run.getfloat("MinimumFreeDiskGiB") * GIB, "Campaign disk reserve reached")
        require(record["memory_available_bytes"] >= run.getfloat("MinimumFreeMemoryGiB") * GIB,
                "Shared-host memory reserve reached")
        require(record.get("rss_bytes", 0) <= run.getfloat("MaxChildRSSGiB") * GIB, "Native client RSS ceiling reached")
        return record

    def wait_for_build(self):
        config = self.config
        directory = config.path_value("Run", "BuildDirectory")
        while not (directory / "completion.json").is_file():
            require(not (directory / "failure.json").exists(), f"DiskANN construction failed: {directory / 'failure.json'}")
            status = read_json(directory / "status.json")
            require(status["state"] != "failed", "DiskANN build reports failure")
            alive = (self.manifest["controller"] is not None
                     and process_identity(self.manifest["controller"]["pid"]) == self.manifest["controller"])
            if not alive and (directory / "completion.json").is_file():
                break
            require(alive,
                    "DiskANN controller exited or its PID was reused without successful completion")
            self.update("waiting_for_index", build_phase=status.get("phase"), build_pid=status.get("child_pid"),
                        build_status=str(directory / "status.json"))
            self.check_resources()
            time.sleep(config.section("Run").getfloat("PollSeconds"))
        require(not (directory / "failure.json").exists(), "Contradictory DiskANN completion/failure markers")
        completion = read_json(directory / "completion.json")
        require(completion["state"] == "completed" and completion["vectors"] == 10**9
                and completion["original_inputs_unchanged"] is True and completion["smoke_only"] is False
                and Path(completion["index_prefix"]).resolve() == config.path_value("DiskANN", "IndexPrefix"),
                "DiskANN completion does not describe the registered production index")
        validation = completion["validation"]
        require(validation["exact_result_distances"] is True and validation["all_labels_match"] is True
                and validation["no_duplicate_ids"] is True and validation["distinct_labels_checked"] == 201,
                "DiskANN production result validation is incomplete")
        source = read_json(directory / "manifest.json")
        require(source["native_source_revision"] == config.section("DiskANN")["SourceRevision"]
                and source["categorical_only"] is True and source["universal_label"] is None
                and source["disk_pq"] is False and source["graph_reused"] is False
                and source["search_pq_reused"] is True
                and {key: source["graph_parameters"][key] for key in ("R", "L", "FilteredL")}
                == {"R": "64", "L": "1", "FilteredL": "100"}, "DiskANN build method changed")
        guarded = validate_guarded_build_completion(config, directory, completion)
        write_json(self.root / "index_completion.json", {
            "completion": completion, "source": identity(directory / "completion.json", True),
            "artifacts": [file_record(path) for path in index_files(config, include_diskann=True)],
            "build_validation": guarded,
        })

    def command(self, label, command):
        self.check_resources()
        stdout_path = self.root / "logs" / f"{label}.stdout.log"
        stderr_path = self.root / "logs" / f"{label}.stderr.log"
        command_path = self.root / "logs" / f"{label}.command.json"
        command_record = {"argv": command, "cwd": str(self.config.prepared), "started_at_utc": utc_now()}
        write_json(command_path, command_record)
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            self.child = subprocess.Popen(command, cwd=self.config.prepared, stdout=stdout, stderr=stderr)
            pid = self.child.pid
            try:
                write_json(command_path, {**command_record, "pid": pid})
                self.update("running", phase=label, child_pid=self.child.pid, stdout=str(stdout_path))
                while True:
                    try:
                        code = self.child.wait(timeout=self.config.section("Run").getfloat("ResourceIntervalSeconds"))
                        break
                    except subprocess.TimeoutExpired:
                        self.check_resources(self.child.pid)
                        self.update("running", phase=label, child_pid=self.child.pid, stdout=str(stdout_path))
                require(code == 0, f"Native command failed ({code}): {label}; see {stderr_path} and {stdout_path}")
            except BaseException:
                if self.child.poll() is None:
                    self.child.terminate()
                    try:
                        self.child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        self.child.kill()
                        self.child.wait()
                raise
            finally:
                self.child = None
        return stdout_path, pid

    def native(self, engine, phase, plan):
        require(plan, "Cannot launch an empty native execution plan")
        plan_record = write_plan(self.config, engine, phase, plan)
        executable = self.manifest["binaries"][engine]["path"]
        command = self.config.affinity(phase) + [executable, str(self.config.path), phase]
        output, pid = self.command(f"{engine}.{phase}", command)
        verify_identities([plan_record])
        completion = validate_native_completion(self.config, engine, phase, plan, pid)
        result = parse_results(self.config, engine, phase, output, plan)
        captures = validate_captures(self.config, result)
        write_json(self.root / f"{engine}.{phase}.validation.json", captures)
        write_json(self.root / f"{engine}.{phase}.completion.json", completion)
        self.verify()
        index_completion = read_json(self.root / "index_completion.json")
        verify_records(index_completion["artifacts"])
        verify_records(index_completion["build_validation"]["protected"])
        return result

    def plot(self, stage):
        destination = self.root / ("plots_single" if stage == "single" else "plots_complete")
        inputs = self.root / f"plot_inputs_{stage}"
        inputs.mkdir()
        for filename in plot_input_files(stage).values():
            source = self.root / filename
            before = identity(source, True)
            shutil.copy2(source, inputs / filename)
            require(sha256_file(inputs / filename) == before["sha256"], "Plot input changed during snapshot")
        self.command("plot." + stage, [str(self.config.path_value("Run", "RScript")),
                     str(self.root / "code/plot_sift1b_threeway.R"), str(inputs), str(destination), "--stage", stage])
        write_json(self.root / f"plot.{stage}.completion.json", validate_plot_publication(self.config, stage))

    def finish_throughput(self, native, selections, availability, engines=ENGINES, retained_prefixes=None):
        config = self.config
        retained_prefixes = {} if retained_prefixes is None else retained_prefixes
        require(engines and set(engines).issubset(ENGINES)
                and set(retained_prefixes).issubset(engines)
                and not any(row["engine"] in engines and row["engine"] not in retained_prefixes for row in native),
                "Remaining throughput phases must not repeat retained measurements")
        for engine in engines:
            capacity = aio_capacity(config)
            write_json(self.root / "plans" / f"{engine}.aio_capacity.json", capacity)
            plan, limited = plan_throughput(config, engine, selections, capacity)
            availability.extend(limited)
            if engine in retained_prefixes:
                retained = [row for row in native if row["engine"] == engine]
                count = retained_prefixes[engine]
                require(engine == "PipeANN" and native_short_results(config, engine)
                        and type(count) is int and 0 < count == len(retained) < len(plan)
                        and [(row["scenario"], row["L"], row["threads"], row["repeat"]) for row in retained] ==
                        [resolved_key(config, engine, "throughput", job) for job in plan[:count]],
                        "Retained measurements are not the authorized exact execution-plan prefix")
                plan = plan[count:]
            if plan:
                native.extend(self.native(engine, "throughput", plan))
            else:
                self.update("no_eligible_points", phase=f"{engine}.throughput",
                            reason="No native search budget/available concurrency passed the registered requirements")
            write_json(self.root / "throughput_raw.json", native)
            rows, invalid_quality = aggregate_throughput(config, native, selections)
            write_csv(self.root / "throughput_summary.csv", THROUGHPUT_FIELDS, rows)
            write_csv(self.root / "availability.csv", AVAILABILITY_FIELDS, availability + invalid_quality)
        self.plot("complete")
        self.verify()
        index_completion = read_json(self.root / "index_completion.json")
        verify_records(index_completion["artifacts"])
        verify_records(index_completion["build_validation"]["protected"])
        for stage in ("single", "complete"):
            publication = read_json(self.root / f"plot.{stage}.completion.json")
            verify_identities(publication["inputs"] + publication["outputs"])
        completion = {
            "state": "completed", "at_utc": utc_now(),
            "single_thread_points": len(read_csv(self.root / "single_summary.csv")),
            "throughput_points": len(rows), "throughput_native_repetitions": len(native),
            "scope": "maximum observed feasible throughput with unchanged shared-host limits",
            "limitations": availability + invalid_quality, "all_protected_inputs_unchanged": True,
            "artifacts": [identity(self.root / name, True) for name in (
                "single_summary.csv", "throughput_summary.csv", "availability.csv", "operating_points.json",
                "plot.single.completion.json", "plot.complete.completion.json")],
        }
        if (self.root / "continuation.json").exists():
            completion["continuation"] = identity(self.root / "continuation.json", True)
        write_json(self.root / "completion.json", completion)
        self.update("completed", completion=str(self.root / "completion.json"))

    def run(self):
        config = self.config
        require(config.path == self.root / "config/benchmark.ini"
                and Path(__file__).resolve() == self.root / "code/run_sift1b_threeway.py",
                "Run the frozen campaign code with its unchanged frozen configuration")
        require(read_json(self.root / "status.json")["state"] == "prepared",
                "Campaign was already started; no implicit overwrite/resume is permitted")
        lock = self.root / "run.lock"
        with lock.open("x") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "started_at_utc": self.started}) + "\n")
        try:
            self.verify()
            self.wait_for_build()
            self.verify()
            history, _ = read_history(config)
            availability = [{
                "phase": "single", "scenario": "mixed_dnf", "engine": "Filtered_DiskANN",
                "status": "unsupported_predicate", "reason": "Original DiskANN supports single labels, not mixed numeric DNF",
                "recall_target": "", "threads": "",
            }]
            single = self.native("Filtered_DiskANN", "single", plan_single(config))
            write_json(self.root / "single_raw.json", single)
            combined = aggregate_single(config, history, single)
            write_csv(self.root / "single_summary.csv", SINGLE_FIELDS, combined)
            write_csv(self.root / "availability.csv", AVAILABILITY_FIELDS, availability)
            self.plot("single")
            write_json(self.root / "single_completion.json",
                       {"state": "completed", "at_utc": utc_now(), "native_repetitions": len(single),
                        "reused_historical_points": len(history), "curve_points": len(combined)})
            selections, missing = select_operating_points(config, combined)
            availability.extend(missing)
            write_json(self.root / "operating_points.json", selections)
            self.finish_throughput([], selections, availability)
        except BaseException as error:
            failure = {"state": "failed", "error": repr(error), "at_utc": utc_now(), "traceback": traceback.format_exc()}
            write_json(self.root / "failure.json", failure)
            self.update("failed", error=repr(error), failure=str(self.root / "failure.json"))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("check-config", "prepare", "run"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    arguments = parser.parse_args()
    reject_benchmark_environment()
    config = load_profile(arguments.config)
    if arguments.stage == "check-config":
        history, _ = read_history(config)
        print(json.dumps({"config": str(config.path), "historical_points": len(history), "aio": aio_capacity(config)}))
    elif arguments.stage == "prepare":
        print(prepare(config))
    else:
        def interrupted(signum, _frame):
            raise InterruptedError(f"Campaign interrupted by signal {signum}")
        signal.signal(signal.SIGTERM, interrupted)
        Campaign(config).run()


if __name__ == "__main__":
    main()
