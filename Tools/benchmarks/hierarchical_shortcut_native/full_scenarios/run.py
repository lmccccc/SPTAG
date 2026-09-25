#!/usr/bin/env python3
"""Joint native unfiltered/categorical/numeric/DNF comparison, without workload weights."""
import argparse
import json
from pathlib import Path
import re
import shutil
import statistics
import sys
import tarfile

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from official_benchmark_config import reject_environment_overrides, require, write_json
from run_full_phase_ab import validate_phase_balance
from run_vanilla_spann_build import read_ini
from run_vanilla_spann_comparison import h3_core_work, h3_records, run_native
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "full"))
from run import fingerprint, rows


def predicate_mask(predicate, attrs):
    if "categorical_eq" in predicate:
        col, value = predicate["categorical_eq"]
        return attrs[:, col] == value
    if "numeric_le" in predicate:
        col, value = predicate["numeric_le"]
        return attrs[:, col] <= value
    if "or" in predicate:
        return np.logical_or.reduce([predicate_mask(p, attrs) for p in predicate["or"]])
    if "and" in predicate:
        return np.logical_and.reduce([predicate_mask(p, attrs) for p in predicate["and"]])
    raise ValueError("Unknown original workload predicate")


def query_quality(data, truth, mask):
    hits, counts = [], []
    for q, row in enumerate(data):
        valid = [int(p[0]) for p in row["results"] if p[0] >= 0]
        require(len(valid) == len(set(valid)), "Duplicate native final IDs")
        require(all(p < len(mask) and mask[p] for p in valid), "Native exact filtering violation")
        hits.append(len(set(valid) & set(truth[q, :10])))
        counts.append(len(valid))
    return {"recall_at_10": sum(hits) / (len(data) * 10),
            "underfilled_queries": sum(n < 10 for n in counts),
            "empty_queries": sum(n == 0 for n in counts),
            "mean_returned": statistics.mean(counts), "filter_violations": 0}


