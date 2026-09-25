from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_full_phase_ab import controls, validate_phase_balance
from run_vanilla_spann_build import read_ini


class FullPhaseControls(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(__file__).resolve().parents[1] / "configs/sift1m_full_phase"
        for path in root.glob("*.ini"):
            shutil.copy2(path, self.directory.name)
        self.config = Path(self.directory.name) / "experiment.ini"
        self.plan = read_ini(self.config)["Experiment"]

    def change(self, case, modes, key, value):
        for mode in modes:
            path = self.config.parent / f"{case}_{mode}.ini"
            native = read_ini(path)
            native["SearchSSDIndex"][key] = value
            with path.open("w") as stream:
                native.write(stream)

    def test_fixed_comparison(self):
        cases = controls(self.config, self.plan)
        self.assertEqual(list(cases), ["flat24", "h3_24", "h3_29"])
        for name, mode, nprobe in (("flat24", "H1Only", 24),
                                   ("h3_24", "H2Only", 24),
                                   ("h3_29", "H2Only", 29)):
            native = cases[name][1]
            self.assertEqual(native["HeadNavigationMode"], mode)
            self.assertEqual(native.getint("InternalResultNum"), nprobe)

    def test_profile_work_change_rejected(self):
        self.change("flat24", ["profile"], "MaxCheck", "1024")
        with self.assertRaisesRegex(ValueError, "Only phase timing"):
            controls(self.config, self.plan)

    def test_cross_case_work_change_rejected(self):
        self.change("h3_29", ["plain", "profile"], "MaxCheck", "1024")
        with self.assertRaisesRegex(ValueError, "across full-query cases"):
            controls(self.config, self.plan)

    def test_head_dump_rejected(self):
        self.change("flat24", ["plain", "profile"], "DumpHeads", "1")
        with self.assertRaisesRegex(ValueError, "Path/head logging"):
            controls(self.config, self.plan)

    def test_path_logging_rejected(self):
        self.change("flat24", ["plain", "profile"], "LogPathStats", "true")
        with self.assertRaisesRegex(ValueError, "Path/head logging"):
            controls(self.config, self.plan)

    def test_wrong_profile_flag_rejected(self):
        self.change("flat24", ["profile"], "LogPhaseTime", "false")
        with self.assertRaisesRegex(ValueError, "Only phase timing"):
            controls(self.config, self.plan)

    def test_extra_section_rejected(self):
        path = self.config.parent / "flat24_profile.ini"
        with path.open("a") as stream:
            stream.write("\n[BuildHead]\nMaxCheck=1024\n")
        with self.assertRaisesRegex(ValueError, "one native search section"):
            controls(self.config, self.plan)

    def test_empty_and_duplicate_cases_rejected(self):
        for value in ("", "flat24,", "flat24,flat24"):
            with self.subTest(value=value):
                self.plan["Cases"] = value
                with self.assertRaisesRegex(ValueError, "Empty or duplicate"):
                    controls(self.config, self.plan)

    def test_phase_rounding_and_nonclosure(self):
        phase = dict(total=0.934, bkt=0.0, pq=0.0, graphOther=0.348,
                     post=0.586, io=0.507, scan=0.064, postOther=0.015)
        validate_phase_balance(phase)
        validate_phase_balance({**phase, "total": 0.935, "postOther": 0.016})
        for key in ("total", "postOther"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "does not close"):
                    validate_phase_balance({**phase, key: phase[key] + 0.01})


if __name__ == "__main__":
    unittest.main()
