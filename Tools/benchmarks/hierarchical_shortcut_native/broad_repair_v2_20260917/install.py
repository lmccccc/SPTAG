"""Install only into the fresh v2 clone, preserving v1 and all native inputs."""
import json
import shutil
from prepare import HERE, TOOL, OUTPUT, PARENT, PARENT_OUT, sha, write

FILES = {
    "BKTIndex.cpp": "AnnService/src/Core/BKT/BKTIndex.cpp",
    "SPANNIndex.cpp": "AnnService/src/Core/SPANN/SPANNIndex.cpp",
    "WorkSpace.h": "AnnService/inc/Core/Common/WorkSpace.h",
    "RelativeNeighborhoodGraph.h": "AnnService/inc/Core/Common/RelativeNeighborhoodGraph.h",
    **{name: "AnnService/" + name for name in
       ("NativeSupplier.h", "NativeNeighborHooks.h", "NativeDeficit.h", "NativeFunctionRef.h",
        "PostingSupplier.h", "FilterCost.h")}}


def main():
    evidence = json.loads((OUTPUT / "allocation_v1_broad/evidence.json").read_text())
    assert evidence["totals"]["ordinary"]["calls_per_query"] > 10000
    for target, digest in json.loads((PARENT_OUT / "repair_sources.json").read_text()).items():
        assert sha(PARENT / "source" / target) == digest
    shutil.copytree(PARENT / "source", TOOL / "source", symlinks=True,
                    ignore=shutil.ignore_patterns("Release", "__pycache__"))
    installed = {}
    for local, target in FILES.items():
        shutil.copyfile(HERE / local, TOOL / "source" / target)
        installed[target] = sha(TOOL / "source" / target)
    write(OUTPUT / "repair_sources.json", installed)
    write(OUTPUT / "iteration_plan.json", {
        "allocation_evidence_sha256": sha(OUTPUT / "allocation_v1_broad/evidence.json"),
        "native_body": "same native BKT queues, visited marking, distance function, own/result admission and budgets",
        "scratch_bound": "head plus ordinary degree plus at most deficit accepted identities; no child/distance cap",
        "purity": "STATIC H/O metadata, IsLimitedTagMutationReadOnly checked; query predicate immutable. "
                  "No stateful result/own dedup decisions are cached.",
        "qualification_storage": "one byte per existing native visited hash slot, reset each query and rehashed on growth; "
                                 "no H1-sized table, extra visited entries or distance cache",
        "ordinary_plan": [["bad", "v1", "repair", "original", "h3"],
                          ["h3", "original", "repair", "v1", "bad"]],
        "queries": 1000, "warmup": 1000, "broad_nprobe": 24, "original_nprobe": 80, "h3_nprobe": 32,
        "unfilter": ["bad", "v1", "repair"], "repetitions": 2, "no_curves": True})


if __name__ == "__main__":
    main()
