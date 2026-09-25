"""Generate an ABI/layout-preserving, isolated callback access shim; never edit upstream."""
import hashlib
import json
from pathlib import Path
import sys

source, output = map(Path, sys.argv[1:])
header = source / "AnnService/inc/Core/BKT/Index.h"
cpp = source / "AnnService/src/Core/BKT/BKTIndex.cpp"
text = header.read_text()
anchor = "        public:\n            Index()\n"
assert text.count(anchor) == 1
shim = """        public:
            // Benchmark-only methods: no fields, virtuals, or algorithm changes.
            auto BenchmarkWrapDistance(std::uint64_t* counter)
            {
                auto original = m_fComputeDistance;
                m_fComputeDistance = [original, counter](const T* a, const T* b, DimensionType d) {
                    ++*counter;
                    return original(a, b, d);
                };
                return original;
            }
            void BenchmarkRestoreDistance(std::function<float(const T*, const T*, DimensionType)> original)
            {
                m_fComputeDistance = std::move(original);
            }
            Index()
"""
patched = text.replace(anchor, shim)
dest = output / "inc/Core/BKT/Index.h"
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(patched)
(output / "BKTIndex.cpp").write_bytes(cpp.read_bytes())
records = {}
for name, path in [("upstream_header", header), ("upstream_cpp", cpp),
                   ("isolated_header", dest), ("isolated_cpp", output / "BKTIndex.cpp")]:
    records[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
(output / "instrumentation.json").write_text(json.dumps(records, indent=2) + "\n")
