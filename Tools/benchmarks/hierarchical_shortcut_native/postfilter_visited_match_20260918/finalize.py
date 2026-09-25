"""Seal the bounded visited-match milestone; never launch more measurements."""
import json
from prepare import HERE,TOOL,OUTPUT,PARENT,FILES,sha,write,protect
from run import require

def main():
    require(not (OUTPUT/"milestone_manifest.json").exists(),"Already sealed: STOP / IDLE")
    operations=json.loads((OUTPUT/"operations.json").read_text())
    records=operations["records"]
    require(len(records) in (6,10),"Ordinary process ceiling")
    require(all(r["csr_partial_rows"]==0 for r in records),"Partial auxiliary row")
    summary=json.loads((OUTPUT/"summary.json").read_text())
    graph,match,posting=(next(s for s in summary if s["scenario"]=="broad_tag" and s["mode"]==mode)
                         for mode in ("graph","match","posting"))
    for rep in (1,2):
        g,m=(next(r for r in records if r["scenario"]=="broad_tag" and r["mode"]==mode and
             r["label"].endswith(f"_r{rep}")) for mode in ("graph","match"))
        require(g["payloads"]==m["payloads"] and g["trajectory_hashes"]==m["trajectory_hashes"],
                "Bit-only native trajectory changed")
    g,p=(next(r for r in records if r["scenario"]=="broad_tag" and r["mode"]==mode)
         for mode in ("graph","posting"))
    sources={name:sha(HERE/name) for name in FILES}
    for name,path in FILES.items():
        require(sources[name]==sha(TOOL/"source"/path),"Installed source differs")
    for name in ("source_tree_manifest.json","compiler_metadata_manifest.json"):
        for path,digest in json.loads((OUTPUT/name).read_text()).items():
            require(sha(path)==digest,"Frozen source/compiler artifact changed: "+path)
    text=(HERE/"BKTIndex.cpp").read_text()
    require("decision.eligible<hook.activationRatio*decision.degree" in text,"Wrong signal")
    require("decision.passes<hook.activationRatio*decision.checks" not in text,"Old signal remains")
    require(not (TOOL/"source/AnnService/Qualification.h").exists(),"Side eligibility cache remains")
    require("expandEdges(head,0,this,ids,0,count,false,&work,nullptr,true)" in text,"Full CSR fix missing")
    proof={
        "single_native_navigation_probe_returns_wasVisited_and_match":True,
        "first_insertion_predicate_before_distance_or_result_competition":True,
        "all_navigation_insertions_include_native_tree_init_and_continuation":True,
        "all_occupied_extended_slots_have_initialized_query_match":True,
        "legacy_insertion_into_extended_table_throws":True,
        "full_row_independent_oracle_in_untimed_capture_only":True,
        "oracle_includes_valid_already_visited":True,
        "one_first_match_evaluation_per_inserted_physical_id_from_real_trace":True,
        "no_predicate_evaluation_on_rehash_or_reused_read":True,
        "rejected_uninserted_auxiliary_is_not_cached":True,
        "all_selected_CSR_rows_consumed_equals_size":True,
        "ordinary_rows_retain_native_per_edge_budget_truncation":True,
        "no_auxiliary_action_after_budget":True,
        "same_trace_graph_match_ids_distances_head_own_queue_checked_visited_SSD":True,
        "all_processes_capture_off_on_ids_distances_SSD_equal":True,
        "native_fixtures":str(OUTPUT/"native-fixtures-detail.log"),
        "protocol_tests":str(OUTPUT/"protocol-tests.log"),
        "graph_oracle":str(PARENT/"broad_tag_native_graph_hitratio_0.01_r1/nprobe_24"),
        "graph_frozen_ids_distances_head_own_queue_checked_SSD_equal":True}
    proof["prior_frozen_raw_visited_trace_available"]=False
    proof["raw_visited_parity_scope"]="same-core graph versus match captures plus native fixtures; prior oracle has aggregate native work but no raw visited stream"
    proof["real_query_auxiliary_budget_crossing_actions"]=sum(r["budget_crossing_posting_actions"] for r in records)
    proof["crossing_budget_validation_scope"]="native fixture explicitly crosses budget8 and completes one selected row; real bounded captures have the above observed crossing count"
    write(OUTPUT/"compile_proof.json",proof)
    write(OUTPUT/"installed_source_manifest.json",{str(TOOL/"source"/path):sources[name] for name,path in FILES.items()})
    protect()
    report={
        "milestone":HERE.name,"status":"COMPLETE_STOP_IDLE","promotion":False,
        "goal_met":{"architecture_and_bounded_validation":True,
                    "native_level_posting_latency":posting["ordinary_ms"]<=graph["ordinary_ms"]},
        "base":{"path":str(PARENT),"report_sha256":sha(PARENT/"report.json"),
                "sealed_files":len(json.loads((PARENT/"milestone_manifest.json").read_text())),
                "ancestor_fullneighbor_report_sha256":"949ab739d7542926f93ec92ad06055566613fb3e2be6ff0ccc5940a7fd612e6f",
                "ancestor_cache_not_imported":True,"prior_full_CSR_correction_retained":True},
        "controls":{"VisitedMatchMode":["graph","match","posting"],"PostingNeighborMatchRatio":.01,
                    "graph":"compact native visited, no match predicate, result-only original admission",
                    "match":"extended native visited and first-match evaluation; posting disabled",
                    "posting":"extended native visited with fixed physical-row gate",
                    "untuned":True,"ratio_zero_rejected":True,
                    "old_observed_admission_signal_removed_not_compatible":True},
        "storage":{"default_slot_bytes":4,"match_slot_bytes":8,"semantic_match_bits":1,
                   "key":"unsigned low32 = uint32(nonnegative signed32 ID)+1; zero empty; bit32 static OR match; upper31 unused",
                   "ID_domain":"storage supports 0..INT_MAX without signed id+1 overflow; actual BKT candidates additionally require ID<GetNumSamples()",
                   "allocation":"exactly one active two-table native slot allocation; compact and extended arrays are mutually exclusive, capacity is visited capacity not H1 size",
                   "reuse":"Reset clears callback/slots and selects compact storage; filtered enable selects extended. Current implementation reallocates on compact/extended transitions; measured tax includes this.",
                   "growth":"rehash both old blocks into a fresh larger allocation, retry on collision overflow, copy full encoded slot, no predicate and no retained slot pointer",
                   "result_dedup":"independent original resultCheckStatus stays compact; never borrows match flag"},
        "match_semantics":{
            "bit":"query-specific OR of posting support/admission and static own predicate, including any native-live collapsed alias",
            "ordinary":"every valid physical edge gets one native probe; true visited bits count in numerator and denominator; false matches still visit/distance/queue/navigate",
            "aliases":"one physical representative bit ORs representative and collapsed result aliases; native per-alias result/own admission is unchanged",
            "components":"OR is not posting truth or own truth; original posting and own component checks remain, including competitive result predicate calls. No claim these were eliminated.",
            "auxiliary":"same probe with insert-if-match; negative absent candidates remain absent and can be reevaluated in later selected rows; no hidden negative cache",
            "seed":"tree init and continuation use WorkSpace::CheckAndSet, which invokes match-aware probe whenever enabled",
            "unknown":"no occupied unknown state exists; legacy direct insert into extended storage throws"},
        "liveness_capability":{
            "supported":"single-thread read-only immutable query/index snapshot in isolated STATIC benchmark; preexisting BKT/version-map deletions enter first-match OR; native result/own liveness checks still run",
            "safe_errors":"enabled raw hook requires immutableSnapshot=true and result filter/no traversal filter; benchmark match/posting rejects native BKT/SPANN Add/Delete entrypoints; same-thread mutation during match hook also rejected",
            "not_supported":"concurrent deletion/update, externally mutating backing arrays, changing mode/index/filter during a query, and general concurrent maintenance. No concurrency/snapshot guarantee is claimed for those unsupported uses.",
            "graph":"ordinary native mutable behavior is retained"},
        "activation":"ordinary physical row completes first; d>0, e<0.01*d and remaining native budget permit one supplier action. d=0 has no ratio; truncated rows never activate.",
        "supplier":"signatures precede representative distance/CSR. Complete selected H2/H3 CSR is consumed even crossing budget. No next action after exhaustion; no useful auxiliary returns to original H1 frontier. No quotas/caps/two-hop/restart.",
        "ownership":"original owner inverse only in posting-mode index load; not an H1 eligibility array. No Qualification lease/cache/fullN match table.",
        "implementation":proof,
        "timing":{"body_sha256":json.loads((OUTPUT/"runtime.json").read_text())["timed_body_sha256"],
                  "ordinary_processes":len(records),"max_ordinary_processes":10,
                  "warmup":1000,"measured":1000,"offset":0,"nprobe":[24],"topk":10,
                  "MaxCheck":2048,"HierarchyMaxCheck":512,"HierarchyInitialProbeRatio":.666666,
                  "pages":15,"NUMA_CPU_memory":2,"query_threads":1,
                  "capture_profile_preload_off":True,"untimed_replay_per_process":1000,
                  "repetitions":2,"second_repetition_reversed":True,
                  "descriptive_only_not_statistical_significance":True,
                  "sparse_runtime_guard":"max of all sparse32 modes <10ms before admitting four sparse ordinary processes"},
        "counter_schema":{
            "match.u64":"raw per-query: visited_probes, first_match_evaluations, reused_bit_reads, all_match_evaluations, rejected_uninserted_aux_evaluations, physical_degree, effective_degree, eligible_already_visited, oracle_evaluations, match_component_calls",
            "visited_probes_scope":"logical native CheckAndSet/probe calls including tree/ordinary/auxiliary; native collision slot iterations are not separately counted",
            "operators.u64":"outer heads, ordinary slots, ordinary native result predicate calls/passes/fails, zero-call rows, auxiliary native result predicate calls, ALL native admitFilteredResult predicate calls (includes seeds/popped heads)",
            "decisions.f64":"query,head,existing_calls,existing_passes,complete,activated,checked_before,checked_after,signatures,representatives,CSRmembers,physical_d,e,eligible_visited,oracle_d,oracle_e",
            "match_component_calls":"candidate liveness/static-OR checks inside physical matching; may exceed physical evaluations due to collapsed aliases. Not a count of every nested support/own operation; oracle evaluations separate.",
            "graph_match_counters":"raw native visited probes and physical degree are measured; effective-degree/first-match/reuse/classification/oracle zero denotes disabled/unmeasured, NOT no matches"},
        "summary":summary,
        "broad":{"bit_only_same_native_trace":True,
                 "same_trace_match_tax_ms":match["ordinary_ms"]-graph["ordinary_ms"],
                 "same_trace_match_tax_runs_ms":[m-g for m,g in zip(match["ordinary_ms_runs"],graph["ordinary_ms_runs"])],
                 "tax_scope":"whole enabled visited-match path, including slot width/reallocation, dispatch, first predicate evaluations and local row bookkeeping; not isolated predicate execution time",
                 "same_trace_match_over_graph_ratio":match["ordinary_ms"]/graph["ordinary_ms"],
                 "posting_over_graph_latency_ratio":posting["ordinary_ms"]/graph["ordinary_ms"],
                 "posting_absolute_delta_ms":posting["ordinary_ms"]-graph["ordinary_ms"],
                 "posting_is_not_a_same_trace_causal_decomposition":True,
                 "posting_payload_changed":g["payloads"]!=p["payloads"],
                 "posting_native_trajectory_changed":g["trajectory_hashes"]!=p["trajectory_hashes"],
                 "bit_read_reuse_fraction":match["match_mean"]["reused_bit_reads"]/match["match_mean"]["visited_probes"],
                 "native_level_latency_achieved":posting["ordinary_ms"]<=graph["ordinary_ms"],
                 "historical_metrics_spliced":False},
        "protection":{"files_verified":len(json.loads((OUTPUT/"protection.json").read_text())),
                      "old_source_build_config_evidence_production_libraries_modules_index_plots_operator_stop_unchanged":True,
                      "GettingStart":"main-owned documentation divergence explicitly excluded; not edited here"},
        "build_iteration_provenance":"build_pass1 preserved initial passing fixtures; build_pass2 preserves a harness/installed-header schema mismatch while lifecycle and counters were being finalized; final coherent source installation rebuilt core/harness and passed all fixtures before any timing. No benchmark architecture/threshold was changed after measurement began.",
        "reproduction":[f"python3 {HERE}/prepare.py --initialize",f"python3 {HERE}/build.py",
                        f"python3 {HERE}/run.py",f"python3 {HERE}/finalize.py"],
        "artifacts":{name:str(OUTPUT/name) for name in (
            "build_commands.json","runtime.json","native_rejection_tests.json","small_replay.json",
            "ordinary_plan.json","operations.json","compile_proof.json","installed_source_manifest.json",
            "source_tree_manifest.json","compiler_metadata_manifest.json","STOP_IDLE.json")},
        "stop":"IDLE; no curves, thresholds, promotion or automatic further optimization"}
    write(OUTPUT/"report.json",report)
    write(OUTPUT/"STOP_IDLE.json",{"resume_allowed":False,"ordinary_processes_completed":len(records),
                                  "promotion":False,"reason":"Bounded visited-match milestone complete; no further automatic work"})
    lines=["# Visited-match bounded milestone — STOP / IDLE","",
           "Architecture/validation completed; **native-level posting latency "+
           ("was achieved" if report["goal_met"]["native_level_posting_latency"] else "was not achieved")+
           "** at the measured means in this bounded comparison.",
           "No promotion, curves, tuning or automatic next optimization. Two repetitions are descriptive, not a significance test.","",
           "| Scenario | Mode | Mean ms | r1 ms | r2 ms | ms range | Recall@10 | QPS at mean |",
           "|---|---|---:|---:|---:|---|---:|---:|"]
    for s in summary:
        lines.append(f"| {s['scenario']} | {s['mode']} | {s['ordinary_ms']:.6f} | "
                     f"{s['ordinary_ms_runs'][0]:.6f} | {s['ordinary_ms_runs'][1]:.6f} | "
                     f"{s['ms_range'][0]:.6f}–{s['ms_range'][1]:.6f} | {s['recall_at_10']:.4f} | {s['qps_at_mean_ms']:.2f} |")
    lines+=["","All work below is per query, from untimed replay. Graph effective degree is unmeasured, not zero.",
            "Probes are logical native visited probes (including tree and auxiliary), not collision-slot reads.","",
            "| Scenario/mode | Probes | First match | Reused bit | Absent aux rejects | Physical d | Effective e | Eligible visited | Native result predicates |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for s in summary:
        m=s["match_mean"];o=s["operator_mean"]
        e="—" if s["mode"]=="graph" else f"{m['effective_degree']:.3f}"
        lines.append(f"| {s['scenario']}/{s['mode']} | {m['visited_probes']:.3f} | {m['first_match_evaluations']:.3f} | "
                     f"{m['reused_bit_reads']:.3f} | {m['rejected_uninserted_aux_evaluations']:.3f} | "
                     f"{m['physical_degree']:.3f} | {e} | {m['eligible_already_visited']:.3f} | "
                     f"{o['all_native_result_predicate_calls']:.3f} |")
    lines+=["","| Scenario/mode | Outer heads | Ordinary slots | Ordinary native predicates | Posting actions | Sig reject | Rep distances | CSR members | H2 rows | H3 rows | Checked |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for s in summary:
        n=s["native_mean"];o=s["operator_mean"];a=s["action_mean"]
        values=[o["outer_heads"],o["ordinary_row_slots"],o["existing_predicate_calls"],
                a["selected_actions"],n["signature_rejects"],n["representative_distances"],
                n["csr_members"],n["h2_rows"],n["h3_rows"],n["checked"]]
        lines.append(f"| {s['scenario']}/{s['mode']} | "+" | ".join(f"{v:.3f}" for v in values)+" |")
    lines+=["",f"Broad bit-only same-trace tax: **{report['broad']['same_trace_match_tax_ms']:.6f} ms**, "
            f"{report['broad']['same_trace_match_over_graph_ratio']:.4f}× graph. Exact IDs/distances/head/own/queue/checked/visited/SSD parity passed.",
            "Posting changes native work and payloads; its latency difference is not a same-trace causal decomposition.",
            "Raw integer totals, SSD vectors, all per-process ranges/QPS, row decisions and independent oracle d/e are in report.json and record.json files.",
            "",
            "- Compact32 is retained for graph/unfiltered; enabled visited uses one uint64 slot with low32 key and bit32 OR match. No separate H1 eligibility cache.",
            "- Every occupied enabled slot is initialized at first navigation insertion, including tree seeds/continuation. Growth copies bit/key without evaluating; legacy unqualified insertion throws.",
            "- The bit ORs posting and own static eligibility (including live collapsed aliases). Native component/result checks remain; own-only never becomes posting truth.",
            "- Absent rejected auxiliary candidates stay absent and can be reevaluated by later rows. Sparse negative evaluations are not cached or hidden.",
            "- Read-only immutable snapshot capability only. Enabled mutation APIs/unsupported hooks fail; concurrent maintenance/raw-array mutation is unsupported, not silently claimed safe.",
            "- Full ordinary-row d/e includes valid visited entries; independent oracle runs only in untimed capture. Truncated rows do not activate; d=0 has no ratio.",
            f"- Every selected CSR row completed. Real captures observed {proof['real_query_auxiliary_budget_crossing_actions']} budget-crossing auxiliary actions; the native budget8 fixture explicitly crosses and completes the selected row.",
            "- Prior frozen oracle has aggregate native work, not raw visited streams; raw visited parity is proved between same-core graph/match and native fixtures.",
            f"- Native C++ fixtures and protocol tests passed; all {len(json.loads((OUTPUT/'native_rejection_tests.json').read_text()))} interface/environment rejection tests passed. Six sparse/unfilter preflights passed; all {len(records)} allowed ordinary processes completed.",
            "- Prior protected source/build/config/evidence, production modules, inputs, plots and OPERATOR_STOP are unchanged. GettingStart remains parent-owned.",
            "",
            "See source README.md and report.json for storage, lifetime, liveness, counters, build commands and provenance. STOP_IDLE.json disallows automatic continuation.",""]
    (OUTPUT/"REPORT.md").write_text("\n".join(lines))
    write(OUTPUT/"report.sha256.json",{name:sha(OUTPUT/name) for name in ("report.json","summary.json")})
    manifest={}
    for root in (HERE,OUTPUT):
        for path in root.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.name!="milestone_manifest.json":
                manifest[str(path)]=sha(path)
    for name,path in FILES.items(): manifest[str(TOOL/"source"/path)]=sha(TOOL/"source"/path)
    for path in [TOOL/"harness/postfilter-bench",TOOL/"harness/native-tests",
                 *(TOOL/"source/Release").glob("*.a")]: manifest[str(path)]=sha(path)
    write(OUTPUT/"milestone_manifest.json",manifest)
    print(json.dumps({"report_sha256":sha(OUTPUT/"report.json"),"sealed_files":len(manifest),
                      "broad":report["broad"],"goal_met":report["goal_met"],"stop":"IDLE"},indent=2))

if __name__=="__main__":main()
