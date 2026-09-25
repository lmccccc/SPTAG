"""Reconcile the bounded native-reuse result without launching more searches."""
import collections
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("native_reuse_report", HERE / "run.py")
phase = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase)


def compare(a, b):
    changed_heads, changed_results = [], []
    with gzip.open(a, "rt") as left, gzip.open(b, "rt") as right:
        l, r = [json.loads(s) for s in left], [json.loads(s) for s in right]
    assert len(l) == len(r)
    for x, y in zip(l, r):
        if x["heads"] != y["heads"]: changed_heads.append(x["query"])
        if x["results"] != y["results"]: changed_results.append(x["query"])
    return {"queries": len(l), "head_changed_queries": changed_heads,
            "result_changed_queries": changed_results}


def main():
    run = phase.Experiment()
    root, tool = run.root, run.toolchain
    fixtures = json.loads((root / "fixtures.json").read_text())
    ordinary = json.loads((root / "runs.json").read_text())
    summary = json.loads((root / "summary.json").read_text())
    assert len(fixtures) == 8 and len(ordinary) == 6
    evidence, captures, frames = [], 0, 0
    for record in fixtures + ordinary:
        directory = root / record["directory"]
        assert json.loads((directory / "native.exit.json").read_text())["returncode"] == 0
        assert json.loads((directory / "native.io.json").read_text())["direct_io"]
        text = "\n".join((directory / f"native.{k}.log").read_text() for k in ("stdout", "stderr"))
        assert text.splitlines().count("NATIVE_INDEX_LOAD calls=1") == 1
        assert text.count("NATIVE_SUPPLIER_OWNER_LOAD count=1") == 1
        components = {label: text.count("Load " + label + " Finish!") for label in
            ("Vector (160091,128)", "BKT (1,160093)", "RNG (160091,32)",
             "Vector (4098,128)", "BKT (1,4100)", "RNG (4098,32)")}
        assert set(components.values()) == {1}
        evidence.append({"directory": record["directory"], "native_loadall_entries": 1,
                         "owner_metadata_loads": 1, "physical_component_loads": components})
        for point in record["points"]:
            h, n = hashlib.sha256(), 0
            with gzip.open(directory / (point["native"]["capture_file"] + ".gz"), "rb") as stream:
                for line in stream:
                    h.update(line); n += 1
                    row = json.loads(line)
                    assert row["native_raw_distance_function"] == 1
                    assert len(set(row["own_ids"])) == len(row["own_ids"]) <= 10
                    assert all(run.masks[record["scenario"]][i] for i in row["own_ids"])
                    frames += len(row["degree_frames"])
            assert n == point["validation"]["queries"]
            assert h.hexdigest() == point["validation"]["raw_sha256"]
            captures += 1
    assert captures == 22
    parent = root.parent / "h1_ratio_phase1_20260917"
    h1 = root / "plain_r1_h1/queries.jsonl.nprobe_24.gz"
    supplier = root / "plain_r1_supplier/queries.jsonl.nprobe_24.gz"
    differences = {
        "new_supplier_vs_new_h1": compare(supplier, h1),
        "new_h1_vs_authenticated_h1": compare(h1, parent / "plain_r1_h1/queries.jsonl.nprobe_24.gz"),
        "new_supplier_vs_phase1": compare(supplier, parent / "plain_r1_supplier/queries.jsonl.nprobe_24.gz"),
    }
    assert not differences["new_h1_vs_authenticated_h1"]["head_changed_queries"]
    assert not differences["new_h1_vs_authenticated_h1"]["result_changed_queries"]
    assert not differences["new_supplier_vs_phase1"]["head_changed_queries"]
    assert not differences["new_supplier_vs_phase1"]["result_changed_queries"]
    assert len(differences["new_supplier_vs_new_h1"]["head_changed_queries"]) == 17
    assert differences["new_supplier_vs_new_h1"]["result_changed_queries"] == [915]
    filtered = []
    for record in fixtures:
        if record["scenario"] == "unfilter":
            for point in record["points"]:
                probe = point["native"]["nprobe"]
                order = record["directory"].rsplit("_", 1)[1]
                old = parent / f'fixture_unfilter_{record["case"]}_{order}/queries.jsonl.nprobe_{probe}.gz'
                differences[record["directory"] + "_" + str(probe)] = compare(
                    root / record["directory"] / (point["native"]["capture_file"] + ".gz"), old)
            continue
        point = record["points"][0]
        delta = compare(root / record["directory"] / (point["native"]["capture_file"] + ".gz"),
            parent / f'fixture_{record["scenario"]}_supplier/queries.jsonl.nprobe_24.gz')
        filtered.append({"scenario": record["scenario"], "queries": 8,
                         "recall_at_10": point["validation"]["recall_at_10"],
                         "work": point["validation"]["work"], "phase1_difference": delta})
    phase.write(root / "result_differences.json", differences)
    phase.write(root / "filtered_fixture_work.json", filtered)
    phases = {}
    for case in ("h1", "supplier"):
        logs = [root / f"profile_{case}/native.{k}.log" for k in ("stdout", "stderr")]
        _, phases[case] = phase.native.h3_records(logs, 1000, 2000, 24, True)
        phase.native.validate_phase_balance(phases[case])
    phase.write(root / "native_phases.json", phases)
    inputs = json.loads((parent / "input_hashes.json").read_text())
    for item in inputs:
        assert phase.fp(Path(item["path"]))["sha256"] == item["sha256"], item["path"]
    phase.write(root / "input_hashes.json", inputs)
    proof = json.loads((tool / "native_reuse_provenance.json").read_text())
    for name, digest in proof["after"].items():
        assert phase.fp(tool / "source" / name)["sha256"] == digest
    for name, digest in proof["parent_source_hashes"].items():
        assert phase.fp(Path(proof["parent"]) / "source" / name)["sha256"] == digest
    for name in ("Supplier.h", "NativeAdapter.h", "ShortcutHooks.h"):
        assert not (tool / "source/AnnService" / name).exists()
    symbols = subprocess.check_output(["nm", "-C", "--defined-only", str(tool / "source/Release/spannaclbench")],
                                      text=True)
    forbidden = ("H1Supplier::", "BenchmarkRestore", "BenchmarkDistance", "Engine::Score")
    assert all(token not in symbols for token in forbidden)
    inspected = ("NativeSupplier.h", "BKTIndex.cpp", "BKTree.h", "SPANNIndex.cpp")
    for name in inspected:
        text = (HERE / name).read_text()
        assert all(token not in text for token in forbidden)
    phase.write(root / "native_reuse_proof.json", {
        "forbidden_defined_symbols_absent": list(forbidden),
        "original_native_distance_function_target_all_queries": True,
        "ordinary_and_capture_target_checked_by_native_benchmark": True,
        "shadow_h1_result_heaps": 0, "h1_result_heap": "original SPANN p_queryResults passed to native dispatcher",
        "candidate_processing": "same lifted original expandEdges lambda, same native visited/frontier/bound",
        "retained_own_point_heap": "original SPANN admitHeadPoint / hierarchyState helper",
        "owner_metadata_bytes": (160091 + 25607) * 8 * 4,
        "owner_metadata_built_during_native_load_not_query": True,
        "diagnostic_ledgers": "untimed capture/profile only; helper counters remain on actual helper branch",
        "no_raw_vectors_graph_postings_or_hierarchy_rebuilt": True,
        "binary": phase.fp(tool / "source/Release/spannaclbench"),
    })
    # Keep the initial output before adding derived presentation fields.
    shutil.copy2(root / "summary.json", root / "summary.native_initial.json")
    for row in summary:
        row["qps_min"] = 1000 / max(row["ordinary_ms_runs"])
        row["qps_max"] = 1000 / min(row["ordinary_ms_runs"])
        row["phases"] = phases[row["case"]]
        row["nprobe_ini_api"] = "SearchSweep.NProbe"
        row["ordinary_distance_interception"] = False
        row["shadow_h1_result_heaps"] = 0
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    by_case = {r["case"]: r for r in summary}
    ratio = by_case["supplier"]["ordinary_ms"] / by_case["h1"]["ordinary_ms"]
    report = [
        "# Native H1 reuse: bounded milestone complete", "",
        "The old Engine/NativeAdapter search scaffold is removed from the isolated runtime. "
        "Native H1 traversal, primitive, visited state, frontier/result queues and stopping are reused. "
        "The remaining latency gap is not claimed solved; no full sweep was launched.",
        "", "| unfilter nprobe24 | Recall@10 | Ordinary ms | QPS | Separate native-profile navigation ms |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in summary:
        report.append(f'| {row["case"]} | {row["recall_at_10"]:.4f} | {row["ordinary_ms"]:.6f} | '
                      f'{row["qps"]:.3f} | {phases[row["case"]]["graphOther"]:.6f} |')
    report += [
        "", "Fresh same-runtime H1->supplier / supplier->H1 comparisons;1000 warmup+1000 measured, "
        "one query thread, NUMA CPU/memory2, O_DIRECT/page15, native single-load NProbe=[24]. "
        "No old timing is spliced into these rows.",
        f'Supplier still costs {ratio:.3f}x H1 latency. Separate native phase profiles include profiling and '
        "diagnostic overhead and are not ordinary component timings. Native io is retrieval minus scan, "
        "not physical SSD delay.",
        "", "## Removed hot mechanisms", "",
        "No BenchmarkRestore/global distance interception; no Engine::Score or H1 score/qualification epochs; "
        "no sample-pointer identity arithmetic/division; no parallel spatial heap plus custom posting head heap; "
        "no ordinary trace hashing or fine timers; no Phase2 intrinsic cache/version guards. "
        "The binary has no defined old adapter/interception symbols. Actual primitive target checks pass "
        "in every ordinary and captured query, not just source inspection.",
        "", "## Necessary retained extensions", "",
        "A thin API exposes the original private native dispatcher's separate posting-result and traversal "
        "predicates. Known-distance notifications at native candidate admission reuse the original SPANN "
        "own-point helper, including own-only heads. The old public Boolean-only filter cannot provide that "
        "distance and cannot separate own-only eligibility from usable postings.",
        "A workspace-local adjacency hook borrows native rows. The original expandEdges lambda was lifted, "
        "not duplicated, so startup and supplied IDs reuse native candidate scoring, visited checks, heap "
        "acceptance and stopping. Predicate-first terminal handling retains structural/collapsed routing "
        "exceptions. Per-entry anchor observation is not a distance wrapper.",
        "PostingSupplier contains only the necessary signature-first synchronous owner/CSR policy and upper "
        "ranking reuse. No upper priority queue or H1 search state is created. Fixed eight-owner metadata "
        "uses5942336 bytes, prepared once during native load (also present in the matched H1 process), "
        "not by a whole-layer query scan. No raw vectors, graph, posting bytes or hierarchy are rebuilt.",
        "", "## Verified behavior", "",
        "d is the genuine distinct ordinary degree; e includes visited eligible neighbors. d<16 never "
        "supplements; otherwise e/d<0.5 triggers and ceil(0.5*d) is the relative target. Selected rows "
        "finish completely; native MaxCheck is checked before the next row. There is no added cap.",
        "All unfilter supplier calls, parent/child distances and CSR member reads are exactly zero. "
        "H1 uses2231.960 actual distances/query; supplier uses2226.294. Supplier native heap "
        "offers/accepts/rejects are1868.859/333.993/1534.866 per query; rejection does not erase prior "
        "qualification/scoring cost. Remaining work includes native result/traversal admission and the "
        "necessary ratio observation/injection interface; no exact attribution to those pieces is claimed.",
        "The1000-query native-reuse H1 output exactly matches authenticated H1. New supplier heads and final "
        "IDs/distances exactly match Phase1 on unfilter24 despite removal of the shadow heap and Score "
        "framework. Relative to H1, the same17 head-query differences and one final-result difference(q915) "
        "remain: native own-only/posting admission and native boundary behavior, not result rewriting.",
        "Eight native small fixtures cover forward/reverse16/24/384 and four filtered predicates. "
        "Final IDs, predicate validity, complete/unique rows, ratio triggers and budget boundaries are audited. "
        "Native unit fixtures cover primitive/single-heap parity, own-only admission outside the posting heap, "
        "predicate-before-terminal-distance, exact ratio boundaries, odd/short/empty rows, duplicates/self/"
        "invalid IDs/sentinels, signature rejection and complete rows beyond deficit/native boundary.",
        "", "| Filtered fixture (8 queries only) | Recall@10 | Helper calls/query | CSR reads/query | Parent distances/query | Changed final queries vs Phase1 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in filtered:
        w = row["work"]
        report.append(f'| {row["scenario"]} | {row["recall_at_10"]:.4f} | {w["supplier_calls"]:.3f} | '
                      f'{w["supplier_members"]:.3f} | {w["supplier_parent_distances"]:.3f} | '
                      f'{len(row["phase1_difference"]["result_changed_queries"])} |')
    report += [
        "", "Filtered work is not promised identical to the old scaffold: upper ranking no longer reuses a "
        "global H1 Score cache or admits parent representatives as own points. These are small correctness/"
        "work fixtures, not filtered QPS curves.",
        f"14 native protocol loads and22 native captures are reconciled; {frames} degree frames checked. "
        "Each process has one actual LoadAll entry, one owner-metadata preparation and one load of each "
        "H1/H3 vector/BKT/RNG component. The historical top graph is backing only, not an upper frontier.",
        "Original inputs and parent frozen sources rehash unchanged. Phase1/Phase2 results remain intact. "
        "The initial premeasurement build is preserved separately; moving owner preparation to index loading "
        "was completed before any native fixture/timing output. This phase stops here.",
    ]
    (root / "report.md").write_text("\n".join(report) + "\n")
    phase.write(root / "independent_reconciliation.json", {
        "native_protocol_loads": 14, "capture_files_rehashed": 22, "actual_load_evidence": evidence,
        "degree_frames_checked": frames, "inputs_rehashed": len(inputs),
        "parent_frozen_sources_unchanged": True, "raw_native_primitive_verified": True,
        "unfilter_zero_supplier_work": True, "ordinary_capture_profile_repeat_parity": True,
        "same_h1_and_phase1_supplier_outputs": True, "remaining_h1_latency_ratio": ratio,
        "full_sweep_launched": False, "performance_gap_solved": False,
        "summary_sha256": phase.fp(root / "summary.json")["sha256"],
        "report_sha256": phase.fp(root / "report.md")["sha256"],
    })
    final = root / "final_provenance"
    shutil.copytree(HERE, final / "experiment", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("tests-final.log", "tests-admission.log"):
        shutil.copy2(tool / name, final / name)
    phase.write(final / "manifest.json", {
        "experiment_files": [phase.fp(p) for p in sorted((final / "experiment").rglob("*")) if p.is_file()],
        "native_binary": phase.fp(tool / "source/Release/spannaclbench"),
        "native_test_binary": phase.fp(tool / "tests/nativereusetests"),
        "source_archive": phase.fp(root / "snapshot/source.tar.gz"),
    })
    phase.write(root / "status.json", {"state": "completed_bounded_native_reuse",
        "full_sweep_allowed": False, "performance_gap_solved": False})
    print("\n".join(report))


if __name__ == "__main__":
    main()
