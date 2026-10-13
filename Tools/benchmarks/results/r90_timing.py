#!/usr/bin/env python3
"""Select six observed R90 points, measure exclusive steps, and publish a diagnostic table."""
import argparse
from collections import defaultdict
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import subprocess

import numpy as np

import native_results as native
import update_results as store
from ssd_label_distribution import prospective

require, read, save = native.require, native.read_json, native.save
SCENARIOS = ("sel50", "sel20", "sel10", "sel5", "sel1", "sel01")
STAGES = ("wrapper", "head_prepare", "h1_search", "hierarchy_supplement", "posting_prepare",
          "aio_prepare", "aio_lock_wait", "aio_submit", "aio_completion_wait", "scan_other",
          "filter_dedup", "distance", "topk_update", "finalize", "decompress")
TABLE = "single-r90-step-timing"
STUDY = "query-step-timing"


def utc():
    return datetime.now(timezone.utc).isoformat()


def selected_points(current, dataset, index):
    points = [point for point in current["points"]
              if point["dataset"] == dataset and point["algorithm"] == "SPTAG"
              and point["study"] == "main" and point["phase"] == "single" and point["threads"] == 1
              and point.get("topk") == 100 and point["cohort_queries"] == 1000
              and point["version"]["index"] == index and point["complete_repeats_same_client"]
              and point["recall"] >= .9]
    selected = []
    for scenario in SCENARIOS:
        candidates = [point for point in points if point["scenario"] == scenario]
        require(candidates, f"No accepted observed R90 point for {scenario}; no interpolation/fallback")
        selected.append(min(candidates, key=lambda point: (-point["qps"], point["event_id"])))
    return selected


def verify_seal(root, artifacts, required):
    require(isinstance(artifacts, dict) and required <= artifacts.keys(), "Incomplete completion seal")
    for relative, expected in artifacts.items():
        path = (root / relative).resolve()
        require(root in path.parents and store.file_sha(path) == expected, "Sealed artifact changed: " + relative)


def verify_reference(config, build):
    plan = native.load_plan(config)
    complete = read(plan["root"] / "completion.json")
    require(complete["state"] == "complete" and complete["exit_code"] == 0
            and not (plan["root"] / "failure.json").exists(), "Reference experiment failed")
    verify_seal(plan["root"], complete["artifacts"],
                {"registration.json", "native.log", "single_raw.json", "events.json", "measurement-analysis.json"})
    require(build["schema_version"] == 1 and build["status"] == "complete", "Unaccepted matched build")
    require(set(build["clients"]) == {"ordinary", "timing"} and build["sources"], "Incomplete matched build")
    for path, expected in build["sources"].items():
        require(store.file_sha(path) == expected, "Matched build source changed: " + path)
    for client in build["clients"].values():
        require(store.file_sha(client["binary"]) == client["sha256"], "Matched client changed")
    proof = read(plan["root"] / "registration.json")
    require(proof["binary_sha256"] == build["clients"]["ordinary"]["sha256"],
            "Reference must use the ordinary client from this exact matched build")
    native.verify(plan, proof)
    inputs = native.load_inputs(plan)
    rows = native.validate_log(plan, inputs)
    require(rows == read(plan["root"] / "single_raw.json"), "Reference log/captures changed")
    events = native.normalize(plan, inputs, rows, complete["completed_at"])
    require(events == read(plan["root"] / "events.json"), "Reference events changed")
    return plan, inputs, events


def write_ini(path, config):
    with Path(path).open("x") as stream:
        for section, values in config.items():
            stream.write(f"[{section}]\n")
            for key, value in values.items():
                stream.write(f"{key}={value}\n")
            stream.write("\n")


def prepare(config):
    config = native.absolute(str(config))
    root = native.absolute(native.read_config(config)["r90"]["directory"])
    require(not root.exists(), "Diagnostic output directory must be fresh")
    try:
        _prepare(config)
    except Exception as error:
        if root.is_dir():
            save(root / "failure.json", dict(error=str(error), failed_at=utc(), phase="prepare"))
        raise


