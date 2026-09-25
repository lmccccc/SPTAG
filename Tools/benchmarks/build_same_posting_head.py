#!/usr/bin/env python3
"""Build a flat graph only; keep ordered head vectors and all SSD data immutable."""

import argparse
from pathlib import Path
import shutil
import struct

from official_benchmark_config import identity, reject_environment_overrides, require, write_json
from run_spann_memory_diagnostics import run_sampled_process
from run_vanilla_spann_build import read_ini


def command_from_ini(plan, config):
    source, index, execution = plan["Input"], plan["Index"], plan["Execution"]
    require(source["VectorType"] == "DEFAULT" and source["ValueType"] == "Float",
            "Require the existing native Float head catalog")
    require(index["IndexAlgoType"] == "BKT" and index["ValueType"] == source["ValueType"],
            "Require a flat native BKT graph")
    return [
        "numactl", f"--cpunodebind={execution.getint('CPUNode')}",
        f"--membind={execution.getint('MemoryNode')}", execution["Binary"],
        "-a", index["IndexAlgoType"],
        "-o", str(Path(execution["OutputDirectory"]) / execution["HeadIndexFolder"]),
        "-i", source["VectorPath"], "-f", source["VectorType"],
        "-v", source["ValueType"], "-d", str(source.getint("Dim")),
        "-t", str(index.getint("NumberOfThreads")), "-c", str(config),
    ]


def run(config):
    reject_environment_overrides()
    config = config.resolve(strict=True)
    plan = read_ini(config)
    command = command_from_ini(plan, config)
    source, execution = plan["Input"], plan["Execution"]
    path = Path(source["VectorPath"])
    with path.open("rb") as stream:
        require(struct.unpack("<ii", stream.read(8)) ==
                (source.getint("Rows"), source.getint("Dim")), "Head catalog header differs")
    require(path.stat().st_size == 8 + 4 * source.getint("Rows") * source.getint("Dim"),
            "Head catalog payload size differs")
    before = identity(path, True)
    binary = identity(Path(execution["Binary"]), True)
    require(binary["sha256"] == execution["BinarySHA256"], "Unexpected native graph builder")
    output = Path(execution["OutputDirectory"])
    output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(config, output / config.name)
    shutil.copy2(Path(__file__), output / Path(__file__).name)
    write_json(output / "provenance.json", {
        "input": before, "binary": binary, "command": command, "config": identity(config, True),
        "scope": "Flat graph on all ordered existing H1 rows; no SPANN index or posting build.",
    })
    write_json(output / "status.json", {"state": "building_head"})
    try:
        usage = run_sampled_process(
            command, output, output / "native", execution.getfloat("SampleSeconds"),
            lambda pid: print(f"Building flat H1 graph; native pid={pid}", flush=True))
        vectors = output / execution["HeadIndexFolder"] / "vectors.bin"
        require(identity(vectors, True)["sha256"] == before["sha256"],
                "Flat BKT build changed H1 vector values or row ordering")
        require(identity(path, True) == before, "Source H1 vector catalog changed")
        require(identity(Path(execution["Binary"]), True) == binary, "Native builder changed")
    except (OSError, ValueError) as error:
        write_json(output / "status.json", {"state": "failed", "error": str(error)})
        raise
    write_json(output / "usage.json", usage)
    write_json(output / "status.json", {
        "state": "completed", "head_vectors_byte_identical": True,
        "index": str(vectors.parent), "rows": source.getint("Rows"),
    })
    print(f"Flat H1 graph saved: {vectors.parent}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    run(parser.parse_args().config)
