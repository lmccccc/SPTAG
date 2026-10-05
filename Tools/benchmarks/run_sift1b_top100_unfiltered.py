"""Append native unfiltered top100 curves to the accepted three-label comparison."""

import argparse
import copy
import fcntl
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import numpy as np

import run_sift1b_top100 as shared
from native_input_io import read_config
from official_benchmark_config import read_json, require, sha256_file, write_json
from sift1b_official_inputs import native_header, write_bin

HERE = Path(__file__).resolve().parent


def frozen(path):
    path = Path(path)
    return dict(identity=shared.identity(path),
                sha256=sha256_file(path) if path.stat().st_size < 64 << 20 else None)


def check_artifacts(root, manifest):
    for name, digest in manifest["artifacts"].items():
        path = Path(root) / name
        require(path.resolve().is_relative_to(Path(root).resolve()) and sha256_file(path) == digest,
                f"Completed artifact changed: {path}")


def read_official(path, dtype, rows, width):
    require(Path(path).stat().st_size == rows * (width + 1) * 4, "Wrong official vecs file size")
    words = np.memmap(path, mode="r", dtype="<i4", shape=(rows, width + 1))
    require(np.all(words[:, 0] == width), "Official vecs row width changed")
    return words[:, 1:].view(dtype)


def check_bvecs_prefix(path, expected):
    count, dimension = expected.shape
    with gzip.open(path, "rb") as stream:
        payload = stream.read(count * (dimension + 4))
    require(len(payload) == count * (dimension + 4), "Truncated original bvecs prefix")
    rows = np.frombuffer(payload, dtype="u1").reshape(count, dimension + 4)
    dimensions = np.ascontiguousarray(rows[:, :4]).view("<i4").ravel()
    require(np.all(dimensions == dimension) and np.array_equal(rows[:, 4:], expected),
            "Native vectors differ from original bvecs order or payload")


def transition_parent(root, parent):
    complete = read_json(parent / "completion.json")
    require(complete["exit_code"] == 0 and complete["aggregate_points"] == 108 and
            complete["combined_measurements"] == 216, "Unexpected parent comparison")
    check_artifacts(parent, complete)
    proof = copy.deepcopy(read_json(parent / "registration.json"))
    transition = read_json(root / "provenance/source-transition.json")
    source = Path(shared.__file__).resolve()
    snapshot = Path(transition["snapshot"]["path"])
    require(transition["source"] == str(source) and
            transition["original"] == proof["files"][str(source)] and
            shared.identity(snapshot) == transition["snapshot"] and
            sha256_file(snapshot) == transition["snapshot_sha256"] == transition["original"]["sha256"],
            "Missing or unauthenticated original shared evaluator")
    proof["files"][str(source)] = frozen(source)
    proof["files"][str(snapshot)] = frozen(snapshot)
    proof["unfiltered_source_transition"] = dict(transition, current_sha256=sha256_file(source))
    shared.verify(proof)
    return proof


def validate_config(config, reference):
    bench = config.section("Benchmark")
    require((bench.getint("TopK"), bench.getint("QueryCount"), bench.getint("WarmupQueries"),
             bench.getint("SingleRepeats")) == (100, 1000, 1000, 2), "Wrong unfiltered top100 protocol")
    require(config.csv("Benchmark", "Scenarios") == ["unfilter"] and
            dict(config.section("Dataset")) == dict(reference.section("Dataset")) and
            dict(config.section("Single")) == dict(reference.section("Single")) and
            dict(config.section("Throughput")) == dict(reference.section("Throughput")),
            "Unfiltered comparison must retain original inputs, workers and affinity")
    scenario = config.section("Scenario.unfilter")
    require(set(scenario) == {"kind", "truth", "candidatecount"} and scenario["Kind"] == "unfilter" and
            scenario.getint("CandidateCount") == config.section("Dataset").getint("VectorCount") == 10**9,
            "Unfiltered scenario must cover the entire original billion-vector corpus")
    for key in ("IndexDirectory", "BenchmarkBinary", "WarmupPolicy"):
        require(config.section("SPANN")[key] == reference.section("SPANN")[key], "Changed SPTAG native binding")
    for engine, section in (("Filtered_DiskANN", "DiskANN"), ("PipeANN", "PipeANN")):
        require({k: v for k, v in config.section(section).items() if k != "lsweep"} ==
                {k: v for k, v in reference.section(section).items() if k != "lsweep"},
                f"Changed baseline index, binary or native settings: {engine}")
        controls = config.controls(engine)
        require(controls == sorted(set(controls)) and min(controls) >= 100 and len(controls) >= 4,
                "Each baseline needs a complete native top100 L sweep")


