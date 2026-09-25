"""Read-only independent checks on completed diagnostic evidence."""
import json
import unittest
from prepare import HERE,TOOL,OUTPUT,sha
from experiment import NAVIGATION

class CostEvidence(unittest.TestCase):
    def test_same_core_same_trajectory(self):
        runs=json.loads((OUTPUT/"cost_runs.json").read_text())
        self.assertEqual(len(runs),10)
        self.assertEqual(sum(r["kind"]=="profile" for r in runs),2)
        expected=json.loads((OUTPUT/"cross_control_trajectory.json").read_text())["hashes"]
        for r in runs:
            for suffix in NAVIGATION:
                self.assertEqual(sha(OUTPUT/r["label"]/("nprobe_24"+suffix)),expected[suffix])
            self.assertEqual(r["native"]["recall"],.9153)
            for key in ("csr_members","signature_checks","representative_distances"):
                self.assertEqual(r["native_mean"][key],0)
            self.assertEqual(r["storage_max"]["slot_bytes"],4)
            self.assertEqual(r["storage_max"]["allocations"],0)
            self.assertEqual(r["storage_max"]["conversions"],0)
    def test_real_predicate_ablation(self):
        body=(HERE/"BKTIndex.cpp").read_text().split("const auto admitFilteredResult =",1)[1].split(
            "const auto admitCollapsedResults =",1)[0]
        self.assertLess(body.index("const bool valid="),body.index("if (shortcut) return false;"))
        self.assertLess(body.index("if (shortcut) return false;"),body.index("return valid &&"))
        runs=json.loads((OUTPUT/"cost_runs.json").read_text())
        for r in runs:
            expected={"A":221.616,"Aprime":1892.887,"B":1892.887,"bit":1967.457}[r["control"]]
            self.assertAlmostEqual(r["actual_predicate_callbacks_per_query"],expected,places=9)
        self.assertIn("PASS diagnostic A-prime executes actual predicate",
                      (OUTPUT/"native-fixtures-detail.log").read_text())
    def test_exact_pc_mapping(self):
        proof=json.loads((OUTPUT/"linked_executable_equivalence.json").read_text())
        self.assertEqual(proof["instruction_byte_mismatches"],[])
        self.assertEqual(proof["frozen_sha256"],sha(TOOL/"harness/postfilter-bench"))
        s=json.loads((OUTPUT/"symbolization.json").read_text())
        runs=json.loads((OUTPUT/"cost_runs.json").read_text())
        self.assertEqual(s["samples"],sum(int(r["flags"]["samples"]) for r in runs))
        self.assertEqual(s["samples"],s["executable_map_matches"])
    def test_cpu_conservation_and_window(self):
        r=json.loads((OUTPUT/"cost_attribution.json").read_text())
        for mode in ("Aprime","bit"):
            p=r["per_sampled_window"][mode]
            self.assertAlmostEqual(sum(p["cpu_ms_by_category"].values()),p["cpu_ms"],places=12)
        for t in r["timings"]:
            self.assertEqual(t["buffer_drops"],0)
            self.assertEqual(t["wrong_thread_samples"],0)
            self.assertEqual(t["queries"],1000)
    def test_no_search_body_change(self):
        old=(HERE.with_name("h1_postfilter_predicate_order_20260918")/"MatchedBench.cpp").read_text()
        new=(HERE/"MatchedBench.cpp").read_text()
        def body(text):
            return text.split("const auto start = std::chrono::steady_clock::now();",1)[1].split(
                "const auto finish = std::chrono::steady_clock::now();",1)[0]
        self.assertEqual(body(old),body(new))

if __name__=="__main__": unittest.main()
