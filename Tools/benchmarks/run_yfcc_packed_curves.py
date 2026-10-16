#!/usr/bin/env python3
"""Rerun the fixed YFCC10M packed-H1 grid with only the Lion native client."""
import argparse
import configparser
import csv
import json
import math
import os
from pathlib import Path
import shutil
import signal
import statistics
import struct
import subprocess
import sys

import numpy as np

from run_camera_threeway import (
    BAD_FIELDS, Campaign, identity, matrix, now, registry, require, save, validate_capture,
)

HERE = Path(__file__).resolve().parent
SCENARIOS = ("sel_50pct", "sel_20pct", "sel_10pct", "sel_5pct", "sel_1pct", "sel_01pct")
THREADS = (1, 4, 8, 16, 32, 48)
MEMORY_SCOPE = "native_process_lifetime_including_load_warmup_validation"
SELECTION = HERE / "configs/yfcc10m_packed_heads/selection.json"
TIME = Path("/usr/bin/time")


def read(path):
    return json.loads(Path(path).read_text())


def load_selection(path):
    raw = read(path)
    require(set(raw) == set(SCENARIOS), "Expected all six YFCC camera scenarios")
    result = {}
    for scenario in SCENARIOS:
        pairs = raw[scenario]
        require(len(pairs) == 9 and all(
            isinstance(pair, list) and len(pair) == 2 and
            all(type(value) is int for value in pair) and
            100 <= pair[0] <= 3072 and pair[1] in (1024, 4096)
            for pair in pairs), "Expected nine valid nprobe/MaxCheck pairs per scenario")
        require(len(set(map(tuple, pairs))) == 9, "Duplicate parameter pair")
        result[scenario] = [dict(value=value, maxcheck=maxcheck) for value, maxcheck in pairs]
    return result


class LionInputs(Campaign):
    def prepare(self):
        self.prepared.mkdir()
        require(self.queries.dtype == np.uint8, "Expected authenticated UInt8 queries")
        with (self.prepared / "query.u8bin").open("xb") as stream:
            stream.write(struct.pack("<II", *self.queries.shape))
            self.queries.tofile(stream)
        for name in self.names:
            (self.prepared / f"gt_{name}.u32bin").symlink_to(self.w["truth"][name]["native_ids"])


def profile(campaign, directory, scenario, settings, threads):
    require(threads in THREADS, "Only 1/4/8/16/32/48 query workers are supported")
    phase = "single" if threads == 1 else "throughput"
    controls = sorted({item["value"] for item in settings})
    ini = configparser.ConfigParser(interpolation=None)
    ini.optionxform = str
    ini["Dataset"] = {
        "Vectors": campaign.w["base_file"], "Queries": campaign.w["query_file"],
        "Attributes": campaign.w["attribute_source"], "VectorCount": "10000000",
        "Dimension": "192", "AttributeColumns": "4", "CategoricalColumn": "2", "NumericColumn": "0",
    }
    ini["Run"] = {"OutputDirectory": str(directory), "PreparedDirectory": str(campaign.prepared)}
    ini["Benchmark"] = {
        "QueryCount": "1000", "WarmupQueries": "1000", "TopK": "100", "SingleRepeats": "3",
        "Scenarios": scenario, "AllowShortResults": "true", "MinimumQPS": "10",
        "WarmupPolicy": "once_per_scenario", "MaximumSeconds": "100",
    }
    placement = {"CPUNodes": ",".join(map(str, campaign.c["cpu_nodes"])),
                 "MemoryNodes": ",".join(map(str, campaign.c["memory_nodes"]))}
    ini["Single"] = placement
    ini["Throughput"] = {
        **placement, "Threads": str(threads), "Repeats": "3", "MinimumSeconds": "0",
        "RecallTargets": "0.9,0.95", "AIOReserve": "8192", "ResourcePolicy": "current-host-limit",
    }
    ini["SPANN"] = {"IndexDirectory": campaign.c["indexes"]["lion"],
                    "NProbe": ",".join(map(str, controls)), "Protocol": "explicit-ini-v1"}
    ini["SearchSSDIndex"] = {
        "isExecute": "true", "BuildSsdIndex": "false", "NumberOfThreads": "1",
        "InternalResultNum": str(settings[0]["value"]), "ResultNum": "100",
        "MaxCheck": "4096", "HashTableExponent": "4", "MaxDistRatio": "8",
        "SearchPostingPageLimit": "3", "DisableCrossEdges": "true",
        "EnablePostingNavigation": "true", "PostingAnchorCount": "0",
        "PostingAdditionalMaxCheck": "2048", "PostingNavigationWidth": "128",
        "PostingMatchRatePercent": "12.5", "PostingMatchWindow": "1024",
        "EnableHybridDistance": "false", "EnablePackedHeadLayout": "true",
        "LogPhaseTime": "false", "LogPathStats": "false", "DumpHeads": "0",
    }
    ini["Scenario." + scenario] = {
        "Kind": "categorical_or", "Tags": ",".join(map(str, campaign.tags[scenario])),
        "CandidateCount": str(campaign.w["truth"][scenario]["candidate_count"]),
        "Truth": campaign.w["truth"][scenario]["native_ids"],
    }
    with (directory / "profile.ini").open("x") as stream:
        ini.write(stream, space_around_delimiters=False)
    jobs = [(scenario, item["value"], threads, repeat, item["maxcheck"])
            for item in settings for repeat in (1, 2, 3)]
    (directory / "plans").mkdir()
    with (directory / "plans" / f"SPTAG_adaptive.{phase}.tsv").open("x") as stream:
        stream.write("scenario\tcontrol_index\tthread_index\trepeat\tmaxcheck\n")
        for name, value, _, repeat, maxcheck in jobs:
            stream.write(f"{name}\t{controls.index(value)}\t0\t{repeat}\t{maxcheck}\n")
    return jobs


