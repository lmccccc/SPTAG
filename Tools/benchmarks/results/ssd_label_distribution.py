#!/usr/bin/env python3
"""Scan immutable SSD postings, export a portable report, and publish a descriptive table."""

import argparse
import copy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import update_results as store

HERE = Path(__file__).resolve().parent
GROUPS = ("physical_H_plus_O", "H_prefix", "O_suffix")
STUDY = "index-statistics"
TABLE = "ssd-posting-label-proportions"
OUTPUT = "posting_label_distribution.json"
MEAN = "unweighted mean of label_count/posting_record_count over positive postings"
DISTRIBUTION = {"unit": "fraction", "bin_semantics": "[left,right), last bin includes 1",
                "edges": [i / 100 for i in range(101)]}
require = store.require


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text())


def identity(path):
    path = Path(path).resolve()
    value = path.stat()
    return dict(path=str(path), device=value.st_dev, inode=value.st_ino, size=value.st_size,
                mtime_ns=value.st_mtime_ns, ctime_ns=value.st_ctime_ns)


def integer(value, name, minimum=0):
    require(type(value) is int and value >= minimum, f"Invalid {name}")
    return value


def validate(result):
    require(result["schema_version"] == 1 and result["state"] == "complete", "Incomplete/unknown scan format")
    require(result["population"] == "all_persisted_SSD_records", "Expected a full-population scan")
    require(result["zero_policy"] == "exclude_postings_without_the_label"
            and result["mean_definition"] == MEAN, "Unexpected conditional-mean policy")
    require(result["primary_group"] == GROUPS[0] and set(result["groups"]) == set(GROUPS),
            "Unexpected posting groups")
    require(result["distribution"] == DISTRIBUTION, "Unexpected distribution bins")
    require(result["categorical_column"] == 0, "Unexpected categorical column")
    generation = result["generation_fingerprint"]
    require(isinstance(generation, str) and generation.isascii() and generation.isdecimal()
            and 0 < int(generation) < 2**64, "Invalid generation fingerprint")
    total = integer(result["directory_entries"], "directory entries", 1)
    empty = integer(result["empty_directory_entries"], "empty directory entries")
    corpus = integer(result["corpus_vectors"], "corpus vectors", 1)
    physical = result["groups"][GROUPS[0]]
    require(physical["nonempty_postings"] + empty == total, "Posting-directory accounting mismatch")
    labels = [row["label"] for row in physical["labels"]]
    require(labels == list(range(len(labels))) and 0 < len(labels) <= 4096, "Invalid dense label inventory")
    for name, group in result["groups"].items():
        count = integer(group["nonempty_postings"], "nonempty postings")
        records = integer(group["stored_records"], "stored records")
        require(count <= physical["nonempty_postings"] and records >= count
                and (count > 0 or records == 0), f"Invalid {name} totals")
        require([row["label"] for row in group["labels"]] == labels, "Group label inventory mismatch")
        require(sum(row["source_vector_count"] for row in group["labels"]) == corpus,
                "Corpus label accounting mismatch")
        require(sum(row["stored_label_occurrences"] for row in group["labels"]) == records,
                "Stored label accounting mismatch")
        for row in group["labels"]:
            integer(row["label"], "label")
            integer(row["source_vector_count"], "source count", 1)
            positive = integer(row["postings_with_label"], "positive posting count")
            occurrences = integer(row["stored_label_occurrences"], "stored occurrences")
            pure = integer(row["pure_postings"], "pure posting count")
            bins = row["histogram_counts"]
            require(isinstance(bins, list) and len(bins) == 100
                    and all(type(n) is int and n >= 0 for n in bins), "Invalid histogram counts")
            require(sum(bins) == positive <= count and positive <= occurrences <= records,
                    "Positive-posting accounting mismatch")
            require(0 <= pure <= bins[-1] <= positive, "Invalid pure-posting count")
            ratios = [row[key] for key in ("min_proportion", "mean_proportion", "max_proportion")]
            if positive:
                require(all(type(r) in (int, float) and math.isfinite(r) for r in ratios)
                        and 0 < ratios[0] <= ratios[1] <= ratios[2] <= 1, "Invalid conditional proportions")
            else:
                require(ratios == [None, None, None] and occurrences == 0, "Invalid absent-label statistics")
    h, o = (result["groups"][name] for name in GROUPS[1:])
    require(physical["stored_records"] == h["stored_records"] + o["stored_records"],
            "H/O record accounting mismatch")
    for full, hr, other in zip(*(result["groups"][name]["labels"] for name in GROUPS)):
        require(full["stored_label_occurrences"] == hr["stored_label_occurrences"] + other["stored_label_occurrences"],
                "Per-label H/O accounting mismatch")
        require(full["source_vector_count"] == hr["source_vector_count"] == other["source_vector_count"],
                "Per-label source count mismatch")
    store.encoded(result)


