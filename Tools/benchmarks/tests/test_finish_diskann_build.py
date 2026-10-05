"""Guarded handoff orchestration contracts; no native process or index loads."""

import configparser
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np


HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import finish_diskann_build as finish
import test_sift1b_threeway as contracts


class GuardedStockValidationTest(unittest.TestCase):
    def setUp(self):
        fixture = contracts.ThreewayControllerTest()
        fixture.setUp()
        self.fixture = fixture
        self.addCleanup(fixture.doCleanups)
        self.context, certificate, self.original_certificate, library = fixture.build_validation_fixture()
        self.root = self.context.root
        self.certificate = self.root / "admission.json"
        shutil.copy2(certificate, self.certificate)
        self.calls = []
        calls = self.calls
        certificate_path = self.certificate

        class FakeOriginalRunner:
            def __init__(self, root, config):
                self.root, self.cfg, self.status = root, config, {}

            def update(self, **changes):
                self.status.update(changes)

            def command(self, name, argv):
                calls.append((name, argv))
                self.update(phase=name, child_pid=1234)
                log = self.root / f"{name}.log"
                log.write_text("DISKANN_BUILD_ADMISSION " + certificate_path.read_text().replace("\n", "") + "\n"
                               if name == "admission" else "bounded orchestration fixture\n")
                self.update(child_pid=None)
                return log

        self.registration = {
            "admission_binary": {"path": str(self.root / "fixture-admission")},
            "library": str(library),
        }
        self.verify = mock.Mock()
        self.runner = finish.guarded_runner_class(
            SimpleNamespace(Runner=FakeOriginalRunner), FakeOriginalRunner.command)(
            self.root, self.context, self.registration, self.verify)
        self.resource_guard = mock.patch.object(finish, "check_resources", return_value={})
        self.resource_guard.start()
        self.addCleanup(self.resource_guard.stop)

    def admit(self):
        self.runner.command("admission", self.context.admission_command(
            self.registration["admission_binary"]["path"], self.root))
        self.runner.authorize_stock_validation()

    def inputs(self):
        finish.shared.write_bin(self.root / "validation_queries.u8bin",
                                np.zeros((self.context.query_calls, self.context.dimension), dtype="u1"), "u1")
        (self.root / "validation_filters.txt").write_text(
            "".join(f"{label}\n" * count for label, count in self.context.groups))

    def test_stock_query_is_impossible_before_admission(self):
        with self.assertRaisesRegex(ValueError, "forbidden before admitted"):
            self.runner.command("validate-search", self.context.stock_command(self.root))
        with self.assertRaisesRegex(ValueError, "without successful admission"):
            self.runner.authorize_stock_validation()
        self.assertEqual([], self.calls)

    def test_exact_admission_then_original_stock_argv_records_actual_child(self):
        self.admit()
        self.inputs()
        self.runner.command("validate-search", self.context.stock_command(self.root))
        self.assertEqual(["admission", "validate-search"], [name for name, _ in self.calls])
        admission = finish.shared.read_json(self.root / "admission.execution.json")
        stock = finish.shared.read_json(self.root / "validate-search.execution.json")
        self.assertEqual((1234, 0, "completed"), (stock["pid"], stock["exit_code"], stock["status"]))
        self.assertEqual(str(self.root), stock["cwd"])
        self.assertLessEqual(admission["ended_unix"], stock["started_unix"])
        with self.assertRaisesRegex(ValueError, "Never repeat"):
            self.runner.command("validate-search", self.context.stock_command(self.root))

    def test_rejected_admission_never_reaches_stock_search(self):
        changed = dict(self.original_certificate, status="rejected", error="disconnected")
        finish.shared.write_json(self.certificate, changed)
        with self.assertRaisesRegex(ValueError, "admission mismatch"):
            self.admit()
        with self.assertRaisesRegex(ValueError, "forbidden before admitted"):
            self.runner.command("validate-search", self.context.stock_command(self.root))
        self.assertEqual(["admission"], [name for name, _ in self.calls])

    def test_admitted_certificate_change_stops_before_stock(self):
        self.admit()
        self.inputs()
        self.certificate.write_text(self.certificate.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "Frozen artifact changed"):
            self.runner.command("validate-search", self.context.stock_command(self.root))
        self.assertEqual(["admission"], [name for name, _ in self.calls])

    def test_query_filter_and_argv_changes_never_launch_stock(self):
        self.admit()
        self.inputs()
        filters = self.root / "validation_filters.txt"
        original = filters.read_text()
        filters.write_text("0\n" * self.context.query_calls)
        with self.assertRaisesRegex(ValueError, "admitted all-label"):
            self.runner.command("validate-search", self.context.stock_command(self.root))
        filters.write_text(original)
        arguments = self.context.stock_command(self.root)
        arguments[arguments.index("-L") + 1] = "400"
        with self.assertRaisesRegex(ValueError, "differs from the original"):
            self.runner.command("validate-search", arguments)
        with self.assertRaisesRegex(ValueError, "Unregistered native"):
            self.runner.command("build-memory-index", [])
        self.assertEqual(["admission"], [name for name, _ in self.calls])

    def test_resource_failure_and_marker_mismatch_stop_explicitly(self):
        with mock.patch.object(finish, "check_resources", side_effect=ValueError("reserve reached")):
            with self.assertRaisesRegex(ValueError, "reserve reached"):
                self.admit()
        self.assertEqual([], self.calls)
        self.admit()
        (self.root / "admission.log").write_text("No native certificate emitted\n")
        with self.assertRaisesRegex(ValueError, "zero-search native output"):
            self.runner.authorize_stock_validation()
        self.assertIsNone(self.runner.admitted)

    def test_failed_or_reused_layout_controller_never_launches_validation(self):
        layout = self.root / "layout"
        layout.mkdir()
        finish.shared.write_json(layout / "status.json", {
            "state": "running", "pid": 12, "phase": "layout", "updated_unix": time.time(), "child_pid": 13,
        })
        registration = {"layout_directory": str(layout), "layout_controller": {"pid": 12, "start_ticks": 9}}
        with mock.patch.object(finish.shared, "process_identity", return_value=None):
            with self.assertRaisesRegex(ValueError, "responsive controller"):
                finish.wait_for_layout(self.runner, registration)
        finish.shared.write_json(layout / "failure.json", {"error": "layout failed"})
        with self.assertRaisesRegex(ValueError, "Layout handoff failed"):
            finish.wait_for_layout(self.runner, registration)
        self.assertEqual([], self.calls)

    def test_fresh_output_refusal_precedes_preparation(self):
        with self.assertRaisesRegex(ValueError, "existing validation directory"):
            finish.prepare(self.context.config.path, self.root.parent, self.root, self.root / "absent.json")
        self.assertEqual([], self.calls)

    def loader_binding(self):
        directory = self.root / "loader-only"
        directory.mkdir()
        stock, library = directory / "search_disk_index", directory / "libdiskann.a"
        stock.write_bytes(b"fixture corrected stock")
        library.write_bytes(b"fixture corrected library")
        binding = {
            "mode": "linear-label-delimiters-v1", "path": str(directory / "policy.ini"),
            "sha256": "a" * 64, "proof": str(directory / "proof.json"), "proof_sha256": "b" * 64,
            "library": str(library), "stock_binary": str(stock),
        }
        self.context.loader = {"binding": binding}
        self.registration.update(loader_policy=binding, library=str(library))
        certificate = dict(self.original_certificate, linked_library=str(library),
                           stock_search_binary=str(stock), loader_policy=binding)
        finish.shared.write_json(self.certificate, certificate)
        return binding, certificate

    def test_loader_policy_rebinds_only_executable_and_keeps_original_input_inventory(self):
        binding, certificate = self.loader_binding()
        original = self.context.config.parser
        effective = self.context.validation_parser()
        differences = [(section, key) for section in original.sections() for key in original[section]
                       if original[section][key] != effective[section][key]]
        self.assertEqual([("DiskANN", "binarydirectory")], differences)
        self.assertEqual(self.original_certificate["input_identities"], certificate["input_identities"])
        self.admit()
        self.inputs()
        self.runner.command("validate-search", self.context.stock_command(self.root))
        self.assertEqual(["--loader-policy", binding["path"]], self.calls[0][1][-2:])
        self.assertEqual(binding["stock_binary"], self.calls[1][1][0])
        self.assertEqual(2, len(self.calls))

    def test_unregistered_or_missing_loader_binding_never_authorizes_stock(self):
        binding, certificate = self.loader_binding()
        arguments = self.context.admission_command(self.registration["admission_binary"]["path"], self.root)
        with self.assertRaisesRegex(ValueError, "differs from the original"):
            self.runner.command("admission", arguments[:-2])
        missing = dict(certificate)
        missing.pop("loader_policy")
        finish.shared.write_json(self.certificate, missing)
        with self.assertRaisesRegex(ValueError, "certificate schema"):
            self.admit()
        self.assertIsNone(self.runner.admitted)
        with self.assertRaisesRegex(ValueError, "forbidden before admitted"):
            self.runner.command("validate-search", self.context.stock_command(self.root))

    def test_corrected_certificate_cannot_retarget_original_input_identities(self):
        _, certificate = self.loader_binding()
        changed = dict(certificate, input_identities=certificate["input_identities"][:-1])
        finish.shared.write_json(self.certificate, changed)
        with self.assertRaisesRegex(ValueError, "inventory is incomplete"):
            self.admit()
        self.assertIsNone(self.runner.admitted)


    def test_benchmark_authenticates_the_complete_guarded_execution_chain(self):
        self.admit()
        self.inputs()
        self.runner.command("validate-search", self.context.stock_command(self.root))
        binary = Path(self.registration["admission_binary"]["path"])
        binary.write_bytes(b"not a native executable: orchestration fixture")
        self.registration["admission_binary"] = finish.shared.identity(binary, True)
        acceptance = {
            "status": "accepted", "builds": {"diskannBuildAdmission": {
                "binary": self.registration["admission_binary"],
                "native_artifacts": [{"path": self.registration["library"]}],
            }},
        }
        finish.shared.write_json(self.root / "admission-acceptance.json", acceptance)
        layout = self.root / "layout-contract"
        layout.mkdir()
        layout_records = []
        for name in ("layout_completion.json", "manifest.json", "saved-graph-handoff.json",
                     "controller-retirement.command.json", "create-disk-layout.command.json",
                     "create-disk-layout.log"):
            path = layout / name
            path.write_text("bounded layout provenance fixture\n")
            layout_records.append(finish.shared.identity(path, True))
        (self.root / "failure.json").write_text("fixture original-controller retirement\n")
        layout_records.append(finish.shared.identity(self.root / "failure.json", True))
        finish.shared.write_json(self.root / "layout-evidence.json", {
            "state": "layout_completed", "native_builder_exit_status": 0, "graph_rebuilt": False,
            "production_search_invocations": 0, "index_prefix": str(self.context.prefix),
            "records": layout_records,
        })
        validation = {
            "queries": 3, "topk": 10, "search_l": 32, "distinct_labels_checked": 2,
            "labels_absent_from_fixture": [], "exact_result_distances": True,
            "all_labels_match": True, "no_duplicate_ids": True,
        }
        finish.shared.write_json(self.root / "validation.json", validation)
        self.registration.update(
            purpose=finish.PURPOSE, config=finish.shared.identity(self.context.config.path, True),
            layout_directory=str(layout), frozen=[finish.shared.identity(binary, True),
                                                  finish.shared.identity(self.root / "admission-acceptance.json", True)],
            protected=[],
        )
        finish.shared.write_json(self.root / "registration.json", self.registration)
        for name in ("admission.command.json", "validate-search.command.json",
                     "validation_32_idx_uint32.bin", "validation_32_dists_float.bin"):
            (self.root / name).write_text("bounded raw-output identity fixture\n")
        output_names = [
            "admission.command.json", "admission.log", "validate-search.command.json", "validate-search.log",
            "validation_queries.u8bin", "validation_filters.txt",
            "validation_32_idx_uint32.bin", "validation_32_dists_float.bin",
        ]
        evidence = {
            "schema_version": 1, "status": "completed", "config": self.registration["config"],
            **{key: finish.shared.identity(self.root / name, True) for key, name in (
                ("registration", "registration.json"), ("layout", "layout-evidence.json"),
                ("admission", "admission.json"), ("admission_execution", "admission.execution.json"),
                ("stock_execution", "validate-search.execution.json"), ("validation", "validation.json"))},
            "outputs": [finish.shared.identity(self.root / name, True) for name in output_names],
        }
        finish.shared.write_json(self.root / "guarded-validation.json", evidence)
        completion = {"validation": validation,
                      "guarded_validation": finish.shared.identity(self.root / "guarded-validation.json", True)}
        for name in ("Vectors", "Queries", "Attributes"):
            self.fixture.parser["Dataset"][name] = str(self.context.config.path_value("Inputs", name))
        self.fixture.parser["Dataset"]["Dimension"] = "4"
        self.fixture.parser["DiskANN"]["Library"] = self.registration["library"]
        self.fixture.save()
        verified = finish.shared.validate_guarded_build_completion(self.fixture.config, self.root, completion)
        self.assertEqual(self.runner.admitted, verified["certificate"])
        evidence["outputs"].pop()
        finish.shared.write_json(self.root / "guarded-validation.json", evidence)
        completion["guarded_validation"] = finish.shared.identity(self.root / "guarded-validation.json", True)
        with self.assertRaisesRegex(ValueError, "raw outputs are incomplete"):
            finish.shared.validate_guarded_build_completion(self.fixture.config, self.root, completion)


class NativeExitMonitorTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        parser = configparser.ConfigParser()
        parser.read_dict({"Run": {"MinimumFreeMemoryGiB": "0", "MinimumFreeDiskGiB": "0",
                                 "MaxBuilderRSSGiB": "1"}})
        self.updates = []
        self.runner = SimpleNamespace(root=self.root, cfg=parser, status={}, active_execution={},
                                      update=lambda **changes: self.updates.append(changes))
        self.child = mock.Mock(pid=1234, returncode=0)
        self.child.poll.return_value = None
        self.argv = ["/bounded-native-fixture"]
        self.snapshot = {
            "pid": 1234, "parent_pid": os.getpid(), "start_ticks": 100, "state": "R", "flags": 0,
            "command_sha256": hashlib.sha256(b"/bounded-native-fixture\0").hexdigest(), "rss_bytes": 4096,
        }
        self.expected = {key: self.snapshot[key] for key in ("pid", "parent_pid", "start_ticks")}

    def test_kernel_exit_without_memory_or_command_waits_for_actual_success(self):
        exiting = dict(self.snapshot, flags=0x4, rss_bytes=None,
                       command_sha256=hashlib.sha256(b"").hexdigest())
        self.child.wait.side_effect = [finish.subprocess.TimeoutExpired(self.argv, 15), 0]
        with mock.patch.object(finish.subprocess, "Popen", return_value=self.child), mock.patch.object(
                finish, "native_process_snapshot", side_effect=[self.snapshot, exiting]), mock.patch.object(
                finish.shared, "resources", return_value={"memory_available_bytes": 1 << 30}):
            result = finish.run_native_command(self.runner, "admission", self.argv)
        self.assertEqual(self.root / "admission.log", result)
        self.child.terminate.assert_not_called()
        self.child.kill.assert_not_called()
        self.assertEqual(0, self.runner.active_execution["exit_code"])
        record = json.loads((self.root / "resources.jsonl").read_text())
        self.assertIsNone(record["rss_bytes"])
        self.assertTrue(record["process"]["kernel_exiting"])

    def test_missing_memory_and_changed_identity_do_not_masquerade_as_exit(self):
        for changes, message in (
            ({"rss_bytes": None}, "no VmRSS accounting"),
            ({"flags": 0x4, "rss_bytes": None, "start_ticks": 101}, "identity changed"),
            ({"flags": 0x4, "rss_bytes": None, "parent_pid": os.getpid() + 1}, "identity changed"),
            ({"flags": 0x4, "command_sha256": "0" * 64}, "command changed"),
        ):
            with self.subTest(changes=changes), mock.patch.object(
                    finish, "native_process_snapshot", return_value=dict(self.snapshot, **changes)):
                with self.assertRaisesRegex(ValueError, message):
                    finish.observe_native_child(self.child, self.expected, self.argv)

    def test_reaped_child_and_nonzero_exit_are_not_invented_success(self):
        self.child.poll.return_value = 7
        with mock.patch.object(finish, "native_process_snapshot", side_effect=FileNotFoundError):
            self.assertIsNone(finish.observe_native_child(self.child, self.expected, self.argv))
        self.child.wait.return_value = 7
        with mock.patch.object(finish.subprocess, "Popen", return_value=self.child), mock.patch.object(
                finish, "native_process_snapshot", return_value=self.snapshot):
            with self.assertRaisesRegex(ValueError, "exited 7"):
                finish.run_native_command(self.runner, "admission", self.argv)
        self.assertEqual(7, self.runner.active_execution["exit_code"])

    def test_kernel_exit_keeps_enforcing_host_reserves(self):
        self.runner.cfg["Run"]["MinimumFreeMemoryGiB"] = "2"
        exiting = dict(self.snapshot, flags=0x4, rss_bytes=None)
        self.child.wait.side_effect = [finish.subprocess.TimeoutExpired(self.argv, 15), 0]
        with mock.patch.object(finish.subprocess, "Popen", return_value=self.child), mock.patch.object(
                finish, "native_process_snapshot", side_effect=[self.snapshot, exiting]), mock.patch.object(
                finish.shared, "resources", return_value={"memory_available_bytes": 1 << 30}):
            with self.assertRaisesRegex(ValueError, "memory reserve reached"):
                finish.run_native_command(self.runner, "admission", self.argv)
        self.child.terminate.assert_called_once()

    def test_real_small_process_reports_actual_exit_without_loading_an_index(self):
        finish.run_native_command(self.runner, "success", [sys.executable, "-c", "print('bounded child')"])
        self.assertEqual(0, self.runner.active_execution["exit_code"])
        self.assertEqual("bounded child\n", (self.root / "success.log").read_text())
        with self.assertRaisesRegex(ValueError, "exited 7"):
            finish.run_native_command(self.runner, "failure", [sys.executable, "-c", "raise SystemExit(7)"])
        self.assertEqual(7, self.runner.active_execution["exit_code"])


if __name__ == "__main__":
    unittest.main()
