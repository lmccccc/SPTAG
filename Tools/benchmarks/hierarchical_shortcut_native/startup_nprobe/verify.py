"""Independently reconcile every completed curve point against persisted native runs."""
import argparse
import configparser
import csv
import hashlib
import json
import math
from pathlib import Path
import shutil


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify(root):
    snapshot = root / "snapshot"
    prereg = json.loads((snapshot / "experiment/preregistration.json").read_text())
    schedule = json.loads((snapshot / "experiment/schedule.json").read_text())
    status = json.loads((root / "status.json").read_text())
    assert status["state"] == "completed" and status["measured_points"] == 198
    records = json.loads((root / "runs.json").read_text())
    assert [r["job"] for r in records] == schedule and len(records) == 468
    summary = json.loads((root / "summary.json").read_text())
    validation = json.loads((root / "validation.json").read_text())
    assert sha(root / "summary.json") == validation["summary_sha256"]
    assert sha(root / "summary.csv") == validation["summary_csv_sha256"]
    assert sha(root / "thresholds.json") == validation["thresholds_sha256"]
    expected = {(s, c, p) for s in prereg["scenarios"] for c in prereg["cases"] for p in prereg["grid"]}
    actual = {(r["scenario"], r["case"], r["nprobe"]) for r in summary}
    assert actual == expected and len(summary) == len(expected) == 198
    by_key = {}
    first_command_time = None
    for r in records:
        job = r["job"]
        directory = root / r["directory"]
        assert json.loads((directory / "record.json").read_text()) == r
        assert json.loads((directory / "native.exit.json").read_text())["returncode"] == 0
        assert json.loads((directory / "native.io.json").read_text())["direct_io"]
        command_file = directory / "native.command.json"
        first_command_time = min(first_command_time or command_file.stat().st_mtime_ns,
                                 command_file.stat().st_mtime_ns)
        command = json.loads(command_file.read_text())["command"]
        search = Path(command[command.index("--search-sweep-ini") + 1])
        assert search == snapshot / "experiment/configs" / f'{job["case"]}_{job["nprobe"]}_{job["kind"]}.ini'
        ini = configparser.ConfigParser()
        ini.read(search)
        assert ini["SearchSSDIndex"].getint("InternalResultNum") == job["nprobe"]
        found = []
        for name in ("stdout", "stderr"):
            path = directory / f"native.{name}.log"
            assert sha(path) == r[f"native_{name}"]["sha256"]
            with path.open() as stream:
                for line in stream:
                    if line.startswith("{") and '"engine":' in line:
                        found.append(json.loads(line))
        assert found == [r["native"]]
        count = 8 if job["kind"] == "fixture" else 1000
        assert r["native"]["queries"] == count and r["native"]["failed_queries"] == 0
        assert command[command.index("--max-queries") + 1] == str(count)
        assert command[command.index("--warmup") + 1] == ("0" if job["kind"] == "fixture" else "1000")
        if job["kind"] != "frozen":
            assert (directory / "queries.jsonl.gz").is_file()
            assert not (directory / "queries.jsonl").exists()
            assert r["validation"]["queries"] == count and r["validation"]["filter_violations"] == 0
            assert r["validation"]["recall_at_10"] == r["native"]["recall"]
        if job["kind"] == "plain":
            assert r["phases"] is None
            assert ini["SearchSSDIndex"]["LogPhaseTime"] == "false"
            assert ini["SearchSSDIndex"]["ShortcutProfile"] == "false"
            by_key.setdefault((job["scenario"], job["case"], job["nprobe"]), []).append(r)
    assert all(p.stat().st_mtime_ns <= first_command_time
               for p in (snapshot / "experiment/configs").glob("*.ini"))
    for point in summary:
        pair = by_key[point["scenario"], point["case"], point["nprobe"]]
        assert len(pair) == 2 and {r["job"]["repeat"] for r in pair} == {1, 2}
        assert pair[0]["validation"] == pair[1]["validation"]
        times = [r["native"]["mean_latency_ms"] for r in pair]
        assert point["ordinary_ms_runs"] == times
        assert point["ordinary_ms"] == sum(times) / 2
        assert math.isclose(point["qps"], 2000 / sum(times), rel_tol=1e-14)
        assert point["qps_min"] == 1000 / max(times) and point["qps_max"] == 1000 / min(times)
        assert point["qps_runs"] == [1000 / t for t in times]
        for r in pair:
            n, v = r["native"], r["validation"]
            assert point["recall_at_10"] == n["recall"] == v["recall_at_10"]
            for field, native_field in (("postings", "postings_per_query"),
                                        ("pages", "posting_page_reads_per_query"),
                                        ("disk_distances", "distance_computations_per_query")):
                assert point[field] == n[native_field]
            assert point["underfilled_queries"] == v["underfilled_queries"]
    with (root / "summary.csv").open() as stream:
        csv_rows = list(csv.DictReader(stream))
    assert len(csv_rows) == 198
    for c, point in zip(csv_rows, summary):
        assert (c["scenario"], c["case"], int(c["nprobe"])) == (
            point["scenario"], point["case"], point["nprobe"])
        assert float(c["recall_at_10"]) == point["recall_at_10"] and float(c["qps"]) == point["qps"]
    thresholds = json.loads((root / "thresholds.json").read_text())
    assert len(thresholds) == 36
    report = (root / "report.md").read_text()
    for t in thresholds:
        points = [p for p in summary if (p["scenario"], p["case"]) == (t["scenario"], t["case"])]
        candidates = [p for p in points if p["recall_at_10"] >= t["target_recall"]]
        best = max(candidates, key=lambda p: p["qps"]) if candidates else None
        assert t["observed_best"] == best and t["reached"] == (best is not None)
        assert t["maximum_observed_recall"] == max(p["recall_at_10"] for p in points)
        cells = (f'{best["nprobe"]} | {100*best["recall_at_10"]:.2f} | {best["qps"]:.3f} | {best["underfilled_queries"]}'
                 if best else f'- | max {100*t["maximum_observed_recall"]:.2f} | unreached | -')
        assert f'| {t["scenario"]} | {t["case"]} | {100*t["target_recall"]:.0f}% | {cells} |' in report
    proof = json.loads((snapshot / "capacity_guard_provenance.json").read_text())
    assert proof["changed_files"] == ["AnnService/FullHooks.h", "AnnService/src/Core/SPANN/SPANNIndex.cpp"]
    old = Path(proof["parent_toolchain"]) / "source"
    for name, digest in proof["before"].items():
        assert sha(old / name) == digest, name
    provenance = json.loads((root / "provenance.json").read_text())
    assert sha(snapshot / "spannaclbench") == provenance["native_binary"]["sha256"]
    assert sha(snapshot / "source.tar.gz") == provenance["source_archive"]["sha256"]
    destination = root / "independent_reconciliation.json"
    assert not destination.exists()
    shutil.copy2(Path(__file__), root / "independent_verifier.py")
    destination.write_text(json.dumps({
        "completed_points": 198, "reconciled_ordinary_raw_results": 396,
        "all_preregistered_native_processes": 468,
        "materialized_inis_predate_first_native_command": True,
        "every_point_recall_qps_underfill_work_matches_native_records": True,
        "thresholds_selected_only_from_measured_points": True,
        "csv_json_consistent": True, "parent_native_sources_unchanged": True,
        "summary_sha256": sha(root / "summary.json"), "report_sha256": sha(root / "report.md"),
        "verifier_sha256": sha(root / "independent_verifier.py"),
    }, indent=2) + "\n")
    print("PASS: all198 points,396 ordinary raw results,468 native processes; CSV/JSON/threshold report reconciled.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    verify(parser.parse_args().results.resolve())
