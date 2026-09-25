"""Bounded native-reuse fixtures and paired unfilter24 benchmark; no grid/resume mode."""
import argparse
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import statistics
import tarfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("native_reuse_parent", HERE.parent / "ratio_degree_phase1/run.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)
native, require, write, fp = parent.native, parent.require, parent.write, parent.fp
NEW_FIELDS = {"native_default_admission", "native_filtered_admission", "row_eligibility_evaluations"}


def capture_rows(path):
    with gzip.open(path, "rt") as stream:
        return [json.loads(line) for line in stream]


class Experiment(parent.Phase1):
    def __init__(self):
        # Reuse only read-only input/predicate setup, never an old runner phase.
        super().__init__()
        self.plan = native.read_ini(HERE / "experiment.ini")["Experiment"]
        self.root, self.toolchain = Path(self.plan["OutputDirectory"]), Path(self.plan["Toolchain"])
        self.snapshot = self.root / "snapshot"
        self.prereg = json.loads((HERE / "preregistration.json").read_text())
        proof = json.loads((self.toolchain / "native_reuse_provenance.json").read_text())
        for name, digest in proof["after"].items():
            require(fp(self.toolchain / "source" / name)["sha256"] == digest, "Changed native source")
        for name, digest in self.prereg["configs"].items():
            require(fp(HERE / "configs" / name)["sha256"] == digest, "Changed native INI")
        if not self.root.exists():
            self.snapshot.mkdir(parents=True)
            shutil.copytree(HERE, self.snapshot / "experiment", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(self.plan["Binary"], self.snapshot / "spannaclbench")
            for name in ("native_reuse_provenance.json", "tests.log", "build-owner-load.log"):
                shutil.copy2(self.toolchain / name, self.snapshot / name)
            with tarfile.open(self.snapshot / "source.tar.gz", "w:gz") as archive:
                archive.add(self.toolchain / "source", arcname="source",
                            filter=lambda p: None if "/Release/" in p.name else p)
            write(self.root / "provenance.json", {"binary": fp(self.snapshot / "spannaclbench"),
                "source_archive": fp(self.snapshot / "source.tar.gz"), "source": proof, "protocol": self.prereg})
        require(fp(self.snapshot / "spannaclbench")["sha256"] == fp(Path(self.plan["Binary"]))["sha256"],
                "Changed frozen native binary")

    def audit(self, path, count, probe, scenario):
        payload, results, heads = [hashlib.sha256() for _ in range(3)]
        totals, histogram = {}, {}
        hits = 0
        for q, line in enumerate(path.read_bytes().splitlines()):
            row = json.loads(line)
            require(row["query"] == q and q < count and row["native_raw_distance_function"] == 1,
                    "Invalid native primitive/query sequence")
            valid = [(int(i), float(d)) for i, d in row["results"] if i >= 0]
            require(len(row["results"]) == 10 and len(set(i for i, _ in valid)) == len(valid),
                    "Invalid native top10/dedup")
            require(all(i < len(self.masks[scenario]) and self.masks[scenario][i] and math.isfinite(d) and d >= 0
                        for i, d in valid), "Invalid native final result")
            require(len(row["heads"]) == probe, "Wrong native capacity")
            require(row["supplier_calls"] == row["supplier_returns"], "Supplier continuation mismatch")
            require(row["queue_offers"] == row["queue_accepted"] + row["queue_rejected"], "Native heap ledger")
            rows = row["rows"]
            require(len(set((r[0], r[1]) for r in rows)) == len(rows) and
                    all(r[2] == r[3] for r in rows) and sum(r[3] for r in rows) == row["supplier_members"],
                    "Rescanned/truncated selected CSR row")
            require(not set((r[0], r[1]) for r in rows).intersection(map(tuple, row["rejected_rows"])),
                    "Signature-rejected row scanned")
            require(sum(f["calls"] for f in row["degree_frames"]) == row["supplier_calls"], "Frame/call ledger")
            for f in row["degree_frames"]:
                physical, _, _ = parent.old.physical_row(f["head"])
                require([i for i, _ in f["ordinary"]] == physical and f["d"] == len(physical) and
                        f["e"] == sum(bool(v) for _, v in f["ordinary"]), "Wrong native degree or visited semantics")
                require(f["required"] == (0 if f["d"] < 16 else (f["d"] + 1) // 2) and
                        (not f["calls"] or (f["d"] >= 16 and 2 * f["e"] < f["d"] and f["checked_before"] < 2048)),
                        "Wrong ratio/native budget trigger")
                require(len(set(f["supplied"])) == len(f["supplied"]) and
                        not set(f["supplied"]).intersection(physical) and f["head"] not in f["supplied"],
                        "Duplicate supplied/ordinary connectivity")
                key = f'{f["d"]}/{f["e"]}'
                histogram[key] = histogram.get(key, 0) + 1
            if scenario == "unfilter":
                require(all(row[k] == 0 for k in ("supplier_calls", "supplier_parent_distances",
                            "native_child_distances", "supplier_members", "row_eligibility_evaluations",
                            "native_filtered_admission")) and row["native_default_admission"] == 1,
                        "Unfilter failed certified native default admission")
                require(not row["own_ids"] and all(f["d"] == f["e"] for f in row["degree_frames"]),
                        "Native default used custom own admission or nonconstant eligibility")
            else:
                require(row["native_default_admission"] == 0 and row["native_filtered_admission"] == 1,
                        "Exact predicate bypassed native filtered admission")
            hits += len(set(i for i, _ in valid) & set(self.truth[scenario][q, :10]))
            for digest, value in ((payload, row), (results, row["results"]), (heads, row["heads"])):
                parent.old.digest_update(digest, value)
            for k, value in row.items():
                if isinstance(value, (int, float)) and k != "query":
                    totals[k] = totals.get(k, 0) + value
        require(q + 1 == count, "Truncated native capture")
        return {"queries": count, "recall_at_10": hits / (count * 10), "payload_sha256": payload.hexdigest(),
                "results_sha256": results.hexdigest(), "heads_sha256": heads.hexdigest(),
                "raw_sha256": fp(path)["sha256"], "degree_histogram": histogram,
                "work": {k: v / count for k, v in totals.items()}}

    def invoke(self, name, case, scenario, probes, config, count, profile=False):
        directory = self.root / name
        directory.mkdir()
        command = ["numactl", "--cpunodebind=2", "--membind=2", str(self.snapshot / "spannaclbench"),
                   "--index", self.plan["Index"], "--queries", self.plan["Queries"],
                   "--truth", self.workloads["truth"][scenario]["ids"], "--search-sweep-ini",
                   str(self.snapshot / "experiment/configs" / config), "--value-type", "Float",
                   "--topk", "10", "--warmup", str(count), "--measure-offset", "0",
                   "--max-queries", str(count), *self.args[scenario]]
        logs = native.run_native(command, directory, "native", 0.2, True)
        text = "\n".join(p.read_text() for p in logs)
        require(text.splitlines().count("NATIVE_INDEX_LOAD calls=1") == 1 and
                text.count("NATIVE_SUPPLIER_OWNER_LOAD count=1") == 1, "Not one native/index-metadata load")
        require(text.count("NATIVE_DEFAULT_CERT heads=160091 own_all_live=1 immutable=1 vectors=1000000") == 1,
                "Missing actual immutable live-own certificate")
        for part in ("Vector (160091,128)", "BKT (1,160093)", "RNG (160091,32)",
                     "Vector (4098,128)", "BKT (1,4100)", "RNG (4098,32)"):
            require(text.count("Load " + part + " Finish!") == 1, "Physical component reloaded")
        rows = [json.loads(line) for line in text.splitlines() if line.startswith('{"engine":')]
        require([r["nprobe"] for r in rows] == probes, "Wrong native probe sequence")
        require(("PhaseTime:" in text) == profile, "Wrong clock protocol")
        points = []
        for position, row in enumerate(rows):
            require(not row["failed_queries"] and row["queries"] == count and
                    row["index_load_count"] == row["native_index_load_calls"] == 1 and
                    row["native_workspace_resets"] == position + 1, "Native lifecycle failure")
            capture = directory / row["capture_file"]
            validation = self.audit(capture, count, row["nprobe"], scenario)
            require(abs(validation["recall_at_10"] - row["recall"]) < 1e-12, "Native final recall mismatch")
            parent.old.compress_capture(capture, validation["raw_sha256"])
            points.append({"native": row, "validation": validation})
        record = {"directory": name, "case": case, "scenario": scenario, "profile": profile, "points": points}
        write(directory / "record.json", record)
        print(name, [(p["native"]["nprobe"], p["native"]["recall"], p["native"]["mean_latency_ms"])
                     for p in points], flush=True)
        return record

    def fixtures(self):
        records = []
        for case in ("h1", "supplier"):
            first = None
            for order, probes in (("asc", [16,24,384]), ("desc", [384,24,16])):
                r = self.invoke(f"fixture_{case}_{order}", case, "unfilter", probes, f"{case}_{order}.ini", 8)
                current = {p["native"]["nprobe"]: p for p in r["points"]}
                if first:
                    for probe in probes:
                        require(first[probe]["validation"]["payload_sha256"] ==
                                current[probe]["validation"]["payload_sha256"] and
                                parent.core(first[probe]["native"]) == parent.core(current[probe]["native"]),
                                "Forward/reverse native parity failure")
                first = current
                records.append(r)
        for scenario in ("medium_tag", "extreme_tag", "numeric", "mixed_dnf"):
            records.append(self.invoke(f"fixture_{scenario}", "supplier", scenario, [24], "supplier_plain.ini", 8))
        previous = self.root.parent / "h1_native_reuse_20260917_v2"
        for record in records:
            for point in record["points"]:
                file = point["native"]["capture_file"] + ".gz"
                current = capture_rows(self.root / record["directory"] / file)
                if record["scenario"] == "unfilter":
                    baseline = capture_rows(self.root / record["directory"].replace("supplier", "h1") / file)
                    ignored = {"degree_frames"}
                else:
                    baseline = capture_rows(previous / record["directory"] / file)
                    ignored = NEW_FIELDS
                require(len(current) == len(baseline) and
                        all({k: v for k, v in a.items() if k not in ignored} ==
                            {k: v for k, v in b.items() if k not in ignored}
                            for a, b in zip(current, baseline)), "Native baseline/filtered-v2 capture mismatch")
        write(self.root / "fixtures.json", records)
        write(self.root / "fixture_validation.json", {"native_loads": 8, "forward_reverse_parity": True,
              "full_native_validity": True, "raw_native_distance_function": True, "unfilter_zero_supplier_work": True,
              "certified_default_unfilter": True, "filtered_v2_full_payload_parity_except_new_metadata": True,
              "unfilter_h1_full_payload_parity_except_capture_degree_observation": True})

    def measure(self):
        require((self.root / "fixture_validation.json").exists(), "Native fixtures must pass first")
        runs = []
        for repeat, order in enumerate(self.prereg["ordinary_order"], 1):
            for case in order:
                runs.append(self.invoke(f"plain_r{repeat}_{case}", case, "unfilter", [24], f"{case}_plain.ini", 1000))
        for case in ("h1", "supplier"):
            runs.append(self.invoke(f"profile_{case}", case, "unfilter", [24], f"{case}_profile.ini", 1000, True))
        write(self.root / "runs.json", runs)
        summary = []
        for case in ("h1", "supplier"):
            selected = [r for r in runs if r["case"] == case]
            point = selected[0]["points"][0]
            for r in selected[1:]:
                require(r["points"][0]["validation"]["payload_sha256"] == point["validation"]["payload_sha256"] and
                        parent.core(r["points"][0]["native"]) == parent.core(point["native"]),
                        "Ordinary/profile/repetition native parity failure")
            times = [r["points"][0]["native"]["mean_latency_ms"] for r in selected if not r["profile"]]
            summary.append({"scenario": "unfilter", "case": case, "nprobe": 24,
                "sweep_execution": "single_load_nprobe_array", "supplier_degree_semantics": "predicate_valid_neighbors",
                "retained_ratio": 0.5, "minimum_physical_degree": 16,
                "ordinary_ms": statistics.mean(times), "ordinary_ms_runs": times, "qps": 1000 / statistics.mean(times),
                "recall_at_10": point["native"]["recall"], "work_source": "untimed_native_capture",
                "work": point["validation"]["work"], "results_sha256": point["validation"]["results_sha256"],
                "heads_sha256": point["validation"]["heads_sha256"],
                "postings": point["native"]["postings_per_query"],
                "pages": point["native"]["posting_page_reads_per_query"],
                "disk_distances": point["native"]["distance_computations_per_query"]})
        write(self.root / "summary.json", summary)
        h1 = capture_rows(self.root / "plain_r1_h1/queries.jsonl.nprobe_24.gz")
        supplier = capture_rows(self.root / "plain_r1_supplier/queries.jsonl.nprobe_24.gz")
        require(len(h1) == len(supplier) == 1000 and all(
            {k: v for k, v in a.items() if k != "degree_frames"} ==
            {k: v for k, v in b.items() if k != "degree_frames"}
            for a, b in zip(h1, supplier)), "Full native default/H1 work or result mismatch")
        write(self.root / "default_parity.json", {"queries": 1000,
            "all_heads_final_ids_distances_native_work_equal": True,
            "only_extra_capture_field": "degree_frames",
            "explicit_queries": {str(q): {"heads": supplier[q]["heads"], "results": supplier[q]["results"],
                "native_checked": supplier[q]["native_checked"]} for q in (661, 772, 915)}})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("fixtures", "measure"))
    options = parser.parse_args()
    run = Experiment()
    getattr(run, options.phase)()
