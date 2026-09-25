"""Reconcile bounded Phase2 evidence; no native search launches."""
from collections import Counter
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("phase2_report_runner", HERE / "run.py")
phase = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase)


def main():
    plan = phase.native.read_ini(HERE / "experiment.ini")["Experiment"]
    root, tool = Path(plan["OutputDirectory"]), Path(plan["Toolchain"])
    parent = root.parent / "h1_ratio_phase1_20260917"
    rows = json.loads((root / "summary.json").read_text())
    by_case = {r["case"]: r for r in rows}
    runs = json.loads((root / "runs.json").read_text())
    fixtures = json.loads((root / "fixtures.json").read_text())
    assert len(runs) == 9 and len(fixtures) == 6
    loads, captures = [], 0
    component_lines = [
        "Load Vector (160091,128) Finish!", "Load BKT (1,160093) Finish!", "Load RNG (160091,32) Finish!",
        "Load Vector (4098,128) Finish!", "Load BKT (1,4100) Finish!", "Load RNG (4098,32) Finish!",
    ]
    for run in runs + fixtures:
        directory = root / run["directory"]
        assert json.loads((directory / "native.exit.json").read_text())["returncode"] == 0
        assert json.loads((directory / "native.io.json").read_text())["direct_io"]
        logs = "\n".join((directory / f"native.{p}.log").read_text() for p in ("stdout", "stderr"))
        entries = [s for s in logs.splitlines() if s.startswith("NATIVE_INDEX_LOAD ")]
        assert entries == ["NATIVE_INDEX_LOAD calls=1"]
        components = {s: logs.count(s) for s in component_lines}
        assert all(n == 1 for n in components.values())
        loads.append({"directory": run["directory"], "actual_loadall_entries": entries,
                      "physical_component_load_counts": components, "measured_probe_batches": len(run["points"])})
        for point in run["points"]:
            digest, count = hashlib.sha256(), 0
            with gzip.open(directory / (point["native"]["capture_file"] + ".gz"), "rb") as stream:
                for line in stream:
                    digest.update(line); count += 1
            assert count == point["validation"]["queries"]
            assert digest.hexdigest() == point["validation"]["raw_capture_sha256"]
            captures += 1
    assert captures == 19
    differences = {"head_changed_queries": [], "result_changed_queries": []}
    with gzip.open(root / "plain_r1_h1/queries.jsonl.nprobe_24.gz", "rt") as h, \
         gzip.open(root / "plain_r1_supplier/queries.jsonl.nprobe_24.gz", "rt") as s:
        for i, (a, b) in enumerate(zip(h, s)):
            a, b = json.loads(a), json.loads(b)
            if a["heads"] != b["heads"]: differences["head_changed_queries"].append(i)
            if a["results"] != b["results"]: differences["result_changed_queries"].append(i)
        assert i == 999
    assert len(differences["head_changed_queries"]) == 17
    assert differences["result_changed_queries"] == [915]
    phase.write(root / "h1_difference_queries.json", differences)
    actual_distances = {}
    for case in by_case:
        with gzip.open(root / f"profile_{case}/queries.jsonl.nprobe_24.gz", "rt") as stream:
            actual_distances[case] = sum(json.loads(line)["calls"] for line in stream) / 1000
    diagnostics = root / "diagnostics"
    old_probes = json.loads((diagnostics / "runs.json").read_text())
    clock_counts = {r["directory"]: sum(c["count"] for c in r["clocks"]) for r in old_probes}
    assert clock_counts["clock_supplier_plain"] == clock_counts["clock_h1_plain"] == 2
    assert clock_counts["clock_supplier_profile"] == 435574
    sampling = {}
    for name in ("sample_supplier_plain_v2", "sample_h1_plain_v2", "sample_phase2_supplier_plain"):
        data = json.loads((diagnostics / name / "symbols.json").read_text())
        assert sum(r["count"] for r in data["clocks"]) == 2
        assert all(r["function"] == "Run(int, char**)" for r in data["clocks"])
        counts = Counter()
        for r in data["samples"]:
            if r["kind"] == "pc":
                counts[r.get("function", r["binary"])] += r["count"]
        sampling[name] = {
            "total_pc_samples": sum(counts.values()),
            "buffer_overflow_drops": sum(r["count"] for r in data["samples"] if r["kind"] == "dropped"),
            "functions": dict(counts.most_common()),
        }
    phase.write(root / "sampling_evidence.json", sampling)
    binary = tool / "source/Release/spannaclbench"
    with (diagnostics / "remaining_hot_instructions.asm").open("x") as stream:
        for begin, end in ((0x135020, 0x135056), (0x3e2d09, 0x3e2d40), (0xda03c, 0xda090)):
            subprocess.run(["objdump", "-d", f"--start-address={begin}", f"--stop-address={end}",
                            str(binary)], stdout=stream, check=True)
    # Rehash exactly the authenticated inputs used by the inherited protocol.
    inputs = json.loads((parent / "input_hashes.json").read_text())
    for entry in inputs:
        assert phase.fp(Path(entry["path"]))["sha256"] == entry["sha256"], entry["path"]
    phase.write(root / "input_hashes.json", inputs)
    proof = json.loads((tool / "ratio_provenance.json").read_text())
    for name, digest in proof["after"].items():
        assert phase.fp(tool / "source" / name)["sha256"] == digest, name
    for name, digest in proof["before"].items():
        assert phase.fp(Path(proof["parent_toolchain"]) / "source" / name)["sha256"] == digest, name
    assert phase.fp(Path(proof["parent_toolchain"]) / "source/Release/spannaclbench")["sha256"] == proof["parent_binary_sha256"]
    h1, reference, supplier = [by_case[c] for c in ("h1", "reference", "supplier")]
    gain = 100 * (1 - supplier["ordinary_ms"] / reference["ordinary_ms"])
    w = supplier["work"]
    filtered = [
        {"scenario": r["scenario"], "queries": 8, **r["points"][0]["validation"]}
        for r in fixtures if r["scenario"] != "unfilter"
    ]
    phase.write(root / "filtered_fixture_work.json", filtered)
    report = [
        "# Ratio hot-path Phase2: bounded work complete; performance gap remains", "",
        "Same ratio policy: d>=16 and e/d<0.5; equality and physically short rows never trigger. "
        "Visited does not reduce eligibility, and selected upper rows still finish completely. "
        "No new cap, graph/frontier policy, result substitution or full sweep.",
        "", "## Ordinary, fresh within-runtime comparison", "",
        "1000 warm-up +1000 measured queries,1 query thread, NUMA CPU/memory node2, O_DIRECT, page15, "
        "native SearchSweep.NProbe=[24]; reversed case order in repetition2. Ordinary measurements have "
        "no preload and no phase clocks. Captures are separate untimed native passes.",
        "The reference is the Phase1-optimized implementation rebuilt in this same frozen-compatible runtime "
        "with ShortcutIntrinsicCache=false, not timing copied from the archived Phase1 executable. "
        "Every new native capture/core-work record is checked against the archived Phase1 evidence. "
        "The actual archived executable was used for the original clock and CPU-sampling diagnostics.",
        "", "| Case | Recall@10 | Ordinary ms | QPS | Separate coarse-profile navigation ms |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        report.append(f'| {r["case"]} | {r["recall_at_10"]:.4f} | {r["ordinary_ms"]:.6f} | '
                      f'{r["qps"]:.3f} | {r["phases"]["graphOther"]:.6f} |')
    report += [
        "", f'Compact qualification lowers ordinary latency by {gain:.2f}% '
        f'({reference["ordinary_ms"]:.6f} -> {supplier["ordinary_ms"]:.6f}ms), but still costs '
        f'{supplier["ordinary_ms"]/h1["ordinary_ms"]:.3f}x H1. The user performance goal is not yet met.',
        "", "## Actual clocks: hidden ordinary fine-clock hypothesis refuted", "",
        "Interposition observes the real libstdc++ steady_clock/system_clock symbols, by return address. "
        "Original Phase1 supplier and H1 ordinary runs each make exactly2 chrono calls, both in Run. "
        "The positive-control fine profile makes435574 calls. Original1000-query sampled runs and the "
        "new Phase2 sampled ordinary run also have exactly2 calls. Query-source search finds no direct "
        "clock_gettime call in AnnService. This is runtime entry evidence, not a claim from zero Ns fields.",
        "Phase2 disables Engine fine clocks, even during profiling, and keeps only coarse adapter boundaries "
        "plus existing native phase profiling. Old fine Ns fields are deliberately zero, not measured costs.",
        "", "## Causal change and validity", "",
        "Original sampled qualification thunk:278 PCs, of which127 follow the canonical VID load and135 "
        "follow the live version-byte test; CheckValidPosting has73 more samples. The new compact intrinsic "
        "flag table replaces these repeated scattered metadata reads; predicate qualification remains per query.",
        "The table stores real native posting-valid and own-not-deleted bits, including own-only heads; "
        "it does not assume all postings valid. Memory is160091 bytes per query thread, initialized on the "
        "first guarded navigation (in warm-up), with no copied vectors or parent table. VersionLabel mutation "
        "revisions cover initialization, both load implementations, deletion, SetVersion, IncVersion, AddBatch "
        "and SetR. Static posting LoadIndex has its own identity/revision. Read guards span native navigation; "
        "these mutations acquire write guards. Static canonical head identity is required and mutable layout "
        "is rejected. This is isolated experiment code, not a production routing/cache feature.",
        "Native fixtures cover deletion, undelete via SetVersion, version increment, resize/add, layout-key "
        "change, distinct instance identity, own-only validity and writer exclusion. All inherited ratio, "
        "startup, signature, visited, whole-row and native-continuation fixtures remain passing.",
        "", "## Remaining cost: measurements, not callback speculation", "",
        "Measured-region SIGPROF sample totals: Phase1 supplier1573, H1683, Phase2 supplier1215. "
        "The qualifier thunk falls278 ->30 samples; CheckValidPosting is no longer a major hotspot. "
        "These are process CPU PCs (including native worker/syscall contexts), not exact wall-time shares "
        "or call counts. perf was denied at perf_event_paranoid=4; permissions were not changed.",
        "Phase2 remaining notable PCs:179 immediately after the native result-filtered path reads a candidate's "
        "last graph slot to detect collapsed nodes (BKTIndex.cpp targetCheckNode);62 after Engine::Score's "
        "distance-epoch load;48 after Engine::Qualify's qualification-epoch load. The native collapsed-node "
        "check is required by the retained result-filtered path, not an upper-posting scan. Do not remove it "
        "or the epoch/admission state without preserving collapsed/own-only/native stopping semantics.",
        "", "| Coarse scope | Cache-off us/query | Cache-on us/query |",
        "|---|---:|---:|",
    ]
    for scope in ("Intrinsic", "Prepare", "Search", "Finish"):
        k = "supply" + scope + "Ns"
        report.append(f'| {scope} | {reference["profile_component_work"][k]/1000:.3f} | '
                      f'{supplier["profile_component_work"][k]/1000:.3f} |')
    report += [
        "", "These separate clock-light profiles localize residual cost inside native search; preparation "
        "includes callback/scratch construction before Reset, and finalization includes result sorting/capture "
        "copying when enabled. They are not spliced into ordinary latency or interpreted as exact ordinary "
        "component costs. No thousands-of-scopes attribution is used.",
        f'Actual distances/query: H1 {actual_distances["h1"]:.3f}, supplier '
        f'{actual_distances["supplier"]:.3f}; supplier complete degree frames {w["supplyConnChecks"]:.3f}, '
        f'qualification evaluations/cache hits {w["supplyPredicateChecks"]:.3f}/{w["supplyPredicateCache"]:.3f}.',
        f'Native heap offers/accepts/rejects {w["supplyQueueOffers"]:.3f}/'
        f'{w["supplyQueueAccepted"]:.3f}/{w["supplyQueueRejected"]:.3f}. Rejection occurs after necessary '
        "qualification/scoring; far neighbors are not free. Every actual unfilter helper call, upper distance "
        "and CSR scan remains zero.",
        "", "## Evidence boundaries", "",
        "15 successful protocol processes:6 ordinary,3 coarse profiles,6 small fixture invocations,19 native "
        "captures independently rehashed. Both array orderings16/24/384 and four8-query filtered fixtures "
        "match Phase1 full payloads/core native work. Physical H1/H3 vector/BKT/RNG load messages each occur "
        "once per invocation; the historical top H3 graph still loads as backing but no upper frontier is used.",
        "H1 differs from supplier on exactly17 selected-head queries and1 final-result query(915), as in "
        "Phase1. The own-only admission and native boundary explanation is preserved; no result rewriting.",
        "Six successful diagnostic native processes are separate from performance evidence. The initial "
        "constructor-start sampling attempt failed before observed IO: its timer could survive numactl exec "
        "after signal handlers reset. That failed attempt is preserved, not a successful benchmark. "
        "The corrected sampler arms only at the verified first native outer timer and stops at the second.",
        "No full grid or second policy/parameter search was started. Remaining measured native "
        "collapsed-admission/epoch-table costs are reported for review; Phase2 stops here.",
    ]
    (root / "report.md").write_text("\n".join(report) + "\n")
    phase.write(root / "independent_reconciliation.json", {
        "phase": 2, "protocol_native_processes": 15, "actual_load_evidence": loads,
        "compressed_captures_rehashed": captures, "successful_diagnostic_processes": 6,
        "failed_diagnostic_attempt_preserved": "diagnostics/sample_supplier_plain",
        "clock_counts": clock_counts, "phase2_ordinary_clock_calls": 2,
        "exact_phase1_payload_and_core_work": True, "h1_difference_queries": differences,
        "actual_distance_calls_per_query": actual_distances,
        "unfilter_actual_supplier_and_upper_work_zero": True,
        "ordinary_latency_reduction_percent": gain, "remaining_h1_latency_ratio": supplier["ordinary_ms"]/h1["ordinary_ms"],
        "parent_frozen_sources_and_binary_unchanged": True, "used_input_files_rehashed": len(inputs),
        "full_sweep_launched": False, "performance_goal_met": False,
        "summary_sha256": phase.fp(root / "summary.json")["sha256"],
        "report_sha256": phase.fp(root / "report.md")["sha256"],
    })
    final = root / "final_provenance"
    final.mkdir()
    shutil.copytree(HERE, final / "experiment", ignore=shutil.ignore_patterns("__pycache__"))
    phase.write(final / "manifest.json", {
        "files": [phase.fp(p) for p in sorted((final / "experiment").rglob("*")) if p.is_file()],
        "binary": phase.fp(binary), "source_archive": phase.fp(root / "snapshot/source.tar.gz"),
        "diagnostic_artifacts": [phase.fp(p) for p in sorted(diagnostics.rglob("*")) if p.is_file()],
    })
    print("\n".join(report))


if __name__ == "__main__":
    main()
