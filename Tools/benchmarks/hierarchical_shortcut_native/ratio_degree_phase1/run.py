"""Execute only the preregistered focused ratio Phase1; no full-sweep entrypoint."""
import argparse
import csv
import hashlib
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
spec = importlib.util.spec_from_file_location("ratio_parent_audit", HERE.parent / "predicate_degree_native/run.py")
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)
native = old.native
require, write, fp = native.require, native.write_json, native.fingerprint
META = {"nprobe", "probe_position", "probe_count", "native_index_load_calls", "native_workspace_resets",
        "capture_file", "index_load_count", "sweep_execution"}


def core(row):
    return {k: v for k, v in native.h3_core_work(row).items() if k not in META}


def audit(path, count, probe, case, mask, truth):
    raw, payload, results, heads = [hashlib.sha256() for _ in range(4)]
    hits = returned = underfill = 0
    totals, histogram = {}, {}
    for q, line in enumerate(old.query_stream(path)):
        row = json.loads(line)
        require(row["query"] == q and q < count, "Invalid native query sequence")
        raw.update(line)
        valid = [(int(i), float(d)) for i, d in row["results"] if i >= 0]
        require(len(row["results"]) == 10 and len({i for i, _ in valid}) == len(valid), "Invalid top10/dedup")
        require(all(i < len(mask) and mask[i] and math.isfinite(d) and d >= 0 for i, d in valid),
                "Invalid final predicate or distance")
        require(len(row["heads"]) == probe, "Wrong native nprobe capacity")
        old.digest_update(results, row["results"])
        old.digest_update(heads, row["heads"])
        old.digest_update(payload, {k: v for k, v in row.items()
                                   if k not in ("calls", "adjacency", "supplyTrace") and not k.endswith("Ns")})
        hits += len({i for i, _ in valid} & set(truth[q, :10]))
        returned += len(valid); underfill += len(valid) < 10
        if case != "h1":
            require(row["supplyStarts"] == 1 and row["supplyCalls"] == row["supplyReturns"] and
                    row["supplyH2Rows"] == row["supplyH2Completed"] and
                    row["supplyH3Rows"] == row["supplyH3Completed"], "Restart/partial posting")
            rows = row["row_audits"]
            require(len({(r[0], r[1]) for r in rows}) == len(rows) and all(r[2] == r[3] for r in rows),
                    "Row rescan/truncation")
            require(sum(r[2] for r in rows) == row["supplyMembers"] ==
                    row["supplyUpperMembers"] + row["supplyLowerMembers"], "Member ledger mismatch")
            require(row["supplyQueueOffers"] == row["supplyQueueAccepted"] + row["supplyQueueRejected"],
                    "Native queue offer/rejection ledger mismatch")
            require(sum(row[k] for k in ("supplyGraph", "supplyRouting", "supplyParent", "supplyChild")) ==
                    row["supplyCandidates"] + row["supplyRepeats"], "Actual distance ledger mismatch")
            require(row["supplyNativeMaxCheck"] == 2048 and not row["exhausted"], "Changed/custom budget")
            frames = row["connectivity_audits"]
            require(len(frames) == row["supplyConnChecks"] and
                    sum(a["calls"] for a in frames) == row["supplyCalls"] and
                    sum(a["before"] for a in frames) == row["supplyConnBefore"] and
                    sum(a["after"] for a in frames) == row["supplyConnAfter"], "Frame ledger mismatch")
            evaluated = {int(h): flag for (h, _), flag in zip(row["evaluated_h1"], row["qualifications"])}
            for a in frames:
                physical, stop, marker = old.physical_row(a["node"])
                flags = [flag for _, flag in a["ordinary"]]
                require([i for i, _ in a["ordinary"]] == physical and a["physical"] == len(physical) and
                        a["stop_slot"] == stop and a["terminator"] == marker, "Wrong physical native adjacency")
                d, e = len(physical), sum(bool(f) for f in flags)
                require(a["before"] == e and a["required"] == (0 if d < 16 else (d + 1) // 2),
                        "Wrong retained ratio goal")
                require(not a["calls"] or (d >= 16 and 2 * e < d), "Invalid ratio trigger")
                require(all(bool(f & 2) == bool(mask[old.HEAD_VIDS[i]]) for i, f in a["ordinary"]),
                        "Wrong own eligibility")
                supplied = a["supplied"]
                require(len(supplied) == len(set(supplied)) == a["after"] - e and
                        not set(supplied).intersection(physical) and a["node"] not in supplied and
                        all(evaluated.get(i, 0) for i in supplied), "Duplicate/ineligible supplied connectivity")
                key = f"{d}/{e}"
                item = histogram.setdefault(key, {"frames": 0, "calls": 0})
                item["frames"] += 1; item["calls"] += a["calls"]
        for k, v in row.items():
            if k.startswith("supply"):
                totals[k] = totals.get(k, 0) + v
    require(q + 1 == count, "Truncated native capture")
    return {"queries": count, "recall_at_10": hits / (count * 10), "underfilled_queries": underfill,
            "mean_returned": returned / count, "raw_capture_sha256": raw.hexdigest(),
            "payload_sha256": payload.hexdigest(), "results_sha256": results.hexdigest(),
            "heads_sha256": heads.hexdigest(), "degree_histogram": histogram,
            "work": {k: v / count for k, v in totals.items()}}


class Phase1:
    def __init__(self):
        native.reject_environment_overrides()
        self.plan = native.read_ini(HERE / "experiment.ini")["Experiment"]
        self.root = Path(self.plan["OutputDirectory"])
        self.toolchain = Path(self.plan["Toolchain"])
        self.prereg = json.loads((HERE / "preregistration.json").read_text())
        require(self.prereg["phase"] == "1_only_no_full_sweep", "Wrong experiment scope")
        for name, h in self.prereg["configs"].items():
            require(fp(HERE / "configs" / name)["sha256"] == h, "Preregistered config changed")
        proof = json.loads((self.toolchain / "ratio_provenance.json").read_text())
        for name, h in proof["after"].items():
            require(fp(self.toolchain / "source" / name)["sha256"] == h, "Native source changed")
        self.snapshot = self.root / "snapshot"
        if not self.root.exists():
            self.snapshot.mkdir(parents=True)
            shutil.copytree(HERE, self.snapshot / "experiment", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(self.plan["Binary"], self.snapshot / "spannaclbench")
            for name in ("ratio_provenance.json", "configure.log", "build.log", "supplier-tests.log"):
                shutil.copy2(self.toolchain / name, self.snapshot / name)
            with tarfile.open(self.snapshot / "source.tar.gz", "w:gz") as archive:
                archive.add(self.toolchain / "source", arcname="source",
                            filter=lambda p: None if "/Release/" in p.name else p)
            write(self.root / "provenance.json", {
                "native_binary": fp(self.snapshot / "spannaclbench"),
                "source_archive": fp(self.snapshot / "source.tar.gz"), "source_change": proof,
                "parent_summary": fp(Path(self.plan["PreviousResults"]) / "summary.json"),
                "configs": [fp(p) for p in sorted((self.snapshot / "experiment/configs").iterdir())],
                "workloads": fp(Path(self.plan["Workloads"])), "physical_graph": fp(Path(self.plan["PhysicalGraph"])),
                "scope": self.prereg,
            })
        require(fp(Path(self.plan["Binary"]))["sha256"] == fp(self.snapshot / "spannaclbench")["sha256"],
                "Binary differs from frozen snapshot")
        require((HERE / "run.py").read_bytes() == (self.snapshot / "experiment/run.py").read_bytes(),
                "Runner changed after snapshot")
        self.workloads = json.loads(Path(self.plan["Workloads"]).read_text())
        attrs = np.load(self.workloads["attributes"], mmap_mode="r")
        self.masks, self.truth, self.args = {}, {}, {}
        for scenario in ("unfilter", "medium_tag", "extreme_tag", "numeric", "mixed_dnf"):
            self.masks[scenario] = (np.ones(len(attrs), dtype=bool) if scenario == "unfilter" else
                                   native.predicate_mask(self.workloads["predicates"][scenario], attrs))
            self.truth[scenario] = np.load(self.workloads["truth"][scenario]["ids"], mmap_mode="r")
            self.args[scenario] = []
            if scenario in self.workloads["flat_query_tags"]:
                self.args[scenario] = ["--query-tags", self.workloads["flat_query_tags"][scenario], "--tag-column", "0"]
            elif scenario in ("numeric", "mixed_dnf"):
                self.args[scenario] = ["--query-dnf", self.workloads["query_dnf"]["numeric" if scenario == "numeric" else "mixed"]]
        graph = Path(self.plan["PhysicalGraph"])
        with graph.open("rb") as stream:
            n, width = struct.unpack("<ii", stream.read(8))
        require((n, width) == (160091, 32), "Wrong native graph layout")
        old.PHYSICAL = np.memmap(graph, dtype="<i4", offset=8, shape=(n, width), mode="r")
        io = json.loads((Path(self.plan["PreviousResults"]) / "batch_r1_unfilter_h1/native.io.json").read_text())
        old.HEAD_VIDS = np.fromfile(Path(io["target"]).parent / "SPTAGHeadVectorIDs.bin", dtype="<u8", offset=8)

    def invoke(self, name, case, scenario, probes, config, count, warmup, profile=False):
        directory = self.root / name
        require(not directory.exists(), "Do not resume or overwrite a stopped native attempt")
        directory.mkdir()
        command = ["numactl", "--cpunodebind=2", "--membind=2", str(self.snapshot / "spannaclbench"),
                   "--index", self.plan["Index"], "--queries", self.plan["Queries"],
                   "--truth", self.workloads["truth"][scenario]["ids"],
                   "--search-sweep-ini", str(self.snapshot / "experiment/configs" / config),
                   "--value-type", "Float", "--topk", "10", "--warmup", str(warmup),
                   "--measure-offset", "0", "--max-queries", str(count), *self.args[scenario]]
        logs = native.run_native(command, directory, "native", 0.2, True)
        native_rows, phases, loads = [], [], []
        for path in logs:
            for line in path.read_text().splitlines():
                if line.startswith('{"engine":'):
                    native_rows.append(json.loads(line))
                if line.startswith("NATIVE_INDEX_LOAD "):
                    loads.append(line)
                if "PhaseTime:" in line:
                    phases.append(line)
        require(loads == ["NATIVE_INDEX_LOAD calls=1"] and [r["nprobe"] for r in native_rows] == probes,
                "Not one native array load/loop")
        require(bool(phases) == profile, "Wrong profiling mode")
        require(json.loads((directory / "native.io.json").read_text())["direct_io"], "Not O_DIRECT")
        phase = None
        if profile:
            require(len(probes) == 1, "Bounded profiles use only24")
            _, phase = native.h3_records(logs, count, warmup + count, 24, True)
            native.validate_phase_balance(phase)
        points = []
        for i, result in enumerate(native_rows):
            require(result["queries"] == count and not result["failed_queries"] and
                    result["index_load_count"] == result["native_index_load_calls"] == 1 and
                    result["native_workspace_resets"] == i + 1, "Invalid native lifecycle/result")
            capture = directory / result["capture_file"]
            v = audit(capture, count, result["nprobe"], case, self.masks[scenario], self.truth[scenario])
            require(abs(v["recall_at_10"] - result["recall"]) < 1e-12, "Capture/timing recall mismatch")
            if scenario == "unfilter" and case != "h1":
                require(all(v["work"][k] == 0 for k in
                            ("supplyCalls", "supplyParent", "supplyChild", "supplyMembers",
                             "supplyH2Rows", "supplyH3Rows")), "All-valid unfilter invoked supplier")
            old.compress_capture(capture, v["raw_capture_sha256"])
            points.append({"native": result, "validation": v})
        record = {"directory": name, "case": case, "scenario": scenario, "probes": probes,
                  "profile": profile, "points": points, "phases": phase,
                  "stdout": fp(logs[0]), "stderr": fp(logs[1])}
        write(directory / "record.json", record)
        print(name, [(p["native"]["nprobe"], p["native"]["recall"], p["native"]["mean_latency_ms"])
                     for p in points], flush=True)
        return record

    def fixtures(self):
        records = []
        for case in ("h1", "control", "supplier"):
            first = None
            for order, probes in (("asc", [16,24,384]), ("desc", [384,24,16])):
                record = self.invoke(f"fixture_unfilter_{case}_{order}", case, "unfilter", probes,
                                     f"{case}_{order}.ini", 8, 8)
                by_probe = {p["native"]["nprobe"]: p for p in record["points"]}
                if first:
                    for probe in probes:
                        require(first[probe]["validation"]["payload_sha256"] ==
                                by_probe[probe]["validation"]["payload_sha256"] and
                                core(first[probe]["native"]) == core(by_probe[probe]["native"]),
                                "Forward/reverse native parity failed")
                first = by_probe
                records.append(record)
        for scenario in ("medium_tag", "extreme_tag", "numeric", "mixed_dnf"):
            records.append(self.invoke(f"fixture_{scenario}_supplier", "supplier", scenario, [24],
                                       "supplier_plain.ini", 8, 8))
        write(self.root / "fixtures.json", records)
        write(self.root / "fixture_validation.json", {"native_processes": 10, "ratio_semantics_valid": True,
              "unfilter_zero_supplier_work": True, "forward_reverse_parity": True})

    def measure(self):
        require((self.root / "fixture_validation.json").exists(), "Focused fixtures required first")
        records = []
        for repeat, order in enumerate(self.prereg["ordinary_order"], 1):
            for case in order:
                record = self.invoke(f"plain_r{repeat}_{case}", case, "unfilter", [24],
                                     f"{case}_plain.ini", 1000, 1000)
                records.append({**record, "repeat": repeat})
        for case in ("h1", "control", "supplier", "reference"):
            records.append(self.invoke(f"profile_{case}", case, "unfilter", [24],
                                       f"{case}_profile.ini", 1000, 1000, True))
        write(self.root / "runs.json", records)
        summary = []
        for case in ("h1", "control", "supplier", "reference"):
            pair = [r for r in records if r["case"] == case and not r["profile"]]
            profile = next(r for r in records if r["case"] == case and r["profile"])
            a, b = [r["points"][0] for r in pair]
            require(a["validation"]["payload_sha256"] == b["validation"]["payload_sha256"] and
                    core(a["native"]) == core(b["native"]), "Ordinary repetition mismatch")
            require(a["validation"]["payload_sha256"] == profile["points"][0]["validation"]["payload_sha256"] and
                    core(a["native"]) == core(profile["points"][0]["native"]), "Profile/ordinary ID/work mismatch")
            v, n = a["validation"], a["native"]
            ms = [r["points"][0]["native"]["mean_latency_ms"] for r in pair]
            summary.append({
                "scenario": "unfilter", "case": case, "nprobe": 24,
                "sweep_execution": "single_load_nprobe_array", "nprobe_ini_api": "SearchSweep.NProbe",
                "supplier_degree_semantics": "retained_eligible_ratio",
                "retained_ratio": 0.5, "minimum_physical_degree": 16,
                "recall_at_10": v["recall_at_10"], "underfilled_queries": v["underfilled_queries"],
                "mean_returned": v["mean_returned"], "ordinary_ms": statistics.mean(ms),
                "ordinary_ms_runs": ms, "qps": 1000 / statistics.mean(ms),
                "qps_min": 1000 / max(ms), "qps_max": 1000 / min(ms),
                "postings": n["postings_per_query"], "pages": n["posting_page_reads_per_query"],
                "disk_distances": n["distance_computations_per_query"],
                "work": v["work"], "profile_component_work": profile["points"][0]["validation"]["work"],
                "phases": profile["phases"], "result_hash": v["results_sha256"], "heads_hash": v["heads_sha256"],
                "degree_histogram": v["degree_histogram"],
            })
        by_case = {r["case"]: r for r in summary}
        for case in ("control", "reference"):
            require(by_case[case]["result_hash"] == by_case["supplier"]["result_hash"] and
                    by_case[case]["heads_hash"] == by_case["supplier"]["heads_hash"],
                    "Same ratio/no-supplier unfilter results disagree")
        write(self.root / "summary.json", summary)
        with (self.root / "summary.csv").open("w") as stream:
            keys = [k for k, v in summary[0].items() if not isinstance(v, (list, dict))]
            writer = csv.DictWriter(stream, fieldnames=keys)
            writer.writeheader(); writer.writerows({k: r[k] for k in keys} for r in summary)
        write(self.root / "validation.json", {
            "ordinary_native_processes": 8, "profile_native_processes": 4, "small_fixture_processes": 10,
            "actual_native_load_calls_per_process": 1, "full_sweep_launched": False,
            "unfilter_actual_supplier_calls_parent_child_distances_and_csr_reads_zero": True,
            "profile_ordinary_repetition_exact_payload_work_parity": True,
            "h1_result_ids_equal_supplier": by_case["h1"]["result_hash"] == by_case["supplier"]["result_hash"],
            "h1_selected_heads_equal_supplier": by_case["h1"]["heads_hash"] == by_case["supplier"]["heads_hash"],
            "same_policy_reference_optimized_result_ids_equal": True,
            "summary_sha256": fp(self.root / "summary.json")["sha256"],
        })
        write(self.root / "status.json", {"state": "completed_phase1", "full_sweep_allowed": False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("fixtures", "measure"))
    options = parser.parse_args()
    experiment = Phase1()
    if options.phase == "fixtures":
        experiment.fixtures()
    else:
        experiment.measure()