def prepare_truth(config, source):
    base, queries, _ = shared.load_inputs(config)
    require(shared.hashlib_payload(queries) == config.section("Dataset")["QueryPayloadSHA256"],
            "Original query cohort changed")
    count = int(source["officialquerycount"])
    width = int(source["officialneighbors"])
    require(count == native_header(config.path_value("Dataset", "Queries"), 1)[0] and
            width >= 100, "Official truth must cover the original query set and top100")
    ids = read_official(source["ids"], "<i4", count, width)[:len(queries), :100].astype("<i8")
    distances = np.array(read_official(source["distances"], "<f4", count, width)[:len(queries), :100])
    require(shared.validate_exact(ids, distances, base, queries, None, None, 100).all(),
            "Official top100 truth is incomplete")
    previous = np.load(source["previoustop10ids"], allow_pickle=False)
    require(previous.shape == (1000, 10) and np.array_equal(ids[:, :10], previous),
            "Official truth prefix differs from the accepted original top10 truth")
    check_bvecs_prefix(source["originalqueries"], queries)
    check_bvecs_prefix(source["originalvectors"], base[:len(queries)])
    truth = config.root / "truth"
    truth.mkdir()
    config.prepared.mkdir()
    ids_path = truth / "groundtruth_unfilter_local_ids.npy"
    distance_path = truth / "groundtruth_unfilter_distances.npy"
    require(config.path_value("Scenario.unfilter", "Truth") == ids_path, "Different evaluator/native truth")
    np.save(ids_path, ids, allow_pickle=False)
    np.save(distance_path, distances, allow_pickle=False)
    write_bin(config.prepared / "query.u8bin", queries, "u1")
    write_bin(config.prepared / "gt_unfilter.u32bin", ids, "<u4")
    source_files = [Path(source[key]) for key in
                    ("ids", "distances", "readme", "originalqueries", "originalvectors", "previoustop10ids")]
    shared.save(truth / "completion.json", dict(
        status="complete", topk=100, queries=1000, candidate_count=len(base),
        official_query_count=count, official_neighbors=width, original_zero_based_ids=True,
        all_100000_distances_match_original_squared_l2=True, accepted_top10_ids_identical=True,
        original_query_bvecs_prefix_identical=True, original_base_bvecs_prefix_identical=True,
        query_payload_sha256=shared.hashlib_payload(queries), approximate_truth=False,
        sources={str(p): frozen(p) for p in source_files},
        artifacts={p.name: sha256_file(p) for p in (ids_path, distance_path)}))
    return source_files


