"""Build/authenticate an isolated count-aware client; never rebuild its native library."""

import argparse
import difflib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from threeway_native import build_pipeann_client as base
from threeway_native import build_pipeann_page_lifetime as build_helpers
from threeway_native import pipeann_page_lifetime as lifetime


MODE = "native-count-prefix-v1"
AUTHORIZATION = "allow-native-short-results"
BASE_CLIENT_SHA256 = "f3269119b6ee1e90f4c0c285c4e9813d68af1f6577525c85be70db4da0ea56dc"
SOURCE_HASHES = {
    "benchmark.h": "4630e0bc019cb01ab755870ea83d87d42b0d647cedd008b29085f488d6240a97",
    "pipeann_bench.cpp": "975b869a9e87b1e16fc6f719e32564d58ff2bc7603bb4db9e90e659f290a13d8",
}
HELPERS = (
    "build_pipeann_client.py", "build_pipeann_page_lifetime.py", "pipeann_page_lifetime.py",
    "pipeann_page_lifetime.patch", "build_pipeann_short_results.py", "pipeann_short_results.patch",
)


def sources(directory):
    lifetime.require({path.name for path in directory.iterdir()} == set(SOURCE_HASHES)
                     and all(path.is_file() and not path.is_symlink() for path in directory.iterdir()),
                     "Isolated client must contain exactly its two regular source files")
    return {name: base.identity(directory / name) for name in SOURCE_HASHES}


def source_delta(directory):
    original = {name: base.identity(HERE / name) for name in SOURCE_HASHES}
    lifetime.require(all(original[name]["sha256"] == expected for name, expected in SOURCE_HASHES.items()),
                     "Preserved original client source changed")
    patched = sources(directory)
    difference = "".join("".join(difflib.unified_diff(
        (HERE / name).read_text().splitlines(True), (directory / name).read_text().splitlines(True),
        fromfile="a/" + name, tofile="b/" + name)) for name in SOURCE_HASHES)
    lifetime.require(difference == (HERE / "pipeann_short_results.patch").read_text(),
                     "Client changes exceed the explicit native-count accounting patch")
    return original, patched


def client_command(source, directory, binary):
    common, link, provenance = lifetime.native_recipe(source)
    command = [
        *common, f"-ffile-prefix-map={directory}={HERE}",
        '-DTHREEWAY_PIPEANN_SOURCE="' + str(source) + '"',
        '-DTHREEWAY_PIPEANN_LIBRARY_SHA256="' + provenance["libraries"]["pipeann"]["sha256"] + '"',
        str(directory / "pipeann_bench.cpp"), *link, "-o", str(binary),
    ]
    return command, provenance


def validate(binding, source):
    path = Path(binding["proof"]).resolve(strict=True)
    lifetime.require(path.is_file() and not path.is_symlink()
                     and base.identity(path)["sha256"] == binding["proof_sha256"],
                     "Native-count client proof changed")
    proof = json.loads(path.read_text())
    directory = Path(proof["client_source_directory"]).resolve(strict=True)
    source = Path(source).resolve(strict=True)
    expected = {"mode": MODE, "authorization": AUTHORIZATION, "schema_version": 2, "proof": str(path),
                "proof_sha256": base.identity(path)["sha256"], "client_source_directory": str(directory)}
    lifetime.require(binding == expected and proof["mode"] == MODE and proof["authorization"] == AUTHORIZATION
                     and proof["status"] == "complete" and proof["native_core_rebuilt"] is False
                     and proof["production_index_loaded"] is False, "Unapproved native short-result protocol")
    original, patched = source_delta(directory)
    lifetime.require(proof["original_client_sources"] == original and proof["client_sources"] == patched,
                     "Client source inventory changed after compilation")
    original_binary = base.authenticate(proof["original_client"])
    lifetime.require(original_binary["sha256"] == BASE_CLIENT_SHA256,
                     "Unexpected native client accounting baseline")
    original_manifest = base.authenticate(proof["original_client_manifest"])
    decoded = json.loads(Path(original_manifest["path"]).read_text())
    lifetime.require(decoded["binary"] == original_binary
                     and all(decoded["inputs"][name] == entry for name, entry in original.items()),
                     "Original compiled client does not bind the preserved source")
    binary = base.authenticate(proof["binary"])
    command, native = client_command(source, directory, binary["path"])
    execution = proof["execution"]
    lifetime.require(command == execution["argv"] and execution["exit_code"] == 0
                     and execution["cwd"] == str(HERE)
                     and proof["compiler"] == native["compiler"]
                     and proof["library"] == native["libraries"]["pipeann"]
                     and proof["native_correctness"] == native["native_correctness"]
                     and decoded["native_correctness"] == native["native_correctness"],
                     "Native core, compiler flags or client link recipe changed")
    helpers = {name: base.identity(HERE / name) for name in HELPERS}
    lifetime.require(helpers == proof["helpers"], "Compiled client protocol helpers changed")
    protected = [base.identity(path), original_binary, original_manifest, binary,
                 base.authenticate(execution["log"]), *original.values(), *patched.values(), *helpers.values(),
                 base.authenticate(proof["compiler"]), base.authenticate(proof["library"])]
    return {"binding": binding, "proof": proof, "sources": patched, "protected": protected,
            "command": command}


