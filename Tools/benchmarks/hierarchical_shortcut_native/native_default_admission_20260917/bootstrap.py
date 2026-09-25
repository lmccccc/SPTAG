"""Copy the immutable native-reuse v2 integration into a new bounded experiment."""
from pathlib import Path
import shutil

here = Path(__file__).resolve().parent
parent = here.parent / "native_reuse_20260917_v2"
if (here / "NativeSupplier.h").exists():
    raise RuntimeError("Preserve materialized default-admission source")
for name in (
    "NativeNeighborHooks.h", "PostingSupplier.h", "FullHooks.h", "NativeSupplier.h",
    "BKTIndex.cpp", "BKTIndex.h", "BKTree.h", "SPANNIndex.cpp", "SPANNIndex.h",
    "WorkSpace.h", "Signature.h", "SpannAclBench.cpp", "Capture.inc", "NativeTests.cpp",
    "CMakeLists.txt", "run.py", "experiment.ini", "preregistration.json",
):
    shutil.copy2(parent / name, here / name)
shutil.copytree(parent / "configs", here / "configs")
