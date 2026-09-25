import configparser
import hashlib
import unittest
from prepare import HERE

class Protocol(unittest.TestCase):
    def test_timed_body(self):
        text=(HERE/"MatchedBench.cpp").read_text()
        body=text.split("const auto start = std::chrono::steady_clock::now();",1)[1].split(
            "const auto finish = std::chrono::steady_clock::now();",1)[0]
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(),
                         "9f412c63b71f7310e09180dff0858ccef3957bd20d7a96c34fdb440e3d559d53")
    def test_fixed_configs(self):
        for path in (HERE/"configs").glob("*.ini"):
            cfg=configparser.ConfigParser();cfg.read(path)
            self.assertEqual(cfg["SearchSweep"]["NProbe"],"[24]")
            self.assertEqual(cfg["SearchSSDIndex"]["MaxCheck"],"2048")
            self.assertEqual(cfg["Benchmark"]["Warmup"],"1000")
            self.assertEqual(cfg["Benchmark"]["MaxQueries"],"1000")
            self.assertFalse(any(k.startswith(("arbitration","shortcut")) for k in cfg["SearchSSDIndex"]))
            if not path.stem.endswith("original"):
                self.assertEqual(cfg["SearchSSDIndex"]["NavixPostingThreshold"],"0.05")
                self.assertEqual(cfg["SearchSSDIndex"]["NavixCapture"],"false")
    def test_no_active_cost_model(self):
        for name in ("PostingSupplier.h","NativeSupplier.h","BKTIndex.cpp","FullHooks.h"):
            text=(HERE/name).read_text()
            self.assertNotIn("CostArbitration",text)
            self.assertNotIn("afterGraph",text)
    def test_capture_after_timing(self):
        text=(HERE/"MatchedBench.cpp").read_text()
        self.assertLess(text.index("const auto finish ="),text.index("ShortcutFull::capture = true"))
        self.assertIn("Untimed capture changed final result",text)
    def test_native_threshold_strictness(self):
        text=(HERE/"BKTIndex.cpp").read_text()
        self.assertIn("double(decision.e)/decision.d<hook.postingThreshold",text)
        self.assertIn("if (!decision.d ||",text)

if __name__=="__main__":unittest.main()
