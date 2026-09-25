"""Bounded synthetic native reports; no benchmark or renderer is executed."""

import configparser
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import unittest
from unittest import mock
import uuid

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import assemble_sift1b_selectivity as export
from sift1b_official_inputs import write_bin


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def write_ini(path, sections):
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_dict(sections)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as stream:
        parser.write(stream)


class SelectivityAssembly(unittest.TestCase):
    def setUp(self):
        self.root = Path(f".synthetic-selectivity-assembly-{uuid.uuid4().hex}")
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.b, self.p, self.inputs = [self.root / name for name in ("sptag", "pipeann_fresh_v2", "inputs")]
        for path in (self.b, self.p, self.inputs):
            path.mkdir()
        self.b = self.b.resolve()
        self.p = self.p.resolve()
        self.inputs = self.inputs.resolve()
        self.make_inputs()
        self.make_sptag()
        self.make_pipeann()

    def protect(self, paths):
        return {str(path): export.common.sha256_file(path) for path in paths}

    def make_inputs(self):
        self.query = self.inputs / "queries.npy"
        self.queries = np.arange(export.COUNT * export.DIMENSION, dtype="u1").reshape(export.COUNT, export.DIMENSION)
        np.save(self.query, self.queries)
        self.large = {}
        for name in ("base.bin", "attrs.npy", "attrs.u32"):
            path = self.inputs / name
            path.write_bytes(b"SYNTHETIC STAT-ONLY INPUT: MUST NEVER BE READ")
            self.large[str(path)] = export.identity(path)
        self.index = self.inputs / "sptag-index"
        self.index.mkdir()
        (self.index / "posting-store.bin").write_bytes(b"SYNTHETIC INDEX: MUST NEVER BE READ")
        self.index_files = {"posting-store.bin": export.identity(self.index / "posting-store.bin")}
        self.profile = self.p / "profile/benchmark.ini"
        self.profile.parent.mkdir()
        source = HERE / "configs/sift1b_pipeann_curves"
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(source / "benchmark.ini")
        parser["Dataset"]["ReferenceConfig"] = str(self.inputs / "reference.ini")
        parser["Dataset"]["Workloads"] = str(self.inputs / "workloads.json")
        parser["Benchmark"]["OutputDirectory"] = str(self.p)
        parser["Benchmark"]["SPTAGCampaign"] = str(self.b)
        parser["PipeANN"]["IndexPrefix"] = str(self.inputs / "pipe-index")
        with self.profile.open("w") as stream:
            parser.write(stream)
        (self.inputs / "reference.ini").write_text("[Synthetic]\nOnly=true\n")
        shutil.copytree(source / "filters", self.profile.parent / "filters")
        self.config = export.pipe.config_at(self.profile)
        counts = [1000000000, 170124930, 17012493, 1000735, 425710]
        self.workload = dict(
            corpus_count=export.CORPUS, dimension=128, query_count=1000, topk=10, value_type="UInt8",
            scenarios=export.SCENARIOS, queries=str(self.query), attributes=str(self.inputs / "attrs.npy"),
            base_file=str(self.inputs / "base.bin"), scenario_metadata={}, native_predicates={},
            predicates={}, truth={}, protected_large=self.large,
        )
        self.truths = {}
        protected = [self.query]
        for i, (scenario, count) in enumerate(zip(export.SCENARIOS, counts)):
            contract = export.scenario_contract(self.config, scenario)
            truth = (np.arange(10000, dtype="<i8").reshape(1000, 10) + i * 100000)
            path = self.inputs / f"gt_{scenario}.npy"
            np.save(path, truth)
            self.truths[scenario] = truth
            protected.append(path)
            self.workload["truth"][scenario] = dict(
                ids=str(path), sha256=export.common.sha256_file(path),
                candidate_count=count, selectivity=count / export.CORPUS,
            )
            self.workload["scenario_metadata"][scenario] = dict(
                title=f"SYNTHETIC {scenario}", eligible_count=count,
                corpus_count=export.CORPUS, selectivity=count / export.CORPUS,
            )
            if scenario == "unfilter":
                self.workload["native_predicates"][scenario] = dict(kind="empty", file="")
                self.workload["predicates"][scenario] = None
            elif scenario != "mixed_dnf":
                tag = self.config.section(f"Scenario.{scenario}").getint("Tag")
                path = self.inputs / f"tags_{scenario}.npy"
                np.save(path, np.full((1000, 1), tag, dtype="<u4"))
                protected.append(path)
                self.workload["native_predicates"][scenario] = dict(kind="categorical", file=str(path))
                self.workload["predicates"][scenario] = {"categorical_eq": [0, tag]}
            else:
                packed = [0, 0x444E4633, len(contract["clauses"])]
                for clause in contract["clauses"]:
                    packed.append(len(clause))
                    for term in clause:
                        packed.extend(term)
                packed[0] = len(packed) - 1
                path = self.inputs / "dnf.npy"
                np.save(path, np.tile(np.asarray(packed, dtype="<u4"), (1000, 1)))
                protected.append(path)
                self.workload["native_predicates"][scenario] = dict(kind="dnf", file=str(path))
                self.workload["predicates"][scenario] = {"or": [
                    {"categorical_eq": [0, 200]},
                    {"and": [{"categorical_eq": [0, 199]}, {"numeric_le": [1, 2147483647]}]},
                ]}
        self.workload["protected"] = self.protect(protected)
        save(self.inputs / "workloads.json", self.workload)

    def command(self, resource, tail):
        return ["/usr/bin/time", "-v", "-o", str(resource), "numactl",
                "--cpunodebind=3", "--membind=3", sys.executable] + tail

    def resource(self, path, command, offset=0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'\tCommand being timed: "{" ".join(command[4:])}"\n'
                        "\tElapsed (wall clock) time (h:mm:ss or m:ss): 0:01.00\n\tExit status: 0\n")
        timestamp = datetime(2026, 9, 24, 12, tzinfo=timezone.utc).timestamp() + offset
        os.utime(path, (timestamp, timestamp))

    def make_sptag(self, variants=export.VARIANTS):
        for name in ("configs", "runtime", "source"):
            (self.b / name).mkdir()
        helpers = []
        for name in ("run_adaptive_frontier.py", "run_full.py", "selectivity_common.py"):
            destination = self.b / "source" / name
            shutil.copy2(export.pipe.SHARED / name, destination)
            helpers.append(destination)
        self.binary = self.b / "runtime/after-normal"
        self.binary.write_bytes(b"SYNTHETIC ADAPTIVE RUNTIME - NOT EXECUTABLE")
        binary_sha = export.common.sha256_file(self.binary)
        binaries = dict(
            normal=dict(path=str(self.binary), source=str(self.inputs / "normal-source"),
                        sha256=binary_sha),
            diagnostic=dict(path=str(self.b / "runtime/unused-diagnostic"),
                            source=str(self.inputs / "diagnostic-source"), sha256="d" * 64),
        )
        approval, provenance = self.b / "approval.ini", self.b / "provenance.json"
        approval.write_text("[Authorization]\nAllowNativeRuns=true\n")
        save(provenance, dict(policy_label=export.POLICIES["after"], navigation_schema_version=6,
                              navigation_columns=57, binary_hashes={k: v["sha256"] for k, v in binaries.items()}))
        self.authorization = dict(
            approval=str(approval), approval_sha256=export.common.sha256_file(approval),
            runtimes={"after": dict(schema=6, policy=export.POLICIES["after"], binaries=binaries,
                                   provenance=dict(path=str(provenance), sha256=export.common.sha256_file(provenance)))},
        )
        save(self.b / "authorization.json", self.authorization)
        base_cases, configs = [], []
        for scenario in export.SCENARIOS:
            for variant in variants:
                native = self.workload["native_predicates"][scenario]
                settings = dict(
                    NumberOfThreads="1", ResultNum="10", MaxCheck="2048",
                    PostingAdditionalMaxCheck="0" if variant == "graph" else "2048",
                    PostingAnchorCount="8", SearchPostingPageLimit="3",
                    EnablePostingNavigation="false" if variant == "graph" else "true",
                    LogPhaseTime="false", LogPathStats="false", EnableHybridDistance="false",
                )
                path = self.b / "configs" / f"{scenario}_{variant}.ini"
                write_ini(path, {
                    "SearchSSDIndex": settings,
                    "SearchSweep": {"NProbe": json.dumps(export.GRID)},
                    "Benchmark": dict(Index=str(self.index), Queries=str(self.query), ValueType="UInt8",
                                      Predicate=native["kind"], PredicateFile=native["file"],
                                      MaxQueries="1000", Warmup="1000"),
                })
                configs.append(path)
                base_cases.append(dict(case=f"{scenario}_{variant}", scenario=scenario, variant=variant,
                                       queries=1000, probes=export.GRID, max_check=2048,
                                       posting_additional_max_check=0 if variant == "graph" else 2048,
                                       posting_anchor_count=8, posting_page_limit=3, config=str(path)))
        cases = export.repeat_cases(base_cases, 2)
        batch = {"Batch": {"CaseCount": str(len(cases))}}
        for number, case in enumerate(cases, 1):
            batch[f"Case{number}"] = dict(Config=case["config"],
                                         OutputDirectory=str(self.b / "after/normal" / case["case"]))
        batch_path = self.b / "configs/after-normal.ini"
        write_ini(batch_path, batch)
        self.breg = dict(
            dataset="SIFT1B", mode="selectivity_curve", value_type="UInt8", head_count=120040156,
            index=str(self.index), queries=str(self.query), workload=str(self.inputs / "workloads.json"),
            corpus_query_rows=1000, measured_query_rows=1000, scenarios=export.SCENARIOS,
            grid=export.GRID, ordinary_repetitions=2, points_per_process=len(cases) * len(export.GRID),
            native_processes=1,
            cpu_node=3, memory_node=3, schema_version=6, policy_labels=export.POLICIES,
            ordinary_timing="two reversed case-order passes; full ascending sweep per case",
            index_files=self.index_files, protected_large=self.large,
            runtimes={"after": {"Normal": binaries["normal"]["source"]}}, cases=cases,
            protected={**self.workload["protected"], **self.protect(
                [self.inputs / "workloads.json", batch_path] + configs + helpers)},
        )
        if variants != export.VARIANTS:
            counters = [
                dict(case, case="counter_" + case["case"], queries=32, probes=[16, 96, 384],
                     repetition=1, counter_only=True)
                for case in base_cases if case["scenario"] in ("sel_01pct", "mixed_dnf")
            ]
            self.breg.update(
                variants=list(variants), implementation_revision="controlled_ascent",
                native_processes=2, points_by_kind={"normal": 70, "diagnostic": 6},
                cases_by_kind={"normal": cases, "diagnostic": counters},
            )
        save(self.b / "registration.json", self.breg)
        self.sptag_rows, events = [], [
            dict(event="batch_begin", cases=len(cases), phase_timing=False, value_type="UInt8",
                 index=str(self.index), queries=str(self.query)),
            dict(event="batch_loaded", index_load_count=1, query_corpus_load_count=1),
        ]
        for number, case in enumerate(cases, 1):
            parent = self.b / "after/normal" / case["case"]
            native_predicate = self.workload["native_predicates"][case["scenario"]]
            begin = dict(event="case_begin", case_id=f"Case{number}", config=case["config"],
                         output_directory=str(parent), predicate=native_predicate["kind"],
                         predicate_file=native_predicate["file"], phase_timing=False,
                         warmup_queries=1000, measured_queries=1000, replay_queries=1000,
                         search_settings={k.lower(): v for k, v in
                                          export.read_ini(case["config"])["SearchSSDIndex"].items()})
            events.append(begin)
            for index, probe in enumerate(export.GRID):
                folder = parent / f"nprobe_{probe}"
                folder.mkdir(parents=True)
                hits = [5, 6, 5, 8, 9, 8, 10][index]
                variant = int(case["variant"] == "postgraph_extra" and case["scenario"] != "unfilter")
                hits = min(10, hits + variant)
                ids = self.truths[case["scenario"]].astype("<i4").copy()
                ids[:, hits:] += 10000000
                ids.tofile(folder / "ids.i32")
                np.tile(np.arange(10, dtype="<f4"), (1000, 1)).tofile(folder / "dist.f32")
                work = np.tile(np.asarray([probe, variant, 2, 3, 4, 5, 6, 7], dtype="<u8"), (1000, 1))
                work.tofile(folder / "work.u64")
                latency = 300.0 + index * 50 + case["repetition"] * 100
                np.full(1000, latency, dtype="<f8").tofile(folder / "latency_us.f64")
                native = dict(
                    mode="graph" if case["variant"] == "graph" else "posting", event="point",
                    queries=1000, warmup_queries=1000, measured_queries=1000, replay_queries=1000,
                    value_type="UInt8", search_posting_page_limit=3, nprobe=probe, max_check=2048,
                    posting_additional_max_check=case["posting_additional_max_check"], posting_anchor_count=8,
                    diagnostic=False, phase_timing=False, navigation_schema_version=6, navigation_columns=57,
                    mean_ms=(latency + 0.25) / 1000, qps=1000000 / (latency + 0.25),
                    p50_us=latency, p95_us=latency, p99_us=latency,
                    case_id=f"Case{number}", config=case["config"], output_directory=str(parent),
                )
                events.append(native)
                self.sptag_rows.append(dict(
                    native, **{k: v for k, v in case.items() if k not in native},
                    runtime="after", kind="normal", policy=export.POLICIES["after"],
                    timing_accepted=False, timing_scope="repeated ordinary curve",
                    recall=hits / 10, underfilled_queries=0, empty_queries=0, mean_returned=10.0,
                    mean_ssd_work=work.mean(axis=0).tolist(),
                    payload_hashes={name: export.common.sha256_file(folder / name) for name in export.PAYLOADS},
                    latency_sha256=export.common.sha256_file(folder / "latency_us.f64"),
                ))
                if "implementation_revision" in self.breg:
                    self.sptag_rows[-1]["implementation_revision"] = self.breg["implementation_revision"]
            events.append(dict(event="case_end", case_id=f"Case{number}", config=case["config"],
                               output_directory=str(parent), completed_points=7))
        events.append(dict(event="batch_end", completed_cases=len(cases)))
        self.write_sptag_events(events)
        save(self.b / "after-normal-results.json", self.sptag_rows)
        resource = self.b / "after-normal.resources.txt"
        command = self.command(resource, [
            "-B", str(self.b / "source/selectivity_common.py"), str(self.b / "after/normal"),
            str(self.binary), str(batch_path),
        ])
        save(self.b / "after-normal-command.json", command)
        self.resource(resource, command)
        save(self.b / "status.json", dict(state="stage_complete", runtime="after", kind="normal"))

    def write_sptag_events(self, events):
        lines = []
        for event in events:
            first = "mode" if event["event"] == "point" else "event"
            ordered = {first: event[first], **{k: v for k, v in event.items() if k != first}}
            lines.append(json.dumps(ordered, separators=(",", ":")))
        (self.b / "after-normal.log").write_text("\n".join(lines) + "\n")

    def make_pipeann(self):
        for name in ("source", "runtime", "logs", "inputs"):
            (self.p / name).mkdir()
        helpers = []
        for name in ("run_pipeann_selectivity.py", "run_sift1b_official.py",
                     "official_benchmark_config.py", "sift1b_official_inputs.py"):
            destination = self.p / "source" / name
            shutil.copy2(HERE / name, destination)
            helpers.append(destination)
        write_bin(self.p / "inputs/query.u8bin", self.queries, "u1")
        definitions = []
        for name in export.SCENARIOS:
            truth = self.workload["truth"][name]
            write_bin(self.p / "inputs" / f"gt_{name}.ibin", self.truths[name], "<u4")
            definitions.append(dict(
                name=name, truth_bin=f"gt_{name}.ibin", truth_npy=truth["ids"],
                candidate_count=truth["candidate_count"], selectivity=truth["selectivity"],
                predicate=export.scenario_contract(self.config, name)["predicate"],
                kind=self.config.section(f"Scenario.{name}")["Kind"],
            ))
        binaries = {}
        for name in ("search_disk_index", "search_disk_index_filtered"):
            path = self.p / "runtime" / name
            path.write_bytes(f"SYNTHETIC NATIVE BINARY {name}: NEVER EXECUTE".encode())
            binaries[name] = dict(path=str(path), sha256=export.common.sha256_file(path), origin="SYNTHETIC")
        index = self.inputs / "pipe-index"
        index.write_bytes(b"SYNTHETIC PIPEANN INDEX: MUST NEVER BE READ")
        (self.p / "inputs/base.label.0").symlink_to(index)
        toolchain, memory = self.inputs / "toolchain.json", self.inputs / "memory.json"
        save(toolchain, {"fixture": True})
        save(memory, {"status": "complete", "fixture": True})
        self.preg = dict(
            query_count=1000, warmup_queries=1000, repeats=2, threads=1, cpu_nodes=3, memory_nodes=3,
            corpus_count=export.CORPUS, scenario_count=5, points_per_scenario=26, read_only=True, no_mapping=True,
            historical_timings_used=False, memory_validation="complete", definitions=definitions, binaries=binaries,
            index_files={str(index): export.identity(index)}, protected_large=self.large,
            alias_targets={"base.label.0": str(index)},
            reference_toolchain=export.common.identity(toolchain, True),
            reference_memory=export.common.identity(memory, True),
            jobs=export.pipe.native_jobs(self.config, definitions, binaries, self.p),
        )
        self.preg["protected"] = self.protect(
            [self.profile, self.inputs / "workloads.json"] + helpers +
            list((self.profile.parent / "filters").iterdir()) +
            [path for path in (self.p / "inputs").iterdir() if not path.is_symlink()] +
            list((self.p / "runtime").iterdir())
        )
        save(self.p / "registration.json", self.preg)
        native = []
        for number, job in enumerate(self.preg["jobs"]):
            stage = self.p / job["scenario"]
            stage.mkdir()
            log = self.p / "logs" / f"{job['scenario']}.log"
            lines = []
            for point in job["points"]:
                result = dict(
                    has_recall=True, L=point["L"], recall_percent=45 + point["L"] / 100 - point["repeat"],
                    qps=(9000 if point["stage"] == "warmup" else 1000 + 100 * point["repeat"]) + point["L"],
                )
                lines.append("RESULT " + json.dumps(result))
            log.write_text("\n".join(lines) + "\n")
            parsed = export.parse_results(log, job, 1000)
            native.extend(parsed)
            save(stage / "results.json", parsed)
            resource = self.p / "logs" / f"{job['scenario']}.resources.txt"
            command = self.command(resource, ["-B", str(self.p / "source/run_pipeann_selectivity.py"),
                                              "native", str(stage)] + job["command"])
            save(stage / "command.json", command)
            self.resource(resource, command, number + 1)
        save(self.p / "native-results.json", native)
        save(self.p / "results.json", [row for row in native if row["stage"] == "measured"])
        save(self.p / "status.json", dict(state="complete", native_processes=5, measured_points=130))

    def enable_descriptor_adaptation(self):
        root = self.inputs / "readonly-descriptor-adaptation"
        self.adaptation_path = root / "toolchain.json"
        verifier = root / "validation/verify_toolchain.py"
        verifier.parent.mkdir(parents=True)
        verifier.write_text("raise RuntimeError('Synthetic verifier must never be executed by assembly')\n")
        reference = self.inputs / "toolchain.json"
        save(reference, dict(fixture=True, config_sha256="c" * 64))
        self.preg["reference_toolchain"] = export.common.identity(reference, True)
        self.preg["source_revision"] = "a" * 40
        binaries = {}
        for name, binary in self.preg["binaries"].items():
            origin = root / "build/tests" / name
            origin.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(binary["path"], origin)
            binary["origin"] = str(origin)
            binaries[name] = dict(path=str(origin), sha256=binary["sha256"])
        self.adaptation_manifest = dict(
            kind="pipeann-query-readonly-fds-adaptation", version=1, status="complete",
            original_toolchain=self.preg["reference_toolchain"], source_revision=self.preg["source_revision"],
            original_config_sha256="c" * 64, binaries=binaries,
            scope=dict(
                query_guard="READ_ONLY_TESTS",
                changed_native_files=["include/filter/attribute.h", "src/utils/linux_aligned_file_reader.cpp"],
                algorithms_unchanged=True, io_backend_unchanged=True, allocator_unchanged=True,
                all_other_authenticated_source_files_unchanged=True, frozen_toolchain_unchanged=True,
                original_source_repositories_modified=False, data_or_index_bytes_modified=False,
                existing_benchmark_outputs_modified=False,
            ),
            effective=dict(backend="uring", allocator="tcmalloc",
                           definitions=["NDEBUG", "NO_MAPPING", "READ_ONLY_TESTS", "USE_URING", "USE_TCMALLOC"]),
            flag_equivalence=dict(result="pass", compile_commands_identical_after_root_normalization=True,
                                  search_link_commands_identical_after_root_normalization=True),
            validation=dict(
                fixture_status="pass", functional_smoke_status="pass", all_native_processes_exited=True,
                functional_only=True, plottable=False, query_rows=[0, 16],
                smoke_results=[dict(qps=999999, recall=1.0, queries=16, fixture_only=True)],
            ),
            linkage=dict(files={"SYNTHETIC pinned library": {"sha256": "f" * 64}}),
        )
        self.preg["readonly_descriptor_adaptation"] = dict(
            path=str(self.adaptation_path), sha256="", verifier=str(verifier),
            description="Synthetic file-access-only adaptation; ANN unchanged", backend="uring", allocator="tcmalloc",
        )
        self.preg["protected"][str(verifier)] = export.common.sha256_file(verifier)
        self.save_descriptor_adaptation()

    def save_descriptor_adaptation(self):
        save(self.adaptation_path, self.adaptation_manifest)
        digest = export.common.sha256_file(self.adaptation_path)
        self.preg["readonly_descriptor_adaptation"]["sha256"] = digest
        self.preg["protected"][str(self.adaptation_path)] = digest
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(self.profile)
        parser["PipeANN"]["ReadOnlyAdaptationManifest"] = str(self.adaptation_path)
        parser["PipeANN"]["ReadOnlyAdaptationSHA256"] = digest
        with self.profile.open("w") as stream:
            parser.write(stream)
        self.preg["protected"][str(self.profile)] = export.common.sha256_file(self.profile)
        self.config = export.pipe.config_at(self.profile)
        self.preg["jobs"] = export.pipe.native_jobs(self.config, self.preg["definitions"], self.preg["binaries"], self.p)
        save(self.p / "registration.json", self.preg)

    def assemble(self):
        return export.assemble(self.b, self.p, completed=True)

    def adaptive_only_campaign(self):
        self.b = self.root.resolve() / "sptag_adaptive_only"
        self.b.mkdir()
        self.make_sptag(export.ADAPTIVE_VARIANTS)
        return self.root.resolve() / "new-publication/plot_input"

    def complete_adaptive_campaign(self):
        ordinary = dict(
            state="complete", runtime="after", kind="normal", completed_points=70,
            independent_of_counter_stage=True, exact_unfiltered_payload_parity=True,
            reverse_pass_payload_parity=True, report=str(self.b / "after-normal-results.json"),
            report_sha256=export.common.sha256_file(self.b / "after-normal-results.json"),
            command=export.common.read_json(self.b / "after-normal-command.json"),
        )
        save(self.b / "ordinary-completion.json", ordinary)
        save(self.b / "after-normal-status.json", {
            key: value for key, value in ordinary.items()
            if key not in ("independent_of_counter_stage", "exact_unfiltered_payload_parity",
                           "reverse_pass_payload_parity")
        })
        completed = dict(
            state="complete", completed_ordinary_points=70, completed_counter_only_points=6,
            native_processes=2, search_settings_changed=False, phase_timing=False,
            performance_acceptance=False,
        )
        for name in ("status.json", "completion.json"):
            save(self.b / name, completed)

    def reject(self, message):
        with self.assertRaisesRegex((ValueError, RuntimeError), message):
            self.assemble()
        self.assertFalse((self.p / "plot_input").exists())

    def reseal(self, root, path):
        registration = export.common.read_json(root / "registration.json")
        registration["protected"][str(path)] = export.common.sha256_file(path)
        save(root / "registration.json", registration)

    def test_complete_assembly_uses_only_two_measured_repetitions_and_preserves_curves(self):
        output = self.assemble()
        self.assertEqual({p.name for p in output.iterdir()},
                         {"summary.csv", "plot_registration.json", "assembly_manifest.json"})
        with (output / "summary.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 135)
        manifest = export.common.read_json(output / "assembly_manifest.json")
        registration = export.common.read_json(output / "plot_registration.json")
        self.assertEqual(registration["comparison"], "fresh_paired")
        self.assertEqual(registration["scenarios"], export.SCENARIOS)
        self.assertEqual(manifest["validated_counts"]["measured_repetitions"], 270)
        self.assertEqual(manifest["validated_counts"]["pipeann_warmups_excluded"], 130)
        self.assertFalse(manifest["renderer_invoked"])
        self.assertFalse(manifest["native_execution"])
        self.assertFalse(manifest["historical_timings_used"])
        self.assertFalse(manifest["checks"]["index_or_base_content_scanned"])
        engines = registration["engines"]
        self.assertEqual(engines["SPTAG_adaptive"]["runtime_id"], engines["SPTAG_H1"]["runtime_id"])
        self.assertEqual(engines["SPTAG_adaptive"]["index_id"], engines["SPTAG_H1"]["index_id"])
        self.assertEqual(engines["SPTAG_adaptive"]["runtime_id"], "sha256:" + export.common.sha256_file(self.binary))
        self.assertEqual({entry["query_cohort_id"] for entry in engines.values()},
                         {"sha256:" + hashlib.sha256(self.queries.tobytes()).hexdigest()})
        self.assertNotEqual(*manifest["query_cohort"]["container_hashes"])
        self.assertEqual({entry["source_date"] for entry in engines.values()}, {"2026-09-24"})
        self.assertIn("whole measured-window", engines["SPTAG_adaptive"]["native_case_protocol"]["timing"])
        first = self.sptag_rows[0]
        samples = np.fromfile(self.b / "after/normal" / first["case"] / "nprobe_16/latency_us.f64", dtype="<f8")
        self.assertNotEqual(first["qps"], 1000000 / float(samples.mean()))
        for engine, entry in engines.items():
            self.assertEqual(entry["L"], export.pipe.LS if engine == "PipeANN" else export.GRID)
            self.assertEqual(entry["qps_aggregation"], "median")
            self.assertEqual((entry["threads"], entry["cpu_nodes"], entry["memory_nodes"]), (1, "3", "3"))
            self.assertEqual(entry["io_mode"], "direct" if engine == "PipeANN" else "buffered")
            self.assertEqual(entry["native_case_protocol"]["replay_queries"], 0 if engine == "PipeANN" else 1000)
        for row in rows:
            records = [point for point in manifest["measured_records"] if point["scenario"] == row["scenario"] and
                       point["engine"] == row["engine"] and point["L"] == int(row["L"])]
            self.assertEqual({point["repetition"] for point in records}, {1, 2})
            self.assertEqual(len(records), 2)
            for metric in ("recall", "qps"):
                values = [point[metric] for point in records]
                self.assertEqual(float(row[metric]), statistics.median(values))
                self.assertEqual(float(row[metric + "_min"]), min(values))
                self.assertEqual(float(row[metric + "_max"]), max(values))
            if row["engine"] == "PipeANN":
                self.assertLess(float(row["qps_max"]), 2000)
        graph = [row for row in rows if row["scenario"] == "broad_tag" and row["engine"] == "SPTAG_H1"]
        self.assertEqual([int(row["L"]) for row in graph], export.GRID)
        self.assertLess(float(graph[2]["recall"]), float(graph[1]["recall"]))
        for path, record in manifest["source_files"].items():
            self.assertEqual(export.common.sha256_file(path), record["sha256"])
        for name, digest in manifest["output_sha256"].items():
            self.assertEqual(export.common.sha256_file(output / name), digest)
        self.assertFalse((self.p / "plots_selectivity").exists())

    def test_parent_declaration_and_completed_status_are_required_before_any_output(self):
        with self.assertRaisesRegex(ValueError, "Parent must declare"):
            export.assemble(self.b, self.p)
        for root, state, message in (
            (self.b, dict(state="running"), "Incomplete SPTAG"),
            (self.p, dict(state="prepared_not_run", native_runs=0), "Incomplete PipeANN"),
        ):
            with self.subTest(root=root):
                old = (root / "status.json").read_text()
                save(root / "status.json", state)
                self.reject(message)
                (root / "status.json").write_text(old)

    def test_adaptive_only_reuses_baseline_without_modifying_prior_publication(self):
        self.enable_descriptor_adaptation()
        old_output = self.assemble()
        old_hashes = {
            path: export.common.sha256_file(path)
            for path in self.p.rglob("*") if path.is_file() and not path.is_symlink()
        }
        output = self.adaptive_only_campaign()
        self.complete_adaptive_campaign()
        result = export.assemble(
            self.b, self.p, completed=True, adaptive_only=True,
            reuse_pipeann_baseline=True, output_directory=output,
        )
        self.assertEqual(result, output)
        with (output / "summary.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 100)
        self.assertEqual({row["engine"] for row in rows}, {"SPTAG_adaptive", "PipeANN"})
        registration = export.common.read_json(output / "plot_registration.json")
        manifest = export.common.read_json(output / "assembly_manifest.json")
        self.assertEqual(registration["series"], "adaptive_only")
        self.assertEqual(registration["comparison"], "reused_pipeann_baseline")
        self.assertEqual(set(registration["engines"]), {"SPTAG_adaptive", "PipeANN"})
        self.assertFalse(registration["engines"]["SPTAG_adaptive"]["measurement_reused"])
        self.assertTrue(registration["engines"]["PipeANN"]["measurement_reused"])
        self.assertEqual(registration["engines"]["PipeANN"]["source_campaign"], str(self.p))
        self.assertTrue(manifest["historical_timings_used"])
        self.assertFalse(manifest["source_completion"]["SPTAG"]["performance_acceptance"])
        self.assertEqual(manifest["reused_measurement_engines"], ["PipeANN"])
        self.assertIsNone(manifest["checks"]["unfiltered_sptag_control_payload_equality"])
        self.assertEqual(manifest["validated_counts"], dict(
            sptag_measured=70, pipeann_measured=130, pipeann_warmups_excluded=130,
            measured_repetitions=200, plotted_points=100,
        ))
        for path, digest in old_hashes.items():
            self.assertEqual(export.common.sha256_file(path), digest, str(path))
        self.assertTrue(old_output.is_dir())
        self.assertFalse((output.parent / "plots_selectivity").exists())

    def test_final_completion_cannot_hide_missing_or_changed_ordinary_evidence(self):
        output = self.adaptive_only_campaign()
        self.complete_adaptive_campaign()
        options = dict(
            completed=True, adaptive_only=True, reuse_pipeann_baseline=True, output_directory=output
        )
        record = export.common.read_json(self.b / "ordinary-completion.json")
        (self.b / "ordinary-completion.json").unlink()
        with self.assertRaisesRegex(ValueError, "Missing completed input"):
            export.assemble(self.b, self.p, **options)
        changed = dict(record, report_sha256="0" * 64)
        for name in ("ordinary-completion.json", "after-normal-status.json"):
            save(self.b / name, changed)
        with self.assertRaisesRegex(ValueError, "Registered hash mismatch"):
            export.assemble(self.b, self.p, **options)
        save(self.b / "ordinary-completion.json", record)
        save(self.b / "after-normal-status.json", dict(record, completed_points=69))
        with self.assertRaisesRegex(ValueError, "independent ordinary completion"):
            export.assemble(self.b, self.p, **options)
        self.assertFalse(output.exists())

    def test_separate_counter_process_requires_its_explicit_plan(self):
        output = self.adaptive_only_campaign()
        options = dict(
            completed=True, adaptive_only=True, reuse_pipeann_baseline=True, output_directory=output
        )
        original = json.loads(json.dumps(self.breg))
        del self.breg["points_by_kind"]
        save(self.b / "registration.json", self.breg)
        with self.assertRaisesRegex(ValueError, "explicit controlled-ascent counter plan"):
            export.assemble(self.b, self.p, **options)
        self.breg = json.loads(json.dumps(original))
        self.breg["cases_by_kind"]["diagnostic"][0]["queries"] = 1000
        save(self.b / "registration.json", self.breg)
        with self.assertRaisesRegex(ValueError, "separate diagnostic case/query grid"):
            export.assemble(self.b, self.p, **options)
        self.assertFalse(output.exists())

    def test_adaptive_only_requires_declared_complete_variant_selection(self):
        output = self.adaptive_only_campaign()
        options = dict(completed=True, reuse_pipeann_baseline=True, output_directory=output)
        with self.assertRaisesRegex(ValueError, "Unexpected SPTAG variants"):
            export.assemble(self.b, self.p, **options)
        del self.breg["variants"]
        save(self.b / "registration.json", self.breg)
        with self.assertRaisesRegex(ValueError, "Unexpected SPTAG variants"):
            export.assemble(self.b, self.p, adaptive_only=True, **options)
        self.breg["variants"] = list(export.ADAPTIVE_VARIANTS)
        self.breg["cases"].pop()
        save(self.b / "registration.json", self.breg)
        with self.assertRaisesRegex(ValueError, "Unexpected SPTAG case order"):
            export.assemble(self.b, self.p, adaptive_only=True, **options)
        self.assertFalse(output.exists())

    def test_reusing_pipeann_requires_explicit_reuse_and_separate_output(self):
        output = self.adaptive_only_campaign()
        options = dict(completed=True, adaptive_only=True)
        with self.assertRaisesRegex(ValueError, "explicit separate output"):
            export.assemble(self.b, self.p, reuse_pipeann_baseline=True, **options)
        for destination in (self.b / "new", self.p / "new"):
            with self.subTest(destination=destination):
                with self.assertRaisesRegex(ValueError, "outside both immutable source campaigns"):
                    export.assemble(
                        self.b, self.p, reuse_pipeann_baseline=True, output_directory=destination, **options
                    )
                self.assertFalse(destination.exists())
        with self.assertRaisesRegex(ValueError, "registered fixed PipeANN selectivity profile"):
            export.assemble(self.b, self.p, output_directory=output, **options)
        self.assertFalse(output.exists())
        with self.assertRaisesRegex(ValueError, "inside the immutable SPTAG index"):
            export.assemble(
                self.b, self.p, reuse_pipeann_baseline=True, output_directory=self.index / "new", **options
            )
        self.assertFalse((self.index / "new").exists())

    def test_adaptive_only_cannot_silently_drop_completed_h1_controls(self):
        with self.assertRaisesRegex(ValueError, "Unexpected SPTAG variants"):
            export.assemble(self.b, self.p, completed=True, adaptive_only=True)
        self.assertFalse((self.p / "plot_input").exists())

    def test_descriptor_adaptation_is_authenticated_preserved_and_disclosed_without_execution(self):
        self.enable_descriptor_adaptation()
        with mock.patch("subprocess.Popen", side_effect=AssertionError("No native/verifier execution")), \
                mock.patch.object(export.pipe, "verify_adaptation", side_effect=AssertionError("Parent-only verifier")):
            output = self.assemble()
        registration = export.common.read_json(output / "plot_registration.json")
        manifest = export.common.read_json(output / "assembly_manifest.json")
        engine = registration["engines"]["PipeANN"]
        self.assertEqual(engine["readonly_descriptor_adaptation"], self.preg["readonly_descriptor_adaptation"])
        self.assertEqual(engine["native_case_protocol"]["readonly_descriptor_scope"], self.adaptation_manifest["scope"])
        self.assertEqual((engine["controls"]["backend"], engine["controls"]["allocator"]), ("uring", "tcmalloc"))
        adaptation = manifest["protocols"]["PipeANN"]["readonly_descriptor_adaptation"]
        self.assertEqual(adaptation["manifest"], self.adaptation_manifest)
        self.assertFalse(adaptation["verifier_invoked_by_assembler"])
        self.assertEqual(registration["caption_note"], export.READONLY_FD_CAPTION)
        self.assertLessEqual(max(map(len, registration["caption_note"].splitlines())), 105)
        for phrase in ("file-access-only", "READ_ONLY_TESTS O_RDONLY", "open-failure checks",
                       "ANN/search/filter/distance unchanged", "PQ, pipeline 32, uring, tcmalloc and NO_MAPPING unchanged"):
            self.assertIn(phrase, registration["caption_note"])
        self.assertIn(str(self.adaptation_path), manifest["source_files"])
        self.assertEqual(manifest["validated_counts"]["pipeann_measured"], 130)
        self.assertTrue(all(point["qps"] < 5000 for point in manifest["measured_records"]))
        self.assertFalse(manifest["renderer_invoked"])
        self.assertFalse((self.p / "plots_selectivity").exists())

    def test_running_adapted_campaign_still_cannot_be_assembled(self):
        self.enable_descriptor_adaptation()
        save(self.p / "status.json", dict(state="running", scenario="unfilter"))
        self.reject("Incomplete PipeANN campaign")

    def test_declared_adaptation_cannot_be_omitted_from_registration(self):
        self.enable_descriptor_adaptation()
        self.preg["readonly_descriptor_adaptation"] = None
        save(self.p / "registration.json", self.preg)
        self.reject("Missing registered read-only descriptor adaptation provenance")

    def test_adaptation_must_keep_ann_backend_flags_and_binary_identity(self):
        self.enable_descriptor_adaptation()
        original = json.loads(json.dumps(self.adaptation_manifest))
        changes = (
            ("scope", "algorithms_unchanged", False, "query-file-access-only"),
            ("effective", "backend", "aio", "backend/allocator/flag"),
            ("effective", "definitions", ["READ_ONLY_TESTS"], "backend/allocator/flag"),
            ("validation", "fixture_status", "failed", "backend/allocator/flag"),
        )
        for section, key, value, message in changes:
            with self.subTest(section=section, key=key):
                self.adaptation_manifest = json.loads(json.dumps(original))
                self.adaptation_manifest[section][key] = value
                self.save_descriptor_adaptation()
                self.reject(message)
        self.adaptation_manifest = json.loads(json.dumps(original))
        self.adaptation_manifest["binaries"]["search_disk_index"]["sha256"] = "0" * 64
        self.save_descriptor_adaptation()
        self.reject("Adaptation native binary identity differs")

    def test_adaptation_manifest_hash_cannot_change(self):
        self.enable_descriptor_adaptation()
        with self.adaptation_path.open("a") as stream:
            stream.write("\n")
        self.reject("Registered hash mismatch")

    def test_cli_requires_parent_gate_and_never_runs_native_tools(self):
        result = subprocess.run(
            [sys.executable, "-B", str(HERE / "assemble_sift1b_selectivity.py"), str(self.b), str(self.p)],
            capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--completed", result.stderr)
        self.assertFalse((self.p / "plot_input").exists())
        with mock.patch("subprocess.Popen", side_effect=AssertionError("Native execution forbidden")):
            self.assemble()

    def test_completed_cli_assembles_only_synthetic_inputs(self):
        result = subprocess.run(
            [sys.executable, "-B", str(HERE / "assemble_sift1b_selectivity.py"),
             str(self.b), str(self.p), "--completed"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("135 points from 270 ordinary measured repetitions", result.stdout)
        self.assertFalse((self.p / "plots_selectivity").exists())

    def test_failed_attempt_with_partial_unfiltered_results_is_never_plottable(self):
        self.assertTrue((self.p / "unfilter/results.json").is_file())
        (self.p / "native-results.json").unlink()
        (self.p / "results.json").unlink()
        save(self.p / "status.json", dict(
            state="failed", stage="run", error="unfilter EBADF; broad_tag native exit 132",
        ))
        self.reject("Incomplete PipeANN campaign")

    def test_fresh_cli_path_uses_its_registered_profile_not_a_fixed_filename(self):
        replacement = self.profile.with_name("benchmark_fresh_v2.ini")
        self.profile.rename(replacement)
        registration = export.common.read_json(self.p / "registration.json")
        del registration["protected"][str(self.profile)]
        registration["protected"][str(replacement)] = export.common.sha256_file(replacement)
        save(self.p / "registration.json", registration)
        output = self.assemble()
        self.assertEqual(output, self.p / "plot_input")
        manifest = export.common.read_json(output / "assembly_manifest.json")
        self.assertIn(str(replacement), manifest["source_files"])

    def test_index_and_base_are_stat_only_never_content_hashed_or_loaded(self):
        forbidden = {Path(path).resolve() for path in self.large}
        forbidden |= {Path(value["path"]) for value in self.index_files.values()}
        forbidden |= {Path(value["path"]) for value in self.preg["index_files"].values()}
        original_hash, original_load = export.common.sha256_file, np.load
        def checked_hash(path):
            self.assertNotIn(Path(path).resolve(), forbidden)
            return original_hash(path)
        def checked_load(path, *args, **kwargs):
            self.assertNotIn(Path(path).resolve(), forbidden)
            return original_load(path, *args, **kwargs)
        with mock.patch.object(export.common, "sha256_file", side_effect=checked_hash), \
                mock.patch.object(np, "load", side_effect=checked_load), \
                mock.patch("os.walk", side_effect=AssertionError("No directory inventories")):
            self.assemble()

    def test_missing_duplicate_or_reordered_sptag_points_fail(self):
        original = export.common.read_json(self.b / "after-normal-results.json")
        for rows in (original[:-1], original[:-1] + [original[0]], list(reversed(original))):
            save(self.b / "after-normal-results.json", rows)
            self.reject("Incomplete, duplicate or reordered SPTAG")

    def test_sptag_native_order_and_windows_are_verified(self):
        path = self.b / "after-normal.log"
        original = [json.loads(line) for line in path.read_text().splitlines()]
        incomplete = original[:-1]
        self.write_sptag_events(incomplete)
        self.reject("Incomplete/out-of-order SPTAG")
        events = json.loads(json.dumps(original))
        first = next(row for row in events if row["event"] == "point")
        first["warmup_queries"] = 999
        self.write_sptag_events(events)
        report = export.common.read_json(self.b / "after-normal-results.json")
        report[0]["warmup_queries"] = 999
        save(self.b / "after-normal-results.json", report)
        self.reject("ordinary native point/window")

    def test_sptag_native_errors_reject_valid_looking_points(self):
        with (self.b / "after-normal.log").open("a") as stream:
            stream.write(":ERROR] synthetic native I/O failure\n")
        self.reject("SPTAG native error diagnostics")

    def test_raw_payload_hash_changes_are_not_hidden_by_report_aggregation(self):
        path = self.b / "after/normal/r1_unfilter_graph/nprobe_16/latency_us.f64"
        values = np.fromfile(path, dtype="<f8")
        values[0] += 1
        values.tofile(path)
        self.reject("Registered hash mismatch")

    def test_latency_percentiles_are_checked_without_redefining_native_qps(self):
        events = [json.loads(line) for line in (self.b / "after-normal.log").read_text().splitlines()]
        first = next(row for row in events if row["event"] == "point")
        first["p95_us"] += 1
        self.write_sptag_events(events)
        report = export.common.read_json(self.b / "after-normal-results.json")
        report[0]["p95_us"] += 1
        save(self.b / "after-normal-results.json", report)
        self.reject("latency percentile differs from payload")

    def test_repetition_ssd_work_must_match_even_if_its_report_is_updated(self):
        report = export.common.read_json(self.b / "after-normal-results.json")
        row = next(row for row in report if row["case"] == "r2_broad_tag_graph" and row["nprobe"] == 16)
        folder = self.b / "after/normal" / row["case"] / "nprobe_16"
        work = np.fromfile(folder / "work.u64", dtype="<u8").reshape(1000, 8)
        work[0, 0] += 1
        work.tofile(folder / "work.u64")
        row["payload_hashes"]["work.u64"] = export.common.sha256_file(folder / "work.u64")
        row["mean_ssd_work"] = work.mean(axis=0).tolist()
        save(self.b / "after-normal-results.json", report)
        self.reject("repetitions changed IDs/distances/SSD work")

    def test_query_payload_identity_is_not_container_hash_identity(self):
        path = self.p / "inputs/query.u8bin"
        payload = bytearray(path.read_bytes())
        payload[8] ^= 1
        path.write_bytes(payload)
        self.reseal(self.p, path)
        self.reject("logical UInt8 query payload differs")

    def test_native_truth_requires_ids_only_layout_and_identical_ids(self):
        path = self.p / "inputs/gt_unfilter.ibin"
        original = path.read_bytes()
        path.write_bytes(original + b"\x00" * 40000)
        self.reseal(self.p, path)
        self.reject("Invalid native shape or file size")
        changed = bytearray(original)
        changed[8] ^= 1
        path.write_bytes(changed)
        self.reseal(self.p, path)
        self.reject("IDs-only truth payload differs")

    def test_pipeann_predicate_and_density_must_match_authenticated_workload(self):
        original = export.common.read_json(self.p / "registration.json")
        for field, value in (("predicate", "tag = 1"), ("candidate_count", 170000000), ("selectivity", 0.10)):
            registration = json.loads(json.dumps(original))
            registration["definitions"][1][field] = value
            save(self.p / "registration.json", registration)
            self.reject("predicates disagree|Predicate population differs")

    def test_pipeann_reports_must_equal_logs_and_exclude_warmups(self):
        path = self.p / "results.json"
        original = export.common.read_json(path)
        native = export.common.read_json(self.p / "native-results.json")
        for rows in (original[:-1], original[:-1] + [original[0]], [native[0]] + original[1:]):
            save(path, rows)
            self.reject("measured report is incomplete or includes warmups")
        save(path, original)
        log = self.p / "logs/unfilter.log"
        lines = log.read_text().splitlines()
        result = json.loads(lines[1][7:])
        result["qps"] += 1
        lines[1] = "RESULT " + json.dumps(result)
        log.write_text("\n".join(lines) + "\n")
        self.reject("scenario report differs from RESULT")

    def test_pipeann_native_errors_reject_valid_looking_result_lines(self):
        path = self.p / "logs/unfilter.log"
        original = path.read_text()
        for error in (":ERROR] synthetic read failure", ":FATAL] synthetic failure", "Bad file descriptor"):
            path.write_text(original + error + "\n")
            self.reject("native error diagnostics")

    def test_frozen_launcher_can_differ_from_current_controller_without_rewriting_it(self):
        path = self.p / "source/run_pipeann_selectivity.py"
        path.write_text(path.read_text() + "\n# Synthetic frozen launcher revision.\n")
        self.reseal(self.p, path)
        before = path.read_bytes()
        output = self.assemble()
        manifest = export.common.read_json(output / "assembly_manifest.json")
        current = manifest["source_files"][str(HERE / "run_pipeann_selectivity.py")]
        frozen = manifest["source_files"][str(path)]
        self.assertNotEqual(current["sha256"], frozen["sha256"])
        self.assertEqual(path.read_bytes(), before)

    def test_pipeann_registered_reverse_order_is_not_optional(self):
        registration = export.common.read_json(self.p / "registration.json")
        registration["jobs"][0]["points"] = list(reversed(registration["jobs"][0]["points"]))
        save(self.p / "registration.json", registration)
        self.reject("registered jobs/control order differ")

    def test_authorized_runtime_and_registered_index_cannot_change(self):
        original = self.binary.read_bytes()
        self.binary.write_bytes(original + b"changed")
        self.reject("Registered hash mismatch")
        self.binary.write_bytes(original)
        (self.index / "posting-store.bin").write_bytes(b"CHANGED")
        self.reject("Registered input identity changed")

    def test_failed_or_unrelated_completed_resource_is_rejected(self):
        path = self.p / "logs/unfilter.resources.txt"
        original = path.read_text()
        path.write_text(original.replace("Exit status: 0", "Exit status: 1"))
        self.reject("Incomplete or failed native resource")
        path.write_text("Command terminated by signal 4\n" + original)
        self.reject("Incomplete or failed native resource")
        path.write_text(original.replace("--cpunodebind=3", "--cpunodebind=2"))
        self.reject("resource belongs to another native command")

    def test_source_dates_come_from_completed_resources(self):
        timestamp = datetime(2026, 9, 23, 23, 59, tzinfo=timezone.utc).timestamp()
        path = self.b / "after-normal.resources.txt"
        os.utime(path, (timestamp, timestamp))
        output = self.assemble()
        registration = export.common.read_json(output / "plot_registration.json")
        self.assertEqual(registration["engines"]["SPTAG_H1"]["source_date"], "2026-09-23")
        self.assertEqual(registration["engines"]["PipeANN"]["source_date"], "2026-09-24")

    def test_existing_output_is_never_replaced(self):
        output = self.p / "plot_input"
        output.mkdir()
        sentinel = output / "summary.csv"
        sentinel.write_bytes(b"synthetic protected prior assembly")
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            self.assemble()
        self.assertEqual(sentinel.read_bytes(), b"synthetic protected prior assembly")

    def test_source_mutation_during_writing_removes_only_new_assembly(self):
        original = export.common.write_json
        def mutate(path, value):
            original(path, value)
            if Path(path).name == "plot_registration.json":
                with (self.p / "results.json").open("a") as stream:
                    stream.write("\n")
        with mock.patch.object(export.common, "write_json", side_effect=mutate):
            self.reject("Input changed during assembly")


if __name__ == "__main__":
    unittest.main()
