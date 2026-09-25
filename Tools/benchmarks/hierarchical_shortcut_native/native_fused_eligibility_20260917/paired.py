"""Explicit bounded before/after phases; never continues a curve stage."""
import argparse
import configparser
import importlib.util
import json
import statistics
from pathlib import Path

from prepare import DATA, HERE, OLD, PARENT
spec = importlib.util.spec_from_file_location("fused_cost_runner", HERE / "run.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
Experiment, curve, require, write = runner.Experiment, runner.curve, runner.require, runner.write


def configure():
    fused = configparser.ConfigParser()
    fused.read(HERE / "experiment.ini")
    fused["Experiment"]["BoundResults"] = str(DATA / "comparisons/h1_native_filter_cost_bound_20260917")
    with (HERE / "experiment.ini").open("w") as out:
        fused.write(out, space_around_delimiters=False)
    reference = configparser.ConfigParser()
    reference.read(HERE / "experiment.ini")
    reference["Experiment"].update(Toolchain=str(PARENT), Binary=str(PARENT / "source/Release/spannaclbench"),
        OutputDirectory=str(DATA / "comparisons/h1_native_fused_eligibility_reference_20260917"))
    with (HERE / "reference_experiment.ini").open("x") as out:
        reference.write(out, space_around_delimiters=False)
    write(HERE / "paired_preregistration.json", {
        "reference": str(PARENT), "fused": fused["Experiment"]["Toolchain"],
        "ordinary_order": [
            [["reference", "A"], ["fused", "A"], ["fused", "B"], ["reference", "B"],
             ["reference", "C"], ["fused", "C"]],
            [["fused", "C"], ["reference", "C"], ["reference", "A"], ["fused", "A"],
             ["fused", "B"], ["reference", "B"]]],
        "nprobe": [24], "queries": 1000, "warmup": 1000, "repeats": 2,
        "no_additional_curve_stages": True, "fine_clocks": False,
        "semantic_parity": "heads, final IDs/distances, own IDs, native navigation and SSD work exact",
        "diagnostic_changes": "qualification counts and omitted budget-incomplete degree frames only"})


def measure():
    experiments = {name: Experiment(HERE / config) for name, config in
                   (("reference", "reference_experiment.ini"), ("fused", "experiment.ini"))}
    require((experiments["fused"].root / "fixtures.json").exists(), "Fused fixtures required")
    plan = json.loads((HERE / "paired_preregistration.json").read_text())
    records = {name: [] for name in experiments}
    for repeat, order in enumerate(plan["ordinary_order"], 1):
        for name, case in order:
            exp = experiments[name]
            records[name].append(exp.invoke(f"ordinary_r{repeat}_{case}", case,
                "unfilter" if case == "A" else "alltrue", 1000))
    for name, exp in experiments.items():
        exp.parity(next(r for r in records[name] if r["case"] == "B"),
                   next(r for r in records[name] if r["case"] == "C"))
        summary = []
        for case in ("A", "B", "C"):
            pair = [r for r in records[name] if r["case"] == case]
            require(pair[0]["validation"] == pair[1]["validation"] and
                    pair[0]["filter_cost"] == pair[1]["filter_cost"] and
                    curve.core(pair[0]["native"]) == curve.core(pair[1]["native"]), "Repetition mismatch")
            ms = [r["native"]["mean_latency_ms"] for r in pair]
            summary.append({"case": case, "scenario": "unfilter" if case == "A" else "real_alltrue_numeric",
                "nprobe": 24, "ordinary_ms": statistics.mean(ms), "ordinary_ms_runs": ms,
                "ordinary_ms_min": min(ms), "ordinary_ms_max": max(ms), "qps": 1000 / statistics.mean(ms),
                "recall_at_10": pair[0]["native"]["recall"], "work": pair[0]["validation"]["work"],
                "native_ssd_work": curve.core(pair[0]["native"]), "filter_cost": pair[0]["filter_cost"],
                "mean_returned": pair[0]["validation"]["mean_returned"],
                "underfill_rate": pair[0]["validation"]["underfill_rate"],
                "sweep_execution": "single_load_nprobe_array", "nprobe_ini_api": "SearchSweep.NProbe",
                "work_source": "untimed_native_capture"})
        write(exp.root / "runs.json", records[name])
        write(exp.root / "summary.json", summary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("configure", "measure"))
    args = parser.parse_args()
    globals()[args.phase]()
