#!/usr/bin/env python3
"""Prepare/authorize/run bounded same-index policy controls; no native work at prepare."""
import argparse
import configparser
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time

import numpy as np

from run_full import PAYLOADS, quality, read_ini, require, sha
from run_postgraph import COUNTERS, save, validate_navigation
from run_selectivity import native_identity, terminate_owned
from selectivity_common import SelectedPredicate, identity, inventory, validate_identities

HERE = Path(__file__).resolve().parent
REGISTRATION = "registration.v2.json"
SOURCE_DIRECTORY = "source_v2"
SCHEMA_VERSION = 6
POLICIES = {"before": "predicate_first_legacy_posting_order",
            "after": "predicate_first_global_distance_frontier"}
GRID = [96, 384]
VARIANTS = ("graph", "postgraph_extra")
RUNTIMES = ("before", "after")
KINDS = ("normal", "diagnostic")
SCENARIOS = {
    "SIFT1M": ["unfilter", "broad_tag", "medium_tag", "extreme_tag", "numeric", "mixed_dnf"],
    "SIFT1B": ["unfilter", "sel_01pct", "mixed_dnf"],
}
GRAPH_FIELDS = ("graph_rows", "tree_visits", "graph_unique_h1", "graph_may_matches",
                "graph_checked_leaves", "graph_distances", "head_before", "head_target")
HELPERS = ("run_frontier_repair.py", "run_full.py", "run_postgraph.py",
           "run_selectivity.py", "selectivity_common.py", "test_frontier_repair.py")


def write_ini(path, config):
    with Path(path).open("x") as stream:
        config.write(stream, space_around_delimiters=False)


def bounded_hash(path):
    require(Path(path).stat().st_size <= 128 << 20, f"Refusing large content hash: {path}")
    return sha(path)


def input_predicate(workload, scenario):
    if "native_predicates" in workload:
        item = workload["native_predicates"][scenario]
        return item["kind"], item["file"]
    if scenario in workload["flat_query_tags"]:
        return "categorical", workload["flat_query_tags"][scenario]
    if scenario in ("numeric", "mixed_dnf"):
        return "dnf", workload["query_dnf"]["numeric" if scenario == "numeric" else "mixed"]
    return "empty", ""


