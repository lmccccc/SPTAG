"""Reconcile actual unfiltered node-level trigger evidence and report the nprobe24 comparison."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import struct

import numpy as np


def main(root):
    assert json.loads((root / "status.json").read_text())["state"] == "completed"
    rows = json.loads((root / "summary.json").read_text())
    provenance = json.loads((root / "provenance.json").read_text())
    graph_path = Path(provenance["physical_graph"]["path"])
    with graph_path.open("rb") as stream:
        n, width = struct.unpack("<ii", stream.read(8))
    graph = np.memmap(graph_path, dtype="<i4", offset=8, shape=(n, width), mode="r")
    points = [r for r in rows if r["scenario"] == "unfilter" and r["case"] == "supplier"]
    assert len(points) == 11
    aggregates, details = [], {}
    for point in points:
        histogram = point["connectivity_evidence"]["trigger_physical_degree_histogram"]
        assert all(int(d) < 16 for d in histogram)
        assert sum(histogram.values()) == round(point["supplier_work"]["supplyCalls"] * 1000)
        counts = {"frames": 0, "adequate": 0, "adequate_visited20": 0, "full32": 0,
                  "full32_visited20": 0, "calls": 0, "startup_calls": 0}
        path = root / point["directories"][0] / "queries.jsonl.gz"
        native_histogram = {}
        with gzip.open(path, "rt") as stream:
            for q, line in enumerate(stream):
                row = json.loads(line)
                for frame in row["connectivity_audits"]:
                    node = frame["node"]
                    ids, seen = [], set()
                    for value in graph[node]:
                        id = int(value)
                        if id < 0:
                            break
                        if id >= n or id == node or id in seen:
                            continue
                        seen.add(id); ids.append(id)
                    assert [p[0] for p in frame["ordinary"]] == ids
                    assert all(flag & 2 for _, flag in frame["ordinary"])
                    assert frame["before"] == len(ids)
                    counts["frames"] += 1
                    if len(ids) >= 16:
                        counts["adequate"] += 1
                        counts["adequate_visited20"] += frame["visited"] >= 20
                        assert frame["calls"] == 0
                    if len(ids) == 32:
                        counts["full32"] += 1
                        counts["full32_visited20"] += frame["visited"] >= 20
                    if frame["calls"]:
                        assert len(ids) < 16
                        counts["calls"] += frame["calls"]
                        counts["startup_calls"] += frame["phase"] == 1
                        key = str(len(ids))
                        native_histogram[key] = native_histogram.get(key, 0) + 1
                        if point["nprobe"] == 24:
                            item = details.setdefault(str(node), {
                                "physical_degree": len(ids), "ordinary_ids": ids,
                                "calls": 0, "startup_calls": 0, "example_queries": []})
                            item["calls"] += 1
                            item["startup_calls"] += frame["phase"] == 1
                            if len(item["example_queries"]) < 4:
                                item["example_queries"].append(q)
        assert q == 999 and native_histogram == histogram
        assert counts["frames"] == point["connectivity_evidence"]["frames"]
        aggregates.append({"nprobe": point["nprobe"], **counts, "trigger_physical_degree_histogram": histogram})
    comparison = []
    for case in ("h1", "h3", "supplier"):
        point = next(r for r in rows if (r["scenario"], r["case"], r["nprobe"]) == ("unfilter", case, 24))
        result = {k: point[k] for k in ("case", "recall_at_10", "ordinary_ms", "ordinary_ms_runs",
                                       "qps", "qps_min", "qps_max", "underfilled_queries",
                                       "postings", "pages", "disk_distances", "head_actual_distances")}
        if case == "supplier":
            result["supplier_work"] = point["supplier_work"]
            result["latency_ratio_to_h1"] = point["ordinary_ms"] / comparison[0]["ordinary_ms"]
        comparison.append(result)
    output = {"supplier_degree_semantics": "predicate_valid_neighbors",
              "independent_unfilter_native_queries_audited": 11000,
              "all_adequate_ordinary_degrees_never_supply": True,
              "all_real_calls_have_physical_degree_below16": True,
              "per_probe": aggregates, "nprobe24_trigger_nodes": details,
              "unfilter_nprobe24_comparison": comparison}
    destination = root / "unfilter_degree_audit.json"
    assert not destination.exists()
    destination.write_text(json.dumps(output, indent=2) + "\n")
    shutil.copy2(Path(__file__), root / "degree_report_source.py")
    md = ["# Predicate-valid degree: unfiltered evidence", "",
          "Each row below comes from1000 native query captures; all11 unfiltered probes were checked.",
          "Every qualifying physical degree>=16, including fully connected32-neighbor rows "
          "with many visited neighbors, had zero posting-supplier calls.", "",
          "| nprobe | Checked frames | Degree32 frames | Degree32 with >=20 visited skips | Supplier calls |",
          "|---|---:|---:|---:|---:|"]
    for a in aggregates:
        md.append(f'| {a["nprobe"]} | {a["frames"]} | {a["full32"]} | '
                  f'{a["full32_visited20"]} | {a["calls"]} |')
    md += ["", "## Unfilter, nprobe24", "",
           "| Mode | Recall (%) | Ordinary ms | QPS | Postings | Pages | SSD distances |",
           "|---|---:|---:|---:|---:|---:|---:|"]
    for r in comparison:
        md.append(f'| {r["case"]} | {100*r["recall_at_10"]:.2f} | {r["ordinary_ms"]:.6f} | '
                  f'{r["qps"]:.3f} | {r["postings"]:.3f} | {r["pages"]:.3f} | {r["disk_distances"]:.3f} |')
    s = comparison[-1]
    w = s["supplier_work"]
    md += ["", "Supplier per-query calls / CSR-member inspections / actual graph / routing / parent / child distances:",
           " / ".join(f'{w[k]:.3f}' for k in ("supplyCalls", "supplyMembers", "supplyGraph",
                      "supplyRouting", "supplyParent", "supplyChild")), "",
           f'Supplier/H1 full latency ratio: {s["latency_ratio_to_h1"]:.3f}. '
           "Existing common callback/qualification/admission overhead was not removed.", "",
           "Any nonzero calls are listed by actual low-degree node and its physical ordinary IDs "
           "in unfilter_degree_audit.json. These are not inferred from fresh counts.", ""]
    (root / "degree_report.md").write_text("\n".join(md))
    print("\n".join(md))
    print("Audit SHA256:", hashlib.sha256(destination.read_bytes()).hexdigest())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    main(parser.parse_args().results.resolve())
