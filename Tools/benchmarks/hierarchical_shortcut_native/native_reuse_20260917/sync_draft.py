"""Synchronize a pre-measurement draft; refuse any runtime with query evidence."""
import json
import shutil
from integrate import HERE, DATA
from prepare import sha, write

target = DATA / "toolchains/h1_native_reuse_20260917"
if (DATA / "comparisons/h1_native_reuse_20260917").exists():
    raise RuntimeError("Cannot change a measured runtime")
manifest = target / "native_reuse_provenance.json"
old = json.loads(manifest.read_text())
if not (target / "native_reuse_provenance.initial.json").exists():
    shutil.copy2(manifest, target / "native_reuse_provenance.initial.json")
    shutil.copy2(target / "source/Release/spannaclbench", target / "spannaclbench.initial")
paths = {f"AnnService/{name}": name for name in
         ("NativeNeighborHooks.h", "PostingSupplier.h", "NativeSupplier.h", "FullHooks.h", "Signature.h")}
paths.update({
    "AnnService/inc/Core/Common/WorkSpace.h": "WorkSpace.h",
    "AnnService/inc/Core/Common/BKTree.h": "BKTree.h",
    "AnnService/inc/Core/BKT/Index.h": "BKTIndex.h",
    "AnnService/inc/Core/SPANN/Index.h": "SPANNIndex.h",
    "AnnService/src/Core/BKT/BKTIndex.cpp": "BKTIndex.cpp",
    "AnnService/src/Core/SPANN/SPANNIndex.cpp": "SPANNIndex.cpp",
    "Tools/benchmarks/SpannAclBench.cpp": "SpannAclBench.cpp",
})
for relative, name in paths.items():
    shutil.copy2(HERE / name, target / "source" / relative)
old["after"] = {name: sha(target / "source" / name) for name in old["after"]}
old["premeasurement_revision"] = "Owner inverse prepared during native load, never by a query/global rescue scan."
manifest.write_text(json.dumps(old, indent=2) + "\n")
