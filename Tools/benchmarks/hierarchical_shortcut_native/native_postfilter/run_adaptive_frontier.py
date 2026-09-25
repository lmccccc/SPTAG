#!/usr/bin/env python3
"""Bounded adaptive-frontier revision of the frozen native comparison harness."""
import argparse
import configparser
from datetime import datetime, timezone
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
REGISTRATION = "registration.json"
SOURCE_DIRECTORY = "source"
SCHEMA_VERSION = 6
POLICIES = {"before": "predicate_first_legacy_posting_order",
            "after": "predicate_first_adaptive_distance_frontier"}
GRID = [96, 384]
VARIANTS = ("graph", "postgraph_extra")
RUNTIMES = ("before", "after")
KINDS = ("normal", "diagnostic")
SCENARIOS = {
    "SIFT1M": ["unfilter", "broad_tag", "medium_tag", "extreme_tag", "numeric", "mixed_dnf"],
    "SIFT1B": ["unfilter", "sel_01pct", "mixed_dnf"],
}
CURVE_SCENARIOS = ["unfilter", "broad_tag", "medium_tag", "sel_01pct", "mixed_dnf"]
CURVE_GRID = [16, 24, 48, 96, 192, 384, 768]
GRAPH_FIELDS = ("graph_rows", "tree_visits", "graph_unique_h1", "graph_may_matches",
                "graph_checked_leaves", "graph_distances", "head_before", "head_target")
OBSERVERS = ("graph_unique_h1", "graph_may_matches")
HELPERS = ("run_adaptive_frontier.py", "run_full.py", "run_postgraph.py",
           "run_selectivity.py", "selectivity_common.py", "test_adaptive_frontier.py")


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


def read_design(config):
    plan = config["Validation"]
    dataset = plan["Dataset"]
    mode = plan.get("Mode", "control")
    scenarios = [value.strip() for value in plan["Scenarios"].split(",")]
    grid = json.loads(config["SearchSweep"]["NProbe"])
    repeats = plan.getint("Repeats", fallback=1)
    require(mode in ("control", "selectivity_curve"), "Unknown comparison mode")
    if mode == "selectivity_curve":
        require(dataset == "SIFT1B" and scenarios == CURVE_SCENARIOS and repeats == 2,
                "Expected five-scenario SIFT1B curves with two ordinary repetitions")
        require(isinstance(grid, list) and len(grid) >= 2 and
                all(type(value) is int and value >= 10 for value in grid) and
                grid == sorted(set(grid)), "Native nprobe grid must be positive, unique and ascending")
        output_name = "main_posting_adaptive_curves_20260924"
    else:
        require(dataset in SCENARIOS and scenarios == SCENARIOS[dataset] and
                grid == GRID and repeats == 1, "Unexpected control scenario/grid/repetition scope")
        output_name = "main_posting_adaptive_frontier_20260924"
    variants = selected_variants(config)
    revision = plan.get("ImplementationRevision", "")
    require(revision in ("", "controlled_ascent"), "Unknown implementation revision")
    if revision:
        require(mode == "selectivity_curve" and variants == ("postgraph_extra",) and
                grid == CURVE_GRID and plan.getint("CPUNode") == plan.getint("MemoryNode") == 3,
                "Controlled-ascent scope requires adaptive-only fixed curves on NUMA3")
        counter_design(config)
        output_name = "main_posting_controlled_ascent_curves_20260924"
    return dataset, mode, scenarios, grid, repeats, output_name


def selected_variants(config):
    plan = config["Validation"]
    variants = tuple(value.strip() for value in plan.get("Variants", ",".join(VARIANTS)).split(","))
    require(variants and len(set(variants)) == len(variants) and
            all(value in VARIANTS for value in variants), "Invalid explicit variant selection")
    require(plan.get("Mode", "control") == "selectivity_curve" or variants == VARIANTS,
            "Control mode retains graph and posting controls")
    return variants


def counter_design(config):
    section = config["CounterProbe"]
    scenarios = section["Scenarios"].split(",")
    probes = json.loads(section["NProbe"])
    count = section.getint("MaxQueries")
    require(scenarios == ["sel_01pct", "mixed_dnf"] and probes == [16, 96, 384] and
            all(type(p) is int for p in probes) and count == section.getint("Warmup") == 32,
            "Counter-only scope must be two sparse scenarios, three probes, first32 full windows")
    return scenarios, probes, count


