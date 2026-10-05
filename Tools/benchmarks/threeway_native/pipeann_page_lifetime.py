"""Authenticate the authorized isolated PipeANN page-lifetime correction."""

import difflib
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess

from threeway_native import build_pipeann_client as base


HERE = Path(__file__).resolve().parent
MODE = "retain-unexpanded-pages-before-ring-reuse-v1"
AUTHORIZATION = "isolate-pipeann-correctness-fix"
HEADER = "src/search/pipe_search_common.h"
MEMBERS = ("pipe_search.cpp.o", "spec_filter_search.cpp.o")
ORIGINAL_LIBRARY_SHA256 = "1c6f6cb74b704b68d09030a2b02acb63437245c1e9ccc267e342c028d26613c3"
PROOF = "provenance/page-lifetime-proof.json"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def source_files(directory):
    paths = sorted(directory.rglob("*"))
    require(not any(path.is_symlink() for path in paths), "Native source inventory must not contain symlinks")
    return {str(path.relative_to(directory)): base.identity(path) for path in paths if path.is_file()}


def source_delta(original, patched):
    before, after = source_files(original), source_files(patched)
    require(set(before) == set(after), "Corrected native source inventory differs")
    changed = [name for name in before if before[name]["sha256"] != after[name]["sha256"]]
    require(changed == [HEADER], "Only the native pipelined page-lifetime header may change")
    difference = "".join(difflib.unified_diff(
        (original / HEADER).read_text().splitlines(True), (patched / HEADER).read_text().splitlines(True),
        fromfile="a/" + HEADER, tofile="b/" + HEADER))
    require(difference == (HERE / "pipeann_page_lifetime.patch").read_text(),
            "Native source differs from the approved page-lifetime-only patch")
    return [{"relative_path": name, "original": before[name], "patched": after[name]} for name in before]


def compile_command(template, original, source, output):
    command = [arg.replace(str(original), str(source)) for arg in shlex.split(template["command"])]
    command[command.index("-o") + 1] = str(output)
    if original != source:
        command.insert(1, f"-ffile-prefix-map={source}={original}")
    return command


def archive_members(path):
    names = subprocess.check_output(["ar", "t", str(path)], text=True).splitlines()
    require(names and len(names) == len(set(names)), "Archive has empty or ambiguous member inventory")
    return [(name, hashlib.sha256(subprocess.check_output(["ar", "p", str(path), name])).hexdigest())
            for name in names]


