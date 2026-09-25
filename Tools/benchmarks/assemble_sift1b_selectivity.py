#!/usr/bin/env python3
"""Assemble two completed native SIFT1B campaigns; never execute or render them."""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys

sys.dont_write_bytecode = True

import numpy as np

import official_benchmark_config as common
import run_pipeann_selectivity as pipe
from run_sift1b_official import parse_results, sequence
from sift1b_official_inputs import dnf_clauses, native_header, scenario_contract, validate_filter_config

from run_adaptive_frontier import POLICIES, SCHEMA_VERSION, repeat_cases
from run_full import PAYLOADS, read_ini
from selectivity_common import identity, validate_identities


HERE = Path(__file__).resolve().parent
SCENARIOS = list(pipe.SCENARIOS)
GRID = [16, 24, 48, 96, 192, 384, 768]
VARIANTS = ("graph", "postgraph_extra")
ADAPTIVE_VARIANTS = ("postgraph_extra",)
ENGINE_ORDER = ("SPTAG_adaptive", "SPTAG_H1", "PipeANN")
ENGINE = {"graph": "SPTAG_H1", "postgraph_extra": "SPTAG_adaptive"}
COUNT, DIMENSION, TOPK, CORPUS = 1000, 128, 10, 1000000000
COLUMNS = (
    "scenario", "engine", "L", "queries", "repeats", "threads", "cpu_nodes",
    "recall", "recall_min", "recall_max", "qps", "qps_min", "qps_max",
    "candidate_count", "selectivity", "predicate",
)
READONLY_FD_CAPTION = (
    "PipeANN file-access-only adaptation: READ_ONLY_TESTS O_RDONLY + explicit open-failure checks.\n"
    "ANN/search/filter/distance unchanged; PQ, pipeline 32, uring, tcmalloc and NO_MAPPING unchanged."
)
require = common.require


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


class Evidence:
    def __init__(self):
        self.files = {}
        self.identities = {}
        self.no_content_reads = set()

    def inventory(self, records, root=None):
        require(isinstance(records, dict) and records, "Missing registered identity inventory")
        paths = {}
        for name, recorded in records.items():
            if root is not None:
                require(not Path(name).is_absolute() and ".." not in Path(name).parts,
                        f"Invalid relative inventory path: {name}")
            path = Path(root) / name if root is not None else Path(name)
            paths[str(path)] = recorded
            self.no_content_reads.add(path.resolve())
        validate_identities(paths)
        self.identities.update(paths)
        return "inventory-sha256:" + canonical_hash(records)

    def digest(self, path, expected=None, role="source"):
        path = Path(path).absolute()
        require(path.is_file(), f"Missing completed input: {path}")
        require(path.resolve() not in self.no_content_reads, f"Refusing index/base content read: {path}")
        require(path.stat().st_size <= 128 << 20, f"Refusing unbounded content hash: {path}")
        key = str(path)
        if key not in self.files:
            before = identity(path)
            digest = common.sha256_file(path)
            require(identity(path) == before, f"Input changed while hashing: {path}")
            self.files[key] = dict(sha256=digest, identity=before, roles=[])
        record = self.files[key]
        require(identity(path) == record["identity"], f"Input changed during assembly: {path}")
        if expected is not None:
            require(isinstance(expected, str) and re.fullmatch("[0-9a-f]{64}", expected) and
                    record["sha256"] == expected, f"Registered hash mismatch: {path}")
        if role not in record["roles"]:
            record["roles"].append(role)
        return record["sha256"]

    def read_json(self, path, role="source"):
        self.digest(path, role=role)
        return common.read_json(path)

    def protected(self, records):
        require(isinstance(records, dict) and records, "Missing registered protected hashes")
        for path, expected in records.items():
            self.digest(path, expected, "registered")

    def require_registered(self, path, records):
        path = str(Path(path).absolute())
        require(path in records, f"Input has no registered fingerprint: {path}")
        return self.digest(path, records[path], "registered")

    def verify(self):
        validate_identities(self.identities)
        for path, record in self.files.items():
            require(identity(path) == record["identity"] and
                    common.sha256_file(path) == record["sha256"], f"Input changed during assembly: {path}")


def completed_status(evidence, root, expected, label, *, alternatives=()):
    path = root / "status.json"
    require(path.is_file(), f"Incomplete {label} campaign: missing {path}; wait for the parent")
    status = evidence.read_json(path, "completion")
    require(status == expected or status in alternatives,
            f"Incomplete {label} campaign: {status}; wait for the parent")
    return status


def completed_sptag_status(evidence, root, adaptive_only):
    expected = dict(state="stage_complete", runtime="after", kind="normal")
    path = root / "status.json"
    require(path.is_file(), f"Incomplete SPTAG campaign: missing {path}; wait for the parent")
    status = evidence.read_json(path, "completion")
    if not adaptive_only or status.get("state") != "complete":
        return completed_status(
            evidence, root, expected, "SPTAG",
            alternatives=(dict(state="stage_complete", runtime="after", kind="diagnostic"),)
            if adaptive_only else (),
        )
    require(status == evidence.read_json(root / "completion.json", "completion") and
            status.get("completed_ordinary_points") == 70 and
            status.get("completed_counter_only_points") == 6 and status.get("native_processes") == 2 and
            status.get("search_settings_changed") is False and status.get("phase_timing") is False,
            "Invalid completed adaptive campaign counts/settings")
    ordinary = evidence.read_json(root / "ordinary-completion.json", "ordinary_completion")
    process_status = evidence.read_json(root / "after-normal-status.json", "ordinary_completion")
    validation_flags = {
        "independent_of_counter_stage", "exact_unfiltered_payload_parity", "reverse_pass_payload_parity",
    }
    require({key: value for key, value in ordinary.items() if key not in validation_flags} ==
            {key: value for key, value in process_status.items() if key not in validation_flags} and
            all(key not in process_status or process_status[key] is True for key in validation_flags) and
            ordinary.get("state") == "complete" and ordinary.get("runtime") == "after" and
            ordinary.get("kind") == "normal" and ordinary.get("completed_points") == 70 and
            ordinary.get("independent_of_counter_stage") is True and
            ordinary.get("exact_unfiltered_payload_parity") is True and
            ordinary.get("reverse_pass_payload_parity") is True and
            ordinary.get("report") == str(root / "after-normal-results.json"),
            "Missing or invalid independent ordinary completion")
    evidence.digest(root / "after-normal-results.json", ordinary.get("report_sha256"), "ordinary_completion")
    require(isinstance(ordinary.get("report_sha256"), str) and
            re.fullmatch("[0-9a-f]{64}", ordinary["report_sha256"]) and
            ordinary.get("command") == evidence.read_json(root / "after-normal-command.json", "native_command"),
            "Independent ordinary completion must bind its report and command")
    return ordinary


