#!/usr/bin/env python3
"""Seal an already-tested loader-only delivery; never execute an index operation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess

from build_diskann_client import artifact, copy_exclusive, sha256, verify_inputs_unchanged
from loader_policy import IDENTITY_KEYS, authenticate


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
TC = ROOT / "datasets/sift1b/toolchains/threeway_native_20260929"


def save(path: Path, data) -> None:
    with path.open("x") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o444)


def check_record(record: dict) -> None:
    expected = {key: record[key] for key in IDENTITY_KEYS}
    if artifact(Path(record["path"])) != expected:
        raise RuntimeError("Referenced evidence changed: " + record["path"])


def check_references(value) -> int:
    count = 0
    if isinstance(value, dict):
        if IDENTITY_KEYS.issubset(value):
            check_record(value)
            count += 1
        for child in value.values():
            count += check_references(child)
    elif isinstance(value, list):
        for child in value:
            count += check_references(child)
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publication", type=Path, required=True)
    args = parser.parse_args()
    publication = args.publication.resolve(strict=True)
    if publication.parent != TC:
        parser.error("Only the dedicated isolated native publication is permitted")
    handoff = publication / "handoff.json"
    if handoff.exists() or handoff.is_symlink():
        parser.error("Refusing to overwrite a published native handoff")
    original = ROOT / "DiskANN"
    policy, proof, protected_policy = authenticate(
        publication / "policy.ini", original, artifact(original / "build/install/lib/libdiskann.a"), artifact)
    before_path = publication / "provenance/protected-before.json"
    protected = json.loads(before_path.read_text())
    verify_inputs_unchanged(protected)
    prior_path = publication / "provenance/prior-publication-before.json"
    prior = json.loads(prior_path.read_text()) if prior_path.exists() else None
    if prior:
        verify_inputs_unchanged(prior["files"])
        prior_handoff = json.loads((Path(prior["publication"]) / "handoff.json").read_text())
        if any(proof[key]["sha256"] != prior_handoff[key]["sha256"]
               for key in ("patched_library", "patched_stock_binary")):
            raise RuntimeError("Metadata-only continuation unexpectedly changed native library/stock bytes")
    manifests, native_sources = {}, {}
    for name in ("diskannBuildAdmission", "diskannBench"):
        path = publication / "bin" / name
        manifest_path = path.with_name(path.name + ".build.json")
        manifest = json.loads(manifest_path.read_text())
        if (manifest["loader_policy"] != policy or manifest["library"] != proof["patched_library"]
                or manifest["library"]["path"] != policy["library"]
                or manifest["original_library"] != proof["original_library"]
                or manifest["algorithm_base_revision"] != proof["base_revision"]
                or manifest["native_source_variant"] != policy["mode"]
                or manifest["compiler"] != proof["compiler"]
                or manifest["binary"]["path"] != str(path)
                or manifest["binary"]["sha256"] != sha256(path)
                or manifest["binary"]["size"] != path.stat().st_size):
            raise RuntimeError("Native client does not bind this exact loader-only publication")
        check_references(manifest)
        for source in manifest["sources"]:
            native_sources[source["path"]] = source
        if path.stat().st_mode & 0o222:
            path.chmod(0o555)
        if manifest_path.stat().st_mode & 0o222:
            manifest_path.chmod(0o444)
        manifests[name] = {"binary": artifact(path), "manifest": artifact(manifest_path),
                           "runtime_dependencies": manifest["runtime_dependencies"],
                           "compile_command": manifest["command"]}
    report_paths = [
        publication / "tests/validation-001/report.json",
        *[publication / "tests/validation-001" / stage / "report.json"
          for stage in ("admission", "benchmark", "stock", "parser", "policy")],
        publication / "tests/compatibility-001/report.json",
        publication / "tests/compatibility-001/compatibility/report.json",
    ]
    reports = {str(path.relative_to(publication)): json.loads(path.read_text()) for path in report_paths}
    if any(report["status"] != "passed" for report in reports.values()):
        raise RuntimeError("All bounded native acceptance reports must pass")
    verified_references = sum(check_references(report) for report in reports.values())
    admission = reports["tests/validation-001/admission/report.json"]
    benchmark = reports["tests/validation-001/benchmark/report.json"]
    stock = reports["tests/validation-001/stock/report.json"]
    parser_report = reports["tests/validation-001/parser/report.json"]
    policy_report = reports["tests/validation-001/policy/report.json"]
    compatibility = reports["tests/compatibility-001/compatibility/report.json"]
    if (admission["selected_labels"] != 201 or admission["planned_stock_query_calls"] != 261
            or benchmark["capture_pairs"] != 48 or benchmark["exact_id_distance_pairs"] != 3840
            or not benchmark["live_original_fixed_equal"] or not benchmark["accepted_captures_equal"]
            or stock["labels"] != 201 or stock["native_calls_per_variant"] != 261
            or stock["exact_id_distance_pairs"] != 2610 or not stock["ids_and_distances_byte_identical"]):
        raise RuntimeError("Required all-label/native-result coverage is incomplete")
    for name in ("build_diskann_loader.py", "publish_diskann_loader.py",
                 "tests/test_diskann_loader.py", "tests/diskann_label_parser_probe.cpp",
                 "tests/test_diskann_build_guard.py", "tests/header_unit_test.cpp",
                 "tests/profile_options_test.cpp"):
        native_sources[str(HERE / name)] = artifact(HERE / name)
    snapshots = []
    snapshot_root = publication / "provenance/native-client-sources"
    snapshot_root.mkdir(exist_ok=False)
    for source in sorted(native_sources.values(), key=lambda item: item["path"]):
        original_source = Path(source["path"])
        target = snapshot_root / original_source.relative_to(HERE)
        target.parent.mkdir(parents=True, exist_ok=True)
        copy_exclusive(original_source, target, 0o444)
        if sha256(target) != source["sha256"]:
            raise RuntimeError("Native client source snapshot differs")
        snapshots.append({"source": source, "frozen_snapshot": artifact(target)})
    earlier_reports = [
        "diskann-build-admission-final-report.json",
        "diskann-build-admission-published-001/tests.json",
        "diskann-shared-guard-published-001/admission-tests.json",
        "diskann-shared-guard-regression-001/admission-tests.json",
        "diskann-build-admission-delivery-001/source-proof-and-derivation.json",
        "diskann-build-admission-delivery-001/shared-guard-equivalence.json",
        "diskann-build-admission-delivery-001/stock-caller-coverage.json",
    ]
    clean = subprocess.check_output(
        ["git", "-C", str(original), "status", "--porcelain", "--untracked-files=no"], text=True).strip()
    revision = subprocess.check_output(["git", "-C", str(original), "rev-parse", "HEAD"], text=True).strip()
    if clean or revision != proof["base_revision"]:
        raise RuntimeError("Original native checkout changed")
    verify_inputs_unchanged(protected_policy + protected + list(native_sources.values()))
    protected_after = publication / "provenance/protected-after.json"
    save(protected_after, [artifact(Path(record["path"])) for record in protected])
    immutable_paths = [
        publication / "lib/libdiskann.a", publication / "bin/search_disk_index",
        publication / "policy.ini", publication / "provenance/loader-proof.json",
        *[Path(record[key]["path"]) for record in manifests.values() for key in ("binary", "manifest")],
    ]
    if any(path.stat().st_mode & 0o222 for path in immutable_paths):
        raise RuntimeError("A published native authority file is still writable")
    source_changes = [row["relative_path"] for row in proof["source_inventory"] if not row["unchanged"]]
    if source_changes != ["src/pq_flash_index.cpp"]:
        raise RuntimeError("Unapproved native-source delta")
    changed_members = [row for row in proof["archive_members"] if not row["unchanged"]]
    if len(changed_members) != 1 or changed_members[0]["name"] != "pq_flash_index.cpp.o":
        raise RuntimeError("Unapproved native archive delta")
    handoff_record = {
        "schema_version": 1, "purpose": proof["purpose"], "status": "complete",
        "approval": "approve-loader-only-fix", "publication": str(publication),
        "base_revision": proof["base_revision"], "algorithm_base_revision": proof["base_revision"],
        "policy": artifact(publication / "policy.ini"), "loader_policy": policy,
        "proof": artifact(publication / "provenance/loader-proof.json"),
        "original_source_tree": proof["original_source_tree"],
        "patched_source_tree": proof["patched_source_tree"],
        "original_pq_flash_index_cpp": proof["original_pq_flash_index_cpp"],
        "patched_pq_flash_index_cpp": proof["patched_pq_flash_index_cpp"],
        "exact_native_diff": proof["diff"],
        "original_library": proof["original_library"], "patched_library": proof["patched_library"],
        "original_stock_binary": proof["original_stock_binary"],
        "patched_stock_binary": proof["patched_stock_binary"],
        "native_clients": manifests, "compiler": proof["compiler"], "compiler_flags": proof["flags"],
        "archive_members": proof["archive_members"], "changed_archive_members": proof["changed_archive_members"],
        "unchanged_archive_members": len(proof["archive_members"]) - 1,
        "tracked_native_source_files": len(proof["source_inventory"]),
        "all_other_native_sources_and_headers_unchanged": True,
        "reference_object_byte_identical": proof["reference_object_byte_identical"],
        "reference_stock_byte_identical": proof["reference_stock_byte_identical"],
        "stock_object_reused_without_recompile": True,
        "stock_runtime_closure_identical": proof["stock_runtime_dependencies"] == proof["original_stock_runtime_dependencies"],
        "native_client_sources": snapshots,
        "validation": {
            "reports": [artifact(path) for path in report_paths], "verified_file_identity_references": verified_references,
            "fixture": str(ROOT / "DiskANN/build/categorical-reuse-smoke-filtered-only"),
            "fixture_shape": [16783, 128], "standalone_positive_cases_per_variant": 2,
            "standalone_negative_cases_per_variant": len([case for case in admission["cases"] if case["status"] == "rejected"]),
            "selected_labels": 201, "all201_certificate": admission["all201_certificate"],
            "all201_original_certificate": admission["all201_original_certificate"],
            "native_stock_calls_per_variant": 261, "stock_exact_id_distance_pairs": 2610,
            "benchmark_jobs_per_variant": 24, "benchmark_capture_pairs": 48,
            "benchmark_exact_id_distance_pairs": 3840,
            "benchmark_negative_cases_per_variant": len([case for case in benchmark["cases"] if case["expected"] == "rejected"]),
            "native_original_fixed_ids_distances_equal": True, "accepted_benchmark_captures_equal": True,
            "parser_semantic_cases": len(parser_report["semantic_cases"]),
            "semantic_notes": "Single/multiple labels, tabs, CRLF, trailing commas, and legacy EOF/error/cast behavior retained; an unterminated final row remains ignored.",
            "scaling": [{"rows": row["rows"], **{variant: {
                key: data[key] for key in ("metadata_median_seconds", "parser_median_seconds")}
                for variant, data in row["variants"].items()}} for row in parser_report["scaling"]],
            "scaling_measurement": parser_report["measurement"],
            "scaling_maximum_rows": 256000, "scaling_n_growth": parser_report["n_growth"],
            "original_parser_time_growth": parser_report["original_parser_growth"],
            "fixed_parser_time_growth": parser_report["fixed_parser_growth"],
            "largest_n_parser_speedup": parser_report["largest_n_speedup"],
            "reference_loop_microbenchmark": False, "full_native_index_load_scaling": False,
            "genuine_bounded_native_loads_and_searches": True,
            "builder_policy_negative_cases": len(policy_report["negative_cases"]),
            "standalone_policy_negative_cases": len(admission["policy_rejections"]),
            "adapter_policy_negative_cases": len([case for case in compatibility["benchmark_policy_cases"] if case["expected"] == "rejected"]),
            "legacy_no_policy_schema_and_captures_preserved": True,
            "existing_publication_refusals": len(compatibility["fresh_publication_refusals"]),
            "unit_test_logs": [artifact(publication / "build" / name) for name in
                               ("immutable-input-unit-tests.log", "header-unit-tests.log", "profile-options-tests.log")],
        },
        "preservation": {
            "protected_before": artifact(before_path), "protected_after": artifact(protected_after),
            "full_identity_unchanged_files": len(protected), "original_checkout_clean": True,
            "original_library_stock_and_build_executables_preserved": True,
            "prior_published_clients_and_manifests_preserved": True,
            "spann_pipeann_sources_binaries_and_shared_benchmark_header_preserved": True,
            "sptag_release_files_preserved": True,
            "original_production_config_sha256": "4dedff37d770d0860067c78437d26b25c96a957443a68d49316507fdf3903719",
            "earlier_accepted_reports_retained": [artifact(TC / "provenance" / name) for name in earlier_reports],
            "prior_native_publication": {
                "publication": prior["publication"], "full_identity_manifest": artifact(prior_path),
                "files_unchanged": len(prior["files"]), "library_and_stock_byte_identical": True,
            } if prior else None,
        },
        "interface": {
            "standalone_optional_argument": "--loader-policy ABSOLUTE_POLICY_INI",
            "standalone_argv_template": [str(publication / "bin/diskannBuildAdmission"), "--config",
                                        "ORIGINAL_BUILD/config.ini", "--certificate", "FRESH.json",
                                        "--loader-policy", str(publication / "policy.ini")],
            "certificate_loader_policy_exactly_matches_manifests": True,
            "loader_policy_schema": {"keys": ["mode", "path", "sha256", "proof", "proof_sha256", "library", "stock_binary"],
                                     "value_types": "exactly seven strings",
                                     "full_artifact_identities": "SHA256-bound proof JSON"},
            "historical_original_config_manifest_inputs_and_three_executables_still_authenticated": True,
            "benchmark_added_key": "[DiskANN] LoaderPolicy",
            "benchmark_Library": "Keep original algorithm-base archive; policy selects the corrected linked library.",
            "benchmark_SourceDirectory": str(original),
            "policy_only_metadata": True, "shared_benchmark_header_changed": False,
            "policy_bound_binaries_reject_missing_or_mismatched_policy": True,
        },
        "immutability": {
            "authority_files_read_only": [artifact(path) for path in immutable_paths],
            "native_source_and_client_source_snapshots_read_only": True,
            "publication_directories_sealed_after_handoff": True,
            "build_and_test_intermediates": "Retained at their captured file identities; no chmod that would invalidate proof/test ctime records. They are not runtime authority.",
        },
        "production_loaded": False, "production_index_loaded": False, "native_production_searches": 0,
        "production_builds": 0, "new_graphs_built": 0, "production_process_control_performed": False,
        "parent_files_modified": False, "dependencies_installed": False, "commits_or_pushes": False,
        "limitations": [
            "No production load/search was performed, as required; parent must authenticate and restart its guarded workflow.",
            "Scaling exercises the actual native metadata/parser in isolation, not a production/full-index scaling experiment.",
        ],
        "next_owner": "Parent exclusively owns orchestration, validators, INI/README, production controls and any later validation/search.",
    }
    save(handoff, handoff_record)
    for directory in sorted((path for path in publication.rglob("*") if path.is_dir() and not path.is_symlink()),
                            key=lambda path: len(path.parts), reverse=True):
        if directory.stat().st_mode & 0o222:
            directory.chmod(stat.S_IMODE(directory.stat().st_mode) & ~0o222)
    publication.chmod(stat.S_IMODE(publication.stat().st_mode) & ~0o222)
    verify_inputs_unchanged(protected_policy + protected + list(native_sources.values()))
    if prior:
        verify_inputs_unchanged(prior["files"])
    print(json.dumps({"status": "complete", "handoff": artifact(handoff),
                      "production_loaded": False, "native_production_searches": 0}), flush=True)


if __name__ == "__main__":
    main()