def validate(source):
    source = Path(source).resolve(strict=True)
    proof_path = source.parent / PROOF
    proof = json.loads(proof_path.read_text())
    require(proof["schema_version"] == 1 and proof["status"] == "complete" and proof["mode"] == MODE
            and proof["authorization"] == AUTHORIZATION and proof["production_index_loaded"] is False,
            "Unapproved or incomplete PipeANN correction")
    original = Path(proof["original_source_directory"]).resolve(strict=True)
    require(original != source and proof["patched_source_directory"] == str(source),
            "Corrected source must be isolated from the preserved original")
    _, _, recipe = base.native_recipe(original)
    require(recipe["libraries"]["pipeann"]["sha256"] == ORIGINAL_LIBRARY_SHA256
            and proof["original_library"] == recipe["libraries"]["pipeann"]
            and proof["original_toolchain"] == recipe["toolchain"]
            and proof["compiler"] == recipe["compiler"], "Original PipeANN/compiler authority changed")
    records = [base.identity(proof_path), base.authenticate(proof["patch"]),
               base.authenticate(proof["builder"]), base.authenticate(proof["proof_reader"]),
               base.authenticate(proof["compiler"]), base.authenticate(proof["ar"])]
    require(proof["patch"] == base.identity(HERE / "pipeann_page_lifetime.patch")
            and proof["proof_reader"] == base.identity(Path(__file__)),
            "Correction proof reader or approved patch changed")
    inventory = source_delta(original, source)
    require(inventory == proof["source_inventory"], "Recorded native source inventory changed")
    for entry in inventory:
        records.extend((entry["original"], entry["patched"]))
    original_library = base.authenticate(proof["original_library"])
    library = base.authenticate(proof["library"])
    require(Path(library["path"]) == source.parent / "build/src/libpipeann.a",
            "Corrected archive is outside its isolated toolchain")
    records.extend((original_library, library))
    before, after = archive_members(original_library["path"]), archive_members(library["path"])
    require([name for name, _ in before] == [name for name, _ in after], "Archive membership/order changed")
    delta = [{"name": name, "original_sha256": old, "patched_sha256": new}
             for (name, old), (_, new) in zip(before, after)]
    require(delta == proof["archive_members"]
            and sorted(item["name"] for item in delta if item["original_sha256"] != item["patched_sha256"])
            == list(MEMBERS), "Unexpected native archive member changes")
    templates = json.loads(Path(recipe["compile_commands"]["path"]).read_text())
    executions = proof["commands"]
    require(len(executions) == 6 and all(item["exit_code"] == 0 for item in executions),
            "Incomplete native compilation/archive command evidence")
    for index, member in enumerate(MEMBERS):
        template = next(item for item in templates if item["file"] ==
                        str(original / "src/search" / member.removesuffix(".o")))
        objects = proof["objects"][member]
        for variant, tree, offset in (("reference", original, 0), ("patched", source, 1)):
            obj = base.authenticate(objects[variant])
            expected_hash = dict(before if variant == "reference" else after)[member]
            require(obj["sha256"] == expected_hash, "Compiled native object differs from its archive member")
            execution = executions[index * 2 + offset]
            require(execution["name"] == variant + "-" + member and execution["cwd"] == template["directory"]
                    and execution["argv"] == compile_command(template, original, tree, obj["path"]),
                    "Native compiler flags changed beyond source/output path and __FILE__ mapping")
            records.extend((obj, base.authenticate(execution["log"])))
    ar = Path(shutil.which("ar")).resolve(strict=True)
    require(proof["ar"] == base.identity(ar), "Archive tool identity changed")
    expected_archive = [
        [str(ar), "r", library["path"], *[proof["objects"][member]["patched"]["path"] for member in MEMBERS]],
        [str(ar), "s", library["path"]],
    ]
    for execution, expected in zip(executions[4:], expected_archive):
        require(execution["argv"] == expected and execution["cwd"] == str(source.parent),
                "Unregistered native archive operation")
        records.append(base.authenticate(execution["log"]))
    manifest = base.authenticate(proof["source_manifest"])
    require(Path(manifest["path"]) == source.parent / "source-manifest.json",
            "Corrected source manifest is outside its isolated toolchain")
    decoded = json.loads(Path(manifest["path"]).read_text())
    require(decoded == {"directory": str(source), "revision": recipe["source_revision"],
                        "files": {entry["relative_path"]: {key: entry["patched"][key] for key in ("bytes", "sha256")}
                                  for entry in inventory}}, "Corrected source manifest changed")
    records.append(manifest)
    return {
        "binding": {"mode": MODE, "authorization": AUTHORIZATION, "proof": str(proof_path),
                    "proof_sha256": base.identity(proof_path)["sha256"], "original_source": str(original),
                    "source": str(source), "library": library["path"]},
        "proof": proof, "protected": records,
    }


def native_recipe(source):
    source = Path(source).resolve(strict=True)
    correction = validate(source)
    original = Path(correction["binding"]["original_source"])
    common, link, provenance = base.native_recipe(original)
    common = [arg.replace(str(original), str(source)) for arg in common]
    common.insert(1, f"-ffile-prefix-map={source}={original}")
    original_library = provenance["libraries"]["pipeann"]
    library = correction["proof"]["library"]
    link = [library["path"] if arg == original_library["path"] else arg for arg in link]
    provenance.update(original_source_manifest=provenance["source_manifest"],
                      source_manifest=correction["proof"]["source_manifest"],
                      original_library=original_library, native_correctness=correction["binding"])
    provenance["libraries"]["pipeann"] = library
    return common, link, provenance
