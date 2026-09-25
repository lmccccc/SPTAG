"""Create a new isolated toolchain and fixed native INIs; never modify frozen parents."""
import configparser
import hashlib
import json
from pathlib import Path
import shutil

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[3]
DATA=REPO.parent/"datasets/sift1m_zipf200_sparse193_numeric"
BASE=DATA/"toolchains/cost_arbitration_v2_20260917"
PARENT=DATA/"comparisons/cost_arbitration_v2_20260917"
MATCHED=DATA/"comparisons/matched_baseline_20260917"
TOOL=DATA/"toolchains/navix_posting_20260917"
OUTPUT=DATA/"comparisons/navix_posting_20260917"

def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(8*1024*1024),b""): h.update(block)
    return h.hexdigest()

def write(path,value):
    with Path(path).open("x") as out:
        json.dump(value,out,indent=2); out.write("\n")

FILES={
    "BKTIndex.cpp":"AnnService/src/Core/BKT/BKTIndex.cpp",
    "SPANNIndex.cpp":"AnnService/src/Core/SPANN/SPANNIndex.cpp",
    "BKTree.h":"AnnService/inc/Core/Common/BKTree.h",
    "RelativeNeighborhoodGraph.h":"AnnService/inc/Core/Common/RelativeNeighborhoodGraph.h",
    **{name:"AnnService/"+name for name in (
        "NativeSupplier.h","PostingSupplier.h","FullHooks.h","NativeNeighborHooks.h",
        "NativeFunctionRef.h","NavixPolicy.h")}}

def install():
    for name,path in FILES.items():
        target=TOOL/"source"/path
        if target.is_symlink(): raise RuntimeError("Refuse source symlink "+str(target))
        shutil.copyfile(HERE/name,target)
    retired=TOOL/"source/AnnService/CostModel.h"
    if retired.exists(): retired.unlink()

def main():
    assert not json.loads((MATCHED/"OPERATOR_STOP.json").read_text())["resume_allowed"]
    assert sha(PARENT/"report.json")=="a17f9ab85ce54a633badefd5fe814adec3150fe4edf5b63b1c7f42f43295470c"
    TOOL.mkdir(); OUTPUT.mkdir()
    proof=json.loads((PARENT/"sources.json").read_text())
    for name,digest in proof.items(): assert sha(BASE/"source"/name)==digest
    runtime=json.loads((PARENT/"runtime.json").read_text())
    assert sha(runtime["binary"])==runtime["binary_sha256"]
    for name,digest in runtime["libraries"].items(): assert sha(BASE/"source/Release"/name)==digest
    shutil.copytree(BASE/"source",TOOL/"source",symlinks=True,
                    ignore=shutil.ignore_patterns("Release","__pycache__"))
    install()
    shutil.copyfile(MATCHED/"snapshot/matched-original",TOOL/"matched-original")
    shutil.copymode(MATCHED/"snapshot/matched-original",TOOL/"matched-original")
    write(OUTPUT/"parent.json",{"base_source":proof,"runtime":runtime,
        "report_sha256":sha(PARENT/"report.json"),"stop_sha256":sha(MATCHED/"OPERATOR_STOP.json"),
        "original_current_provenance":json.loads((MATCHED/"cores.json").read_text())})
    shutil.copyfile(PARENT/"production_protection.json",OUTPUT/"production_protection.json")
    (HERE/"configs").mkdir()
    previous=HERE.with_name("cost_arbitration_v2_20260917")
    for scenario in ("broad_tag","extreme_tag","unfilter"):
        for mode in ("navix","posting","original"):
            cfg=configparser.ConfigParser(); cfg.optionxform=str
            source=previous/f"configs/{scenario}_auto.ini"
            if mode=="original": source=previous/"configs/unfilter_original.ini"
            cfg.read(source)
            if mode=="original" and scenario!="unfilter":
                ref=configparser.ConfigParser(); ref.optionxform=str
                ref.read(previous/f"configs/{scenario}_auto.ini")
                cfg["Benchmark"]=dict(ref["Benchmark"])
            cfg["Benchmark"]["Case"]="h1_original" if mode=="original" else mode
            cfg["Benchmark"]["Scenario"]=scenario
            cfg["SearchSSDIndex"]["InternalResultNum"]="24"
            cfg["SearchSweep"]["NProbe"]="[24]"
            for key in list(cfg["SearchSSDIndex"]):
                if key.lower().startswith("arbitration"): del cfg["SearchSSDIndex"][key]
            if mode!="original":
                cfg["SearchSSDIndex"].update(NavixMode=mode,NavixCapture="false",NavixPostingThreshold="0.05")
            with (HERE/f"configs/{scenario}_{mode}.ini").open("x") as out:
                cfg.write(out,space_around_delimiters=False)

if __name__=="__main__": main()
