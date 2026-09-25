"""Validate recorded decisions and seal this bounded milestone, never resume curves."""
import json
import numpy as np
from prepare import HERE, TOOL, OUTPUT, MATCHED, V1, DEFAULTS, sha, write
from install import FILES
from run import protect, freeze, require


def verify_trace(record):
    prefix=OUTPUT/record["label"]/f'nprobe_{record["native"]["nprobe"]}'
    rows=np.fromfile(str(prefix)+".decisions.f64",dtype="<f8").reshape(-1,44)
    totals={"decisions":len(rows),"graph":0,"h2":0,"h3":0,"complete_h1_rows":0,
            "budget_crossing_rows":0,"cold_start_posting":0,"retained_descriptors":0,
            "retained_h2_later":0,"discovery_without_immediate_h2":0,"representative_distances":0,
            "graph_examples":[],"h2_examples":[],"h3_examples":[]}
    for query in np.unique(rows[:,0]):
        trace=rows[rows[:,0]==query]
        histories=np.zeros((3,5))
        for row in trace:
            histories[0]+=row[25:30]
            level=int(row[2]); chosen=int(row[4]); source=int(row[37])
            for start,history,prior in ((9,histories[0],.5),(14,histories[source],.5 if source==1 else .75),
                                        (20,histories[2],.75)):
                if (start==14 and row[3]<0) or (start==20 and row[19]<0): continue
                cost,gain,novel,useful,length=row[start:start+5]
                require(np.isclose(novel,(history[1]+8*prior)/(history[0]+8),rtol=1e-12),
                        "Novelty does not reconstruct from prior observations")
                require(np.isclose(useful,(history[4]+.5)/(history[1]+8),rtol=1e-12),
                        "Useful gain does not reconstruct from prior observations")
                require(np.isclose(gain,min(row[7],length*novel*useful),rtol=1e-12),
                        "Remaining-objective gain mismatch")
                setup=.25 if start==9 else row[40] if start==20 else row[39]
                require(np.isclose(cost,setup+length*(.05+novel*1.2),rtol=1e-12),
                        "V2 unpaid operation-count cost mismatch")
                if start==20 or (start==14 and level==1):
                    require(setup==.25,"Sunk H2 representative was charged twice")
            totals["representative_distances"]+=int(row[36])
            name=("graph","h2","h3")[chosen]; totals[name]+=1
            if len(totals[name+"_examples"])<3: totals[name+"_examples"].append(row.tolist())
            if chosen:
                require(row[15]>0 and row[14]/row[15] < (row[9]/row[10] if row[10]>0 else np.inf),
                        "Selected posting was not predicted cheaper per useful gain")
                if chosen==2:
                    expected=1.5+row[35]*.1+row[18]*(.05+row[16]*1.2)
                    require(np.isclose(row[14],expected,rtol=1e-12),"H3 whole-descriptor cost mismatch")
                    totals["discovery_without_immediate_h2"]+=int(row[30]==0)
                totals["cold_start_posting"]+=int(histories[0,4]==0)
                totals["retained_descriptors"]+=int(row[42])
                totals["retained_h2_later"]+=int(row[43])
                if row[43]: require(chosen==1 and source==2,"Retained row lost its discovery history")
                if row[30]>0:
                    length=row[18] if chosen==1 else row[24]
                    require(row[30]==length,"Selected H1 row was truncated")
                    totals["complete_h1_rows"]+=1
                    totals["budget_crossing_rows"]+=int(row[6]>2048)
                histories[source]+=row[30:35]
            else:
                require(np.all(row[30:36]==0) and np.all(row[42:44]==0),
                        "Graph-selected action consumed auxiliary row")
            for work in (row[25:30],row[30:35]):
                require(0<=work[4]<=work[3]<=work[1]<=work[0],"Invalid useful/valid/fresh accounting")
    return totals


