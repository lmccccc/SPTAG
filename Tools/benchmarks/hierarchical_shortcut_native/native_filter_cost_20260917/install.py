"""Install only the bounded diagnostic changes into its new source snapshot."""
from pathlib import Path
import hashlib
import json
import shutil

HERE = Path(__file__).resolve().parent
TOOL = HERE.parents[4] / "datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_native_filter_cost_20260917"


def main():
    parent = json.loads((TOOL / "parent_provenance.json").read_text())
    paths = {"AnnService/FilterCost.h": "FilterCost.h", "AnnService/NativeSupplier.h": "NativeSupplier.h",
        "AnnService/src/Core/SPANN/SPANNIndex.cpp": "SPANNIndex.cpp",
        "Tools/benchmarks/SpannAclBench.cpp": "SpannAclBench.cpp"}
    for dest, source in paths.items():
        shutil.copy2(HERE / source, TOOL / "source" / dest)
    after = {name: hashlib.sha256((TOOL / "source" / name).read_bytes()).hexdigest()
             for name in set(parent["parent_source_hashes"]) | set(paths)}
    with (TOOL / "diagnostic_provenance.json").open("x") as output:
        json.dump({**parent, "after": after, "changed_files": paths,
                   "control": "mode control installs same admission callbacks but no degree/injection observer",
                   "counters": "native capture only; no per-candidate clocks"}, output, indent=2)


if __name__ == "__main__":
    main()