def validate_memory(ready, rows, exit_peak):
    require(type(exit_peak) is int and exit_peak > 0, "Missing native exit peak RSS")
    previous = 0
    for ordinal, row in enumerate([ready, *rows]):
        fields = ("rss_after_load_bytes",) if ordinal == 0 else (
            "rss_before_queries_bytes", "rss_after_queries_bytes")
        require(row.get("peak_rss_scope") == MEMORY_SCOPE and all(
            type(row.get(key)) is int and row[key] > 0 for key in (*fields, "peak_rss_bytes")),
            "Missing or invalid native RSS record")
        require(previous <= row["peak_rss_bytes"] <= exit_peak,
                "Process-lifetime RSS high-water must be cumulative and bounded by wait4")
        previous = row["peak_rss_bytes"]


def aggregate(scenario, threads, settings, rows, directory, ready, exit_peak):
    validate_memory(ready, rows, exit_peak)
    points = []
    for item in settings:
        measured = [row for row in rows if (row["L"], row["maxcheck"]) ==
                    (item["value"], item["maxcheck"])]
        require(sorted(row["repeat"] for row in measured) == [1, 2, 3],
                "Incomplete repetition group cannot be published")
        require(all(row["qps"] >= 10 for row in measured), "Observed QPS below the 10-QPS floor")
        points.append(dict(
            scenario=scenario, phase="single" if threads == 1 else "throughput", threads=threads,
            nprobe=item["value"], maxcheck=item["maxcheck"], pages=3, repeats=3,
            recall=statistics.median(row["recall"] for row in measured),
            recall_min=min(row["recall"] for row in measured),
            recall_max=max(row["recall"] for row in measured),
            qps=statistics.median(row["qps"] for row in measured),
            qps_min=min(row["qps"] for row in measured), qps_max=max(row["qps"] for row in measured),
            underfilled_queries=max(row["underfilled_queries"] for row in measured),
            packed_head_layout=True, reused=False, source=dict(directory=str(directory)),
            rss_after_load_bytes=ready["rss_after_load_bytes"],
            rss_before_queries_bytes=max(row["rss_before_queries_bytes"] for row in measured),
            rss_after_queries_bytes=max(row["rss_after_queries_bytes"] for row in measured),
            peak_rss_bytes=max(row["peak_rss_bytes"] for row in measured),
            process_exit_peak_rss_bytes=exit_peak, peak_rss_scope=MEMORY_SCOPE,
            memory_aggregation="maximum_observed_across_three_repetitions",
            measurements=measured))
    return points


