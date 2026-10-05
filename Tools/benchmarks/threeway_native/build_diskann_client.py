#!/usr/bin/env python3
"""Link a client to original DiskANN, or an authenticated loader-only repair.

Default build (one compiler process, no dependency installs or native rebuild):
    python3 Tools/benchmarks/threeway_native/build_diskann_client.py
The complete compiler/linker argv and original revision are recorded in build/.
Use --publish-binary to copy that exact binary to a fresh workspace artifact.
Publication is exclusive by default: an existing target is never overwritten.
--loader-policy supplies only immutable native provenance, never data/search
parameters. Its proof authenticates the isolated two-scan archive replacement.
During client development, --replace-published-sha256 can update only an exact
known previous artifact, retaining that version and its record under .prior.*.
Repeated byte-identical prior executables receive distinct backup generations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess


HERE = Path(__file__).resolve().parent
PINNED_REVISION = "78256bbab4685e1774e78d331e081a153be26823"


def run(command: list[str], env: dict[str, str]) -> None:
    print(shlex.join(command), flush=True)
    subprocess.run(command, check=True, env=env, cwd=HERE)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact(path: Path) -> dict:
    path = path.absolute()
    before = path.stat()
    digest = sha256(path)
    after = path.stat()
    attributes = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, key) != getattr(after, key) for key in attributes):
        raise RuntimeError("Artifact changed while hashing: " + str(path))
    return {"path": str(path), "resolved": str(path.resolve(strict=True)),
            "sha256": digest, "bytes": before.st_size, "device": before.st_dev,
            "inode": before.st_ino, "mtime_ns": before.st_mtime_ns, "ctime_ns": before.st_ctime_ns}


def verify_inputs_unchanged(inputs: list[dict]) -> None:
    for expected in inputs:
        path = Path(expected["path"])
        try:
            observed = artifact(path)
        except OSError as error:
            raise RuntimeError("Build input disappeared or became unreadable: " + str(path)) from error
        if observed != expected:
            raise RuntimeError("Build input changed during compilation; refusing publication: " + str(path))


def runtime_dependencies(binary: Path) -> list[dict]:
    dependencies = []
    for line in subprocess.check_output(["ldd", str(binary)], text=True).splitlines():
        line = line.strip()
        if line.startswith("linux-vdso."):
            continue
        match = re.fullmatch(r"(\S+)\s+=>\s+(/\S+)\s+\(0x[0-9a-f]+\)", line)
        if match:
            name, path = match.groups()
        else:
            match = re.fullmatch(r"(/\S+)\s+\(0x[0-9a-f]+\)", line)
            if not match:
                raise RuntimeError("Unresolved or unrecognized native dependency: " + line)
            path = match.group(1)
            name = Path(path).name
        dependencies.append({"soname": name, **artifact(Path(path))})
    return dependencies


def copy_exclusive(source: Path, target: Path, mode: int = 0o755) -> None:
    with source.open("rb") as input_file, target.open("xb") as output_file:
        shutil.copyfileobj(input_file, output_file)
        output_file.flush()
        os.fchmod(output_file.fileno(), mode)
        os.fsync(output_file.fileno())


def move_exclusive(source: Path, target: Path) -> None:
    os.link(source, target, follow_symlinks=False)
    source.unlink()


def write_record_exclusive(path: Path, record: dict) -> None:
    with path.open("x") as output:
        output.write(json.dumps(record, indent=2) + "\n")
        output.flush()
        os.fsync(output.fileno())


def prior_artifacts(published: Path, previous: str) -> list[tuple[Path, Path]]:
    suffixes = ("", ".build.json", ".link-provenance.json")
    generation = 1
    while True:
        name = published.name + ".prior." + previous
        if generation > 1:
            name += "." + str(generation)
        backup = published.with_name(name)
        if not any(os.path.lexists(backup.with_name(backup.name + suffix)) for suffix in suffixes):
            return [(published.with_name(published.name + suffix), backup.with_name(backup.name + suffix))
                    for suffix in suffixes if os.path.lexists(published.with_name(published.name + suffix))]
        generation += 1


def main(target: str = "diskann_bench", build_entry: Path | None = None,
         extra_libraries: tuple[str, ...] = ()) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-directory", type=Path, default=HERE.parents[3] / "DiskANN")
    parser.add_argument("--output-directory", type=Path, default=HERE / "build")
    parser.add_argument("--compiler", default="g++")
    parser.add_argument("--with-tests", action="store_true")
    parser.add_argument("--publish-binary", type=Path)
    parser.add_argument("--replace-published-sha256")
    parser.add_argument("--loader-policy", type=Path)
    args = parser.parse_args()
    source = args.source_directory.resolve()
    output = args.output_directory.resolve()
    loader_output = (args.loader_policy is not None and args.loader_policy.is_absolute()
                     and output.is_relative_to(args.loader_policy.parent / "build"))
    if (not output.is_relative_to(HERE) or output == HERE) and not loader_output:
        parser.error("Build output must be below threeway_native or the declared loader publication/build")
    published = args.publish_binary.absolute() if args.publish_binary else None
    previous = args.replace_published_sha256
    if previous and not published:
        parser.error("--replace-published-sha256 requires --publish-binary")
    if published:
        if not published.parent.resolve().is_relative_to(HERE.parents[3]):
            parser.error("Published artifacts must remain on this workspace's NVMe filesystem")
        if previous:
            if (len(previous) != 64 or any(char not in "0123456789abcdef" for char in previous)
                    or published.is_symlink() or not published.is_file() or sha256(published) != previous):
                parser.error("Previous published artifact does not match the exact expected SHA-256")
        elif os.path.lexists(published) or os.path.lexists(published.with_name(published.name + ".build.json")):
            parser.error(f"Refusing to overwrite an existing published artifact: {published}")
    archive = source / "build/install/lib/libdiskann.a"
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"], text=True
    ).strip()
    if revision != PINNED_REVISION or dirty:
        parser.error(f"Original DiskANN must remain clean at {PINNED_REVISION}; got {revision}, dirty={bool(dirty)}")
    if not archive.is_file():
        parser.error(f"Missing already-installed original archive: {archive}")
    original_archive = artifact(archive)
    loader_binding, loader_proof, loader_inputs = None, None, []
    if args.loader_policy:
        from loader_policy import authenticate
        loader_binding, loader_proof, loader_inputs = authenticate(
            args.loader_policy, source, original_archive, artifact)
        archive = Path(loader_binding["library"])
    linked_archive = artifact(archive)
    compiler_path = shutil.which(args.compiler)
    if compiler_path is None:
        parser.error(f"Missing selected compiler: {args.compiler}")
    compiler = artifact(Path(compiler_path))
    if loader_proof and compiler != loader_proof["compiler"]:
        parser.error("Loader-only clients must retain the exact original compiler file identity")
    compiler_version = subprocess.check_output([args.compiler, "--version"], text=True).strip()
    source_paths = [HERE / (target + ".cpp"), HERE / "diskann_admission.h",
                    HERE / "diskann_loader_policy.h", HERE / "loader_policy.py",
                    HERE / "benchmark.h", Path(__file__).resolve()]
    if build_entry is not None:
        source_paths.append(build_entry.resolve())
    sources = [artifact(path) for path in source_paths]
    protected_inputs = sources + [original_archive, linked_archive, compiler] + loader_inputs
    deps = source / "build/deps/usr"
    mkl = Path("/opt/intel/oneapi/mkl/2023.2.0/lib/intel64")
    iomp = Path("/opt/intel/oneapi/compiler/2023.2.0/linux/compiler/lib/intel64_lin")
    library_paths = [deps / "lib/x86_64-linux-gnu", mkl, iomp]
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TMPDIR=str(output))
    common = [args.compiler, "-std=c++17", "-O3", "-mavx2", "-mfma", "-fopenmp", "-Wall", "-Wextra"]
    command = [
        *common,
        "-I" + str(source / "include"),
        "-I" + str(deps / "include"),
        "-I/opt/intel/oneapi/mkl/latest/include",
        '-DTHREEWAY_DISKANN_LIBRARY="' + str(archive) + '"',
        '-DTHREEWAY_DISKANN_ORIGINAL_LIBRARY="' + original_archive["path"] + '"',
        '-DTHREEWAY_DISKANN_SOURCE="' + str(source) + '"',
        '-DTHREEWAY_DISKANN_REVISION="' + revision + '"',
        *(['-DTHREEWAY_DISKANN_LOADER_POLICY_JSON=' +
           json.dumps(json.dumps(loader_binding, separators=(",", ":")))] if loader_binding else []),
        str(HERE / (target + ".cpp")),
        str(archive),
        *["-L" + str(path) for path in library_paths],
        "-Wl,--enable-new-dtags",
        *["-Wl,-rpath," + str(path) for path in library_paths],
        "-ltcmalloc",
        "-laio",
        "-lmkl_intel_ilp64",
        "-lmkl_intel_thread",
        "-lmkl_core",
        "-liomp5",
        "-lpthread",
        "-lm",
        "-ldl",
        *dict.fromkeys((*extra_libraries, "-lcrypto")),
        "-o", str(output / target),
    ]
    compile_record = "compile_diskann.json" if target == "diskann_bench" else "compile_" + target + ".json"
    (output / compile_record).write_text(
        json.dumps({"source_revision": revision, "library": str(archive), "command": command}, indent=2) + "\n"
    )
    run(command, env)
    if args.with_tests:
        for name in ("mock_bench", "header_unit_test", "profile_options_test"):
            test_command = [*common, str(HERE / "tests" / (name + ".cpp")), "-o", str(output / name)]
            (output / ("compile_" + name + ".json")).write_text(json.dumps(test_command, indent=2) + "\n")
            run(test_command, env)
    binary = output / target
    verify_inputs_unchanged(protected_inputs)
    final_revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    final_dirty = subprocess.check_output(
        ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"], text=True
    ).strip()
    if final_revision != revision or final_dirty:
        raise RuntimeError("Original DiskANN source revision/cleanliness changed during compilation")
    record = {
        "schema_version": 1,
        "engine": "Filtered_DiskANN",
        "status": "complete",
        "source_directory": str(source),
        "source_revision": revision,
        "source_clean": True,
        "library": linked_archive,
        "compiler": compiler,
        "compiler_version": compiler_version,
        "command": command,
        "sources": sources,
        "inputs_captured_before_compile": True,
        "inputs_verified_unchanged_after_compile": True,
        "binary": {"path": str(published or binary), "sha256": sha256(binary), "size": binary.stat().st_size},
        "runtime_dependencies": runtime_dependencies(binary),
    }
    if loader_binding:
        record.update({"loader_policy": loader_binding, "original_library": original_archive,
                       "algorithm_base_revision": revision,
                       "native_source_variant": "linear-label-delimiters-v1",
                       "patched_source_directory": loader_proof["patched_source_tree"]})
    verify_inputs_unchanged(protected_inputs)
    (output / (target + ".build.json")).write_text(json.dumps(record, indent=2) + "\n")
    if published:
        published.parent.mkdir(parents=True, exist_ok=True)
        published_record = published.with_name(published.name + ".build.json")
        if previous:
            if published.is_symlink() or sha256(published) != previous:
                raise RuntimeError("Published artifact changed while the replacement was being compiled")
            archived = prior_artifacts(published, previous)
            for old, saved in archived:
                if old.is_symlink() or not old.is_file() or os.path.lexists(saved):
                    raise RuntimeError("Refusing a nonregular source or existing prior artifact: " + str(saved))
            candidate = published.with_name(published.name + ".candidate." + record["binary"]["sha256"])
            candidate_record = candidate.with_name(candidate.name + ".build.json")
            copy_exclusive(binary, candidate)
            write_record_exclusive(candidate_record, record)
            for old, saved in archived:
                move_exclusive(old, saved)
            move_exclusive(candidate, published)
            move_exclusive(candidate_record, published_record)
        else:
            copy_exclusive(binary, published)
            write_record_exclusive(published_record, record)
    print(json.dumps({"binary": str(published or binary), "source_revision": revision}), flush=True)


if __name__ == "__main__":
    main()
