"""Authenticate the explicitly approved DiskANN label-loader-only replacement."""

import hashlib
import os
from pathlib import Path
import re
import shlex
import subprocess

from official_benchmark_config import Config, identity, read_json, require, sha256_file


MODE = "linear-label-delimiters-v1"
PURPOSE = "diskann-loader-only-linear-label-delimiters-v1"
SOURCE = "src/pq_flash_index.cpp"
BASE_REVISION = "78256bbab4685e1774e78d331e081a153be26823"
ORIGINAL_ARTIFACT_SHA256 = {
    "original_library": "90e77b2e175ee9288d51d3a153737a306d4d68d0bdc1f307eb5e6ca68814f8af",
    "original_stock_binary": "9019152db925f4f9496b475c750913a6e71631cf6f626aad90af7eff725f527f",
    "compiler": "2360901d864cf10bfd6296e261cb2c14053552a80377761ab07146ec9ec9a2c0",
}
REPLACEMENTS = (
    (b"fileContent.find(',', lbl_pos)", b'fileContent.find_first_of(",\\n", lbl_pos)'),
    (b"buffer.find(',', lbl_pos)", b'buffer.find_first_of(",\\n", lbl_pos)'),
)


def artifact(entry, expected_path=None):
    require(isinstance(entry, dict) and isinstance(entry.get("path"), str)
            and isinstance(entry.get("sha256"), str)
            and re.fullmatch("[0-9a-f]{64}", entry["sha256"]), "Malformed loader artifact identity")
    path = Path(entry["path"])
    require(path.is_absolute() and path.is_file(), "Loader artifact needs an existing absolute path")
    if expected_path is not None:
        require(path.resolve() == Path(expected_path).resolve(strict=True), "Loader artifact path changed")
    require(sha256_file(path) == entry["sha256"], f"Loader artifact changed: {path}")
    stat = path.stat()
    for key in ("bytes", "size", "size_bytes"):
        if key in entry:
            require(type(entry[key]) is int and entry[key] == stat.st_size, "Loader artifact size changed")
    for key in ("device", "inode", "mtime_ns", "ctime_ns"):
        if key in entry:
            field = {"device": "st_dev", "inode": "st_ino"}.get(key, "st_" + key)
            require(type(entry[key]) is int and entry[key] == getattr(stat, field),
                    f"Loader artifact {key} changed")
    if "resolved" in entry:
        require(entry["resolved"] == str(path.resolve(strict=True)), "Loader artifact target changed")
    return identity(path, True)


def verify_source_delta(original_tree, patched_tree, revision, inventory=None):
    original_tree, patched_tree = Path(original_tree), Path(patched_tree)
    require(original_tree.resolve() != patched_tree.resolve(), "Loader source must be an isolated copy")
    head = subprocess.check_output(
        ["git", "-C", str(original_tree), "rev-parse", "HEAD"], text=True).strip()
    require(head == revision, "Loader source base revision changed")
    require(not subprocess.check_output(
        ["git", "-C", str(original_tree), "status", "--porcelain", "--untracked-files=no"]),
        "Original DiskANN source checkout must remain unchanged")
    tree = subprocess.check_output(
        ["git", "-C", str(original_tree), "ls-tree", "-rz", "--full-tree", revision])
    names = set()
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        metadata, relative = entry.decode().split("\t", 1)
        mode, kind, _ = metadata.split()
        require(not Path(relative).is_absolute() and ".." not in Path(relative).parts,
                "Invalid native source inventory path")
        if kind == "commit":
            for directory in (original_tree, patched_tree):
                path = directory / relative
                require(not path.exists() or (path.is_dir() and not any(path.iterdir())),
                        "Loader repair cannot introduce populated native submodules")
            continue
        require(kind == "blob" and mode in ("100644", "100755"),
                "Unsupported native source inventory entry")
        names.add(relative)
    require(SOURCE in names and {
        str(path.relative_to(patched_tree)) for path in patched_tree.rglob("*")
        if path.is_file() or path.is_symlink()
    } == names, "Loader patch changes the native source/header inventory")
    indexed = None
    if inventory is not None:
        require(isinstance(inventory, list), "Missing loader source inventory")
        indexed = {entry["relative_path"]: entry for entry in inventory}
        require(len(indexed) == len(inventory) and set(indexed) == names,
                "Loader proof source inventory is incomplete")
    original = (original_tree / SOURCE).read_bytes()
    expected = original
    for before, after in REPLACEMENTS:
        require(original.count(before) == 1, "Original delimiter call is missing or ambiguous")
        expected = expected.replace(before, after, 1)
    require((patched_tree / SOURCE).read_bytes() == expected,
            "Native library differs from the two approved delimiter replacements")
    protected = []
    for name in sorted(names):
        paths = [directory / name for directory in (original_tree, patched_tree)]
        require(all(path.is_file() and not path.is_symlink() for path in paths),
                "Native source inventory contains nonregular files")
        records = ([artifact(indexed[name][variant], path)
                    for variant, path in zip(("original", "patched"), paths)]
                   if indexed is not None else [identity(path, True) for path in paths])
        require(name == SOURCE or records[0]["sha256"] == records[1]["sha256"],
                "Loader patch changes other native source or headers")
        protected.extend(records)
    return protected


