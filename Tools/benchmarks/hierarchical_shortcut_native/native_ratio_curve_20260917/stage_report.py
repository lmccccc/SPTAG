"""Read-only reconciliation/presentation of a completed explicit stage; never runs queries."""
import argparse
import configparser
import hashlib
import json
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, choices=("unfilter_broad", "medium_extreme", "numeric_mixed"))
    args = parser.parse_args()
    config = configparser.ConfigParser()
    config.read(HERE / "experiment.ini")
    root = Path(config["Experiment"]["OutputDirectory"])
    stage = json.loads((root / f"stage_{args.stage}.json").read_text())
    summary_path = root / f"summary.stage_{args.stage}.json"
    summary = json.loads(summary_path.read_text())
    assert hashlib.sha256(summary_path.read_bytes()).hexdigest() == stage["summary_sha256"]
    assert len(summary) == stage["unique_points"] == 66
    assert len(stage["records"]) == 12 and sum(len(r["points"]) for r in stage["records"]) == 132
    for record in stage["records"]:
        directory = root / record["directory"]
        assert json.loads((directory / "native.exit.json").read_text())["returncode"] == 0
        assert json.loads((directory / "native.io.json").read_text())["direct_io"]
        stdout = (directory / "native.stdout.log").read_text()
        assert stdout.splitlines().count("NATIVE_INDEX_LOAD calls=1") == 1
        assert stdout.count("NATIVE_PROBE_BEGIN ") == 11
        assert record["native_loadall_entries"] == record["owner_loads"] == 1
        assert set(record["physical_component_loads"].values()) == {1}
        assert all(point["native"]["failed_queries"] == 0 for point in record["points"])
    thresholds = {}
    for scenario in stage["observed_threshold_points"]:
        thresholds[scenario] = {}
        for case in ("h1", "h3", "supplier"):
            points = [r for r in summary if r["scenario"] == scenario and r["case"] == case]
            thresholds[scenario][case] = {}
            for threshold in (.90, .95):
                candidates = [r for r in points if r["recall_at_10"] >= threshold]
                best = max(candidates, key=lambda r: r["qps"]) if candidates else None
                thresholds[scenario][case][str(threshold)] = None if best is None else {
                    k: best[k] for k in ("nprobe", "recall_at_10", "qps", "ordinary_ms", "underfilled_queries")}
    comparison = []
    for probe in sorted({r["nprobe"] for r in summary if r["scenario"] == "unfilter"}):
        h1, supplier = [next(r for r in summary if r["scenario"] == "unfilter" and
                            r["case"] == case and r["nprobe"] == probe) for case in ("h1", "supplier")]
        assert h1["heads_sha256"] == supplier["heads_sha256"] and h1["results_sha256"] == supplier["results_sha256"]
        assert h1["work"] == supplier["work"] and h1["native_ssd_work"] == supplier["native_ssd_work"]
        assert all(supplier["work"][k] == 0 for k in ("supplier_calls", "supplier_parent_distances",
            "native_child_distances", "supplier_members", "signature_checks", "row_eligibility_evaluations"))
        comparison.append({"nprobe": probe, "recall_at_10": h1["recall_at_10"], "h1_ms": h1["ordinary_ms"],
            "supplier_ms": supplier["ordinary_ms"], "h1_qps": h1["qps"], "supplier_qps": supplier["qps"],
            "latency_ratio": supplier["ordinary_ms"] / h1["ordinary_ms"]})
    result = {"stage": args.stage, "unique_points": 66, "ordinary_batches": 132, "ordinary_index_loads": 12,
        "unfilter_comparison": comparison, "fastest_observed_points_at_or_above_threshold": thresholds,
        "operation_totals": stage["operation_totals"], "summary_sha256": stage["summary_sha256"],
        "matrix_complete": False, "canonical_summary_exists": (root / "summary.json").exists()}
    assert not result["canonical_summary_exists"]
    report = [
        f"# Stage {args.stage}:66 paired points complete, full matrix still pending", "",
        "Exactly132 timed batches from12 native single-load ordinary processes. Every point has two "
        "ordinary repetitions; rep2 reverses probes and rotates scenario/mode order. "
        "All native arrays use SearchSweep.NProbe.1000 warmup+1000 measured per probe, "
        "one query thread, NUMA CPU/memory2, O_DIRECT/page15. No other stage was launched.",
        "The accepted native search libraries and wrapper object are unchanged. Benchmark-only "
        "compatibility supports authentic H3 and coarse operation logging. Both failed preflights "
        "and the native ownership-counter diagnosis are preserved; they are not counted as successful "
        "stage loads or timed points. There are six successful eight-query fixture processes in "
        "addition to the12 ordinary processes.",
        "", "## Unfilter paired native H1/supplier", "",
        "| nprobe | Recall@10 | H1 ms | Supplier ms | H1 QPS | Supplier QPS | latency ratio |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for row in comparison:
        report.append(f'| {row["nprobe"]} | {row["recall_at_10"]:.4f} | {row["h1_ms"]:.6f} | '
                      f'{row["supplier_ms"]:.6f} | {row["h1_qps"]:.2f} | {row["supplier_qps"]:.2f} | '
                      f'{row["latency_ratio"]:.4f} |')
    if comparison:
        ratios = [r["latency_ratio"] for r in comparison]
        report += ["", f"Supplier/H1 latency ratio min/median/max: {min(ratios):.4f}/"
                   f"{statistics.median(ratios):.4f}/{max(ratios):.4f}. "
                   "Every unfilter probe has exact H1 selected heads/final IDs/distances/native core work, "
                   "with zero helper/upper/CSR/row-qualification work. This is not a claimed speed advantage."]
    report += ["", "## Fastest observed points reaching each recall threshold", "",
        "Observed grid points only; no interpolation or old latency splicing.",
        "", "| scenario | case | threshold | nprobe | Recall@10 | QPS |",
        "|---|---|---:|---:|---:|---:|"]
    for scenario, cases in thresholds.items():
        for case, values in cases.items():
            for threshold, point in values.items():
                if point:
                    report.append(f'| {scenario} | {case} | {threshold} | {point["nprobe"]} | '
                                  f'{point["recall_at_10"]:.4f} | {point["qps"]:.2f} |')
                else:
                    report.append(f"| {scenario} | {case} | {threshold} | not reached | - | - |")
    report += ["", "## Operation costs and evidence", "", "| operation | seconds |", "|---|---:|"]
    for key, value in stage["operation_totals"].items():
        report.append(f"| {key} | {value:.3f} |")
    report += [
        "", "LoadAll timing covers that API entry. Deferred physical component loading is inside the "
        "first capture when lazy; it is not mislabeled as pure query compute. Native process wall time "
        "also includes initialization, capture, warmup, serialization and launcher observation overhead. "
        "Python audit/compression time is separately measured. No fine query timers or per-point "
        "profiles were introduced.",
        "Required native validity, full-row/signature/ratio and stopping checks are preserved. "
        "First repetitions were fully audited; second repetitions must exactly match those raw "
        "captures and deterministic native work. The native executable independently checks "
        "capture/ordinary results and work. As in the authentic benchmark, contributing-posting "
        "ownership can change with IO completion order; actual differing values are retained in "
        "NATIVE_CONTRIBUTION logs and per-repetition summary fields, not forged into equality.",
        "H3 boundary fixtures match authentic H3 heads/own points/final results/SSD work. Its historical "
        "predicate-guided route is preserved. H3 navigation primitive counts are unavailable through "
        "the H1 observer and are explicitly omitted, not reported as zero hierarchy work. "
        "Native SSD counters remain available for every case.",
        "This is a partial-stage dataset, not published curves. Remaining four scenarios and132 unique "
        "points are pending explicit authorization. Canonical summary.json is absent.",
        "", f'Stage summary SHA-256: `{stage["summary_sha256"]}`']
    (root / f"report.stage_{args.stage}.md").write_text("\n".join(report) + "\n")
    (root / f"reconciliation.stage_{args.stage}.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
