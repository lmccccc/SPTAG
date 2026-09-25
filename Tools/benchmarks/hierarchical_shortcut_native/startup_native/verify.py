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


def categorical_anchors(predicate):
    if "categorical_eq" in predicate:
        col, tag = predicate["categorical_eq"]
        return {tag} if col == 0 else set()
    if "or" in predicate:
        parts = [categorical_anchors(p) for p in predicate["or"]]
        return set().union(*parts) if all(parts) else set()
    if "and" in predicate:
        return set().union(*(categorical_anchors(p) for p in predicate["and"]))
    return set()


def verify(config):
    plan = runner.read_ini(config)["Experiment"]
    root = Path(plan["OutputDirectory"])
    require("PASS:" in (root / "snapshot/supplier-tests-pass.log").read_text(),
            "Focused native fixtures did not complete")
    status = json.loads((root / "status.json").read_text())
    require(status["state"] == "completed" and status["native_runs"] == 120, "Incomplete matrix")
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
    layers = []
    for name in ("second_level_head_postings.bin", "second_level_head_postings.bin.level2"):
        path = mapfile.parent / name
        with path.open("rb") as f:
            header = struct.unpack("<Q8I2d6Q", f.read(104))
        require(header[1:3] == (3, 104) and header[5:8] == (8, 4, 32),
                "Invalid original eight-replica CSR header")
        upper, member_count = header[4], header[11]
        offsets = np.fromfile(path, dtype="<u8", offset=104, count=upper + 1)
        start = 104 + 8 * (upper + 1)
        members = np.fromfile(path, dtype="<u4", offset=start, count=member_count)
        signatures = np.fromfile(path, dtype="<u8", offset=start + 4 * member_count).reshape(upper, 4)
        layers.append((header, offsets, members, signatures))
    summary = json.loads((root / "summary.json").read_text())
    previous = Path(plan["PriorResults"])
    previous_dirs = {(r["scenario"], r["case"], r["mode"], r["repeat"]): previous / r["directory"]
                     for r in json.loads((previous / "runs.json").read_text())}
    audits = []
    for row in summary:
        scenario, case = row["scenario"], row["case"]
        custom = case.startswith(("supplier", "control"))
        mask = (np.ones(len(attrs), dtype=bool) if scenario == "unfilter" else
                runner.predicate_mask(workloads["predicates"][scenario], attrs))
        truth = np.load(workloads["truth"][scenario]["ids"], mmap_mode="r")
        anchors = categorical_anchors(workloads["predicates"].get(scenario, {}))
        if any(not (layer[0][9] < np.mean(attrs[:, 0] == tag) <= layer[0][10])
               for layer in layers for tag in anchors):
            anchors = set()
        own_row_matches = []
        lower_matches = mask[vids]
        for header, offsets, members, signatures in layers:
            lower_matches = np.array([np.any(lower_matches[members[offsets[i]:offsets[i + 1]]])
                                      for i in range(header[4])])
            own_row_matches.append(lower_matches)
        data = runner.rows(dirs[scenario, case, "profile", 1] / "queries.jsonl")
        if case in ("h1", "h3", "control"):
            old = runner.rows(previous_dirs[scenario, case, "profile", 1] / "queries.jsonl")
            stable = ("heads", "results", "own_ids", "calls", "evaluated_h1", "parent_h1",
                      "qualifications", "graph_checked")
            require(len(old) == len(data) and
                    all(all(a[k] == b[k] for k in stable) for a, b in zip(old, data)),
                    "Startup change altered an unchanged original baseline or matched graph control")
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
                              "qualifications", "degree_audits", "row_audits", "signature_rejected",
                              "predicate_rejected", "entry_ids", "entry_scored", "discoveries", "startup_audits")
                    require(all(all(a[k] == b[k] for k in fields) and
                                all(a[k] == b[k] for k in a if k.startswith("supply"))
                                for a, b in zip(comparison, data)), "Count-off parity failure")
                require(json.loads((directory / "native.io.json").read_text())["direct_io"],
                        "Not native O_DIRECT posting access")
        parent_own, parent_true, outside_true, own_counts, pending_choices = [], [], [], [], []
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
            require(r["calls"] == r["supplyGraph"] + r["supplyRouting"] + r["supplyParent"] + r["supplyChild"],
                    "Unified actual ledger failure")
            require(r["calls"] == r["supplyCandidates"] + r["supplyRepeats"], "Repeated-call ledger failure")
            # Native filtered-search insertions consume a scored tree node or an edge score.
            require(r["calls"] + r["supplyCache"] < 30 * r["supplyNativeMaxCheck"],
                    "Native heap capacity could invalidate successful-insertion interpretation")
            require(r["supplyStarts"] == 1 and r["supplyReturns"] == r["supplyCalls"] and
                    r["supplyQueued"] <= r["supplyNeighbors"], "Continuation/helper bound failure")
            row_audits = r["row_audits"]
            scanned = {(x[0], x[1]) for x in row_audits}
            rejected = {tuple(x) for x in r["signature_rejected"]}
            require(len(scanned) == len(row_audits) == r["supplyH2Rows"] + r["supplyH3Rows"] and
                    all(x[2] == x[3] for x in row_audits) and
                    sum(x[2] for x in row_audits) == r["supplyMembers"] and
                    scanned.isdisjoint(rejected), "Posting rescan, partial scan or rejected row scanned")
            require(all(x[2] == int(layers[x[0] - 1][1][x[1] + 1] -
                                   layers[x[0] - 1][1][x[1]]) for x in row_audits),
                    "Recorded row scan differs from original complete CSR length")
            require(not rejected or bool(anchors), "Numeric/unanchored query falsely signature-pruned")
            for level, id in rejected:
                signature = layers[level - 1][3][id]
                require(not any(int(signature[(tag % 256) // 64]) & (1 << (tag % 64))
                                for tag in anchors), "Rejected row does not match original signature test")
                require(not own_row_matches[level - 1][id], "Signature pruned an exact matching own point")
            require(r["supplyH2Rows"] == r["supplyH2Completed"] and
                    r["supplyH3Rows"] == r["supplyH3Completed"] and
                    r["supplySignatureRejects"] == len(rejected), "Signature/completion ledger failure")
            require(r["supplyMembers"] == r["supplyUpperMembers"] + r["supplyLowerMembers"] and
                    r["supplyUpperMembers"] == sum(x[2] for x in row_audits if x[0] == 2) and
                    r["supplyLowerMembers"] == sum(x[2] for x in row_audits if x[0] == 1),
                    "Upper enumeration was conflated with descendant H1 reads")
            discoveries = {tuple(x) for x in r["discoveries"]}
            require(len(discoveries) == len(r["discoveries"]) == r["supplyDiscovered"],
                    "Lost or repeated H3 adjacency discovery")
            done_h2 = set()
            for level, id, length, processed in row_audits:
                if level == 1:
                    done_h2.add(id)
                    continue
                offsets, members = layers[1][1:3]
                expected = {int(h) for h in members[offsets[id]:offsets[id + 1]]
                            if int(h) not in done_h2 and (1, int(h)) not in rejected}
                require({h for upper, h in discoveries if upper == id} == expected,
                        "Whole upper row did not retain every valid unvisited child posting")
            pending_choices.append(sum(h not in done_h2 for _, h in discoveries))
            require(len(r["entry_ids"]) == len(set(r["entry_ids"])) == r["supplyEntryCandidates"] and
                    len(r["entry_scored"]) == r["supplyEntryCalls"],
                    "Missing original BKT entry evidence")
            entry_set = set(r["entry_ids"])
            require(all(id in entry_set for id, distance in r["entry_scored"]),
                    "Startup used an unreachable BKT identity")
            expected_anchor = (min(r["entry_scored"], key=lambda p: (p[1], p[0]))[0]
                               if r["entry_scored"] else (r["entry_ids"][0] if r["entry_ids"] else -1))
            sa = r["startup_audits"]
            if case == "supplier":
                require(r["supplyStartupChecks"] == 1 and
                        len(sa) == (r["supplyEntryBefore"] < 16) and
                        r["supplyStartupCalls"] + r["supplyStartupBlocked"] == len(sa),
                        "Initial frontier deficiency was not considered exactly once")
                if sa:
                    anchor, before, after, checked, final_checked, calls, qualified, row_count = sa[0]
                    require(anchor == expected_anchor and before == r["supplyEntryBefore"] and
                            after == r["supplyStartupAfter"] and qualified == r["supplyStartupQualified"],
                            "Wrong startup anchor or frontier accounting")
                    require(r["supplyStartupCalls"] == (anchor >= 0 and checked < 2048),
                            "Startup crossed native boundary or failed to run within it")
                    if r["supplyStartupCalls"]:
                        require(r["degree_audits"][0][1] == before,
                                "Startup supplied from the wrong entry degree")
                        require(final_checked >= checked and calls <= r["calls"] and
                                qualified <= r["supplyQualified"] and row_count <= r["supplyH2Rows"],
                                "Startup deltas exceed complete native query work")
                        if after and final_checked < 2048:
                            require(r["supplyContinuationPops"] > 0,
                                    "Seeded frontier did not resume original native H1 traversal")
                    else:
                        require(calls == qualified == row_count == 0,
                                "Blocked startup silently bypassed native MaxCheck")
            else:
                require(not sa and r["supplyStartupCalls"] == r["supplyStartupChecks"] == 0,
                        "Startup leaked into graph-only control")
            require(r["supplyNativeMaxCheck"] == 2048 and
                    r["supplyNativeOvershoot"] == max(0, r["graph_checked"] - 2048),
                    "Native MaxCheck/overshoot accounting mismatch")
            require(r["supplyRejectedLeaves"] <= min(2048, r["graph_checked"]),
                    "Rejected-leaf native checked accounting missing")
            require(r["supplyEffectiveBefore"] <= r["supplyRawBefore"] and
                    r["supplyEffectiveAfter"] == r["supplyEffectiveBefore"] + r["supplyQualified"],
                    "Effective degree accounting failure")
            da = r["degree_audits"]
            require(len(da) == r["supplyCalls"] and sum(x[2] for x in da) == r["supplyRequested"]
                    and sum(x[3] for x in da) == r["supplyQualified"]
                    and sum(x[4] for x in da) == r["supplyNeighbors"], "Per-call quota ledger mismatch")
            require(all(x[0] >= x[1] and x[1] < 16 and x[2] == 16 - x[1] and
                        x[3] <= x[4] and x[5] <= x[4] and
                        ((x[7] == 0) == (x[3] >= x[2])) for x in da),
                    "Invalid qualified-degree/full-row accounting")
            require(r["supplyAscents"] <= r["supplyH2Exhausted"] == sum(x[8] for x in da),
                    "Ascent without genuinely exhausted H2 scope")
            require(r["supplyFills"] + r["supplyNativeStops"] + r["supplyScopeStops"] == r["supplyCalls"],
                    "Unclassified supplier stop")
            if case.startswith("control"):
                require(r["supplyCalls"] == r["supplyParent"] == r["supplyChild"] == 0,
                        "Supplementation leaked into matched control")
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
        audits[-1]["mean_pending_discovered_choices"] = statistics.mean(pending_choices) if pending_choices else 0
        if custom:
            audits[-1]["max_native_queue_insertion_upper_bound"] = max(
                r["calls"] + r["supplyCache"] for r in data)
    fixtures = json.loads((root / "native_fixtures.json").read_text())
    require(len(fixtures) == 24 and all(r["filter_violations"] == 0 for r in fixtures),
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
        "certified_native_processes": 144, "scenario_cases": 24,
        "own_heap_equals_exact_top10_of_all_evaluated_h1": True,
        "parent_representative_identity_and_admission_verified": True,
        "native_frontier_single_start_and_full_posting_completion_verified": True,
        "startup_anchor_reachability_and_single_call_verified": True,
        "upper_row_not_atomic_descendant_subtree_verified": True,
        "complete_cached_upper_adjacency_verified": True,
        "previous_original_baselines_and_matched_control_exact_output_work_unchanged": True,
        "native_queue_capacity_not_reached_by_conservative_score_event_bound": True,
        "persisted_signature_domains": [list(layer[0][9:11]) for layer in layers],
        "persisted_signature_domains": [list(layer[0][9:11]) for layer in layers],
        "predicate_invariant_supplier_traces_required": False,
        "same_predicate_repeated_spatial_traces_equal": True,
        "qualified_degree_trigger_and_complete_row_audits_passed": True,
        "counted_uncounted_ordered_results_equal": True,
        "protected_files_unchanged": len(protected), "audits": audits,
        "sources": [runner.fingerprint(p) for p in sorted(saved.iterdir()) if p.is_file()]})
    print("PASS: 144 native processes,24 cases, exact own heaps including parent representatives, "
          "same-predicate/count parity, degree16 trigger, single native H1 traversal, complete postings, "
          f"{len(protected)} protected files unchanged")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    verify(parser.parse_args().config)