def prepare(path):
    config = read_ini(path)
    plan = config["Validation"]
    dataset = plan["Dataset"]
    scenarios = [s.strip() for s in plan["Scenarios"].split(",")]
    require(dataset in SCENARIOS and scenarios == SCENARIOS[dataset], "Unexpected scenario scope")
    require(json.loads(config["SearchSweep"]["NProbe"]) == GRID, "Expected nprobe96,384")
    output = Path(plan["OutputDirectory"])
    require(output.is_absolute() and output.name == "main_posting_frontier_20260924" and
            not output.exists(), "Require a fresh main_posting_frontier_20260924 output")
    historical = Path(plan["HistoricalCampaign"]) / "registration.json"
    previous = json.loads(historical.read_text())
    workload_path = Path(plan["Workloads"])
    workload = json.loads(workload_path.read_text())
    require(previous["protected"].get(str(workload_path)) == bounded_hash(workload_path),
            "Workload is not authenticated by the historical campaign")
    value_type = "Float" if dataset == "SIFT1M" else "UInt8"
    protected = {str(path): bounded_hash(path), str(historical): bounded_hash(historical),
                 str(workload_path): bounded_hash(workload_path)}
    large = {}
    if dataset == "SIFT1B":
        validate_identities(workload["protected_large"])
        large.update(workload["protected_large"])
        for name, digest in workload["protected"].items():
            require(bounded_hash(name) == digest, f"Authenticated input changed: {name}")
            protected[name] = digest
    cases, source_configs = [], []
    for scenario in scenarios:
        truth = workload["truth"][scenario]
        require(bounded_hash(truth["ids"]) == truth["sha256"], f"Truth changed: {scenario}")
        truth_ids = np.load(truth["ids"], mmap_mode="r", allow_pickle=False)
        require(truth_ids.ndim == 2 and truth_ids.shape[0] >= 1000 and truth_ids.shape[1] == 10,
                "Missing complete first1000 top10 truth")
        protected[truth["ids"]] = truth["sha256"]
        if "distances" in truth:
            digest = truth.get("distances_sha256", truth.get("distance_sha256"))
            require(digest == bounded_hash(truth["distances"]), "Truth distance changed")
            protected[truth["distances"]] = digest
        for variant in VARIANTS:
            selected = [c for c in previous["schedule"] if c["kind"] == "plain" and
                        c["repetition"] == 1 and c["scenario"] == scenario and c["variant"] == variant]
            require(len(selected) == 1, "Missing unique historical generated INI")
            original = Path(selected[0]["config"])
            require(previous["protected"].get(str(original)) == bounded_hash(original),
                    "Historical generated INI changed")
            protected[str(original)] = bounded_hash(original)
            native = read_ini(original)
            b, s = native["Benchmark"], native["SearchSSDIndex"]
            require(b["MaxQueries"] == b["Warmup"] == "1000", "Historical cohort window differs")
            require(b.get("ValueType", "Float") == value_type, "Historical value type mismatch")
            predicate, predicate_file = input_predicate(workload, scenario)
            require((b["Predicate"], b.get("PredicateFile", "")) == (predicate, predicate_file),
                    "Predicate differs from authenticated workload")
            require(s["MaxCheck"] == "2048" and s["PostingAnchorCount"] == "8" and
                    s["EnablePostingNavigation"] == str(variant != "graph").lower() and
                    s["PostingAdditionalMaxCheck"] == ("0" if variant == "graph" else "2048") and
                    s["NumberOfThreads"] == "1" and s["ResultNum"] == "10" and
                    s["LogPhaseTime"] == s["LogPathStats"] == s["EnableHybridDistance"] == "false" and
                    s["DumpHeads"] == "0" and b.get("PhaseTiming", "false") == "false",
                    "Historical search protocol differs")
            native["SearchSweep"]["NProbe"] = json.dumps(GRID)
            b["ValueType"] = value_type
            source_configs.append(native)
            cases.append(dict(case=f"{scenario}_{variant}", scenario=scenario, variant=variant,
                queries=1000, probes=GRID, max_check=2048, posting_additional_max_check=0
                    if variant == "graph" else 2048, posting_anchor_count=8,
                posting_page_limit=int(s["SearchPostingPageLimit"]), original_config=str(original)))
    first = source_configs[0]["Benchmark"]
    index, queries = first["Index"], first["Queries"]
    require(not output.resolve().is_relative_to(Path(index).resolve()), "Output is inside the immutable index")
    require(len(COUNTERS) == 57, "Unexpected navigation layout")
    for native in source_configs:
        require(native["Benchmark"]["Index"] == index and native["Benchmark"]["Queries"] == queries,
                "Mixed historical index/cohort")
    expected_queries = workload.get("queries", workload.get("query_vectors"))
    require(queries == expected_queries, "Historical query path differs from workload")
    q = np.load(queries, mmap_mode="r", allow_pickle=False)
    require(q.ndim == 2 and q.shape[1] == 128 and len(q) >= 1000 and
            q.dtype == np.dtype("<f4" if value_type == "Float" else "|u1"), "Strict query dtype/shape")
    for name in {queries, workload["attributes"], workload["base_file"]}:
        if Path(name).stat().st_size <= 128 << 20:
            digest = bounded_hash(name)
            require(previous["protected"].get(name, workload.get("protected", {}).get(name)) == digest,
                    f"Missing matching historical fingerprint: {name}")
            protected[name] = digest
        else:
            large.setdefault(name, identity(name))
    for native in source_configs:
        name = native["Benchmark"].get("PredicateFile", "")
        if name:
            digest = bounded_hash(name)
            require(previous["protected"].get(name, workload.get("protected", {}).get(name)) == digest,
                    "Predicate fingerprint mismatch")
            pred = np.load(name, mmap_mode="r", allow_pickle=False)
            require(pred.dtype == np.dtype("<u4") and len(pred) == len(q), "Predicate dtype/cohort mismatch")
            protected[name] = digest
    index_files = inventory(index)
    require(index_files, "Empty index inventory")
    if dataset == "SIFT1B":
        require(index == previous["index"] and index_files == previous["index_files"],
                "Completed 1B index identities differ from accepted registration")
    with (Path(index) / "tenant_0/HeadIndex/vectors.bin").open("rb") as stream:
        head_count, dimension = struct.unpack("<ii", stream.read(8))
    require(head_count > 1 and dimension == 128, "Missing real preserved H1 graph catalog")
    output.mkdir(parents=True)
    save(output / "owner.json", dict(runner="posting_frontier_repair", plan=str(path)))
    protected[str(output / "owner.json")] = bounded_hash(output / "owner.json")
    for name in ("configs", SOURCE_DIRECTORY, "runtime"):
        (output / name).mkdir()
    for helper in HELPERS:
        shutil.copy2(HERE / helper, output / SOURCE_DIRECTORY / helper)
        protected[str(output / SOURCE_DIRECTORY / helper)] = bounded_hash(output / SOURCE_DIRECTORY / helper)
    for native, case in zip(source_configs, cases):
        destination = output / "configs" / f"{case['case']}.ini"
        write_ini(destination, native)
        case["config"] = str(destination)
        protected[str(destination)] = bounded_hash(destination)
    for runtime in RUNTIMES:
        for kind in KINDS:
            batch = configparser.ConfigParser(interpolation=None)
            batch.optionxform = str
            batch["Batch"] = dict(CaseCount=str(len(cases)))
            for i, case in enumerate(cases, 1):
                batch[f"Case{i}"] = dict(Config=case["config"],
                    OutputDirectory=str(output / runtime / kind / case["case"]))
            destination = output / "configs" / f"{runtime}-{kind}.ini"
            write_ini(destination, batch)
            protected[str(destination)] = bounded_hash(destination)
    pending = configparser.ConfigParser(interpolation=None)
    pending.optionxform = str
    pending["Authorization"] = dict(AllowNativeRuns="false")
    for runtime in RUNTIMES:
        pending[f"Runtime.{runtime}"] = dict(NormalSHA256="", DiagnosticSHA256="",
            NavigationSchemaVersion=str(SCHEMA_VERSION), PolicyLabel=POLICIES[runtime],
            Provenance="", ProvenanceSHA256="")
    write_ini(output / "authorization.v2.template.ini", pending)
    save(output / REGISTRATION, dict(dataset=dataset, value_type=value_type, index=index,
        head_count=head_count, queries=queries, corpus_query_rows=len(q), measured_query_rows=1000,
        workload=str(workload_path), cases=cases, grid=GRID, scenarios=scenarios,
        cpu_node=plan.getint("CPUNode"), memory_node=plan.getint("MemoryNode"),
        runtimes={r: dict(config[f"Runtime.{r}"]) for r in RUNTIMES},
        protected=protected, protected_large=large, index_files=index_files,
        historical_index_fingerprints={p: v for p, v in previous["protected"].items()
                                      if p.startswith(index + "/")},
        counter_columns=list(COUNTERS), schema_version=SCHEMA_VERSION, policy_labels=POLICIES,
        ordinary_repetitions=1, points_per_process=len(cases) * len(GRID),
        native_processes=4, ordinary_timing="single-run same-batch controls, not performance acceptance",
        limitations=["57 columns do not expose final supplementary head IDs or last-row length/order",
                     "Protected-head counts plus unchanged original graph IDs are checked; final membership "
                     "and exact row-stop order need parent's native semantic fixtures",
                     "Complete-row budget overshoot is retained, not rejected as a hard cap",
                     "Index payload protection uses identities and historical fingerprints, not a new SSD hash",
                     "Preparation does not prove the new policy improves candidate quality"]))
    save(output / "status.json", dict(state="prepared_not_authorized", native_runs=0))


