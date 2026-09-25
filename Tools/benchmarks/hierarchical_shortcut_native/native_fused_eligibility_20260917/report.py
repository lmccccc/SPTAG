"""Reconcile the bounded fused/reference runs without launching queries."""
import gzip
import hashlib
import json
import shutil
import subprocess

from prepare import DATA, HERE, PARENT, TOOL, sha


def write(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def rows(root, name):
    with gzip.open(root / name / "queries.jsonl.nprobe_24.gz", "rt") as stream:
        return [json.loads(line) for line in stream]


def compare(old, new, frames=True):
    assert len(old) == len(new)
    ignored = {"filter_cost", "row_eligibility_evaluations", "degree_frames"}
    for a, b in zip(old, new):
        assert {k: v for k, v in a.items() if k not in ignored} == {
            k: v for k, v in b.items() if k not in ignored}, (a["query"], "native payload changed")
        if frames:
            old_frames = iter(a["degree_frames"])
            for frame in b["degree_frames"]:
                assert any(frame == prior for prior in old_frames), (a["query"], "completed frame changed")


def main():
    roots = {"reference": DATA / "comparisons/h1_native_fused_eligibility_reference_20260917",
             "fused": DATA / "comparisons/h1_native_fused_eligibility_20260917_v2"}
    tables, evidence = {}, []
    for version, root in roots.items():
        tables[version] = {r["case"]: r for r in json.loads((root / "summary.json").read_text())}
        records = [json.loads(p.read_text()) for p in sorted(root.glob("*/record.json"))]
        assert len(records) == (6 if version == "reference" else 14)
        for record in records:
            directory = root / record["directory"]
            assert json.loads((directory / "native.exit.json").read_text())["returncode"] == 0
            assert json.loads((directory / "native.io.json").read_text())["direct_io"]
            text = "\n".join((directory / f"native.{part}.log").read_text() for part in ("stdout", "stderr"))
            assert text.splitlines().count("NATIVE_INDEX_LOAD calls=1") == 1
            assert text.count("NATIVE_SUPPLIER_OWNER_LOAD count=1") == 1
            for label in ("Vector (160091,128)", "BKT (1,160093)", "RNG (160091,32)",
                          "Vector (4098,128)", "BKT (1,4100)", "RNG (4098,32)"):
                assert text.count("Load " + label + " Finish!") == 1
            digest, count = hashlib.sha256(), 0
            with gzip.open(directory / (record["native"]["capture_file"] + ".gz"), "rb") as stream:
                for line in stream:
                    digest.update(line)
                    count += 1
            assert digest.hexdigest() == record["validation"]["raw_sha256"]
            assert count == record["native"]["queries"]
            evidence.append({"version": version, "directory": record["directory"],
                "native_loads": 1, "queries": count, "process_wall_seconds": record["process_wall_seconds"],
                "operations": record["operations"], "actual_direct_io": True})
        for case in ("B", "C"):
            row = tables[version][case]
            assert row["filter_cost"]["exact_predicate"] == row["filter_cost"]["numeric_predicate"] == 1
            assert row["work"]["native_default_admission"] == 0 and row["work"]["native_filtered_admission"] == 1
            assert all(row["work"][key] == 0 for key in ("supplier_calls", "supplier_parent_distances",
                       "native_child_distances", "supplier_members", "signature_checks"))
        assert tables[version]["B"]["filter_cost"]["observer_installed"] == 0
        assert tables[version]["C"]["filter_cost"]["observer_installed"] == 1
        assert tables[version]["B"]["native_ssd_work"] == tables[version]["C"]["native_ssd_work"]

    diffs = {}
    for case in ("A", "B", "C"):
        old = rows(roots["reference"], f"ordinary_r1_{case}")
        new = rows(roots["fused"], f"ordinary_r1_{case}")
        compare(old, new)
        assert tables["reference"][case]["native_ssd_work"] == tables["fused"][case]["native_ssd_work"]
        diffs["before_after_" + case] = {"queries": len(old), "head_changes": 0, "final_changes": 0,
            "native_payload_equal_except_diagnostic_reductions": True}
    for version, root in roots.items():
        a, b, c = [rows(root, f"ordinary_r1_{case}") for case in ("A", "B", "C")]
        compare(b, c, frames=False)
        assert all(f["d"] == f["e"] for row in c for f in row["degree_frames"])
        diffs[version + "_A_B"] = {
            key: [x["query"] for x, y in zip(a, b) if x[key] != y[key]] for key in ("heads", "results")}
        diffs[version + "_B_C"] = {"queries": len(b), "head_changes": 0, "final_changes": 0}
    for scenario in ("medium_tag", "extreme_tag", "numeric", "mixed_dnf"):
        compare(rows(DATA / "comparisons/h1_native_filter_cost_bound_20260917", "fixture_" + scenario),
                rows(roots["fused"], "fixture_" + scenario))

    clock = json.loads((roots["fused"] / "clocks_C/clock_proof.json").read_text())
    assert clock["actual_calls"] == 8 and clock["fine_query_clocks"] == 0
    proof = json.loads((TOOL / "diagnostic_provenance.json").read_text())
    for name, digest in proof["after"].items():
        assert sha(TOOL / "source" / name) == digest
    parent = proof["fused_parent"]
    for name, digest in parent["source"]["after"].items():
        assert sha(PARENT / "source" / name) == digest
    assert sha(PARENT / "source/Release/spannaclbench") == parent["binary_sha256"]
    symbols = subprocess.check_output(["nm", "-C", "--defined-only", str(TOOL / "source/Release/spannaclbench")],
                                     text=True)
    assert not any(s in symbols for s in ("H1Supplier::", "BenchmarkRestore", "Engine::Score"))
    curve = DATA / "comparisons/h1_native_ratio_curve_20260917_v3"
    assert sha(curve / "summary.stage_unfilter_broad.json") == \
        "426282677cb03f06db67f3adb99f8388f173949c1e0b92cc5b3757c73be0b0e2"
    assert json.loads((curve / "status.json").read_text())["completed_stages"] == ["unfilter_broad"]
    assert not (curve / "summary.json").exists()
    protected = json.loads((curve / "input_hashes.json").read_text())
    rehashed = []
    from pathlib import Path
    for item in protected:
        path = Path(item["path"])
        stat = path.stat()
        assert stat.st_size == item["bytes"]
        if stat.st_mtime_ns != item["mtime_ns"]:
            assert sha(path) == item["sha256"]
            rehashed.append(str(path))
    write(roots["fused"] / "input_hashes.json", protected)
    write(roots["fused"] / "input_preservation.json", {
        "origin": str(curve / "input_hashes.json"), "unchanged_metadata_hashes_reused": len(protected) - len(rehashed),
        "changed_metadata_rehashed": rehashed})

    deltas = {name: {"B_minus_A_ms_not_pure_predicate": table["B"]["ordinary_ms"] - table["A"]["ordinary_ms"],
                     "C_minus_B_ms_observer": table["C"]["ordinary_ms"] - table["B"]["ordinary_ms"]}
              for name, table in tables.items()}
    savings = {case: {"ordinary_ms": tables["reference"][case]["ordinary_ms"] - tables["fused"][case]["ordinary_ms"],
        "posting_calls_capture": tables["reference"][case]["filter_cost"]["posting_calls"] -
                                 tables["fused"][case]["filter_cost"]["posting_calls"],
        "traversal_calls_capture": tables["reference"][case]["filter_cost"]["traversal_calls"] -
                                   tables["fused"][case]["filter_cost"]["traversal_calls"]}
        for case in ("B", "C")}
    result = {"tables": tables, "deltas": deltas, "savings": savings, "output_differences": diffs,
        "native_loads": len(evidence), "ordinary_comparison_loads": 12, "load_evidence": evidence,
        "fine_clock_proof": clock, "additional_curve_stages": 0, "curve_evidence_unchanged": True,
        "fused_summary_sha256": sha(roots["fused"] / "summary.json"),
        "reference_summary_sha256": sha(roots["reference"] / "summary.json"),
        "remaining_work": "Visited-degree predicates; startup scan; native Boolean tree API discards eligibility; "
                          "supplier CSR eligibility/consume repeats; own helper unchanged",
        "whole_filtered_cost_solved": False}
    write(roots["fused"] / "reconciliation.json", result)
    report = ["# Native fused eligibility: bounded result", "",
        "Fresh paired original-bound reference and fused runtime; no curve stages resumed.", "",
        "| version | case | final Recall@10 | ordinary ms | QPS | min/max ms |",
        "|---|---|---:|---:|---:|---|"]
    for version, table in tables.items():
        for case, row in table.items():
            report.append(f'| {version} | {case} | {row["recall_at_10"]:.4f} | {row["ordinary_ms"]:.6f} | '
                          f'{row["qps"]:.2f} | {min(row["ordinary_ms_runs"]):.6f}/{max(row["ordinary_ms_runs"]):.6f} |')
    report += ["", "A: original empty native H1. B: real nonempty all-matching numeric native admission, no observer. "
        "C: same admission plus retained-ratio observer. All use native NProbe=[24], topk10, MaxCheck2048, "
        "HierarchyMaxCheck512, hierarchy ratio0.666666, retention0.5/min16,1000warmup+1000measured, "
        "two rotated ordinary repetitions, NUMA2/thread1/O_DIRECT/page15.",
        "", "## Causal change and counters", "",
        "d/e is counted in original native ordinary-neighbor processing. No separate ordinary adjacency "
        "qualification pass remains. Local eligibility is handed to traversal and result admission; "
        "posting success still avoids own-predicate evaluation. Visited markings, rejected candidates, "
        "native checked counts, scoring, queues, own notifications and collapsed stopping are preserved. "
        "Incomplete ordinary rows cannot call the helper. Startup keeps its separately counted observation "
        "and original expandEdges consumption. Selected CSR rows remain complete.",
        "", "The graph-width scratch contains only deferred prefix IDs, not a query-wide eligibility cache. "
        "It avoids eager visited-member qualification before the minimum degree is established. "
        "Capture-only short-row proof calls are explicitly counted and are not ordinary hot-loop calls.",
        "", "| version/case | posting calls | qualification/traversal calls | separate startup rows | "
        "fused members | visited members | traversal handoffs | result handoffs |",
        "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for version, table in tables.items():
        for case in ("B", "C"):
            f = table[case]["filter_cost"]
            report.append(f'| {version}/{case} | {f["posting_calls"]:.3f} | {f["traversal_calls"]:.3f} | '
                f'{f["degree_rows"]:.3f} | {f.get("fused_members", 0):.3f} | '
                f'{f.get("degree_visited_members", 0):.3f} | {f.get("traversal_handoffs", 0):.3f} | '
                f'{f.get("result_handoffs", 0):.3f} |')
    report += ["", "Counters are actual untimed capture calls, not milliseconds. "
        "The reference degree_rows field includes its separate ordinary scans, not just startup. "
        "See reconciliation.json for unique/repeated qualification IDs, short-row proof-only calls, "
        "partial rows and per-process operation timings. Unique/repeated sets exist only in capture.",
        "", f'Observer C-B: reference {deltas["reference"]["C_minus_B_ms_observer"]:.6f}ms; '
        f'fused {deltas["fused"]["C_minus_B_ms_observer"]:.6f}ms. '
        f'Fused B-A is {deltas["fused"]["B_minus_A_ms_not_pure_predicate"]:.6f}ms, not pure predicate cost.',
        "", "## Correctness and limits", "",
        "All1000 heads, final IDs/distances, own results and deterministic native work are identical "
        "before/after for each case. B/C are identical on outputs/core work, retain actual nonempty/exact/numeric "
        "flags, reject the default certificate, have e=d and zero helpers/upper distances/CSR/signature checks. "
        "The native coverage proof matches1M records and160091 persisted own attributes for "
        "column1<=4294966124; predicate bytes and inputs are unchanged.",
        "", f'A/B differ on {len(diffs["fused_A_B"]["heads"])} head queries and '
        f'{len(diffs["fused_A_B"]["results"])} final queries, including q915; this preserves native filtered '
        "posting-only/all-evaluated-own/O-tail semantics rather than tuning recall. "
        "Four tiny real-filter payloads match the frozen bound version except explicit diagnostic reductions. "
        "Native fixtures compare exact visited decisions (including rejected IDs), own notifications, "
        "distances, queues and results at budgets1/2/8/32/2048, with own-only/deleted/collapsed/alias/tie "
        "and malformed degree rows. Existing ratio/startup/signature/full-row fixtures remain.",
        "", "Actual chrono interposition finds eight coarse Run boundary calls and zero fine query clocks. "
        "Instrumented timings are not ordinary performance samples. Native LoadAll may defer loading "
        "into first capture; its small API duration is not physical loading cost.",
        "", "Remaining visited-member qualification is required without a persistent cache. "
        "Tree Boolean APIs and CSR eligibility/consume paths still discard some results. The existing "
        "own helper already checks known distance against its heap bound before canonical/version/predicate "
        "metadata. None of these remaining paths was optimized in this task. No further variant was run.",
        "", f'Fused summary SHA-256: `{result["fused_summary_sha256"]}`. '
        "The broader filtered-performance goal is not declared solved; stop for review."]
    with (roots["fused"] / "report.md").open("x") as stream:
        stream.write("\n".join(report) + "\n")
    final = roots["fused"] / "final_provenance"
    final.mkdir()
    shutil.copytree(HERE, final / "experiment", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("tests.log", "native-coverage.log", "diagnostic_provenance.json",
                 "build/AnnService/CMakeFiles/spannaclbench.dir/flags.make",
                 "build/AnnService/CMakeFiles/spannaclbench.dir/link.txt"):
        shutil.copy2(TOOL / name, final / name.split("/")[-1])
    write(final / "manifest.json", {"binary_sha256": sha(TOOL / "source/Release/spannaclbench"),
        "test_binary_sha256": sha(TOOL / "tests/nativereusetests"),
        "files": [{"relative": str(p.relative_to(final)), "sha256": sha(p)}
                  for p in sorted(final.rglob("*")) if p.is_file()]})
    for root in roots.values():
        write(root / "status.json", {"status": "completed_bounded_fused_eligibility",
            "ordinary_loads": 6, "additional_curve_stages": 0, "automatic_continuation": False,
            "whole_filtered_cost_solved": False})
    print(json.dumps({"deltas": deltas, "savings": savings, "summary": result["fused_summary_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
