"""Append registered unfiltered search points without replaying completed experiments."""

import argparse
import os
from pathlib import Path
import shutil
import signal
import statistics
import traceback

import run_sift1b_threeway as shared
from continue_sift1b_threeway import read_plan


ENGINES = ("PipeANN", "Filtered_DiskANN")
MODE = "fixed-grid-unfilter-only-v1"
SINGLE_FIELDS = (*shared.SINGLE_FIELDS, "measurement_series")
THROUGHPUT_FIELDS = (*shared.THROUGHPUT_FIELDS, "source_campaign")


def config_delta(previous, current):
    shared.require(set(previous.parser.sections()) == set(current.parser.sections()),
                   "Supplement changed INI sections")
    changes = []
    for section in previous.parser.sections():
        old, new = previous.parser[section], current.parser[section]
        shared.require(set(old) == set(new), "Supplement changed INI keys")
        changes.extend({"section": section, "key": key, "previous": old[key], "current": new[key]}
                       for key in old if old[key] != new[key])
    allowed = {("run", "outputdirectory"), ("run", "prepareddirectory"),
               ("pipeann", "lsweep"), ("diskann", "lsweep")}
    shared.require({(row["section"], row["key"]) for row in changes} == allowed,
                   "Supplement may change only output paths and the two search grids")
    shared.require(shared.native_short_results(current, "PipeANN"), "Use the accepted native-count client")
    return changes


def scope(row):
    return row["engine"], row["scenario"], float(row["recall_target"])


def missing_scopes(config, previous_rows):
    present = {scope(row) for row in previous_rows}
    return [{"engine": engine, "scenario": "unfilter", "recall_target": target}
            for engine in ENGINES for target in config.targets()
            if (engine, "unfilter", target) not in present]


def plan_single(config, engine):
    result = []
    for repeat in range(1, config.repeats("single") + 1):
        controls = list(range(len(config.controls(engine))))
        if repeat % 2 == 0:
            controls.reverse()
        result.extend(dict(scenario="unfilter", control_index=index, thread_index=0, repeat=repeat)
                      for index in controls)
    return result


def aggregate_single(config, native):
    groups = shared.defaultdict(list)
    for row in native:
        shared.require(row["engine"] in ENGINES and row["scenario"] == "unfilter"
                       and row["phase"] == "single", "Supplement escaped the unfiltered single-thread scope")
        groups[(row["engine"], row["L"])].append(row)
    result = []
    for (engine, control), values in groups.items():
        shared.require(sorted(value["repeat"] for value in values)
                       == list(range(1, config.repeats("single") + 1)), "Incomplete supplemental repetitions")
        row = {
            "engine": engine, "scenario": "unfilter", "L": control, "queries": 1000,
            "repeats": len(values), "threads": 1, "cpu_nodes": config.section("Single")["CPUNodes"],
            "candidate_count": 10**9, "selectivity": 1.0,
            "predicate": shared.scenario_contract(config, "unfilter")["predicate"],
            "measurement_reused": "false", "io_mode": "direct",
            "source": str(config.root / "single_raw.json"),
            "measurement_series": "pipeann_current" if engine == "PipeANN" else "baseline",
        }
        for field in ("recall", "qps"):
            observations = [value[field] for value in values]
            row.update({field: statistics.median(observations),
                        field + "_min": min(observations), field + "_max": max(observations)})
        result.append(row)
    return result


def select_points(config, rows, missing):
    shared.require(all(row["engine"] in ENGINES and row["scenario"] == "unfilter"
                       and row["measurement_reused"] == "false" for row in rows),
                   "Only fresh unfiltered points may calibrate the supplement")
    selected, unavailable = shared.select_operating_points(config, rows)
    requested = {scope(row) for row in missing}
    return ([row for row in selected if scope(row) in requested],
            [row for row in unavailable if scope(row) in requested])


def retain_availability(previous, missing):
    requested = {scope(row) for row in missing}
    retained, superseded = [], []
    for row in previous:
        replace = (row["phase"] == "throughput" and row["status"] == "recall_target_unmet"
                   and row["recall_target"] != "" and scope(row) in requested)
        (superseded if replace else retained).append(row)
    return retained, superseded


