"""Install only into the pre-created isolated hotpath source clone."""
import hashlib
import json
from pathlib import Path
import shutil

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[3]
DATA=REPO.parent/"datasets/sift1m_zipf200_sparse193_numeric"
BASE=DATA/"toolchains/navix_posting_20260917"
PARENT=DATA/"comparisons/navix_posting_20260917"
MATCHED=DATA/"comparisons/matched_baseline_20260917"
TOOL=DATA/"toolchains/navix_hotpath_20260918"
OUTPUT=DATA/"comparisons/navix_hotpath_20260918"

def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(8*1024*1024),b""): h.update(block)
    return h.hexdigest()

def write(path,value):
    with Path(path).open("x") as out:
        json.dump(value,out,indent=2);out.write("\n")

FILES={
    "BKTIndex.cpp":"AnnService/src/Core/BKT/BKTIndex.cpp",
    "SPANNIndex.cpp":"AnnService/src/Core/SPANN/SPANNIndex.cpp",
    "BKTree.h":"AnnService/inc/Core/Common/BKTree.h",
    "WorkSpace.h":"AnnService/inc/Core/Common/WorkSpace.h",
    "RelativeNeighborhoodGraph.h":"AnnService/inc/Core/Common/RelativeNeighborhoodGraph.h",
    **{name:"AnnService/"+name for name in (
        "NativeSupplier.h","PostingSupplier.h","FullHooks.h","NativeNeighborHooks.h",
        "NativeFunctionRef.h","NavixPolicy.h","PredicateCache.h")}}

def protect():
    mismatches=[path for path,digest in json.loads((OUTPUT/"protection.json").read_text()).items()
                if sha(path)!=digest]
    assert not mismatches,mismatches
    assert not json.loads((MATCHED/"OPERATOR_STOP.json").read_text())["resume_allowed"]
    return True

def install():
    protect()
    for name,path in FILES.items():
        target=TOOL/"source"/path
        assert not target.is_symlink(),target
        if not target.exists() or sha(HERE/name)!=sha(target):
            shutil.copyfile(HERE/name,target)

if __name__=="__main__": install()