def checked(path):
    output = Path(read_ini(path)["Validation"]["OutputDirectory"])
    registration = json.loads((output / REGISTRATION).read_text())
    for name, expected in registration["protected"].items():
        require(bounded_hash(name) == expected, f"Registered input changed: {name}")
    validate_identities(registration["protected_large"])
    require(inventory(registration["index"]) == registration["index_files"], "Index identity changed")
    require(registration["counter_columns"] == list(COUNTERS) and
            registration["schema_version"] == SCHEMA_VERSION and
            registration["policy_labels"] == POLICIES, "Counter layout/version/policy drift")
    return output, registration


def validate_provenance(provenance, label, binaries):
    require(provenance.get("policy_label") == label and
            provenance.get("navigation_schema_version") == SCHEMA_VERSION and
            provenance.get("navigation_columns") == 57, "Provenance policy/schema mismatch")
    require(provenance.get("binary_hashes") == {k: v["sha256"] for k, v in binaries.items()},
            "Provenance binary hashes differ")
    source_hashes = provenance.get("source_hashes", {})
    require(source_hashes and all(isinstance(v, str) and re.fullmatch("[0-9a-f]{64}", v)
                                 for v in source_hashes.values()), "Frozen source hashes required")
    core_hashes = provenance.get("core_hashes", {})
    for binary in binaries.values():
        library = str(Path(binary["source"]).with_name("libSPTAGLibStatic.a"))
        require(library in core_hashes, "Matching normal/diagnostic core archive hashes required")
    for name, digest in core_hashes.items():
        require(Path(name).is_absolute() and sha(name) == digest, "Frozen core input changed")