def csv_values(rows):
    return [{key: "" if value is None else str(value) for key, value in row.items()} for row in rows]


def verify_previous(previous):
    root = previous.root
    completion = shared.read_json(root / "completion.json")
    status = shared.read_json(root / "status.json")
    shared.require(completion["state"] == status["state"] == "completed"
                   and completion["all_protected_inputs_unchanged"] is True
                   and not (root / "failure.json").exists()
                   and shared.process_identity(status["pid"]) is None,
                   "Supplement requires a completed, stopped, immutable campaign")
    shared.Campaign(previous).verify()
    shared.verify_identities(completion["artifacts"])
    index = shared.read_json(root / "index_completion.json")
    shared.verify_records(index["artifacts"] + index["build_validation"]["protected"])
    for stage in ("single", "complete"):
        shared.validate_plot_publication(previous, stage)
    chain = shared.read_json(root / "continuation.json")
    shared.require(chain["authorization"] == "allow-native-short-results",
                   "Expected the accepted native-count completed campaign")
    inherited = shared.read_json(Path(chain["previous_directory"]) / "throughput_raw.json")
    prefix = shared.read_json(root / "partial-pipeann-reuse.json")["results"]
    native = []
    for engine in ENGINES:
        plan = read_plan(root / "plans" / f"{engine}.throughput.tsv")
        command = shared.read_json(root / "logs" / f"{engine}.throughput.command.json")
        shared.require(command["argv"] == previous.affinity("throughput") + [
            shared.read_json(root / "manifest.json")["binaries"][engine]["path"],
            str(previous.path), "throughput"], "Completed native command changed")
        shared.validate_native_completion(previous, engine, "throughput", plan, command["pid"])
        values = shared.parse_results(previous, engine, "throughput",
                                      root / "logs" / f"{engine}.throughput.stdout.log", plan)
        shared.validate_captures(previous, values)
        native.extend(values)
    raw = shared.read_json(root / "throughput_raw.json")
    shared.require(raw == inherited + prefix + native
                   and len(raw) == completion["throughput_native_repetitions"],
                   "Completed raw records differ from authenticated retained prefixes and native logs")
    summary, _ = shared.aggregate_throughput(previous, raw, shared.read_json(root / "operating_points.json"))
    shared.require(csv_values(summary) == shared.read_csv(root / "throughput_summary.csv"),
                   "Completed throughput table differs from its raw records")
    return raw