def verify_sources(registration):
    for path, expected in registration["inputs"].items():
        require(identity(path) == expected, f"Scan input changed: {path}")
    for path, expected in registration["sources"].items():
        require(store.file_sha(path) == expected, f"Scanner artifact changed: {path}")


def scan(tenant, binary, output, index_version, reserve_mib):
    sys.path.insert(0, str(HERE.parent))
    import native_input_io
    import validate_spann_hierarchy_config

    tenant, binary, output = (Path(p).resolve() for p in (tenant, binary, output))
    require(bool(index_version), "An explicit physical index version is required")
    integer(reserve_mib, "memory reserve")
    require(reserve_mib < 2**44, "Memory reserve is too large")
    require(output != tenant and output not in tenant.parents and tenant not in output.parents,
            "Scan output overlaps the immutable tenant directory")
    config_path = tenant / "indexloader.ini"
    config = native_input_io.read_config(config_path)
    base, build = config["base"], config["buildssdindex"]
    require(base["valuetype"] == "UInt8" and base["dim"] == "128" and build["storage"] == "STATIC",
            "Only raw STATIC UInt8/128 postings are supported")
    require(all(build[key] == "false" for key in (
        "enabledatacompression", "enabledeltaencoding", "enablepostinglistrearrange", "enablehybriddistance")),
        "Compressed, rearranged, delta-encoded or hybrid postings are unsupported")
    require(build["enablelimitedtagposting"] == "true" and build["limitedtagcolumn"] == "0"
            and build["numtagspervec"] == "2" and base["ssdindexfilenum"] == "1",
            "Expected one SSD file and two attributes with categorical column 0")
    require(build["columntypes"] == "categorical,numeric", "Expected categorical,numeric attribute schema")
    require(build.get("postingquantizer", "").lower() in ("", "none"), "Quantized postings are unsupported")
    ssd = tenant / base["ssdindex"]
    support = tenant / build["limitedtagsupportfile"]
    require(not Path(base["ssdindex"]).is_absolute() and not Path(build["limitedtagsupportfile"]).is_absolute(),
            "Saved SSD/support filenames must be relative to the tenant directory")
    inputs = {str(path): identity(path) for path in (config_path, ssd, support)}
    source_paths = (Path(__file__).resolve(), HERE / "ssd_label_distribution_scan.cpp", binary,
                    Path(store.__file__), Path(native_input_io.__file__), Path(validate_spann_hierarchy_config.__file__))
    sources = {str(path): store.file_sha(path) for path in source_paths}
    command = ["nice", "-n", "10", "ionice", "-c", "2", "-n", "7",
               str(binary), str(ssd), str(support), str(reserve_mib)]
    registration = dict(schema_version=1, started_at=now(), index_version=index_version,
                        inputs=inputs, sources=sources, command=command, minimum_available_mib=reserve_mib,
                        scan="exact full population; direct read-only IO; one scanner worker")
    output.mkdir(parents=True, exist_ok=False)
    store.atomic_json(output / "registration.json", registration)
    try:
        with (output / "scan-result.json").open("x") as result_stream, (output / "scan-progress.log").open("x") as log:
            process = subprocess.Popen(command, stdout=result_stream, stderr=log)
            store.atomic_json(output / "execution.json", dict(
                pid=process.pid, controller_pid=os.getpid(), command=command))
            code = process.wait()
        require(code == 0, f"Scanner exited {code}; see scan-progress.log")
        verify_sources(registration)
        result = read_json(output / "scan-result.json")
        validate(result)
        require(result["generation_fingerprint"] == build["limitedtaggenerationfingerprint"],
                "Saved INI generation disagrees with SSD/support")
        result.update(index_version=index_version, completed_at=now(), source_identities=inputs,
                      registration_sha256=store.file_sha(output / "registration.json"),
                      disclosures=[
                          "Primary posting is a complete persisted H+O directory entry, not a query-limited prefix.",
                          "H and O statistics use their separate subregion denominators and are not pooled.",
                          "Stored replication counts in each posting; no corpus-wide or cross-region deduplication.",
                          "Absent-label postings are excluded; each positive posting has equal weight.",
                          "Histogram counts are exact; means use unbinned ratios.",
                          "Numeric column 1 is not a second label.",
                          "Input identities and generation are checked; no full SSD-content digest is claimed."])
        store.atomic_json(output / OUTPUT, result)
        store.atomic_json(output / "completion.json", dict(
            state="complete", exit_code=0, completed_at=result["completed_at"], output=OUTPUT,
            sha256=store.file_sha(output / OUTPUT), source_inputs_unchanged=True, accounting_validated=True))
        return result
    except Exception as error:
        store.atomic_json(output / "failure.json", dict(state="failed", error=str(error), failed_at=now()))
        raise


