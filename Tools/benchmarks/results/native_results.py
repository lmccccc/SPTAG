#!/usr/bin/env python3
"""Run registered native batches, validate captures, and publish schema-1 events."""

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys

import numpy as np

BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS))

from native_input_io import open_attributes, open_vectors, read_config
from predicate_groundtruth import matching_rows, native_predicate, parse_predicate
from run_sift1b_top100 import identity, inventory, save, validate_exact
import update_results as store

require = store.require
POSTFILTER = BENCHMARKS / "hierarchical_shortcut_native/native_postfilter"
FORMAT = 1
SEARCH_NAMES = {name.lower(): name for name in (
    "isExecute BuildSsdIndex InternalResultNum ResultNum NumberOfThreads HashTableExponent "
    "MaxCheck MaxDistRatio SearchPostingPageLimit DisableCrossEdges LogPhaseTime LogPathStats "
    "DumpHeads EnableHybridDistance EnablePostingNavigation GlobalPostingSearch PostingAnchorCount "
    "PostingAdditionalMaxCheck PostingNavigationWidth PostingMatchRatePercent PostingMatchWindow").split()}


def absolute(value):
    path = Path(value)
    require(path.is_absolute(), f"An absolute path is required: {value}")
    return path.resolve()


def read_json(path):
    return json.loads(Path(path).read_text())


def frozen(path):
    path = absolute(str(path))
    result = {"identity": identity(path)}
    # Large immutable data/index files use the same before/after identity guard
    # as truth preparation, not a repeated billion-vector content scan.
    if path.stat().st_size < 64 << 20:
        result["sha256"] = store.file_sha(path)
    return result


def verify_files(files):
    for path, expected in files.items():
        require(frozen(path) == expected, f"Frozen artifact changed: {path}")


