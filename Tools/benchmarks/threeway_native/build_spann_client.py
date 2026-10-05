#!/usr/bin/env python3
"""Build only the SPANN three-way client, reusing the immutable measured core.

    python3 Tools/benchmarks/threeway_native/build_spann_client.py \
        --output-directory ../datasets/sift1b/toolchains/threeway_native_20260929/spann-build \
        --publish-binary ../datasets/sift1b/toolchains/threeway_native_20260929/bin/spannBench

One compiler process; no core rebuild, generated replacement INI, or installs.
Both build directory and published binary must be fresh. compile_spann.json,
dependencies.json, spann_bench.d and runtime_dependencies.txt retain provenance.
The installed binary also receives an exclusive <binary>.build.json sidecar
(schema_version=1) containing compiler/argv, original source dependency hashes,
frozen object/archive identities, runtime libraries and the installed hash.
Adapter, shared-header and build-entry identities are captured before GCC and
verified unchanged afterward; a source change aborts publication.
Shared libraries use the system loader's standard paths; their exact resolved
paths and hashes are recorded rather than silently depending on a shell RPATH.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess


HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[3]
FROZEN = WORKSPACE / "datasets/sift1m_zipf200_sparse193_numeric/toolchains/main_posting_controlled_ascent_20260924"
MEASURED_SHA256 = "d8b03189cb07eb42838699ffd5c2ccd9741e93625f526a11318e31f4c9434843"
LIBRARIES = ("libSPTAGLibStatic.a", "libDistanceUtils.a", "libRaBitQ2Lib.a", "libzstd.a")
WRAPPER = Path("AnnService/CMakeFiles/nativeBench.dir/__/Wrappers/src/CoreInterface.cpp.o")
RECIPE = Path("AnnService/CMakeFiles/nativeBench.dir/link.txt")
SOURCE_KEYS = ("adapter_source", "benchmark_header", "build_entry")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path) -> dict:
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha256(path)}


def verify_source_snapshots(before: dict, after: dict) -> None:
    if set(before) != set(SOURCE_KEYS) or set(after) != set(SOURCE_KEYS):
        raise ValueError("Missing pre/post-compiler source identities")
    changed = [name for name in SOURCE_KEYS if before[name] != after[name]]
    if changed:
        raise ValueError("Source changed during compilation: " + ", ".join(changed))


def frozen_inputs() -> list[Path]:
    core = FROZEN / "normal"
    paths = [core / "bin/nativeBench", core / WRAPPER, *(core / "bin" / name for name in LIBRARIES)]
    for path in paths:
        if not path.is_file():
            raise ValueError(f"Missing measured immutable native artifact: {path}")
    if sha256(paths[0]) != MEASURED_SHA256:
        raise ValueError("Measured nativeBench identity differs from the controlled-ascent reference")
    cache = (core / "CMakeCache.txt").read_text()
    if "SPTAG_QUERY_WORK_DIAGNOSTICS:BOOL=OFF\n" not in cache:
        raise ValueError("The ordinary, non-diagnostic frozen core ABI is required")
    return [
        *paths, core / RECIPE, core / "CMakeCache.txt",
        core / "AnnService/CMakeFiles/nativeBench.dir/flags.make",
        core / "AnnService/CMakeFiles/nativeBench.dir/build.make",
    ]


def compile_command(source: Path, binary: Path, compiler: str, depfile: Path) -> list[str]:
    core, headers = FROZEN / "normal", FROZEN / "source"
    # Preserve the original link recipe's libraries without recompiling any
    # native object. Standard system library paths need no private RPATH.
    recipe = shlex.split((core / RECIPE).read_text())
    libraries = [
        str((core / "AnnService" / item).resolve()) if not Path(item).is_absolute() else item
        for item in recipe
        if item.endswith(".a") or ".so" in Path(item).name
    ]
    return [
        compiler, "-std=c++17", "-O3", "-DNDEBUG", "-DNUMA", "-DTBB",
        "-fopenmp", "-fno-omit-frame-pointer", "-Wall", "-Wextra", "-Wno-reorder",
        "-Wno-sign-compare", "-Wno-unused-parameter", "-Wno-delete-non-virtual-dtor",
        "-I" + str(headers / "AnnService"), "-I" + str(headers / "Wrappers"),
        '-DTHREEWAY_SPANN_CORE="' + str(core) + '"',
        "-MMD", "-MF", str(depfile), str(source), str(core / WRAPPER),
        *libraries, "-lm", "-lrt", "-o", str(binary),
    ]


def run_compile(command: list[str], directory: Path) -> None:
    print(shlex.join(command), flush=True)
    # GCC also uses temporary storage internally; keep that storage local.
    subprocess.run(command, check=True, cwd=directory, env=dict(os.environ, TMPDIR=str(directory)))


def runtime_dependencies(binary: Path) -> tuple[str, list[dict]]:
    output = subprocess.check_output(["ldd", str(binary)], text=True, env=dict(os.environ, LC_ALL="C"))
    if "not found" in output:
        raise ValueError("Unresolved runtime libraries:\n" + output)
    paths = set()
    for line in output.splitlines():
        fields = line.split()
        if "=>" in fields:
            candidate = fields[fields.index("=>") + 1]
        elif fields and fields[0].startswith("/"):
            candidate = fields[0]
        else:
            continue
        if candidate.startswith("/"):
            paths.add(Path(candidate))
    return output, [identity(path) for path in sorted(paths)]


def write_build_sidecar(binary: Path, manifest_path: Path) -> Path:
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("status") != "complete" or manifest.get("engine") != "SPTAG_adaptive":
        raise ValueError("Only a completed SPANN build can receive a provenance sidecar")
    if manifest.get("source_hashes_verified_unchanged") is not True:
        raise ValueError("Cannot retroactively assert pre/post-compiler source verification")
    before = manifest["source_snapshots"]["before_compile"]
    verify_source_snapshots(before, manifest["source_snapshots"]["after_compile"])
    verify_source_snapshots(before, {name: manifest[name] for name in SOURCE_KEYS})
    expected = manifest.get("published_binary", manifest["binary"])
    installed = identity(binary)
    if installed != expected:
        raise ValueError("Installed binary differs from its recorded build identity")
    for artifact in manifest["frozen_artifacts"]:
        if identity(Path(artifact["path"])) != artifact:
            raise ValueError(f"Frozen native artifact changed: {artifact['path']}")
    dependencies_path = manifest_path.parent / "dependencies.json"
    dependencies = json.loads(dependencies_path.read_text())
    sources = {Path(item["path"]).resolve(): item for item in dependencies}
    for name in ("adapter_source", "benchmark_header"):
        artifact = before[name]
        if sources.get(Path(artifact["path"])) != artifact:
            raise ValueError(f"Dependency manifest differs from pre-compiler {name}")
    sidecar = {
        **manifest, "schema_version": 1, "binary": installed,
        "compiled_binary": manifest["binary"],
        "source_dependencies": dependencies, "build_manifest": identity(manifest_path),
        "dependency_manifest": identity(dependencies_path),
    }
    path = binary.with_name(binary.name + ".build.json")
    with path.open("x") as stream:
        json.dump(sidecar, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, default=HERE / "spann-build")
    parser.add_argument("--publish-binary", type=Path)
    parser.add_argument("--compiler", default="/usr/bin/c++")
    args = parser.parse_args()
    output = args.output_directory.absolute()
    published = args.publish_binary.absolute() if args.publish_binary else None
    published_sidecar = published.with_name(published.name + ".build.json") if published else None
    for path in (output, published, published_sidecar):
        if path is None:
            continue
        if not path.parent.resolve().is_relative_to(WORKSPACE):
            parser.error("Build and publication must remain within the current project workspace")
        if os.path.lexists(path):
            parser.error(f"Refusing to overwrite existing build/publication artifact: {path}")
        if path.resolve().is_relative_to(FROZEN):
            parser.error("Frozen measured toolchains are immutable")
    try:
        artifacts = frozen_inputs()
    except ValueError as error:
        parser.error(str(error))
    compiler = shutil.which(args.compiler)
    if compiler is None:
        parser.error(f"Compiler not found: {args.compiler}")
    output.mkdir(parents=True, exist_ok=False)
    binary, depfile = output / "spann_bench", output / "spann_bench.d"
    command = compile_command(HERE / "spann_bench.cpp", binary, compiler, depfile)
    source_paths = {
        "adapter_source": HERE / "spann_bench.cpp",
        "benchmark_header": HERE / "benchmark.h",
        "build_entry": Path(__file__),
    }
    source_before = {name: identity(path) for name, path in source_paths.items()}
    manifest = {
        **source_before,
        "engine": "SPTAG_adaptive", "measured_nativeBench_sha256": MEASURED_SHA256,
        "compiler": identity(Path(compiler)),
        "compiler_version": subprocess.check_output([compiler, "--version"], text=True).splitlines()[0],
        "command": command, "cwd": str(output), "compiler_workers": 1,
        "frozen_artifacts": [identity(path) for path in artifacts],
        "profile": identity(HERE.parent / "configs/sift1b_threeway/benchmark.ini"),
        "source_snapshots": {"before_compile": source_before},
        "status": "building",
    }
    manifest_path = output / "compile_spann.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    run_compile(command, output)
    source_after = {name: identity(path) for name, path in source_paths.items()}
    manifest["source_snapshots"]["after_compile"] = source_after
    try:
        verify_source_snapshots(source_before, source_after)
    except ValueError as error:
        manifest.update(status="failed", error=str(error))
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        raise
    dependencies = shlex.split(depfile.read_text().replace("\\\n", " ").split(":", 1)[1])
    dependency_identities = [identity(Path(path)) for path in sorted(set(dependencies))]
    for name in ("adapter_source", "benchmark_header"):
        if source_before[name] not in dependency_identities:
            raise ValueError(f"Dependency identity changed after compilation: {name}")
    (output / "dependencies.json").write_text(json.dumps(dependency_identities, indent=2) + "\n")
    runtime, shared = runtime_dependencies(binary)
    (output / "runtime_dependencies.txt").write_text(runtime)
    if [identity(path) for path in artifacts] != manifest["frozen_artifacts"]:
        raise ValueError("An immutable native artifact changed while the client was being built")
    verify_source_snapshots(source_before, {name: identity(path) for name, path in source_paths.items()})
    manifest.update(status="complete", binary=identity(binary), runtime_libraries=shared,
                    source_hashes_verified_unchanged=True)
    if published:
        published.parent.mkdir(parents=True, exist_ok=True)
        with binary.open("rb") as source, published.open("xb") as destination:
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fchmod(destination.fileno(), 0o755)
            os.fsync(destination.fileno())
        manifest["published_binary"] = identity(published)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    sidecar = write_build_sidecar(published or binary, manifest_path)
    print(json.dumps({"binary": str(published or binary), "sha256": sha256(binary),
                      "provenance": str(manifest_path), "build_sidecar": str(sidecar)}), flush=True)


if __name__ == "__main__":
    main()
