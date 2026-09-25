import configparser
import hashlib
import json
import unittest
from prepare import HERE, MATCHED
from run import parity


class BoundedProtocolTests(unittest.TestCase):
    def test_shared_timed_body(self):
        source = (HERE / "MatchedBench.cpp").read_text()
        body = source.split("const auto start = std::chrono::steady_clock::now();", 1)[1].split(
            "const auto finish = std::chrono::steady_clock::now();", 1)[0]
        proof = json.loads((MATCHED / "cores.json").read_text())
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(), proof["common_timed_body_sha256"])
        self.assertNotIn("FilterCost", body)

    def test_frozen_ordinary_parameters(self):
        for name, probe in (("broad_bad24", 24), ("broad_original80", 80),
                            ("broad_h3_32", 32), ("unfilter24", 24)):
            cfg = configparser.ConfigParser()
            cfg.read(HERE / f"configs/{name}.ini")
            self.assertEqual(cfg["SearchSweep"]["NProbe"], f"[{probe}]")
            self.assertEqual(cfg["Benchmark"]["Warmup"], "1000")
            self.assertEqual(cfg["Benchmark"]["MaxQueries"], "1000")
            self.assertEqual(cfg["Benchmark"]["IO"], "buffered")
            for key, value in (("MaxCheck", "2048"), ("HierarchyMaxCheck", "512"),
                               ("SearchPostingPageLimit", "15"), ("DumpHeads", "0"),
                               ("LogPhaseTime", "false"), ("LogPathStats", "false")):
                self.assertEqual(cfg["SearchSSDIndex"][key], value)

    def test_parity_covers_all_payloads(self):
        base = {"payloads": {"ids": "a", "dist": "b", "native": "c", "own": "d", "heads": "e"}}
        parity(base, base)
        for key in base["payloads"]:
            with self.assertRaisesRegex(RuntimeError, "parity"):
                parity(base, {"payloads": {**base["payloads"], key: "different"}})

    def test_handler_is_bounded_pc_only(self):
        source = (HERE / "ClockAudit.cpp").read_text()
        handler = source.split("void sample(", 1)[1].split("\nvoid count(", 1)[0]
        self.assertIn("position < 100000", handler)
        self.assertIn("REG_RIP", handler)
        for forbidden in ("new ", "malloc", "unwind", "mutex", "dladdr", "fprintf"):
            self.assertNotIn(forbidden, handler)
        self.assertIn("CLOCK_THREAD_CPUTIME_ID", source)
        self.assertIn("SIGEV_THREAD_ID", source)


if __name__ == "__main__":
    unittest.main()
