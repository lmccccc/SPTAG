#!/usr/bin/env python3
"""Summarize fixed corrected-search pairs and separate native work evidence."""
import argparse
import csv
import json
from pathlib import Path
import statistics

import numpy as np
from run_postgraph import GRID, SCENARIOS, VARIANTS, read_counters, read_ini, require, save


def payload(root, row, name, dtype, columns):
    return np.fromfile(root / row["case"] / f"nprobe_{row['nprobe']}" / name,
                       dtype=dtype).reshape(row["queries"], columns)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign", type=Path)
    parser.add_argument("--prior", type=Path, required=True)
    args = parser.parse_args()
    output = args.campaign
    normal = json.loads((output / "plain-results.json").read_text())
    diagnostic = json.loads((output / "diagnostic-results.json").read_text())
    require(len(normal) == 288 and len(diagnostic) == 144, "Complete fixed campaign required")
    indexed = {(r["scenario"], r["variant"], r["nprobe"], r["repetition"]): r for r in normal}
    paired = []
    for scenario in SCENARIOS:
        for probe in GRID:
            for variant in VARIANTS:
                pair = [indexed[(scenario, variant, probe, repetition)] for repetition in (1, 2)]
                ratios = [row["qps"] / indexed[(scenario, "graph", probe, row["repetition"])]["qps"]
                          for row in pair]
                paired.append(dict(scenario=scenario, nprobe=probe, variant=variant,
                    recall=pair[0]["recall"], qps_mean=statistics.mean(r["qps"] for r in pair),
                    r1_qps=pair[0]["qps"], r2_qps=pair[1]["qps"],
                    r1_ratio_vs_graph=ratios[0], r2_ratio_vs_graph=ratios[1],
                    mean_paired_ratio_vs_graph=statistics.mean(ratios),
                    underfilled_queries=pair[0]["underfilled_queries"]))
    with (output / "paired-coordinates.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(paired[0]))
        writer.writeheader(); writer.writerows(paired)
    save(output / "paired-assessment.json", dict(
        points=paired, repetitions=2, node=3,
        comparison="Only same-batch corrected project engines; no historical QPS splice",
        qps_boundary="Outer native client batch includes result/work extraction and loop bookkeeping; warmup and replay excluded",
        acceptance="Descriptive fixed pairs, no numerical tolerance or statistical no-regression claim"))

    phases = []
    for row in diagnostic:
        counters = read_counters(output, row)
        leaves = counters["graph_checked_leaves"]
        budget = row["max_check"]
        if row["scenario"] != "unfilter":
            require(np.all(leaves <= budget), f"Filtered hard cap exceeded: {row['case']}")
        phases.append(dict(scenario=row["scenario"], variant=row["variant"], nprobe=row["nprobe"],
            graph_budget=budget, graph_checked_mean=float(leaves.mean()),
            graph_checked_max=int(leaves.max()),
            graph_cap_reached_queries=int(np.count_nonzero(leaves >= budget)),
            graph_exact_cap_queries=int(np.count_nonzero(leaves == budget)),
            graph_overshoot_queries=int(np.count_nonzero(leaves > budget)),
            graph_distance_mean=float(counters["graph_distances"].mean()),
            supplement_checked_mean=float(counters["supplement_checked_leaves"].mean()),
            supplement_activations=int(counters["posting_activations"].sum()),
            heads_before_mean=float(counters["head_before"].mean()),
            heads_after_mean=float(counters["head_after"].mean()),
            preserved_heads=int(counters["preserved_heads"].sum()),
            graph_heads=int(counters["head_before"].sum())))
    save(output / "phase-cap-accounting.json", dict(points=phases,
        cap_definition="Per-query graph checked leaves reach nominal MaxCheck; not a count of branch executions or an inferred termination cause",
        unfiltered="Nominal overshoot allowed by pinned upstream adaptive semantics",
        filtered="Hard graph checked-leaf cap, independent of result fullness",
        supplement="Separate additional budget; complete selected rows may overshoot total ceiling",
        timing="Diagnostic runs never contribute ordinary QPS"))

    prior = json.loads((args.prior / "plain-results.json").read_text())
    prior_diag = json.loads((args.prior / "diagnostic-results.json").read_text())
    old = {(r["scenario"], r["nprobe"]): r for r in prior
           if r["variant"] == "compact_layout" and r["repetition"] == 1}
    old_diag = {(r["scenario"], r["nprobe"]): r for r in prior_diag
                if r["variant"] == "compact_layout"}
    new_diag = {(r["scenario"], r["nprobe"]): r for r in diagnostic
                if r["variant"] == "postgraph_extra"}
    differences = []
    for scenario in SCENARIOS:
        for probe in GRID:
            before = old[(scenario, probe)]
            after = indexed[(scenario, "postgraph_extra", probe, 1)]
            left, right = read_ini(before["config"]), read_ini(after["config"])
            require(dict(left["SearchSSDIndex"]) == dict(right["SearchSSDIndex"]) and
                    dict(left["Benchmark"]) == dict(right["Benchmark"]),
                    "Historical work comparison must use identical native settings/cohort/index")
            old_ids = payload(args.prior, before, "ids.i32", "<i4", 10)
            new_ids = payload(output, after, "ids.i32", "<i4", 10)
            old_work = payload(args.prior, before, "work.u64", "<u8", 8)
            new_work = payload(output, after, "work.u64", "<u8", 8)
            a, b = old_diag[(scenario, probe)], new_diag[(scenario, probe)]
            old_heads = payload(args.prior, a, "graph_ids.i32", "<i4", probe)
            new_heads = payload(output, b, "graph_ids.i32", "<i4", probe)
            old_counts, new_counts = read_counters(args.prior, a), read_counters(output, b)
            differences.append(dict(scenario=scenario, nprobe=probe,
                prior_recall=before["recall"], corrected_recall=after["recall"],
                final_id_changed_queries=int(np.count_nonzero(np.any(old_ids != new_ids, axis=1))),
                ssd_work_changed_queries=int(np.count_nonzero(np.any(old_work != new_work, axis=1))),
                graph_head_changed_queries=int(np.count_nonzero(np.any(old_heads != new_heads, axis=1))),
                prior_graph_checked_mean=float(old_counts["graph_checked_leaves"].mean()),
                corrected_graph_checked_mean=float(new_counts["graph_checked_leaves"].mean()),
                prior_tree_visits_mean=float(old_counts["tree_visits"].mean()),
                corrected_tree_visits_mean=float(new_counts["tree_visits"].mean())))
    save(output / "prior-behavior-differences.json", dict(
        prior=str(args.prior), points=differences,
        scope="Same compact index/cohort/settings for postgraph 2048+2048; compare IDs/recall/work only",
        historical_timing_used=False, old_payload_parity_required=False))


if __name__ == "__main__":
    main()