def completed_resource(evidence, path, command):
    evidence.digest(path, role="completed_resource")
    text = path.read_text()
    require(re.findall(r"^\s*Exit status:\s*(\d+)\s*$", text, re.MULTILINE) == ["0"] and
            not re.search(r"^\s*Command (?:terminated by signal|exited with non-zero status)",
                          text, re.MULTILINE),
            f"Incomplete or failed native resource report: {path}")
    require(re.findall(r'^\s*Command being timed: "(.*)"\s*$', text, re.MULTILINE) ==
            [" ".join(command[4:])], f"Completed resource belongs to another native command: {path}")
    timestamp = path.stat().st_mtime_ns
    completed = datetime.fromtimestamp(timestamp // 1000000000, timezone.utc).replace(
        microsecond=(timestamp % 1000000000) // 1000)
    return dict(path=str(path), completed_at_utc=completed.isoformat(), completion_mtime_ns=str(timestamp),
        time_source="completed GNU time resource file mtime", exit_status=0)


def checked_command(evidence, path, resource, tail):
    command = evidence.read_json(path, "native_command")
    prefix = ["/usr/bin/time", "-v", "-o", str(resource), "numactl",
              "--cpunodebind=3", "--membind=3"]
    require(isinstance(command, list) and command[:7] == prefix and len(command) > 8 and
            Path(command[7]).is_absolute() and command[8:] == tail,
            f"Native command/NUMA protocol mismatch: {path}")
    return command


def check_helper(evidence, root, protected, source):
    frozen = root / "source" / source.name
    digest = evidence.require_registered(frozen, protected)
    evidence.digest(source, digest, "parser_implementation")


def pipe_profile(evidence, b, p, registration, *, reuse_baseline=False):
    candidates = []
    for name in registration["protected"]:
        if Path(name).suffix.lower() == ".ini":
            config = common.Config(name)
            if (config.parser.has_option("Benchmark", "SPTAGCampaign") and
                    config.path_value("Benchmark", "OutputDirectory") == p and
                    (reuse_baseline or config.path_value("Benchmark", "SPTAGCampaign") == b)):
                candidates.append(Path(name))
    require(len(candidates) == 1, "Expected one registered fixed PipeANN selectivity profile")
    evidence.require_registered(candidates[0], registration["protected"])
    return pipe.config_at(candidates[0])


def readonly_descriptor_adaptation(evidence, registration, config):
    record = registration.get("readonly_descriptor_adaptation")
    section = config.section("PipeANN")
    declared = "readonlyadaptationmanifest" in section
    if record is None:
        require(not declared, "Missing registered read-only descriptor adaptation provenance")
        return None
    require(declared and isinstance(record, dict) and
            {"path", "sha256", "verifier", "description", "backend", "allocator"} <= record.keys(),
            "Invalid registered read-only descriptor adaptation")
    path = config.path_value("PipeANN", "ReadOnlyAdaptationManifest")
    require(record["path"] == str(path) and record["sha256"] == section["ReadOnlyAdaptationSHA256"],
            "Read-only descriptor adaptation differs from its registered profile")
    evidence.require_registered(path, registration["protected"])
    evidence.digest(path, record["sha256"], "readonly_descriptor_adaptation")
    manifest = evidence.read_json(path, "readonly_descriptor_adaptation")
    require(isinstance(manifest, dict) and manifest.get("kind") == "pipeann-query-readonly-fds-adaptation" and
            manifest.get("version") == 1 and manifest.get("status") == "complete",
            "Incomplete or unrecognized read-only descriptor adaptation manifest")
    scope, effective = manifest.get("scope"), manifest.get("effective")
    equivalence, validation = manifest.get("flag_equivalence"), manifest.get("validation")
    require(all(isinstance(value, dict) for value in (scope, effective, equivalence, validation)),
            "Missing adaptation scope/flag/validation provenance")
    unchanged = ("algorithms_unchanged", "io_backend_unchanged", "allocator_unchanged",
                 "all_other_authenticated_source_files_unchanged", "frozen_toolchain_unchanged")
    untouched = ("original_source_repositories_modified", "data_or_index_bytes_modified",
                 "existing_benchmark_outputs_modified")
    require(scope.get("query_guard") == "READ_ONLY_TESTS" and
            set(scope.get("changed_native_files", [])) ==
            {"include/filter/attribute.h", "src/utils/linux_aligned_file_reader.cpp"} and
            all(scope.get(key) is True for key in unchanged) and
            all(scope.get(key) is False for key in untouched),
            "Adaptation is not the declared query-file-access-only change")
    require(record["backend"] == effective.get("backend") == "uring" and
            record["allocator"] == effective.get("allocator") == "tcmalloc" and
            {"READ_ONLY_TESTS", "NO_MAPPING", "USE_URING", "USE_TCMALLOC"} <=
            set(effective.get("definitions", [])) and
            equivalence.get("result") == "pass" and
            equivalence.get("compile_commands_identical_after_root_normalization") is True and
            equivalence.get("search_link_commands_identical_after_root_normalization") is True and
            validation.get("fixture_status") == validation.get("functional_smoke_status") == "pass" and
            validation.get("all_native_processes_exited") is True,
            "Adaptation backend/allocator/flag/validation evidence differs")
    original = evidence.read_json(registration["reference_toolchain"]["path"])
    require(manifest.get("original_toolchain") == registration["reference_toolchain"] and
            isinstance(manifest.get("source_revision"), str) and bool(manifest["source_revision"]) and
            manifest.get("source_revision") == registration.get("source_revision") and
            isinstance(manifest.get("original_config_sha256"), str) and
            re.fullmatch("[0-9a-f]{64}", manifest["original_config_sha256"]) and
            manifest.get("original_config_sha256") == original.get("config_sha256"),
            "Adaptation derives from a different registered toolchain")
    binaries = manifest.get("binaries", {})
    require(set(binaries) == set(registration["binaries"]), "Adaptation native binary set differs")
    for name, binary in registration["binaries"].items():
        require(binaries[name].get("sha256") == binary["sha256"] and
                binaries[name].get("path") == binary["origin"],
                f"Adaptation native binary identity differs: {name}")
    require(record["verifier"] == str(path.parent / "validation/verify_toolchain.py"),
            "Adaptation verifier provenance differs")
    evidence.require_registered(record["verifier"], registration["protected"])
    # The parent ran native verification; assembly authenticates that evidence, never reruns it.
    return dict(registration=record, manifest=manifest, verifier_invoked_by_assembler=False)


def checked_workload(evidence, breg, preg, config):
    path = Path(breg["workload"])
    evidence.require_registered(path, breg["protected"])
    evidence.require_registered(path, preg["protected"])
    require(config.path_value("Dataset", "Workloads") == path, "Engines registered different workloads")
    workload = evidence.read_json(path)
    require(workload["corpus_count"] == CORPUS and workload["dimension"] == DIMENSION and
            workload["query_count"] == COUNT and workload["topk"] == TOPK and
            workload["value_type"] == "UInt8" and workload["scenarios"] == SCENARIOS and
            workload["queries"] == breg["queries"], "Authenticated workload/cohort differs")
    evidence.protected(workload["protected"])
    require(workload["protected_large"] == breg["protected_large"] == preg["protected_large"],
            "Engines registered different corpus/attribute identities")
    require(set(workload["scenario_metadata"]) == set(SCENARIOS) and
            [entry["name"] for entry in preg["definitions"]] == SCENARIOS, "Scenario metadata differs")
    metadata, truths, contracts = {}, {}, {}
    for definition in preg["definitions"]:
        name = definition["name"]
        entry, truth = workload["scenario_metadata"][name], workload["truth"][name]
        count, fraction = entry["eligible_count"], entry["selectivity"]
        require(type(count) is int and 0 <= count <= CORPUS and entry["corpus_count"] == CORPUS and
                type(fraction) in (int, float) and math.isfinite(fraction) and
                abs(fraction - count / CORPUS) <= 1e-12 and
                isinstance(entry["title"], str) and entry["title"].strip(),
                f"Invalid authenticated actual density: {name}")
        require(definition["candidate_count"] == truth["candidate_count"] == count and
                definition["selectivity"] == truth["selectivity"] == fraction and
                (name != "unfilter" or fraction == 1), f"Predicate population differs: {name}")
        contract = scenario_contract(config, name)
        contracts[name] = contract
        native = workload["native_predicates"][name]
        if name == "unfilter":
            expected = None
            require(native == dict(kind="empty", file=""), "Unfiltered predicate changed")
        else:
            evidence.require_registered(native["file"], breg["protected"])
            validate_filter_config(config, name, contract)
            section = config.section(f"Scenario.{name}")
            if name != "mixed_dnf":
                tag = section.getint("Tag")
                expected = {"categorical_eq": [0, tag]}
                tags = np.load(native["file"], mmap_mode="r", allow_pickle=False)
                require(native["kind"] == "categorical" and tags.dtype == np.dtype("<u4") and
                        tags.shape == (COUNT, 1) and np.all(tags == tag),
                        f"Native categorical predicate differs: {name}")
            else:
                expected = {"or": [
                    {"categorical_eq": [0, section.getint("RareTag")]},
                    {"and": [{"categorical_eq": [0, section.getint("RegularTag")]},
                             {"numeric_le": [1, section.getint("UpperInclusive")]}]},
                ]}
                require(native["kind"] == "dnf" and dnf_clauses(native["file"], COUNT) == contract["clauses"],
                        "Native mixed DNF differs")
        require(workload["predicates"][name] == expected and definition["predicate"] == contract["predicate"] and
                definition["kind"] == config.section(f"Scenario.{name}")["Kind"],
                f"Authenticated predicates disagree: {name}")
        evidence.require_registered(truth["ids"], breg["protected"])
        evidence.digest(truth["ids"], truth["sha256"], "truth")
        ids = np.load(truth["ids"], mmap_mode="r", allow_pickle=False)
        require(ids.dtype == np.dtype("<i8") and ids.shape == (COUNT, TOPK) and
                np.all((ids >= 0) & (ids < CORPUS)) and
                all(len(set(row)) == TOPK for row in ids), f"Invalid authenticated IDs-only truth: {name}")
        require(definition["truth_npy"] == truth["ids"], f"Truth identity differs: {name}")
        truths[name] = ids
        metadata[name] = dict(title=entry["title"], predicate=definition["predicate"],
                              candidate_count=count, selectivity=fraction)
    return workload, metadata, truths, contracts


def query_identity(evidence, breg, preg, p):
    npy = Path(breg["queries"])
    native = p / "inputs/query.u8bin"
    evidence.require_registered(npy, breg["protected"])
    evidence.require_registered(native, preg["protected"])
    queries = np.load(npy, mmap_mode="r", allow_pickle=False)
    require(queries.dtype == np.dtype("u1") and queries.shape == (COUNT, DIMENSION) and
            native_header(native, 1) == queries.shape, "Query UInt8 type/shape differs")
    native_payload = np.fromfile(native, dtype="u1", offset=8).reshape(queries.shape)
    require(np.array_equal(queries, native_payload), "Ordered logical UInt8 query payload differs across engines")
    digest = hashlib.sha256(queries.tobytes(order="C")).hexdigest()
    return dict(id="sha256:" + digest, logical_payload_sha256=digest, dtype="UInt8",
                shape=[COUNT, DIMENSION], sptag_container=str(npy), pipeann_container=str(native),
                container_hashes=[evidence.digest(npy), evidence.digest(native)])


def checked_sptag_registration(registration, *, adaptive_only=False):
    variants = ADAPTIVE_VARIANTS if adaptive_only else VARIANTS
    require(registration.get("variants", list(VARIANTS)) == list(variants),
            "Unexpected SPTAG variants; adaptive-only requires an explicit registered variant selection")
    base_count = len(SCENARIOS) * len(variants)
    require(registration["dataset"] == "SIFT1B" and registration["mode"] == "selectivity_curve" and
            registration["value_type"] == "UInt8" and registration["head_count"] == 120040156 and
            registration["corpus_query_rows"] == registration["measured_query_rows"] == COUNT and
            registration["scenarios"] == SCENARIOS and registration["grid"] == GRID and
            registration["ordinary_repetitions"] == 2 and
            registration["points_per_process"] == base_count * 2 * len(GRID) and
            registration["cpu_node"] == registration["memory_node"] == 3 and
            registration["schema_version"] == SCHEMA_VERSION and registration["policy_labels"] == POLICIES,
            "Unexpected SPTAG ordinary curve registration")
    cases = registration["cases"]
    require(len(cases) == base_count * 2 and
            [(c["scenario"], c["variant"]) for c in cases[:base_count]] ==
            [(s, v) for s in SCENARIOS for v in variants], "Unexpected SPTAG case order")
    base = [dict(c, case=f"{c['scenario']}_{c['variant']}") for c in cases[:base_count]]
    for case in base:
        case.pop("repetition")
    require(repeat_cases(base, 2) == cases, "SPTAG second pass must reverse cases, not nprobe sweeps")
    for case in cases:
        require(case["queries"] == COUNT and case["probes"] == GRID and case["max_check"] == 2048 and
                case["posting_anchor_count"] == 8 and case["posting_page_limit"] == 3 and
                case["posting_additional_max_check"] == (0 if case["variant"] == "graph" else 2048),
                "SPTAG case budget/grid protocol differs")
    if registration["native_processes"] == 2:
        grouped = registration.get("cases_by_kind", {})
        require(adaptive_only and registration.get("implementation_revision") == "controlled_ascent" and
                registration.get("points_by_kind") == {"normal": 70, "diagnostic": 6} and
                set(grouped) == {"normal", "diagnostic"} and grouped["normal"] == cases,
                "Separate diagnostic process requires the explicit controlled-ascent counter plan")
        counters = grouped["diagnostic"]
        require(isinstance(counters, list) and len(counters) == 2 and
                [case["scenario"] for case in counters] == ["sel_01pct", "mixed_dnf"] and
                all(case.get("counter_only") is True and case["queries"] == 32 and
                    case["probes"] == [16, 96, 384] and case["variant"] == "postgraph_extra" and
                    case["repetition"] == 1 for case in counters),
                "Unexpected separate diagnostic case/query grid")
    else:
        require(registration["native_processes"] == 1, "Unexpected SPTAG native process count")
    return variants


def sptag_points(evidence, b, registration, authorization, workload, truths, *, variants=VARIANTS):
    entry = authorization["runtimes"]["after"]
    require(entry["schema"] == SCHEMA_VERSION and entry["policy"] == POLICIES["after"],
            "SPTAG runtime is not the approved adaptive policy")
    evidence.digest(authorization["approval"], authorization["approval_sha256"], "authorization")
    evidence.digest(entry["provenance"]["path"], entry["provenance"]["sha256"], "authorization")
    provenance = evidence.read_json(entry["provenance"]["path"])
    binary = entry["binaries"]["normal"]
    require(binary["source"] == registration["runtimes"]["after"]["Normal"] and
            binary["path"] == str(b / "runtime/after-normal") and
            provenance["policy_label"] == entry["policy"] and
            provenance["navigation_schema_version"] == SCHEMA_VERSION and provenance["navigation_columns"] == 57 and
            provenance["binary_hashes"] == {key: value["sha256"] for key, value in entry["binaries"].items()},
            "SPTAG approved runtime/provenance differs")
    evidence.digest(binary["path"], binary["sha256"], "runtime")
    command = checked_command(evidence, b / "after-normal-command.json", b / "after-normal.resources.txt",
                              ["-B", str(b / "source/selectivity_common.py"), str(b / "after/normal"),
                               binary["path"], str(b / "configs/after-normal.ini")])
    resource = completed_resource(evidence, b / "after-normal.resources.txt", command)
    batch = read_ini(b / "configs/after-normal.ini")
    case_count = len(SCENARIOS) * len(variants) * 2
    require(batch["Batch"].getint("CaseCount") == case_count, "Wrong native batch case count")
    log = b / "after-normal.log"
    evidence.digest(log, role="native_log")
    events = []
    for line in log.read_text().splitlines():
        require("PhaseTime:" not in line, "SPTAG phase timings must not enter ordinary curves")
        require(not any(marker in line for marker in (":ERROR]", ":FATAL]", "Bad file descriptor")),
                "SPTAG native error diagnostics invalidate ordinary curves")
        if line.startswith(('{"event":', '{"mode":')):
            events.append(json.loads(line))
    expected_events = ["batch_begin", "batch_loaded"]
    for case in registration["cases"]:
        expected_events += ["case_begin"] + ["point"] * len(GRID) + ["case_end"]
    expected_events += ["batch_end"]
    require([e.get("event") for e in events] == expected_events, "Incomplete/out-of-order SPTAG native batch")
    require(events[0]["cases"] == events[-1]["completed_cases"] == case_count and
            events[0].get("phase_timing", False) is False and
            events[0]["value_type"] == "UInt8" and events[0]["index"] == registration["index"] and
            events[0]["queries"] == registration["queries"] and
            events[1]["index_load_count"] == events[1]["query_corpus_load_count"] == 1,
            "SPTAG native batch identity/load count differs")
    report = evidence.read_json(b / "after-normal-results.json", "measured_report")
    expected = [(c["case"], probe) for c in registration["cases"] for probe in GRID]
    require(isinstance(report, list) and [(r["case"], r["nprobe"]) for r in report] == expected,
            "Incomplete, duplicate or reordered SPTAG measured report")
    points, protocols, paired = [], [], {}
    cursor, report_index = 2, 0
    for number, case in enumerate(registration["cases"], 1):
        begin, end = events[cursor], events[cursor + len(GRID) + 1]
        parent = b / "after/normal" / case["case"]
        require(batch[f"Case{number}"]["Config"] == case["config"] and
                batch[f"Case{number}"]["OutputDirectory"] == str(parent), "Native batch config changed")
        for event in (begin, end):
            require(event["case_id"] == f"Case{number}" and event["config"] == case["config"] and
                    event["output_directory"] == str(parent) and event.get("phase_timing", False) is False,
                    "SPTAG native case boundary differs")
        native_predicate = workload["native_predicates"][case["scenario"]]
        config = read_ini(case["config"])
        settings = dict(config["SearchSSDIndex"])
        require(begin["search_settings"] == {key.lower(): value for key, value in settings.items()} and
                settings["NumberOfThreads"] == "1" and settings["ResultNum"] == "10" and
                settings["EnablePostingNavigation"] == ("false" if case["variant"] == "graph" else "true") and
                all(settings[key] == "false" for key in ("LogPhaseTime", "LogPathStats", "EnableHybridDistance")) and
                begin["predicate"] == native_predicate["kind"] and
                begin["predicate_file"] == native_predicate["file"] and
                begin["warmup_queries"] == begin["measured_queries"] == begin["replay_queries"] == COUNT and
                end["completed_points"] == len(GRID), "SPTAG native case settings/window protocol differs")
        for setting, field in (("MaxCheck", "max_check"),
                               ("PostingAdditionalMaxCheck", "posting_additional_max_check"),
                               ("PostingAnchorCount", "posting_anchor_count"),
                               ("SearchPostingPageLimit", "posting_page_limit")):
            require(config["SearchSSDIndex"].getint(setting) == case[field],
                    f"SPTAG frozen INI budget differs: {setting}")
        benchmark = config["Benchmark"]
        require(benchmark["Index"] == registration["index"] and benchmark["Queries"] == registration["queries"] and
                benchmark["ValueType"] == "UInt8" and benchmark["Predicate"] == native_predicate["kind"] and
                benchmark.get("PredicateFile", "") == native_predicate["file"] and
                benchmark.getint("MaxQueries") == benchmark.getint("Warmup") == COUNT and
                json.loads(config["SearchSweep"]["NProbe"]) == GRID, "SPTAG frozen case INI differs")
        protocols.append(dict(case=case, native_begin=begin, native_end=end))
        for offset, probe in enumerate(GRID, 1):
            native, row = events[cursor + offset], report[report_index]
            require(all(row.get(key) == value for key, value in native.items()) and
                    all(row.get(key) == value for key, value in case.items()),
                    "SPTAG report differs from native log/registered case")
            require(row["runtime"] == "after" and row["kind"] == "normal" and row["policy"] == entry["policy"] and
                    row["case_id"] == f"Case{number}" and row["output_directory"] == str(parent) and
                    row["nprobe"] == probe and row["diagnostic"] is False and row["phase_timing"] is False and
                    row["navigation_schema_version"] == SCHEMA_VERSION and row["navigation_columns"] == 57 and
                    row["queries"] == row["warmup_queries"] == row["measured_queries"] == row["replay_queries"] == COUNT and
                    row["value_type"] == "UInt8" and row["mode"] == ("graph" if case["variant"] == "graph" else "posting") and
                    row["search_posting_page_limit"] == 3 and
                    all(row[key] == case[key] for key in
                        ("max_check", "posting_additional_max_check", "posting_anchor_count")),
                    "SPTAG ordinary native point/window protocol differs")
            if "implementation_revision" in registration:
                require(row.get("implementation_revision") == registration["implementation_revision"],
                        "SPTAG measured implementation revision differs")
            folder = parent / f"nprobe_{probe}"
            require(set(row["payload_hashes"]) == set(PAYLOADS), "Missing SPTAG replay payload hashes")
            sizes = {"ids.i32": 40000, "dist.f32": 40000, "work.u64": 64000, "latency_us.f64": 8000}
            for name, digest in {**row["payload_hashes"], "latency_us.f64": row["latency_sha256"]}.items():
                require((folder / name).is_file() and (folder / name).stat().st_size == sizes[name],
                        f"Incomplete SPTAG raw payload: {folder / name}")
                evidence.digest(folder / name, digest, "raw_payload")
            ids = np.fromfile(folder / "ids.i32", dtype="<i4").reshape(COUNT, TOPK)
            distances = np.fromfile(folder / "dist.f32", dtype="<f4").reshape(COUNT, TOPK)
            work = np.fromfile(folder / "work.u64", dtype="<u8").reshape(COUNT, 8)
            latency = np.fromfile(folder / "latency_us.f64", dtype="<f8")
            valid = ids >= 0
            require(np.all(ids >= -1) and np.all(ids[valid] < CORPUS) and
                    not np.any((~valid[:, :-1]) & valid[:, 1:]) and
                    all(len(set(r[r >= 0])) == np.count_nonzero(r >= 0) for r in ids) and
                    np.all(np.isfinite(distances[valid])) and np.all(distances[valid] >= 0) and
                    not np.any((distances[:, 1:] < distances[:, :-1]) & valid[:, 1:]),
                    "Invalid SPTAG result ID/distance payload")
            hits = sum(len(set(result[result >= 0]) & set(truth))
                       for result, truth in zip(ids, truths[case["scenario"]]))
            require(row["recall"] == hits / (COUNT * TOPK) and
                    row["underfilled_queries"] == int(np.count_nonzero(valid.sum(axis=1) < TOPK)) and
                    row["empty_queries"] == int(np.count_nonzero(valid.sum(axis=1) == 0)) and
                    row["mean_returned"] == float(valid.sum(axis=1).mean()) and
                    row["mean_ssd_work"] == work.mean(axis=0).tolist(), "SPTAG replay report differs from raw payloads")
            require(np.all(np.isfinite(latency)) and np.all(latency > 0) and
                    type(row["qps"]) in (int, float) and math.isfinite(row["qps"]) and row["qps"] > 0 and
                    type(row["mean_ms"]) in (int, float) and math.isfinite(row["mean_ms"]) and row["mean_ms"] > 0 and
                    math.isclose(row["qps"] * row["mean_ms"], 1000, rel_tol=1e-8),
                    "SPTAG whole-window QPS/elapsed timing is invalid")
            # Whole-window timing also includes work outside the per-query clocks.
            require(float(latency.mean()) / 1000 <= row["mean_ms"] + max(1e-9, row["mean_ms"] * 1e-8),
                    "SPTAG per-query latency exceeds the whole measured window")
            ordered_latency = np.sort(latency)
            for field, fraction in (("p50_us", .50), ("p95_us", .95), ("p99_us", .99)):
                require(type(row[field]) in (int, float) and math.isclose(
                    row[field], float(ordered_latency[math.ceil(fraction * COUNT) - 1]), rel_tol=1e-8, abs_tol=1e-9),
                    f"SPTAG latency percentile differs from payload: {field}")
            key = (case["scenario"], case["variant"], probe)
            if key in paired:
                require(row["payload_hashes"] == paired[key], "SPTAG repetitions changed IDs/distances/SSD work")
            else:
                paired[key] = row["payload_hashes"]
            points.append(dict(scenario=case["scenario"], engine=ENGINE[case["variant"]], L=probe,
                               repetition=case["repetition"], queries=COUNT, recall=row["recall"], qps=row["qps"],
                               source=str(b / "after-normal-results.json"), source_row=report_index,
                               source_record_sha256=canonical_hash(row), payload_hashes=row["payload_hashes"],
                               latency_sha256=row["latency_sha256"]))
            report_index += 1
        cursor += len(GRID) + 2
    if "graph" in variants:
        for probe in GRID:
            require(paired["unfilter", "graph", probe] == paired["unfilter", "postgraph_extra", probe],
                    "Unfiltered SPTAG control changed IDs/distances/SSD work")
    return points, dict(command=command, cases=protocols, resource=resource,
                        policy=entry["policy"], provenance=entry["provenance"],
                        runtime_sha256=binary["sha256"],
                        timing="Native QPS uses whole measured-window elapsed, including loop/result-release overhead; not inverse mean per-query latency",
                        report_timing_flag="Legacy timing_accepted is not the stage selector; only completed after/normal is used",
                        replay_validation="IDs, distances, SSD work and GT-ID recall; no base/attribute payload scan")


def pipe_points(evidence, p, registration, config, truths):
    require(registration["query_count"] == registration["warmup_queries"] == COUNT and
            registration["repeats"] == 2 and registration["threads"] == 1 and
            registration["cpu_nodes"] == registration["memory_nodes"] == 3 and
            registration["corpus_count"] == CORPUS and registration["scenario_count"] == 5 and
            registration["points_per_scenario"] == 26 and registration["read_only"] is True and
            registration["no_mapping"] is True and registration["historical_timings_used"] is False and
            registration["memory_validation"] == "complete", "Unexpected PipeANN ordinary curve registration")
    require(set(registration["binaries"]) == {"search_disk_index", "search_disk_index_filtered"},
            "Expected the two verified native PipeANN binaries")
    for binary in registration["binaries"].values():
        evidence.require_registered(binary["path"], registration["protected"])
        evidence.digest(binary["path"], binary["sha256"], "runtime")
    for definition in registration["definitions"]:
        name = definition["name"]
        require(definition["truth_bin"] == f"gt_{name}.ibin", "Unexpected native truth filename")
        path = p / "inputs" / definition["truth_bin"]
        evidence.require_registered(path, registration["protected"])
        require(native_header(path, 4) == (COUNT, TOPK) and
                np.array_equal(np.fromfile(path, dtype="<u4", offset=8).reshape(COUNT, TOPK), truths[name]),
                f"PipeANN IDs-only truth payload differs: {name}")
    expected_jobs = pipe.native_jobs(config, registration["definitions"], registration["binaries"], p)
    require(registration["jobs"] == expected_jobs, "PipeANN registered jobs/control order differ")
    all_rows, protocols, resources = [], [], []
    for job in registration["jobs"]:
        name = job["scenario"]
        expected_points = [dict(point, L=point["value"]) for point in sequence(pipe.LS, 2, warmup_pair=True)]
        require(job["points"] == expected_points, "PipeANN warm/measured reverse-L passes differ")
        log, resource_path = p / "logs" / f"{name}.log", p / "logs" / f"{name}.resources.txt"
        command = checked_command(evidence, p / name / "command.json", resource_path,
                                  ["-B", str(p / "source/run_pipeann_selectivity.py"), "native", str(p / name)] +
                                  job["command"])
        resource = completed_resource(evidence, resource_path, command)
        evidence.digest(log, role="native_log")
        parsed = pipe.validated_results(log, job)
        require(all(type(row["native"][key]) in (int, float)
                    for row in parsed for key in ("recall_percent", "qps")),
                f"Invalid PipeANN native numeric fields: {name}")
        require(parsed == evidence.read_json(p / name / "results.json", "native_report"),
                f"PipeANN scenario report differs from RESULT lines: {name}")
        all_rows.extend(parsed)
        protocols.append(dict(job=job, command=command, resource=resource))
        resources.append(resource)
    require(len(all_rows) == 260 and all_rows == evidence.read_json(p / "native-results.json", "native_report"),
            "PipeANN native-results report differs from RESULT lines")
    measured = [row for row in all_rows if row["stage"] == "measured"]
    require(len(measured) == 130 and measured == evidence.read_json(p / "results.json", "measured_report"),
            "PipeANN measured report is incomplete or includes warmups")
    points = [dict(scenario=row["scenario"], engine="PipeANN", L=row["L"], repetition=row["repeat"],
                   queries=COUNT, recall=row["recall"], qps=row["qps"], source=str(p / "results.json"),
                   source_row=i, source_record_sha256=canonical_hash(row))
              for i, row in enumerate(measured)]
    return points, dict(jobs=protocols, resources=resources, warmup_rows=130, measured_rows=130,
                        runtime_binaries=registration["binaries"], replay_queries=0,
                        ordering="Each L warmed then measured; second pass reverses L")


def aggregate(points, metadata, *, engines=ENGINE_ORDER):
    expected = [(scenario, engine, control) for scenario in SCENARIOS for engine in engines
                for control in (pipe.LS if engine == "PipeANN" else GRID)]
    groups = {}
    for point in points:
        key = point["scenario"], point["engine"], point["L"]
        require(key in expected, f"Unregistered measured point: {key}")
        require(point["queries"] == COUNT and all(type(point[key]) in (int, float) and
                math.isfinite(point[key]) for key in ("recall", "qps")) and
                0 <= point["recall"] <= 1 and point["qps"] > 0, "Invalid ordinary measurement")
        groups.setdefault(key, []).append(point)
    require(set(groups) == set(expected), "Incomplete combined native-control matrix")
    rows = []
    for scenario, engine, control in expected:
        group = groups[scenario, engine, control]
        require(len(group) == 2 and {row["repetition"] for row in group} == {1, 2},
                f"Incomplete or duplicate measured repetitions: {scenario}/{engine}/{control}")
        row = dict(scenario=scenario, engine=engine, L=control, queries=COUNT, repeats=2, threads=1, cpu_nodes="3")
        for metric in ("recall", "qps"):
            values = [point[metric] for point in group]
            row.update({metric: statistics.median(values), metric + "_min": min(values), metric + "_max": max(values)})
        row.update({field: metadata[scenario][field] for field in ("candidate_count", "selectivity", "predicate")})
        rows.append(row)
    return rows


def assemble(sptag, pipeann, *, completed=False, adaptive_only=False,
             reuse_pipeann_baseline=False, output_directory=None):
    require(completed, "Parent must declare both native campaigns complete with --completed")
    b, p = Path(sptag).resolve(), Path(pipeann).resolve()
    require(not reuse_pipeann_baseline or output_directory is not None,
            "Reusing PipeANN requires an explicit separate output directory")
    requested_output = Path(output_directory).absolute() if output_directory is not None else p / "plot_input"
    require(not requested_output.exists() and not requested_output.is_symlink(),
            f"Refusing to overwrite assembly: {requested_output}")
    output = requested_output.resolve()
    require(b.is_dir() and p.is_dir() and b != p, "Expected two distinct existing campaign directories")
    require(not output.exists() and not output.is_symlink(), f"Refusing to overwrite assembly: {output}")
    require(not reuse_pipeann_baseline or
            (not output.is_relative_to(b) and not output.is_relative_to(p)),
            "Reused-baseline publication must be outside both immutable source campaigns")
    evidence = Evidence()
    bstatus = completed_sptag_status(evidence, b, adaptive_only)
    completed_status(evidence, p, dict(state="complete", native_processes=5, measured_points=130), "PipeANN")
    breg, preg = (evidence.read_json(root / "registration.json", "registration") for root in (b, p))
    variants = checked_sptag_registration(breg, adaptive_only=adaptive_only)
    require((bstatus["kind"] != "diagnostic" and bstatus["state"] != "complete") or
            breg["native_processes"] == 2,
            "Combined/diagnostic completion requires a separately registered counter process")
    engine_order = tuple(engine for engine in ENGINE_ORDER
                         if engine == "PipeANN" or engine in {ENGINE[variant] for variant in variants})
    require(not output.is_relative_to(Path(breg["index"]).resolve()),
            "Publication must not be inside the immutable SPTAG index")
    index_ids = {
        "SPTAG": evidence.inventory(breg["index_files"], breg["index"]),
        "PipeANN": evidence.inventory(preg["index_files"]),
    }
    evidence.inventory(breg["protected_large"])
    evidence.inventory(preg["protected_large"])
    evidence.protected(breg["protected"])
    evidence.protected(preg["protected"])
    for name in ("reference_toolchain", "reference_memory"):
        record = preg[name]
        evidence.digest(record["path"], record["sha256"], name)
        common.verify_identities([record])
    for alias, target in preg["alias_targets"].items():
        require((p / "inputs" / alias).is_symlink() and
                (p / "inputs" / alias).resolve() == Path(target), f"Registered attribute alias changed: {alias}")
        evidence.inventory({str(p / "inputs" / alias): identity(p / "inputs" / alias)})
    for name in ("run_sift1b_official.py", "official_benchmark_config.py", "sift1b_official_inputs.py"):
        check_helper(evidence, p, preg["protected"], HERE / name)
    # Controller-only error handling may improve without rewriting the frozen native launcher.
    evidence.digest(HERE / "run_pipeann_selectivity.py", role="job_and_error_validator")
    for name in ("run_adaptive_frontier.py", "run_full.py", "selectivity_common.py"):
        check_helper(evidence, b, breg["protected"], pipe.SHARED / name)
    config = pipe_profile(evidence, b, p, preg, reuse_baseline=reuse_pipeann_baseline)
    adaptation = readonly_descriptor_adaptation(evidence, preg, config)
    workload, metadata, truths, _ = checked_workload(evidence, breg, preg, config)
    cohort = query_identity(evidence, breg, preg, p)
    authorization = evidence.read_json(b / "authorization.json", "authorization")
    sptag_rows, sptag_protocol = sptag_points(
        evidence, b, breg, authorization, workload, truths, variants=variants)
    pipe_rows, pipe_protocol = pipe_points(evidence, p, preg, config, truths)
    pipe_protocol["measurement_reused"] = reuse_pipeann_baseline
    pipe_protocol["original_sptag_campaign"] = str(config.path_value("Benchmark", "SPTAGCampaign"))
    if adaptation is not None:
        pipe_protocol["readonly_descriptor_adaptation"] = adaptation
    points = sptag_rows + pipe_rows
    rows = aggregate(points, metadata, engines=engine_order)
    expected_points = len(SCENARIOS) * (len(variants) * len(GRID) + len(pipe.LS))
    require(len(rows) == expected_points and len(points) == expected_points * 2,
            "Incomplete combined curve totals")
    runtime_ids = {
        "SPTAG": "sha256:" + sptag_protocol["runtime_sha256"],
        "PipeANN": "sha256:" + canonical_hash({key: value["sha256"] for key, value in preg["binaries"].items()}),
    }
    engines = {}
    for engine in engine_order:
        spann = engine != "PipeANN"
        resources = [sptag_protocol["resource"]] if spann else pipe_protocol["resources"]
        completed_at = max(resource["completed_at_utc"] for resource in resources)
        controls = dict(graph_maxcheck=2048, posting_additional_maxcheck=2048 if engine == "SPTAG_adaptive" else 0,
                        posting_anchor_count=8, search_posting_page_limit=3,
                        enable_posting_navigation=engine == "SPTAG_adaptive") if spann else dict(
                            pipeline=32, unfiltered_mem_L=10, filtered_mem_L=0, search_mode=2,
                            filter_mode="auto", neighbor_type="pq", read_only=True, no_mapping=True)
        engines[engine] = dict(
            native_control="nprobe" if spann else "searchL", L=GRID if spann else pipe.LS,
            repeats=2, queries=COUNT, threads=1, cpu_nodes="3", memory_nodes="3",
            query_cohort_id=cohort["id"], index_id=index_ids["SPTAG" if spann else "PipeANN"],
            runtime_id=runtime_ids["SPTAG" if spann else "PipeANN"],
            source_date=completed_at[:10], source_completed_at_utc=completed_at,
            source_campaign=str(b if spann else p), measurement_reused=not spann and reuse_pipeann_baseline,
            io_mode="buffered" if spann else "direct", qps_aggregation="median",
            search_policy=("predicate_first_adaptive_global_posting_frontier" if engine == "SPTAG_adaptive"
                           else "h1_only" if spann else "official_search_disk_index"),
            controls=controls, native_case_protocol=dict(
                warmup_queries=COUNT, measured_queries=COUNT, replay_queries=COUNT if spann else 0,
                order=breg["ordinary_timing"] if spann else pipe_protocol["ordering"],
                timing=sptag_protocol["timing"] if spann else "Native RESULT QPS from measured passes only",
                completed_resources=resources, source_protocol_manifest="assembly_manifest.json",
                native_policy_label=sptag_protocol["policy"] if spann else "official_search_disk_index"),
        )
        if spann:
            engines[engine]["index_h1_count"] = breg["head_count"]
            if "implementation_revision" in breg:
                engines[engine]["implementation_revision"] = breg["implementation_revision"]
        elif adaptation is not None:
            engines[engine]["readonly_descriptor_adaptation"] = adaptation["registration"]
            engines[engine]["native_case_protocol"]["readonly_descriptor_scope"] = adaptation["manifest"]["scope"]
            engines[engine]["controls"].update(backend="uring", allocator="tcmalloc")
    registration = dict(
        schema_version=1, dataset="SIFT1B", corpus_count=CORPUS,
        comparison="reused_pipeann_baseline" if reuse_pipeann_baseline else "fresh_paired",
        series="adaptive_only" if adaptive_only else "with_h1_control",
        scenarios=SCENARIOS, scenario_metadata=metadata, engines=engines,
        assembly_manifest="assembly_manifest.json",
    )
    if adaptation is not None:
        registration["caption_note"] = READONLY_FD_CAPTION
    evidence.digest(Path(__file__), role="assembler")
    evidence.digest(HERE / "plot_sift1b_official.R", role="renderer_schema")
    evidence.verify()
    output.mkdir(parents=True)
    success = False
    try:
        with (output / "summary.csv").open("x", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        common.write_json(output / "plot_registration.json", registration)
        evidence.verify()
        manifest = dict(
            schema_version=1, parent_declared_complete=True, historical_timings_used=reuse_pipeann_baseline,
            reused_measurement_engines=["PipeANN"] if reuse_pipeann_baseline else [],
            renderer_invoked=False, native_execution=False, query_cohort=cohort,
            validated_counts=dict(sptag_measured=len(sptag_rows), pipeann_measured=130, pipeann_warmups_excluded=130,
                                  measured_repetitions=len(points), plotted_points=len(rows)),
            source_files=evidence.files, stat_only_identities=evidence.identities,
            protocols=dict(SPTAG=sptag_protocol, PipeANN=pipe_protocol),
            source_completion=dict(SPTAG=evidence.read_json(b / "status.json", "completion"),
                                   PipeANN=evidence.read_json(p / "status.json", "completion")),
            source_registrations=dict(SPTAG=breg, PipeANN=preg), authorization=authorization,
            measured_records=points,
            checks=dict(sptag_repetition_payload_equality=True,
                        unfiltered_sptag_control_payload_equality=True if "graph" in variants else None,
                        pipeann_RESULT_report_equality=True, identical_logical_query_payload=True,
                        identical_ids_only_truth=True, index_or_base_content_scanned=False),
            output_sha256={name: common.sha256_file(output / name) for name in ("summary.csv", "plot_registration.json")},
            io_comparison="SPTAG buffered versus PipeANN direct; not matched I/O",
        )
        common.write_json(output / "assembly_manifest.json", manifest)
        evidence.verify()
        success = True
    finally:
        if not success:
            for name in ("summary.csv", "plot_registration.json", "assembly_manifest.json",
                         "plot_registration.json.tmp", "assembly_manifest.json.tmp"):
                (output / name).unlink(missing_ok=True)
            if not any(output.iterdir()):
                output.rmdir()
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sptag_campaign", type=Path)
    parser.add_argument("pipeann_campaign", type=Path)
    parser.add_argument("--completed", action="store_true", required=True,
                        help="parent confirms both native campaigns have completed")
    parser.add_argument("--adaptive-only", action="store_true",
                        help="require a complete explicitly registered adaptive-only SPTAG campaign")
    parser.add_argument("--reuse-pipeann-baseline", action="store_true",
                        help="reuse an authenticated PipeANN campaign with matching workload and placement")
    parser.add_argument("--output-directory", type=Path,
                        help="new plot-input directory; required outside both campaigns when reusing PipeANN")
    args = parser.parse_args()
    try:
        output = assemble(args.sptag_campaign, args.pipeann_campaign, completed=args.completed,
                          adaptive_only=args.adaptive_only, reuse_pipeann_baseline=args.reuse_pipeann_baseline,
                          output_directory=args.output_directory)
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(2, f"Assembly refused: {error}\n")
    counts = common.read_json(output / "assembly_manifest.json")["validated_counts"]
    print(f"Assembled {counts['plotted_points']} points from {counts['measured_repetitions']} "
          f"ordinary measured repetitions in {output}; no rendering performed")


if __name__ == "__main__":
    main()