def run_native(directory, native_argv, env, jobs):
    # A small intermediate parent avoids inheriting the Python controller's
    # pre-exec RSS high-water. GNU time waits on the actual native child.
    peak_path = directory / "native-peak-rss.json"
    command = [str(TIME), "--quiet", "--output=" + str(peak_path),
               '--format={"peak_rss_kib":%M,"exit_status":%x}', *native_argv]
    execution = dict(argv=command, native_argv=native_argv, plan=jobs, started_at=now(),
                     total_timeout_seconds=None,
                     environment={key: env[key] for key in ("LD_LIBRARY_PATH", "GLIBC_TUNABLES") if key in env})
    with (directory / "native.log").open("x") as log:
        child = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        execution.update(launcher_pid=child.pid, state="running")
        save(directory / "execution.json", execution)
        try:
            child.wait()
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
    execution.update(state="exited", completed_at=now(), returncode=child.returncode)
    save(directory / "execution.json", execution)
    require(child.returncode == 0, f"Native failure or cohort timeout; inspect {directory / 'native.log'}")
    peak = read(peak_path)
    require(peak["exit_status"] == 0 and type(peak["peak_rss_kib"]) is int and peak["peak_rss_kib"] > 0,
            "Invalid native-child peak RSS from GNU time")
    execution.update(peak_rss_bytes=peak["peak_rss_kib"] * 1024, peak_rss_scope=MEMORY_SCOPE,
                     peak_rss_source="GNU time native-child wait4 ru_maxrss; KiB converted to bytes")
    save(directory / "execution.json", execution)
    return execution


def measure(campaign, binary, scenario, settings, threads):
    phase = "single" if threads == 1 else "throughput"
    directory = campaign.root / "batches" / f"{phase}-t{threads}-{scenario}"
    directory.mkdir(parents=True)
    jobs = profile(campaign, directory, scenario, settings, threads)
    command = ["numactl", "--cpunodebind=" + ",".join(map(str, campaign.c["cpu_nodes"])),
               "--interleave=" + ",".join(map(str, campaign.c["memory_nodes"])),
               str(binary), str(directory / "profile.ini"), phase]
    campaign.status("running", scenario=scenario, threads=threads, phase=phase)
    execution = run_native(directory, command, campaign.env, jobs)
    exit_peak = execution["peak_rss_bytes"]
    records = {key: [] for key in ("RESULT", "SKIP", "READY", "COMPLETE", "SPANN_INDEX")}
    with (directory / "native.log").open() as stream:
        for line in stream:
            for key, values in records.items():
                prefix = "THREEWAY_" + key + " "
                if line.startswith(prefix):
                    values.append(json.loads(line[len(prefix):]))
    rows = records["RESULT"]
    save(directory / "raw.json", rows)
    require(not records["SKIP"], f"Native QPS-floor stop; inspect {directory}")
    require(len(records["READY"]) == len(records["COMPLETE"]) == len(records["SPANN_INDEX"]) == 1,
            "Missing or duplicate native lifecycle event")
    ready, complete, native = records["READY"][0], records["COMPLETE"][0], records["SPANN_INDEX"][0]
    require(type(ready["pid"]) is int and ready["pid"] > 0 and ready["pid"] != execution["launcher_pid"] and
            ready["max_threads"] == threads and ready["job_count"] == len(jobs),
            "Native resident process/plan mismatch")
    execution["native_pid"] = ready["pid"]
    save(directory / "execution.json", execution)
    require(native["packed_head_layout"] is True and native["index_load_count"] == 1 and
            native["enable_posting_navigation"] is True and native["max_threads"] == threads,
            "Packed shared resident index was not activated")
    require(complete["completed_jobs"] == len(rows) == len(jobs) and complete.get("skipped_jobs", 0) == 0,
            "Incomplete native cohort accounting")
    observed = [(row["scenario"], row["L"], row["threads"], row["repeat"], row["maxcheck"]) for row in rows]
    require(len(set(observed)) == len(jobs) and set(observed) == set(jobs), "Missing/duplicate/unplanned result")
    for ordinal, row in enumerate(rows):
        require(row["engine"] == "SPTAG_adaptive" and row["phase"] == phase and
                all(row[field] == 0 for field in BAD_FIELDS), "Invalid native query output")
        require(row["queries"] == 1000 and row["passes"] == 1 and
                row["warmup_queries"] == (1000 if ordinal == 0 else 0) and
                0 < row["elapsed_seconds"] <= 100 and
                math.isclose(row["qps"], 1000 / row["elapsed_seconds"], rel_tol=1e-10),
                "Native timing/cohort mismatch")
        for prefix in ("first", "last"):
            ids, distances = Path(row[prefix + "_ids"]), Path(row[prefix + "_distances"])
            require(ids.resolve().is_relative_to(directory) and distances.resolve().is_relative_to(directory),
                    "Capture escaped its registered batch")
            recall = validate_capture(matrix(ids, "<u4"), matrix(distances, "<f4"), campaign.base,
                                      campaign.queries, campaign.attrs, campaign.tags[scenario], campaign.truth[scenario])
            require(math.isclose(recall, row["recall"], abs_tol=1e-12) and
                    math.isclose(recall, row[prefix + "_recall"], abs_tol=1e-12), "Capture recall mismatch")
    points = aggregate(scenario, threads, settings, rows, directory, ready, exit_peak)
    save(directory / "summary.json", points)
    save(directory / "accepted.json", dict(
        state="validated", completed_at=now(),
        sha256={str(path.relative_to(directory)): registry.file_sha(path)
                for path in sorted(directory.rglob("*")) if path.is_file()}))
    return points


