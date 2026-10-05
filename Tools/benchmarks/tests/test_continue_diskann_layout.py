"""Bounded continuation contracts; no production index loads or native searches."""

import os
from pathlib import Path
import struct
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import continue_diskann_layout as continuation


shared = continuation.shared
finish = continuation.finish


class LayoutContinuationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.build = self.base / "build"
        self.build.mkdir()
        self.root = self.build / "new-layout"
        self.root.mkdir()
        self.original = self.build / "config.ini"
        self.original.write_text(
            "[Run]\nMinimumFreeDiskGiB=100\nMinimumFreeMemoryGiB=256\nMaxBuilderRSSGiB=768\n"
            "ValidationThreads=1\nValidationK=10\nValidationL=256\n"
            f"[DiskANN]\nR=64\nL=1\nFilteredL=100\nBinaryDirectory={self.base / 'bin'}\n"
            f"[Inputs]\nPQPrefix={self.base / 'pq'}\nVectors={self.base / 'base.u8bin'}\n")
        self.config = shared.Config(self.original)
        self.context = SimpleNamespace(
            config=self.config, root=self.build, prefix=self.build / "sift1b", rows=64, dimension=128)
        self.ini = self.root / "continuation.ini"
        self.ini.write_text(
            f"[Continuation]\nOriginalConfig={self.original}\n"
            f"PreviousLayoutDirectory={self.build / 'old-layout'}\n"
            f"PreviousCampaignDirectory={self.base / 'old-campaign'}\nOutputDirectory={self.root}\n"
            "[Run]\nMinimumFreeDiskGiB=64\n"
            f"[Authorization]\nAction={shared.DISK_RESERVE_AUTHORIZATION}\n")
        self.write_policy()
        self.preserved = self.base / "preserved"
        self.preserved.mkdir()
        for name in ("graph_labels.txt", "graph_labels_map.txt", "graph_labels_to_medoids.txt"):
            (self.preserved / name).write_bytes(b"fixture labels\n")
        graph = struct.pack("<QIIQ", 24 + 64 * 20, 4, 0, 0)
        graph += b"".join(struct.pack("<5I", 4, *((node + offset) % 64 for offset in (1, 2, 3, 4)))
                          for node in range(64))
        (self.preserved / "graph").write_bytes(graph)
        (self.base / "pq_pq_pivots.bin").write_bytes(b"fixture pivots")
        self.partial = Path(str(self.context.prefix) + "_disk.index")
        self.partial.write_bytes(bytes(4096 * 2))
        self.width = 4
        self.registration = {
            "resource_policy": self.policy,
            "partial": continuation.partial_record(self.context, self.width),
        }

    def write_policy(self):
        shared.write_json(self.root / "resource-policy.json", {
            "schema_version": 1, "purpose": "authorized-post-build-disk-reserve",
            "authorization": shared.DISK_RESERVE_AUTHORIZATION,
            "configuration": shared.identity(self.ini, True),
            "original_config": shared.identity(self.original, True),
            "original_minimum_free_disk_gib": 100, "minimum_free_disk_gib": 64,
        })
        self.policy = shared.layout_disk_reserve_policy(self.config, self.root)

    def resources(self, free=90 * shared.GIB):
        return mock.patch.object(shared, "resources", return_value={
            "memory_available_bytes": 256 * shared.GIB, "aio_nr": 0, "aio_max_nr": 65536,
        }), mock.patch.object(finish.shutil, "disk_usage", return_value=SimpleNamespace(free=free))

    def test_only_disk_reserve_changes_and_original_ini_stays_identical(self):
        before = self.original.read_bytes()
        runtime = shared.build_resource_parser(self.config, self.policy)
        differences = [(section, key) for section in runtime.sections() for key in runtime[section]
                       if runtime[section][key] != self.config.parser[section][key]]
        self.assertEqual([("Run", "minimumfreediskgib")], differences)
        self.assertEqual(64, runtime["Run"].getint("MinimumFreeDiskGiB"))
        self.assertEqual(before, self.original.read_bytes())
        self.assertEqual(100, self.config.section("Run").getint("MinimumFreeDiskGiB"))

    def test_unauthorized_or_additional_overrides_fail(self):
        text = self.ini.read_text()
        for changed in (
                text.replace("MinimumFreeDiskGiB=64", "MinimumFreeDiskGiB=63"),
                text.replace(shared.DISK_RESERVE_AUTHORIZATION, "not-approved"),
                text.replace("MinimumFreeDiskGiB=64", "MinimumFreeDiskGiB=64\nValidationL=400"),
                text + "\n[DiskANN]\nR=32\n"):
            with self.subTest(changed=changed):
                self.ini.write_text(changed)
                with self.assertRaises(ValueError):
                    shared.load_layout_continuation_config(self.ini)
        self.ini.write_text(text)

    def test_frozen_policy_mutation_stops_before_native_work(self):
        self.ini.write_text(self.ini.read_text() + "\n; changed after authorization\n")
        with self.assertRaisesRegex(ValueError, "Frozen artifact changed"):
            shared.build_resource_parser(self.config, self.policy)
        with self.assertRaisesRegex(ValueError, "immutable INI"):
            shared.layout_disk_reserve_policy(self.config, self.root)

    def test_resource_guard_uses_exact_authorized_boundary(self):
        memory, disk = self.resources()
        with memory, disk:
            with self.assertRaisesRegex(ValueError, "disk reserve"):
                finish.check_resources(self.root, self.config)
            finish.check_resources(self.root, self.config, self.policy)
        for free, allowed in ((64 * shared.GIB, True), (64 * shared.GIB - 1, False)):
            memory, disk = self.resources(free)
            with self.subTest(free=free), memory, disk:
                if allowed:
                    finish.check_resources(self.root, self.config, self.policy)
                else:
                    with self.assertRaisesRegex(ValueError, "disk reserve"):
                        finish.check_resources(self.root, self.config, self.policy)

    def test_guarded_native_runner_receives_policy_not_original_limit(self):
        class BaseRunner:
            def __init__(self, root, parser):
                self.cfg = parser

        runner = finish.guarded_runner_class(SimpleNamespace(Runner=BaseRunner))(
            self.root, self.context, self.registration, mock.Mock())
        self.assertEqual(64, runner.cfg["Run"].getint("MinimumFreeDiskGiB"))
        self.assertEqual(100, self.config.section("Run").getint("MinimumFreeDiskGiB"))

    def test_initialized_or_complete_layout_cannot_be_deleted(self):
        for payload, error in (
                (b"x" + bytes(8191), "initialized"),
                (bytes(continuation.expected_layout_bytes(self.context, self.width)), "incomplete")):
            with self.subTest(error=error):
                self.partial.write_bytes(payload)
                with self.assertRaisesRegex(ValueError, error):
                    continuation.partial_record(self.context, self.width)

    def test_aliases_cannot_be_deleted_as_owned_partial(self):
        other = self.base / "other"
        os.link(self.partial, other)
        with self.assertRaisesRegex(ValueError, "singly owned"):
            continuation.partial_record(self.context, self.width)
        self.partial.unlink()
        self.partial.symlink_to(other)
        with self.assertRaisesRegex(ValueError, "singly owned"):
            continuation.partial_record(self.context, self.width)
        self.assertTrue(other.is_file())

    def test_changed_partial_stops_without_deleting_any_file(self):
        with self.partial.open("r+b") as stream:
            stream.seek(5000)
            stream.write(b"x")
        with self.assertRaisesRegex(ValueError, "changed since"):
            continuation.discard_partial(self.root, self.context, self.registration, self.preserved, self.width)
        self.assertTrue(self.partial.is_file())
        self.assertFalse((self.root / "discarded-partial-layout.json").exists())

    def test_capacity_shortfall_stops_before_unlink(self):
        with mock.patch.object(continuation.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(ValueError, "Insufficient capacity"):
                continuation.discard_partial(self.root, self.context, self.registration, self.preserved, self.width)
        self.assertTrue(self.partial.is_file())

    def test_slow_unlink_keeps_supervisor_heartbeat_without_early_success(self):
        pending = mock.Mock()
        pending.result.return_value = None
        executor = mock.Mock()
        executor.submit.return_value = pending
        heartbeat = mock.Mock()
        with mock.patch.object(continuation, "ThreadPoolExecutor") as pool, \
                mock.patch.object(continuation, "wait", side_effect=[
                    SimpleNamespace(done=set()), SimpleNamespace(done={pending})]):
            pool.return_value.__enter__.return_value = executor
            continuation.unlink_with_heartbeat(self.partial, heartbeat)
        self.assertEqual(3, heartbeat.call_count)
        executor.submit.assert_called_once_with(self.partial.unlink)
        pending.result.assert_called_once_with()
        self.assertTrue(self.partial.exists())

    def test_unlink_worker_failure_is_not_a_successful_cleanup(self):
        with mock.patch.object(Path, "unlink", side_effect=OSError("fixture unlink failure")):
            with self.assertRaisesRegex(OSError, "unlink failure"):
                continuation.unlink_with_heartbeat(self.partial, mock.Mock())
        self.assertTrue(self.partial.exists())

    def test_cleanup_removes_only_the_authorized_partial_and_records_identity(self):
        before = {path: path.read_bytes() for path in self.preserved.iterdir()}
        memory, disk = self.resources()
        with memory, disk:
            continuation.discard_partial(self.root, self.context, self.registration, self.preserved, self.width)
        self.assertFalse(self.partial.exists())
        record = shared.read_json(self.root / "discarded-partial-layout.json")
        self.assertEqual([str(self.partial)], record["removed_paths"])
        self.assertEqual(self.registration["partial"], record["partial"])
        self.assertEqual(0, record["native_search_invocations"])
        for path, content in before.items():
            self.assertEqual(content, path.read_bytes())

    def test_saved_graph_still_requires_original_success_and_sample_evidence(self):
        handoff = {
            "user_authorized": True, "native_builder_exit_status": 0, "graph_rebuilt": False,
            "saved_vectors": {"exact_original_vector_samples": True, "sample_count": 64},
            "graph_inputs": [shared.original_build_identity(path) for path in self.preserved.iterdir()],
        }
        self.assertEqual((self.preserved, 4), continuation.saved_graph(self.context, handoff))
        for key, value in (("native_builder_exit_status", 1), ("graph_rebuilt", True), ("user_authorized", False)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                continuation.saved_graph(self.context, {**handoff, key: value})

    def test_complete_continuation_evidence_and_failure_rejections(self):
        memory, disk = self.resources()
        with memory, disk:
            continuation.discard_partial(self.root, self.context, self.registration, self.preserved, self.width)
        self.registration.update(
            purpose=continuation.PURPOSE, original_config=shared.identity(self.original, True),
            frozen=[self.policy["record"], self.policy["configuration"]], previous_records=[], protected=[])
        argv = [str(self.base / "bin/create_disk_layout"), "uint8", str(self.base / "base.u8bin"),
                str(self.preserved / "graph"), str(self.partial)]
        self.registration["argv"] = argv
        shared.write_json(self.root / "layout-continuation-registration.json", self.registration)
        shared.write_json(self.root / "saved-graph-handoff.json", {
            "graph_inputs": [shared.original_build_identity(self.preserved / "graph")],
        })
        shared.write_json(self.root / "create-disk-layout.command.json", argv)
        now = time.time()
        execution = {
            "status": "completed", "exit_code": 0, "pid": 1234, "cwd": str(self.root), "argv": argv,
            "minimum_free_disk_gib": 64, "started_unix": now, "ended_unix": now + 1,
        }
        shared.write_json(self.root / "create-disk-layout.execution.json", execution)

        def update_evidence():
            shared.write_json(self.root / "layout-continuation-evidence.json", {
                "status": "completed", "graph_rebuilt": False, "native_search_invocations": 0,
                "registration": shared.identity(self.root / "layout-continuation-registration.json", True),
                "discarded_partial": shared.identity(self.root / "discarded-partial-layout.json", True),
                "execution": shared.identity(self.root / "create-disk-layout.execution.json", True),
                "resource_policy": self.policy,
            })

        update_evidence()
        self.assertTrue(shared.validate_layout_continuation(self.context, self.root, self.policy))
        for key, value in (("status", "failed"), ("exit_code", 1), ("pid", 0),
                           ("minimum_free_disk_gib", 100), ("started_unix", 1)):
            with self.subTest(key=key):
                shared.write_json(self.root / "create-disk-layout.execution.json", {**execution, key: value})
                update_evidence()
                with self.assertRaises(ValueError):
                    shared.validate_layout_continuation(self.context, self.root, self.policy)
        shared.write_json(self.root / "create-disk-layout.execution.json", execution)
        discarded = shared.read_json(self.root / "discarded-partial-layout.json")
        shared.write_json(self.root / "discarded-partial-layout.json",
                          {**discarded, "removed_paths": [str(self.partial), str(self.original)]})
        update_evidence()
        with self.assertRaisesRegex(ValueError, "restrict cleanup"):
            shared.validate_layout_continuation(self.context, self.root, self.policy)

    def test_existing_output_refused_before_original_build_is_loaded(self):
        with mock.patch.object(shared, "DiskANNBuildValidation") as context:
            with self.assertRaisesRegex(ValueError, "must be fresh"):
                continuation.prepare(self.ini)
            context.assert_not_called()


if __name__ == "__main__":
    unittest.main()
