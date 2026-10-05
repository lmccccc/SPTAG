#!/usr/bin/env python3
"""Link one resident PipeANN client; never rebuild or modify the frozen core.

    python3 Tools/benchmarks/threeway_native/build_pipeann_client.py \
        --output-directory Tools/benchmarks/threeway_native/build/pipeann

The production INI supplies SourceDirectory and the optional publication target.
Add --publish to exclusively create its BenchmarkBinary after linking. The fresh
output directory retains compiler argv, core/source hashes, ELF RPATH and resolved
dependency identities. Compilation uses one worker and the original native flags.
Client, compiler, frozen-source and archive identities are verified before and
after compilation, with the successful post-compile checks retained in provenance.
Publication also exclusively creates <BenchmarkBinary>.build.json, schema_version
1 / kind "threeway-native-build" / engine "PipeANN", with compile-time identities.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess


HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[3]
PROFILE = HERE.parent / "configs/sift1b_threeway/benchmark.ini"
TOOLCHAIN_SHA256 = "cef5aeae4f67a3a54a2df5fcf0249895e9ac24621dcda9031e5eb927aa47546f"


def identity(path: Path) -> dict:
    path = path.resolve(strict=True)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def authenticate(entry: dict) -> dict:
    actual = identity(Path(entry["path"]))
    if any(actual[key] != entry[key] for key in ("bytes", "sha256")):
        raise ValueError(f"Frozen native input changed: {entry['path']}")
    return actual


def publish_manifest(provenance: dict, published: Path, build_record: Path) -> Path:
    binary = identity(published)
    if any(binary[key] != provenance["binary"][key] for key in ("bytes", "sha256")):
        raise ValueError("Installed PipeANN binary differs from its recorded build")
    compiler = authenticate(provenance["compiler"])
    for entry in provenance["libraries"].values():
        authenticate(entry)
    for entry in provenance["resolved_dependencies"]:
        authenticate(entry)
    # Preserve recorded compilation inputs, not hashes of subsequently edited sources.
    manifest = dict(provenance)
    manifest.update(
        schema_version=1, kind="threeway-native-build", engine="PipeANN",
        binary=binary, build_binary=provenance["binary"], build_provenance=identity(build_record),
        compiler_version=provenance.get("compiler_version") or subprocess.check_output(
            [compiler["path"], "--version"], text=True).strip(),
    )
    path = Path(str(published) + ".build.json")
    with path.open("x") as stream:
        json.dump(manifest, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return path


def native_recipe(source: Path) -> tuple[list[str], list[str], dict]:
    root = source.parent
    manifest_identity = identity(root / "toolchain.json")
    if manifest_identity["sha256"] != TOOLCHAIN_SHA256:
        raise ValueError("SourceDirectory must name the pinned read-only-descriptor PipeANN toolchain")
    manifest = json.loads((root / "toolchain.json").read_text())
    source_manifest_identity = authenticate(manifest["source_manifest"])
    source_manifest = json.loads(Path(source_manifest_identity["path"]).read_text())
    if Path(source_manifest["directory"]).resolve() != source:
        raise ValueError("Frozen source directory differs from its source manifest")
    for relative, expected in source_manifest["files"].items():
        authenticate(dict(expected, path=str(source / relative)))
    compile_identity = authenticate(manifest["effective"]["compile_commands"])
    entries = json.loads(Path(compile_identity["path"]).read_text())
    entry = next(item for item in entries if Path(item["file"]) == source / "tests/search_disk_index_filtered.cpp")
    original = shlex.split(entry["command"])
    compiler = authenticate(manifest["effective"]["compiler"])
    if Path(original[0]).resolve() != Path(compiler["path"]):
        raise ValueError("Original compiler no longer resolves to its authenticated executable")
    flags = []
    cursor = 1
    while cursor < len(original):
        if original[cursor] in ("-o", "-c"):
            cursor += 2
        else:
            flags.append(original[cursor])
            cursor += 1
    required = {"-DREAD_ONLY_TESTS", "-DNO_MAPPING", "-DUSE_URING", "-DUSE_TCMALLOC"}
    if not required.issubset(flags) or any(flag.startswith("-DUSE_SPDK") for flag in flags):
        raise ValueError("Frozen compile template does not retain READ_ONLY_TESTS/NO_MAPPING/uring/tcmalloc")
    libraries = {name: authenticate(manifest["libraries"][name]) for name in ("pipeann", "uring")}
    for name in ("uring_compat_header", "uring_version_header"):
        authenticate(manifest["libraries"][name])
    resolved = manifest["linkage"]["targets"]["search_disk_index_filtered"]["resolved"]
    dependencies = {entry["path"]: entry for entry in manifest["linkage"]["files"]}
    dynamic = {}
    for name in ("libtcmalloc.so.4", "libopenblas.so.0", "libgomp.so.1"):
        dynamic[name] = authenticate(dependencies[resolved[name]])
    rpaths = list(dict.fromkeys(
        str(Path(entry["path"]).parent) for entry in [*dynamic.values(), libraries["uring"]]
    ))
    link = [
        libraries["pipeann"]["path"], "-Wl,--no-as-needed", dynamic["libtcmalloc.so.4"]["path"],
        "-Wl,--as-needed", libraries["uring"]["path"], dynamic["libopenblas.so.0"]["path"],
        dynamic["libgomp.so.1"]["path"], "-pthread", "-Wl,--enable-new-dtags",
        *["-Wl,-rpath," + path for path in rpaths],
    ]
    return [original[0], "-std=c++17", *flags], link, {
        "toolchain": manifest_identity, "source_manifest": source_manifest_identity,
        "source_revision": source_manifest["revision"],
        "descriptor_adaptation": authenticate(manifest["patch"]),
        "authenticated_source_files": len(source_manifest["files"]),
        "compiler": compiler, "compile_template": entry, "compile_commands": compile_identity,
        "compiler_version": subprocess.check_output([compiler["path"], "--version"], text=True).strip(),
        "libraries": libraries, "original_dynamic_dependencies": dynamic, "rpaths": rpaths,
        "core_recompiled": False, "maximum_compile_workers": 1,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=PROFILE)
    parser.add_argument("--output-directory", type=Path, default=HERE / "build/pipeann")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    profile = args.profile.resolve(strict=True)
    config = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
    with profile.open() as stream:
        config.read_file(stream)
    section = next(config[name] for name in config.sections() if name.lower() == "pipeann")
    source = (profile.parent / section["SourceDirectory"]).resolve(strict=True)
    requested_output = args.output_directory.absolute()
    if os.path.lexists(requested_output):
        parser.error(f"Refusing existing build output: {requested_output}")
    output = requested_output.parent.resolve() / requested_output.name
    if not output.parent.resolve().is_relative_to(WORKSPACE) or output.is_relative_to(source.parent):
        parser.error("Build output must be a fresh workspace directory outside the frozen toolchain")
    published = (profile.parent / section["BenchmarkBinary"]).absolute() if args.publish else None
    if published:
        for path in (published, Path(str(published) + ".build.json")):
            if os.path.lexists(path):
                parser.error(f"Refusing to overwrite an existing published artifact: {path}")
        published = published.parent.resolve() / published.name
        if not published.parent.resolve().is_relative_to(WORKSPACE) or published.is_relative_to(source.parent):
            parser.error("Publication must remain in the workspace, outside the frozen toolchain")
    common, link, provenance = native_recipe(source)
    output.mkdir(parents=True)
    env = dict(os.environ, TMPDIR=str(output), TMP=str(output), TEMP=str(output))
    binary = output / "pipeannBench"
    command = [
        *common, '-DTHREEWAY_PIPEANN_SOURCE="' + str(source) + '"',
        '-DTHREEWAY_PIPEANN_LIBRARY_SHA256="' + provenance["libraries"]["pipeann"]["sha256"] + '"',
        str(HERE / "pipeann_bench.cpp"), *link, "-o", str(binary),
    ]
    inputs = {path.name: identity(path) for path in (HERE / "pipeann_bench.cpp", HERE / "benchmark.h", Path(__file__))}
    provenance.update(schema_version=1, kind="threeway-native-build", engine="PipeANN",
                      profile=identity(profile), inputs=inputs, command=command)
    (output / "build.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(shlex.join(command), flush=True)
    subprocess.run(command, check=True, cwd=HERE, env=env)
    compiled_inputs = {name: identity(HERE / name) for name in inputs}
    if compiled_inputs != inputs:
        raise ValueError("Client sources changed during compilation; use a fresh build after edits finish")
    verified_common, verified_link, verified_core = native_recipe(source)
    if verified_common != common or verified_link != link or any(
            provenance[key] != value for key, value in verified_core.items()):
        raise ValueError("Frozen native compilation inputs changed during compilation")
    provenance["post_compile_verification"] = {
        "unchanged": True, "inputs": compiled_inputs,
        **{key: verified_core[key] for key in (
            "compiler", "compiler_version", "toolchain", "source_manifest", "libraries",
            "original_dynamic_dependencies", "authenticated_source_files")},
    }
    dynamic = subprocess.check_output(["readelf", "-d", str(binary)], text=True, env=env)
    ldd = subprocess.check_output(["ldd", str(binary)], text=True, env=env)
    (output / "readelf.txt").write_text(dynamic)
    (output / "ldd.txt").write_text(ldd)
    if "not found" in ldd or "libtcmalloc.so.4" not in ldd or "libgomp.so.1" not in ldd:
        raise ValueError("Required native dependencies did not resolve")
    linked = []
    for line in ldd.splitlines():
        tokens = line.split()
        if tokens:
            path = Path(tokens[tokens.index("=>") + 1] if "=>" in tokens else tokens[0])
            if path.is_absolute():
                linked.append(identity(path))
    provenance.update(binary=identity(binary), resolved_dependencies=linked, elf_dynamic=dynamic)
    if published:
        published.parent.mkdir(parents=True, exist_ok=True)
        with binary.open("rb") as source_file, published.open("xb") as destination:
            shutil.copyfileobj(source_file, destination)
            destination.flush()
            os.fchmod(destination.fileno(), 0o755)
            os.fsync(destination.fileno())
        provenance["published_binary"] = identity(published)
    (output / "build.json").write_text(json.dumps(provenance, indent=2) + "\n")
    installed_manifest = publish_manifest(provenance, published, output / "build.json") if published else None
    print(json.dumps({"binary": str(published or binary), "provenance": str(output / "build.json"),
                      "installed_manifest": str(installed_manifest) if installed_manifest else None}), flush=True)


if __name__ == "__main__":
    main()
