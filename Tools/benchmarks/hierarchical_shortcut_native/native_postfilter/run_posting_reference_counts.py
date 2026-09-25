#!/usr/bin/env python3
"""Diagnostic-only posting-reference accounting for the frozen all-members policy."""
import argparse
import csv
import json
from pathlib import Path
import time

import numpy as np
from run_full import execute, predicate_mask, quality, read_ini, require, sha
from run_member_postfilter import COUNTERS as V4
from run_posting_min_sweep import SCENARIOS, event, save

FIELDS = ("owner_references", "member_references", "candidate_considerations", "unique_states",
          "expand_attempts", "expanded_skips", "completed_rows", "representative_distances")
COLUMNS = V4 + tuple(f"{level}_{field}" for level in ("h2", "h3plus") for field in FIELDS)
HERE = Path(__file__).resolve().parent


def prefix_equal(new, old, count):
    for name, dtype, width in (("ids.i32", "<i4", 10), ("dist.f32", "<f4", 10), ("work.u64", "<u8", 8)):
        current = np.fromfile(new / name, dtype=dtype, count=count * width)
        previous = np.fromfile(old / name, dtype=dtype, count=count * width)
        require(current.size == count * width and np.array_equal(current, previous),
                f"Frozen payload mismatch: {name}, {old}")


def prepare(path):
    config = read_ini(path)
    plan = config["Campaign"]
    output = Path(plan["OutputDirectory"])
    output.mkdir(parents=True, exist_ok=True)
    require(not (output / "registration.json").exists(), "Already registered")
    controls = output / "configs"
    controls.mkdir()
    workload = json.loads(Path(plan["Workloads"]).read_text())
    schedule = []
    for scenario in SCENARIOS:
        for target in (1, 10):
            native = read_ini(path)
            native.remove_section("Campaign")
            native["SearchSSDIndex"]["PostingMinCandidates"] = str(target)
            native["SearchSweep"] = dict(NProbe="[24]")
            predicate, predicate_file = "empty", ""
            if scenario in workload["flat_query_tags"]:
                predicate, predicate_file = "categorical", workload["flat_query_tags"][scenario]
            elif scenario in ("numeric", "mixed_dnf"):
                predicate = "dnf"
                predicate_file = workload["query_dnf"]["numeric" if scenario == "numeric" else "mixed"]
            native["Benchmark"] = dict(Index=plan["Index"], Queries=plan["Queries"], Predicate=predicate,
                PredicateFile=predicate_file, MaxQueries="1000", Warmup="1000")
            case = f"{scenario}_min{target}"
            filename = controls / f"{case}.ini"
            with filename.open("w") as stream:
                native.write(stream, space_around_delimiters=False)
            record = dict(case=case, scenario=scenario, minimum=target, config=str(filename))
            if scenario != "medium_tag" and target == 1:
                native["Benchmark"]["MaxQueries"] = native["Benchmark"]["Warmup"] = "32"
                filename = controls / f"parity32_{case}.ini"
                with filename.open("w") as stream:
                    native.write(stream, space_around_delimiters=False)
                record["parity_config"] = str(filename)
            schedule.append(record)
    inputs = {path, Path(__file__).resolve(), Path(plan["Binary"]), Path(plan["FrozenBinary"]),
              Path(plan["Queries"]), Path(plan["Workloads"]), Path(workload["attributes"])}
    inputs.update(controls.glob("*.ini"))
    inputs.update(p for p in Path(plan["Index"]).rglob("*") if p.is_file())
    inputs.update(Path(workload["truth"][s]["ids"]) for s in SCENARIOS)
    inputs.update(Path(p) for p in workload["flat_query_tags"].values())
    inputs.update(Path(p) for p in workload["query_dnf"].values())
    repo = HERE.parents[3]
    sources = [repo / name for name in (
        "AnnService/inc/Core/Common/GraphAccessStats.h", "AnnService/inc/Core/SPANN/PostingNavigation.h",
        "AnnService/src/Core/BKT/BKTIndex.cpp", "AnnService/inc/Core/Common/NavigationVisited.h",
        "Test/PostingCollectionTest.cpp")]
    sources.append(HERE / "Bench.cpp")
    inputs.update(sources)
    save(output / "source-manifest.json", {str(p): sha(p) for p in sources})
    save(output / "registration.json", dict(query_count=1000, warmup=1000, nprobe=24, minima=[1, 10],
        scenarios=SCENARIOS, schedule=schedule, schema_version=5, columns=COLUMNS, cpu_node=0,
        protected={str(p): sha(p) for p in sorted(inputs)}, expected_diagnostic_processes=12,
        narrow_missing_controls="Five frozen normal32 min1 runs; no throughput accepted",
        definitions={
            "activation": "One supplier Expand(head) call, not a posting count",
            "physical_owner_references": "Sum of Parents-returned range lengths, bucketed by destination layer, before fallback dedup",
            "physical_member_references": "Each upper CSR child ID enumerated, bucketed by child layer; excludes H1 member IDs",
            "candidate_considerations": "Each Collect registration entry and each upper CSR child registration, before Allowed; includes rejects/repeats",
            "unique_states": "Existing try_emplace inserted.second for (level,id), including rejected/unexpanded candidates",
            "expand_attempts": "Each call of ExpandRow before the expanded guard",
            "expanded_skips": "ExpandRow guard found state.expanded; no member read",
            "completed_rows": "Full-row validation then false-to-true expanded transition; distinct rows per query",
            "candidate_repeats": "candidate_considerations minus unique_states per query",
            "physical_repeats": "owner_references plus member_references minus unique_states per query",
            "h3plus": "Every auxiliary posting layer above H2; current index is H2/H3",
            "ratios": "Ratio of summed counts; never a mean of per-query ratios"},
        timing="Diagnostic WORK only. Native log timing is not throughput evidence."))
    event(output, "prepared", diagnostic_processes=12)


