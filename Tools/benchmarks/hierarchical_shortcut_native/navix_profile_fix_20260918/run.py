"""Fresh ordinary Broad pairs only; sparse/empty parity is a separate 32-query replay."""
import hashlib
import json
import os
from pathlib import Path
import statistics
import time
import numpy as np
from prepare import HERE,DATA,BASE,PARENT,MATCHED,TOOL,OUTPUT,FILES,sha,write,protect
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
TRACES=(".native.u64",".navix.u64",".decisions.f64",".rows.u64",".head.i32",".head.f32",".own.i32")

def require(value,message):
    if not value: raise RuntimeError(message)

def freeze():
    require(not [k for k in os.environ if k.startswith(("SPTAG_","SPANN_","SHORTCUT_")) or
                 k in ("LD_PRELOAD","OMP_NUM_THREADS","OMP_PROC_BIND","OMP_PLACES")],"Environment overrides")
    protect()
    text=(HERE/"MatchedBench.cpp").read_text()
    body=text.split("const auto start = std::chrono::steady_clock::now();",1)[1].split(
        "const auto finish = std::chrono::steady_clock::now();",1)[0]
    digest=hashlib.sha256(body.encode()).hexdigest()
    require(digest=="9f412c63b71f7310e09180dff0858ccef3957bd20d7a96c34fdb440e3d559d53","Timed body changed")
    for name,path in FILES.items():
        require(sha(HERE/name)==sha(TOOL/"source"/path),"Installed source differs: "+name)
    binaries={"new":TOOL/"harness/navix-bench","old":BASE/"harness/navix-bench",
              "postfilter":DATA/"toolchains/cost_arbitration_v2_20260917/harness/cost-bench"}
    configs=list((HERE/"configs").glob("*.ini"))
    configs.append(HERE.with_name("cost_arbitration_v2_20260917")/"configs/broad_tag_graph.ini")
    value={"timed_body_sha256":digest,"binaries":{k:{"path":str(v),"sha256":sha(v)} for k,v in binaries.items()},
        "libraries":{str(p):sha(p) for p in sorted((TOOL/"source/Release").glob("*.a"))},
        "sources":{name:sha(HERE/name) for name in FILES},
        "harness_source_sha256":sha(HERE/"MatchedBench.cpp"),
        "configs":{str(p):sha(p) for p in configs},
        "graph_provenance":"Frozen cost-v2 graph mode: H1 result-only postfilter plus native own points; no active cost model or afterGraph in ordinary timing. Not the unfiltered-head original control."}
    write(OUTPUT/"runtime.json",value)
    return binaries

def hashes(prefix,suffixes):
    return {s:sha(Path(str(prefix)+s)) for s in suffixes}