def authorize(path, approval_path):
    output, registration = checked(path)
    require(not (output / "authorization.json").exists(), "Authorization already sealed")
    approval = read_ini(approval_path)
    require(approval["Authorization"].get("AllowNativeRuns") == "true", "Native runs not authorized")
    entries = {}
    for runtime in RUNTIMES:
        section = approval[f"Runtime.{runtime}"]
        schema, label = section.getint("NavigationSchemaVersion"), section["PolicyLabel"]
        require(schema == SCHEMA_VERSION and label == POLICIES[runtime], "Registered schema/policy mismatch")
        require(not section.get("NativePolicyField", ""), "Policy labels are provenance-only; no native tag")
        provenance = Path(section["Provenance"])
        require(provenance.is_file() and bounded_hash(provenance) == section["ProvenanceSHA256"],
                "Runtime provenance absent or changed")
        entries[runtime] = dict(schema=schema, policy=label,
            binaries={},
            provenance=dict(path=str(provenance), sha256=bounded_hash(provenance)))
        for kind in KINDS:
            source = Path(registration["runtimes"][runtime][kind.title()])
            expected = section[kind.title() + "SHA256"]
            require(len(expected) == 64 and sha(source) == expected, "Unbuilt/unpinned native client")
            entries[runtime]["binaries"][kind] = dict(source=str(source), sha256=expected)
        validate_provenance(json.loads(provenance.read_text()), label, entries[runtime]["binaries"])
    require(entries["before"]["policy"] != entries["after"]["policy"], "Distinct policy labels required")
    for runtime, entry in entries.items():
        for kind, binary in entry["binaries"].items():
            target = output / "runtime" / f"{runtime}-{kind}"
            require(not target.exists(), "Partial authorization output retained")
            shutil.copy2(binary["source"], target)
            require(sha(target) == binary["sha256"], "Runtime copy differs")
            binary["path"] = str(target)
    save(output / "authorization.json", dict(approval=str(approval_path),
        approval_sha256=bounded_hash(approval_path), runtimes=entries))
    save(output / "status.json", dict(state="authorized_not_run", native_runs=0))


def counters_at(folder, count):
    data = np.fromfile(folder / "navigation.u64", dtype="<u8").reshape(count, len(COUNTERS))
    return {name: data[:, i] for i, name in enumerate(COUNTERS)}


def compare_graph_work(left, right, graph_only=False):
    fields = GRAPH_FIELDS + (("h1_distances", "visited_checks", "head_predicate_calls") if graph_only else ())
    for field in fields:
        require(np.array_equal(left[field], right[field]), f"Graph work differs: {field}")