def build(source, original_client, output):
    source, original_client = source.resolve(strict=True), original_client.resolve(strict=True)
    output = output.absolute()
    lifetime.require(not os.path.lexists(output) and output.parent.resolve().is_relative_to(base.WORKSPACE)
                     and not output.is_relative_to(source.parent), "Use a fresh isolated workspace output")
    original_binary = base.identity(original_client)
    lifetime.require(original_binary["sha256"] == BASE_CLIENT_SHA256, "Unexpected approved baseline client")
    original_manifest_path = original_client.with_name(original_client.name + ".build.json")
    original_manifest = json.loads(original_manifest_path.read_text())
    for name, expected in SOURCE_HASHES.items():
        entry = base.authenticate(original_manifest["inputs"][name])
        lifetime.require(entry["sha256"] == expected and entry == base.identity(HERE / name),
                         "Original client compile-time source mismatch")
    output.mkdir(parents=True)
    directory = output / "client-source"
    directory.mkdir()
    logs = output / "provenance"
    logs.mkdir()
    for name in SOURCE_HASHES:
        shutil.copy2(HERE / name, directory / name)
    patch = HERE / "pipeann_short_results.patch"
    build_helpers.execute("patch-check", ["git", "apply", "--check", str(patch)], directory, logs)
    build_helpers.execute("patch-apply", ["git", "apply", str(patch)], directory, logs)
    original, patched = source_delta(directory)
    helpers = {name: base.identity(HERE / name) for name in HELPERS}
    binary = output / "build/pipeannBench"
    binary.parent.mkdir()
    command, provenance = client_command(source, directory, binary)
    execution = build_helpers.execute("resident-short-client", command, HERE, logs)
    verified_command, verified_native = client_command(source, directory, binary)
    lifetime.require((command, provenance) == (verified_command, verified_native)
                     and source_delta(directory) == (original, patched)
                     and helpers == {name: base.identity(HERE / name) for name in HELPERS},
                     "Client/native inputs changed during compilation")
    proof = {
        "schema_version": 1, "status": "complete", "mode": MODE, "authorization": AUTHORIZATION,
        "client_source_directory": str(directory), "original_client": original_binary,
        "original_client_manifest": base.identity(original_manifest_path),
        "original_client_sources": original, "client_sources": patched, "helpers": helpers,
        "compiler": provenance["compiler"], "library": provenance["libraries"]["pipeann"],
        "native_correctness": provenance["native_correctness"], "execution": execution,
        "binary": base.identity(binary), "native_core_rebuilt": False, "production_index_loaded": False,
        "policy": "Use the native API's returned prefix, preserve raw tail bytes, never pad or repair results; "
                  "missing neighbors contribute zero to Recall@K with denominator completed_queries*K",
    }
    proof_path = logs / "native-short-results-proof.json"
    build_helpers.write_json(proof_path, proof)
    binding = {"mode": MODE, "authorization": AUTHORIZATION, "schema_version": 2, "proof": str(proof_path),
               "proof_sha256": base.identity(proof_path)["sha256"], "client_source_directory": str(directory)}
    validate(binding, source)
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
    inputs = {**patched, **helpers}
    provenance.update(schema_version=1, kind="threeway-native-build", engine="PipeANN", status="complete",
                      inputs=inputs, command=command, command_execution=execution, binary=base.identity(binary),
                      resolved_dependencies=dependencies, elf_dynamic=dynamic, native_result_policy=binding,
                      inputs_captured_before_compile=True, inputs_verified_unchanged_after_compile=True,
                      post_compile_verification={"unchanged": True, "inputs": inputs})
    record = logs / "resident-client-build.json"
    build_helpers.write_json(record, provenance)
    published = output / "bin/pipeannBench"
    published.parent.mkdir()
    with binary.open("rb") as reader, published.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
        writer.flush()
        os.fchmod(writer.fileno(), 0o755)
        os.fsync(writer.fileno())
    manifest = base.publish_manifest(provenance, published, record)
    build_helpers.write_json(output / "handoff.json", {
        "schema_version": 1, "status": "client_build_complete", "native_result_policy": binding,
        "native_correctness": provenance["native_correctness"], "binary": base.identity(published),
        "manifest": base.identity(manifest), "acceptance_required": True, "native_core_rebuilt": False,
    })
    return output / "handoff.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-directory", required=True, type=Path)
    parser.add_argument("--base-client", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    arguments = parser.parse_args()
    print(build(arguments.source_directory, arguments.base_client, arguments.output_directory), flush=True)


if __name__ == "__main__":
    main()
