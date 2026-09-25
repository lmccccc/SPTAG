"""Seal the one bounded milestone and report outcomes without promotion."""
import json
import subprocess
from prepare import HERE,DATA,TOOL,OUTPUT,PARENT,FILES,sha,write,protect

def main():
    protect()
    summary=json.loads((OUTPUT/"summary.json").read_text())
    operations=json.loads((OUTPUT/"operations.json").read_text())
    small=json.loads((OUTPUT/"small_replay.json").read_text())
    points={(s["scenario"],s["mode"]):s for s in summary}
    graph=points["broad_tag","graph"];observe=points["broad_tag","observe"];posting=points["broad_tag","posting"]
    goals={}
    for scenario in dict.fromkeys(p["scenario"] for p in summary):
        g=points[scenario,"graph"];p=points[scenario,"posting"]
        goals[scenario]={
            "faster":p["ordinary_ms"]<g["ordinary_ms"],
            "recall_not_lower":p["recall_at_10"]>=g["recall_at_10"],
            "goal_met":p["ordinary_ms"]<g["ordinary_ms"] and p["recall_at_10"]>=g["recall_at_10"],
            "speedup_graph_over_posting":g["ordinary_ms"]/p["ordinary_ms"],
            "recall_delta":p["recall_at_10"]-g["recall_at_10"]}
    installed={str(TOOL/"source"/path):sha(TOOL/"source"/path) for name,path in FILES.items()}
    write(OUTPUT/"installed_source_manifest.json",installed)
    paths=[TOOL/"build/AnnService/CMakeFiles/SPTAGLibStatic.dir/flags.make",
           TOOL/"harness/CMakeFiles/postfilter-bench.dir/flags.make",
           TOOL/"harness/CMakeFiles/native-tests.dir/flags.make"]
    write(OUTPUT/"compile_proof.json",{str(p):p.read_text() for p in paths})
    report={
        "milestone":HERE.name,"status":"completed; STOP IDLE","state":"idle",
        "semantic_isolation_pass":True,"activation_ratio":.01,"ratio_tuning":False,
        "ordinary_broad_processes":6,"ordinary_sparse_processes":4 if small["full_sparse_allowed"] else 0,
        "ordinary_single_load_processes":operations["ordinary_index_loads"],
        "fresh_broad":[s for s in summary if s["scenario"]=="broad_tag"],
        "fresh_sparse":[s for s in summary if s["scenario"]=="extreme_tag"],
        "sparse32_preflight":small,"native_speed_recall_goals":goals,
        "goal_met":all(g["goal_met"] for g in goals.values()),
        "observe_tax":{
            "exact_native_graph_trace_parity":True,
            "mean_ms_observe_minus_graph":observe["ordinary_ms"]-graph["ordinary_ms"],
            "relative_percent":100*(observe["ordinary_ms"]/graph["ordinary_ms"]-1),
            "paired_ms_deltas":[o-g for o,g in zip(observe["ordinary_ms_runs"],graph["ordinary_ms_runs"])],
            "meaning":"Fused qualification/cache/observation overhead on exactly identical native graph trajectory; not a profiler-caused distribution claim"},
        "posting_minus_observe":{
            "mean_ms":posting["ordinary_ms"]-observe["ordinary_ms"],
            "pure_cost_decomposition":False,
            "meaning":"Posting may change native trajectory, distances, checked work, posting choices and recall"},
        "signature_evidence":{
            "broad_checks_per_query":posting["native_mean"]["signature_checks"],
            "broad_rejects_per_query":posting["native_mean"]["signature_rejects"],
            "claim":"No signature pruning savings established when Broad rejects are zero/near zero; counters are not elapsed-time savings"},
        "action_order":"Original ordinary row first; fused before-visited d/e; completed sparse row and remaining budget then at most one signed action; same native frontier",
        "ordinary_current_row_work_avoided":False,"connectivity_deficit_or_degree_quota":False,
        "degree_accounting":"d counts actually processed ordinary encounters before visited, not a physical degree for an incomplete row. Incomplete counts include rows entered after the native budget was already exhausted. Graph does not collect row completion observations (null, not zero).",
        "hardware_evidence":"No hardware counters collected. Additional random accesses are not assumed DRAM accesses.",
        "removed_active_runtime":["NaviX one/direct/full-twohop","classifier prescan","valid vector","directed heap",
            "second-row loops","CostModel","upper ANN frontier","fallback/restart"],
        "concrete_introduced_overhead_fixed_before_final_timing":{
            "issue":"Inherited supplier allocated lower/upper vectors each sparse expansion and linearly searched retained descriptors",
            "fix":"Direct fixed owner-array nearest selection, stack array64 for H3 owners, once-per-query reserved descriptor storage and per-row retained flag",
            "no_independent_algorithm_variant":True},
        "unfilter":"Direct original native SearchIndex even when diagnostic capture is enabled; emitted zero new runtime activity",
        "graph_observe_no_model_requirement":"Validated native fixtures with null PostingModel and empty catalogs/postings",
        "static_owner_metadata":"Posting-only static inverse ownership is built at index load; per-query owner access and supplier initialization occur only after sparse gate",
        "native_fixture_log":str(OUTPUT/"native-fixtures-detail.log"),
        "frozen_base":{
            "report":str(PARENT/"report.json"),"report_sha256":sha(PARENT/"report.json"),
            "summary_sha256":sha(PARENT/"summary.json"),
            "sealed_manifest_files":len(json.loads((PARENT/"milestone_manifest.json").read_text())),
            "oracle":str(PARENT/"broad_tag_postfilter_graph_twohop_0_posting_0_r1/nprobe_24")},
        "protection":{
            "verified_files":len(json.loads((OUTPUT/"protection.json").read_text())),
            "unchanged":True,"production_libraries_modules":True,"prior_sources_builds_evidence":True,
            "inputs_index_plots_operator_stop":True,
            "documentation_exception":"GettingStart main-owned divergence explicitly excluded; no edits made here"},
        "runtime_manifest":str(OUTPUT/"runtime.json"),"ordinary_native_commands":str(OUTPUT/"ordinary_plan.json"),
        "no_curves_no_sweep_no_promotion":True,"stop":"Bounded milestone completed; no further process launched"}
    write(OUTPUT/"report.json",report)
    write(OUTPUT/"report.sha256.json",{"sha256":sha(OUTPUT/"report.json")})
    manifest={}
    for root in (HERE,OUTPUT):
        for p in root.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts and p.name not in ("milestone_manifest.json",):
                manifest[str(p)]=sha(p)
    for p in (TOOL/"harness/postfilter-bench",TOOL/"harness/native-tests"):
        manifest[str(p)]=sha(p)
    manifest.update(installed)
    for p in (TOOL/"source/Release").glob("*.a"):manifest[str(p)]=sha(p)
    write(OUTPUT/"milestone_manifest.json",manifest)
    protect()
    print(json.dumps({"state":"idle","goal_met":report["goal_met"],"report":str(OUTPUT/"report.json"),
        "sha256":sha(OUTPUT/"report.json"),"sealed_files":len(manifest)},indent=2))

if __name__=="__main__":main()