def check_accounting(folder, row, head_count):
    # Reuse the established partition/charged-distance invariants, not old stopping-policy assumptions.
    validate_navigation(folder.parent.parent, row)
    c = counters_at(folder, row["queries"])
    ids = np.fromfile(folder / "graph_ids.i32", dtype="<i4").reshape(row["queries"], row["nprobe"])
    distances = np.fromfile(folder / "graph_dist.f32", dtype="<f4").reshape(ids.shape)
    valid = ids >= 0
    require(np.all(ids >= -1) and np.all(ids[valid] < head_count), "Invalid original head IDs")
    require(np.all(np.isfinite(distances[valid])) and np.all(distances[valid] >= 0), "Invalid head distance")
    require(np.array_equal(valid.sum(axis=1), c["head_before"]), "Original head payload/count mismatch")
    require(all(len(set(a[a >= 0])) == np.count_nonzero(a >= 0) for a in ids), "Duplicate graph heads")
    if row["scenario"] != "unfilter":
        require(np.all(c["graph_checked_leaves"] <= row["max_check"]), "Filtered graph hard cap exceeded")
    if row["variant"] == "graph":
        for field in ("posting_activations", "supplement_checked_leaves", "supplement_distances",
                      "auxiliary_members", "h2_completed_rows", "h3plus_completed_rows"):
            require(not np.any(c[field]), "Graph-only control performed supplementation")
    for layer in ("h2", "h3plus"):
        require(np.all(c[layer + "_completed_rows"] <= c[layer + "_expand_attempts"]),
                "Completed rows exceed expansion attempts")
    total = c["graph_checked_leaves"].astype(np.int64) + c["supplement_checked_leaves"].astype(np.int64)
    overrun = np.maximum(total - row["max_check"] - row["posting_additional_max_check"], 0)
    supplement_overrun = np.where(c["supplement_checked_leaves"] > 0, overrun, 0)
    require(np.all((supplement_overrun == 0) | (c["h2_completed_rows"] > 0)),
            "Supplement budget overshoot without a completed H2 row")
    row["budget_accounting"] = dict(complete_row_overrun_queries=int(np.count_nonzero(supplement_overrun)),
        max_complete_row_overrun=int(supplement_overrun.max()),
        max_total_checked_leaves=int(total.max()), strict_last_row_bound_available=False,
        graph_nominal_overshoot_queries=int(np.count_nonzero(c["graph_checked_leaves"] > row["max_check"])))
    reasons, counts = np.unique(c["supplement_reason"], return_counts=True)
    row["supplement_reasons"] = {str(int(k)): int(v) for k, v in zip(reasons, counts)}
    if row.get("runtime") == "after":
        reason = c["supplement_reason"]
        active = c["posting_activations"] > 0
        filled = c["head_after"] == c["head_target"]
        require(not np.any(reason == 4), "After policy retained historical first-fill termination")
        processed = (c["supplement_checked_leaves"] > 0) | (active & filled)
        require(np.all(np.isin(reason[processed], [5, 6])), "Unexpected frontier termination reason")
        require(np.all(total[reason == 6] >= row["max_check"] + row["posting_additional_max_check"]),
                "Budget termination without consuming checked-leaf budget")
        require(np.array_equal(c["posting_target_met"][active], filled[active].astype(np.uint64)),
                "Posting target-met must report result status, not termination")
        row["filled_termination_reasons"] = {
            str(code): int(np.count_nonzero(active & filled & (reason == code))) for code in (5, 6)}
    row["head_target_met_queries"] = int(np.count_nonzero(c["head_after"] == row["nprobe"]))
    row["graph_head_distance_mean"] = float(distances[valid].mean()) if np.any(valid) else None