def validate(c):
    require(c.shape == (1000, 45), "Unexpected counter matrix")
    require(np.all(c[:, 29] + c[:, 37] == c[:, 6]), "Owner-reference buckets disagree")
    require(np.all(c[:, 30] + c[:, 38] == c[:, 7]), "Posting-member buckets disagree")
    require(np.all(c[:, 32] + c[:, 40] == c[:, 10]), "Unique-state buckets disagree")
    require(np.all(c[:, 36] + c[:, 44] == c[:, 4]), "Representative buckets disagree")
    require(np.all(c[:, 35] == c[:, 12]), "Completed H2 rows disagree with actual selected H2 rows")
    for offset in (29, 37):
        require(np.all(c[:, offset + 4] == c[:, offset + 5] + c[:, offset + 6]), "Incomplete expansion accounting")
        require(np.all(c[:, offset + 6] <= c[:, offset + 3]), "More completed rows than unique states")
        require(np.all(c[:, offset + 3] <= c[:, offset + 2]), "Unique states exceed candidate registrations")
        require(np.all(c[:, offset + 2] <= c[:, offset] + c[:, offset + 1]), "Registrations exceed physical refs")
    require(np.all(c[:, 22] + c[:, 24] == c[:, 8]) and np.all(c[:, 22] == c[:, 23] + c[:, 18]),
            "All-member H1 visit accounting changed")
    require(not np.any(c[:, 25]), "Unexpected unvisited-negative skips")


def run(path):
    plan = read_ini(path)["Campaign"]
    output = Path(plan["OutputDirectory"])
    registration = json.loads((output / "registration.json").read_text())
    workload = json.loads(Path(plan["Workloads"]).read_text())
    attrs = np.load(workload["attributes"], mmap_mode="r")
    frozen = Path(plan["FrozenCampaign"])
    records = []
    for case in registration["schedule"]:
        folder = output / case["case"]
        if not (folder / "validated.json").exists():
            require(not folder.exists(), f"Incomplete diagnostic run retained: {folder}")
            command = ["numactl", "--cpunodebind=0", "--membind=0", plan["Binary"], case["config"]]
            event(output, "process_started", case=case["case"], native_log=str(folder / "native.log"))
            started = time.monotonic()
            peak = execute(command, folder)
            log = (folder / "native.log").read_text()
            rows = [json.loads(line) for line in log.splitlines() if line.startswith('{"mode":')]
            require(len(rows) == 1 and rows[0]["queries"] == 1000 and rows[0]["nprobe"] == 24 and
                    rows[0]["diagnostic"] and rows[0]["navigation_schema_version"] == 5 and
                    rows[0]["navigation_columns"] == 45, "Invalid native diagnostic output")
            require("numeric lanes=1" in log and "conservative unknown" not in log, "Invalid routing signatures")
            point = folder / "nprobe_24"
            c = np.fromfile(point / "navigation.u64", dtype="<u8").reshape(1000, 45)
            validate(c)
            variant = f"all_members_min{case['minimum']}"
            if case["scenario"] == "medium_tag":
                control = frozen / f"plain_medium_tag_{variant}_r1/nprobe_24"
                prefix_equal(point, control, 1000)
                old_counts = np.fromfile(frozen / f"diagnostic_medium_tag_{variant}_r1/nprobe_24/navigation.u64",
                                        dtype="<u8").reshape(1000, 29)
                require(np.array_equal(c[:, :29], old_counts), "Existing v4 Medium counters changed")
                parity_count = 1000
            elif case["minimum"] == 10:
                control = frozen / f"functional_plain_{case['scenario']}_{variant}_r1/nprobe_24"
                prefix_equal(point, control, 32)
                old_counts = np.fromfile(frozen / f"functional_diagnostic_{case['scenario']}_{variant}_r1/nprobe_24/navigation.u64",
                                        dtype="<u8").reshape(32, 29)
                require(np.array_equal(c[:32, :29], old_counts), "Existing v4 functional counters changed")
                parity_count = 32
            else:
                control_root = output / f"parity32_{case['case']}"
                if not (control_root / "parity-ready.json").exists():
                    require(not control_root.exists(), f"Incomplete parity run retained: {control_root}")
                    execute(["numactl", "--cpunodebind=0", "--membind=0", plan["FrozenBinary"],
                             case["parity_config"]], control_root)
                    old_rows = [json.loads(line) for line in (control_root / "native.log").read_text().splitlines()
                                if line.startswith('{"mode":')]
                    require(len(old_rows) == 1 and old_rows[0]["queries"] == 32 and
                            old_rows[0]["navigation_schema_version"] == 4 and not old_rows[0]["diagnostic"],
                            "Unexpected frozen parity control")
                    save(control_root / "parity-ready.json", dict(queries=32, throughput_accepted=False))
                control = control_root / "nprobe_24"
                prefix_equal(point, control, 32)
                parity_count = 32
            mask = np.ones(len(attrs), dtype=bool) if case["scenario"] == "unfilter" else \
                predicate_mask(workload["predicates"][case["scenario"]], attrs)
            derived = np.column_stack([c[:, o + 2] - c[:, o + 3] for o in (29, 37)] +
                [c[:, o] + c[:, o + 1] - c[:, o + 3] for o in (29, 37)])
            np.savez_compressed(folder / "per-query.npz", query_index=np.arange(1000), counters=c,
                columns=np.asarray(COLUMNS), derived=derived,
                derived_columns=np.asarray(("h2_candidate_repeats", "h3plus_candidate_repeats",
                                            "h2_physical_repeats", "h3plus_physical_repeats")))
            record = dict(case, queries=1000, nprobe=24, timing_accepted=False,
                process_wall_seconds=time.monotonic() - started, peak_process_rss_bytes=peak,
                parity_queries=parity_count, parity_control=str(control), counter_schema=5,
                means=dict(zip(COLUMNS, c.mean(0).tolist())),
                p50=dict(zip(COLUMNS, np.percentile(c, 50, axis=0).tolist())),
                p95=dict(zip(COLUMNS, np.percentile(c, 95, axis=0).tolist())))
            record.update(quality(point, workload["truth"][case["scenario"]], mask, 1000))
            save(folder / "validated.json", record)
        records.append(json.loads((folder / "validated.json").read_text()))
        save(output / "diagnostic-results.json", records)
        event(output, "process_complete", case=case["case"], completed_points=len(records))


