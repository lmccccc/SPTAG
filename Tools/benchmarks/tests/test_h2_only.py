import configparser
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_h2_only import validate
from run_vanilla_spann_build import build_command


class H2OnlyControls(unittest.TestCase):
    def plans(self):
        root = Path(__file__).resolve().parents[1] / "configs/sift1m_h2_sweep"
        for name in ("r08", "r16", "r32"):
            path = root / f"build_{name}.ini"
            plan = configparser.ConfigParser(interpolation=None)
            plan.read(path)
            yield path, plan

    def test_native_graph_only_controls(self):
        for path, plan in self.plans():
            with self.subTest(path=path.name):
                validate(plan)
                command = build_command(plan, path)
                self.assertNotIn("-i", command)
                self.assertEqual(plan["Base"].getint("VectorSize"), 160091)
                self.assertNotEqual(plan["Execution"]["ExportDirectory"], plan["Base"]["IndexDirectory"])
                self.assertEqual(Path(plan["Execution"]["ExportDirectory"]).parent,
                                 Path(plan["Base"]["IndexDirectory"]).parent)
                self.assertEqual(plan["BuildHead"].getint("NumberOfThreads"), 24)

    def test_ssd_enabling_rejected(self):
        for key in ("isExecute", "BuildSsdIndex"):
            _, plan = next(self.plans())
            plan["BuildSSDIndex"][key] = "true"
            with self.assertRaises(ValueError):
                validate(plan)

    def test_source_side_effects_rejected(self):
        for section, key in (("Base", "DeleteHeadVectors"), ("SelectHead", "SaveBKT")):
            _, plan = next(self.plans())
            plan[section][key] = "true"
            with self.assertRaises(ValueError):
                validate(plan)

    def test_existing_selection_requires_map(self):
        _, plan = list(self.plans())[1]
        del plan["Execution"]["SeedHeadIDs"]
        with self.assertRaises(ValueError):
            validate(plan)

    def test_sweep_controls_and_inputs_fixed(self):
        root = Path(__file__).resolve().parents[1] / "configs/sift1m_h2_sweep"
        plans = []
        for name in ("r08", "r16", "r32"):
            plan = configparser.ConfigParser(interpolation=None)
            plan.read(root / f"sweep_{name}.ini")
            self.assertEqual(plan["Sweep"].getint("ResultNum"), 24)
            self.assertEqual(plan["Sweep"].getint("Threads"), 1)
            self.assertEqual(plan["Input"].getint("QueryCount"), 1000)
            self.assertIn(f"/{name}/index/", plan["Input"]["HeadIDs"])
            plans.append(plan)
        for plan in plans[1:]:
            self.assertEqual(dict(plan["Sweep"]), dict(plans[0]["Sweep"]))
            for key in ("HeadVectors", "Queries", "FlatIndexDirectory"):
                self.assertEqual(plan["Input"][key], plans[0]["Input"][key])

    def test_replica32_reuses_graphs_and_search_controls(self):
        root = Path(__file__).resolve().parents[1] / "configs/sift1m_h2_sweep"
        for name in ("r08", "r16", "r32"):
            old = configparser.ConfigParser(interpolation=None)
            new = configparser.ConfigParser(interpolation=None)
            old.read(root / f"sweep_{name}.ini")
            new.read(root / f"r32_{name}.ini")
            self.assertEqual(dict(old["Input"]), dict(new["Input"]))
            expected = dict(old["Sweep"])
            expected["replicas"] = "8,16,32"
            self.assertEqual(expected, dict(new["Sweep"]))
            self.assertNotEqual(old["Output"]["Directory"], new["Output"]["Directory"])

    def test_half_ratio_uses_native_density_adjustment(self):
        root = Path(__file__).resolve().parents[1] / "configs/sift1m_h2_sweep"
        build = configparser.ConfigParser(interpolation=None)
        build.read(root / "build_r50.ini")
        validate(build)
        self.assertEqual(build["SelectHead"].getfloat("Ratio"), 0.5)
        self.assertEqual(build["SelectHead"].getint("SplitFactor"), 0)
        sweep = configparser.ConfigParser(interpolation=None)
        sweep.read(root / "r32_r50.ini")
        self.assertEqual(sweep["Sweep"]["Replicas"], "32")
        self.assertEqual(sweep["Input"]["HeadVectors"], build["Base"]["VectorPath"])
        self.assertEqual(sweep["Input"]["HeadIndexDirectory"], build["Base"]["IndexDirectory"] + "/HeadIndex")
        self.assertEqual(sweep["Sweep"].getint("ResultNum"), 24)


if __name__ == "__main__":
    unittest.main()
