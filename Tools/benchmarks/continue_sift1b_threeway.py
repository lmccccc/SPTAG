"""Continue only the unfinished throughput phases after an accepted isolated native repair."""

import argparse
import csv
import os
from pathlib import Path
import shutil
import signal
import traceback

import run_sift1b_threeway as shared


AUTHORIZATION = "isolate-pipeann-correctness-fix"
SHORT_AUTHORIZATION = "allow-native-short-results"
REMAINING = ("PipeANN", "Filtered_DiskANN")
REUSED_PHASES = (("Filtered_DiskANN", "single"), ("SPTAG_adaptive", "throughput"))
COPIED = (
    "single_raw.json", "single_summary.csv", "single_completion.json", "operating_points.json",
    "throughput_raw.json", "SPTAG_adaptive.throughput.validation.json",
    "SPTAG_adaptive.throughput.completion.json", "Filtered_DiskANN.single.validation.json",
    "Filtered_DiskANN.single.completion.json", "plot.single.completion.json",
)


def read_plan(path):
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        shared.require(reader.fieldnames == list(shared.PLAN_FIELDS), "Invalid retained ordinal plan")
        return [{key: value if key == "scenario" else int(value) for key, value in row.items()} for row in reader]


def config_delta(previous, current):
    shared.require(set(previous.parser.sections()) == set(current.parser.sections()),
                   "Continuation changed INI sections")
    changes = []
    short = shared.native_short_results(current, "PipeANN")
    if short:
        shared.require(not shared.native_short_results(previous, "PipeANN"),
                       "Native-count continuation requires its strict-protocol predecessor")
    for section in previous.parser.sections():
        old, new = previous.parser[section], current.parser[section]
        additions = {"allowshortresults"} if short and section == "pipeann" else set()
        shared.require(set(old) | additions == set(new), "Continuation changed INI keys")
        changes.extend({"section": section, "key": key, "previous": old[key], "current": new[key]}
                       for key in old if old[key] != new[key])
        changes.extend({"section": section, "key": key, "previous": None, "current": new[key]} for key in additions)
    allowed = {("run", "outputdirectory"), ("run", "prepareddirectory"),
               ("pipeann", "benchmarkbinary"), ("pipeann", "sourcedirectory")}
    if short:
        allowed.remove(("pipeann", "sourcedirectory"))
        allowed.add(("pipeann", "allowshortresults"))
    shared.require({(row["section"], row["key"]) for row in changes} == allowed,
                   "Continuation may change only output paths and the accepted PipeANN binary/source")
    return changes


def verify_acceptance(path, config):
    report = shared.read_json(path)
    if report.get("authorization") == SHORT_AUTHORIZATION:
        shared.require(shared.native_short_results(config, "PipeANN")
                       and report["schema_version"] == 1 and report["status"] == "accepted"
                       and report["native_core_rebuilt"] is False and report["search_controls_changed"] is False
                       and report["header_native_counts"] == [0, 3, 10]
                       and report["full_k_native_jobs"] == 15 and report["full_k_exact_capture_pairs"] == 30,
                       "Incomplete native-count protocol acceptance")
        shared.verify_records(report["protected"])
        core_path = Path(report["core_acceptance"]["path"])
        shared.authenticate_build_artifact(report["core_acceptance"], core_path)
        core = shared.read_json(core_path)
        verify_acceptance(core_path, shared.Profile(core["production_recheck"]["profile"]["path"]))
        binary = config.path_value("PipeANN", "BenchmarkBinary")
        build = shared.validate_native_build(config, "PipeANN", binary, binary.with_name(binary.name + ".build.json"))
        shared.require(build["result_policy"] is not None
                       and build["result_policy"]["binding"] == report["native_result_policy"]
                       and build["correctness"]["binding"] == core["native_correctness"] == report["native_correctness"]
                       and build["binary"] == report["native_build"]["binary"],
                       "Continuation does not use the accepted native-count client/core")
        return report
    shared.require(report["schema_version"] == 1 and report["status"] == "accepted"
                   and report["authorization"] == AUTHORIZATION and report["original_inputs_unchanged"] is True
                   and report["graph_rebuilt"] is False and report["search_controls_changed"] is False
                   and report["deterministic_cases"] == 24 and report["original_invalid_pairs"] == 30
                   and report["fixed_invalid_pairs"] == 0 and report["paired_native_jobs"] == 30
                   and report["paired_exact_captures"] == 60, "Incomplete authorized native repair acceptance")
    shared.verify_records(report["protected"])
    probe = report["production_recheck"]
    shared.require(probe["formal_benchmark_point"] is False and probe["exit_code"] == 0
                   and (probe["scenario"], probe["L"], probe["threads"], probe["repeats"],
                        probe["minimum_seconds"]) == ("broad_tag", 120, 2, 3, 30),
                   "The originally failing production control was not independently rechecked")
    diagnostic = shared.Profile(probe["profile"]["path"])
    for section in ("Dataset", "Throughput", "PipeANN", "Scenario.broad_tag"):
        shared.require(dict(diagnostic.section(section)) == dict(config.section(section)),
                       "Accepted production recheck differs from the continuation's native inputs")
    plan = read_plan(diagnostic.root / "plans/PipeANN.throughput.tsv")
    shared.require(plan == [
        {"scenario": "broad_tag", "control_index": config.controls("PipeANN").index(120),
         "thread_index": config.threads("throughput").index(2), "repeat": repeat} for repeat in range(1, 4)],
        "Production recheck did not use the original registered controls")
    shared.validate_native_completion(diagnostic, "PipeANN", "throughput", plan, probe["pid"])
    rows = shared.parse_results(diagnostic, "PipeANN", "throughput",
                                diagnostic.root / "logs/PipeANN.throughput.stdout.log", plan)
    shared.require(rows == probe["results"], "Accepted production results differ from raw native output")
    shared.validate_captures(diagnostic, rows)
    binary = config.path_value("PipeANN", "BenchmarkBinary")
    build = shared.validate_native_build(config, "PipeANN", binary, binary.with_name(binary.name + ".build.json"))
    shared.require(build["correctness"] is not None
                   and build["correctness"]["binding"] == report["native_correctness"]
                   and build["binary"] == report["native_build"]["binary"],
                   "Continuation does not use the accepted corrected native client")
    return report


