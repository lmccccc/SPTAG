"""Authenticate loader proof identities; emit exactly seven string-valued metadata fields."""

from __future__ import annotations

import configparser
import hashlib
import json
from pathlib import Path
import subprocess


MODE = "linear-label-delimiters-v1"
PURPOSE = "diskann-loader-only-linear-label-delimiters-v1"
REVISION = "78256bbab4685e1774e78d331e081a153be26823"
IDENTITY_KEYS = {"path", "resolved", "bytes", "device", "inode", "mtime_ns", "ctime_ns", "sha256"}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate loader proof JSON key: " + key)
        result[key] = value
    return result


def authenticate(policy: Path, source: Path, original_library: dict, file_record) -> tuple[dict, dict, list[dict]]:
    if not policy.is_absolute() or policy != policy.resolve(strict=True):
        raise ValueError("Loader policy must be an explicit canonical absolute file")
    protected = []

    def capture(path: Path) -> dict:
        record = file_record(path)
        protected.append(record)
        return record

    def identity(expected: dict, path: Path | None = None) -> dict:
        if set(expected) != IDENTITY_KEYS:
            raise ValueError("Loader proof requires complete native file identities")
        actual = capture(path or Path(expected["path"]))
        if actual != expected:
            raise ValueError("Loader proof file identity mismatch: " + str(actual["path"]))
        return actual

    policy_record = capture(policy)
    text = policy.read_text()
    if "\0" in text:
        raise ValueError("NUL in loader policy")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith((";", "[")) and "=" not in stripped:
            raise ValueError("Loader policy requires one key=value per physical line")
    ini = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",),
                                   inline_comment_prefixes=None, empty_lines_in_values=False, strict=True)
    ini.read_string(text)
    if ini.sections() != ["Loader"] or ini.defaults():
        raise ValueError("Loader policy must contain exactly [Loader], without defaults")
    values = dict(ini["Loader"])
    if set(values) != {"mode", "proof", "proofsha256", "stockbinary", "library"} or values["mode"] != MODE:
        raise ValueError("Loader policy has an unknown mode or non-metadata keys")
    for key in ("proof", "stockbinary", "library"):
        path = Path(values[key])
        if not path.is_absolute() or path != path.resolve(strict=True):
            raise ValueError("Loader metadata paths must be canonical absolute files: " + key)
    proof_record = capture(Path(values["proof"]))
    if proof_record["sha256"] != values["proofsha256"]:
        raise ValueError("Loader proof SHA256 mismatch")
    proof = json.loads(Path(values["proof"]).read_text(), object_pairs_hook=unique_object)
    if (type(proof.get("schema_version")) is not int or proof["schema_version"] != 1
            or proof.get("purpose") != PURPOSE or proof.get("base_revision") != REVISION):
        raise ValueError("Unrecognized loader-only proof schema, purpose or base revision")
    if Path(proof["original_source_tree"]) != source or proof["original_library"] != original_library:
        raise ValueError("Loader proof does not bind the original pinned source/archive")
    identity(proof["original_library"])
    patched_library = identity(proof["patched_library"], Path(values["library"]))
    stock_binary = identity(proof["patched_stock_binary"], Path(values["stockbinary"]))
    identity(proof["original_stock_binary"], source / "build/install/bin/search_disk_index")
    identity(proof["compiler"])
    patched_source = Path(proof["patched_source_tree"])
    if not patched_source.is_absolute() or patched_source == source:
        raise ValueError("Loader repair requires an isolated native source tree")
    original_cpp = identity(proof["original_pq_flash_index_cpp"], source / "src/pq_flash_index.cpp")
    patched_cpp = identity(proof["patched_pq_flash_index_cpp"], patched_source / "src/pq_flash_index.cpp")
    before = Path(original_cpp["path"]).read_bytes()
    after = before
    for old, new in (
        (b"fileContent.find(',', lbl_pos)", b'fileContent.find_first_of(",\\n", lbl_pos)'),
        (b"buffer.find(',', lbl_pos)", b'buffer.find_first_of(",\\n", lbl_pos)'),
    ):
        if after.count(old) != 1:
            raise ValueError("Unexpected original label parser")
        after = after.replace(old, new)
    if after != Path(patched_cpp["path"]).read_bytes():
        raise ValueError("Native source differs beyond the two approved delimiter scans")
    changed_sources = []
    native_paths = [name for name in subprocess.check_output(
        ["git", "-C", str(source), "ls-files", "-z"]).decode().split("\0")
                    if name and (source / name).is_file()]
    inventory = proof["source_inventory"]
    if [item["relative_path"] for item in inventory] != native_paths:
        raise ValueError("Loader source inventory does not cover the pinned original source")
    for item in inventory:
        name = item["relative_path"]
        original = identity(item["original"], source / name)
        patched = identity(item["patched"], patched_source / name)
        unchanged = original["sha256"] == patched["sha256"]
        if item["unchanged"] is not unchanged:
            raise ValueError("Inconsistent loader source-inventory flag")
        if not unchanged:
            changed_sources.append(name)
    if changed_sources != ["src/pq_flash_index.cpp"] or proof["changed_native_sources"] != changed_sources:
        raise ValueError("Unapproved native source/header change")
    members = proof["archive_members"]
    for key, archive in (("original", original_library), ("patched", patched_library)):
        names = subprocess.check_output(["/usr/bin/ar", "t", archive["path"]], text=True).splitlines()
        if names != [item["name"] for item in members] or len(set(names)) != len(names):
            raise ValueError("Loader archive member inventory differs")
        for ordinal, item in enumerate(members):
            payload = subprocess.check_output(["/usr/bin/ar", "p", archive["path"], item["name"]])
            expected = {"ordinal": ordinal, "name": item["name"], "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest()}
            if item[key] != expected:
                raise ValueError("Loader archive member hash mismatch")
    changed_members = [item["name"] for item in members if item["original"] != item["patched"]]
    if changed_members != ["pq_flash_index.cpp.o"] or proof["changed_archive_members"] != changed_members:
        raise ValueError("Loader repair changed an unapproved archive member")
    if any(item["unchanged"] is not (item["original"] == item["patched"]) for item in members):
        raise ValueError("Inconsistent archive member unchanged flag")
    for record in protected:
        if file_record(Path(record["path"])) != record:
            raise ValueError("Loader authority changed during authentication")
    binding = {
        "mode": MODE, "path": str(policy), "sha256": policy_record["sha256"],
        "proof": proof_record["path"], "proof_sha256": proof_record["sha256"],
        "library": patched_library["path"], "stock_binary": stock_binary["path"],
    }
    return binding, proof, protected
