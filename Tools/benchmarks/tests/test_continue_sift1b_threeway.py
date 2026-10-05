"""No-index contracts for the explicitly authorized throughput continuation."""

import configparser
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import continue_sift1b_threeway as continuation
import run_sift1b_threeway as runner


class ContinuationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(runner.DEFAULT_CONFIG)
        self.previous_path = self.root / "previous.ini"
        with self.previous_path.open("x") as stream:
            parser.write(stream)
        self.previous = runner.Profile(self.previous_path)
        parser["Run"]["OutputDirectory"] = str(self.root / "continued")
        parser["Run"]["PreparedDirectory"] = str(self.root / "continued/inputs")
        parser["PipeANN"]["SourceDirectory"] = str(self.root / "corrected/source")
        parser["PipeANN"]["BenchmarkBinary"] = str(self.root / "corrected/bin/pipeannBench")
        self.parser = parser
        self.path = self.root / "continued.ini"
        self.save()

    def save(self):
        with self.path.open("w") as stream:
            self.parser.write(stream)
        self.config = runner.Profile(self.path)

    def test_only_approved_artifact_and_output_fields_can_change(self):
        changes = continuation.config_delta(self.previous, self.config)
        self.assertEqual(4, len(changes))
        for section, key, value in (
            ("PipeANN", "PipelineWidth", "16"), ("DiskANN", "LSweep", "10,400"),
            ("Throughput", "Threads", "1,2"), ("Throughput", "Repeats", "1"),
            ("Run", "MinimumFreeDiskGiB", "1"), ("Dataset", "Attributes", "/replacement"),
        ):
            with self.subTest(section=section, key=key):
                old = self.parser[section][key]
                self.parser[section][key] = value
                self.save()
                with self.assertRaisesRegex(ValueError, "only output paths"):
                    continuation.config_delta(self.previous, self.config)
                self.parser[section][key] = old
        self.save()

    def test_added_keys_and_undeclared_library_replacement_are_rejected(self):
        self.parser["PipeANN"]["UnregisteredBudget"] = "10"
        self.save()
        with self.assertRaisesRegex(ValueError, "INI keys"):
            continuation.config_delta(self.previous, self.config)
        del self.parser["PipeANN"]["UnregisteredBudget"]
        self.parser["PipeANN"]["SourceDirectory"] = self.previous.section("PipeANN")["SourceDirectory"]
        self.save()
        with self.assertRaisesRegex(ValueError, "only output paths"):
            continuation.config_delta(self.previous, self.config)

    def test_native_count_transition_keeps_the_native_core_and_search_grid(self):
        self.parser["PipeANN"]["SourceDirectory"] = self.previous.section("PipeANN")["SourceDirectory"]
        self.parser["PipeANN"]["AllowShortResults"] = "true"
        self.save()
        changes = continuation.config_delta(self.previous, self.config)
        self.assertEqual({("run", "outputdirectory"), ("run", "prepareddirectory"),
                          ("pipeann", "benchmarkbinary"), ("pipeann", "allowshortresults")},
                         {(row["section"], row["key"]) for row in changes})
        self.parser["PipeANN"]["SourceDirectory"] = str(self.root / "another-core")
        self.save()
        with self.assertRaisesRegex(ValueError, "only output paths"):
            continuation.config_delta(self.previous, self.config)

    def test_remaining_phases_cannot_rerun_retained_measurements(self):
        fake = SimpleNamespace(config=self.config)
        for engines in ((), ("SPTAG_adaptive",), ("unregistered-engine",)):
            with self.subTest(engines=engines), self.assertRaisesRegex(ValueError, "must not repeat"):
                runner.Campaign.finish_throughput(fake, [{"engine": "SPTAG_adaptive"}], [], [], engines)
        self.assertEqual(("PipeANN", "Filtered_DiskANN"), continuation.REMAINING)

    def test_ordinal_plans_and_frozen_execution_are_required(self):
        path = self.root / "plan.tsv"
        path.write_text("scenario\tcontrol_index\tthread_index\trepeat\nbroad_tag\t10\t1\t1\n")
        self.assertEqual([{"scenario": "broad_tag", "control_index": 10, "thread_index": 1, "repeat": 1}],
                         continuation.read_plan(path))
        path.write_text("scenario\tL\tthreads\trepeat\nbroad_tag\t120\t2\t1\n")
        with self.assertRaisesRegex(ValueError, "ordinal plan"):
            continuation.read_plan(path)
        with self.assertRaisesRegex(ValueError, "frozen continuation"):
            continuation.run(self.root)


if __name__ == "__main__":
    unittest.main()
