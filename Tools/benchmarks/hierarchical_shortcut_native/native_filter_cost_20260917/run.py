"""Bounded real nonempty predicate diagnostic, not a curve runner."""
import argparse
import functools
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import time

import numpy as np

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("filter_cost_curve_audit", HERE.parent / "native_ratio_curve_20260917/run.py")
curve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(curve)
native, require, fp, write = curve.native, curve.require, curve.fp, curve.write


class Experiment:
    def __init__(self, config):
        native.reject_environment_overrides()
        self.plan = native.read_ini(config)["Experiment"]
        self.tool = Path(self.plan["Toolchain"])
        self.root = Path(self.plan["OutputDirectory"])
        self.prereg = json.loads((HERE / "preregistration.json").read_text())
        require("records=1000000 own_heads=160091 predicate_empty=0 numeric=1" in
                (self.tool / "native-coverage.log").read_text(), "Native coverage proof required")
        require(fp(Path(self.plan["AllTruePredicate"]))["sha256"] == self.prereg["predicate_sha256"],
                "Actual predicate changed")
        proof = json.loads((self.tool / "diagnostic_provenance.json").read_text())
        for name, digest in proof["after"].items():
            require(fp(self.tool / "source" / name)["sha256"] == digest, "Changed diagnostic source")
        if not self.root.exists():
            snap = self.root / "snapshot"
            snap.mkdir(parents=True)
            shutil.copytree(HERE, snap / "experiment", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(self.plan["Binary"], snap / "spannaclbench")
            shutil.copy2(config, snap / "active_experiment.ini")
            for name in ("diagnostic_provenance.json", "predicate_coverage.json", "native-coverage.log", "tests.log"):
                shutil.copy2(self.tool / name, snap / name)
            write(self.root / "provenance.json", {"binary": fp(snap / "spannaclbench"), "source": proof,
                  "protocol": self.prereg, "actual_experiment": fp(config)})
        self.binary = self.root / "snapshot/spannaclbench"
        require(fp(self.binary)["sha256"] == fp(Path(self.plan["Binary"]))["sha256"], "Native binary changed")
        self.workloads = json.loads(Path(self.plan["Workloads"]).read_text())
        attrs = np.load(self.workloads["attributes"], mmap_mode="r")
        self.masks = {"alltrue": np.ones(len(attrs), dtype=bool), "unfilter": np.ones(len(attrs), dtype=bool)}
        self.truth = {s: np.load(self.workloads["truth"]["unfilter"]["ids"], mmap_mode="r")
                      for s in self.masks}
        self.args = {"unfilter": [], "alltrue": ["--query-dnf", self.plan["AllTruePredicate"]]}
        for scenario in ("medium_tag", "extreme_tag", "numeric", "mixed_dnf"):
            self.masks[scenario] = native.predicate_mask(self.workloads["predicates"][scenario], attrs)
            self.truth[scenario] = np.load(self.workloads["truth"][scenario]["ids"], mmap_mode="r")
            self.args[scenario] = (["--query-tags", self.workloads["flat_query_tags"][scenario], "--tag-column", "0"]
                if scenario in self.workloads["flat_query_tags"] else ["--query-dnf",
                    self.workloads["query_dnf"]["numeric" if scenario == "numeric" else "mixed"]])
        curve.old.PHYSICAL = np.memmap(self.plan["PhysicalGraph"], dtype="<i4", offset=8, mode="r", shape=(160091, 32))
        self.physical = functools.lru_cache(maxsize=160091)(curve.old.physical_row)

    def invoke(self, name, case, scenario, count, preload=None):
        out = self.root / name
        if (out / "record.json").exists():
            return json.loads((out / "record.json").read_text())
        recover = out.exists()
        if recover:
            require((out / "native.exit.json").exists() and
                    json.loads((out / "native.exit.json").read_text())["returncode"] == 0,
                    "Preserve failed native attempt; no automatic retries")
        else:
            out.mkdir()
        command = ["numactl", "--cpunodebind=2", "--membind=2", str(self.binary),
            "--index", self.plan["Index"], "--queries", self.plan["Queries"],
            "--truth", self.workloads["truth"]["unfilter" if scenario == "alltrue" else scenario]["ids"],
            "--search-sweep-ini", str(self.root / "snapshot/experiment/configs" / f"{case}.ini"),
            "--value-type", "Float", "--topk", "10", "--warmup", str(count),
            "--max-queries", str(count), "--measure-offset", "0", *self.args[scenario]]
        if preload:
            command = ["env", f"LD_PRELOAD={preload}", *command]
        if recover:
            require(json.loads((out / "native.command.json").read_text())["command"] == command,
                    "Cannot recover changed native invocation")
            logs = [out / f"native.{part}.log" for part in ("stdout", "stderr")]
            wall = None
        else:
            start = time.monotonic()
            logs = native.run_native(command, out, "native", .2, True)
            wall = time.monotonic() - start
        text = "\n".join(p.read_text() for p in logs)
        require(text.splitlines().count("NATIVE_INDEX_LOAD calls=1") == 1 and
                text.count("NATIVE_SUPPLIER_OWNER_LOAD count=1") == 1 and
                text.count("NATIVE_DEFAULT_CERT heads=160091 own_all_live=1 immutable=1 vectors=1000000") == 1,
                "Native load/certificate failure")
        for label in ("Vector (160091,128)", "BKT (1,160093)", "RNG (160091,32)",
                      "Vector (4098,128)", "BKT (1,4100)", "RNG (4098,32)"):
            require(text.count("Load " + label + " Finish!") == 1, "Physical native component reloaded")
        require("PhaseTime:" not in text, "Fine profiling enabled")
        results = [json.loads(s) for s in text.splitlines() if s.startswith('{"engine":')]
        require(len(results) == 1, "Not a single-element native array")
        n = results[0]
        require(n["nprobe"] == 24 and n["queries"] == count and not n["failed_queries"] and
                n["native_index_load_calls"] == n["index_load_count"] == n["native_workspace_resets"] == 1 and
                n["sweep_execution"] == "single_load_nprobe_array", "Native protocol mismatch")
        path = out / n["capture_file"]
        v = curve.Experiment.audit(self, path, count, 24, "supplier" if case == "C" else "h1", scenario)
        require(v["recall_at_10"] == n["recall"], "Native recall/capture mismatch")
        costs = {}
        for row in curve.rows(path):
            f = row["filter_cost"]
            require(bool(f["exact_predicate"]) == (case != "A") and
                    bool(f["numeric_predicate"]) == (scenario in ("alltrue", "numeric", "mixed_dnf")),
                    "Predicate was empty/normalized")
            require(bool(f["observer_installed"]) == (case == "C"), "Wrong diagnostic control observer")
            if scenario == "alltrue":
                require(row["native_default_admission"] == 0 and row["native_filtered_admission"] == 1,
                        "Real nonempty predicate used default fastpath")
                require(all(row[k] == 0 for k in ("supplier_calls", "supplier_parent_distances",
                            "native_child_distances", "supplier_members")), "Alltrue predicate invoked supplier")
                require(all(frame["d"] == frame["e"] for frame in row["degree_frames"]), "Alltrue e != d")
            if case == "B":
                require(not row["degree_frames"] and not row["row_eligibility_evaluations"],
                        "Admission-only control performed degree observation")
            for key, value in f.items():
                costs[key] = costs.get(key, 0) + value
        curve.old.compress_capture(path, v["raw_sha256"])
        record = {"directory": name, "case": case, "scenario": scenario, "native": n, "validation": v,
                  "filter_cost": {k: v / count for k, v in costs.items()}, "process_wall_seconds": wall,
                  "operations": [json.loads(s[len("NATIVE_OPERATION "):]) for s in text.splitlines()
                                 if s.startswith("NATIVE_OPERATION ")], "preload": str(preload) if preload else None}
        write(out / "record.json", record)
        print(name, n["recall"], n["mean_latency_ms"], record["filter_cost"], flush=True)
        return record

    def fixtures(self):
        records = [self.invoke(f"fixture_{case}", case, "unfilter" if case == "A" else "alltrue", 8)
                   for case in ("A", "B", "C")]
        self.parity(records[1], records[2])
        for scenario in ("medium_tag", "extreme_tag", "numeric", "mixed_dnf"):
            r = self.invoke(f"fixture_{scenario}", "C", scenario, 8)
            actual = curve.rows(self.root / r["directory"] / r["native"]["capture_file"])
            previous = curve.rows(Path(self.plan["AcceptedResults"]) / f"fixture_{scenario}/queries.jsonl.nprobe_24")
            require(all({k: v for k, v in x.items() if k not in ("filter_cost", "native_h1_hooks_invoked")} == y
                        for x, y in zip(actual, previous)), "Filtered native reference changed")
            records.append(r)
        write(self.root / "fixtures.json", records)

    def parity(self, a, b):
        require(a["validation"]["heads_sha256"] == b["validation"]["heads_sha256"] and
                a["validation"]["results_sha256"] == b["validation"]["results_sha256"] and
                curve.core(a["native"]) == curve.core(b["native"]), "B/C native result/admission/core-work mismatch")
        wa, wb = a["validation"]["work"], b["validation"]["work"]
        ignored = {"row_eligibility_evaluations"}
        require({k: v for k, v in wa.items() if k not in ignored} ==
                {k: v for k, v in wb.items() if k not in ignored}, "B/C native navigation work changed")

    def measure(self):
        require((self.root / "fixtures.json").exists(), "Fixtures required")
        records = []
        for repeat, order in enumerate(self.prereg["ordinary_order"], 1):
            for case in order:
                records.append(self.invoke(f"ordinary_r{repeat}_{case}", case,
                    "unfilter" if case == "A" else "alltrue", 1000))
        self.parity(next(r for r in records if r["case"] == "B"), next(r for r in records if r["case"] == "C"))
        summary = []
        for case in ("A", "B", "C"):
            pair = [r for r in records if r["case"] == case]
            require(pair[0]["validation"] == pair[1]["validation"] and
                    pair[0]["filter_cost"] == pair[1]["filter_cost"] and
                    curve.core(pair[0]["native"]) == curve.core(pair[1]["native"]), "Ordinary repetition mismatch")
            ms = [r["native"]["mean_latency_ms"] for r in pair]
            summary.append({"case": case, "nprobe": 24, "ordinary_ms": statistics.mean(ms), "ordinary_ms_runs": ms,
                "qps": 1000 / statistics.mean(ms), "recall_at_10": pair[0]["native"]["recall"],
                "work": pair[0]["validation"]["work"], "native_ssd_work": curve.core(pair[0]["native"]),
                "filter_cost": pair[0]["filter_cost"], "work_source": "untimed_native_capture",
                "sweep_execution": "single_load_nprobe_array", "nprobe_ini_api": "SearchSweep.NProbe"})
        write(self.root / "runs.json", records)
        write(self.root / "summary.json", summary)

    def instrument(self, sample):
        library = self.tool / ("cpu-sampler.so" if sample else "clock-audit.so")
        command = ["c++", "-shared", "-fPIC", "-O2", "-std=c++17", str(HERE / "ClockAudit.cpp"),
                   "-o", str(library), "-ldl", "-pthread"]
        if sample:
            command.append("-DAUDIT_SAMPLE")
        subprocess.run(command, check=True)
        for case in (("B", "C") if sample else ("C",)):
            name = f"{'sample' if sample else 'clocks'}_{case}"
            r = self.invoke(name, case, "alltrue", 1000 if sample else 8, library)
            path = self.root / name / "clock_audit.tsv"
            clock_rows = [line.split("\t") for line in path.read_text().splitlines()]
            require(sum(int(row[3]) for row in clock_rows) == 8 and
                    all(row[0] == "steady" for row in clock_rows), "Unexpected fine-clock invocation")
            locations = subprocess.check_output(["addr2line", "-Cf", "-e", str(self.binary),
                *[row[2] for row in clock_rows]], text=True)
            require(all("Run(int, char**)" in name for name in locations.splitlines()[::2]),
                    "Clock invoked outside coarse native Run boundaries")
            write(self.root / name / "clock_proof.json", {"actual_calls": 8,
                "fine_query_clocks": 0, "locations": locations.splitlines(), "sampled": sample})
            if sample:
                lines = [line.split("\t") for line in (self.root / name / "cpu_samples.tsv").read_text().splitlines()
                         if line.startswith("pc\t")]
                own = [line for line in lines if Path(line[1]) == self.binary]
                addresses = [line[2] for line in own]
                resolved = subprocess.check_output(["addr2line", "-Cf", "-e", str(self.binary), *addresses],
                                                  text=True).splitlines()
                counts = {}
                for i, line in enumerate(own):
                    key = (resolved[2 * i], resolved[2 * i + 1])
                    counts[key] = counts.get(key, 0) + int(line[3])
                write(self.root / name / "resolved_samples.json",
                    [{"function": k[0], "source": k[1], "samples": v}
                     for k, v in sorted(counts.items(), key=lambda kv: -kv[1])])
                write(self.root / name / "sample_totals.json", {"all_pcs": sum(int(l[3]) for l in lines),
                    "native_executable_pcs": sum(int(l[3]) for l in own), "ordinary_boundary_calls": [6, 7],
                    "ordinary_timing_source": False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("fixtures", "measure", "clocks", "sample"))
    parser.add_argument("--experiment", type=Path, default=HERE / "experiment.ini")
    options = parser.parse_args()
    experiment = Experiment(options.experiment)
    if options.phase == "clocks":
        experiment.instrument(False)
    elif options.phase == "sample":
        experiment.instrument(True)
    else:
        getattr(experiment, options.phase)()
