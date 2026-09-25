"""Complete the missing result-only H1 controls without changing the NaviX runtime."""
import importlib.util
import json
import statistics
import sys
import time

from prepare import HERE, BASE, PARENT, OUTPUT, sha, write
from run import protect, freeze, require


def main():
    protect()
    runtime=freeze()
    original_report=json.loads((OUTPUT/"report.json").read_text())
    frozen=HERE.with_name("cost_arbitration_v2_20260917")
    prep_spec=importlib.util.spec_from_file_location("frozen_control_prepare",frozen/"prepare.py")
    control_prepare=importlib.util.module_from_spec(prep_spec)
    prep_spec.loader.exec_module(control_prepare)
    specification=importlib.util.spec_from_file_location("frozen_postfilter_reader",frozen/"run.py")
    reader=importlib.util.module_from_spec(specification)
    original_prepare=sys.modules["prepare"]
    try:
        sys.modules["prepare"]=control_prepare
        specification.loader.exec_module(reader)
    finally:
        sys.modules["prepare"]=original_prepare
    directory=OUTPUT/"postfilter_control_completion"
    directory.mkdir()
    reader.HERE=frozen
    reader.TOOL=BASE
    reader.OUTPUT=directory
    provenance=json.loads((PARENT/"runtime.json").read_text())
    require(provenance["timed_body_sha256"]==runtime["timed_body_sha256"],"Shared timing body mismatch")
    require(sha(BASE/"harness/cost-bench")==provenance["binary_sha256"],"Frozen control binary changed")
    records=[]
    start=time.monotonic()
    for repeat,scenarios in enumerate((("broad_tag","extreme_tag"),("extreme_tag","broad_tag")),1):
        for scenario in scenarios:
            records.append(reader.invoke(f"{scenario}_graph_r{repeat}",scenario,"graph",
                                         frozen/f"configs/{scenario}_graph.ini"))
    controls=[]
    for scenario in ("broad_tag","extreme_tag"):
        pair=[r for r in records if r["scenario"]==scenario]
        reader.equal_core(*pair)
        require(pair[0]["trace_sha256"]==pair[1]["trace_sha256"],"Control trace nondeterminism")
        for record in pair:
            n=record["native_mean"]
            require(all(n[key]==0 for key in ("calls","child_distances","representative_distances",
                                             "csr_members","signature_checks","h2_rows","h3_rows")),
                    "Post-filter graph control performed upper work")
        ms=[r["native"]["mean_latency_ms"] for r in pair]
        controls.append({"scenario":scenario,"case":"postfilter_graph","nprobe":24,
            "ordinary_ms_runs":ms,"ordinary_ms":statistics.mean(ms),"qps":1000/statistics.mean(ms),
            "recall_at_10":pair[0]["native"]["recall"],"ssd_mean":pair[0]["ssd_mean"],
            "native_mean":pair[0]["native_mean"],"control_binary_sha256":provenance["binary_sha256"]})
    summary=controls+[r for r in original_report["measurements"]
                       if r["scenario"]=="unfilter" or r["case"]!="original"]
    write(directory/"records.json",records)
    write(OUTPUT/"summary_with_postfilter_controls.json",summary)
    completion={
        "status":"bounded milestone complete with corrected control; IDLE; not promoted",
        "goal_met":False,
        "correction":"Initial Broad/extreme 'original' points used unfiltered H1 head admission plus final SSD filtering. They were not the requested result-only H1 post-filter control.",
        "authoritative_summary":str(OUTPUT/"summary_with_postfilter_controls.json"),
        "summary_sha256":sha(OUTPUT/"summary_with_postfilter_controls.json"),
        "implementation_report":str(OUTPUT/"report.json"),
        "implementation_report_sha256":sha(OUTPUT/"report.json"),
        "initial_original_rows":"Preserved unchanged as four additional diagnostic batches; do not label them H1 result-only post-filter controls.",
        "control_runtime":"Frozen cost-v2 graph-only core, freshly executed; no old timings copied. Cost policy inactive and all upper work verified zero.",
        "control_provenance":provenance,
        "pairing_limit":"Missing controls ran after the initial campaign, in two reversed-order repetitions; not interleaved with NaviX runs. Do not claim a simultaneous paired-core latency ratio.",
        "primary_points":8,"primary_ordinary_batches":16,
        "additional_original_diagnostic_batches":4,"total_single_load_processes":20,
        "measurements":summary,
        "completion_operation_seconds":time.monotonic()-start,
        "completion_process_seconds":sum(r["process_seconds"] for r in records),
        "completion_ordinary_seconds":sum(r["native"]["ordinary_seconds"] for r in records),
        "protection_index_fingerprint":protect(),
        "navix_runtime_unchanged":freeze()==runtime,
        "reproduce_control":f"numactl --cpunodebind=2 --membind=2 {BASE}/harness/cost-bench --config {frozen}/configs/broad_tag_graph.ini",
        "reproduce_warning":"Only in a newly authorized empty output directory; existing evidence must not be overwritten.",
        "source_sha256":sha(HERE/"complete_postfilter_controls.py")
    }
    write(OUTPUT/"completion_report.json",completion)
    artifacts=[p for p in directory.rglob("*") if p.is_file()]
    artifacts += [OUTPUT/"completion_report.json",OUTPUT/"summary_with_postfilter_controls.json",
                  HERE/"complete_postfilter_controls.py"]
    write(OUTPUT/"completion_manifest.json",{str(p):sha(p) for p in sorted(artifacts)})
    print(json.dumps({"report":str(OUTPUT/"completion_report.json"),
                      "sha256":sha(OUTPUT/"completion_report.json"),"controls":controls},indent=2))


if __name__=="__main__":
    main()