def read_cases(path, config, parent_points):
    batch = read_config(path)
    count = int(batch["batch"]["casecount"])
    require(batch["batch"] == {"casecount": str(count), "warmuppolicy": "once"} and
            set(batch) == {"batch"} | {f"case{i}" for i in range(1, count + 1)}, "Invalid once-warmup batch")
    templates = {}
    for point in parent_points:
        if point["engine"] == "SPTAG" and point["scenario"] == "broad_tag":
            templates.setdefault(point["setting"], read_config(point["config"]))
    require(count == 2 * len(templates) and templates, "Missing original SPTAG profile families")
    cases, signatures, outputs = [], {}, set()
    for number in range(1, count + 1):
        item = batch[f"case{number}"]
        require(set(item) == {"config", "outputdirectory"}, "Unexpected native batch fields")
        source, output = Path(item["config"]), Path(item["outputdirectory"])
        require(source.is_absolute() and output.is_absolute() and
                output.parent == config.root / "native/SPTAG" and output not in outputs and
                not output.exists(), "Invalid, duplicate or existing native output")
        outputs.add(output)
        require(output.name.endswith("_unfilter"), "Wrong native scenario output")
        repetition, setting = output.name.removesuffix("_unfilter").split("_", 1)
        require(repetition in ("r1", "r2") and setting in templates, "Unregistered search profile")
        native, template = read_config(source), templates[setting]
        expected = dict(template["benchmark"], predicate="empty", predicatefile="")
        require(set(native) == {"benchmark", "searchssdindex", "searchsweep"} and
                set(native["searchsweep"]) == {"nprobe"} and
                native["benchmark"] == expected and native["searchssdindex"] == template["searchssdindex"],
                "Only the predicate and declared probe sweep may differ from the accepted native profile")
        probes = json.loads(native["searchsweep"]["nprobe"])
        require(all(type(p) is int and p >= 100 for p in probes) and probes == sorted(set(probes)) and
                set(json.loads(template["searchsweep"]["nprobe"])).issubset(probes),
                "Invalid or truncated original nprobe grid")
        require((repetition, setting) not in signatures, "Duplicate profile/repetition")
        signatures[(repetition, setting)] = native
        cases.append(dict(id=f"Case{number}", config=str(source), output=str(output),
                          scenario="unfilter", setting=setting, repeat=int(repetition[1]), probes=probes))
    require(all(signatures.get(("r1", setting)) == signatures.get(("r2", setting)) is not None
                for setting in templates), "Missing matching native profile repetition")
    require([c["setting"] for c in cases if c["repeat"] == 1] ==
            list(reversed([c["setting"] for c in cases if c["repeat"] == 2])),
            "Second repetition must reverse profile order")
    return cases


def prepare(path, experiment, config):
    root = config.root
    require(not (root / "registration.json").exists(), "Campaign is already registered")
    parent = Path(experiment["parentdirectory"])
    reference = shared.shared.Profile(experiment["parentbenchmarkconfig"])
    validate_config(config, reference)
    proof = transition_parent(root, parent)
    original = read_json(parent / "combined.raw.json")
    batch = Path(experiment["nativebatch"])
    cases = read_cases(batch, config, original)
    source_files = prepare_truth(config, read_config(path)["truth"])
    for name in ("config", "logs", "plans", "native/SPTAG"):
        (root / name).mkdir(parents=True)
    copied_batch = root / "config/spann_batch.ini"
    shutil.copyfile(batch, copied_batch)
    plans = {}
    for engine in ("Filtered_DiskANN", "PipeANN"):
        indices = list(range(len(config.controls(engine))))
        plans[engine] = [dict(scenario="unfilter", control_index=i, thread_index=0, repeat=repeat)
                         for repeat in (1, 2) for i in (indices if repeat == 1 else reversed(indices))]
        shared.shared.write_plan(config, engine, "single", plans[engine])
    files = [path, Path(__file__), Path(shared.__file__), config.path, batch, copied_batch,
             Path(experiment["plotscript"]), root / "provenance/source-transition.json",
             parent / "completion.json", parent / "result-audit.json",
             HERE / "tests/test_sift1b_top100_unfiltered.py"]
    files += [Path(case["config"]) for case in cases] + source_files
    files += [parent / name for name in read_json(parent / "completion.json")["artifacts"]]
    files += [p for folder in ("truth", "inputs", "plans", "provenance")
              for p in (root / folder).iterdir() if p.is_file()]
    proof["files"].update({str(p.resolve()): frozen(p) for p in files})
    proof.update(cases=cases, plans=plans, warmup_policy="once", diagnostic=False,
                 created_utc=shared.shared.utc_now(), native_core_rebuilt=False, index_rebuilt=False,
                 unfiltered_extension=dict(
                     parent=str(parent), parent_completion_sha256=sha256_file(parent / "completion.json"),
                     inherited_measurements=216, inherited_points=108, prior_filtering_results_reused=True,
                     query_workers=1, official_truth="original SIFT1B idx_1000M/dis_1000M first1000 x first100",
                     diskann_index="same categorical R64/buildL1/FilteredL100/PQ32 index, native unfiltered mode"))
    shared.verify(proof)
    shared.save(root / "registration.json", proof)
    write_json(root / "status.json", dict(
        state="ready", sptag_measurements=sum(len(c["probes"]) for c in cases),
        baseline_measurements={engine: len(plan) for engine, plan in plans.items()},
        sptag_total_warmup_queries=1000, prepared_utc=shared.shared.utc_now()))


