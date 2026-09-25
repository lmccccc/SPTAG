"""Execute the preregistered, ordinary native nprobe sweep with streaming audits."""
import argparse
import csv
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import shutil
import statistics
import struct
import sys
import tarfile

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "startup_native"))
spec = importlib.util.spec_from_file_location(
    "startup_native_runner", HERE.parent / "startup_native/run.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
require = native.require
write_json = native.write_json
fingerprint = native.fingerprint
FIELDS = ("heads", "results", "csr_distances", "csr_assignments", "graph_checked",
          "own_ids", "evaluated_h1", "parent_h1", "qualifications", "degree_audits",
          "row_audits", "signature_rejected", "predicate_rejected", "entry_ids",
          "entry_scored", "startup_audits", "discoveries")
NEW_FIELDS = {"supplyConnChecks", "supplyConnLow", "supplyConnBefore", "supplyConnAfter",
              "supplyConnAdded", "supplyOrdinaryVisited", "supplyStartupConnected"}
PHYSICAL = None
HEAD_VIDS = None
GEOMETRY = {}


def physical_row(node):
    if node not in GEOMETRY:
        ids, seen = [], set()
        row = PHYSICAL[node]
        stop, terminator = len(row), 0
        for slot, value in enumerate(row):
            id = int(value)
            if id < 0:
                stop, terminator = slot, id
                break
            if id >= len(PHYSICAL) or id == node or id in seen:
                continue
            ids.append(id); seen.add(id)
        GEOMETRY[node] = (ids, stop, terminator)
    return GEOMETRY[node]


def digest_update(h, item):
    h.update(json.dumps(item, sort_keys=True, separators=(",", ":")).encode() + b"\n")


def query_stream(path):
    if path.exists():
        return path.open("rb")
    return gzip.open(path.with_suffix(path.suffix + ".gz"), "rb")


def audit_queries(path, count, probe, case, truth, mask, legacy=False):
    raw, heads, payload, first8 = [hashlib.sha256() for _ in range(4)]
    hits = underfilled = empty = returned = head_count = 0
    totals = {}
    legacy_payload = hashlib.sha256()
    trigger_degrees, trigger_nodes, startup_degrees = {}, set(), {}
    frame_count = helper_frames = zero_pop_queries = 0
    q = -1
    with query_stream(path) as stream:
        for q, line in enumerate(stream):
            raw.update(line)
            row = json.loads(line)
            require(q < count and row["query"] == q, "Wrong native query order/count")
            valid = [(int(id), float(d)) for id, d in row["results"] if id >= 0]
            require(len(row["results"]) == 10 and len(valid) == len({id for id, _ in valid}),
                    "Invalid or duplicate native final top10")
            require(all(id < len(mask) and mask[id] and math.isfinite(d) and d >= 0
                        for id, d in valid), "Final native predicate/distance violation")
            selected = [[int(id), float(d)] for id, d in row["heads"] if id >= 0]
            require(len(row["heads"]) == probe and len(selected) <= probe and
                    len(selected) == len({id for id, _ in selected}) and
                    all(0 <= id < 160091 and math.isfinite(d) and d >= 0 for id, d in selected),
                    "Native variable-nprobe capacity/identity failure")
            require(all(0 <= id < len(mask) and mask[id] for id in row["own_ids"]),
                    "Invalid native own-point predicate")
            digest_update(heads, selected)
            shared = {k: row[k] for k in FIELDS}
            shared.update({k: v for k, v in row.items() if k.startswith("supply")})
            digest_update(legacy_payload, {k: v for k, v in shared.items() if k not in NEW_FIELDS})
            if not legacy:
                shared["connectivity_audits"] = row["connectivity_audits"]
            digest_update(payload, shared)
            if q < 8:
                digest_update(first8, shared)
            ids = {id for id, _ in valid}
            hits += len(ids & set(truth[q, :10]))
            underfilled += len(valid) < 10
            empty += not valid
            returned += len(valid)
            head_count += len(selected)
            if case == "supplier":
                require(not legacy, "Do not compare corrected supplier with defective predecessor")
                require(row["supplyStarts"] == 1 and
                        row["supplyCalls"] == row["supplyReturns"] and
                        row["supplyH2Rows"] == row["supplyH2Completed"] and
                        row["supplyH3Rows"] == row["supplyH3Completed"],
                        "Supplier search restart or partial posting")
                audits = row["row_audits"]
                require(len({(r[0], r[1]) for r in audits}) == len(audits) and
                        all(r[2] == r[3] for r in audits) and
                        sum(r[2] for r in audits) == row["supplyMembers"] ==
                        row["supplyUpperMembers"] + row["supplyLowerMembers"],
                        "Posting rescan or whole-row accounting failure")
                require(row["supplyNativeMaxCheck"] == 2048 and
                        row["supplyNativeOvershoot"] == max(0, row["graph_checked"] - 2048) and
                        row["supplyStartupChecks"] == 1 and not row["exhausted"],
                        "Changed native boundary/startup or artificial cap")
                require(sum(row[k] for k in ("supplyGraph", "supplyRouting", "supplyParent", "supplyChild")) ==
                        row["supplyCandidates"] + row["supplyRepeats"], "Actual-distance ledger mismatch")
                frames = row["connectivity_audits"]
                require(len(frames) == row["supplyConnChecks"] and
                        sum(a["calls"] for a in frames) == row["supplyCalls"] and
                        sum(a["before"] for a in frames) == row["supplyConnBefore"] and
                        sum(a["after"] for a in frames) == row["supplyConnAfter"] and
                        sum(a["after"] - a["before"] for a in frames) == row["supplyConnAdded"],
                        "Connectivity/frame/call ledger mismatch")
                evaluated = {int(h): flag for (h, _), flag in zip(row["evaluated_h1"], row["qualifications"])}
                for a in frames:
                    node = a["node"]
                    require(0 <= node < len(PHYSICAL), "Invalid degree-audit native node")
                    physical, stop, marker = physical_row(node)
                    require(a["width"] == PHYSICAL.shape[1] and a["stop_slot"] == stop and
                            a["terminator"] == marker and [p[0] for p in a["ordinary"]] == physical,
                            "Degree did not inspect exact native ordinary adjacency prefix")
                    flags = [p[1] for p in a["ordinary"]]
                    require(all(0 <= f <= 3 and bool(f & 2) == bool(mask[HEAD_VIDS[id]])
                                for id, f in a["ordinary"]), "Degree own-eligibility disagrees with exact predicate")
                    require(all(id not in evaluated or evaluated[id] == f for id, f in a["ordinary"]),
                            "Degree qualifier changed native candidate eligibility")
                    require(a["before"] == sum(f != 0 for f in flags) and
                            a["before"] <= len(physical) and a["calls"] in (0, 1) and
                            (not a["calls"] or a["before"] < 16),
                            "Supplier triggered on adequate predicate-valid connectivity")
                    supplied = a["supplied"]
                    require(len(supplied) == len(set(supplied)) == a["after"] - a["before"] and
                            not (set(supplied) & set(physical)) and node not in supplied and
                            all(evaluated.get(id, 0) != 0 for id in supplied),
                            "Supplied connectivity inflated by duplicates, self or ineligible IDs")
                    if a["calls"]:
                        helper_frames += 1; trigger_nodes.add(node)
                        k = str(len(physical))
                        trigger_degrees[k] = trigger_degrees.get(k, 0) + 1
                    if a["phase"] == 1:
                        k = str(a["before"])
                        startup_degrees[k] = startup_degrees.get(k, 0) + 1
                frame_count += len(frames)
                zero_pop_queries += row["supplyPops"] == 0
            for k, v in row.items():
                if k.startswith("supply") and k != "supplyTrace":
                    totals[k] = totals.get(k, 0) + v
    require(q + 1 == count, "Incomplete capture")
    return {
        "queries": count, "recall_at_10": hits / (10 * count),
        "underfilled_queries": underfilled, "empty_queries": empty,
        "mean_returned": returned / count, "selected_h1": head_count / count,
        "filter_violations": 0, "raw_capture_sha256": raw.hexdigest(),
        "heads_sha256": heads.hexdigest(), "payload_sha256": payload.hexdigest(),
        "first8_payload_sha256": first8.hexdigest(),
        "baseline_payload_sha256": legacy_payload.hexdigest(),
        "connectivity_evidence": {"frames": frame_count, "helper_frames": helper_frames,
                                 "trigger_physical_degree_histogram": trigger_degrees,
                                 "trigger_nodes": sorted(trigger_nodes),
                                 "startup_valid_degree_histogram": startup_degrees,
                                 "zero_h1_pop_queries": zero_pop_queries},
        "supplier_work": {k: v / count for k, v in totals.items()},
    }


def compress_capture(path, expected):
    if not path.exists():
        return
    zipped = path.with_suffix(path.suffix + ".gz")
    require(not zipped.exists(), "Cannot overwrite compressed raw capture")
    with path.open("rb") as source, gzip.open(zipped, "wb", compresslevel=1) as target:
        shutil.copyfileobj(source, target, 8 * 1024 * 1024)
    h = hashlib.sha256()
    with gzip.open(zipped, "rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    require(h.hexdigest() == expected, "Compressed capture differs from raw native evidence")
    path.unlink()


def frozen_heads(logs, count, probe):
    observations = []
    for log in logs:
        with log.open() as stream:
            for line in stream:
                if "DUMPHEADS q=" in line:
                    points = [[int(a), float(b)] for a, b in
                              re.findall(r"(\d+):([0-9.eE+-]+)", line.split(" :", 1)[1])]
                    require(len(points) <= probe, "Frozen selected-head capacity failure")
                    observations.append(points)
    require(len(observations) == 2 * count and observations[:count] == observations[count:],
            "Frozen warmup/measurement heads differ")
    h = hashlib.sha256()
    for points in observations[:count]:
        digest_update(h, points)
    return h.hexdigest()


def run(config, resume):
    global PHYSICAL, HEAD_VIDS
    native.reject_environment_overrides()
    plan = native.read_ini(config)["Experiment"]
    root, toolchain = Path(plan["OutputDirectory"]), Path(plan["Toolchain"])
    prereg = json.loads((HERE / "preregistration.json").read_text())
    schedule = json.loads((HERE / "schedule.json").read_text())
    require(prereg["points"] == 198 and len(schedule) == 468, "Changed fixed sweep")
    source_proof = json.loads((toolchain / "predicate_degree_provenance.json").read_text())
    for name, h in source_proof["after"].items():
        require(fingerprint(toolchain / "source" / name)["sha256"] == h, "Native source changed")
    for case in prereg["cases"]:
        unchanged = None
        for probe in prereg["grid"]:
            params = dict(native.read_ini(HERE / f"configs/{case}_{probe}_plain.ini")["SearchSSDIndex"])
            require(int(params.pop("internalresultnum")) == probe, "Relabeled nprobe")
            require(params["maxcheck"] == "2048" and params["hierarchymaxcheck"] == "512" and
                    params["hierarchyinitialproberatio"] == "0.666666" and
                    params["searchpostingpagelimit"] == "15" and params["resultnum"] == "10" and
                    params["numberofthreads"] == "1" and params["logphasetime"] == "false" and
                    params["shortcutprofile"] == "false" and params["dumpheads"] == "0" and
                    params["logpathstats"] == "false", "Changed native search controls")
            if case == "supplier":
                require(params["shortcuteffectivedegree"] == "16" and
                        "shortcutcap" not in params and "shortcutmemberlimit" not in params,
                        "Added supplier cap or changed degree")
            require(unchanged is None or params == unchanged, "More than InternalResultNum swept")
            unchanged = params
    root.mkdir(exist_ok=resume)
    snapshot = root / "snapshot"
    prior_root = Path(plan["PriorResults"])
    if not resume:
        shutil.copytree(HERE, snapshot / "experiment", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(plan["Binary"], snapshot / "spannaclbench")
        for name in ("predicate_degree_provenance.json", "configure.log", "build.log",
                     "parent_capacity_guard_provenance.json", "parent_parent_final_source_manifest.json",
                     "parent_parent_authentication.json", "parent_parent_supplier-tests-pass.log",
                     "supplier-tests.log"):
            shutil.copy2(toolchain / name, snapshot / name)
        with tarfile.open(snapshot / "source.tar.gz", "w:gz") as archive:
            archive.add(toolchain / "source", arcname="source",
                        filter=lambda p: None if "/Release/" in p.name else p)
        prior = json.loads((prior_root / "provenance.json").read_text())["protected"]
        require(all(fingerprint(p["path"]) == p for p in prior), "Previously protected inputs changed")
        extra = [fingerprint(p) for p in sorted(prior_root.rglob("*")) if p.is_file()]
        extra += [fingerprint(p) for p in sorted(Path(plan["PreviousSweep"]).rglob("*")) if p.is_file()]
        extra += [fingerprint(p) for p in sorted((HERE.parent / "startup_native").rglob("*"))
                  if p.is_file() and "__pycache__" not in p.parts]
        extra += [fingerprint(p) for p in sorted((HERE.parent / "startup_nprobe").rglob("*"))
                  if p.is_file() and "__pycache__" not in p.parts]
        write_json(root / "provenance.json", {
            "protected": prior + extra, "native_binary": fingerprint(snapshot / "spannaclbench"),
            "source_archive": fingerprint(snapshot / "source.tar.gz"),
            "source_change": source_proof["change"], "parent_toolchain": source_proof["parent_toolchain"],
            "only_changed_native_files": source_proof["changed_files"],
            "preregistration": fingerprint(snapshot / "experiment/preregistration.json"),
            "schedule": fingerprint(snapshot / "experiment/schedule.json"),
            "physical_graph": fingerprint(Path(plan["PhysicalGraph"])),
            "supplier_degree_semantics": "predicate_valid_neighbors",
        })
    else:
        for f in ("experiment.ini", "preregistration.json", "schedule.json"):
            require((HERE / f).read_bytes() == (snapshot / "experiment" / f).read_bytes(),
                    "Resume changed preregistration")
        require(fingerprint(Path(plan["Binary"]))["sha256"] ==
                fingerprint(snapshot / "spannaclbench")["sha256"], "Resume changed binary")
        shutil.copy2(Path(__file__), root / "resume_runner.py")
    workloads = json.loads(Path(plan["Workloads"]).read_text())
    attrs = np.load(workloads["attributes"], mmap_mode="r")
    require("PASS:" in (snapshot / "supplier-tests.log").read_text(), "Native connectivity fixtures incomplete")
    graph_path = Path(plan["PhysicalGraph"])
    with graph_path.open("rb") as stream:
        n, width = struct.unpack("<ii", stream.read(8))
    require(n == 160091 and width == 32 and graph_path.stat().st_size == 8 + 4 * n * width,
            "Unexpected native graph layout")
    PHYSICAL = np.memmap(graph_path, dtype="<i4", offset=8, mode="r", shape=(n, width))
    old_io = json.loads((prior_root / "unfilter_h1_plain_r1/native.io.json").read_text())
    HEAD_VIDS = np.fromfile(Path(old_io["target"]).parent / "SPTAGHeadVectorIDs.bin", dtype="<u8", offset=8)
    require(len(HEAD_VIDS) == n and len(set(HEAD_VIDS)) == n, "Invalid canonical H1 VID map")
    histogram = {}
    for id in range(n):
        degree = str(len(physical_row(id)[0]))
        histogram[degree] = histogram.get(degree, 0) + 1
    write_json(root / "physical_degree.json", {"nodes": n, "width": width, "histogram": histogram,
                "rule": "distinct in-range nonself IDs before first negative entry; collapsed marker is metadata"})
    masks, truth, args = {}, {}, {}
    for scenario in prereg["scenarios"]:
        masks[scenario] = (np.ones(len(attrs), dtype=bool) if scenario == "unfilter" else
                           native.predicate_mask(workloads["predicates"][scenario], attrs))
        require(int(masks[scenario].sum()) == workloads["truth"][scenario]["candidate_count"],
                "Changed original workload")
        truth[scenario] = np.load(workloads["truth"][scenario]["ids"], mmap_mode="r")
        args[scenario] = []
        if scenario in workloads["flat_query_tags"]:
            args[scenario] = ["--query-tags", workloads["flat_query_tags"][scenario], "--tag-column", "0"]
        elif scenario in ("numeric", "mixed_dnf"):
            args[scenario] = ["--query-dnf", workloads["query_dnf"]["numeric" if scenario == "numeric" else "mixed"]]
    old_runs = {(r["scenario"], r["case"]): r for r in
                json.loads((prior_root / "runs.json").read_text())
                if r["mode"] == "plain" and r["repeat"] == 1}
    records, frozen, fixtures, first = [], {}, {}, {}
    write_json(root / "status.json", {"state": "running", "required_points": 198})
    try:
        for position, job in enumerate(schedule):
            kind, scenario, case, probe, repeat = [job[k] for k in ("kind", "scenario", "case", "nprobe", "repeat")]
            key = scenario, case, probe
            directory = root / f"{kind}_r{repeat}_{scenario}_{case}_p{probe}"
            record_path = directory / "record.json"
            if record_path.exists():
                require(resume, "Unexpected existing run record")
                record = json.loads(record_path.read_text())
                require(record["job"] == job, "Resume run identity changed")
            else:
                directory.mkdir(exist_ok=resume)
                count, warmup = (8, 0) if kind == "fixture" else (1000, 1000)
                search = snapshot / "experiment/configs" / f"{case}_{probe}_{kind}.ini"
                require(search.read_bytes() == (HERE / "configs" / search.name).read_bytes(),
                        "Materialized native INI changed")
                command = ["numactl", "--cpunodebind=2", "--membind=2",
                           plan["FrozenBinary"] if kind == "frozen" else str(snapshot / "spannaclbench"),
                           "--index", plan["Index"], "--queries", plan["Queries"],
                           "--truth", workloads["truth"][scenario]["ids"],
                           "--search-sweep-ini", str(search), "--value-type", "Float", "--topk", "10",
                           "--warmup", str(warmup), "--measure-offset", "0", "--max-queries", str(count),
                           *args[scenario]]
                if (directory / "native.exit.json").exists():
                    require(resume and json.loads((directory / "native.exit.json").read_text())["returncode"] == 0,
                            "Incomplete/failed native run cannot be declared complete")
                    require(json.loads((directory / "native.command.json").read_text())["command"] == command,
                            "Resume changed native command")
                    logs = [directory / f"native.{part}.log" for part in ("stdout", "stderr")]
                else:
                    require(not list(directory.iterdir()), "Preserve incomplete process artifacts")
                    logs = native.run_native(command, directory, "native", 0.2, True)
                result, phases = native.h3_records(logs, count, warmup + (0 if kind == "frozen" else count),
                                                   probe, kind != "plain")
                require(json.loads((directory / "native.io.json").read_text())["direct_io"],
                        "Not authentic native O_DIRECT SSD access")
                if phases:
                    native.validate_phase_balance(phases)
                if kind == "frozen":
                    validation = {"heads_sha256": frozen_heads(logs, count, probe)}
                else:
                    validation = audit_queries(directory / "queries.jsonl", count, probe, case,
                                               truth[scenario], masks[scenario])
                    require(abs(validation["recall_at_10"] - result["recall"]) < 1e-12,
                            "Capture/ordinary native recall mismatch")
                    compress_capture(directory / "queries.jsonl", validation["raw_capture_sha256"])
                record = {"job": job, "directory": directory.name, "native": result,
                          "validation": validation, "phases": phases,
                          "native_stdout": fingerprint(logs[0]), "native_stderr": fingerprint(logs[1])}
                write_json(record_path, record)
            if kind == "frozen":
                frozen[key] = record
            elif kind == "fixture":
                fixtures[key] = record
            else:
                if key in frozen:
                    require(record["validation"]["heads_sha256"] == frozen[key]["validation"]["heads_sha256"]
                            and native.h3_core_work(record["native"]) == native.h3_core_work(frozen[key]["native"]),
                            f"Frozen baseline parity failed: {key}")
                if key in fixtures:
                    require(record["validation"]["first8_payload_sha256"] ==
                            fixtures[key]["validation"]["payload_sha256"],
                            f"Counted/uncounted boundary fixture parity failed: {key}")
                if probe == 24 and case in ("h1", "h3"):
                    old = old_runs[scenario, case]
                    require(native.h3_core_work(record["native"]) == native.h3_core_work(old["native"]),
                            f"Guard-only rebuild changed native nprobe24 work: {key}")
                    if repeat == 1:
                        previous = audit_queries(prior_root / old["directory"] / "queries.jsonl", 1000, 24,
                                                 case, truth[scenario], masks[scenario], legacy=True)
                        require(record["validation"]["baseline_payload_sha256"] == previous["baseline_payload_sha256"],
                                f"Original baseline changed nprobe24 ordered results/work: {key}")
                if key in first:
                    require(record["validation"] == first[key]["validation"] and
                            native.h3_core_work(record["native"]) == native.h3_core_work(first[key]["native"]),
                            f"Ordinary repetition result/work mismatch: {key}")
                else:
                    first[key] = record
            records.append(record)
            write_json(root / "runs.json", records)
            write_json(root / "progress.json", {"completed_native_processes": len(records),
                                              "required_native_processes": len(schedule),
                                              "last_run": directory.name})
            print(f"{position+1}/468 {directory.name}: recall={record['native']['recall']:.4f} "
                  f"ms={record['native']['mean_latency_ms']:.6f}", flush=True)
        require(len(first) == 198 and len(records) == 468, "Missing measured sweep point")
        protected = json.loads((root / "provenance.json").read_text())["protected"]
        require(all(fingerprint(p["path"]) == p for p in protected), "Protected previous artifacts changed")
        summary = []
        for scenario in prereg["scenarios"]:
            for case in prereg["cases"]:
                for probe in prereg["grid"]:
                    pair = [r for r in records if r["job"]["kind"] == "plain" and
                            (r["job"]["scenario"], r["job"]["case"], r["job"]["nprobe"]) == (scenario, case, probe)]
                    require(len(pair) == 2, "Missing ordinary repetition")
                    v, n = pair[0]["validation"], pair[0]["native"]
                    ms = [r["native"]["mean_latency_ms"] for r in pair]
                    head_actual = (sum(v["supplier_work"][k] for k in
                                       ("supplyGraph", "supplyRouting", "supplyParent", "supplyChild"))
                                   if case == "supplier" else None)
                    summary.append({
                        "scenario": scenario, "case": case, "nprobe": probe,
                        **{k: v[k] for k in ("recall_at_10", "underfilled_queries", "empty_queries",
                                            "mean_returned", "filter_violations", "selected_h1")},
                        "ordinary_ms": statistics.mean(ms), "ordinary_ms_runs": ms,
                        "qps": 1000 / statistics.mean(ms), "qps_runs": [1000 / t for t in ms],
                        "qps_min": 1000 / max(ms), "qps_max": 1000 / min(ms),
                        "postings": n["postings_per_query"], "pages": n["posting_page_reads_per_query"],
                        "disk_distances": n["distance_computations_per_query"],
                        "head_actual_distances": head_actual, "supplier_work": v["supplier_work"],
                        "directories": [r["directory"] for r in pair],
                    })
                    if case == "supplier":
                        summary[-1]["supplier_degree_semantics"] = "predicate_valid_neighbors"
                        summary[-1]["connectivity_evidence"] = v["connectivity_evidence"]
        write_json(root / "summary.json", summary)
        scalar = list(dict.fromkeys(k for row in summary for k, v in row.items()
                                   if not isinstance(v, (list, dict))))
        with (root / "summary.csv").open("w") as stream:
            writer = csv.DictWriter(stream, fieldnames=scalar)
            writer.writeheader()
            writer.writerows({k: r.get(k) for k in scalar} for r in summary)
        thresholds = []
        for scenario in prereg["scenarios"]:
            for case in prereg["cases"]:
                points = [r for r in summary if r["scenario"] == scenario and r["case"] == case]
                for target in (0.90, 0.95):
                    candidates = [r for r in points if r["recall_at_10"] >= target]
                    best = max(candidates, key=lambda r: r["qps"]) if candidates else None
                    thresholds.append({"scenario": scenario, "case": case, "target_recall": target,
                                       "reached": best is not None, "observed_best": best,
                                       "maximum_observed_recall": max(r["recall_at_10"] for r in points)})
        write_json(root / "thresholds.json", thresholds)
        table = ["# Observed nprobe recall/QPS sweep", "",
                 "198 measured points, two ordinary repetitions each; no interpolation or policy tuning.",
                 "QPS = 1000 / mean ordinary latency in ms. Fastest observed point meeting each recall threshold.",
                 "A missing threshold is explicitly unreached over the full common eleven-point grid.", "",
                 "| Scenario | Mode | Target | nprobe | Recall (%) | QPS | Underfilled /1000 |",
                 "|---|---|---:|---:|---:|---:|---:|"]
        for t in thresholds:
            b = t["observed_best"]
            cells = (f'{b["nprobe"]} | {100*b["recall_at_10"]:.2f} | {b["qps"]:.3f} | {b["underfilled_queries"]}'
                     if b else f'- | max {100*t["maximum_observed_recall"]:.2f} | unreached | -')
            table.append(f'| {t["scenario"]} | {t["case"]} | {100*t["target_recall"]:.0f}% | {cells} |')
        (root / "report.md").write_text("\n".join(table) + "\n")
        write_json(root / "validation.json", {
            "measured_points": 198, "ordinary_processes": 396, "frozen_parity_processes": 36,
            "profile_fixture_processes": 36, "native_processes": 468,
            "all_points_have_two_successful_ordinary_runs": True,
            "native_variable_head_capacity_validated": True,
            "all_result_predicate_underfill_checks_passed": True,
            "capture_measurement_exact_ids_and_work_checked_by_native_binary": True,
            "ordinary_repetition_ordered_payload_and_ssd_work_equal": True,
            "counted_uncounted_boundary_fixtures_equal": True,
            "frozen_boundary_and_24_baseline_heads_recall_work_equal": True,
            "original_baselines_24_ordered_payload_and_work_equal": True,
            "supplier_degree_semantics": "predicate_valid_neighbors",
            "every_native_degree_frame_reconciled_to_physical_graph": True,
            "no_adequate_connectivity_fallback": True,
            "baseline_head_distance_calls_not_measured_in_ordinary_runs": True,
            "compressed_raw_captures_sha_verified_before_removing_uncompressed_copies": True,
            "protected_files_unchanged": len(protected),
            "summary_sha256": fingerprint(root / "summary.json")["sha256"],
            "summary_csv_sha256": fingerprint(root / "summary.csv")["sha256"],
            "thresholds_sha256": fingerprint(root / "thresholds.json")["sha256"],
        })
        write_json(root / "status.json", {"state": "completed", "measured_points": 198,
                                         "native_processes": 468, "ordinary_repetitions": 2})
    except BaseException as error:
        write_json(root / "status.json", {"state": "failed", "completed_processes": len(records),
                                         "error": repr(error)})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "experiment.ini")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    run(args.config.resolve(), args.resume)
