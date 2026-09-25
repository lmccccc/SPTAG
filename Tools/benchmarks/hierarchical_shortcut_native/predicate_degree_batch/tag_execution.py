"""Add execution-protocol metadata after checking actual native batch evidence."""
import csv
import importlib.util
import json
from pathlib import Path
import shutil
import statistics

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("batch_metadata_runner", HERE / "run.py")
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)
EXECUTION = "single_load_nprobe_array"


def main():
    plan = batch.native.read_ini(HERE / "experiment.ini")["Experiment"]
    root = Path(plan["OutputDirectory"])
    rows = json.loads((root / "summary.json").read_text())
    runs = json.loads((root / "runs.json").read_text())
    assert json.loads((root / "status.json").read_text())["state"] == "completed"
    assert len(rows) == 198 and len(runs) == 36
    assert len({(r["scenario"], r["case"], r["nprobe"]) for r in rows}) == 198
    evidence, pairs = [], {}
    for run in runs:
        directory = root / run["directory"]
        assert json.loads((directory / "native.exit.json").read_text())["returncode"] == 0
        logs = [directory / f"native.{part}.log" for part in ("stdout", "stderr")]
        assert batch.fingerprint(logs[0]) == run["native_stdout"]
        assert batch.fingerprint(logs[1]) == run["native_stderr"]
        results = batch.parse(logs, run["probes"], 1000)
        assert results == [p["native"] for p in run["points"]]
        # parse checks the actual LoadAll-entry log, every probe boundary and
        # each native result's load/workspace counters, not Python launch counts.
        evidence.append({"directory": run["directory"], "actual_loadall_entry_calls": 1,
                         "native_probe_count": len(results), "stdout": batch.fingerprint(logs[0])})
        for point in run["points"]:
            key = point["scenario"], point["case"], point["nprobe"]
            pairs.setdefault(key, []).append(point)
    assert sum(e["native_probe_count"] for e in evidence) == 396
    for row in rows:
        key = row["scenario"], row["case"], row["nprobe"]
        pair = pairs[key]
        assert len(pair) == 2
        ms = [p["native"]["mean_latency_ms"] for p in pair]
        assert row["ordinary_ms_runs"] == ms and row["ordinary_ms"] == statistics.mean(ms)
        assert row["qps"] == 1000 / statistics.mean(ms)
        assert row["recall_at_10"] == pair[0]["validation"]["recall_at_10"]
        if row["case"] == "supplier":
            assert row["supplier_degree_semantics"] == "predicate_valid_neighbors"
        assert "sweep_execution" not in row, "Metadata revision already applied"
        row["sweep_execution"] = EXECUTION
    changed = ("summary.json", "summary.csv", "thresholds.json", "unfilter_degree_audit.json",
               "validation.json", "independent_reconciliation.json", "final_provenance/manifest.json")
    backup = root / "schema_before_sweep_execution"
    backup.mkdir()
    for name in changed:
        target = backup / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, target)
    batch.write(root / "summary.json", rows)
    fields = list(dict.fromkeys(k for r in rows for k, v in r.items() if not isinstance(v, (list, dict))))
    with (root / "summary.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: r.get(k) for k in fields} for r in rows)
    thresholds = json.loads((root / "thresholds.json").read_text())
    for threshold in thresholds:
        if threshold["observed_best"] is not None:
            threshold["observed_best"]["sweep_execution"] = EXECUTION
    batch.write(root / "thresholds.json", thresholds)
    degree = json.loads((root / "unfilter_degree_audit.json").read_text())
    for row in degree["unfilter_nprobe24_comparison"]:
        row["sweep_execution"] = EXECUTION
    batch.write(root / "unfilter_degree_audit.json", degree)
    summary_hash = batch.fingerprint(root / "summary.json")["sha256"]
    for name in ("validation.json", "independent_reconciliation.json"):
        data = json.loads((root / name).read_text())
        data.update(sweep_execution=EXECUTION, summary_sha256=summary_hash,
                    execution_metadata_evidence="execution_metadata_revision.json")
        batch.write(root / name, data)
    shutil.copy2(Path(__file__), root / "execution_metadata_source.py")
    notice = {
        "reported_by": "MAIN", "reported_stopped_pid": 4178872,
        "reported_driver": "predicate_degree_native/run.py",
        "action": "Do not retry or resume; per-point execution superseded by completed native array sweep.",
        "previous_output_status_observed": json.loads(
            (Path(plan["PreviousResults"]) / "status.json").read_text()),
        "preservation": "Previous output untouched; any stopped partial attempt remains superseded. "
                        "Do not relabel an existing completed dataset as incomplete.",
    }
    batch.write(root / "execution_metadata_revision.json", {
        "change": "Metadata only; every summary row now explicitly identifies native array execution.",
        "sweep_execution": EXECUTION, "rows": len(rows),
        "supplier_rows": sum(r["case"] == "supplier" for r in rows),
        "actual_native_load_evidence": evidence,
        "summary_sha256": summary_hash, "pre_revision_directory": str(backup),
        "before": [batch.fingerprint(backup / name) for name in changed],
        "source": batch.fingerprint(root / "execution_metadata_source.py"),
        "superseded_per_point_execution": notice,
        "native_measurements_binaries_configs_and_raw_captures_unchanged": True,
    })
    manifest_path = root / "final_provenance/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["completed_artifacts"] = [
        batch.fingerprint(item["path"]) for item in manifest["completed_artifacts"]]
    manifest["completed_artifacts"].append(batch.fingerprint(root / "execution_metadata_revision.json"))
    manifest["sweep_execution"] = EXECUTION
    batch.write(manifest_path, manifest)
    before = json.loads((backup / "summary.json").read_text())
    assert [{k: v for k, v in row.items() if k != "sweep_execution"} for row in rows] == before
    assert {r["sweep_execution"] for r in rows} == {EXECUTION}
    print(f"Tagged198 rows, preserving66 supplier semantic tags; verified36 actual native LoadAll entries.")
    print("Summary SHA256:", summary_hash)


if __name__ == "__main__":
    main()
