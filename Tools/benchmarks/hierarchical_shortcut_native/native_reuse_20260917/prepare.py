"""Freeze a new runtime, retaining native array plumbing but no old search adapter."""
import configparser
import hashlib
import json
import shutil
from pathlib import Path
from integrate import HERE, DATA, ORIGINAL, PARENT


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def main():
    target = DATA / "toolchains/h1_native_reuse_20260917"
    output = DATA / "comparisons/h1_native_reuse_20260917"
    if target.exists() or output.exists():
        raise RuntimeError("Preserve native-reuse artifacts")
    proof = json.loads((PARENT.parent / "ratio_provenance.json").read_text())
    for name, digest in proof["after"].items():
        assert sha(PARENT / name) == digest, name
    authenticated = json.loads((ORIGINAL / "authentication.json").read_text())["verified"]
    for name in ("AnnService/src/Core/SPANN/SPANNIndex.cpp", "AnnService/src/Core/BKT/BKTIndex.cpp",
                 "AnnService/inc/Core/BKT/Index.h"):
        assert sha(ORIGINAL / "frozen" / name) == authenticated[name], name
    target.mkdir()
    shutil.copytree(PARENT, target / "source", symlinks=True, ignore=shutil.ignore_patterns("Release"))
    replacements = {f"AnnService/{name}": HERE / name for name in (
        "NativeNeighborHooks.h", "PostingSupplier.h", "NativeSupplier.h", "FullHooks.h", "Signature.h")}
    replacements.update({
        "AnnService/inc/Core/Common/WorkSpace.h": HERE / "WorkSpace.h",
        "AnnService/inc/Core/Common/BKTree.h": HERE / "BKTree.h",
        "AnnService/inc/Core/BKT/Index.h": HERE / "BKTIndex.h",
        "AnnService/src/Core/BKT/BKTIndex.cpp": HERE / "BKTIndex.cpp",
        "AnnService/src/Core/SPANN/SPANNIndex.cpp": HERE / "SPANNIndex.cpp",
        "AnnService/inc/Core/SPANN/Index.h": HERE / "SPANNIndex.h",
        "Tools/benchmarks/SpannAclBench.cpp": HERE / "SpannAclBench.cpp",
    })
    for name, path in replacements.items():
        shutil.copy2(path, target / "source" / name)
    for name in ("Supplier.h", "NativeAdapter.h", "ShortcutHooks.h"):
        # These are only copies in this newly-created runtime, not archived source.
        (target / "source/AnnService" / name).unlink()
    after = {name: sha(target / "source" / name) for name in set(proof["after"]) | set(replacements)
             if (target / "source" / name).is_file()}
    write(target / "native_reuse_provenance.json", {
        "parent": str(PARENT.parent), "parent_source_hashes": proof["after"],
        "authenticated_native_revision": "3552194536cb01dd70099e955a29e235a0cf4d2e",
        "authenticated_native_files": {n: authenticated[n] for n in (
            "AnnService/src/Core/SPANN/SPANNIndex.cpp", "AnnService/src/Core/BKT/BKTIndex.cpp",
            "AnnService/inc/Core/BKT/Index.h")},
        "after": after, "replacements": sorted(replacements),
        "removed_runtime_files": ["Supplier.h", "NativeAdapter.h", "ShortcutHooks.h"],
        "primitive": "Unmodified native m_fComputeDistance; observed function-pointer target, never intercepted.",
        "heaps": "One native H1 QueryResultSet; native frontier/workspace; existing SPANN own-point heap.",
    })
    configs = HERE / "configs"
    configs.mkdir()
    for case in ("h1", "supplier"):
        for suffix in ("plain", "profile", "asc", "desc"):
            text = (HERE.parent / f"ratio_degree_phase1/configs/{case}_{suffix}.ini").read_text()
            text = "\n".join(s for s in text.splitlines() if not s.startswith(
                ("ShortcutCap=", "ShortcutEdges=", "ShortcutHotPath="))) + "\n"
            if case == "h1":
                text = text.replace("ShortcutMode=ordinary\n",
                    "ShortcutMode=ordinary\nShortcutRetainedRatio=0.5\nShortcutMinBaseDegree=16\n")
            (configs / f"{case}_{suffix}.ini").write_text(text)
    c = configparser.ConfigParser()
    c.read(HERE.parent / "ratio_degree_phase1/experiment.ini")
    c["Experiment"].update(binary=str(target / "source/Release/spannaclbench"), toolchain=str(target),
        outputdirectory=str(output), cases="h1,supplier")
    with (HERE / "experiment.ini").open("x") as stream:
        c.write(stream)
    write(HERE / "preregistration.json", {
        "phase": "bounded_native_reuse", "ordinary_order": [["h1", "supplier"], ["supplier", "h1"]],
        "ordinary_processes": 4, "ordinary_queries": 1000, "warmup": 1000,
        "profile_processes": 2, "fixtures": "h1/supplier forward/reverse16,24,384; four filtered8-query fixtures",
        "nprobe": [24], "threads": 1, "numa": 2, "O_DIRECT": True, "page_limit": 15,
        "configs": {p.name: sha(p) for p in configs.iterdir()},
        "no_full_sweep": True,
    })


if __name__ == "__main__":
    main()
