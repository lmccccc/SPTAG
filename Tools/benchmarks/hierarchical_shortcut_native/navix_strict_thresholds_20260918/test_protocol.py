import configparser
import hashlib
import os
import subprocess
import unittest
from prepare import HERE,TOOL,OUTPUT,write

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
            if not path.stem.endswith("postfilter_graph"):
                self.assertFalse(any(k.startswith(("arbitration","shortcut")) for k in cfg["SearchSSDIndex"]))
                self.assertEqual(cfg["SearchSSDIndex"]["NavixPostingThreshold"],"0.01")
                self.assertEqual(cfg["SearchSSDIndex"]["NavixCapture"],"false")
                self.assertIn(cfg["SearchSSDIndex"]["NavixTwoHopThreshold"],("0.1","0.05"))
                self.assertEqual(cfg["Benchmark"]["Case"],
                    cfg["SearchSSDIndex"]["NavixMode"]+"_twohop_"+
                    cfg["SearchSSDIndex"]["NavixTwoHopThreshold"]+"_posting_0.01")
            else:
                self.assertEqual(cfg["SearchSSDIndex"]["ArbitrationMode"],"graph")
                self.assertEqual(cfg["SearchSSDIndex"]["ArbitrationCapture"],"false")
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
        self.assertIn("Navix::RouteValidated",text)
        self.assertIn("if (!decision.d ||",text)
        self.assertIn("hooks.ValidatePolicy()",text)
    def test_native_ini_and_environment_rejection(self):
        binary=TOOL/"harness/navix-bench"
        template=(HERE/"configs/broad_tag_posting_twohop_0.1_posting_0.01.ini").read_text()
        directory=OUTPUT/"negative_configs";directory.mkdir(exist_ok=True)
        inputs={f"twohop_{i}":template.replace("NavixTwoHopThreshold=0.1",f"NavixTwoHopThreshold={value}")
            for i,value in enumerate(("nan","inf","-inf","0","-1","1.1","0.1junk","broken"))}
        inputs.update(
            missing=template.replace("NavixTwoHopThreshold=0.1",""),
            unknown=template.replace("NavixTwoHopThreshold=0.1","NavixTwoHopThreshold=0.1\nNavixUnknown=0"),
            unknown_native=template.replace("NavixTwoHopThreshold=0.1","NavixTwoHopThreshold=0.1\nUnknownNative=0"),
            malformed=template.replace("NavixTwoHopThreshold=0.1","# malformed native INI"),
            capture=template.replace("NavixCapture=false","NavixCapture=true"),
            sweep=template.replace("NProbe=[24]","NProbe=[24,32]"))
        records=[]
        for t in ("0.05","0.1"):
            for p in (t,"0.2"):
                inputs[f"strict_{t}_{p}"]=template.replace("NavixTwoHopThreshold=0.1",
                    f"NavixTwoHopThreshold={t}").replace("NavixPostingThreshold=0.01",f"NavixPostingThreshold={p}")
        for i,value in enumerate(("nan","inf","-inf","-0.1","0.6","0.01junk","broken")):
            inputs[f"posting_{i}"]=template.replace("NavixPostingThreshold=0.01",f"NavixPostingThreshold={value}")
        for name,text in inputs.items():
            cfg=directory/(name+".ini");cfg.write_text(text)
            result=subprocess.run([str(binary),"--config",str(cfg)],capture_output=True,text=True,timeout=10)
            self.assertNotEqual(result.returncode,0,name)
            self.assertNotIn("MATCHED_LOAD",result.stdout,name)
            if name.startswith("strict_"):
                self.assertIn("strictly less",result.stderr,name)
            records.append({"case":name,"returncode":result.returncode,"stdout":result.stdout,"stderr":result.stderr})
        valid=HERE/"configs/broad_tag_posting_twohop_0.1_posting_0.01.ini"
        for key in ("SPTAG_NAVIX_TWO_HOP_THRESHOLD","SPANN_NAVIX_TWO_HOP_THRESHOLD",
                    "SHORTCUT_TWO_HOP_THRESHOLD","NAVIX_TWO_HOP_THRESHOLD","OMP_NUM_THREADS",
                    "OMP_PROC_BIND","OMP_PLACES","LD_PRELOAD"):
            result=subprocess.run([str(binary),"--config",str(valid)],capture_output=True,text=True,
                timeout=10,env=dict(os.environ,**{key:""}))
            self.assertNotEqual(result.returncode,0,key)
            self.assertIn("Forbidden native environment override",result.stderr,key)
            self.assertNotIn("MATCHED_LOAD",result.stdout,key)
            records.append({"case":key,"returncode":result.returncode,"stdout":result.stdout,"stderr":result.stderr})
        write(OUTPUT/"native_rejection_tests.json",records)

if __name__=="__main__":unittest.main()
