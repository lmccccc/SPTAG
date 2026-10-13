#!/usr/bin/env python3
"""Bounded real-native ordinary/control/timing parity and scenario-warmup fixture."""
import argparse
import configparser
import json
from pathlib import Path
import struct
import subprocess
import sys

import numpy as np

from NativeBenchClientTest import identities, read_ini, require
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Tools/benchmarks/results"))
from r90_timing import STAGES, stage_summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("ordinary", "timing", "fixtures", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir()
    source = args.fixtures.resolve() / "uint8/native.ini"
    base = read_ini(source)
    index = Path(base["Benchmark"]["Index"])
    with (index / "tenant_0/HeadIndex/vectors.bin").open("rb") as stream:
        rows, dimension = struct.unpack("<ii", stream.read(8))
    require(0 < rows <= 200000 and dimension == 128, "Only small existing fixtures are allowed")
    before = identities(index)
    observations = {}
    for client in ("ordinary", "timing"):
        batch = configparser.ConfigParser(interpolation=None)
        batch.optionxform = str
        cases = []
        for scenario in ("empty", "dnf"):
            for repeat in (1, 2, 3):
                modes = (False,) if client == "ordinary" else ((False, True) if repeat % 2 else (True, False))
                for enabled in modes:
                    config = read_ini(source)
                    config["Benchmark"].update(MaxQueries="32", Warmup="32", Predicate=scenario,
                        PredicateFile="" if scenario == "empty" else str(args.fixtures.resolve() / "mixed.npy"),
                        StepTiming=str(enabled).lower())
                    config["SearchSweep"]["NProbe"] = "16"
                    path = output / f"{client}-{scenario}-{repeat}-{enabled}.ini"
                    with path.open("x") as stream:
                        config.write(stream, space_around_delimiters=False)
                    case_id = "Case" + str(len(cases) + 1)
                    directory = output / f"{client}-{scenario}-{repeat}-{enabled}"
                    batch[case_id] = dict(Config=str(path), OutputDirectory=str(directory))
                    cases.append((scenario, repeat, enabled, directory))
        batch["Batch"] = dict(CaseCount=str(len(cases)), WarmupPolicy="once_per_predicate")
        manifest = output / f"{client}-batch.ini"
        with manifest.open("x") as stream:
            batch.write(stream, space_around_delimiters=False)
        log = output / f"{client}.log"
        with log.open("x") as stream:
            subprocess.run([str(getattr(args, client).resolve()), str(manifest)], cwd=output,
                           stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=120)
        events = [json.loads(line) for line in log.read_text().splitlines() if line.startswith("{")]
        require(events[-1]["event"] == "batch_end" and events[-1]["completed_warmup_queries"] == 64,
                "Each predicate must warm exactly once, not each repetition")
        require(sum(event["event"] == "scenario_warmup_end" for event in events) == 2, "Wrong warmup count")
        points = [event for event in events if event["event"] == "point"]
        require(len(points) == len(cases), "Incomplete native cases")
        for (scenario, repeat, enabled, directory), point in zip(cases, points):
            require(point["diagnostic"] == (client == "timing") and point["step_timing"] == enabled,
                    "Clock-off control or timing leaked into ordinary performance")
            require(point["measured_queries"] == point["replay_queries"] == 32 and point["warmup_queries"] == 0,
                    "Incorrect query windows")
            folder = directory / "nprobe_16"
            payloads = {name: (folder / name).read_bytes() for name in ("ids.i32", "dist.f32", "work.u64")}
            if scenario in observations:
                require(payloads == observations[scenario], "Clocks or batch history changed IDs/distances/work")
            observations[scenario] = payloads
            if enabled:
                steps = np.fromfile(folder / "steps.ns.u64", dtype="<u8").reshape(32, len(STAGES))
                latency = np.fromfile(folder / "latency_us.f64", dtype="<f8")
                stage_summary(steps, latency)
                require(steps[:, 2].sum() > 0 and steps[:, 7].sum() > 0, "Native H1/libaio hooks did not run")
            else:
                require(not (folder / "steps.ns.u64").exists(), "Unexpected clock-off capture")
    require(before == identities(index), "Immutable fixture index changed")
    (output / "result.json").write_text(json.dumps(dict(passed=True, cases=18, query_window=32,
        exact_parity=True, exclusive_accounting=True, index_unchanged=True)) + "\n")
    print("PASS: 18 real-native cases, scenario-once warmup, exact parity and exclusive accounting")


if __name__ == "__main__":
    main()