def exact_returned_distances(folder, workload, registration):
    count = 1000
    ids = np.fromfile(folder / "ids.i32", dtype="<i4").reshape(count, 10)
    distances = np.fromfile(folder / "dist.f32", dtype="<f4").reshape(count, 10)
    valid = ids >= 0
    require(np.all(ids >= -1) and not np.any((~valid[:, :-1]) & valid[:, 1:]), "Malformed result tail")
    require(not np.any((distances[:, 1:] < distances[:, :-1]) & valid[:, 1:]), "Unsorted top10 distances")
    queries = np.load(registration["queries"], mmap_mode="r", allow_pickle=False)[:count]
    base = workload["base_file"]
    if registration["value_type"] == "UInt8":
        with open(base, "rb") as stream:
            rows, dimension = struct.unpack("<ii", stream.read(8))
        require(dimension == 128 and Path(base).stat().st_size == 8 + rows * 128, "Native base header")
        vectors = np.memmap(base, dtype="u1", offset=8, mode="r", shape=(rows, 128))
    else:
        dtype = np.dtype([("dimension", "<i4"), ("vector", "<f4", (128,))])
        require(Path(base).stat().st_size % dtype.itemsize == 0, "Invalid fvecs extent")
        records = np.memmap(base, dtype=dtype, mode="r")
        require(np.all(records["dimension"][ids[valid]] == 128), "Selected fvecs dimension")
        vectors = records["vector"]
    selected = vectors[ids[valid]]
    targets = np.broadcast_to(queries[:, None, :], (count, 10, 128))[valid]
    require(np.all(np.isfinite(selected)) and np.all(selected == np.floor(selected)) and
            np.all(targets == np.floor(targets)) and np.all((selected >= 0) & (selected <= 255)) and
            np.all((targets >= 0) & (targets <= 255)), "Expected authenticated integer-valued SIFT")
    delta = selected.astype(np.int64) - targets.astype(np.int64)
    require(np.array_equal(np.einsum("ij,ij->i", delta, delta), distances[valid]),
            "Returned distance differs from exact original-vector L2")


def summarize(output, registration, authorization, runtime, kind):
    entry = authorization["runtimes"][runtime]
    workload = json.loads(Path(registration["workload"]).read_text())
    attrs = np.load(workload["attributes"], mmap_mode="r", allow_pickle=False)
    rows, expected_events = [], ["batch_begin", "batch_loaded"]
    for _ in registration["cases"]:
        expected_events += ["case_begin", "point", "point", "case_end"]
    expected_events += ["batch_end"]
    events = []
    for line in (output / f"{runtime}-{kind}.log").read_text().splitlines():
        require("PhaseTime:" not in line, "Phase logging contaminates controls")
        if line.startswith(('{"event":', '{"mode":')):
            events.append(json.loads(line))
    require([e.get("event") for e in events] == expected_events, "Incomplete/out-of-order batch")
    require(events[0]["cases"] == events[-1]["completed_cases"] == len(registration["cases"]) and
            events[0]["value_type"] == registration["value_type"] and
            events[0]["index"] == registration["index"] and events[0]["queries"] == registration["queries"],
            "Batch identity differs")
    require(events[1]["index_load_count"] == events[1]["query_corpus_load_count"] == 1, "Not load-once")
    points = [e for e in events if e["event"] == "point"]
    for i, case in enumerate(registration["cases"], 1):
        begin = 2 + (i - 1) * (len(GRID) + 2)
        for event in (events[begin], events[begin + len(GRID) + 1]):
            require(event["case_id"] == f"Case{i}" and event["config"] == case["config"] and
                    event["output_directory"] == str(output / runtime / kind / case["case"]),
                    "Case boundary identity differs")
        require(events[begin + len(GRID) + 1]["completed_points"] == len(GRID), "Incomplete case")
        for probe in GRID:
            row = dict(points[len(rows)])
            folder = output / runtime / kind / case["case"] / f"nprobe_{probe}"
            require(row["case_id"] == f"Case{i}" and row["config"] == case["config"] and
                    row["output_directory"] == str(folder.parent) and row["nprobe"] == probe and
                    row["queries"] == row["warmup_queries"] == row["measured_queries"] ==
                    row["replay_queries"] == 1000, "Case/window protocol mismatch")
            require(row["value_type"] == registration["value_type"] and
                    row["navigation_schema_version"] == entry["schema"] and
                    row["navigation_columns"] == 57 and row["diagnostic"] == (kind == "diagnostic") and
                    row.get("phase_timing", False) is False, "Type/schema/instrumentation mismatch")
            require(row["mode"] == ("graph" if case["variant"] == "graph" else "posting"), "Wrong policy mode")
            for key in ("max_check", "posting_additional_max_check", "posting_anchor_count"):
                require(row[key] == case[key], f"Budget mismatch: {key}")
            require(row["search_posting_page_limit"] == case["posting_page_limit"], "Page limit changed")
            for filename, size in (("ids.i32", 40000), ("dist.f32", 40000), ("work.u64", 64000),
                                   ("latency_us.f64", 8000)):
                require((folder / filename).stat().st_size == size, "Incomplete payload")
            row.update(quality(folder, workload["truth"][case["scenario"]],
                SelectedPredicate(attrs, workload["predicates"].get(case["scenario"])), 1000))
            exact_returned_distances(folder, workload, registration)
            row.update(case)
            row.update(runtime=runtime, kind=kind, policy=entry["policy"], timing_accepted=False,
                       timing_scope="single-run control" if kind == "normal" else "diagnostic only")
            require(np.isfinite(row["qps"]) and row["qps"] > 0, "Invalid timing")
            row["latency_sha256"] = sha(folder / "latency_us.f64")
            if kind == "diagnostic":
                check_accounting(folder, row, registration["head_count"])
                row["navigation_sha256"] = sha(folder / "navigation.u64")
                row["diagnostic_qps_not_for_throughput"] = row.pop("qps")
            rows.append(row)
    save(output / f"{runtime}-{kind}-results.json", rows)