def prepare(config, previous_directory):
    previous = shared.load_profile(previous_directory / "config/benchmark.ini")
    shared.require(previous.root == previous_directory and not config.root.exists(),
                   "Use the actual predecessor and a fresh supplement output")
    changes = config_delta(previous, config)
    previous_raw = verify_previous(previous)
    previous_single = shared.read_csv(previous.root / "single_summary.csv")
    previous_throughput = shared.read_csv(previous.root / "throughput_summary.csv")
    missing = missing_scopes(config, previous_throughput)
    shared.require(missing, "No missing unfiltered target requires supplementation")
    for engine in ENGINES:
        measured = {int(row["L"]) for row in previous_raw
                    if row["engine"] == engine and row["scenario"] == "unfilter"}
        if engine == "Filtered_DiskANN":
            measured.update(int(row["L"]) for row in previous_single
                            if row["engine"] == engine and row["scenario"] == "unfilter")
        shared.require(set(config.controls(engine)).isdisjoint(measured),
                       "Supplement would repeat a completed current-client search control")
    shared.prepare(config, historical_config=previous)
    retained = config.root / "retained"
    retained.mkdir()
    for name in ("single_summary.csv", "throughput_summary.csv", "throughput_raw.json",
                 "availability.csv", "operating_points.json", "completion.json"):
        shutil.copy2(previous.root / name, retained / name)
    runner = Path(__file__).resolve()
    shutil.copy2(runner, config.root / "code" / runner.name)
    old_manifest = shared.read_json(previous.root / "manifest.json")
    protected = [*old_manifest["protected"], *old_manifest["prepared_inputs"]]
    protected.extend(shared.file_record(entry["path"], True) for entry in old_manifest["frozen"])
    previous_files = [path for path in previous.root.rglob("*") if path.is_file()]
    protected.extend(shared.file_record(path, True) for path in previous_files)
    shared.verify_records(protected)
    retained_availability, superseded = retain_availability(
        shared.read_csv(retained / "availability.csv"), missing)
    shared.write_csv(config.root / "superseded_availability.csv", shared.AVAILABILITY_FIELDS, superseded)
    declaration = {
        "mode": MODE, "previous_directory": str(previous.root),
        "search_grids": {engine: config.controls(engine) for engine in ENGINES},
        "missing_targets": missing, "configuration_changes": changes,
        "policy": "Measure every new INI control twice; select the smallest fresh qualifying control only "
                  "for missing unfiltered targets. No retries, output-dependent grid widening or index rebuilds.",
    }
    shared.write_json(config.root / "supplement.json", declaration)
    shared.write_json(config.root / "reuse-state.json", {
        "availability": retained_availability, "missing_targets": missing,
        "reused_single_points": len(previous_single), "reused_throughput_jobs": len(previous_raw)})
    registration = shared.read_json(config.root / "registration.json")
    registration["unfiltered_supplement"] = declaration
    registration["caption_note"] += (
        " This is a separately registered unfiltered-only supplement. All prior measurements are retained. "
        "New PipeANN single-thread points use the current corrected native-count client and form a separate "
        "series from historical PipeANN points, even at the same L. New DiskANN search points extend the "
        "same categorical-focused graph; construction L remains 1. Only missing unfiltered target throughput "
        "is newly measured; existing SPANN, categorical and valid unfiltered throughput is reused.")
    shared.write_json(config.root / "registration.json", registration)
    manifest = shared.read_json(config.root / "manifest.json")
    manifest["protected"].extend(protected)
    manifest["frozen"].extend(shared.identity(path, True) for path in (
        runner, config.root / "code" / runner.name, config.root / "supplement.json",
        config.root / "reuse-state.json", config.root / "registration.json",
        config.root / "superseded_availability.csv", *sorted(retained.iterdir())))
    shared.write_json(config.root / "manifest.json", manifest)
    return config.root


