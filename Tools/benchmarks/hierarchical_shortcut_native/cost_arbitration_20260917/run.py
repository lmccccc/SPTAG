"""Bounded corrected-policy measurements only. No sweep or automatic continuation."""
import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import time
import numpy as np
from prepare import HERE, DATA, TOOL, OUTPUT, BASE, MATCHED, sha, write
from process import process

NATIVE=["head_distances","routing_distances","child_distances","checked","calls","returns",
        "representative_distances","csr_members","signature_checks","signature_rejects","h2_rows","h3_rows",
        "queue_offers","queue_accepted","queue_rejected","filtered","default","native_distance"]
ACTION=["estimates","graph_choices","posting_choices","discovery_choices","graph_members","graph_fresh",
        "graph_predicate_checks","graph_valid","graph_useful","posting_members","posting_fresh",
        "posting_predicate_checks","posting_valid","posting_useful"]
CORE_COLS=[0,1,2,3,12,13,14,15,16,17]
SUFFIXES=(".ids.i32",".dist.f32",".work.u64")


def require(ok,message):
    if not ok: raise RuntimeError(message)


def protect():
    forbidden=[k for k in os.environ if k.startswith(("SPTAG_","SPANN_","SHORTCUT_")) or
               k in ("LD_PRELOAD","OMP_NUM_THREADS","OMP_PROC_BIND","OMP_PLACES")]
    require(not forbidden,"Native INIs only: "+str(forbidden))
    require(not json.loads((MATCHED/"OPERATOR_STOP.json").read_text())["resume_allowed"],"Curve stop removed")
    reg=json.loads((MATCHED/"registration.json").read_text())
    view=DATA/"toolchains/matched_baseline_20260917/buffered_view/tenant_0"
    require(sha(view/"indexloader.ini")==reg["view_loader_sha256"],"Loader changed")
    for name,path in reg["view_targets"].items(): require(str((view/name).resolve())==path,"Index target changed")
    for item in json.loads((MATCHED/"input_hashes.json").read_text()):
        path=Path(item["path"])
        require(path.stat().st_size==item["bytes"],"Input size changed")
        if path.stat().st_mtime_ns!=item["mtime_ns"]:
            require(sha(path)==item["sha256"],"Protected input changed")
    for core in json.loads((MATCHED/"cores.json").read_text())["cores"].values():
        for item in core["linked_files"]: require(sha(item["path"])==item["sha256"],"Frozen core changed")
    for name,digest in json.loads((OUTPUT/"sources.json").read_text()).items():
        require(sha(TOOL/"source"/name)==digest,"Compiled source changed")
    return reg["index_fingerprint"]


def controls():
    cfg=configparser.ConfigParser(); cfg.optionxform=str
    cfg.read(HERE/"configs/broad_tag_graph.ini")
    cfg["Benchmark"]["Case"]=cfg["SearchSSDIndex"]["ArbitrationMode"]="estimate_only"
    with (HERE/"configs/broad_tag_estimate_only.ini").open("x") as out: cfg.write(out,space_around_delimiters=False)
    import shutil
    shutil.copyfile(MATCHED/"snapshot/experiment/configs/unfilter_h1_original_smoke.ini",
                    HERE/"configs/unfilter_original.ini")
    for scenario in ("broad_tag","extreme_tag"):
        for mode in ("auto","graph"):
            cfg=configparser.ConfigParser(); cfg.optionxform=str
            cfg.read(HERE/f"configs/{scenario}_{mode}.ini")
            cfg["Benchmark"].update(Warmup="32",MaxQueries="32",DiagnosticOnly="true")
            with (HERE/f"configs/fixture_{scenario}_{mode}.ini").open("x") as out:
                cfg.write(out,space_around_delimiters=False)


def freeze():
    source=(HERE/"MatchedBench.cpp").read_text()
    body=source.split("const auto start = std::chrono::steady_clock::now();",1)[1].split(
        "const auto finish = std::chrono::steady_clock::now();",1)[0]
    body_sha=hashlib.sha256(body.encode()).hexdigest()
    require(body_sha==json.loads((MATCHED/"cores.json").read_text())["common_timed_body_sha256"],
            "Ordinary timed body changed")
    value={"source_sha256":sha(HERE/"MatchedBench.cpp"),"timed_body_sha256":body_sha,
        "binary":str(TOOL/"harness/cost-bench"),"binary_sha256":sha(TOOL/"harness/cost-bench"),
        "libraries":{p.name:sha(p) for p in sorted((TOOL/"source/Release").glob("*.a"))},
        "configs":{p.name:sha(p) for p in sorted((HERE/"configs").glob("*.ini"))}}
    path=OUTPUT/"runtime.json"
    if path.exists(): require(json.loads(path.read_text())==value,"Frozen runtime changed")
    else: write(path,value)
    return value


