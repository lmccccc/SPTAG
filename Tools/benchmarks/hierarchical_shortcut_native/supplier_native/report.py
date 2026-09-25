#!/usr/bin/env python3
"""Render measured supplier tables without mixing runtimes or selecting workload weights."""
import argparse
import hashlib
import json
from pathlib import Path


def render(root):
    summary = json.loads((root / "summary.json").read_text())
    validation = json.loads((root / "independent_validation.json").read_text())
    if validation["certified_native_processes"] != 216:
        raise ValueError("Cannot report an incomplete native experiment")
    rows = {(r["scenario"], r["case"]): r for r in summary}
    scenarios = ["unfilter", "broad_tag", "medium_tag", "extreme_tag", "numeric", "mixed_dnf"]
    cases = ["h1", "h3", "control2000", "supplier2000", "control3200", "supplier3200"]
    text = ["## Completed paired experiment", "",
            f"All216 native processes completed; {validation['protected_files_unchanged']} protected files "
            "retain their original hashes/size/mtime. Original H1/H3 match frozen selected heads/distances "
            "and native recall/work. Frozen final IDs were unavailable and are not claimed. "
            "Counted/uncounted ordered outputs match; all experimental spatial traces match across "
            "predicates. Native own heaps equal the exact nearest10 matches among **all** evaluated "
            "H1s, including parent-first representatives.", "",
            "### Final Recall@10 (%) / ordinary full latency (ms)", "",
            "| Scenario | H1 | H3 | C2000 | S2000 | C3200 | S3200 |",
            "|---|---:|---:|---:|---:|---:|---:|"]
    for scenario in scenarios:
        text.append("| " + scenario + " | " + " | ".join(
            f"{rows[scenario, case]['recall_at_10'] * 100:.2f} / "
            f"{rows[scenario, case]['ordinary_ms']:.6f}" for case in cases) + " |")
    text += ["", "`C` is the matched native BKT admission/budget control; `S` is synchronous supply. "
             "These are not the old manual-frontier G/O cases. Timings are two fresh ordinary "
             "repetitions, not profile timings or historical spliced values.", "",
             "### Underfilled queries (%)", "",
             "| Scenario | H1 | H3 | C2000 | S2000 | C3200 | S3200 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for scenario in scenarios:
        text.append("| " + scenario + " | " + " | ".join(
            f"{rows[scenario, case]['underfilled_queries'] / 10:.1f}" for case in cases) + " |")
    text += ["", "### Predicate-invariant native navigation and helper work", "",
             "Per-query means; upper work is supplier row choice, never an upper graph search. "
             "All parent distances also represent real, admitted canonical H1 candidates.", "",
             "| Case | Graph actual | Parent actual | Child actual | Distinct H1 | CSR entries | Calls | Fresh supplied | Enqueued | H2 rows | H3 rows |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    keys = ["supplyGraph", "supplyParent", "supplyChild", "supplyCandidates", "supplyMembers",
            "supplyCalls", "supplyNeighbors", "supplyQueued", "supplyH2Rows", "supplyH3Rows"]
    for case in cases[2:]:
        w = rows["unfilter", case]["supplier_work"]
        text.append("| " + case + " | " + " | ".join(f"{w[k]:.3f}" for k in keys) + " |")
    text += ["", "Caps are ceilings, not forced expenditure: the original checked-leaf limit and "
             "native convergence also stop search. Every query has exactly one native H1 search start. "
             "Real repeated callbacks are charged; cache hits are separate. Each helper returns "
             "synchronously with <=8 fresh submitted neighbors and <=72 CSR entries. "
             "Sentinel handling was exercised by the native1024-vector duplicate fixture, not "
             "claimed from SIFT when its observed sentinel counter is zero.", "",
             "### Native SSD postings / pages / distance evaluations", "",
             "| Scenario | H1 | H3 | C2000 | S2000 | C3200 | S3200 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for scenario in scenarios:
        text.append("| " + scenario + " | " + " | ".join(
            " / ".join(f"{rows[scenario, case][k]:.3f}" for k in ["postings", "pages", "disk_distances"])
            for case in cases) + " |")
    text += ["", "The native pipeline uses unchanged H/O setup and O_DIRECT SSD access. Full actual "
             "distance work is head actual plus disk distances, both in `summary.json`. "
             "Retrieval-minus-scan time is not physical SSD delay.", "",
             "### Descriptive latency ratios", "",
             "Reference is original H1 for unfilter and historical H3 for every filter. "
             "Recall mismatches prevent calling these same-recall costs. No weighted mixture.", "",
             "| Scenario | S2000/reference | S2000/C2000 | S3200/reference | S3200/C3200 |",
             "|---|---:|---:|---:|---:|"]
    comparisons = []
    for scenario in scenarios:
        values = []
        ref = rows[scenario, "h1" if scenario == "unfilter" else "h3"]
        for cap in (2000, 3200):
            s, c = rows[scenario, f"supplier{cap}"], rows[scenario, f"control{cap}"]
            reference_ratio = s["ordinary_ms"] / ref["ordinary_ms"]
            control_ratio = s["ordinary_ms"] / c["ordinary_ms"]
            values.extend([reference_ratio, control_ratio])
            comparisons.append({
                "scenario": scenario, "budget": cap, "reference": ref["case"],
                "supplier_recall": s["recall_at_10"], "control_recall": c["recall_at_10"],
                "reference_recall": ref["recall_at_10"],
                "supplier_underfill_queries": s["underfilled_queries"],
                "control_underfill_queries": c["underfilled_queries"],
                "reference_underfill_queries": ref["underfilled_queries"],
                "latency_ratio_to_reference": reference_ratio,
                "latency_ratio_to_control": control_ratio,
                "recall_delta_pp_to_control": 100 * (s["recall_at_10"] - c["recall_at_10"]),
                "same_recall_claim": False})
        text.append("| " + scenario + " | " + " | ".join(f"{v:.3f}" for v in values) + " |")
    text += ["", "`supplier_usage.json` reports actual triggering/H3-query counts. "
             "`independent_validation.json` includes parent-derived own-point contributions, exact "
             "admission checks and previous-artifact preservation. Historical H3's predicate-guided "
             "candidate discovery remains the explicit comparison confound above.", ""]
    readme = Path(__file__).with_name("README.md")
    prefix = readme.read_text().split("## Completed paired experiment")[0]
    content = prefix + "\n".join(text)
    readme.write_text(content)
    (root / "report.md").write_text(content)
    (root / "matched_comparisons.json").write_text(json.dumps(comparisons, indent=2) + "\n")
    print("report_sha256", hashlib.sha256((root / "report.md").read_bytes()).hexdigest())


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results", required=True, type=Path)
    render(p.parse_args().results)
