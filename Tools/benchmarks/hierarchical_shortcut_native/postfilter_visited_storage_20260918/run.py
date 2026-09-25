"""One fixed ratio: exactly six Broad ordinary processes; sparse/unfilter32 only."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import time
import numpy as np
from prepare import HERE,DATA,TOOL,OUTPUT,PARENT,ORACLE,FILES,case,sha,write,protect
from process import process

NATIVE=["head_distances","routing_distances","child_distances","checked","calls","returns",
        "representative_distances","csr_members","signature_checks","signature_rejects","h2_rows","h3_rows",
        "queue_offers","queue_accepted","queue_rejected","filtered","default","native_distance"]
ACTION=["sparse_activations","selected_actions","second_hop_loops","classifier_prescan_entries",
        "graph_members","graph_fresh","graph_predicate_checks","graph_valid","graph_useful",
        "posting_members","posting_fresh_qualified_adds","posting_predicate_checks","posting_valid","posting_useful"]
OPERATORS=["outer_heads","ordinary_row_slots","existing_predicate_calls","existing_predicate_passes",
           "existing_predicate_fails","checks_zero_rows","auxiliary_predicate_calls","all_native_result_predicate_calls"]
MATCH=["visited_probes","first_match_evaluations","reused_bit_reads","all_match_evaluations",
       "rejected_uninserted_aux_evaluations","physical_degree","effective_degree",
       "eligible_already_visited","oracle_evaluations","match_component_calls"]
STORAGE=["allocations","conversions","allocated_bytes_during_query","nonempty_clears","mode_changes",
         "slot_bytes","persistent_bytes","capacity","occupied","result_persistent_bytes","result_slot_bytes"]
PAYLOADS=(".ids.i32",".dist.f32",".work.u64")
CONTROL_TRACES=(".native.u64",".head.i32",".head.f32",".own.i32")
TRAJECTORY=CONTROL_TRACES+(".visited.u64",".evaluated.f64",".posting.u64",".operators.u64")
TRACES=TRAJECTORY+(".decisions.f64",".rows.u64",".match.u64",".storage.u64")

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
    for folder in ("configs","diagnostic_configs"):
        if (OUTPUT/folder).exists():
            for p in (HERE/folder).glob("*.ini"):
                require(sha(p)==sha(OUTPUT/folder/p.name),"Frozen configuration changed")
        else: shutil.copytree(HERE/folder,OUTPUT/folder)
    binary=TOOL/"harness/postfilter-bench"
    ldd=subprocess.check_output(["ldd",str(binary)],text=True)
    dynamic={}
    for line in ldd.splitlines():
        for token in line.split():
            if token.startswith("/") and Path(token).is_file(): dynamic[token]=sha(token)
    runtime={
        "timed_body_sha256":digest,"binary":{"path":str(binary),"sha256":sha(binary)},
        "libraries":{str(p):sha(p) for p in (TOOL/"source/Release").glob("*.a")},
        "dynamic_libraries":dynamic,
        "sources":{name:sha(HERE/name) for name in FILES},
        "harness_source_sha256":sha(HERE/"MatchedBench.cpp"),
        "configs":{str(p):sha(p) for root in ("configs","diagnostic_configs") for p in (OUTPUT/root).glob("*.ini")}}
    if (OUTPUT/"runtime.json").exists():
        require(runtime==json.loads((OUTPUT/"runtime.json").read_text()),"Cannot splice changed runtime into measurements")
    else: write(OUTPUT/"runtime.json",runtime)

def rejection_tests():
    if (OUTPUT/"native_rejection_tests.json").exists():
        require(all(r["returncode"]!=0 for r in json.loads((OUTPUT/"native_rejection_tests.json").read_text())),
                "Existing rejection failure")
        return
    directory=OUTPUT/"negative_configs";directory.mkdir()
    baseline=(HERE/"configs"/f"broad_tag_{case('posting')}.ini").read_text()
    changes={key:baseline.replace("[SearchSweep]",key+"=0\n\n[SearchSweep]") for key in (
        "NavixMode","NavixTwoHopThreshold","NavixPostingThreshold","TwoHopThreshold",
        "ArbitrationMode","ShortcutRetainedRatio","MinBaseDegree","NativePostfilterUnknown",
        "Observe","PostFilterPostingMode","PostingActivationRatio","PostingFilterHitRatio","NativePostfilterMode","CostMode")}
    for value in ("0","nan","inf","-0.01","0.05","1","0.01junk"):
        changes["ratio_"+value]=baseline.replace("PostingNeighborMatchRatio=0.01","PostingNeighborMatchRatio="+value)
    for value in ("navix","observe","postingjunk"):
        changes["bad_mode_"+value]=baseline.replace("VisitedMatchMode=posting","VisitedMatchMode="+value)
    changes["missing_ratio"]=baseline.replace("PostingNeighborMatchRatio=0.01","")
    results=[]
    for name,text in changes.items():
        cfg=directory/(name+".ini");cfg.write_text(text)
        command=[str(TOOL/"harness/postfilter-bench"),"--config",str(cfg)]
        p=subprocess.run(command,capture_output=True,text=True)
        require(p.returncode!=0 and "MATCHED_LOAD" not in p.stdout,"Retired/invalid control accepted")
        results.append({"name":name,"config":str(cfg),"command":command,"returncode":p.returncode,
            "stderr":p.stderr,"no_index_load":True})
    for key in ("SPTAG_POSTING_FILTER_HIT_RATIO","SPANN_NATIVE_POSTFILTER_MODE","NAVIX_MODE",
                "SHORTCUT_MODE","LD_PRELOAD","OMP_NUM_THREADS"):
        cfg=HERE/"configs"/f"broad_tag_{case('posting')}.ini"
        command=[str(TOOL/"harness/postfilter-bench"),"--config",str(cfg)]
        env=dict(os.environ);env[key]="" if key=="LD_PRELOAD" else "1"
        p=subprocess.run(command,env=env,capture_output=True,text=True)
        require(p.returncode!=0 and "MATCHED_LOAD" not in p.stdout,"Environment override accepted")
        results.append({"name":"environment_"+key,"command":command,"returncode":p.returncode,
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
    o=matrix(prefix,".operators.u64","<u8",8)
    d=matrix(prefix,".decisions.f64","<f8",16)
    m=matrix(prefix,".match.u64","<u8",10)
    visits=matrix(prefix,".visited.u64","<u8",3)
    rows=matrix(prefix,".rows.u64","<u8",5)
    storage=matrix(prefix,".storage.u64","<u8",11)
    require(storage.shape==(count,11) and np.all(storage[:,:3]==0),"Visited backend allocated/converted")
    require(np.all(storage[:,5]==4) and np.all(storage[:,10]==4),"Native or unrelated table widened")
    require(np.all(storage[:,6]==4*storage[:,7]),"Persistent allocation mismatch")
    require(np.all(storage[:,8]<=storage[:,7]),"Invalid visited load")
    require(n.shape==(count,18) and a.shape==(count,14) and o.shape==(count,8),"Counter schema")
    require(np.all(a[:,2:4]==0),"Forbidden two-hop or classifier prescan")
    require(np.all(o[:,2]==o[:,3]+o[:,4]),"Predicate boolean accounting")
    require(np.all(o[:,7]>=o[:,2]+o[:,6]),"Native result predicate totals exclude ordinary/auxiliary calls")
    require(np.all(rows[:,4]==rows[:,3]),"Selected CSR row was truncated or overrun")
    if scenario=="unfilter":
        require(np.all(n==0) and np.all(a==0) and np.all(o==0) and np.all(m==0) and
            len(d)==len(rows)==0,"Unfilter query initialized supplier/stats/hooks")
    else:
        require(np.all(d[:,3]<=d[:,2]) and np.all(d[:,3]>=0) and np.all(d[:,7]>=d[:,6]),"Decision schema")
        require(int(d[:,2].sum())==int(o[:,2].sum()) and int(d[:,3].sum())==int(o[:,3].sum()),
            "Existing predicate scope counters")
        require(int(np.count_nonzero(d[:,2]==0))==int(o[:,5].sum()),"Unknown rows counter")
        activated=d[d[:,5]==1]
        require(np.all(activated[:,4]==1) and np.all(activated[:,11]>0) and
            np.all(activated[:,12]/activated[:,11]<.01),"Posting outside completed full-neighbor sparse gate")
        require(int(d[:,5].sum())==int(a[:,0].sum()),"Sparse activation count")
        require(np.all(d[d[:,5]==0,8:11]==0),"Upper work before sparse gate")
        if mode!="graph":
            complete=d[d[:,4]==1]
            require(np.array_equal(complete[:,11:13],complete[:,14:16]),"Independent full-row oracle mismatch")
            require(np.all(m[:,0]==m[:,1]+m[:,2]+m[:,4]) and np.all(m[:,3]==m[:,1]+m[:,4]),
                    "Single-probe first/reuse/rejected accounting")
            require(np.all(m[:,1]>0) and np.all(m[:,2]>0),"No initialized/reused visited bits")
            require(int(d[:,11].sum())==int(m[:,5].sum()) and int(d[:,12].sum())==int(m[:,6].sum()) and
                    int(d[:,13].sum())==int(m[:,7].sum()),"Physical d/e accounting")
            first=visits[visits[:,2]==0]
            require(len(np.unique(first[:,:2],axis=0))==len(first)==int(m[:,1].sum()),
                "A physical ID was initialized more than once or seed initialization was missed")
            require(int(np.count_nonzero(visits[:,2]))==int(m[:,2].sum()),"Reused-bit read trace mismatch")
            require(int(m[:,5].sum())==int(o[:,1].sum()),"Ordinary physical rows not fully accounted")
        else:
            require(np.all(m[:,[1,2,3,4,6,7,8,9]]==0),"Graph performed match work")
            require(int(m[:,0].sum())==len(visits),"Graph native probe accounting")
            require(np.array_equal(m[:,5],o[:,1]),"Graph physical degree accounting")
    if mode in ("graph","match"):
        require(np.all(n[:,[2,4,5,6,7,8,9,10,11]]==0) and
            np.all(a[:,:4]==0) and np.all(a[:,9:]==0) and np.all(o[:,6]==0),"Graph hierarchy activity")
    record={
        "label":directory.name,"scenario":scenario,"mode":mode,"activation_ratio":.01,
        "native":native,"process_seconds":seconds,"payloads":hashes(prefix,PAYLOADS),
        "trace_hashes":hashes(prefix,TRACES),"native_mean":dict(zip(NATIVE,n.mean(axis=0).tolist())),
        "operator_mean":dict(zip(OPERATORS,o.mean(axis=0).tolist())),
        "match_mean":dict(zip(MATCH,m.mean(axis=0).tolist())),
        "match_raw_totals":dict(zip(MATCH,m.sum(axis=0).tolist())),
        "storage_mean":dict(zip(STORAGE,storage.mean(axis=0).tolist())),
        "storage_min":dict(zip(STORAGE,storage.min(axis=0).tolist())),
        "storage_max":dict(zip(STORAGE,storage.max(axis=0).tolist())),
        "storage_loadfactor_mean":float(np.mean(storage[:,8]/storage[:,7])),
        "effective_degree_measured":mode!="graph" and scenario!="unfilter",
        "trajectory_hashes":hashes(prefix,TRAJECTORY),
        "action_mean":dict(zip(ACTION,a.mean(axis=0).tolist())),
        "ssd_mean":work.mean(axis=0).tolist(),"exact_final_predicate_valid":True,
        "diagnostic_off_on_ids_distances_ssd_parity":True,
        "ordinary_partial_rows":int(np.count_nonzero(d[:,4]==0)) if scenario!="unfilter" else None,
        "csr_partial_rows":int(np.count_nonzero(rows[:,4]<rows[:,3])),
        "csr_completed_rows":len(rows),
        "budget_crossing_posting_actions":int(np.count_nonzero((d[:,5]==1)&(d[:,7]>2048))),
        "timing_kind":"ordinary1000" if count==1000 else "bounded32 functional/runtime preflight"}
    if scenario=="broad_tag" and mode=="graph":
        oracle=PARENT/"broad_tag_visited_graph_ratio_0.01_r1/nprobe_24"
        require(record["payloads"]==hashes(oracle,PAYLOADS),"Frozen original native output/SSD mismatch")
        require(hashes(prefix,CONTROL_TRACES)==hashes(oracle,CONTROL_TRACES),"Frozen original native work/heads/own mismatch")
        record["exact_frozen_oracle"]=str(oracle)
        require(hashes(prefix,TRAJECTORY)==hashes(oracle,TRAJECTORY),"Predecessor exact graph trajectory")
    if count==32 and mode=="graph":
        oracle=PARENT/f"small_{scenario}_visited_graph_ratio_0.01/nprobe_24"
        require(record["payloads"]==hashes(oracle,PAYLOADS),"Frozen preflight graph payload mismatch")
        require(hashes(prefix,CONTROL_TRACES)==hashes(oracle,CONTROL_TRACES),"Frozen preflight native work mismatch")
        record["exact_frozen_oracle"]=str(oracle)
    write(directory/"record.json",record)
    print(directory.name,native["mean_latency_ms"],native["recall"],flush=True)
    return record

def invoke(label,scenario,mode,small=False):
    directory=OUTPUT/label
    cfg=HERE/("diagnostic_configs" if small else "configs")/f"{scenario}_{case(mode)}.ini"
    if directory.exists():
        require(json.loads((directory/"exit.json").read_text())["returncode"]==0,
                "Never silently rerun an executed point")
        seconds=json.loads((directory/"process_seconds.json").read_text())["seconds"]
    else:
        directory.mkdir()
        seconds=process(["numactl","--cpunodebind=2","--membind=2",str(TOOL/"harness/postfilter-bench"),
            "--config",str(cfg)],directory,max_seconds=180)
        write(directory/"process_seconds.json",{"seconds":seconds})
    record=validate(directory,scenario,mode,32 if small else 1000,seconds)
    record.update(config=str(cfg),config_sha256=sha(cfg))
    write(directory/"record.json",record)
    return record

def main():
    require(not (OUTPUT/"milestone_manifest.json").exists(),"Sealed milestone: STOP / IDLE")
    require((OUTPUT/"build_artifacts.json").exists(),"Native fixtures must pass first")
    freeze()
    rejection_tests()
    write(OUTPUT/"measurement_plan.json",{
        "broad_points":["graph","match","posting"],"ratio":.01,"broad_ordinary_processes":6,
        "sparse_preflight_modes":["graph","match","posting"],"unfilter_preflight_modes":["graph","match","posting"],
        "sparse_full_runtime_rule":"Forbidden; sparse32 functional replay only",
        "ordinary":"1000 warmup +1000 measured +1000 untimed capture; native [24], offset0 topk10; CPU/memory NUMA2; thread1",
        "repetitions":"two, reverse order","stop":"No additional thresholds, variants, sweeps, promotion"})
    small=[]
    for scenario in ("extreme_tag","unfilter"):
        group=[invoke(f"small_{scenario}_{case(mode)}",scenario,mode,True) for mode in ("graph","match","posting")]
        require(group[0]["payloads"]==group[1]["payloads"] and
            group[0]["trajectory_hashes"]==group[1]["trajectory_hashes"],"Bit-only native trajectory parity")
        if scenario=="unfilter": require(group[0]["payloads"]==group[2]["payloads"],"Unfilter parity")
        small.extend(group)
    sparse=small[:3]
    require(sparse[2]["action_mean"]["sparse_activations"]>0 and sparse[2]["native_mean"]["h2_rows"]>0,
        "Sparse preflight did not activate real posting")
    allow=False
    write(OUTPUT/"small_replay.json",{"records":small,"unfilter_exact_parity":True,
        "full_sparse_allowed":allow,"decision_input_only_runtime_ms":[r["native"]["mean_latency_ms"] for r in sparse],
        "rule":"No full sparse campaign authorized","no_ground_truth_tuning":True})
    records=[];plan=[]
    for scenario,modes in (("broad_tag",["graph","match","posting"]),):
        for rep,order in ((1,modes),(2,modes[::-1])):
            plan.extend((f"{scenario}_{case(mode)}_r{rep}",scenario,mode) for mode in order)
    write(OUTPUT/"ordinary_plan.json",plan)
    for label,scenario,mode in plan:
        records.append(invoke(label,scenario,mode))
        write(OUTPUT/"ordinary_progress.json",records)
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
            **{k:pair[0][k] for k in ("native_mean","operator_mean","action_mean","match_mean","match_raw_totals","effective_degree_measured",
                                     "storage_mean","storage_min","storage_max","storage_loadfactor_mean",
                                     "ssd_mean","ordinary_partial_rows","csr_partial_rows",
                                     "csr_completed_rows","budget_crossing_posting_actions")},
            "native_files":[str(OUTPUT/r["label"]/"nprobe_24") for r in pair],
            "exact_repetition_determinism":True})
    write(OUTPUT/"summary.json",summary)
    for rep in (1,2):
        graph=next(r for r in records if r["scenario"]=="broad_tag" and r["mode"]=="graph" and r["label"].endswith(f"_r{rep}"))
        match=next(r for r in records if r["scenario"]=="broad_tag" and r["mode"]=="match" and r["label"].endswith(f"_r{rep}"))
        require(graph["payloads"]==match["payloads"] and graph["trajectory_hashes"]==match["trajectory_hashes"],
            "Broad graph/bit-only exact native trajectory mismatch")
    write(OUTPUT/"operations.json",{"records":records,"ordinary_index_loads":len(records),
        "stop":"No more measurement permitted"})
    protect()

if __name__=="__main__":main()
