"""Reconcile the bounded native-default experiment without further searches."""
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("default_report_run", HERE / "run.py")
phase = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase)


def compare(a, b):
    left, right = phase.capture_rows(a), phase.capture_rows(b)
    assert len(left) == len(right)
    return {"queries": len(left),
            "head_changed_queries": [x["query"] for x, y in zip(left, right) if x["heads"] != y["heads"]],
            "result_changed_queries": [x["query"] for x, y in zip(left, right) if x["results"] != y["results"]]}


def main():
    run = phase.Experiment()
    root, tool = run.root, run.toolchain
    fixtures = json.loads((root / "fixtures.json").read_text())
    runs = json.loads((root / "runs.json").read_text())
    summary = json.loads((root / "summary.json").read_text())
    assert len(fixtures) == 8 and len(runs) == 6
    evidence, frames, captures = [], 0, 0
    for record in fixtures + runs:
        directory = root / record["directory"]
        assert json.loads((directory / "native.exit.json").read_text())["returncode"] == 0
        assert json.loads((directory / "native.io.json").read_text())["direct_io"]
        text = "\n".join((directory / f"native.{k}.log").read_text() for k in ("stdout", "stderr"))
        assert text.splitlines().count("NATIVE_INDEX_LOAD calls=1") == 1
        assert text.count("NATIVE_SUPPLIER_OWNER_LOAD count=1") == 1
        certificate = "NATIVE_DEFAULT_CERT heads=160091 own_all_live=1 immutable=1 vectors=1000000"
        assert text.count(certificate) == 1
        components = {label: text.count("Load " + label + " Finish!") for label in
            ("Vector (160091,128)", "BKT (1,160093)", "RNG (160091,32)",
             "Vector (4098,128)", "BKT (1,4100)", "RNG (4098,32)")}
        assert set(components.values()) == {1}
        evidence.append({"directory": record["directory"], "native_loadall_entries": 1,
                         "owner_metadata_loads": 1, "certificate_log": certificate,
                         "physical_component_loads": components})
        for point in record["points"]:
            h, count = hashlib.sha256(), 0
            with gzip.open(directory / (point["native"]["capture_file"] + ".gz"), "rb") as stream:
                for line in stream:
                    h.update(line)
                    count += 1
                    row = json.loads(line)
                    assert row["native_raw_distance_function"] == 1
                    assert len(set(row["own_ids"])) == len(row["own_ids"]) <= 10
                    assert all(run.masks[record["scenario"]][i] for i in row["own_ids"])
                    frames += len(row["degree_frames"])
            assert count == point["validation"]["queries"]
            assert h.hexdigest() == point["validation"]["raw_sha256"]
            captures += 1
    assert captures == 22
    previous = root.parent / "h1_native_reuse_20260917_v2"
    phase1 = root.parent / "h1_ratio_phase1_20260917"
    h1 = root / "plain_r1_h1/queries.jsonl.nprobe_24.gz"
    supplier = root / "plain_r1_supplier/queries.jsonl.nprobe_24.gz"
    differences = {
        "new_supplier_vs_new_h1": compare(supplier, h1),
        "new_h1_vs_authenticated_h1": compare(h1, phase1 / "plain_r1_h1/queries.jsonl.nprobe_24.gz"),
        "new_supplier_vs_native_v2": compare(supplier, previous / "plain_r1_supplier/queries.jsonl.nprobe_24.gz")}
    for name in ("new_supplier_vs_new_h1", "new_h1_vs_authenticated_h1"):
        assert not differences[name]["head_changed_queries"] and not differences[name]["result_changed_queries"]
    assert len(differences["new_supplier_vs_native_v2"]["head_changed_queries"]) == 17
    assert differences["new_supplier_vs_native_v2"]["result_changed_queries"] == [915]
    filtered = []
    for record in fixtures:
        if record["scenario"] == "unfilter":
            continue
        point = record["points"][0]
        file = point["native"]["capture_file"] + ".gz"
        current, prior = phase.capture_rows(root / record["directory"] / file), phase.capture_rows(
            previous / record["directory"] / file)
        assert len(current) == len(prior) == 8
        assert all({k: v for k, v in a.items() if k not in phase.NEW_FIELDS} == b
                   for a, b in zip(current, prior))
        filtered.append({"scenario": record["scenario"], "queries": 8, "v2_complete_payload_parity": True,
                         "recall_at_10": point["validation"]["recall_at_10"], "work": point["validation"]["work"]})
    phase.write(root / "result_differences.json", differences)
    phase.write(root / "filtered_fixture_work.json", filtered)
    phases = {}
    for case in ("h1", "supplier"):
        logs = [root / f"profile_{case}/native.{k}.log" for k in ("stdout", "stderr")]
        _, phases[case] = phase.native.h3_records(logs, 1000, 2000, 24, True)
        phase.native.validate_phase_balance(phases[case])
    phase.write(root / "native_phases.json", phases)
    inputs = json.loads((phase1 / "input_hashes.json").read_text())
    for item in inputs:
        assert phase.fp(Path(item["path"]))["sha256"] == item["sha256"], item["path"]
    phase.write(root / "input_hashes.json", inputs)
    proof = json.loads((tool / "native_reuse_provenance.json").read_text())
    for name, digest in proof["after"].items():
        assert phase.fp(tool / "source" / name)["sha256"] == digest
    for name, digest in proof["parent_source_hashes"].items():
        assert phase.fp(Path(proof["parent"]) / "source" / name)["sha256"] == digest
    assert phase.fp(Path(proof["parent"]) / "source/Release/spannaclbench")["sha256"] == proof["parent_binary_sha256"]
    parser = tool / "source/Tools/benchmarks/NativeNProbeSweep.h"
    assert phase.fp(parser)["sha256"] == "94dc8cf2e9313e683cb7a5d4e160990ab69d2fe54c0e9dc66cc5f4fd3f1b0c41"
    symbols = subprocess.check_output(["nm", "-C", "--defined-only", str(tool / "source/Release/spannaclbench")],
                                      text=True)
    forbidden = ("H1Supplier::", "BenchmarkRestore", "BenchmarkDistance", "Engine::Score")
    assert all(name not in symbols for name in forbidden)
    for name in ("Supplier.h", "NativeAdapter.h", "ShortcutHooks.h"):
        assert not (tool / "source/AnnService" / name).exists()
    phase.write(root / "native_default_proof.json", {
        "predicate": "actual empty native DNF and tag list, not workload name",
        "certificate": "all native own heads verified live at load; existing immutable generation; "
                       "unchanged H1/vector counts; no H1 deletion; native nprobe>=topk; ratio<=1",
        "certificate_storage": "one Boolean and uint64 vector-count scalar in existing posting model",
        "constant_eligibility": "every physical valid ordinary neighbor own-admissible => e=d regardless of visited",
        "posting_absence": "not a rejection of own points; native selected-head translation is outside posting-valid branch",
        "default_ordinary_callbacks": {"result": 0, "traversal": 0, "own_notification": 0, "expansion": 0},
        "default_row_eligibility_evaluations": 0,
        "ordinary_capture_admission_flags_checked_in_native_executable": True,
        "default_extra_own_heap": False, "shadow_h1_heaps": 0,
        "fallback": "mutable/deleted-invalid-own/exact-predicate generations keep v2 admission",
        "forbidden_defined_symbols_absent": list(forbidden),
        "native_parser_sha256": phase.fp(parser)["sha256"],
        "owner_metadata_bytes_unchanged": (160091 + 25607) * 8 * 4,
        "no_input_graph_vector_posting_mutation_or_duplication": True})
    shutil.copy2(root / "summary.json", root / "summary.native_initial.json")
    for row in summary:
        row.update(qps_min=1000 / max(row["ordinary_ms_runs"]), qps_max=1000 / min(row["ordinary_ms_runs"]),
                   phases=phases[row["case"]], nprobe_ini_api="SearchSweep.NProbe",
                   ordinary_distance_interception=False, shadow_h1_result_heaps=0,
                   native_default_admission=True, default_admission_certificate_verified=True)
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    by_case = {row["case"]: row for row in summary}
    ratio = by_case["supplier"]["ordinary_ms"] / by_case["h1"]["ordinary_ms"]
    report = [
        "# Native default admission: bounded result", "",
        "Original native no-predicate admission is safe for this verified immutable generation. "
        "It is now reused, instead of preserving the prototype's posting-only head heap selection and "
        "all-scored own notifications. The fresh supplier/H1 latency ratio is "
        f"{ratio:.6f}; the measured difference is {(ratio - 1) * 100:.3f}% and is smaller than "
        "the spread across these two rotated repetitions. This is not evidence of a speed advantage.",
        "", "| unfilter nprobe24 | Final Recall@10 | Ordinary ms | QPS | Separate profiled navigation ms |",
        "|---|---:|---:|---:|---:|"]
    for row in summary:
        report.append(f'| {row["case"]} | {row["recall_at_10"]:.4f} | {row["ordinary_ms"]:.6f} | '
                      f'{row["qps"]:.3f} | {phases[row["case"]]["graphOther"]:.6f} |')
    report += [
        "", "Two ordinary repetitions, H1->supplier then supplier->H1; each has1000 warmup+1000 measured, "
        "one query thread, NUMA CPU/memory2, O_DIRECT/page15. All use native SearchSweep.NProbe=[24]. "
        "Timing is entirely fresh and same-runtime; v2 timing is not spliced in. Separate profiles include "
        "instrumentation overhead, and native io remains retrieval minus scan, not physical SSD delay.",
        "", "## Semantic proof and exact outputs", "",
        "Load-time native checks certify that every H1 has a live canonical own point. Existing "
        "IsLimitedTagMutationReadOnly blocks native mutation of this expanded/recovered generation. "
        "The query gate also requires actual empty native DNF/tags, unchanged head/vector domain, zero "
        "native H1 deletions, no primary bypass and nprobe>=topk. All14 process logs independently report "
        "NATIVE_DEFAULT_CERT heads=160091 own_all_live=1 immutable=1 vectors=1000000.",
        "Therefore e=d, including visited, whether or not the head has a posting. The ratio trigger "
        "e/d<0.5 cannot fire, including short/empty/odd/collapsed rows. This is constant evaluation of "
        "the same rule, not a workload-label bypass. Only two scalars were added to existing load-time "
        "metadata; no H1 cache, per-query degree pass, new locks or distance wrapper was added. "
        "Native Deleted is checked directly, including a SetVersion(0xfe) unit case that does not "
        "increment the deletion counter. Mutable or invalid/deleted-own states cannot get this proof "
        "and retain the v2 path.",
        "Native SPANN translates selected own heads independently of posting validity, translates "
        "remaining heads after posting cutoff, then applies native deletion/dedup, heap reversal and "
        "SSD merge. An empty posting does not remove its selected live own point. Ties and collapsed "
        "aliases follow the original approximate selection semantics, not all-evaluated top-k.",
        "All1000 queries match authenticated H1 on every selected head, final ID/distance and native work. "
        "Actual q915 and native-budget boundary queries661/772 are stored in default_parity.json. "
        "Relative to v2,17 head-query outputs and only q915's final result change, exactly restoring "
        "baseline behavior; there is no answer rewriting or recall tuning.",
        "", "## Removed remaining ordinary overhead", "",
        "Certified ordinary search installs no filtered result/traversal predicate, known-distance "
        "own-point callback or neighbor-expansion callback, and falls through to the original public "
        "native BKT SearchIndex API. The extra SPANN own-point workspace/heap is not allocated. "
        "Actual ordinary/capture admission flags and raw distance targets are checked by the native "
        "benchmark; throwing callback unit fixtures prove default execution does not call them. "
        "All unfilter supplier calls, upper/child distances, signature checks, CSR reads and row "
        "eligibility callback evaluations are zero. Untimed capture alone may enumerate degree frames.",
        "Both cases have2231.960 actual H1 distances/query,1920.405 native checked leaves, "
        "1869.247 queue offers,333.955 accepted and1535.292 rejected;23.828 postings,143.651 pages "
        "and918.673 SSD distance evaluations. Rejection is native bound-based, not a new radius/cap.",
        "", "## Filtered behavior and validation", "",
        "All four filtered8-query captures match v2's complete payload, including IDs/distances/work, "
        "after excluding only the three added diagnostic metadata fields. Existing native "
        "posting/traversal predicates and own helper remain where needed. Ratio, signature-before-upper "
        "distance/CSR, predicate-before-H1-distance structural exception, full selected rows and native "
        "MaxCheck boundary rules are unchanged. There is no extra upper frontier/restart/global scan.",
        "Native unit tests cover live/invalid/deleted certificate gates, a selected own-only point "
        "without callbacks, genuine1024-vector collapsed aliases/ties at MaxCheck1/8/2048, native "
        "primitive/checked-count parity, startup/odd/short/empty/duplicate/sentinel rows, ratio equality "
        "and deficiency, signature rejection and complete rows past deficit/native boundary. "
        "The own-only unit is a native head-selection fixture plus the unchanged full SPANN translation "
        "source proof, not a newly built synthetic SSD dataset.",
        "Forward/reverse[16,24,384] fixture parity passes, including complete native work and results. "
        f"14 native protocol processes,22 captures and{frames} captured degree frames are reconciled. "
        "Each process has one actual native LoadAll entry, owner preparation and physical H1/H3 "
        "vector/BKT/RNG load; process counts alone were not used as load evidence. Historical H3 remains "
        "backing metadata, not an independent frontier.",
        "Parent source/binary, shared native array parser and protected inputs rehash unchanged. "
        "Production and MAIN-owned generic sources are untouched. No full sweep was launched. "
        "The bounded task ends here; no claim is made about filtered QPS or the overall multi-workload goal.",
    ]
    (root / "report.md").write_text("\n".join(report) + "\n")
    phase.write(root / "independent_reconciliation.json", {
        "native_protocol_loads": 14, "capture_files_rehashed": captures, "degree_frames_checked": frames,
        "actual_load_evidence": evidence, "inputs_rehashed": len(inputs), "parent_source_binary_unchanged": True,
        "all_unfilter_heads_results_distances_work_match_h1": True, "filtered_v2_complete_payload_parity": True,
        "ordinary_capture_profile_repeat_parity": True, "remaining_h1_latency_ratio": ratio,
        "result_differences": differences, "full_sweep_launched": False,
        "overall_multiscenario_performance_claim": False,
        "summary_sha256": phase.fp(root / "summary.json")["sha256"],
        "report_sha256": phase.fp(root / "report.md")["sha256"]})
    final = root / "final_provenance"
    shutil.copytree(HERE, final / "experiment", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(tool / "tests.log", final / "tests.log")
    phase.write(final / "manifest.json", {
        "experiment_files": [phase.fp(p) for p in sorted((final / "experiment").rglob("*")) if p.is_file()],
        "native_binary": phase.fp(tool / "source/Release/spannaclbench"),
        "native_test_binary": phase.fp(tool / "tests/nativereusetests"),
        "source_archive": phase.fp(root / "snapshot/source.tar.gz")})
    phase.write(root / "status.json", {"state": "completed_bounded_native_default_admission",
        "full_sweep_allowed": False, "resume_allowed": False, "overall_performance_goal_done": False})
    print(json.dumps({"summary": summary, "ratio": ratio, "summary_sha256": phase.fp(root / "summary.json")["sha256"]},
                     indent=2))


if __name__ == "__main__":
    main()
