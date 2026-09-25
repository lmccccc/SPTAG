#!/usr/bin/env python3
"""Audit native supplier work, exact own points, parent aliases and saved outcomes."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import statistics
import struct

import numpy as np

spec = importlib.util.spec_from_file_location("supplier_runner", Path(__file__).with_name("run.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
require = runner.require


def verify(config):
    plan = runner.read_ini(config)["Experiment"]
    root = Path(plan["OutputDirectory"])
    status = json.loads((root / "status.json").read_text())
    require(status["state"] == "completed" and status["native_runs"] == 168, "Incomplete matrix")
    runs = json.loads((root / "runs.json").read_text())
    dirs = {(r["scenario"], r["case"], r["mode"], r["repeat"]): root / r["directory"] for r in runs}
    workloads = json.loads(Path(plan["Workloads"]).read_text())
    attrs = np.load(workloads["attributes"], mmap_mode="r")
    io = json.loads((dirs["unfilter", "h1", "plain", 1] / "native.io.json").read_text())
    mapfile = Path(io["target"]).parent / "SPTAGHeadVectorIDs.bin"
    with mapfile.open("rb") as f:
        require(struct.unpack("<ii", f.read(8)) == (160091, 1), "Invalid canonical VID map")
    vids = np.fromfile(mapfile, dtype="<u8", offset=8)
    require(len(vids) == len(set(vids)) == 160091, "Invalid canonical identities")
    summary = json.loads((root / "summary.json").read_text())
    reference_traces, audits = {}, []
    for row in summary:
        scenario, case = row["scenario"], row["case"]
        custom = case.startswith(("supplier", "control"))
        mask = (np.ones(len(attrs), dtype=bool) if scenario == "unfilter" else
                runner.predicate_mask(workloads["predicates"][scenario], attrs))
        truth = np.load(workloads["truth"][scenario]["ids"], mmap_mode="r")
        data = runner.rows(dirs[scenario, case, "profile", 1] / "queries.jsonl")
        quality = runner.query_quality(data, truth, mask)
        require(all(row[k] == v for k, v in quality.items()), "Summary quality mismatch")
        for repeat in (1, 2):
            for mode in ("plain", "profile"):
                directory = dirs[scenario, case, mode, repeat]
                comparison = runner.rows(directory / "queries.jsonl")
                if mode == "profile":
                    require(comparison == data, "Profile repeat changed ordered results or work")
                else:
                    fields = ("heads", "results", "own_ids", "evaluated_h1", "parent_h1",
                              "qualifications", "degree_audits")
                    require(all(all(a[k] == b[k] for k in fields) and
                                all(a[k] == b[k] for k in a if k.startswith("supply"))
                                for a, b in zip(comparison, data)), "Count-off parity failure")
                require(json.loads((directory / "native.io.json").read_text())["direct_io"],
                        "Not native O_DIRECT posting access")
        parent_own, parent_true, outside_true, own_counts = [], [], [], []
        for q, r in enumerate(data):
            own = set(r["own_ids"])
            require(len(own) == len(r["own_ids"]) and len(own) <= 10, "Invalid own heap")
            require(all(mask[id] for id in own), "Own point violates exact predicate")
            selected = {int(vids[h]) for h, _ in r["heads"] if h >= 0}
            returned = {int(id) for id, _ in r["results"] if id >= 0}
            outside_true.append(len((own - selected) & returned & set(truth[q, :10])))
            own_counts.append(len(own))
            if not custom:
                continue
            pairs = r["evaluated_h1"]
            require(len(r["qualifications"]) == len(pairs), "Missing native eligibility observations")
            require(all(0 <= flag <= 3 and bool(flag & 2) == bool(mask[vids[h]])
                        for (h, _), flag in zip(pairs, r["qualifications"])),
                    "Own eligibility disagrees with the exact dataset predicate")
            evaluated = {h: d for h, d in pairs}
            require(len(evaluated) == len(pairs) == r["supplyCandidates"], "Lost/repeated H1 admission")
            parents = set(r["parent_h1"])
            require(len(parents) == len(r["parent_h1"]) == r["supplyParent"] and
                    parents <= evaluated.keys(), "Parent scoring lost canonical H1 identities")
            eligible_own = sorted((d, int(vids[h])) for h, d in pairs if mask[vids[h]])[:10]
            require(own == {id for _, id in eligible_own},
                    "Native own heap is not nearest exact matches among all evaluated H1/parents")
            parent_vids = {int(vids[h]) for h in parents}
            parent_own.append(len(own & parent_vids))
            parent_true.append(len(own & returned & parent_vids & set(truth[q, :10])))
            budget = 2000 if case.endswith("2000") else 3200
            require(r["calls"] == r["supplyGraph"] + r["supplyParent"] + r["supplyChild"] <= budget,
                    "Unified actual ledger/cap failure")
            require(r["calls"] == r["supplyCandidates"] + r["supplyRepeats"], "Repeated-call ledger failure")
            require(r["supplyStarts"] == 1 and r["supplyReturns"] == r["supplyCalls"] and
                    r["supplyMaxQualified"] <= 16 and r["supplyMembers"] <= 2048 and
                    r["supplyQueued"] <= r["supplyNeighbors"], "Continuation/helper bound failure")
            require(r["supplyEffectiveBefore"] <= r["supplyRawBefore"] and
                    r["supplyEffectiveAfter"] == r["supplyEffectiveBefore"] + r["supplyQualified"],
                    "Effective degree accounting failure")
            da = r["degree_audits"]
            require(len(da) == r["supplyCalls"] and sum(x[2] for x in da) == r["supplyRequested"]
                    and sum(x[3] for x in da) == r["supplyQualified"]
                    and sum(x[4] for x in da) == r["supplyNeighbors"], "Per-call quota ledger mismatch")
            require(all(x[0] >= x[1] and x[1] < 16 and x[2] == 16 - x[1] and
                        x[3] <= x[2] and x[3] <= x[4] and x[5] <= x[4] and
                        x[6] <= 2048 and ((x[7] == 0) == (x[3] == x[2])) for x in da),
                    "Ineligible/duplicate candidates filled slots or deficit was exceeded")
            require(r["supplyAscents"] <= r["supplyH2Exhausted"] == sum(x[8] for x in da),
                    "Ascent without genuinely exhausted H2 scope")
            require(r["supplyFills"] + r["supplyDistanceStops"] + r["supplyMemberStops"] +
                    r["supplyNativeStops"] + r["supplyScopeStops"] == r["supplyCalls"],
                    "Unclassified supplier stop")
            if case.startswith("control"):
                require(r["supplyCalls"] == r["supplyParent"] == r["supplyChild"] == 0,
                        "Supplementation leaked into matched control")
        if case.startswith("control"):
            keys = ("supplyTrace", "evaluated_h1", "parent_h1", "calls", "csr_assignments",
                    "supplyPops", "supplyExpansions", "supplySentinels", "supplyTrees",
                    "supplyCalls", "supplyNeighbors", "supplyH2Rows", "supplyH3Rows")
            trace = [[r[k] for k in keys] for r in data]
            if case in reference_traces:
                require(trace == reference_traces[case], "Predicate influenced native spatial traversal")
            reference_traces[case] = trace
        calls = [x for r in data for x in r["degree_audits"]]
        flags = [flag for r in data for flag in r["qualifications"]]
        audits.append({"scenario": scenario, "case": case, **quality,
                       "mean_effective_before_started_call": statistics.mean(x[1] for x in calls) if calls else None,
                       "mean_effective_after_started_call": statistics.mean(x[1] + x[3] for x in calls) if calls else None,
                       "mean_requested_per_started_call": statistics.mean(x[2] for x in calls) if calls else None,
                       "mean_qualified_per_started_call": statistics.mean(x[3] for x in calls) if calls else None,
                       "qualification_counts": {"posting_only": flags.count(1), "own_only": flags.count(2),
                                                "both": flags.count(3), "neither": flags.count(0)},
                       "mean_native_own_heap": statistics.mean(own_counts),
                       "true_own_neighbors_outside_selected_postings": sum(outside_true),
                       "mean_parent_first_own_heap_points": statistics.mean(parent_own) if parent_own else 0,
                       "true_final_neighbors_from_parent_first_own_points": sum(parent_true),
                       "queries_triggering_supplier": sum(r["supplyCalls"] > 0 for r in data),
                       "queries_using_h3_supply": sum(r["supplyH3Rows"] > 0 for r in data)})
    fixtures = json.loads((root / "native_fixtures.json").read_text())
    require(len(fixtures) == 48 and all(r["filter_violations"] == 0 for r in fixtures),
            "Incomplete native full-path fixtures")
    protected = json.loads((root / "provenance.json").read_text())["protected"]
    require(all(runner.fingerprint(p["path"]) == p for p in protected), "Protected previous evidence changed")
    saved = root / "validation_sources"
    if saved.exists():
        saved = root / "validation_sources_final"
        require(not saved.exists(), "Final validation snapshot already exists")
        shutil.copy2(root / "independent_validation.json", root / "independent_validation_initial.json")
    shutil.copytree(config.parent, saved, ignore=shutil.ignore_patterns("__pycache__"))
    runner.write_json(root / "independent_validation.json", {
        "certified_native_processes": 216, "scenario_cases": 36,
        "own_heap_equals_exact_top10_of_all_evaluated_h1": True,
        "parent_representative_identity_and_admission_verified": True,
        "native_frontier_single_start_and_helper_bounds_verified": True,
        "predicate_invariant_supplier_traces_required": False,
        "same_predicate_repeated_spatial_traces_equal": True,
        "qualified_degree_and_exact_deficit_audits_passed": True,
        "counted_uncounted_ordered_results_equal": True,
        "protected_files_unchanged": len(protected), "audits": audits,
        "sources": [runner.fingerprint(p) for p in sorted(saved.iterdir()) if p.is_file()]})
    print("PASS: 216 native processes,36 cases, exact own heaps including parent representatives, "
          "same-predicate/count parity, degree16, single native H1 traversal, bounded supply, "
          f"{len(protected)} protected files unchanged")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    verify(parser.parse_args().config)
