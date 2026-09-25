import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import run_pipeann_selectivity as runner
from sift1b_official_inputs import scenario_contract, validate_filter_config, write_bin, write_binding


class PipeANNSelectivityTest(unittest.TestCase):
    def config(self):
        return runner.config_at(HERE / "configs/sift1b_pipeann_curves/benchmark.ini")

    def test_fixed_native_scenarios_and_tag169_encoding(self):
        config = self.config()
        for name in runner.SCENARIOS[1:]:
            validate_filter_config(config, name, scenario_contract(config, name))
        contract = scenario_contract(config, "sel_01pct")
        self.assertEqual(contract["clauses"], [[(0, 0, 0, 169)]])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_binding(root / "tag.spmat", [169], 1000, "label")
            raw = (root / "tag.spmat").read_bytes()
            self.assertEqual(struct.unpack("<qqq", raw[:24]), (1000, 201, 1000))
            ids = np.frombuffer(raw, dtype="<i4", count=1000, offset=24 + 1001 * 8)
            self.assertTrue(np.all(ids == 169))
            truth = np.arange(10000, dtype="<i8").reshape(1000, 10)
            write_bin(root / "gt.ibin", truth, "<u4")
            self.assertEqual((root / "gt.ibin").stat().st_size, 40008)
            self.assertEqual(struct.unpack("<II", (root / "gt.ibin").read_bytes()[:8]), (1000, 10))
            np.testing.assert_array_equal(np.fromfile(root / "gt.ibin", dtype="<u4", offset=8).reshape(1000, 10),
                                          truth)

    def test_adaptation_requires_its_explicit_manifest_pin(self):
        profile = HERE / "configs/sift1b_pipeann_curves/benchmark_v2.ini"
        config = runner.config_at(profile)
        self.assertEqual(config.section("PipeANN")["ReadOnlyAdaptationSHA256"],
                         "cef5aeae4f67a3a54a2df5fcf0249895e9ac24621dcda9031e5eb927aa47546f")
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / "benchmark.ini"
            changed.write_text(profile.read_text().replace(
                "ReadOnlyAdaptationSHA256=cef5aeae4f67a3a54a2df5fcf0249895e9ac24621dcda9031e5eb927aa47546f\n", ""))
            with self.assertRaisesRegex(ValueError, "Unknown/missing"):
                runner.config_at(changed)

    def test_jobs_keep_native_argument_order_and_two_warmed_passes(self):
        config = self.config()
        definitions = [dict(name=name, truth_bin=f"gt_{name}.ibin") for name in runner.SCENARIOS]
        binaries = {name: dict(path=f"/frozen/{name}", sha256="0" * 64)
                    for name in ("search_disk_index", "search_disk_index_filtered")}
        jobs = runner.native_jobs(config, definitions, binaries, Path("/new/campaign"))
        self.assertEqual(len(jobs), 5)
        for job in jobs:
            command = job["command"]
            self.assertEqual(command[1:2], ["uint8"])
            self.assertEqual(command[3:5], ["1", "32"])
            self.assertEqual(command[7:10], ["10", "l2", "pq"])
            self.assertEqual(command[11], "10" if job["scenario"] == "unfilter" else "0")
            self.assertEqual(len(job["points"]), 52)
            self.assertEqual(sum(p["stage"] == "measured" for p in job["points"]), 26)
            for first, second in zip(job["points"][::2], job["points"][1::2]):
                self.assertEqual(first["L"], second["L"])
                self.assertEqual((first["stage"], second["stage"]), ("warmup", "measured"))
            self.assertEqual([p["L"] for p in job["points"][:26:2]], runner.LS)
            self.assertEqual([p["L"] for p in job["points"][26::2]], list(reversed(runner.LS)))
            if job["scenario"] != "unfilter":
                self.assertEqual(command[-1], "--filter-mode=auto")

    def test_native_launcher_passes_multiple_arguments_and_confines_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            protected = root / "index"
            protected.write_bytes(b"immutable")
            code = (
                "from pathlib import Path; import sys; "
                "Path(sys.argv[1]).write_text(sys.argv[3]); "
                "\ntry: Path(sys.argv[2]).write_bytes(b'changed')"
                "\nexcept PermissionError: pass"
                "\nelse: raise RuntimeError('escaped confinement')"
            )
            subprocess.run([sys.executable, "-B", str(HERE / "run_pipeann_selectivity.py"),
                "native", str(output), sys.executable, "-c", code, str(output / "ok"),
                str(protected), "arguments-preserved"], check=True, capture_output=True, text=True)
            self.assertEqual((output / "ok").read_text(), "arguments-preserved")
            self.assertEqual(protected.read_bytes(), b"immutable")

    def test_unknown_configuration_does_not_silently_override_search(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "benchmark.ini"
            source = HERE / "configs/sift1b_pipeann_curves/benchmark.ini"
            shutil.copy2(source, path)
            path.write_text(path.read_text().replace("PipelineWidth=32", "PipelineWidth=32\nUnknownBudget=10"))
            with self.assertRaisesRegex(ValueError, "Unknown/missing"):
                runner.config_at(path)

    def test_zero_exit_metrics_do_not_hide_native_io_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "native.log"
            point = dict(L=10, value=10, stage="measured", repeat=1)
            job = dict(engine="PipeANN", scenario="unfilter", points=[point])
            result = dict(has_recall=True, L=10, recall_percent=.93, qps=8707)
            log.write_text("[linux_aligned_file_reader.cpp:724:ERROR] Failed Bad file descriptor\n"
                           + "RESULT " + json.dumps(result) + "\n")
            with self.assertRaisesRegex(ValueError, "results are invalid"):
                runner.validated_results(log, job)
            log.write_text("RESULT " + json.dumps(result) + "\n")
            self.assertAlmostEqual(runner.validated_results(log, job)[0]["recall"], .0093)


if __name__ == "__main__":
    unittest.main()
