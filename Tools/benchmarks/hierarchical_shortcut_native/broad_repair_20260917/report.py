"""Freeze the bounded negative/positive result without promoting it or resuming curves."""
import difflib
import json
from pathlib import Path
import shutil
from prepare import HERE, TOOL, OUTPUT, BASE, MATCHED, sha, write
from prepare_repair import FILES
from run import protect, freeze


def bucket(name, repaired):
    if "_Hashtable<" in name or "_Hash_node<" in name:
        return "generic_unordered_containers"
    if any(s in name for s in ("malloc", "_int_free", "__libc_free", "tcache", "free(")):
        return "allocator_symbols"
    if name.startswith("SPTAG::COMMON::DistanceUtils::"):
        return "vector_distance"
    if "OptHashPosVector::" in name:
        return "native_visited_hash"
    if name.startswith("SPTAG::SPANN::ExtraStaticSearcher<float>::CheckValidPosting"):
        return "qualification_related_symbols"
    if name.startswith("std::_Function_handler<bool (int), SPTAG::SPANN::Index<float>::SearchIndex"):
        if "{lambda(int)#3}" in name or (not repaired and "{lambda(int)#11}" in name):
            return "qualification_related_symbols"
    if name.startswith("std::_Function_handler<SPTAG::COMMON::NativeEligibility (int),"):
        return "qualification_related_symbols"
    if name.startswith("SPTAG::SPANN::Index<float>::SearchIndex") and (
            "{lambda(int)#2}" in name or (repaired and "{lambda(int)#11}" in name)):
        return "qualification_related_symbols"
    if name.startswith(("void NativeReuse::PostingSupplier::", "NativeReuse::PostingSupplier::",
                        "NativeReuse::Search<")):
        return "mixed_supplier_bodies_including_inlined_work"
    if name == "syscall":
        return "syscall"
    return "other_including_native_neighbor_loops"