def _prepare(config):
    config = native.absolute(str(config))
    values = native.read_config(config)
    policy = values["r90"]
    require(set(policy) == {"directory", "datadirectory", "dataset", "indexversion", "clients"},
            "R90 requires Directory, DataDirectory, Dataset, IndexVersion, Clients")
    root = native.absolute(policy["directory"])
    require(not root.exists(), "Diagnostic output directory must be fresh")
    registry = native.absolute(policy["datadirectory"])
    require(root != registry and root not in registry.parents and registry not in root.parents,
            "Diagnostic output and registry must be separate")
    current = store.update(registry, check=True)
    selected = selected_points(current, policy["dataset"], policy["indexversion"])
    names = policy["clients"].lower().split(",")
    require(set(values) == {"r90", *names} and len(set(names)) == len(names), "Unexpected/duplicate client sections")
    refs, events_by_id, protected = [], {}, {str(config): native.frozen(config)}
    common_binding = None
    for name in names:
        entry = values[name]
        require(set(entry) == {"experiment", "build"}, "Each client needs Experiment and Build")
        build_path = native.absolute(entry["build"])
        build = read(build_path)
        reference, inputs, events = verify_reference(native.absolute(entry["experiment"]), build)
        require(reference["policy"]["dataset"] == policy["dataset"]
                and reference["policy"]["indexversion"] == policy["indexversion"], "Reference index/dataset differs")
        workload = inputs["workload"]
        binding = dict(queries_sha256=store.file_sha(workload["queries"]),
            base_identity=native.identity(workload["base_file"]),
            attribute_identity=native.identity(workload["attribute_source"]),
            cpu_nodes=reference["policy"]["cpunodes"], memory_nodes=reference["policy"]["memorynodes"])
        require(common_binding is None or binding == common_binding,
                "References must share source vectors/attributes, query bytes and CPU/memory policy")
        common_binding = binding
        require(root != reference["index"] and root not in reference["index"].parents
                and reference["index"] not in root.parents, "Output overlaps immutable index")
        refs.append((name, reference, inputs, build_path, build))
        for event in events:
            event_id = store.digest(event)
            require(event_id not in events_by_id, "Duplicate reference event")
            events_by_id[event_id] = (name, event)
    require(all(point["event_id"] in events_by_id for point in selected),
            "Provide the original experiment and matched build for every selected R90 client")
    root.mkdir(parents=True)
    groups = []
    for name, reference, inputs, build_path, build in refs:
        points = [point for point in selected if events_by_id[point["event_id"]][0] == name]
        if not points:
            continue
        directory = root / name
        directory.mkdir()
        cases = []
        for point in points:
            event = events_by_id[point["event_id"]][1]
            source_config = event["measurements"][0]["config"]
            original_case = next(case for case in reference["cases"] if str(case["config"]) == source_config)
            profile = copy.deepcopy(original_case["profile"])
            require(profile["benchmark"]["maxqueries"] == profile["benchmark"]["warmup"] == "1000",
                    "Timing requires full1000-query windows")
            scenario = point["scenario"]
            profiles = {}
            for mode in ("control", "timing"):
                profile["benchmark"]["steptiming"] = str(mode == "timing").lower()
                profile["searchsweep"] = {"nprobe": str(point["search_value"])}
                profile["searchssdindex"]["internalresultnum"] = str(point["search_value"])
                path = directory / f"{scenario}-{mode}.ini"
                write_ini(path, profile)
                profiles[mode] = str(path)
            for repeat in (1, 2, 3):
                # Balance paired clock-off/on order without additional warmups.
                for mode in (("control", "timing") if repeat % 2 else ("timing", "control")):
                    case_id = "Case" + str(len(cases) + 1)
                    cases.append(dict(case_id=case_id, scenario=scenario, repeat=repeat, mode=mode,
                        config=profiles[mode], output=str(directory / f"{scenario}-{repeat}-{mode}"),
                        nprobe=point["search_value"], source_event_id=point["event_id"],
                        reference_output=event["measurements"][0]["output_directory"]))
        batch = dict(Batch=dict(CaseCount=str(len(cases)), WarmupPolicy="once_per_predicate"))
        batch.update({case["case_id"]: dict(Config=case["config"], OutputDirectory=case["output"]) for case in cases})
        batch_path = directory / "batch.ini"
        write_ini(batch_path, batch)
        group = dict(name=name, directory=str(directory), reference=str(reference["path"]),
            build=str(build_path), binary=build["clients"]["timing"]["binary"], batch=str(batch_path),
            cases=cases, cpu_nodes=reference["policy"]["cpunodes"], memory_nodes=reference["policy"]["memorynodes"],
            timeout_seconds=int(reference["policy"]["timeoutseconds"]))
        groups.append(group)
        protected[str(build_path)] = native.frozen(build_path)
        protected[str(reference["path"])] = native.frozen(reference["path"])
        protected[str(reference["root"] / "completion.json")] = native.frozen(reference["root"] / "completion.json")
        protected[group["binary"]] = native.frozen(group["binary"])
        linked = subprocess.check_output(["ldd", group["binary"]], text=True)
        require("not found" not in linked, "Missing timing client runtime library")
        for line in linked.splitlines():
            for word in line.split():
                if word.startswith("/"):
                    path = str(Path(word).resolve())
                    protected[path] = native.frozen(path)
                    break
    save(root / "selected.json", selected)
    save(root / "plan.json", dict(schema_version=1, groups=groups, dataset=policy["dataset"],
        index_version=policy["indexversion"], registry=str(registry), reference_history=current["history"],
        cohort_binding=common_binding,
        created_at=utc(), threads=1, repeats=3, queries=1000, target_recall=.9,
        warmup_policy="once_per_predicate", stages=list(STAGES),
        timing_scope="Exclusive application wall time; buffered libaio; not device service latency"))
    for path in list(root.rglob("*")) + [Path(__file__), Path(native.__file__), Path(store.__file__)]:
        if path.is_file():
            protected[str(path)] = native.frozen(path)
    save(root / "registration.json", dict(files=protected))
    print(json.dumps(dict(state="prepared", directory=str(root), scenarios=6, diagnostic_repeats=18,
                          paired_control_repeats=18, resident_groups=len(groups))))


