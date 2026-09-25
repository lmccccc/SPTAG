"""Mutually exclusive CPU-residence attribution; never optimize or run searches."""
from collections import Counter,defaultdict
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
from profile import HERE,DATA,TOOL,OUT,ROOT,FROZEN,BINARY,sha,write,protect

CATEGORIES={
    "first_match":"First-match-only callback / own-liveness / physical alias qualification",
    "shared_qualification":"Shared support / posting-validity / exact predicate / liveness (caller ambiguous)",
    "visited":"Visited + dedup probe/hash/flag/dispatch (shared CheckAndSet caller ambiguous)",
    "row":"Ordinary adjacency/prefetch, degree and row-state / diagnostic guards",
    "distance":"Distance kernels and distance dispatch (head + final SSD combined)",
    "heaps_admission":"Heaps/frontier/native result + own admission",
    "ssd":"Final ordinary SSD scan / filtering / IO setup (exclusive native PCs)",
    "syscall":"libc syscall return-site (kernel operation/caller unresolved)",
    "memset":"libc memset (visited/result/SSD caller unresolved)",
    "other":"Other / unattributed, including small allocation and setup"}

def category(row):
    f=row["function"];source=row["source"]
    file,_,number=source.rpartition(":")
    n=int(number) if number.isdigit() else -1
    if row.get("foreign_function")=="syscall":return "syscall"
    if "memset" in row.get("foreign_function",""):return "memset"
    if f=="FOREIGN":return "other"
    if ("NativeFunctionRef<bool (int)>" in f and "lambda(int)#13" in f or
        "std::_Function_handler<bool (int), SPTAG::BKT::Index<float>::SearchIndexWithNativeHooks" in f):
        return "first_match"
    if ("SPTAG::SPANN::Index<float>::SearchIndex" in f and
        ("lambda(int)#3" in f or "lambda(int)#2" in f) or
        "CheckValidPosting" in f or "LabelSet::Contains" in f):
        return "shared_qualification"
    if ("OptHashPosVector::" in f or "WorkSpace::CheckAndSetMatch" in f or
        file=="WorkSpace.h" and (n<=320 or 490<=n<=540) or
        file=="BKTIndex.cpp" and 890<=n<=893):
        return "visited"
    if ("DistanceUtils::" in f or "ComputeDistance(" in f or
        "std::_Function_handler<float (float const*" in f):
        return "distance"
    if (file=="RelativeNeighborhoodGraph.h" or
        file=="BKTIndex.cpp" and (875<=n<=905 or 1012<=n<=1060 or n in (580,581,582))):
        return "row"
    if ("DistPriorityQueue::" in f or "::Heap<" in f or
        file in ("Heap.h","QueryResultSet.h","SearchResult.h") or
        "SPTAG::SPANN::Index<float>::SearchIndex" in f and
            ("lambda(int, float const*)#9" in f or "lambda(int, float)#11" in f) or
        file=="BKTIndex.cpp" and (557<=n<=608 or 926<=n<=1007)):
        return "heaps_admission"
    if "ExtraStaticSearcher<float>::SearchIndex(" in f or "BatchReadFile" in f:
        return "ssd"
    return "other"

