#!/usr/bin/env python3
"""Paired full-query timing on the frozen same-posting runtime, without head dumps."""
import argparse
import json
from pathlib import Path
import shutil
import statistics

from official_benchmark_config import identity, reject_environment_overrides, require, write_json
from run_same_posting_head import prepare_view, source_inventory
from run_spann_memory_diagnostics import parse_phase_line
from run_vanilla_spann_build import read_ini
from run_vanilla_spann_comparison import h3_core_work, h3_records, run_native


def controls(config, plan):
    result = {}
    names = [case.strip() for case in plan["Cases"].split(",")]
    require(all(names) and len(set(names)) == len(names), "Empty or duplicate full-query cases")
    common = None
    for case in names:
        paths = [config.parent / f"{case}_{mode}.ini" for mode in ("plain", "profile")]
        paired = [read_ini(p) for p in paths]
        require(all(p.sections() == ["SearchSSDIndex"] for p in paired),
                "Full-query controls require one native search section")
        native = [p["SearchSSDIndex"] for p in paired]
        plain, profile = [dict(n) for n in native]
        require(plain.pop("logphasetime", None) == "false" and
                profile.pop("logphasetime", None) == "true"
                and plain == profile, "Only phase timing may change between paired runs")
        require(plain["logpathstats"] == "false" and int(plain["dumpheads"]) == 0,
                "Path/head logging must not enter the measured navigation stage")
        require(native[0].getint("NumberOfThreads") == 1 and
                native[0].getint("SearchPostingPageLimit") == 15 and
                native[0]["HeadNavigationMode"] in {"H1Only", "H2Only"},
                "Unexpected native full-query controls")
        shared = {k: v for k, v in plain.items()
                  if k not in {"headnavigationmode", "internalresultnum"}}
        require(common is None or shared == common,
                "Only navigation mode and nprobe may differ across full-query cases")
        common = shared
        result[case] = (paths, native[0])
    require(result, "No full-query cases")
    return result


def validate_phase_balance(phase):
    # Each native timer is rounded independently to 0.001 ms.
    tolerance = 0.002001
    require(abs(phase["total"] - phase["bkt"] - phase["pq"] -
                phase["graphOther"] - phase["post"]) <= tolerance,
            "Navigation/post timing does not close")
    require(abs(phase["post"] - phase["io"] - phase["scan"] -
                phase["postOther"]) <= tolerance, "Posting phase does not close")


