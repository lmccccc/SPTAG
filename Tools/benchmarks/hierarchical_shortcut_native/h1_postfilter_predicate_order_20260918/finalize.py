"""Seal original predicate-order correction; no further measurements."""
import json
import subprocess
from prepare import HERE,TOOL,OUTPUT,PARENT,FILES,sha,write,protect
from run import require

def main():
    require(not (OUTPUT/"milestone_manifest.json").exists(),"Already sealed")
    summary=json.loads((OUTPUT/"summary.json").read_text())
    records=json.loads((OUTPUT/"operations.json").read_text())["records"]
    small=json.loads((OUTPUT/"small_replay.json").read_text())["records"]
    proof=json.loads((OUTPUT/"predicate_order_proof.json").read_text())
    require(len(records)==4 and len(summary)==2,"Bounded four-process protocol incomplete")
    require(not (OUTPUT/"sparse_summary.json").exists(),"Unauthorized sparse1000 measurement")
    require(proof[0]["full_SPANN_capture_heads_and_callback_counts_exact"],"Untimed/full capture mismatch")
    for rep in (1,2):
        g,m=(next(r for r in records if r["mode"]==mode and r["label"].endswith(f"_r{rep}"))
             for mode in ("graph","match"))
        require(g["payloads"]==m["payloads"] and g["trajectory_hashes"]==m["trajectory_hashes"],
                "Graph/match trajectory mismatch")
    for r in records+small:
        require(r["csr_partial_rows"]==0,"Partial selected CSR")
        require(r["storage_max"]["allocations"]==r["storage_max"]["conversions"]==0,"Backend churn")
        require(r["storage_min"]["slot_bytes"]==r["storage_max"]["slot_bytes"]==4,"Native slot widened")
    for name,path in FILES.items():
        require(sha(HERE/name)==sha(TOOL/"source"/path),"Installed source mismatch")
    for path,digest in json.loads((PARENT/"milestone_manifest.json").read_text()).items():
        require(sha(path)==digest,"Predecessor changed: "+path)
    protect()
    symbols=subprocess.check_output(["nm","-C",str(TOOL/"harness/postfilter-bench")],text=True)
    require("OrderTrace::" not in symbols,"Diagnostic objects linked into ordinary binary")
    fixture=(OUTPUT/"native-fixtures-detail.log").read_text()
    for text in ("PASS native alias/tie/delete","PASS single first evaluation",
                 "PASS signature before representative/CSR","128 queries zero backend",
                 "PASS UBSan","CollectHierarchyHeadSignatures includes own tags",
                 "closest24 unmatched","native_preSSD_own=1 final_exact_own=1 supplemental_heap=0",
                 "all 127 fresh ordinary neighbors evaluated despite distance above output worstDist"):
        require(text in fixture,"Missing native proof "+text)
    graph,bit=(next(s for s in summary if s["mode"]==mode) for mode in ("graph","match"))
    delta=bit["ordinary_ms"]-graph["ordinary_ms"]
    percent=100*delta/graph["ordinary_ms"]
    paired=[b-g for g,b in zip(graph["ordinary_ms_runs"],bit["ordinary_ms_runs"])]
    report={
        "milestone":HERE.name,"status":"COMPLETE_STOP_IDLE","promotion":False,
        "correction":{
            "false_explanation_retracted":"Native original does NOT evaluate only221.616 predicates/query.",
            "old_artifact":str(PARENT),"old_report_sha256":sha(PARENT/"report.json"),
            "old_definition":"H1 result postfilter WITH nonnative output-distance predicate shortcut",
            "old_graph_predicate_calls_per_query":221.616,
            "old_same_trace_tax_ms":0.1082624675,
            "new_definition":"authenticated original predicate/admission order, same Supports predicate, no own heap",
            "removed":"distance>worstDist early return inside helper and its special collapsed exception",
            "also_restored":"original alias traversal-filter dedup guard, inactive because traversal filter is empty",
            "not_algorithm_speedup":"Restoring original work is not a performance improvement; no old/new timing splicing"},
        "source_identity":json.loads((OUTPUT/"baseline_identity.json").read_text()),
        "bounded_source_audit":json.loads((OUTPUT/"predicate_order_audit.json").read_text()),
        "predicate_order_proof":proof,"summary":summary,
        "fair_bit_tax_ms":delta,"fair_bit_tax_percent":percent,"paired_deltas_ms":paired,
        "attribution":{
            "established":"Old baseline skipped1671.271 original predicate calls/query; fresh baseline restores them.",
            "current_extra_predicate_calls_per_query":proof[0]["extra_actual_predicate_calls_per_query"],
            "remaining_cpu_residence":"UNATTRIBUTED: no timing attribution to individual predicates, bit probes or row counters",
            "not_zero_overhead":"One4-byte slot/one probe does not prove zero instruction or callback cost.",
            "no_speculative_fix":"No extra cache, tree propagation, profiling campaign or algorithm change"},
        "counter_scope":{
            "actual_callbacks":"visited initialization plus remaining native result callbacks; not their logical sum",
            "logical_result_checks":"original helper predicate positions, evaluated or locally reused",
            "excluded_capture_only":"full-row predicate oracle and selected-head verification; never in normal timed queries"},
        "architecture":{
            "H1_result_postfilter":True,"nonmatching_bridges_and_distance_frontier":True,
            "original_native_result_checks_and_final_exact_filter":True,
            "supplemental_own_heap":False,"new_eager_own_liveness_alias_or_validity_in_bit":False,
            "same_slot_bytes":4,"warmed_backend_allocations_conversions":0,
            "bit_only_auxiliary_CSR_signature_representatives":0,
            "same_local_bit_reused_without_second_lookup":True,
            "tree_remaining_repeat":"No local bit survives to popped admission; keep native predicate rather than add lookup/cache.",
            "routing_predicate":"native anchor support may-match, not exact record truth",
            "fixed_posting_ratio":0.01,"ordinary_budget_and_full_selected_CSR":"unchanged"},
        "functional32":small,
        "original_reference32":json.loads((OUTPUT/"original_reference_parity.json").read_text()),
        "validation":{
            "native_fixtures":"3/3 native, UBSan and real empty-posting own-head tests",
            "protocol":"9/9 tests",
            "ordered":"untouched authenticated library callback sequence; separate diagnostic original/current full event equality",
            "samecore":"graph/bit results, H1 heads, distance/visited/frontier/SSD trace exact",
            "capture_off_on":"final IDs/distances/SSD work exact",
            "no_trace_code_in_timing_binary":True,
            "commands":[f"python3 {HERE}/test_protocol.py",f"python3 {HERE}/verify_order.py",
                        f"ctest --test-dir {TOOL}/harness --output-on-failure"]},
        "protocol":{"ordinary_order":["graph","match","match","graph"],"ordinary_processes":4,
            "warmup":1000,"measured":1000,"thread":1,"NUMA_CPU_memory":2,"nprobe":24,
            "topk":10,"MaxCheck":2048,"HierarchyMaxCheck":512,"HierarchyInitialProbeRatio":0.666666,
            "pages":15,"sparse_unfilter_functional_queries":32,
            "diagnostic_only_replay":"Broad1000 and sparse32; clean original, diagnostic original, graph, bit; never timed"},
        "limitations":[
            "Only two reversed repetitions: descriptive paired results, no significance claim.",
            "The remaining measured bit cost is not apportioned to CPU categories.",
            "Actual SIFT ordered replays have zero alias callbacks; alias/tie/deletion behavior is additionally covered by native fixtures.",
            "Reference is authenticated source-compatible pre-supplier H1 postfilter, not pristine upstream SPANN.",
            "Native Supports is routing may-match; numeric/DNF exact filters retained, not newly benchmarked.",
            "Static read-only snapshot only; no concurrency or universal performance claim.",
            "Historical distance-shortcut measurements remain frozen; they are not the original-order baseline."]}
    write(OUTPUT/"report.json",report)
    lines=["# Original predicate-order correction - STOP / IDLE","",
        "The earlier claim that native H1 postfilter evaluates only221.616 predicates/query was false.",
        "That graph control retained a nonnative distance>worstDist early return. Output equality concealed the work difference.",
        "Both graph and bit now use original admission order, without own heap or eager own/liveness/alias qualification.","",
        "| Mode | r1 ms | r2 ms | Mean ms | QPS | Recall@10 |","|---|---:|---:|---:|---:|---:|"]
    for s in summary:
        lines.append(f"| {s['mode']} | {s['ordinary_ms_runs'][0]:.9f} | {s['ordinary_ms_runs'][1]:.9f} | "
                     f"{s['ordinary_ms']:.9f} | {s['qps_at_mean_ms']:.3f} | {s['recall_at_10']:.4f} |")
    lines+=["",f"Fresh fair bit delta: **{delta:.9f} ms ({percent:.3f}%)**. Paired deltas: {paired}.",
        "Do not subtract historical timings to assign the shortcut a duration. Restoring native work is not an algorithm speedup.",
        "Remaining CPU cost is UNATTRIBUTED; callback counts are not a time breakdown.","",
        "| Predicate work / query | Original-order graph | Bit |","|---|---:|---:|",
        f"| Total actual callbacks | {proof[0]['native_result_predicate_calls_per_query']:.3f} | "
        f"{proof[0]['bit_total_actual_predicate_calls_per_query']:.3f} |",
        f"| Ordinary fresh-neighbor callbacks | {proof[0]['native_callbacks_by_stage']['ordinary']:.3f} | "
        f"{proof[0]['bit_initializations_by_stage']['ordinary']:.3f} |",
        f"| Tree visited initialization | 0 | {proof[0]['reconciliation_per_query']['tree_initializations']:.3f} |",
        f"| Popped-head callbacks | {proof[0]['native_callbacks_by_stage']['popped_head']:.3f} | "
        f"{proof[0]['bit_remaining_callbacks_by_stage']['popped_head']:.3f} |",
        f"| Local bit reuse at admission (not callbacks) | 0 | {proof[0]['bit_local_value_admission_reuses_per_query']:.3f} |",
        "", "Tree bit initialization is72.910 initial +1.660 continuation. Of these74.570,",
        "50.930 never undergo a native result predicate;23.640 later repeat at popped admission with no local bit available.",
        "No ordinary fresh-neighbor predicate is computed twice. No secondary lookup/cache is added.",
        "The original and new graph execute1671.271 predicates above the output distance bound; the old shortcut skipped them.",
        "", "All native helper inputs, ordered predicate values/admission decisions, visited and distance events match.",
        "The independent unmodified original library also matches callback order. Full SPANN capture agrees with that reference.",
        "Both controls output24 matching H1 heads and23.979 SSD postings/query. Bit-only CSR/signature/representative work is zero.",
        "Visited uses4-byte slots,524288 bytes/131072 slots,3288.875 probes/query, load1.483%;",
        "bit first initialization1943.817 and visited cache hits1345.058/query. Warmed backend allocation/conversion is zero.",
        "", "Native fixtures preserve farther-matching-head postfilter, real empty-posting own head42704/VID264103,",
        "native aliases/deletion, single-bit reset/rehash/generic IDs, negative auxiliary and signature/full-CSR behavior.",
        "Sparse/posting and unfilter were32-query functional checks only; no new sparse1000 or profiling campaign.",
        "", "## Independent checks","", "```bash",*report["validation"]["commands"],"```","",
        "## Limitations","",*("- "+x for x in report["limitations"]),"",
        "Source refs: BKTIndex.cpp:558-600 helper; :887-989 ordinary distance/admission; :1647 bit initializer.",
        "Exact commands/hashes: build_commands.json, order_build_commands.json, per-process command.json, runtime.json.",
        "Production, indexes, predecessor evidence, plots and OPERATOR_STOP are unchanged."]
    (OUTPUT/"REPORT.md").write_text("\n".join(lines)+"\n")
    manifest={}
    for root in (HERE,OUTPUT,TOOL/"order_diagnostic"):
        for path in root.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.name not in (
                    "milestone_manifest.json","report.sha256.json","completion.json"):
                manifest[str(path)]=sha(path)
    for path in list((TOOL/"harness").glob("*"))+list((TOOL/"source/Release").glob("*.a")):
        if path.is_file(): manifest[str(path)]=sha(path)
    write(OUTPUT/"milestone_manifest.json",manifest)
    for path,digest in manifest.items(): require(sha(path)==digest,"Seal mismatch")
    write(OUTPUT/"report.sha256.json",{"report.json":sha(OUTPUT/"report.json"),
        "REPORT.md":sha(OUTPUT/"REPORT.md"),"manifest":sha(OUTPUT/"milestone_manifest.json")})
    print("STOP_IDLE",sha(OUTPUT/"report.json"),"sealed",len(manifest))

if __name__=="__main__":main()