def load_plan(path):
    path = absolute(str(path))
    config = read_config(path)
    policy = config["experiment"]
    keys = {"directory", "batch", "binary", "workloads", "dataset", "indexversion",
            "clientversion", "versionorder", "study", "variant", "repeats",
            "timeoutseconds", "cpunodes", "memorynodes", "minimumqps"}
    require(set(policy) == keys, f"Experiment keys must be exactly {sorted(keys)}")
    for key in keys:
        require(bool(policy[key]), f"Missing Experiment.{key}")
    root, batch, binary, workloads = (absolute(policy[k]) for k in
                                     ("directory", "batch", "binary", "workloads"))
    registration = read_config(batch)
    require(set(registration["batch"]) == {"casecount", "warmuppolicy"},
            "Batch must explicitly declare CaseCount and WarmupPolicy")
    count = int(registration["batch"]["casecount"])
    require(1 <= count <= 10000, "Invalid batch case count")
    require(registration["batch"]["warmuppolicy"] in ("once", "per_point", "once_per_predicate",
                                                    "once_per_mode_predicate"),
            "Invalid warmup policy")
    names = [f"case{i}" for i in range(1, count + 1)]
    require(set(registration) == {"batch", *names}, "Unexpected/missing batch sections")
    require(set(config) == {"experiment", *names}, "Every native case needs experiment metadata")
    repeats = int(policy["repeats"])
    require(repeats > 0 and int(policy["versionorder"]) >= 0 and int(policy["timeoutseconds"]) > 0,
            "Invalid repeat count, version order or timeout")
    store.finite(float(policy["minimumqps"]), "presentation QPS floor", positive=True)
    for key in ("cpunodes", "memorynodes"):
        require(all(s.isdecimal() for s in policy[key].split(",")), f"Invalid {key}")
    cases, seen, indexes = [], set(), set()
    for name in names:
        record, metadata = registration[name], config[name]
        require(set(record) == {"config", "outputdirectory"}, "Invalid native case registration")
        require(set(metadata) == {"scenario", "repetition"}, "Invalid case metadata")
        ini, output = absolute(record["config"]), absolute(record["outputdirectory"])
        require(root in output.parents, "Case output must be strictly inside the experiment directory")
        require(all(output not in c["output"].parents and c["output"] not in output.parents
                    and output != c["output"] for c in cases), "Overlapping case outputs")
        profile = read_config(ini)
        bench, search = profile["benchmark"], profile["searchssdindex"]
        require(search.get("globalpostingsearch", "false") in ("true", "false"),
                "GlobalPostingSearch must be true or false")
        require(search.get("globalpostingsearch", "false") != "true" or
                search["enablepostingnavigation"] == "false",
                "GlobalPostingSearch requires EnablePostingNavigation=false")
        require(bench["valuetype"] == "UInt8", "Portable capture validator currently requires UInt8")
        require(bench.get("phasetiming", "false") == "false" and bench.get("steptiming", "false") == "false"
                and search["logphasetime"] == "false"
                and search["logpathstats"] == "false" and search["dumpheads"] == "0",
                "Diagnostics are not ordinary performance measurements")
        require(search["numberofthreads"] == "1" and search["buildssdindex"] == "false"
                and search["isexecute"] == "true", "Only immutable single-worker search is supported")
        require(bench["maxqueries"] == bench["warmup"], "Warmup and measured cohorts must agree")
        index = absolute(bench["index"])
        indexes.add(index)
        require(root != index and root not in index.parents and index not in root.parents,
                "Experiment output overlaps the immutable index")
        probes = [int(p.strip()) for p in profile.get("searchsweep", {}).get(
            "nprobe", search["internalresultnum"]).split(",")]
        topk = int(search["resultnum"])
        require(topk > 0 and probes and len(set(probes)) == len(probes)
                and all(p >= topk for p in probes), "Invalid native probe sweep/top-k")
        repetition = int(metadata["repetition"])
        require(1 <= repetition <= repeats, "Invalid repetition identity")
        key = (metadata["scenario"], str(ini), repetition)
        require(key not in seen, "Duplicate scenario/configuration/repetition")
        seen.add(key)
        cases.append(dict(name=name, case_id="Case" + name[4:], config=ini, output=output,
                          profile=profile, scenario=metadata["scenario"], repetition=repetition,
                          probes=probes, topk=topk))
    require(len(indexes) == 1, "One experiment must use one physical index")
    for case in cases:
        group = [c for c in cases if (c["scenario"], c["config"]) == (case["scenario"], case["config"])]
        require({c["repetition"] for c in group} == set(range(1, repeats + 1)),
                "Every configuration must have all declared repetitions")
    return dict(path=path, policy=policy, root=root, batch=batch, binary=binary,
                workloads=workloads, cases=cases, index=indexes.pop(),
                warmup_policy=registration["batch"]["warmuppolicy"])


