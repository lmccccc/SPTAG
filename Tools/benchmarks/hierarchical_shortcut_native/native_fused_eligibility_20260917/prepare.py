"""Create a new frozen-parent clone; never write the accepted bound runtime."""
import configparser
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / "native_filter_cost_20260917"
DATA = HERE.parents[4] / "datasets/sift1m_zipf200_sparse193_numeric"
PARENT = DATA / "toolchains/h1_native_filter_cost_bound_20260917"
TOOL = DATA / "toolchains/h1_native_fused_eligibility_20260917"
FILES = {
    "BKTIndex.cpp": "AnnService/src/Core/BKT/BKTIndex.cpp",
    "SPANNIndex.cpp": "AnnService/src/Core/SPANN/SPANNIndex.cpp",
    "NativeNeighborHooks.h": "AnnService/NativeNeighborHooks.h",
    "NativeSupplier.h": "AnnService/NativeSupplier.h",
    "FilterCost.h": "AnnService/FilterCost.h",
    "SpannAclBench.cpp": "Tools/benchmarks/SpannAclBench.cpp",
}


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main():
    if TOOL.exists():
        raise RuntimeError("Preserve existing fused runtime")
    proof = json.loads((PARENT / "diagnostic_provenance.json").read_text())
    for name, digest in proof["after"].items():
        assert sha(PARENT / "source" / name) == digest, name
    TOOL.mkdir()
    shutil.copytree(PARENT / "source", TOOL / "source", symlinks=True,
                    ignore=shutil.ignore_patterns("Release"))
    for local, source in FILES.items():
        shutil.copy2(PARENT / "source" / source, HERE / local)
    for name in ("predicate_coverage.json", "native-coverage.log"):
        shutil.copy2(PARENT / name, TOOL / name)
    for name in ("CMakeLists.txt", "Coverage.cpp", "ClockAudit.cpp", "preregistration.json", "run.py"):
        shutil.copy2(OLD / name, HERE / name)
    shutil.copy2(OLD / "bound_fix/NativeTests.cpp", HERE / "NativeTests.cpp")
    shutil.copytree(OLD / "configs", HERE / "configs")
    cfg = configparser.ConfigParser()
    cfg.read(OLD / "bound_experiment.ini")
    cfg["Experiment"].update(Toolchain=str(TOOL), Binary=str(TOOL / "source/Release/spannaclbench"),
        OutputDirectory=str(DATA / "comparisons/h1_native_fused_eligibility_20260917"))
    with (HERE / "experiment.ini").open("x") as out:
        cfg.write(out, space_around_delimiters=False)
    with (TOOL / "parent_provenance.json").open("x") as out:
        json.dump({"parent": str(PARENT), "source": proof,
                   "binary_sha256": sha(PARENT / "source/Release/spannaclbench")}, out, indent=2)


if __name__ == "__main__":
    main()
