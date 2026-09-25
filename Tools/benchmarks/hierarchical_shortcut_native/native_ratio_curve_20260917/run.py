"""Explicit-stage native ratio curves. No command automatically runs the full matrix."""
import argparse
import functools
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import statistics
import struct
import time

import numpy as np

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ratio_curve_accepted", HERE.parent / "native_default_admission_20260917/run.py")
accepted = importlib.util.module_from_spec(spec)
spec.loader.exec_module(accepted)
native, require, fp = accepted.native, accepted.require, accepted.fp
old = accepted.parent.old


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def stream(path):
    return path.open("rb") if path.exists() else gzip.open(str(path) + ".gz", "rb")


def digest(path):
    h = hashlib.sha256()
    with stream(path) as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rows(path):
    with stream(path) as source:
        return [json.loads(line) for line in source]


def core(record):
    return accepted.parent.core(record)


class Experiment:
    def __init__(self):
        native.reject_environment_overrides()
        self.plan = native.read_ini(HERE / "experiment.ini")["Experiment"]
        self.root = Path(self.plan["OutputDirectory"])
        self.snapshot = self.root / "snapshot"
        self.prereg = json.loads((HERE / "preregistration.json").read_text())
        self.schedule = json.loads((HERE / "schedule.json").read_text())
        require(len(self.schedule) == 36 and self.prereg["unique_points"] == 198, "Wrong matrix")
        require(fp(HERE / "schedule.json")["sha256"] == self.prereg["schedule_sha256"], "Schedule changed")
        for name, expected in self.prereg["configs"].items():
            require(fp(HERE / "configs" / name)["sha256"] == expected, "Native INI changed")
        if not self.root.exists():
            self.initialize()
        proof = json.loads((self.root / "provenance.json").read_text())
        for item in proof["experiment_files"]:
            require(fp(HERE / item["relative"])["sha256"] == item["sha256"], "Frozen runner/source changed")
        require(fp(self.snapshot / "spannaclbench")["sha256"] == proof["binary"]["sha256"], "Binary changed")
        self.workloads = json.loads(Path(self.plan["Workloads"]).read_text())
        attrs = np.load(self.workloads["attributes"], mmap_mode="r")
        self.masks, self.truth, self.args = {}, {}, {}
        for scenario in self.prereg["scenarios"]:
            self.masks[scenario] = np.ones(len(attrs), dtype=bool) if scenario == "unfilter" else native.predicate_mask(
                self.workloads["predicates"][scenario], attrs)
            require(int(self.masks[scenario].sum()) == self.workloads["truth"][scenario]["candidate_count"],
                    "Wrong predicate population")
            self.truth[scenario] = np.load(self.workloads["truth"][scenario]["ids"], mmap_mode="r")
            self.args[scenario] = []
            if scenario in self.workloads["flat_query_tags"]:
                self.args[scenario] = ["--query-tags", self.workloads["flat_query_tags"][scenario], "--tag-column", "0"]
            elif scenario in ("numeric", "mixed_dnf"):
                self.args[scenario] = ["--query-dnf", self.workloads["query_dnf"][
                    "numeric" if scenario == "numeric" else "mixed"]]
        graph = Path(self.plan["PhysicalGraph"])
        with graph.open("rb") as source:
            require(struct.unpack("<ii", source.read(8)) == (160091, 32), "Wrong native H1 graph")
        old.PHYSICAL = np.memmap(graph, dtype="<i4", offset=8, shape=(160091, 32), mode="r")
        self.physical = functools.lru_cache(maxsize=160091)(old.physical_row)

    def initialize(self):
        start = time.monotonic()
        self.snapshot.mkdir(parents=True)
        shutil.copytree(HERE, self.snapshot / "experiment", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(self.plan["Binary"], self.snapshot / "spannaclbench")
        runtime = Path(self.plan["Binary"]).parent
        for name in ("accepted_provenance.json", "build.log", "configure.log"):
            shutil.copy2(runtime / name, self.snapshot / name)
        accepted_root = Path(self.plan["AcceptedResults"])
        inputs = json.loads((accepted_root / "input_hashes.json").read_text())
        paths = {item["path"] for item in inputs}
        workloads = json.loads(Path(self.plan["Workloads"]).read_text())
        paths.update([self.plan["Workloads"], workloads["attributes"], self.plan["Queries"]])
        paths.update(workloads["flat_query_tags"].values())
        paths.update(workloads["query_dnf"].values())
        paths.update(value["ids"] for value in workloads["truth"].values())
        expected = {item["path"]: item["sha256"] for item in inputs}
        protected = []
        for path in sorted(paths):
            item = fp(Path(path))
            if path in expected:
                require(item["sha256"] == expected[path], "Accepted input changed")
            protected.append(item)
        write(self.root / "input_hashes.json", protected)
        source = json.loads((runtime / "accepted_provenance.json").read_text())
        for item in source["linked_files"] + [source["accepted_binary"]]:
            require(fp(Path(item["path"]))["sha256"] == item["sha256"], "Accepted library/object changed")
        write(self.root / "provenance.json", {
            "binary": fp(self.snapshot / "spannaclbench"), "accepted_native": source,
            "experiment_files": [{"relative": str(p.relative_to(HERE)), "sha256": fp(p)["sha256"]}
                for p in sorted(HERE.rglob("*")) if p.is_file() and "__pycache__" not in p.parts],
            "accepted_summary": fp(accepted_root / "summary.json"),
            "initialization_and_input_hash_seconds": time.monotonic() - start,
            "native_search_library_changed": False, "benchmark_compatibility_only": True})
        write(self.root / "status.json", {"state": "matrix_in_progress", "completed_stages": [],
            "ordinary_index_loads_completed": 0, "ordinary_batches_completed": 0,
            "canonical_summary_published": False, "automatic_next_stage": False})

    def audit(self, path, count, probe, case, scenario):
        hashes = {name: hashlib.sha256() for name in ("raw", "payload", "heads", "results")}
        hits = returned = underfilled = empty = selected = frames = 0
        totals, histogram = {}, {}
        with path.open("rb") as source:
            for q, line in enumerate(source):
                hashes["raw"].update(line)
                row = json.loads(line)
                require(row["query"] == q and q < count, "Bad query sequence")
                require(bool(row["native_h1_hooks_invoked"]) == (case != "h3"), "Wrong native route")
                require(case == "h3" or row["native_raw_distance_function"] == 1, "Non-native H1 primitive")
                result = [(i, d) for i, d in row["results"] if i >= 0]
                require(len(row["results"]) == 10 and len({i for i, _ in result}) == len(result),
                        "Invalid native result count/dedup")
                require(all(i < len(self.masks[scenario]) and self.masks[scenario][i] and
                            math.isfinite(d) and d >= 0 for i, d in result), "Invalid native predicate result")
                require(len(row["heads"]) == probe, "Wrong native nprobe capacity")
                require(all(0 <= i < 160091 and math.isfinite(d) and d >= 0
                            for i, d in row["heads"] if i >= 0), "Invalid native H1 selection")
                require(len(set(row["own_ids"])) == len(row["own_ids"]) and
                        all(self.masks[scenario][i] for i in row["own_ids"]), "Invalid own-point admission")
                hits += len({i for i, _ in result}.intersection(self.truth[scenario][q, :10]))
                returned += len(result)
                underfilled += len(result) < 10
                empty += not result
                selected += sum(i >= 0 for i, _ in row["heads"])
                require(row["supplier_calls"] == row["supplier_returns"] and
                        row["queue_offers"] == row["queue_accepted"] + row["queue_rejected"],
                        "Broken native continuation/queue ledger")
                scanned = row["rows"]
                require(len({(r[0], r[1]) for r in scanned}) == len(scanned) and
                        all(r[2] == r[3] for r in scanned) and
                        sum(r[3] for r in scanned) == row["supplier_members"], "Partial/repeated selected row")
                require(not {(r[0], r[1]) for r in scanned}.intersection(map(tuple, row["rejected_rows"])),
                        "Signature-rejected CSR row scanned")
                require(sum(f["calls"] for f in row["degree_frames"]) == row["supplier_calls"], "Call/frame mismatch")
                for f in row["degree_frames"]:
                    physical, _, _ = self.physical(f["head"])
                    require([i for i, _ in f["ordinary"]] == physical and f["d"] == len(physical) and
                            f["e"] == sum(bool(v) for _, v in f["ordinary"]), "Invalid visited/degree accounting")
                    require(f["required"] == (0 if f["d"] < 16 else (f["d"] + 1) // 2) and
                            (not f["calls"] or (f["d"] >= 16 and 2 * f["e"] < f["d"] and
                                               f["checked_before"] < 2048)), "Invalid ratio/native boundary")
                    require(len(set(f["supplied"])) == len(f["supplied"]) and
                            not set(f["supplied"]).intersection(physical) and f["head"] not in f["supplied"],
                            "Duplicate supplied eligibility")
                    key = f'{f["d"]}/{f["e"]}'
                    histogram[key] = histogram.get(key, 0) + 1
                    frames += 1
                if case == "supplier":
                    if scenario == "unfilter":
                        require(row["native_default_admission"] == 1 and
                                all(row[k] == 0 for k in ("native_filtered_admission", "row_eligibility_evaluations",
                                    "supplier_calls", "supplier_parent_distances", "native_child_distances",
                                    "supplier_members", "signature_checks")), "Unfilter default certificate not applied")
                        require(not row["own_ids"] and all(f["e"] == f["d"] for f in row["degree_frames"]),
                                "Unfilter used custom own heap/nonconstant eligibility")
                    else:
                        require(row["native_default_admission"] == 0 and row["native_filtered_admission"] == 1,
                                "Filtered supplier lost native admission")
                for name in ("payload", "heads", "results"):
                    old.digest_update(hashes[name], row if name == "payload" else row[name])
                if case != "h3":
                    for key, value in row.items():
                        if isinstance(value, (int, float)) and key != "query":
                            totals[key] = totals.get(key, 0) + value
        require(q + 1 == count, "Truncated capture")
        return {"queries": count, "recall_at_10": hits / (10 * count), "mean_returned": returned / count,
            "underfilled_queries": underfilled, "underfill_rate": underfilled / count, "empty_queries": empty,
            "filter_violations": 0, "selected_h1": selected / count, "degree_frames": frames,
            "degree_histogram": histogram, "work": {k: v / count for k, v in totals.items()},
            "navigation_work_available": case != "h3",
            **{name + "_sha256": value.hexdigest() for name, value in hashes.items()}}

    def invoke(self, name, scenario, case, probes, config, count, repeat=0, retry=False):
        job = self.root / name
        if (job / "record.json").exists():
            return json.loads((job / "record.json").read_text())
        job.mkdir(exist_ok=True)
        attempts = sorted(job.glob("attempt_*"))
        directory = attempts[-1] if attempts else job / "attempt_001"
        completed_process = (directory / "native.exit.json").exists() and json.loads(
            (directory / "native.exit.json").read_text())["returncode"] == 0
        if directory.exists() and not completed_process:
            require(retry, "Incomplete native attempt preserved; explicit --retry-incomplete required")
            directory = job / f"attempt_{len(attempts) + 1:03d}"
        command = ["numactl", "--cpunodebind=2", "--membind=2", str(self.snapshot / "spannaclbench"),
                   "--index", self.plan["Index"], "--queries", self.plan["Queries"],
                   "--truth", self.workloads["truth"][scenario]["ids"], "--search-sweep-ini",
                   str(self.snapshot / "experiment/configs" / config), "--value-type", "Float", "--topk", "10",
                   "--warmup", str(count), "--measure-offset", "0", "--max-queries", str(count), *self.args[scenario]]
        if not directory.exists():
            directory.mkdir()
            start = time.monotonic()
            native.run_native(command, directory, "native", .2, True)
            write(directory / "process_wall.json", {"seconds": time.monotonic() - start})
        else:
            require(json.loads((directory / "native.command.json").read_text())["command"] == command,
                    "Recovery invocation changed")
        require(json.loads((directory / "native.exit.json").read_text())["returncode"] == 0, "Native failure")
        require(json.loads((directory / "native.io.json").read_text())["direct_io"], "Missing actual O_DIRECT")
        text = "\n".join((directory / f"native.{k}.log").read_text() for k in ("stdout", "stderr"))
        require(text.splitlines().count("NATIVE_INDEX_LOAD calls=1") == 1 and
                text.count("NATIVE_SUPPLIER_OWNER_LOAD count=1") == 1, "Multiple native index/owner loads")
        certificate = "NATIVE_DEFAULT_CERT heads=160091 own_all_live=1 immutable=1 vectors=1000000"
        require(text.count(certificate) == 1, "Missing actual load-time eligibility certificate")
        components = {label: text.count("Load " + label + " Finish!") for label in
            ("Vector (160091,128)", "BKT (1,160093)", "RNG (160091,32)",
             "Vector (4098,128)", "BKT (1,4100)", "RNG (4098,32)")}
        require(set(components.values()) == {1}, "Physical component load mismatch")
        require("PhaseTime:" not in text, "Ordinary run enabled fine profiling")
        results = [json.loads(s) for s in text.splitlines() if s.startswith('{"engine":')]
        operations = [json.loads(s[len("NATIVE_OPERATION "):]) for s in text.splitlines()
                      if s.startswith("NATIVE_OPERATION ")]
        require([r["nprobe"] for r in results] == probes and len(operations) == len(probes) + 1,
                "Incomplete native batches/operation timings")
        require(operations[0]["operation"] == "load" and
                [o["nprobe"] for o in operations[1:]] == probes, "Wrong operation sequence")
        points = []
        for position, result in enumerate(results):
            probe = result["nprobe"]
            require(result["queries"] == count and not result["failed_queries"] and
                    result["index_load_count"] == result["native_index_load_calls"] == 1 and
                    result["native_workspace_resets"] == position + 1 and result["probe_position"] == position and
                    result["probe_count"] == len(probes) and result["sweep_execution"] == "single_load_nprobe_array",
                    "Native query/array lifecycle failure")
            path = directory / result["capture_file"]
            require(path.parent == directory, "Unexpected capture path")
            checkpoint = directory / f"audit_{probe}.json"
            if checkpoint.exists():
                validation = json.loads(checkpoint.read_text())
                require(digest(path) == validation["raw_sha256"], "Completed audit capture changed")
                method, seconds = "recovered_hash_verified_audit", 0
            else:
                start = time.monotonic()
                if repeat == 2:
                    prior = json.loads((self.root / f"batch_r1_{scenario}_{case}/record.json").read_text())
                    point = next(p for p in prior["points"] if p["native"]["nprobe"] == probe)
                    require(digest(path) == point["validation"]["raw_sha256"] and core(result) == core(point["native"]),
                            "Reverse repetition changed ordered native results/work")
                    validation = point["validation"]
                    method = "byte_exact_to_fully_audited_first_repetition"
                else:
                    validation = self.audit(path, count, probe, case, scenario)
                    method = "full_native_validity_ratio_signature_row_audit"
                seconds = time.monotonic() - start
                write(checkpoint, validation)
            require(abs(validation["recall_at_10"] - result["recall"]) < 1e-12, "Measured/captured recall mismatch")
            start = time.monotonic()
            if path.exists():
                old.compress_capture(path, validation["raw_sha256"])
            points.append({"native": result, "validation": validation, "audit_method": method,
                           "audit_seconds": seconds, "compression_seconds": time.monotonic() - start,
                           "operations": operations[position + 1]})
        record = {"directory": str(directory.relative_to(self.root)), "scenario": scenario, "case": case,
                  "repeat": repeat, "probes": probes, "command": command, "points": points,
                  "native_loadall_entries": 1, "owner_loads": 1, "physical_component_loads": components,
                  "certificate": certificate, "load_seconds": operations[0]["seconds"],
                  "process_wall": json.loads((directory / "process_wall.json").read_text()),
                  "process_usage": json.loads((directory / "native.usage.json").read_text())}
        write(job / "record.json", record)
        print(name, "one native load;", [(p["native"]["nprobe"], p["native"]["recall"],
                                         p["native"]["mean_latency_ms"]) for p in points], flush=True)
        return record

    def fixtures(self, stage, retry):
        marker = self.root / f"fixtures.{stage}.json"
        if marker.exists():
            return
        results = []
        for scenario in self.prereg["stages"][stage]:
            prior = {}
            for order, probes in (("asc", [16, 24, 384]), ("desc", [384, 24, 16])):
                r = self.invoke(f"fixture_{scenario}_h3_{order}", scenario, "h3", probes,
                                f"h3_fixture_{order}.ini", 8, retry=retry)
                for point in r["points"]:
                    probe = point["native"]["nprobe"]
                    capture = self.root / r["directory"] / point["native"]["capture_file"]
                    reference = Path(self.plan["AuthenticArrayResults"]) / f"fixture_{scenario}_h3_{order}"
                    authentic = reference / point["native"]["capture_file"]
                    a, b = rows(capture), rows(authentic)
                    require(len(a) == len(b) == 8 and all(
                        x["heads"] == y["heads"] and x["results"] == y["results"] and x["own_ids"] == y["own_ids"]
                        for x, y in zip(a, b)), "H3 differs from authentic hierarchy boundary fixture")
                    old_record = json.loads((reference / "record.json").read_text())
                    old_point = next(p for p in old_record["points"] if p["native"]["nprobe"] == probe)
                    require(core(point["native"]) == core(old_point["native"]), "H3 changed authentic native SSD work")
                    if probe in prior:
                        require(point["validation"] == prior[probe]["validation"] and
                                core(point["native"]) == core(prior[probe]["native"]), "H3 reverse array mismatch")
                    prior[probe] = point
                results.append(r)
        if stage == "unfilter_broad":
            for case in ("h1", "supplier"):
                r = self.invoke(f"fixture_unfilter_{case}_asc", "unfilter", case, [16, 24, 384],
                                f"{case}_fixture_asc.ini", 8, retry=retry)
                for point in r["points"]:
                    reference = Path(self.plan["AcceptedResults"]) / f"fixture_{case}_asc"
                    a = rows(self.root / r["directory"] / point["native"]["capture_file"])
                    b = rows(reference / point["native"]["capture_file"])
                    require(all({k: v for k, v in x.items() if k != "native_h1_hooks_invoked"} == y
                                for x, y in zip(a, b)), "Benchmark compatibility changed accepted H1/supplier")
                results.append(r)
        write(marker, {"stage": stage, "records": results, "authentic_h3_heads_results_own_ssd_work_parity": True})

    def stage(self, stage, retry):
        require((self.root / f"fixtures.{stage}.json").exists(), "Run explicit stage fixtures first")
        for job in (j for j in self.schedule if j["stage"] == stage):
            self.invoke(f'batch_r{job["repeat"]}_{job["scenario"]}_{job["case"]}', job["scenario"], job["case"],
                        job["probes"], f'{job["case"]}_r{job["repeat"]}.ini', 1000, job["repeat"], retry)
            self.progress()
        self.summarize(stage)

    def records(self):
        return [json.loads(p.read_text()) for p in sorted(self.root.glob("batch_*/record.json"))]

    def progress(self):
        records = self.records()
        done = [name for name, scenarios in self.prereg["stages"].items()
                if sum(r["scenario"] in scenarios for r in records) == 12]
        write(self.root / "status.json", {"state": "matrix_in_progress", "completed_stages": done,
            "ordinary_index_loads_completed": len(records),
            "ordinary_batches_completed": sum(len(r["points"]) for r in records),
            "canonical_summary_published": False, "automatic_next_stage": False})

    def summarize(self, stage):
        scenarios = self.prereg["stages"][stage]
        records = [r for r in self.records() if r["scenario"] in scenarios]
        require(len(records) == 12, "Stage missing native process")
        summary = []
        for scenario in scenarios:
            for case in self.prereg["cases"]:
                for probe in self.prereg["grid"]:
                    pair = sorted([(r["repeat"], p) for r in records if r["scenario"] == scenario and r["case"] == case
                                   for p in r["points"] if p["native"]["nprobe"] == probe])
                    require([r for r, _ in pair] == [1, 2], "Missing ordinary repetition")
                    a, b = pair[0][1], pair[1][1]
                    require(a["validation"] == b["validation"] and core(a["native"]) == core(b["native"]),
                            "Pair native result/work mismatch")
                    v, n = a["validation"], a["native"]
                    times = [p["native"]["mean_latency_ms"] for _, p in pair]
                    row = {"scenario": scenario, "case": case, "nprobe": probe,
                        "recall_at_10": v["recall_at_10"], "ordinary_ms": statistics.mean(times),
                        "ordinary_ms_runs": times, "qps": 1000 / statistics.mean(times),
                        "qps_min": 1000 / max(times), "qps_max": 1000 / min(times),
                        "qps_runs": [1000 / t for t in times],
                        **{k: v[k] for k in ("underfilled_queries", "underfill_rate", "empty_queries",
                                            "mean_returned", "filter_violations", "selected_h1")},
                        "work": v["work"], "work_source": "untimed_native_capture",
                        "navigation_work_available": v["navigation_work_available"],
                        "native_ssd_work": core(n), "postings": n["postings_per_query"],
                        "contributing_postings_per_query_runs":
                            [p["native"]["contributing_postings_per_query"] for _, p in pair],
                        "pages": n["posting_page_reads_per_query"], "disk_distances": n["distance_computations_per_query"],
                        "sweep_execution": "single_load_nprobe_array", "nprobe_ini_api": "SearchSweep.NProbe",
                        "heads_sha256": v["heads_sha256"], "results_sha256": v["results_sha256"],
                        "stage": stage, "matrix_complete": False}
                    if case == "supplier":
                        row.update(supplier_degree_semantics="retained_eligible_ratio", retained_ratio=.5,
                                   minimum_physical_degree=16)
                    summary.append(row)
        if "unfilter" in scenarios:
            for probe in self.prereg["grid"]:
                a, b = [next(r for r in summary if r["scenario"] == "unfilter" and r["case"] == case
                             and r["nprobe"] == probe) for case in ("h1", "supplier")]
                require(a["heads_sha256"] == b["heads_sha256"] and a["results_sha256"] == b["results_sha256"] and
                        a["native_ssd_work"] == b["native_ssd_work"] and a["work"] == b["work"],
                        "Unfilter supplier no longer matches H1")
        require(len(summary) == 66, "Incomplete stage points")
        write(self.root / f"summary.stage_{stage}.json", summary)
        thresholds = {}
        for scenario in scenarios:
            thresholds[scenario] = {}
            for case in self.prereg["cases"]:
                points = [r for r in summary if r["case"] == case and r["scenario"] == scenario]
                thresholds[scenario][case] = {str(threshold): [
                    {"nprobe": r["nprobe"], "recall": r["recall_at_10"], "qps": r["qps"]}
                    for r in points if r["recall_at_10"] >= threshold] for threshold in (.90, .95)}
        proof = json.loads((self.root / "provenance.json").read_text())
        for item in proof["accepted_native"]["linked_files"] + [proof["accepted_native"]["accepted_binary"]]:
            require(fp(Path(item["path"]))["sha256"] == item["sha256"], "Accepted native evidence changed")
        protected = json.loads((self.root / "input_hashes.json").read_text())
        for item in protected:
            path = Path(item["path"])
            stat = path.stat()
            require(stat.st_size == item["bytes"], "Protected input size changed")
            # Hash once at stage end only if filesystem metadata changed since initialization.
            if stat.st_mtime_ns != item["mtime_ns"]:
                require(fp(path)["sha256"] == item["sha256"], "Protected input bytes changed")
        operations = {"native_load_seconds": sum(r["load_seconds"] for r in records),
            "native_process_seconds": sum(r["process_wall"]["seconds"] for r in records),
            "python_audit_seconds": sum(p["audit_seconds"] for r in records for p in r["points"]),
            "python_compression_seconds": sum(p["compression_seconds"] for r in records for p in r["points"])}
        for key in ("capture_seconds", "warmup_seconds", "ordinary_seconds",
                    "validation_serialization_seconds", "batch_seconds"):
            operations[key] = sum(p["operations"][key] for r in records for p in r["points"])
        result = {"stage": stage, "state": "stage_complete_matrix_partial", "unique_points": 66,
            "ordinary_batches": 132, "ordinary_native_index_loads": 12, "records": records,
            "observed_threshold_points": thresholds, "operation_totals": operations,
            "protected_inputs_metadata_checked": len(protected), "accepted_libraries_objects_unchanged": True,
            "summary_sha256": fp(self.root / f"summary.stage_{stage}.json")["sha256"],
            "next_stage_started": False}
        write(self.root / f"stage_{stage}.json", result)
        self.progress()
        print(json.dumps({"stage": stage, "points": 66, "operation_totals": operations,
                          "summary_sha256": result["summary_sha256"]}, indent=2), flush=True)

    def finalize(self):
        require(len(self.records()) == 36, "Canonical summary requires all36 native processes")
        summary = []
        for stage in self.prereg["stages"]:
            path = self.root / f"summary.stage_{stage}.json"
            require(path.exists(), "Unfinished stage")
            summary.extend(json.loads(path.read_text()))
        require(len(summary) == 198 and len({(r["scenario"], r["case"], r["nprobe"]) for r in summary}) == 198,
                "Canonical matrix incomplete")
        for row in summary:
            row["matrix_complete"] = True
        require(not (self.root / "summary.json").exists(), "Preserve completed canonical summary")
        write(self.root / "summary.json", summary)
        write(self.root / "status.json", {"state": "matrix_complete", "ordinary_batches_completed": 396,
                                        "canonical_summary_published": True, "automatic_next_stage": False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "fixtures", "stage", "finalize"))
    parser.add_argument("--stage", choices=("unfilter_broad", "medium_extreme", "numeric_mixed"))
    parser.add_argument("--retry-incomplete", action="store_true")
    args = parser.parse_args()
    if args.command in ("fixtures", "stage") and not args.stage:
        parser.error("--stage is required; there is no implicit full sweep")
    experiment = Experiment()
    if args.command == "fixtures":
        experiment.fixtures(args.stage, args.retry_incomplete)
    elif args.command == "stage":
        experiment.stage(args.stage, args.retry_incomplete)
    elif args.command == "finalize":
        experiment.finalize()
