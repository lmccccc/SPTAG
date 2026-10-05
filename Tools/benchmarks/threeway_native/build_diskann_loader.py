#!/usr/bin/env python3
"""Publish only the approved two-delimiter DiskANN loader repair.

Run --stage initialize on a fresh publication directory, apply the two reviewed
replacements to source/src/pq_flash_index.cpp, then run --stage build. No native
builder, index load, search, production artifact or existing binary is invoked
or overwritten. The reference object/relink are bounded reproducibility checks.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

from build_diskann_client import artifact, runtime_dependencies, sha256, verify_inputs_unchanged


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
TC = ROOT / "datasets/sift1b/toolchains/threeway_native_20260929"
ORIGINAL = ROOT / "DiskANN"
REVISION = "78256bbab4685e1774e78d331e081a153be26823"
MODE = "linear-label-delimiters-v1"
PURPOSE = "diskann-loader-only-linear-label-delimiters-v1"
COMPILER = Path("/usr/bin/x86_64-linux-gnu-g++-11")
COMPILER_SHA256 = "2360901d864cf10bfd6296e261cb2c14053552a80377761ab07146ec9ec9a2c0"
ARCHIVE_SHA256 = "90e77b2e175ee9288d51d3a153737a306d4d68d0bdc1f307eb5e6ca68814f8af"
STOCK_SHA256 = "9019152db925f4f9496b475c750913a6e71631cf6f626aad90af7eff725f527f"
MEMBER = "pq_flash_index.cpp.o"
REPLACEMENTS = {
    "fileContent.find(',', lbl_pos)": 'fileContent.find_first_of(",\\n", lbl_pos)',
    "buffer.find(',', lbl_pos)": 'buffer.find_first_of(",\\n", lbl_pos)',
}


def write_json(path: Path, value) -> None:
    with path.open("x") as out:
        json.dump(value, out, indent=2)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    path.chmod(0o444)


def clean_original() -> None:
    revision = subprocess.check_output(["git", "-C", str(ORIGINAL), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(ORIGINAL), "status", "--porcelain", "--untracked-files=no"], text=True).strip()
    if revision != REVISION or dirty:
        raise RuntimeError("Original DiskANN must remain clean at " + REVISION)
    expected = [(COMPILER, COMPILER_SHA256),
                (ORIGINAL / "build/install/lib/libdiskann.a", ARCHIVE_SHA256),
                (ORIGINAL / "build/install/bin/search_disk_index", STOCK_SHA256)]
    for path, digest in expected:
        if sha256(path) != digest:
            raise RuntimeError("Original compiler/archive/stock identity differs: " + str(path))


def export_tree(repository: Path, destination: Path) -> None:
    archive = subprocess.Popen(["git", "-C", str(repository), "archive", "HEAD"], stdout=subprocess.PIPE)
    try:
        subprocess.run(["tar", "-xf", "-", "-C", str(destination)], stdin=archive.stdout, check=True)
    finally:
        archive.stdout.close()
    if archive.wait() != 0:
        raise RuntimeError("Cannot export pinned source tree")


def tracked_files(repository: Path) -> list[str]:
    return [name for name in subprocess.check_output(
        ["git", "-C", str(repository), "ls-files", "-z"]).decode().split("\0")
            if name and (repository / name).is_file()]


def protected_paths() -> list[Path]:
    paths = set(path for path in (TC / "bin").iterdir() if path.is_file())
    paths.update(path for path in (ORIGINAL / "build/install/bin").iterdir() if path.is_file())
    paths.update(path for path in (ROOT / "SPTAG/Release").iterdir() if path.is_file())
    paths.update(HERE / name for name in (
        "benchmark.h", "diskann_admission.h", "spann_bench.cpp", "pipeann_bench.cpp",
        "build_spann_client.py", "build_pipeann_client.py"))
    paths.update(ORIGINAL / name for name in (
        "src/pq_flash_index.cpp", "build/install/lib/libdiskann.a", "build/src/libdiskann.a",
        "build/src/CMakeFiles/diskann.dir/pq_flash_index.cpp.o",
        "build/src/CMakeFiles/diskann.dir/flags.make",
        "build/src/CMakeFiles/diskann.dir/build.make",
        "build/src/CMakeFiles/diskann.dir/link.txt",
        "build/apps/CMakeFiles/search_disk_index.dir/search_disk_index.cpp.o",
        "build/apps/CMakeFiles/search_disk_index.dir/link.txt",
        "build/apps/search_disk_index"))
    paths.add(ROOT / "datasets/sift1b/filtered_diskann/categorical_r64_l1_lf100_pq32_20260928/config.ini")
    return sorted(paths)


def initialize(publication: Path) -> None:
    publication.mkdir(exist_ok=False)
    for name in ("source", "build", "bin", "lib", "provenance", "tests"):
        (publication / name).mkdir()
    interface = {
        "schema_version": 1, "purpose": PURPOSE, "status": "initializing",
        "publication": str(publication.resolve()),
        "policy": {"section": "Loader", "keys": ["Mode", "Proof", "ProofSHA256", "StockBinary", "Library"]},
        "loader_policy_keys": ["mode", "path", "sha256", "proof", "proof_sha256", "library", "stock_binary"],
        "loader_policy_value_types": "exactly seven strings; all path values are canonical absolute paths",
        "loader_policy_full_identities": "original/patched library and stock identities are authenticated by the SHA-bound proof",
        "file_identity_keys": ["path", "resolved", "bytes", "device", "inode", "mtime_ns", "ctime_ns", "sha256"],
        "build_manifest_library": "corrected archive, with original_library and algorithm_base_revision",
        "benchmark_DiskANN_Library": "unchanged original archive path; LoaderPolicy selects the corrected linked archive",
        "benchmark_DiskANN_SourceDirectory": str(ORIGINAL),
        "benchmark_DiskANN_LoaderPolicy": str((publication / "policy.ini").resolve()),
        "standalone_argv": ["diskannBuildAdmission", "--config", "ORIGINAL_BUILD/config.ini",
                            "--certificate", "FRESH.json", "--loader-policy",
                            str((publication / "policy.ini").resolve())],
        "missing_policy": "unpatched builds preserve legacy schema; policy-bound builds reject before loading",
        "proof_keys": ["schema_version", "purpose", "base_revision", "original_source_tree", "patched_source_tree",
                       "original_pq_flash_index_cpp", "patched_pq_flash_index_cpp", "diff",
                       "original_library", "patched_library", "original_stock_binary", "patched_stock_binary",
                       "compiler", "flags", "archive_members", "changed_archive_members", "source_inventory"],
        "production_loaded": False, "native_production_searches": 0,
    }
    write_json(publication / "provenance/interface.json", interface)
    write_json(publication / "provenance/protected-before.json",
               [artifact(path) for path in protected_paths()])
    export_tree(ORIGINAL, publication / "source")
    if (ORIGINAL / "gperftools/.git").exists():
        export_tree(ORIGINAL / "gperftools", publication / "source/gperftools")
    elif any((ORIGINAL / "gperftools").iterdir()):
        raise RuntimeError("Unexpected nonempty, unregistered native submodule")
    print(json.dumps({"status": "initialized", "publication": str(publication.resolve()),
                      "next": "apply only the two approved delimiter replacements, then --stage build"}), flush=True)


def archive_members(path: Path) -> list[dict]:
    names = subprocess.check_output(["/usr/bin/ar", "t", str(path)], text=True).splitlines()
    if len(names) != len(set(names)):
        raise RuntimeError("Duplicate archive member names are not supported")
    result = []
    for ordinal, name in enumerate(names):
        data = subprocess.check_output(["/usr/bin/ar", "p", str(path), name])
        result.append({"ordinal": ordinal, "name": name, "bytes": len(data),
                       "sha256": hashlib.sha256(data).hexdigest()})
    return result


def runpath(path: Path) -> str:
    text = subprocess.check_output(["readelf", "-d", str(path)], text=True)
    found = re.findall(r"\((?:RUNPATH|RPATH)\).*\[([^\]]*)\]", text)
    if len(found) != 1:
        raise RuntimeError("Expected one explicit native runtime path")
    return found[0]


def build(publication: Path) -> None:
    publication = publication.resolve()
    source = publication / "source"
    build_dir = publication / "build"
    provenance = publication / "provenance"
    original_cpp = ORIGINAL / "src/pq_flash_index.cpp"
    patched_cpp = source / "src/pq_flash_index.cpp"
    original_bytes = original_cpp.read_bytes()
    expected = original_bytes
    for old, new in REPLACEMENTS.items():
        if expected.count(old.encode()) != 1:
            raise RuntimeError("Expected exactly one original scan: " + old)
        expected = expected.replace(old.encode(), new.encode())
    if patched_cpp.read_bytes() != expected:
        raise RuntimeError("Isolated native source must contain exactly the two approved replacements")
    for name in tracked_files(ORIGINAL):
        copied = source / name
        if name != "src/pq_flash_index.cpp" and copied.read_bytes() != (ORIGINAL / name).read_bytes():
            raise RuntimeError("Unapproved native source change: " + name)
    for path in source.rglob("*"):
        if path.is_file() and not path.is_symlink():
            path.chmod(0o444)
    archive = publication / "lib/libdiskann.a"
    stock = publication / "bin/search_disk_index"
    if archive.exists() or stock.exists() or (provenance / "loader-proof.json").exists():
        raise RuntimeError("Refusing to rebuild an existing native publication")
    commands = []
    env = dict(os.environ, TMPDIR=str(build_dir))

    def run(name: str, argv: list[str]) -> None:
        record = {"name": name, "cwd": str(build_dir), "argv": argv}
        commands.append(record)
        write_json(build_dir / (name + ".command.json"), record)
        with (build_dir / (name + ".log")).open("x") as log:
            subprocess.run(argv, check=True, cwd=build_dir, env=env, stdout=log, stderr=subprocess.STDOUT)

    flags_file = ORIGINAL / "build/src/CMakeFiles/diskann.dir/flags.make"
    values = dict(re.findall(r"^(CXX_\w+) = (.*)$", flags_file.read_text(), re.MULTILINE))
    flags = [*shlex.split(values["CXX_DEFINES"]), *shlex.split(values["CXX_INCLUDES"]),
             *shlex.split(values["CXX_FLAGS"])]
    reference_obj = build_dir / "reference/pq_flash_index.cpp.o"
    patched_obj = build_dir / MEMBER
    reference_obj.parent.mkdir(exist_ok=False)

    def compile_command(cpp: Path, obj: Path, extra: list[str]) -> list[str]:
        return [str(COMPILER), *flags, *extra, "-MD", "-MT", "src/CMakeFiles/diskann.dir/" + MEMBER,
                "-MF", str(obj) + ".d", "-o", str(obj), "-c", str(cpp)]

    run("reference-object", compile_command(original_cpp, reference_obj, []))
    prefix_map = "-ffile-prefix-map=" + str(source) + "=" + str(ORIGINAL)
    run("patched-object", compile_command(patched_cpp, patched_obj, [prefix_map]))
    original_archive = ORIGINAL / "build/install/lib/libdiskann.a"
    original_members = archive_members(original_archive)
    reference_exact = sha256(reference_obj) == next(
        item["sha256"] for item in original_members if item["name"] == MEMBER)
    if not reference_exact:
        raise RuntimeError("Exact original compiler/flags did not reproduce the original pq_flash_index member")
    with original_archive.open("rb") as incoming, archive.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing)
    run("replace-one-member", ["/usr/bin/ar", "rD", str(archive), str(patched_obj)])
    run("archive-index", ["/usr/bin/ranlib", "-D", str(archive)])
    patched_members = archive_members(archive)
    if [item["name"] for item in original_members] != [item["name"] for item in patched_members]:
        raise RuntimeError("Archive member order or inventory changed")
    changed = [old["name"] for old, new in zip(original_members, patched_members) if old != new]
    if changed != [MEMBER]:
        raise RuntimeError("More than the approved archive member changed: " + repr(changed))

    link_file = ORIGINAL / "build/apps/CMakeFiles/search_disk_index.dir/link.txt"
    old_link = shlex.split(link_file.read_text())
    search_obj = ORIGINAL / "build/apps/CMakeFiles/search_disk_index.dir/search_disk_index.cpp.o"
    original_stock = ORIGINAL / "build/install/bin/search_disk_index"
    old_runpath = runpath(ORIGINAL / "build/apps/search_disk_index")
    installed_runpath = runpath(original_stock)

    def relink(name: str, library: Path, output: Path) -> None:
        argv = list(old_link)
        argv[0] = str(COMPILER)
        argv[argv.index("CMakeFiles/search_disk_index.dir/search_disk_index.cpp.o")] = str(search_obj)
        argv[argv.index("../src/libdiskann.a")] = str(library)
        boost = "../deps/usr/lib/x86_64-linux-gnu/libboost_program_options.so"
        argv[argv.index(boost)] = str(ORIGINAL / "build/deps/usr/lib/x86_64-linux-gnu/libboost_program_options.so")
        argv[argv.index("-o") + 1] = str(output)
        run(name, argv)
        script = build_dir / (name + "-install-rpath.cmake")
        with script.open("x") as out:
            out.write(f'file(RPATH_CHANGE FILE "{output}" OLD_RPATH "{old_runpath}" '
                      f'NEW_RPATH "{installed_runpath}")\n')
        run(name + "-install-rpath", ["/usr/bin/cmake", "-P", str(script)])

    reference_stock = build_dir / "reference/search_disk_index"
    relink("reference-stock", original_archive, reference_stock)
    if sha256(reference_stock) != STOCK_SHA256:
        raise RuntimeError("Original stock object/relink/install-RPATH did not reproduce the installed binary")
    relink("patched-stock", archive, stock)
    archive.chmod(0o444)
    stock.chmod(0o555)
    inventory = []
    for name in tracked_files(ORIGINAL):
        before, after = artifact(ORIGINAL / name), artifact(source / name)
        inventory.append({"relative_path": name, "original": before, "patched": after,
                          "unchanged": before["sha256"] == after["sha256"]})
    diff = "".join(difflib.unified_diff(original_bytes.decode().splitlines(keepends=True),
                                       expected.decode().splitlines(keepends=True),
                                       fromfile="a/src/pq_flash_index.cpp", tofile="b/src/pq_flash_index.cpp"))
    with (provenance / "loader.patch").open("x") as out:
        out.write(diff)
    (provenance / "loader.patch").chmod(0o444)
    protected = json.loads((provenance / "protected-before.json").read_text())
    verify_inputs_unchanged(protected)
    clean_original()
    proof = {
        "schema_version": 1, "purpose": PURPOSE, "mode": MODE, "base_revision": REVISION,
        "original_source_tree": str(ORIGINAL), "patched_source_tree": str(source),
        "original_pq_flash_index_cpp": artifact(original_cpp), "patched_pq_flash_index_cpp": artifact(patched_cpp),
        "diff": diff, "diff_file": artifact(provenance / "loader.patch"),
        "replacements": [{"old": old, "new": new, "occurrences": 1} for old, new in REPLACEMENTS.items()],
        "original_library": artifact(original_archive), "patched_library": artifact(archive),
        "original_stock_binary": artifact(original_stock), "patched_stock_binary": artifact(stock),
        "compiler": artifact(COMPILER),
        "compiler_version": subprocess.check_output([str(COMPILER), "--version"], text=True).strip(),
        "flags": {"original_flags_make": artifact(flags_file), "original": flags,
                  "isolated_path_only_addition": prefix_map,
                  "explanation": "Identical original optimization/defines/includes; prefix mapping preserves __FILE__."},
        "reference_object": artifact(reference_obj), "reference_object_byte_identical": reference_exact,
        "patched_object": artifact(patched_obj),
        "stock_search_object": artifact(search_obj),
        "stock_object_reused_without_recompile": True,
        "reference_stock": artifact(reference_stock), "reference_stock_byte_identical": True,
        "original_stock_link_command": old_link, "commands": commands,
        "installed_runpath": installed_runpath,
        "archive_members": [{"name": old["name"], "original": old, "patched": new,
                             "unchanged": old == new}
                            for old, new in zip(original_members, patched_members)],
        "changed_archive_members": changed,
        "source_inventory": inventory,
        "changed_native_sources": ["src/pq_flash_index.cpp"],
        "all_other_native_sources_and_headers_unchanged": True,
        "gperftools_gitlink": "fe85bbdf4cb891a67a8e2109c1c22a33aa958c7e",
        "gperftools_original_directory_empty": not any((ORIGINAL / "gperftools").iterdir()),
        "stock_runtime_dependencies": runtime_dependencies(stock),
        "original_stock_runtime_dependencies": runtime_dependencies(original_stock),
        "native_builder": artifact(Path(__file__)),
        "production_loaded": False, "native_production_searches": 0,
        "production_builds": 0, "new_graphs_built": 0,
    }
    write_json(provenance / "loader-proof.json", proof)
    policy = publication / "policy.ini"
    with policy.open("x") as out:
        out.write(f"[Loader]\nMode={MODE}\nProof={provenance / 'loader-proof.json'}\n"
                  f"ProofSHA256={sha256(provenance / 'loader-proof.json')}\n"
                  f"StockBinary={stock}\nLibrary={archive}\n")
        out.flush()
        os.fsync(out.fileno())
    policy.chmod(0o444)
    print(json.dumps({"status": "native-loader-published", "proof": str(provenance / "loader-proof.json"),
                      "policy": str(policy), "changed_members": changed,
                      "reference_object_exact": reference_exact, "reference_stock_exact": True}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publication", type=Path, required=True)
    parser.add_argument("--stage", choices=("initialize", "build"), required=True)
    args = parser.parse_args()
    absolute = args.publication.absolute()
    if absolute.parent != TC or absolute.is_symlink():
        parser.error("Publication must be a fresh direct child of the dedicated threeway toolchain")
    clean_original()
    relative = Path(os.path.relpath(absolute, Path.cwd()))
    (initialize if args.stage == "initialize" else build)(relative)


if __name__ == "__main__":
    main()
