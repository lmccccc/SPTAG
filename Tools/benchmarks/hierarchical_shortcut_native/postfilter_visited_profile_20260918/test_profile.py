import json
import math
from pathlib import Path
import unittest
from analyze import category,CATEGORIES
from profile import HERE,OUT,TOOL,FROZEN,sha

class AttributionTests(unittest.TestCase):
    def test_exact_core_and_debug(self):
        proof=json.loads((OUT/"linked_executable_equivalence.json").read_text())
        self.assertEqual(proof["instruction_byte_mismatches"],[])
        for path in (TOOL/"debug_objects").glob("*.cpp.json"):
            value=json.loads(path.read_text())
            self.assertEqual(value["instruction_byte_mismatches"],[])
            self.assertTrue(value["symbol_tables_equal"])

    def test_bounded_plan_and_gating(self):
        plan=json.loads((OUT/"plan.json").read_text())
        self.assertEqual(plan["processes"],8)
        self.assertEqual({p[1] for p in plan["points"]},{"graph","match"})
        runs=json.loads((OUT/"runs.json").read_text())
        self.assertEqual(len(runs),8)
        for r in runs:
            self.assertTrue(r["all_frozen_payloads_and_traces_exact"])
            self.assertEqual(r["flags"]["dropped"],"0")
            self.assertEqual(r["flags"]["wrong_thread_samples"],"0")
            self.assertEqual(r["native"]["postings_per_query"],24)
            self.assertEqual(r["native_mean"]["csr_members"],0)
            self.assertEqual(r["native_mean"]["signature_checks"],0)
            self.assertEqual(r["storage_max"]["allocations"],0)
            self.assertEqual(r["storage_max"]["conversions"],0)

    def test_exclusive_cpu_accounting(self):
        samples=json.loads((OUT/"symbolized_samples.json").read_text())
        self.assertEqual(len(samples),3528)
        for s in samples:
            self.assertIn(category(s),CATEGORIES)
            self.assertEqual(int(s["pc"],16),int(s["base"],16)+int(s["offset"],16))
        report=json.loads((OUT/"cpu_attribution.json").read_text())
        for run in report["per_run"].values():
            self.assertEqual(sum(run["counts"].values()),run["samples"])
            self.assertAlmostEqual(sum(run["cpu_ms_per_query"].values()),run["thread_cpu_ms_per_query"],12)
        categories=report["categories"]
        for mode in ("graph","match"):
            self.assertAlmostEqual(sum(c[mode+"_ms"] for c in categories),
                sum(r["thread_cpu_ms_per_query"] for k,r in report["per_run"].items()
                    if k.startswith("profile_"+mode))/2,12)
        self.assertEqual(next(c for c in categories if c["category"]=="first_match")["graph_samples"],0)

    def test_configs_byte_identical(self):
        for mode in ("graph","match"):
            name=f"broad_tag_visited_{mode}_ratio_0.01.ini"
            self.assertEqual(sha(OUT/"configs"/name),sha(FROZEN/"configs"/name))

if __name__=="__main__":unittest.main()
