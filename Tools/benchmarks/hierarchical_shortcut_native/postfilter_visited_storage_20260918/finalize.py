"""Seal the storage-only successor after correctness and exactly six processes."""
import json
from prepare import HERE,TOOL,OUTPUT,PARENT,FILES,sha,write,protect
from run import require,hashes,PAYLOADS,TRAJECTORY

def reference(path,needle):
    lines=path.read_text().splitlines()
    matches=[i for i,line in enumerate(lines,1) if needle in line]
    require(matches,"Missing source reference "+needle)
    return {"path":str(path),"lines":matches,"symbol":needle}

def main():
    require(not (OUTPUT/"milestone_manifest.json").exists(),"Sealed: STOP / IDLE")
    operations=json.loads((OUTPUT/"operations.json").read_text())
    records=operations["records"]
    require(len(records)==6 and all(r["scenario"]=="broad_tag" for r in records),"SIX Broad only")
    small=json.loads((OUTPUT/"small_replay.json").read_text())
    require(len(small["records"])==6 and not small["full_sparse_allowed"],"Functional-only sparse")
    summary=json.loads((OUTPUT/"summary.json").read_text())
    graph,match,posting=(next(s for s in summary if s["mode"]==mode) for mode in ("graph","match","posting"))
    for rep in (1,2):
        g,m=(next(r for r in records if r["mode"]==mode and r["label"].endswith(f"_r{rep}"))
             for mode in ("graph","match"))
        require(g["payloads"]==m["payloads"] and g["trajectory_hashes"]==m["trajectory_hashes"],
                "Exact same-core graph/match parity")
    for r in records+small["records"]:
        require(r["csr_partial_rows"]==0,"Partial CSR")
        require(all(r["storage_max"][key]==0 for key in
                    ("allocations","conversions","allocated_bytes_during_query")),"Backend churn")
        require(r["storage_min"]["nonempty_clears"]>0,"Storage diagnostics not attached")
        require(r["storage_max"]["slot_bytes"]==r["storage_max"]["result_slot_bytes"]==4,
                "Native or result table widened")
        current=OUTPUT/r["label"]/"nprobe_24"
        prior=PARENT/r["label"]/"nprobe_24"
        suffixes=PAYLOADS+TRAJECTORY+(".decisions.f64",".rows.u64",".match.u64")
        require(hashes(current,suffixes)==hashes(prior,suffixes),
                "Storage-only successor changed predecessor results/trajectory/counters: "+r["label"])
    fixture=(OUTPUT/"native-fixtures-detail.log").read_text()
    for expected in ("STORAGE_CALIBRATION init+promotion+growth allocations=3 conversions=1",
                     "STORAGE_MIXED actual_queries=128",
                     "STORAGE_INDEX_SWITCH actual_queries=64",
                     "PASS UBSan packed native highest/sentinel",
                     "100 packed mixed queries +100 sticky-wide mixed queries zero allocations/conversions"):
        require(expected in fixture,"Missing storage fixture "+expected)
    sources={name:sha(HERE/name) for name in FILES}
    for name,path in FILES.items(): require(sources[name]==sha(TOOL/"source"/path),"Installed mismatch")
    for name in ("source_tree_manifest.json","compiler_metadata_manifest.json"):
        for path,digest in json.loads((OUTPUT/name).read_text()).items():
            require(sha(path)==digest,"Frozen build changed "+path)
    predecessor=json.loads((PARENT/"milestone_manifest.json").read_text())
    require(len(predecessor)==478,"Unexpected predecessor seal")
    for path,digest in predecessor.items(): require(sha(path)==digest,"Predecessor changed "+path)
    protect()
    native=TOOL/"source/AnnService"
    refs={
        "predecessor_reset":reference(HERE.with_name("postfilter_visited_match_20260918")/"WorkSpace.h",
                                     "nodeCheckStatus.EnableMatch(false)"),
        "predecessor_transition":reference(HERE.with_name("postfilter_visited_match_20260918")/"WorkSpace.h",
                                          "void EnableMatch"),
        "size_type":reference(native/"inc/Core/Common.h","typedef std::int32_t SizeType"),
        "max_size":reference(native/"inc/Core/Common.h","const SizeType MaxSize"),
        "sample_count":reference(native/"inc/Core/BKT/Index.h","inline SizeType GetNumSamples"),
        "sample_access":reference(native/"inc/Core/Common/Dataset.h","if (index < R() && index >= 0)"),
        "dataset_growth":reference(native/"inc/Core/Common/Dataset.h","if (R() > maxRows - num)"),
        "dataset_stream_load":reference(native/"inc/Core/Common/Dataset.h","ErrorCode Load(std::shared_ptr"),
        "dataset_memory_load":reference(native/"inc/Core/Common/Dataset.h","ErrorCode Load(char*"),
        "build":reference(HERE/"BKTIndex.cpp","ErrorCode Index<T>::BuildIndex("),
        "stream_load":reference(HERE/"BKTIndex.cpp","ErrorCode Index<T>::LoadIndexData("),
        "memory_load":reference(HERE/"BKTIndex.cpp","ErrorCode Index<T>::LoadIndexDataFromMemory"),
        "locator_limit":reference(HERE/"BKTIndex.cpp","encoded >= static_cast<std::uint64_t>(MaxSize)"),
        "native_enable":reference(HERE/"BKTIndex.cpp","EnableNativeMatch(GetNumSamples())"),
        "masked_key":reference(HERE/"WorkSpace.h","StoredKey(Slot slot)"),
        "generic_promotion":reference(HERE/"WorkSpace.h","void PromoteWide()"),
        "query_reset":reference(HERE/"WorkSpace.h","void ResetQuery()"),
        "native_domain_validation":reference(HERE/"WorkSpace.h","id>=m_nativeCount"),
        "insertion":reference(HERE/"WorkSpace.h","std::pair<bool,bool> Probe("),
        "growth":reference(HERE/"WorkSpace.h","void Grow("),
        "counter_hook":reference(HERE/"WorkSpace.h","struct StorageDiagnostics"),
        "untimed_proof":reference(HERE/"MatchedBench.cpp","Ordinary capture-off queries, outside timing"),
        "mixed_fixture":reference(HERE/"NativeTests.cpp","auto mixedQuery="),
        "index_switch_fixture":reference(HERE/"NativeTests.cpp","auto switched=")}
    write(OUTPUT/"source_references.json",refs)
    write(OUTPUT/"installed_source_manifest.json",{str(TOOL/"source"/path):sources[name] for name,path in FILES.items()})
    old=json.loads((PARENT/"summary.json").read_text())
    historical={s["mode"]:s["ordinary_ms"] for s in old if s["scenario"]=="broad_tag"}
    tax=match["ordinary_ms"]-graph["ordinary_ms"]
    proof={
        "correctness_before_timing":"Native ctest and protocol tests pass before run.py; sparse/unfilter32 functional replays precede Broad",
        "warmed_ordinary_counter_queries_before_timing":6000,
        "functional_counter_queries":192,
        "visited_backend_allocations":0,"visited_backend_conversions":0,
        "actual_mixed_pool_queries":128,"actual_cross_index_pool_queries":64,
        "generic_packed_mixed_queries":100,"generic_wide_mixed_queries":100,
        "stable_allocation_addresses":True,"result_dedup_remains_compact":True,
        "instrumentation_positive_control":"Init+generic INT_MAX promotion+growth =3 allocations,1 conversion",
        "single_probe_callback_once":"native fixtures and first-insert/reused real trace accounting; rehash copies key+flag",
        "full_row_oracle_including_visited":True,
        "all_selected_CSR_rows_complete":True,
        "native_cross_budget_fixture":"budget8 selected CSR completes; no next action after exhaustion",
        "actual_broad_cross_budget_actions":sum(r["budget_crossing_posting_actions"] for r in records),
        "exact_graph_match_ids_distances_heads_own_checked_queues_visited_native_admissions_SSD":True,
        "graph_matches_predecessor_exact_trajectory":True,
        "all_12_processes_match_predecessor_payloads_and_all_existing_trace_counters":True,
        "capture_off_on_ids_distances_SSD":True,
        "graph_metadata_free_unfilter_zero_matching":True,
        "own_only_alias_delete_tie_snapshot_and_rejected_aux_bridge_fixtures":True}
    write(OUTPUT/"compile_proof.json",proof)
    report={
        "milestone":HERE.name,"status":"COMPLETE_STOP_IDLE","promotion":False,
        "scope":"Allocation/storage-only isolated successor; fixed .01 and all predecessor policies retained",
        "root_cause":"Predecessor Reset selected compact then filtered enable selected wide, each freeing/reallocating; two visited-backend conversions/allocations every steady match/posting query",
        "source_references":refs,
        "native_ID_proof":{
            "SizeType":"int32_t","MaxSize":2147483647,
            "count":"GetNumSamples returns signed32 Dataset::R, valid physical IDs satisfy 0<=id<count",
            "highest_valid_ID_at_max_count":2147483646,"invalid_sentinel":2147483647,
            "encoded_highest":2147483647,"match_flag":2147483648,
            "validation":"EnableNativeMatch rejects negative count; insertion checks id>=0 && id<count before encoding",
            "build_load":"Build accepts SizeType vector count; stream/memory Dataset loaders read signed32 row header; BKT load checks sample/graph/deletion row consistency; Dataset::At enforces bounds; AddBatch bounded by capacity; cross locator explicitly rejects >=MaxSize",
            "caveat":"Not a proof of comprehensive malformed-file validation or overflow-safe legacy building at INT_MAX. Boundary fixtures exercise the actual storage API without allocating a multi-billion-row index. No valid native ID is excluded."},
        "storage":{
            "native_graph_match_posting_slot_bytes":4,"result_slot_bytes":4,
            "encoding":"low31 unsigned id+1, bit31 static match, zero empty; keys masked before comparison/rehash/hash",
            "generic":"Unqualified compact accepts 0..INT_MAX. Generic match starts packed, promotes only on INT_MAX to low32 key+bit32 uint64. Bound native mode never promotes. Generic wide allocation stays sticky across reset/modes until explicit Init.",
            "ownership":"One active array containing both native blocks; no simultaneous persistent compact/wide buffers, no separate match array; result/other tables not widened",
            "reset":"Clear nonempty allocation and query predicate/hooks; reset scalar match mode without allocation or conversion; following enable on empty changes interpretation only",
            "growth":"Both blocks rehashed, masks key and preserves flag, no callbacks, no retained pointer after growth",
            "dispatch":"No newly removed callback layer: existing match callback only on first insertion; reused-bit reads and rehash do not dispatch predicates. Native distance/result/own callbacks unchanged."},
        "semantics":{
            "predicate":"Exact predecessor posting OR own incl live collapsed aliases; immutable snapshot guards retained",
            "component_admission":"OR never replaces separate native posting/result/own checks",
            "gate":"Full physical ordinary row completes; d>0 and e<.01*d incl visited; no prescan",
            "budgets":"Ordinary per-edge cutoff and native queues/bridges unchanged; selected signed CSR completed across budget; no next action after exhaustion",
            "auxiliary":"Reject absent negative without insertion; later reevaluation allowed",
            "unfilter":"Native bypass; graph no matching/supplier metadata",
            "unsupported":"Concurrent maintenance, raw array mutation, changing predicate/index during one query; not general concurrency support"},
        "verification":proof,
        "instrumentation":{
            "scope":"Navigation visited backend arrays only, NOT zero query allocations overall",
            "allocation":"Successful backend new[] at Init/growth/retry/promotion; allocated_bytes sums those allocation payloads",
            "conversion":"Compact-to-wide backend conversion; reset or scalar packed-bit interpretation change NOT conversion",
            "resets":"Nonempty allocation cleared; no-op clear excluded",
            "mode_changes":"Enable/disable scalar match interpretation, intentionally not backend conversion",
            "ordinary_timing":"Diagnostics pointer null, counters not incremented, no full oracle",
            "proof":"Capture-off actual ordinary queries AFTER warmup and BEFORE timing; native hook diagnostics/oracle remain off",
            "memory":"Persistent payload bytes=two-block capacity*actual slot size; not allocator headers, whole process RSS or heaps",
            "occupancy":"Scanned outside timing after each proof query; load=occupied/total two-block capacity",
            "logical_probes":"Includes seed/ordinary/aux CheckAndSet; collision-slot reads unavailable, not conflated",
            "storage_u64_schema":["allocations","conversions","allocated_bytes_during_query","nonempty_clears","mode_changes",
                "slot_bytes","persistent_bytes","capacity","occupied","result_persistent_bytes","result_slot_bytes"]},
        "timing":{
            "ordinary_processes":6,"warmup":1000,"untimed_storage_proof_before_timing":1000,
            "measured":1000,"untimed_semantic_capture_after_timing":1000,
            "ordering":["graph","match","posting","posting","match","graph"],
            "sparse_unfilter":"32-query functional replays only; no sparse1000",
            "NProbe":[24],"topk":10,"offset":0,"NUMA_CPU_memory":2,"threads":1,
            "MaxCheck":2048,"HierarchyMaxCheck":512,"HierarchyInitialProbeRatio":.666666,"pages":15,
            "data_search_authority":"Frozen native INIs only; no environment overrides",
            "timed_body_sha256":json.loads((OUTPUT/"runtime.json").read_text())["timed_body_sha256"]},
        "summary":summary,
        "samecore":{"graph_match_exact_trace_tax_ms":tax,
            "graph_match_tax_runs_ms":[m-g for m,g in zip(match["ordinary_ms_runs"],graph["ordinary_ms_runs"])],
            "match_over_graph":match["ordinary_ms"]/graph["ordinary_ms"],
            "posting_over_graph":posting["ordinary_ms"]/graph["ordinary_ms"],
            "posting_changes_trajectory":True,"no_posting_causal_same_trace_claim":True},
        "historical_predecessor":{"path":str(PARENT),"report_sha256":sha(PARENT/"report.json"),
            "summary_sha256":sha(PARENT/"summary.json"),"sealed_files_verified":len(predecessor),
            "broad_ms":historical,"same_trace_tax_ms":historical["match"]-historical["graph"],
            "comparison":"Historical not fresh-paired; absolute differences not attributed solely to storage"},
        "protection":{"verified_files":len(json.loads((OUTPUT/"protection.json").read_text())),
            "production_dirty_sources_Release_modules_inputs_plots_operator_stop":True,
            "predecessor_sources_toolchains_results":True,"GettingStart":"parent-owned; not edited"},
        "limitations":["Two reversed repetitions, descriptive not statistical significance or universal speedup",
            "Only fixed Broad n24 ordinary timing; sparse/unfilter functional32",
            "A separate capture-off 1000-query storage proof follows the configured 1000 warmup before timing in each ordinary process; disclosed additional untimed phase, identical across modes",
            "No all-query allocation claim; callback/result/heap/trace allocations outside visited backend scope",
            "No collision-slot read counter; no isolated predicate-only latency",
            "Native max-ID is an arithmetic/storage fixture, not a physical INT_MAX-sized build",
            "Generic sticky-wide pool remains wide after actual INT_MAX match demand; unrelated tables remain compact",
            "Immutable read-only single-thread experiment; no concurrent updates"],
        "build_iteration":"Initial compile was stopped before timing while occupied-domain transition and UBSan boundary fixtures were strengthened. build_pass1 preserves its logs/installed header. Final source installed coherently and all core/harness/tests rebuilt before run.py.",
        "artifacts":{name:str(OUTPUT/name) for name in (
            "source_references.json","compile_proof.json","native-fixtures-detail.log","protocol-tests.log",
            "build_commands.json","runtime.json","operations.json","ordinary_plan.json","small_replay.json",
            "source_tree_manifest.json","compiler_metadata_manifest.json","installed_source_manifest.json")},
        "stop":"IDLE; no further optimization, campaigns, promotion or commits"}
    write(OUTPUT/"report.json",report)
    lines=["# Native visited storage — COMPLETE / STOP / IDLE","",
        "Storage-only successor; threshold .01 and predecessor predicate/traversal retained.",
        "Steady Reset-induced compact/wide churn removed; native match slot width is **4 bytes**.",
        "Exactly six Broad ordinary processes; no sparse1000 campaign.","",
        "| Mode | Mean ms | r1 ms | r2 ms | QPS at mean | r1 QPS | r2 QPS | Recall@10 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for s in summary:
        lines.append(f"| {s['mode']} | {s['ordinary_ms']:.9f} | {s['ordinary_ms_runs'][0]:.9f} | "
            f"{s['ordinary_ms_runs'][1]:.9f} | {s['qps_at_mean_ms']:.3f} | "
            f"{s['qps_runs'][0]:.3f} | {s['qps_runs'][1]:.3f} | {s['recall_at_10']:.4f} |")
    lines+=["",f"Exact same-core graph/match tax: **{tax:.9f} ms**. Posting is not same-trace.",
        "Predecessor timings are historical, not fresh-paired. Two repetitions are descriptive only.","",
        "| Mode | Slot B | Persistent B | Capacity | Occupied mean | Load | Result B | Alloc/query | Convert/query |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for s in summary:
        t=s["storage_mean"]
        lines.append(f"| {s['mode']} | {t['slot_bytes']:.0f} | {t['persistent_bytes']:.0f} | "
            f"{t['capacity']:.0f} | {t['occupied']:.3f} | {s['storage_loadfactor_mean']:.6f} | "
            f"{t['result_persistent_bytes']:.0f} | 0 | 0 |")
    lines+=["","6000 capture-off warmed actual Broad proof queries before timing: zero visited-backend allocations/conversions.",
        "128 actual mixed graph/match/posting/unfilter and 64 cross-index queries reuse stable allocations.",
        "Positive instrumentation control sees Init+promotion+growth =3 allocations and1 conversion.",
        "**Not** zero query allocations overall; measurements cover navigation backend arrays only.","",
        "| Mode | Logical probes | First match | Reused bit | Physical d | Effective e | Checked | Queue offers | Native result calls | CSR members |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for s in summary:
        m=s["match_mean"];n=s["native_mean"];o=s["operator_mean"]
        values=[m["visited_probes"],m["first_match_evaluations"],m["reused_bit_reads"],m["physical_degree"],
            m["effective_degree"],n["checked"],n["queue_offers"],o["all_native_result_predicate_calls"],n["csr_members"]]
        lines.append("| "+s["mode"]+" | "+" | ".join(f"{v:.3f}" for v in values)+" |")
    lines+=["","Graph effective degree is unmeasured (disabled), not no matches. Probes are logical; collision-slot reads unavailable.",
        "Raw payloads/SSD/queue/checked/distances and all ranges/work are preserved in records and binary traces.",
        "Exact graph/match trajectory and all twelve predecessor payload/trace/counter parity checks passed; off/on captures agree.",
        "Full-row oracle, alias/delete/tie/own-only, rejected auxiliary bridge, snapshot and CSR-cross-budget fixtures passed.","",
        "## ID domain / fallback","",
        "Signed32 native sample count bounds valid IDs at INT_MAX-1. Unsigned id+1 fits low31; bit31 stores match.",
        "Explicit validated native mode rejects INT_MAX sentinel; generic mode still accepts INT_MAX and promotes only then.",
        "Generic wide storage is sticky (one persistent array), no mode allocation oscillation or unrelated result-table widening.",
        "Maximum-ID fixture exercises real storage arithmetic without allocating an INT_MAX-row index; legacy malformed loaders are not comprehensively hardened.","",
        "See `source_references.json` for exact source locations, `report.json` for complete proof/protocol/caveats,",
        "and `milestone_manifest.json` / `report.sha256.json` for the seal. Production, index, predecessor and OPERATOR_STOP unchanged.",
        "No commits, downloads, plots, promotion or public GettingStart edits. **IDLE**.",""]
    (OUTPUT/"REPORT.md").write_text("\n".join(lines))
    write(OUTPUT/"STOP_IDLE.json",{"resume_allowed":False,"ordinary_processes_completed":6,
                                  "promotion":False,"reason":"Bounded storage successor completed"})
    write(OUTPUT/"report.sha256.json",{name:sha(OUTPUT/name) for name in ("report.json","summary.json","REPORT.md")})
    manifest={}
    for root in (HERE,OUTPUT):
        for path in root.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.name!="milestone_manifest.json":
                manifest[str(path)]=sha(path)
    for path in FILES.values(): manifest[str(TOOL/"source"/path)]=sha(TOOL/"source"/path)
    for path in [TOOL/"harness/postfilter-bench",TOOL/"harness/native-tests",
                 TOOL/"harness/storage-boundary-tests",*(TOOL/"source/Release").glob("*.a")]:
        manifest[str(path)]=sha(path)
    write(OUTPUT/"milestone_manifest.json",manifest)
    print(json.dumps({"report_sha256":sha(OUTPUT/"report.json"),"summary_sha256":sha(OUTPUT/"summary.json"),
                      "sealed_files":len(manifest),"samecore":report["samecore"],"stop":"IDLE"},indent=2))

if __name__=="__main__": main()
