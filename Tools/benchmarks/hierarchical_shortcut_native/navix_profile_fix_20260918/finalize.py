"""Seal measured evidence without promoting the successor or changing main docs."""
from collections import Counter
import difflib
import json
from pathlib import Path
import shutil
import statistics
import numpy as np
from prepare import HERE,DATA,TOOL,OUTPUT,BASE,FILES,protect,sha,write
from profile import CONTROLS
from run import NATIVE,ACTION

def load(name): return json.loads((OUTPUT/name).read_text())
def main():
    protect()
    summary=load("summary.json")
    profiles=load("profile_summary.json")
    lines=load("profile_lines_summary.json")
    distance=load("distance_profiles.json")
    replays=load("small_replay.json")
    operations=load("operations.json")
    profile_validation=load("profile_work_validation.json")
    assert len(operations["records"])==10 and len(replays)==4 and len(profile_validation)==9
    assert set(profiles)==set(lines)=={"old","graph","failed"}
    rows={(r["case"],r["version"]):r for r in summary}
    deltas={}
    for case in ("navix","posting"):
        old=rows[case,"old"];new=rows[case,"new"]
        deltas[case]={
            "old_ms":old["ordinary_ms"],"successor_ms":new["ordinary_ms"],
            "saved_ms":old["ordinary_ms"]-new["ordinary_ms"],
            "reduction_percent":100*(1-new["ordinary_ms"]/old["ordinary_ms"]),
            "old_runs_ms":old["ordinary_ms_runs"],"successor_runs_ms":new["ordinary_ms_runs"],
            "all_successor_runs_below_all_old_runs":max(new["ordinary_ms_runs"])<min(old["ordinary_ms_runs"]),
            "recall":new["recall_at_10"],"exact_frozen_native_trajectory":True}
    graph=rows["graph","postfilter"]
    goal=deltas["navix"]["all_successor_runs_below_all_old_runs"] and deltas["posting"]["saved_ms"]>0
    attributions={}
    for label,profile in profiles.items():
        cpu=sum(float(run["flags"]["query_thread_cpu_seconds"]) for run in profile["runs"])
        assert all(run["flags"]["dropped"]=="0" and run["flags"]["query_tid"]==run["flags"]["pid"]
                   for run in profile["runs"])
        detailed_lines=load(f"profile_{label}_lines.json")
        source_lines=Counter(dict(detailed_lines["lines"]))
        unresolved=source_lines.pop("unresolved-line",0)
        source_lines.update(dict(distance[label]["lines"]))
        prefix=OUTPUT/f"profile_{label}_r1/nprobe_24"
        native_counts=np.fromfile(str(prefix)+".native.u64",dtype="<u8").reshape(1000,18).sum(axis=0)
        ssd_counts=np.fromfile(str(prefix)+".work.u64",dtype="<u8").reshape(1000,8).sum(axis=0)
        attributions[label]={
            "samples":profile["samples"],"thread_cpu_seconds":cpu,
            "diagnostic_thread_cpu_ms_per_query":cpu/3,
            "actual_sample_delivery_hz":profile["samples"]/cpu,
            "requested_timer_interval_ns":250000,"dropped":0,
            "image_counts":profile["images"],"top_functions":profile["functions"][:12],
            "mapped_native_line_samples":lines[label]["mapped_samples"]-unresolved+distance[label]["samples"],
            "unmapped_native_line_samples":lines[label]["unmapped_samples"]+unresolved-distance[label]["samples"],
            "top_source_lines":source_lines.most_common(30),
            "distance_line_evidence":"distance_profiles.json",
            "absolute_native_work_per_1000_queries":dict(zip(NATIVE,map(int,native_counts))),
            "absolute_ssd_work_per_1000_queries":dict(zip(
                ("read_postings","scanned_vectors","matched_vectors","dedup_skipped_vectors","distances",
                 "posting_page_reads","logical_bytes","physical_bytes"),map(int,ssd_counts))),
            "semantics":"PC instruction residence only; neither stack attribution nor hardware-stall proof"}
        if label!="graph":
            counts=np.fromfile(str(prefix)+".navix.u64",dtype="<u8").reshape(1000,19).sum(axis=0)
            attributions[label]["absolute_policy_work_per_1000_queries"]=dict(zip(ACTION,map(int,counts)))
    oldcpu=attributions["old"]["diagnostic_thread_cpu_ms_per_query"]
    graphcpu=attributions["graph"]["diagnostic_thread_cpu_ms_per_query"]
    oldfuncs=profiles["old"]["functions"]
    selected=[(f,n) for f,n in oldfuncs if (
        "NativeFunctionRef<bool (int)>" in f or
        ("std::_Function_handler<bool (int)" in f and "lambda(int)#3" in f) or
        "ExtraStaticSearcher<float>::CheckValidPosting" in f or
        ("Index<float>::SearchIndex" in f and "lambda(int)#2}::operator()" in f))]
    attribution_proxy=oldcpu*sum(n for _,n in selected)/profiles["old"]["samples"]
    fixed_binaries={
        "old":BASE/"harness/navix-bench",
        "failed":DATA/"toolchains/navix_hotpath_20260918/harness/navix-bench",
        "proper_postfilter_graph":DATA/"toolchains/cost_arbitration_v2_20260917/harness/cost-bench",
        "successor":TOOL/"harness/navix-bench"}
    expected={"old":"94062cff0a03d6457a6ca184709209d7d76a5f2328d7fcf35c5b8410c4b65ecc",
        "failed":"3b136ac4b12c5a09d8339bccae17b37bbca7d1d8f9d121b76ce83a55b129c144",
        "proper_postfilter_graph":"6df6541aed40b26ff5a66b006912a146f404c1df7b75125139c5c30c8a3bbc39"}
    for label,digest in expected.items(): assert sha(fixed_binaries[label])==digest
    diff=[]
    for name,relative in FILES.items():
        previous=BASE/"source"/relative
        if previous.exists():
            before=previous.read_text().splitlines(True)
        else: before=[]
        after=(HERE/name).read_text().splitlines(True)
        diff.extend(difflib.unified_diff(before,after,fromfile="OLD/"+relative,tofile="successor/"+relative))
    (OUTPUT/"targeted_source.patch").write_text("".join(diff))
    assert sha(HERE/"BKTIndex.cpp")==sha(HERE.with_name("navix_posting_20260917")/"BKTIndex.cpp")
    assert sha(TOOL/"source/AnnService/inc/Core/Common/WorkSpace.h")==sha(BASE/"source/AnnService/inc/Core/Common/WorkSpace.h")
    installed={str(p.relative_to(TOOL/"source")):sha(p) for p in sorted((TOOL/"source").rglob("*"))
        if p.is_file() and "Release" not in p.relative_to(TOOL/"source").parts and "__pycache__" not in p.parts}
    write(OUTPUT/"installed_source_manifest.json",installed)
    compile_proof={}
    for name in ("build/AnnService/CMakeFiles/SPTAGLibStatic.dir/flags.make",
                 "harness/CMakeFiles/navix-bench.dir/flags.make"):
        text=(TOOL/name).read_text()
        assert "-O3" in text
        compile_proof[name]=text
    write(OUTPUT/"compile_proof.json",compile_proof)
    report={
        "milestone":HERE.name,"goal_met":goal,
        "success_criterion":"A clearly separated NaviX improvement across the two fresh repetitions, "
            "with shared combined-mode mean improvement and exact frozen behavior",
        "status":"measured hotpath improvement, isolated and unpromoted" if goal else
                 "bounded milestone completed; requested measurable improvement NOT achieved",
        "proper_control":"Frozen cost-v2 H1 result-only postfilter graph; not matched-original unfiltered H1",
        "single_targeted_implementation":True,"fresh_broad":summary,"paired_deltas":deltas,
        "proper_graph_mean_ms":graph["ordinary_ms"],
        "successor_navix_remaining_gap_to_graph_ms":deltas["navix"]["successor_ms"]-graph["ordinary_ms"],
        "profiling":attributions,
        "root_cause":{
            "diagnostic_old_minus_graph_thread_cpu_ms_per_query":oldcpu-graphcpu,
            "old_qualification_callback_residence_proxy_ms_per_query":attribution_proxy,
            "selected_nonoverlapping_native_symbols":selected,
            "evidence":"OLD PCs concentrate in support slot reads, final posting validity, native eligibility, "
                "and collapsed-alias marker access; failed component callback is even hotter. "
                "Distance residence is comparable across frozen graph/OLD, not the source of the added ~0.8ms.",
            "fix":"Replace repeated immutable final posting/exact-own classification with a direct per-head byte; "
                "clone OLD, removing every failed variant component-generation-cache, prefetch, emitter and scratch layer. "
                "No support component lookup, no per-entry generation, no layered callback cache.",
            "preserved":"Distinct posting versus own-static truth; uncached version deletion, native deleted-head checks, "
                "ownGain/result competitiveness, collapsed aliases, visited intermediate behavior, complete rows, "
                "all graph/reference branches, thresholds and budgets.",
            "remaining_cost":"Native qualification/alias checks and mutable own VID/deletion reads still execute. "
                "No graph-parity claim or speculative next implementation.",
            "measured_interpretation":"Reduced immutable predicate evaluations did not establish a reliable latency "
                "improvement: NaviX reverses in the second pair and combined is effectively unchanged. "
                "The frozen PC profiles identify residence in qualification, but do not prove repeated cacheable "
                "evaluations, rather than first evaluations and mandatory mutable/alias checks, own that residence. "
                "This single targeted implementation is not promoted; no next speculative cache or policy change.",
            "uncertainty":"CPU PC sampling cannot distinguish instruction execution from hardware memory-stall time, "
                "and has no stacks. Some native samples lack line mapping; foreign DSOs are separate. "
                "Residence proxy is diagnostic only, not an ordinary latency splice."
        },
        "verification":{
            "native_fixture_and_protocol_commands":load("build_commands.json"),
            "profile_outputs_and_absolute_work":"profile_work_validation.json",
            "ordinary_output_work_validation":"operations.json",
            "small_replays":replays,
            "small_unfilter_zero_qualification_work":all(
                all(v==0 for v in row["qualification_head_phase_mean"].values())
                for row in replays if row["scenario"]=="unfilter"),
            "frozen_protection_passed":True,"production_and_frozen_sources_untouched":True,
            "timed_body_sha256":"9f412c63b71f7310e09180dff0858ccef3957bd20d7a96c34fdb440e3d559d53",
            "index_native_arrays_loaded_once_per_process":True,
            "capture_plain_exact":"Every harness compares timed IDs/distances/SSD work to untimed native capture; "
                "full native/head/own/branch/CSR trajectories also match frozen OLD.",
            "ordinary_instrumentation":"No LD_PRELOAD; native capture=false and profile=false in the shared timed body",
            "qualification_counters":"Untimed head-phase diagnostics, not total allocations and not the success criterion"
        },
        "binary_hashes":{k:{"path":str(p),"sha256":sha(p)} for k,p in fixed_binaries.items()},
        "build_artifacts":load("build_artifacts.json"),
        "reproduction":"README.md and retained command.json/build_commands.json/profile records",
        "limitations":[
            "Only two fresh reverse/interleaved Broad repetitions; no universal or statistical-significance claim.",
            "No expensive extreme1000 campaign; extreme parity is first32 against frozen captures only.",
            "No full unfiltered latency campaign; native fixture and bounded32 default-path parity only.",
            "No coefficients/thresholds/budgets changed, no curves/numeric_mixed/resume, no promotion.",
            "No successor PC campaign: the retained profiles explain the frozen controls, while the successor "
            "is judged by the one paired ordinary timing milestone. Residual successor CPU attribution is uncertain.",
            "Requested 250us CPU timer delivered approximately 1ms samples on this Linux host.",
            "Raw frozen PCs were mapped through O3 -g1 objects only after all executable section bytes matched; "
            "debug links were not executed. GNU addr2line was replaced by one-pass readelf line-matrix decoding.",
            "Initial Python hashlib compatibility and replay-launcher/oracle-label bookkeeping errors were fixed; "
            "valid captures were reused, not rerun. One profile process wall duration and one replay process wall "
            "duration are unavailable; native phase/thread CPU durations remain retained."
        ]}
    write(OUTPUT/"report.json",report)
    for root in (HERE,TOOL/"source"):
        for cache in root.rglob("__pycache__"):
            if cache.is_dir(): shutil.rmtree(cache)
    if (TOOL/"compiler-work").exists(): shutil.rmtree(TOOL/"compiler-work")
    manifest={}
    for root in (HERE,OUTPUT):
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name not in ("milestone_manifest.json","report.sha256.json"):
                manifest[str(path)]=sha(path)
    for path in sorted(TOOL.rglob("*")):
        if path.is_file() and (
            path.suffix in (".a",".so",".json") or "debug_objects" in path.parts or
            path.name in ("navix-bench","cost-bench","native-tests","CMakeCache.txt","flags.make","link.txt")):
            manifest[str(path)]=sha(path)
    write(OUTPUT/"milestone_manifest.json",manifest)
    write(OUTPUT/"report.sha256.json",{"report_sha256":sha(OUTPUT/"report.json"),
        "manifest_sha256":sha(OUTPUT/"milestone_manifest.json"),"files":len(manifest)})
    print(json.dumps({"goal_met":goal,"paired_deltas":deltas,
        "graph_ms":graph["ordinary_ms"],"seal":load("report.sha256.json")},indent=2))
if __name__=="__main__":main()
