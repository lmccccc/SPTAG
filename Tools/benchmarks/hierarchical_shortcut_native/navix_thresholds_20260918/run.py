"""Exactly seven fresh Broad points, two reverse-order repetitions, then stop."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import time
import numpy as np
from prepare import HERE,DATA,TOOL,OUTPUT,PARENT,FILES,THRESHOLDS,case,sha,write,protect
from process import process

NATIVE=["head_distances","routing_distances","child_distances","checked","calls","returns",
        "representative_distances","csr_members","signature_checks","signature_rejects","h2_rows","h3_rows",
        "queue_offers","queue_accepted","queue_rejected","filtered","default","native_distance"]
ACTION=["no_neighbors","one_hop","directed","full_two_hop","posting","fallbacks",
        "qualification_checks","second_rows","intermediate_distances","graph_members","graph_fresh",
        "graph_predicate_checks","graph_valid","graph_useful","posting_members","posting_fresh",
        "posting_predicate_checks","posting_valid","posting_useful"]
QUALIFICATION=["eligible_requests","posting_requests","posting_evaluations",
               "exact_requests","exact_evaluations","storage_growths"]
PAYLOADS=(".ids.i32",".dist.f32",".work.u64")
TRACES=(".native.u64",".navix.u64",".decisions.f64",".rows.u64",".head.i32",".head.f32",".own.i32",".qualification.u64")
CONTROL_TRACES=(".native.u64",".head.i32",".head.f32",".own.i32")
ROUTES=("no_neighbors","one_hop","directed","full_two_hop","posting")

def require(value,message):
    if not value: raise RuntimeError(message)

def environment():
    require(not [k for k in os.environ if k.startswith(("SPTAG_","SPANN_","SHORTCUT_","NAVIX_")) or
        k in ("LD_PRELOAD","OMP_NUM_THREADS","OMP_PROC_BIND","OMP_PLACES")],"Environment overrides")

def hashes(prefix,suffixes): return {s:sha(Path(str(prefix)+s)) for s in suffixes}
def matrix(prefix,suffix,dtype,width): return np.fromfile(str(prefix)+suffix,dtype=dtype).reshape(-1,width)

def freeze():
    environment();protect()
    text=(HERE/"MatchedBench.cpp").read_text()
    body=text.split("const auto start = std::chrono::steady_clock::now();",1)[1].split(
        "const auto finish = std::chrono::steady_clock::now();",1)[0]
    digest=hashlib.sha256(body.encode()).hexdigest()
    require(digest=="9f412c63b71f7310e09180dff0858ccef3957bd20d7a96c34fdb440e3d559d53","Timed body changed")
    for name,path in FILES.items(): require(sha(HERE/name)==sha(TOOL/"source"/path),"Installed mismatch "+name)
    binary=TOOL/"harness/navix-bench"
    graph=DATA/"toolchains/cost_arbitration_v2_20260917/harness/cost-bench"
    evidence=OUTPUT/"configs";shutil.copytree(HERE/"configs",evidence)
    shutil.copytree(HERE/"diagnostic_configs",OUTPUT/"diagnostic_configs")
    write(OUTPUT/"runtime.json",{"timed_body_sha256":digest,
        "binary":{"path":str(binary),"sha256":sha(binary)},
        "postfilter_binary":{"path":str(graph),"sha256":sha(graph)},
        "libraries":{str(p):sha(p) for p in (TOOL/"source/Release").glob("*.a")},
        "sources":{name:sha(HERE/name) for name in FILES},
        "harness_source_sha256":sha(HERE/"MatchedBench.cpp"),
        "configs":{str(p):sha(p) for p in sorted(evidence.glob("*.ini"))}})

def validate(directory,scenario,mode,t,count,seconds):
    text=(directory/"stdout.log").read_text()
    require(text.count("MATCHED_LOAD calls=1")==1,"Wrong load count")
    for component in ("Vector (160091,128)","BKT (1,160093)","RNG (160091,32)",
                      "Vector (4098,128)","BKT (1,4100)","RNG (4098,32)"):
        require(text.count("Load "+component+" Finish!")==1,"Array load mismatch")
    results=[json.loads(s) for s in text.splitlines() if s.startswith('{"engine":')]
    require(len(results)==1,"One fixed point required")
    native=results[0]
    require(native["nprobe"]==24 and native["queries"]==count and native["index_load_count"]==
        native["probe_count"]==native["workspace_resets"]==1 and
        native["failed_queries"]==native["measure_offset"]==0,"Protocol mismatch")
    if mode!="postfilter": require(native["case"]==case(mode,t),"Ambiguous case identifier")
    prefix=directory/"nprobe_24"
    ids=matrix(prefix,".ids.i32","<i4",10);distances=matrix(prefix,".dist.f32","<f4",10)
    work=matrix(prefix,".work.u64","<u8",8)
    require(ids.shape==(count,10) and np.all((ids>=-1)&(ids<1000000)) and
        np.all(np.isfinite(distances[ids>=0])),"Invalid results")
    workload=json.loads((DATA/"query/workloads.json").read_text())
    truth=np.load(workload["truth"][scenario]["ids"],mmap_mode="r")
    recall=sum(len(set(row[row>=0])&set(truth[i,:10])) for i,row in enumerate(ids))/(count*10)
    require(abs(recall-native["recall"])<1e-10,"Recall mismatch")
    if scenario!="unfilter":
        attrs=np.load(workload["attributes"],mmap_mode="r")
        tags=np.load(workload["flat_query_tags"][scenario]).reshape(-1)
        require(all(np.all(attrs[row[row>=0],0]==tags[i]) for i,row in enumerate(ids)),"Predicate violation")
    n=matrix(prefix,".native.u64","<u8",18)
    require(n.shape==(count,18),"Native shape")
    record={"label":directory.name,"scenario":scenario,"mode":mode,"two_hop_threshold":None if t is None else float(t),
        "posting_threshold":None if mode=="postfilter" else .05,
        "posting_active":mode=="posting","native":native,"process_seconds":seconds,
        "payloads":hashes(prefix,PAYLOADS),"ssd_mean":work.mean(axis=0).tolist(),
        "native_mean":dict(zip(NATIVE,n.mean(axis=0).tolist())),"exact_predicate_valid":True,
        "diagnostic_off_on_ids_distances_ssd_parity":True,
        "timing_kind":"ordinary Broad, no preload/profile/capture in timed body" if count==1000 else "bounded32 validation only"}
    if mode=="postfilter":
        require(np.all(n[:,[2,4,5,6,7,8,9,10,11]]==0),"Postfilter upper work")
        require(np.all(n[:,15]==1) and np.all(n[:,16]==0),"Wrong postfilter admission")
        record["trace_hashes"]=hashes(prefix,CONTROL_TRACES)
        oracle=PARENT/"broad_tag_postfilter_graph_r1/nprobe_24"
    else:
        a=matrix(prefix,".navix.u64","<u8",19)
        d=matrix(prefix,".decisions.f64","<f8",11)
        rows=matrix(prefix,".rows.u64","<u8",5)
        q=matrix(prefix,".qualification.u64","<u8",6)
        require(a.shape==(count,19) and q.shape==(count,6),"Work schema")
        require(np.all(np.isfinite(d)) and np.all(d[:,7]>=d[:,6]) and
            np.all(d[:,3]<=d[:,2]) and np.all(d[:,3]>=0),"Invalid decisions")
        require(np.all(rows[:,3]==rows[:,4]),"Truncated CSR row")
        require(np.all(d[d[:,4]!=4,8:]==0),"Graph did upper work")
        require(np.all((d[:,2]>0)|((d[:,3]==0)&(d[:,4]==0))),"Degree zero mismatch")
        positive=d[d[:,2]>0]
        def graph(x):
            return np.where(x[:,3]/x[:,2]>=float(t),1,
                np.where(.4*(x[:,2]*x[:,3]+x[:,3])>2*x[:,2]-x[:,3],2,3))
        expected=graph(positive)
        if mode=="posting":expected=np.where(positive[:,3]/positive[:,2]<.05,4,expected)
        require(np.array_equal(positive[:,4],expected),"Branch formula mismatch")
        fallback=d[d[:,5]>0]
        require(np.all(fallback[:,4]==4) and np.array_equal(fallback[:,5],graph(fallback)),"Fallback formula mismatch")
        for route in range(5):
            actual=np.bincount(d[d[:,4]==route,0].astype(int),minlength=count)
            require(np.array_equal(actual,a[:,route]),"Outer decisions/accounting mismatch")
        require(np.array_equal(np.bincount(fallback[:,0].astype(int),minlength=count),a[:,5]),"Fallback accounting")
        if mode=="navix": require(np.all(n[:,[2,4,5,6,7,8,9,10,11]]==0),"NaviX-only upper work")
        if scenario=="unfilter":
            require(np.all(n[:,[2,4,5,6,7,8,9,10,11,15]]==0) and np.all(n[:,16:]==1) and
                np.all(a==0) and len(d)==len(rows)==0 and np.all(q==0),"Empty predicate new work")
        require(np.all(q[:,1]>=q[:,2]) and np.all(q[:,3]>=q[:,4]) and np.all(q[:,5]==0),"Cache accounting")
        record.update(action_mean=dict(zip(ACTION,a.mean(axis=0).tolist())),
            qualification_head_phase_mean=dict(zip(QUALIFICATION,q.mean(axis=0).tolist())),
            trace_hashes=hashes(prefix,TRACES),
            outer_decision_counts={name:int(np.count_nonzero(d[:,4]==i)) for i,name in enumerate(ROUTES)},
            direct_graph_counts={name:int(np.count_nonzero(d[:,4]==i)) for i,name in enumerate(ROUTES[:4])},
            fallback_graph_counts={name:int(np.count_nonzero(d[:,5]==i)) for i,name in enumerate(ROUTES[1:4],1)},
            decision_rows=len(d),csr_rows=len(rows))
        oracle=None
        if t=="0.5" or scenario=="unfilter":
            oracle=PARENT/(f"broad_tag_new_{mode}_r1" if count==1000 else f"small_{scenario}_{mode}")/"nprobe_24"
        if mode=="posting" and t=="0.05":
            require(record["direct_graph_counts"]["directed"]==record["direct_graph_counts"]["full_two_hop"]==0,
                "Equal cutoffs have a direct two-hop interval")
    if oracle:
        require(record["payloads"]==hashes(oracle,PAYLOADS),"Frozen final/SSD parity "+directory.name)
        require(record["trace_hashes"]==hashes(oracle,CONTROL_TRACES if mode=="postfilter" else TRACES),
            "Frozen exact trajectory parity "+directory.name)
        record["exact_frozen_oracle"]=str(oracle)
    write(directory/"record.json",record)
    print(directory.name,native["mean_latency_ms"],native["recall"],flush=True)
    return record

def invoke(label,scenario,mode,t,small=False):
    directory=OUTPUT/label;directory.mkdir()
    name=f"{scenario}_{case(mode,t)}.ini" if mode!="postfilter" else "broad_tag_postfilter_graph.ini"
    cfg=HERE/("diagnostic_configs" if small else "configs")/name
    binary=TOOL/"harness/navix-bench" if mode!="postfilter" else DATA/"toolchains/cost_arbitration_v2_20260917/harness/cost-bench"
    seconds=process(["numactl","--cpunodebind=2","--membind=2",str(binary),"--config",str(cfg)],
        directory,max_seconds=180)
    record=validate(directory,scenario,mode,t,32 if small else 1000,seconds)
    record["config"]=str(cfg);record["config_sha256"]=sha(cfg)
    write(directory/"record.json",record)
    return record

def main():
    require((OUTPUT/"build_artifacts.json").exists() and (OUTPUT/"small_replay.json").exists(),
        "Native tests and bounded actual sparse validation must finish first")
    freeze()
    points=[("postfilter",None)]+[(mode,t) for mode in ("navix","posting") for t in THRESHOLDS]
    plan=[(f"broad_tag_{case(mode,t) if t else 'postfilter_graph'}_r{r}",mode,t)
        for r,order in ((1,points),(2,points[::-1])) for mode,t in order]
    write(OUTPUT/"measurement_plan.json",{"points":7,"ordinary_processes":14,"plan":plan,
        "protocol":"1000 warmup +1000 measured +1000 untimed capture; native [24], offset0 topk10, CPU+memory NUMA2, one query thread",
        "stop":"Both requested thresholds irrespective of recall; no optimization/tuning/full curve or Extreme campaign"})
    start=time.monotonic();records=[]
    for label,mode,t in plan:
        try: records.append(invoke(label,"broad_tag",mode,t))
        except Exception as error:
            write(OUTPUT/"bounded_failure.json",{"label":label,"error":str(error),"completed":len(records)})
            raise
    summary=[]
    for mode,t in points:
        pair=[r for r in records if r["mode"]==mode and r["two_hop_threshold"]==(float(t) if t else None)]
        require(len(pair)==2 and pair[0]["payloads"]==pair[1]["payloads"] and
            pair[0]["trace_hashes"]==pair[1]["trace_hashes"],"Repetition nondeterminism")
        ms=[r["native"]["mean_latency_ms"] for r in pair]
        qps=[r["native"]["qps"] for r in pair]
        summary.append({"case":case(mode,t) if t else "native_result_only_postfilter_graph",
            "mode":mode,"two_hop_threshold":float(t) if t else None,"posting_threshold":.05 if t else None,
            "ordinary_ms_runs":ms,"ordinary_ms":statistics.mean(ms),"ms_range":[min(ms),max(ms)],
            "qps_runs":qps,"qps_at_mean_ms":1000/statistics.mean(ms),"qps_range":[min(qps),max(qps)],
            "recall_at_10":pair[0]["native"]["recall"],
            **{k:pair[0][k] for k in ("native_mean","action_mean","qualification_head_phase_mean","ssd_mean",
                "outer_decision_counts","direct_graph_counts","fallback_graph_counts","exact_frozen_oracle") if k in pair[0]},
            "avg_second_hop_rows":pair[0].get("action_mean",{}).get("second_rows",0),
            "avg_posting_fallbacks":pair[0].get("action_mean",{}).get("fallbacks",0),
            "two_repetition_exact_determinism":True})
    write(OUTPUT/"summary.json",summary)
    write(OUTPUT/"operations.json",{"records":records,"operation_seconds":time.monotonic()-start,"ordinary_index_loads":14})
    protect()

if __name__=="__main__":main()
