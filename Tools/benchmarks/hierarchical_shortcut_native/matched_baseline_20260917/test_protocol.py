import configparser
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent


class ProtocolTests(unittest.TestCase):
    def test_finalization_rejects_partial_smoke(self):
        import run
        with self.assertRaisesRegex(RuntimeError, "All three"):
            run.finalize()
        self.assertFalse((run.OUTPUT / "summary.json").exists())

    def test_all_native_configs_share_protocol(self):
        protocol = json.loads((HERE / "protocol.json").read_text())
        configs = list((HERE / "configs").glob("*.ini"))
        self.assertEqual(len(configs), 56)
        for path in configs:
            ini = configparser.ConfigParser()
            ini.read(path)
            self.assertEqual(ini["Benchmark"]["IO"], "buffered")
            self.assertEqual(ini["Benchmark"]["Index"], protocol["index"])
            for key, expected in {"MaxCheck": "2048", "HierarchyMaxCheck": "512",
                                  "SearchPostingPageLimit": "15", "NumberOfThreads": "1",
                                  "ResultNum": "10", "LogPhaseTime": "false", "DumpHeads": "0"}.items():
                self.assertEqual(ini["SearchSSDIndex"][key], expected)
            if ini["Benchmark"]["Case"] == "h1_original":
                self.assertFalse(any(k.startswith("shortcut") for k in ini["SearchSSDIndex"]))
            else:
                self.assertEqual(ini["SearchSSDIndex"]["ShortcutCapture"], "false")
            probes = json.loads(ini["SearchSweep"]["NProbe"])
            expected = [24] if path.stem.endswith("_smoke") else (
                protocol["grid"][::-1] if path.stem.endswith("_r2") else protocol["grid"])
            self.assertEqual(probes, expected)

    def test_timing_body_has_no_adapter_or_capture(self):
        source = (HERE / "MatchedBench.cpp").read_text()
        body = source.split("const auto start = std::chrono::steady_clock::now();", 1)[1].split(
            "const auto finish = std::chrono::steady_clock::now();", 1)[0]
        for forbidden in ("Shortcut", "Observation", "capture", "#if"):
            self.assertNotIn(forbidden, body)
        self.assertIn("const auto result = search(", body)


if __name__ == "__main__":
    unittest.main()
