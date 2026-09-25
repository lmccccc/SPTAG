import collections
import configparser
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

HERE = Path(__file__).resolve().parent


class ProtocolTests(unittest.TestCase):
    def test_complete_preregistration(self):
        p = json.loads((HERE / "preregistration.json").read_text())
        jobs = json.loads((HERE / "schedule.json").read_text())
        self.assertEqual(len(jobs), 36)
        self.assertEqual(set(collections.Counter(j["stage"] for j in jobs).values()), {12})
        self.assertEqual(len({(j["scenario"], j["case"], n) for j in jobs for n in j["probes"]}), 198)
        self.assertEqual(sum(len(j["probes"]) for j in jobs), 396)
        for j in jobs:
            self.assertEqual(j["probes"], p["grid"] if j["repeat"] == 1 else p["grid"][::-1])

    def test_native_ini_parameters(self):
        for path in (HERE / "configs").glob("*.ini"):
            self.assertNotIn("= ", path.read_text())
            c = configparser.ConfigParser()
            c.read(path)
            s = c["SearchSSDIndex"]
            self.assertEqual((s["MaxCheck"], s["HierarchyMaxCheck"], s["SearchPostingPageLimit"]),
                             ("2048", "512", "15"))
            self.assertEqual((s["ShortcutRetainedRatio"], s["ShortcutMinBaseDegree"]), ("0.5", "16"))
            self.assertEqual(s["LogPhaseTime"], "false")
            self.assertEqual(s["ShortcutProfile"], "false")
            self.assertEqual(list(c["SearchSweep"]), ["nprobe"])
            self.assertEqual(s["HeadNavigationMode"], "H2Only" if path.name.startswith("h3_") else "H1Only")

    def test_stage_required(self):
        r = subprocess.run([sys.executable, str(HERE / "run.py"), "stage"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--stage is required", r.stderr)

    def test_partial_cannot_finalize(self):
        spec = importlib.util.spec_from_file_location("curve_test", HERE / "run.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        e = module.Experiment.__new__(module.Experiment)
        e.records = lambda: [{}] * 12
        with self.assertRaises(ValueError):
            e.finalize()


if __name__ == "__main__":
    unittest.main()
