#!/usr/bin/env python3
"""Verify native per-query work and select observed, noninterpolated sweep points."""
import argparse
from collections import defaultdict
import csv
import json
import math
from pathlib import Path
import statistics

from official_benchmark_config import identity, require, write_json
from run_vanilla_spann_build import read_ini


def rows(path):
    with path.open() as stream:
        return [json.loads(line) for line in stream]


def key(row):
    return row["mode"], row.get("replicas", 0), row.get("beam", 0)


def best(points, predicate):
    eligible = [p for p in points if predicate(p)]
    return max(eligible, key=lambda p: (p["recall_mean"], -p["total_distances_mean"])) if eligible else None


def analyze(config):
    plan = read_ini(config)["Experiment"]
    root = Path(plan["OutputDirectory"])
    require(json.loads((root / "status.json").read_text())["state"] == "completed", "Sweep incomplete")
    require(not (root / "comparison.json").exists(), "Refusing to overwrite analysis")
    all_points, flat_rows, boundary, sources = [], [], {}, []
    reference_queries = None
    for path in plan["Configs"].split(","):
        native = read_ini(Path(path))
        directory = Path(native["Output"]["Directory"])
        require((directory / "COMPLETE").is_file(), "Missing native completion marker")
        count = native["Input"].getint("QueryCount")
        groups = defaultdict(list)
        for row in rows(directory / "queries.jsonl"):
            if row["mode"] == "flat":
                row["total_distances"] = row["graph_distances"]
            else:
                require(row["total_distances"] == row["graph_distances"] + row["h1_unique_distances"],
                        "Native distance accounting differs")
            valid_ids = [i for i in row["ids"] if i >= 0]
            require(len(valid_ids) == len(set(valid_ids)) and len(valid_ids) <= native["Sweep"].getint("ResultNum"),
                    "Invalid selected H1 IDs")
            groups[key(row)].append(row)
        summary = rows(directory / "summary.jsonl")
        require(len(summary) == len(groups), "Missing summary/query group")
        for point in summary:
            require(point["counted"], "Graph work must be counted, not inferred from GetScanned")
            measured = groups[key(point)]
            require(len(measured) == count and {r["query"] for r in measured} == set(range(count)),
                    "Missing or duplicate measured queries")
            for field in ("total_distances", "graph_distances", "h1_unique_distances", "assignment_entries",
                          "recall", "ordinary_us", "oracle_nonruntime_recall", "oracle_nonruntime_unique"):
                if field + "_mean" not in point:
                    continue
                values = [r[field] for r in measured]
                for suffix, expected in (("_mean", statistics.mean(values)),
                                         ("_p95", sorted(values)[math.ceil(0.95 * count) - 1])):
                    require(math.isclose(point[field + suffix], expected, abs_tol=1e-6, rel_tol=1e-10),
                            f"Summary mismatch: {directory.name}, {key(point)}, {field}{suffix}")
            point["case"] = directory.name
            if point["mode"] == "flat":
                flat_rows.append(point)
                invariant = [(r["query"], r["ids"], r["graph_distances"], r["recall"])
                             for r in sorted(measured, key=lambda r: r["query"])]
                if reference_queries is None:
                    reference_queries = invariant
                else:
                    require(invariant == reference_queries, "Flat reference work/results changed between ratios")
            else:
                all_points.append(point)
        boundary[directory.name] = rows(directory / "boundary_summary.jsonl")
        for filename in ("summary.jsonl", "queries.jsonl", "boundary_summary.jsonl", "COMPLETE"):
            sources.append(identity(directory / filename, True))
    flat = flat_rows[0]
    baseline = next((p for p in all_points
                     if p["case"] == "r16" and p["replicas"] == 8 and p["beam"] == 16), None)
    same_flat = lambda p: (p["total_distances_mean"] <= flat["total_distances_mean"] and
                           p["total_distances_p95"] <= flat["total_distances_p95"])
    same_h2 = lambda p: (baseline is not None and
                         p["total_distances_mean"] <= baseline["total_distances_mean"] and
                         p["total_distances_p95"] <= baseline["total_distances_p95"])
    recovered = [p for p in all_points if p["recall_mean"] >= flat["recall_mean"] - 1e-10]
    report = {
        "scope": "Navigation H1 recall only; H2 graph plus one CSR, not full H3 or disk search.",
        "comparison": "Observed points only, no interpolation; matched work caps both mean and p95 native L2 calls.",
        "flat_references": flat_rows, "baseline_h2": baseline,
        "best_under_flat_mean_work": best(all_points, lambda p: p["total_distances_mean"] <= flat["total_distances_mean"]),
        "best_under_flat_work": best(all_points, same_flat),
        "best_under_baseline_h2_work": best(all_points, same_h2),
        "best_under_777_h1_distances": best(all_points, lambda p: p["h1_unique_distances_mean"] <= 777),
        "cheapest_recovered_flat_recall": min(recovered, key=lambda p: p["total_distances_mean"]) if recovered else None,
        "per_replica_under_flat_mean_work": {
            str(r): best([p for p in all_points if p["replicas"] == r],
                         lambda p: p["total_distances_mean"] <= flat["total_distances_mean"])
            for r in sorted({p["replicas"] for p in all_points})
        },
        "per_replica_cheapest_recovered": {
            str(r): min((p for p in recovered if p["replicas"] == r),
                        key=lambda p: p["total_distances_mean"], default=None)
            for r in sorted({p["replicas"] for p in all_points})
        },
        "fixed_beam16": [p for p in all_points if p["beam"] == 16],
        "per_ratio_under_flat_work": {
            name: best([p for p in all_points if p["case"] == name], same_flat)
            for name in sorted({p["case"] for p in all_points})
        },
        "per_ratio_under_flat_mean_work": {
            name: best([p for p in all_points if p["case"] == name],
                       lambda p: p["total_distances_mean"] <= flat["total_distances_mean"])
            for name in sorted({p["case"] for p in all_points})
        },
        "boundary": boundary, "sources": sources, "point_count": len(all_points),
    }
    write_json(root / "comparison.json", report)
    fields = ["case", "actual_ratio", "h2_nodes", "replicas", "beam", "recall_mean",
              "total_distances_mean", "total_distances_p95", "graph_distances_mean",
              "h1_unique_distances_mean", "assignment_entries_mean", "ordinary_us_mean",
              "oracle_nonruntime_recall_mean"]
    with (root / "curve.csv").open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_points)
    print(json.dumps({k: report[k] for k in (
        "baseline_h2", "best_under_flat_mean_work", "best_under_flat_work", "best_under_baseline_h2_work",
        "best_under_777_h1_distances", "cheapest_recovered_flat_recall", "per_ratio_under_flat_work")},
        indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    analyze(parser.parse_args().config.resolve(strict=True))
