"""Verify bounded completion and seal, without promotion or publication edits."""
import difflib
import json
from pathlib import Path
import shutil
from prepare import HERE,DATA,TOOL,OUTPUT,BASE,PARENT,FILES,sha,write,protect

def load(name): return json.loads((OUTPUT/name).read_text())

def main():
    protect()
    summary=load("summary.json");operations=load("operations.json");small=load("small_replay.json")
    assert len(summary)==7 and len(operations["records"])==14 and len(small)==12
    assert len(load("native_rejection_tests.json"))==23
    assert all(c["returncode"]==0 for c in load("build_commands.json"))
    assert all(r["two_repetition_exact_determinism"] for r in summary)
    assert all("exact_frozen_oracle" in r for r in operations["records"] if r["two_hop_threshold"]==.5)
    source_changes={};diff=[]
    allowed={FILES[n] for n in ("BKTIndex.cpp","NativeNeighborHooks.h","FullHooks.h","NavixPolicy.h","NativeSupplier.h")}
    installed={}
    for p in sorted((TOOL/"source").rglob("*")):
        if not p.is_file():continue
        relative=p.relative_to(TOOL/"source")
        if "Release" in relative.parts or "__pycache__" in relative.parts:continue
        installed[str(relative)]=sha(p)
        old=BASE/"source"/relative
        assert old.exists(),str(relative)
        if sha(p)!=sha(old):
            assert str(relative) in allowed,str(relative)
            source_changes[str(relative)]={"before":sha(old),"after":sha(p)}
            diff.extend(difflib.unified_diff(old.read_text().splitlines(True),p.read_text().splitlines(True),
                fromfile="frozen/"+str(relative),tofile="successor/"+str(relative)))
    assert set(source_changes)==allowed
    write(OUTPUT/"installed_source_manifest.json",installed)
    (OUTPUT/"targeted_source.patch").write_text("".join(diff))
    compile_proof={}
    for name in ("build/AnnService/CMakeFiles/SPTAGLibStatic.dir/flags.make",
                 "harness/CMakeFiles/navix-bench.dir/flags.make"):
        text=(TOOL/name).read_text();assert "-O3" in text
        compile_proof[name]=text
    write(OUTPUT/"compile_proof.json",compile_proof)
    baseline={r["mode"]:r for r in summary if r["two_hop_threshold"]==.5}
    graph=summary[0]
    deltas=[]
    for r in summary[1:]:
        base=baseline[r["mode"]]
        deltas.append({"case":r["case"],"speedup_vs_same_mode_0.5":base["ordinary_ms"]/r["ordinary_ms"],
            "recall_delta_vs_same_mode_0.5":r["recall_at_10"]-base["recall_at_10"],
            "speedup_vs_proper_postfilter_graph":graph["ordinary_ms"]/r["ordinary_ms"],
            "recall_delta_vs_proper_postfilter_graph":r["recall_at_10"]-graph["recall_at_10"],
            "all_runs_faster_than_0.5":max(r["ordinary_ms_runs"])<min(base["ordinary_ms_runs"])})
    sparse=[r for r in small if r["scenario"]=="extreme_tag"]
    unfiltered=[r for r in small if r["scenario"]=="unfilter"]
    assert all(all(v==0 for v in r["qualification_head_phase_mean"].values()) for r in unfiltered)
    report={
        "milestone":HERE.name,"status":"completed exactly bounded request; STOP IDLE",
        "requested_new_thresholds":[.1,.05],"parity_threshold":.5,"posting_threshold_unchanged":.05,
        "ordinary_points":7,"ordinary_single_load_processes":14,
        "fresh_broad":summary,"paired_deltas":deltas,
        "semantics":{
            "two_hop_cutoff":"e/d >= configured two-hop threshold selects existing FILTERED one-hop, including equality",
            "degree":"native entries before sentinel; e includes eligible already-visited neighbors",
            "posting":"strict e/d<0.05 takes precedence only in posting mode; d0 explicitly no-neighbors",
            "directed_full":"Below graph cutoff, .4*(d*e+e)>2*d-e selects directed, otherwise full",
            "fallback":"Failed posting uses configured graph cutoff; .05 combined has no DIRECT two-hop interval, but can still FALLBACK to two-hop",
            "control":"Fresh frozen cost-v2 proper native result-only postfilter graph plus native own points; not original unfiltered-H1 admission",
            "not_restored":"Lowered cutoffs do not restore native postfilter semantics; existing filtered-one-hop remains",
            "unchanged":"candidate order, queues, budgets, ownPoint/ownGain, aliases/ties/deletions, qualification byte cache, signed CSR, complete rows and empty-predicate fast path"},
        "verification":{
            "build_and_native_protocol_tests":load("build_commands.json"),
            "native_rejection_cases":load("native_rejection_tests.json"),
            "default_0.5_exact_frozen_parity":"All1000 latest frozen IDs/distances/SSD/native/head/own/branches/CSR/decisions/qualification bytes match, both modes and repetitions",
            "requested_cutoffs":"Exact predicate checks, native route formulas, posting precedence/fallback, complete CSR rows, diagnostic-off/on IDs/distances/SSD parity; no old-trajectory equality demanded",
            "repetition_determinism":"Exact all result/work/diagnostic artifact hashes for each of seven pairs",
            "native_fixtures":"All inherited bridge, directed order, full-through-visited, complete rows, budgets, aliases, ties, mutable deletions, own-only, signatures, CSR, qualification, empty/native-default fixtures retained; new .05/.1/.5 equality/just-below and d0 in both modes, visited e, fallback and strict nativeINI rejection",
            "actual_sparse32":sparse,"actual_unfilter32":unfiltered,
            "small_unfilter_zero_new_cache_work":True,
            "naviX_only_zero_upper_work":True,
            "source_changes":source_changes,
            "source_audit":"Every installed source file compared against frozen base; exactly the five threshold-policy/plumbing files differ",
            "frozen_and_production_protection":True,
            "operator_stop":"Preserved resume_allowed=false",
            "gettingstart":"Main-owned publication/documentation divergence is explicitly excluded from protection; not edited by this task"},
        "runtime":load("runtime.json"),"build_artifacts":load("build_artifacts.json"),
        "interpretation":"Compare latency and recall together, not speed alone. Lowered cutoffs change filtered traversal and may change recall. Only two reverse-order repetitions; no statistical-significance or universal improvement claim. No recall-guided tuning occurred.",
        "limits":["Only Broad n24 timed; no full curve or Extreme1000 campaign",
            "Sparse and empty replays use 32 warm +32 measured-as-validation +32 untimed captures only",
            "No automatic promotion, plots, production updates, third new cutoff, or post-measurement optimization",
            "Direct/fallback counts are per1000 queries for Broad, per32 for sparse; averages explicitly named"],
        "paths":{"source":str(HERE),"toolchain":str(TOOL),"evidence":str(OUTPUT),"base_evidence":str(PARENT)},
        "reproduction":{"instructions":str(HERE/"README.md"),"build":"build_commands.json",
            "native_runs":"Each point's command.json retains exact numactl/config command",
            "sequence":[f"python3 {HERE}/{s}" for s in ("prepare.py --initialize","build.py","small_replay.py","run.py","finalize.py")]}}
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
        "manifest_sha256":sha(OUTPUT/"milestone_manifest.json"),"files":len(manifest)})
    print(json.dumps({"summary":[{k:r[k] for k in ("case","ordinary_ms","qps_at_mean_ms","recall_at_10",
        "avg_second_hop_rows","avg_posting_fallbacks")} for r in summary],"seal":load("report.sha256.json")},indent=2))

if __name__=="__main__":main()
