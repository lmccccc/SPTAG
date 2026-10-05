import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import diagnose_sift1b_medium as diagnosis
import measure_sift1b_medium_expansion as measurement


class CaseFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.destination = self.root / "diagnostic"
        self.reference = SimpleNamespace(root=self.root / "reference")
        self.batch_path = self.root / "batch.ini"
        self.native_path = self.root / "medium.ini"
        self.templates = {}
        self.points = []
        for scenario in diagnosis.shared.SCENARIOS:
            path = self.root / (scenario + ".ini")
            self.points.append(dict(scenario=scenario, setting="base", config=str(path)))
            self.templates[path] = dict(
                searchssdindex=dict(internalresultnum="384", maxcheck="2048", numberofthreads="1",
                                    resultnum="100", postingadditionalmaxcheck="2048",
                                    postingnavigationwidth="8", enablepostingnavigation="true"),
                benchmark=dict(index="/original/index", queries="/original/queries.npy",
                               predicatefile="/original/" + scenario + ".npy",
                               maxqueries="1000", warmup="1000"),
                searchsweep=dict(nprobe="[384]"))
        self.native = copy.deepcopy(self.templates[self.root / "medium_tag.ini"])
        self.native["benchmark"].update(maxqueries="1000", warmup="1000")
        self.templates[self.native_path] = self.native
        self.batch = dict(batch=dict(casecount="1", warmuppolicy="once"),
                          case1=dict(config=str(self.native_path),
                                     outputdirectory=str(self.destination / "native/SPTAG/r1_base_medium_tag")))
        self.templates[self.batch_path] = self.batch


class DiagnosticCaseTest(CaseFixture):
    def read(self):
        with mock.patch.object(diagnosis, "read_json", return_value=self.points), \
                mock.patch.object(diagnosis, "read_config", side_effect=lambda path: self.templates[Path(path)]):
            return diagnosis.read_cases(self.batch_path, self.destination, self.reference)

    def test_native_cases_retain_original_predicate_and_explicit_cohort(self):
        cases = self.read()
        self.assertEqual(1, len(cases))
        self.assertEqual("medium_tag", cases[0]["scenario"])
        self.assertEqual(1000, cases[0]["queries"])
        self.assertEqual([384], cases[0]["probes"])

    def test_only_declared_search_parameters_may_change(self):
        self.native["searchssdindex"].update(maxcheck="32768", postingadditionalmaxcheck="32768",
                                             postingnavigationwidth="0")
        self.assertEqual("32768", self.read()[0]["search"]["maxcheck"])
        self.native["searchssdindex"]["numberofthreads"] = "2"
        with self.assertRaisesRegex(ValueError, "unrelated search parameter"):
            self.read()

    def test_original_input_replacement_is_rejected(self):
        self.native["benchmark"]["index"] = "/other/index"
        with self.assertRaisesRegex(ValueError, "inputs differ"):
            self.read()

    def test_under_topk_probe_is_rejected(self):
        self.native["searchsweep"]["nprobe"] = "[99]"
        with self.assertRaisesRegex(ValueError, "probe sweep"):
            self.read()

    def test_no_repeated_warmup_or_invalid_query_count(self):
        self.batch["batch"]["warmuppolicy"] = "per_point"
        with self.assertRaisesRegex(ValueError, "one batch warmup"):
            self.read()
        self.batch["batch"]["warmuppolicy"] = "once"
        self.native["benchmark"]["maxqueries"] = "0"
        with self.assertRaisesRegex(ValueError, "cohort"):
            self.read()
        self.native["benchmark"].update(maxqueries="128", warmup="128")
        with self.assertRaisesRegex(ValueError, "native client requires"):
            self.read()

    def test_outputs_cannot_escape_or_overwrite(self):
        self.batch["case1"]["outputdirectory"] = str(self.reference.root / "r1_base_medium_tag")
        with self.assertRaisesRegex(ValueError, "fresh campaign output"):
            self.read()
        output = self.destination / "native/SPTAG/r1_base_medium_tag"
        self.batch["case1"]["outputdirectory"] = str(output)
        output.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "existing native output"):
            self.read()

    def test_duplicate_case_outputs_are_rejected(self):
        self.batch["batch"]["casecount"] = "2"
        self.batch["case2"] = dict(self.batch["case1"])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.read()