def verify_archive_delta(original, patched):
    def members(path):
        names = subprocess.check_output(["ar", "t", str(path)], text=True).splitlines()
        require(names and len(names) == len(set(names)), "Duplicate or empty native archive member inventory")
        return names, {
            name: hashlib.sha256(subprocess.check_output(["ar", "p", str(path), name])).hexdigest()
            for name in names
        }

    original_names, old = members(original)
    patched_names, new = members(patched)
    require(original_names == patched_names, "Native archive member inventory/order changed")
    changed = [name for name in original_names if old[name] != new[name]]
    require(changed == ["pq_flash_index.cpp.o"], "Loader replacement changed unrelated native archive members")
    return changed


def verify_compile_link(proof):
    original_tree, patched_tree = (Path(proof[key]) for key in ("original_source_tree", "patched_source_tree"))
    compiler = proof["compiler"]["path"]
    objects = {key: artifact(proof[key]) for key in
               ("reference_object", "patched_object", "stock_search_object", "reference_stock")}
    commands = {entry["name"]: entry for entry in proof["commands"]}
    required = {"reference-object", "patched-object", "reference-stock", "patched-stock",
                "replace-one-member", "archive-index",
                "reference-stock-install-rpath", "patched-stock-install-rpath"}
    require(len(commands) == len(proof["commands"]) and set(commands) == required,
            "Loader compile/link command inventory changed")
    build = Path(objects["patched_object"]["path"]).parent
    require(build.is_absolute() and build.is_dir() and all(
        entry["cwd"] == str(build) and isinstance(entry["argv"], list)
        and entry["argv"] and all(isinstance(arg, str) and arg for arg in entry["argv"])
        for entry in commands.values()), "Loader compile/link working directory or argv changed")
    flags_path = original_tree / "build/src/CMakeFiles/diskann.dir/flags.make"
    flags_record = artifact(proof["flags"]["original_flags_make"], flags_path)
    fields = {}
    for line in flags_path.read_text().splitlines():
        if " = " in line:
            key, value = line.split(" = ", 1)
            require(key not in fields, "Duplicate original compiler flag field")
            fields[key] = shlex.split(value)
    flags = [arg for name in ("CXX_DEFINES", "CXX_INCLUDES", "CXX_FLAGS") for arg in fields[name]]
    prefix_map = f"-ffile-prefix-map={patched_tree}={original_tree}"
    require(proof["flags"]["original"] == flags
            and proof["flags"]["isolated_path_only_addition"] == prefix_map,
            "Loader compiler flags differ from the original build")
    for name, key, source, additions in (
        ("reference-object", "reference_object", original_tree / SOURCE, []),
        ("patched-object", "patched_object", patched_tree / SOURCE, [prefix_map]),
    ):
        output = objects[key]["path"]
        expected = [compiler, *flags, *additions, "-MD", "-MT",
                    "src/CMakeFiles/diskann.dir/pq_flash_index.cpp.o", "-MF", output + ".d",
                    "-o", output, "-c", str(source)]
        require(commands[name]["argv"] == expected,
                "Loader compile argv changes more than source/output paths and the exact __FILE__ map")
        library = proof["original_library" if key == "reference_object" else "patched_library"]["path"]
        member = subprocess.check_output(["ar", "p", library, "pq_flash_index.cpp.o"])
        require(hashlib.sha256(member).hexdigest() == objects[key]["sha256"],
                "Loader archive parser member differs from its compiled object")
    stock_object = original_tree / "build/apps/CMakeFiles/search_disk_index.dir/search_disk_index.cpp.o"
    require(Path(objects["stock_search_object"]["path"]) == stock_object,
            "Loader stock search object is not the preserved original object")
    link_path = stock_object.with_name("link.txt")
    original_link = shlex.split(link_path.read_text())
    require(proof["original_stock_link_command"] == original_link and original_link.count("-o") == 1,
            "Loader stock link flags differ from the original build")
    for name, library_key, output_key in (
        ("reference-stock", "original_library", "reference_stock"),
        ("patched-stock", "patched_library", "patched_stock_binary"),
    ):
        expected = [compiler, *original_link[1:]]
        substitutions = {
            "CMakeFiles/search_disk_index.dir/search_disk_index.cpp.o": str(stock_object),
            "../src/libdiskann.a": proof[library_key]["path"],
            "../deps/usr/lib/x86_64-linux-gnu/libboost_program_options.so":
                os.path.abspath(original_tree / "build/deps/usr/lib/x86_64-linux-gnu/libboost_program_options.so"),
        }
        require(all(expected.count(key) == 1 for key in substitutions),
                "Original stock link object/library inventory changed")
        expected = [substitutions.get(arg, arg) for arg in expected]
        expected[expected.index("-o") + 1] = proof[output_key]["path"]
        require(commands[name]["argv"] == expected,
                "Loader stock link argv changes original objects or semantic options")
    require(objects["reference_stock"]["sha256"] == proof["original_stock_binary"]["sha256"],
            "Original stock executable was not reproduced byte-for-byte")
    for key in ("original_stock_binary", "patched_stock_binary"):
        dynamic = subprocess.check_output(["readelf", "-d", proof[key]["path"]], text=True)
        paths = re.findall(r"\((?:RUNPATH|RPATH)\).*\[([^\]]*)\]", dynamic)
        require(paths == [proof["installed_runpath"]], "Loader stock executable changed its runtime search path")
    return [flags_record, identity(link_path, True), *objects.values()]


