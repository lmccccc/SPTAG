"""Publish durable evidence for this isolated bounded milestone, never promote it."""
import json
from pathlib import Path
from prepare import HERE,TOOL,OUTPUT,PARENT,FILES,sha,write,protect

def main():
    protect()
    summary=json.loads((OUTPUT/"summary.json").read_text())
    records=json.loads((OUTPUT/"operations.json").read_text())["records"]
    tests=json.loads((OUTPUT/"tests.json").read_text())
    assert tests["exit"]==0 and len(records)==16
    broad=[r for r in summary if r["scenario"]=="broad_tag"]
    new=[r for r in broad if r["version"]=="new"]
    effects=[]
    control=next(r for r in broad if r["version"]=="postfilter")
    for row in new:
        old=next(r for r in broad if r["version"]=="old" and r["case"]==row["case"])
        assert row["action_mean"]==old["action_mean"]
        assert row["native_mean"]==old["native_mean"]
        effects.append({"case":row["case"],"new_over_old_latency":row["ordinary_ms"]/old["ordinary_ms"],
            "new_over_fresh_result_only_graph_latency":row["ordinary_ms"]/control["ordinary_ms"],
            "qualification_requests_unchanged":row["action_mean"]["qualification_checks"],
            "component_requests":row["hotpath_mean"]["component_requests"],
            "component_evaluations":row["hotpath_mean"]["component_evaluations"],
            "component_evaluations_avoided_by_hits":row["hotpath_mean"]["positive_hits"]+row["hotpath_mean"]["negative_hits"],
            "component_hit_fraction":1-row["hotpath_mean"]["component_evaluations"]/row["hotpath_mean"]["component_requests"],
            "old_valid_vector_allocations_inferred_per_query":sum(old["action_mean"][key] for key in
                ("no_neighbors","one_hop","directed","full_two_hop","posting")),
            "scratch_growths_after_warmup":row["hotpath_mean"]["graph_scratch_growths"]})
    runtime=json.loads((OUTPUT/"runtime.json").read_text())
    native_level=all(r["ordinary_ms"]<=control["ordinary_ms"] for r in new)
    report={
        "status":"IDLE; bounded optimized milestone complete; not promoted; OPERATOR_STOP retained",
        "bounded_implementation_goal_met":True,
        "goal_met":native_level,
        "goal_definition":"Behavior-preserving native hotpath optimization is complete; overall native-level speed goal requires both Broad optimized policies to match or beat fresh proper result-only H1 graph control. No recall loss is accepted.",
        "protection":True,
        "scope":"Only navix_hotpath_20260918 source/toolchain/evidence. No sweep, numeric_mixed resume, threshold or budget tuning, extra policy variants, production source/library changes, plot changes, or main-owned documentation edits.",
        "runtime":runtime,
        "source_changes":{
            "PredicateCache.h":"Lazy per-H1 support/exact/posting components, both boolean outcomes; query generation reset and wrap protection; reusable thread-local storage with exclusive RAII lease and nested-query fallback. No full predicate mask or head enumeration. Index/ID/predicate reuse always gets a new generation.",
            "SPANNIndex.cpp":"Share cached static predicate components across eligibility, own exact matching and posting admission; remove duplicate CheckValidPosting in limited-pure native callback. Re-evaluate native VID deletion and BKT deletion. Own gain, result competitiveness, dedup and alias ownership never cached.",
            "BKTIndex.cpp":"Workspace-backed validity and directed heap; ascending native (distance,ID) order and complete rows unchanged. Prefetch cache/row metadata, vectors only immediately before necessary native distance (including nonmatching directed intermediates). Specialized local graph emitter avoids singleton cross-edge path; graph Work/Outcome null without diagnostics.",
            "WorkSpace.h":"Dynamically sized reusable validity/intermediate vectors; no fixed 4096 buffer or degree cap.",
            "PostingSupplier.h":"Reuse per-query lower/upper candidate vectors across expansions; retain posting fresh-progress outcome with diagnostics disabled.",
            "NativeNeighborHooks.h/NativeSupplier.h":"Independent cache evaluation/hit/allocation-growth diagnostics, cleared nonowning callbacks, native-default empty fastpath.",
            "MatchedBench.cpp":"Untimed separate .hotpath.u64 evidence only; identical timed body and pre-existing capture parity checks."},
        "formulas_preserved":{"one_hop":"e/d >= 0.5","directed":"0.4*(d*e+e) > 2*d-e",
            "posting":"d>0 and e/d < 0.05; strict; fallback native GraphRoute",
            "constraints":"Valid visited neighbors count in e; unselected directed intermediates not marked; full-twohop scans visited intermediates; native checked leaves, MaxCheck2048, hierarchy512, initialratio0.666666, pages15, n24 unchanged."},
        "native_fixtures":tests,
        "fresh_broad_table":broad,
        "fresh_broad_effects":effects,
        "performance_conclusion":"No native-level speed recovery. Despite bit-exact outputs/work and fewer predicate evaluations/graph scratch allocations, both new Broad means remain above the fresh proper result-only control and above their fresh old-policy means. This is not a successful latency optimization for Broad; no promotion or further campaign is authorized.",
        "extreme_new_fresh":[r for r in summary if r["scenario"]=="extreme_tag"],
        "extreme_old_frozen_only":{
            "source":str(PARENT/"completion_report.json"),
            "sha256":sha(PARENT/"completion_report.json"),
            "interpretation":"Frozen old final/head/own/distance/SSD/native/branch/row/work payload hashes are the trajectory oracle. Historical old timings are not paired with new timings and are not spliced into this fresh table."},
        "unfilter":[r for r in summary if r["scenario"]=="unfilter"],
        "acceptance":{
            "all_final_ids_distances_ssd_work_bit_exact":True,
            "all_native_head_own_branch_decision_row_work_hashes_bit_exact":True,
            "qualification_requests_unchanged":True,
            "capture_on_off_ids_distances_ssd_work_exact":True,
            "fresh_repetitions_exact":True,
            "all_native_arrays_loaded_once_per_process":True,
            "graph_and_unfilter_upper_work_zero":True,
            "warm_cache_and_graph_scratch_growths_zero":True,
            "positive_and_negative_cache_hits":True},
        "measurement_protocol":"16 single-load processes; fixed n24, 1000 warmup + 1000 measured offset0 topk10, CPU+memory NUMA2, querythread1, same buffered matched index. Broad proper graph/old NaviX/new NaviX/old combined/new combined then reverse; new unfilter two; new extreme NaviX/combined then reverse. Diagnostics and allocation-growth counters outside timing.",
        "counter_scope":"qualification_checks retains original encounter requests. hotpath component_requests/evaluations/positive_hits/negative_hits are separate, sampled at native supplier completion. Growth counts cover cache backing storage and graph valid/intermediate vectors, not all query/SSD allocations.",
        "limitations":[
            "No attribution percentages from a hardware profiler; native SIMD distance dispatch is unchanged, with no invented SIMD, distance cache, or reordered distance admission.",
            "Collapsed representative qualification still traverses native aliases and rechecks mutable deletion; only immutable predicate components are memoized.",
            "Measured frozen traces contain decisions, counters, complete CSR rows and selected heads/own points, not every transient distance/visited event. Native fixtures separately audit visited encounter parity; no unrecorded trace equality is claimed.",
            "Per-query supplier state and capture output allocations remain. Scratch proof does not claim zero total allocations.",
            "Only bounded n24 Broad/extreme/unfilter evidence; no production promotion or numeric_mixed/full-curve extrapolation.",
            "Two timing repetitions are descriptive, not a statistical significance claim.",
            "Main-owned GettingStart.md inherited divergence is preserved; publication remains with the parent."],
        "commands":{"build":"TMPDIR=<new-build> cmake -S <new-source> -B <new-build> -DCMAKE_BUILD_TYPE=Release -DSPDK=OFF -DROCKSDB=OFF; cmake --build <new-build> --target SPTAGLibStatic -j2",
            "harness":"cmake -S "+str(HERE)+" -B "+str(TOOL/"harness")+" -DSPANN_ROOT="+str(TOOL/"source")+" -DCMAKE_BUILD_TYPE=Release; cmake --build <new-harness> -j2",
            "tests":"ctest --test-dir <new-harness> --output-on-failure; python3 "+str(HERE/"test_protocol.py"),
            "measure":"python3 "+str(HERE/"run.py"),
            "finalize":"python3 "+str(HERE/"finalize.py")},
        "records":[str(OUTPUT/r["label"]/"record.json") for r in records]}
    write(OUTPUT/"report.json",report)
    write(OUTPUT/"report.sha256.json",{"sha256":sha(OUTPUT/"report.json")})
    print(json.dumps({"status":report["status"],"goal_met":report["goal_met"],
                      "bounded_implementation_goal_met":True,"fresh_broad_effects":effects},indent=2),flush=True)
    files=list(HERE.rglob("*"))+list(OUTPUT.rglob("*"))
    files += [TOOL/"harness/navix-bench",TOOL/"harness/native-tests"]
    files += list((TOOL/"source/Release").glob("*.a"))
    files += [TOOL/"source"/p for p in FILES.values()]
    write(OUTPUT/"milestone_manifest.json",{str(p):sha(p) for p in files
          if p.is_file() and "__pycache__" not in str(p) and p.name!="milestone_manifest.json"})

if __name__=="__main__":main()
