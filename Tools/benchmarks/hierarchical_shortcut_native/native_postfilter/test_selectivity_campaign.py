import configparser
import errno
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

from run_postgraph import GRID, VARIANTS
from run_selectivity import plan_schedule, read_budgets, run, validate_point
from selectivity_common import confine_outputs, inventory


def control_config():
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config["SearchSSDIndex"] = {"PostingAnchorCount": "8"}
    for variant, graph, enabled, extra in (
        ("graph", 2048, False, 0), ("postgraph_shared", 2048, True, 0),
        ("postgraph_extra", 2048, True, 2048), ("graph_total", 4096, False, 0),
    ):
        config[f"Variant.{variant}"] = dict(MaxCheck=str(graph),
            EnablePostingNavigation=str(enabled), PostingAdditionalMaxCheck=str(extra))
    return config


class SelectivityCampaignTest(unittest.TestCase):
    def test_complete_balanced_schedule_including_unfilter(self):
        scenarios = ["unfilter", "broad_tag", "medium_tag", "sel_01pct", "mixed_dnf"]
        cases = plan_schedule(scenarios, read_budgets(control_config()))
        ordinary = [case for case in cases if case["kind"] == "plain"]
        diagnostic = [case for case in cases if case["kind"] == "diagnostic"]
        self.assertEqual(len(ordinary), 40)
        self.assertEqual(len(diagnostic), 20)
        first, second = ordinary[:20], ordinary[20:]
        self.assertEqual([(c["scenario"], c["variant"]) for c in first],
                         [(c["scenario"], c["variant"]) for c in reversed(second)])
        self.assertEqual(first[0]["scenario"], "unfilter")
        self.assertEqual({c["variant"] for c in first}, set(VARIANTS))
        self.assertTrue(all(c["probes"] == GRID and c["queries"] == 1000 for c in cases))

    def test_nominal_budget_controls_cannot_drift(self):
        for key, value in (("MaxCheck", "2048"), ("EnablePostingNavigation", "true"),
                           ("PostingAdditionalMaxCheck", "1")):
            config = control_config()
            config["Variant.graph_total"][key] = value
            with self.assertRaises(RuntimeError):
                read_budgets(config)

    def test_inventory_follows_directory_alias_without_reading_payloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index, source = root / "index", root / "source"
            index.mkdir()
            source.mkdir()
            payload = source / "payload.bin"
            with payload.open("wb") as stream:
                stream.truncate(1_800_922_103_808)
            (index / "catalog").symlink_to(source, target_is_directory=True)
            (source / "cycle").symlink_to(index, target_is_directory=True)
            entries = inventory(index)
            self.assertEqual(set(entries), {"catalog/payload.bin"})
            self.assertEqual(entries["catalog/payload.bin"]["size"], 1_800_922_103_808)

    def test_native_child_cannot_write_index_through_alias(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            source = root / "source"
            source.write_bytes(b"immutable")
            (output / "alias").symlink_to(source)
            pid = os.fork()
            if pid == 0:
                try:
                    confine_outputs(str(output))
                    try:
                        (output / "alias").write_bytes(b"wrong")
                    except OSError as error:
                        if error.errno not in (errno.EACCES, errno.EPERM):
                            raise
                    else:
                        raise AssertionError("Protected source was writable")
                    (output / "allowed").write_bytes(b"okay")
                    os._exit(0)
                except BaseException:
                    os._exit(1)
            _, status = os.waitpid(pid, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)
            self.assertEqual(source.read_bytes(), b"immutable")
            self.assertEqual((output / "allowed").read_bytes(), b"okay")

    def test_native_result_protocol_is_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = plan_schedule(["unfilter"], read_budgets(control_config()))[0]
            case.update(native_case_id="Case1", config=str(root / "native.ini"))
            folder = root / case["case"] / "nprobe_16"
            folder.mkdir(parents=True)
            ids = np.tile(np.arange(10, dtype="<i4"), (1000, 1))
            ids.tofile(folder / "ids.i32")
            np.tile(np.arange(10, dtype="<f4"), (1000, 1)).tofile(folder / "dist.f32")
            np.zeros((1000, 8), dtype="<u8").tofile(folder / "work.u64")
            np.ones(1000, dtype="<f8").tofile(folder / "latency_us.f64")
            np.save(root / "truth.npy", ids.astype("<i8"))
            workload = dict(predicates={"unfilter": None},
                            truth={"unfilter": {"ids": str(root / "truth.npy")}})
            row = dict(nprobe=16, queries=1000, diagnostic=False, navigation_schema_version=6,
                navigation_columns=57, mode="graph", value_type="UInt8", search_posting_page_limit=3,
                max_check=2048, posting_anchor_count=8, posting_additional_max_check=0,
                qps=1000, mean_ms=1, event="point", warmup_queries=1000,
                measured_queries=1000, replay_queries=1000, case_id="Case1",
                config=case["config"], output_directory=str(root / case["case"]))
            validated = validate_point(root, case, dict(row), workload, np.zeros((10, 2), dtype=np.uint32), False)
            self.assertEqual(validated["recall"], 1)
            self.assertTrue(validated["timing_accepted"])
            for key, value in (("value_type", "Float"), ("search_posting_page_limit", 15),
                               ("queries", 32), ("max_check", 4096), ("qps", float("nan")),
                               ("replay_queries", 32), ("case_id", "Case2"),
                               ("output_directory", str(root / "wrong"))):
                wrong = dict(row)
                wrong[key] = value
                with self.assertRaises(RuntimeError):
                    validate_point(root, case, wrong, workload, np.zeros((10, 2), dtype=np.uint32), False)

    def test_complete_batch_controller_lifecycle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = root / "index"
            index.mkdir()
            attributes_path = root / "attributes.npy"
            np.save(attributes_path, np.zeros((10, 2), dtype=np.uint32))
            ids = np.tile(np.arange(10, dtype="<i4"), (1000, 1))
            np.save(root / "truth.npy", ids.astype("<i8"))
            workload = dict(attributes=str(attributes_path), predicates={"unfilter": None},
                            truth={"unfilter": {"ids": str(root / "truth.npy")}})
            (root / "workloads.json").write_text(json.dumps(workload))
            case = plan_schedule(["unfilter"], read_budgets(control_config()))[0]
            case.update(native_case_id="Case1", config=str(root / "native.ini"))
            registration = dict(schedule=[case], workload=str(root / "workloads.json"),
                binaries={"Binary": {"path": "/test/fake-native"}},
                cpu_node=0, memory_node=0, protected_large={}, index=str(index), index_files={})

            def execute_fixture(command, **kwargs):
                log = kwargs["stdout"]
                log.write("authenticated numeric lanes=1\n")
                for probe in GRID:
                    folder = root / case["case"] / f"nprobe_{probe}"
                    folder.mkdir(parents=True)
                    ids.tofile(folder / "ids.i32")
                    np.tile(np.arange(10, dtype="<f4"), (1000, 1)).tofile(folder / "dist.f32")
                    np.zeros((1000, 8), dtype="<u8").tofile(folder / "work.u64")
                    np.ones(1000, dtype="<f8").tofile(folder / "latency_us.f64")
                    log.write(json.dumps(dict(nprobe=probe, queries=1000, diagnostic=False,
                        navigation_schema_version=6, navigation_columns=57, mode="graph",
                        value_type="UInt8", search_posting_page_limit=3, max_check=2048,
                        posting_anchor_count=8, posting_additional_max_check=0,
                        qps=1000, mean_ms=1, event="point", warmup_queries=1000,
                        measured_queries=1000, replay_queries=1000, case_id="Case1",
                        config=case["config"], output_directory=str(root / case["case"]))) + "\n")
                log.flush()
                (root / "plain-resources.txt").write_text("Maximum resident set size (kbytes): 1024\n")
                process = mock.Mock(pid=0, returncode=0)
                process.poll.return_value = 0
                return process

            with mock.patch("run_selectivity.load_registered", return_value=({}, root, registration)), \
                    mock.patch("run_selectivity.native_identity", return_value=None), \
                    mock.patch("run_selectivity.subprocess.Popen", side_effect=execute_fixture):
                run(root / "unused.ini", "plain")
            self.assertEqual(json.loads((root / "status.json").read_text())["state"], "plain_complete")
            self.assertEqual(len(json.loads((root / "plain-results.json").read_text())), len(GRID))
            events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
            self.assertEqual(events[0]["event"], "batch_started")
            self.assertEqual(events[-1]["event"], "batch_complete")
            self.assertTrue(all(event["phase"] == "plain" for event in events))


if __name__ == "__main__":
    unittest.main()
