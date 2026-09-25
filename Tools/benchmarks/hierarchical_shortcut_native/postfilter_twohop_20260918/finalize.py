"""Seal one bounded postfilter/two-hop milestone; never launch follow-up work."""
import difflib
import json
import shutil
from prepare import HERE,TOOL,OUTPUT,BASE,PARENT,FILES,sha,write,protect

def load(name): return json.loads((OUTPUT/name).read_text())

def main():
    protect()
    summary=load("summary.json");operations=load("operations.json");small=load("small_replay.json")
    assert len(summary)==3 and len(operations["records"])==6 and len(small)==4
    assert operations["ordinary_index_loads"]==6
    assert all(c["returncode"]==0 for c in load("build_commands.json"))
    assert all(r["two_repetition_exact_determinism"] for r in summary)
    installed={};changes={};diff=[]
    allowed={FILES[n] for n in ("BKTIndex.cpp","SPANNIndex.cpp","WorkSpace.h",
        "NativeNeighborHooks.h","FullHooks.h","NavixPolicy.h","NativeSupplier.h")}
    for p in sorted((TOOL/"source").rglob("*")):
        if not p.is_file():continue
        rel=p.relative_to(TOOL/"source")
        if "Release" in rel.parts or "__pycache__" in rel.parts:continue
        installed[str(rel)]=sha(p)
        old=BASE/"source"/rel
        assert old.exists(),str(rel)
        if sha(p)!=sha(old):
            assert str(rel) in allowed,str(rel)
            changes[str(rel)]={"before":sha(old),"after":sha(p)}
            diff.extend(difflib.unified_diff(old.read_text().splitlines(True),p.read_text().splitlines(True),
                fromfile="frozen/"+str(rel),tofile="postfilter_twohop/"+str(rel)))
    assert set(changes)==allowed
    write(OUTPUT/"installed_source_manifest.json",installed)
    (OUTPUT/"targeted_source.patch").write_text("".join(diff))
    proof={}
    for name in ("build/AnnService/CMakeFiles/SPTAGLibStatic.dir/flags.make",
                 "harness/CMakeFiles/navix-bench.dir/flags.make"):
        proof[name]=(TOOL/name).read_text();assert "-O3" in proof[name]
    write(OUTPUT/"compile_proof.json",proof)
    graph=summary[0]
    comparisons=[]
    for row in summary[1:]:
        comparisons.append({"case":row["case"],
            "latency_ratio_to_same_core_graph":row["ordinary_ms"]/graph["ordinary_ms"],
            "latency_increase_percent":100*(row["ordinary_ms"]/graph["ordinary_ms"]-1),
            "recall_delta_to_same_core_graph":row["recall_at_10"]-graph["recall_at_10"],
            "latency_improved":row["ordinary_ms"]<graph["ordinary_ms"],
            "recall_not_decreased":row["recall_at_10"]>=graph["recall_at_10"],
            "speed_and_recall_goal_pass":row["ordinary_ms"]<graph["ordinary_ms"] and
                row["recall_at_10"]>=graph["recall_at_10"],
            "ordinary_expansion_fraction":row["direct_graph_counts"]["one_hop"]/
                sum(row["direct_graph_counts"].values())})
    low=summary[2]
    interpretation=(
        f"At .05, {comparisons[1]['ordinary_expansion_fraction']:.4%} of expansions use restored ordinary "
        f"postfilter. Outer expansions {low['operator_mean']['outer_expansions']:.3f}/query versus "
        f"{graph['operator_mean']['outer_expansions']:.3f} for control; native head distances "
        f"{low['native_mean']['head_distances']:.3f} versus {graph['native_mean']['head_distances']:.3f}. "
        f"Work is graph-like, but latency is {comparisons[1]['latency_increase_percent']:.2f}% higher, "
        f"not graph-like performance. Hybrid still scans {low['classified_initial_neighbor_entries']:.3f} "
        f"initial entries and makes {low['operator_mean']['qualification_requests']:.3f} qualification "
        "requests/query. This is residual classification work, not an exact causal attribution of the "
        "latency gap: sparse two-hop can also change trajectories, cache effects and bounds. Both "
        "requested hybrids fail the speed goal; recall is reported separately. No equality to old "
        "filtered-onehop trajectories, per-phase decomposition or statistical significance claim.")
    for record in operations["records"]+small:
        assert all(record["native_mean"][k]==0 for k in (
            "child_distances","calls","returns","representative_distances","csr_members",
            "signature_checks","signature_rejects","h2_rows","h3_rows"))
    assert all(r.get("exact_frozen_oracle") for r in operations["records"] if r["mode"]=="postfilter_graph")
    sparse=[r for r in small if r["scenario"]=="extreme_tag"]
    empty=[r for r in small if r["scenario"]=="unfilter"]
    assert all(r["outer_decision_counts"]["posting"]==0 for r in sparse)
    assert all(all(v==0 for v in r["qualification_head_phase_mean"].values()) for r in empty)
    report={
        "milestone":HERE.name,"status":"completed; STOP IDLE","semantic_isolation_pass":True,
        "ordinary_points":3,"ordinary_single_load_processes":6,"posting_threshold":0,
        "two_hop_thresholds":[.1,.05],"fresh_broad":summary,"fresh_comparison":comparisons,
        "semantics":{
            "explicit_modes":"NavixMode=postfilter_graph: result-only plus own, navix=false, no classifier; NavixMode=postfilter_twohop: explicit new graph/two-hop policy. Old navix/posting mode semantics and frozen builds are not reinterpreted.",
            "graph":"r>=T2 and d0 use original native expandEdges over the entire physical adjacency, empty traversal filter, no valid-mask gate and no finishInjectedRow override. Native CheckAndSet, distance, own/result admission, queues, m_Results, bounds, termination and per-edge MaxCheck remain authoritative.",
            "twohop":"r<T2 retains directed/full .4*(d*e+e)>2*d-e operators. Nonmatching intermediates are handled by the existing operators; useful destinations remain FILTERED, not an unfiltered d-squared scan. Selected second rows finish under native checked-leaf accounting.",
            "posting":"Entirely disabled in both new modes, including e0: threshold0, no callback/model/owner requirement or owner construction; no query-time signature, representative or CSR work.",
            "ratio":"e counts eligible visited and unvisited physical first-row entries before the sentinel. Valid vector chooses the route only on the ordinary graph branch.",
            "qualification":"Static posting/exact-own bytes are separately cached for hybrid classification; deletion/liveness and actual own-heap gain remain uncached. Control has no qualification lease/classifier.",
            "empty":"Unfiltered timed requests retain true SearchIndex bypass; untimed diagnostic native hooks preserve exact outputs.",
            "no_policy_additions":"No global scan, new fallback/retry, shadow visited/heap/engine, budget/threshold tuning, or promotion."},
        "diagnostics":{
            "uniform_schema":{".operators.u64":"N x 3 uint64: outer_expansions, ordinary_neighbor_entries, qualification_requests",
                ".native.u64":"N x 18 uint64; native distances/checked/queue/admission flags",
                ".navix.u64":"N x 19 uint64; branch and graph/twohop work",
                ".qualification.u64":"N x 6 uint64; hybrid cache request/evaluation counts",
                ".work.u64":"N x 8 uint64: readPostings, scannedVectors, matchedVectors, dedupSkippedVectors, scanned, postingPageReads, postingLogicalBytes, postingPhysicalBytes",
                ".decisions.f64":"M x 11 float64: query,head,d,e,route,fallback,checkedBefore,checkedAfter,signatures,representatives,members"},
            "outer_expansions":"Counts actual outer expansion dispatch after native bounds/termination tests, not every popped queue element.",
            "ordinary_neighbor_entries":"Counts valid nonnegative physical row entries reaching the ORIGINAL expandEdges body after its per-edge budget test, before native visited/result checks; duplicates/visited/nonmatches count. Sentinel, budget-truncated suffix and two-hop destination offers do not count.",
            "classified_initial_neighbor_entries":"Sum d from hybrid decisions. Complete classifier scans occur before choosing a route and can exceed ordinary entries actually processed. Control is zero; do not confuse initial scans, qualification requests, emitted useful destinations, fresh distances, or distinct expanded heads.",
            "qualification_requests":"Native qualifies invocations: first-row classification plus selected second-row candidates; separate from cached posting/exact predicate evaluations.",
            "timing":"All increments gated on diagnostics; capture and its allocations occur after unchanged ordinary timed body. No profile, preload or phase-timer decomposition.",
            "route_name_compatibility":"Capture route code1 retains legacy one_hop field name but means ORIGINAL ordinary unfiltered-navigation postfilter in the NEW explicit mode, never filtered-onehop.",
            "full_visited_fixture":"WorkSpace CheckAndSet audit records every actual encounter including tree and graph calls without adding visited or Contains operations; exact ordinary/classifier graph fixture equality."},
        "verification":{
            "commands":load("build_commands.json"),"native_rejection_cases":load("native_rejection_tests.json"),
            "native_fixtures_log":"native-fixtures-detail.log",
            "native_fixtures":"Actual BKT high-r A-B-C nonmatching bridge; original SearchIndexWithResultFilter outputs/checked; diagnostic native/classifier graph full encounters/distances/queues equality; visited ratio; .05/.1 boundaries/direct/full; graph per-edge truncation vs complete two-hop rows; e0 no posting; null model/empty catalogs; own/alias/tie/deletion/final filter; diagnostic parity; empty native.",
            "actual_sparse32":sparse,"actual_empty32":empty,"ordinary_records":operations["records"],
            "same_core_graph_oracle":"1000-query exact frozen proper result-only graph IDs/distances/SSD work plus native distances/checked/queue/head/own traces; the old binary is oracle only, not a timed point.",
            "determinism":"Each reverse-order pair has identical final IDs/distances/SSD/native/action/head/own/decisions/cache/operator artifacts.",
            "source_changes":changes,"old_artifacts_protected":True,"operator_stop_preserved":True,
            "gettingstart":"Main-owned documentation explicitly excluded from inherited protection, never edited here."},
        "runtime":load("runtime.json"),"build_artifacts":load("build_artifacts.json"),
        "interpretation":interpretation,
        "scope":"One bounded milestone: same-core graph/.1/.05, reverse paired six Broad processes; four small sparse/unfiltered replays only. No Extreme latency claim, fourth classifier experiment, curve, new policy iteration, follow-up optimization, or promotion.",
        "paths":{"source":str(HERE),"toolchain":str(TOOL),"evidence":str(OUTPUT),"frozen":str(PARENT)},
        "reproduction":{"build_commands":"build_commands.json","process_commands":"Each run command.json",
            "plan":load("measurement_plan.json")}}
    write(OUTPUT/"report.json",report)
    for root in (HERE,TOOL/"source"):
        for cache in root.rglob("__pycache__"):
            if cache.is_dir():shutil.rmtree(cache)
    if (TOOL/"compiler-work").exists():shutil.rmtree(TOOL/"compiler-work")
    manifest={}
    for root in (HERE,OUTPUT):
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.name not in ("milestone_manifest.json","report.sha256.json"):
                manifest[str(p)]=sha(p)
    for p in sorted(TOOL.rglob("*")):
        if p.is_file() and (p.suffix in (".a",".so",".json") or
            p.name in ("navix-bench","native-tests","CMakeCache.txt","flags.make","link.txt")):
            manifest[str(p)]=sha(p)
    write(OUTPUT/"milestone_manifest.json",manifest)
    write(OUTPUT/"report.sha256.json",{"report_sha256":sha(OUTPUT/"report.json"),
        "summary_sha256":sha(OUTPUT/"summary.json"),
        "manifest_sha256":sha(OUTPUT/"milestone_manifest.json"),"files":len(manifest)})
    print(json.dumps({"summary":[{k:r[k] for k in ("case","ordinary_ms","ms_range","qps_at_mean_ms",
        "recall_at_10","operator_mean","avg_second_hop_rows")} for r in summary],
        "comparison":comparisons,"seal":load("report.sha256.json")},indent=2))

if __name__=="__main__":main()