def invoke(label,scenario,case,version,binaries,diagnostic_directory=None):
    directory=diagnostic_directory or OUTPUT/label
    cfg=HERE/f"configs/{scenario}_{case}.ini"
    if version=="postfilter":
        cfg=HERE.with_name("cost_arbitration_v2_20260917")/f"configs/{scenario}_graph.ini"
    wall=None
    if diagnostic_directory is None:
        directory.mkdir()
        wall=process(["numactl","--cpunodebind=2","--membind=2",str(binaries[version]),"--config",str(cfg)],
                     directory,max_seconds=180)
    text=(directory/"stdout.log").read_text()
    require(text.count("MATCHED_LOAD calls=1")==1,"Wrong load count")
    for component in ("Vector (160091,128)","BKT (1,160093)","RNG (160091,32)",
                      "Vector (4098,128)","BKT (1,4100)","RNG (4098,32)"):
        require(text.count("Load "+component+" Finish!")==1,"Array load mismatch")
    results=[json.loads(s) for s in text.splitlines() if s.startswith('{"engine":')]
    require(len(results)==1,"One fixed point required")
    native=results[0]
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
        require(all(np.all(attrs[row[row>=0],0]==tags[i]) for i,row in enumerate(ids)),"Predicate violation")
    record={"label":label,"scenario":scenario,"case":case,"version":version,"native":native,
            "process_seconds":wall,"payloads":hashes(prefix,PAYLOADS),
            "ssd_mean":work.mean(axis=0).tolist(),"exact_predicate_valid":True}
    if version!="postfilter":
        n=np.fromfile(str(prefix)+".native.u64",dtype="<u8").reshape(1000,18)
        a=np.fromfile(str(prefix)+".navix.u64",dtype="<u8").reshape(1000,19)
        d=np.fromfile(str(prefix)+".decisions.f64",dtype="<f8").reshape(-1,11)
        rows=np.fromfile(str(prefix)+".rows.u64",dtype="<u8").reshape(-1,5)
        require(np.all(np.isfinite(d)) and np.all(d[:,7]>=d[:,6]),"Invalid decisions")
        require(np.all(rows[:,3]==rows[:,4]),"Truncated CSR row")
        require(np.all(d[d[:,4]!=4,8:]==0),"Graph did upper work")
        require(np.all((d[:,2]>0)|((d[:,3]==0)&(d[:,4]==0))),"Degree zero mismatch")
        positive=d[d[:,2]>0]
        expected=np.where(positive[:,3]/positive[:,2]>=.5,1,
                 np.where(.4*(positive[:,2]*positive[:,3]+positive[:,3])>2*positive[:,2]-positive[:,3],2,3))
        if case=="posting":expected=np.where(positive[:,3]/positive[:,2]<.05,4,expected)
        require(np.array_equal(positive[:,4],expected),"Branch formula mismatch")
        fallback=d[d[:,5]>0]
        require(np.all(fallback[:,4]==4) and np.all(np.isin(fallback[:,5],[2,3])),"Fallback mismatch")
        if case=="navix":
            require(np.all(n[:,[2,4,5,6,7,8,9,10,11]]==0),"NaviX-only upper work")
        if scenario=="unfilter":
            require(np.all(n[:,[2,4,5,6,7,8,9,10,11,15]]==0) and np.all(n[:,16:]==1) and
                    np.all(a==0) and len(d)==len(rows)==0,"Empty predicate did upper/policy work")
        record.update(native_mean=dict(zip(NATIVE,n.mean(axis=0).tolist())),
                      action_mean=dict(zip(ACTION,a.mean(axis=0).tolist())),
                      trace_hashes=hashes(prefix,TRACES))
        oracle=PARENT/f"{scenario}_{case}_r1/nprobe_24"
        require(record["payloads"]==hashes(oracle,PAYLOADS),"Frozen final/SSD parity failed: "+label)
        require(record["trace_hashes"]==hashes(oracle,TRACES),"Frozen trajectory parity failed: "+label)
        record["frozen_oracle"]=str(oracle)
        if scenario=="unfilter":
            require(record["payloads"]==hashes(PARENT/"unfilter_original_r1/nprobe_24",PAYLOADS),
                    "Native original empty-predicate parity mismatch")
        if version=="new":
            hot=np.fromfile(str(prefix)+".qualification.u64",dtype="<u8").reshape(1000,6)
            record["qualification_head_phase_mean"]=dict(zip(QUALIFICATION,hot.mean(axis=0).tolist()))
            require(np.all(hot[:,1]>=hot[:,2]) and np.all(hot[:,3]>=hot[:,4]),"Qualification accounting mismatch")
            if scenario!="unfilter":
                require(np.all(hot[:,5]==0),"Qualification storage growth after warmup")
            else:require(np.all(hot==0),"Empty default uses hotpath cache")
    else:
        record["control_semantics"]="Fresh proper native H1 RESULT-ONLY postfilter, not original unfiltered-head control"
        n=np.fromfile(str(prefix)+".native.u64",dtype="<u8").reshape(1000,18)
        require(np.all(n[:,[2,4,5,6,7,8,9,10,11]]==0),"Postfilter control did upper work")
        require(np.all(n[:,15]==1) and np.all(n[:,16]==0),"Postfilter used default/unfiltered admission")
        oracle=PARENT/f"postfilter_control_completion/{scenario}_graph_r1/nprobe_24"
        require(record["payloads"]==hashes(oracle,PAYLOADS),"Corrected graph oracle parity mismatch")
        controlTraces=(".native.u64",".head.i32",".head.f32",".own.i32")
        require(hashes(prefix,controlTraces)==hashes(oracle,controlTraces),"Corrected graph trace mismatch")
        record["native_mean"]=dict(zip(NATIVE,n.mean(axis=0).tolist()))
        record["frozen_oracle"]=str(oracle)
    record["timing_kind"]="diagnostic PC sampling; NOT ordinary" if diagnostic_directory else "ordinary; no preload, capture/profile off"
    write(directory/("native_validation.json" if diagnostic_directory else "record.json"),record)
    print(label,native["mean_latency_ms"],native["recall"],flush=True)
    return record

def main():
    require(set(json.loads((OUTPUT/"profile_lines_summary.json").read_text()))=={"old","graph","failed"},
            "All three frozen PC attribution builds must finish before ordinary timing")
    require((OUTPUT/"build_artifacts.json").exists() and (OUTPUT/"small_replay.json").exists(),
            "Build, native fixtures and bounded parity must pass before timing")
    binaries=freeze()
    start=time.monotonic(); records=[]
    # Both rounds interleave implementations; the second reverses the first.
    broad=[("graph","postfilter"),("navix","old"),("navix","new"),("posting","old"),("posting","new")]
    plan=[(f"broad_tag_{v}_{c}_r{r}","broad_tag",c,v) for r,order in
          ((1,broad),(2,broad[::-1])) for c,v in order]
    write(OUTPUT/"measurement_plan.json",{"plan":plan,
        "extreme":"NO 1000-query campaign; separate bounded32 parity only",
        "protocol":"1000 warmup + 1000 timed + 1000 untimed diagnostics; n24 offset0 topk10 NUMA CPU+memory2 querythread1; native INI authority; no LD_PRELOAD",
        "shared_source":"combined affected by qualification; paired combined included"})
    for label,scenario,case,version in plan:
        try:records.append(invoke(label,scenario,case,version,binaries))
        except Exception as error:
            write(OUTPUT/"bounded_failure.json",{"label":label,"error":str(error),"completed":len(records)})
            raise
    summary=[]
    for scenario,case,version in dict.fromkeys((r["scenario"],r["case"],r["version"]) for r in records):
        pair=[r for r in records if (r["scenario"],r["case"],r["version"])==(scenario,case,version)]
        require(len(pair)==2 and pair[0]["payloads"]==pair[1]["payloads"],"Repetition parity mismatch")
        if version!="postfilter":
            require(pair[0]["trace_hashes"]==pair[1]["trace_hashes"],"Repetition trace mismatch")
        ms=[r["native"]["mean_latency_ms"] for r in pair]
        summary.append({"scenario":scenario,"case":case,"version":version,"ordinary_ms_runs":ms,
            "ordinary_ms":statistics.mean(ms),"recall_at_10":pair[0]["native"]["recall"],
            **{k:pair[0][k] for k in ("native_mean","action_mean","qualification_head_phase_mean","ssd_mean") if k in pair[0]}})
    write(OUTPUT/"summary.json",summary)
    write(OUTPUT/"operations.json",{"records":records,"operation_seconds":time.monotonic()-start,
                                   "index_loads":len(records),"protection":protect()})

if __name__=="__main__":main()
