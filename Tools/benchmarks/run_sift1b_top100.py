"""Query-only top100 curves for current SPTAG, PipeANN and Filtered DiskANN."""

import argparse
import configparser
import copy
import csv
import fcntl
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

import numpy as np

import run_sift1b_threeway as shared
from native_input_io import open_attributes, open_vectors, read_config
from official_benchmark_config import read_json, require, sha256_file, write_json
from sift1b_official_inputs import write_bin, write_binding

HERE = Path(__file__).resolve().parent
POSTFILTER = HERE / "hierarchical_shortcut_native/native_postfilter"
sys.path.insert(0, str(POSTFILTER))
from selectivity_common import confine_outputs, identity, inventory

SCENARIOS = {"broad_tag": "broad", "medium_tag": "medium", "sel_01pct": "sparse"}
VERSIONS = {
    "SPTAG": "local_label_v5/V5_local_admission_topk_client",
    "PipeANN": "archived_fixed_graph/page_lifetime_native_prefix",
    "Filtered_DiskANN": "R64_buildL1_FilteredL100_PQ32/linear_label_loader",
}


def save(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def table(path, rows):
    require(bool(rows), f"Refusing empty result table: {path}")
    with Path(path).open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def dominates(left, right):
    return (left["recall"] >= right["recall"] and left["qps"] >= right["qps"] and
            (left["recall"] > right["recall"] or left["qps"] > right["qps"]))


def build_files(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("path", "resolved") and isinstance(item, str) and Path(item).is_file():
                yield Path(item)
            else:
                yield from build_files(item)
    elif isinstance(value, list):
        for item in value:
            yield from build_files(item)


def validate_exact(ids, distances, base, queries, attrs, tag, topk):
    require(ids.shape == distances.shape == (len(queries), topk), "Wrong top-k result width")
    valid = ids >= 0
    require(np.all(ids >= -1) and np.all(ids[valid] < len(base)), "Invalid result ID")
    require(np.all(np.diff(valid.astype(np.int8), axis=1) <= 0), "Underfill must be a suffix")
    require(all(len(set(row[row >= 0])) == int(mask.sum()) for row, mask in zip(ids, valid)),
            "Duplicate returned IDs")
    if tag is not None:
        require(np.all(attrs[ids[valid], 0] == tag), "Returned ID violates the original label")
    require(np.all(np.isfinite(distances[valid])), "Nonfinite returned distance")
    for start in range(0, len(queries), 64):
        rows, ranks = np.nonzero(valid[start:start + 64])
        delta = base[ids[start + rows, ranks]].astype(np.int32) - queries[start + rows].astype(np.int32)
        exact = np.sum(delta * delta, axis=1, dtype=np.int64)
        require(np.array_equal(exact, distances[start + rows, ranks]), "Incorrect original-vector squared L2")
    require(np.all((distances[:, 1:] >= distances[:, :-1]) | ~valid[:, 1:]), "Unordered returned distances")
    return valid


def load_inputs(config):
    data = config.section("Dataset")
    base = open_vectors(config.path_value("Dataset", "Vectors"), "UInt8", data.getint("Dimension")).data
    queries = open_vectors(config.path_value("Dataset", "Queries"), "UInt8", base.shape[1]).data[:1000]
    attrs = open_attributes(config.path_value("Dataset", "Attributes"), len(base), 2)
    return base, queries, attrs


def prepare(config):
    shared.reject_benchmark_environment()
    root, bench = config.root, config.section("Benchmark")
    require((bench.getint("TopK"), bench.getint("QueryCount"), bench.getint("WarmupQueries"),
             bench.getint("SingleRepeats")) == (100, 1000, 1000, 2), "Wrong top100 protocol")
    require(tuple(config.csv("Benchmark", "Scenarios")) == tuple(SCENARIOS), "Unexpected scenarios")
    complete = read_json(root / "truth/completion.json")
    require(complete["state"] == "complete" and complete["workloads_sha256"] ==
            sha256_file(root / "truth/workloads.json"), "Incomplete or changed top100 truth")
    workload = read_json(root / "truth/workloads.json")
    require(workload["topk"] == 100 and workload["corpus_count"] == 10**9 and
            workload["query_count"] == 1000, "Wrong truth corpus/cohort/top-k")
    smoke = read_json(root / "client-smoke/completion.json")
    require(smoke["top10_ids_distances_work_identical"] and smoke["top100_shape"] == [32, 100] and
            smoke["top100_unique_valid_neighbors"] and smoke["replay_checked_by_native_client"],
            "Top-k-aware client lacks native top10/top100 regression acceptance")
    warmup_policy = config.section("SPANN").get("WarmupPolicy", "per_point")
    require(warmup_policy in ("per_point", "once"), "Invalid SPTAG batch warmup policy")
    if warmup_policy == "once":
        require(smoke.get("once_warmup_batch_verified") is True,
                "One-time batch warmup lacks real-index acceptance")
    base, queries, attrs = load_inputs(config)
    require(hashlib_payload(queries) == config.section("Dataset")["QueryPayloadSHA256"],
            "Query prefix differs from the registered top10 cohort")
    require(np.array_equal(queries, np.load(workload["queries"])), "Native query payload differs")
    for path, expected in workload["protected_large"].items():
        require(identity(path) == expected, f"Truth source changed: {path}")
    config.prepared.mkdir()
    write_bin(config.prepared / "query.u8bin", queries, "u1")
    truths = {}
    for scenario in SCENARIOS:
        truth = workload["truth"][scenario]
        definition = config.section("Scenario." + scenario)
        require(truth["candidate_count"] == definition.getint("CandidateCount"), "Candidate count mismatch")
        for field, digest in (("ids", "ids_sha256"), ("distances", "distances_sha256")):
            require(sha256_file(truth[field]) == truth[digest], "Truth payload changed")
        ids, distances = np.load(truth["ids"]), np.load(truth["distances"])
        valid = validate_exact(ids, distances, base, queries, attrs, definition.getint("Tag"), 100)
        require(np.all(valid), "Top100 truth is incomplete")
        require(Path(truth["ids"]) == config.path_value("Scenario." + scenario, "Truth"),
                "Native and evaluator truths differ")
        write_bin(config.prepared / f"gt_{scenario}.u32bin", ids, "<u4")
        write_binding(config.prepared / f"{scenario}.spmat", [definition.getint("Tag")], len(queries), "label")
        truths[scenario] = truth
    prefix = config.path_value("PipeANN", "IndexPrefix")
    for suffix in (".label.0", ".label.0.filter"):
        target = Path(str(prefix) + suffix)
        require(target.is_file(), f"Missing PipeANN sidecar: {target}")
        (config.prepared / ("base" + suffix)).symlink_to(target)

    (root / "config").mkdir()
    (root / "plans").mkdir()
    (root / "logs").mkdir()
    (root / "native/SPTAG").mkdir(parents=True)
    specs = []
    profile_directory = (config.path_value("SPANN", "ProfileDirectory")
                         if "ProfileDirectory" in config.section("SPANN") else config.path.parent)
    for scenario, short in SCENARIOS.items():
        for setting in ("base", "wide"):
            name = f"spann_{short}" + ("_wide" if setting == "wide" else "") + ".ini"
            source = profile_directory / name
            path = root / "config" / name
            shutil.copyfile(source, path)
            native = read_config(path)
            search, inputs = native["searchssdindex"], native["benchmark"]
            require(int(search["resultnum"]) == 100 and int(search["numberofthreads"]) == 1 and
                    inputs["index"] == str(config.path_value("SPANN", "IndexDirectory")) and
                    inputs["queries"] == workload["queries"] and inputs["predicate"] == "categorical" and
                    inputs["predicatefile"] == workload["native_predicates"][scenario]["file"] and
                    inputs["maxqueries"] == inputs["warmup"] == "1000", "SPTAG protocol differs")
            probes = json.loads(native["searchsweep"]["nprobe"])
            require(probes == sorted(set(probes)) and min(probes) >= 100 and len(probes) >= 4,
                    "Each SPTAG profile requires a complete top100 sweep")
            specs.append(dict(scenario=scenario, setting=setting, config=str(path), probes=probes))
    batch = configparser.ConfigParser(interpolation=None)
    batch.optionxform = str
    batch["Batch"] = {"CaseCount": str(2 * len(specs)), "WarmupPolicy": warmup_policy}
    cases = []
    for repeat in (1, 2):
        for spec in specs if repeat == 1 else reversed(specs):
            name = f"r{repeat}_{spec['setting']}_{spec['scenario']}"
            output = root / "native/SPTAG" / name
            case = dict(spec, repeat=repeat, output=str(output), id=f"Case{len(cases) + 1}")
            batch[case["id"]] = {"Config": spec["config"], "OutputDirectory": str(output)}
            cases.append(case)
    with (root / "config/spann_batch.ini").open("x") as stream:
        batch.write(stream, space_around_delimiters=False)
    plans, builds = {}, {}
    for engine, section in (("Filtered_DiskANN", "DiskANN"), ("PipeANN", "PipeANN")):
        controls = config.controls(engine)
        require(controls == sorted(set(controls)) and min(controls) >= 100 and len(controls) >= 4,
                "Each baseline requires a complete native top100 L sweep")
        pairs = [(scenario, i) for scenario in SCENARIOS for i in range(len(controls))]
        plans[engine] = [dict(scenario=scenario, control_index=i, thread_index=0, repeat=repeat)
                         for repeat in (1, 2) for scenario, i in (pairs if repeat == 1 else reversed(pairs))]
        shared.write_plan(config, engine, "single", plans[engine])
        binary = config.path_value(section, "BenchmarkBinary")
        builds[engine] = shared.validate_native_build(config, engine, binary, Path(str(binary) + ".build.json"))

    frozen = {path for path in root.joinpath("config").iterdir()}
    frozen.update(config.path.parent.glob("*.ini"))
    frozen.update(profile_directory.glob("*.ini"))
    frozen.update(root.joinpath("plans").iterdir())
    frozen.update(root.joinpath("inputs").iterdir())
    frozen.update(Path(workload[key]) for key in ("queries",))
    frozen.update(Path(record[field]) for record in truths.values() for field in ("ids", "distances"))
    frozen.update(Path(record["file"]) for record in workload["native_predicates"].values())
    frozen.update((Path(__file__), Path(shared.__file__), HERE / "native_input_io.py",
                   HERE / "sift1b_official_inputs.py", HERE / "official_benchmark_config.py",
                   POSTFILTER / "selectivity_common.py", POSTFILTER / "run_full.py",
                   root / "truth/workloads.json", root / "truth/completion.json",
                   root / "client-smoke/completion.json", HERE / "plot_sift1b_top100.R",
                   POSTFILTER / "Bench.cpp", POSTFILTER / "client_build/CMakeLists.txt",
                   root / "native-client/CMakeCache.txt",
                   root / "native-client/CMakeFiles/nativeBench.dir/link.txt",
                   config.path_value("SPANN", "BenchmarkBinary")))
    for section in ("DiskANN", "PipeANN"):
        binary = config.path_value(section, "BenchmarkBinary")
        frozen.update((binary, Path(str(binary) + ".build.json")))
    frozen.update(build_files(builds))
    indexes = {str(config.path_value("SPANN", "IndexDirectory")):
               inventory(config.path_value("SPANN", "IndexDirectory"))}
    for section in ("DiskANN", "PipeANN"):
        prefix = config.path_value(section, "IndexPrefix")
        frozen.update(path for path in prefix.parent.glob(prefix.name + "*") if path.is_file())
    core = root.parent / "spann_layered_local_v5"
    frozen.update(core.joinpath("normal-bin").glob("*"))
    frozen.add(core / "normal-build/AnnService/CMakeFiles/nativeBench.dir/__/Wrappers/src/CoreInterface.cpp.o")
    records = {str(path): dict(identity=identity(path),
                              sha256=sha256_file(path) if path.stat().st_size < 64 << 20 else None)
               for path in sorted(frozen)}
    save(root / "registration.json", dict(
        topk=100, queries=1000, repeats=2, warmup_policy=warmup_policy, created_utc=shared.utc_now(),
        cases=cases, plans=plans, builds=builds, files=records, indexes=indexes,
        native_core_rebuilt=False, index_rebuilt=False, versions=VERSIONS,
        index_identity_policy="full file identity inventories, native children write-confined",
        input_identity={str(config.path_value("Dataset", key)): identity(config.path_value("Dataset", key))
                        for key in ("Vectors", "Queries", "Attributes")},
        scope="Fresh single-worker top100 curves only; never overwrite paper top10 current.json"))


def hashlib_payload(array):
    import hashlib
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def verify(proof):
    shared.reject_benchmark_environment()
    for path, record in proof["files"].items():
        require(identity(path) == record["identity"], f"Frozen file identity changed: {path}")
        if record["sha256"] is not None:
            require(sha256_file(path) == record["sha256"], f"Frozen contents changed: {path}")
    for path, expected in proof["input_identity"].items():
        require(identity(path) == expected, f"Original dataset changed: {path}")
    for path, expected in proof["indexes"].items():
        require(inventory(path) == expected, f"SPTAG index changed: {path}")


def prepare_resume(config):
    root = config.root
    require((root / "failure.json").is_file() and not (root / "completion.json").exists(),
            "Resume requires a failed, incomplete campaign")
    original_path = root / "registration.json"
    original = read_json(original_path)
    proof = copy.deepcopy(original)
    snapshot_path = root / "recovery/source-snapshots.json"
    snapshots = read_json(snapshot_path) if snapshot_path.exists() else {"sources": {}}
    if snapshot_path.exists():
        require(snapshots["original_registration_sha256"] == sha256_file(original_path),
                "Source snapshots belong to another registration")
    updates = {}
    for source in (Path(__file__).resolve(), POSTFILTER / "selectivity_common.py"):
        recorded = original["files"][str(source)]
        current = dict(identity=identity(source), sha256=sha256_file(source))
        if current == recorded:
            continue
        require(str(source) in snapshots["sources"], f"Missing original launcher snapshot: {source}")
        saved = snapshots["sources"][str(source)]
        path = Path(saved["snapshot"])
        require(identity(path) == saved["snapshot_identity"] and
                sha256_file(path) == saved["sha256"] == recorded["sha256"],
                f"Original launcher snapshot changed: {path}")
        proof["files"][str(source)] = current
        proof["files"][str(path)] = dict(identity=identity(path), sha256=saved["sha256"])
        updates[str(source)] = dict(original_sha256=recorded["sha256"], current_sha256=current["sha256"],
                                    original_snapshot=str(path))
    completed = []
    for engine in ("SPTAG", "Filtered_DiskANN", "PipeANN"):
        if (root / (engine + ".raw.json")).exists():
            completed.append(engine)
            paths = list((root / "native" / engine).rglob("*"))
            paths += [root / (engine + suffix) for suffix in (".raw.json", ".summary.csv", ".execution.json")]
            paths.append(root / "logs" / (engine + ".log"))
            if engine != "SPTAG":
                paths.append(root / (engine + ".captures.json"))
            for path in paths:
                if path.is_file():
                    proof["files"][str(path)] = dict(identity=identity(path), sha256=sha256_file(path))
        else:
            require(not (root / "native" / engine).exists(),
                    f"Cannot resume partial native output; use a fresh baseline campaign: {engine}")
    require(bool(completed), "No completed engine to preserve")
    for path in (original_path, snapshot_path):
        if path.is_file():
            proof["files"][str(path)] = dict(identity=identity(path), sha256=sha256_file(path))
    proof["continuation"] = dict(original_registration_sha256=sha256_file(original_path),
                                completed_engines=completed, launcher_updates=updates,
                                baseline_shared_memory="/dev/shm", resumed_utc=shared.utc_now())
    verify(proof)
    recovery = root / "recovery"
    recovery.mkdir(exist_ok=True)
    save(recovery / "registration.json", proof)
    archive = recovery / "initial"
    archive.mkdir(exist_ok=True)
    paths = [root / "failure.json", root / "status.json"]
    for engine in VERSIONS:
        if engine not in completed:
            paths += [root / "logs" / (engine + ".log"), root / (engine + ".execution.json")]
    for path in paths:
        if path.exists():
            destination = archive / path.name
            require(not destination.exists(), f"Refusing to overwrite failed-attempt evidence: {destination}")
            path.rename(destination)
    return proof


def invoke(config, proof, engine):
    verify(proof)
    root = config.root
    section = {"SPTAG": "SPANN", "Filtered_DiskANN": "DiskANN", "PipeANN": "PipeANN"}[engine]
    binary = config.path_value(section, "BenchmarkBinary")
    args = [str(root / "config/spann_batch.ini")] if engine == "SPTAG" else [str(config.path), "single"]
    command = config.affinity("single") + [sys.executable, "-B", str(Path(__file__).resolve()),
               "native-child", str(root), str(binary), *args]
    write_json(root / "status.json", dict(state="running", engine=engine, started_utc=shared.utc_now()))
    print(f"Starting {engine}: one resident index, all scenarios and repetitions", flush=True)
    with (root / "logs" / (engine + ".log")).open("x") as stream:
        process = subprocess.Popen(command, cwd=root, stdout=stream, stderr=subprocess.STDOUT)
        save(root / (engine + ".execution.json"), dict(pid=process.pid, argv=command, started_utc=shared.utc_now()))
        code = process.wait()
    require(code == 0, f"{engine} native exit {code}; inspect {root / 'logs' / (engine + '.log')}")
    verify(proof)
    return process.pid


def analyze_engine(config, proof, engine, pid):
    if engine == "SPTAG":
        return analyze_spann(config, proof)
    plan = proof["plans"][engine]
    shared.validate_native_completion(config, engine, "single", plan, pid)
    points = shared.parse_results(config, engine, "single", config.root / "logs" / (engine + ".log"), plan)
    captures = shared.validate_captures(config, points)
    path = config.root / (engine + ".captures.json")
    if path.exists():
        require(read_json(path) == captures, f"Saved capture validation differs: {engine}")
    else:
        save(path, captures)
    return [dict(p, topk=100, setting="L_sweep", search_value=p["L"]) for p in points]


def analyze_spann(config, proof):
    base, queries, attrs = load_inputs(config)
    events = [json.loads(line) for line in (config.root / "logs/SPTAG.log").read_text().splitlines()
              if line.startswith("{")]
    require(len(events) >= 3 and events[0]["event"] == "batch_begin" and
            events[0]["cases"] == len(proof["cases"]) and events[1]["event"] == "batch_loaded" and
            events[1]["index_load_count"] == events[1]["query_corpus_load_count"] == 1 and
            events[-1]["event"] == "batch_end" and events[-1]["completed_cases"] == len(proof["cases"]),
            "Incomplete single-load SPTAG batch")
    warmup_policy = proof["warmup_policy"]
    require(events[0]["warmup_policy"] == warmup_policy, "Native batch warmup policy differs")
    expected_total = (1000 if warmup_policy == "once"
                      else 1000 * sum(len(case["probes"]) for case in proof["cases"]))
    require(events[-1]["completed_warmup_queries"] == expected_total, "Incorrect total warmup query count")
    events = events[2:-1]
    if warmup_policy == "once":
        first = proof["cases"][0]
        require(events[0]["event"] == "batch_warmup_begin" and events[0]["queries"] == 1000 and
                events[0]["nprobe"] == first["probes"][0] and events[0]["case_id"] == first["id"] and
                events[0]["config"] == first["config"] and events[1]["event"] == "batch_warmup_end" and
                events[1]["completed_queries"] == 1000, "Incomplete or repeated initial batch warmup")
        events = events[2:]
    points, position = [], 0
    for case in proof["cases"]:
        require(position < len(events), "Missing SPTAG case")
        begin = events[position]
        require(begin["event"] == "case_begin" and begin["case_id"] == case["id"] and
                begin["config"] == case["config"] and begin["output_directory"] == case["output"],
                "SPTAG case identity differs")
        expected_warmup = 0 if warmup_policy == "once" else 1000
        require(begin["warmup_queries"] == expected_warmup, "Unexpected case-level warmup")
        position += 1
        native = read_config(case["config"])["searchssdindex"]
        truth = np.load(config.path_value("Scenario." + case["scenario"], "Truth"))
        for probe in case["probes"]:
            point = events[position]
            position += 1
            require(point["event"] == "point" and point["case_id"] == case["id"] and
                    point["topk"] == 100 and point["nprobe"] == probe and not point["diagnostic"] and
                    point["config"] == case["config"] and point["output_directory"] == case["output"] and
                    point["posting_anchor_limit"] == probe and
                    not point["phase_timing"] and point["value_type"] == "UInt8" and
                    point["queries"] == point["replay_queries"] == 1000 and
                    point["warmup_queries"] == expected_warmup and point["warmup_policy"] == warmup_policy,
                    "Unexpected native SPTAG point protocol")
            for field, key in (("max_check", "maxcheck"), ("posting_anchor_count", "postinganchorcount"),
                               ("posting_additional_max_check", "postingadditionalmaxcheck"),
                               ("posting_navigation_width", "postingnavigationwidth"),
                               ("search_posting_page_limit", "searchpostingpagelimit")):
                require(point[field] == int(native[key]), f"Native SPTAG setting differs: {key}")
            folder = Path(case["output"]) / f"nprobe_{probe}"
            ids = np.fromfile(folder / "ids.i32", dtype="<i4").reshape(1000, 100)
            distances = np.fromfile(folder / "dist.f32", dtype="<f4").reshape(1000, 100)
            definition = config.section("Scenario." + case["scenario"])
            require(definition["Kind"] in ("unfilter", "categorical"), "Unsupported top100 predicate")
            tag = None if definition["Kind"] == "unfilter" else definition.getint("Tag")
            require(tag is not None or definition["Kind"] == "unfilter", "Missing categorical tag")
            valid = validate_exact(ids, distances, base, queries, attrs, tag, 100)
            require(np.all(distances[~valid] == np.finfo(np.float32).max / np.float32(10)),
                    "Invalid SPTAG missing-distance sentinel")
            recall = sum(len(set(row[row >= 0]) & set(correct)) for row, correct in zip(ids, truth)) / ids.size
            latency = np.fromfile(folder / "latency_us.f64", dtype="<f8")
            require(latency.shape == (1000,) and np.all(np.isfinite(latency)) and np.all(latency > 0) and
                    point["qps"] > 0 and math.isclose(point["qps"] * point["mean_ms"], 1000, rel_tol=1e-8) and
                    abs(latency.mean() / 1000 - point["mean_ms"]) < .02, "Invalid SPTAG timing")
            work = np.fromfile(folder / "work.u64", dtype="<u8").reshape(1000, 8)
            require(np.all(work[:, 0] <= probe), "SPTAG fanout exceeds nprobe")
            points.append(dict(point, engine="SPTAG", scenario=case["scenario"], setting=case["setting"],
                               repeat=case["repeat"], search_value=probe, recall=recall,
                               underfilled_queries=int(np.count_nonzero(valid.sum(axis=1) < 100)),
                               payload_hashes={name: sha256_file(folder / name)
                                               for name in ("ids.i32", "dist.f32", "work.u64")}))
        require(events[position]["event"] == "case_end" and events[position]["case_id"] == case["id"],
                "Missing SPTAG case end")
        position += 1
    require(position == len(events), "Unexpected extra SPTAG events")
    for point in points:
        other = [p for p in points if (p["scenario"], p["setting"], p["search_value"]) ==
                 (point["scenario"], point["setting"], point["search_value"])]
        require(len(other) == 2 and other[0]["payload_hashes"] == other[1]["payload_hashes"],
                "SPTAG repetition results/work differ")
    return points


def aggregate(points):
    groups = {}
    for point in points:
        groups.setdefault((point["engine"], point["scenario"], point["setting"], point["search_value"]), []).append(point)
    rows = []
    for (engine, scenario, setting, control), group in groups.items():
        require(len(group) == 2 and {p["repeat"] for p in group} == {1, 2}, "Incomplete repetition group")
        rows.append(dict(engine=engine, version=VERSIONS[engine], scenario=scenario, setting=setting,
                         search_parameter="nprobe" if engine == "SPTAG" else "L", search_value=control,
                         topk=100, queries=1000, threads=1, repeats=2,
                         recall=statistics.median(p["recall"] for p in group),
                         recall_min=min(p["recall"] for p in group), recall_max=max(p["recall"] for p in group),
                         qps=statistics.median(p["qps"] for p in group),
                         qps_min=min(p["qps"] for p in group), qps_max=max(p["qps"] for p in group),
                         underfilled_queries_max=max(p["underfilled_queries"] for p in group)))
    for row in rows:
        peers = [p for p in rows if p["scenario"] == row["scenario"]]
        row["algorithm_frontier"] = not any(dominates(p, row) for p in peers if p["engine"] == row["engine"])
        row["joint_frontier"] = not any(dominates(p, row) for p in peers)
    return rows


def run(config, resume=False):
    root = config.root
    if resume:
        proof = prepare_resume(config)
    else:
        write_json(root / "status.json", dict(state="preparing", controller_pid=os.getpid(),
                                             started_utc=shared.utc_now()))
        prepare(config)
        proof = read_json(root / "registration.json")
    all_points = []
    for engine in ("SPTAG", "Filtered_DiskANN", "PipeANN"):
        raw = root / (engine + ".raw.json")
        if resume and raw.exists():
            write_json(root / "status.json", dict(state="revalidating", engine=engine,
                                                 started_utc=shared.utc_now()))
            pid = read_json(root / (engine + ".execution.json"))["pid"]
            points = analyze_engine(config, proof, engine, pid)
            require(read_json(raw) == points, f"Completed results changed: {engine}")
            print(f"Preserved {engine}: {len(points)} validated measurements; no queries rerun", flush=True)
        else:
            pid = invoke(config, proof, engine)
            points = analyze_engine(config, proof, engine, pid)
            save(raw, points)
            table(root / (engine + ".summary.csv"), aggregate(points))
            print(f"Validated {engine}: {len(points)} fresh top100 measurements", flush=True)
        all_points.extend(points)
    rows = aggregate(all_points)
    table(root / "summary.csv", rows)
    table(root / "pareto_frontiers.csv", [row for row in rows if row["algorithm_frontier"]])
    targets = []
    for scenario in SCENARIOS:
        for target in (.8, .9, .95, .99):
            best = {}
            for engine in VERSIONS:
                eligible = [p for p in rows if p["scenario"] == scenario and p["engine"] == engine and p["recall"] >= target]
                best[engine] = max(eligible, key=lambda p: p["qps"]) if eligible else None
            targets.append(dict(scenario=scenario, recall_target=target, best_observed=best))
    save(root / "analysis.json", dict(topk=100, aggregate_points=len(rows), thresholds=targets,
         sptag_warmup_policy=proof["warmup_policy"],
         no_interpolation=True, no_index_rebuild=True, source_registration_sha256=sha256_file(root / "registration.json"),
         continuation_registration_sha256=(sha256_file(root / "recovery/registration.json") if resume else None),
         caveats=["SPTAG buffered IO; baselines direct IO.",
                  f"SPTAG warmup policy: {proof['warmup_policy']}; baseline clients retain per-point warmup.",
                  "Filtered DiskANN uses its existing categorical R64/buildL1/FilteredL100/PQ32 index.",
                  "SPTAG wide profile uses MaxCheck=8192, extra=8192, width=32 in all three scenarios.",
                  "This top100 experiment does not replace the paper's top10 current.json."]))
    subprocess.run(["Rscript", str(HERE / "plot_sift1b_top100.R"), str(root)], check=True)
    verify(proof)
    artifacts = [root / name for name in ("summary.csv", "pareto_frontiers.csv", "analysis.json")]
    artifacts += sorted(root.glob("*.raw.json")) + sorted(root.joinpath("plots").iterdir())
    save(root / "completion.json", dict(exit_code=0, completed_utc=shared.utc_now(), topk=100,
         sptag_warmup_policy=proof["warmup_policy"],
         aggregate_points=len(rows), ordinary_measurements=len(all_points),
         artifacts={str(p.relative_to(root)): sha256_file(p) for p in artifacts}))
    write_json(root / "status.json", dict(state="complete", topk=100, completed_utc=shared.utc_now()))
    print(f"Completed {len(rows)} top100 operating points: {root}", flush=True)


def main():
    if len(sys.argv) >= 4 and sys.argv[1] == "native-child":
        baseline = len(sys.argv) == 6 and sys.argv[5] in ("single", "throughput")
        confine_outputs(sys.argv[2], allow_shared_memory=baseline)
        os.execv(sys.argv[3], sys.argv[3:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "configs/sift1b_top100_20261004/benchmark.ini")
    parser.add_argument("--truth-pid", type=int)
    parser.add_argument("--resume", action="store_true",
                        help="Preserve and revalidate completed engines after a pre-query startup failure")
    args = parser.parse_args()
    config = shared.Profile(args.config)
    lock = (config.root / "run.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        if args.truth_pid:
            process = shared.process_identity(args.truth_pid)
            write_json(config.root / "status.json", dict(state="waiting_for_top100_truth",
                       controller_pid=os.getpid(), truth_process=process, started_utc=shared.utc_now()))
            while not (config.root / "truth/completion.json").exists():
                require(not (config.root / "truth/failure.json").exists(), "Exact-truth generation failed")
                current = shared.process_identity(args.truth_pid)
                if current != process or current is None:
                    require((config.root / "truth/completion.json").exists(),
                            "Truth process exited or changed before completion")
                    break
                time.sleep(30)
        run(config, resume=args.resume)
    except Exception as error:
        failure = config.root / "failure.json"
        if failure.exists():
            failure = config.root / f"failure-{time.time_ns()}.json"
        save(failure, dict(error=str(error), failed_utc=shared.utc_now()))
        write_json(config.root / "status.json", dict(state="failed", error=str(error),
                   failure_report=str(failure), failed_utc=shared.utc_now()))
        raise
    finally:
        lock.close()


if __name__ == "__main__":
    main()
