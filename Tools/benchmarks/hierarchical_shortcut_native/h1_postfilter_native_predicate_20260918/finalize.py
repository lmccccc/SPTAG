"""Seal the clean H1 postfilter correction, never the nearest24 over-restoration."""
import json
from prepare import HERE,TOOL,OUTPUT,PARENT,FILES,sha,write,protect
from run import require

def main():
    require(not (OUTPUT/"milestone_manifest.json").exists(),"Already sealed")
    summary=json.loads((OUTPUT/"summary.json").read_text())
    records=json.loads((OUTPUT/"operations.json").read_text())["records"]
    small=json.loads((OUTPUT/"small_replay.json").read_text())["records"]
    reference=json.loads((OUTPUT/"original_reference_parity.json").read_text())
    sparse=json.loads((OUTPUT/"sparse_summary.json").read_text())
    sparse_records=json.loads((OUTPUT/"sparse_operations.json").read_text())
    head_reference=json.loads((OUTPUT/"head_reference_proof.json").read_text())
    require(head_reference["native_head_ids_distances_exact"],"Original selected-head mismatch")
    require(len(records)==6 and len(reference)==3,"Bounded protocol incomplete")
    require(len(sparse_records)==4,"Fixed sparse pair incomplete")
    for rep in (1,2):
        g,m=(next(r for r in records if r["mode"]==mode and r["label"].endswith(f"_r{rep}"))
             for mode in ("graph","match"))
        require(g["payloads"]==m["payloads"] and g["trajectory_hashes"]==m["trajectory_hashes"],
                "Graph/match native trajectory mismatch")
    for r in records+small+sparse_records:
        require(r["csr_partial_rows"]==0,"Partial selected CSR")
        require(r["storage_max"]["allocations"]==r["storage_max"]["conversions"]==0,"Backend churn")
        require(r["storage_min"]["slot_bytes"]==r["storage_max"]["slot_bytes"]==4,"Native slot widened")
    for name,path in FILES.items(): require(sha(HERE/name)==sha(TOOL/"source"/path),"Installed source mismatch")
    for path,digest in json.loads((PARENT/"milestone_manifest.json").read_text()).items():
        require(sha(path)==digest,"Predecessor modified: "+path)
    protect()
    fixture=(OUTPUT/"native-fixtures-detail.log").read_text()
    for text in ("PASS native alias/tie/delete","PASS single first evaluation",
                 "PASS signature before representative/CSR","128 queries zero backend",
                 "PASS UBSan","CollectHierarchyHeadSignatures includes own tags",
                 "closest24 unmatched","native_preSSD_own=1 final_exact_own=1 supplemental_heap=0"):
        require(text in fixture,"Missing native proof "+text)
    g,m,p=(next(s for s in summary if s["mode"]==mode) for mode in ("graph","match","posting"))
    report={
        "milestone":HERE.name,"status":"COMPLETE_STOP_IDLE","promotion":False,
        "baseline":json.loads((OUTPUT/"baseline_identity.json").read_text()),
        "own_support":json.loads((OUTPUT/"actual_own_support_proof.json").read_text()),
        "architecture":{
            "distance_only_frontier":True,"H1_results_postfiltered":True,"result_filter_passed_to_BKT":True,
            "supplemental_own_heap_in_H1":False,"match_alias_walk":False,
            "match_liveness_version_VID_walk":False,"match_CheckValidPosting":False,
            "predicate":"existing compiled anchor OR LimitedTagSupport::Supports",
            "unanchored":"existing unrestricted may-match; no inferred sparse fraction from unknown attributes",
            "not_exact_record_truth":True,"native_exact_final_filter_retained":True,
            "full_physical_row_ratio_includes_visited":True,"fixed_ratio":.01,
            "ordinary_budget_and_frontier":"authenticated native SearchIndexWithResultFilter, no traversal filter",
            "result_bit_reuse":"same-ID fresh-neighbor bit already in local scope; no extra lookup/cache",
            "selected_CSR_full_row":True,"unfilter_true_native_bypass":True,
            "native_slot_bytes":4,"generic_INT_MAX_fallback":"unchanged lazy sticky uint64",
            "warm_backend_allocations_conversions":0,
            "new_own_alias_liveness_qualification_callbacks":0},
        "summary":summary,"sparse_summary":sparse,"functional32":small,"original_reference32":reference,
        "original_selected_head_reference32":head_reference,
        "bit_only_same_trajectory_tax_ms":m["ordinary_ms"]-g["ordinary_ms"],
        "bit_only_over_graph":m["ordinary_ms"]/g["ordinary_ms"],
        "posting_over_graph":p["ordinary_ms"]/g["ordinary_ms"],
        "counter_schema":{
            "admission.u64":["selected H1 heads","matching H1 heads","actual result predicate evaluations",
                             "same-ID local bit reads for result admission"],
            "legacy_nativePredicateCalls":"logical result checks = evaluations + local bit reads, not actual callback count",
            "first_match_evaluations":"one initialization per inserted physical visited ID",
            "rejected_auxiliary":"negative uninserted candidates may be evaluated again; no negative cache",
            "new_own_liveness_alias_qualification":"absent from bit initializer; original result-level checks still run"},
        "fixture_own_only":{"physical_head":42704,"canonical_VID":264103,"posting_records":0,
            "modes":["graph","match","posting"],"selected_H1_preSSD_and_final_exact_own":True},
        "architecture_acceptance":True,
        "native_level_latency_achieved":False,
        "validation":{
            "native_and_UBSan":"ctest --test-dir "+str(TOOL/"harness")+" --output-on-failure",
            "protocol":"python3 "+str(HERE/"test_protocol.py"),
            "original_source_authentication":"python3 "+str(HERE/"provenance.py"),
            "original_selected_heads":"python3 "+str(HERE/"verify_head_reference.py"),
            "original_payloads":"Broad/sparse32 H1 IDs/distances equal authentic result-filter helper; unfilter32 full IDs/distances/SSD equal original",
            "own_only_fixture":"actual immutable empty-posting head, selected H1 and native pre-SSD own record and final exact VID checked in all modes",
            "samecore_raw_trace":"graph/match IDs/distances/heads/checked/queues/visited/distance sequence/SSD equal",
            "capture_off_on":"all final IDs/distances/SSD work exact",
            "real_ratio_oracle":"every complete captured ordinary row",
            "native_fixtures":str(OUTPUT/"native-fixtures-detail.log")},
        "timing":{"ordinary_processes":10,"broad_processes":6,"sparse_processes":4,
            "warmup":1000,"measured":1000,
            "untimed_storage_proof":1000,"untimed_semantic_replay":1000,
            "order":["graph","match","posting","posting","match","graph"],
            "NUMA_CPU_memory":2,"thread":1,"nprobe":24,"topk":10,"MaxCheck":2048,
            "HierarchyMaxCheck":512,"HierarchyInitialProbeRatio":.666666,"pages":15,
            "no_profile_or_threshold_search":True},
        "limitations":[
            "Pre-supplier authenticated source contains historical storage/attribute changes; not pristine upstream.",
            "Original reference exports postfiltered H1 IDs/distances, not raw visited traces; same-core fixtures/captures verify navigation parity.",
            "Inherited cumulative head diagnostic frames are corrected to query-local frames outside normal timing.",
            "Earlier postfilter graph had a supplementary own heap; over-restoration had unfiltered H1 output. Neither is this clean baseline.",
            "The unified Supports predicate includes matching empty-posting own heads, unlike old evaluatePosting; final validity/filtering remains native.",
            "Routing membership is summary may-match, not exact predicate population/selectivity/liveness.",
            "Numeric/unanchored routing remains unrestricted; exact final native DNF filtering remains.",
            "This bounded input set tests categorical and unfiltered queries; numeric/DNF mapping is source-reviewed, not measured here.",
            "Read-only static snapshot only, no concurrency guarantee.",
            "Two repetitions descriptive; sparse runs show noticeable runtime variation; no equal-recall speed claim.",
            "One fixed sparse pair after Broad; unfilter functional32 only.",
            "H1 requests 24 matching heads but may underfill at the native budget; actual SSD posting count can be smaller.",
            "Unequal recall across old/new or graph/posting cases is not a same-recall speed comparison; no promotion."] }
    write(OUTPUT/"report.json",report)
    lines=["# Clean H1 result-postfilter — STOP / IDLE","",
        "Matching H1 output through native SearchIndexWithResultFilter; nonmatching nodes remain graph bridges.",
        "All three controls have the same attribute predicate and NO supplemental own heap.",
        "No eager own/alias/liveness/VID qualification or bit-time CheckValidPosting.",
        "Match caches native anchor support; final exact record filtering and selected-head handling stay native.","",
        "| Mode | r1 ms | r2 ms | Mean ms | QPS | Recall@10 |",
        "|---|---:|---:|---:|---:|---:|"]
    for s in summary:
        lines.append(f"| {s['mode']} | {s['ordinary_ms_runs'][0]:.9f} | {s['ordinary_ms_runs'][1]:.9f} | "
                     f"{s['ordinary_ms']:.9f} | {s['qps_at_mean_ms']:.3f} | {s['recall_at_10']:.4f} |")
    lines+=["","## Fixed sparse pair (after Broad)","",
        "| Mode | r1 ms | r2 ms | Mean ms | QPS | Recall@10 |",
        "|---|---:|---:|---:|---:|---:|"]
    for s in sparse:
        lines.append(f"| {s['mode']} | {s['ordinary_ms_runs'][0]:.9f} | {s['ordinary_ms_runs'][1]:.9f} | "
                     f"{s['ordinary_ms']:.9f} | {s['qps_at_mean_ms']:.3f} | {s['recall_at_10']:.4f} |")
    lines+=["",f"Same-trajectory graph/match delta: {report['bit_only_same_trajectory_tax_ms']:.9f} ms.",
        "Authentic native result-filter helper H1 IDs/distances match Broad/sparse32; original full unfilter32 results/work match.",
        "Regression fixture: nearest24 are nonmatching, but native H1 postfilter returns 24 farther matching heads.",
        "Actual empty-posting own-head fixture verifies selected H1 -> native pre-SSD own record -> exact final result, without supplemental heap.",
        "Real fixture: physical H1 42704, canonical VID 264103, zero posting records, all three modes pass.",
        "All 160091 actual H1 own tags are present in native support slots; empty-posting-PS signature fixture also passes.",
        "No added own/alias/liveness qualification callbacks; 4-byte native visited with zero warmed backend allocations/conversions.",
        "", "| Mode | First predicate | Reused bit | H1 distances | Checked | Auxiliary CSR | Recall@10 |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for s in summary:
        n=s["native_mean"];a=s["match_mean"]
        lines.append(f"| {s['mode']} | {a['first_match_evaluations']:.3f} | {a['reused_bit_reads']:.3f} | "
            f"{n['head_distances']:.3f} | {n['checked']:.3f} | {n['csr_members']:.3f} | {s['recall_at_10']:.4f} |")
    lines+=["","| Scenario / mode | Selected H1 | Matching H1 | SSD postings | Actual result predicate calls | Local bit reads for admission |",
            "|---|---:|---:|---:|---:|---:|"]
    for s in summary+sparse:
        a=s["h1_admission_mean"]
        lines.append(f"| {s['scenario']} / {s['mode']} | {a['selected_heads']:.3f} | {a['matching_heads']:.3f} | "
            f"{s['ssd_mean'][0]:.3f} | {a['result_predicate_evaluations']:.3f} | {a['result_predicate_bit_reads']:.3f} |")
    lines+=["","## Boundaries","",*("- "+x for x in report["limitations"]),"",
        "Exact commands: build_commands.json, each process command.json, README.md. No production/plots/old evidence changes."]
    (OUTPUT/"REPORT.md").write_text("\n".join(lines)+"\n")
    manifest={}
    for root in (HERE,OUTPUT):
        for path in root.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.name not in (
                "milestone_manifest.json","report.sha256.json","completion.json"):
                manifest[str(path)]=sha(path)
    for path in list((TOOL/"harness").glob("*"))+list((TOOL/"source/Release").glob("*.a")):
        if path.is_file():manifest[str(path)]=sha(path)
    write(OUTPUT/"milestone_manifest.json",manifest)
    for path,digest in manifest.items():require(sha(path)==digest,"Seal mismatch")
    write(OUTPUT/"report.sha256.json",{"report.json":sha(OUTPUT/"report.json"),
                                     "REPORT.md":sha(OUTPUT/"REPORT.md"),
                                     "manifest":sha(OUTPUT/"milestone_manifest.json")})
    print("STOP_IDLE",sha(OUTPUT/"report.json"),"sealed",len(manifest))

if __name__=="__main__":main()
