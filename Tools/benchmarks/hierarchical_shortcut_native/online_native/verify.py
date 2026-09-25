#!/usr/bin/env python3
"""Independent online-policy, candidate admission, exact-filter and provenance audit."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import statistics
import struct

import numpy as np

spec = importlib.util.spec_from_file_location("online_matrix_runner", Path(__file__).with_name("run.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def verify(config):
    config = config.resolve()
    plan = runner.read_ini(config)["Experiment"]
    root = Path(plan["OutputDirectory"])
    status = json.loads((root / "status.json").read_text())
    runner.require(status["state"] == "completed" and status["native_runs"] == 168,
                   "Incomplete online matrix")
    records = json.loads((root / "runs.json").read_text())
    directories = {(r["scenario"], r["case"], r["mode"], r["repeat"]):
                   root / r["directory"] for r in records}
    workloads = json.loads(Path(plan["Workloads"]).read_text())
    attrs = np.load(workloads["attributes"], mmap_mode="r")
    example = directories["unfilter", "h1", "plain", 1]
    io = json.loads((example / "native.io.json").read_text())
    map_path = Path(io["target"]).parent / "SPTAGHeadVectorIDs.bin"
    with map_path.open("rb") as stream:
        runner.require(struct.unpack("<ii", stream.read(8)) == (160091, 1), "Wrong native map header")
    head_ids = np.fromfile(map_path, dtype="<u8", offset=8)
    runner.require(len(head_ids) == 160091 and len(set(head_ids)) == 160091,
                   "Wrong canonical head map")
    head_set = set(int(n) for n in head_ids)
    summary = json.loads((root / "summary.json").read_text())
    audits = []
    trace_reference = {}
    for row in summary:
        scenario, case = row["scenario"], row["case"]
        truth = np.load(workloads["truth"][scenario]["ids"], mmap_mode="r")
        mask = (np.ones(len(attrs), dtype=bool) if scenario == "unfilter" else
                runner.predicate_mask(workloads["predicates"][scenario], attrs))
        reference = runner.rows(directories[scenario, case, "profile", 1] / "queries.jsonl")
        q = runner.query_quality(reference, truth, mask)
        runner.require(all(row[k] == v for k, v in q.items()), "Summary quality mismatch")
        own_outside_postings, extra_true, own_counts = [], [], []
        for i, r in enumerate(reference):
            own = set(r["own_ids"])
            runner.require(len(own) == len(r["own_ids"]) and len(own) <= 10 and own <= head_set,
                           "Invalid native own-point heap")
            runner.require(all(mask[vid] for vid in own), "Own-point exact predicate failed")
            selected = {int(head_ids[h]) for h, _ in r["heads"] if h >= 0}
            returned = {int(vid) for vid, _ in r["results"] if vid >= 0}
            outside = (own - selected) & returned
            own_outside_postings.append(len(outside))
            extra_true.append(len(outside & set(truth[i, :10])))
            own_counts.append(len(own))
        for repeat in range(1, 3):
            for mode in ("plain", "profile"):
                directory = directories[scenario, case, mode, repeat]
                data = runner.rows(directory / "queries.jsonl")
                if mode == "profile":
                    runner.require(data == reference, "Actual online work/result nondeterminism")
                else:
                    keys = ("heads", "results", "own_ids", "onlineTrace")
                    runner.require(all(all(a[k] == b[k] for k in keys) for a, b in zip(data, reference)),
                                   "Profile/off candidate-admission parity failed")
                runner.require(json.loads((directory / "native.io.json").read_text())["direct_io"],
                               "Posting FD is not native O_DIRECT")
        if case.startswith(("admit", "online")):
            fields = ("onlineTrace", "calls", "onlineGraph", "onlineParent", "onlineChild",
                      "onlineCandidates", "onlineCache", "csr_assignments", "onlineBatches")
            traces = [[r[k] for k in fields] for r in reference]
            if case in trace_reference:
                runner.require(traces == trace_reference[case], "Predicate changed traversal/callback charges")
            trace_reference[case] = traces
            budget = 2000 if case.endswith("2000") else 3200
            runner.require(all(r["calls"] <= budget and
                               r["calls"] == r["onlineGraph"] + r["onlineParent"] + r["onlineChild"]
                               and r["onlineCandidates"] <= r["calls"] for r in reference),
                           "Unified budget accounting failed")
            if case.startswith("admit"):
                runner.require(all(r["onlineParent"] == r["onlineChild"] == r["onlineRowsH3"] == 0
                                   for r in reference), "Hierarchy present in matched control")
            else:
                runner.require(any(r["onlineUpH2"] > 0 and r["onlineRowsH3"] > 0 for r in reference),
                               "Full H2/H3 online path unexercised")
        audits.append({"scenario": scenario, "case": case, **q,
            "mean_pre_ssd_own_heap": statistics.mean(own_counts),
            "queries_returning_own_points_outside_selected_posting_heads":
                sum(n > 0 for n in own_outside_postings),
            "mean_returned_own_points_outside_selected_posting_heads": statistics.mean(own_outside_postings),
            "true_neighbors_from_own_heap_outside_selected_heads": sum(extra_true)})
    runner.require(any(a["case"].startswith("admit") and a["scenario"] == "numeric" and
                       a["true_neighbors_from_own_heap_outside_selected_heads"] > 0 for a in audits),
                   "Evaluated-own-point admission was not demonstrated")
    fixtures = json.loads((root / "native_fixtures.json").read_text())
    runner.require(len(fixtures) == 48 and all(r["filter_violations"] == 0 for r in fixtures),
                   "Incomplete native full-path fixtures")
    protected = json.loads((root / "provenance.json").read_text())["protected"]
    runner.require(all(runner.fingerprint(p["path"]) == p for p in protected),
                   "Prior controls or protected inputs changed")
    comparisons = []
    for r in summary:
        if not r["case"].startswith("online"):
            continue
        control = next(c for c in summary if c["scenario"] == r["scenario"] and
                       c["case"] == r["case"].replace("online", "admit"))
        comparisons.append({"scenario": r["scenario"], "online": r["case"], "control": control["case"],
            "recall_delta_pp": 100 * (r["recall_at_10"] - control["recall_at_10"]),
            "latency_ratio": r["ordinary_ms"] / control["ordinary_ms"],
            "underfill_delta_queries": r["underfilled_queries"] - control["underfilled_queries"]})
    saved = root / "validation_sources"
    shutil.copytree(config.parent, saved, ignore=shutil.ignore_patterns("__pycache__"))
    runner.write_json(root / "independent_validation.json", {
        "native_certified_matrix_runs": 168, "native_fixture_runs": 48,
        "predicate_invariant_online_and_matched_control": True,
        "all_actual_callback_and_profile_result_parity": True,
        "protected_files_unchanged": len(protected), "own_point_audits": audits,
        "online_vs_matched_admission_control": comparisons,
        "policy": "Online query-ranked eight direct owners with bounded CSR cursors; not the fixed overlay",
        "sources": [runner.fingerprint(p) for p in sorted(saved.iterdir()) if p.is_file()]})
    print(f"PASS: 216 certified native processes, 36 scenario/case qualities, "
          f"own-point admission outside selected heads, unified budgets, H3 use, "
          f"predicate invariance, {len(protected)} protected files unchanged")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    verify(parser.parse_args().config)
