#!/usr/bin/env python3
"""Run immutable-input H2 navigation sweeps from fixed native INIs."""
import argparse
import json
from pathlib import Path
import shutil

from official_benchmark_config import identity, reject_environment_overrides, require, write_json
from run_spann_memory_diagnostics import run_sampled_process
from run_vanilla_spann_build import read_ini


def run(config):
    reject_environment_overrides()
    config = config.resolve(strict=True)
    plan = read_ini(config)["Experiment"]
    configs = [Path(p).resolve(strict=True) for p in plan["Configs"].split(",")]
    settings = [read_ini(p) for p in configs]
    root = Path(plan["OutputDirectory"])
    require(not root.exists(), "Refusing to overwrite sweep outputs")
    binary = Path(plan["Binary"])
    binary_before = identity(binary, True)
    require(binary_before["sha256"] == plan["BinarySHA256"], "Unexpected sweep binary")
    files = {config, binary, Path(plan["PostingFile"]), *configs}
    reference = settings[0]
    for native in settings:
        require(dict(native["Sweep"]) == dict(reference["Sweep"]), "Sweep controls differ across ratios")
        for key in ("HeadVectors", "Queries", "QueryCount", "Dim", "FlatIndexDirectory"):
            require(native["Input"][key] == reference["Input"][key], f"Input {key} differs across ratios")
        require(Path(native["Output"]["Directory"]).parent == root, "Native output not isolated in run")
        for key in ("HeadVectors", "Queries", "HeadIDs"):
            files.add(Path(native["Input"][key]))
        for key in ("HeadIndexDirectory", "FlatIndexDirectory"):
            folder = Path(native["Input"][key])
            require((folder / "indexloader.ini").is_file(), "Missing native graph")
            files.update(p for p in folder.rglob("*") if p.is_file())
    before = [identity(p, True) for p in sorted(files)]
    root.mkdir(parents=True, exist_ok=False)
    snapshot = root / "snapshot"
    snapshot.mkdir()
    for p in [config, *configs, Path(__file__), binary, Path(__file__).with_name("H2SweepBench.cpp")]:
        shutil.copy2(p, snapshot / p.name)
    shutil.copytree(Path(__file__).parent / "h2_sweep_native", snapshot / "h2_sweep_native")
    shutil.copytree(binary.parent.parent / "isolated", snapshot / "isolated")
    write_json(root / "provenance.json", {"inputs": before, "binary": binary_before,
               "scope": "H2 graph + one CSR, navigation only, final H1 result count fixed; no SSD queries"})
    write_json(root / "status.json", {"state": "running"})
    try:
        for config, native in zip(configs, settings):
            output = Path(native["Output"]["Directory"])
            command = ["numactl", f"--cpunodebind={plan.getint('CPUNode')}",
                       f"--membind={plan.getint('MemoryNode')}", str(binary), "--config", str(config)]
            write_json(root / f"{output.name}.command.json", command)
            usage = run_sampled_process(command, root, root / output.name, plan.getfloat("SampleSeconds"),
                                        lambda pid: print(f"{output.name} sweep pid={pid}", flush=True))
            require((output / "COMPLETE").is_file(), "Native sweep did not complete")
            with (output / "summary.jsonl").open() as stream:
                summary = [json.loads(line) for line in stream]
            points = len(native["Sweep"]["Replicas"].split(",")) * len(native["Sweep"]["Beams"].split(","))
            require(len(summary) == points + 1, "Missing native summary rows")
            write_json(root / f"{output.name}.usage.json", usage)
            print(f"{output.name} completed: {points} points + flat reference", flush=True)
        require([identity(p, True) for p in sorted(files)] == before, "Input artifacts changed")
        write_json(root / "status.json", {"state": "completed", "cases": len(configs)})
    except (OSError, ValueError) as error:
        write_json(root / "status.json", {"state": "failed", "error": str(error)})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    run(parser.parse_args().config)