def verify_previous(previous):
    root = previous.root
    shared.require(shared.read_json(root / "status.json")["state"] == "failed"
                   and "PipeANN.throughput" in shared.read_json(root / "failure.json")["error"]
                   and not (root / "completion.json").exists()
                   and not (root / "native/PipeANN/throughput/completion.json").exists()
                   and not (root / "logs/Filtered_DiskANN.throughput.command.json").exists(),
                   "Only the stopped PipeANN throughput failure may be continued")
    shared.require(shared.process_identity(shared.read_json(root / "run.lock")["pid"]) is None,
                   "Previous campaign controller is still alive")
    shared.Campaign(previous).verify()
    if (root / "continuation.json").exists():
        chain = shared.read_json(root / "continuation.json")
        shared.require(chain["authorization"] == AUTHORIZATION and chain["reused_single_jobs"] == 104
                       and chain["reused_spann_throughput_jobs"] == 390
                       and chain["reused_pipeann_throughput_jobs"] == 0
                       and Path(chain["previous_directory"]) != root,
                       "Unexpected predecessor continuation scope")
        origin = shared.load_profile(Path(chain["previous_directory"]) / "config/benchmark.ini")
        config_delta(origin, previous)
        shared.require(not (origin.root / "continuation.json").exists(), "Unexpected cyclic/nested predecessor")
        results, selections, availability = verify_previous(origin)
        for name in COPIED:
            shared.require(shared.sha256_file(root / name) == shared.sha256_file(origin.root / name),
                           "Retained completed-phase data changed in the predecessor")
        partial, evidence = verify_partial_pipeann(previous, selections)
        results["partial_pipeann"], results["partial_evidence"] = partial, evidence
        return results, selections, availability
    index = shared.read_json(root / "index_completion.json")
    shared.verify_records(index["artifacts"] + index["build_validation"]["protected"])
    shared.validate_guarded_build_completion(previous, previous.path_value("Run", "BuildDirectory"),
                                             index["completion"])
    combined = shared.read_csv(root / "single_summary.csv")
    selections, missing = shared.select_operating_points(previous, combined)
    shared.require(selections == shared.read_json(root / "operating_points.json"),
                   "Retained operating points changed after single-thread selection")
    results = {}
    for engine, phase in REUSED_PHASES:
        plan = read_plan(root / "plans" / f"{engine}.{phase}.tsv")
        expected = (shared.plan_single(previous) if phase == "single" else shared.plan_throughput(
            previous, engine, selections, shared.read_json(root / "plans" / f"{engine}.aio_capacity.json"))[0])
        shared.require(plan == expected, "Retained completed phase changed its registered plan")
        command = shared.read_json(root / "logs" / f"{engine}.{phase}.command.json")
        shared.require(command["argv"] == previous.affinity(phase) + [
            shared.read_json(root / "manifest.json")["binaries"][engine]["path"], str(previous.path), phase],
            "Retained native command changed its executable, INI or affinity")
        shared.validate_native_completion(previous, engine, phase, plan, command["pid"])
        raw = shared.parse_results(previous, engine, phase, root / "logs" / f"{engine}.{phase}.stdout.log", plan)
        shared.validate_captures(previous, raw)
        results[phase] = raw
    shared.require(results["single"] == shared.read_json(root / "single_raw.json")
                   and results["throughput"] == shared.read_json(root / "throughput_raw.json")
                   and len(results["single"]) == 104 and len(results["throughput"]) == 390,
                   "Retained results are incomplete or include unfinished PipeANN measurements")
    summary = shared.read_json(root / "single_completion.json")
    shared.require(summary["state"] == "completed" and summary["native_repetitions"] == 104
                   and summary["reused_historical_points"] == 100 and summary["curve_points"] == len(combined) == 152,
                   "Retained single-thread publication is incomplete")
    shared.require(shared.sha256_file(root / "single_summary.csv") ==
                   shared.sha256_file(root / "plot_inputs_single/single_summary.csv"),
                   "Retained single-thread table differs from the published snapshot")
    shared.validate_plot_publication(previous, "single")
    availability = [{
        "phase": "single", "scenario": "mixed_dnf", "engine": "Filtered_DiskANN",
        "status": "unsupported_predicate", "reason": "Original DiskANN supports single labels, not mixed numeric DNF",
        "recall_target": "", "threads": "",
    }, *missing]
    return results, selections, availability


