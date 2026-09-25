"""Seal one bounded observed-native-switch milestone. No further executions."""
import json
from prepare import HERE,TOOL,OUTPUT,PARENT,FILES,sha,write,protect
from run import matrix,require,CONTROL_TRACES

def main():
    operations=json.loads((OUTPUT/"operations.json").read_text())
    records=operations["records"]
    require(len(records) in (4,8),"Ordinary process ceiling")
    require(all(r["mode"] in ("graph","posting") for r in records),"Only two policies")
    require(all(r["csr_partial_rows"]==0 for r in records),"Never accept partial CSR")
    summary=json.loads((OUTPUT/"summary.json").read_text())
    graph=next(s for s in summary if s["scenario"]=="broad_tag" and s["mode"]=="graph")
    posting=next(s for s in summary if s["scenario"]=="broad_tag" and s["mode"]=="posting")
    g=next(r for r in records if r["scenario"]=="broad_tag" and r["mode"]=="graph")
    p=next(r for r in records if r["scenario"]=="broad_tag" and r["mode"]=="posting")
    same_trace=g["payloads"]==p["payloads"] and all(
        g["trace_hashes"][k]==p["trace_hashes"][k] for k in CONTROL_TRACES)
    sources={name:sha(HERE/name) for name in FILES}
    for name,path in FILES.items():
        require(sources[name]==sha(TOOL/"source"/path),"Installed source differs")
    text=(HERE/"BKTIndex.cpp").read_text()
    spann=(HERE/"SPANNIndex.cpp").read_text()
    for forbidden in ("QualificationLease","qualifies","hooks.eligible","gather"):
        require(forbidden not in text and forbidden not in spann,"Removed mechanism remains "+forbidden)
    require(not (TOOL/"source/AnnService/Qualification.h").exists(),"Qualification cache retained")
    require("ordinaryObservation->passes+=valid" in text,"Not observing actual predicate")
    require("expandEdges(head,0,this,ids,0,count,false,&work,nullptr,true)" in text,"Lost fullrow fix")
    proof={
        "actual_native_boolean_observation":True,"no_classifier_prescan":True,"no_second_hop":True,
        "no_qualification_lease_or_cache":True,"ordinary_extra_predicate_calls":0,
        "ordinary_scope":"Natural admitFilteredResult calls during one ordinary expandEdges, aliases included; initialization/popped-head admission excluded",
        "auxiliary_separate":True,"scope_cleared_before_posting":True,
        "all_selected_rows_consumed_equals_size":True,
        "no_auxiliary_row_start_after_budget":True,
        "complete_row_runtime_assertion":True,
        "native_fixtures_log":str(OUTPUT/"native-fixtures-detail.log"),
        "protocol_tests_log":str(OUTPUT/"protocol-tests.log"),
        "graph_oracle":str(PARENT/"broad_tag_postfilter_graph_ratio_0.01_r1/nprobe_24"),
        "frozen_graph_exact_ids_distances_head_own_queue_checked_ssd":True}
    write(OUTPUT/"compile_proof.json",proof)
    write(OUTPUT/"installed_source_manifest.json",{str(TOOL/"source"/path):sources[name] for name,path in FILES.items()})
    protect()
    protected=json.loads((OUTPUT/"protection.json").read_text())
    sparse=[s for s in summary if s["scenario"]=="extreme_tag"]
    report={
        "milestone":HERE.name,"status":"COMPLETE_STOP_IDLE","promotion":False,
        "base":{"path":str(PARENT),"report_sha256":sha(PARENT/"report.json"),
                "summary_sha256":sha(PARENT/"summary.json"),
                "sealed_files":len(json.loads((PARENT/"milestone_manifest.json").read_text())),
                "fullrow_correction_validation_sha256":sha(PARENT/"full_row_correction_validation.json")},
        "controls":{"NativePostfilterMode":["graph","posting"],"PostingFilterHitRatio":.01,
                    "zero_disables":True,"untuned":True},
        "meaning":"Native-observed ordinary-expansion head postfilter acceptance, NOT physical-neighbor valid degree or population selectivity",
        "activation":"Only after completed ordinary row, remaining native budget, checks>0 and passes<ratio*checks",
        "unknown":"checks==0 stays native graph; unjudged/visited never synthesize failure",
        "work_avoided_claim":False,"no_new_ordinary_qualification":True,
        "ownership":"Original immutable owner inverse constructed once at posting-mode index load, never graph. All query owner/signature/representative/CSR access is post-activation.",
        "implementation":proof,
        "timing":{"body_sha256":json.loads((OUTPUT/"runtime.json").read_text())["timed_body_sha256"],
                  "ordinary_processes":len(records),"max_ordinary_processes":8,
                  "warmup":1000,"measured":1000,"offset":0,"nprobe":[24],"topk":10,
                  "MaxCheck":2048,"HierarchyMaxCheck":512,"HierarchyInitialProbeRatio":.666666,
                  "pages":15,"NUMA_CPU_memory":2,"query_threads":1,
                  "capture_profile_preload_off":True,"untimed_replay_per_process":1000,
                  "repetitions":2,"second_repetition_reversed":True},
        "summary":summary,
        "broad":{"same_native_trace":same_trace,
                 "posting_triggers":posting["action_mean"]["sparse_activations"],
                 "interpretation":"different native trajectory, not same-trace observation tax" if not same_trace else "exact trace parity",
                 "posting_over_graph_latency_ratio":posting["ordinary_ms"]/graph["ordinary_ms"],
                 "absolute_delta_ms":posting["ordinary_ms"]-graph["ordinary_ms"],
                 "native_level_broad_latency_achieved":posting["ordinary_ms"]<=graph["ordinary_ms"],
                 "no_net_signature_pruning_speedup_claim":True,
                 "conclusion":"Posting still slower than native graph; acceptance scope changed and actions changed, so residual latency is not a same-trace qualification tax",
                 "historical_observe":"Frozen history only; no observe policy or timings spliced into this comparison"},
        "sparse":sparse,
        "bounded_only":True,"no_curves_or_threshold_search":True,
        "initial_setup_correction":"Restored zstd source build/cmake excluded by overly broad copy ignore; configure failure preserved; no algorithm variant.",
        "protection":{"files_verified":len(protected),"old_sources_builds_evidence_configs_unchanged":True,
                      "production_libraries_modules_index_plots_operator_stop_unchanged":True,
                      "GettingStart":"main-owned; not edited"},
        "artifacts":{name:str(OUTPUT/name) for name in (
            "build_commands.json","runtime.json","native_rejection_tests.json","small_replay.json",
            "ordinary_plan.json","operations.json","compile_proof.json","installed_source_manifest.json")},
        "stop":"IDLE; no automatic promotion or additional measurements"}
    write(OUTPUT/"report.json",report)
    write(OUTPUT/"report.sha256.json",{name:sha(OUTPUT/name) for name in ("report.json","summary.json")})
    manifest={}
    for root in (HERE,OUTPUT):
        for path in root.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.name!="milestone_manifest.json":
                manifest[str(path)]=sha(path)
    for name,path in FILES.items():
        manifest[str(TOOL/"source"/path)]=sha(TOOL/"source"/path)
    for path in [TOOL/"harness/postfilter-bench",TOOL/"harness/native-tests",
                 *(TOOL/"source/Release").glob("*.a")]:
        manifest[str(path)]=sha(path)
    write(OUTPUT/"milestone_manifest.json",manifest)
    print(json.dumps({"report_sha256":sha(OUTPUT/"report.json"),
                      "summary_sha256":sha(OUTPUT/"summary.json"),"sealed_files":len(manifest),
                      "broad":report["broad"],"stop":"IDLE"},indent=2))

if __name__=="__main__":main()