def stage_cases(registration, kind):
    return registration.get("cases_by_kind", {}).get(kind, registration["cases"])


def repeat_cases(cases, repeats):
    result = []
    for repetition in range(1, repeats + 1):
        for case in (cases if repetition % 2 else list(reversed(cases))):
            item = dict(case, repetition=repetition)
            if repeats > 1:
                item["case"] = f"r{repetition}_{case['case']}"
            result.append(item)
    return result


def prepare(path):
    config = read_ini(path)
    plan = config["Validation"]
    dataset, mode, scenarios, grid, repeats, output_name = read_design(config)
    controlled = plan.get("ImplementationRevision") == "controlled_ascent"
    variants = selected_variants(config)
    runtimes = ("after",) if controlled else RUNTIMES
    output = Path(plan["OutputDirectory"])
    require(output.is_absolute() and output.name == output_name and
            not output.exists(), f"Require a fresh {output_name} output")
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
        for variant in variants:
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
            if controlled:
                require(dict(config["SearchSSDIndex"]) == dict(s),
                        "Checked-in search settings differ from the authenticated native settings")
                native["SearchSSDIndex"] = dict(config["SearchSSDIndex"])
            native["SearchSweep"]["NProbe"] = json.dumps(grid)
            b["ValueType"] = value_type
            source_configs.append(native)
            cases.append(dict(case=f"{scenario}_{variant}", scenario=scenario, variant=variant,
                queries=1000, probes=grid, max_check=2048, posting_additional_max_check=0
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
    require(max(grid) <= head_count, "Probe capacity exceeds the H1 catalog")
    references = {}
    if controlled:
        for key, files in (
                ("PriorAdaptiveCampaign", ("registration.json", "after-normal-results.json")),
                ("PreservedPipeANNBaseline", ("registration.json", "results.json")),
                ("PriorCounterCampaign", ("cost-attribution.json",))):
            root = Path(plan[key])
            require(root.is_absolute() and root.resolve() != output.resolve(), "Invalid preserved reference")
            references[key] = str(root)
            for filename in files:
                name = str(root / filename)
                protected[name] = bounded_hash(name)
        old_root = Path(references["PriorAdaptiveCampaign"])
        old = json.loads((old_root / REGISTRATION).read_text())
        require(old["index"] == index and old["queries"] == queries and
                old["index_files"] == index_files, "Prior adaptive index/cohort differs")
        old_points = json.loads((old_root / "after-normal-results.json").read_text())
        for row in old_points:
            if row["variant"] == "postgraph_extra":
                folder = old_root / "after/normal" / row["case"] / f"nprobe_{row['nprobe']}"
                for filename, digest in row["payload_hashes"].items():
                    name = str(folder / filename)
                    require(bounded_hash(name) == digest, "Preserved adaptive payload changed")
                    protected[name] = digest
    output.mkdir(parents=True)
    save(output / "owner.json", dict(runner="posting_adaptive_frontier", plan=str(path)))
    protected[str(output / "owner.json")] = bounded_hash(output / "owner.json")
    for name in ("configs", SOURCE_DIRECTORY, "runtime"):
        (output / name).mkdir()
    for helper in HELPERS:
        shutil.copy2(HERE / helper, output / SOURCE_DIRECTORY / helper)
        protected[str(output / SOURCE_DIRECTORY / helper)] = bounded_hash(output / SOURCE_DIRECTORY / helper)
    if controlled:
        destination = output / SOURCE_DIRECTORY / "sift1b_controlled_ascent.ini"
        shutil.copy2(path, destination)
        protected[str(destination)] = bounded_hash(destination)
    for native, case in zip(source_configs, cases):
        destination = output / "configs" / f"{case['case']}.ini"
        write_ini(destination, native)
        case["config"] = str(destination)
        protected[str(destination)] = bounded_hash(destination)
    diagnostic_cases = []
    if controlled:
        counter_scenarios, counter_probes, count = counter_design(config)
        for scenario in counter_scenarios:
            case = next(c for c in cases if c["scenario"] == scenario)
            item = dict(case, case="counter_" + case["case"], queries=count,
                        probes=counter_probes, repetition=1, counter_only=True)
            native = read_ini(case["config"])
            native["Benchmark"]["MaxQueries"] = native["Benchmark"]["Warmup"] = str(count)
            native["SearchSweep"]["NProbe"] = json.dumps(counter_probes)
            destination = output / "configs" / f"{item['case']}.ini"
            write_ini(destination, native)
            item["config"] = str(destination)
            protected[str(destination)] = bounded_hash(destination)
            diagnostic_cases.append(item)
    cases = repeat_cases(cases, repeats)
    by_kind = dict(normal=cases, diagnostic=diagnostic_cases if controlled else cases)
    for runtime in runtimes:
        for kind in KINDS:
            batch = configparser.ConfigParser(interpolation=None)
            batch.optionxform = str
            batch["Batch"] = dict(CaseCount=str(len(by_kind[kind])))
            for i, case in enumerate(by_kind[kind], 1):
                batch[f"Case{i}"] = dict(Config=case["config"],
                    OutputDirectory=str(output / runtime / kind / case["case"]))
            destination = output / "configs" / f"{runtime}-{kind}.ini"
            write_ini(destination, batch)
            protected[str(destination)] = bounded_hash(destination)
    pending = configparser.ConfigParser(interpolation=None)
    pending.optionxform = str
    pending["Authorization"] = dict(AllowNativeRuns="false")
    for runtime in runtimes:
        pending[f"Runtime.{runtime}"] = dict(NormalSHA256="", DiagnosticSHA256="",
            NavigationSchemaVersion=str(SCHEMA_VERSION), PolicyLabel=POLICIES[runtime],
            Provenance="", ProvenanceSHA256="")
    write_ini(output / "authorization.template.ini", pending)
    save(output / REGISTRATION, dict(dataset=dataset, mode=mode, value_type=value_type, index=index,
        head_count=head_count, queries=queries, corpus_query_rows=len(q), measured_query_rows=1000,
        workload=str(workload_path), cases=cases, cases_by_kind=by_kind, grid=grid, scenarios=scenarios,
        variants=list(variants), implementation_revision=plan.get("ImplementationRevision", ""),
        preserved_references=references,
        cpu_node=plan.getint("CPUNode"), memory_node=plan.getint("MemoryNode"),
        runtimes={r: dict(config[f"Runtime.{r}"]) for r in runtimes},
        protected=protected, protected_large=large, index_files=index_files,
        historical_index_fingerprints={p: v for p, v in previous["protected"].items()
                                      if p.startswith(index + "/")},
        counter_columns=list(COUNTERS), schema_version=SCHEMA_VERSION, policy_labels=POLICIES,
        after_stop_reasons={"5": "reachable exhaustion", "6": "checked-leaf budget",
                           "7": "ANN frontier convergence, possibly underfilled"},
        convergence_scope="Native navigation-pool ANN heuristic; representative distances are not member bounds",
        ordinary_repetitions=repeats, points_per_process=len(cases) * len(grid),
        native_processes=2 if controlled else (1 if mode == "selectivity_curve" else 4),
        points_by_kind={k: sum(len(c["probes"]) for c in v) for k, v in by_kind.items()},
        ordinary_timing="two reversed case-order passes; full ascending sweep per case"
            if repeats == 2 else "single-run same-batch controls, not performance acceptance",
        limitations=["57 columns do not expose final supplementary head IDs or last-row length/order",
                     "Protected-head counts plus unchanged original graph IDs are checked; final membership "
                     "and exact row-stop order need parent's native semantic fixtures",
                     "Complete-row budget overshoot is retained, not rejected as a hard cap",
                     "Index payload protection uses identities and historical fingerprints, not a new SSD hash",
                     "Preparation does not prove the new policy improves candidate quality"]))
    save(output / "status.json", dict(state="prepared_not_authorized", native_runs=0))
    if controlled:
        save(output / "series-contract.json", dict(
            ordinary_file="after-normal-results.json", ordinary_points=70,
            case_order=[case["case"] for case in cases], probes=grid, queries=1000,
            variant="postgraph_extra", runtime="after", kind="normal",
            policy=POLICIES["after"], implementation_revision="controlled_ascent",
            fields=["case", "repetition", "scenario", "variant", "nprobe", "queries", "recall", "qps"],
            counter_file="after-diagnostic-results.json", counter_points=6, counter_queries=32,
            counter_has_no_qps_field=True, preserved_references=references,
            old_new_ordinary_comparison="ordinary-comparison.json",
            counter_comparison="counter-comparison.json",
            historical_timing_spliced=False, h1_only_series=False))


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
    for runtime in registration["runtimes"]:
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
        provenance_data = json.loads(provenance.read_text())
        validate_provenance(provenance_data, label, entries[runtime]["binaries"])
        if registration.get("implementation_revision"):
            require(provenance_data.get("implementation_revision") == registration["implementation_revision"],
                    "Frozen implementation revision mismatch")
    if "before" in entries:
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


def compare_graph_work(left, right, left_enabled, right_enabled):
    for values, enabled in ((left, left_enabled), (right, right_enabled)):
        if not enabled:
            for field in OBSERVERS:
                require(not np.any(values[field]), f"Disabled observer must be zero: {field}")
    fields = tuple(field for field in GRAPH_FIELDS if field not in OBSERVERS)
    if left_enabled and right_enabled:
        fields += OBSERVERS
    elif not left_enabled and not right_enabled:
        fields += ("h1_distances", "visited_checks", "head_predicate_calls")
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
    adaptive = row.get("runtime") == "after"
    reason = c["supplement_reason"]
    require(np.all(reason <= (7 if adaptive else 6)), "Reason outside registered policy decoder")
    if adaptive:
        require(row.get("policy") == POLICIES["after"], "Adaptive-after policy label mismatch")
        active = c["posting_activations"] > 0
        filled = c["head_after"] == c["head_target"]
        require(not np.any(reason == 4), "After policy retained historical first-fill termination")
        processed = (c["supplement_checked_leaves"] > 0) | (active & filled)
        require(np.all(np.isin(reason[processed], [5, 6, 7])), "Unexpected adaptive termination reason")
        require(not np.any((reason == 7) & ~active), "Convergence without activated supplementation")
        require(np.all(total[reason == 6] >= row["max_check"] + row["posting_additional_max_check"]),
                "Budget termination without consuming checked-leaf budget")
        require(np.array_equal(c["posting_target_met"][active], filled[active].astype(np.uint64)),
                "Posting target-met must report result status, not termination")
        row["filled_termination_reasons"] = {
            str(code): int(np.count_nonzero(active & filled & (reason == code))) for code in (5, 6, 7)}
    converged = reason == 7
    underfilled = c["head_after"] < c["head_target"]
    row["convergence_fraction"] = float(converged.mean())
    row["head_underfill_fraction"] = float(underfilled.mean())
    row["convergence_underfilled_fraction"] = float((converged & underfilled).mean())
    row["convergence_filled_fraction"] = float((converged & ~underfilled).mean())
    row["underfill_given_convergence"] = (float(underfilled[converged].mean())
                                        if np.any(converged) else None)
    row["head_target_met_queries"] = int(np.count_nonzero(c["head_after"] == row["nprobe"]))
    row["graph_head_distance_mean"] = float(distances[valid].mean()) if np.any(valid) else None


def exact_returned_distances(folder, workload, registration, count=1000):
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
    cases = stage_cases(registration, kind)
    for case in cases:
        expected_events += ["case_begin"] + ["point"] * len(case["probes"]) + ["case_end"]
    expected_events += ["batch_end"]
    events = []
    for line in (output / f"{runtime}-{kind}.log").read_text().splitlines():
        require("PhaseTime:" not in line, "Phase logging contaminates controls")
        if line.startswith(('{"event":', '{"mode":')):
            events.append(json.loads(line))
    require([e.get("event") for e in events] == expected_events, "Incomplete/out-of-order batch")
    require(events[0]["cases"] == events[-1]["completed_cases"] == len(cases) and
            events[0]["value_type"] == registration["value_type"] and
            events[0]["index"] == registration["index"] and events[0]["queries"] == registration["queries"],
            "Batch identity differs")
    require(events[1]["index_load_count"] == events[1]["query_corpus_load_count"] == 1, "Not load-once")
    points = [e for e in events if e["event"] == "point"]
    begin = 2
    for i, case in enumerate(cases, 1):
        probes = case["probes"]
        count = case["queries"]
        for event in (events[begin], events[begin + len(probes) + 1]):
            require(event["case_id"] == f"Case{i}" and event["config"] == case["config"] and
                    event["output_directory"] == str(output / runtime / kind / case["case"]),
                    "Case boundary identity differs")
        require(events[begin + len(probes) + 1]["completed_points"] == len(probes), "Incomplete case")
        for probe in probes:
            row = dict(points[len(rows)])
            folder = output / runtime / kind / case["case"] / f"nprobe_{probe}"
            require(row["case_id"] == f"Case{i}" and row["config"] == case["config"] and
                    row["output_directory"] == str(folder.parent) and row["nprobe"] == probe and
                    row["queries"] == row["warmup_queries"] == row["measured_queries"] ==
                    row["replay_queries"] == count, "Case/window protocol mismatch")
            require(row["value_type"] == registration["value_type"] and
                    row["navigation_schema_version"] == entry["schema"] and
                    row["navigation_columns"] == 57 and row["diagnostic"] == (kind == "diagnostic") and
                    row.get("phase_timing", False) is False, "Type/schema/instrumentation mismatch")
            require(row["mode"] == ("graph" if case["variant"] == "graph" else "posting"), "Wrong policy mode")
            for key in ("max_check", "posting_additional_max_check", "posting_anchor_count"):
                require(row[key] == case[key], f"Budget mismatch: {key}")
            require(row["search_posting_page_limit"] == case["posting_page_limit"], "Page limit changed")
            for filename, size in (("ids.i32", count * 40), ("dist.f32", count * 40),
                                   ("work.u64", count * 64), ("latency_us.f64", count * 8)):
                require((folder / filename).stat().st_size == size, "Incomplete payload")
            row.update(quality(folder, workload["truth"][case["scenario"]],
                SelectedPredicate(attrs, workload["predicates"].get(case["scenario"])), count))
            exact_returned_distances(folder, workload, registration, count)
            row.update(case)
            row.update(runtime=runtime, kind=kind, policy=entry["policy"], timing_accepted=False,
                       timing_scope=("repeated ordinary curve" if registration["ordinary_repetitions"] > 1
                                     else "single-run control") if kind == "normal" else "diagnostic only")
            row["implementation_revision"] = registration.get("implementation_revision", "")
            require(np.isfinite(row["qps"]) and row["qps"] > 0, "Invalid timing")
            row["latency_sha256"] = sha(folder / "latency_us.f64")
            if kind == "diagnostic":
                check_accounting(folder, row, registration["head_count"])
                row["navigation_sha256"] = sha(folder / "navigation.u64")
                row["diagnostic_qps_not_for_throughput"] = row.pop("qps")
            rows.append(row)
        begin += len(probes) + 2
    save(output / f"{runtime}-{kind}-results.json", rows)
    if registration.get("implementation_revision") == "controlled_ascent":
        compare_preserved_curves(output, registration, rows, kind)


def verify_payloads(folder, row):
    hashes = dict(row["payload_hashes"], **{"latency_us.f64": row["latency_sha256"]})
    if row["kind"] == "diagnostic":
        hashes.update(row["graph_payload_hashes"])
        hashes["navigation.u64"] = row["navigation_sha256"]
    require(all(sha(folder / name) == digest for name, digest in hashes.items()),
            "Validated curve payload changed")


def compare_preserved_curves(output, registration, rows, kind):
    expected = [(c["case"], p) for c in stage_cases(registration, kind) for p in c["probes"]]
    require([(r["case"], r["nprobe"]) for r in rows] == expected, "Curve report order differs")
    for row in rows:
        verify_payloads(output / "after" / kind / row["case"] / f"nprobe_{row['nprobe']}", row)
    references = registration["preserved_references"]
    if kind == "normal":
        old_root = Path(references["PriorAdaptiveCampaign"])
        old_rows = json.loads((old_root / "after-normal-results.json").read_text())
        key = lambda row: (row["scenario"], row["variant"], row["nprobe"], row["repetition"])
        old = {key(r): r for r in old_rows if r["variant"] == "postgraph_extra"}
        require(len(old) == len(rows) == 70 and set(old) == {key(r) for r in rows},
                "Preserved/current adaptive coordinates differ")
        first, points = {}, []
        for row in rows:
            coordinate = key(row)[:3]
            if coordinate in first:
                require(first[coordinate] == row["payload_hashes"], "Reverse-pass payload parity failed")
            else:
                first[coordinate] = row["payload_hashes"]
            previous = old[key(row)]
            require(previous["queries"] == row["queries"] == 1000 and
                    previous["kind"] == row["kind"] == "normal", "Mixed ordinary windows/instrumentation")
            if row["scenario"] == "unfilter":
                require(previous["payload_hashes"] == row["payload_hashes"],
                        "Preserved/new exact unfiltered IDs/distances/SSD work differ")
            points.append(dict(scenario=row["scenario"], variant=row["variant"],
                nprobe=row["nprobe"], repetition=row["repetition"],
                old_recall=previous["recall"], new_recall=row["recall"],
                recall_delta=row["recall"] - previous["recall"],
                old_preserved_qps=previous["qps"], new_ordinary_qps=row["qps"],
                qps_ratio=row["qps"] / previous["qps"],
                recall_regressed=row["recall"] < previous["recall"],
                qps_regressed=row["qps"] < previous["qps"],
                old_ssd_work=previous["mean_ssd_work"], new_ssd_work=row["mean_ssd_work"]))
        save(output / "ordinary-comparison.json", dict(
            ordinary_report_sha256=sha(output / "after-normal-results.json"),
            ordinary_log_sha256=sha(output / "after-normal.log"),
            references=references, points=points, exact_unfiltered_payload_parity=True,
            reverse_pass_payload_parity=True, performance_acceptance=False,
            timing_scope="Preserved prior adaptive versus fresh ordinary passes; not fresh paired "
                         "timing, not pooled, and no historical QPS substituted into the new series"))
        return
    ordinary = json.loads((output / "ordinary-comparison.json").read_text())
    require(sha(output / "after-normal-results.json") == ordinary["ordinary_report_sha256"] and
            sha(output / "after-normal.log") == ordinary["ordinary_log_sha256"],
            "Validated ordinary report/log changed")
    normals = json.loads((output / "after-normal-results.json").read_text())
    reference = json.loads((Path(references["PriorCounterCampaign"]) / "cost-attribution.json").read_text())
    require(reference["query_count"] == 32 and reference["diagnostic_points"] == 6,
            "Prior counter window/scope differs")
    old = {(r["scenario"], r["nprobe"]): r for r in reference["points"]}
    require(len(rows) == len(old) == 6, "Expected six counter-only points")
    points = []
    for row in rows:
        require(row["queries"] == 32 and row["counter_only"] and "qps" not in row,
                "Counter-only result mislabeled as ordinary throughput")
        folder = output / "after/diagnostic" / row["case"] / f"nprobe_{row['nprobe']}"
        matching = [r for r in normals if (r["scenario"], r["nprobe"]) ==
                    (row["scenario"], row["nprobe"])]
        require(len(matching) == 2, "Missing both ordinary prefixes")
        for normal in matching:
            other = output / "after/normal" / normal["case"] / f"nprobe_{row['nprobe']}"
            verify_payloads(other, normal)
            for filename in PAYLOADS:
                data = (folder / filename).read_bytes()
                require(data == (other / filename).read_bytes()[:len(data)],
                        "Counter instrumentation changed ordinary-prefix IDs/distances/SSD work")
        previous = old[row["scenario"], row["nprobe"]]["counters"]
        current = row["mean_navigation"]
        for field in GRAPH_FIELDS:
            require(previous[field] == current[field], f"Counter graph-phase mean changed: {field}")
        points.append(dict(scenario=row["scenario"], nprobe=row["nprobe"], queries=32,
            old_counters=previous, new_counters=current,
            counter_delta={name: current[name] - previous[name] for name in COUNTERS},
            supplement_reasons=row["supplement_reasons"],
            convergence_fraction=row["convergence_fraction"],
            head_underfill_fraction=row["head_underfill_fraction"],
            throughput_evidence=False))
    save(output / "counter-comparison.json", dict(points=points, ordinary_prefix_payload_parity=True,
        graph_phase_mean_parity=True, timing_scope="Counter-only first32; no QPS/performance evidence",
        performance_acceptance=False))


def compare(path):
    output, registration = checked(path)
    if registration.get("implementation_revision") == "controlled_ascent":
        for kind in KINDS:
            rows = json.loads((output / f"after-{kind}-results.json").read_text())
            compare_preserved_curves(output, registration, rows, kind)
        return
    reports = {(r, k): json.loads((output / f"{r}-{k}-results.json").read_text())
               for r in RUNTIMES for k in KINDS}
    require(registration.get("mode", "control") == "control",
            "Ordinary curve mode does not claim a four-runtime diagnostic comparison")
    expected = [(c["case"], p) for c in registration["cases"] for p in c["probes"]]
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
        require(len(normal) == len(diag) == len(expected), "Incomplete comparison")
        for left, right in zip(normal, diag):
            require(left["payload_hashes"] == right["payload_hashes"], "Instrumentation changed IDs/distances/SSD work")
        indexed = {(r["scenario"], r["variant"], r["nprobe"]): r for r in diag}
        for row in diag:
            graph = indexed[row["scenario"], "graph", row["nprobe"]]
            require(row["graph_payload_hashes"] == graph["graph_payload_hashes"], "Graph-before payload changed")
            a = counters_at(output / runtime / "diagnostic" / row["case"] / f"nprobe_{row['nprobe']}", 1000)
            b = counters_at(output / runtime / "diagnostic" / graph["case"] / f"nprobe_{row['nprobe']}", 1000)
            compare_graph_work(a, b, row["variant"] != "graph", False)
            if row["scenario"] == "unfilter":
                require(row["payload_hashes"] == graph["payload_hashes"], "Unfiltered output changed")
    for before, after in zip(reports["before", "diagnostic"], reports["after", "diagnostic"]):
        require(before["graph_payload_hashes"] == after["graph_payload_hashes"], "Old/new graph heads differ")
        arrays = [counters_at(output / runtime / "diagnostic" / row["case"] /
                             f"nprobe_{row['nprobe']}", 1000)
                  for runtime, row in (("before", before), ("after", after))]
        enabled = before["variant"] != "graph"
        compare_graph_work(*arrays, enabled, enabled)
        if before["variant"] == "graph":
            require(before["payload_hashes"] == after["payload_hashes"], "Graph-only final payload changed")
        paired.append(dict(scenario=before["scenario"], variant=before["variant"], nprobe=before["nprobe"],
            before_recall=before["recall"], after_recall=after["recall"],
            recall_delta=after["recall"] - before["recall"],
            before_heads=before["mean_navigation"]["head_after"], after_heads=after["mean_navigation"]["head_after"],
            before_candidates=before["mean_navigation"]["posting_new_candidates"],
            after_candidates=after["mean_navigation"]["posting_new_candidates"],
            convergence_fraction=after["convergence_fraction"],
            head_underfill_fraction=after["head_underfill_fraction"],
            convergence_underfilled_fraction=after["convergence_underfilled_fraction"],
            convergence_filled_fraction=after["convergence_filled_fraction"],
            underfill_given_convergence=after["underfill_given_convergence"],
            after_stopping_reasons=after["supplement_reasons"],
            before_ssd_work=before["mean_ssd_work"], after_ssd_work=after["mean_ssd_work"]))
    for point, before, after in zip(paired, reports["before", "normal"], reports["after", "normal"]):
        point.update(before_single_run_qps=before["qps"], after_single_run_qps=after["qps"],
                     timing_accepted=False)
    save(output / "comparison.json", dict(parity_passed=True, coordinates=paired,
        performance_acceptance=False, limitations=registration["limitations"]))


def run(path, runtime, kind):
    output, registration = checked(path)
    if registration.get("mode") == "selectivity_curve":
        controlled = registration.get("implementation_revision") == "controlled_ascent"
        require(runtime == "after" and (kind == "normal" or controlled),
                "Curve mode authorizes only registered adaptive stages")
        if kind == "diagnostic":
            require((output / "ordinary-comparison.json").is_file(),
                    "Complete and validate the ordinary curve before counter-only execution")
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
        started_utc = datetime.now(timezone.utc).isoformat()
        try:
            while process.poll() is None:
                if native is None:
                    native = native_identity(process.pid, binary["path"])
                    if native is not None:
                        event = dict(event="native_started", runtime=runtime, kind=kind, native=native,
                            controller_started_utc=started_utc,
                            observed_utc=datetime.now(timezone.utc).isoformat(),
                            log_path=str(output / f"{runtime}-{kind}.log"))
                        save(output / f"{runtime}-{kind}-native-start.json", event)
                        print(json.dumps(event), flush=True)
                save(output / "status.json", dict(state="running", runtime=runtime, kind=kind,
                    started_utc=started_utc, log_path=str(output / f"{runtime}-{kind}.log"),
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
        if owner.is_file() and json.loads(owner.read_text()).get("runner") == "posting_adaptive_frontier":
            save(output / f"failure-{time.time_ns()}.json",
                 dict(stage=args.stage, error=str(error), state="failed"))
        raise