def load_inputs(plan):
    path = plan["workloads"]
    complete = read_json(path.parent / "completion.json")
    require(complete["state"] == "complete" and complete["workloads_sha256"] == store.file_sha(path),
            "Groundtruth is incomplete or its manifest changed")
    workload = read_json(path)
    require(workload["schema_version"] == 2 and workload["value_type"] == "UInt8"
            and workload["dimension"] == 128 and workload["metric"] == "squared L2",
            "Use the native UInt8/L2/128-dimensional schema-2 truth generator")
    require(workload["dataset"] == plan["policy"]["dataset"], "Dataset metadata mismatch")
    count, queries, topk = (workload[k] for k in ("corpus_count", "query_count", "topk"))
    require(all(type(n) is int and n > 0 for n in (count, queries, topk)), "Invalid truth dimensions")
    for name in ("base_file", "query_file", "attribute_source"):
        source = workload[name]
        require(identity(source) == workload["protected_large"][source],
                f"Truth source changed: {source}")
    base = open_vectors(workload["base_file"], "UInt8", 128, count).data
    original_queries = open_vectors(workload["query_file"], "UInt8", 128).data[:queries]
    attrs = open_attributes(workload["attribute_source"], count, len(workload["column_types"]),
                            workload["source_vector_count"])
    required = {str(path), str(path.parent / "completion.json"), workload["base_file"],
                workload["query_file"], workload["attribute_source"], workload["queries"]}
    require(store.file_sha(workload["queries"]) == workload["protected"][workload["queries"]],
            "Native query array changed")
    xq = np.load(workload["queries"], mmap_mode="r", allow_pickle=False)
    require(xq.dtype == np.dtype("u1") and xq.shape == (queries, 128) and xq.flags.c_contiguous
            and np.array_equal(xq, original_queries), "Native queries differ from original source cohort")
    truths, predicates = {}, {}
    for case in plan["cases"]:
        scenario, bench = case["scenario"], case["profile"]["benchmark"]
        require(scenario in workload["scenarios"] and case["topk"] == topk, "Scenario/top-k mismatch")
        require(absolute(bench["queries"]) == absolute(workload["queries"]), "Query path mismatch")
        require(bench["maxqueries"] == "all" or int(bench["maxqueries"]) == queries, "Query count mismatch")
        expression, clauses = parse_predicate(json.dumps(workload["predicates"][scenario]),
                                             tuple(workload["column_types"]))
        kind, expected_payload = native_predicate(clauses, tuple(workload["column_types"]), queries)
        declared = workload["native_predicates"][scenario]
        require(bench["predicate"] == declared["kind"] == kind
                and bench.get("predicatefile", "") == declared["file"], "Native predicate binding mismatch")
        if kind != "empty":
            filename = absolute(declared["file"])
            require(store.file_sha(filename) == workload["protected"][str(filename)], "Predicate file changed")
            actual = np.load(filename, allow_pickle=False)
            require(actual.dtype == np.dtype("<u4") and np.array_equal(actual, expected_payload),
                    "Predicate payload does not encode the declared expression")
            required.add(str(filename))
        if scenario in truths:
            continue
        truth = workload["truth"][scenario]
        for key in ("ids", "distances"):
            filename = truth[key]
            require(store.file_sha(filename) == truth[key + "_sha256"], "Groundtruth capture changed")
            required.add(filename)
        ids = np.load(truth["ids"], mmap_mode="r", allow_pickle=False)
        distances = np.load(truth["distances"], mmap_mode="r", allow_pickle=False)
        require(np.issubdtype(ids.dtype, np.signedinteger), "Groundtruth IDs must be signed integers")
        valid = validate_exact(ids, distances, base, xq, attrs, None, topk)
        require(np.all(matching_rows(clauses, attrs[ids[valid]])), "Groundtruth predicate mismatch")
        eligible = truth["candidate_count"]
        require(type(eligible) is int and 0 <= eligible <= count
                and np.all(valid.sum(axis=1) == min(topk, eligible)), "Incomplete exhaustive truth")
        metadata = workload["scenario_metadata"][scenario]
        require(metadata["eligible_count"] == eligible and metadata["corpus_count"] == count
                and metadata["selectivity"] == truth["selectivity"] == eligible / count,
                "Inconsistent selectivity metadata")
        truths[scenario], predicates[scenario] = ids, clauses
    return dict(workload=workload, base=base, queries=xq, attrs=attrs, truths=truths,
                predicates=predicates, required=required)


