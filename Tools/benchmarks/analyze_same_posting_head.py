#!/usr/bin/env python3
"""Report navigation recall differences with fixed posting storage and budgets."""

import argparse
import csv
import json
from pathlib import Path
import shutil

from official_benchmark_config import identity, require, write_json
from run_vanilla_spann_build import read_ini


def load_run(config):
    plan = read_ini(config)["Experiment"]
    output = Path(plan["OutputDirectory"])
    require(json.loads((output / "status.json").read_text())["state"] == "completed",
            "Native comparison is incomplete")
    provenance = json.loads((output / "provenance.json").read_text())
    require(provenance["config"] == identity(config, True), "Fixed experiment control changed")
    rows = json.loads((output / "summary.json").read_text())
    descriptors = [json.loads(file.read_text()) for file in output.glob("*.io.json")]
    require(descriptors and all(row["direct_io"] for row in descriptors), "Expected direct IO")
    targets = {row["target"] for row in descriptors}
    require(len(targets) == 1, "Native modes did not open the same posting file")
    return output, rows, provenance, targets


def paired_comparison(flat, hierarchy):
    return {
        "flat": flat, "hierarchy": hierarchy,
        "recall_loss_percentage_points": 100 * (flat["recall"] - hierarchy["recall"]),
        "hierarchy_over_flat": {
            key: hierarchy[key] / flat[key] for key in
            ("postings", "pages", "requested_bytes", "scanned_vectors",
             "distance_computations", "mean_ms", "median_qps")
        },
    }


def run(config, matched_config):
    config, matched_config = config.resolve(strict=True), matched_config.resolve(strict=True)
    output, rows, provenance, targets = load_run(config)
    matched_output, matched_rows, matched_provenance, matched_targets = load_run(matched_config)
    for key in ("binary", "source_inventory", "flat_graph_inventory", "queries", "truth"):
        require(provenance[key] == matched_provenance[key], f"Comparison inputs differ: {key}")
    require(targets == matched_targets, "Matched-recall run changed posting storage")
    require(not (output / "comparison.json").exists() and not (output / "curve.csv").exists(),
            "Refusing to overwrite analysis")
    by_probe = {}
    for row in rows:
        by_probe.setdefault(row["nprobe"], {})[row["mode"]] = row
    pairs = []
    for probe, modes in sorted(by_probe.items()):
        require(set(modes) == {"H1Only", "H2Only"}, "Equal nprobe lacks a paired native mode")
        pairs.append({"nprobe": probe, **paired_comparison(modes["H1Only"], modes["H2Only"])})
    require(len(matched_rows) == 2, "Require one explicit matched-recall comparison")
    matched = {row["mode"]: row for row in matched_rows}
    require(set(matched) == {"H1Only", "H2Only"}, "Matched-recall modes differ")
    require(abs(matched["H1Only"]["recall"] - matched["H2Only"]["recall"]) <= 0.001,
            "Matched recall differs by more than 0.1 percentage point")
    report = {
        "conclusion_scope": "Frozen H3 versus native flat BKT on identical ordered H1 IDs and identical SSD postings.",
        "same_budget": pairs,
        "matched_recall": paired_comparison(matched["H1Only"], matched["H2Only"]),
        "fixed_native_posting_path": next(iter(targets)),
        "source_posting_identity": next(file for file in provenance["source_inventory"]
                                       if file["path"].endswith("/SPTAGFullList.bin")),
        "frozen_binary": provenance["binary"],
        "native_runs": [str(output), str(matched_output)],
        "caveats": [
            "The flat BKT is built on the same existing H1 rows, not the previous independently selected vanilla heads.",
            "Equal nprobe need not mean exactly equal pages: different selected postings have slightly different lengths.",
            "Near-equal final recall requires more H3 postings; this is navigation-efficiency loss, not a posting-layout change.",
            "Diagnostics capture selected heads before posting selection; they do not yet isolate top-graph versus CSR-level losses.",
            "The frozen H3 exposes no exact final-result ID dump; aggregate work/recall equality is verified, selected head sets are independently dumped.",
            "Requested pages/bytes are not physical device I/O counts. Both modes use the same observed direct descriptor.",
        ],
    }
    fields = ["case", "nprobe", "mode", "recall", "postings", "pages",
              "scanned_vectors", "distance_computations", "requested_bytes", "mean_ms", "median_qps"]
    with (output / "curve.csv").open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in fields})
    write_json(output / "comparison.json", report)
    shutil.copy2(Path(__file__), output / Path(__file__).name)
    write_json(output / "analysis_provenance.json", {
        "script": identity(Path(__file__), True),
        "input_summaries": [identity(path / "summary.json", True) for path in (output, matched_output)],
        "native_logs_modified": False,
    })
    print(json.dumps({"same_budget": [{"nprobe": p["nprobe"],
                                       "recall_loss_pp": p["recall_loss_percentage_points"],
                                       "pages_ratio": p["hierarchy_over_flat"]["pages"]}
                                      for p in pairs],
                      "matched_recall_ratios": report["matched_recall"]["hierarchy_over_flat"]}, indent=2))
    print(output / "comparison.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--matched-config", required=True, type=Path)
    args = parser.parse_args()
    run(args.config, args.matched_config)
