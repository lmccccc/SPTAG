"""Shared configuration and provenance for the fixed SIFT1B comparison."""

import configparser
import hashlib
import json
import os
from pathlib import Path
import re


DEFAULT_CONFIG = Path(__file__).resolve().parent / "configs/sift1b_official/benchmark.ini"
SCENARIOS = ("unfilter", "broad_tag", "medium_tag", "numeric", "mixed_dnf", "extreme_tag")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
    temporary.replace(path)


def identity(path, hash_content=False):
    path = Path(path)
    stat = path.stat()
    result = {"path": str(path), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    if hash_content:
        result["sha256"] = sha256_file(path)
    return result


def verify_identities(items):
    for item in items:
        require(identity(item["path"], "sha256" in item) == item,
                f"Frozen artifact changed: {item['path']}")


def reject_environment_overrides(environment=None):
    environment = os.environ if environment is None else environment
    forbidden = sorted(key for key in environment if key.startswith(("SPTAG_", "PIPEANN_"))
                       or key in {"ADDITIONAL_DEFINITIONS", "CXXFLAGS", "CPPFLAGS"})
    require(not forbidden, "Remove environment overrides before using the fixed profile: "
            + ", ".join(forbidden))


class Config:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
        with self.path.open(encoding="ascii") as stream:
            self.parser.read_file(stream)

    def section(self, name):
        require(self.parser.has_section(name), f"Missing INI section [{name}]")
        return self.parser[name]

    def path_value(self, section, key):
        return self.relative_path(self.section(section)[key])

    def relative_path(self, value):
        path = Path(value)
        return (path if path.is_absolute() else self.path.parent / path).resolve()

    def csv(self, section, key):
        values = [part.strip() for part in self.section(section)[key].split(",")]
        require(all(values) and len(values) == len(set(values)), f"Invalid [{section}] {key}")
        return values

    def integer_list(self, section, key):
        return [int(value) for value in self.csv(section, key)]

    def affinity(self, section="Execution"):
        values = self.section(section)
        for key in ("CPUNodes", "MemoryNodes"):
            require(re.fullmatch(r"\d+(,\d+)*", values[key]) is not None,
                    f"[{section}] {key} must contain NUMA node IDs")
        return ["numactl", f"--cpunodebind={values['CPUNodes']}", f"--membind={values['MemoryNodes']}"]

    def config_files(self):
        files = [self.path, self.path_value("PipeANN", "CMakeProfile")]
        files += [self.relative_path(name) for name in self.csv("SPANN", "SearchConfigs")]
        for name in self.csv("Benchmark", "Scenarios"):
            if name != "unfilter":
                files.append(self.path_value(f"Scenario.{name}", "FilterConfig"))
        return files

    def digest(self):
        digest = hashlib.sha256()
        for path in self.config_files():
            require(path.is_relative_to(self.path.parent),
                    f"Control files must live inside the fixed profile directory: {path}")
            digest.update(path.relative_to(self.path.parent).as_posix().encode())
            digest.update(bytes.fromhex(sha256_file(path)))
        return digest.hexdigest()


def _validate_keys(config):
    allowed = {
        "Dataset": {"VectorFile", "QueryFile", "WorkloadDirectory", "PreparedDirectory", "VectorCount",
                    "Dimension", "ValueType", "Metric"},
        "Benchmark": {"QueryCount", "WarmupQueries", "ResultNum", "NumberOfThreads", "Repeats",
                      "Scenarios", "OutputDirectory"},
        "Execution": {"CPUNodes", "MemoryNodes"},
        "SPANN": {"IndexDirectory", "BenchmarkBinary", "BenchmarkSHA256", "SearchConfigs",
                  "InitialSearchConfig", "PreviousRunManifest", "CoverageSummary"},
        "PipeANN": {"SourceDirectory", "SourceRevision", "SourceIndexPrefix", "IndexPrefix",
                    "ToolchainDirectory", "CMakeProfile", "NeighborType", "PipelineWidth",
                    "SearchMode", "UnfilteredMemoryL", "FilteredMemoryL", "FilterMode", "LSweep"},
        "MemoryIndex": {"SamplingRate", "R", "L", "Alpha", "SamplePrefix"},
        "Build": {"CPUNodes", "MemoryNodes", "Threads", "Jobs"},
    }
    for name in SCENARIOS:
        keys = {"Kind", "TruthFile"}
        if name != "unfilter":
            keys |= {"SourcePredicate", "FilterConfig"}
            keys |= {"RareTag", "RegularTag", "UpperInclusive"} if name == "mixed_dnf" else (
                {"UpperInclusive"} if name == "numeric" else {"Tag"})
        allowed[f"Scenario.{name}"] = keys
    require(set(config.parser.sections()) == set(allowed), "Unknown or missing configuration section")
    for section, keys in allowed.items():
        require(set(config.section(section)) == {key.lower() for key in keys},
                f"Unknown or missing key in [{section}]")


def load_config(path=DEFAULT_CONFIG):
    config = Config(path)
    _validate_keys(config)
    dataset = config.section("Dataset")
    benchmark = config.section("Benchmark")
    pipeann = config.section("PipeANN")
    memory = config.section("MemoryIndex")
    require(dataset["ValueType"] == "UInt8" and dataset["Metric"] == "L2"
            and dataset.getint("Dimension") == 128 and dataset.getint("VectorCount") == 1000000000,
            "The fixed recipe is for full SIFT1B UInt8/L2")
    require(benchmark.getint("ResultNum") == 10 and benchmark.getint("NumberOfThreads") == 1,
            "The official latency comparison uses top-10 and one query thread")
    require(benchmark.getint("QueryCount") > 0
            and benchmark.getint("WarmupQueries") == benchmark.getint("QueryCount")
            and benchmark.getint("Repeats") > 0, "Require full identical-set warmup and positive repetitions")
    require(tuple(config.csv("Benchmark", "Scenarios")) == SCENARIOS, "Unexpected workload matrix")
    require(pipeann["NeighborType"] == "pq" and pipeann.getint("PipelineWidth") == 32
            and pipeann.getint("SearchMode") == 2 and pipeann["FilterMode"] == "auto",
            "PipeANN must use its native PQ/pipeline-32/mode-2/auto baseline")
    require(pipeann.getint("UnfilteredMemoryL") == 10, "Unfiltered mem_L must remain 10; no missing-file fallback")
    require(pipeann.getint("FilteredMemoryL") == 0, "Filtered search uses its documented mem_L=0 path")
    require(memory.getfloat("SamplingRate") == 0.01 and memory.getint("R") == 32
            and memory.getint("L") == 64 and memory.getfloat("Alpha") == 1.2,
            "Use the documented 1% / R32 / L64 / alpha1.2 memory entry recipe")
    values = config.integer_list("PipeANN", "LSweep")
    require(values == sorted(values) and min(values) >= benchmark.getint("ResultNum"),
            "PipeANN L values must be unique, increasing and at least top-k")
    config.affinity()
    config.affinity("Build")
    require(config.section("Build").getint("Threads") > 0 and config.section("Build").getint("Jobs") > 0,
            "Build thread counts must be positive")

    reference = None
    for path in config.config_files():
        require(path.is_file(), f"Missing fixed configuration file: {path}")
    for name in config.csv("SPANN", "SearchConfigs"):
        parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
        with config.relative_path(name).open() as stream:
            parser.read_file(stream)
        require(parser.sections() == ["SearchSSDIndex"], f"Not a native search-only INI: {name}")
        fields = dict(parser["SearchSSDIndex"])
        require(int(fields.pop("internalresultnum")) >= benchmark.getint("ResultNum"),
                f"Invalid SPANN budget: {name}")
        require(int(fields["numberofthreads"]) == benchmark.getint("NumberOfThreads")
                and int(fields["resultnum"]) == benchmark.getint("ResultNum")
                and int(fields["searchpostingpagelimit"]) > 0, f"Invalid shared search controls: {name}")
        if reference is None:
            reference = fields
        require(fields == reference, "SPANN sweep INIs may differ only in InternalResultNum")
    require(config.section("SPANN")["InitialSearchConfig"] in config.csv("SPANN", "SearchConfigs"),
            "Initial SPANN INI must be one of the fixed sweep files")
    return config