def run(config):
    reject_environment_overrides()
    config = config.resolve(strict=True)
    ini = read_ini(config)
    plan = ini["Experiment"]
    count, warmup, repeats = [plan.getint(k) for k in ("QueryCount", "Warmup", "Repeats")]
    require(count == warmup == 1000 and repeats == 3, "Unexpected fixed scenario protocol")
    workloads = json.loads(Path(plan["Workloads"]).read_text())
    attrs = np.load(workloads["attributes"], mmap_mode="r")
    require(attrs.shape == (1000000, 2), "Wrong original attribute schema")
    scenarios, cases = plan["Scenarios"].split(","), plan["Cases"].split(",")
    masks, truth, available, predicate_args = {}, {}, {}, {}
    for scenario in scenarios:
        manifest = workloads["truth"][scenario]
        path = Path(manifest["ids"])
        require(fingerprint(path)["sha256"] == manifest["sha256"], "Original truth changed")
        truth[scenario] = np.load(path, mmap_mode="r")
        require(truth[scenario].shape[0] >= count and truth[scenario].shape[1] >= 10,
                "Insufficient native scenario truth")
        masks[scenario] = (np.ones(len(attrs), dtype=bool) if scenario == "unfilter" else
                           predicate_mask(workloads["predicates"][scenario], attrs))
        require(int(masks[scenario].sum()) == manifest["candidate_count"],
                "Original predicate/candidate count mismatch")
        predicate_args[scenario] = []
        if scenario in workloads["flat_query_tags"]:
            query_path = workloads["flat_query_tags"][scenario]
            tag = workloads["predicates"][scenario]["categorical_eq"][1]
            tags = np.load(query_path, mmap_mode="r")
            require(tags.shape == (1000, 1) and np.all(tags == tag), "Wrong native tag rows")
            predicate_args[scenario] = ["--query-tags", query_path, "--tag-column", "0"]
        elif scenario in ("numeric", "mixed_dnf"):
            query_path = workloads["query_dnf"]["numeric" if scenario == "numeric" else "mixed"]
            dnf = np.load(query_path, mmap_mode="r")
            require(dnf.shape[0] == count and np.all(dnf == dnf[0]), "Unexpected DNF query cohort")
            predicate_args[scenario] = ["--query-dnf", query_path]
        available[scenario] = {"predicate": workloads["predicates"].get(scenario, "unfiltered"),
                               "candidates": manifest["candidate_count"],
                               "selectivity": manifest["selectivity"],
                               "truth": fingerprint(path), "native_arguments": predicate_args[scenario]}

    output = Path(plan["OutputDirectory"])
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "snapshot"
    snapshot.mkdir()
    controls = snapshot / "controls"
    shutil.copytree(config.parent, controls, ignore=shutil.ignore_patterns("__pycache__"))
    search_paths, common = {}, None
    for case in cases:
        pair = []
        for mode in ("plain", "profile"):
            path = (config.parent / ini[f"Case.{case}"][mode]).resolve(strict=True)
            search_paths[case, mode] = controls / f"{case}_{mode}.ini"
            shutil.copy2(path, search_paths[case, mode])
            native = dict(read_ini(path)["SearchSSDIndex"])
            require(native["dumpheads"] == "0" and native["logpathstats"] == "false",
                    "Query logging contaminates main timing")
            require(native["logphasetime"] == native["shortcutprofile"] ==
                    ("true" if mode == "profile" else "false"), "Mismatched profile flags")
            pair.append({k: v for k, v in native.items() if k not in ("logphasetime", "shortcutprofile")})
        require(pair[0] == pair[1], "Count-mode changes algorithm controls")
        shared = {k: v for k, v in pair[0].items()
                  if k not in ("shortcutmode", "shortcutcap", "headnavigationmode")}
        require(common is None or shared == common, "Search budgets changed between implementations")
        common = shared
        if case in ("h1", "h3"):
            path = (config.parent / ini[f"Case.{case}"]["FrozenDiagnostic"]).resolve(strict=True)
            search_paths[case, "frozen"] = controls / f"{case}_frozen.ini"
            shutil.copy2(path, search_paths[case, "frozen"])
    shutil.copy2(plan["Binary"], snapshot / "spannaclbench")
    toolchain = Path(plan["Toolchain"])
    for name in ("authentication.json", "integration.json", "scenario_integration.json", "configure.log", "build.log"):
        shutil.copy2(toolchain / name, snapshot / name)
    with tarfile.open(snapshot / "source.tar.gz", "w:gz") as archive:
        archive.add(toolchain / "source", arcname="source",
                    filter=lambda p: None if "/Release/" in p.name else p)
    prior = json.loads(Path(plan["PriorProvenance"]).read_text())["protected"]
    protected = {Path(row["path"]) for row in prior}
    require(all(fingerprint(row["path"]) == row for row in prior), "Completed unfiltered inputs changed")
    protected.update(Path(plan["PriorResults"]).rglob("*"))
    protected.update(Path(plan["PrototypeResults"]).rglob("*"))
    protected.update(Path(plan["Workloads"]).parent.glob("*.npy"))
    protected.update([Path(plan["Workloads"]), Path(workloads["attributes"]),
                      Path(plan["Binary"]), Path(plan["FrozenBinary"])])
    protected = sorted(p for p in protected if p.is_file())
    before = [fingerprint(p) for p in protected]
    write_json(output / "provenance.json", {"protected": before, "available_scenarios": available,
        "acceptance": "Unfilter vs H1; each filtered scenario vs H3, with recall and underfill shown. No weighted mix.",
        "native_binary": fingerprint(snapshot / "spannaclbench")})
    write_json(output / "status.json", {"state": "running"})
    all_runs, baseline, payloads, quality = [], {}, {}, {}

    def execute(scenario, case, mode, repeat, frozen=False):
        name = f"{scenario}_{case}_{mode}_r{repeat}"
        directory = output / name
        directory.mkdir()
        search = search_paths[case, "frozen" if frozen else mode]
        command = ["numactl", f"--cpunodebind={plan.getint('CPUNode')}",
                   f"--membind={plan.getint('MemoryNode')}",
                   plan["FrozenBinary"] if frozen else str(snapshot / "spannaclbench"),
                   "--index", plan["Index"], "--queries", plan["Queries"],
                   "--truth", workloads["truth"][scenario]["ids"],
                   "--search-sweep-ini", str(search), "--value-type", "Float", "--topk", "10",
                   "--warmup", str(warmup), "--measure-offset", "0", "--max-queries", str(count),
                   *predicate_args[scenario]]
        logs = run_native(command, directory, "native", 0.2, True)
        native, phases = h3_records(logs, count, warmup if frozen else warmup + count,
                                   24, frozen or mode == "profile")
        core = h3_core_work(native)
        key = scenario, case
        if key in baseline:
            require(core == baseline[key], f"{name}: native final recall/work changed")
        else:
            baseline[key] = core
        if phases:
            validate_phase_balance(phases)
        if frozen:
            captured = []
            for log in logs:
                for line in log.read_text().splitlines():
                    if "DUMPHEADS q=" in line:
                        captured.append([[int(a), float(b)] for a, b in re.findall(
                            r"(\d+):([0-9.eE+-]+)", line.split(" :", 1)[1])])
            require(len(captured) == count * 2, "Missing frozen selected-head evidence")
            write_json(directory / "heads.json", captured[:count])
            require(captured[:count] == captured[count:], "Frozen head selection is nondeterministic")
        else:
            require(all("DUMPHEADS" not in log.read_text() for log in logs), "Head logs in main timer")
            data = rows(directory / "queries.jsonl")
            require(len(data) == count, "Incomplete native query observations")
            selected = [[[int(p[0]), p[1]] for p in row["heads"] if p[0] >= 0] for row in data]
            require(all(len(h) <= 24 and len({p[0] for p in h}) == len(h) for h in selected),
                    "Invalid selected H1 ordinals")
            if case in ("h1", "h3"):
                reference = json.loads((output / f"{scenario}_{case}_frozen_r0/heads.json").read_text())
                require(selected == reference, "Original H1/H3 head-result path changed")
            q = query_quality(data, truth[scenario], masks[scenario])
            require(abs(q["recall_at_10"] - native["recall"]) < 1e-8, "Native final recall mismatch")
            quality[key] = q
            payload = [{"heads": r["heads"], "results": r["results"],
                        "csr_distances": r["csr_distances"], "csr_assignments": r["csr_assignments"],
                        "graph_checked": r["graph_checked"]} for r in data]
            if key in payloads:
                require(payload == payloads[key], "Count-mode/repetition changed head/CSR/final results")
            payloads[key] = payload
            if mode == "profile":
                cap = read_ini(search)["SearchSSDIndex"].getint("ShortcutCap")
                require(all(r["calls"] > 0 and (not cap or r["calls"] <= cap)
                            and (not r["exhausted"] or r["calls"] == cap)
                            and r["csr_distances"] <= r["calls"] for r in data),
                        "Actual native distance accounting/cap failure")
        row = {"scenario": scenario, "case": case, "mode": mode, "repeat": repeat,
               "native": native, "phases": phases}
        all_runs.append(row)
        write_json(output / "runs.json", all_runs)
        print(f"{name}: recall={native['recall']:.4f} ms={native['mean_latency_ms']:.6f}", flush=True)

    try:
        # Semantic baseline controls finish BEFORE measuring shortcuts for each scenario.
        for scenario in scenarios:
            for case in ("h1", "h3"):
                execute(scenario, case, "frozen", 0, True)
                execute(scenario, case, "plain", 0)
        for repeat in range(1, repeats + 1):
            shift = (repeat - 1) * 2 % len(scenarios)
            scenario_order = scenarios[shift:] + scenarios[:shift]
            for scenario_index, scenario in enumerate(scenario_order):
                rotate = (scenario_index + repeat - 1) % len(cases)
                for case in cases[rotate:] + cases[:rotate]:
                    for mode in (("plain", "profile") if repeat % 2 else ("profile", "plain")):
                        execute(scenario, case, mode, repeat)
        summary = []
        for scenario in scenarios:
            for case in cases:
                selected = [r for r in all_runs if r["scenario"] == scenario and
                            r["case"] == case and r["repeat"] > 0]
                timing = [r["native"]["mean_latency_ms"] for r in selected if r["mode"] == "plain"]
                profiles = [r["phases"] for r in selected if r["mode"] == "profile"]
                phase = {k: statistics.mean(p[k] for p in profiles) for k in profiles[0]}
                data = rows(output / f"{scenario}_{case}_profile_r1/queries.jsonl")
                w = baseline[scenario, case]
                head_calls = statistics.mean(q["calls"] for q in data)
                csr = statistics.mean(q["csr_distances"] for q in data)
                summary.append({"scenario": scenario, "case": case, **quality[scenario, case],
                    "ordinary_ms": statistics.mean(timing), "ordinary_ms_runs": timing,
                    "qps": 1000 / statistics.mean(timing),
                    "selected_h1": statistics.mean(sum(p[0] >= 0 for p in q["heads"]) for q in data),
                    "postings": w["postings_per_query"], "pages": w["posting_page_reads_per_query"],
                    "disk_distances": w["distance_computations_per_query"],
                    "scanned_records": w["scanned_vectors_per_query"],
                    "dedup_skipped": w["dedup_skipped_vectors_per_query"],
                    "head_graph_actual_distances": head_calls - csr, "csr_actual_distances": csr,
                    "head_total_actual_distances": head_calls,
                    "total_actual_distances": head_calls + w["distance_computations_per_query"],
                    "csr_member_entries": statistics.mean(q["csr_assignments"] for q in data),
                    "graph_adjacency_entries": statistics.mean(q["adjacency"] for q in data),
                    "shortcut_distances": statistics.mean(q["shortcut_calls"] for q in data),
                    "graph_native_checked": phase["headScanned"] if case != "h3" else phase["h2Upper"],
                    "phases": phase})
        for row in summary:
            ref = next(r for r in summary if r["scenario"] == row["scenario"] and
                       r["case"] == ("h1" if row["scenario"] == "unfilter" else "h3"))
            row["acceptance_reference"] = ref["case"]
            row["latency_ratio_to_reference"] = row["ordinary_ms"] / ref["ordinary_ms"]
            row["recall_delta_pp_to_reference"] = 100 * (row["recall_at_10"] - ref["recall_at_10"])
        write_json(output / "summary.json", summary)
        # Hybrid and original H1 navigation must be identical across predicates.
        for case in ("h1", "add2000", "rewire2000"):
            reference = rows(output / f"unfilter_{case}_profile_r1/queries.jsonl")
            for scenario in scenarios:
                data = rows(output / f"{scenario}_{case}_profile_r1/queries.jsonl")
                keys = ("heads", "calls", "adjacency", "shortcut_calls", "exhausted")
                require(all(all(a[k] == b[k] for k in keys) for a, b in zip(reference, data)),
                        "Predicate changed H1/hybrid navigation or budget")
        require(before == [fingerprint(p) for p in protected], "Protected input/prior result changed")
        write_json(output / "validation.json", {"original_h1_h3_heads_and_native_work_equal": True,
            "counted_uncounted_final_ids_distances_and_csr_equal": True,
            "h1_and_hybrid_navigation_predicate_invariant": True,
            "independent_final_recall_and_exact_filter_checks": True,
            "protected_files_unchanged": len(before), "scenarios": available})
        write_json(output / "status.json", {"state": "completed", "native_runs": len(all_runs),
            "scenario_count": len(scenarios), "implementation_cases": len(cases),
            "acceptance": "Report each filtered scenario against H3 with recall/underfill; no weighted aggregate."})
    except Exception as error:
        write_json(output / "status.json", {"state": "failed", "error": str(error)})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    run(parser.parse_args().config)
