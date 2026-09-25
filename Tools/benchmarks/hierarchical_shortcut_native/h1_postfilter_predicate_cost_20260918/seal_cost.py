"""Persist bounded cost evidence without claiming complete residual attribution."""
import json
import subprocess
from prepare import HERE,TOOL,OUTPUT,PARENT,FILES,sha,write,protect

def main():
    assert not (OUTPUT/"milestone_manifest.json").exists(),"Already sealed"
    runs=json.loads((OUTPUT/"cost_runs.json").read_text())
    cost=json.loads((OUTPUT/"cost_attribution.json").read_text())
    assert len(runs)==10 and sum(r["kind"]=="profile" for r in runs)==2
    for name,path in FILES.items(): assert sha(HERE/name)==sha(TOOL/"source"/path),name
    for path,digest in json.loads((PARENT/"milestone_manifest.json").read_text()).items():
        assert sha(path)==digest,path
    protect()
    original=TOOL.parent/"matched_baseline_20260917/original"
    auth=json.loads((original/"authentication.json").read_text())
    native="AnnService/src/Core/BKT/BKTIndex.cpp"
    assert sha(original/"source"/native)==auth["verified"][native]
    differences={}
    for name in ("BKTIndex.cpp","FullHooks.h","MatchedBench.cpp"):
        old=HERE.with_name(PARENT.name)/name
        diff=subprocess.run(["git","--no-pager","diff","--no-index",str(old),str(HERE/name)],
                            capture_output=True,text=True)
        assert diff.returncode in (0,1)
        output=OUTPUT/(name+".diagnostic.diff");output.write_text(diff.stdout)
        differences[name]={"parent_sha256":sha(old),"diagnostic_sha256":sha(HERE/name),"diff":str(output)}
    source_audit={
        "original_BKT_sha256":auth["verified"][native],"original_helper_lines":[552,570],
        "diagnostic_changes":differences,
        "predicate_chain_graph":"BKT admitFilteredResult -> std::function -> SPANN lambda#11 -> compiled anchor iteration -> LimitedTagSupport::Supports",
        "predicate_chain_bit":"WorkSpace CheckAndSetMatch -> slot miss -> m_matchPredicate std::function -> BKT matchPhysical -> same resultFilter std::function -> same Supports",
        "sampled_memory":"PCs at LimitedTagSupport.h:951 show base support-slot compares; separate node-ID-indexed tags array, not distance vector payload.",
        "within_row":"Two base support slots from native persisted configuration; sequential within row, graph-dependent head access across rows. Expansion path remains native.",
        "capture_off_updates":["BKTIndex.cpp:584-586 ordinaryObservation checks/passes",
            "BKTIndex.cpp:882-886 physical degree/eligible/eligibleVisited",
            "BKTIndex.cpp:1004-1057 decision/setup/gate"],
        "counter_semantics":"Physical d/e retained; legacy checks/passes not used by sparse decision. No counters disabled.",
        "native_marker":"BKTIndex.cpp:931 / PC0xd3d58 is original target collapsed-group marker, not new alias eligibility",
        "sampled_specialization":"Native BKT template uses AlwaysTrue for notDeleted and metadata checkFilter in this no-deletion/nonmetadata H1 case; CheckDup remains.",
        "data_final_filter":"SPANN final exact categorical/numeric/DNF filtering and SSD/own liveness remain native",
        "hardware_counters":"Unavailable/restricted; permissions unchanged. No cache-miss/DRAM/branch-misprediction conclusion."}
    write(OUTPUT/"source_cost_audit.json",source_audit)
    means=cost["control_means"];delta=cost["controlled_deltas"]
    share=delta["actual_predicate_path"]["cpu_ms"]/delta["bit_over_shortcut"]["cpu_ms"]
    report={"status":"COMPLETE_BOUNDED_DIAGNOSIS_STOP_IDLE","optimization_implemented":False,
        "production_promoted":False,"same_core_binary":str(TOOL/"harness/postfilter-bench"),
        "binary_sha256":sha(TOOL/"harness/postfilter-bench"),
        "controls":{"A":"historical distance shortcut, predicate only when competitive",
            "Aprime":"A plus actual same predicate before shortcut, still no later admission on distant candidates",
            "B":"original predicate/admission order","bit":"B plus visited bit and full-row accounting"},
        "fresh_only":True,"no_cross_run_timing_splicing":True,
        "history":{"distance_shortcut_report":str(PARENT.parent/"h1_postfilter_native_predicate_20260918/report.json"),
            "original_order_report":str(PARENT/"report.json"),"original_order_report_sha256":sha(PARENT/"report.json"),
            "false_claim_retracted":"Native original does not evaluate only222 predicates/query",
            "restoration_not_optimization":True},
        "cost":cost,"source_audit":source_audit,
        "principal_conclusion":{
            "actual_predicate_path_cpu_ms":delta["actual_predicate_path"]["cpu_ms"],
            "share_of_fresh_bit_over_shortcut_gap":share,
            "interpretation":"Measured implemented callback/support path, not the arithmetic comparison alone.",
            "remaining_bit_over_Aprime_cpu_ms":delta["bit_over_predicate_only"]["cpu_ms"],
            "remaining_causal_attribution":"UNRESOLVED; do not assign the entire residual to extra predicates or hash bits.",
            "PC_evidence":"Larger residence at routing support and original graph-marker PCs, with smaller distance-kernel residence despite identical work; small direct first-dispatch and physical-degree samples.",
            "no_false_alias_story":"Original marker remains necessary; no new eager alias eligibility was introduced.",
            "next_step_not_executed":"Review capture-off legacy row checks/passes and callback plumbing; current samples do not justify predicting a speedup or optimizing before architecture review."},
        "validation":{"native_and_UBSan_tests":3,"protocol_tests":9,"cost_evidence_tests":5,
            "all_ten_ID_distance_navigation_SSD_traces_equal":True,"recall_at_10":.9153,
            "selected_matching_H1":24,"final_SSD_postings_per_query":23.979,
            "auxiliary_CSR_signature_representatives":0,"visited_slot_bytes":4,
            "warmed_backend_allocations_conversions":0,
            "real_predicate_compiler_execution":"side-effecting native A-prime regression, actual untimed callback counts, sampled predicate PCs",
            "source_debug_mapping":"all optimized executable sections and symbol tables match; debug executable never run"},
        "limitations":["Only two reversed CPU-clock-only repetitions and one sampled window per relevant control.",
            "A-prime isolates the implemented predicate path including existing dispatch/guard scaffolding, not a single compare instruction.",
            "B-A-prime paired CPU differences vary from negative to positive; do not claim a precise admission-only cost.",
            "PC residence is gross sampling evidence, not causal component execution time; positive and negative shifts can reflect interactions or aliasing.",
            "Actual delivered sampling cadence is about1ms despite requested200us.",
            "Zero buffer overflow/wrong-thread samples; timer coalescing not quantified.",
            "Static read-only SIFT1M Broad case; no sparse1000, tuning, production changes or general performance claim."]}
    write(OUTPUT/"report.json",report)
    lines=["# Real predicate cost controls - bounded diagnosis / STOP","",
        "**Restoring original order is not an optimization or CPU attribution.** The faster historical shortcut remains control A.",
        "A-prime adds only the actual predicate path before A's early return; it does not add visited-bit or row-statistics work.",
        "All controls share one binary, native INIs/index and identical result/navigation/SSD trajectories.","",
        "| Control | CPU r1 ms/q | CPU r2 ms/q | Mean CPU ms/q | Native elapsed r1/r2 ms/q | Actual predicates/q |",
        "|---|---:|---:|---:|---|---:|"]
    for label,m in means.items():
        lines.append(f"| {label} | {m['cpu_ms_runs'][0]:.9f} | {m['cpu_ms_runs'][1]:.9f} | {m['cpu_ms']:.9f} | "
            f"{m['native_elapsed_ms_runs'][0]:.9f} / {m['native_elapsed_ms_runs'][1]:.9f} | {m['actual_callbacks_per_query']:.3f} |")
    lines+=["","| Controlled contrast | Mean CPU delta us/q | Paired deltas us/q |","|---|---:|---|"]
    for key in ("actual_predicate_path","remaining_admission","bit_over_original","bit_over_predicate_only","bit_over_shortcut"):
        d=delta[key]
        lines.append(f"| {d['left']} - {d['right']} | {1000*d['cpu_ms']:.3f} | "
                     +" / ".join(f"{1000*x:.3f}" for x in d["paired_cpu_ms"])+" |")
    lines+=["",f"Actual predicate path accounts for {100*share:.1f}% of the fresh bit-over-shortcut CPU gap.",
        "This measures std::function/callback/support-row lookup plus its existing guards, not a bare integer comparison.",
        "Distance accesses vector payloads; predicate accesses separate node-ID-indexed support rows.",
        "The remaining bit-over-A-prime cost is **causally unresolved**. Counts alone are not its explanation.",
        "No historical0.108ms number is subtracted or relabeled as an optimization.","",
        "## Raw sampled windows","",
        "| Control | CPU ms/q | Window elapsed ms/q | Native elapsed ms/q | Samples | Actual CPU us/sample |",
        "|---|---:|---:|---:|---:|---:|"]
    for t in cost["timings"]:
        if t["kind"]=="profile":
            lines.append(f"| {t['control']} | {t['cpu_ms_per_query']:.9f} | {t['window_elapsed_ms_per_query']:.9f} | "
                f"{t['native_elapsed_ms_per_query']:.9f} | {t['samples']} | {t['effective_cpu_us_per_sample']:.3f} |")
    lines+=["","Requested200us, delivered about1ms;1780 samples total, zero buffer drops/wrong-thread samples.",
        "Native elapsed includes finish-boundary map audit; actual CPU window excludes map dumps.",
        "Sampled/control CPU differences: "+", ".join(f"{k} {v['sampled_vs_control_cpu_percent']:+.3f}%"
            for k,v in cost["per_sampled_window"].items())+". These include run variation, not isolated profiler overhead.",
        "", "## Exclusive sampled CPU residence (us/query)","",
        "| Category | A-prime | bit | Delta | Conditional counting +/- |",
        "|---|---:|---:|---:|---:|"]
    for r in cost["sampled_residence"]:
        lines.append(f"| {r['description']} | {1000*r['Aprime_ms']:.3f} | {1000*r['bit_ms']:.3f} | "
                     f"{1000*r['delta_ms']:+.3f} | {1000*r['conditional_counting_95_halfwidth_ms']:.3f} |")
    lines+=["","Counting halfwidth assumes independent samples; periodic signal sampling can alias, so this is not a validated CI.",
        "Each leaf PC is counted once. PC-role subsets below must not be added to this table.",
        "Support slot compare/branch PC0x38852b dominates predicate samples. Original graph-marker branch PC0xd3d58",
        "accounts for74 versus140 samples; this is NOT an added eager alias check.",
        "Physical d/e update source line885 has0 versus6 samples; first-insertion callback-only leaf has0 versus4.",
        "Support/native-marker residence grows while unchanged distance-kernel residence falls substantially.",
        "That does not establish cache misses, DRAM, branch mispredictions or a complete causal residual breakdown.","",
        "## Capture-off source audit","",
        "Bit computes required physical degree/eligible/eligibleVisited. Legacy ordinaryObservation checks/passes",
        "also update with capture off (BKTIndex.cpp:584-586). Their exact source line had no samples in this short window;",
        "absence of samples is not proof of zero cost. No statistics were disabled.",
        "Bit first callback chain includes WorkSpace m_matchPredicate -> BKT matchPhysical -> resultFilter -> native Supports.",
        "A-prime directly uses the original resultFilter -> Supports chain after distance.",
        "No own/liveness/alias/CheckValidPosting qualification was added to either predicate.","",
        "## Validation and boundaries","",
        "All10 runs: Recall@10=.9153,24 matching H1,23.979 final SSD postings/query; no auxiliary posting/signature work.",
        "Four-byte visited; zero warmed backend allocation/conversion. Full capture-off/on and cross-control native traces match.",
        "3 native/UBSan/own-only,9 protocol and5 cost-evidence tests pass. Original-order regression is retained.",
        *("- "+x for x in report["limitations"]),"",
        "Independent checks: `python3 test_cost.py`, `python3 test_protocol.py`, and the native ctest harness.",
        "Raw evidence: cost_attribution.json, source_cost_audit.json, symbolized_samples.json, category_audit.json,",
        "sampled_disassembly.txt, per-run raw_pcs.tsv/module_maps/profile_flags.tsv, runtime.json and native INIs.",
        "No further optimization, profiling or measurements were executed."]
    (OUTPUT/"REPORT.md").write_text("\n".join(lines)+"\n")
    manifest={}
    for root in (HERE,OUTPUT,TOOL/"debug_objects"):
        for path in root.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.name not in (
                    "milestone_manifest.json","report.sha256.json","completion.json"):
                manifest[str(path)]=sha(path)
    for path in list(TOOL.glob("sampler_*.so"))+list((TOOL/"harness").glob("*"))+list((TOOL/"source/Release").glob("*.a")):
        if path.is_file(): manifest[str(path)]=sha(path)
    write(OUTPUT/"milestone_manifest.json",manifest)
    for path,digest in manifest.items(): assert sha(path)==digest,path
    write(OUTPUT/"report.sha256.json",{"report.json":sha(OUTPUT/"report.json"),
        "REPORT.md":sha(OUTPUT/"REPORT.md"),"manifest":sha(OUTPUT/"milestone_manifest.json")})
    print("STOP_IDLE",sha(OUTPUT/"report.json"),"sealed",len(manifest))

if __name__=="__main__":main()
