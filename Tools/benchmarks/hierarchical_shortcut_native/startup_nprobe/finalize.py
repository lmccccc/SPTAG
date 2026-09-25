"""Complete QPS-range fields from unchanged completed ordinary-run latency records."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil


def main(root):
    assert json.loads((root / "status.json").read_text())["state"] == "completed"
    summary = json.loads((root / "summary.json").read_text())
    assert len(summary) == 198
    assert not (root / "qps_schema_finalization.json").exists()
    for name in ("summary.json", "summary.csv", "thresholds.json", "validation.json"):
        shutil.copy2(root / name, root / (name + ".before_qps_bounds"))
    for row in summary:
        times = row["ordinary_ms_runs"]
        assert len(times) == 2 and row["qps"] == 1000 / row["ordinary_ms"]
        row["qps_runs"] = [1000 / t for t in times]
        row["qps_min"], row["qps_max"] = min(row["qps_runs"]), max(row["qps_runs"])
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    scalar = [k for k, v in summary[0].items() if not isinstance(v, (list, dict))]
    with (root / "summary.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=scalar)
        writer.writeheader()
        writer.writerows({k: row[k] for k in scalar} for row in summary)
    points = {(r["scenario"], r["case"], r["nprobe"]): r for r in summary}
    thresholds = json.loads((root / "thresholds.json").read_text())
    for t in thresholds:
        if t["observed_best"] is not None:
            p = t["observed_best"]
            t["observed_best"] = points[p["scenario"], p["case"], p["nprobe"]]
    (root / "thresholds.json").write_text(json.dumps(thresholds, indent=2) + "\n")
    validation = json.loads((root / "validation.json").read_text())
    for filename, key in (("summary.json", "summary_sha256"), ("summary.csv", "summary_csv_sha256"),
                          ("thresholds.json", "thresholds_sha256")):
        validation[key] = hashlib.sha256((root / filename).read_bytes()).hexdigest()
    (root / "validation.json").write_text(json.dumps(validation, indent=2) + "\n")
    shutil.copy2(Path(__file__), root / "qps_schema_finalizer.py")
    (root / "qps_schema_finalization.json").write_text(json.dumps({
        "reason": "The in-flight runner omitted required min/max QPS fields from its initial renderer.",
        "only_changes": ["qps_min", "qps_max", "qps_runs derived from ordinary_ms_runs"],
        "recall_latency_work_and_primary_qps_unchanged": True,
        "native_runs_repeated_or_added": False,
        "before_reports_preserved": True,
    }, indent=2) + "\n")
    print("PASS: all198 points now expose QPS ranges derived from both ordinary runs.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    main(parser.parse_args().results.resolve())