def summarize(points, selection, plan):
    expected = {(name, threads, item["value"], item["maxcheck"])
                for name, settings in selection.items() for threads in THREADS for item in settings}
    indexed = {}
    for point in points:
        key = (point["scenario"], point["threads"], point["nprobe"], point["maxcheck"])
        require(key not in indexed and point["reused"] is False and point["packed_head_layout"] is True,
                "Duplicate, reused or unpacked point")
        require(point["repeats"] == 3 and len(point["measurements"]) == 3 and point["qps_min"] >= 10,
                "Incomplete or below-floor point")
        indexed[key] = point
    require(set(indexed) == expected and len(expected) == 324, "Incomplete 324-point coverage")
    paired = []
    for key, point in indexed.items():
        if point["threads"] == 1:
            continue
        single = indexed[(key[0], 1, key[2], key[3])]
        paired.append(dict(scenario=key[0], threads=key[1], nprobe=key[2], maxcheck=key[3],
                           single_recall=single["recall"], throughput_recall=point["recall"],
                           single_qps=single["qps"], throughput_qps=point["qps"],
                           qps_scale=point["qps"] / single["qps"]))
    targets = []
    for scenario in SCENARIOS:
        for target in (.90, .95, .97, .99):
            by_threads = {}
            for threads in THREADS:
                eligible = [point for point in points if point["scenario"] == scenario and
                            point["threads"] == threads and point["recall_min"] + 1e-12 >= target]
                best = max(eligible, key=lambda point: point["qps"]) if eligible else None
                by_threads[str(threads)] = ({key: best[key] for key in (
                    "nprobe", "maxcheck", "recall", "qps", "peak_rss_bytes", "process_exit_peak_rss_bytes")}
                    if best else None)
            targets.append(dict(scenario=scenario, target=target, by_threads=by_threads))
    memory = dict(
        unit="bytes", scope=MEMORY_SCOPE, native_processes=36,
        peak_rss_bytes=max(point["process_exit_peak_rss_bytes"] for point in points),
        by_threads={str(threads): dict(
            peak_rss_bytes=max(point["process_exit_peak_rss_bytes"] for point in points if point["threads"] == threads),
            rss_after_load_bytes=max(point["rss_after_load_bytes"] for point in points if point["threads"] == threads),
            rss_before_queries_bytes=max(point["rss_before_queries_bytes"] for point in points if point["threads"] == threads),
            rss_after_queries_bytes=max(point["rss_after_queries_bytes"] for point in points if point["threads"] == threads))
            for threads in THREADS},
        aggregation="maximum_per_process_not_sum_or_worker_multiple",
        note="Native RSS includes input mappings, load/packing, warmup, query work and native validation; "
             "excludes the Python controller. Point peaks are cumulative through that repetition, "
             "not isolated point/query peaks. GNU time isolates native-child wait4 from Python's inherited RSS. "
             "Loading may temporarily retain old and packed storage; all workers share one index.")
    return dict(state="complete", coverage_complete=True, completed_at=now(), protocol=plan,
                selection=selection, points=points, paired=paired, targets=targets, omitted=[],
                reused_points=0, newly_measured_points=324, repetitions=972, memory=memory)


