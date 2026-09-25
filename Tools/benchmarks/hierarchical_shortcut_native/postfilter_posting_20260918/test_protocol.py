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
        self.assertLess(text.index("observation->e+=qualifies"),text.index("const bool visited=p_space.CheckAndSet"))
    def test_private_headers(self):
        self.assertIn("#include <NativeSupplier.h>",(HERE/"NativeTests.cpp").read_text())
        self.assertIn("#include <FullHooks.h>",(HERE/"MatchedBench.cpp").read_text())

if __name__=="__main__": unittest.main()
