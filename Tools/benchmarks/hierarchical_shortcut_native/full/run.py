#!/usr/bin/env python3
"""Bounded, paired native full-query experiment, with untimed per-query parity."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import statistics
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from official_benchmark_config import reject_environment_overrides, require, write_json
from run_full_phase_ab import validate_phase_balance
from run_vanilla_spann_build import read_ini
from run_vanilla_spann_comparison import h3_core_work, h3_records, run_native


def fingerprint(path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(data)
    st = path.stat()
    return {"path": str(path), "bytes": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": digest.hexdigest()}


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def run(config):
    reject_environment_overrides()
    config = config.resolve(strict=True)
    plan = read_ini(config)["Experiment"]
    output = Path(plan["OutputDirectory"])
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "snapshot"
    shutil.copytree(config.parent, snapshot / "controls", ignore=shutil.ignore_patterns("__pycache__"))
    toolchain = Path(plan["Toolchain"])
    shutil.copy2(plan["Binary"], snapshot / "spannaclbench")
    for name in ("authentication.json", "integration.json", "configure.log", "build.log"):
        shutil.copy2(toolchain / name, snapshot / name)
    with tarfile.open(snapshot / "source.tar.gz", "w:gz") as archive:
        archive.add(toolchain / "source", arcname="source",
                    filter=lambda item: None if "/Release/" in item.name else item)
        archive.add(toolchain / "frozen", arcname="frozen")
    protected = [Path(p["path"]) for p in json.loads(
        Path(plan["ProtectedProvenance"]).read_text())["protected_input_sha256"]]
    protected += [Path(plan["Binary"]), Path(plan["FrozenBinary"])]
    protected += list((config.parents[4] / "AnnService").glob("**/*.h"))
    protected += list((config.parents[4] / "AnnService").glob("**/*.cpp"))
    protected += [Path(read_ini(config.parent / "ordinary_plain.ini")[
        "SearchSSDIndex"]["ShortcutEdges"])]
    before = [fingerprint(p) for p in sorted(set(protected))]
    write_json(output / "provenance.json", {
        "protected": before, "config": fingerprint(config),
        "source_snapshot": fingerprint(snapshot / "source.tar.gz"),
        "scope": "Native SearchWithPredicate -> full SPANN SearchIndex -> H1 -> unchanged H/O/own-head/SSD",
    })
    historical = next(row["native_work"] for row in json.loads(
        Path(plan["Reference"]).read_text()) if row["case"] == "flat24")
    names = plan["Cases"].split(",")
    count, warmup = plan.getint("QueryCount"), plan.getint("Warmup")
    require(count == warmup == 1000 and plan.getint("Repeats") == 3, "Unexpected bounded protocol")
    standard = config.parents[2] / "configs/sift1m_full_phase/flat24_plain.ini"
    diagnostic = config.parents[2] / "configs/sift1m_same_posting/flat24_diagnostic.ini"
    require(standard.exists() and diagnostic.exists(), "Missing frozen native controls")
    shutil.copy2(standard, snapshot / "frozen_plain.ini")
    shutil.copy2(diagnostic, snapshot / "frozen_diagnostic.ini")
    common = None
    for name in names:
        pair = [dict(read_ini(config.parent / f"{name}_{mode}.ini")["SearchSSDIndex"])
                for mode in ("plain", "profile")]
        for flag in ("logphasetime", "shortcutprofile"):
            require(pair[0].pop(flag) == "false" and pair[1].pop(flag) == "true",
                    "Incorrect profiling controls")
        require(pair[0] == pair[1], "Profiling changed algorithm controls")
        base = {k: v for k, v in pair[0].items() if k not in ("shortcutmode", "shortcutcap")}
        require(common is None or common == base, "Mismatched shared SSD controls")
        common = base
    runs, results, heads, work, nav_work = [], {}, {}, {}, {}

    def execute(name, mode, repeat, frozen=False, diag=False):
        directory = output / f"{name}_{mode}_r{repeat}"
        directory.mkdir()
        search = ((snapshot / "frozen_diagnostic.ini") if diag else
                  (snapshot / "frozen_plain.ini") if frozen else
                  snapshot / "controls" / f"{name}_{mode}.ini")
        binary = plan["FrozenBinary"] if frozen else str(snapshot / "spannaclbench")
        command = ["numactl", f"--cpunodebind={plan.getint('CPUNode')}",
                   f"--membind={plan.getint('MemoryNode')}", binary,
                   "--index", plan["Index"], "--queries", plan["Queries"], "--truth", plan["Truth"],
                   "--search-sweep-ini", str(search), "--value-type", "Float", "--topk", "10",
                   "--warmup", str(warmup), "--measure-offset", "0", "--max-queries", str(count)]
        logs = run_native(command, directory, "native", 0.2, True)
        profile = mode == "profile" or diag
        # Capture is a separate 1000-query pass BEFORE warmup, never in the timer.
        row, phase = h3_records(logs, count, warmup if frozen else warmup + count, 24, profile)
        core = h3_core_work(row)
        if name in ("ordinary", "frozen"):
            require(core == historical, f"{name}: frozen recall/work parity failure")
        if name in work:
            require(core == work[name], f"{name}: repetition/profile changed native SSD work")
        work[name] = core
        if not diag:
            for log in logs:
                require("DUMPHEADS" not in log.read_text(), "Head logging contaminated timing")
        if phase:
            validate_phase_balance(phase)
        if not frozen:
            query_rows = rows(directory / "queries.jsonl")
            require(len(query_rows) == count, "Wrong output query count")
            current_results = [r["results"] for r in query_rows]
            current_heads = [r["heads"] for r in query_rows]
            require(all(len(r) == 24 and len({p[0] for p in r}) == 24 and
                        all(p[0] >= 0 for p in r) for r in current_heads), "Invalid selected H1s")
            require(all(len(r) == 10 and len({p[0] for p in r}) == 10 for r in current_results),
                    "Invalid final top10")
            if name in results:
                require(current_results == results[name], f"{name}: final IDs/distances changed")
                require(current_heads == heads[name], f"{name}: selected H1 IDs/distances changed")
            results[name], heads[name] = current_results, current_heads
            if profile:
                budget = read_ini(search)["SearchSSDIndex"].getint("ShortcutCap")
                require(all(r["calls"] > 0 and (not budget or r["calls"] <= budget)
                            and (not r["exhausted"] or r["calls"] == budget)
                            for r in query_rows), "Hard callback cap violated")
                current_work = [{k: r[k] for k in ("calls", "adjacency", "shortcut_calls", "exhausted")}
                                for r in query_rows]
                if name in nav_work:
                    require(current_work == nav_work[name], "Navigation work changed across repeats")
                nav_work[name] = current_work
        if diag:
            captured = []
            for log in logs:
                for line in log.read_text().splitlines():
                    if "DUMPHEADS q=" in line:
                        tail = line.split(" :", 1)[1]
                        captured.append([[int(a), float(b)] for a, b in re.findall(
                            r"(\d+):([0-9.eE+-]+)", tail)])
            require(len(captured) == 2 * count and captured[:count] == heads["ordinary"] and
                    captured[count:] == heads["ordinary"], "Frozen selected-H1 parity failure")
            write_json(output / "frozen_head_parity.json", {"queries": count, "ordered_ids_and_float_distances_equal": True})
        runs.append({"case": name, "mode": mode, "repeat": repeat, "native": row, "phase": phase})
        write_json(output / "runs.json", runs)
        print(f"{directory.name}: recall={row['recall']:.4f} ms={row['mean_latency_ms']:.6f}", flush=True)

    write_json(output / "status.json", {"state": "running"})
    try:
        execute("ordinary", "plain", 0)
        execute("frozen", "diagnostic", 0, frozen=True, diag=True)
        order = ["frozen", *names]
        for repeat in range(1, plan.getint("Repeats") + 1):
            shift = (repeat - 1) * 2 % len(order)
            for name in order[shift:] + order[:shift]:
                if name == "frozen":
                    execute(name, "plain", repeat, frozen=True)
                else:
                    for mode in (("plain", "profile") if repeat % 2 else ("profile", "plain")):
                        execute(name, mode, repeat)
        summary = []
        for name in order:
            timing = [r["native"]["mean_latency_ms"] for r in runs
                      if r["case"] == name and r["mode"] == "plain" and r["repeat"] > 0]
            phases = [r["phase"] for r in runs
                      if r["case"] == name and r["mode"] == "profile"]
            means = {k: statistics.mean(r[k] for r in phases) for k in phases[0]} if phases else {}
            w = work[name]
            distance = statistics.mean(r["calls"] for r in nav_work[name]) if name in nav_work else None
            summary.append({
                "case": name, "recall_at_10": w["recall"],
                "postings": w["postings_per_query"], "pages": w["posting_page_reads_per_query"],
                "disk_distances": w["distance_computations_per_query"],
                "head_distances": distance,
                "total_distances": distance + w["distance_computations_per_query"] if distance else None,
                "adjacency": statistics.mean(r["adjacency"] for r in nav_work[name]) if name in nav_work else None,
                "shortcut_distances": statistics.mean(r["shortcut_calls"] for r in nav_work[name]) if name in nav_work else None,
                "ordinary_ms": statistics.mean(timing), "ordinary_ms_runs": timing,
                "qps_from_mean_ms": 1000 / statistics.mean(timing),
                "profile_navigation_ms": means.get("graphOther"),
                "profile_retrieval_non_scan_ms": means.get("io"),
                "profile_scan_ms": means.get("scan"), "phases": means,
            })
        write_json(output / "summary.json", summary)
        after = [fingerprint(p) for p in sorted(set(protected))]
        require(before == after, "Protected source/index/binary changed")
        write_json(output / "input_verification.json", {"unchanged": True, "files": len(before)})
        write_json(output / "status.json", {
            "state": "completed", "native_runs": len(runs),
            "counted_uncounted_ordered_heads_and_final_results_equal": True,
            "capture_measured_final_results_and_work_equal": True,
            "frozen_baseline_work_and_head_parity": True,
        })
    except Exception as error:
        write_json(output / "status.json", {"state": "failed", "error": str(error)})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    run(parser.parse_args().config)