def validate(path, source_directory, revision, original_stock=None, original_library=None):
    config = Config(path)
    require(config.parser.sections() == ["Loader"]
            and set(config.section("Loader")) == {"mode", "proof", "proofsha256", "stockbinary", "library"},
            "Loader policy accepts provenance only, never data/search overrides")
    values = config.section("Loader")
    require(values["Mode"] == MODE, "Unapproved native loader replacement")
    for key in ("Proof", "StockBinary", "Library"):
        require(Path(values[key]).is_absolute(), "Loader policy paths must be absolute")
    proof_path = config.path_value("Loader", "Proof")
    require(re.fullmatch("[0-9a-f]{64}", values["ProofSHA256"])
            and sha256_file(proof_path) == values["ProofSHA256"], "Native loader proof changed")
    proof = read_json(proof_path)
    require(type(proof.get("schema_version")) is int and proof["schema_version"] == 1
            and proof.get("purpose") == PURPOSE and proof.get("base_revision") == revision == BASE_REVISION,
            "Native loader proof has an unapproved purpose or algorithm revision")
    require(Path(proof["original_source_tree"]).resolve() == Path(source_directory).resolve(strict=True),
            "Loader proof belongs to a different original native source")
    protected = [identity(config.path, True), identity(proof_path, True)]
    protected += verify_source_delta(
        proof["original_source_tree"], proof["patched_source_tree"], revision, proof["source_inventory"])
    for key, expected in (
        ("original_library", original_library), ("patched_library", config.path_value("Loader", "Library")),
        ("original_stock_binary", original_stock), ("patched_stock_binary", config.path_value("Loader", "StockBinary")),
    ):
        protected.append(artifact(proof[key], expected))
    require(proof["original_library"]["path"] != proof["patched_library"]["path"]
            and proof["original_stock_binary"]["path"] != proof["patched_stock_binary"]["path"],
            "Loader repair must preserve original native artifacts")
    verify_archive_delta(proof["original_library"]["path"], proof["patched_library"]["path"])
    protected.append(artifact(proof["compiler"]))
    require(all(proof[key]["sha256"] == expected for key, expected in ORIGINAL_ARTIFACT_SHA256.items()),
            "Loader proof does not preserve the accepted original library, stock binary and compiler")
    protected.extend(verify_compile_link(proof))
    binding = {
        "mode": MODE, "path": str(config.path), "sha256": sha256_file(config.path),
        "proof": str(proof_path), "proof_sha256": values["ProofSHA256"],
        "library": str(config.path_value("Loader", "Library")),
        "stock_binary": str(config.path_value("Loader", "StockBinary")),
    }
    return {"binding": binding, "proof": proof, "identities": protected}
