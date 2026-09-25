"""Load-once native array fixtures and the fresh 36-invocation sweep."""
import argparse
import copy
import hashlib
import csv
import importlib.util
import json
import math
from pathlib import Path
import shutil
import statistics
import struct
import tarfile

import numpy as np

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("predicate_degree_audit", HERE.parent / "predicate_degree_native/run.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
native = audit.native
require, write, fingerprint = native.require, native.write_json, native.fingerprint
META = {"nprobe", "probe_position", "probe_count", "native_index_load_calls",
        "native_workspace_resets", "capture_file", "index_load_count", "sweep_execution"}


def core(row):
    return {k: v for k, v in native.h3_core_work(row).items() if k not in META}


def parse(logs, probes, count):
    results, loads, boundaries = [], [], []
    for path in logs:
        with path.open() as stream:
            for line in stream:
                require("PhaseTime:" not in line, "Ordinary run has profiling overhead")
                if line.startswith('{"engine":'):
                    results.append(json.loads(line))
                if line.startswith("NATIVE_INDEX_LOAD "):
                    loads.append(line.strip())
                if line.startswith("NATIVE_PROBE_BEGIN "):
                    boundaries.append(line.strip())
    require(loads == ["NATIVE_INDEX_LOAD calls=1"], "Actual native LoadAll count is not exactly one")
    require([r["nprobe"] for r in results] == probes and len(boundaries) == len(probes),
            "Missing, extra or reordered native probe batches")
    for i, row in enumerate(results):
        require(row["probe_position"] == i and row["probe_count"] == len(probes) and
                row["native_index_load_calls"] == row["index_load_count"] == 1 and
                row["native_workspace_resets"] == i + 1,
                "Native load/workspace lifecycle mismatch")
        require(row["queries"] == count and row["failed_queries"] == 0 and
                math.isfinite(row["mean_latency_ms"]) and row["mean_latency_ms"] > 0,
                "Native query or latency failure")
    return results


class Experiment:
    def __init__(self):
        native.reject_environment_overrides()
        self.plan = native.read_ini(HERE / "experiment.ini")["Experiment"]
        self.root = Path(self.plan["OutputDirectory"])
        self.previous = Path(self.plan["PreviousResults"])
        self.toolchain = Path(self.plan["Toolchain"])
        self.prereg = json.loads((HERE / "preregistration.json").read_text())
        self.schedule = json.loads((HERE / "schedule.json").read_text())
        require(len(self.schedule) == 36 and self.prereg["unique_points"] == 198, "Changed protocol")
        for name, digest in self.prereg["configs"].items():
            require(fingerprint(HERE / "configs" / name)["sha256"] == digest, "INI changed after preregistration")
        require(fingerprint(HERE / "schedule.json")["sha256"] == self.prereg["schedule_sha256"],
                "Schedule changed after preregistration")
        proof = json.loads((self.toolchain / "batch_provenance.json").read_text())
        for name, digest in proof["after"].items():
            require(fingerprint(self.toolchain / "source" / name)["sha256"] == digest, "Isolated source changed")
        self.snapshot = self.root / "snapshot"
        if not self.root.exists():
            self.snapshot.mkdir(parents=True)
            shutil.copytree(HERE, self.snapshot / "experiment", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(self.plan["Binary"], self.snapshot / "spannaclbench")
            for name in ("batch_provenance.json", "configure.log", "build.log"):
                shutil.copy2(self.toolchain / name, self.snapshot / name)
            with tarfile.open(self.snapshot / "source.tar.gz", "w:gz") as archive:
                archive.add(self.toolchain / "source", arcname="source",
                            filter=lambda p: None if "/Release/" in p.name else p)
            previous_proof = json.loads((self.previous / "provenance.json").read_text())
            protected = previous_proof["protected"]
            require(all(fingerprint(p["path"]) == p for p in protected), "Previously protected inputs changed")
            protected += [fingerprint(p) for p in sorted(self.previous.rglob("*")) if p.is_file()]
            write(self.root / "provenance.json", {
                "protected": protected, "native_binary": fingerprint(self.snapshot / "spannaclbench"),
                "source_archive": fingerprint(self.snapshot / "source.tar.gz"),
                "experiment_files": [fingerprint(p) for p in sorted((self.snapshot / "experiment").rglob("*"))
                                     if p.is_file()],
                "source_change": proof["change"], "changed_native_files": proof["changed_files"],
                "supplier_degree_semantics": "predicate_valid_neighbors",
                "parent_completed_summary": fingerprint(self.previous / "summary.json"),
                "previous_scalar_protocol": "Parent is an independently verified native array dataset; "
                                            "its old INI spelling is superseded, timings are not reused.",
            })
            write(self.root / "status.json", {"state": "fixtures_pending", "measured_points": 0})
        require(fingerprint(Path(self.plan["Binary"]))["sha256"] ==
                fingerprint(self.snapshot / "spannaclbench")["sha256"], "Runtime differs from snapshot")
        for name in ("experiment.ini", "preregistration.json", "schedule.json", "run.py"):
            require((HERE / name).read_bytes() == (self.snapshot / "experiment" / name).read_bytes(),
                    "Runner/config differs from immutable snapshot")
        self.references = {
            (point["scenario"], point["case"], point["nprobe"]):
                {**point, "directory": run["directory"]}
            for run in json.loads((self.previous / "runs.json").read_text()) if run["repeat"] == 1
            for point in run["points"]}
        require(len(self.references) == 198, "Incomplete authentic scalar references")
        self.workloads = json.loads(Path(self.plan["Workloads"]).read_text())
        attrs = np.load(self.workloads["attributes"], mmap_mode="r")
        self.masks, self.truth, self.args = {}, {}, {}
        for scenario in self.prereg["scenarios"]:
            self.masks[scenario] = (np.ones(len(attrs), dtype=bool) if scenario == "unfilter" else
                                   native.predicate_mask(self.workloads["predicates"][scenario], attrs))
            require(int(self.masks[scenario].sum()) == self.workloads["truth"][scenario]["candidate_count"],
                    "Workload changed")
            self.truth[scenario] = np.load(self.workloads["truth"][scenario]["ids"], mmap_mode="r")
            self.args[scenario] = []
            if scenario in self.workloads["flat_query_tags"]:
                self.args[scenario] = ["--query-tags", self.workloads["flat_query_tags"][scenario],
                                       "--tag-column", "0"]
            elif scenario in ("numeric", "mixed_dnf"):
                self.args[scenario] = ["--query-dnf",
                                      self.workloads["query_dnf"]["numeric" if scenario == "numeric" else "mixed"]]
        graph = Path(self.plan["PhysicalGraph"])
        with graph.open("rb") as stream:
            n, width = struct.unpack("<ii", stream.read(8))
        require((n, width) == (160091, 32), "Unexpected graph")
        audit.PHYSICAL = np.memmap(graph, dtype="<i4", offset=8, mode="r", shape=(n, width))
        io = json.loads((self.previous / "batch_r1_unfilter_h1/native.io.json").read_text())
        audit.HEAD_VIDS = np.fromfile(Path(io["target"]).parent / "SPTAGHeadVectorIDs.bin",
                                     dtype="<u8", offset=8)
        require(len(audit.HEAD_VIDS) == n, "Invalid head VID map")

    def invoke(self, name, scenario, case, probes, config, count, warmup):
        directory = self.root / name
        record_path = directory / "record.json"
        search = self.snapshot / "experiment/configs" / config
        require(search.read_bytes() == (HERE / "configs" / config).read_bytes(), "Native config changed")
        command = ["numactl", "--cpunodebind=2", "--membind=2", str(self.snapshot / "spannaclbench"),
                   "--index", self.plan["Index"], "--queries", self.plan["Queries"],
                   "--truth", self.workloads["truth"][scenario]["ids"],
                   ("--search-ini" if "_fixture_desc" in config or "_scalar_" in config
                    else "--search-sweep-ini"), str(search), "--value-type", "Float", "--topk", "10",
                   "--warmup", str(warmup), "--measure-offset", "0", "--max-queries", str(count),
                   *self.args[scenario]]
        if record_path.exists():
            record = json.loads(record_path.read_text())
            require(record["command"] == command and record["probes"] == probes, "Resume changed invocation")
            return record
        directory.mkdir(exist_ok=True)
        logs = [directory / f"native.{part}.log" for part in ("stdout", "stderr")]
        if (directory / "native.exit.json").exists():
            require(json.loads((directory / "native.exit.json").read_text())["returncode"] == 0 and
                    json.loads((directory / "native.command.json").read_text())["command"] == command,
                    "Failed/interrupted native process cannot be declared complete")
        else:
            require(not list(directory.iterdir()), "Preserve incomplete invocation; do not overwrite raw data")
            logs = native.run_native(command, directory, "native", 0.2, True)
        results = parse(logs, probes, count)
        require(json.loads((directory / "native.io.json").read_text())["direct_io"], "Missing native O_DIRECT")
        points = []
        for result in results:
            probe = result["nprobe"]
            capture = directory / result["capture_file"]
            require(capture.parent == directory, "Unexpected native capture path")
            reference = self.references[scenario, case, probe]
            if count == 1000:
                digest = hashlib.sha256()
                with audit.query_stream(capture) as stream:
                    for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                        digest.update(block)
                require(digest.hexdigest() == reference["validation"]["raw_capture_sha256"],
                        "New native capture is not byte-identical to the fully audited parent capture")
                validation = copy.deepcopy(reference["validation"])
            else:
                validation = audit.audit_queries(capture, count, probe, case,
                                                 self.truth[scenario], self.masks[scenario])
            require(abs(validation["recall_at_10"] - result["recall"]) < 1e-12, "Capture/measured recall mismatch")
            reference = self.references[scenario, case, probe]
            expected = reference["validation"]["first8_payload_sha256" if count == 8 else "payload_sha256"]
            require(validation["payload_sha256"] == expected,
                    f"Native array/scalar ordered IDs/distances/head/work parity failed: {scenario}/{case}/{probe}")
            if count == 1000:
                require(core(result) == core(reference["native"]), "Native batch/scalar SSD work changed")
            audit.compress_capture(capture, validation["raw_capture_sha256"])
            point = {"scenario": scenario, "case": case, "nprobe": probe, "native": result,
                     "validation": validation, "scalar_reference": reference["directory"],
                     "validation_method": ("byte_exact_audited_parent_capture" if count == 1000
                                           else "full_native_fixture_audit")}
            points.append(point)
        record = {"directory": name, "scenario": scenario, "case": case, "probes": probes,
                  "command": command, "points": points, "native_index_load_calls": 1,
                  "native_stdout": fingerprint(logs[0]), "native_stderr": fingerprint(logs[1])}
        write(record_path, record)
        print(f"{name}: ONE native load, {len(probes)} probe batches, exact scalar payload parity", flush=True)
        return record

    def fixtures(self):
        records, small = [], {}
        for scenario in self.prereg["scenarios"]:
            for case in self.prereg["cases"]:
                for order, probes in (("asc", [16, 24, 384]), ("desc", [384, 24, 16])):
                    r = self.invoke(f"fixture_{scenario}_{case}_{order}", scenario, case, probes,
                                    f"{case}_fixture_{order}.ini", 8, 8)
                    for point in r["points"]:
                        key = scenario, case, point["nprobe"]
                        if key in small:
                            require(small[key]["validation"] == point["validation"] and
                                    core(small[key]["native"]) == core(point["native"]),
                                    "Ascending/descending array result/work mismatch")
                        small[key] = point
                    records.append(r)
        for case in self.prereg["cases"]:
            for probe in (16, 24, 384):
                r = self.invoke(f"scalar_unfilter_{case}_p{probe}", "unfilter", case, [probe],
                                f"{case}_scalar_{probe}.ini", 8, 8)
                point = r["points"][0]
                reference = small["unfilter", case, probe]
                require(point["validation"] == reference["validation"] and
                        core(point["native"]) == core(reference["native"]),
                        "New scalar execution differs from native array")
                records.append(r)
        require(len(records) == 45, "Incomplete native fixture matrix")
        write(self.root / "fixtures.json", records)
        write(self.root / "fixture_validation.json", {
            "native_array_invocations": 36, "native_scalar_invocations": 9,
            "native_load_calls_each": 1, "array_orderings": [[16, 24, 384], [384, 24, 16]],
            "query_count": 8, "warmup_per_probe": 8,
            "all_scenarios_modes_exact_scalar_reference_payload_equal": True,
            "ascending_descending_array_and_new_scalar_work_equal": True,
            "supplier_core_unchanged": True,
        })
        write(self.root / "status.json", {"state": "fixtures_passed", "measured_points": 0})

    def sweep(self):
        require((self.root / "fixture_validation.json").exists(), "Complete native fixtures before sweep")
        records = []
        write(self.root / "status.json", {"state": "running", "ordinary_groups_completed": 0})
        for job in self.schedule:
            scenario, case, repeat = job["scenario"], job["case"], job["repeat"]
            record = self.invoke(f"batch_r{repeat}_{scenario}_{case}", scenario, case, job["probes"],
                                 f"{case}_r{repeat}.ini", 1000, 1000)
            records.append({**record, "repeat": repeat})
            write(self.root / "runs.json", records)
            write(self.root / "progress.json", {"ordinary_groups_completed": len(records),
                  "ordinary_measurement_batches_completed": sum(len(r["points"]) for r in records),
                  "required_groups": 36, "required_measurement_batches": 396})
        summary = []
        for scenario in self.prereg["scenarios"]:
            for case in self.prereg["cases"]:
                for probe in self.prereg["grid"]:
                    pair = [(r, p) for r in records for p in r["points"]
                            if (p["scenario"], p["case"], p["nprobe"]) == (scenario, case, probe)]
                    require(len(pair) == 2, "Missing ordinary repetition")
                    (_, a), (_, b) = pair
                    require(a["validation"] == b["validation"] and core(a["native"]) == core(b["native"]),
                            "Ordinary repetition work/IDs changed")
                    v, n = a["validation"], a["native"]
                    ms = [p["native"]["mean_latency_ms"] for _, p in pair]
                    row = {"scenario": scenario, "case": case, "nprobe": probe,
                           **{k: v[k] for k in ("recall_at_10", "underfilled_queries", "empty_queries",
                                               "mean_returned", "filter_violations", "selected_h1")},
                           "ordinary_ms": statistics.mean(ms), "ordinary_ms_runs": ms,
                           "qps": 1000 / statistics.mean(ms), "qps_runs": [1000 / t for t in ms],
                           "qps_min": 1000 / max(ms), "qps_max": 1000 / min(ms),
                           "postings": n["postings_per_query"], "pages": n["posting_page_reads_per_query"],
                           "disk_distances": n["distance_computations_per_query"],
                           "head_actual_distances": (sum(v["supplier_work"][k] for k in
                               ("supplyGraph", "supplyRouting", "supplyParent", "supplyChild"))
                               if case == "supplier" else None),
                           "supplier_work": v["supplier_work"], "work": core(n),
                           "directories": [r["directory"] for r, _ in pair],
                           "timing_protocol": "native_load_once_nprobe_array",
                           "sweep_execution": "single_load_nprobe_array",
                           "nprobe_ini_api": "SearchSweep.NProbe"}
                    if case == "supplier":
                        row["supplier_degree_semantics"] = "predicate_valid_neighbors"
                        row["connectivity_evidence"] = v["connectivity_evidence"]
                    summary.append(row)
        require(len(records) == 36 and len(summary) == 198, "Incomplete batch sweep")
        protected = json.loads((self.root / "provenance.json").read_text())["protected"]
        require(all(fingerprint(p["path"]) == p for p in protected), "Protected historical evidence changed")
        write(self.root / "summary.json", summary)
        fields = list(dict.fromkeys(k for r in summary for k, v in r.items() if not isinstance(v, (list, dict))))
        with (self.root / "summary.csv").open("w") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows({k: r.get(k) for k in fields} for r in summary)
        thresholds = []
        table = ["# Observed native load-once nprobe sweep", "",
                 "Fresh batch timings only:36 index loads,396 ordinary batches,198 points.",
                 "Fastest observed QPS meeting each threshold; no interpolation.", "",
                 "| Scenario | Case | Target | nprobe | Recall | QPS | Underfilled |",
                 "|---|---|---:|---:|---:|---:|---:|"]
        for scenario in self.prereg["scenarios"]:
            for case in self.prereg["cases"]:
                points = [r for r in summary if (r["scenario"], r["case"]) == (scenario, case)]
                for target in (0.90, 0.95):
                    eligible = [r for r in points if r["recall_at_10"] >= target]
                    best = max(eligible, key=lambda r: r["qps"]) if eligible else None
                    thresholds.append({"scenario": scenario, "case": case, "target_recall": target,
                                       "reached": best is not None, "observed_best": best,
                                       "maximum_observed_recall": max(r["recall_at_10"] for r in points)})
                    cells = (f'{best["nprobe"]} | {best["recall_at_10"]:.4f} | {best["qps"]:.3f} | '
                             f'{best["underfilled_queries"]}' if best else "- | unreached | - | -")
                    table.append(f"| {scenario} | {case} | {target:.0%} | {cells} |")
        write(self.root / "thresholds.json", thresholds)
        (self.root / "report.md").write_text("\n".join(table) + "\n")
        write(self.root / "validation.json", {
            "measured_points": 198, "ordinary_measurement_batches": 396, "ordinary_index_load_invocations": 36,
            "actual_native_load_count_each_invocation": 1, "fixture_invocations": 45,
            "all_native_probes_explicit_and_in_preregistered_order": True,
            "all_capture_measurement_repetition_and_single_probe_payloads_equal": True,
            "all_scalar_reference_ssd_work_equal": True,
            "all_predicate_dedup_underfill_and_connectivity_frames_validated": True,
            "supplier_degree_semantics": "predicate_valid_neighbors",
            "supplier_rows": sum(r["case"] == "supplier" for r in summary),
            "protected_files_unchanged": len(protected),
            "summary_sha256": fingerprint(self.root / "summary.json")["sha256"],
            "timing_protocol": "Fresh SearchSweep.NProbe arrays; no previous timings spliced.",
            "sweep_execution": "single_load_nprobe_array",
            "nprobe_ini_api": "SearchSweep.NProbe",
            "full_capture_validation": "Every byte matched the fully audited parent native capture; "
                                       "fresh fixtures additionally received full semantic audits",
        })
        write(self.root / "status.json", {"state": "completed", "measured_points": 198,
              "ordinary_measurement_batches": 396, "ordinary_index_load_invocations": 36})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("fixtures", "sweep", "all"))
    options = parser.parse_args()
    experiment = Experiment()
    try:
        if options.phase in ("fixtures", "all"):
            experiment.fixtures()
        if options.phase in ("sweep", "all"):
            experiment.sweep()
    except BaseException as error:
        write(experiment.root / "status.json", {"state": "failed_or_interrupted", "error": repr(error)})
        raise
