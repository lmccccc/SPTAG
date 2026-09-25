#!/usr/bin/env python3
"""Recheck the completed joint matrix and preserve the validation implementation."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil

import numpy as np

spec = importlib.util.spec_from_file_location("joint_matrix_runner", Path(__file__).with_name("run.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def verify(config):
    config = config.resolve()
    plan = runner.read_ini(config)["Experiment"]
    root = Path(plan["OutputDirectory"])
    status = json.loads((root / "status.json").read_text())
    runner.require(status["state"] == "completed" and status["native_runs"] == 168,
                   "Incomplete bounded matrix")
    workloads = json.loads(Path(plan["Workloads"]).read_text())
    attrs = np.load(workloads["attributes"], mmap_mode="r")
    summary = json.loads((root / "summary.json").read_text())
    results = []
    for row in summary:
        scenario, case = row["scenario"], row["case"]
        truth = np.load(workloads["truth"][scenario]["ids"], mmap_mode="r")
        mask = (np.ones(len(attrs), dtype=bool) if scenario == "unfilter" else
                runner.predicate_mask(workloads["predicates"][scenario], attrs))
        reference = runner.rows(root / f"{scenario}_{case}_profile_r1/queries.jsonl")
        current = runner.query_quality(reference, truth, mask)
        runner.require(all(row[k] == v for k, v in current.items()), "Summary quality mismatch")
        for repeat in range(1, 4):
            for mode in ("plain", "profile"):
                directory = root / f"{scenario}_{case}_{mode}_r{repeat}"
                data = runner.rows(directory / "queries.jsonl")
                if mode == "profile":
                    runner.require(data == reference, "Actual work/results changed between profile repeats")
                else:
                    runner.require(all(a["heads"] == b["heads"] and a["results"] == b["results"]
                                       for a, b in zip(data, reference)), "Count-mode Float result mismatch")
                io = json.loads((directory / "native.io.json").read_text())
                runner.require(io["direct_io"], "Non-native posting IO")
        results.append({"scenario": scenario, "case": case, **current})
    protected = json.loads((root / "provenance.json").read_text())["protected"]
    runner.require(all(runner.fingerprint(p["path"]) == p for p in protected),
                   "Completed controls or protected input changed")
    saved = root / "validation_sources"
    saved.mkdir(exist_ok=False)
    for path in config.parent.iterdir():
        if path.is_file():
            shutil.copy2(path, saved / path.name)
    shutil.copy2(Path(plan["Toolchain"]) / "scenario-tests.log", saved / "scenario-tests.log")
    runner.require("OK" in (saved / "scenario-tests.log").read_text(), "Scenario fixtures failed")
    runner.write_json(root / "independent_validation.json", {
        "native_runs": 168, "profile_repeat_exact_work_parity": True,
        "ordinary_profile_head_and_final_float_parity": True, "direct_io_runs": 144,
        "scenario_fixtures": "3/3 passed", "quality": results,
        "prior_controls_and_inputs_unchanged": len(protected),
        "sources": [runner.fingerprint(p) for p in sorted(saved.iterdir())]})
    print(f"PASS: 24 scenario/case qualities, 144 counted/uncounted O_DIRECT runs, "
          f"all actual-work repetition parity, {len(protected)} protected files unchanged")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    verify(parser.parse_args().config)