def compare(path):
    output, registration = checked(path)
    reports = {(r, k): json.loads((output / f"{r}-{k}-results.json").read_text())
               for r in RUNTIMES for k in KINDS}
    expected = [(c["case"], p) for c in registration["cases"] for p in GRID]
    for (runtime, kind), rows in reports.items():
        require([(r["case"], r["nprobe"]) for r in rows] == expected, "Result report order differs")
        for row in rows:
            folder = output / runtime / kind / row["case"] / f"nprobe_{row['nprobe']}"
            hashes = dict(row["payload_hashes"], **{"latency_us.f64": row["latency_sha256"]})
            if kind == "diagnostic":
                hashes.update(row["graph_payload_hashes"])
                hashes["navigation.u64"] = row["navigation_sha256"]
            require(all(sha(folder / name) == digest for name, digest in hashes.items()),
                    "Validated raw payload changed")
    paired = []
    for runtime in RUNTIMES:
        normal, diag = reports[runtime, "normal"], reports[runtime, "diagnostic"]
        require(len(normal) == len(diag) == len(registration["cases"]) * 2, "Incomplete comparison")
        for left, right in zip(normal, diag):
            require(left["payload_hashes"] == right["payload_hashes"], "Instrumentation changed IDs/distances/SSD work")
        indexed = {(r["scenario"], r["variant"], r["nprobe"]): r for r in diag}
        for row in diag:
            graph = indexed[row["scenario"], "graph", row["nprobe"]]
            require(row["graph_payload_hashes"] == graph["graph_payload_hashes"], "Graph-before payload changed")
            a = counters_at(output / runtime / "diagnostic" / row["case"] / f"nprobe_{row['nprobe']}", 1000)
            b = counters_at(output / runtime / "diagnostic" / graph["case"] / f"nprobe_{row['nprobe']}", 1000)
            compare_graph_work(a, b)
            if row["scenario"] == "unfilter":
                require(row["payload_hashes"] == graph["payload_hashes"], "Unfiltered output changed")
    for before, after in zip(reports["before", "diagnostic"], reports["after", "diagnostic"]):
        require(before["graph_payload_hashes"] == after["graph_payload_hashes"], "Old/new graph heads differ")
        arrays = [counters_at(output / runtime / "diagnostic" / row["case"] /
                             f"nprobe_{row['nprobe']}", 1000)
                  for runtime, row in (("before", before), ("after", after))]
        compare_graph_work(*arrays, graph_only=before["variant"] == "graph")
        if before["variant"] == "graph":
            require(before["payload_hashes"] == after["payload_hashes"], "Graph-only final payload changed")
        paired.append(dict(scenario=before["scenario"], variant=before["variant"], nprobe=before["nprobe"],
            before_recall=before["recall"], after_recall=after["recall"],
            recall_delta=after["recall"] - before["recall"],
            before_heads=before["mean_navigation"]["head_after"], after_heads=after["mean_navigation"]["head_after"],
            before_candidates=before["mean_navigation"]["posting_new_candidates"],
            after_candidates=after["mean_navigation"]["posting_new_candidates"],
            before_ssd_work=before["mean_ssd_work"], after_ssd_work=after["mean_ssd_work"]))
    for point, before, after in zip(paired, reports["before", "normal"], reports["after", "normal"]):
        point.update(before_single_run_qps=before["qps"], after_single_run_qps=after["qps"],
                     timing_accepted=False)
    save(output / "comparison.json", dict(parity_passed=True, coordinates=paired,
        performance_acceptance=False, limitations=registration["limitations"]))