def run(config):
    reject_environment_overrides()
    config = config.resolve(strict=True)
    plan = read_ini(config)["Experiment"]
    cases = controls(config, plan)
    index = prepare_view(plan, config)
    binary = identity(Path(plan["Binary"]), True)
    require(binary["sha256"] == plan["BinarySHA256"], "Frozen executable changed")
    count, warmup, repeats = [plan.getint(k) for k in ("QueryCount", "Warmup", "Repeats")]
    require(count == warmup == 1000 and repeats == 5 and plan.getint("MeasureOffset") == 0,
            "Require the fixed five-repeat first1000 protocol")
    source = Path(plan["SourceIndex"])
    flat = Path(plan["FlatHeadIndex"])
    before, graph_before = source_inventory(source), source_inventory(flat)
    output = Path(plan["OutputDirectory"])
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "config"
    snapshot.mkdir()
    for p in [config, config.parent / plan["IndexLoader"],
              *[p for paths, _ in cases.values() for p in paths]]:
        shutil.copy2(p, snapshot / p.name)
    shutil.copy2(Path(__file__), output / Path(__file__).name)
    references = []
    for path in plan["ReferenceRuns"].split(","):
        references.extend(json.loads(Path(path).read_text()))
    write_json(output / "provenance.json", {
        "source_inventory": before, "flat_graph_inventory": graph_before, "binary": binary,
        "config": identity(config, True), "queries": identity(Path(plan["Queries"]), True),
        "truth": identity(Path(plan["Truth"]), True),
        "scope": "Same binary/H1/posting file; phase flags only; no head or path dumps.",
    })
    write_json(output / "status.json", {"state": "running"})
    runs, work = [], {}

    def command(search, query_count, warmup_count):
        native = read_ini(search)["SearchSSDIndex"]
        return ["numactl", f"--cpunodebind={plan.getint('CPUNode')}",
                f"--membind={plan.getint('MemoryNode')}", plan["Binary"],
                "--index", str(index), "--queries", plan["Queries"], "--truth", plan["Truth"],
                "--search-sweep-ini", str(search), "--value-type", plan["ValueType"],
                "--topk", str(native.getint("ResultNum")), "--warmup", str(warmup_count),
                "--measure-offset", "0", "--max-queries", str(query_count)]

    try:
        for case, (paths, native) in cases.items():
            logs = run_native(command(snapshot / paths[0].name, 1, 0), output,
                              f"preflight_{case}", plan.getfloat("SampleSeconds"), True)
            h3_records(logs, 1, 0, native.getint("InternalResultNum"), False)
        names = list(cases)
        for repeat in range(repeats):
            offset = repeat % len(names)
            for case in names[offset:] + names[:offset]:
                paths, native = cases[case]
                for profile in ((False, True) if repeat % 2 == 0 else (True, False)):
                    search = snapshot / paths[int(profile)].name
                    name = f"{case}_r{repeat+1}_{'profile' if profile else 'plain'}"
                    logs = run_native(command(search, count, warmup), output, name,
                                      plan.getfloat("SampleSeconds"), True)
                    row, phase = h3_records(logs, count, warmup, native.getint("InternalResultNum"), profile)
                    current = h3_core_work(row)
                    if case in work:
                        require(current == work[case], "Profiling or repetition changed recall/work")
                    else:
                        work[case] = current
                    previous = [r["native"] for r in references if r["case"] == case]
                    require(previous and all(h3_core_work(r) == current for r in previous),
                            "Native work differs from the original full-QPS comparison")
                    for path in logs:
                        with path.open() as stream:
                            for line in stream:
                                require("DUMPHEADS" not in line and "HEADDUMP:" not in line,
                                        "Head dump contaminated timing")
                                if "PhaseTime:" in line:
                                    validate_phase_balance(parse_phase_line(
                                        line.split("PhaseTime:", 1)[1]))
                    if phase:
                        validate_phase_balance(phase)
                    runs.append({"case": case, "repeat": repeat+1, "profile": profile,
                                 "native": row, "phases": phase})
                    write_json(output / "runs.json", runs)
                    print(f"{name}: recall={row['recall']:.5f} ms={row['mean_latency_ms']:.6f}"
                          + (f" nav={phase['bkt']+phase['pq']+phase['graphOther']:.6f}"
                             f" io={phase['io']:.6f} scan={phase['scan']:.6f}" if phase else ""),
                          flush=True)
        require(source_inventory(source) == before and source_inventory(flat) == graph_before,
                "Input index changed")
        require(identity(Path(plan["Binary"]), True) == binary, "Binary changed")
        summaries = []
        for case in cases:
            ordinary = [r["native"] for r in runs if r["case"] == case and not r["profile"]]
            profiled = [r for r in runs if r["case"] == case and r["profile"]]
            phases = {k: statistics.mean(r["phases"][k] for r in profiled)
                      for k in profiled[0]["phases"]}
            nav = phases["bkt"] + phases["pq"] + phases["graphOther"]
            outer = statistics.mean(r["native"]["mean_latency_ms"] for r in profiled)
            summaries.append({
                "case": case, "recall": ordinary[0]["recall"],
                "ordinary_ms": statistics.mean(r["mean_latency_ms"] for r in ordinary),
                "ordinary_ms_runs": [r["mean_latency_ms"] for r in ordinary],
                "ordinary_qps_median": statistics.median(r["qps"] for r in ordinary),
                "profile_outer_ms": outer, "profile_core_ms": phases["total"],
                "navigation_ms": nav, "posting_retrieval_non_scan_ms": phases["io"],
                "posting_scan_ms": phases["scan"], "posting_other_ms": phases["postOther"],
                "outside_core_ms": outer - phases["total"], "phases": phases,
                "native_work": work[case],
            })
        write_json(output / "summary.json", summaries)
        write_json(output / "status.json", {"state": "completed", "runs": len(runs)})
    except (OSError, ValueError) as error:
        write_json(output / "status.json", {"state": "failed", "error": str(error)})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    run(parser.parse_args().config)