def finish(path):
    plan = read_ini(path)["Campaign"]
    output = Path(plan["OutputDirectory"])
    registration = json.loads((output / "registration.json").read_text())
    records = json.loads((output / "diagnostic-results.json").read_text())
    require(len(records) == 12 and len({(r["scenario"], r["minimum"]) for r in records}) == 12,
            "Incomplete diagnostic matrix")
    summary = []
    for record in records:
        m = record["means"]
        row = dict(scenario=record["scenario"], minimum=record["minimum"], queries=1000,
            activations=m["posting_activations"], new_matching_h1=m["posting_new_candidates"],
            visited_h1_skips=m["auxiliary_visited_skips"], negative_h1_first_visits=m["auxiliary_negative_first_visits"],
            zero_fresh_h2_rows=m["selected_h2_zero_fresh_rows"], recall=record["recall"])
        for level in ("h2", "h3plus"):
            row.update({f"{level}_{field}": m[f"{level}_{field}"] for field in FIELDS})
            physical = m[f"{level}_owner_references"] + m[f"{level}_member_references"]
            candidate = m[f"{level}_candidate_considerations"]
            unique = m[f"{level}_unique_states"]
            row[f"{level}_physical_references"] = physical
            row[f"{level}_physical_repeats"] = physical - unique
            row[f"{level}_candidate_repeats"] = candidate - unique
            row[f"{level}_candidate_repeat_ratio_of_sums"] = (candidate - unique) / candidate if candidate else None
            row[f"{level}_csr_expansions_total"] = m[f"{level}_expand_attempts"] - m[f"{level}_expanded_skips"]
            row[f"{level}_completed_unique_rows"] = m[f"{level}_completed_rows"]
            row[f"{level}_completed_repeat_rows"] = row[f"{level}_csr_expansions_total"] - row[f"{level}_completed_unique_rows"]
        summary.append(row)
    for source, expected in registration["protected"].items():
        require(sha(source) == expected, f"Registered bytes changed: {source}")
    save(output / "summary.json", dict(status="completed", queries_per_point=1000, diagnostic_points=12,
        scope="Current all-member, row-local1% entry. WORK ONLY; no throughput or timing attribution",
        definitions=registration["definitions"], rows=summary, protected_bytes="unchanged",
        parity="Medium1000 including original29 counters; other scenes first32 payloads; min10 also original29 counters",
        repeat_expansion_evidence="Expanded state is monotone; completion counts only validated false-to-true transitions. Fixtures compare physical row reads. Attempts = completed + guarded skips."))
    with (output / "summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    event(output, "completed", diagnostic_points=12, queries_per_point=1000)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ini", type=Path)
    parser.add_argument("--stage", choices=("prepare", "run", "finish"), required=True)
    args = parser.parse_args()
    try:
        {"prepare": prepare, "run": run, "finish": finish}[args.stage](args.ini.resolve())
    except Exception as error:
        root = Path(read_ini(args.ini)["Campaign"]["OutputDirectory"])
        if root.exists():
            event(root, "failed", stage=args.stage, error=str(error))
        raise
