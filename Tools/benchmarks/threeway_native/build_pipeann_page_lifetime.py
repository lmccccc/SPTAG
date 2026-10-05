"""Build the approved isolated PipeANN correction without changing the original toolchain."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from threeway_native import build_pipeann_client as base
from threeway_native import pipeann_page_lifetime as lifetime


def write_json(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def execute(name, command, cwd, logs):
    path = logs / (name + ".log")
    started = time.time()
    with path.open("x") as stream:
        result = subprocess.run(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT)
    record = {"name": name, "argv": command, "cwd": str(cwd), "exit_code": result.returncode,
              "started_unix": started, "ended_unix": time.time(), "log": base.identity(path)}
    write_json(logs / (name + ".execution.json"), record)
    lifetime.require(result.returncode == 0, f"{name} failed with exit {result.returncode}; see {path}")
    return record


def build_client(source, output):
    common, link, provenance = lifetime.native_recipe(source)
    directory = output / "build/client"
    directory.mkdir()
    binary = directory / "pipeannBench"
    inputs = {name: base.identity(HERE / name) for name in (
        "pipeann_bench.cpp", "benchmark.h", "build_pipeann_client.py",
        "build_pipeann_page_lifetime.py", "pipeann_page_lifetime.py", "pipeann_page_lifetime.patch")}
    command = [
        *common, '-DTHREEWAY_PIPEANN_SOURCE="' + str(source) + '"',
        '-DTHREEWAY_PIPEANN_LIBRARY_SHA256="' + provenance["libraries"]["pipeann"]["sha256"] + '"',
        str(HERE / "pipeann_bench.cpp"), *link, "-o", str(binary),
    ]
    execution = execute("resident-client", command, HERE, output / "provenance")
    lifetime.require({name: base.identity(HERE / name) for name in inputs} == inputs,
                     "Client/provenance sources changed during compilation")
    checked_common, checked_link, checked = lifetime.native_recipe(source)
    lifetime.require((common, link, provenance) == (checked_common, checked_link, checked),
                     "Native build inputs changed during client compilation")
    dynamic = subprocess.check_output(["readelf", "-d", str(binary)], text=True)
    linkage = subprocess.check_output(["ldd", str(binary)], text=True)
    lifetime.require("not found" not in linkage and "libtcmalloc.so.4" in linkage
                     and "libgomp.so.1" in linkage, "Native runtime dependencies did not resolve")
    dependencies = []
    for line in linkage.splitlines():
        tokens = line.split()
        if tokens:
            path = Path(tokens[tokens.index("=>") + 1] if "=>" in tokens else tokens[0])
            if path.is_absolute():
                dependencies.append(base.identity(path))
    provenance.update(schema_version=1, kind="threeway-native-build", engine="PipeANN", status="complete",
                      inputs=inputs, command=command, command_execution=execution,
                      binary=base.identity(binary), resolved_dependencies=dependencies, elf_dynamic=dynamic,
                      inputs_captured_before_compile=True, inputs_verified_unchanged_after_compile=True,
                      post_compile_verification={"unchanged": True, "inputs": inputs,
                                                 "native_correctness": provenance["native_correctness"]})
    build_record = output / "provenance/resident-client-build.json"
    write_json(build_record, provenance)
    published = output / "bin/pipeannBench"
    published.parent.mkdir()
    with binary.open("rb") as reader, published.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
        writer.flush()
        os.fchmod(writer.fileno(), 0o755)
        os.fsync(writer.fileno())
    manifest = base.publish_manifest(provenance, published, build_record)
    return {"binary": base.identity(published), "manifest": base.identity(manifest)}


def build(original, output):
    original = original.resolve(strict=True)
    output = output.absolute()
    lifetime.require(not os.path.lexists(output) and output.parent.resolve().is_relative_to(base.WORKSPACE),
                     "Corrected toolchain requires a fresh workspace output")
    lifetime.require(not output.is_relative_to(original.parent), "Never write inside the preserved toolchain")
    _, _, recipe = base.native_recipe(original)
    lifetime.require(recipe["libraries"]["pipeann"]["sha256"] == lifetime.ORIGINAL_LIBRARY_SHA256,
                     "Unexpected original PipeANN library")
    original_inventory = lifetime.source_files(original)
    sources = {name: base.identity(HERE / name) for name in (
        "build_pipeann_page_lifetime.py", "pipeann_page_lifetime.py", "pipeann_page_lifetime.patch")}
    output.mkdir(parents=True)
    source = output / "source"
    shutil.copytree(original, source, symlinks=True)
    logs = output / "provenance"
    logs.mkdir()
    patch = HERE / "pipeann_page_lifetime.patch"
    execute("patch-check", ["git", "apply", "--check", str(patch)], source, logs)
    execute("patch-apply", ["git", "apply", str(patch)], source, logs)
    inventory = lifetime.source_delta(original, source)
    source_manifest = output / "source-manifest.json"
    write_json(source_manifest, {
        "directory": str(source), "revision": recipe["source_revision"],
        "files": {entry["relative_path"]: {key: entry["patched"][key] for key in ("bytes", "sha256")}
                  for entry in inventory},
    })
    templates = json.loads(Path(recipe["compile_commands"]["path"]).read_text())
    original_library = recipe["libraries"]["pipeann"]
    original_members = lifetime.archive_members(original_library["path"])
    commands, objects = [], {}
    for member in lifetime.MEMBERS:
        template = next(entry for entry in templates if entry["file"] ==
                        str(original / "src/search" / member.removesuffix(".o")))
        objects[member] = {}
        for variant, tree in (("reference", original), ("patched", source)):
            directory = output / "build" / variant
            directory.mkdir(parents=True, exist_ok=True)
            obj = directory / member
            command = lifetime.compile_command(template, original, tree, obj)
            commands.append(execute(variant + "-" + member, command, Path(template["directory"]), logs))
            objects[member][variant] = base.identity(obj)
        lifetime.require(objects[member]["reference"]["sha256"] == dict(original_members)[member],
                         "Unchanged original compiler recipe did not reproduce its archive member")
    library = output / "build/src/libpipeann.a"
    library.parent.mkdir()
    with Path(original_library["path"]).open("rb") as reader, library.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
    ar = Path(shutil.which("ar")).resolve(strict=True)
    commands.append(execute("replace-two-members", [
        str(ar), "r", str(library), *[objects[member]["patched"]["path"] for member in lifetime.MEMBERS]],
        output, logs))
    commands.append(execute("index-archive", [str(ar), "s", str(library)], output, logs))
    patched_members = lifetime.archive_members(library)
    lifetime.require([name for name, _ in original_members] == [name for name, _ in patched_members],
                     "Repaired archive changed member inventory/order")
    lifetime.require(lifetime.source_files(original) == original_inventory
                     and lifetime.source_delta(original, source) == inventory
                     and {name: base.identity(HERE / name) for name in sources} == sources,
                     "Native build inputs changed during compilation")
    base.authenticate(original_library)
    proof = {
        "schema_version": 1, "status": "complete", "mode": lifetime.MODE,
        "authorization": lifetime.AUTHORIZATION, "production_index_loaded": False,
        "original_source_directory": str(original), "patched_source_directory": str(source),
        "original_toolchain": recipe["toolchain"], "original_library": original_library,
        "library": base.identity(library), "compiler": recipe["compiler"], "ar": base.identity(ar),
        "patch": sources["pipeann_page_lifetime.patch"], "builder": sources["build_pipeann_page_lifetime.py"],
        "proof_reader": sources["pipeann_page_lifetime.py"],
        "source_manifest": base.identity(source_manifest), "source_inventory": inventory, "commands": commands,
        "objects": objects,
        "archive_members": [{"name": name, "original_sha256": before, "patched_sha256": after}
                            for (name, before), (_, after) in zip(original_members, patched_members)],
        "scope": "Retain a landed unexpanded page only before its circular IO slot is reused; "
                 "native candidate selection, filtering, budgets, IO requests and termination are unchanged",
    }
    write_json(output / lifetime.PROOF, proof)
    correction = lifetime.validate(source)
    client = build_client(source, output)
    write_json(output / "handoff.json", {
        "schema_version": 1, "status": "native_build_complete", "authorization": lifetime.AUTHORIZATION,
        "correction": correction["binding"], "client": client, "bounded_acceptance_required": True,
        "production_index_loaded": False,
    })
    return output / "handoff.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-directory", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    args = parser.parse_args()
    print(build(args.source_directory, args.output_directory), flush=True)


if __name__ == "__main__":
    main()
