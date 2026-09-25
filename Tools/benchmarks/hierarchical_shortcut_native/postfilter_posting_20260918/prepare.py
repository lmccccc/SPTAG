"""Isolated fused postfilter/posting milestone; frozen artifacts stay sealed."""
import hashlib
import json
from pathlib import Path
import shutil

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[4]
DATA=ROOT/"datasets/sift1m_zipf200_sparse193_numeric"
TOOL=DATA/"toolchains"/HERE.name
OUTPUT=DATA/"comparisons"/HERE.name
BASE=DATA/"toolchains/postfilter_twohop_20260918"
PARENT=DATA/"comparisons/postfilter_twohop_20260918"
MATCHED=DATA/"comparisons/matched_baseline_20260917"
FILES={
    "BKTIndex.cpp":"AnnService/src/Core/BKT/BKTIndex.cpp",
    "SPANNIndex.cpp":"AnnService/src/Core/SPANN/SPANNIndex.cpp",
    "BKTree.h":"AnnService/inc/Core/Common/BKTree.h",
    "WorkSpace.h":"AnnService/inc/Core/Common/WorkSpace.h",
    "RelativeNeighborhoodGraph.h":"AnnService/inc/Core/Common/RelativeNeighborhoodGraph.h",
    **{name:"AnnService/"+name for name in ("NativeSupplier.h","PostingSupplier.h","FullHooks.h",
        "NativeNeighborHooks.h","NativeFunctionRef.h","PostingPolicy.h","Qualification.h")}}

def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b""): h.update(block)
    return h.hexdigest()

def write(path,value): Path(path).write_text(json.dumps(value,indent=2)+"\n")
def case(mode): return f"postfilter_{mode}_ratio_0.01"

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
        PARENT/"report.json":"934d73d08d458f4b1fe3fa7d537a4fbd4c56a85dc3dac8e1b13a14437b192c32",
        PARENT/"summary.json":"011909f24735bd31e78e165c9e64fcc8b86483b772103f515f1ca7051df0cd5c",
        DATA/"toolchains/cost_arbitration_v2_20260917/harness/cost-bench":"6df6541aed40b26ff5a66b006912a146f404c1df7b75125139c5c30c8a3bbc39"}
    for p,h in checks.items(): assert sha(p)==h,str(p)
    protection={}
    roots=[HERE.with_name("postfilter_twohop_20260918"),BASE,PARENT,
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
    configs=HERE/"configs";configs.mkdir(exist_ok=True)
    diagnostics=HERE/"diagnostic_configs";diagnostics.mkdir(exist_ok=True)
    for scenario in ("broad_tag","extreme_tag","unfilter"):
        folder="configs" if scenario=="broad_tag" else "diagnostic_configs"
        text=(HERE.with_name("postfilter_twohop_20260918")/
            f"{folder}/{scenario}_postfilter_graph_twohop_0_posting_0.ini").read_text()
        for mode in ("graph","observe","posting"):
            value=text.replace("postfilter_graph_twohop_0_posting_0",case(mode))
            value="\n".join(line for line in value.splitlines() if not line.startswith("Navix"))
            value=value.replace("[SearchSweep]",
                f"PostFilterPostingMode={mode}\nPostingActivationRatio=0.01\n\n[SearchSweep]")+"\n"
            name=f"{scenario}_{case(mode)}.ini"
            if scenario=="broad_tag": (configs/name).write_text(value)
            else: (diagnostics/name).write_text(value)
            if scenario=="extreme_tag" and mode!="observe":
                (configs/name).write_text(value.replace("DiagnosticOnly=true","DiagnosticOnly=false").
                    replace("Warmup=32","Warmup=1000").replace("MaxQueries=32","MaxQueries=1000"))
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
