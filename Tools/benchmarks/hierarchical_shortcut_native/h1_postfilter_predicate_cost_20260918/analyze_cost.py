"""Controlled CPU deltas plus exclusive leaf-PC residence, not counts-to-time inference."""
from collections import Counter,defaultdict
import json
import math
import statistics
from prepare import HERE,TOOL,OUTPUT,sha,write

CATEGORIES={
    "support":"Routing support predicate/callback (separate attribute rows)",
    "first_dispatch":"First-insertion callback-only dispatch leaf",
    "visited":"Visited/result-dedup hash/probe/flag and workspace dispatch",
    "row":"Ordinary prefetch/row degree, statistics and guards",
    "admission":"Native graph marker, heaps/frontier/result admission",
    "distance":"Distance kernels/dispatch, H1 and final SSD combined",
    "ssd":"Exclusive final SSD scan/filter/IO setup",
    "syscall":"libc syscall return PC; operation/caller unresolved",
    "memset":"memset; visited/result/SSD caller unresolved",
    "other":"Other/unattributed"}

def category(row):
    function=row["function"]
    file,_,line=row["source"].rpartition(":")
    n=int(line) if line.isdigit() else -1
    if row.get("foreign_function")=="syscall": return "syscall"
    if "memset" in row.get("foreign_function",""): return "memset"
    if function=="FOREIGN": return "other"
    if ("LimitedTagSupport" in function or file=="LimitedTagSupport.h" or
        "SPTAG::SPANN::Index<float>::SearchIndex" in function and "{lambda(int)#11}" in function):
        return "support"
    if "std::_Function_handler<bool (int), SPTAG::BKT::Index<float>::SearchIndexWithNativeHooks" in function:
        return "first_dispatch"
    if "DistanceUtils::" in function or "std::_Function_handler<float (float const*" in function:
        return "distance"
    if "DistPriorityQueue::" in function or "::Heap<" in function or file in ("Heap.h","QueryResultSet.h","SearchResult.h"):
        return "admission"
    if ("OptHashPosVector::" in function or "WorkSpace::CheckAndSetMatch" in function or
        file=="WorkSpace.h" and (n<=320 or 490<=n<=540)):
        return "visited"
    if (file=="RelativeNeighborhoodGraph.h" or
        file=="BKTIndex.cpp" and (866<=n<=893 or 1004<=n<=1057 or 584<=n<=586)):
        return "row"
    if (file=="BKTIndex.cpp" and (558<=n<=605 or 922<=n<=1000 or 1100<=n<=1170)):
        return "admission"
    if "ExtraStaticSearcher<float>::SearchIndex(" in function or "BatchReadFile" in function:
        return "ssd"
    return "other"