def finish(root, report):
    keys = ("scenario", "phase", "threads", "maxcheck", "nprobe", "recall", "qps", "qps_min", "qps_max",
            "reused", "rss_after_load_bytes", "rss_before_queries_bytes", "rss_after_queries_bytes",
            "peak_rss_bytes", "process_exit_peak_rss_bytes")
    with (root / "curves.csv").open("x") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows({key: point[key] for key in keys} for point in report["points"])
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for group in [(threads,) for threads in THREADS] + [THREADS[1:]]:
        fig, axes = plt.subplots(2, 3, figsize=(15, 9))
        for ax, scenario, label in zip(axes.flat, SCENARIOS, ("50%", "20%", "10%", "5%", "1%", "0.1%")):
            for threads in group:
                selected = sorted((point for point in report["points"] if
                                   point["threads"] == threads and point["scenario"] == scenario),
                                  key=lambda point: point["recall"])
                ax.plot([point["recall"] * 100 for point in selected], [point["qps"] for point in selected],
                        "o-", label=f"{threads} threads", markersize=3)
                if len(group) == 1:
                    for point in selected:
                        ax.annotate(f'{point["nprobe"]}/{point["maxcheck"]}',
                                    (point["recall"] * 100, point["qps"]), xytext=(4, 3),
                                    textcoords="offset points", fontsize=6)
            ax.set(title=label, xlabel="Recall@100 (%)", ylabel="QPS")
            ax.grid(alpha=.25)
            if len(group) > 1:
                ax.legend(fontsize=8)
        fig.suptitle("YFCC10M packed H1, 3 pages, 3 repeats; " +
                     (f"{group[0]} thread(s), labels=nprobe/MaxCheck" if len(group) == 1 else "4/8/16/32/48 threads"))
        fig.tight_layout()
        stem = f"recall-qps-{group[0]}t" if len(group) == 1 else "concurrency-six-scenarios"
        for extension in ("png", "svg"):
            fig.savefig(root / f"{stem}.{extension}", dpi=180)
        plt.close(fig)
    save(root / "final.json", report)
    save(root / "partial.json", dict(state="complete", points=report["points"], omitted=[]))
    save(root / "status.json", dict(state="complete", updated_at=now(), points=324, repetitions=972, reused=0))
    save(root / "checksums.json", {
        str(path.relative_to(root)): registry.file_sha(path)
        for path in sorted(root.rglob("*")) if path.is_file() and path.name != "checksums.json"})


