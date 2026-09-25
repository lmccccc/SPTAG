"""One fixed two-repetition n24 milestone. Ordinary timing precedes untimed captures."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import time
import numpy as np
from prepare import HERE,DATA,BASE,PARENT,MATCHED,TOOL,OUTPUT,FILES,sha,write
from process import process

NATIVE=["head_distances","routing_distances","child_distances","checked","calls","returns",
        "representative_distances","csr_members","signature_checks","signature_rejects","h2_rows","h3_rows",
        "queue_offers","queue_accepted","queue_rejected","filtered","default","native_distance"]
ACTION=["no_neighbors","one_hop","directed","full_two_hop","posting","fallbacks",
        "qualification_checks","second_rows","intermediate_distances","graph_members","graph_fresh",
        "graph_predicate_checks","graph_valid","graph_useful","posting_members","posting_fresh",
        "posting_predicate_checks","posting_valid","posting_useful"]
SUFFIXES=(".ids.i32",".dist.f32",".work.u64")

def require(value,message):
    if not value: raise RuntimeError(message)

def protect():
    forbidden=[k for k in os.environ if k.startswith(("SPTAG_","SPANN_","SHORTCUT_")) or
               k in ("LD_PRELOAD","OMP_NUM_THREADS","OMP_PROC_BIND","OMP_PLACES")]
    require(not forbidden,"Native INIs only: "+str(forbidden))
    parent=json.loads((OUTPUT/"parent.json").read_text())
    require(sha(MATCHED/"OPERATOR_STOP.json")==parent["stop_sha256"],"Operator stop changed")
    require(not json.loads((MATCHED/"OPERATOR_STOP.json").read_text())["resume_allowed"],"Curve resume forbidden")
    require(sha(PARENT/"report.json")==parent["report_sha256"],"Frozen v2 report changed")
    for name,digest in parent["base_source"].items(): require(sha(BASE/"source"/name)==digest,"Frozen v2 source changed")
    for name,digest in parent["runtime"]["libraries"].items():
        require(sha(BASE/"source/Release"/name)==digest,"Frozen v2 library changed")
    require(sha(parent["runtime"]["binary"])==parent["runtime"]["binary_sha256"],"Frozen v2 binary changed")
    for path,digest in json.loads((OUTPUT/"production_protection.json").read_text()).items():
        # This documentation is owned by the parent milestone, not native code.
        # Its pre-measurement divergence is preserved, never overwritten.
        if Path(path)==HERE.parents[3]/"docs/GettingStart.md":
            require((OUTPUT/"inherited-protection-differences.json").exists(),"Missing documentation provenance")
            continue
        require(sha(path)==digest,"Production changed: "+path)
    for prior in (PARENT,DATA/"comparisons/cost_arbitration_20260917"):
        for path,digest in json.loads((prior/"milestone_manifest.json").read_text()).items():
            require(sha(path)==digest,"Frozen milestone changed: "+path)
    reg=json.loads((MATCHED/"registration.json").read_text())
    view=DATA/"toolchains/matched_baseline_20260917/buffered_view/tenant_0"
    require(sha(view/"indexloader.ini")==reg["view_loader_sha256"],"Loader changed")
    for name,path in reg["view_targets"].items(): require(str((view/name).resolve())==path,"Index target changed")
    for item in json.loads((MATCHED/"input_hashes.json").read_text()):
        p=Path(item["path"])
        require(p.stat().st_size==item["bytes"],"Input size changed")
        if p.stat().st_mtime_ns!=item["mtime_ns"]: require(sha(p)==item["sha256"],"Input changed")
    for core in json.loads((MATCHED/"cores.json").read_text())["cores"].values():
        for item in core["linked_files"]: require(sha(item["path"])==item["sha256"],"Matched core changed")
    require(sha(TOOL/"matched-original")==sha(MATCHED/"snapshot/matched-original"),"Original changed")
    for name,path in FILES.items(): require(sha(HERE/name)==sha(TOOL/"source"/path),"Installed source differs")
    require(not (TOOL/"source/AnnService/CostModel.h").exists(),"Old cost model remains")
    return reg["index_fingerprint"]

def freeze():
    text=(HERE/"MatchedBench.cpp").read_text()
    body=text.split("const auto start = std::chrono::steady_clock::now();",1)[1].split(
        "const auto finish = std::chrono::steady_clock::now();",1)[0]
    digest=hashlib.sha256(body.encode()).hexdigest()
    require(digest=="9f412c63b71f7310e09180dff0858ccef3957bd20d7a96c34fdb440e3d559d53","Timed body changed")
    value={"timed_body_sha256":digest,"binary":str(TOOL/"harness/navix-bench"),
        "binary_sha256":sha(TOOL/"harness/navix-bench"),
        "libraries":{p.name:sha(p) for p in sorted((TOOL/"source/Release").glob("*.a"))},
        "sources":{name:sha(HERE/name) for name in FILES},
        "harness_source_sha256":sha(HERE/"MatchedBench.cpp"),
        "configs":{p.name:sha(p) for p in sorted((HERE/"configs").glob("*.ini"))}}
    path=OUTPUT/"runtime.json"
    if path.exists(): require(json.loads(path.read_text())==value,"Frozen runtime changed")
    else: write(path,value)
    return value

def invoke(label,scenario,case):
    directory=OUTPUT/label;directory.mkdir()
    binary=TOOL/("matched-original" if case=="original" else "harness/navix-bench")
    wall=process(["numactl","--cpunodebind=2","--membind=2",str(binary),"--config",
                  str(HERE/f"configs/{scenario}_{case}.ini")],directory)
    text=(directory/"stdout.log").read_text()
    require(text.count("MATCHED_LOAD calls=1")==1,"Wrong native load count")
    for component in ("Vector (160091,128)","BKT (1,160093)","RNG (160091,32)",
                      "Vector (4098,128)","BKT (1,4100)","RNG (4098,32)"):
        require(text.count("Load "+component+" Finish!")==1,"Physical reload/component mismatch")
    result=[json.loads(s) for s in text.splitlines() if s.startswith('{"engine":')]
    require(len(result)==1,"Exactly one fixed point required")
    native=result[0]
    require(native["nprobe"]==24 and native["queries"]==1000 and native["index_load_count"]==
        native["probe_count"]==native["workspace_resets"]==1 and
        native["failed_queries"]==native["measure_offset"]==0,"Protocol mismatch")
    prefix=directory/"nprobe_24"
    ids=np.fromfile(str(prefix)+".ids.i32",dtype="<i4").reshape(1000,10)
    distances=np.fromfile(str(prefix)+".dist.f32",dtype="<f4").reshape(1000,10)
    work=np.fromfile(str(prefix)+".work.u64",dtype="<u8").reshape(1000,8)
    require(np.all((ids>=-1)&(ids<1000000)) and np.all(np.isfinite(distances[ids>=0])),"Invalid results")
    workload=json.loads((DATA/"query/workloads.json").read_text())
    truth=np.load(workload["truth"][scenario]["ids"],mmap_mode="r")
    recall=sum(len(set(row[row>=0])&set(truth[i,:10])) for i,row in enumerate(ids))/10000
    require(abs(recall-native["recall"])<1e-10,"Recall mismatch")
    if scenario!="unfilter":
        attrs=np.load(workload["attributes"],mmap_mode="r")
        tags=np.load(workload["flat_query_tags"][scenario]).reshape(-1)
        require(all(np.all(attrs[row[row>=0],0]==tags[i]) for i,row in enumerate(ids)),"Exact predicate violation")
    record={"label":label,"scenario":scenario,"case":case,"native":native,"process_seconds":wall,
        "payloads":{s:sha(Path(str(prefix)+s)) for s in SUFFIXES},"ssd_mean":work.mean(axis=0).tolist(),
        "exact_predicate_valid":True,"returned_mean":float((ids>=0).sum()/1000)}
    if case!="original":
        n=np.fromfile(str(prefix)+".native.u64",dtype="<u8").reshape(1000,18)
        a=np.fromfile(str(prefix)+".navix.u64",dtype="<u8").reshape(1000,19)
        d=np.fromfile(str(prefix)+".decisions.f64",dtype="<f8").reshape(-1,11)
        rows=np.fromfile(str(prefix)+".rows.u64",dtype="<u8").reshape(-1,5)
        require(np.all(np.isfinite(d)) and np.all(d[:,7]>=d[:,6]),"Invalid decision trace")
        require(np.all(rows[:,3]==rows[:,4]),"Truncated CSR row")
        graph=d[d[:,4]!=4]
        require(np.all(graph[:,8:]==0),"Graph branch performed upper work")
        require(np.all((d[:,2]>0) | ((d[:,3]==0)&(d[:,4]==0))),"Degree-zero route mismatch")
        positive=d[d[:,2]>0]
        expected=np.where(positive[:,3]/positive[:,2]>=.5,1,
            np.where(.4*(positive[:,2]*positive[:,3]+positive[:,3])>2*positive[:,2]-positive[:,3],2,3))
        if case=="posting": expected=np.where(positive[:,3]/positive[:,2]<.05,4,expected)
        require(np.array_equal(positive[:,4],expected),"Actual native route formula mismatch")
        fallback=d[d[:,5]>0]
        require(np.all(fallback[:,4]==4) and np.all(np.isin(fallback[:,5],[2,3])),"Incorrect fallback")
        if scenario=="unfilter":
            require(np.all(n[:,[2,4,5,6,7,8,9,10,11,15]]==0) and np.all(n[:,16:]==1) and
                    np.all(a==0) and len(d)==len(rows)==0,"Empty predicate did hierarchy work")
        else: require(np.all(n[:,15]==1)&np.all(n[:,16]==0),"Filtered admission bypass")
        if case=="navix": require(np.all(n[:,[2,4,5,6,7,8,9,10,11]]==0),"NaviX-only did upper work")
        record.update(native_mean=dict(zip(NATIVE,n.mean(axis=0).tolist())),
            action_mean=dict(zip(ACTION,a.mean(axis=0).tolist())),
            branch_counts=dict(zip(ACTION[:9],a[:,:9].sum(axis=0).tolist())),
            posting_query_count=int(np.count_nonzero(a[:,4])),h1_posting_rows=int(np.count_nonzero(rows[:,1]==1)),
            complete_csr_rows=len(rows),budget_crossing_expansions=int(np.count_nonzero(d[:,7]>2048)),
            trace_hashes={s:sha(Path(str(prefix)+s)) for s in
                (".native.u64",".navix.u64",".decisions.f64",".rows.u64",".head.i32",".head.f32",".own.i32")})
    write(directory/"record.json",record)
    print(label,native["mean_latency_ms"],native["recall"],record.get("posting_query_count"),flush=True)
    return record

def measure():
    require((OUTPUT/"tests.json").exists() and json.loads((OUTPUT/"tests.json").read_text())["exit"]==0,
            "Native/protocol fixtures must pass first")
    protect();freeze()
    plan=[(s,c) for s in ("broad_tag","extreme_tag") for c in ("original","navix","posting")]
    plan.extend([("unfilter","original"),("unfilter","posting")])
    start=time.monotonic();records=[]
    for repeat,order in enumerate((plan,plan[::-1]),1):
        for scenario,case in order:records.append(invoke(f"{scenario}_{case}_r{repeat}",scenario,case))
    summary=[]
    for scenario,case in plan:
        pair=[r for r in records if r["scenario"]==scenario and r["case"]==case]
        require(pair[0]["payloads"]==pair[1]["payloads"],"Repeat result/work mismatch")
        if case!="original":require(pair[0]["trace_hashes"]==pair[1]["trace_hashes"],"Repeat trace mismatch")
        ms=[r["native"]["mean_latency_ms"] for r in pair]
        summary.append({"scenario":scenario,"case":case,"nprobe":24,"ordinary_ms_runs":ms,
            "ordinary_ms":statistics.mean(ms),"qps":1000/statistics.mean(ms),"recall_at_10":pair[0]["native"]["recall"],
            "ssd_mean":pair[0]["ssd_mean"],"returned_mean":pair[0]["returned_mean"],
            **{k:pair[0][k] for k in ("native_mean","action_mean","branch_counts","posting_query_count",
                "h1_posting_rows","complete_csr_rows","budget_crossing_expansions") if k in pair[0]}})
    for repeat in (1,2):
        pair=[r for r in records if r["label"] in (f"unfilter_original_r{repeat}",f"unfilter_posting_r{repeat}")]
        require(pair[0]["payloads"]==pair[1]["payloads"],"Original unfiltered parity failed")
    sparse=next(r for r in summary if r["scenario"]=="extreme_tag" and r["case"]=="posting")
    require(sparse["posting_query_count"]>0 and sparse["h1_posting_rows"]>0,"No real sparse posting use")
    write(OUTPUT/"summary.json",summary)
    write(OUTPUT/"operations.json",{"records":records,"operation_seconds":time.monotonic()-start,
        "ordinary_batches":len(records),"index_loads":len(records),"protection":protect()})

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase",choices=("measure",))
    globals()[parser.parse_args().phase]()