def run(path, runtime, kind):
    output, registration = checked(path)
    require((output / "authorization.json").is_file(), "Explicit parent authorization required")
    authorization = json.loads((output / "authorization.json").read_text())
    require(bounded_hash(authorization["approval"]) == authorization["approval_sha256"], "Approval changed")
    entry = authorization["runtimes"][runtime]
    require(bounded_hash(entry["provenance"]["path"]) == entry["provenance"]["sha256"], "Provenance changed")
    binary = entry["binaries"][kind]
    require(sha(binary["path"]) == binary["sha256"], "Frozen client changed")
    require(not [k for k in os.environ if k.startswith(("SPTAG_", "SPANN_", "OMP_")) or k == "LD_PRELOAD"],
            "Search environment overrides forbidden")
    target = output / runtime / kind
    require(not target.exists(), "Partial/completed stage retained; never overwrite")
    target.mkdir(parents=True)
    command = ["/usr/bin/time", "-v", "-o", str(output / f"{runtime}-{kind}.resources.txt"),
        "numactl", f"--cpunodebind={registration['cpu_node']}", f"--membind={registration['memory_node']}",
        sys.executable, "-B", str(output / SOURCE_DIRECTORY / "selectivity_common.py"), str(target),
        binary["path"], str(output / "configs" / f"{runtime}-{kind}.ini")]
    save(output / f"{runtime}-{kind}-command.json", command)
    with (output / f"{runtime}-{kind}.log").open("x") as stream:
        process = subprocess.Popen(command, cwd=output, stdout=stream, stderr=subprocess.STDOUT)
        native = None
        started = time.monotonic()
        try:
            while process.poll() is None:
                if native is None:
                    native = native_identity(process.pid, binary["path"])
                save(output / "status.json", dict(state="running", runtime=runtime, kind=kind,
                    native=native, wrapper_pid=process.pid, controller_pid=os.getpid(),
                    elapsed_seconds=time.monotonic() - started))
                time.sleep(15)
            require(process.returncode == 0, f"Native stage failed: {process.returncode}")
        except BaseException:
            terminate_owned(process, native)
            raise
        finally:
            checked(path)
    summarize(output, registration, authorization, runtime, kind)
    save(output / "status.json", dict(state="stage_complete", runtime=runtime, kind=kind))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "authorize", "run", "compare"))
    parser.add_argument("ini", type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--runtime", choices=RUNTIMES)
    parser.add_argument("--kind", choices=KINDS)
    args = parser.parse_args()
    try:
        if args.stage == "prepare":
            prepare(args.ini.resolve())
        elif args.stage == "authorize":
            require(args.approval is not None, "Supply parent approval INI")
            authorize(args.ini.resolve(), args.approval.resolve())
        elif args.stage == "run":
            require(args.runtime is not None and args.kind is not None, "Supply runtime and kind")
            run(args.ini.resolve(), args.runtime, args.kind)
        else:
            compare(args.ini.resolve())
    except Exception as error:
        output = Path(read_ini(args.ini)["Validation"]["OutputDirectory"])
        owner = output / "owner.json"
        if owner.is_file() and json.loads(owner.read_text()).get("runner") == "posting_frontier_repair":
            save(output / f"failure-{time.time_ns()}.json",
                 dict(stage=args.stage, error=str(error), state="failed"))
        raise