def prepare(plan, inputs):
    root = plan["root"]
    require(not os.path.lexists(root), "Experiment directory must be fresh")
    files = {plan["path"], plan["batch"], plan["binary"],
             *(case["config"] for case in plan["cases"]), *map(Path, inputs["required"])}
    files.update(Path(module.__file__).resolve() for module in tuple(sys.modules.values())
                 if getattr(module, "__file__", None)
                 and BENCHMARKS in Path(module.__file__).resolve().parents)
    linked = subprocess.run(["ldd", str(plan["binary"])], check=True, capture_output=True, text=True)
    require("not found" not in linked.stdout, "Unresolved native shared library")
    for line in linked.stdout.splitlines():
        fields = line.split()
        candidates = [field for field in fields if field.startswith("/")]
        files.update(Path(field).resolve() for field in candidates)
    require(all(root != path and root not in path.parents for path in files),
            "Experiment output overlaps a protected input")
    frozen_files = {str(path): frozen(path) for path in sorted(files)}
    index = inventory(plan["index"])
    require(index, "Empty immutable index inventory")
    index_small = {str(plan["index"] / relative): frozen(plan["index"] / relative)
                   for relative, value in index.items() if value["size"] < 64 << 20}
    root.mkdir(parents=True, exist_ok=False)
    command = ["numactl", "--cpunodebind=" + plan["policy"]["cpunodes"],
               "--membind=" + plan["policy"]["memorynodes"], sys.executable, "-B",
               str(POSTFILTER / "selectivity_common.py"), str(root),
               str(plan["binary"]), str(plan["batch"])]
    proof = dict(schema_version=FORMAT, files=frozen_files, index=index, index_small=index_small,
                 command=command, binary_sha256=store.file_sha(plan["binary"]),
                 started_at=datetime.now(timezone.utc).isoformat(),
                 large_file_guard="device/inode/size/mtime_ns/ctime_ns, not a content digest")
    save(root / "registration.json", proof)
    return proof


def verify(plan, proof):
    verify_files(proof["files"])
    verify_files(proof["index_small"])
    require(inventory(plan["index"]) == proof["index"], "Immutable index inventory changed")


def payload(path, dtype, shape):
    require(path.stat().st_size == math.prod(shape) * np.dtype(dtype).itemsize,
            f"Wrong capture extent: {path}")
    return np.fromfile(path, dtype=dtype).reshape(shape)