def verify_partial_pipeann(config, selections):
    import numpy as np
    from threeway_native import build_pipeann_short_results

    root = config.root
    manifest = shared.read_json(root / "manifest.json")
    shared.require(not shared.native_short_results(config, "PipeANN")
                   and manifest["binaries"]["PipeANN"]["sha256"] == build_pipeann_short_results.BASE_CLIENT_SHA256,
                   "Only the verified strict full-K client prefix may be retained")
    capacity = shared.read_json(root / "plans/PipeANN.aio_capacity.json")
    plan, _ = shared.plan_throughput(config, "PipeANN", selections, capacity)
    shared.require(plan == read_plan(root / "plans/PipeANN.throughput.tsv"), "Partial phase changed its ordinal plan")
    command = shared.read_json(root / "logs/PipeANN.throughput.command.json")
    shared.require(command["argv"] == config.affinity("throughput") +
                   [manifest["binaries"]["PipeANN"]["path"], str(config.path), "throughput"]
                   and shared.process_identity(command["pid"]) is None, "Partial native process/command changed")
    ready = shared.validate_native_ready(config, "PipeANN", "throughput", plan, command["pid"])
    log = root / "logs/PipeANN.throughput.stdout.log"
    count = sum(line.startswith("THREEWAY_RESULT ") for line in log.read_text().splitlines())
    shared.require(0 < count < len(plan), "Expected a nonempty incomplete native phase")
    results = shared.parse_results(config, "PipeANN", "throughput", log, plan[:count])
    captures = shared.validate_captures(config, results)
    name, control, threads, repeat = shared.resolved_key(config, "PipeANN", "throughput", plan[count])
    shared.require((name, control, threads, repeat) == ("mixed_dnf", 35, 1, 1),
                   "A different failure requires separate diagnosis")
    native = root / "native/PipeANN/throughput"
    failure = shared.read_json(native / "failure.json")
    stem = f"{name}.L{control}.T{threads}.r{repeat}"
    shared.require(failure["error"] == "Native warmup produced invalid/underfilled results for " + stem,
                   "The partial native phase failed for an unrelated reason")
    ids_path = native / (stem + ".failed_warmup.ids.u32bin")
    distance_path = native / (stem + ".failed_warmup.distances.f32bin")
    ids, distances = shared.matrix(ids_path, "<u4"), shared.matrix(distance_path, "<f4")
    shared.require(ids.shape == distances.shape == (1000, 10), "Invalid saved failed-warmup extent")
    max_float = np.finfo(np.float32).max
    counts = np.sum(distances < max_float, axis=1)
    shared.require(np.any(counts < 10)
                   and np.all((np.arange(10)[None, :] >= counts[:, None]) == (distances == max_float)),
                   "Legacy warmup failure is not a native FLT_MAX tail")
    attrs = shared.open_attributes(config.path_value("Dataset", "Attributes"), 10**9, 2)
    base = shared.open_vectors(config.path_value("Dataset", "Vectors"), "UInt8", 128).data
    queries = shared.matrix(config.prepared / "query.u8bin", "u1")
    for q, returned in enumerate(counts):
        group = ids[q:q + 1, :returned]
        shared.validate_ids(config, name, group, attrs)
        delta = np.asarray(base[group[0]], dtype=np.int32) - queries[q].astype(np.int32)
        shared.require(np.array_equal(np.sum(delta * delta, axis=1, dtype=np.int64), distances[q, :returned]),
                       "Legacy native returned prefix contains real data corruption")
    return results, {
        "authorization": SHORT_AUTHORIZATION, "completed_jobs": count, "ready": ready, "captures": captures,
        "failure": shared.identity(native / "failure.json", True),
        "failed_warmup_ids": shared.identity(ids_path, True),
        "failed_warmup_distances": shared.identity(distance_path, True),
        "short_warmup_queries": int(np.count_nonzero(counts < 10)),
        "missing_warmup_neighbors": int(np.sum(10 - counts)),
        "failed_warmup_never_scored": True,
        "legacy_count_inference_scope": "Classification only; no synthetic count files or retroactive measurements",
    }


