"""Seal exactly one fixed NaviX/posting milestone, including honest limitations."""
import json
from pathlib import Path
from prepare import HERE,TOOL,OUTPUT,MATCHED,FILES,sha,write
from run import protect,freeze,require,NATIVE,ACTION

def main():
    fingerprint=protect();runtime=freeze()
    summary=json.loads((OUTPUT/"summary.json").read_text())
    operations=json.loads((OUTPUT/"operations.json").read_text())
    tests=json.loads((OUTPUT/"tests.json").read_text())
    orderedTests=json.loads((OUTPUT/"ordered-fixture-tests.json").read_text())
    build=json.loads((OUTPUT/"build.json").read_text())
    require(len(summary)==8 and operations["ordinary_batches"]==operations["index_loads"]==16,
            "Incomplete or oversized fixed matrix")
    require(tests["exit"]==orderedTests["exit"]==build["exit"]==0,"Native validation/build failed")
    require("PASS real BKT:" in (OUTPUT/"native-tests.log").read_text(),"Fixture evidence missing")
    get=lambda s,c:next(r for r in summary if r["scenario"]==s and r["case"]==c)
    broad=get("broad_tag","posting");original=get("broad_tag","original")
    sparse=get("extreme_tag","posting");sparseOriginal=get("extreme_tag","original")
    goal=broad["ordinary_ms"]<=original["ordinary_ms"] and broad["recall_at_10"]>=original["recall_at_10"]
    report={
        "status":"ONE bounded milestone complete; IDLE; not promoted",
        "goal_met":goal,"semantic_checks_passed":True,"operator_stop_unchanged":True,
        "plan":"Broad/extreme: original post-filter n24, NaviX-only n24, NaviX+posting n24; unfilter original/new n24",
        "ordinary_batches":16,"index_loads":16,"repetitions":2,"order":"second repetition reversed",
        "measurement_protocol":{"warmup":1000,"measured":1000,"offset":0,"topk":10,"maxcheck":2048,
            "hierarchymaxcheck":512,"hierarchyinitialproberatio":.666666,"pages":15,"numa_cpu":2,
            "numa_mem":2,"querythreads":1,"nprobe":[24],"io":"buffered observed through procfs",
            "captures":"separate untimed pass after ordinary timing; exact IDs/distances/SSD work equality"},
        "upstream":{"repo":"https://github.com/gaurav8297/faiss-navix",
            "commit":"192fafbff7ead780185891e6056bbf67cbb19606","license":"MIT",
            "notice":str(HERE/"NAVIX_LICENSE"),"reference":"HNSW.cpp 1038-1053,1112-1198,1275-1312,1421-1469"},
        "policy":{
            "qualification":"native posting support/region eligibility OR exact own-head eligibility; collapsed aliases and native deleted labels respected",
            "degree":"d counts nonnegative ordinary neighbor IDs before first negative sentinel",
            "eligible":"e counts qualifying ordinary neighbors INCLUDING already-native-visited neighbors; scan reused for first-hop emission",
            "empty_degree":"d=0 native no-neighbor route; no division and no sparsity inference",
            "switch":["d>0 && e/d < NavixPostingThreshold -> posting",
                "otherwise e/d >= 0.5 -> one_hop",
                "otherwise 0.4*(d*e+e) > 2*d-e -> directed_two_hop",
                "otherwise -> full_two_hop"],
            "threshold":{"native_ini":"NavixPostingThreshold","initial":.05,"tuned":False,
                "upstream_guarantee":False,"upstream_constants":{"one_hop":.5,"full_cost_factor":.4},
                "naviX_only":"NavixMode=navix disables posting route without any budget change"},
            "one_hop":"Only eligible, native-unvisited first neighbors offered",
            "directed":"Count all valid first encounters. Offer fresh valid first neighbors; native-distance-score fresh nonmatching intermediates, order by ascending distance then ID in a local heap. Mark only selected intermediates visited. Expand until valid encounters reach d, always completing selected row and counting valid before visited; no second ANN frontier",
            "full_two_hop":"Each first neighbor row is scanned, including visited/nonmatching intermediates; eligible fresh first/second neighbors offered through native common machinery",
            "posting":"Only AFTER sparse selection initialize supplier/cache, enumerate spatial H2 owners and retained H3-discovered descriptors. Check signature before representative/member access; nearest native-distance row with ID tie-break. When H2 unavailable select nearest signed H3 owner, scan its full descriptor row, retain eligible H2 descriptors and open nearest full H1 CSR row",
            "fallback":"No selected usable fresh eligible CSR candidate -> correct upstream directed/full two-hop branch, not ordinary-only traversal",
            "visited":"Ineligible auxiliary members rejected BEFORE native CheckAndSet; selected native candidates share native visited/frontier/results",
            "no_active_cost_model":True,"no_global_filtered_seed_scan":True,"no_global_support_scan":True,
            "groundtruth_used_by_policy":False},
        "upstream_deviations":[
            "Native BKT entry and existing tree continuation, aliases, deletion checks, native own admission, native queues/results and checked-leaf budget retained; not bitwise Faiss",
            "Native support/own eligibility replaces Faiss filter_id_map",
            "New untuned .05 most-sparse posting route; H2/H3 signed native hierarchy is not upstream NaviX",
            "No Faiss global first-ten-filtered seed scan; no fixed-4096 node buffer",
            "Directed equal-distance intermediate ties use ascending native H1 ID",
            "Native MaxCheck gates the next local expansion; selected two-hop expansion/CSR rows complete atomically and can cross MaxCheck, without changing its configured value; checked count is not total tree-routing distances",
            "Own-empty-posting eligibility is preserved; exact SSD/final predicates remain native",
            "Full two-hop is implemented, not upstream commented-out blind branch"],
        "runtime":runtime,"index_fingerprint":fingerprint,"ancestry":str(OUTPUT/"parent.json"),
        "protection_scope_note":"Inherited v2 production manifest differs ONLY for parent-owned SPTAG/docs/GettingStart.md; this agent never edited it. Frozen milestones, production sources/libraries/_SPTAG, baseline/core/index remain byte-protected. Exact historical/current document hashes are preserved in inherited-protection-differences.json.",
        "source_files":FILES,"measurements":summary,
        "branch_evidence":{r["scenario"]+"/"+r["case"]:{k:r[k] for k in
            ("branch_counts","posting_query_count","h1_posting_rows","complete_csr_rows","budget_crossing_expansions")
            if k in r} for r in summary if r["case"]!="original"},
        "trace_schema":{"native_u64":NATIVE,"navix_u64":ACTION,
            "decisions_f64":["query","head","d","e","route","fallback","checked_before","checked_after",
                "signature_checks","representative_distances","csr_members"],
            "rows_u64":["query","level","row","size","consumed"],
            "route_ids":{"0":"no_neighbors","1":"one_hop","2":"directed","3":"full_two_hop","4":"posting"}},
        "validation":{"native_fixture_log":str(OUTPUT/"native-tests.log"),"protocol_test_log":str(OUTPUT/"protocol-tests.log"),
            "strengthened_ascending_fixture_log":str(OUTPUT/"native-tests-ordered.log"),
            "fixture_note":"After all measurements completed, added assertion of all fifteen ascending directed intermediate IDs; rebuilt only native-tests and reran fixtures. No search implementation/config/binary changed and no additional measurement iteration.",
            "native_fixtures":["threshold equality","all three real BKT branches","degree zero","valid visited ratio",
                "nonmatching two-hop bridge","directed ascending intermediates","directed selected row complete",
                "valid encounter before visited","full two-hop through visited intermediate","graph zero upper callbacks",
                "actual signed H2/H3 CSR posting","signature before representative/member access",
                "full selected rows","auxiliary rejection without visited poisoning","own-empty-posting",
                "collapsed aliases/ties","deleted","diagnostics on/off parity","empty-predicate original bypass"],
            "real_workload_checks":["formula reconstructed from each actual native decision",
                "every graph branch has zero signature/representative/CSR work","all captured CSR rows complete",
                "exact categorical final predicates","repetition and diagnostic determinism",
                "unfiltered original/new exact ID/distance/SSD parity","sparse actual posting rows",
                "single physical load per process","buffered SSD FD observation",
                "production/v1/v2/core/index/stop protected"]},
        "findings":{"broad_latency_ratio_vs_original":broad["ordinary_ms"]/original["ordinary_ms"],
            "broad_recall_delta_vs_original":broad["recall_at_10"]-original["recall_at_10"],
            "broad_latency_ratio_vs_navix":broad["ordinary_ms"]/get("broad_tag","navix")["ordinary_ms"],
            "broad_recall_delta_vs_navix":broad["recall_at_10"]-get("broad_tag","navix")["recall_at_10"],
            "sparse_latency_ratio_vs_original":sparse["ordinary_ms"]/sparseOriginal["ordinary_ms"],
            "sparse_recall_delta_vs_original":sparse["recall_at_10"]-sparseOriginal["recall_at_10"],
            "sparse_latency_ratio_vs_navix":sparse["ordinary_ms"]/get("extreme_tag","navix")["ordinary_ms"],
            "sparse_recall_delta_vs_navix":sparse["recall_at_10"]-get("extreme_tag","navix")["recall_at_10"],
            "interpretation":"Native NaviX two-hop gains recall but performs many repeated local qualification scans on very sparse predicates. Posting increases sparse recall further but does not restore original latency. MaxCheck limits checked leaf distances, not repeated eligibility tests, visited-row scans, or upper representatives."},
        "durations":{"build":build,"tests":tests,"strengthened_fixture":orderedTests,
            "measure_operation_seconds":operations["operation_seconds"],
            "native_process_seconds":sum(r["process_seconds"] for r in operations["records"]),
            **{key:sum(r["native"][key] for r in operations["records"])
               for key in ("ordinary_seconds","warmup_seconds","capture_seconds")}},
        "limits":["Only two ordinary repetitions; not a confidence interval or curve",
            "Original80/H3 controls were NOT run; no historical timing spliced into this comparison",
            "No numeric_mixed resume, dataset downloads, threshold fitting, follow-on optimization or promotion",
            "New checked-leaf usage/row overshoot is reported explicitly; no hidden budget/probe edits"],
        "blockers":[] if goal else ["Combined Broad did not achieve BOTH original n24 latency and recall",
            "No further optimization iteration or tuning authorized"],
        "reproduce":{"configure_core":f"cmake -S {TOOL}/source -B {TOOL}/build -DCMAKE_BUILD_TYPE=Release -DSPDK=OFF -DROCKSDB=OFF",
            "configure_harness":f"cmake -S {HERE} -B {TOOL}/harness -DCMAKE_BUILD_TYPE=Release -DSPANN_ROOT={TOOL}/source",
            "core":f"cmake --build {TOOL}/build --target SPTAGLibStatic -j2",
            "harness":f"cmake --build {TOOL}/harness -j2","native_tests":str(TOOL/"harness/native-tests"),
            "protocol":f"python3 -m unittest discover -s {HERE} -p test_protocol.py -v",
            "single_point":f"numactl --cpunodebind=2 --membind=2 {TOOL}/harness/navix-bench --config {HERE}/configs/broad_tag_posting.ini",
            "warning":"Single point requires a NEW empty output directory. Existing evidence directories must not be overwritten. Do not rerun bounded phases or resume curves."}}
    artifacts=[p for p in HERE.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    report["exact_new_implementation_files"]=[str(p) for p in sorted(artifacts)]
    artifacts += [p for p in OUTPUT.rglob("*") if p.is_file()]
    artifacts += [TOOL/"harness/navix-bench",TOOL/"harness/native-tests",TOOL/"matched-original",
                  TOOL/"build/CMakeCache.txt",TOOL/"harness/CMakeCache.txt"]
    artifacts += list((TOOL/"source/Release").glob("*.a"))
    artifacts += [TOOL/"source"/p for p in FILES.values()]
    write(OUTPUT/"milestone_manifest.json",{str(p):sha(p) for p in sorted(set(artifacts))})
    report["manifest"]={"path":str(OUTPUT/"milestone_manifest.json"),"sha256":sha(OUTPUT/"milestone_manifest.json")}
    write(OUTPUT/"report.json",report)
    write(OUTPUT/"report.sha256.json",{"sha256":sha(OUTPUT/"report.json")})
    print(json.dumps({"goal_met":goal,"report":str(OUTPUT/"report.json"),"sha256":sha(OUTPUT/"report.json"),
        "findings":report["findings"]},indent=2))

if __name__=="__main__":main()