def validate_log(plan, inputs):
    events = []
    with (plan["root"] / "native.log").open() as stream:
        for line in stream:
            if line.lstrip().startswith("{"):
                event = json.loads(line)
                require("event" in event, "A load-once native batch log is required")
                events.append(event)
    position = 0

    def take(kind, expected=None):
        nonlocal position
        require(position < len(events) and events[position]["event"] == kind,
                f"Missing/out-of-order native lifecycle event: {kind}")
        event = events[position]
        position += 1
        for key, value in (expected or {}).items():
            require(event.get(key) == value, f"Native {kind}.{key} mismatch")
        return event

    count, topk = (inputs["workload"][k] for k in ("query_count", "topk"))
    take("batch_begin", dict(cases=len(plan["cases"]), warmup_policy=plan["warmup_policy"],
                            phase_timing=False, value_type="UInt8", index=str(plan["index"]),
                            queries=inputs["workload"]["queries"]))
    take("batch_loaded", dict(index_load_count=1, query_corpus_load_count=1))
    if plan["warmup_policy"] == "once":
        take("batch_warmup_begin", dict(queries=count, nprobe=plan["cases"][0]["probes"][0]))
        take("batch_warmup_end", dict(completed_queries=count))
    rows, warmed = [], set()
    per_point = plan["warmup_policy"] == "per_point"
    for case in plan["cases"]:
        binding = dict(case_id=case["case_id"], config=str(case["config"]), output_directory=str(case["output"]))
        search, bench = case["profile"]["searchssdindex"], case["profile"]["benchmark"]
        mode = ("global" if search.get("globalpostingsearch", "false") == "true" else
                "posting" if search["enablepostingnavigation"] == "true" else "graph")
        warmup_group = bench["predicate"] + "\n" + bench.get("predicatefile", "")
        if plan["warmup_policy"] == "once_per_mode_predicate":
            warmup_group = mode + "\n" + warmup_group
        if plan["warmup_policy"] in ("once_per_predicate", "once_per_mode_predicate") and warmup_group not in warmed:
            take("scenario_warmup_begin", dict(**binding, queries=count, warmup_group=warmup_group))
            take("scenario_warmup_end", dict(completed_queries=count))
            warmed.add(warmup_group)
        take("case_begin", dict(**binding, predicate=bench["predicate"],
                               predicate_file=bench.get("predicatefile", ""), phase_timing=False,
                               measured_queries=count, replay_queries=count,
                               warmup_queries=count if per_point else 0,
                               search_settings=search))
        for probe in case["probes"]:
            point = take("point", dict(**binding, nprobe=probe, queries=count, topk=topk,
                                      value_type="UInt8", diagnostic=False, phase_timing=False,
                                      measured_queries=count, replay_queries=count,
                                      warmup_policy=plan["warmup_policy"],
                                      warmup_queries=count if per_point else 0))
            for field, key in (("max_check", "maxcheck"), ("posting_anchor_count", "postinganchorcount"),
                               ("posting_additional_max_check", "postingadditionalmaxcheck"),
                               ("posting_navigation_width", "postingnavigationwidth"),
                               ("posting_match_window", "postingmatchwindow"),
                               ("search_posting_page_limit", "searchpostingpagelimit"),
                               ("posting_match_rate_percent", "postingmatchratepercent")):
                require(key in search and point[field] == float(search[key]),
                        f"Effective native control differs from explicit INI: {field}")
            global_posting = search.get("globalpostingsearch", "false") == "true"
            require(point["mode"] == mode and
                    point.get("global_posting_search", False) == global_posting,
                    "Native navigation mode mismatch")
            for key in ("qps", "mean_ms", "p50_us", "p95_us", "p99_us"):
                store.finite(point[key], key, positive=True)
            require(math.isclose(point["qps"] * point["mean_ms"], 1000, rel_tol=1e-8),
                    "Native timing/QPS mismatch")
            directory = case["output"] / f"nprobe_{probe}"
            ids = payload(directory / "ids.i32", "<i4", (count, topk))
            distances = payload(directory / "dist.f32", "<f4", (count, topk))
            payload(directory / "work.u64", "<u8", (count, 8))
            latency = payload(directory / "latency_us.f64", "<f8", (count,))
            require(np.all(np.isfinite(latency)) and np.all(latency >= 0), "Invalid latency capture")
            valid = validate_exact(ids, distances, inputs["base"], inputs["queries"], inputs["attrs"], None, topk)
            require(np.all(matching_rows(inputs["predicates"][case["scenario"]], inputs["attrs"][ids[valid]])),
                    "Returned ID violates the exact predicate")
            truth = inputs["truths"][case["scenario"]]
            recall = sum(len(set(row[row >= 0]) & set(exact[exact >= 0]))
                         for row, exact in zip(ids, truth)) / (count * topk)
            rows.append(dict(**point, scenario=case["scenario"], repetition=case["repetition"], recall=recall,
                             underfilled_queries=int(np.count_nonzero(valid.sum(axis=1) < topk)),
                             capture_sha256={p.name: store.file_sha(p) for p in
                                             (directory / name for name in
                                              ("ids.i32", "dist.f32", "work.u64", "latency_us.f64"))}))
        take("case_end", dict(**binding, completed_points=len(case["probes"])))
    warmups = (count * len(warmed) if plan["warmup_policy"] in ("once_per_predicate", "once_per_mode_predicate") else
               count if plan["warmup_policy"] == "once" else count * sum(len(c["probes"]) for c in plan["cases"]))
    take("batch_end", dict(completed_cases=len(plan["cases"]), completed_warmup_queries=warmups))
    require(position == len(events), "Unexpected native lifecycle events after batch completion")
    return rows