def stage_summary(steps, latency):
    require(steps.dtype == np.dtype("<u8") and steps.ndim == 2 and steps.shape[1] == len(STAGES),
            "Wrong step capture schema")
    require(len(steps) > 0 and latency.shape == (len(steps),), "Wrong latency capture shape")
    values = steps.astype(np.float64) / 1e6
    total = latency / 1000
    require(np.isfinite(total).all() and (total > 0).all(), "Invalid per-query latency")
    residual = total - values.sum(axis=1)
    require((residual >= -1e-6).all(), "Exclusive stage sum exceeds complete query latency")
    values = np.column_stack((values, np.maximum(residual, 0)))
    names = (*STAGES, "timer_boundary")
    require(np.allclose(values.sum(axis=1), total, atol=1e-6, rtol=0), "Unaccounted query time")
    def stats(column):
        return dict(mean_ms=float(column.mean()), p50_ms=float(np.median(column)),
                    p95_ms=float(np.percentile(column, 95)), p99_ms=float(np.percentile(column, 99)))
    return dict(total=stats(total), stages={name: dict(**stats(values[:, i]),
        share_percent=100 * float(values[:, i].mean()) / float(total.mean())) for i, name in enumerate(names)})


def collect(root):
    plan = read(root / "plan.json")
    native.verify_files(read(root / "registration.json")["files"])
    selected = {point["event_id"]: point for point in read(root / "selected.json")}
    rows = []
    for group in plan["groups"]:
        reference, inputs, _ = verify_reference(Path(group["reference"]), read(group["build"]))
        directory = Path(group["directory"])
        require(read(directory / "execution.json")["exit_code"] == 0, "Failed native timing group")
        events = [json.loads(line) for line in (directory / "native.log").read_text().splitlines()
                  if line.startswith("{")]
        position = 0
        def take(kind, expected):
            nonlocal position
            require(position < len(events) and events[position]["event"] == kind, "Missing native event: " + kind)
            event = events[position]
            position += 1
            require(all(event.get(key) == value for key, value in expected.items()), "Wrong binding in " + kind)
            return event
        take("batch_begin", dict(cases=len(group["cases"]), warmup_policy="once_per_predicate",
             index=str(reference["index"]), queries=inputs["workload"]["queries"]))
        take("batch_loaded", dict(index_load_count=1, query_corpus_load_count=1))
        warmed = set()
        for case in group["cases"]:
            cfg = native.read_config(case["config"])
            binding = dict(case_id=case["case_id"], config=case["config"], output_directory=case["output"])
            if case["scenario"] not in warmed:
                take("scenario_warmup_begin", dict(**binding, queries=1000,
                    warmup_group=cfg["benchmark"]["predicate"] + "\n" + cfg["benchmark"].get("predicatefile", "")))
                take("scenario_warmup_end", dict(completed_queries=1000))
                warmed.add(case["scenario"])
            enabled = case["mode"] == "timing"
            take("case_begin", dict(**binding, warmup_queries=0, measured_queries=1000, replay_queries=1000,
                                   step_timing=enabled, phase_timing=enabled,
                                   search_settings=cfg["searchssdindex"]))
            point = take("point", dict(**binding, queries=1000, topk=100, nprobe=case["nprobe"],
                diagnostic=True, phase_timing=enabled, step_timing=enabled, step_timing_schema_version=1,
                step_timing_columns=len(STAGES), warmup_queries=0, measured_queries=1000, replay_queries=1000,
                warmup_policy="once_per_predicate", value_type="UInt8"))
            folder = Path(case["output"]) / f"nprobe_{case['nprobe']}"
            original = Path(case["reference_output"]) / f"nprobe_{case['nprobe']}"
            for name in ("ids.i32", "dist.f32", "work.u64"):
                require(store.file_sha(folder / name) == store.file_sha(original / name),
                        "Diagnostic/control output differs from accepted ordinary reference: " + name)
            latency = native.payload(folder / "latency_us.f64", "<f8", (1000,))
            require(np.isfinite(latency).all() and (latency > 0).all(), "Invalid diagnostic latency")
            row = dict(scenario=case["scenario"], mode=case["mode"], repeat=case["repeat"],
                       source_event_id=case["source_event_id"], mean_ms=float(latency.mean() / 1000))
            if enabled:
                steps = native.payload(folder / "steps.ns.u64", "<u8", (1000, len(STAGES)))
                require(steps[:, 2].sum() > 0 and steps[:, 7].sum() > 0,
                        "Expected native H1 and libaio hooks did not execute")
                row["breakdown"] = stage_summary(steps, latency)
            else:
                require(not (folder / "steps.ns.u64").exists(), "Clock-off control contains timing captures")
            rows.append(row)
            take("case_end", dict(**binding, completed_points=1))
        take("batch_end", dict(completed_cases=len(group["cases"]), completed_warmup_queries=1000 * len(warmed)))
        require(position == len(events), "Unexpected trailing native events")
    summary = []
    for scenario in SCENARIOS:
        timed = [row for row in rows if row["scenario"] == scenario and row["mode"] == "timing"]
        controls = [row for row in rows if row["scenario"] == scenario and row["mode"] == "control"]
        require(sorted(row["repeat"] for row in timed) == sorted(row["repeat"] for row in controls) == [1, 2, 3],
                "Incomplete three-repeat diagnostic/control group")
        source = selected[timed[0]["source_event_id"]]
        mean_total = statistics.mean(row["mean_ms"] for row in timed)
        means = {stage: statistics.mean(row["breakdown"]["stages"][stage]["mean_ms"] for row in timed)
                 for stage in (*STAGES, "timer_boundary")}
        summary.append(dict(scenario=scenario, source_event_id=source["event_id"], recall=source["recall"],
            nprobe=source["search_value"], source_variant=source["variant"], parameters=source["parameters"],
            reference_qps=source["qps"], unit="ms/query", queries_per_repeat=1000, repeats=3,
            total_mean_ms=mean_total, stage_mean_ms=means,
            stage_share_percent={key: value * 100 / mean_total for key, value in means.items()},
            control_mean_ms=statistics.mean(row["mean_ms"] for row in controls),
            paired_clock_on_over_off_ratio=[timed[i]["mean_ms"] / controls[i]["mean_ms"] for i in range(3)],
            repetitions=timed, controls=controls, dominant_stage=max(means, key=means.get)))
    return dict(schema_version=1, dataset=plan["dataset"], index_version=plan["index_version"],
        threads=1, target_recall=.9, rows=summary, stages=list(STAGES), diagnostic_only=True,
        limitations=[
            "Observed recall>=0.90 operating points, not exact90% or interpolated recall.",
            "Clock-on/off controls share the timing-enabled binary; they are not formal uninstrumented QPS.",
            "Per-record clocks perturb CPU/cache costs; report the paired overhead ratios with the breakdown.",
            "All stages are exclusive application wall time; nanosecond storage does not imply nanosecond accuracy.",
            "AIO submit may perform buffered reads synchronously; submit+completion_wait is application IO time.",
            "AIO completion batching and page cache preclude per-request physical SSD latency claims.",
            "Wrapper includes predicate preparation, return adaptation and capture copying; timer_boundary closes the interval.",
            "Per-query latency excludes result destruction and between-query loop overhead; stage totals are not cohort QPS time.",
            "Head preparation includes workspace/routing; hierarchy includes its candidate filtering/distance/heap work.",
            "Filter/dedup, distance and topk_update columns refer to final raw posting scans, not all graph operations.",
            "No cache dropping, direct-IO conversion, index rebuild or search-policy change."])


