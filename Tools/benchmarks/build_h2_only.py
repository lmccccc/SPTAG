#!/usr/bin/env python3
"""Build only an H2 catalog and graph over immutable, ordered H1 vectors."""
import argparse
from pathlib import Path
import shutil
import struct
import subprocess

from official_benchmark_config import identity, reject_environment_overrides, require, write_json
from run_spann_memory_diagnostics import run_sampled_process
from run_vanilla_spann_build import build_command, read_ini


def validate(plan):
    require(not plan["BuildSSDIndex"].getboolean("isExecute") and
            not plan["BuildSSDIndex"].getboolean("BuildSsdIndex"),
            "H2-only builds must disable all SSD construction")
    require(plan["BuildHead"].getboolean("isExecute"), "Require a native H2 graph")
    require(not plan["Base"].getboolean("DeleteHeadVectors"), "Preserve the H2 catalog")
    require(plan["Base"]["VectorType"] == "DEFAULT" and plan["Base"]["ValueType"] == "Float",
            "Require the existing ordered native Float H1 catalog")
    require(not plan["SelectHead"].getboolean("SaveBKT"), "Do not write beside source vectors")
    if not plan["SelectHead"].getboolean("isExecute"):
        require(plan["Execution"].get("SeedHeadVectors") and plan["Execution"].get("SeedHeadIDs"),
                "A reused selection requires both catalog and ordinal map")


def run(config):
    reject_environment_overrides()
    config = config.resolve(strict=True)
    plan = read_ini(config)
    validate(plan)
    base, execution = plan["Base"], plan["Execution"]
    source = Path(execution["Source"])
    require(subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"],
                                    text=True).strip() == execution["SourceRevision"], "Wrong native source")
    require(not subprocess.check_output(["git", "-C", str(source), "status", "--porcelain",
                                         "--untracked-files=no"], text=True), "Native source is dirty")
    index = Path(base["IndexDirectory"])
    output = index.parent
    stage = Path(plan["BuildSSDIndex"]["TmpDir"])
    export = Path(execution["ExportDirectory"])
    require(index.is_absolute() and stage.parent == output and export.parent == output and
            export != index and export != stage and not output.exists(),
            "Require a new isolated output directory")
    inputs = [Path(base["VectorPath"])]
    if not plan["SelectHead"].getboolean("isExecute"):
        inputs.extend(Path(execution[k]) for k in ("SeedHeadVectors", "SeedHeadIDs"))
    before = [identity(p, True) for p in inputs]
    binary = identity(Path(execution["Binary"]), True)
    require(binary["sha256"] == execution["BinarySHA256"], "Wrong native builder")
    with inputs[0].open("rb") as stream:
        rows, dim = struct.unpack("<ii", stream.read(8))
    require((rows, dim) == (base.getint("VectorSize"), base.getint("Dim")) and
            inputs[0].stat().st_size == 8 + rows * dim * 4, "Invalid H1 native catalog")
    output.mkdir(parents=True, exist_ok=False)
    index.mkdir()
    stage.mkdir()
    for p in (config, Path(__file__)):
        shutil.copy2(p, output / p.name)
    if len(inputs) == 3:
        shutil.copy2(inputs[1], index / "SPTAGHeadVectors.bin")
        shutil.copy2(inputs[2], index / "SPTAGHeadVectorIDs.bin")
    command = build_command(plan, config)
    # Generic SPANN SaveIndex has an empty translation map when SSD is disabled.
    # Keep that final export separate from SelectHead's authoritative ordinal map.
    command[command.index("-o") + 1] = str(export)
    write_json(output / "provenance.json", {
        "inputs": before, "binary": binary, "command": command, "config": identity(config, True),
        "scope": "Native H2 selection/graph only; H1 and disk posting bytes are immutable.",
    })
    write_json(output / "status.json", {"state": "building"})
    try:
        usage = run_sampled_process(command, output, output / "native",
                                    execution.getfloat("SampleSeconds"),
                                    lambda pid: print(f"H2-only native build pid={pid}", flush=True))
        vectors = index / "SPTAGHeadVectors.bin"
        ids = index / "SPTAGHeadVectorIDs.bin"
        graph = index / "HeadIndex"
        with vectors.open("rb") as stream:
            upper, dimensions = struct.unpack("<ii", stream.read(8))
        with ids.open("rb") as stream:
            count, width = struct.unpack("<ii", stream.read(8))
            require(0 < count < rows and width == 1 and ids.stat().st_size == 8 + count * 8,
                    "Invalid H2 ordinal-map header or size")
            ordinals = struct.unpack(f"<{count}Q", stream.read())
        require(0 < upper < rows and dimensions == dim and count == upper and width == 1 and
                len(set(ordinals)) == upper and max(ordinals) < rows, "Invalid H2 catalog/ordinal map")
        require(vectors.stat().st_size == 8 + upper * dim * 4, "Invalid H2 vector size")
        require(identity(vectors, True)["sha256"] == identity(graph / "vectors.bin", True)["sha256"],
                "Native H2 graph changed selected vector order")
        with vectors.open("rb") as selected, inputs[0].open("rb") as original:
            selected.seek(8)
            for ordinal in ordinals:
                original.seek(8 + ordinal * dim * 4)
                require(selected.read(dim * 4) == original.read(dim * 4),
                        "H2 vector is not its mapped H1 row")
        require(not (index / "SPTAGFullList.bin").exists(), "Unexpected SSD posting construction")
        require([identity(p, True) for p in inputs] == before, "Input artifacts changed")
        require(identity(Path(execution["Binary"]), True) == binary, "Builder changed")
        write_json(output / "usage.json", usage)
        write_json(output / "inventory.json", [identity(p, True) for p in sorted(index.rglob("*"))
                                               if p.is_file()])
        write_json(output / "status.json", {"state": "completed", "h1_count": rows,
                   "h2_count": upper, "actual_ratio": upper / rows, "native_exit_code": 0,
                   "no_ssd_build": True, "graph": str(graph)})
    except (OSError, ValueError) as error:
        write_json(output / "status.json", {"state": "failed", "error": str(error)})
        raise
    print(f"H2 catalog and graph ready: {upper} / {rows} rows at {graph}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    run(parser.parse_args().config)