def main():
    runs=json.loads((OUTPUT/"cost_runs.json").read_text())
    samples=json.loads((OUTPUT/"symbolized_samples.json").read_text())
    assert len(runs)==10 and all(r["all_control_trajectory_exact"] for r in runs)
    timings=[]
    for r in runs:
        f=r["flags"]
        timings.append({"run":r["label"],"kind":r["kind"],"control":r["control"],"rep":r["rep"],
            "queries":1000,"cpu_ms_per_query":float(f["query_thread_cpu_seconds"]),
            "window_elapsed_ms_per_query":float(f["window_elapsed_seconds"]),
            "native_elapsed_ms_per_query":r["native"]["mean_latency_ms"],
            "samples":int(f["samples"]),"buffer_drops":int(f["dropped"]),
            "wrong_thread_samples":int(f["wrong_thread_samples"]),"requested_period_ns":int(f["interval_ns"]),
            "effective_cpu_us_per_sample":float(f["query_thread_cpu_seconds"])*1e6/int(f["samples"]) if int(f["samples"]) else None,
            "actual_callbacks_per_query":r["actual_predicate_callbacks_per_query"]})
    means={}
    for control in ("A","Aprime","B","bit"):
        pair=[r for r in timings if r["kind"]=="control" and r["control"]==control]
        means[control]={"cpu_ms":statistics.mean(r["cpu_ms_per_query"] for r in pair),
            "cpu_ms_runs":[r["cpu_ms_per_query"] for r in pair],
            "native_elapsed_ms":statistics.mean(r["native_elapsed_ms_per_query"] for r in pair),
            "native_elapsed_ms_runs":[r["native_elapsed_ms_per_query"] for r in pair],
            "window_elapsed_ms_runs":[r["window_elapsed_ms_per_query"] for r in pair],
            "actual_callbacks_per_query":pair[0]["actual_callbacks_per_query"]}
    deltas={}
    for name,left,right in (("actual_predicate_path","Aprime","A"),("remaining_admission","B","Aprime"),
                            ("bit_over_original","bit","B"),("bit_over_predicate_only","bit","Aprime"),
                            ("bit_over_shortcut","bit","A")):
        deltas[name]={"cpu_ms":means[left]["cpu_ms"]-means[right]["cpu_ms"],
            "paired_cpu_ms":[a-b for a,b in zip(means[left]["cpu_ms_runs"],means[right]["cpu_ms_runs"])],
            "percent_of_right":100*(means[left]["cpu_ms"]/means[right]["cpu_ms"]-1),
            "left":left,"right":right}
    for r in samples: r["category"]=category(r)
    write(OUTPUT/"attributed_samples.json",samples)
    residence={}
    for t in timings:
        if t["kind"]!="profile": continue
        rows=[r for r in samples if r["run"]==t["run"]]
        assert len(rows)==t["samples"] and not t["buffer_drops"] and not t["wrong_thread_samples"]
        counts=Counter(r["category"] for r in rows)
        residence[t["control"]]={"samples":len(rows),"cpu_ms":t["cpu_ms_per_query"],
            "counts":dict(counts),"cpu_ms_by_category":{c:t["cpu_ms_per_query"]*counts[c]/len(rows) for c in CATEGORIES},
            "source_line_coverage":sum(r["source"]!="unresolved" for r in rows),
            "native_image_samples":sum(r["image"]==str(TOOL/"harness/postfilter-bench") for r in rows),
            "sampled_vs_control_cpu_percent":100*(t["cpu_ms_per_query"]/means[t["control"]]["cpu_ms"]-1)}
    table=[]
    for key,description in CATEGORIES.items():
        a,b=residence["Aprime"],residence["bit"]
        variance=0
        for r in (a,b):
            p=r["counts"].get(key,0)/r["samples"]
            variance+=r["cpu_ms"]**2*p*(1-p)/r["samples"]
        table.append({"category":key,"description":description,
            "Aprime_ms":a["cpu_ms_by_category"][key],"bit_ms":b["cpu_ms_by_category"][key],
            "delta_ms":b["cpu_ms_by_category"][key]-a["cpu_ms_by_category"][key],
            "Aprime_samples":a["counts"].get(key,0),"bit_samples":b["counts"].get(key,0),
            "conditional_counting_95_halfwidth_ms":1.96*math.sqrt(variance)})
    roles={}
    for name,source in (("native_collapsed_marker","BKTIndex.cpp:931"),
                        ("support_base_slot_compare","LimitedTagSupport.h:951"),
                        ("physical_degree_updates","BKTIndex.cpp:885"),
                        ("legacy_optional_row_checks","BKTIndex.cpp:585")):
        roles[name]={}
        for control in ("Aprime","bit"):
            selected=[r for r in samples if r["run"]=="profile_"+control+"_r1" and r["source"]==source]
            r=residence[control]
            roles[name][control]={"samples":len(selected),"cpu_ms":r["cpu_ms"]*len(selected)/r["samples"],
                "PCs":dict(Counter(x["offset"] for x in selected)),"source":source}
    audit=defaultdict(Counter)
    for r in samples: audit[r["category"]][(r["function"],r["source"],r["offset"])]+=1
    write(OUTPUT/"category_audit.json",{k:[{"function":f,"source":s,"PC":pc,"samples":n}
        for (f,s,pc),n in rows.most_common()] for k,rows in audit.items()})
    result={"timings":timings,"control_means":means,"controlled_deltas":deltas,
        "sampled_residence":table,"per_sampled_window":residence,"PC_roles_nonadditive":roles,
        "same_core_binary_sha256":sha(TOOL/"harness/postfilter-bench"),
        "causal_scope":{
            "Aprime_minus_A":"Real predicate/callback/support path plus its existing dispatch/guard scaffolding; no later admission and no bit row statistics.",
            "B_minus_Aprime":"Remaining result-admission path and different early-return branch; NOT a pure predicate comparison.",
            "bit_minus_Aprime":"Bit plumbing, extra74.570 tree callbacks, full row accounting, native admission and ordering interactions together.",
            "not_instruction_cost":"No per-call clocks, no volatile writes, no fake predicates or precomputed masks.",
            "timing_windows":"CPU-audited controls have no sampling timer. Native elapsed includes finish-boundary map audit overhead; thread CPU window excludes map dumps.",
            "PC_conversion":"Each leaf PC sample belongs to exactly one category; category CPU = actual window thread CPU * sample fraction. Never add PC-role subsets again.",
            "uncertainty":"One sampled1000-query window per relevant control. Periodic samples correlated/aliased; conditional counting halfwidths are illustrative, not validated confidence intervals.",
            "drop_scope":"Zero buffer overflow/wrong-thread samples; timer coalescing/overruns were not counted.",
            "no_cache_claim":"Source/disassembly identifies addresses and operations, not cache misses, DRAM latency or branch mispredictions."},
        "finding":{
            "predicate_path_measurable":True,
            "predicate_not_all_bit_cost":True,
            "remaining_bit_CPU_not_causally_resolved":True,
            "static_capture_off_counters":"ordinaryObservation->checks/passes update whenever bit full-row observation exists, even with capture=false. Degree/eligible/eligibleVisited also update. Nothing was disabled.",
            "marker_warning":"BKTIndex.cpp:931 is ORIGINAL native collapsed-group marker admission, not a newly added alias qualification.",
            "sample_warning":"Residence increases at support and native marker PCs coincide with decreases at unchanged distance kernels; do not interpret all positive deltas as new instructions.",
            "no_optimization_implemented":True}}
    write(OUTPUT/"cost_attribution.json",result)
    print(json.dumps({"means":means,"deltas":deltas,"sampled_residence":table},indent=2))

if __name__=="__main__":main()
