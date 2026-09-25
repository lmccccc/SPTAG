#!/usr/bin/env python3
"""Isolated bounded H1/H2/H3 shortcut experiment; no SSD-stage surrogate."""
import argparse
import configparser
import json
from pathlib import Path
import shutil
import subprocess

from official_benchmark_config import identity, reject_environment_overrides, require, write_json


def read(path):
    ini = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
    with Path(path).open() as stream:
        ini.read_file(stream)
    return ini


def run(config):
    import numpy as np
    reject_environment_overrides()
    config = config.resolve(strict=True)
    parser = read(config)
    require(parser.sections() == ["Experiment"], "Unexpected experiment sections")
    plan = parser["Experiment"]
    require(set(plan) == {k.lower() for k in (
        "NativeINI", "Binary", "OutputDirectory", "CPUNode", "MemoryNode", "QueryNPY",
        "TruthNPY", "SourceIndex", "HeadNativeIDs", "FrozenRuntime", "ExistingSweep", "ProtectedModule")},
        "Unexpected experiment keys")
    native_ini = (config.parent / plan["NativeINI"]).resolve(strict=True)
    native = read(native_ini)
    inputs = native["Input"]
    count = inputs.getint("QueryCount")
    nq = np.load(plan["QueryNPY"], mmap_mode="r", allow_pickle=False)
    fvecs = np.fromfile(inputs["Queries"], dtype="<i4").reshape(-1,129)
    require(np.all(fvecs[:,0] == 128) and nq.dtype == np.float32 and
            np.array_equal(fvecs[:count,1:].copy().view("<f4"), nq[:count]),
            "Native XVEC and first-query NPY cohorts differ")
    output = Path(plan["OutputDirectory"])
    require(Path(native["Output"]["Directory"]).parent == output, "Native output outside experiment")
    output.mkdir(exist_ok=False)
    snapshot = output / "snapshot"
    snapshot.mkdir()
    benchmarks = Path(__file__).resolve().parent
    for path in [config, native_ini, Path(__file__), benchmarks/"HierarchicalShortcutBench.cpp",
                 benchmarks/"H2SweepBench.cpp", benchmarks/"official_benchmark_config.py"]:
        shutil.copy2(path,snapshot/path.name)
    shutil.copytree(benchmarks/"hierarchical_shortcut_native",snapshot/"hierarchical_shortcut_native",
                    ignore=shutil.ignore_patterns("__pycache__"))
    binary = Path(plan["Binary"])
    shutil.copy2(binary,snapshot/binary.name)
    shutil.copytree(binary.parent.parent/"isolated",snapshot/"isolated")
    paths = {Path(value) for key,value in inputs.items() if key not in {"querycount","dim"}}
    paths.update(Path(plan[key]) for key in ("QueryNPY","TruthNPY","HeadNativeIDs",
                                           "FrozenRuntime","ExistingSweep","ProtectedModule"))
    paths.add(Path(plan["SourceIndex"]))
    files = set()
    for path in paths:
        if path.is_dir():
            files.update(p.resolve() for p in path.rglob("*") if p.is_file())
        else:
            files.add(path.resolve())
    before = [identity(p,True) for p in sorted(files)]
    write_json(output/"provenance.json", {
        "protected_input_sha256": before, "binary": identity(binary,True),
        "native_ini": identity(native_ini,True), "query_cohort_byte_equal": True,
        "scope": "Navigation only; exact-H1 oracle diagnostic; final-data recall and full-query QPS unmeasured.",
    })
    command = ["numactl", f"--cpunodebind={plan.getint('CPUNode')}",
               f"--membind={plan.getint('MemoryNode')}", str(snapshot/binary.name),
               "--config", str(snapshot/native_ini.name)]
    write_json(output/"command.json",command)
    write_json(output/"status.json",{"state":"running"})
    with (output/"native.log").open("w") as log:
        result = subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
    after = [identity(p,True) for p in sorted(files)]
    require(before == after, "An original input changed")
    require(identity(binary,True)["sha256"] == identity(snapshot/binary.name,True)["sha256"],
            "Built executable changed during the experiment")
    write_json(output/"input_verification.json",{"unchanged": True,"files":len(before)})
    require(result.returncode == 0, "Native experiment failed; inspect native.log")
    require((Path(native["Output"]["Directory"])/"COMPLETE").is_file(), "Missing native completion")
    rows = [json.loads(line) for line in (Path(native["Output"]["Directory"])/"summary.jsonl").read_text().splitlines()]
    write_json(output/"summary.json",rows)
    write_json(output/"status.json",{"state":"completed_navigation_only", "originals_unchanged":True})
    for row in rows:
        print(f"{row['case']:16} H1recall={row['exact_h1_recall_mean']:.6f} "
              f"calls={row['distance_calls_mean']:.3f} us={row['ordinary_us_mean']:.3f}")


if __name__ == "__main__":
    argument = argparse.ArgumentParser(description=__doc__)
    argument.add_argument("--config",required=True,type=Path)
    run(argument.parse_args().config)
