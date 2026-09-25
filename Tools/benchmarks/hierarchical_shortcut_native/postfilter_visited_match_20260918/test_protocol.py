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
    def test_removed_navigation(self):
        text=(HERE/"BKTIndex.cpp").read_text()
        for forbidden in ("expandNavix","finishInjectedRow","GraphRoute","valid.reserve","secondRows","Navix::"):
            self.assertNotIn(forbidden,text)
        for forbidden in ("qualifies", "gather", "QualificationLease", "hooks.eligible"):
            self.assertNotIn(forbidden,text)
        spann=(HERE/"SPANNIndex.cpp").read_text()
        self.assertNotIn("qualification",spann)
        self.assertIn("ordinaryObservation->passes+=valid",text)
        self.assertIn("decision.eligible<hook.activationRatio*decision.degree",text)
        self.assertNotIn("decision.passes<hook.activationRatio*decision.checks",text)
        self.assertLess(text.index("ordinaryObservation=nullptr;",text.index("const auto expandPostfilter")),
                        text.index("hook.posting(head,native)"))
    def test_private_headers(self):
        self.assertIn("#include <NativeSupplier.h>",(HERE/"NativeTests.cpp").read_text())
        self.assertIn("#include <FullHooks.h>",(HERE/"MatchedBench.cpp").read_text())
    def test_distinct_row_budget_boundaries(self):
        text=(HERE/"BKTIndex.cpp").read_text()
        self.assertIn("bool completeAuxiliaryRow=false",text)
        self.assertIn("if (!completeAuxiliaryRow && p_space.m_iNumberOfCheckedLeaves >=",text)
        self.assertIn("expandEdges(head,0,this,ids,0,count,false,&work,nullptr,true)",text)
    def test_visited_not_side_cache(self):
        text=(HERE/"WorkSpace.h").read_text()
        self.assertIn("std::unique_ptr<std::uint64_t[]> m_matchTable",text)
        self.assertIn("m_hashTable.reset();",text)
        self.assertIn("Uninitialized visited match insertion",text)
        self.assertNotIn("vector<bool>",text)
        self.assertNotIn("Qualification",text)

if __name__=="__main__": unittest.main()
