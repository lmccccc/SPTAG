"""Clone authenticated frozen v1 safely; install v2 only in the new private source."""
import json
import shutil
from prepare import HERE, TOOL, OUTPUT, BASE, sha, write

FILES = {
    "BKTIndex.cpp": "AnnService/src/Core/BKT/BKTIndex.cpp",
    "SPANNIndex.cpp": "AnnService/src/Core/SPANN/SPANNIndex.cpp",
    "BKTree.h": "AnnService/inc/Core/Common/BKTree.h",
    "RelativeNeighborhoodGraph.h": "AnnService/inc/Core/Common/RelativeNeighborhoodGraph.h",
    **{n: "AnnService/" + n for n in ("NativeSupplier.h", "PostingSupplier.h", "FullHooks.h",
        "NativeNeighborHooks.h", "NativeFunctionRef.h", "CostModel.h")}}


def main():
    shutil.copytree(BASE / "source", TOOL / "source", symlinks=True,
                    ignore=shutil.ignore_patterns("Release", "__pycache__"))
    for local, path in FILES.items():
        shutil.copyfile(HERE / local, TOOL / "source" / path)
    write(OUTPUT / "sources.json", {path: sha(TOOL / "source" / path) for path in FILES.values()})
    write(OUTPUT / "removed_mechanisms.json", {
        "ordinary_traversal_filter": "empty; result-only native API",
        "degree_qualification_scan": "removed", "deficit_ratio_trigger": "removed",
        "qualification_cache": "not installed; original native-default WorkSpace retained",
        "two_hops": "ordinary lazy best-first graph steps, no separate eager two-hop operator",
        "auxiliary_semantics": "all H1 CSR members use identical native graph distance/queue/admission; "
            "a result-rejected member remains navigable, not an eligibility-poisoned visited entry"})


if __name__ == "__main__":
    main()