def normalize(plan, inputs, rows, completed_at):
    policy, workload = plan["policy"], inputs["workload"]
    proof = read_json(plan["root"] / "registration.json")
    groups = store.defaultdict_list()
    for row in rows:
        groups[(row["scenario"], row["config"], row["nprobe"])].append(row)
    events = []
    for (scenario, config, probe), group in sorted(groups.items()):
        group.sort(key=lambda row: row["repetition"])
        require([r["repetition"] for r in group] == list(range(1, int(policy["repeats"]) + 1)),
                "Incomplete or duplicate repetition group")
        for filename in ("ids.i32", "dist.f32", "work.u64"):
            require(len({r["capture_sha256"][filename] for r in group}) == 1,
                    f"Repetitions disagree: {filename}")
        case = next(c for c in plan["cases"] if str(c["config"]) == config)
        parameters = {SEARCH_NAMES[key]: value for key, value in case["profile"]["searchssdindex"].items()
                      if key not in ("internalresultnum", "isexecute", "buildssdindex",
                                     "logphasetime", "logpathstats", "dumpheads")}
        parameters["WarmupPolicy"] = plan["warmup_policy"]
        version = dict(id=policy["indexversion"] + "/" + policy["clientversion"],
                       order=int(policy["versionorder"]), index=policy["indexversion"],
                       client=policy["clientversion"], topk=workload["topk"],
                       index_identity=store.digest(proof["index"]), client_sha256=proof["binary_sha256"])
        measurements = [dict(r, repeat=r["repetition"], threads=1, cohort_queries=workload["query_count"],
                             search_value=probe, client_version=version["client"]) for r in group]
        recalls, rates = [r["recall"] for r in group], [r["qps"] for r in group]
        metadata = workload["scenario_metadata"][scenario]
        point = dict(scenario=scenario, predicate=json.dumps(workload["predicates"][scenario], sort_keys=True),
                     selectivity=metadata["selectivity"], candidate_count=metadata["eligible_count"],
                     phase="single", threads=1, topk=workload["topk"], cohort_queries=workload["query_count"],
                     search_parameter="nprobe", search_value=probe,
                     parameters=json.dumps(parameters, sort_keys=True, separators=(",", ":")),
                     curve_id=scenario, setting="native", client_version=version["client"],
                     index_version=version["index"], recall=statistics.median(recalls),
                     recall_min_observed=min(recalls), recall_max_repeat=max(recalls),
                     qps=statistics.median(rates), qps_min=min(rates), qps_max=max(rates),
                     repeats=len(group), expected_repeats=int(policy["repeats"]), complete_repeats_same_client=True)
        event = dict(schema_version=store.SCHEMA, kind="measurement", study=policy["study"],
                     variant=policy["variant"], dataset=policy["dataset"], algorithm="SPTAG",
                     version=version, completed_at=completed_at, point=point, measurements=measurements,
                     source=dict(description="Ordinary native batch; exact captures and full repetitions validated",
                                 path=str(plan["root"] / "single_raw.json"),
                                 registration_sha256=store.file_sha(plan["root"] / "registration.json"),
                                 native_log_sha256=store.file_sha(plan["root"] / "native.log"),
                                 experiment_ini=str(plan["path"]), experiment_sha256=store.file_sha(plan["path"])))
        store.validate_event(event)
        events.append(event)
    require(events, "No complete native measurements")
    return events


def initialize(config, directory):
    plan = load_plan(config)
    inputs = load_inputs(plan)
    require(not directory.exists(), "A new registry directory is required; do not reinitialize existing history")
    policy, workload = plan["policy"], inputs["workload"]
    catalog = dict(datasets=[policy["dataset"]], studies=[dict(id=policy["study"])],
                   selected_indexes=[dict(dataset=policy["dataset"], algorithm="SPTAG", index=policy["indexversion"])],
                   expected_cells=[dict(study=policy["study"], variant=policy["variant"], dataset=policy["dataset"],
                       algorithm="SPTAG", scenario=scenario, phase="single", threads=1)
                       for scenario in sorted({case["scenario"] for case in plan["cases"]})],
                   experiment_views=dict(main_selection="observed-pareto-envelope", main_studies=[policy["study"]],
                       main_protocol=dict(topk=workload["topk"], cohort_queries=workload["query_count"],
                                          phase="single", threads=1), minimum_qps=float(policy["minimumqps"]),
                       disclosures=["Observed points only; no interpolation.",
                                    "SPTAG is restricted to the explicitly selected physical index.",
                                    "Cross-version selection can combine query clients, not selected SPTAG indexes.",
                                    "QPS medians exclude loading, warmup, replay and capture validation."]))
    event = dict(schema_version=store.SCHEMA, kind="catalog", revision=1, catalog=catalog,
                 completed_at=datetime.now(timezone.utc).isoformat(),
                 source=dict(description="Explicit native experiment registration; no measured results yet",
                             path=str(plan["path"]), sha256=store.file_sha(plan["path"])))
    return store.update(directory, [event])


