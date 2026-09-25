"""Explicit smoke or ONE authorized stage. Never auto-continue to another group."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import time

import numpy as np
from prepare import HERE, DATA, TOOL, OUTPUT, CURRENT, GRID, CASES, STAGES, sha, write

WORK_FIELDS = ["postings", "scanned_records", "matched_records", "dedup_skips",
               "distance_evaluations", "pages", "logical_bytes", "physical_bytes"]


def require(value, message):
    if not value:
        raise RuntimeError(message)


def fingerprint(items):
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()


def freeze():
    target = OUTPUT / "snapshot"
    target.mkdir()
    shutil.copytree(HERE, target / "experiment", ignore=shutil.ignore_patterns("__pycache__"))
    original_auth = json.loads((TOOL / "original/authentication.json").read_text())
    current_auth = json.loads((CURRENT / "native_reuse_provenance.json").read_text())
    source_checks = {}
    for relative, digest in original_auth["verified"].items():
        path = TOOL / "original/source" / relative
        require(sha(path) == digest, "Original source changed: " + relative)
    for relative in ("AnnService/src/Core/BKT/BKTIndex.cpp", "AnnService/src/Core/SPANN/SPANNIndex.cpp",
                     "AnnService/inc/Core/Common/BKTree.h", "AnnService/inc/Core/Common/WorkSpace.h",
                     "Wrappers/src/CoreInterface.cpp", "AnnService/NativeSupplier.h",
                     "AnnService/PostingSupplier.h", "AnnService/FullHooks.h", "AnnService/NativeNeighborHooks.h"):
        digest = sha(CURRENT / "source" / relative)
        require(digest == current_auth["after"][relative], "Accepted native-default source changed")
        source_checks[relative] = digest
    require(sha(DATA / "comparisons/h1_native_default_admission_20260917/summary.json") ==
            "6917dd7c20901c77dce946fd81832383f0bcff8cb2aa30e7c1080433f9ef2c8d", "Wrong accepted current core")
    cores = {}
    for name, tool in (("original", TOOL / "original"), ("current", CURRENT)):
        linked = [tool / "source/Release" / filename for filename in
                  ("libSPTAGLibStatic.a", "libDistanceUtils.a", "libRaBitQ2Lib.a", "libzstd.a")]
        linked.append(tool / "build/AnnService/CMakeFiles/spannaclbench.dir/__/Wrappers/src/CoreInterface.cpp.o")
        files = [{"path": str(p), "sha256": sha(p), "mtime_ns": p.stat().st_mtime_ns} for p in linked]
        binary = TOOL / "harness" / f"matched-{name}"
        shutil.copyfile(binary, target / binary.name)
        shutil.copymode(binary, target / binary.name)
        cores[name] = {"linked_files": files, "fingerprint": fingerprint([(v["path"], v["sha256"]) for v in files]),
                       "binary_sha256": sha(binary), "toolchain": str(tool)}
    symbols = subprocess.check_output(["nm", "-C", "--defined-only", str(target / "matched-original")], text=True)
    require(not any(x in symbols for x in ("H1Supplier::", "NativeReuse::", "ShortcutFull::", "BenchmarkRestore")),
            "Original contains supplier symbols")
    source = (HERE / "MatchedBench.cpp").read_text()
    body = source.split("const auto start = std::chrono::steady_clock::now();", 1)[1].split(
        "const auto finish = std::chrono::steady_clock::now();", 1)[0]
    require(not any(x in body for x in ("Shortcut", "capture", "#if", "Observation")),
            "Adapter or capture in ordinary body")
    write(OUTPUT / "cores.json", {"cores": cores, "common_source_sha256": sha(HERE / "MatchedBench.cpp"),
        "common_timed_body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "original_authentication": original_auth, "current_source_checks": source_checks,
        "original_audit": json.loads((TOOL / "original_audit_crosscheck.json").read_text())})


def verify_protected():
    registration = json.loads((OUTPUT / "registration.json").read_text())
    require(sha(TOOL / "buffered_view/tenant_0/indexloader.ini") == registration["view_loader_sha256"],
            "Buffered loader changed")
    for name, path in registration["view_targets"].items():
        require(str((TOOL / "buffered_view/tenant_0" / name).resolve()) == path, "Index target changed")
    for item in json.loads((OUTPUT / "input_hashes.json").read_text()):
        p = Path(item["path"])
        stat = p.stat()
        require(stat.st_size == item["bytes"], "Protected input size changed")
        if stat.st_mtime_ns != item["mtime_ns"]:
            require(sha(p) == item["sha256"], "Protected input bytes changed")
    if (OUTPUT / "cores.json").exists():
        for core in json.loads((OUTPUT / "cores.json").read_text())["cores"].values():
            for item in core["linked_files"]:
                path = Path(item["path"])
                if path.stat().st_mtime_ns != item["mtime_ns"]:
                    require(sha(path) == item["sha256"], "Frozen core object/library changed")
    return registration


def process(command, directory):
    write(directory / "command.json", {"command": command, "cwd": str(directory)})
    start = time.monotonic()
    observed = {}
    def save_io():
        with (directory / "io.json").open("w") as stream:
            json.dump({"buffered": bool(observed), "observations": [
                {"target": target, "flags": flags, "samples": samples}
                for (target, flags), samples in observed.items()]}, stream, indent=2)
    with (directory / "stdout.log").open("x") as stdout, (directory / "stderr.log").open("x") as stderr:
        child = subprocess.Popen(command, cwd=directory, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
        try:
            while child.poll() is None:
                fdroot = Path("/proc") / str(child.pid)
                try:
                    descriptors = list((fdroot / "fd").iterdir())
                except (FileNotFoundError, ProcessLookupError, PermissionError) as error:
                    child.wait(timeout=5)
                    require(child.returncode == 0 and observed, "Procfs failure without validated native exit")
                    write(directory / "sampling_exit_race.json", {"error": str(error), "returncode": 0})
                    break
                for fd in descriptors:
                    try:
                        target = os.readlink(fd)
                        if not target.endswith("/SPTAGFullList.bin"):
                            continue
                        fields = dict(line.split(":", 1) for line in
                                      (fdroot / "fdinfo" / fd.name).read_text().splitlines() if ":" in line)
                        flags = int(fields["flags"].strip(), 8)
                        require(not flags & os.O_DIRECT, "Actual SSD handle unexpectedly uses O_DIRECT")
                        observed[(target, flags)] = observed.get((target, flags), 0) + 1
                        save_io()
                    except (FileNotFoundError, ProcessLookupError):
                        continue
                    except PermissionError as error:
                        child.wait(timeout=5)
                        require(child.returncode == 0 and observed, "Descriptor denied while native remained active")
                        write(directory / "sampling_exit_race.json", {"error": str(error), "returncode": 0})
                        break
                time.sleep(.02)
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=30)
            write(directory / "exit.json", {"returncode": child.returncode})
    save_io()
    require(child.returncode == 0 and observed, "Native failure or unverified buffered IO: " + str(directory))
    return time.monotonic() - start


def invoke(scenario, case, repeat, smoke, registration, provenance):
    kind = "smoke" if smoke else "stage"
    directory = OUTPUT / f"{kind}_{scenario}_{case}_r{repeat}"
    if (directory / "record.json").exists():
        return json.loads((directory / "record.json").read_text())
    require(not directory.exists(), "Incomplete attempt preserved; inspect it, do not retry silently")
    directory.mkdir()
    label = "smoke" if smoke else f"r{repeat}"
    config_name = f"{scenario}_{case}_{label}.ini"
    config = OUTPUT / "snapshot/experiment/configs" / config_name
    require(sha(config) == registration["config_hashes"][config_name], "Native INI changed")
    core = "original" if case == "h1_original" else "current"
    binary = OUTPUT / "snapshot" / f"matched-{core}"
    require(sha(binary) == provenance["cores"][core]["binary_sha256"], "Native executable changed")
    wall = process(["numactl", "--cpunodebind=2", "--membind=2", str(binary),
                    "--config", str(config)], directory)
    text = (directory / "stdout.log").read_text()
    require(text.count("MATCHED_LOAD calls=1") == 1, "Not a single native LoadAll")
    for label in ("Vector (160091,128)", "BKT (1,160093)", "RNG (160091,32)",
                  "Vector (4098,128)", "BKT (1,4100)", "RNG (4098,32)"):
        require(text.count("Load " + label + " Finish!") == 1, "Physical component reload/mismatch")
    native = [json.loads(line) for line in text.splitlines() if line.startswith('{"engine":')]
    probes = [24] if smoke else GRID if repeat == 1 else GRID[::-1]
    require([r["nprobe"] for r in native] == probes, "Wrong native array execution")
    truth_files = json.loads((DATA / "query/workloads.json").read_text())["truth"]
    truth = np.load(truth_files[scenario]["ids"], mmap_mode="r")
    points = []
    for position, row in enumerate(native):
        require(row["case"] == case and row["scenario"] == scenario and row["queries"] == 1000 and
                row["measure_offset"] == row["failed_queries"] == 0 and row["workspace_resets"] == position + 1 and
                row["probe_count"] == len(probes) and row["index_load_count"] == 1, "Native protocol mismatch")
        prefix = directory / f'nprobe_{row["nprobe"]}'
        paths = [Path(str(prefix) + suffix) for suffix in (".ids.i32", ".dist.f32", ".work.u64")]
        ids = np.fromfile(paths[0], dtype="<i4").reshape(1000, 10)
        distances = np.fromfile(paths[1], dtype="<f4").reshape(1000, 10)
        work = np.fromfile(paths[2], dtype="<u8").reshape(1000, 8)
        require(np.all(np.isfinite(distances[ids >= 0])) and np.all(distances[ids >= 0] >= 0), "Invalid distances")
        require(np.all(ids[ids >= 0] < 1000000), "Native result outside the dataset")
        require(all(len(set(r[r >= 0])) == len(r[r >= 0]) for r in ids), "Duplicate native result")
        recall = sum(len(set(r[r >= 0]).intersection(truth[i, :10])) for i, r in enumerate(ids)) / 10000
        require(abs(recall - row["recall"]) < 1e-10, "Final recall mismatch")
        if case == "supplier" and scenario == "unfilter":
            require(row["native_default_queries_capture"] == row["h1_capture_queries"] == 1000 and
                    all(row[k] == 0 for k in ("helper_calls_capture", "parent_distances_capture",
                        "child_distances_capture", "csr_members_capture")), "Unfilter supplier did upper work")
        points.append({"native": row, "payload_hashes": {p.suffixes[-2]: sha(p) for p in paths},
            "ids_sha256": sha(paths[0]), "distances_sha256": sha(paths[1]), "work_sha256": sha(paths[2]),
            "mean_returned": float((ids >= 0).sum() / 1000),
            "underfill": int(np.count_nonzero((ids >= 0).sum(axis=1) < 10)),
            "actual_work": dict(zip(WORK_FIELDS, work.mean(axis=0).tolist()))})
    record = {"scenario": scenario, "case": case, "repeat": repeat, "points": points,
              "process_seconds": wall, "directory": str(directory)}
    write(directory / "record.json", record)
    print(case, scenario, repeat, [(p["native"]["nprobe"], p["native"]["recall"],
                                   p["native"]["mean_latency_ms"]) for p in points], flush=True)
    return record


def execute(stage, smoke):
    forbidden = [name for name in os.environ if name.startswith(("SPTAG_", "SPANN_", "SHORTCUT_")) or
                 name in ("LD_PRELOAD", "OMP_NUM_THREADS", "OMP_PROC_BIND", "OMP_PLACES")]
    require(not forbidden, "Native INIs are sole authority; remove environment overrides: " + str(forbidden))
    registration = verify_protected()
    provenance = json.loads((OUTPUT / "cores.json").read_text())
    records = []
    scenarios = STAGES[stage]
    for repeat in (1, 2):
        for position, scenario in enumerate(scenarios if repeat == 1 else scenarios[::-1]):
            order = CASES if repeat == 1 else ["supplier", "h3", "h1", "h1_original"]
            order = order[position:] + order[:position]
            for case in order:
                records.append(invoke(scenario, case, repeat, smoke, registration, provenance))
    rows = []
    for scenario in scenarios:
        for case in CASES:
            pair = [r for r in records if r["scenario"] == scenario and r["case"] == case]
            for point in pair[0]["points"]:
                n = point["native"]["nprobe"]
                other = next(p for p in pair[1]["points"] if p["native"]["nprobe"] == n)
                require(all(point[k] == other[k] for k in
                            ("ids_sha256", "distances_sha256", "work_sha256")), "Repetition/array parity failed")
                ms = [p["native"]["mean_latency_ms"] for p in (point, other)]
                core = "original" if case == "h1_original" else "current"
                rows.append({"case": case, "scenario": scenario, "nprobe": n,
                    "recall": point["native"]["recall"], "recall_at_10": point["native"]["recall"],
                    "mean_latency_ms": statistics.mean(ms), "qps": 1000 / statistics.mean(ms),
                    "repetitions": [{"repeat": i + 1, "mean_latency_ms": value} for i, value in enumerate(ms)],
                    "mean_latency_ms_runs": ms, "min_ms": min(ms), "max_ms": max(ms),
                    "mean_returned": point["mean_returned"], "underfill": point["underfill"],
                    "actual_work": point["actual_work"], "io": "buffered",
                    "protocol_fingerprint": registration["protocol_sha256"],
                    "index_fingerprint": registration["index_fingerprint"],
                    "core_fingerprint": provenance["cores"][core]["fingerprint"],
                    "common_timed_body_fingerprint": provenance["common_timed_body_sha256"],
                    "sweep_execution": "single_load_nprobe_array", "nprobe_ini_api": "SearchSweep.NProbe",
                    "result_ids_sha256": point["ids_sha256"], "result_distances_sha256": point["distances_sha256"],
                    "native_work_sha256": point["work_sha256"]})
    for scenario in scenarios:
        for original in [r for r in rows if r["case"] == "h1_original" and r["scenario"] == scenario]:
            current = next(r for r in rows if r["case"] == "h1" and r["scenario"] == scenario and
                           r["nprobe"] == original["nprobe"])
            require(all(original[key] == current[key] for key in
                        ("result_ids_sha256", "result_distances_sha256", "native_work_sha256")),
                    "Original/current H1 final outputs or work differ")
            if scenario == "unfilter":
                supplier = next(r for r in rows if r["case"] == "supplier" and r["scenario"] == scenario and
                                r["nprobe"] == original["nprobe"])
                require(all(original[key] == supplier[key] for key in
                            ("result_ids_sha256", "result_distances_sha256", "native_work_sha256")),
                        "Unfilter supplier changed original native outputs/work")
    suffix = "smoke" if smoke else f"stage_{stage}"
    summary = OUTPUT / f"summary.{suffix}.json"
    if summary.exists():
        require(json.loads(summary.read_text()) == rows, "Completed summary changed")
    else:
        write(summary, rows)
        write(OUTPUT / f"operations.{suffix}.json", records)
    status = json.loads((OUTPUT / "status.json").read_text())
    if smoke:
        status["smoke_complete"] = True
    elif stage not in status["completed_stages"]:
        status["completed_stages"].append(stage)
    with (OUTPUT / "status.json").open("w") as stream:
        json.dump(status, stream, indent=2)
    verify_protected()
    if not smoke:
        from renderer_contract import export
        export(summary_name=f"summary.{suffix}.json")


def finalize():
    verify_protected()
    status = json.loads((OUTPUT / "status.json").read_text())
    require(set(status["completed_stages"]) == set(STAGES), "All three explicitly authorized stages required")
    rows = [row for stage in STAGES
            for row in json.loads((OUTPUT / f"summary.stage_{stage}.json").read_text())]
    expected = {(case, scenario, probe) for case in CASES
                for scenarios in STAGES.values() for scenario in scenarios for probe in GRID}
    require(len(rows) == len(expected) == 264 and
            {(r["case"], r["scenario"], r["nprobe"]) for r in rows} == expected,
            "Incomplete or duplicate full matrix")
    require(all(len(r["repetitions"]) == 2 for r in rows), "Missing ordinary repetitions")
    require(len({r["protocol_fingerprint"] for r in rows}) == 1 and
            len({r["index_fingerprint"] for r in rows}) == 1, "Mixed comparison protocols")
    write(OUTPUT / "summary.json", rows)
    from renderer_contract import export
    export(summary_name="summary.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "smoke", "stage", "finalize"))
    parser.add_argument("--stage", choices=STAGES)
    options = parser.parse_args()
    if options.action == "stage":
        require(options.stage is not None, "Explicit stage required")
        execute(options.stage, False)
    elif options.action == "smoke":
        require(options.stage is None, "Smoke has a fixed unfilter/broad scope")
        execute("unfilter_broad", True)
    elif options.action == "freeze":
        freeze()
    else:
        require(options.stage is None, "Finalize does not run a stage")
        finalize()
