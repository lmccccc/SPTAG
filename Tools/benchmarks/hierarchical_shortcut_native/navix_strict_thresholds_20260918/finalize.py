"""Seal precisely the strict correction; historical timing is never paired."""
import difflib
import json
import shutil
import numpy as np
from prepare import HERE,DATA,TOOL,OUTPUT,BASE,PARENT,FILES,sha,write,protect

def load(name): return json.loads((OUTPUT/name).read_text())

def main():
    protect()
    summary=load("summary.json");operations=load("operations.json");small=load("small_replay.json")
    assert len(summary)==3 and len(operations["records"])==6 and len(small)==4
    assert operations["ordinary_index_loads"]==6
    assert all(c["returncode"]==0 for c in load("build_commands.json"))
    assert all(r["two_repetition_exact_determinism"] for r in summary)
    installed={};changes={};diff=[]
    allowed={FILES[n] for n in ("BKTIndex.cpp","NativeNeighborHooks.h","FullHooks.h","NavixPolicy.h","NativeSupplier.h")}
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
                fromfile="frozen/"+str(rel),tofile="strict/"+str(rel)))
    assert set(changes)==allowed
    write(OUTPUT/"installed_source_manifest.json",installed)
    (OUTPUT/"targeted_source.patch").write_text("".join(diff))
    proof={}
    for name in ("build/AnnService/CMakeFiles/SPTAGLibStatic.dir/flags.make",
                 "harness/CMakeFiles/navix-bench.dir/flags.make"):
        proof[name]=(TOOL/name).read_text();assert "-O3" in proof[name]
    write(OUTPUT/"compile_proof.json",proof)
    sparse=[r for r in small if r["scenario"]=="extreme_tag"]
    empty=[r for r in small if r["scenario"]=="unfilter"]
    assert all(r["outer_decision_counts"]["posting"]>0 for r in sparse)
    assert all(all(v==0 for v in r["qualification_head_phase_mean"].values()) for r in empty)
    historical=json.loads((PARENT/"summary.json").read_text())
    history=[]
    for fresh in summary:
        old=next(r for r in historical if r["mode"]==fresh["mode"] and
            r["two_hop_threshold"]==fresh["two_hop_threshold"])
        label=f"broad_tag_{fresh['case']}_r1" if fresh["mode"]=="posting" else "broad_tag_postfilter_graph_r1"
        oldlabel=label.replace("_posting_0.01","_posting_0.05")
        artifact_equality={};row_equality={}
        for suffix,dtype,width in ((".ids.i32","<i4",10),(".dist.f32","<f4",10),(".work.u64","<u8",8)):
            current=OUTPUT/label/("nprobe_24"+suffix)
            previous=PARENT/oldlabel/("nprobe_24"+suffix)
            artifact_equality[suffix]=sha(current)==sha(previous)
            a=np.fromfile(current,dtype=dtype).reshape(-1,width)
            b=np.fromfile(previous,dtype=dtype).reshape(-1,width)
            row_equality[suffix]={"identical_query_rows":int(np.count_nonzero(np.all(a==b,axis=1))),
                "total_query_rows":len(a)}
        history.append({"fresh_case":fresh["case"],"historical_case":old["case"],
            "historical_equal_0.05_cutoffs_diagnostic_only":
                old["mode"]=="posting" and old["two_hop_threshold"]==.05,
            "historical_outputs_work_recall":{k:old[k] for k in
                ("recall_at_10","native_mean","action_mean","ssd_mean","direct_graph_counts",
                 "fallback_graph_counts","avg_second_hop_rows") if k in old},
            "recall_delta":fresh["recall_at_10"]-old["recall_at_10"],
            "final_artifacts_equal":artifact_equality,"query_row_parity":row_equality,
            "work_deltas":{group:{k:fresh[group][k]-old[group][k] for k in fresh[group]}
                for group in ("native_mean","action_mean","direct_graph_counts","fallback_graph_counts")
                if group in fresh and group in old},
            "timing_not_paired":True,
            "artifact_comparison":[r.get("historical_comparison",{"exact_frozen_oracle":r.get("exact_frozen_oracle")})
                for r in operations["records"] if r["mode"]==fresh["mode"] and
                r["two_hop_threshold"]==fresh["two_hop_threshold"]]})
    graph=summary[0]
    write(OUTPUT/"historical_comparison.json",history)
    report={
        "milestone":HERE.name,"status":"completed; STOP IDLE","strict_correction_pass":True,
        "performance_goal_independent":True,"posting_threshold":.01,"posting_threshold_untuned":True,
        "two_hop_thresholds":[.1,.05],"ordinary_points":3,"ordinary_single_load_processes":6,
        "fresh_broad":summary,"historical_not_paired":history,
        "fresh_comparison":[{"case":r["case"],"latency_ratio_to_fresh_graph":r["ordinary_ms"]/graph["ordinary_ms"],
            "recall_delta_to_fresh_graph":r["recall_at_10"]-graph["recall_at_10"]}
            for r in summary[1:]],
        "semantics":{
            "validation":"Shared native helper: finite twohop in (0,1], posting in [0,.5]; active posting strictly below twohop. Parser, supplier, direct helper and injectable hook runtime covered.",
            "disabled":"Posting zero is disabled (r<0 never); NaviX-only disabled extension permits irrelevant equal cutoffs.",
            "precedence":"d0 no-neighbors; r>=T2 existing FILTERED one-hop; in r<T2 enabled r<Tpost posting; else immutable .4*(d*e+e)>2*d-e directed, otherwise full.",
            "fallback":"Failed posting graph fallback remains strictly within r<Tpost<T2; no one-hop fallback.",
            "ratio":"e counts eligible already-visited neighbors; d counts native neighbors before sentinel.",
            "unchanged":"Own points/gains, callbacks, aliases/ties, mutable deletion, cache lease/reset/static posting-vs-own truth, native queues/budgets/checked, complete CSR rows/signatures, ordinary empty fast path.",
            "not_restored":"No native postfilter restoration, floors, quotas, distance caps or tuning.",
            "discrete":"Positive .01 and .05 cutoffs can both select e0; Broad can be locally e0. No guaranteed reduction in posting branches."},
        "verification":{
            "commands":load("build_commands.json"),"native_rejection_cases":load("native_rejection_tests.json"),
            "native_fixtures":"Inherited bridge/directed/full/visited/CSR/signature/own/aliases/ties/deletion/cache/budgets/diagnostic tests plus shared helper/parser/hooks invalid equality and reversal; both levels equality/below; true .01<r<.05 degree100/e2; posting0 disabled; NaviX-only equal allowed; d0.",
            "actual_sparse32":sparse,"actual_empty32":empty,
            "ordinary_records":operations["records"],
            "determinism":"Exact final IDs/distances/SSD/head/own/11-column decisions/CSR/cache and native/action work hashes match each reverse pair.",
            "diagnostics":"Untimed capture compared against ordinary output and SSD work; exact predicate checked independently; direct/fallback formula and complete rows checked.",
            "source_changes":changes,"source_audit":"Only five policy/plumbing native files differ; qualification and all other installed source hashes unchanged.",
            "old_artifacts_protected":True,"operator_stop_preserved":True,
            "gettingstart":"Main-owned docs excluded from protection and never edited here."},
        "runtime":load("runtime.json"),"build_artifacts":load("build_artifacts.json"),
        "interpretation":"Strict semantic correction is separate from performance. Combined latency remains higher than the fresh proper result-only graph control; no latency success, optimum, significance or historical paired speedup claim. Two repetitions only.",
        "observed_discreteness":"Current Broad posting branches all have e0; lowering .05 to .01 nevertheless changes rows with degree>=21/e1 from posting to direct graph. Actual recorded d/e/r/route/fallback histograms, not a Broad label assumption, establish the domain.",
        "scope":"Exactly fixed .01 correction plus six ordinary processes and four small replays. No sweep, promotion, third two-hop setting, extra baseline campaign, or automatic iteration.",
        "paths":{"source":str(HERE),"toolchain":str(TOOL),"evidence":str(OUTPUT),"historical":str(PARENT)},
        "reproduction":{"readme":str(HERE/"README.md"),"build_commands":"build_commands.json",
            "process_commands":"Each run command.json","plan":load("measurement_plan.json")}}
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
    print(json.dumps({"summary":[{k:r[k] for k in ("case","ordinary_ms","ms_range","qps_at_mean_ms","recall_at_10",
        "avg_second_hop_rows","avg_posting_fallbacks")} for r in summary],"seal":load("report.sha256.json")},indent=2))

if __name__=="__main__":main()