def run(config):
    plan = load_plan(config)
    inputs = load_inputs(plan)
    proof = prepare(plan, inputs)
    root = plan["root"]
    try:
        verify(plan, proof)
        with (root / "native.log").open("x") as log:
            result = subprocess.run(proof["command"], stdout=log, stderr=subprocess.STDOUT,
                                    timeout=int(plan["policy"]["timeoutseconds"]), check=False)
        save(root / "execution.json", dict(command=proof["command"], exit_code=result.returncode))
        require(result.returncode == 0, f"Native batch failed: exit {result.returncode}; see {root / 'native.log'}")
        verify(plan, proof)
        rows = validate_log(plan, inputs)
        completed_at = datetime.now(timezone.utc).isoformat()
        save(root / "single_raw.json", rows)
        events = normalize(plan, inputs, rows, completed_at)
        save(root / "events.json", events)
        save(root / "measurement-analysis.json",
             dict(schema_version=FORMAT, completed_utc=completed_at,
                  exact_predicates_distances_dedup=True, repetitions_byte_identical=True,
                  replay_checked_by_native_client=True, points=len(events), measurements=len(rows)))
        artifacts = {str(p.relative_to(root)): store.file_sha(p) for p in sorted(root.rglob("*")) if p.is_file()}
        save(root / "completion.json", dict(schema_version=FORMAT, state="complete", exit_code=0,
                                           completed_at=completed_at, artifacts=artifacts))
        print(json.dumps(dict(state="complete", directory=str(root), points=len(events))))
    except Exception as error:
        save(root / "failure.json", dict(state="failed", error=str(error),
                                        failed_at=datetime.now(timezone.utc).isoformat()))
        raise


def accepted_events(config):
    plan = load_plan(config)
    root = plan["root"]
    require(not (root / "failure.json").exists(), "A failed experiment cannot be published")
    complete = read_json(root / "completion.json")
    require(complete["schema_version"] == FORMAT and complete["state"] == "complete"
            and complete["exit_code"] == 0, "Experiment did not complete")
    for relative, expected in complete["artifacts"].items():
        path = (root / relative).resolve()
        require(root in path.parents and store.file_sha(path) == expected, f"Completed artifact changed: {relative}")
    require({"events.json", "registration.json", "single_raw.json", "native.log", "execution.json",
             "measurement-analysis.json"} <= set(complete["artifacts"]), "Incomplete completion seal")
    require(read_json(root / "execution.json")["exit_code"] == 0, "Native execution failed")
    proof = read_json(root / "registration.json")
    verify(plan, proof)
    inputs = load_inputs(plan)
    rows = validate_log(plan, inputs)
    require(rows == read_json(root / "single_raw.json"), "Raw measurements no longer reproduce")
    events = normalize(plan, inputs, rows, complete["completed_at"])
    require(events == read_json(root / "events.json"), "Normalized measurements no longer reproduce")
    return events


def publish(config, directory):
    current = store.update(directory, accepted_events(config))
    print(json.dumps(dict(current=str(directory / "current.json"), events=current["history"]["records"],
                          points=len(current["points"]))))
    return current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check-config", "init", "run", "publish"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    if args.action == "run":
        run(args.config)
    elif args.action in ("init", "publish"):
        require(args.data_dir is not None, "Registry operations require --data-dir")
        if args.action == "init":
            initialize(args.config, args.data_dir.resolve())
            print(json.dumps(dict(state="registered", directory=str(args.data_dir.resolve()))))
        else:
            publish(args.config, args.data_dir.resolve())
    else:
        plan = load_plan(args.config)
        load_inputs(plan)
        print(json.dumps(dict(state="validated", cases=len(plan["cases"]), index=str(plan["index"]))))


if __name__ == "__main__":
    main()