def read_sealed(directory):
    directory = Path(directory).resolve()
    require(not (directory / "failure.json").exists(), "Failed scan/report cannot be published")
    seal = read_json(directory / "completion.json")
    require(seal["state"] == "complete" and seal["output"] == OUTPUT, "Missing/invalid completion seal")
    require(store.file_sha(directory / OUTPUT) == seal["sha256"], "Result digest mismatch")
    result = read_json(directory / OUTPUT)
    validate(result)
    require(result["completed_at"] == seal["completed_at"], "Completion timestamp mismatch")
    store.timestamp(result["completed_at"])
    require(isinstance(result["index_version"], str) and result["index_version"], "Missing physical index version")
    return result, seal


def export_report(scan_directory, output, dataset):
    scan_directory, output = Path(scan_directory).resolve(), Path(output).resolve()
    result, seal = read_sealed(scan_directory)
    require(seal.get("exit_code") == 0 and seal.get("source_inputs_unchanged") is True
            and seal.get("accounting_validated") is True, "Source scan was not accepted")
    registration_path = scan_directory / "registration.json"
    require(store.file_sha(registration_path) == result["registration_sha256"], "Registration digest mismatch")
    registration = read_json(registration_path)
    require(registration["index_version"] == result["index_version"]
            and registration["inputs"] == result["source_identities"], "Source provenance mismatch")
    verify_sources(registration)
    require(bool(dataset), "Dataset is required")
    portable = {key: result[key] for key in (
        "schema_version", "state", "population", "zero_policy", "mean_definition", "primary_group",
        "directory_entries", "empty_directory_entries", "corpus_vectors", "categorical_column",
        "generation_fingerprint", "distribution", "groups", "index_version", "completed_at", "disclosures")}
    portable.update(dataset=dataset, provenance={
        "source_result_sha256": seal["sha256"], "source_registration_sha256": result["registration_sha256"],
        "source_completion_sha256": store.file_sha(scan_directory / "completion.json"),
        "inputs": [{"file": Path(path).name, "size": value["size"]} for path, value in registration["inputs"].items()],
        "sources": [{"file": Path(path).name, "sha256": digest} for path, digest in registration["sources"].items()],
        "verification": "Source identities and registered code hashes rechecked at export; transferred report is not a new scan.",
        "source_inputs_rechecked_at_export": True, "exported_at": now()})
    output.mkdir(parents=True, exist_ok=False)
    store.atomic_json(output / OUTPUT, portable)
    store.atomic_json(output / "completion.json", dict(
        state="complete", kind="portable-ssd-label-report", output=OUTPUT, completed_at=result["completed_at"],
        sha256=store.file_sha(output / OUTPUT), source_result_sha256=seal["sha256"]))
    return portable


def check_report(directory):
    result, seal = read_sealed(directory)
    require(seal.get("kind") == "portable-ssd-label-report", "Export a portable report before importing it")
    require(isinstance(result["dataset"], str) and result["dataset"], "Missing dataset")
    require(result["provenance"]["source_inputs_rechecked_at_export"] is True
            and result["provenance"]["source_result_sha256"] == seal["source_result_sha256"],
            "Portable provenance mismatch")
    return result, seal


def table_event(result, seal, version_order):
    metadata = {key: value for key, value in result.items() if key != "groups"}
    metadata["group_totals"] = {
        name: {key: value for key, value in group.items() if key != "labels"}
        for name, group in result["groups"].items()}
    return dict(
        schema_version=1, kind="table", study=STUDY, variant=TABLE,
        dataset=result["dataset"], algorithm="SPTAG", table_name=TABLE, status="validated",
        completed_at=result["completed_at"],
        version={"id": result["index_version"] + ":ssd-label-distribution-v1", "order": version_order,
                 "index": result["index_version"], "client": "exact-ssd-label-distribution-v1"},
        source={"description": "Exact descriptive SSD posting-label composition; not query performance or a causal ablation.",
                "sha256": seal["sha256"], **metadata},
        rows=[{"group": name, **row} for name in GROUPS for row in result["groups"][name]["labels"]])


