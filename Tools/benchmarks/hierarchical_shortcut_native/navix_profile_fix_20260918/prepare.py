"""Isolated OLD-based successor; frozen artifacts and production are read-only."""
import json
from pathlib import Path
import shutil
from profile import HERE,DATA,TOOL,OUT as OUTPUT,sha,write

BASE=DATA/"toolchains/navix_posting_20260917"
PARENT=DATA/"comparisons/navix_posting_20260917"
MATCHED=DATA/"comparisons/matched_baseline_20260917"
FILES={
    "BKTIndex.cpp":"AnnService/src/Core/BKT/BKTIndex.cpp",
    "SPANNIndex.cpp":"AnnService/src/Core/SPANN/SPANNIndex.cpp",
    "BKTree.h":"AnnService/inc/Core/Common/BKTree.h",
    "RelativeNeighborhoodGraph.h":"AnnService/inc/Core/Common/RelativeNeighborhoodGraph.h",
    **{name:"AnnService/"+name for name in ("NativeSupplier.h","PostingSupplier.h","FullHooks.h",
        "NativeNeighborHooks.h","NativeFunctionRef.h","NavixPolicy.h","Qualification.h")}}

def protect():
    expected=json.loads((OUTPUT/"protection.json").read_text())
    differences=[str(path) for path,digest in expected.items() if sha(path)!=digest]
    assert not differences,differences
    assert not json.loads((MATCHED/"OPERATOR_STOP.json").read_text())["resume_allowed"]
    return True

def initialize():
    assert not (TOOL/"source").exists()
    protection=json.loads((DATA/"comparisons/navix_hotpath_20260918/protection.json").read_text())
    for version in ("navix_posting_20260917","navix_hotpath_20260918","cost_arbitration_v2_20260917"):
        for root in (HERE.with_name(version),DATA/"toolchains"/version/"source"):
            for path in root.rglob("*"):
                if path.is_file() and "__pycache__" not in str(path):
                    protection[str(path)]=sha(path)
        for path in (DATA/"toolchains"/version/"harness").glob("*bench"):
            protection[str(path)]=sha(path)
    for name,digest in {
        "report.json":"4fbff4f2ee833d036094b4fc551d599aeb2711428d085129580f9265775e1f03",
        "milestone_manifest.json":"36dca772e41269f09174128f62c37b518db9ca6ddcdefc4a008740e78f888634",
    }.items():
        path=DATA/"comparisons/navix_hotpath_20260918"/name
        assert sha(path)==digest
        protection[str(path)]=digest
    write(OUTPUT/"protection.json",protection)
    protect()
    shutil.copytree(BASE/"source",TOOL/"source",symlinks=True,
        ignore=shutil.ignore_patterns("Release","CMakeCache.txt","CMakeFiles","__pycache__", ".venv"))
    for name in list(FILES)+["CMakeLists.txt","MatchedBench.cpp","NativeTests.cpp",
        "NativeNProbeSweep.h","NAVIX_LICENSE","process.py","test_protocol.py"]:
        source=HERE.with_name("navix_posting_20260917")/name
        if source.exists(): shutil.copyfile(source,HERE/name)
    shutil.copytree(HERE.with_name("navix_posting_20260917")/"configs",HERE/"configs")
    write(OUTPUT/"preparation.json",{"base":"frozen OLD, not failed hotpath",
        "source":str(BASE/"source"),"symlinks":True,
        "excludes":["Release","CMakeCache.txt","CMakeFiles","__pycache__",".venv"],
        "failed_layers_removed":"all failed component generation cache, selective prefetch, emitter, and scratch changes absent",
        "protected_files":len(protection)})

def install():
    protect()
    for name,path in FILES.items():
        target=TOOL/"source"/path
        assert not target.is_symlink()
        if not target.exists() or sha(HERE/name)!=sha(target):
            shutil.copyfile(HERE/name,target)

if __name__=="__main__":
    import sys
    if sys.argv[1:] == ["--initialize"]: initialize()
    else: install()