def prepare(config, previous_directory, acceptance):
    previous = shared.load_profile(previous_directory / "config/benchmark.ini")
    shared.require(previous.root == previous_directory, "Previous configuration belongs to another campaign")
    shared.require(not config.root.exists(), "Continuation output must be fresh")
    changes = config_delta(previous, config)
    accepted = verify_acceptance(acceptance, config)
    results, selections, availability = verify_previous(previous)
    shared.prepare(config)
    for name in COPIED:
        shutil.copy2(previous.root / name, config.root / name)
    for name in ("plots_single", "plot_inputs_single"):
        (config.root / name).symlink_to(previous.root / name, target_is_directory=True)
    partial = results.get("partial_pipeann", [])
    shared.require(not partial or accepted["authorization"] == SHORT_AUTHORIZATION,
                   "Partial-phase reuse requires the native-count protocol authorization")
    prefixes = {"PipeANN": len(partial)} if partial else {}
    shared.write_json(config.root / "reuse-state.json", {
        "availability": availability, "selections": selections, "retained_prefixes": prefixes})
    if partial:
        shared.write_json(config.root / "throughput_raw.json", results["throughput"] + partial)
        shared.write_json(config.root / "partial-pipeann-reuse.json",
                          {**results["partial_evidence"], "results": partial})
    rows, invalid_quality = shared.aggregate_throughput(config, results["throughput"], selections)
    shared.write_csv(config.root / "throughput_summary.csv", shared.THROUGHPUT_FIELDS, rows)
    shared.write_csv(config.root / "availability.csv", shared.AVAILABILITY_FIELDS, availability + invalid_quality)
    previous_manifest = shared.read_json(previous.root / "manifest.json")
    protected = [*previous_manifest["protected"], *previous_manifest["prepared_inputs"], *accepted["protected"]]
    protected.extend(shared.file_record(entry["path"], True) for entry in previous_manifest["frozen"])
    directories = ("native", "plans", "logs", "plots_single", "plot_inputs_single")
    previous_files = [path for path in previous.root.iterdir() if path.is_file()]
    previous_files.extend(path for name in directories for path in (previous.root / name).rglob("*") if path.is_file())
    protected.extend(shared.file_record(path, True) for path in sorted(set(previous_files)))
    protected.append(shared.file_record(acceptance, True))
    shared.verify_records(protected)
    continuation = {
        "schema_version": 1, "authorization": accepted["authorization"], "prepared_at_utc": shared.utc_now(),
        "previous_directory": str(previous.root), "previous_failure": shared.identity(previous.root / "failure.json", True),
        "acceptance": shared.identity(acceptance, True), "native_correctness": accepted["native_correctness"],
        "configuration_changes": changes, "reused_single_jobs": 104, "reused_spann_throughput_jobs": 390,
        "reused_pipeann_throughput_jobs": len(partial), "remaining_engines": list(REMAINING), "protected": protected,
        "policy": "Retain only independently verified completed jobs; never score a failed warmup or repair outputs. "
                  "Run the exact remaining registered PipeANN and DiskANN jobs serially, without budget substitution.",
    }
    shared.write_json(config.root / "continuation.json", continuation)
    registration = shared.read_json(config.root / "registration.json")
    registration["throughput_continuation"] = {
        "previous_directory": str(previous.root), "retained_engine": "SPTAG_adaptive",
        "retained_native_repetitions": 390, "restarted_engines": list(REMAINING),
    }
    registration["caption_note"] += (
        " Completed SPANN throughput and all single-thread points are retained from the prior campaign; "
        "only corrected PipeANN and DiskANN throughput are measured in this continuation.")
    if partial:
        registration["native_full_k_prefix_reuse"] = {
            "jobs": len(partial), "directory": str(previous.root), "schema_version": 1,
            "all_native_return_counts_equal_K": True,
        }
        registration["caption_note"] += (
            f" {len(partial)} completed full-K PipeANN jobs from the strict accounting client are retained; "
            "remaining jobs use explicit native-count schema 2 with the identical native library. "
            "Schema-1 returned-neighbor totals follow its verified full-K invariant; raw records stay unchanged.")
    shared.write_json(config.root / "registration.json", registration)
    manifest = shared.read_json(config.root / "manifest.json")
    manifest["protected"].extend(protected)
    manifest["frozen"].extend(shared.identity(config.root / name, True) for name in (
        "continuation.json", "reuse-state.json", "registration.json",
        *[name for name in COPIED if name != "throughput_raw.json"]))
    if partial:
        manifest["frozen"].append(shared.identity(config.root / "partial-pipeann-reuse.json", True))
    shared.write_json(config.root / "manifest.json", manifest)
    shared.validate_plot_publication(config, "single")
    return config.root