def prospective(records, events):
    records = list(records)
    existing = {r["event_id"] for r in records}
    for event in events:
        store.validate_event(event)
        event_id = store.digest(event)
        if event_id in existing:
            continue
        record = dict(sequence=len(records) + 1, previous_hash=records[-1]["hash"] if records else "",
                      event_id=event_id, event=event)
        record["hash"] = store.digest(record)
        records.append(record)
        existing.add(event_id)
    return store.project(records)


def publish(directory, data_directory, version_order):
    result, seal = check_report(directory)
    integer(version_order, "version order")
    directory, data_directory = Path(directory).resolve(), Path(data_directory).resolve()
    require(data_directory != directory and directory not in data_directory.parents
            and data_directory not in directory.parents, "Registry and portable report must be separate directories")
    event = table_event(result, seal, version_order)
    with store.locked(data_directory):
        records = store.load_history(data_directory / "history.jsonl")
        if records:
            before = store.project(records)
            require(read_json(data_directory / "current.json") == before, "Unified JSON is stale; run refresh")
            catalog = copy.deepcopy(before["catalog"])
        else:
            require(not (data_directory / "current.json").exists(), "Current JSON exists without history")
            before = None
            catalog = {"datasets": [result["dataset"]], "studies": [], "expected_cells": []}
    for policy in catalog.get("selected_indexes", []):
        if (policy["dataset"], policy["algorithm"]) == (result["dataset"], "SPTAG"):
            require(policy["index"] == result["index_version"], "Report does not match the selected physical index")
    require((result["dataset"], "SPTAG", result["index_version"]) not in store.retired_indexes(records),
            "Cannot publish statistics for a retired index")
    require(result["dataset"] in catalog["datasets"], "Dataset is absent from the existing catalog")
    events = []
    if not any(study["id"] == STUDY for study in catalog["studies"]):
        catalog["studies"].append({"id": STUDY, "description": "Descriptive physical-index statistics"})
        revision = 1 + max((r["event"]["revision"] for r in records if r["event"]["kind"] == "catalog"), default=0)
        events.append(dict(schema_version=1, kind="catalog", revision=revision, catalog=catalog,
                           completed_at=result["completed_at"],
                           source={"description": "Register descriptive statistics without altering benchmark coverage."}))
    events.append(event)
    after = prospective(records, events)
    event_id = store.digest(event)
    require(any(t["event_id"] == event_id for t in after["tables"]), "Report would not become an active table")
    if before:
        for key in ("points", "experiments", "coverage", "retired_indexes"):
            require(before.get(key) == after.get(key), f"Publication would change performance field {key}")
        table_scope = ("study", "variant", "dataset", "algorithm", "table_name")
        require(all(t in after["tables"] or (
            all(t[key] == event[key] for key in table_scope))
            for t in before["tables"]), "Publication would replace an unrelated table")
    current = store.update(data_directory, events, expected_history_head=records[-1]["hash"] if records else "")
    require(current == after, "Published registry differs from preflight")
    return dict(state="complete", unified_json=str(data_directory / "current.json"), event_id=event_id,
                table_name=TABLE, labels=len(result["groups"][GROUPS[0]]["labels"]),
                rows=len(event["rows"]), history=current["history"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("scan", help="Read a local immutable tenant index")
    run.add_argument("--tenant-dir", type=Path, required=True)
    run.add_argument("--binary", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--index-version", required=True)
    run.add_argument("--min-available-mib", type=int, default=1024)
    export = commands.add_parser("export", help="Recheck a local scan and export path-independent statistics")
    export.add_argument("--scan-dir", type=Path, required=True)
    export.add_argument("--output-dir", type=Path, required=True)
    export.add_argument("--dataset", required=True)
    for name in ("check", "publish"):
        command = commands.add_parser(name)
        command.add_argument("--report-dir", type=Path, required=True)
        if name == "publish":
            command.add_argument("--data-dir", type=Path, required=True)
            command.add_argument("--version-order", type=int, required=True)
    args = parser.parse_args()
    if args.command == "scan":
        result = scan(args.tenant_dir, args.binary, args.output_dir, args.index_version, args.min_available_mib)
    elif args.command == "export":
        result = export_report(args.scan_dir, args.output_dir, args.dataset)
    elif args.command == "check":
        result, _ = check_report(args.report_dir)
    else:
        print(json.dumps(publish(args.report_dir, args.data_dir, args.version_order)))
        return
    print(json.dumps(dict(state="complete", index_version=result["index_version"],
                          labels=len(result["groups"][GROUPS[0]]["labels"]),
                          nonempty_postings=result["groups"][GROUPS[0]]["nonempty_postings"])))


if __name__ == "__main__":
    main()