def invoke(label,scenario,case,config,diagnostic=False):
    directory=OUTPUT/label; directory.mkdir()
    binary=TOOL/("matched-original" if case=="original" else "matched-current" if case=="h3" else "harness/cost-bench")
    wall=process(["numactl","--cpunodebind=2","--membind=2",str(binary),"--config",str(config)],directory)
    text=(directory/"stdout.log").read_text()
    require(text.count("MATCHED_LOAD calls=1")==1,"Wrong native load count")
    for component in ("Vector (160091,128)","BKT (1,160093)","RNG (160091,32)",
                      "Vector (4098,128)","BKT (1,4100)","RNG (4098,32)"):
        require(text.count("Load "+component+" Finish!")==1,"Physical component mismatch/reload")
    rows=[json.loads(s) for s in text.splitlines() if s.startswith('{"engine":')]
    require(len(rows)==1,"Only one predetermined native array point permitted")
    row=rows[0]; count=32 if diagnostic else 1000
    require(row["queries"]==count and row["index_load_count"]==row["probe_count"]==row["workspace_resets"]==1 and
            row["failed_queries"]==row["measure_offset"]==0,"Native protocol mismatch")
    prefix=directory/f'nprobe_{row["nprobe"]}'
    ids=np.fromfile(str(prefix)+".ids.i32",dtype="<i4").reshape(count,10)
    distances=np.fromfile(str(prefix)+".dist.f32",dtype="<f4").reshape(count,10)
    work=np.fromfile(str(prefix)+".work.u64",dtype="<u8").reshape(count,8)
    require(np.all((ids>=-1)&(ids<1000000)) and np.all(np.isfinite(distances[ids>=0])),"Invalid result")
    workload=json.loads((DATA/"query/workloads.json").read_text())
    truth=np.load(workload["truth"][scenario]["ids"],mmap_mode="r")
    recall=sum(len(set(r[r>=0])&set(truth[i,:10])) for i,r in enumerate(ids))/(10*count)
    require(abs(recall-row["recall"])<1e-10,"Recall mismatch")
    if scenario!="unfilter":
        attrs=np.load(workload["attributes"],mmap_mode="r")
        tags=np.load(workload["flat_query_tags"][scenario]).reshape(-1)
        require(all(np.all(attrs[r[r>=0],0]==tags[i]) for i,r in enumerate(ids)),
                "Final result violates actual categorical predicate")
    payload={s:sha(Path(str(prefix)+s)) for s in SUFFIXES}
    record={"label":label,"scenario":scenario,"case":case,"directory":str(directory),"native":row,
            "process_seconds":wall,"diagnostic_only":diagnostic,"payloads":payload,
            "ssd_mean":work.mean(axis=0).tolist(),"exact_predicate_valid":True,
            "returned_mean":float((ids>=0).sum()/count)}
    if case not in ("original","h3"):
        native=np.fromfile(str(prefix)+".native.u64",dtype="<u8").reshape(count,18)
        actions=np.fromfile(str(prefix)+".arbitration.u64",dtype="<u8").reshape(count,14)
        decisions=np.fromfile(str(prefix)+".decisions.f64",dtype="<f8").reshape(-1,37)
        require(np.all(np.isfinite(decisions)),"Nonfinite estimator trace")
        require(np.all(decisions[:,6]>=decisions[:,5]),"Native checked count decreased")
        graph_selected=decisions[decisions[:,4]==0]
        require(np.all(graph_selected[:,30:37]==0),"Graph-selected decision expanded CSR or representatives")
        if scenario=="unfilter":
            require(np.all(native[:,[2,4,5,6,7,8,9,10,11,15]]==0) and np.all(native[:,16:]==1) and
                    np.all(actions==0),"Empty predicate did arbitration/upper work")
        else: require(np.all(native[:,15]==1)&np.all(native[:,16]==0),"Filtered admission bypass")
        if case in ("graph","estimate_only"):
            require(np.all(native[:,[2,4,5,6,7,10,11]]==0),"Graph control did posting expansion")
        record.update(native_mean=dict(zip(NATIVE,native.mean(axis=0).tolist())),
            action_mean=dict(zip(ACTION,actions.mean(axis=0).tolist())),
            core_hash=hashlib.sha256(native[:,CORE_COLS].tobytes()).hexdigest(),
            trace_sha256=sha(Path(str(prefix)+".decisions.f64")),
            native_sha256=sha(Path(str(prefix)+".native.u64")),
            decisions=len(decisions),posting_query_count=int(np.count_nonzero(actions[:,2])),
            discovery_query_count=int(np.count_nonzero(actions[:,3])),
            heads_sha256=sha(Path(str(prefix)+".head.i32")),
            head_distances_sha256=sha(Path(str(prefix)+".head.f32")),
            own_sha256=sha(Path(str(prefix)+".own.i32")))
    write(directory/"record.json",record)
    print(label,row["recall"],row["mean_latency_ms"],record.get("posting_query_count"),flush=True)
    return record