def main():
    index=protect(); runtime=freeze()
    require(sha(MATCHED/"OPERATOR_STOP.json")==json.loads((OUTPUT/"parent.json").read_text())["stop_sha256"],
            "Operator stop changed")
    for local,installed in FILES.items():
        require(sha(HERE/local)==sha(TOOL/"source"/installed),"Local/compiled source mismatch")
    for path,digest in json.loads((V1/"milestone_manifest.json").read_text()).items():
        require(sha(path)==digest,"Frozen v1 milestone artifact changed")
    operations=json.loads((OUTPUT/"operations.json").read_text())
    summary=json.loads((OUTPUT/"summary.json").read_text())
    fixtures=json.loads((OUTPUT/"fixtures.json").read_text())
    require(operations["ordinary_batches"]==operations["index_loads"]==20 and len(summary)==10,
            "Incomplete bounded matrix")
    branches={}
    for record in operations["records"]:
        if record["case"] in ("auto","graph","estimate_only") and record["label"].endswith("_r1"):
            branches[record["label"]]=verify_trace(record)
    get=lambda s,c:next(r for r in summary if r["scenario"]==s and r["case"]==c)
    auto=get("broad_tag","auto"); graph=get("broad_tag","graph")
    original=get("broad_tag","original"); h3=get("broad_tag","h3")
    overhead=get("broad_tag","estimate_only")["ordinary_ms"]-graph["ordinary_ms"]
    native_range=max(original["ordinary_ms"],h3["ordinary_ms"])
    test_log=(TOOL/"tests-release.log").read_text()
    require("Native bridge A->B->C" in test_log and "V2 native cold-start posting" in test_log,
            "Required native fixture evidence missing")
    build=json.loads((TOOL/"build-duration.json").read_text())
    require(build["exit"]==0,"Native build failed")
    tests=json.loads((TOOL/"tests-duration.json").read_text())
    preflight=json.loads((TOOL/"preflight-duration.json").read_text())
    require(tests["exit"]==preflight["exit"]==0,"Tests/preflight failed")
    protocol=(TOOL/"protocol-tests.log").read_text()
    require("OK" in protocol and "Ran 5 tests" in protocol,"Protocol fixture evidence missing")
    write(OUTPUT/"final_checks.json",{"semantic_checks_passed":True,"trace_schema_width":44,
        "native_tests":sha(TOOL/"tests-release.log"),"protocol_tests":sha(TOOL/"protocol-tests.log"),
        "protected_v1":True,"stop_sha256":sha(MATCHED/"OPERATOR_STOP.json"),
        "ordinary_batches":20,"index_loads":20,"paired_equalities_passed":True,
        "timed_body_sha256":runtime["timed_body_sha256"]})
    artifacts=[p for p in HERE.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    artifacts += list(OUTPUT.glob("*.json"))
    artifacts += [TOOL/"harness/cost-bench",TOOL/"harness/native-tests",
                  TOOL/"matched-original",TOOL/"matched-current",TOOL/"tests-release.log",
                  TOOL/"build/CMakeCache.txt",TOOL/"harness/CMakeCache.txt",
                  TOOL/"build-duration.json",TOOL/"tests-duration.json",TOOL/"preflight-duration.json",
                  TOOL/"protocol-tests.log",TOOL/"preflight.log",TOOL/"measure.log"]
    artifacts += sorted((TOOL/"source/Release").glob("*.a"))
    write(OUTPUT/"milestone_manifest.json",{str(p):sha(p) for p in sorted(set(artifacts))})
    report={
        "status":"bounded corrected-policy v2 milestone complete; idle; not promoted",
        "full_curves":"operator-stopped; no numeric_mixed resume or full-matrix finalization",
        "goal_met":auto["ordinary_ms"]<=native_range and auto["recall_at_10"]>=h3["recall_at_10"],
        "semantic_checks_passed":True,"ordinary_batches":20,"single_load_groups":20,
        "additional_preflight_groups":4,"primary_unique_points":9,"estimator_control_unique_points":1,
        "summary":str(OUTPUT/"summary.json"),"summary_sha256":sha(OUTPUT/"summary.json"),
        "manifest":str(OUTPUT/"milestone_manifest.json"),"manifest_sha256":sha(OUTPUT/"milestone_manifest.json"),
        "runtime":runtime,"index_fingerprint":index,"source_files":FILES,
        "ancestry":str(OUTPUT/"parent.json"),"measurements":summary,"branch_evidence":branches,
        "trace_schema":json.loads((HERE/"trace_schema.json").read_text()),
        "source_changes":[
            "PostingSupplier removes the observed-positive graph gate; uses declared smoothed cold-start priors",
            "PostingSupplier screens before signature/representative work; caches scored native geometry and uses nearest-parent family ordering",
            "Paid H3 descriptors remain query-local H2 metadata; later decisions compare them even when absent from current H1 owners",
            "CostModel caches three operator probability pairs per decision; immutable configuration validates only at native INI configuration",
            "Native BKT/SPANN integration and ordinary timed body remain byte-identical to v1; capture schema only gains v2 fields"],
        "durations":{
            "measure_operation_seconds":operations["operation_seconds"],
            "measure_native_process_seconds":operations["native_process_seconds"],
            "ordinary_seconds":operations["ordinary_seconds"],
            "warmup_seconds":sum(r["native"]["warmup_seconds"] for r in operations["records"]),
            "capture_seconds":sum(r["native"]["capture_seconds"] for r in operations["records"]),
            "preflight_native_process_seconds":sum(r["process_seconds"] for r in fixtures),
            "all_native_process_seconds":operations["native_process_seconds"]+sum(r["process_seconds"] for r in fixtures),
            "build_wall_seconds":build["finished_epoch"]-build["started_epoch"],
            "tests_wall_seconds":tests["finished_epoch"]-tests["started_epoch"],
            "preflight_operation_wall_seconds":preflight["finished_epoch"]-preflight["started_epoch"],
            "bounded_execution_wall_seconds":sum(d["finished_epoch"]-d["started_epoch"]
                for d in (build,tests,preflight))+operations["operation_seconds"],
            "build_duration_note":"Recorded configure/core/harness, native/protocol tests, four preflights and twenty-batch measurement wall durations. Sum excludes authoring, clone/provenance preparation and final reporting; no inferred end-to-end authoring duration"},
        "broad_findings":{
            "recall_delta_vs_previous_target_0_918":auto["recall_at_10"]-.918,
            "recall_delta_vs_graph_only":auto["recall_at_10"]-graph["recall_at_10"],
            "latency_over_native_range_ms":auto["ordinary_ms"]-native_range,
            "ratio_to_original":auto["ordinary_ms"]/original["ordinary_ms"],
            "ratio_to_h3":auto["ordinary_ms"]/h3["ordinary_ms"],
            "fixed_graph_trace_estimator_overhead_ms":overhead,
            "overhead_scope":"estimate_only minus graph: observation/model overhead on identical graph outputs/core work; not an exact decomposition of auto"},
        "estimator":{
            "units":"Uncalibrated operation counts, vector distance=1; not microseconds",
            "defaults":DEFAULTS,
            "formula":[
                "p_new=(fresh+W*novelty_prior)/(members+W)",
                "p_useful_given_new=(useful+W*predicate_prior*competitive_prior)/(fresh+W)",
                "cost=setup+length*(member_cost+p_new*(distance_cost+predicate_cost))",
                "gain=min(remaining_objective,length*p_new*p_useful_given_new)",
                "Metadata screen prospective candidate work (including uncached representative) against graph; signature first, then native spatial representative",
                "Choose distance-nearest economic parent per H2/H3 family (ID breaks spatial ties); arbitrate family winners by unpaid cost/gain",
                "Subtract a scored representative from remaining action cost; cached row representatives/signatures reused",
                "Choose strictly lower cost/gain; zero gain -> infinity; graph ties/no supported candidate -> graph; zero observed graph success uses the unchanged smoothed priors"],
            "objective":"max(1, unfilled native H1 result slots); near-valid head/own improvement proxy, not final dataset recall",
            "graph":"Next unpaid lazy native graph step, width limited in the estimate by remaining checked-leaf budget; no eager d^2 enumeration",
            "posting":"Complete row cost including overshoot and setup. H3 includes full descriptor scan plus mean H2 row, conservatively reserving signature checks and a hypothetical H2 representative; after discovery compare the actual unpaid H2 tail and retain uneconomic signed descriptors",
            "spatial":"Native vector-only representative distance orders individually economic candidates within a family. It is not a radius bound, probability coefficient or mixed attribute distance",
            "overhead_changes":"Immutable INI validates once; three history probability pairs once/decision; no per-row probability division/validation; row signature/representative cache; consumed/rejected rows skipped; ancestor dedup uses per-row generation instead of copy/sort/unique",
            "useful":"New and native posting-predicate-valid at competitive distance, or actual own-heap improvement; inclusive ties are a proxy, not exact heap insertion count",
            "default_rationale":"Distance normalization; inexpensive scan/signature=.05, predicate=.2 and setup=.25 structural priors, not calibrated. Weak W=8, novelty=.5/.75 and joint valid-near=.25*.25; no workload-specific gate or groundtruth fit"},
        "invariants":[
            "Result filter only; ordinary and auxiliary H1 neighbors use native visited/distance/frontier/admission",
            "Nonmatching bridge remains navigable; no auxiliary eligibility rejection poisons visited",
            "Signature precedes representative/member access; full selected rows complete at native row boundary",
            "H3 discovers H2 descriptors, not H1 IDs; no separate upper search queue or subtree flattening",
            "Own forwarding/bounds, aliases, ties, deletes and final exact SSD predicate retained",
            "Degree quotas/scans/cache removed; old controls and invalid/nonfinite costs rejected",
            "Empty predicate uses native fast path and has exact original final IDs/distances/SSD parity",
            "Capture on/off exact final IDs/distances/SSD work; repetitions exact native/head/own/decision traces",
            "No environment search/data overrides; buffered IO observed; one native array load per process",
            "All protected core/source/input checks pass; original operator stop unchanged"],
        "limits":[
            "No claim of restored Broad speed or preserved 0.918 recall if goal_met is false",
            "Estimator is a shared per-operator proxy, not calibrated or centroid-specific",
            "Nearest-parent order is only within individually economic operator families; cost/gain still chooses between families",
            "H3 mean-row/signature/representative reserves are conservative structural estimates, not exact future realized cost",
            "Discovered H2 IDs are query-local metadata, not a second search frontier; rejected/consumed IDs are skipped and no descendants are flattened",
            "Two repetitions support bounded observations, not statistical confidence or a curve",
            "No further optimization, probe/budget change or automatic tuning authorized"],
        "reproduce":{
            "build_core":f"cmake --build {TOOL}/build --target SPTAGLibStatic -j2",
            "build_harness":f"cmake --build {TOOL}/harness -j2",
            "native_tests":f"{TOOL}/harness/native-tests",
            "protocol_tests":f"cd {HERE} && python3 -m unittest -v test_protocol",
            "single_point":f"numactl --cpunodebind=2 --membind=2 {TOOL}/harness/cost-bench --config {HERE}/configs/broad_tag_auto.ini",
            "warning":"Run a single-point reproduction only in a newly authorized empty output directory. Existing run.py phases intentionally refuse to overwrite evidence; do not rerun them or resume curves"}
    }
    report["v1_comparison"]={
        "report_sha256":json.loads((OUTPUT/"parent.json").read_text())["v1_report_sha256"],
        "broad_graph_ms":.704365,"broad_auto_ms":.805065,"broad_estimate_only_ms":.799171,
        "v1_same_trace_overhead_ms":.094806,"v2_same_trace_overhead_ms":overhead,
        "same_trace_overhead_change_ms":overhead-.094806,
        "caution":"Cross-revision timing is not a matched paired run; no precise decomposition of auto"}
    report["blockers"]=[] if report["goal_met"] else [
        "Broad auto did not meet both native-range latency and H3 recall in this bounded run",
        "No tuning, extra variants, probe changes or full curves authorized"]
    report["estimator"]["measured_overhead_reduced_vs_v1"]=overhead<.094806
    if overhead>=.094806:
        report["blockers"].append(
            "Same-trace estimate-only overhead did not decrease versus v1 despite reduced repeated arithmetic/validation; v2 also pays spatial screening/cache work. No extra optimization variant was run")
    write(OUTPUT/"report.json",report)
    write(OUTPUT/"report.sha256.json",{"path":str(OUTPUT/"report.json"),"sha256":sha(OUTPUT/"report.json")})
    print(json.dumps({"goal_met":report["goal_met"],"report":str(OUTPUT/"report.json"),
                      "sha256":sha(OUTPUT/"report.json"),"broad":report["broad_findings"]},indent=2))


if __name__=="__main__":
    main()
