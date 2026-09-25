"""Only bounded fixtures and a two-repeat Broad repair comparison; no curve driver."""
import argparse
import configparser
import json
import os
from pathlib import Path
import statistics
import time
import numpy as np
from prepare import HERE, DATA, TOOL, OUTPUT, BASE, MATCHED, sha, write
from process import process, require

PAYLOADS = (".ids.i32", ".dist.f32", ".work.u64")
NATIVE_PAYLOADS = (".native.u64", ".head.i32", ".head.f32", ".own.i32")
WORK = ["postings", "scanned_records", "matched_records", "dedup_skips",
        "distance_evaluations", "pages", "logical_bytes", "physical_bytes"]
NATIVE = ["head_distances", "routing_distances", "child_distances", "checked", "calls", "returns",
          "parent_distances", "members", "signature_checks", "signature_rejects", "h2_rows", "h3_rows",
          "queue_offers", "queue_accepted", "queue_rejected", "filtered", "default", "original_distance"]
REPAIR = ["fused_rows", "fused_members", "degree_visited", "degree_fresh", "traversal_reuse",
          "result_reuse", "partial_rows", "short_row_proof_qualifications", "batch_rows",
          "csr_qualifications", "csr_traversal_reuse", "qualifications", "unique_qualifications",
          "repeated_qualifications"]


def protect():
    forbidden = [k for k in os.environ if k.startswith(("SPTAG_", "SPANN_", "SHORTCUT_")) or
                 k in ("LD_PRELOAD", "OMP_NUM_THREADS", "OMP_PROC_BIND", "OMP_PLACES")]
    require(not forbidden, "Ordinary environment overrides: " + str(forbidden))
    stop = json.loads((MATCHED / "OPERATOR_STOP.json").read_text())
    require(not stop["resume_allowed"], "The stopped curve must remain stopped")
    registration = json.loads((MATCHED / "registration.json").read_text())
    view = DATA / "toolchains/matched_baseline_20260917/buffered_view/tenant_0"
    require(sha(view / "indexloader.ini") == registration["view_loader_sha256"], "Loader changed")
    for name, target in registration["view_targets"].items():
        require(str((view / name).resolve()) == target, "Shared data target changed")
    for item in json.loads((MATCHED / "input_hashes.json").read_text()):
        path = Path(item["path"])
        require(path.stat().st_size == item["bytes"], "Protected input size changed")
        if path.stat().st_mtime_ns != item["mtime_ns"]:
            require(sha(path) == item["sha256"], "Protected input changed")
    for core in json.loads((MATCHED / "cores.json").read_text())["cores"].values():
        for item in core["linked_files"]:
            require(sha(item["path"]) == item["sha256"], "Frozen core changed")
    for target, digest in json.loads((OUTPUT / "repair_sources.json").read_text()).items():
        require(sha(TOOL / "source" / target) == digest, "Repair source changed")
    return {"protected_metadata_and_core_hashes_valid": True,
            "index_fingerprint": registration["index_fingerprint"]}


def freeze():
    require((TOOL / "harness/native-tests").exists(), "Native fixtures must be built")
    snapshot = OUTPUT / "runtime.json"
    binaries = {"bad": TOOL / "harness/matched-bad", "repair": TOOL / "harness/matched-repair",
                "original": TOOL / "matched-original", "h3": TOOL / "matched-current"}
    source = (HERE / "MatchedBench.cpp").read_text()
    timed = source.split("const auto start = std::chrono::steady_clock::now();", 1)[1].split(
        "const auto finish = std::chrono::steady_clock::now();", 1)[0]
    import hashlib
    body_sha = hashlib.sha256(timed.encode()).hexdigest()
    require(body_sha == json.loads((MATCHED / "cores.json").read_text())["common_timed_body_sha256"],
            "Accepted ordinary timed body changed")
    value = {"binaries": {k: {"path": str(v), "sha256": sha(v)} for k, v in binaries.items()},
             "harness_source_sha256": sha(HERE / "MatchedBench.cpp"),
             "timed_body_sha256": body_sha,
             "repair_libraries": {p.name: sha(p) for p in sorted((TOOL / "source/Release").glob("*.a"))}}
    if snapshot.exists():
        require(json.loads(snapshot.read_text()) == value, "Frozen repair runtime changed")
    else:
        write(snapshot, value)
    return binaries