def run(directory):
    shared.require(Path(__file__).resolve() == directory / "code/supplement_sift1b_threeway.py",
                   "Run the frozen supplemental controller")
    config = shared.load_profile(directory / "config/benchmark.ini")
    shared.require(config.root == directory and shared.read_json(directory / "status.json")["state"] == "prepared",
                   "Supplement already started; no implicit retry or overwrite")
    campaign = shared.Campaign(config)
    with (directory / "run.lock").open("x") as stream:
        stream.write(shared.json.dumps({"pid": os.getpid(), "started_at_utc": shared.utc_now()}) + "\n")
    try:
        campaign.verify()
        declaration = shared.read_json(directory / "supplement.json")
        shared.require(declaration["mode"] == MODE
                       and declaration["search_grids"] == {engine: config.controls(engine) for engine in ENGINES},
                       "Supplement registration differs from the frozen INI")
        reuse = shared.read_json(directory / "reuse-state.json")
        campaign.wait_for_build()
        single_native = []
        for engine in ENGINES:
            single_native.extend(campaign.native(engine, "single", plan_single(config, engine)))
            shared.write_json(directory / "single_raw.json", single_native)
        fresh_single = aggregate_single(config, single_native)
        original = shared.read_csv(directory / "retained/single_summary.csv")
        combined = [{**row, "measurement_series": "baseline"} for row in original] + fresh_single
        shared.write_csv(directory / "single_summary.csv", SINGLE_FIELDS, combined)
        selected, missing = select_points(config, fresh_single, reuse["missing_targets"])
        shared.write_json(directory / "operating_points.json", selected)
        availability = reuse["availability"] + missing
        shared.write_csv(directory / "availability.csv", shared.AVAILABILITY_FIELDS, availability)
        campaign.plot("single")
        shared.write_json(directory / "single_completion.json", {
            "state": "completed", "at_utc": shared.utc_now(), "native_repetitions": len(single_native),
            "retained_points": len(original), "new_points": len(fresh_single), "curve_points": len(combined)})
        native = []
        original_raw = shared.read_json(directory / "retained/throughput_raw.json")
        original_rows = [{**row, "source_campaign": declaration["previous_directory"]}
                         for row in shared.read_csv(directory / "retained/throughput_summary.csv")]
        for engine in ENGINES:
            capacity = shared.aio_capacity(config)
            shared.write_json(directory / "plans" / f"{engine}.aio_capacity.json", capacity)
            plan, limited = shared.plan_throughput(config, engine, selected, capacity)
            availability.extend(limited)
            if plan:
                native.extend(campaign.native(engine, "throughput", plan))
            else:
                campaign.update("no_eligible_points", phase=engine + ".throughput",
                                reason="No new unfiltered control met a missing target")
            shared.write_json(directory / "supplement_throughput_raw.json", native)
            shared.write_json(directory / "throughput_raw.json", original_raw + native)
            rows, invalid = shared.aggregate_throughput(config, native, selected)
            merged = original_rows + [{**row, "source_campaign": str(directory)} for row in rows]
            shared.write_csv(directory / "throughput_summary.csv", THROUGHPUT_FIELDS, merged)
            shared.write_csv(directory / "availability.csv", shared.AVAILABILITY_FIELDS, availability + invalid)
        campaign.plot("complete")
        campaign.verify()
        index = shared.read_json(directory / "index_completion.json")
        shared.verify_records(index["artifacts"] + index["build_validation"]["protected"])
        for stage in ("single", "complete"):
            shared.validate_plot_publication(config, stage)
        artifacts = [
            "single_raw.json", "single_summary.csv", "single_completion.json", "throughput_raw.json",
            "supplement_throughput_raw.json", "throughput_summary.csv", "availability.csv",
            "operating_points.json", "supplement.json", "plot.single.completion.json",
            "plot.complete.completion.json",
        ]
        artifacts.extend(str(path.relative_to(directory)) for subdirectory in ("native", "plans", "logs")
                         for path in sorted((directory / subdirectory).rglob("*")) if path.is_file())
        shared.write_json(directory / "completion.json", {
            "state": "completed", "at_utc": shared.utc_now(), "mode": MODE,
            "single_thread_points": len(combined), "new_single_repetitions": len(single_native),
            "throughput_points": len(merged), "throughput_native_repetitions": len(original_raw) + len(native),
            "new_throughput_repetitions": len(native), "retained_throughput_repetitions": len(original_raw),
            "limitations": availability + invalid, "all_protected_inputs_unchanged": True,
            "scope": "Same-index unfiltered search-parameter supplement; unchanged shared-host limits",
            "artifacts": [shared.identity(directory / name, True) for name in artifacts]})
        campaign.update("completed", completion=str(directory / "completion.json"))
    except BaseException as error:
        shared.write_json(directory / "failure.json", {
            "state": "failed", "error": repr(error), "at_utc": shared.utc_now(), "traceback": traceback.format_exc()})
        campaign.update("failed", error=repr(error))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="stage", required=True)
    preparation = commands.add_parser("prepare")
    preparation.add_argument("--config", required=True, type=Path)
    preparation.add_argument("--previous-campaign", required=True, type=Path)
    execution = commands.add_parser("run")
    execution.add_argument("--directory", required=True, type=Path)
    arguments = parser.parse_args()
    shared.reject_benchmark_environment()
    if arguments.stage == "prepare":
        print(prepare(shared.load_profile(arguments.config), arguments.previous_campaign.resolve(strict=True)),
              flush=True)
    else:
        def interrupted(signum, _frame):
            raise InterruptedError(f"Supplement interrupted by signal {signum}")
        signal.signal(signal.SIGTERM, interrupted)
        run(arguments.directory.resolve(strict=True))


if __name__ == "__main__":
    main()
