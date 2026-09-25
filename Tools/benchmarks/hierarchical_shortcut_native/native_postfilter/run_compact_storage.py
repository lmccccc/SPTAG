#!/usr/bin/env python3
"""Paired immutable V8 versus native compact storage; never changes search policy."""
import argparse
import configparser
import json
import os
from pathlib import Path
import statistics
import time

import numpy as np

from run_full import execute, predicate_mask, quality, read_ini, require, sha
from run_postgraph import COUNTERS, GRID, SCENARIOS, save

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[4] / "datasets/sift1m_zipf200_sparse193_numeric"
OUTPUT = DATA / "comparisons/main_compact_storage_20260921"
FROZEN = DATA / "comparisons/main_postgraph_posting_20260921"
VARIANTS = ("original_layout", "compact_layout")
SETTINGS = dict(graph_maxcheck=2048, posting_additional_maxcheck=2048, posting_anchor_count=8)


def prepare():
    require(not (OUTPUT / "registration.json").exists(), "Already registered")
    old = json.loads((FROZEN / "registration.json").read_text())
    binaries = {
        "original_layout": old["binaries"]["Binary"]["path"],
        "compact_layout": str(DATA / "toolchains/main_compact_storage_build/bin/nativeBench"),
        "original_diagnostic": old["binaries"]["DiagnosticBinary"]["path"],
        "compact_diagnostic": str(DATA / "toolchains/main_compact_storage_diagnostics/bin/nativeBench"),
    }
    require(sha(binaries["original_layout"]) == old["binaries"]["Binary"]["sha256"],
            "Frozen original normal binary changed")
    require(sha(binaries["original_diagnostic"]) == old["binaries"]["DiagnosticBinary"]["sha256"],
            "Frozen diagnostic binary changed")
    (OUTPUT / "configs").mkdir(exist_ok=True)
    schedule = []
    for kind in ("functional", "plain", "diagnostic"):
        for repetition in ((1, 2) if kind == "plain" else (1,)):
            for scenario in (SCENARIOS if repetition == 1 else SCENARIOS[::-1]):
                for variant in (VARIANTS if repetition == 1 else VARIANTS[::-1]):
                    native = read_ini(FROZEN / "configs" / f"plain_{scenario}_postgraph_extra_r1.ini")
                    queries = 32 if kind == "functional" else 1000
                    probes = [24] if kind == "functional" else GRID
                    if variant == "compact_layout":
                        native["Benchmark"]["Index"] = str(OUTPUT / "index_streaming")
                    native["Benchmark"]["MaxQueries"] = str(queries)
                    native["Benchmark"]["Warmup"] = str(queries)
                    native["SearchSweep"]["NProbe"] = json.dumps(probes)
                    require(native["SearchSSDIndex"]["MaxCheck"] == "2048" and
                            native["SearchSSDIndex"]["PostingAdditionalMaxCheck"] == "2048" and
                            native["SearchSSDIndex"]["PostingAnchorCount"] == "8" and
                            native["SearchSSDIndex"]["EnablePostingNavigation"].lower() == "true",
                            "Frozen search settings differ")
                    name = f"{kind}_{scenario}_{variant}_r{repetition}"
                    filename = OUTPUT / "configs" / f"{name}.ini"
                    with filename.open("w") as stream:
                        native.write(stream, space_around_delimiters=False)
                    schedule.append(dict(case=name, scenario=scenario, variant=variant,
                        repetition=repetition, kind=kind, queries=queries, probes=probes,
                        config=str(filename), config_sha256=sha(filename)))
    save(OUTPUT / "registration.json", dict(query_count=1000, repetitions=2, warmup=1000,
        nprobe=GRID, scenarios=SCENARIOS, variants=VARIANTS, search_settings=SETTINGS,
        cpu_node=2, memory_node=2, schedule=schedule,
        binaries={name: dict(path=path, sha256=sha(path)) for name, path in binaries.items()},
        counter_columns=COUNTERS, normal_points=144, normal_processes=24,
        diagnostic_points=72, diagnostic_timings_are_not_throughput=True,
        baseline="Frozen original-layout executable and V8 view, not compatibility loading into compact RAM",
        work_budget="Checked leaves, not all distance work; selected full rows may overshoot"))


