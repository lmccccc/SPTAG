"""Reconcile real-predicate cost and the one bounded result-admission fix."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[4] / "datasets/sift1m_zipf200_sparse193_numeric"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_capture(root, name):
    path = root / name / "queries.jsonl.nprobe_24.gz"
    with gzip.open(path, "rt") as stream:
        return [json.loads(line) for line in stream]


def differences(a, b):
    assert len(a) == len(b)
    return {"queries": len(a), "heads": [x["query"] for x, y in zip(a, b) if x["heads"] != y["heads"]],
            "results": [x["query"] for x, y in zip(a, b) if x["results"] != y["results"]]}


def write(path, data):
    with path.open("x") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


def main():
    reference = DATA / "comparisons/h1_native_filter_cost_20260917"
    fixed = DATA / "comparisons/h1_native_filter_cost_bound_20260917"
    tables, evidence = {}, []
    captures = 0
    for label, root in (("reference", reference), ("bound_fix", fixed)):
        tables[label] = {r["case"]: r for r in json.loads((root / "summary.json").read_text())}
        records = [json.loads(p.read_text()) for p in sorted(root.glob("*/record.json"))]
        assert len(records) == (16 if label == "reference" else 14)
        for record in records:
            directory = root / record["directory"]
            assert json.loads((directory / "native.exit.json").read_text())["returncode"] == 0
            assert json.loads((directory / "native.io.json").read_text())["direct_io"]
            text = "\n".join((directory / f"native.{part}.log").read_text() for part in ("stdout", "stderr"))
            assert text.splitlines().count("NATIVE_INDEX_LOAD calls=1") == 1
            assert text.count("NATIVE_SUPPLIER_OWNER_LOAD count=1") == 1
            assert text.count("NATIVE_DEFAULT_CERT heads=160091 own_all_live=1 immutable=1 vectors=1000000") == 1
            components = {s: text.count("Load " + s + " Finish!") for s in
                ("Vector (160091,128)", "BKT (1,160093)", "RNG (160091,32)",
                 "Vector (4098,128)", "BKT (1,4100)", "RNG (4098,32)")}
            assert set(components.values()) == {1}
            h, count = hashlib.sha256(), 0
            with gzip.open(directory / (record["native"]["capture_file"] + ".gz"), "rb") as stream:
                for line in stream:
                    h.update(line)
                    count += 1
            assert h.hexdigest() == record["validation"]["raw_sha256"] and count == record["native"]["queries"]
            captures += 1
            evidence.append({"version": label, "directory": record["directory"], "loadall_calls": 1,
                             "physical_components": components, "queries": count})
        for case in ("B", "C"):
            r = tables[label][case]
            assert r["filter_cost"]["exact_predicate"] == r["filter_cost"]["numeric_predicate"] == 1
            assert r["work"]["native_default_admission"] == 0 and r["work"]["native_filtered_admission"] == 1
            assert all(r["work"][k] == 0 for k in ("supplier_calls", "supplier_parent_distances",
                "native_child_distances", "supplier_members", "signature_checks"))
        assert tables[label]["B"]["filter_cost"]["observer_installed"] == 0
        assert tables[label]["C"]["filter_cost"]["observer_installed"] == 1
        for r in records:
            if r["directory"].startswith("sample_"):
                plain = json.loads((root / f'ordinary_r1_{r["case"]}/record.json').read_text())
                assert r["validation"] == plain["validation"] and r["filter_cost"] == plain["filter_cost"]
        tool = DATA / "toolchains" / ("h1_native_filter_cost_20260917" if label == "reference"
                                     else "h1_native_filter_cost_bound_20260917")
        proof = json.loads((tool / "diagnostic_provenance.json").read_text())
        for name, digest in proof["after"].items():
            assert sha(tool / "source" / name) == digest
        parent = Path(proof["parent"])
        for name, digest in proof["parent_source_hashes"].items():
            assert sha(parent / "source" / name) == digest
        assert sha(parent / "source/Release/spannaclbench") == proof["parent_binary_sha256"]
        symbols = subprocess.check_output(["nm", "-C", "--defined-only", str(tool / "source/Release/spannaclbench")],
                                          text=True)
        assert all(x not in symbols for x in ("H1Supplier::", "BenchmarkRestore", "BenchmarkDistance", "Engine::Score"))
        final = root / "final_provenance"
        final.mkdir()
        shutil.copytree(HERE, final / "experiment", ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("tests.log", "native-coverage.log", "diagnostic_provenance.json",
                     "build/AnnService/CMakeFiles/spannaclbench.dir/flags.make",
                     "build/AnnService/CMakeFiles/spannaclbench.dir/link.txt"):
            shutil.copy2(tool / name, final / Path(name).name)
        write(final / "manifest.json", {"native_binary_sha256": sha(tool / "source/Release/spannaclbench"),
            "native_test_binary_sha256": sha(tool / "tests/nativereusetests"),
            "files": [{"path": str(p.relative_to(final)), "sha256": sha(p)}
                      for p in sorted(final.rglob("*")) if p.is_file()]})
    changes = {}
    for case in ("A", "B", "C"):
        a, b = read_capture(reference, f"ordinary_r1_{case}"), read_capture(fixed, f"ordinary_r1_{case}")
        delta = differences(a, b)
        assert not delta["heads"] and not delta["results"]
        assert all({k: v for k, v in x.items() if k != "filter_cost"} ==
                   {k: v for k, v in y.items() if k != "filter_cost"} for x, y in zip(a, b))
        assert tables["reference"][case]["native_ssd_work"] == tables["bound_fix"][case]["native_ssd_work"]
        changes["reference_vs_fixed_" + case] = delta
    for label, root in (("reference", reference), ("bound_fix", fixed)):
        a, b, c = [read_capture(root, f"ordinary_r1_{case}") for case in ("A", "B", "C")]
        changes[label + "_A_vs_B"] = differences(a, b)
        changes[label + "_B_vs_C"] = differences(b, c)
        assert not changes[label + "_B_vs_C"]["heads"] and not changes[label + "_B_vs_C"]["results"]
        for q in range(1000):
            assert all(f["d"] == f["e"] for f in c[q]["degree_frames"])
        for scenario in ("medium_tag", "extreme_tag", "numeric", "mixed_dnf"):
            old = read_capture(reference, f"fixture_{scenario}")
            new = read_capture(fixed, f"fixture_{scenario}")
            assert all({k: v for k, v in x.items() if k != "filter_cost"} ==
                       {k: v for k, v in y.items() if k != "filter_cost"} for x, y in zip(old, new))
    samples = {}
    for case in ("B", "C"):
        resolved = json.loads((reference / f"sample_{case}/resolved_samples.json").read_text())
        totals = json.loads((reference / f"sample_{case}/sample_totals.json").read_text())
        samples[case] = {**totals,
            "CheckValidPosting_samples": sum(r["samples"] for r in resolved if "CheckValidPosting" in r["function"]),
            "wrapper_predicate_thunk_samples": sum(r["samples"] for r in resolved
                if "TenantIndexManager::SearchWithPredicate" in r["function"] and "lambda(int)#7" in r["function"]),
            "supplier_observer_samples": sum(r["samples"] for r in resolved
                if "NativeReuse::Search" in r["function"] and "lambda(int, int const*, int, bool," in r["function"]),
            "find_if_samples": sum(r["samples"] for r in resolved if "__find_if" in r["function"])}
    deltas = {}
    for label, table in tables.items():
        deltas[label] = {"B_minus_A_ms_not_pure_predicate": table["B"]["ordinary_ms"] - table["A"]["ordinary_ms"],
            "C_minus_B_ms_observer": table["C"]["ordinary_ms"] - table["B"]["ordinary_ms"],
            "C_over_A": table["C"]["ordinary_ms"] / table["A"]["ordinary_ms"]}
    savings = {case: {"ms": tables["reference"][case]["ordinary_ms"] - tables["bound_fix"][case]["ordinary_ms"],
                     "posting_calls": tables["reference"][case]["filter_cost"]["posting_calls"] -
                                      tables["bound_fix"][case]["filter_cost"]["posting_calls"]}
               for case in ("B", "C")}
    curve_summary = DATA / "comparisons/h1_native_ratio_curve_20260917_v3/summary.stage_unfilter_broad.json"
    assert sha(curve_summary) == "426282677cb03f06db67f3adb99f8388f173949c1e0b92cc5b3757c73be0b0e2"
    curve_status = json.loads((curve_summary.parent / "status.json").read_text())
    assert curve_status["completed_stages"] == ["unfilter_broad"] and curve_status["ordinary_index_loads_completed"] == 12
    result = {"tables": tables, "deltas": deltas, "savings": savings, "output_differences": changes,
        "ordinary_cpu_samples_not_wall_ms": samples, "native_successful_processes": 30,
        "ordinary_comparison_processes": 12, "capture_files_rehashed": captures, "load_evidence": evidence,
        "coverage": json.loads((reference / "snapshot/predicate_coverage.json").read_text()),
        "fine_query_clocks": 0, "coarse_clock_calls_per_process_audited": 8,
        "one_causal_fix": "Noncollapsed result-bound rejection after own notification",
        "extra_degree_eligibility_calls_per_query": tables["bound_fix"]["C"]["filter_cost"]["traversal_calls"] -
                                                  tables["bound_fix"]["B"]["filter_cost"]["traversal_calls"],
        "overall_filtered_cost_solved": False, "curve_version_unchanged": True, "additional_curve_stages": 0,
        "reference_summary_sha256": sha(reference / "summary.json"), "fixed_summary_sha256": sha(fixed / "summary.json")}
    write(fixed / "reconciliation.json", result)
    report = ["# Real native filter cost: bounded diagnostic and one fix", "",
        "**Filtered admission and degree observation are not yet cheap.** A real all-matching nonempty "
        "numeric predicate was measured, not a Boolean shim. The one result-bound fix removes redundant "
        "posting qualification but does not close the ordinary cost gap. No curve stage was resumed.",
        "", "| version | case | final Recall@10 | ordinary ms | QPS |",
        "|---|---|---:|---:|---:|"]
    for label, table in tables.items():
        for case, row in table.items():
            report.append(f'| {label} | {case} | {row["recall_at_10"]:.4f} | {row["ordinary_ms"]:.6f} | {row["qps"]:.2f} |')
    report += [
        "", "A: native empty-predicate H1. B: real filtered-result/traversal plus existing own admission, "
        "with no ratio/injection observer. C: identical admission plus unchanged ratio supplier. "
        "Two ordinary repetitions A/B/C then C/A/B,1000 warmup+1000 measured, native NProbe=[24], "
        "one thread/NUMA2/O_DIRECT/page15; MaxCheck2048, HierarchyMaxCheck512, ratio0.666666, retention0.5/min16.",
        "", "## Coverage and semantics", "",
        "Actual NUM2 metadata and ColumnTypes identify numeric column1 and encoded maximum4294966124. "
        "Native DNFPredicate::Matches covers all1,000,000 existing dataset records; native "
        "LimitedTagSupport::Load plus canonical VID mapping verifies all160,091 persisted own attributes "
        "are identical to the dataset attributes and match the same predicate. Unfilter truth is valid "
        "only after this proof. The actual native wrapper receives the nonempty numeric DNF3 buffer. "
        "Every B/C captured and ordinary query reports exact/numeric predicate true, filtered admission "
        "true and default admission false. This is not the empty-predicate optimization.",
        "B/C have exact selected heads/final IDs/distances and deterministic navigation/SSD work. "
        "C has e=d for every observed row; helpers, parent/child distances, signatures and CSR scans are zero. "
        "B has no installed observer, degree frames or degree-eligibility calls.",
        f'A/B differ on{len(changes["reference_A_vs_B"]["heads"])} head-query outputs and'
        f'{len(changes["reference_A_vs_B"]["results"])} final-query outputs (lists in reconciliation.json). '
        "B/C keep posting-only head selection and all-evaluated own admission; numeric nonempty requests "
        "also install the existing O-tail coarse/quantized posting filter. Data coverage does not make "
        "these admission semantics identical to A. A-B is therefore NOT pure predicate evaluation time.",
        "", "## Measured cost, not nested timer attribution", "",
        f'Reference B-A={deltas["reference"]["B_minus_A_ms_not_pure_predicate"]:.6f}ms; '
        f'C-B={deltas["reference"]["C_minus_B_ms_observer"]:.6f}ms. '
        f'After the bound fix B-A={deltas["bound_fix"]["B_minus_A_ms_not_pure_predicate"]:.6f}ms; '
        f'C-B={deltas["bound_fix"]["C_minus_B_ms_observer"]:.6f}ms. '
        "The latter is the matched observer cost, including startup-anchor/row bookkeeping, not just a "
        "single ratio division. Observer qualification adds3941.914 traversal/posting calls per query.",
        "Reference B/C have4019.276/7961.190 posting calls,2140.687/6082.601 traversal calls, "
        "1952.801 own notifications and31.818/32.848 exact-head predicate calls per query. "
        "C visits168.476 degree rows and3941.914 physical members. These are actual untimed capture "
        "counts, not ordinary clock times. The exact-head counter covers the native head-attribute "
        "predicate helper, not every SSD predicate operation; native SSD work is recorded separately.",
        "Runtime chrono interposition observes exactly eight steady-clock calls, all coarse Run boundaries; "
        "no fine query clocks. Separately sampled ordinary blocks use1ms CPU sampling only between "
        "verified timer calls6/7. B:1108 total PCs/827 executable PCs,173 CheckValidPosting and107 native "
        "wrapper predicate-thunk samples. C:1283/1022 PCs,234 CheckValidPosting,123 wrapper predicate-thunk, "
        "64 observer and18 find_if samples. These locate posting-validity/metadata and observer work; "
        "they are NOT precise milliseconds or complete wall-time attribution. Most source lines were "
        "unavailable because the inherited native CMake Release recipe overrides release debug flags.",
        "", "## Single surgical fix and its limit", "",
        "A noncollapsed scored candidate strictly beyond the CURRENT native result worst distance cannot "
        "enter that posting-result heap. The fix preserves result dedup and own notification first, then "
        "skips unnecessary posting-result qualification. It does not suppress scoring/traversal, skip "
        "own-only results, change ties, add a radius/budget or truncate rows. Collapsed callers opt out: "
        "their CheckDup return value controls alias enumeration, so globally returning false early would "
        "be incorrect. Native tests verify own notifications survive bound rejection, plus existing "
        "collapsed/tie/deletion/MaxCheck/signature/ratio/full-row behavior.",
        f'Both B and C avoid{savings["B"]["posting_calls"]:.3f} posting checks/query. '
        f'B saves{savings["B"]["ms"]:.6f}ms and C saves{savings["C"]["ms"]:.6f}ms in the two fresh paired sets. '
        "All three cases retain their reference outputs/native work; all four small filtered fixtures "
        "retain exact complete native payloads apart from the new diagnostic call counts.",
        "This is a modest improvement, not a resolution. Degree observation still performs two passes "
        "and repeats qualification; traversal/result APIs still lose reusable eligibility information. "
        "The graph collapsed-marker access cannot simply be skipped using the posting bound while "
        "own/alias acceptance remains possible. No second optimization or blind variant was attempted.",
        "", "## Persistence and stop", "",
        "30 successful native protocol processes/captures are reconciled, including12 ordinary comparison "
        "processes. Each has one actual LoadAll/owner preparation and physical H1/H3 component load. "
        "Clock/sample runs are labeled and never used as ordinary QPS. Parent accepted source/binary and "
        "the frozen curve-stage summary rehash unchanged. The new bound fix is a separate runtime/data "
        "version and is not promoted into existing curves. No production, generic benchmark/parser, "
        "R or GettingStart files were changed. The bounded task stops here.",
        "", f'Reference summary SHA-256: `{result["reference_summary_sha256"]}`',
        f'Fixed summary SHA-256: `{result["fixed_summary_sha256"]}`']
    (fixed / "report.md").write_text("\n".join(report) + "\n")
    for root in (reference, fixed):
        write(root / "status.json", {"state": "completed_bounded_real_filter_cost",
            "overall_filtered_cost_solved": False, "additional_curve_stages_allowed": False, "resume_allowed": False})
    print(json.dumps({"deltas": deltas, "savings": savings, "output_differences": {
        k: {"heads": len(v["heads"]), "results": len(v["results"])} for k, v in changes.items()},
        "reference_summary_sha256": result["reference_summary_sha256"],
        "fixed_summary_sha256": result["fixed_summary_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
