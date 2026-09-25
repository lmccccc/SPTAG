#!/usr/bin/env python3
"""Build a new vanilla SPANN index from a fixed native INI and existing data."""

import argparse
import configparser
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess

from official_benchmark_config import identity, reject_environment_overrides, require, write_json
from run_spann_memory_diagnostics import run_sampled_process


def read_ini(path):
    parser = configparser.ConfigParser(interpolation=None)
    with path.open() as stream:
        parser.read_file(stream)
    return parser


def build_command(plan, config):
    base, execution = plan["Base"], plan["Execution"]
    require(plan["Index"]["IndexAlgoType"] == "SPANN", "Require the native SPANN builder")
    require(plan["Index"]["ValueType"] == base["ValueType"], "Conflicting native value types")
    require(base["VectorType"] in {"DEFAULT", "XVEC"}, "Require an existing native vector file")
    threads = plan["BuildSSDIndex"].getint("NumberOfThreads")
    require(threads > 0, "Build thread count must be positive")
    require(all(plan[section].getint("NumberOfThreads") == threads
                for section in ("SelectHead", "BuildHead")), "Build thread controls must agree")
    return [
        "numactl", f"--cpunodebind={execution.getint('CPUNode')}",
        f"--membind={execution.getint('MemoryNode')}", execution["Binary"],
        "-a", plan["Index"]["IndexAlgoType"], "-o", base["IndexDirectory"],
        "-v", base["ValueType"], "-d", str(base.getint("Dim")),
        "-f", base["VectorType"], "-t", str(threads), "-c", str(config),
    ]


def run(config):
    reject_environment_overrides()
    config = config.resolve(strict=True)
    plan = read_ini(config)
    command = build_command(plan, config)
    base, execution = plan["Base"], plan["Execution"]
    source = Path(execution["Source"]).resolve(strict=True)
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    require(revision == execution["SourceRevision"], "Unexpected upstream revision")
    require(not subprocess.check_output(
        ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"], text=True),
        "Upstream tracked source must be unmodified")
    index = Path(base["IndexDirectory"])
    stage = Path(plan["BuildSSDIndex"]["TmpDir"])
    output = index.parent
    require(index.is_absolute() and stage.is_absolute() and stage.parent == output,
            "Require isolated absolute index/stage paths in one new run directory")
    require(not output.exists(), f"Refusing to overwrite an existing run: {output}")
    inputs = {key: identity(Path(base[key]), True)
              for key in ("VectorPath", "QueryPath", "TruthPath")}
    binary = identity(Path(execution["Binary"]), True)
    sample_seconds = execution.getfloat("SampleSeconds")
    require(sample_seconds > 0, "Sampling interval must be positive")
    output.mkdir(parents=True, exist_ok=False)
    stage.mkdir()
    snapshot = output / "snapshot"
    snapshot.mkdir()
    shutil.copy2(config, snapshot / config.name)
    shutil.copy2(execution["BuildProfile"], snapshot / "vanilla_build.cmake")
    shutil.copy2(Path(__file__), snapshot / Path(__file__).name)
    provenance = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": revision, "source": str(source), "tracked_source_clean": True,
        "command": command, "cwd": str(output),
        "config": identity(config, True), "binary": binary, "inputs": inputs,
        "build_profile": identity(Path(execution["BuildProfile"]), True),
        "no_input_cli_file": "The builder's -i flag is intentionally omitted; native Base.VectorPath is used.",
        "search_section": "IndexBuilder does not apply SearchSSDIndex; benchmark applies fixed search INIs.",
        "io_mode": "Original upstream STATIC reader uses O_DIRECT; no cache drops or IO patches.",
    }
    write_json(output / "provenance.json", provenance)
    write_json(output / "status.json", {"state": "building"})

    def started(pid):
        write_json(output / "process.json", {"pid": pid, "command": command})
        print(f"Building: {output}; native pid={pid}", flush=True)

    try:
        usage = run_sampled_process(command, output, output / "native", sample_seconds, started)
    except (OSError, ValueError) as error:
        write_json(output / "status.json", {"state": "failed", "error": str(error)})
        raise
    require((index / "indexloader.ini").is_file(), "Native build did not save its loader")
    require((index / "SPTAGFullList.bin").is_file(), "Native build did not save its posting store")
    require(identity(Path(execution["Binary"]), True) == binary, "Builder changed during execution")
    write_json(output / "usage.json", usage)
    write_json(output / "inventory.json", [
        identity(path, path.stat().st_size < 16 * 1024 * 1024)
        for path in sorted(index.rglob("*")) if path.is_file()
    ])
    write_json(output / "status.json", {
        "state": "completed", "completed_at": datetime.now(timezone.utc).isoformat(),
        "index": str(index), "native_exit_code": 0,
    })
    print(f"Built original SPANN: {index}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    run(parser.parse_args().config)
