"""Freeze the second bounded iteration, including its remaining native latency gap."""
import difflib
import json
import shutil
import numpy as np
from prepare import HERE, TOOL, OUTPUT, PARENT, PARENT_OUT, sha, write
from install import FILES
from run import freeze, protect


def main():
    protection = protect()
    freeze()
    parent_runtime = json.loads((PARENT_OUT / "runtime.json").read_text())
    for name, digest in parent_runtime["repair_libraries"].items():
        assert sha(PARENT / "source/Release" / name) == digest
    old_flags = (PARENT / "build/AnnService/CMakeFiles/SPTAGLibStatic.dir/flags.make").read_text()
    new_flags = (TOOL / "build/AnnService/CMakeFiles/SPTAGLibStatic.dir/flags.make").read_text()
    assert old_flags.replace(str(PARENT), str(TOOL)) == new_flags
    summary = json.loads((OUTPUT / "summary.json").read_text())
    fixtures = json.loads((OUTPUT / "fixtures.json").read_text())
    by = {(r["scenario"], r["variant"]): r for r in summary["rows"]}
    bad, v1, v2, h3 = (by[("broad_tag", name)] for name in ("bad", "v1", "repair", "h3"))
    v1_work = [np.fromfile(OUTPUT / f"broad_v1_r{repeat}/nprobe_24.repair.u64",
                           dtype="<u8").reshape(1000, 14) for repeat in (1, 2)]
    assert np.array_equal(*v1_work)
    v1_costs = v1_work[0].mean(axis=0)
    allocation = {}
    for variant in ("v1", "v2"):
        for scenario in ("broad", "unfilter"):
            path = OUTPUT / f"allocation_{variant}_{scenario}/evidence.json"
            value = json.loads(path.read_text())
            allocation[f"{variant}_{scenario}"] = {
                "path": str(path), "sha256": sha(path), "totals": value["totals"],
                "unordered_ordinary_calls_per_query": sum(r["calls_per_query"] for r in value["sites"]
                    if r["phase"] == "ordinary" and "_Hashtable<" in r["function"]),
                "native_BKT_ordinary_sites": [r for r in value["sites"] if r["phase"] == "ordinary" and
                    r["function"].startswith("void SPTAG::BKT::Index<float>::Search<")]}
    assert allocation["v2_broad"]["unordered_ordinary_calls_per_query"] == 0
    assert all(r["calls_per_query"] == 1 and r["bytes_per_query"] == 256
               for r in allocation["v2_broad"]["native_BKT_ordinary_sites"])
    samples = {}
    for name in ("v1", "repair"):
        path = OUTPUT / f"diagnostic_sample_{name}/evidence.json"
        samples[name] = json.loads(path.read_text())
        assert samples[name]["fine_clocks"] == 0
    flags = json.loads((OUTPUT / "diagnostic_flags_repair/evidence.json").read_text())
    assert flags["clock_calls"] == 14 and flags["fine_clocks"] == 0
    diffs = []
    for local, target in FILES.items():
        parent = PARENT / "source" / target
        before = parent.read_text().splitlines(keepends=True) if parent.exists() else []
        assert sha(HERE / local) == sha(TOOL / "source" / target)
        diffs.extend(difflib.unified_diff(before, (HERE / local).read_text().splitlines(keepends=True),
                                        fromfile="v1/" + target, tofile="v2/" + target))
    with (OUTPUT / "native_source.diff").open("x") as out:
        out.writelines(diffs)
    frozen = OUTPUT / "experiment_source"
    shutil.copytree(HERE, frozen, ignore=shutil.ignore_patterns("__pycache__"))
    write(OUTPUT / "experiment_source_hashes.json",
          {str(p.relative_to(frozen)): sha(p) for p in sorted(frozen.rglob("*")) if p.is_file()})
    report = {
        "status": "second_bounded_iteration_complete_idle_not_promoted",
        "goal_met": False, "curves_resumed": False, "numeric_mixed_resumed": False,
        "ordinary_results": summary,
        "broad_latency_reduction_vs_bad": 1 - v2["ordinary_ms"] / bad["ordinary_ms"],
        "broad_latency_reduction_vs_v1": 1 - v2["ordinary_ms"] / v1["ordinary_ms"],
        "remaining_vs_H3_ms": v2["ordinary_ms"] - h3["ordinary_ms"],
        "remaining_vs_H3_multiple": v2["ordinary_ms"] / h3["ordinary_ms"],
        "unfilter_vs_bad_change": by[("unfilter", "repair")]["ordinary_ms"] /
                                  by[("unfilter", "bad")]["ordinary_ms"] - 1,
        "allocation_evidence": allocation,
        "allocation_calls_removed_fraction": 1 -
            allocation["v2_broad"]["totals"]["ordinary"]["calls_per_query"] /
            allocation["v1_broad"]["totals"]["ordinary"]["calls_per_query"],
        "allocation_scope": "Actual C++ operator-new/new[] callsites on the main query thread, ordinary and "
            "capture stages separately; excludes C malloc and worker-thread allocations. Collector uses "
            "preallocated counters. Instrumented latency is never an ordinary result.",
        "qualification_evidence": {
            "source": "Untimed capture counters, not ordinary clocks or inferred wall cost.",
            "v1_fresh_capture_qualifications": float(v1_costs[11]),
            "v1_fresh_capture_repeats": float(v1_costs[13]),
            "v2_capture": v2["repair_mean_capture_only"],
            "caveat": "Capture reconstructs full identity/proof lists, including after deficit. Ordinary "
                      "execution skips that bookkeeping. Do not subtract proof-member counts to invent "
                      "an exact ordinary predicate-call count."},
        "sampling": {name: {"path": str(OUTPUT / f"diagnostic_sample_{name}/evidence.json"),
            "samples": value["samples"], "main_thread_cpu_ms_per_query": value["sampled_thread_cpu_seconds"],
            "requested_period_us": 250, "observed_period": "approximately1ms kernel delivery",
            "top_symbols": value["functions"][:15]} for name, value in samples.items()},
        "sampling_caveat": "Fresh diagnostics on both cores, separate from paired ordinary measurements. "
            "PC-only optimized leaf symbols: inlining changes attribution buckets, so bucket movement "
            "is not evidence of speedup; only fresh ordinary timings establish the latency change.",
        "semantic_proofs": {
            "purity": "Native SPANN explicitly rejects reuse unless IsLimitedTagMutationReadOnly() is true. "
                "Qualification reads immutable posting validity, support/own attributes, VID mapping, "
                "version metadata and the fixed per-query predicate. Dedup/heap admission remains stateful "
                "and is never cached. Static source and input fingerprints are preserved.",
            "identity": "Every persisted CSR row is unique: BuildOwners rejects duplicate parent membership "
                "for a child. Rejected identities need no storage for a pure predicate. Before deficit is "
                "met, fewer than deficit qualifying novel identities have been credited. Seed head plus "
                "ordinary row and accepted IDs therefore need at most1+2*graph_width integers. This is "
                "an accounting memory bound, not a candidate, row or distance cap.",
            "full_row": "After reaching deficit inside a selected row, all remaining IDs still take the "
                "original native visited/admission/distance processing; only deficit-only qualification "
                "of visited members can disappear. Stopping remains between rows.",
            "workspace": "Qualification bits attach only to existing native visited hash slots: one byte/"
                "slot, no added visited IDs, H1-global/epoch table or distance cache. Full reset between "
                "queries and exact migration on native hash growth are unit-tested.",
            "known_state": "Traversal, posting-known/posting-eligible, own-known/own-eligible flags are "
                "handed through native admission. Own metadata stays lazy; own heap bound, canonical "
                "validation, own dedup, ties and independent posting-result bound remain.",
            "callbacks": "Expansion and row consumers are lvalue-only non-owning function views whose "
                "callables live on the synchronous caller stack.1000 callback/deficit frames allocate "
                "zero times in a scoped allocation fixture; ordinary callsites confirm removal.",
            "parent_cache": "Distance lives alongside indexed existing parent-row status, not per-parent "
                "unordered nodes. Rank scratch covers the original eight owners or their8x8 owners, "
                "without changing ranking/signature order.",
            "unfilter": "Original certificate path enters native BKT before constructing supplier hooks, "
                "scratch or parent state. Measured ordinary unfilter has no BKT/filter-state allocation "
                "site and no upper work; a1.6% latency residual versus bad remains."},
        "verification": [
            "1000 final IDs/Float distances, selected heads/distances, own IDs,18 core native work and8 "
            "deterministic SSD work fields exactly equal for bad/v1/v2 in both Broad and unfilter repetitions.",
            "Four32-query real-predicate fixtures have exact complete before/after payload equality.",
            "Native fixtures cover rejected visited marking, full selected rows beyond budget, own-only, "
            "deletion, aliases/collapsed ties,1/2/8/32/2048 budgets, cache reset/growth, deficit scratch "
            "and non-owning callback lifetimes.",
            "Same native INI budgets/probes, buffered descriptors, NUMA2 and one load per process.",
            "Ordinary timed-body hash and native compiler flags unchanged; capture/profile flags remain "
            "off across[32,24], with14 outer clocks and zero fine clocks."],
        "remaining_blocker": "The requested allocation mechanisms are removed, but native-level Broad "
            "performance is not restored. V2 still takes1.8929ms vs H3 .5187ms at essentially equal recall. "
            "Remaining sampled work includes qualification/valid-posting metadata, native visited and "
            "qualification-slot probes, vector distances and full-row native consumption. The existing "
            "duplicate posting-validity check and extra cache Get/Put probing remain; their individual "
            "removal benefit is not isolated. Parent indexed state still initializes237640bytes/query "
            "plus the existing discovery vectors. No third optimization iteration was attempted.",
        "operation_durations": {
            "ordinary_processes_seconds": summary["native_process_seconds"],
            "ordinary_timed_body_seconds": summary["ordinary_seconds"],
            "real_fixture_processes_seconds": sum(r["process_seconds"] for r in fixtures["records"]),
            "separate_diagnostics_seconds": sum(json.loads(p.read_text())["process_seconds"]
                for pattern in ("allocation_*/evidence.json", "diagnostic_*/evidence.json")
                for p in OUTPUT.glob(pattern))},
        "protection": protection,
        "parent_report_sha256": sha(PARENT_OUT / "report.json"),
        "summary_sha256": sha(OUTPUT / "summary.json"),
        "runtime_manifest_sha256": sha(OUTPUT / "runtime.json"),
        "source_manifest_sha256": sha(OUTPUT / "experiment_source_hashes.json"),
        "native_diff_sha256": sha(OUTPUT / "native_source.diff"),
        "tests_log": str(TOOL / "native-tests.log"),
        "paths": {"source": str(HERE), "toolchain": str(TOOL), "results": str(OUTPUT)}}
    write(OUTPUT / "report.json", report)
    print("report", OUTPUT / "report.json", sha(OUTPUT / "report.json"))
    print("summary", OUTPUT / "summary.json", sha(OUTPUT / "summary.json"))


if __name__ == "__main__":
    main()