def main():
    protection = protect()
    freeze()
    summary = json.loads((OUTPUT / "summary.json").read_text())
    fixtures = json.loads((OUTPUT / "fixtures.json").read_text())
    by = {(r["scenario"], r["variant"]): r for r in summary["rows"]}
    bad, repair, h3 = (by[("broad_tag", v)] for v in ("bad", "repair", "h3"))
    profiles = []
    for suffix, repaired in (("", False), ("_repair", True)):
        directory = OUTPUT / ("diagnostic_sample" + suffix)
        evidence = json.loads((directory / "evidence.json").read_text())
        counts = {}
        for row in evidence["functions"]:
            key = bucket(row["function"], repaired)
            counts[key] = counts.get(key, 0) + row["samples"]
        profiles.append({
            "variant": "repair" if repaired else "frozen_bad",
            "source": str(directory / "evidence.json"), "samples": evidence["samples"],
            "main_thread_cpu_ms_per_query": evidence["sampled_thread_cpu_seconds"],
            "observed_samples_per_cpu_second": evidence["samples"] / evidence["sampled_thread_cpu_seconds"],
            "requested_period_us": 250, "dropped_samples": 0,
            "native_resize_log_events_all_phases": sum((directory / f).read_text().count(
                "Hash table is full!") for f in ("stdout.log", "stderr.log")),
            "groups": {k: {"samples": n, "cpu_fraction": n / evidence["samples"],
                "estimated_instrumented_cpu_ms_per_query":
                    evidence["sampled_thread_cpu_seconds"] * n / evidence["samples"]}
                for k, n in sorted(counts.items(), key=lambda kv: -kv[1])}})
    old_flags = (BASE / "build/AnnService/CMakeFiles/SPTAGLibStatic.dir/flags.make").read_text()
    new_flags = (TOOL / "build/AnnService/CMakeFiles/SPTAGLibStatic.dir/flags.make").read_text()
    assert old_flags.replace(str(BASE), str(TOOL)) == new_flags
    for suffix in ("", "_repair"):
        flags = json.loads((OUTPUT / f"diagnostic_flags{suffix}/evidence.json").read_text())
        assert flags["clock_calls"] == 14 and flags["fine_clocks"] == 0
    frozen = OUTPUT / "experiment_source"
    shutil.copytree(HERE, frozen, ignore=shutil.ignore_patterns("__pycache__"))
    source_manifest = {str(p.relative_to(frozen)): sha(p) for p in sorted(frozen.rglob("*")) if p.is_file()}
    write(OUTPUT / "experiment_source_hashes.json", source_manifest)
    diffs = []
    for local, target in FILES.items():
        original = BASE / "source" / target
        before = original.read_text().splitlines(keepends=True) if original.exists() else []
        diffs.extend(difflib.unified_diff(before, (HERE / local).read_text().splitlines(keepends=True),
                                        fromfile="frozen/" + target, tofile="repair/" + target))
    with (OUTPUT / "native_source.diff").open("x") as out:
        out.writelines(diffs)
    report = {
        "status": "one_bounded_iteration_complete_not_promoted_idle",
        "normal_level_goal_met": False, "curve_resume_authorized": False,
        "broad_latency_reduction_fraction": 1 - repair["ordinary_ms"] / bad["ordinary_ms"],
        "broad_repair_over_h3_latency": repair["ordinary_ms"] / h3["ordinary_ms"],
        "unfilter_latency_change_fraction": by[("unfilter", "repair")]["ordinary_ms"] /
                                            by[("unfilter", "bad")]["ordinary_ms"] - 1,
        "ordinary": summary,
        "evidence": profiles,
        "profiling_limitations": [
            "PC-only leaf sampling; no unwinding or nested timers. Requested250us delivered about1000samples/CPU-second.",
            "Bucket estimates describe sampled optimized symbols, not pure API dispatch or exact wall components.",
            "Inlining moves work between symbol buckets; allocator/container PCs alone cannot prove every caller.",
            "Instrumented CPU and all diagnostic timings are excluded from paired ordinary results.",
            "Two ordinary repetitions are not a significance study; bad runs varied2.6013 vs2.4277ms."],
        "verified": [
            "All1000 Broad and unfilter IDs, Float distances, selected heads/distances, own IDs,18 native work "
            "fields and8 deterministic SSD work fields exactly match bad vs repair in both repetitions.",
            "Actual unfilter retains original native default admission and zero upper/CSR/qualification work.",
            "Four real-predicate32-query fixtures match exact complete payloads.",
            "Native scalar-vs-batch fixtures execute full selected rows beyond native budget; exact visited "
            "decisions including rejects, own notifications, scored order/distances, queues and final results.",
            "Native own-only/deleted/collapsed-alias/tie and1/2/8/32/2048-budget fixtures pass.",
            "Both runtimes show14 coarse calls across[32,24], no fine clocks, capture/profile false at ordinary boundaries.",
            "Native core compiler flags match frozen baseline; common ordinary timed-body SHA unchanged.",
            "Buffered SSD descriptors and one native/component load per process observed."],
        "structural_repair": {
            "ordinary_degree": "Fused into original neighbor loop, including visited degree and original visited marking.",
            "csr": "One native batched row callback, generic native prefetch, local qualification handed to traversal/result admission.",
            "admission": "Proven strict noncollapsed posting bound after own notification; collapsed-stop and ties unchanged.",
            "capture_counts_per_query": repair["repair_mean_capture_only"],
            "visited_csr_qualifications_residual": repair["repair_mean_capture_only"]["csr_qualifications"] -
                                                  repair["repair_mean_capture_only"]["csr_traversal_reuse"],
            "ordinary_qualification_calls_source_derived_not_instrumented":
                repair["repair_mean_capture_only"]["qualifications"] -
                repair["repair_mean_capture_only"]["short_row_proof_qualifications"],
            "interface_limitation": "The batched row callback remains an owning std::function constructed "
                "per expansion, alongside the compatible scalar callback; this is not an allocation-free "
                "interface. No caller-specific allocator timing was measured, so its exact contribution "
                "is unproven. Do not promote this as a completed native-cost repair."},
        "remaining_blocker": "Native-level performance is not restored: repaired supplier remains4.60x H3 "
            "at essentially equal recall. Qualification-related optimized symbols and repeated supplier "
            "unordered-container/allocation work remain substantial. Capture still has9562.823 qualifications/"
            "query,4413.199 repeats;2482.141 CSR accounting qualifications concern already-visited candidates. "
            "These counts are not wall-time attribution. A duplicate CheckValidPosting remains in posting admission, "
            "but its individual contribution is not isolated and removing it alone is not established to close the gap. "
            "Native visited-table growth logged zero events; no evidence supports blaming resize. No second repair attempted.",
        "durations": {"ordinary_processes": summary["native_process_seconds"],
            "ordinary_timed_body": summary["ordinary_seconds"],
            "four_fixture_processes": sum(r["process_seconds"] for r in fixtures["records"]),
            "separate_diagnostic_processes": sum(json.loads(p.read_text())["process_seconds"]
                for p in OUTPUT.glob("diagnostic_*/evidence.json"))},
        "protection": protection,
        "source_manifest_sha256": sha(OUTPUT / "experiment_source_hashes.json"),
        "native_diff_sha256": sha(OUTPUT / "native_source.diff"),
        "runtime_manifest_sha256": sha(OUTPUT / "runtime.json"),
        "summary_sha256": sha(OUTPUT / "summary.json"),
        "native_fixture_source_sha256": sha(HERE / "NativeTests.cpp"),
        "native_fixture_log": str(TOOL / "native-tests-final.log"),
        "preserved_setup_failures": ["Python helper-module import collision before native execution",
            "Wrong copied harness-header filename before build", "Native unit duplicate include-path compile error"],
        "outputs": {name: str(OUTPUT / name) for name in
            ("summary.json", "operations.json", "fixtures.json", "runtime.json", "repair_sources.json",
             "native_source.diff", "experiment_source_hashes.json")}}
    write(OUTPUT / "report.json", report)
    print("report", OUTPUT / "report.json", sha(OUTPUT / "report.json"))
    print("summary", OUTPUT / "summary.json", sha(OUTPUT / "summary.json"))


if __name__ == "__main__":
    main()