def fixtures():
    controls(); protect(); freeze()
    records=[]
    for scenario in ("broad_tag","extreme_tag"):
        for case in ("auto","graph"):
            records.append(invoke(f"fixture_{scenario}_{case}",scenario,case,
                                  HERE/f"configs/fixture_{scenario}_{case}.ini",True))
    write(OUTPUT/"fixtures.json",records)


def equal_core(a,b):
    require(a["payloads"]==b["payloads"],"Final IDs/distances/SSD work differ")
    if "core_hash" in a and "core_hash" in b:
        require(all(a[k]==b[k] for k in ("core_hash","heads_sha256","head_distances_sha256","own_sha256")),
                "Native navigation or own/head output differs")


def measure():
    require((OUTPUT/"fixtures.json").exists(),"Preflight required")
    protect(); freeze()
    plan=[("broad_tag","graph","broad_tag_graph"),("broad_tag","auto","broad_tag_auto"),
          ("broad_tag","original","broad_original80"),("broad_tag","h3","broad_h3_32"),
          ("unfilter","original","unfilter_original"),("unfilter","graph","unfilter_graph"),
          ("unfilter","auto","unfilter_auto"),("extreme_tag","graph","extreme_tag_graph"),
          ("extreme_tag","auto","extreme_tag_auto"),("broad_tag","estimate_only","broad_tag_estimate_only")]
    start=time.monotonic(); records=[]
    for repeat,order in enumerate((plan,plan[::-1]),1):
        for scenario,case,config in order:
            records.append(invoke(f"{scenario}_{case}_r{repeat}",scenario,case,HERE/f"configs/{config}.ini"))
    summary=[]
    for scenario,case,_ in plan:
        pair=[r for r in records if r["scenario"]==scenario and r["case"]==case]
        equal_core(*pair)
        if "trace_sha256" in pair[0]:
            require(pair[0]["trace_sha256"]==pair[1]["trace_sha256"],"Decision determinism failed")
        ms=[r["native"]["mean_latency_ms"] for r in pair]
        summary.append({"scenario":scenario,"case":case,"nprobe":pair[0]["native"]["nprobe"],
            "ordinary_ms":statistics.mean(ms),"ordinary_ms_runs":ms,"qps":1000/statistics.mean(ms),
            "recall_at_10":pair[0]["native"]["recall"],"returned_mean":pair[0]["returned_mean"],
            "ssd_mean":pair[0]["ssd_mean"],"native_mean_capture":pair[0].get("native_mean"),
            "action_mean_capture":pair[0].get("action_mean"),
            "posting_query_count":pair[0].get("posting_query_count",0),
            "discovery_query_count":pair[0].get("discovery_query_count",0)})
    for repeat in (1,2):
        get=lambda s,c:next(r for r in records if r["label"]==f"{s}_{c}_r{repeat}")
        equal_core(get("unfilter","original"),get("unfilter","auto"))
        equal_core(get("unfilter","graph"),get("unfilter","auto"))
        equal_core(get("broad_tag","graph"),get("broad_tag","estimate_only"))
    sparse=next(r for r in summary if r["scenario"]=="extreme_tag" and r["case"]=="auto")
    require(sparse["posting_query_count"]>0,"No posting selection on real sparse workload")
    write(OUTPUT/"summary.json",summary)
    write(OUTPUT/"operations.json",{"records":records,"operation_seconds":time.monotonic()-start,
        "native_process_seconds":sum(r["process_seconds"] for r in records),
        "ordinary_seconds":sum(r["native"]["ordinary_seconds"] for r in records),
        "ordinary_batches":len(records),"index_loads":len(records),"protection":protect()})
    print(json.dumps(summary,indent=2))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase",choices=("fixtures","measure"))
    globals()[parser.parse_args().phase]()