def main():
    assert not (OUT/"milestone_manifest.json").exists(),"Sealed diagnosis; STOP / IDLE"
    runs=json.loads((OUT/"runs.json").read_text())
    samples=json.loads((OUT/"symbolized_samples.json").read_text())
    assert len(runs)==8 and all(r["all_frozen_payloads_and_traces_exact"] for r in runs)
    assert all(not r["action_mean"][key] for r in runs for key in
        ("sparse_activations","selected_actions","posting_members","posting_fresh_qualified_adds"))
    assert all(not r["native_mean"][key] for r in runs for key in
        ("calls","returns","representative_distances","csr_members","signature_checks","h2_rows","h3_rows"))
    assert all(r["storage_max"]["allocations"]==r["storage_max"]["conversions"]==0 for r in runs)
    assert all(r["native"]["recall"]==.9154 for r in runs)
    trajectory_suffixes=(".ids.i32",".dist.f32",".work.u64",".native.u64",".head.i32",".head.f32",
        ".own.i32",".visited.u64",".evaluated.f64",".posting.u64",".operators.u64")
    trajectory={suffix:sha(OUT/"profile_graph_r1"/("nprobe_24"+suffix)) for suffix in trajectory_suffixes}
    for run in runs:
        for suffix,digest in trajectory.items():
            assert sha(OUT/run["label"]/("nprobe_24"+suffix))==digest,(run["label"],suffix)
    write(OUT/"cross_mode_trajectory.json",{"all_eight_runs_equal":True,"hashes":trajectory})
    runtime=json.loads((OUT/"runtime.json").read_text())
    runtime["dynamic_library_sha256"]={token:sha(token) for line in runtime["ldd"].splitlines()
        for token in line.split() if token.startswith("/") and Path(token).is_file()}
    runtime["compiler"]={"path":"/usr/bin/c++","sha256":sha("/usr/bin/c++"),
        "version":subprocess.check_output(["/usr/bin/c++","--version"],text=True)}
    write(OUT/"runtime.json",runtime)
    frozen_text=(HERE.with_name(FROZEN.name)/"MatchedBench.cpp").read_text()
    import hashlib
    body=frozen_text.split("const auto start = std::chrono::steady_clock::now();",1)[1].split(
        "const auto finish = std::chrono::steady_clock::now();",1)[0]
    assert hashlib.sha256(body.encode()).hexdigest()=="9f412c63b71f7310e09180dff0858ccef3957bd20d7a96c34fdb440e3d559d53"
    timing=[]
    residence={}
    for row in samples:row["category"]=category(row)
    write(OUT/"attributed_samples.json",samples)
    for run in runs:
        fields=run["flags"];n=int(fields["samples"])
        cpu=float(fields["query_thread_cpu_seconds"])
        record={"run":run["label"],"kind":run["kind"],"mode":run["mode"],"rep":run["rep"],
            "queries":1000,"thread_cpu_ms_per_query":cpu,
            "elapsed_ms_per_query":float(fields["window_elapsed_seconds"]),
            "native_elapsed_ms_per_query":run["native"]["mean_latency_ms"],
            "requested_ns":int(fields["interval_ns"]),"samples":n,"dropped":int(fields["dropped"]),
            "wrong_thread_samples":int(fields["wrong_thread_samples"]),
            "effective_cpu_us_per_sample":cpu*1e6/n if n else None}
        timing.append(record)
        if n:
            rows=[r for r in samples if r["run"]==run["label"]]
            assert len(rows)==n
            counts=Counter(r["category"] for r in rows)
            values={key:cpu*counts[key]/n for key in CATEGORIES}
            assert abs(sum(values.values())-cpu)<1e-12
            residence[run["label"]]={"counts":dict(counts),"cpu_ms_per_query":values,
                "thread_cpu_ms_per_query":cpu,"samples":n,
                "source_mapped":sum(r["source"]!="unresolved" for r in rows),
                "foreign_named":sum(r.get("foreign_function","??")!="??" for r in rows)}
    table=[]
    for key,label in CATEGORIES.items():
        entry={"category":key,"description":label}
        variance=0
        for mode in ("graph","match"):
            group=[v for name,v in residence.items() if name.startswith("profile_"+mode)]
            values=[v["cpu_ms_per_query"][key] for v in group]
            entry[mode+"_ms"]=statistics.mean(values)
            entry[mode+"_range_ms"]=[min(values),max(values)]
            entry[mode+"_samples"]=sum(v["counts"].get(key,0) for v in group)
            for v in group:
                p=v["counts"].get(key,0)/v["samples"]
                variance+=v["thread_cpu_ms_per_query"]**2*p*(1-p)/v["samples"]/4
        entry["delta_ms"]=entry["match_ms"]-entry["graph_ms"]
        entry["conditional_counting_delta_95_halfwidth_ms"]=1.96*math.sqrt(variance)
        table.append(entry)
    write(OUT/"cpu_attribution.json",{"categories":table,"per_run":residence,
        "conversion":"For each window category_ms/query = thread_CPU_seconds * 1000/1000_queries * category_samples/all_samples; average two windows equally. Includes every sample once; no parent/child double count.",
        "uncertainty":"Ranges descriptive. Counting CI assumes independent unbiased PC samples and fixed measured CPU; periodic signal sampling is correlated and can alias, so this is NOT a validated confidence interval or optimization speedup."})
    write(OUT/"timings.json",timing)
    coverage={}
    for mode in ("graph","match"):
        rows=[r for r in samples if r["run"].startswith("profile_"+mode)]
        coverage[mode]={"samples":len(rows),"native_image":sum(r["image"]==str(BINARY) for r in rows),
            "native_source_line":sum(r["source"]!="unresolved" for r in rows),
            "images":dict(Counter(r["image"] for r in rows)),
            "foreign_named":sum(r.get("foreign_function","??")!="??" for r in rows),
            "sampled_threads":1,"other_thread_samples":0,
            "sampler_image_samples":sum("sampler_" in r["image"] for r in rows)}
    write(OUT/"coverage.json",coverage)
    function_groups=defaultdict(Counter)
    for row in samples:
        function_groups[row["category"]][(row["function"],row["source"],row["offset"])]+=1
    write(OUT/"category_audit.json",{k:[{"function":a,"source":b,"offset":c,"samples":n}
        for (a,b,c),n in v.most_common()] for k,v in function_groups.items()})
    predicates={
        "support_tag_compare_pc":lambda r:r["offset"]=="0x37df9b" and r["image"]==str(BINARY),
        "own_version_deleted_branch_pc":lambda r:r["offset"]=="0x3c4d55" and r["image"]==str(BINARY),
        "own_after_vid_version_dataset_pc":lambda r:r["offset"]=="0x3c4cdc" and r["image"]==str(BINARY),
        "physical_alias_marker_pc":lambda r:r["offset"] in ("0xdaa37","0xdaa3a") and r["image"]==str(BINARY),
        "ordinary_degree_update_region":lambda r:r["image"]==str(BINARY) and 0xc5f4a<=int(r["offset"],16)<=0xc5f75,
        "match_injected_guard_region":lambda r:r["image"]==str(BINARY) and 0xc5f7a<=int(r["offset"],16)<=0xc5f87,
        "shared_slot_probe_line":lambda r:r["source"]=="WorkSpace.h:115"}
    roles={}
    for name,predicate in predicates.items():
        roles[name]={}
        for mode in ("graph","match"):
            values=[];total=0
            for rep in (1,2):
                run=f"profile_{mode}_r{rep}"
                selected=sum(predicate(r) for r in samples if r["run"]==run)
                total+=selected
                values.append(residence[run]["thread_cpu_ms_per_query"]*selected/residence[run]["samples"])
            roles[name][mode]={"samples":total,"ms_per_query":statistics.mean(values),"range_ms":[min(values),max(values)]}
    write(OUT/"pc_roles.json",{"scope":"Selected nonexhaustive subsets of main categories; DO NOT add to main table",
        "no_hit_miss_split":"Shared slot load/test cannot separate hit/miss. Callback-only ELF functions can be separate.",
        "roles":roles})
    agg={}
    for kind in ("control","profile"):
        for mode in ("graph","match"):
            group=[r for r in timing if r["kind"]==kind and r["mode"]==mode]
            agg[kind+"_"+mode]={field:statistics.mean(r[field] for r in group) for field in
                ("thread_cpu_ms_per_query","elapsed_ms_per_query","native_elapsed_ms_per_query")}
    tax={kind:agg[kind+"_match"]["thread_cpu_ms_per_query"]-agg[kind+"_graph"]["thread_cpu_ms_per_query"]
        for kind in ("control","profile")}
    perturb={mode:100*(agg["profile_"+mode]["thread_cpu_ms_per_query"]/
        agg["control_"+mode]["thread_cpu_ms_per_query"]-1) for mode in ("graph","match")}
    shared={key:next(x for x in table if x["category"]==key) for key in CATEGORIES}
    qualification=sum(shared[key]["match_ms"] for key in ("first_match","shared_qualification"))
    qualification_delta=sum(shared[key]["delta_ms"] for key in ("first_match","shared_qualification"))
    work=runs[3]
    refs={
        "visited_slot":f"{ROOT}/source/AnnService/inc/Core/Common/WorkSpace.h:111-164,253-313,506-527",
        "first_match_and_aliases":f"{ROOT}/source/AnnService/src/Core/BKT/BKTIndex.cpp:1654-1675",
        "callback_setup_and_capture":f"{ROOT}/source/AnnService/NativeSupplier.h:16-44",
        "posting_support":f"{ROOT}/source/AnnService/src/Core/SPANN/SPANNIndex.cpp:6280-6340,7037-7047,7367-7370",
        "support_rows":f"{ROOT}/source/AnnService/inc/Core/SPANN/LimitedTagSupport.h:937-959,1093-1106",
        "posting_validity":f"{ROOT}/source/AnnService/inc/Core/SPANN/ExtraStaticSearcher.h:5488-5497",
        "own_live_eligibility":f"{ROOT}/source/AnnService/src/Core/SPANN/SPANNIndex.cpp:7384-7391",
        "ordinary_native_own_admission":f"{ROOT}/source/AnnService/src/Core/SPANN/SPANNIndex.cpp:7092-7137",
        "ordinary_row":f"{ROOT}/source/AnnService/src/Core/BKT/BKTIndex.cpp:875-905,1012-1062",
        "ordinary_predicate_counters":f"{ROOT}/source/AnnService/src/Core/BKT/BKTIndex.cpp:557-603",
        "buffered_aio":f"{ROOT}/source/AnnService/src/Helper/AsyncFileReader.cpp:130-156",
        "sampler":str(HERE/"ClockAudit.cpp")}
    write(OUT/"source_references.json",refs)
    report={"status":"COMPLETE_STOP_IDLE","optimization_implemented":False,"production_promoted":False,
        "benchmark_processes":8,"extra_profile_windows":0,"ablation":False,
        "frozen_report_sha256":sha(FROZEN/"report.json"),"binary_sha256":sha(BINARY),
        "timed_body_sha256":hashlib.sha256(body.encode()).hexdigest(),
        "timings":timing,"means":agg,"thread_cpu_tax_ms":tax,"sampler_perturbation_percent":perturb,
        "categories":table,"qualification_match_ms":qualification,"qualification_delta_ms":qualification_delta,
        "sample_count":len(samples),"zero_drops":all(t["dropped"]==0 for t in timing),
        "drop_definition":"preallocated-buffer overflow only; timer overruns/coalescing not counted",
        "coverage":coverage,"cross_mode_trajectory_exact":True,
        "actual_auxiliary_posting_zero":True,
        "ordinary_ssd_postings_per_query":work["native"]["postings_per_query"],
        "ordinary_ssd_page_reads_per_query":work["native"]["posting_page_reads_per_query"],
        "all_frozen_outputs_and_traces_exact":True,"recall":.9154,
        "first_match_per_query":work["match_mean"]["first_match_evaluations"],
        "reused_bit_reads_per_query":work["match_mean"]["reused_bit_reads"],
        "native_result_predicates_per_query":work["operator_mean"]["all_native_result_predicate_calls"],
        "extra_alias_component_evaluations_per_query":work["match_mean"]["match_component_calls"]-work["match_mean"]["first_match_evaluations"],
        "source_references":refs,
        "principal_finding":"Residual total includes ordinary H1/distance/final SSD work; extra same-trajectory match cost is concentrated in first-match physical qualification and shared support/validity, not visited allocations or hierarchy postings.",
        "next_fix_recommendation_only":"Specialize and consolidate the first-insertion static qualification chain: avoid redundant posting-validity work and unnecessary layered callback/data-view traversal while preserving live own OR posting, aliases, native component admission and nonmatching bridges. Prove exact trajectory first. Do not infer benefit from call counts or introduce a free precomputed mask.",
        "limitations":["PC residence does not prove cache misses, DRAM, or stalls.",
            "Shared support/validity and liveness function samples cannot distinguish first-match from existing result admission callers.",
            "Visited CheckAndSet and memset caller tables cannot be separated by leaf-PC sampling.",
            "libc syscall return-site may represent kernel CPU; no kernel callchain or syscall-number attribution.",
            "Only two windows per mode; periodic sampling may alias. Conditional counting intervals are illustrative.",
            "No overall allocation-count proof; only visited-backend no-allocation proof."]}
    lines=[
        "# 冻结 visited-storage：1ms 花在哪里（仅归因，未优化）",
        "",
        "**结论：不是层级 posting 偷跑，也不是 visited 后端又在分配。**",
        f"同一冻结二进制，非采样控制的线程 CPU：graph **{agg['control_graph']['thread_cpu_ms_per_query']:.6f} ms/query**，"
        f"match **{agg['control_match']['thread_cpu_ms_per_query']:.6f}**，额外 **{tax['control']:.6f} ms**。"
        f"采样窗口分别 **{agg['profile_graph']['thread_cpu_ms_per_query']:.6f} / {agg['profile_match']['thread_cpu_ms_per_query']:.6f} ms**。",
        "",
        "这里“没有访问 posting”必须限定为 **辅助 H2/H3 posting/CSR**：实际四个采样运行均 calls/returns、"
        "signature checks、representative distances、CSR members、H2/H3 rows、sparse activation = 0。"
        "**完整查询仍读最终普通 SSD postings**：每 query 24 postings，133.836 page reads，"
        "548192.256 physical bytes，882.359 scanned occurrences，471.443 distance computations；"
        "graph/match 完全相同。不能把辅助 posting=0 说成最终 SSD IO=0。",
        "",
        "## 绝对 CPU residence（互斥，ms/query）",
        "",
        "每个窗口实际线程 CPU × 该类别样本数/全部样本数，再平均两次。所有样本恰好计一次，"
        "未解析部分仍分母内；不是用 wall time 或请求的 200us 乘样本数。共享函数不虚构调用者。",
        "",
        "| Category | graph | match | delta | samples graph/match |",
        "|---|---:|---:|---:|---:|"]
    for row in table:
        lines.append(f"| {row['description']} | {row['graph_ms']:.6f} | {row['match_ms']:.6f} | {row['delta_ms']:+.6f} | {row['graph_samples']}/{row['match_samples']} |")
    lines.extend([
        f"| **Total actual thread CPU** | **{agg['profile_graph']['thread_cpu_ms_per_query']:.6f}** | **{agg['profile_match']['thread_cpu_ms_per_query']:.6f}** | **{tax['profile']:+.6f}** | 1475/2053 |",
        "",
        f"**first-match-only + shared qualification** 共约 **{qualification:.6f} ms/query**，相对 graph 增量约 "
        f"**{qualification_delta:.6f} ms**。这是样本证据，不是由 1943.817 次调用直接推算。"
        "shared qualification 含原有 native result admission 的调用，不能全部冠名 first-match。"
        "其它类别可同时下降：早做 qualification 改变数据触达次序，PC 随机误差也存在；"
        "因此这些分项不是相互独立的可节省延迟，也不能承诺修掉其中一项获得同等加速。",
        "",
        "## 实测窗口及扰动",
        "",
        "| Run | CPU ms/q | elapsed ms/q | native elapsed ms/q | samples | effective CPU us/sample |",
        "|---|---:|---:|---:|---:|---:|"])
    for r in timing:
        cadence=f"{r['effective_cpu_us_per_sample']:.3f}" if r["samples"] else "—"
        lines.append(f"| {r['run']} | {r['thread_cpu_ms_per_query']:.9f} | {r['elapsed_ms_per_query']:.9f} | {r['native_elapsed_ms_per_query']:.9f} | {r['samples']} | {cadence} |")
    lines.extend([
        "",
        f"请求 200us，实际约 1ms，不能声称 200us 分辨率。共 {len(samples)} 个样本，"
        "buffer-overflow drop=0，wrong-thread=0；query TID=PID。"
        "未计kernel timer overrun/coalescing；不能宣称请求的每个tick都送达。"
        "effective是CPU/sample平均，未记录每次间隔/jitter。CPU 与 elapsed 接近，不能据此作 cache/DRAM 判断。",
        f"采样/同审计但无 timer 控制的平均 CPU 差：graph {perturb['graph']:+.3f}%，match {perturb['match']:+.3f}%。"
        "这包括过程间漂移，不是精确 sampler overhead。native elapsed 多含边界 module-map 导出，"
        "因此另报不含这项导出的 audit elapsed；固定两次边界开销不作逐 callback 计时。",
        "",
        "确切顺序：control graph/match → profile graph/match → profile match/graph → control match/graph。"
        "仅八个过程，无额外窗口/ablation/curve/sparse/posting-mode run。原生 INI 字节不变：n24、top10、"
        "MaxCheck2048、hierarchy512、ratio .666666、pages15、offset0、thread1、NUMA CPU/memory2、"
        "原 matched buffered_view。每次1000 warmup + 1000非计时 storage proof + 1000主体 + 1000非计时capture。"
        "只有普通主体边界5–6启用 timer，load/warmup/storage proof/capture排除；边界capture/profile都为false。"
        "采样器只记录预分配数组中的 RIP，线程CPU timer只投递给主查询线程；handler无分配/锁/展开/符号化。"
        "额外 syscall(gettid) 仅验证 handler TID。没有后台 benchmark。",
        f"覆盖：graph {coverage['graph']['native_source_line']}/{coverage['graph']['samples']}、"
        f"match {coverage['match']['native_source_line']}/{coverage['match']['samples']}有已证明等价的native source line；"
        "native image样本1160/1748，libc315/303，match另有libstdc++2。"
        "native source未覆盖13/7不伪造行号，其中5个可按唯一IO函数符号分类，其余15个保留Other；"
        "外部image按其本地ELF/debug symbol和hash独立解析。"
        "两模式sampler-image样本均0；其它线程未采样，不将其CPU折算给查询线程。",
        "",
        "两次范围和每类条件 counting 95% half-width 在 `cpu_attribution.json`。后者假设独立、无偏采样；"
        "周期信号可能相关或混叠，不能把这个条件区间当统计显著性证明。只有两个重复的范围是描述性信息。",
        "",
        "## 第一访问的真实链路（冻结源）",
        "",
        "1. `WorkSpace.h:111–164,304–313,506–527`：ID+1 hash 到 uint32 visited slot；"
        "probe未命中才调用 `m_matchPredicate`（std::function），命中读取高位flag并返回。"
        "key低31与高位分离；两表原容量、navigation/result dedup各512KiB/4byte。"
        "八个过程共8000 capture-off warmed storage-proof query：backend allocations/conversions均0。"
        "这不等于全查询无malloc。",
        "2. `BKTIndex.cpp:1654–1675`：验证physical ID、删除集合，再 NativeFunctionRef eligibility；"
        "原head不匹配才读graph最后一格alias marker，必要时枚举BKTree aliases并检查live eligibility。"
        "图marker读取本身也有明显样本，不可把它都说成alias枚举次数。",
        "3. `SPANNIndex.cpp:7367–7370,7384–7391`：posting qualification OR live-own exact qualification。"
        "posting链走secondLevelHeadAdmission→有限tag支持→CheckValidPosting，再evaluatePosting的"
        "CheckValidPosting。后者只读m_listInfos[head].listEleCount等metadata，不是CSR或posting内容扫描。"
        "own分支读m_vectorTranslateMap[head]、version count/deleted byte和head attributes/DNF。"
        "`LimitedTagSupport.h:937–959`支持row逐slot比较，额外支持若存在才走bitmap/offset/tag；"
        "本数据load日志为2 base slots/head、0 extra supports。",
        "4. `NativeSupplier.h:16–44`：capture=false使diagnostics/auditVisited=false；"
        "mode=match没有posting回调，没有supplier对象初始化。native resultFilter/posting与own admission"
        "仍在原站点独立执行，221.716 result-predicate/query未变；first-match1943.817，reuse1345.058。",
        "capture中matchComponentCalls=firstMatchEvaluations=1943.817，因此这1000-query集的额外alias候选"
        "evaluation为0；并不等于没有alias-marker检查。相同原生路径仍有head_distances2070.68、"
        "routing_distances161.28、checked1920.405、queue offers1869.247/query。"
        "这些是真实普通H1成本，辅助CSR为0不会消除它们。",
        "",
        "访问局部性只能作结构描述：邻居行和support row内部顺序，head/VID索引随图路径可能分散；"
        "visited是hash随机访问潜力，own含head→global VID→version indirection。"
        "**PC只能说明指令附近residence；不能证明具体load miss、DRAM latency或stall cycles。**",
        "",
        "## 内联、计数器与未能细分的部分",
        "",
        "`category_audit.json`逐PC列分类，`sampled_disassembly.txt`、`symbols_native.tsv`、"
        "`symbolized_samples.json`保留原函数/offset/source映射；无父子重复计数。"
        "原始ucontext PC、每窗口开始/结束/proc maps、binary/DSO/build/config hash完整保留。",
        "高频PC：`0x37df9b` support tag compare附近（LimitedTagSupport:951），"
        "`0x3c4d55` live version分支（SPANN:7389，前一条为deleted-byte compare），"
        "`0x3c4cdc` own VID之后version dataset元数据（Dataset:321），"
        "`0xdaa37` physical head alias-marker检查（BKT:1667）。"
        "这些不是仅仅std::function调用指令：真实metadata/qualification路径在占CPU。",
        "`0xd8438/0xd843c`是slot load/test（WorkSpace:115），命中与未命中都走这里，"
        "不能把这些样本都拆成callback miss或flag hit。flag路径可由反汇编分支确认，"
        "但没有充分独立样本支撑单列hit成本。first-match专属函数与probe样本可以分开。",
        "`pc_roles.json`给出这些明确PC/短区间的非穷尽绝对residence，都是主表内的子集，不能再相加。"
        "其中已隔离row-degree update区间仅10个match样本（约0.0050ms/query），不是把整个row/prefetch类别"
        "都归为可删除计数器。",
        "`0xc5f4a–0xc5f75`确有row degree/eligible/eligibleVisited更新；"
        "`0xc5f7a–0xc5f87`是match/injected状态分支。"
        "`BKTIndex.cpp:1012–1062,557–603` capture-off仍建立/更新Decision及ordinaryObservation"
        "checks/passes、完成行并评估门槛；即使match没有posting回调也不是零row账本。"
        "这与禁用的oracle/vector traces/诊断累计counter不同，不能说所有instrumentation都消失。"
        "它们可解析的residence远小于资格链，且部分是posting语义需要的状态，不作无条件删除建议。",
        "SnapshotScope的immutableQueries TLS变化、Last().native移动赋值、callback对象"
        "构建清理仍存在，但本次未见它们是主导。libc少量malloc/free样本留在Other；"
        "未测总体alloc次数，不从visited allocation=0推出无其它allocation。",
        "",
        "libc dominant PC `0x11e90d`在syscall返回stub；buffered final SSD路径源码有"
        "io_submit/io_getevents，实际最终posting工作非零，但仅leaf PC不能断言具体syscall、"
        "等待时间或kernel栈。把它单列combined unresolved，不错归为predicate或DRAM。"
        "memset有可识别指令，但无法区分visited/result/SSD buffer，亦不强行分摊。",
        "",
        "## 正确性、符号及不可变性",
        "",
        "所有8次IDS、float distances、SSD工作、native工作、heads/own、visited/evaluated/"
        "decisions/rows/match/storage二进制trace与各自冻结普通基线逐字节相同；"
        "graph/match共同trajectory保持，Recall@10=.9154，1000/1000结果有效。"
        "校验包含最终精确tag predicate、无失败、固定唯一load、full-row oracle等原验证器，"
        "未执行祖先prepare/run main，不会继承Broad posting/sparse campaign。",
        "使用原冻结ELF interpreter --preload加载新诊断DSO，没有LD_PRELOAD环境、"
        "没有搜索/数据override；perf_event_paranoid只读检查，不改权限/sysctl/caps。"
        "没有重建核心或改普通主体，原timed-body SHA为"
        "`9f412c63b71f7310e09180dff0858ccef3957bd20d7a96c34fdb440e3d559d53`。",
        "BKT/SPANN/DistanceUtils对象用原Release优化flag仅加-g1离线编译，所有可执行section"
        "与原object逐byte相同，symbol tables相同；新离线debug link的所有可执行section亦"
        "与冻结binary相同。debug link从未执行。没有source/Release/CMakeCache克隆覆写。"
        "所有3528样本落在其记录module的可执行map范围；ASLR base+offset逐个验证，"
        "仅unique bounded ELF symbol映射source，歧义保留unattributed。",
        "初次离线symbol脚本漏Path import在编译前失败；修正后完成。随后objdump -l"
        "过慢而终止，只重做离线symbol/disassembly（不加-l），没有重跑任何测量点。",
        "",
        "## 下一步（仅建议，未实施）",
        "",
        report["next_fix_recommendation_only"],
        "",
        "优先审视重复CheckValidPosting和first-match own/liveness数据视图/层叠dispatch，"
        "不要再以visited后端分配或辅助posting扫描为假设，也不要据1944调用次数说predicate必然主导。"
        "若未来做优化，必须保留静态posting OR own（含live aliases）、native分项admission、"
        "普通非匹配bridge、原INI/预算/阈值，先验exact trajectory再做同核反序延迟对照。",
        "",
        "命令见源目录README以及每point execution.json / build_*.json / debug_objects/*.json。"
        "前后验证18883 protected files，其中当前385 sealed artifacts，差异0；"
        "production/dirty sources、Release/_SPTAG.so、inputs/graphs/plots/OPERATOR_STOP未改。",
        "",
        "**完成：STOP / IDLE。无优化、无promotion、无阈值/策略修改、无后续campaign。**",
        ""])
    (OUT/"REPORT.md").write_text("\n".join(lines))
    write(OUT/"report.json",report)
    write(OUT/"operations.json",{"benchmark_processes":8,"source_changes":"diagnostic directory only",
        "core_or_harness_changes":False,"no_optimization":True,"no_ablation":True,
        "offline_retries":["missing Path import before compilation","stopped slow objdump -l; reused exact debug objects, plain disassembly"],
        "no_benchmark_reruns":True,"no_privilege_changes":True,"no_downloads":True,"no_commits":True})
    print(json.dumps({"tax":tax,"qualification_ms":qualification,"qualification_delta":qualification_delta,
        "perturbation_percent":perturb,"samples":len(samples)},indent=2))
    if "--no-seal" in sys.argv:return
    # Seal only after report and numerical validation, including predecessor hashes.
    protection=protect()
    write(OUT/"protection_final.json",protection)
    write(OUT/"STOP_IDLE.json",{"state":"IDLE","resume_allowed":False,"reason":"bounded CPU diagnosis complete; no optimization authorized"})
    write(OUT/"completion.json",{"status":"COMPLETE_STOP_IDLE","report":str(OUT/"REPORT.md"),
        "report_json_sha256":sha(OUT/"report.json"),"report_md_sha256":sha(OUT/"REPORT.md"),
        "verified_frozen_artifacts":protection["sealed_artifacts"],"verified_protected_files":protection["verified_files"],
        "sample_count":len(samples),"benchmark_processes":8,"optimization_implemented":False,"promotion":False})
    files={}
    for folder in (HERE,TOOL,OUT):
        for path in folder.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and "compiler-work" not in path.parts:
                files[str(path)]=sha(path)
    write(OUT/"milestone_manifest.json",files)
    write(OUT/"report.sha256.json",{"report.json":sha(OUT/"report.json"),"REPORT.md":sha(OUT/"REPORT.md"),
        "completion.json":sha(OUT/"completion.json"),"milestone_manifest.json":sha(OUT/"milestone_manifest.json")})
if __name__=="__main__":main()
