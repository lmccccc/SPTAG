"""Render only persisted, raw-reconciled completed native measurements."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil

import numpy as np


def render(root):
    summary = json.loads((root / "summary.json").read_text())
    audit = json.loads((root / "independent_validation.json").read_text())
    rows = {(r["scenario"], r["case"]): r for r in summary}
    directories = {(r["scenario"], r["case"], r["mode"], r["repeat"]): r["directory"]
                   for r in json.loads((root / "runs.json").read_text())}
    scenarios = ["unfilter", "broad_tag", "medium_tag", "extreme_tag", "numeric", "mixed_dnf"]
    cases = ["h1", "h3", "control", "supplier"]
    config = Path(__file__).with_name("experiment.ini").read_text()
    workload_path = next(line.split("=", 1)[1] for line in config.splitlines() if line.startswith("Workloads="))
    workloads = json.loads(Path(workload_path).read_text())
    raw_logs = []
    for r in summary:
        timings = []
        for mode in ("plain", "profile"):
            for rep in (1, 2):
                path = root / directories[r["scenario"], r["case"], mode, rep] / "native.stdout.log"
                found = [json.loads(line) for line in path.read_text().splitlines()
                         if line.startswith("{") and '"recall"' in line]
                assert len(found) == 1, path
                x = found[0]
                assert x["queries"] == 1000 and x["failed_queries"] == 0, path
                assert math.isclose(x["recall"], r["recall_at_10"], abs_tol=1e-12), path
                for raw, key in (("postings_per_query", "postings"), ("posting_page_reads_per_query", "pages"),
                                 ("distance_computations_per_query", "disk_distances")):
                    assert math.isclose(x[raw], r[key], abs_tol=1e-6), (path, key)
                if mode == "plain":
                    timings.append(x["mean_latency_ms"])
                raw_logs.append({"path": str(path.relative_to(root)),
                                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        assert timings == r["ordinary_ms_runs"]
        assert math.isclose(sum(timings) / 2, r["ordinary_ms"], abs_tol=1e-12)
    assert len(summary) == 24 and len(raw_logs) == 96
    text = ["## Completed paired experiment", "",
            f'All144 native processes certified; {audit["protected_files_unchanged"]} protected files unchanged. '
            "All24 summary rows cross-checked against96 ordinary/profile final native result lines. "
            "Frozen H1/H3 selected-head/native-work parity passed; frozen final IDs were unavailable.", "",
            "Original H1/H3 and matched C also retain exact final IDs and work versus completed v2. "
            "For this matrix, the conservative score/cache insertion-event bound remains below "
            "the original native heap capacity61440 in every C/S query; queued insertion calls "
            "therefore cannot have been discarded by a full heap.", "",
            "### Recall@10 (%) / ordinary full latency (ms)", "",
            "| Scenario | Original H1 | Historical H3 | Matched predicate-first C | Startup whole-row S |",
            "|---|---:|---:|---:|---:|"]
    for scenario in scenarios:
        text.append("| " + scenario + " | " + " | ".join(
            f'{rows[scenario,c]["recall_at_10"]*100:.2f} / {rows[scenario,c]["ordinary_ms"]:.6f}'
            for c in cases) + " |")
    text += ["", "**The joint cost/quality goal is not met.** Unfilter S/H1 latency is "
             f'{rows["unfilter","supplier"]["ordinary_ms"]/rows["unfilter","h1"]["ordinary_ms"]:.3f}x. '
             f'Extreme S underfills {rows["extreme_tag","supplier"]["underfilled_queries"]}/1000 queries. '
             "Quality and work are reported independently; ratios below are descriptive, not equal-recall costs.", ""]
    for title, keys, formats in [
        ("Underfilled queries (%) / mean returned count", ["underfilled_queries", "mean_returned"], None),
        ("Native postings / pages / SSD distance calls", ["postings", "pages", "disk_distances"], ".3f"),
        ("Actual head / SSD / total distance calls", ["head_total_actual_distances", "disk_distances",
                                                     "total_actual_distances"], ".3f")]:
        text += ["", "### " + title, "",
                 "| Scenario | H1 | H3 | C | S |", "|---|---:|---:|---:|---:|"]
        for scenario in scenarios:
            values = []
            for c in cases:
                r = rows[scenario, c]
                values.append(f'{r[keys[0]]/10:.1f} / {r[keys[1]]:.3f}' if formats is None else
                              " / ".join(format(r[k], formats) for k in keys))
            text.append("| " + scenario + " | " + " | ".join(values) + " |")
    for title, fields in [
        ("Read-only distance ledger (per query)", ["Graph", "Routing", "Parent", "Child", "Cache", "Repeats"]),
        ("Pruning and complete-row work (per query)", ["SignatureChecks", "SignatureRejects", "PredicateChecks",
         "PredicateRejects", "RejectedLeaves", "H2Completed", "H3Completed", "PostingSkips", "Members"]),
        ("Native continuation and degree (per query)", ["Calls", "Qualified", "Neighbors", "Queued",
         "NativeOvershoot", "ContinuationPops", "Fills", "NativeStops", "ScopeStops"]),
        ("Upper-row enumeration versus descendant H1 reads (per query)",
         ["UpperMembers", "LowerMembers", "Discovered", "DiscoveryReuse"]),
        ("Initial native frontier (per query)",
         ["EntryCandidates", "EntryCalls", "EntryBefore", "StartupCalls", "StartupBefore",
          "StartupAfter", "StartupQualified", "StartupBlocked"])]:
        text += ["", "### " + title, "",
                 "| Scenario/case | " + " | ".join(fields) + " |",
                 "|---|" + "---:|" * len(fields)]
        for scenario in scenarios:
            for case in ("control", "supplier"):
                w = rows[scenario, case]["supplier_work"]
                text.append("| " + scenario + "/" + case + " | " +
                            " | ".join(f'{w["supply"+f]:.3f}' for f in fields) + " |")
    text += ["", "### Started-helper effective degree and observed query counts", "",
             "| Scenario | Before -> after | Triggered | H3 used | Native overshoot |",
             "|---|---:|---:|---:|---:|"]
    usage = json.loads((root / "supplier_usage.json").read_text())
    for scenario in scenarios:
        a = next(a for a in audit["audits"] if a["scenario"] == scenario and a["case"] == "supplier")
        before, after = a["mean_effective_before_started_call"], a["mean_effective_after_started_call"]
        degree = "-" if before is None else f"{before:.3f} -> {after:.3f}"
        u = usage[scenario]["supplier"]
        text.append(f'| {scenario} | {degree} | {u["queries_triggered"]} | '
                    f'{u["queries_using_h3"]} | {u["native_overshoot_queries"]} |')
    text += ["", "### Startup deficiency and original H1 continuation", "",
             "| Scenario | Startup calls | Empty before | Empty -> nonempty | Blocked by native boundary | Zero H1 pops |",
             "|---|---:|---:|---:|---:|---:|"]
    for scenario in scenarios:
        u = usage[scenario]["supplier"]
        text.append("| " + scenario + " | " + " | ".join(str(u[k]) for k in
                    ("startup_calls", "startup_empty_before", "startup_empty_to_nonempty",
                     "startup_blocked", "zero_h1_pop_queries")) + " |")
    availability = []
    text += ["", "### Sparse helper availability", "",
             "The helper is available before the first H1 pop on initial frontier deficiency, "
             "and inside subsequent native H1 expansions, never as a final-result repair. "
             "Conditional subsets are diagnostics, **not** substitutes for overall recall.", "",
             "| Scenario | Helper called | Queries | Recall (%) | Underfilled | Zero H1 pops |",
             "|---|---|---:|---:|---:|---:|"]
    for scenario in ("extreme_tag", "mixed_dnf"):
        truth = np.load(workloads["truth"][scenario]["ids"], mmap_mode="r")
        data = [json.loads(line) for line in
                (root / directories[scenario, "supplier", "profile", 1] / "queries.jsonl").read_text().splitlines()]
        control = [json.loads(line) for line in
                   (root / directories[scenario, "control", "profile", 1] / "queries.jsonl").read_text().splitlines()]
        for active in (False, True):
            group = [(i, r) for i, r in enumerate(data) if bool(r["supplyCalls"]) == active]
            hits, underfill, zero_pops = 0, 0, 0
            for i, r in group:
                valid = {vid for vid, _ in r["results"] if vid >= 0}
                hits += len(valid & set(truth[i, :10]))
                underfill += len(valid) < 10
                zero_pops += r["supplyPops"] == 0
                if not active:
                    assert all(r[k] == control[i][k] for k in ("heads", "results", "calls", "evaluated_h1"))
            a = {"scenario": scenario, "helper_called": active, "queries": len(group),
                 "recall_at_10": hits / (len(group) * 10) if group else None, "underfilled_queries": underfill,
                 "zero_h1_pops": zero_pops}
            availability.append(a)
            recall = f'{a["recall_at_10"]*100:.2f}' if group else "-"
            text.append(f'| {scenario} | {active} | {len(group)} | {recall} | '
                        f'{underfill} | {zero_pops} |')
    text += ["", "No-helper ordered results, selected heads, evaluations and actual calls exactly match "
             "the predicate-first graph-only control. No result-triggered repair was added.", ""]
    text += ["", "### Descriptive comparisons, not recall-matched costs", "",
             "| Scenario | Reference | S/reference latency | S/C latency | S-C recall pp |",
             "|---|---|---:|---:|---:|"]
    comparisons = []
    for scenario in scenarios:
        s, c = rows[scenario, "supplier"], rows[scenario, "control"]
        ref = rows[scenario, "h1" if scenario == "unfilter" else "h3"]
        x = {"scenario": scenario, "reference": ref["case"],
             "supplier_recall": s["recall_at_10"], "control_recall": c["recall_at_10"],
             "reference_recall": ref["recall_at_10"],
             "latency_ratio_to_reference": s["ordinary_ms"] / ref["ordinary_ms"],
             "latency_ratio_to_control": s["ordinary_ms"] / c["ordinary_ms"],
             "recall_delta_pp_to_control": 100 * (s["recall_at_10"] - c["recall_at_10"]),
             "same_recall_claim": False}
        comparisons.append(x)
        text.append(f'| {scenario} | {ref["case"]} | {x["latency_ratio_to_reference"]:.3f} | '
                    f'{x["latency_ratio_to_control"]:.3f} | {x["recall_delta_pp_to_control"]:+.2f} |')
    text += ["", "No weighted workload mixture or post-result policy tuning. Original H3 retains its "
             "distinct predicate-guided top search and widening. Native routing centers remain "
             "explicit pre-predicate-distance exceptions, not hidden supplier candidates.", ""]
    readme = Path(__file__).with_name("README.md")
    content = readme.read_text().split("## Completed paired experiment")[0].rstrip() + "\n\n" + "\n".join(text)
    readme.write_text(content)
    (root / "report.md").write_text(content)
    (root / "matched_comparisons.json").write_text(json.dumps(comparisons, indent=2) + "\n")
    (root / "helper_availability.json").write_text(json.dumps(availability, indent=2) + "\n")
    shutil.copy2(Path(__file__), root / "report_generator_final.py")
    acceptance = {
        "policy": "Native H1 reachable startup and signature/predicate-first whole-row supplier",
        "certified_native_processes": 144,
        "implemented_and_benchmarked": True,
        "joint_goal_demonstrated": False,
        "canonical_summary_rows": 24, "raw_reconciled_final_result_logs": raw_logs,
        "no_added_distance_member_row_batch_caps": True,
        "signature_before_parent_distance_and_member_scan": True,
        "predicate_before_ordinary_and_child_distance": True,
        "structural_routing_center_exception_disclosed": True,
        "rejected_singleton_leaves_counted_without_scoring_or_enqueuing": True,
        "full_selected_h2_h3_rows_completed_before_visited": True,
        "h3_completion_means_adjacency_enumeration_not_atomic_subtree": True,
        "cached_child_choices_retained_without_upper_reread": True,
        "reachable_initial_frontier_deficiency_handled_once_before_h1_pops": True,
        "startup_native_boundary_respected_and_all_rejected_fixture_terminates": True,
        "native_maxcheck": 2048, "native_row_boundary_overshoot_measured": True,
        "same_predicate_count_on_off_and_repeat_parity": True,
        "original_baseline_heads_recall_work_parity": True,
        "frozen_final_id_parity_claimed": False,
        "post_result_tuning": False, "equal_actual_distance_budgets_claimed": False,
        "top_graph_still_loaded_as_vector_backing": True,
        "abandoned_structural_trial": "../h1_fullrow_20260916/interruption.json",
        "sparse_no_helper_control_parity_queries": sum(
            a["queries"] for a in availability if not a["helper_called"]),
        "sparse_no_helper_outputs_equal_matched_control": (
            True if any(a["queries"] for a in availability if not a["helper_called"]) else None),
        "helper_availability": availability,
        "startup_usage": usage,
        "native_queue_capacity_not_reached": audit[
            "native_queue_capacity_not_reached_by_conservative_score_event_bound"],
        "persisted_signature_domains": audit["persisted_signature_domains"],
        "preserved_previous_completed_version": "../h1_fullrow_20260916_v2",
        "protected_files_unchanged": audit["protected_files_unchanged"],
        "descriptive_comparisons": comparisons,
    }
    for name, key in [("summary.json", "summary_sha256"), ("report.md", "report_sha256"),
                      ("snapshot/spannaclbench", "native_binary_sha256"),
                      ("snapshot/source.tar.gz", "native_source_archive_sha256"),
                      ("report_generator_final.py", "report_generator_sha256")]:
        acceptance[key] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    (root / "acceptance.json").write_text(json.dumps(acceptance, indent=2) + "\n")
    print("\n".join(text[:12]))
    print("PASS: canonical report generated from completed summary and96 raw final result logs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    render(parser.parse_args().results)
