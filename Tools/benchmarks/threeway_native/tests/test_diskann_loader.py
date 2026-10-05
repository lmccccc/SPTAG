#!/usr/bin/env python3
"""Retain bounded original/fixed native equivalence and loader-policy evidence.

Uses only the accepted 16,783-vector fixture and its frozen positive/negative
cases. No graph builder, production index, parent controller or parent validator
is imported or executed. Every output directory must be fresh.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import struct
import subprocess
import sys
import time


HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[3]
TC = ROOT / "datasets/sift1b/toolchains/threeway_native_20260929"
ACCEPTED = TC / "provenance"
FIXTURE = ROOT / "DiskANN/build/categorical-reuse-smoke-filtered-only"
sys.path.insert(0, str(HERE))
from build_diskann_client import artifact, sha256, verify_inputs_unchanged
from loader_policy import authenticate


def save(path: Path, value) -> None:
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o444)


def write(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)


def ini(path: Path):
    result = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
    result.read(path)
    return result


def replace(text: str, key: str, value: str) -> str:
    result, count = re.subn(r"(?m)^" + re.escape(key) + r"\s*=[^\r\n]*",
                            lambda _: f"{key}={value}", text)
    assert count == 1, (key, count)
    return result


def markers(path: Path, prefix: str):
    return [json.loads(line[len(prefix):]) for line in path.read_text().splitlines() if line.startswith(prefix)]


class Validation:
    def __init__(self, publication: Path, output: Path):
        self.publication = publication
        self.output = output
        self.policy = publication / "policy.ini"
        self.commands = []
        self.proof = json.loads((publication / "provenance/loader-proof.json").read_text())
        self.binding = json.loads((publication / "bin/diskannBuildAdmission.build.json").read_text())["loader_policy"]
        bench = json.loads((publication / "bin/diskannBench.build.json").read_text())
        assert bench["loader_policy"] == self.binding
        self.protected = json.loads((publication / "provenance/protected-before.json").read_text())
        for path in FIXTURE.glob("sift1b*"):
            if path.is_file():
                assert path.stat().st_size <= 64 * 1024 * 1024, "Never hash/load a production fixture"
                self.protected.append(artifact(path))
        for name in ("fixture.u8bin", "fixture_attrs.u32", "validation_queries.u8bin"):
            self.protected.append(artifact(FIXTURE / name))
        with (FIXTURE / "fixture.u8bin").open("rb") as stream:
            self.rows, self.dimension = struct.unpack("<II", stream.read(8))
        assert (self.rows, self.dimension) == (16783, 128), "Only the accepted tiny fixture is permitted"
        assert set(self.binding) == {"mode", "path", "sha256", "proof", "proof_sha256", "library", "stock_binary"}
        assert all(type(value) is str for value in self.binding.values())
        assert self.binding["library"] == self.proof["patched_library"]["path"]
        assert self.binding["stock_binary"] == self.proof["patched_stock_binary"]["path"]

    def run(self, command, directory: Path, name: str, *, env=None, timeout=120):
        log = directory / (name + ".log")
        started = time.monotonic()
        with log.open("x") as stream:
            child = subprocess.Popen(list(map(str, command)), cwd=directory,
                                     stdout=stream, stderr=subprocess.STDOUT, env=env)
            try:
                status = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
                raise
        record = {"argv": list(map(str, command)), "cwd": str(directory), "pid": child.pid,
                  "exit_code": status, "elapsed_seconds": time.monotonic() - started,
                  "log": artifact(log)}
        self.commands.append(record)
        return record

    def admission(self):
        output = self.output / "admission"
        output.mkdir()
        old = json.loads((ACCEPTED / "diskann-build-admission-published-001/tests.json").read_text())
        cases, primary = [], None
        for case in old["cases"]:
            directory = output / case["name"]
            directory.mkdir()
            observations = {}
            for variant, binary in (("original", TC / "bin/diskannBuildAdmission"),
                                    ("fixed", self.publication / "bin/diskannBuildAdmission")):
                certificate = directory / (variant + ".json")
                command = [binary, "--config", case["config"], "--certificate", certificate]
                if variant == "fixed":
                    command += ["--loader-policy", self.policy]
                env = dict(os.environ)
                if case["name"] == "environment-override-rejected":
                    env["OMP_NUM_THREADS"] = "1"
                execution = self.run(command, directory, variant, env=env)
                record = json.loads(certificate.read_text())
                assert record["pid"] == execution["pid"]
                assert record["status"] == case["status"]
                assert (execution["exit_code"] == 0) == (record["status"] == "admitted")
                assert record["native_search_invocations"] == record["warmup_queries"] == record["measured_queries"] == 0
                assert certificate.stat().st_mode & 0o222 == 0
                if record["status"] == "admitted":
                    assert record["native_index_loaded"] and record["converted_labels_verified"]
                    assert record["vector_count"] == self.rows and record["dimension"] == self.dimension
                    assert len(record["selected_labels"]) == len(record["admission"]["witnesses"]) == 201
                    assert all(row["source_count"] == row["native_count"] for row in record["selected_labels"])
                    assert record["admission"]["temporary_membership_released"]
                    if variant == "fixed":
                        assert record["loader_policy"] == self.binding
                        assert record["stock_search_binary"] == self.binding["stock_binary"]
                        assert record["linked_library"] == self.binding["library"]
                    else:
                        assert "loader_policy" not in record
                else:
                    assert not record["native_index_loaded"]
                    assert "Loaded PQ centroids" not in Path(execution["log"]["path"]).read_text()
                observations[variant] = {"execution": execution, "certificate": artifact(certificate),
                                         "record": record}
            original, fixed = (observations[name]["record"] for name in ("original", "fixed"))
            for key in ("status", "error", "selected_labels", "configured_labels", "skipped_below_k",
                        "validation_k", "validation_l", "validation_threads", "planned_stock_query_calls",
                        "input_identities", "native_index_loaded", "converted_labels_verified"):
                assert original[key] == fixed[key], (case["name"], key)
            if original["admission"] is not None:
                for key in ("witnesses", "label_rows", "label_bytes", "label_values",
                            "temporary_membership_bytes", "graph_records_read"):
                    if key in original["admission"]:
                        assert original["admission"][key] == fixed["admission"][key], (case["name"], key)
            cases.append({"name": case["name"], "status": fixed["status"], "error": fixed["error"],
                          "original": {k: v for k, v in observations["original"].items() if k != "record"},
                          "fixed": {k: v for k, v in observations["fixed"].items() if k != "record"},
                          "label_selection_counts_witnesses_equal": True})
            if case["name"] == "all201-full-k":
                primary = fixed
                primary_config = Path(case["config"])
                fixed_command = observations["fixed"]["execution"]["argv"]
                prior = artifact(Path(observations["fixed"]["certificate"]["path"]))
                repeated = self.run(fixed_command, directory, "fresh-certificate-refusal")
                assert repeated["exit_code"] != 0 and "fresh" in Path(repeated["log"]["path"]).read_text()
                assert prior == artifact(Path(prior["path"]))
        assert primary and primary["planned_stock_query_calls"] == 261
        policy_cases = []
        for name, suffix in (
            ("missing-loader-policy", []),
            ("relative-loader-policy", ["--loader-policy", "policy.ini"]),
            ("different-loader-policy", ["--loader-policy", str(primary_config)]),
        ):
            certificate = output / (name + ".json")
            execution = self.run([self.publication / "bin/diskannBuildAdmission", "--config", primary_config,
                                  "--certificate", certificate, *suffix], output, name)
            record = json.loads(certificate.read_text())
            assert execution["exit_code"] != 0 and record["status"] == "rejected"
            assert not record["native_index_loaded"] and record["native_search_invocations"] == 0
            assert "exact absolute --loader-policy" in record["error"]
            policy_cases.append({"name": name, "execution": execution, "certificate": artifact(certificate)})
        certificate = output / "original-rejects-policy.json"
        execution = self.run([TC / "bin/diskannBuildAdmission", "--config", primary_config,
                              "--certificate", certificate, "--loader-policy", self.policy],
                             output, "original-rejects-policy")
        assert execution["exit_code"] != 0 and not certificate.exists()
        report = {"status": "passed", "cases": cases, "selected_labels": 201,
                  "all201_certificate": cases[0]["fixed"]["certificate"],
                  "all201_original_certificate": cases[0]["original"]["certificate"],
                  "planned_stock_query_calls": 261, "policy_rejections": policy_cases,
                  "original_binary_rejects_new_policy": execution,
                  "native_search_invocations": 0, "production_loaded": False}
        save(output / "report.json", report)
        return report

    def benchmark(self):
        output = self.output / "benchmark"
        output.mkdir()
        old = json.loads((ACCEPTED / "diskann-shared-guard-regression-001/admission-tests.json").read_text())
        accepted = json.loads((ACCEPTED / "diskann-shared-guard-published-001/admission-tests.json").read_text())
        reports, captures, paired_values = [], [], 0
        for case in old["cases"]:
            outcomes = {}
            for variant, binary in (("original", TC / "bin/diskannBench"),
                                    ("fixed", self.publication / "bin/diskannBench")):
                directory = output / case["case"] / variant
                directory.mkdir(parents=True)
                results = directory / "results"
                (results / "plans").mkdir(parents=True)
                base_profile = Path(next(iter(case["phases"].values()))["profile"])
                text = replace(base_profile.read_text(), "OutputDirectory", str(results))
                text = replace(text, "BenchmarkBinary", str(binary))
                if variant == "fixed":
                    assert "LoaderPolicy=" not in text
                    text = text.replace("[DiskANN]\n", "[DiskANN]\nLoaderPolicy=" + str(self.policy) + "\n")
                config = directory / "config/benchmark.ini"
                config.parent.mkdir()
                write(config, text.encode())
                phase_results = {}
                for phase, baseline in case["phases"].items():
                    plan = results / "plans" / f"Filtered_DiskANN.{phase}.tsv"
                    write(plan, Path(baseline["plan"]).read_bytes())
                    execution = self.run([binary, config, phase], directory, phase)
                    native = results / "native/Filtered_DiskANN" / phase
                    admission = json.loads((native / "diskann-admission.json").read_text())
                    assert admission["pid"] == execution["pid"] and admission["status"] == case["expected"]
                    assert admission["native_search_invocations"] == admission["warmup_queries"] == admission["measured_queries"] == 0
                    assert admission["temporary_membership_released"]
                    if case["expected"] == "admitted":
                        assert execution["exit_code"] == 0
                        completion = json.loads((native / "completion.json").read_text())
                        assert completion["completed_jobs"] == completion["job_count"] == baseline["jobs"]
                        rows = markers(Path(execution["log"]["path"]), "THREEWAY_RESULT ")
                        assert len(rows) == baseline["jobs"] and all(row["invalid_queries"] == 0 for row in rows)
                        assert admission["native_index_loaded"]
                        for path in sorted(native.glob("*.ids.u32bin")):
                            old_path = Path(accepted["cases"][0]["phases"][phase]["output_directory"]) / path.name
                            assert path.read_bytes() == old_path.read_bytes(), (variant, phase, path.name)
                            distance = path.with_name(path.name.replace(".ids.u32bin", ".distances.f32bin"))
                            old_distance = old_path.with_name(old_path.name.replace(".ids.u32bin", ".distances.f32bin"))
                            assert distance.read_bytes() == old_distance.read_bytes(), (variant, phase, distance.name)
                            if variant == "fixed":
                                capture_rows, k = struct.unpack("<II", path.read_bytes()[:8])
                                assert (capture_rows, k) == (8, 10)
                                paired_values += capture_rows * k
                                captures.append({"phase": phase, "ids": artifact(path), "distances": artifact(distance),
                                                 "accepted_ids": artifact(old_path),
                                                 "accepted_distances": artifact(old_distance),
                                                 "id_distance_pairs": capture_rows * k})
                    else:
                        assert execution["exit_code"] != 0 and not admission["native_index_loaded"]
                        assert not (native / "ready.json").exists() and not (native / "completion.json").exists()
                        assert not list(native.glob("*.ids.u32bin"))
                        assert not markers(Path(execution["log"]["path"]), "THREEWAY_RESULT ")
                    phase_results[phase] = {"execution": execution, "admission": artifact(native / "diskann-admission.json"),
                                            "record": admission, "output_directory": str(native)}
                outcomes[variant] = phase_results
            for phase in case["phases"]:
                original = outcomes["original"][phase]["record"]
                fixed = outcomes["fixed"][phase]["record"]
                assert original["status"] == fixed["status"] and original["error"] == fixed["error"]
                assert original["witnesses"] == fixed["witnesses"]
                if case["expected"] == "admitted":
                    original_dir = Path(outcomes["original"][phase]["output_directory"])
                    fixed_dir = Path(outcomes["fixed"][phase]["output_directory"])
                    for path in original_dir.glob("*.u32bin"):
                        assert path.read_bytes() == (fixed_dir / path.name).read_bytes()
                    for path in original_dir.glob("*.f32bin"):
                        assert path.read_bytes() == (fixed_dir / path.name).read_bytes()
            reports.append({"case": case["case"], "expected": case["expected"],
                            "variants": {variant: {phase: {k: v for k, v in data.items() if k != "record"}
                                                   for phase, data in phases.items()}
                                         for variant, phases in outcomes.items()}})
        assert len(captures) == 48 and paired_values == 3840
        report = {"status": "passed", "cases": reports, "jobs_per_variant": 24, "capture_pairs": 48,
                  "exact_id_distance_pairs": paired_values, "captures": captures,
                  "live_original_fixed_equal": True, "accepted_captures_equal": True, "production_loaded": False}
        save(output / "report.json", report)
        return report

    def stock(self):
        output = self.output / "stock"
        output.mkdir()
        original_certificate = json.loads(
            (ACCEPTED / "diskann-build-admission-published-001/cases/all201-full-k/admission.json").read_text())
        config = ini(Path(original_certificate["config"]))
        k, search_l, threads = (original_certificate[key] for key in
                                ("validation_k", "validation_l", "validation_threads"))
        query_data = Path(config["Inputs"]["Queries"]).read_bytes()
        query_rows, dim = struct.unpack("<II", query_data[:8])
        assert dim == self.dimension and query_rows >= 16
        labels, queries = [], []
        for row in original_certificate["selected_labels"]:
            for q in range(row["planned_queries"]):
                labels.append(row["external_label"])
                queries.append(query_data[8 + q * dim:8 + (q + 1) * dim])
        assert len(labels) == 261 and len(set(labels)) == 201
        query_file, filters = output / "all201-261queries.u8bin", output / "all201.filters.txt"
        write(query_file, struct.pack("<II", len(queries), dim) + b"".join(queries))
        write(filters, ("\n".join(map(str, labels)) + "\n").encode())
        outcomes = {}
        for variant, binary in (("original", Path(self.proof["original_stock_binary"]["path"])),
                                ("fixed", Path(self.proof["patched_stock_binary"]["path"]))):
            result = output / variant
            result.mkdir()
            command = [binary, "--data_type", "uint8", "--dist_fn", "l2", "--index_path_prefix", FIXTURE / "sift1b",
                       "--result_path", result / "result", "--query_file", query_file, "--recall_at", str(k),
                       "--search_list", str(search_l), "--num_threads", str(threads), "--beamwidth", "2",
                       "--num_nodes_to_cache", "0", "--query_filters_file", filters, "--label_type", "uint"]
            execution = self.run(command, result, "search")
            assert execution["exit_code"] == 0
            ids = result / f"result_{search_l}_idx_uint32.bin"
            distances = result / f"result_{search_l}_dists_float.bin"
            assert struct.unpack("<II", ids.read_bytes()[:8]) == (261, 10)
            assert struct.unpack("<II", distances.read_bytes()[:8]) == (261, 10)
            outcomes[variant] = {"execution": execution, "ids": artifact(ids), "distances": artifact(distances)}
        assert outcomes["original"]["ids"]["sha256"] == outcomes["fixed"]["ids"]["sha256"]
        assert outcomes["original"]["distances"]["sha256"] == outcomes["fixed"]["distances"]["sha256"]
        ids = struct.unpack("<" + "I" * (261 * 10), Path(outcomes["fixed"]["ids"]["path"]).read_bytes()[8:])
        distances = struct.unpack("<" + "f" * (261 * 10), Path(outcomes["fixed"]["distances"]["path"]).read_bytes()[8:])
        attributes = list(struct.iter_unpack("<II", (FIXTURE / "fixture_attrs.u32").read_bytes()))
        vectors = (FIXTURE / "fixture.u8bin").read_bytes()[8:]
        for q, label in enumerate(labels):
            assert len(set(ids[q * k:(q + 1) * k])) == k
            for rank in range(k):
                position = q * k + rank
                vid = ids[position]
                assert vid < self.rows and attributes[vid][0] == label
                expected = sum((a - b) ** 2 for a, b in zip(queries[q], vectors[vid * dim:(vid + 1) * dim]))
                assert math.isfinite(distances[position]) and distances[position] == expected
        report = {"status": "passed", "variants": outcomes, "labels": 201, "native_calls_per_variant": 261,
                  "exact_id_distance_pairs": 2610, "ids_and_distances_byte_identical": True,
                  "all_ids_matching_unique_and_exact_original_vector_distances": True,
                  "query_file": artifact(query_file), "filters": artifact(filters),
                  "k": k, "l": search_l, "threads": threads, "beam": 2, "cache_nodes": 0,
                  "effective_io_limit": 4294967295, "stock_warmup_enabled": False, "production_loaded": False}
        save(output / "report.json", report)
        return report

    def parser(self):
        output = self.output / "parser"
        output.mkdir()
        base_command = json.loads((self.publication / "bin/diskannBuildAdmission.build.json").read_text())["command"]
        probes = {}
        for variant, library in (("original", self.proof["original_library"]), ("fixed", self.proof["patched_library"])):
            command = list(base_command)
            command[command.index(str(HERE / "diskann_build_admission.cpp"))] = str(HERE / "tests/diskann_label_parser_probe.cpp")
            command[command.index(self.proof["patched_library"]["path"])] = library["path"]
            binary = output / ("parser-" + variant)
            command[command.index("-o") + 1] = str(binary)
            execution = self.run(command, output, "compile-" + variant, env=dict(os.environ, TMPDIR=str(output)))
            assert execution["exit_code"] == 0
            probes[variant] = binary
        payloads = {
            "single": b"0\n9\n169\n200\n",
            "multiple": b"1,2\n3,4,5\n7\n",
            "later-comma-after-single-rows": b"1\n2\n3,4\n5\n",
            "tabs": b"1\t\n2,\t3\t\n4\n",
            "crlf": b"1\r\n2,3\r\n4\t\r\n",
            "mixed-newlines": b"1\t\n2,3\r\n4\n",
            "missing-terminal-newline-single": b"1\n2\n3",
            "missing-terminal-newline-multiple": b"1,2\n3,4",
            "only-unterminated-line": b"13",
            "empty-file": b"",
            "blank-row-original-exit": b"1\n\n2\n",
            "invalid-token-original-exception": b"1,a\n2\n",
            "trailing-commas": b"1,2,\n3,\n",
            "cr-without-newline": b"1\r2\r",
            "retained-stoul-whitespace": b" 1 \n\t2,3\t\n",
            "retained-stoul-trailing-text": b"1junk,2\n",
            "retained-uint32-cast": b"4294967295\n4294967296\n",
        }
        semantic = []
        for name, payload in payloads.items():
            fixture = output / (name + ".labels")
            write(fixture, payload)
            observations = {}
            for variant, binary in probes.items():
                execution = self.run([binary, "semantics", fixture], output, name + "-" + variant)
                records = markers(Path(execution["log"]["path"]), "LOADER_PARSER ")
                for record in records:
                    record.pop("metadata_seconds")
                    record.pop("parser_seconds")
                errors = markers(Path(execution["log"]["path"]), "LOADER_PARSER_ERROR ")
                observations[variant] = {"execution": execution, "parsed": records, "errors": errors}
            original, fixed = observations["original"], observations["fixed"]
            assert original["execution"]["exit_code"] == fixed["execution"]["exit_code"]
            assert original["parsed"] == fixed["parsed"] and original["errors"] == fixed["errors"]
            if name in ("only-unterminated-line", "empty-file", "cr-without-newline"):
                assert fixed["parsed"][0]["parsed_rows"] == 0
            if name == "missing-terminal-newline-single":
                assert fixed["parsed"][0]["labels"] == [1, 2]
            if name == "missing-terminal-newline-multiple":
                assert fixed["parsed"][0]["labels"] == [1, 2]
            if name == "blank-row-original-exit":
                assert fixed["execution"]["exit_code"] == 255
            if name == "invalid-token-original-exception":
                assert fixed["execution"]["exit_code"] == 2
            semantic.append({"case": name, "fixture": artifact(fixture), "variants": observations, "equal": True})
        scaling = []
        for count in (16000, 32000, 64000, 128000, 256000):
            observations = {}
            for variant, binary in probes.items():
                execution = self.run([binary, "scale", str(count), "3"], output, f"scale-{count}-{variant}", timeout=180)
                assert execution["exit_code"] == 0
                records = markers(Path(execution["log"]["path"]), "LOADER_PARSER ")
                assert len(records) == 3 and all(row["parsed_rows"] == count for row in records)
                observations[variant] = {
                    "execution": execution, "samples": records,
                    "metadata_median_seconds": statistics.median(row["metadata_seconds"] for row in records),
                    "parser_median_seconds": statistics.median(row["parser_seconds"] for row in records)}
            assert {row["checksum"] for row in observations["original"]["samples"]} == {
                row["checksum"] for row in observations["fixed"]["samples"]}
            scaling.append({"rows": count, "variants": observations})
        first, last = scaling[0]["variants"], scaling[-1]["variants"]
        original_growth = last["original"]["parser_median_seconds"] / first["original"]["parser_median_seconds"]
        fixed_growth = last["fixed"]["parser_median_seconds"] / first["fixed"]["parser_median_seconds"]
        speedup = last["original"]["parser_median_seconds"] / last["fixed"]["parser_median_seconds"]
        assert original_growth > 32 and fixed_growth < original_growth / 2 and speedup > 5, (
            original_growth, fixed_growth, speedup)
        report = {"status": "passed", "semantic_cases": semantic, "scaling": scaling,
                  "n_growth": 16, "original_parser_growth": original_growth, "fixed_parser_growth": fixed_growth,
                  "largest_n_speedup": speedup,
                  "measurement": "actual linked native metadata/parser, not a reference-loop reimplementation",
                  "parser_timing_includes_native_metadata_scan": True,
                  "full_native_index_load_scaling": False, "index_loaded": False,
                  "maximum_rows": 256000, "production_loaded": False, "native_search_invocations": 0}
        save(output / "report.json", report)
        return report

    def policy_validation(self):
        output = self.output / "policy"
        output.mkdir()
        original_source = ROOT / "DiskANN"
        original_library = artifact(original_source / "build/install/lib/libdiskann.a")
        binding, _, _ = authenticate(self.policy, original_source, original_library, artifact)
        assert binding == self.binding
        original = self.policy.read_text()
        variants = {
            "unknown-mode": original.replace("Mode=linear-label-delimiters-v1", "Mode=linear-v2"),
            "extra-search-key": original + "Threads=1\n",
            "extra-section": original + "\n[Search]\nThreads=1\n",
            "duplicate-key": original + "Mode=linear-label-delimiters-v1\n",
            "relative-proof": re.sub(r"(?m)^Proof=.*$", "Proof=loader-proof.json", original),
            "wrong-proof-hash": re.sub(r"(?m)^ProofSHA256=.*$", "ProofSHA256=" + "0" * 64, original),
            "wrong-library": re.sub(r"(?m)^Library=.*$", "Library=" + self.proof["original_library"]["path"], original),
            "wrong-stock": re.sub(r"(?m)^StockBinary=.*$", "StockBinary=" + self.proof["original_stock_binary"]["path"], original),
            "default-section": original + "\n[DEFAULT]\nBad=1\n",
        }
        cases = []
        for name, text in variants.items():
            path = output / (name + ".ini")
            write(path, text.encode())
            try:
                authenticate(path, original_source, original_library, artifact)
            except (ValueError, KeyError, configparser.Error) as error:
                cases.append({"name": name, "rejected": True, "error": str(error), "policy": artifact(path)})
            else:
                raise AssertionError("Malformed metadata policy was accepted: " + name)
        for name, edit in (
            ("wrong-base-revision", lambda proof: proof.update(base_revision="0" * 40)),
            ("wrong-changed-member", lambda proof: proof.update(changed_archive_members=["index.cpp.o"])),
            ("missing-source-inventory", lambda proof: proof["source_inventory"].pop()),
            ("false-unchanged-source", lambda proof: proof["source_inventory"][0].update(unchanged=False)),
        ):
            proof = json.loads(json.dumps(self.proof))
            edit(proof)
            path = output / (name + ".proof.json")
            save(path, proof)
            text = re.sub(r"(?m)^Proof=.*$", "Proof=" + str(path), original)
            text = re.sub(r"(?m)^ProofSHA256=.*$", "ProofSHA256=" + sha256(path), text)
            policy = output / (name + ".ini")
            write(policy, text.encode())
            try:
                authenticate(policy, original_source, original_library, artifact)
            except (ValueError, KeyError) as error:
                cases.append({"name": name, "rejected": True, "error": str(error),
                              "policy": artifact(policy), "proof": artifact(path)})
            else:
                raise AssertionError("Malformed loader proof was accepted: " + name)
        report = {"status": "passed", "valid_binding": binding, "negative_cases": cases}
        save(output / "report.json", report)
        return report

    def compatibility(self):
        output = self.output / "compatibility"
        output.mkdir()
        original_library = self.proof["original_library"]["path"]
        patched_library = self.proof["patched_library"]["path"]
        binaries, builds = {}, []
        for name, source in (("diskannBuildAdmission", "diskann_build_admission.cpp"),
                             ("diskannBench", "diskann_bench.cpp")):
            manifest = json.loads((self.publication / "bin" / (name + ".build.json")).read_text())
            command = [arg for arg in manifest["command"]
                       if not arg.startswith("-DTHREEWAY_DISKANN_LOADER_POLICY_JSON=")]
            command[command.index(patched_library)] = original_library
            macro = '-DTHREEWAY_DISKANN_LIBRARY="' + patched_library + '"'
            command[command.index(macro)] = '-DTHREEWAY_DISKANN_LIBRARY="' + original_library + '"'
            binary = output / ("unpatched-" + name)
            command[command.index("-o") + 1] = str(binary)
            assert str(HERE / source) in command
            execution = self.run(command, output, "compile-unpatched-" + name,
                                 env=dict(os.environ, TMPDIR=str(output)))
            assert execution["exit_code"] == 0
            binaries[name] = binary
            builds.append({"execution": execution, "binary": artifact(binary),
                           "linked_library": self.proof["original_library"], "loader_policy_compiled": False})
        old_certificate = json.loads(
            (ACCEPTED / "diskann-build-admission-published-001/cases/all201-full-k/admission.json").read_text())
        certificate = output / "unpatched-no-policy.json"
        execution = self.run([binaries["diskannBuildAdmission"], "--config", old_certificate["config"],
                              "--certificate", certificate], output, "unpatched-no-policy")
        record = json.loads(certificate.read_text())
        assert execution["exit_code"] == 0 and record["status"] == "admitted"
        assert record.keys() == old_certificate.keys() and "loader_policy" not in record
        for key in ("source_revision", "linked_library", "stock_search_binary", "selected_labels",
                    "input_identities", "planned_stock_query_calls"):
            assert record[key] == old_certificate[key], key
        standalone = {"execution": execution, "certificate": artifact(certificate), "legacy_schema_unchanged": True}
        rejected_certificate = output / "unpatched-with-policy.json"
        rejected = self.run([binaries["diskannBuildAdmission"], "--config", old_certificate["config"],
                             "--certificate", rejected_certificate, "--loader-policy", self.policy],
                            output, "unpatched-with-policy")
        rejected_record = json.loads(rejected_certificate.read_text())
        assert rejected["exit_code"] != 0 and not rejected_record["native_index_loaded"]
        assert "built without an approved loader policy" in rejected_record["error"]
        accepted = json.loads((ACCEPTED / "diskann-shared-guard-published-001/admission-tests.json").read_text())
        baseline = accepted["cases"][0]["phases"]["single"]
        base_text = Path(baseline["profile"]).read_text()
        cases = []
        for name, binary, policy, changes, extra, environment, expected in (
            ("unpatched-no-policy", binaries["diskannBench"], None, {}, "", {}, "admitted"),
            ("missing-policy", self.publication / "bin/diskannBench", None, {}, "", {}, "rejected"),
            ("relative-policy", self.publication / "bin/diskannBench", "policy.ini", {}, "", {}, "rejected"),
            ("different-policy", self.publication / "bin/diskannBench", old_certificate["config"], {}, "", {}, "rejected"),
            ("wrong-library", self.publication / "bin/diskannBench", self.policy,
             {"Library": patched_library}, "", {}, "rejected"),
            ("wrong-base-source", self.publication / "bin/diskannBench", self.policy,
             {"SourceDirectory": self.proof["patched_source_tree"]}, "", {}, "rejected"),
            ("wrong-base-revision", self.publication / "bin/diskannBench", self.policy,
             {"SourceRevision": "0" * 40}, "", {}, "rejected"),
            ("unknown-key", self.publication / "bin/diskannBench", self.policy,
             {}, "UndeclaredTuning=1\n", {}, "rejected"),
            ("environment-override", self.publication / "bin/diskannBench", self.policy,
             {}, "", {"OMP_NUM_THREADS": "1"}, "rejected"),
            ("unpatched-rejects-policy", binaries["diskannBench"], self.policy, {}, "", {}, "rejected"),
        ):
            directory = output / name
            directory.mkdir()
            results = directory / "results"
            (results / "plans").mkdir(parents=True)
            text = replace(base_text, "OutputDirectory", str(results))
            text = replace(text, "BenchmarkBinary", str(binary))
            for key, value in changes.items():
                text = replace(text, key, value)
            metadata = "" if policy is None else "LoaderPolicy=" + str(policy) + "\n"
            text = text.replace("[DiskANN]\n", "[DiskANN]\n" + metadata + extra)
            profile = directory / "config/benchmark.ini"
            profile.parent.mkdir()
            write(profile, text.encode())
            write(results / "plans/Filtered_DiskANN.single.tsv",
                  b"scenario\tcontrol_index\tthread_index\trepeat\nbroad_tag\t0\t0\t1\n")
            execution = self.run([binary, profile, "single"], directory, "benchmark",
                                 env=dict(os.environ, **environment))
            native = results / "native/Filtered_DiskANN/single"
            admission_path = native / "diskann-admission.json"
            if expected == "admitted":
                admission = json.loads(admission_path.read_text())
                assert execution["exit_code"] == 0 and admission["status"] == "admitted"
                assert admission["native_index_loaded"]
                for path in [*native.glob("*.u32bin"), *native.glob("*.f32bin")]:
                    assert path.read_bytes() == (Path(baseline["output_directory"]) / path.name).read_bytes()
            else:
                assert execution["exit_code"] != 0
                if admission_path.exists():
                    admission = json.loads(admission_path.read_text())
                    assert admission["status"] == "rejected" and not admission["native_index_loaded"]
                assert "Loaded PQ centroids" not in Path(execution["log"]["path"]).read_text()
                assert not (native / "ready.json").exists()
                assert not list(native.glob("*.ids.u32bin"))
            cases.append({"name": name, "expected": expected, "execution": execution,
                          "admission": artifact(admission_path) if admission_path.exists() else None,
                          "profile": artifact(profile)})
        refusals = []
        for name, command in (
            ("existing-native-publication", [sys.executable, HERE / "build_diskann_loader.py",
                                             "--publication", self.publication, "--stage", "initialize"]),
            ("existing-client-publication", [sys.executable, HERE / "build_diskann_client.py",
                                             "--loader-policy", self.policy, "--compiler",
                                             "/usr/bin/x86_64-linux-gnu-g++-11",
                                             "--output-directory", self.publication / "build/client-benchmark",
                                             "--publish-binary", self.publication / "bin/diskannBench"]),
        ):
            execution = self.run(command, output, name)
            assert execution["exit_code"] != 0
            refusals.append(execution)
        report = {"status": "passed", "unpatched_client_builds": builds,
                  "standalone_legacy": standalone, "unpatched_policy_rejection": rejected,
                  "benchmark_policy_cases": cases, "fresh_publication_refusals": refusals,
                  "production_loaded": False, "native_production_searches": 0}
        save(output / "report.json", report)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publication", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--stages", default="admission,benchmark,stock,parser,policy,compatibility")
    args = parser.parse_args()
    publication, output = args.publication.resolve(), args.output_directory.resolve()
    assert publication.parent == TC and output.is_relative_to(publication / "tests")
    output.mkdir(parents=True, exist_ok=False)
    validation = Validation(publication, output)
    test_source = artifact(Path(__file__))
    reports = {}
    try:
        for stage in args.stages.split(","):
            function = validation.policy_validation if stage == "policy" else getattr(validation, stage)
            reports[stage] = function()
            print(json.dumps({"stage": stage, "status": "passed", "output": str(output / stage)}), flush=True)
        verify_inputs_unchanged(validation.protected)
        verify_inputs_unchanged([test_source])
        save(output / "commands.json", validation.commands)
        save(output / "report.json", {"schema_version": 1, "status": "passed",
             "publication": str(publication), "stages": {key: artifact(output / key / "report.json") for key in reports},
             "source_fixture": str(FIXTURE), "fixture_shape": [validation.rows, validation.dimension],
             "test_source": test_source,
             "originals_peers_and_fixtures_unchanged": True,
             "production_loaded": False, "native_production_searches": 0, "new_graphs_built": 0})
    except Exception as error:
        save(output / "failed-commands.json", validation.commands)
        save(output / "failure.json", {"status": "failed", "error": repr(error), "completed_stages": list(reports)})
        raise


if __name__ == "__main__":
    main()
