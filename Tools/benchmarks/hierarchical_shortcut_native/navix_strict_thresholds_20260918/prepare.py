"""Isolated threshold-only successor. Never install into production or old builds."""
import hashlib
import json
from pathlib import Path
import shutil

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[4]
DATA=ROOT/"datasets/sift1m_zipf200_sparse193_numeric"
TOOL=DATA/"toolchains"/HERE.name
OUTPUT=DATA/"comparisons"/HERE.name
BASE=DATA/"toolchains/navix_thresholds_20260918"
PARENT=DATA/"comparisons/navix_thresholds_20260918"
MATCHED=DATA/"comparisons/matched_baseline_20260917"
FILES={
    "BKTIndex.cpp":"AnnService/src/Core/BKT/BKTIndex.cpp",
    "SPANNIndex.cpp":"AnnService/src/Core/SPANN/SPANNIndex.cpp",
    "BKTree.h":"AnnService/inc/Core/Common/BKTree.h",
    "RelativeNeighborhoodGraph.h":"AnnService/inc/Core/Common/RelativeNeighborhoodGraph.h",
    **{name:"AnnService/"+name for name in ("NativeSupplier.h","PostingSupplier.h","FullHooks.h",
        "NativeNeighborHooks.h","NativeFunctionRef.h","NavixPolicy.h","Qualification.h")}}
THRESHOLDS=("0.1","0.05")

def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b""): h.update(block)
    return h.hexdigest()

def write(path,value): Path(path).write_text(json.dumps(value,indent=2)+"\n")
def case(mode,t): return f"{mode}_twohop_{t}_posting_0.01"

def protect():
    expected=json.loads((OUTPUT/"protection.json").read_text())
    differences=[p for p,h in expected.items() if sha(p)!=h]
    assert not differences,differences
    assert not json.loads((MATCHED/"OPERATOR_STOP.json").read_text())["resume_allowed"]
    return True

def protect_inputs_and_modules():
    protection=json.loads((OUTPUT/"protection.json").read_text())
    for record in json.loads((MATCHED/"input_hashes.json").read_text()):
        path=record["path"]
        assert sha(path)==record["sha256"],path
        protection[path]=record["sha256"]
    for root in (ROOT/"SPTAG/sptag",ROOT/"SPTAG"):
        for path in root.glob("*.so"):
            protection[str(path)]=sha(path)
    write(OUTPUT/"protection.json",protection)

def initialize():
    assert not TOOL.exists() and not OUTPUT.exists()
    TOOL.mkdir(parents=True); OUTPUT.mkdir(parents=True)
    checks={
        PARENT/"report.json":"20a92ac320e5440d727ba0d3443d768a4da828cb548548c9082bf254e614d44b",
        PARENT/"summary.json":"1b164b2ae4129133fafaec611ad39edef7d1378285bcbbf14313357662837b52",
        DATA/"toolchains/cost_arbitration_v2_20260917/harness/cost-bench":"6df6541aed40b26ff5a66b006912a146f404c1df7b75125139c5c30c8a3bbc39"}
    for p,h in checks.items(): assert sha(p)==h,str(p)
    protection={}
    roots=[HERE.with_name("navix_thresholds_20260918"),BASE,PARENT,
        ROOT/"SPTAG/AnnService",ROOT/"SPTAG/Release",MATCHED/"plots",
        HERE.with_name("cost_arbitration_v2_20260917")/"configs"]
    for root in roots:
        for p in root.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                protection[str(p)]=sha(p)
    for name in ("registration.json","input_hashes.json","cores.json","OPERATOR_STOP.json"):
        p=MATCHED/name;protection[str(p)]=sha(p)
    protection.update({str(p):h for p,h in checks.items()})
    for p,h in json.loads((PARENT/"milestone_manifest.json").read_text()).items():
        assert sha(p)==h,p
        protection[p]=h
    for p,h in json.loads((PARENT/"protection.json").read_text()).items():
        assert sha(p)==h,p
        protection[p]=h
    write(OUTPUT/"protection.json",protection)
    protect_inputs_and_modules()
    shutil.copytree(BASE/"source",TOOL/"source",symlinks=True,
        ignore=shutil.ignore_patterns("Release","CMakeCache.txt","CMakeFiles","__pycache__", ".venv"))
    configs=HERE/"configs";configs.mkdir()
    diagnostics=HERE/"diagnostic_configs";diagnostics.mkdir()
    for scenario in ("broad_tag","extreme_tag","unfilter"):
        for mode in ("posting",):
            for t in THRESHOLDS:
                folder="configs" if scenario=="broad_tag" else "diagnostic_configs"
                text=(HERE.with_name("navix_thresholds_20260918")/
                    f"{folder}/{scenario}_{mode}_twohop_{t}_posting_0.05.ini").read_text()
                value=text.replace("_posting_0.05","_posting_0.01")
                value=value.replace("NavixPostingThreshold=0.05","NavixPostingThreshold=0.01")
                name=f"{scenario}_{case(mode,t)}.ini"
                if scenario=="broad_tag": (configs/name).write_text(value)
                else:
                    (diagnostics/name).write_text(value)
    shutil.copyfile(HERE.with_name("cost_arbitration_v2_20260917")/"configs/broad_tag_graph.ini",
                    configs/"broad_tag_postfilter_graph.ini")
    write(OUTPUT/"preparation.json",{"base":str(BASE),"checks":{str(p):h for p,h in checks.items()},
        "protected_files":len(protection),"copy_symlinks":True,"production_untouched":True,
        "docs":"Main-owned GettingStart divergence explicitly excluded from protection; never edited here"})
    protect()

def install():
    protect()
    for name,path in FILES.items():
        target=TOOL/"source"/path
        assert not target.is_symlink()
        if not target.exists() or sha(HERE/name)!=sha(target): shutil.copyfile(HERE/name,target)

if __name__=="__main__":
    import sys
    initialize() if sys.argv[1:]==["--initialize"] else install()