def invoke(label, variant, config, binaries, diagnostic=False):
    directory = OUTPUT / label
    directory.mkdir()
    cfg = configparser.ConfigParser()
    cfg.read(config)
    count = 32 if diagnostic else 1000
    require(cfg["Benchmark"]["Warmup"] == cfg["Benchmark"]["MaxQueries"] == str(count), "Wrong query window")
    require(cfg["Benchmark"].get("DiagnosticOnly", "false") == str(diagnostic).lower(), "Wrong timing class")
    elapsed = process(["numactl", "--cpunodebind=2", "--membind=2", str(binaries[variant]),
                       "--config", str(config)], directory)
    text = (directory / "stdout.log").read_text()
    require(text.count("MATCHED_LOAD calls=1") == 1, "Not one native index load")
    for component in ("Vector (160091,128)", "BKT (1,160093)", "RNG (160091,32)",
                      "Vector (4098,128)", "BKT (1,4100)", "RNG (4098,32)"):
        require(text.count("Load " + component + " Finish!") == 1, "Component reload/mismatch")
    rows = [json.loads(line) for line in text.splitlines() if line.startswith('{"engine":')]
    require(len(rows) == 1, "Only a single predetermined point is authorized")
    row = rows[0]
    require(row["queries"] == count and row["index_load_count"] == row["workspace_resets"] ==
            row["probe_count"] == 1 and row["failed_queries"] == row["measure_offset"] == 0, "Protocol mismatch")
    prefix = directory / f'nprobe_{row["nprobe"]}'
    payloads = {s: Path(str(prefix) + s) for s in PAYLOADS}
    ids = np.fromfile(payloads[".ids.i32"], dtype="<i4").reshape(count, 10)
    distances = np.fromfile(payloads[".dist.f32"], dtype="<f4").reshape(count, 10)
    work = np.fromfile(payloads[".work.u64"], dtype="<u8").reshape(count, 8)
    truth = np.load(cfg["Benchmark"]["Truth"], mmap_mode="r")
    recall = sum(len(set(q[q >= 0]) & set(truth[i, :10])) for i, q in enumerate(ids)) / (count * 10)
    require(abs(recall - row["recall"]) < 1e-10, "Recall does not match actual final IDs")
    require(np.all(np.isfinite(distances[ids >= 0])), "Invalid final distance")
    native = None
    repair = None
    if variant in ("bad", "repair"):
        payloads.update({s: Path(str(prefix) + s) for s in NATIVE_PAYLOADS})
        native = np.fromfile(payloads[".native.u64"], dtype="<u8").reshape(count, 18)
        if row["scenario"] == "unfilter":
            require(np.all(native[:, [2, 4, 5, 6, 7, 8, 9, 10, 11, 15]] == 0) and
                    np.all(native[:, 16:] == 1), "Unfilter did upper work or lost default admission")
        else:
            require(np.all(native[:, 15] == 1) and np.all(native[:, 16] == 0), "Real predicate bypassed")
    if variant == "repair":
        repair = np.fromfile(str(prefix) + ".repair.u64", dtype="<u8").reshape(count, 14)
    record = {"label": label, "variant": variant, "directory": str(directory),
        "diagnostic_only": diagnostic, "native": row, "process_seconds": elapsed,
        "config": str(config), "config_sha256": sha(config),
        "payloads": {s: sha(p) for s, p in payloads.items()},
        "ssd_mean": dict(zip(WORK, work.mean(axis=0).tolist())),
        "native_mean": None if native is None else dict(zip(NATIVE, native.mean(axis=0).tolist())),
        "repair_mean_capture_only": None if repair is None else dict(zip(REPAIR, repair.mean(axis=0).tolist()))}
    write(directory / "record.json", record)
    print(label, row["recall"], row["mean_latency_ms"], flush=True)
    return record