def combine(original, fresh):
    require(all(p["scenario"] != "unfilter" for p in original) and
            all(p["scenario"] == "unfilter" for p in fresh) and
            {p["engine"] for p in fresh} == set(shared.VERSIONS),
            "Extension must append only the three unfiltered engines")
    points = original + fresh
    rows = shared.aggregate(points)
    lookup = {(p["engine"], p["scenario"], p["setting"], p["search_value"]): p for p in points}
    for row in rows:
        point = lookup[(row["engine"], row["scenario"], row["setting"], row["search_value"])]
        for field in ("max_check", "posting_additional_max_check", "posting_navigation_width"):
            row[field] = point[field] if row["engine"] == "SPTAG" else None
    return points, rows


def run(config, experiment):
    root = config.root
    require(not (root / "completion.json").exists(), "Completed campaign must not be overwritten")
    proof = read_json(root / "registration.json")
    shared.verify(proof)
    fresh = []
    for engine in shared.VERSIONS:
        raw, validated = root / (engine + ".raw.json"), root / (engine + ".validated.json")
        if raw.exists():
            check_artifacts(root, read_json(validated))
            pid = read_json(root / (engine + ".execution.json"))["pid"]
            points = shared.analyze_engine(config, proof, engine, pid)
            require(points == read_json(raw), "Completed engine changed during revalidation")
        else:
            directory = root / "native" / engine
            require(not (root / "logs" / (engine + ".log")).exists() and
                    (not directory.exists() or not any(directory.iterdir())),
                    f"Preserve partial native output; explicit recovery is required: {engine}")
            pid = shared.invoke(config, proof, engine)
            points = shared.analyze_engine(config, proof, engine, pid)
            shared.save(raw, points)
            shared.table(root / (engine + ".summary.csv"), shared.aggregate(points))
            artifacts = [p for p in directory.rglob("*") if p.is_file()]
            artifacts += list(root.glob(engine + ".*.json")) + [root / (engine + ".summary.csv"),
                                                              root / "logs" / (engine + ".log")]
            shared.save(validated, dict(artifacts={str(p.relative_to(root)): sha256_file(p) for p in artifacts}))
        fresh.extend(points)
        print(f"Validated {engine}: {len(points)} unfiltered measurements", flush=True)
    parent = Path(experiment["parentdirectory"])
    original = read_json(parent / "combined.raw.json")
    points, rows = combine(original, fresh)
    shared.save(root / "combined.raw.json", points)
    shared.table(root / "summary.csv", rows)
    parent_lines = (parent / "summary.csv").read_text().splitlines()
    require((root / "summary.csv").read_text().splitlines()[:len(parent_lines)] == parent_lines,
            "Previously published filtering rows changed")
    shared.table(root / "pareto_frontiers.csv", [row for row in rows if row["algorithm_frontier"]])
    thresholds = []
    for target in (.8, .85, .9, .93, .95, .97, .99):
        best = {}
        for engine in shared.VERSIONS:
            eligible = [r for r in rows if r["scenario"] == "unfilter" and r["engine"] == engine and
                        r["recall"] >= target]
            best[engine] = max(eligible, key=lambda r: r["qps"]) if eligible else None
        thresholds.append(dict(recall_target=target, best_observed=best))
    coverage = [dict(scenario=scenario, engine=engine,
                     points=sum(r["scenario"] == scenario and r["engine"] == engine for r in rows))
                for scenario in ("unfilter", *shared.SCENARIOS) for engine in shared.VERSIONS]
    shared.save(root / "analysis.json", dict(
        topk=100, aggregate_points=len(rows), fresh_measurements=len(fresh), combined_measurements=len(points),
        coverage=coverage, unfiltered_thresholds=thresholds, previous_filtering_rows_identical=True,
        previous_measurements_identical=points[:len(original)] == original,
        source_registration_sha256=sha256_file(root / "registration.json"), no_interpolation=True,
        sptag_total_warmup_queries=1000,
        maximum_qps_repetition_ratio_by_scenario={
            scenario: max(r["qps_max"] / r["qps_min"] for r in rows if r["scenario"] == scenario)
            for scenario in ("unfilter", *shared.SCENARIOS)},
        caveats=["Unfiltered uses each native engine without a predicate; no auxiliary label-posting search in SPTAG.",
                 "DiskANN retains the categorical R64/buildL1/FilteredL100/PQ32 index, not an unfiltered-tuned rebuild.",
                 "PipeANN retains the matching 1% memory-entry index and mem_L=10.",
                 "SPTAG buffered IO and one warmup; baselines direct IO and per-point warmup.",
                 "Previously completed filtering measurements are reused without normalization or rerunning.",
                 "Some inherited broad high-recall points have 1.24-2.51x repetition QPS spread.",
                 "Same-cohort parameter exploration, not held-out evaluation; every repetition is retained."]))
    subprocess.run(["Rscript", experiment["plotscript"], str(root)], check=True)
    shared.verify(proof)
    check_artifacts(parent, read_json(parent / "completion.json"))
    for engine in shared.VERSIONS:
        check_artifacts(root, read_json(root / (engine + ".validated.json")))
    artifacts = [p for p in root.iterdir() if p.is_file() and p.name not in ("run.lock", "status.json")]
    artifacts += list((root / "plots").iterdir())
    shared.save(root / "completion.json", dict(
        exit_code=0, completed_utc=shared.shared.utc_now(), topk=100, aggregate_points=len(rows),
        combined_measurements=len(points), fresh_measurements=len(fresh),
        previous_filtering_results_unchanged=True,
        artifacts={str(p.relative_to(root)): sha256_file(p) for p in artifacts}))
    write_json(root / "status.json", dict(state="complete", completed_utc=shared.shared.utc_now()))
    print(f"Completed {len(rows)} top100 settings; plots: {root / 'plots'}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    experiment = read_config(args.config)["experiment"]
    config = shared.shared.Profile(experiment["benchmarkconfig"])
    require(config.root.is_dir(), "Create and authenticate the original evaluator snapshot before extension")
    with (config.root / "run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            if not (config.root / "registration.json").exists():
                write_json(config.root / "status.json", dict(
                    state="preparing", controller_pid=os.getpid(), started_utc=shared.shared.utc_now()))
                prepare(args.config.resolve(), experiment, config)
            if args.prepare_only:
                shared.verify(read_json(config.root / "registration.json"))
                print(json.dumps(read_json(config.root / "status.json")), flush=True)
            else:
                run(config, experiment)
        except Exception as error:
            failure = config.root / f"failure-{time.time_ns()}.json"
            shared.save(failure, dict(error=str(error), failed_utc=shared.shared.utc_now()))
            write_json(config.root / "status.json", dict(state="failed", error=str(error), failure=str(failure)))
            raise


if __name__ == "__main__":
    main()