def run(root):
    root = root.resolve()
    plan = read(root / "plan.json")
    native.verify_files(read(root / "registration.json")["files"])
    require(not (root / "failure.json").exists(), "Failed preparation; use a fresh diagnostic directory")
    require(not (root / "started.json").exists(), "Fresh-only execution; preserve failed diagnostics")
    save(root / "started.json", dict(started_at=utc()))
    try:
        for group in plan["groups"]:
            reference, _, _ = verify_reference(Path(group["reference"]), read(group["build"]))
            directory = Path(group["directory"])
            command = ["numactl", "--cpunodebind=" + group["cpu_nodes"], "--membind=" + group["memory_nodes"],
                native.sys.executable, "-B", str(native.POSTFILTER / "selectivity_common.py"), str(directory),
                group["binary"], group["batch"]]
            with (directory / "native.log").open("x") as log:
                process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                         timeout=group["timeout_seconds"], check=False)
            save(directory / "execution.json", dict(command=command, exit_code=process.returncode))
            require(process.returncode == 0, "Native timing failed; see " + str(directory / "native.log"))
        report = collect(root)
        report["completed_at"] = utc()
        save(root / "report.json", report)
        save(root / "completion.json", dict(status="complete", artifacts={
            str(path.relative_to(root)): store.file_sha(path) for path in root.rglob("*") if path.is_file()}))
        print(json.dumps(dict(status="complete", report=str(root / "report.json"), scenarios=6)))
    except Exception as error:
        save(root / "failure.json", dict(error=str(error), failed_at=utc()))
        raise