def parity(a, b):
    require(a["payloads"] == b["payloads"], "Exact final/head/own/native/SSD work parity failed")


def fixtures():
    protect()
    binaries = freeze()
    records = []
    for scenario in ("medium_tag", "extreme_tag", "numeric", "mixed_dnf"):
        config = HERE / f"configs/fixture_{scenario}.ini"
        cfg = configparser.ConfigParser()
        cfg.optionxform = str
        cfg.read(MATCHED / f"snapshot/experiment/configs/{scenario}_supplier_r1.ini")
        cfg["Benchmark"].update(Warmup="32", MaxQueries="32", DiagnosticOnly="true")
        cfg["SearchSweep"]["NProbe"] = "[24]"
        with config.open("x") as out:
            cfg.write(out, space_around_delimiters=False)
        pair = [invoke(f"fixture_{scenario}_{variant}", variant, config, binaries, True)
                for variant in ("bad", "repair")]
        parity(*pair)
        records.extend(pair)
    write(OUTPUT / "fixtures.json", {"exact_parity": True, "records": records, "protection": protect()})


def measure():
    require((OUTPUT / "fixtures.json").exists(), "Four real-predicate fixtures required")
    before = protect()
    binaries = freeze()
    start = time.monotonic()
    records = []
    order = [("bad", "broad_bad24"), ("repair", "broad_bad24"),
             ("original", "broad_original80"), ("h3", "broad_h3_32")]
    for repeat, points in enumerate((order, order[::-1]), 1):
        for variant, name in points:
            records.append(invoke(f"broad_{variant}_r{repeat}", variant, HERE / f"configs/{name}.ini", binaries))
    for repeat, variants in enumerate((("bad", "repair"), ("repair", "bad")), 1):
        for variant in variants:
            records.append(invoke(f"unfilter_{variant}_r{repeat}", variant, HERE / "configs/unfilter24.ini", binaries))
    rows = []
    for scenario in ("broad_tag", "unfilter"):
        variants = ("bad", "repair", "original", "h3") if scenario == "broad_tag" else ("bad", "repair")
        selected = {}
        for variant in variants:
            pair = [r for r in records if r["variant"] == variant and r["native"]["scenario"] == scenario]
            parity(*pair)
            ms = [r["native"]["mean_latency_ms"] for r in pair]
            selected[variant] = pair[0]
            rows.append({"scenario": scenario, "variant": variant, "nprobe": pair[0]["native"]["nprobe"],
                "recall_at_10": pair[0]["native"]["recall"], "ordinary_ms": statistics.mean(ms),
                "ordinary_ms_runs": ms, "qps": 1000 / statistics.mean(ms),
                "ssd_mean": pair[0]["ssd_mean"], "native_mean_capture_only": pair[0]["native_mean"],
                "repair_mean_capture_only": pair[0]["repair_mean_capture_only"]})
        parity(selected["bad"], selected["repair"])
    write(OUTPUT / "summary.json", {"rows": rows, "exact_bad_repair_parity": True,
        "ordinary_batches": len(records), "single_load_processes": len(records),
        "ordinary_seconds": sum(r["native"]["ordinary_seconds"] for r in records),
        "native_process_seconds": sum(r["process_seconds"] for r in records),
        "operation_seconds": time.monotonic() - start, "protection_before": before,
        "protection_after": protect(), "no_curve_resumption": True})
    write(OUTPUT / "operations.json", records)
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("fixtures", "measure"))
    globals()[parser.parse_args().phase]()
