"""Independently reconcile native batch boundaries, scalar parity and final coordinates."""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import statistics
import subprocess

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("batch_runner", HERE / "run.py")
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


def parser_failure(root):
    directory = root / "invalid_array_fixture"
    directory.mkdir()
    command = [str(root / "snapshot/spannaclbench"), "--index", "must-not-load",
               "--queries", "must-not-read.npy", "--truth", "must-not-read.npy",
               "--search-sweep-ini", str(HERE / "invalid_empty.ini")]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    (directory / "stdout.log").write_text(result.stdout)
    (directory / "stderr.log").write_text(result.stderr)
    assert result.returncode == 2 and "Empty nprobe array" in result.stderr
    assert "NATIVE_INDEX_LOAD" not in result.stdout + result.stderr
    batch.write(directory / "record.json", {"command": command, "returncode": result.returncode,
                "native_load_calls": 0, "ini": batch.fingerprint(HERE / "invalid_empty.ini")})


def main(root):
    assert json.loads((root / "status.json").read_text())["state"] == "completed"
    rows = json.loads((root / "summary.json").read_text())
    runs = json.loads((root / "runs.json").read_text())
    fixtures = json.loads((root / "fixtures.json").read_text())
    schedule = json.loads((root / "snapshot/experiment/schedule.json").read_text())
    assert len(rows) == 198 and len(runs) == 36 and len(fixtures) == 45
    assert len({(r["scenario"], r["case"], r["nprobe"]) for r in rows}) == 198
    pairs, load_calls, capture_count = {}, 0, 0
    for i, run in enumerate(runs + fixtures):
        directory = root / run["directory"]
        assert json.loads((directory / "native.exit.json").read_text())["returncode"] == 0
        assert json.loads((directory / "native.io.json").read_text())["direct_io"]
        assert json.loads((directory / "native.command.json").read_text())["command"] == run["command"]
        logs = [directory / f"native.{part}.log" for part in ("stdout", "stderr")]
        assert batch.fingerprint(logs[0]) == run["native_stdout"]
        assert batch.fingerprint(logs[1]) == run["native_stderr"]
        records = batch.parse(logs, run["probes"], 1000 if i < 36 else 8)
        assert records == [r["native"] for r in run["points"]]
        load_calls += 1
        if i < 36:
            job = schedule[i]
            assert (run["scenario"], run["case"], run["repeat"], run["probes"]) == (
                job["scenario"], job["case"], job["repeat"], job["probes"])
        for point in run["points"]:
            path = directory / (point["native"]["capture_file"] + ".gz")
            digest, count = hashlib.sha256(), 0
            with gzip.open(path, "rb") as stream:
                for line in stream:
                    digest.update(line)
                    count += 1
            assert count == (1000 if i < 36 else 8)
            assert digest.hexdigest() == point["validation"]["raw_capture_sha256"]
            capture_count += 1
            if i < 36:
                key = point["scenario"], point["case"], point["nprobe"]
                pairs.setdefault(key, []).append(point)
    assert load_calls == 81 and capture_count == 513 and len(pairs) == 198
    for row in rows:
        key = row["scenario"], row["case"], row["nprobe"]
        pair = pairs[key]
        assert len(pair) == 2
        assert pair[0]["validation"] == pair[1]["validation"]
        assert batch.core(pair[0]["native"]) == batch.core(pair[1]["native"])
        ms = [p["native"]["mean_latency_ms"] for p in pair]
        assert row["ordinary_ms_runs"] == ms and row["ordinary_ms"] == statistics.mean(ms)
        assert row["qps"] == 1000 / statistics.mean(ms)
        assert row["qps_min"] == 1000 / max(ms) and row["qps_max"] == 1000 / min(ms)
        assert row["recall_at_10"] == pair[0]["validation"]["recall_at_10"]
        for field in ("underfilled_queries", "mean_returned", "selected_h1"):
            assert row[field] == pair[0]["validation"][field]
        if row["case"] == "supplier":
            assert row["supplier_degree_semantics"] == "predicate_valid_neighbors"
    provenance = json.loads((root / "provenance.json").read_text())
    for item in provenance["experiment_files"] + [provenance["native_binary"], provenance["source_archive"]]:
        assert batch.fingerprint(item["path"]) == item
    parser_failure(root)
    examples, node_details = [], {}
    for row in rows:
        if (row["scenario"], row["case"]) != ("unfilter", "supplier"):
            continue
        histogram = row["connectivity_evidence"]["trigger_physical_degree_histogram"]
        assert all(int(d) < 16 for d in histogram)
        assert sum(histogram.values()) == round(1000 * row["supplier_work"]["supplyCalls"])
        counts = {"frames": 0, "full32": 0, "full32_visited20": 0, "calls": 0, "startup_calls": 0}
        run = next(r for r in runs if r["directory"] == row["directories"][0])
        point = next(p for p in run["points"] if p["nprobe"] == row["nprobe"])
        path = root / run["directory"] / (point["native"]["capture_file"] + ".gz")
        with gzip.open(path, "rt") as stream:
            for q, line in enumerate(stream):
                query = json.loads(line)
                for frame in query["connectivity_audits"]:
                    degree = len(frame["ordinary"])
                    assert all(flag & 2 for _, flag in frame["ordinary"])
                    assert frame["before"] == degree
                    assert not frame["calls"] or degree < 16
                    counts["frames"] += 1
                    counts["full32"] += degree == 32
                    counts["full32_visited20"] += degree == 32 and frame["visited"] >= 20
                    counts["calls"] += frame["calls"]
                    counts["startup_calls"] += frame["calls"] and frame["phase"] == 1
                    if frame["calls"] and row["nprobe"] == 24:
                        node = str(frame["node"])
                        detail = node_details.setdefault(node, {
                            "physical_degree": degree, "ordinary_ids": [p[0] for p in frame["ordinary"]],
                            "calls": 0, "example_queries": []})
                        detail["calls"] += 1
                        if len(detail["example_queries"]) < 4:
                            detail["example_queries"].append(q)
        assert counts["calls"] == sum(histogram.values())
        examples.append({"nprobe": row["nprobe"], **counts,
                         "trigger_physical_degree_histogram": histogram})
    comparison = [r for r in rows if r["scenario"] == "unfilter" and r["nprobe"] == 24]
    batch.write(root / "unfilter_degree_audit.json", {
        "per_probe": examples, "nprobe24_trigger_nodes": node_details,
        "unfilter_nprobe24_comparison": comparison,
        "supplier_degree_semantics": "predicate_valid_neighbors",
    })
    shutil.copy2(Path(__file__), root / "verification_source.py")
    batch.write(root / "independent_reconciliation.json", {
        "ordinary_native_loads": 36, "ordinary_measurement_batches": 396, "points": 198,
        "array_fixture_native_loads": 36, "scalar_fixture_native_loads": 9,
        "compressed_captures_rehashed": capture_count,
        "raw_native_boundaries_order_and_lifecycle_checked": True,
        "summary_coordinates_recomputed_from_raw_native_outputs": True,
        "invalid_array_rejected_before_native_load": True,
        "unfilter_connectivity_frames_rechecked": sum(x["frames"] for x in examples),
        "no_eligible_degree32_frame_triggered_supplier": True,
        "summary_sha256": batch.fingerprint(root / "summary.json")["sha256"],
        "verification_source": batch.fingerprint(root / "verification_source.py"),
    })
    print(json.dumps({"points": len(rows), "ordinary_native_loads": 36,
                      "ordinary_batches": 396, "all_native_loads_including_fixtures": load_calls}, indent=2))
    for r in comparison:
        print(r["case"], "recall", r["recall_at_10"], "ms", r["ordinary_ms"], "qps", r["qps"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    main(parser.parse_args().results.resolve())