def publish(root, registry, version_order):
    root, registry = root.resolve(), registry.resolve()
    require(version_order >= 0 and not (root / "failure.json").exists(), "Failed report/invalid version")
    completion = read(root / "completion.json")
    require(completion["status"] == "complete", "Timing did not complete")
    verify_seal(root, completion["artifacts"],
                {"registration.json", "selected.json", "plan.json", "started.json", "report.json"})
    report = read(root / "report.json")
    checked = collect(root)
    require(checked == {key: value for key, value in report.items() if key != "completed_at"},
            "Diagnostic report no longer reproduces")
    event = dict(schema_version=1, kind="table", study=STUDY, variant=TABLE, dataset=report["dataset"],
        algorithm="SPTAG", table_name=TABLE, status="validated", completed_at=report["completed_at"],
        version=dict(id=report["index_version"] + "/" + store.file_sha(root / "report.json"),
                     index=report["index_version"], client=TABLE + "-v1", order=version_order),
        rows=report["rows"], source=dict(description="Validated exclusive single-worker R90 diagnostic; never formal QPS",
            report=str(root / "report.json"), sha256=store.file_sha(root / "report.json"),
            limitations=report["limitations"], timing_scope="buffered application wall time; not physical SSD latency"))
    receipt = root / "publication-receipt.json"
    if receipt.exists():
        previous = read(receipt)
        require(previous["status"] == "complete" and previous["event_id"] == store.digest(event)
                and previous["current"] == str(registry / "current.json"), "Publication receipt differs")
    with store.locked(registry):
        records = store.load_history(registry / "history.jsonl")
        before = store.project(records)
        require(before == read(registry / "current.json"), "Canonical projection is stale")
    active_ids = {point["event_id"] for point in before["points"]}
    require(all(row["source_event_id"] in active_ids for row in report["rows"]),
            "Source R90 points are no longer active; do not label this a current breakdown")
    catalog = copy.deepcopy(before["catalog"])
    events = []
    if not any(study["id"] == STUDY for study in catalog["studies"]):
        catalog["studies"].append(dict(id=STUDY))
        events.append(dict(schema_version=1, kind="catalog", catalog=catalog,
            revision=1 + max(row["event"]["revision"] for row in records if row["event"]["kind"] == "catalog"),
            completed_at=report["completed_at"], source=dict(description="Register separate query-step diagnostics")))
    events.append(event)
    after = prospective(records, events)
    for key in ("points", "experiments", "coverage", "retired_indexes"):
        require(before.get(key) == after.get(key), "Diagnostic publication changed performance: " + key)
    require(all(table in after["tables"] or (table["study"], table["variant"], table["dataset"], table["algorithm"]) ==
                (STUDY, TABLE, report["dataset"], "SPTAG") for table in before["tables"]), "Unrelated table changed")
    require(any(table["event_id"] == store.digest(event) for table in after["tables"]), "New table is not active")
    current = store.update(registry, events, expected_history_head=before["history"]["head"])
    require(current == after, "Publication differs from its preservation preview")
    if not receipt.exists():
        save(receipt, dict(status="complete", event_id=store.digest(event),
             current=str(registry / "current.json"), history=current["history"]))
    print(json.dumps(dict(status="published", current=str(registry / "current.json"), scenarios=6)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("prepare").add_argument("--config", type=Path, required=True)
    commands.add_parser("run").add_argument("--directory", type=Path, required=True)
    publication = commands.add_parser("publish")
    publication.add_argument("--directory", type=Path, required=True)
    publication.add_argument("--data-dir", type=Path, required=True)
    publication.add_argument("--version-order", type=int, required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.config)
    elif args.action == "run":
        run(args.directory)
    else:
        publish(args.directory, args.data_dir, args.version_order)


if __name__ == "__main__":
    main()
