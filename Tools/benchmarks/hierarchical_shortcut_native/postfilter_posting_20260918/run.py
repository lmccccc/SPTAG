"""One fixed ratio, six Broad processes and at most four predetermined sparse processes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import time
import numpy as np
from prepare import HERE,DATA,TOOL,OUTPUT,PARENT,FILES,case,sha,write,protect
from process import process

NATIVE=["head_distances","routing_distances","child_distances","checked","calls","returns",
        "representative_distances","csr_members","signature_checks","signature_rejects","h2_rows","h3_rows",
        "queue_offers","queue_accepted","queue_rejected","filtered","default","native_distance"]
ACTION=["sparse_activations","selected_actions","second_hop_loops","classifier_prescan_entries",
        "graph_members","graph_fresh","graph_predicate_checks","graph_valid","graph_useful",
        "posting_members","posting_fresh_qualified_adds","posting_predicate_checks","posting_valid","posting_useful"]
QUALIFICATION=["eligible_requests","posting_requests","posting_evaluations","exact_requests","exact_evaluations","storage_growths"]
OPERATORS=["outer_heads","ordinary_row_slots","ordinary_qualification_requests"]
PAYLOADS=(".ids.i32",".dist.f32",".work.u64")
CONTROL_TRACES=(".native.u64",".head.i32",".head.f32",".own.i32")
TRACES=CONTROL_TRACES+(".posting.u64",".qualification.u64",".operators.u64",".decisions.f64",".rows.u64")

def require(value,message):
    if not value: raise RuntimeError(message)
def hashes(prefix,suffixes): return {s:sha(Path(str(prefix)+s)) for s in suffixes}
def matrix(prefix,suffix,dtype,width): return np.fromfile(str(prefix)+suffix,dtype=dtype).reshape(-1,width)

def freeze():
    require(not [k for k in os.environ if k.startswith(("SPTAG_","SPANN_","SHORTCUT_","NAVIX_")) or
        k in ("LD_PRELOAD","OMP_NUM_THREADS","OMP_PROC_BIND","OMP_PLACES")],"Environment overrides")
    protect()
    text=(HERE/"MatchedBench.cpp").read_text()
    body=text.split("const auto start = std::chrono::steady_clock::now();",1)[1].split(
        "const auto finish = std::chrono::steady_clock::now();",1)[0]
    digest=hashlib.sha256(body.encode()).hexdigest()
    require(digest=="9f412c63b71f7310e09180dff0858ccef3957bd20d7a96c34fdb440e3d559d53","Timed body changed")
    for name,path in FILES.items(): require(sha(HERE/name)==sha(TOOL/"source"/path),"Installed mismatch "+name)
    shutil.copytree(HERE/"configs",OUTPUT/"configs")
    shutil.copytree(HERE/"diagnostic_configs",OUTPUT/"diagnostic_configs")
    binary=TOOL/"harness/postfilter-bench"
    write(OUTPUT/"runtime.json",{
        "timed_body_sha256":digest,"binary":{"path":str(binary),"sha256":sha(binary)},
        "libraries":{str(p):sha(p) for p in (TOOL/"source/Release").glob("*.a")},
        "sources":{name:sha(HERE/name) for name in FILES},
        "harness_source_sha256":sha(HERE/"MatchedBench.cpp"),
        "configs":{str(p):sha(p) for root in ("configs","diagnostic_configs") for p in (OUTPUT/root).glob("*.ini")}})

def rejection_tests():
    directory=OUTPUT/"negative_configs";directory.mkdir()
    baseline=(HERE/"configs"/f"broad_tag_{case('posting')}.ini").read_text()
    changes={key:baseline.replace("[SearchSweep]",key+"=0\n\n[SearchSweep]") for key in (
        "NavixMode","NavixTwoHopThreshold","NavixPostingThreshold","TwoHopThreshold",
        "ArbitrationMode","ShortcutRetainedRatio","MinBaseDegree","PostFilterPostingUnknown")}
    for value in ("nan","inf","-0.01","0.05","1","0.01junk"):
        changes["ratio_"+value]=baseline.replace("PostingActivationRatio=0.01","PostingActivationRatio="+value)
    changes["bad_mode"]=baseline.replace("PostFilterPostingMode=posting","PostFilterPostingMode=navix")
    changes["missing_ratio"]=baseline.replace("PostingActivationRatio=0.01","")
    results=[]
    for name,text in changes.items():
        cfg=directory/(name+".ini");cfg.write_text(text)
        command=[str(TOOL/"harness/postfilter-bench"),"--config",str(cfg)]
        p=subprocess.run(command,capture_output=True,text=True)
        require(p.returncode!=0 and "MATCHED_LOAD" not in p.stdout,"Retired/invalid control accepted")
        results.append({"name":name,"config":str(cfg),"command":command,"returncode":p.returncode,
            "stderr":p.stderr,"no_index_load":True})
    write(OUTPUT/"native_rejection_tests.json",results)

def validate(directory,scenario,mode,count,seconds):
    text=(directory/"stdout.log").read_text()
    require(text.count("MATCHED_LOAD calls=1")==1,"Wrong physical load count")
    for component in ("Vector (160091,128)","BKT (1,160093)","RNG (160091,32)",
                      "Vector (4098,128)","BKT (1,4100)","RNG (4098,32)"):
        require(text.count("Load "+component+" Finish!")==1,"Native array load mismatch")
    require(("NATIVE_SUPPLIER_OWNER_LOAD" in text)==(mode=="posting"),"Owner setup mode mismatch")
    results=[json.loads(s) for s in text.splitlines() if s.startswith('{"engine":')]
    require(len(results)==1,"Expected one native fixed point")
    native=results[0]
    require(native["nprobe"]==24 and native["queries"]==count and native["index_load_count"]==
        native["probe_count"]==native["workspace_resets"]==1 and
        native["failed_queries"]==native["measure_offset"]==0,"Protocol mismatch")
    require(native["case"]==case(mode),"Ambiguous case identifier")
    prefix=directory/"nprobe_24"
    ids=matrix(prefix,".ids.i32","<i4",10);dist=matrix(prefix,".dist.f32","<f4",10)
    work=matrix(prefix,".work.u64","<u8",8)
    require(ids.shape==(count,10) and np.all((ids>=-1)&(ids<1000000)) and
        np.all(np.isfinite(dist[ids>=0])),"Invalid results")
    workload=json.loads((DATA/"query/workloads.json").read_text())
    truth=np.load(workload["truth"][scenario]["ids"],mmap_mode="r")
    recall=sum(len(set(row[row>=0])&set(truth[i,:10])) for i,row in enumerate(ids))/(count*10)
    require(abs(recall-native["recall"])<1e-10,"Recall mismatch")
    if scenario!="unfilter":
        attrs=np.load(workload["attributes"],mmap_mode="r")
        tags=np.load(workload["flat_query_tags"][scenario]).reshape(-1)
        require(all(np.all(attrs[row[row>=0],0]==tags[i]) for i,row in enumerate(ids)),"Predicate violation")
    n=matrix(prefix,".native.u64","<u8",18)
    a=matrix(prefix,".posting.u64","<u8",14)
    q=matrix(prefix,".qualification.u64","<u8",6)
    o=matrix(prefix,".operators.u64","<u8",3)
    d=matrix(prefix,".decisions.f64","<f8",11)
    rows=matrix(prefix,".rows.u64","<u8",5)
    require(n.shape==(count,18) and a.shape==(count,14) and q.shape==(count,6) and o.shape==(count,3),"Counter schema")
    require(np.all(a[:,2:4]==0),"Forbidden two-hop or classifier prescan")
    require(np.all(o[:,2]<=o[:,1]),"More than one ordinary qualification per encounter")
    require(np.all(rows[:,4]<=rows[:,3]),"Overrun CSR row")
    require(np.all(q[:,1]>=q[:,2]) and np.all(q[:,3]>=q[:,4]) and np.all(q[:,5]==0),"Qualification cache accounting")
    if scenario=="unfilter":
        require(np.all(n==0) and np.all(a==0) and np.all(q==0) and np.all(o==0) and
            len(d)==len(rows)==0,"Unfilter query initialized supplier/stats/cache/hooks")
    elif mode=="graph":
        require(np.all(o[:,2]==0) and np.all(q==0) and len(d)==len(rows)==0,"Graph qualification/hierarchy activity")
    else:
        require(np.array_equal(o[:,2],o[:,1]),"Fused ordinary encounter count")
        require(np.all(d[:,3]<=d[:,2]) and np.all(d[:,3]>=0) and np.all(d[:,7]>=d[:,6]),"Decision schema")
        require(int(d[:,2].sum())==int(o[:,1].sum()),"Prescan or missed ordinary slots")
        activated=d[d[:,5]==1]
        require(np.all(activated[:,4]==1) and np.all(activated[:,2]>0) and
            np.all(activated[:,3]/activated[:,2]<.01),"Posting outside completed sparse gate")
        require(int(d[:,5].sum())==int(a[:,0].sum()),"Sparse activation count")
        require(np.all(d[d[:,5]==0,8:]==0),"Upper work before sparse gate")
    if mode in ("graph","observe"):
        require(np.all(n[:,[2,4,5,6,7,8,9,10,11]]==0) and
            np.all(a[:,:4]==0) and np.all(a[:,9:]==0),"Graph/observe hierarchy activity")
    record={
        "label":directory.name,"scenario":scenario,"mode":mode,"activation_ratio":.01,
        "native":native,"process_seconds":seconds,"payloads":hashes(prefix,PAYLOADS),
        "trace_hashes":hashes(prefix,TRACES),"native_mean":dict(zip(NATIVE,n.mean(axis=0).tolist())),
        "operator_mean":dict(zip(OPERATORS,o.mean(axis=0).tolist())),
        "action_mean":dict(zip(ACTION,a.mean(axis=0).tolist())),
        "qualification_head_phase_mean":dict(zip(QUALIFICATION,q.mean(axis=0).tolist())),
        "ssd_mean":work.mean(axis=0).tolist(),"exact_final_predicate_valid":True,
        "diagnostic_off_on_ids_distances_ssd_parity":True,
        "ordinary_partial_rows":int(np.count_nonzero(d[:,4]==0)) if mode!="graph" and scenario!="unfilter" else None,
        "csr_partial_rows":int(np.count_nonzero(rows[:,4]<rows[:,3])),
        "timing_kind":"ordinary1000" if count==1000 else "bounded32 functional/runtime preflight"}
    if scenario=="broad_tag" and mode=="graph":
        oracle=PARENT/"broad_tag_postfilter_graph_twohop_0_posting_0_r1/nprobe_24"
        require(record["payloads"]==hashes(oracle,PAYLOADS),"Frozen original native output/SSD mismatch")
        require(hashes(prefix,CONTROL_TRACES)==hashes(oracle,CONTROL_TRACES),"Frozen original native work/heads/own mismatch")
        record["exact_frozen_oracle"]=str(oracle)
    write(directory/"record.json",record)
    print(directory.name,native["mean_latency_ms"],native["recall"],flush=True)
    return record

def invoke(label,scenario,mode,small=False):
    directory=OUTPUT/label;directory.mkdir()
    cfg=HERE/("diagnostic_configs" if small else "configs")/f"{scenario}_{case(mode)}.ini"
    seconds=process(["numactl","--cpunodebind=2","--membind=2",str(TOOL/"harness/postfilter-bench"),
        "--config",str(cfg)],directory,max_seconds=180)
    record=validate(directory,scenario,mode,32 if small else 1000,seconds)
    record.update(config=str(cfg),config_sha256=sha(cfg))
    write(directory/"record.json",record)
    return record

def graph_observe(graph,observe):
    require(graph["payloads"]==observe["payloads"],"Graph/observe final IDs/distances/SSD mismatch")
    require(all(graph["trace_hashes"][s]==observe["trace_hashes"][s] for s in CONTROL_TRACES),
        "Graph/observe exact native heads/own/checks/queues mismatch")
    g=OUTPUT/graph["label"]/"nprobe_24";o=OUTPUT/observe["label"]/"nprobe_24"
    require(np.array_equal(matrix(g,".operators.u64","<u8",3)[:,:2],matrix(o,".operators.u64","<u8",3)[:,:2]),
        "Graph/observe outer/ordinary trace mismatch")
    require(np.array_equal(matrix(g,".posting.u64","<u8",14)[:,4:9],matrix(o,".posting.u64","<u8",14)[:,4:9]),
        "Graph/observe graph-work mismatch")

def main():
    require((OUTPUT/"build_artifacts.json").exists(),"Native fixtures must pass first")
    freeze()
    rejection_tests()
    write(OUTPUT/"measurement_plan.json",{
        "broad_points":["graph","observe","posting"],"ratio":.01,"broad_ordinary_processes":6,
        "sparse_preflight_modes":["graph","observe","posting"],"unfilter_preflight_modes":["graph","observe","posting"],
        "sparse_full_runtime_rule":"maximum sparse32 native mean latency < 10ms; no recall condition",
        "sparse_full_if_allowed":{"points":["graph","posting"],"ordinary_processes":4},
        "ordinary":"1000 warmup +1000 measured +1000 untimed capture; native [24], offset0 topk10; CPU/memory NUMA2; thread1",
        "repetitions":"two, reverse order","stop":"No additional thresholds, variants, sweeps, promotion"})
    small=[]
    for scenario in ("extreme_tag","unfilter"):
        group=[invoke(f"small_{scenario}_{case(mode)}",scenario,mode,True) for mode in ("graph","observe","posting")]
        graph_observe(group[0],group[1]);small.extend(group)
    sparse=small[:3]
    require(sparse[2]["action_mean"]["sparse_activations"]>0 and sparse[2]["native_mean"]["h2_rows"]>0,
        "Sparse preflight did not activate real posting")
    allow=max(r["native"]["mean_latency_ms"] for r in sparse)<10
    write(OUTPUT/"small_replay.json",{"records":small,"graph_observe_exact_parity":True,
        "full_sparse_allowed":allow,"decision_input_only_runtime_ms":[r["native"]["mean_latency_ms"] for r in sparse],
        "rule_max_native_ms":10,"no_ground_truth_tuning":True})
    records=[];plan=[]
    for scenario,modes in (("broad_tag",["graph","observe","posting"]),
                           *([("extreme_tag",["graph","posting"])] if allow else [])):
        for rep,order in ((1,modes),(2,modes[::-1])):
            plan.extend((f"{scenario}_{case(mode)}_r{rep}",scenario,mode) for mode in order)
    write(OUTPUT/"ordinary_plan.json",plan)
    for label,scenario,mode in plan:
        records.append(invoke(label,scenario,mode))
        write(OUTPUT/"ordinary_progress.json",records)
    for rep in (1,2):
        group={r["mode"]:r for r in records if r["scenario"]=="broad_tag" and r["label"].endswith(f"_r{rep}")}
        graph_observe(group["graph"],group["observe"])
    summary=[]
    for scenario,mode in dict.fromkeys((r["scenario"],r["mode"]) for r in records):
        pair=[r for r in records if r["scenario"]==scenario and r["mode"]==mode]
        require(len(pair)==2 and pair[0]["payloads"]==pair[1]["payloads"] and
            pair[0]["trace_hashes"]==pair[1]["trace_hashes"],"Repetition nondeterminism")
        ms=[r["native"]["mean_latency_ms"] for r in pair];qps=[r["native"]["qps"] for r in pair]
        recall=[r["native"]["recall"] for r in pair]
        summary.append({
            "scenario":scenario,"mode":mode,"activation_ratio":.01,
            "ordinary_ms":statistics.mean(ms),"ordinary_ms_runs":ms,"ms_range":[min(ms),max(ms)],
            "qps_at_mean_ms":1000/statistics.mean(ms),"qps_runs":qps,"qps_range":[min(qps),max(qps)],
            "recall_at_10":recall[0],"recall_runs":recall,"recall_range":[min(recall),max(recall)],
            **{k:pair[0][k] for k in ("native_mean","operator_mean","action_mean","qualification_head_phase_mean",
                                     "ssd_mean","ordinary_partial_rows","csr_partial_rows")},
            "native_files":[str(OUTPUT/r["label"]/"nprobe_24") for r in pair],
            "exact_repetition_determinism":True})
    write(OUTPUT/"summary.json",summary)
    write(OUTPUT/"operations.json",{"records":records,"ordinary_index_loads":len(records),
        "graph_observe_exact_native_parity":True,"stop":"No more measurement permitted"})
    protect()

if __name__=="__main__":main()
