"""Four bounded 32-query parity fixtures, not an extreme performance campaign."""
import configparser
import hashlib
import json
import os
from pathlib import Path
import numpy as np
from prepare import HERE,TOOL,OUTPUT,PARENT,protect,sha,write
from process import process
from run import NATIVE,ACTION,QUALIFICATION,require

def path(prefix,suffix): return Path(str(prefix)+suffix)
def digest(value): return hashlib.sha256(value).hexdigest()
def variable_prefix(prefix,suffix):
    data=np.fromfile(path(prefix,suffix),dtype="<i4")
    end=0; elements=0
    for _ in range(32):
        n=int(data[end]); require(n>=0,"Negative variable count")
        end+=n+1; elements+=n
    return data[:end].tobytes(),elements

def main():
    require(not any(k.startswith(("SPTAG_","SPANN_","SHORTCUT_")) or
        k in ("LD_PRELOAD","OMP_NUM_THREADS","OMP_PROC_BIND","OMP_PLACES") for k in os.environ),
        "Forbidden ordinary/replay override")
    protect()
    configs=HERE/"diagnostic_configs"; configs.mkdir(exist_ok=True)
    records=[]
    for scenario,case in (("extreme_tag","navix"),("extreme_tag","posting"),
                          ("unfilter","posting"),("unfilter","navix")):
        cfg=configparser.ConfigParser();cfg.optionxform=str
        cfg.read(HERE/f"configs/{scenario}_{case}.ini")
        cfg["Benchmark"].update(DiagnosticOnly="true",Warmup="32",MaxQueries="32")
        target=configs/f"{scenario}_{case}.ini"
        with target.open("w") as stream: cfg.write(stream,space_around_delimiters=False)
        directory=OUTPUT/f"small_{scenario}_{case}"
        if directory.exists():
            require(json.loads((directory/"exit.json").read_text())["returncode"]==0,"Failed prior replay")
            record=directory/"validation.json"
            seconds=json.loads(record.read_text())["process_seconds"] if record.exists() else None
        else:
            directory.mkdir()
            seconds=process(["numactl","--cpunodebind=2","--membind=2",
                str(TOOL/"harness/navix-bench"),"--config",str(target)],directory,max_seconds=90)
        text=(directory/"stdout.log").read_text()
        native=[json.loads(line) for line in text.splitlines() if line.startswith('{"engine":')]
        require(len(native)==1 and native[0]["queries"]==32 and cfg["Benchmark"]["Warmup"]=="32",
            "Bounded32 protocol mismatch")
        require(text.count("MATCHED_LOAD calls=1")==1 and native[0]["index_load_count"]==1,
            "One load required")
        for component in ("Vector (160091,128)","BKT (1,160093)","RNG (160091,32)",
                          "Vector (4098,128)","BKT (1,4100)","RNG (4098,32)"):
            require(text.count("Load "+component+" Finish!")==1,"Native array duplicate load")
        prefix=directory/"nprobe_24"
        oracle=PARENT/f"{scenario}_{'posting' if scenario=='unfilter' else case}_r1/nprobe_24"
        hashes={}
        for suffix,dtype,width in ((".ids.i32","<i4",10),(".dist.f32","<f4",10),
            (".work.u64","<u8",8),(".native.u64","<u8",18),(".navix.u64","<u8",19)):
            expected=np.fromfile(path(oracle,suffix),dtype=dtype,count=32*width).tobytes()
            actual=path(prefix,suffix).read_bytes()
            require(actual==expected,"Frozen32 exact mismatch: "+suffix)
            hashes[suffix]=digest(actual)
            if scenario=="unfilter" and suffix in (".ids.i32",".dist.f32",".work.u64"):
                original=PARENT/"unfilter_original_r1/nprobe_24"
                require(actual==np.fromfile(path(original,suffix),dtype=dtype,count=32*width).tobytes(),
                    "Native-original empty predicate mismatch")
        for suffix,dtype,width in ((".decisions.f64","<f8",11),(".rows.u64","<u8",5)):
            data=np.fromfile(path(oracle,suffix),dtype=dtype).reshape(-1,width)
            expected=data[data[:,0]<32].tobytes()
            actual=path(prefix,suffix).read_bytes()
            require(actual==expected,"Frozen32 trajectory mismatch: "+suffix)
            hashes[suffix]=digest(actual)
        for suffix in (".head.i32",".own.i32"):
            expected,n=variable_prefix(oracle,suffix)
            actual=path(prefix,suffix).read_bytes()
            require(actual==expected,"Frozen32 variable mismatch: "+suffix)
            hashes[suffix]=digest(actual)
            if suffix==".head.i32":
                expected=np.fromfile(path(oracle,".head.f32"),dtype="<f4",count=n).tobytes()
                actual=path(prefix,".head.f32").read_bytes()
                require(actual==expected,"Frozen32 head distance mismatch")
                hashes[".head.f32"]=digest(actual)
        q=np.fromfile(path(prefix,".qualification.u64"),dtype="<u8").reshape(32,6)
        require(np.all(q[:,5]==0),"Warm replay storage growth")
        if scenario=="unfilter": require(np.all(q==0),"Unfiltered new qualification work")
        records.append({"scenario":scenario,"case":case,"queries":32,"warmup":32,
            "timing_kind":"bounded parity only; no ordinary extreme/unfilter latency claim",
            "process_seconds":seconds,"oracle":str(oracle),"native":native[0],
            "config":str(target),"config_sha256":sha(target),"exact_payload_hashes":hashes,
            "qualification_head_phase_mean":dict(zip(QUALIFICATION,q.mean(axis=0).tolist()))})
        write(directory/"validation.json",records[-1])
        print("PASS",scenario,case,"first32 IDs/distances/work/heads/own/branches/CSR",flush=True)
    protect();write(OUTPUT/"small_replay.json",records)
if __name__=="__main__":main()