def verify_build(build):
    require(build["status"] == "complete" and build.get("packed_head_layout_supported") is True,
            "A complete packed-capable Lion client build is required")
    for item in build["inputs"] + build["runtime_libraries"] + [build["binary"]]:
        require(registry.file_sha(item["path"]) == item["sha256"], "Client/core/build input changed: " + item["path"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--groundtruth-directory", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--client-build", type=Path, required=True, help="Packed-capable lion.build.json")
    parser.add_argument("--output-directory", type=Path, required=True, help="Must not already exist")
    parser.add_argument("--library-path", default=os.environ.get("LD_LIBRARY_PATH", ""))
    parser.add_argument("--cpu-nodes", nargs="+", type=int, default=[0, 1, 2, 3])
    parser.add_argument("--memory-nodes", nargs="+", type=int, default=[0, 1, 2, 3])
    args = parser.parse_args()
    require(all(nodes and len(nodes) == len(set(nodes)) and min(nodes) >= 0
                for nodes in (args.cpu_nodes, args.memory_nodes)), "Expected distinct nonnegative NUMA nodes")
    selection = load_selection(SELECTION)
    build = read(args.client_build)
    verify_build(build)
    root = args.output_directory.resolve()
    root.mkdir(parents=True, exist_ok=False)
    try:
        config = dict(output_directory=str(root), groundtruth_directory=str(args.groundtruth_directory.resolve()),
                      indexes=dict(lion=str(args.index.resolve())), topk=100, library_path=args.library_path,
                      cpu_nodes=args.cpu_nodes, memory_nodes=args.memory_nodes)
        save(root / "experiment.json", config)
        campaign = LionInputs(root / "experiment.json")
        require(tuple(campaign.names) == SCENARIOS, "Unexpected workload scenario order")
        index_files = lambda: [identity(path) for path in sorted(args.index.resolve().rglob("*")) if path.is_file()]
        before = index_files()
        require(before, "Missing immutable index files")
        paths = {Path(__file__), HERE / "run_camera_threeway.py", HERE / "results/update_results.py", SELECTION,
                 args.client_build.resolve(), TIME, root / "experiment.json", campaign.prepared / "query.u8bin",
                 Path(campaign.w["base_file"]), Path(campaign.w["attribute_source"]), Path(campaign.w["queries"])}
        paths.update(campaign.gt / name for name in ("workloads.json", "completion.json", "verification.json"))
        paths.update(Path(campaign.w["truth"][name][kind]) for name in SCENARIOS
                     for kind in ("ids", "distances", "native_ids"))
        inputs = [identity(path) for path in sorted(paths)]
        save(root / "registration.json", dict(created_at=now(), index_files=before, inputs=inputs,
                                               client_build=build, controller_argv=sys.argv))
        shutil.copyfile(__file__, root / "controller.py")
        plan = dict(
            engine="Lion", index=str(args.index.resolve()), pages=3, threads=list(THREADS),
            concurrency_threads=list(THREADS[1:]), repeats=3, queries=1000, scenarios=list(SCENARIOS),
            points_per_scenario=9, fresh_single_points=54, fresh_throughput_points=270, reused_single_points=0,
            enable_packed_head_layout=True, total_timeout_seconds=None, minimum_qps=10, cohort_guard_seconds=100,
            warmup="1000 once per scenario/index/thread resident batch", measured_cohorts_per_repetition=1,
            selection_rule="Exact preserved nine nprobe/MaxCheck pairs per scenario; no reselection or reused timings.",
            timing="Single validation is outside timing; throughput includes dispatch and native validation/recall "
                   "reduction. Exact Python capture validation and RSS snapshots are outside native timers.",
            placement="Shared host; affinity is not an exclusive CPU reservation. Serial batches, warm buffered IO, "
                      "no cache flush or sysctl change.",
            memory_scope=MEMORY_SCOPE, source_commit=build["native_source"]["commit"])
        save(root / "selection.json", selection)
        save(root / "plan.json", plan)
        points = campaign.events
        for threads in THREADS:
            for scenario in SCENARIOS:
                points.extend(measure(campaign, Path(build["binary"]["path"]), scenario, selection[scenario], threads))
                save(root / "partial.json", dict(state="running", points=points, omitted=[]))
        require(before == index_files(), "Immutable index changed during measurement")
        require(inputs == [identity(path) for path in sorted(paths)], "Registered benchmark inputs changed")
        verify_build(build)
        finish(root, summarize(points, selection, plan))
        print(json.dumps(dict(state="complete", points=324, repetitions=972, reused=0)), flush=True)
    except Exception as error:
        save(root / "failure.json", dict(state="failed", error=str(error), updated_at=now()))
        save(root / "status.json", dict(state="failed", error=str(error), updated_at=now()))
        raise


if __name__ == "__main__":
    main()
