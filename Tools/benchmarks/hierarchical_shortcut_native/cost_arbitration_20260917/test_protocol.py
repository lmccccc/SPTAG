import configparser
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from prepare import HERE, TOOL, MATCHED


class ProtocolTests(unittest.TestCase):
    def test_shared_ordinary_body(self):
        text=(HERE/"MatchedBench.cpp").read_text()
        body=text.split("const auto start = std::chrono::steady_clock::now();",1)[1].split(
            "const auto finish = std::chrono::steady_clock::now();",1)[0]
        expected=json.loads((MATCHED/"cores.json").read_text())["common_timed_body_sha256"]
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(),expected)
        self.assertNotIn("capture",body)

    def test_removed_degree_and_no_groundtruth_policy(self):
        for name in ("NativeSupplier.h","PostingSupplier.h","CostModel.h","NativeNeighborHooks.h"):
            source=(HERE/name).read_text().lower()
            for forbidden in ("groundtruth","recallhits","retainedratio","minbasedegree","nativedeficit"):
                self.assertNotIn(forbidden,source,name)
        self.assertIn("resultFilter,{}",(HERE/"NativeSupplier.h").read_text())

    def test_invalid_native_inputs_fail_before_load(self):
        for key,value in (("ShortcutRetainedRatio","0.5"),("ShortcutMinBaseDegree","16"),
                          ("ArbitrationDistanceCost","nan"),("ArbitrationMemberCost","-1"),
                          ("ArbitrationPriorWeight","0"),("ArbitrationPredicatePrior","2"),
                          ("EnableHybridDistance","true"),("EnableAdaptiveFilteredNprobe","true")):
            cfg=configparser.ConfigParser();cfg.optionxform=str
            cfg.read(HERE/"configs/broad_tag_auto.ini")
            cfg["SearchSSDIndex"][key]=value
            with tempfile.TemporaryDirectory(prefix="invalid-config-",dir=TOOL) as tmp:
                path=Path(tmp)/"invalid.ini"
                with path.open("w") as out: cfg.write(out,space_around_delimiters=False)
                result=subprocess.run([str(TOOL/"harness/cost-bench"),"--config",str(path)],
                                      capture_output=True,text=True,cwd=tmp)
                self.assertNotEqual(result.returncode,0,key)
                self.assertNotIn("MATCHED_LOAD",result.stdout)


if __name__=="__main__":
    unittest.main()