def run(kind):
    require(not [k for k in os.environ if k.startswith(("SPTAG_", "SPANN_", "OMP_")) or k == "LD_PRELOAD"],
            "Search environment overrides forbidden")
    registration = json.loads((OUTPUT / "registration.json").read_text())
    for binary in registration["binaries"].values():
        require(sha(binary["path"]) == binary["sha256"], "Registered runtime changed")
    if kind == "plain":
        require((OUTPUT / "functional-completion.json").exists(), "Functional parity gate is required")
    if kind == "diagnostic":
        require((OUTPUT / "normal-summary.json").exists(), "Publish normal results before diagnostics")
    old_plan = read_ini(HERE / "postgraph.ini")["Campaign"]
    workload = json.loads(Path(old_plan["Workloads"]).read_text())
    attrs = np.load(workload["attributes"], mmap_mode="r")
    masks = {s: np.ones(len(attrs), dtype=bool) if s == "unfilter" else
             predicate_mask(workload["predicates"][s], attrs) for s in SCENARIOS}
    frozen = json.loads((FROZEN / "plain-results.json").read_text())
    frozen_hashes = {(r["scenario"], r["nprobe"]): r["payload_hashes"]
                     for r in frozen if r["variant"] == "postgraph_extra" and r["repetition"] == 1}
    records = []
    for case in (c for c in registration["schedule"] if c["kind"] == kind):
        require(sha(case["config"]) == case["config_sha256"], "Registered native INI changed")
        folder = OUTPUT / case["case"]
        validated = folder / "validated.json"
        if validated.exists():
            rows = json.loads(validated.read_text())
            for row in rows:
                for filename, expected in row["payload_hashes"].items():
                    require(sha(folder / f"nprobe_{row['nprobe']}" / filename) == expected,
                            "Resumed payload changed")
        else:
            require(not folder.exists(), f"Incomplete process retained for inspection: {folder}")
            key = case["variant"] if kind != "diagnostic" else (
                "original_diagnostic" if case["variant"] == "original_layout" else "compact_diagnostic")
            command = ["numactl", "--cpunodebind=2", "--membind=2",
                       registration["binaries"][key]["path"], case["config"]]
            started = time.monotonic()
            peak = execute(command, folder)
            elapsed = time.monotonic() - started
            log = (folder / "native.log").read_text()
            require("numeric lanes=1" in log and "conservative unknown" not in log,
                    "Missing authenticated numeric signatures")
            rows = [json.loads(line) for line in log.splitlines() if line.startswith('{"mode":')]
            require([r["nprobe"] for r in rows] == case["probes"], "Incomplete probe array")
            for row in rows:
                require(row["queries"] == case["queries"] and
                        row["diagnostic"] == (kind == "diagnostic") and row["mode"] == "posting" and
                        row["max_check"] == 2048 and row["posting_additional_max_check"] == 2048 and
                        row["posting_anchor_count"] == 8, "Native settings/cohort changed")
                row.update(quality(folder / f"nprobe_{row['nprobe']}",
                    workload["truth"][case["scenario"]], masks[case["scenario"]], case["queries"]))
                row.update(case, command=command, peak_process_rss_bytes=peak,
                           process_wall_seconds=elapsed, timing_accepted=kind == "plain")
                if kind != "plain":
                    row[f"{kind}_qps_not_for_throughput"] = row.pop("qps")
                if kind != "functional":
                    require(row["payload_hashes"] == frozen_hashes[(row["scenario"], row["nprobe"])],
                            "Final IDs/distances/SSD work differ from frozen postgraph")
                if kind == "diagnostic":
                    row["graph_payload_hashes"] = {
                        name: sha(folder / f"nprobe_{row['nprobe']}" / name)
                        for name in ("graph_ids.i32", "graph_dist.f32")}
            save(validated, rows)
        records.extend(rows)
        index = {(r["scenario"], r["variant"], r["nprobe"], r["repetition"]): r for r in records}
        for row in records:
            peer = index.get((row["scenario"],
                "compact_layout" if row["variant"] == "original_layout" else "original_layout",
                row["nprobe"], row["repetition"]))
            if not peer:
                continue
            require(row["payload_hashes"] == peer["payload_hashes"], "Layout payload parity failed")
            if kind == "diagnostic":
                require(row["graph_payload_hashes"] == peer["graph_payload_hashes"], "H1 phase changed")
                left = np.fromfile(OUTPUT / row["case"] / f"nprobe_{row['nprobe']}" / "navigation.u64",
                                   dtype="<u8").reshape(row["queries"], len(COUNTERS))
                right = np.fromfile(OUTPUT / peer["case"] / f"nprobe_{row['nprobe']}" / "navigation.u64",
                                    dtype="<u8").reshape(peer["queries"], len(COUNTERS))
                for column, name in enumerate(COUNTERS):
                    if "alloc" not in name:
                        require(np.array_equal(left[:, column], right[:, column]),
                                f"Native signature/navigation work differs: {name}")
        save(OUTPUT / f"{kind}-results.json", records)
        print(json.dumps(dict(stage=kind, case=case["case"], completed_points=len(records))), flush=True)
    if kind == "functional":
        save(OUTPUT / "functional-completion.json", dict(points=len(records), queries=32, parity=True))
    if kind == "plain":
        points = []
        for scenario in SCENARIOS:
            for variant in VARIANTS:
                for probe in GRID:
                    pair = [r for r in records if (r["scenario"], r["variant"], r["nprobe"]) ==
                            (scenario, variant, probe)]
                    require(len(pair) == 2 and pair[0]["payload_hashes"] == pair[1]["payload_hashes"],
                            "Missing deterministic reversed pair")
                    points.append(dict(scenario=scenario, variant=variant, nprobe=probe,
                        recall=pair[0]["recall"], qps=statistics.mean(r["qps"] for r in pair),
                        qps_min=min(r["qps"] for r in pair), qps_max=max(r["qps"] for r in pair)))
        save(OUTPUT / "normal-summary.json", dict(normal_points=144, points=points,
            search_settings=SETTINGS, all_frozen_final_payloads_identical=True))
    if kind == "diagnostic":
        save(OUTPUT / "parity.json", dict(points=len(records), query_count=1000, nprobe=GRID,
            final_ids_distances_ssd=True, original_h1_phase=True, signature_navigation_work=True,
            excluded_counter_columns=[x for x in COUNTERS if "alloc" in x]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("prepare", "functional", "plain", "diagnostic"))
    args = parser.parse_args()
    if args.stage == "prepare":
        prepare()
    else:
        run(args.stage)
