"""Clone the frozen bad core and port the authenticated, tested fusion pieces."""
import json
import shutil
from prepare import HERE, TOOL, OUTPUT, BASE, MATCHED, sha, write

FILES = {
    "BKTIndex.cpp": "AnnService/src/Core/BKT/BKTIndex.cpp",
    "SPANNIndex.cpp": "AnnService/src/Core/SPANN/SPANNIndex.cpp",
    "NativeSupplier.h": "AnnService/NativeSupplier.h",
    "NativeNeighborHooks.h": "AnnService/NativeNeighborHooks.h",
    "FilterCost.h": "AnnService/FilterCost.h",
    "PostingSupplier.h": "AnnService/PostingSupplier.h",
    "RelativeNeighborhoodGraph.h": "AnnService/inc/Core/Common/RelativeNeighborhoodGraph.h",
}


def main():
    evidence = json.loads((OUTPUT / "diagnostic_sample/evidence.json").read_text())
    assert evidence["samples"] > 2000 and evidence["fine_clocks"] == 0
    auth = json.loads((BASE / "native_reuse_provenance.json").read_text())
    for name, digest in auth["after"].items():
        assert sha(BASE / "source" / name) == digest, name
    if not (TOOL / "source").exists():
        shutil.copytree(BASE / "source", TOOL / "source", symlinks=True,
                        ignore=shutil.ignore_patterns("Release", "__pycache__"))
    fused = HERE.parent / "native_fused_eligibility_20260917"
    proven = json.loads((BASE.parent / "h1_native_fused_eligibility_20260917/diagnostic_provenance.json").read_text())
    parents = {}
    for local, dest in FILES.items():
        source = fused / local if (fused / local).exists() else BASE / "source" / dest
        if source.parent == fused:
            assert sha(source) == proven["after"][dest], local
        if (HERE / local).exists():
            assert sha(HERE / local) == sha(source), local
        else:
            shutil.copyfile(source, HERE / local)
        parents[local] = {"source": str(source), "sha256": sha(source)}
    for name in ("NativeTests.cpp",):
        shutil.copyfile(fused / name, HERE / name)
    for name in ("MatchedBench.cpp", "NativeNProbeSweep.h"):
        shutil.copyfile(MATCHED / "snapshot/experiment" / name, HERE / name)
    write(OUTPUT / "repair_plan.json", {
        "baseline_cpu_evidence_sha256": sha(OUTPUT / "diagnostic_sample/evidence.json"),
        "source_parents": parents, "baseline_source": auth,
        "one_iteration": "Fuse ordinary degree/qualification, carry candidate-local qualification "
                         "through batched native CSR consumption into existing result admission; "
                         "reuse proven strict noncollapsed posting bound after own notification.",
        "unchanged": ["ratio .5", "floor16", "budgets", "signature ordering", "selected full rows",
                      "visited marking", "own notifications", "collapsed stop", "native heaps"],
        "not_in_scope": ["new cache", "hash-container redesign", "own metadata bound",
                         "policy tuning", "curve resumption"]})


if __name__ == "__main__":
    main()
