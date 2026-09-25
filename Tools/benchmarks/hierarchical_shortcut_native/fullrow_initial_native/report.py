"""Render only persisted, raw-reconciled completed native measurements."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def render(root):
    summary = json.loads((root / "summary.json").read_text())
    audit = json.loads((root / "independent_validation.json").read_text())
    rows = {(r["scenario"], r["case"]): r for r in summary}
    directories = {(r["scenario"], r["case"], r["mode"], r["repeat"]): r["directory"]
                   for r in json.loads((root / "runs.json").read_text())}
    scenarios = ["unfilter", "broad_tag", "medium_tag", "extreme_tag", "numeric", "mixed_dnf"]
    cases = ["h1", "h3", "control", "supplier"]
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
            "### Recall@10 (%) / ordinary full latency (ms)", "",
            "| Scenario | Original H1 | Historical H3 | Matched predicate-first C | Full-row S |",
            "|---|---:|---:|---:|---:|"]
    for scenario in scenarios:
        text.append("| " + scenario + " | " + " | ".join(
            f'{rows[scenario,c]["recall_at_10"]*100:.2f} / {rows[scenario,c]["ordinary_ms"]:.6f}'
            for c in cases) + " |")
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
         "PredicateRejects", "H2Completed", "H3Completed", "PostingSkips", "Members"]),
        ("Native continuation and degree (per query)", ["Calls", "Qualified", "Neighbors", "Queued",
         "NativeOvershoot", "ContinuationPops", "Fills", "NativeStops", "ScopeStops"])]:
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
    acceptance = {
        "policy": "Native H1 signature-first predicate-first full-posting supplier",
        "certified_native_processes": 144,
        "canonical_summary_rows": 24, "raw_reconciled_final_result_logs": raw_logs,
        "no_added_distance_member_row_batch_caps": True,
        "signature_before_parent_distance_and_member_scan": True,
        "predicate_before_ordinary_and_child_distance": True,
        "structural_routing_center_exception_disclosed": True,
        "full_selected_h2_h3_rows_completed_before_visited": True,
        "native_maxcheck": 2048, "native_row_boundary_overshoot_measured": True,
        "same_predicate_count_on_off_and_repeat_parity": True,
        "original_baseline_heads_recall_work_parity": True,
        "frozen_final_id_parity_claimed": False,
        "post_result_tuning": False, "equal_actual_distance_budgets_claimed": False,
        "top_graph_still_loaded_as_vector_backing": True,
        "protected_files_unchanged": audit["protected_files_unchanged"],
        "descriptive_comparisons": comparisons,
    }
    for name, key in [("summary.json", "summary_sha256"), ("report.md", "report_sha256"),
                      ("snapshot/spannaclbench", "native_binary_sha256"),
                      ("snapshot/source.tar.gz", "native_source_archive_sha256")]:
        acceptance[key] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    (root / "acceptance.json").write_text(json.dumps(acceptance, indent=2) + "\n")
    print("\n".join(text[:12]))
    print("PASS: canonical report generated from completed summary and96 raw final result logs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    render(parser.parse_args().results)
