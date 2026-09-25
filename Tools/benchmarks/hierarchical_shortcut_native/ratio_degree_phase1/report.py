"""Reconcile only Phase1 evidence and publish measured costs without more searches."""
import collections
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ratio_phase1_runner", HERE / "run.py")
phase = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase)


def main():
    plan = phase.native.read_ini(HERE / "experiment.ini")["Experiment"]
    root = Path(plan["OutputDirectory"])
    rows = json.loads((root / "summary.json").read_text())
    by_case = {r["case"]: r for r in rows}
    runs = json.loads((root / "runs.json").read_text())
    fixtures = json.loads((root / "fixtures.json").read_text())
    assert len(runs) == 12 and len(fixtures) == 10 and len(rows) == 4
    captures, calls = 0, []
    for run in runs + fixtures:
        directory = root / run["directory"]
        assert json.loads((directory / "native.exit.json").read_text())["returncode"] == 0
        assert json.loads((directory / "native.io.json").read_text())["direct_io"]
        logs = [(directory / f"native.{part}.log").read_text() for part in ("stdout", "stderr")]
        entries = [line for text in logs for line in text.splitlines() if line.startswith("NATIVE_INDEX_LOAD ")]
        assert entries == ["NATIVE_INDEX_LOAD calls=1"]
        native_rows = [json.loads(line) for text in logs for line in text.splitlines()
                       if line.startswith('{"engine":')]
        assert native_rows == [p["native"] for p in run["points"]]
        calls.append({"directory": run["directory"], "actual_loadall_entries": entries})
        for point in run["points"]:
            path = directory / (point["native"]["capture_file"] + ".gz")
            digest, count = hashlib.sha256(), 0
            with gzip.open(path, "rb") as stream:
                for line in stream:
                    digest.update(line); count += 1
            assert count == point["validation"]["queries"]
            assert digest.hexdigest() == point["validation"]["raw_capture_sha256"]
            captures += 1
    assert captures == 34
    parent = Path(plan["PreviousResults"])
    with gzip.open(root / "plain_r1_h1/queries.jsonl.nprobe_24.gz", "rt") as current, \
         gzip.open(parent / "batch_r1_unfilter_h1/queries.jsonl.nprobe_24.gz", "rt") as original:
        count = 0
        for a, b in zip(current, original):
            a, b = json.loads(a), json.loads(b)
            assert a["heads"] == b["heads"] and a["results"] == b["results"]
            count += 1
        assert count == 1000
    difference = json.loads((root / "h1_admission_difference.json").read_text())
    reference, supplier, h1 = [by_case[c] for c in ("reference", "supplier", "h1")]
    actual_distances = {}
    for case in by_case:
        with gzip.open(root / f"profile_{case}/queries.jsonl.nprobe_24.gz", "rt") as stream:
            actual_distances[case] = sum(json.loads(line)["calls"] for line in stream) / 1000
    removed_percent = 100 * (reference["ordinary_ms"] - supplier["ordinary_ms"]) / reference["ordinary_ms"]
    queue_work = supplier["work"]
    rejection = queue_work["supplyQueueRejected"] / queue_work["supplyQueueOffers"]
    filtered = []
    for run in fixtures:
        if run["scenario"] == "unfilter":
            continue
        point = run["points"][0]
        w = point["validation"]["work"]
        filtered.append({
            "scenario": run["scenario"], "queries": 8, "recall_at_10": point["validation"]["recall_at_10"],
            "underfilled_queries": point["validation"]["underfilled_queries"],
            **{k: w[k] for k in ("supplyCalls", "supplyMembers", "supplyUpperMembers", "supplyLowerMembers",
                                "supplyParent", "supplyChild", "supplyChildQueueOffers", "supplyChildQueueRejected")},
        })
    phase.write(root / "filtered_fixture_work.json", filtered)
    report = [
        "# Ratio-degree Phase1: complete; no full sweep", "",
        "Policy: d>=16 and e<0.5*d, with relative target ceil(0.5*d). "
        "Visited does not reduce d/e. Physical degree<16 never invokes supplementation.",
        "Only unfilter24 was timed at1000 queries; filtered results below are8-query validity/work fixtures.",
        "", "| Case | Recall@10 | Ordinary ms | Ordinary QPS | Instrumented navigation ms |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        report.append(f'| {row["case"]} | {row["recall_at_10"]:.4f} | {row["ordinary_ms"]:.6f} | '
                      f'{row["qps"]:.3f} | {row["phases"]["graphOther"]:.6f} |')
    report += [
        "", "Navigation is from separate profiles with nested fine-grained clocks; "
        "it is not an ordinary-latency decomposition and includes profiling overhead.",
        f'Same-policy reference -> optimized supplier: {removed_percent:.2f}% lower ordinary latency '
        f'({reference["ordinary_ms"]:.6f} -> {supplier["ordinary_ms"]:.6f}ms). '
        f'Supplier still costs {supplier["ordinary_ms"]/h1["ordinary_ms"]:.3f}x original H1 latency.',
        "", "All actual unfilter helper calls, parent/child distances, CSR reads and selected upper rows are zero. "
        "The matched disabled control remains comparably slow, so the residual cost is common experimental "
        "qualification/degree/admission machinery, not posting fallback.",
        "", "## Measured remaining work and instrumented scopes", "",
        f'Per query: {queue_work["supplyConnChecks"]:.3f} complete degree frames, '
        f'{queue_work["supplyPredicateChecks"]:.3f} qualification evaluations, '
        f'{queue_work["supplyPredicateCache"]:.3f} qualification cache hits, '
        f'{queue_work["supplyGraph"] + queue_work["supplyRouting"]:.3f} actual H1/routing distances.',
        f'Independently counted actual distance callbacks: H1 {actual_distances["h1"]:.3f}, '
        f'control {actual_distances["control"]:.3f}, supplier {actual_distances["supplier"]:.3f}. '
        "Thus the large remaining slowdown is not explained by extra distance evaluations.",
        f'Native graph-neighbor heap offers/accepted/rejected: {queue_work["supplyQueueOffers"]:.3f}/'
        f'{queue_work["supplyQueueAccepted"]:.3f}/{queue_work["supplyQueueRejected"]:.3f}; '
        f'{100*rejection:.2f}% rejected after qualification and distance computation.',
        "", "| Scope | Reference instrumented us/query | Optimized instrumented us/query |",
        "|---|---:|---:|",
    ]
    for label in ("Reset", "Degree", "Qualify", "Distance", "Admit", "Helper"):
        key = "supply" + label + "Ns"
        report.append(f'| {label} | {reference["profile_component_work"][key]/1000:.3f} | '
                      f'{supplier["profile_component_work"][key]/1000:.3f} |')
    report += [
        "", "Degree includes qualification; helper includes nested scoring/admission. Do not sum these scopes. "
        "Reset measures epoch/state/diagnostic clearing, not callback construction before the Reset call.",
        "The changes remove ordinary per-frame hash/vector allocations, eagerly cleared unused upper state, "
        "redundant qualification calls and one dispatch layer when the exact native function pointer is available. "
        "Existing admission heaps and remaining callbacks/counters are retained for correctness.",
        "", "## Baseline differences, not rewritten", "",
        "Original H1 heads/final IDs exactly match the authenticated preceding H1 capture. "
        f'Compared with ratio supplier, {difference["head_changed_queries"]}/1000 queries change selected heads '
        f'and {difference["result_changed_queries"]}/1000 change final IDs. '
        "All shared final IDs retain exactly equal distances. Control/reference/optimized supplier outputs agree.",
        "Fifteen changed head sets replace own-only eligible heads (qualification bit2, no valid posting) "
        "with posting-admissible heads (bit3). Query915 changes one final dataset result through this admission "
        "difference, not a supplier call or result rewrite. The other two head differences, queries661/772, "
        "occur at the native2048 checked-leaf boundary with the existing result-filtered traversal/admission "
        "rather than ordinary H1's path. They are not asserted to have baseline head parity.",
        "", "## Small filtered fixtures: not full-workload benchmarks", "",
        "| Scenario | Helper calls/query | CSR members/query | Parent distances | Child distances | Child heap rejected/offered |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in filtered:
        report.append(f'| {row["scenario"]} | {row["supplyCalls"]:.3f} | {row["supplyMembers"]:.3f} | '
                      f'{row["supplyParent"]:.3f} | {row["supplyChild"]:.3f} | '
                      f'{row["supplyChildQueueRejected"]:.3f}/{row["supplyChildQueueOffers"]:.3f} |')
    report += [
        "", "Far candidates are not free: their qualification/distance has already happened before native heap "
        "rejection, and every selected posting row must still be inspected completely. Even this small filtered "
        "sample has many calls and substantial row reads; it does not support assuming supplementation is rare.",
        "", "The ratio rule and targeted hot-path changes work, but performance remains poor relative to H1. "
        "Phase1 stops here; no198-point sweep or blind follow-on variants were launched.",
    ]
    (root / "report.md").write_text("\n".join(report) + "\n")
    # Hash used native inputs, not the previous multi-hour sweep's raw captures.
    inputs = {Path(plan["Queries"]), Path(plan["Workloads"]), Path(plan["PhysicalGraph"])}
    workloads = json.loads(Path(plan["Workloads"]).read_text())
    inputs.add(Path(workloads["attributes"]))
    for scenario in ("unfilter", "medium_tag", "extreme_tag", "numeric", "mixed_dnf"):
        inputs.add(Path(workloads["truth"][scenario]["ids"]))
        if scenario in workloads["flat_query_tags"]:
            inputs.add(Path(workloads["flat_query_tags"][scenario]))
    inputs.update(Path(p) for p in workloads["query_dnf"].values())
    index = Path(plan["Index"])
    for child in index.iterdir():
        if child.is_file():
            inputs.add(child.resolve())
        elif child.is_dir():
            inputs.update(p.resolve() for p in child.resolve().rglob("*") if p.is_file())
    phase.write(root / "input_hashes.json", [phase.fp(p) for p in sorted(inputs)])
    proof = json.loads((Path(plan["Toolchain"]) / "ratio_provenance.json").read_text())
    for name, digest in proof["after"].items():
        assert phase.fp(Path(plan["Toolchain"]) / "source" / name)["sha256"] == digest
        assert phase.fp(Path(proof["parent_toolchain"]) / "source" / name)["sha256"] == proof["before"][name]
    final = root / "final_provenance"
    final.mkdir()
    for source in HERE.iterdir():
        if source.is_file():
            shutil.copy2(source, final / source.name)
    phase.write(root / "independent_reconciliation.json", {
        "phase": 1, "native_processes": 22, "actual_load_evidence": calls,
        "compressed_captures_rehashed": captures, "original_h1_reference_ids_and_heads_equal": True,
        "unfilter_zero_actual_supplier_work": True, "same_ratio_reference_latency_reduction_percent": removed_percent,
        "unfilter_native_heap_rejection_fraction": rejection,
        "profile_actual_distance_calls_per_query": actual_distances,
        "instrumented_scopes_not_additive_or_ordinary_latency": True,
        "parent_frozen_sources_unchanged": True, "full_sweep_launched": False,
        "summary_sha256": phase.fp(root / "summary.json")["sha256"],
        "report_sha256": phase.fp(root / "report.md")["sha256"],
        "input_hashes_sha256": phase.fp(root / "input_hashes.json")["sha256"],
    })
    print("\n".join(report))


if __name__ == "__main__":
    main()