class NormalCaseTest(CaseFixture):
    def setUp(self):
        super().setUp()
        for template in self.templates.values():
            if "searchssdindex" in template:
                template["searchssdindex"]["internalresultnum"] = "100"
        specs = [("broad_tag", "control")]
        specs += [(scenario, "graph16384") for scenario in diagnosis.shared.SCENARIOS]
        order = [(1, scenario, setting) for scenario, setting in specs]
        order += [(2, scenario, setting) for scenario, setting in reversed(specs)]
        self.batch = dict(batch=dict(casecount=str(len(order)), warmuppolicy="once"))
        for number, (repeat, scenario, setting) in enumerate(order, 1):
            path = self.root / f"{scenario}_{setting}.ini"
            native = copy.deepcopy(self.templates[self.root / (scenario + ".ini")])
            if setting == "control":
                native["searchsweep"]["nprobe"] = "[100]"
            else:
                native["searchssdindex"].update(maxcheck="16384", internalresultnum="192")
                native["searchsweep"]["nprobe"] = "[192,384,768,1536]"
            self.templates[path] = native
            self.batch[f"case{number}"] = dict(
                config=str(path), outputdirectory=str(self.destination / f"native/SPTAG/r{repeat}_{setting}_{scenario}"))
        self.templates[self.batch_path] = self.batch

    def read(self):
        with mock.patch.object(measurement, "read_json", return_value=self.points), \
                mock.patch.object(measurement, "read_config", side_effect=lambda path: self.templates[Path(path)]):
            return measurement.read_cases(self.batch_path, self.destination, self.reference)

    def test_shared_grid_and_bracketing_controls(self):
        cases = self.read()
        self.assertEqual(8, len(cases))
        self.assertEqual("control", cases[0]["setting"])
        self.assertEqual("control", cases[-1]["setting"])
        self.assertEqual(26, sum(len(case["probes"]) for case in cases))

    def test_scenario_specific_grid_is_rejected(self):
        self.templates[self.root / "medium_tag_graph16384.ini"]["searchsweep"]["nprobe"] = "[192,384]"
        with self.assertRaisesRegex(ValueError, "identical across all three"):
            self.read()

    def test_unrelated_budget_change_is_rejected(self):
        self.templates[self.root / "medium_tag_graph16384.ini"]["searchssdindex"]["postingadditionalmaxcheck"] = "8192"
        with self.assertRaisesRegex(ValueError, "Only the diagnosed graph budget"):
            self.read()

    def test_reversed_repetition_is_required(self):
        self.batch["case2"], self.batch["case3"] = self.batch["case3"], self.batch["case2"]
        with self.assertRaisesRegex(ValueError, "reverse case order"):
            self.read()


class CombinedFrontierTest(unittest.TestCase):
    def setUp(self):
        self.original = []
        self.fresh = []
        for repeat in (1, 2):
            base = dict(engine="SPTAG", scenario="broad_tag", setting="base", search_value=100,
                        recall=.8, qps=100, repeat=repeat, underfilled_queries=0, max_check=2048,
                        posting_additional_max_check=2048, posting_navigation_width=8,
                        payload_hashes={"ids.i32": "ids", "dist.f32": "dist", "work.u64": "work"})
            self.original.append(base)
            self.fresh.append(dict(base, setting="control", qps=1000))
            self.fresh.append(dict(base, setting="graph16384", search_value=192, max_check=16384,
                                   recall=.95, qps=150))

    def test_controls_are_never_selected_into_the_frontier(self):
        points, rows, control = measurement.combine(self.original, self.fresh)
        self.assertEqual(4, len(points))
        self.assertEqual(2, len(rows))
        self.assertNotIn("control", {row["setting"] for row in rows})
        self.assertFalse(rows[0]["algorithm_frontier"])
        self.assertTrue(rows[1]["algorithm_frontier"])
        self.assertEqual(10, control["qps_ratio"])
        self.assertFalse(control["included_in_frontier"])

    def test_changed_control_results_are_rejected(self):
        self.fresh[0]["payload_hashes"] = {"ids.i32": "different"}
        with self.assertRaisesRegex(ValueError, "Control IDs"):
            measurement.combine(self.original, self.fresh)

    def test_rerunning_an_existing_setting_cannot_cherry_pick_timings(self):
        for point in self.fresh:
            if point["setting"] != "control":
                point.update(setting="base", search_value=100)
        with self.assertRaisesRegex(ValueError, "cherry-pick"):
            measurement.combine(self.original, self.fresh)


if __name__ == "__main__":
    unittest.main()