def run(directory):
    shared.require(Path(__file__).resolve() == directory / "code/continue_sift1b_threeway.py",
                   "Run the frozen continuation code")
    config = shared.load_profile(directory / "config/benchmark.ini")
    shared.require(config.root == directory and shared.read_json(directory / "status.json")["state"] == "prepared",
                   "Continuation already started; no implicit retry or overwrite")
    campaign = shared.Campaign(config)
    with (directory / "run.lock").open("x") as stream:
        stream.write(shared.json.dumps({"pid": os.getpid(), "started_at_utc": shared.utc_now()}) + "\n")
    try:
        campaign.verify()
        continuation = shared.read_json(directory / "continuation.json")
        short = continuation["authorization"] == SHORT_AUTHORIZATION
        reused = continuation["reused_pipeann_throughput_jobs"]
        shared.require(continuation["authorization"] in (AUTHORIZATION, SHORT_AUTHORIZATION)
                       and continuation["remaining_engines"] == list(REMAINING)
                       and (reused == 0 or (short and type(reused) is int and reused > 0)),
                       "Unapproved continuation scope")
        campaign.wait_for_build()
        campaign.verify()
        reuse = shared.read_json(directory / "reuse-state.json")
        native = shared.read_json(directory / "throughput_raw.json")
        shared.require(len(native) == 390 + reused
                       and all(row["engine"] == "SPTAG_adaptive" for row in native[:390])
                       and all(row["engine"] == "PipeANN" for row in native[390:])
                       and native[:390] == shared.read_json(
                           Path(continuation["previous_directory"]) / "throughput_raw.json"),
                       "Only complete SPANN measurements may be reused")
        if reused:
            partial = shared.read_json(directory / "partial-pipeann-reuse.json")
            shared.require(short and shared.native_short_results(config, "PipeANN")
                           and reuse["retained_prefixes"] == {"PipeANN": reused}
                           and native[390:] == partial["results"],
                           "Retained PipeANN records differ from the authenticated completed prefix")
        campaign.finish_throughput(native, reuse["selections"], reuse["availability"], REMAINING,
                                   reuse["retained_prefixes"])
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
    preparation.add_argument("--acceptance", required=True, type=Path)
    execution = commands.add_parser("run")
    execution.add_argument("--directory", required=True, type=Path)
    arguments = parser.parse_args()
    shared.reject_benchmark_environment()
    if arguments.stage == "prepare":
        print(prepare(shared.load_profile(arguments.config), arguments.previous_campaign.resolve(strict=True),
                      arguments.acceptance.resolve(strict=True)), flush=True)
    else:
        def interrupted(signum, _frame):
            raise InterruptedError(f"Throughput continuation interrupted by signal {signum}")
        signal.signal(signal.SIGTERM, interrupted)
        run(arguments.directory.resolve(strict=True))


if __name__ == "__main__":
    main()
