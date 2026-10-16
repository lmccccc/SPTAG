#!/usr/bin/env python3
"""YFCC camera-OR controller for the repository's threeway native/result pipeline."""
import argparse
from collections import defaultdict
import configparser
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import statistics
import struct
import subprocess
import sys
import time

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "results"))
import update_results as registry

ENGINES = {"lion": "SPTAG_adaptive", "pipeann": "PipeANN", "diskann": "Filtered_DiskANN"}
BAD_FIELDS = ("invalid_queries", "invalid_ids", "duplicate_ids", "nonmatching_ids",
              "nonfinite_distances", "query_exceptions")
COUNTS = [5000000, 2000000, 1000000, 500000, 100000, 10000]


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def identity(path):
    path = Path(path).absolute()
    stat = path.stat()
    item = {"path": str(path), "resolved": str(path.resolve()), "bytes": stat.st_size,
            "device": stat.st_dev, "inode": stat.st_ino, "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns}
    if stat.st_size < 64 * 1024**2:
        item["sha256"] = registry.file_sha(path)
    return item


def matrix(path, dtype):
    path = Path(path)
    with path.open("rb") as stream:
        rows, cols = struct.unpack("<II", stream.read(8))
    require(path.stat().st_size == 8 + rows * cols * np.dtype(dtype).itemsize,
            f"Invalid matrix extent: {path}")
    return np.memmap(path, dtype=dtype, mode="r", offset=8, shape=(rows, cols))


def validate_capture(ids, distances, base, queries, attrs, tags, truth):
    require(ids.shape == distances.shape == truth.shape and len(ids) == len(queries), "Capture shape mismatch")
    valid = ids != np.uint32(0xffffffff)
    require(np.all(ids[valid] < len(base)), "Out-of-range original IDs")
    require(not np.any(valid[:, 1:] & ~valid[:, :-1]), "Valid IDs after short-result suffix")
    sorted_ids = np.sort(ids, axis=1)
    require(not np.any((sorted_ids[:, 1:] == sorted_ids[:, :-1]) &
                       (sorted_ids[:, 1:] != np.uint32(0xffffffff))), "Duplicate IDs")
    require(np.all(np.isfinite(distances[valid])), "Nonfinite valid distance")
    ranked = np.where(valid, distances, np.inf)
    require(np.all(ranked[:, 1:] >= ranked[:, :-1]), "Unsorted distances")
    q, k = np.nonzero(valid)
    require(np.all(np.isin(attrs[ids[q, k], 2], tags)), "Camera predicate violation")
    delta = base[ids[q, k]].astype(np.int32) - queries[q].astype(np.int32)
    exact = np.sum(delta * delta, axis=1, dtype=np.int64)
    require(np.array_equal(exact, distances[q, k]), "Distances differ from exact original-vector L2")
    hits = np.any(ids[:, :, None] == truth[:, None, :], axis=2) & valid
    return float(hits.sum() / truth.size)


def select_curve_points(points, limit=12, minimum_qps=10):
    eligible = [p for p in points if p["qps_min"] >= minimum_qps and p["recall"] >= .595]
    if not eligible:
        return []
    frontier = []
    best_qps = -1
    for point in sorted(eligible, key=lambda p: (-p["recall"], -p["qps"])):
        if point["qps"] > best_qps:
            frontier.append(point)
            best_qps = point["qps"]
    chosen = []
    for target in (.60, .70, .80, .85, .89, .90, .91, .92, .93, .95, .97, .99):
        above = [p for p in frontier if p["recall_min_observed"] >= target]
        point = max(above, key=lambda p: p["qps"]) if above else max(frontier, key=lambda p: p["recall"])
        if point not in chosen:
            chosen.append(point)
    # Fill gaps with actual observations, not interpolated results.
    while len(chosen) < min(limit, len(eligible)):
        remaining = [p for p in eligible if p not in chosen]
        point = max(remaining, key=lambda p: (min(abs(p["recall"] - q["recall"]) for q in chosen), p["qps"]))
        chosen.append(point)
    return sorted(chosen[:limit], key=lambda p: (p["recall"], p["search_value"]))


class Campaign:
    def __init__(self, config):
        self.config_path = Path(config).resolve()
        self.c = json.loads(self.config_path.read_text())
        self.root = Path(self.c["output_directory"])
        self.root.mkdir(parents=True, exist_ok=True)
        self.prepared = self.root / "prepared"
        self.gt = Path(self.c["groundtruth_directory"])
        self.w = json.loads((self.gt / "workloads.json").read_text())
        self.topk = self.c.get("topk", 10)
        require(type(self.topk) is int and self.topk in (10, 100), "Explicit topk must be 10 or 100")
        self.dataset = self.w["dataset"]
        self.names = self.w["scenarios"]
        require((self.w["vector_count"], self.w["dimension"], self.w["query_count"], self.w["topk"]) ==
                (10000000, 192, 1000, self.topk),
                f"This campaign requires the authenticated YFCC10M/192/1000/Top{self.topk} cohort")
        require([self.w["truth"][name]["candidate_count"] for name in self.names] == COUNTS,
                "Camera selectivity counts differ from the approved workloads")
        for marker in ("completion.json", "verification.json"):
            proof = json.loads((self.gt / marker).read_text())
            require(proof["state"] in ("complete", "passed") and
                    proof["workloads_sha256"] == registry.file_sha(self.gt / "workloads.json"),
                    "Groundtruth completion/verification seal mismatch")
        self.tags = {}
        for name in self.names:
            clauses = self.w["predicates"][name]["or"]
            require(all(set(clause) == {"categorical_eq"} and clause["categorical_eq"][0] == 2
                        for clause in clauses), "Only original-column-2 camera OR is permitted")
            self.tags[name] = sorted(clause["categorical_eq"][1] for clause in clauses)
            require(len(set(self.tags[name])) == len(clauses) and 0 < min(self.tags[name]) <= max(self.tags[name]) <= 66,
                    "Invalid camera vocabulary")
            for key in ("ids", "distances", "native_ids"):
                entry = self.w["truth"][name]
                require(registry.file_sha(entry[key]) == entry[key + "_sha256"], "Groundtruth payload hash mismatch")
        self.base = matrix(self.w["base_file"], "<u1")
        self.queries = np.load(self.w["queries"], mmap_mode="r")
        self.attrs = np.memmap(self.w["attribute_source"], dtype="<u4", mode="r", shape=(10000000, 4))
        require(self.base.shape == (10000000, 192) and self.queries.shape == (1000, 192), "Input shape mismatch")
        self.truth = {name: np.load(self.w["truth"][name]["ids"]) for name in self.names}
        for name in self.names:
            require(self.truth[name].shape == (1000, self.topk), "Groundtruth depth differs from requested recall depth")
            count = int(np.count_nonzero(np.isin(self.attrs[:, 2], self.tags[name])))
            require(count == self.w["truth"][name]["candidate_count"], "Actual camera count mismatch")
            require(validate_capture(self.truth[name], np.load(self.w["truth"][name]["distances"]),
                                     self.base, self.queries, self.attrs, self.tags[name], self.truth[name]) == 1,
                    "Groundtruth integrity failure")
        self.env = dict(os.environ)
        banned = ("SPTAG_", "SPANN_", "PIPEANN_", "DISKANN_", "FILTERED_DISKANN_",
                  "OMP_", "GOMP_", "KMP_", "MKL_", "OPENBLAS_")
        require(not any(name.startswith(banned) or name in ("LD_PRELOAD", "ADDITIONAL_DEFINITIONS",
                                                          "CXXFLAGS", "CPPFLAGS") for name in self.env),
                "Remove native benchmark environment overrides before running")
        self.env["LD_LIBRARY_PATH"] = self.c["library_path"]
        self.env["GLIBC_TUNABLES"] = "glibc.rtld.optional_static_tls=2000000"
        self.events = []
        self.skipped = []
        self.selected = []
        self.prepare()

    def status(self, state, **details):
        save(self.root / "status.json", {"state": state, "updated_at": now(), "pid": os.getpid(), "topk": self.topk,
                                        "accepted_points": len(self.events), **details})
        print(json.dumps({"time": now(), "state": state, **details}), flush=True)

    def prepare(self):
        if self.prepared.exists():
            return
        self.prepared.mkdir()
        (self.prepared / "query.u8bin").symlink_to(self.c["query_prefix"])
        prefix = self.c["indexes"]["pipeann"]
        for suffix in (".label.0", ".label.0.filter"):
            (self.prepared / ("base" + suffix)).symlink_to(prefix + suffix)
        for name in self.names:
            (self.prepared / f"gt_{name}.u32bin").symlink_to(self.w["truth"][name]["native_ids"])
            binding = self.prepared / f"camera.query.{name}.spmat"
            binding.symlink_to(Path(self.c["baseline_inputs"]) / binding.name)
            save(self.prepared / f"pipeann.{name}.json", {
                "attr_indexes": [{"name": "camera", "key": 0, "type": "label", "file": prefix + ".label.0"}],
                "filter": "camera = $$camera", "bindings": {"camera": str(binding)}})

    def register(self):
        paths = {self.config_path, Path(__file__), HERE / "results/update_results.py",
                 self.gt / "workloads.json", self.gt / "verification.json", self.gt / "completion.json",
                 Path(self.c["lion_build_ini"]), Path(self.c["query_prefix"]),
                 Path(self.w["base_file"]), Path(self.w["attribute_source"]), Path(self.w["queries"])}
        if self.c.get("previous_results"):
            paths.add(Path(self.c["previous_results"]))
        paths.update(self.prepared.iterdir())
        paths.update(Path(self.c["toolchain"]).glob("*"))
        for engine, prefix in self.c["indexes"].items():
            directory = Path(prefix) if engine == "lion" else Path(prefix).parent
            paths.update(path for path in directory.rglob("*") if path.is_file())
        paths.update(path for path in Path(self.c["canonical_lion_index"]).rglob("*") if path.is_file())
        for engine in ENGINES:
            build = json.loads((Path(self.c["toolchain"]) / (engine + ".build.json")).read_text())
            require(build["status"] == "complete", "Incomplete native adapter build")
            for item in build["inputs"] + build["runtime_libraries"] + [build["binary"]]:
                require(registry.file_sha(item["path"]) == item["sha256"], "Native build input/library changed")
                paths.add(Path(item["path"]))
        record = {"schema_version": 1, "created_at": now(), "files": [identity(p) for p in sorted(paths) if p.is_file()],
                  "config": self.c, "protocol": ("threeway-native-camera-or-compact-v1"
                                                  if self.c.get("compact_protocol") else "threeway-native-camera-or-v1"),
                  "disclosures": ["Shared Hyper-V host; CPU affinity is not exclusive physical cores.",
                                  "Warm fixed working set: Lion buffered IO, baselines direct IO; no global cache flush.",
                                  "Single QPS excludes validation; throughput includes cohort dispatch/recall accounting.",
                                  "First/last cohorts have offline exact-distance validation, outside the native timer."]}
        path = self.root / "registration.json"
        if path.exists():
            saved = json.loads(path.read_text())
            require(saved["files"] == record["files"] and saved["config"] == record["config"],
                    "Campaign inputs/client/index changed; use a new campaign, never overwrite provenance")
        else:
            save(path, record)
        self.registration_sha = registry.file_sha(path)
        catalog = {"schema_version": 1, "kind": "catalog", "revision": 1, "completed_at": record["created_at"],
                   "source": {"description": "Authenticated YFCC10M camera OR native experiment"},
                   "catalog": {"datasets": [self.dataset], "studies": [{"id": "camera-or"}],
                               "expected_cells": [
                                   {"study": "camera-or", "variant": "default", "dataset": self.dataset,
                                    "algorithm": engine, "scenario": name, "phase": phase, "threads": threads}
                                   for engine in ENGINES for name in self.names
                                   for phase, group in (("single", [1]), ("throughput", self.c["threads"]))
                                   for threads in group]}}
        if not (self.root / "results/history.jsonl").exists():
            registry.update(self.root / "results", [catalog])

    def profile(self, directory, engine, names, controls, maxcheck, phase, threads, smoke, settings=None):
        ini = configparser.ConfigParser(interpolation=None)
        ini.optionxform = str
        ini["Dataset"] = {"Vectors": self.w["base_file"], "Queries": self.w["query_file"],
                          "Attributes": self.w["attribute_source"], "VectorCount": "10000000",
                          "Dimension": "192", "AttributeColumns": "4", "CategoricalColumn": "2", "NumericColumn": "0"}
        ini["Run"] = {"OutputDirectory": str(directory), "PreparedDirectory": str(self.prepared)}
        ini["Benchmark"] = {"QueryCount": "1000", "WarmupQueries": "1000", "TopK": str(self.topk),
                            "SingleRepeats": "2", "Scenarios": ",".join(names), "AllowShortResults": "true",
                            "MinimumQPS": "0" if smoke else str(self.c["minimum_qps"])}
        if self.c.get("compact_protocol"):
            ini["Benchmark"].update(WarmupPolicy="once_per_scenario", MaximumSeconds="100")
        nodes = ",".join(map(str, self.c["cpu_nodes"]))
        memory_nodes = ",".join(map(str, self.c["memory_nodes"]))
        ini["Single"] = {"CPUNodes": nodes, "MemoryNodes": memory_nodes}
        ini["Throughput"] = {"Threads": str(threads), "Repeats": "3", "MinimumSeconds": "30",
                             "RecallTargets": "0.9,0.95", "AIOReserve": "8192", "CPUNodes": nodes,
                             "MemoryNodes": memory_nodes, "ResourcePolicy": "current-host-limit"}
        if self.c.get("compact_protocol"):
            ini["Throughput"]["MinimumSeconds"] = "0"
        values = ",".join(map(str, controls))
        prefix = self.c["indexes"][engine]
        if engine == "lion":
            ini["SPANN"] = {"IndexDirectory": prefix, "NProbe": values, "Protocol": "explicit-ini-v1"}
            build = configparser.ConfigParser(interpolation=None)
            build.optionxform = str
            build.read(self.c["lion_build_ini"])
            ini["SearchSSDIndex"] = dict(build["SearchSSDIndex"])
            ini["SearchSSDIndex"].update(MaxCheck=str(maxcheck), InternalResultNum=str(controls[0]), ResultNum=str(self.topk),
                                          HashTableExponent="4", LogPhaseTime="false", LogPathStats="false",
                                          DumpHeads="0", EnableHybridDistance="false")
        elif engine == "pipeann":
            ini["PipeANN"] = {"IndexPrefix": prefix, "SourceDirectory": self.c["pipeann_source"], "LSweep": values,
                              "PipelineWidth": "32", "SearchMode": "2", "UnfilteredMemoryL": "10",
                              "FilteredMemoryL": "0", "FilterMode": "auto"}
        else:
            ini["DiskANN"] = {"IndexPrefix": prefix, "LSweep": values, "BeamWidth": "2",
                              "CacheNodes": "0", "AIOEventsPerThread": "1024"}
        for name in names:
            ini["Scenario." + name] = {"Kind": "categorical_or", "Tags": ",".join(map(str, self.tags[name])),
                                       "CandidateCount": str(self.w["truth"][name]["candidate_count"]),
                                       "Truth": self.w["truth"][name]["native_ids"],
                                       "FilterConfig": str(self.prepared / f"pipeann.{name}.json")}
        with (directory / "profile.ini").open("x") as stream:
            ini.write(stream)
        repeats = 1 if smoke else (2 if phase == "single" else 3)
        mixed = settings is not None and engine == "lion"
        plan = [(name, item["value"], threads, repeat, item["maxcheck"] if mixed else 0)
                for name in names for item in (settings or [{"value": v, "maxcheck": 0} for v in controls])
                for repeat in range(1, repeats + 1)]
        plans = directory / "plans"
        plans.mkdir()
        with (plans / f"{ENGINES[engine]}.{phase}.tsv").open("x") as stream:
            stream.write("scenario\tcontrol_index\tthread_index\trepeat" + ("\tmaxcheck" if mixed else "") + "\n")
            for name, value, _, repeat, mc in plan:
                stream.write(f"{name}\t{controls.index(value)}\t0\t{repeat}" + (f"\t{mc}" if mixed else "") + "\n")
        return plan

    def batch(self, engine, names, controls, maxcheck=0, phase="single", threads=1, smoke=False, settings=None):
        controls = sorted(set(controls))
        key = {"engine": engine, "names": names, "controls": controls, "maxcheck": maxcheck,
               "phase": phase, "threads": threads, "smoke": smoke, "settings": settings}
        label = f"{'smoke' if smoke else phase}-{engine}-mc{maxcheck}-t{threads}-{registry.digest(key)[:12]}"
        directory = self.root / "batches" / label
        accepted_path = directory / "accepted.json"
        if accepted_path.exists():
            accepted = json.loads(accepted_path.read_text())
            require(accepted["registration_sha256"] == self.registration_sha, "Batch registration mismatch")
            for name, digest in accepted["sha256"].items():
                require(registry.file_sha(directory / name) == digest, "Accepted batch file mutation: " + name)
            events = json.loads((directory / "events.json").read_text())
            skipped = json.loads((directory / "skipped.json").read_text())
        else:
            directory.mkdir(parents=True, exist_ok=False)
            plan = self.profile(directory, engine, names, controls, maxcheck, phase, threads, smoke, settings)
            command = ["numactl", "--cpunodebind=" + ",".join(map(str, self.c["cpu_nodes"])),
                       "--interleave=" + ",".join(map(str, self.c["memory_nodes"])),
                       str(Path(self.c["toolchain"]) / (engine + "Bench")), str(directory / "profile.ini"), phase]
            save(directory / "execution.json", {"state": "registered", "argv": command, "environment": {
                k: self.env[k] for k in ("LD_LIBRARY_PATH", "GLIBC_TUNABLES")}, "jobs": plan})
            self.status("running", batch=label, phase=phase, engine=engine, threads=threads, planned_repetitions=len(plan))
            with (directory / "native.log").open("x") as log:
                child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=self.env)
                try:
                    returncode = child.wait(timeout=self.c["batch_timeout_seconds"])
                except subprocess.TimeoutExpired:
                    child.terminate()
                    try:
                        child.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
                    save(directory / "failure.json", {"state": "timeout", "pid": child.pid, "argv": command})
                    raise RuntimeError("Native batch timed out; timeout is not a measured QPS or recall result")
            save(directory / "execution.json", {"state": "exited", "argv": command, "pid": child.pid,
                                                "returncode": returncode, "jobs": plan})
            timeout_path = directory / "native" / ENGINES[engine] / phase / "timeout.json"
            timeout = json.loads(timeout_path.read_text()) if returncode == 124 and timeout_path.exists() else None
            require(returncode == 0 or (self.c.get("compact_protocol") and timeout),
                    f"Native batch failed with exit {returncode}: {directory / 'native.log'}")
            rows, skipped, ready, complete = [], [], [], []
            for line in (directory / "native.log").read_text(errors="strict").splitlines():
                for prefix, records in (("THREEWAY_RESULT ", rows), ("THREEWAY_SKIP ", skipped),
                                        ("THREEWAY_READY ", ready), ("THREEWAY_COMPLETE ", complete)):
                    if line.startswith(prefix):
                        records.append(json.loads(line[len(prefix):]))
            require(len(ready) == 1 and ready[0]["pid"] == child.pid and ready[0]["job_count"] == len(plan),
                    "Native ready/plan mismatch")
            if timeout:
                require(not complete and timeout["limit_seconds"] == 100 and timeout["stage"] in ("warmup", "measurement"),
                        "Invalid native timeout evidence")
                observed_before_timeout = {(r["scenario"], r["L"], r["threads"], r["repeat"], r.get("maxcheck", 0))
                                           for r in rows + skipped}
                for name, value, team, repeat, mc in plan:
                    if (name, value, team, repeat, mc) not in observed_before_timeout:
                        skipped.append({"scenario": name, "L": value, "threads": team, "repeat": repeat,
                                        "maxcheck": mc, "reason": "cohort_timeout_or_remaining_curve_not_run",
                                        "timeout": timeout, "no_valid_qps": True})
            else:
                require(len(complete) == 1 and complete[0]["completed_jobs"] == len(rows) and
                        complete[0].get("skipped_jobs", 0) == len(skipped) and len(rows) + len(skipped) == len(plan),
                        "Native planned/completed/skipped accounting mismatch")
            observed = [(r["scenario"], r["L"], r["threads"], r["repeat"], r.get("maxcheck", 0)) for r in rows + skipped]
            require(len(set(observed)) == len(plan) and set(observed) == set(plan), "Missing/duplicate/unplanned points")
            for row in skipped:
                if "timeout" not in row:
                    evidence = [r for r in rows if r["scenario"] == row["scenario"] and r["threads"] == row["threads"]
                                and r.get("maxcheck", 0) == row.get("maxcheck", 0)
                                and r["L"] == row["slow_L"] and r["qps"] < self.c["minimum_qps"]]
                    require(evidence and row["L"] > row["slow_L"], "Skipped point has no measured slow-budget evidence")
                row.update(engine=engine, maxcheck=row.get("maxcheck", maxcheck), phase=phase, log=str(directory / "native.log"))
            warmed = set()
            for row in rows:
                require(row["engine"] == ENGINES[engine] and row["phase"] == phase and
                        all(row[field] == 0 for field in BAD_FIELDS), "Invalid native result")
                warm_key = (row["scenario"], row["threads"])
                expected_warmup = 1000 if not self.c.get("compact_protocol") or warm_key not in warmed else 0
                warmed.add(warm_key)
                require(row["queries"] == row["passes"] * 1000 and row["warmup_queries"] == expected_warmup and
                        math.isclose(row["qps"], row["queries"] / row["elapsed_seconds"], rel_tol=1e-10),
                        "Invalid timing/cohort accounting")
                require((row["passes"] == 1 and row["elapsed_seconds"] <= 100) if self.c.get("compact_protocol") else
                        ((phase == "single" and row["passes"] == 1) or
                         (phase == "throughput" and row["elapsed_seconds"] >= 30)), "Measurement duration mismatch")
                for prefix in ("first", "last"):
                    ids_path, distances_path = Path(row[prefix + "_ids"]), Path(row[prefix + "_distances"])
                    require(ids_path.is_relative_to(directory) and distances_path.is_relative_to(directory),
                            "Capture escaped its registered output")
                    recall = validate_capture(matrix(ids_path, "<u4"), matrix(distances_path, "<f4"),
                                              self.base, self.queries, self.attrs, self.tags[row["scenario"]],
                                              self.truth[row["scenario"]])
                    require(math.isclose(recall, row[prefix + "_recall"], abs_tol=1e-12), "Native/capture recall mismatch")
                if phase == "single":
                    require(math.isclose(row["recall"], row["first_recall"], abs_tol=1e-12), "Single recall mismatch")
            save(directory / "single_raw.json" if phase == "single" else directory / "throughput_raw.json", rows)
            complete_rows = rows
            if timeout:
                counts = defaultdict(int)
                expected = 2 if phase == "single" else 3
                for row in rows:
                    counts[(row["scenario"], row["L"], row["threads"], row.get("maxcheck", 0))] += 1
                complete_rows = [row for row in rows if
                                 counts[(row["scenario"], row["L"], row["threads"], row.get("maxcheck", 0))] == expected]
                save(directory / "incomplete-repetitions.json", [row for row in rows if row not in complete_rows])
            events = [] if smoke else self.normalize(engine, complete_rows, maxcheck, directory)
            save(directory / "events.json", events)
            save(directory / "skipped.json", skipped)
            seal = {str(path.relative_to(directory)): registry.file_sha(path)
                    for path in sorted(directory.rglob("*")) if path.is_file()}
            save(accepted_path, {"state": "validated_completed_groups_after_timeout" if timeout else "validated",
                                 "completed_at": now(), "key": key,
                                 "registration_sha256": self.registration_sha, "sha256": seal})
        if not smoke:
            registry.update(self.root / "results", events)
            self.events.extend(events)
            self.skipped.extend(skipped)
            self.report(False)
        return events

    def normalize(self, engine, rows, maxcheck, directory):
        groups = defaultdict(list)
        for row in rows:
            groups[(row["scenario"], row["L"], row["threads"], row.get("maxcheck", maxcheck))].append(row)
        events = []
        for (name, value, threads, point_maxcheck), measurements in groups.items():
            expected = 2 if measurements[0]["phase"] == "single" else 3
            require(sorted(row["repeat"] for row in measurements) == list(range(1, expected + 1)),
                    "Partial repetition group cannot be published")
            client = engine + "-" + registry.file_sha(Path(self.c["toolchain"]) / (engine + "Bench"))[:16]
            for row in measurements:
                row.update(search_value=value, client_version=client, topk=self.topk)
            recalls = [r["recall"] for r in measurements]
            rates = [r["qps"] for r in measurements]
            parameters = {"ResultNum": self.topk, "WarmupQueries": 1000, "AllowShortResults": True,
                          "Index": self.c["indexes"][engine], "Filter": "camera-or",
                          "CPUNodes": self.c["cpu_nodes"], "MemoryNodes": self.c["memory_nodes"]}
            if self.c.get("compact_protocol"):
                parameters.update(WarmupPolicy="once_per_scenario", MaximumSeconds=100,
                                  MeasuredCohortsPerRepetition=1, MinimumSeconds=0)
            if engine == "lion":
                parameters.update(MaxCheck=point_maxcheck, SearchPostingPageLimit=3, MaxDistRatio=8,
                                  PostingAnchorCount=0, PostingAdditionalMaxCheck=2048, PostingNavigationWidth=128,
                                  PostingMatchRatePercent=12.5, PostingMatchWindow=1024,
                                  EnablePostingNavigation=True, DisableCrossEdges=True)
            elif engine == "pipeann":
                parameters.update(PipelineWidth=32, FilteredMemoryL=0, FilterMode="auto")
            else:
                parameters.update(BeamWidth=2, CacheNodes=0)
            point = {"scenario": name, "predicate": "camera IN (" + ",".join(map(str, self.tags[name])) + ")",
                     "phase": measurements[0]["phase"], "threads": threads, "cohort_queries": 1000, "topk": self.topk,
                     "parameters": json.dumps(parameters, sort_keys=True), "curve_id": f"{engine}-mc{point_maxcheck}",
                     "search_parameter": "nprobe" if engine == "lion" else "L", "search_value": value,
                     "selectivity": self.w["truth"][name]["selectivity"],
                     "candidate_count": self.w["truth"][name]["candidate_count"],
                     "recall": statistics.median(recalls), "recall_min_observed": min(r["recall_min_batch"] for r in measurements),
                     "recall_max_repeat": max(recalls), "qps": statistics.median(rates), "qps_min": min(rates),
                     "qps_max": max(rates), "repeats": expected, "expected_repeats": expected,
                     "complete_repeats_same_client": True}
            event = {"schema_version": 1, "kind": "measurement", "study": "camera-or", "variant": "default",
                     "dataset": self.dataset, "algorithm": engine,
                     "version": {"id": client, "client": client, "index": Path(self.c["indexes"][engine]).parent.name
                                 if engine != "lion" else Path(self.c["indexes"][engine]).name, "order": 1},
                     "completed_at": now(), "point": point, "measurements": measurements,
                     "source": {"description": "Threeway native cohorts with exact original-vector camera-OR capture validation",
                                "directory": str(directory), "registration_sha256": self.registration_sha,
                                "native_log": str(directory / "native.log"),
                                "profile_sha256": registry.file_sha(directory / "profile.ini")}}
            registry.validate_event(event)
            events.append(event)
        return events

    def cell_points(self, engine, name):
        return [event["point"] for event in self.events if event["algorithm"] == engine and
                event["point"]["scenario"] == name and event["point"]["phase"] == "single"]

    def report(self, complete):
        coverage = []
        slow = []
        for engine in ENGINES:
            for name in self.names:
                all_points = self.cell_points(engine, name)
                points = [p for p in all_points if p["qps_min"] >= self.c["minimum_qps"]]
                recall = sorted(set(p["recall"] for p in points))
                band = sorted(set(r for r in recall if .89 <= r <= .95))
                coverage.append({"algorithm": engine, "scenario": name, "distinct_recall_points": len(recall),
                                 "points_89_to_95": band, "recall_min": min(recall) if recall else None,
                                 "recall_max": max(recall) if recall else None,
                                 "covers_89_to_95": bool(recall and min(recall) <= .89 and max(recall) >= .95),
                                 "attains_99": bool(recall and max(recall) >= .99),
                                 "at_least_eight_points": len(recall) >= 8,
                                 "note": "Gaps are observed grid coverage, not a proof of mathematical unreachability."})
                slow.extend({"algorithm": engine, "scenario": name, "parameters": json.loads(p["parameters"]),
                             "search_value": p["search_value"], "qps_min": p["qps_min"], "recall": p["recall"],
                             "reason": "measured_qps_below_10"} for p in all_points
                            if p["qps_min"] < self.c["minimum_qps"])
        save(self.root / "final.json", {"schema_version": 1, "state": "complete" if complete else "in_progress",
                                       "topk": self.topk, "recall_metric": f"Recall@{self.topk}", "dataset": self.dataset,
                                       "updated_at": now(), "registration": str(self.root / "registration.json"),
                                       "registry": str(self.root / "results/current.json"),
                                       "coverage": coverage, "slow_points": slow, "skipped": self.skipped,
                                       "measurements": self.events,
                                       "selected_single_thread_points": self.selected,
                                       "discovery_results": self.c.get("previous_results")})

    def run_compact(self):
        previous = (json.loads(Path(self.c["previous_results"]).read_text()) if self.c.get("previous_results")
                    else {"measurements": []})
        hints = defaultdict(list)
        reused = defaultdict(list)
        engine_order = self.c.get("engine_order", list(ENGINES))
        require(len(engine_order) == len(ENGINES) and set(engine_order) == set(ENGINES),
                "engine_order must list each algorithm exactly once")
        verified = set()
        for event in previous["measurements"]:
            require(event["point"]["topk"] == self.topk,
                    "Cannot reuse discovery curves from a different recall depth")
            directory = Path(event["source"]["directory"])
            if directory not in verified:
                seal = json.loads((directory / "accepted.json").read_text())
                for relative, digest in seal["sha256"].items():
                    require(registry.file_sha(directory / relative) == digest,
                            "Archived discovery evidence changed: " + str(directory / relative))
                verified.add(directory)
            require(event in json.loads((directory / "events.json").read_text()),
                    "Discovery hint is not a sealed measured event")
            if event["point"]["phase"] == "single":
                engine, point = event["algorithm"], event["point"]
                parameters = json.loads(point["parameters"])
                if engine == "lion" and self.c.get("lion_maxchecks") and parameters["MaxCheck"] not in self.c["lion_maxchecks"]:
                    continue
                hints[(engine, point["scenario"])].append(point)
                if self.c.get("reuse_completed_curves"):
                    require(parameters.get("WarmupPolicy") == "once_per_scenario" and
                            parameters.get("MeasuredCohortsPerRepetition") == 1 and
                            parameters.get("MinimumSeconds") == 0 and parameters.get("MaximumSeconds") == 100 and
                            parameters["ResultNum"] == self.topk and point["cohort_queries"] == 1000 and
                            point["expected_repeats"] == point["repeats"] == 2 and
                            parameters["Index"] == self.c["indexes"][engine] and
                            parameters["CPUNodes"] == self.c["cpu_nodes"] and
                            parameters["MemoryNodes"] == self.c["memory_nodes"],
                            "Cannot reuse measurements with a different query/index/placement protocol")
                    client = engine + "-" + registry.file_sha(Path(self.c["toolchain"]) / (engine + "Bench"))[:16]
                    require(event["version"]["client"] == client, "Cannot reuse a different native client")
                    old_registration = json.loads((directory.parents[1] / "registration.json").read_text())
                    require(registry.file_sha(directory.parents[1] / "registration.json") ==
                            event["source"]["registration_sha256"], "Reused registration hash mismatch")
                    require(old_registration["config"]["groundtruth_directory"] == self.c["groundtruth_directory"],
                            "Cannot reuse a different groundtruth cohort")
                    registry.validate_event(event)
                    reused[(engine, point["scenario"])].append(event)
        plan = []
        for engine in engine_order:
            for name in self.names:
                prior = hints[(engine, name)]
                chosen = select_curve_points(prior)
                if chosen:
                    settings = [{"value": p["search_value"], "maxcheck": json.loads(p["parameters"]).get("MaxCheck", 0)}
                                for p in chosen]
                    origin = ("reused_same_protocol_sealed_repetitions" if reused[(engine, name)]
                              else "sealed_discovery_parameters_only_timings_remeasured")
                else:
                    floor = max(self.topk, len(self.tags[name])) if engine == "diskann" else self.topk
                    values = sorted(set([floor] + [v for v in self.c["search_values"] if v >= floor]))
                    checks = self.c["lion_maxchecks"] if engine == "lion" else [0]
                    settings = [{"value": value, "maxcheck": check} for value in values for check in checks]
                    origin = "single_resident_discovery_sweep_then_select_12"
                settings = sorted(settings, key=lambda item: (item["value"], item["maxcheck"]))
                plan.append({"algorithm": engine, "scenario": name, "origin": origin, "settings": settings})
        save(self.root / "single-parameter-plan.json", {
            "warmup": "once per algorithm/scenario/thread, shared across MaxCheck/nprobe and repetitions",
            "repetitions": 2, "query_count": 1000, "topk": self.topk,
            "minimum_seconds": 0, "maximum_seconds": 100, "curves": plan})
        save(self.root / "reused-measurements.json", {
            "source": self.c.get("previous_results"), "reason": "reuse complete sealed groups under the identical protocol",
            "events": [event for group in reused.values() for event in group]})
        for group in reused.values():
            self.events.extend(group)
            registry.update(self.root / "results", group)
        for cell in plan:
            engine, name, settings = cell["algorithm"], cell["scenario"], cell["settings"]
            events = reused[(engine, name)]
            if not events:
                events = self.batch(engine, [name], [item["value"] for item in settings],
                                    settings[0]["maxcheck"], settings=settings if engine == "lion" else None)
            selected = select_curve_points([event["point"] for event in events])
            self.selected.extend({"algorithm": engine, **point} for point in selected)
            self.report(False)
        save(self.root / "concurrency-parameters.json", {
            "state": "frozen", "created_at": now(), "selection": "up to 12 actually measured settings per curve; "
            "target recall 60,70,80,85,89,90,91,92,93,95,97,99%; QPS>=10 in both repetitions; "
            "no interpolation and no concurrency retuning",
            "points": self.selected, "threads": self.c["threads"], "query_count_per_repetition": 1000,
            "repetitions": 3, "topk": self.topk, "minimum_seconds": 0, "maximum_seconds": 100,
            "warmup_policy": "once per algorithm/scenario/thread"} )
        for threads in self.c["threads"]:
            for engine in engine_order:
                for name in self.names:
                    points = [p for p in self.selected if p["algorithm"] == engine and p["scenario"] == name]
                    if not points:
                        self.skipped.append({"engine": engine, "scenario": name, "phase": "throughput",
                                             "threads": threads, "reason": "no_eligible_single_thread_parameters"})
                        continue
                    settings = sorted([{"value": p["search_value"],
                                        "maxcheck": json.loads(p["parameters"]).get("MaxCheck", 0)}
                                       for p in points], key=lambda item: (item["value"], item["maxcheck"]))
                    self.batch(engine, [name], [item["value"] for item in settings], settings[0]["maxcheck"],
                               "throughput", threads, settings=settings if engine == "lion" else None)
        saved = json.loads((self.root / "registration.json").read_text())
        for item in saved["files"]:
            require(identity(item["path"]) == item, "Input/index/client mutated during the campaign")
        registry.update(self.root / "results")
        self.report(True)
        self.status("complete", final_json=str(self.root / "final.json"))

    def run(self, smoke_only=False):
        self.register()
        if self.c.get("compact_protocol"):
            require(not smoke_only, "Compact protocol validates measured cohorts directly; no extra smoke warmup")
            return self.run_compact()
        for engine in ENGINES:
            self.batch(engine, self.names, [128], 16324 if engine == "lion" else 0, smoke=True)
        save(self.root / "index-query-validation.json", {"state": "passed", "completed_at": now(),
                                                       "engines": list(ENGINES), "scenarios": self.names})
        if smoke_only:
            self.status("query_validated")
            return
        for engine in ENGINES:
            for name in self.names:
                checks = self.c["lion_maxchecks"] if engine == "lion" else [0]
                floor = max(self.topk, len(self.tags[name])) if engine == "diskann" else self.topk
                initial = sorted(set([floor] + [v for v in self.c["search_values"] if v >= floor]))
                for maxcheck in checks:
                    events = self.batch(engine, [name], initial, maxcheck)
                    while events:
                        last = max((e["point"] for e in events), key=lambda p: p["search_value"])
                        if last["recall_min_observed"] >= .99 or last["qps_min"] < self.c["minimum_qps"]:
                            break
                        value = last["search_value"] * 2
                        if value > self.c["maximum_search_value"]:
                            break
                        events = self.batch(engine, [name], [value], maxcheck)
                # Refine measured parameter brackets, never interpolate a recall/QPS observation.
                for _ in range(self.c["maximum_refinements"]):
                    points = self.cell_points(engine, name)
                    candidates = []
                    for maxcheck in checks:
                        branch = sorted((p for p in points if json.loads(p["parameters"]).get("MaxCheck", 0) == maxcheck),
                                        key=lambda p: p["search_value"])
                        for left, right in zip(branch, branch[1:]):
                            lo, hi = left["recall"], right["recall"]
                            if left["qps_min"] < self.c["minimum_qps"] or right["search_value"] - left["search_value"] <= 1:
                                continue
                            overlap = max(lo, .60) < min(hi, .99)
                            band = max(lo, .89) < min(hi, .95)
                            needed = band and hi - lo > .012 or (overlap and hi - lo > .05)
                            if needed:
                                value = (left["search_value"] + right["search_value"]) // 2
                                candidates.append((int(band), hi - lo, maxcheck, value))
                    if not candidates:
                        break
                    _, _, maxcheck, value = max(candidates)
                    self.batch(engine, [name], [value], maxcheck)
        selections = []
        for engine in ENGINES:
            for name in self.names:
                points = self.cell_points(engine, name)
                selected = [p for p in points if p["qps_min"] >= self.c["minimum_qps"] and .60 <= p["recall"] <= .995]
                # Include the first measured point crossing 99%, even if discrete budgets overshoot it.
                above = [p for p in points if p["qps_min"] >= self.c["minimum_qps"] and p["recall_min_observed"] >= .99]
                if above:
                    endpoint = max(above, key=lambda p: p["qps"])
                    if endpoint not in selected:
                        selected.append(endpoint)
                selections.extend({"algorithm": engine, **point} for point in selected)
        save(self.root / "concurrency-parameters.json", {
            "state": "frozen", "created_at": now(), "selection": "all single-thread measured settings in recall 60%-99.5%, "
            "plus fastest measured >=99% endpoint; QPS>=10 in both repetitions; no concurrency retuning",
            "points": selections, "threads": self.c["threads"], "minimum_measured_seconds":
            len(selections) * len(self.c["threads"]) * 3 * 30})
        for threads in self.c["threads"]:
            for engine in ENGINES:
                for name in self.names:
                    groups = defaultdict(list)
                    for point in selections:
                        if point["algorithm"] == engine and point["scenario"] == name:
                            groups[json.loads(point["parameters"]).get("MaxCheck", 0)].append(point["search_value"])
                    for maxcheck, controls in groups.items():
                        self.batch(engine, [name], controls, maxcheck, "throughput", threads)
        # Recheck physical inputs after all measurements before marking the campaign complete.
        saved = json.loads((self.root / "registration.json").read_text())
        for item in saved["files"]:
            require(identity(item["path"]) == item, "Input/index/client mutated during the campaign")
        registry.update(self.root / "results")
        self.report(True)
        self.status("complete", final_json=str(self.root / "final.json"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--smoke-only", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    root = Path(config["output_directory"])
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".campaign.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            status_file = Path(config["build_status"])
            while True:
                status = json.loads(status_file.read_text())
                if status["state"] == "built_pending_query_validation":
                    break
                require(status["state"] == "running", "Index queue did not finish successfully")
                pid = status["runner_pid"]
                require(Path(f"/proc/{pid}").exists(), "Index runner disappeared without completion")
                save(root / "status.json", {"state": "waiting_for_indexes", "updated_at": now(),
                                          "active_stage": status.get("active_stage"), "pid": os.getpid()})
                time.sleep(30)
            Campaign(args.config).run(args.smoke_only)
        except Exception as error:
            save(root / "failure.json", {"state": "failed", "updated_at": now(), "error": str(error)})
            save(root / "status.json", {"state": "failed", "updated_at": now(), "error": str(error)})
            raise


if __name__ == "__main__":
    main()
