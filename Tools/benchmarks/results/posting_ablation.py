#!/usr/bin/env python3
"""Portable global/limited/full posting ablations on one immutable local index."""
import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np

import native_results as native
import update_results as store
from build_r90_timing import sources
from r90_timing import SCENARIOS, utc, verify_seal, write_ini
from ssd_label_distribution import prospective

require, read, save = native.require, native.read_json, native.save
STUDY, VARIANT = "ablation-posting-path", "global-limited-full"
MODES = ("global", "limited", "full")
HERE = Path(__file__).resolve().parent
PLOT = HERE / "plot_posting_ablation.R"
PLOT_SELECTIVITY = dict(zip(SCENARIOS, ("49.829", "19.848", "10.053", "4.993", "1.001", "0.100")))


def status(root, state, **fields):
    value = dict(state=state, utc=utc(), pid=os.getpid(), **fields)
    store.atomic_json(root / "status.json", value)
    print(json.dumps(value), flush=True)


def probe_grid(text):
    values = text.split(",")
    require(all(v.strip().isdecimal() for v in values), "Use comma-separated integer nprobes")
    result = [int(v) for v in values]
    require(len(result) >= 2 and result == sorted(set(result)) and
            100 <= result[0] <= result[-1] < 2**31, "Invalid top100 nprobe grid")
    return result


def load_config(config):
    config = native.absolute(str(config))
    values = native.read_config(config)
    keys = {"directory", "datadirectory", "referenceexperiment", "pilotnprobe", "nprobegrid",
            "pilottargetrecall", "minimummaxnprobe", "minimumavailablememorygib",
            "minimumfreediskgib", "minimumaioevents"}
    require(set(values) == {"ablation"} and set(values["ablation"]) == keys,
            "Ablation must declare exactly: " + ", ".join(sorted(keys)))
    policy = values["ablation"]
    root, registry, reference_path = (native.absolute(policy[key]) for key in
                                     ("directory", "datadirectory", "referenceexperiment"))
    reference = native.load_plan(reference_path)
    require(reference["policy"]["dataset"] == "SIFT1B", "This six-scenario workflow requires SIFT1B")
    inputs = native.load_inputs(reference)
    require(inputs["workload"]["query_count"] == 1000 and inputs["workload"]["topk"] == 100 and
            {case["scenario"] for case in reference["cases"]} == set(SCENARIOS),
            "Provide all six top100 scenarios with the complete 1000-query cohort")
    require(all(f"{100 * inputs['workload']['truth'][s]['selectivity']:.3f}" == PLOT_SELECTIVITY[s]
                for s in SCENARIOS), "Truth selectivities do not match this SIFT1B plot template")
    templates = {}
    for case in reference["cases"]:
        scenario, search = case["scenario"], case["profile"]["searchssdindex"]
        require(search["enablepostingnavigation"] == "true" and
                search.get("globalpostingsearch", "false") == "false",
                "Reference profiles must describe the full hierarchical method")
        require({"postingnavigationwidth", "postingmatchratepercent", "postingmatchwindow"} <= search.keys(),
                "Reference profiles must declare all native posting controls")
        require(scenario not in templates or templates[scenario] == str(case["config"]),
                "Select exactly one reference configuration per scenario; do not pool settings")
        templates[scenario] = str(case["config"])
    pilot, grid = probe_grid(policy["pilotnprobe"]), probe_grid(policy["nprobegrid"])
    minimum = int(policy["minimummaxnprobe"])
    target = float(policy["pilottargetrecall"])
    require(set(pilot) <= set(grid) and pilot[0] == grid[0] and pilot[-1] == grid[-1] and
            minimum in grid and minimum >= grid[1] and 0 < target <= 1,
            "Pilot must span the declared grid; minimum cap must retain at least two points")
    reserves = {key: int(policy[key]) for key in
                ("minimumavailablememorygib", "minimumfreediskgib", "minimumaioevents")}
    require(all(value > 0 for value in reserves.values()), "Resource reserves must be positive")
    for other in (registry, reference["root"], reference["index"]):
        require(root != other and root not in other.parents and other not in root.parents,
                "Campaign, registry, reference output and immutable index must not overlap")
    for other in (reference["root"], reference["index"]):
        require(registry != other and registry not in other.parents and other not in registry.parents,
                "Registry output overlaps the reference output or immutable index")
    protected = {config, reference_path, reference["batch"], reference["binary"],
                 *map(Path, inputs["required"]), *map(Path, templates.values())}
    require(all(root != path and root not in path.parents for path in protected),
            "Campaign output overlaps a protected input")
    require(all(registry != path and registry not in path.parents for path in protected),
            "Registry output overlaps a protected input")
    plan = dict(schema_version=1, root=str(root), registry=str(registry), reference=str(reference_path),
        index=str(reference["index"]), binary=str(reference["binary"]), templates=templates,
        workloads=str(reference["workloads"]), reference_policy=dict(reference["policy"]),
        pilot_probes=pilot, grid=grid, minimum_max_nprobe=minimum, pilot_target_recall=target,
        reserves=reserves, modes=list(MODES), scenarios=list(SCENARIOS))
    return plan, inputs, protected


def prepare_pilot_truth(root, inputs):
    folder = root / "pilot-truth"
    folder.mkdir()
    workload = copy.deepcopy(inputs["workload"])
    workload.update(query_count=32, scenarios=list(SCENARIOS), protected={}, native_predicates={}, truth={},
        subset_provenance=dict(kind="first32-of-accepted-input-cohort", new_exhaustive_scan=False))
    queries = folder / "queries.npy"
    np.save(queries, inputs["queries"][:32], allow_pickle=False)
    workload["queries"] = str(queries)
    workload["protected"][str(queries)] = store.file_sha(queries)
    for scenario in SCENARIOS:
        kind, payload = native.native_predicate(inputs["predicates"][scenario],
                                               tuple(workload["column_types"]), 32)
        predicate = folder / f"{scenario}-predicate.npy"
        if kind != "empty":
            np.save(predicate, payload, allow_pickle=False)
            workload["protected"][str(predicate)] = store.file_sha(predicate)
        workload["native_predicates"][scenario] = dict(kind=kind, file=str(predicate) if kind != "empty" else "")
        truth = copy.deepcopy(inputs["workload"]["truth"][scenario])
        for field in ("ids", "distances"):
            path = folder / f"{scenario}-{field}.npy"
            np.save(path, np.load(truth[field], allow_pickle=False)[:32], allow_pickle=False)
            truth[field], truth[field + "_sha256"] = str(path), store.file_sha(path)
        workload["truth"][scenario] = truth
    save(folder / "workloads.json", workload)
    save(folder / "completion.json", dict(state="complete",
        workloads_sha256=store.file_sha(folder / "workloads.json"),
        source_workload_sha256=store.digest(inputs["workload"]),
        provenance=workload["subset_provenance"]))


def write_experiment(plan, phase, grids):
    require(phase in ("pilot", "measurement"), "Unknown campaign phase")
    root = Path(plan["root"])
    count, repeats = (32, 1) if phase == "pilot" else (1000, 3)
    workload_path = root / "pilot-truth/workloads.json" if phase == "pilot" else Path(plan["workloads"])
    workload = read(workload_path)
    configs = root / f"{phase}-configs"
    configs.mkdir()
    paths = {}
    for scenario in SCENARIOS:
        for mode in MODES:
            profile = native.read_config(plan["templates"][scenario])
            probes = grids[f"{scenario}/{mode}"]
            profile["benchmark"].update(queries=workload["queries"], maxqueries=str(count), warmup=str(count),
                predicate=workload["native_predicates"][scenario]["kind"],
                predicatefile=workload["native_predicates"][scenario]["file"],
                phasetiming="false", steptiming="false")
            profile["searchssdindex"].update(internalresultnum=str(probes[0]),
                globalpostingsearch=str(mode == "global").lower(),
                enablepostingnavigation=str(mode == "full").lower())
            profile["searchsweep"] = dict(nprobe=",".join(map(str, probes)))
            path = configs / f"{scenario}-{mode}.ini"
            write_ini(path, profile)
            paths[scenario, mode] = path
    ordered = list(paths)
    sequence = [(repeat, scenario, mode) for repeat in range(1, repeats + 1)
                for scenario, mode in (ordered if repeat % 2 else list(reversed(ordered)))]
    batch = {"Batch": dict(CaseCount=str(len(sequence)), WarmupPolicy="once_per_mode_predicate")}
    policy = dict(plan["reference_policy"])
    policy.update(directory=str(root / phase), batch=str(configs / "batch.ini"),
        workloads=str(workload_path), study=STUDY if phase == "measurement" else STUDY + "-pilot",
        variant=VARIANT, repeats=str(repeats))
    experiment = {"Experiment": policy}
    for ordinal, (repeat, scenario, mode) in enumerate(sequence, 1):
        name = f"Case{ordinal}"
        batch[name] = dict(Config=str(paths[scenario, mode]),
                          OutputDirectory=str(root / phase / f"{scenario}-{mode}-r{repeat}"))
        experiment[name] = dict(Scenario=scenario, Repetition=str(repeat))
    write_ini(configs / "batch.ini", batch)
    config = configs / "experiment.ini"
    write_ini(config, experiment)
    native.load_inputs(native.load_plan(config))
    return config


def prepare(config):
    plan, inputs, protected = load_config(config)
    root = Path(plan["root"])
    require(not os.path.lexists(root), "Use a fresh campaign directory")
    root.mkdir(parents=True)
    try:
        save(root / "plan.json", plan)
        prepare_pilot_truth(root, inputs)
        write_experiment(plan, "pilot",
                         {f"{s}/{m}": plan["pilot_probes"] for s in SCENARIOS for m in MODES})
        protected.update(path for path in root.rglob("*") if path.is_file())
        protected.update(Path(module.__file__).resolve() for module in tuple(sys.modules.values())
                         if getattr(module, "__file__", None) and
                         native.BENCHMARKS in Path(module.__file__).resolve().parents)
        protected.update((Path(__file__).resolve(), PLOT))
        index = native.inventory(Path(plan["index"]))
        require(index, "Empty immutable index")
        save(root / "registration.json", dict(schema_version=1,
            files={str(path): native.frozen(path) for path in sorted(protected)},
            index=index, index_small={str(Path(plan["index"]) / relative): native.frozen(Path(plan["index"]) / relative)
                                     for relative, value in index.items() if value["size"] < 64 << 20},
            binary_sha256=store.file_sha(plan["binary"]),
            native_sources=sources(HERE.parents[2]),
            source_snapshot_policy="Preparation-time source/executable binding, not a compiler attestation"))
        status(root, "prepared", scenarios=6, modes=3, pilot_queries=32, measurement_queries=1000, repeats=3)
    except Exception as error:
        save(root / "failure.json", dict(phase="prepare", error=str(error), failed_at=utc()))
        raise


def verify(root):
    proof, plan = read(root / "registration.json"), read(root / "plan.json")
    native.verify_files(proof["files"])
    native.verify_files(proof["index_small"])
    require(store.file_sha(plan["binary"]) == proof["binary_sha256"], "Frozen native executable changed")
    require(all(store.file_sha(path) == digest for path, digest in proof["native_sources"].items()),
            "Frozen native sources changed")
    require(native.inventory(Path(plan["index"])) == proof["index"], "Immutable index changed")
    return plan


def preflight(root, plan):
    for key in os.environ:
        require(not key.startswith(("SPTAG_", "SPANN_", "OMP_")) and key != "LD_PRELOAD",
                "Unregistered benchmark environment: " + key)
    memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    values = dict(memory_available=int(memory["MemAvailable"].split()[0]) * 1024,
                  disk_free=shutil.disk_usage(root).free,
                  aio_available=int(Path("/proc/sys/fs/aio-max-nr").read_text()) -
                                int(Path("/proc/sys/fs/aio-nr").read_text()))
    reserve = plan["reserves"]
    require(values["memory_available"] >= (reserve["minimumavailablememorygib"] << 30) and
            values["disk_free"] >= (reserve["minimumfreediskgib"] << 30) and
            values["aio_available"] >= reserve["minimumaioevents"], "Memory/disk/AIO reserve unavailable")
    return values


def choose_grids(plan, events):
    groups = {}
    for event in events:
        point = event["point"]
        search = json.loads(point["parameters"])
        mode = "global" if search["GlobalPostingSearch"] == "true" else (
            "full" if search["EnablePostingNavigation"] == "true" else "limited")
        require(event["study"] == STUDY + "-pilot" and point["cohort_queries"] == 32 and
                point["repeats"] == 1, "Only accepted 32-query pilot points may select the grid")
        groups.setdefault(point["scenario"] + "/" + mode, []).append(point)
    require(set(groups) == {f"{s}/{m}" for s in SCENARIOS for m in MODES}, "Incomplete pilot modes/scenarios")
    grids, coverage = {}, {}
    for key, points in groups.items():
        require(sorted(p["search_value"] for p in points) == plan["pilot_probes"], "Incomplete pilot grid")
        successful = [p["search_value"] for p in points if p["recall"] >= plan["pilot_target_recall"]]
        cap = max(plan["minimum_max_nprobe"], min(successful)) if successful else plan["pilot_probes"][-1]
        grids[key] = [probe for probe in plan["grid"] if probe <= cap]
        coverage[key] = dict(cap=cap, pilot_target_reached=bool(successful),
                             pilot_recall_max=max(p["recall"] for p in points))
    return dict(grids=grids, coverage=coverage, full_points=sum(map(len, grids.values())))


def plot(root, events):
    measurement = root / "measurement"
    output = measurement / "plots"
    if not output.exists():
        subprocess.run(["Rscript", str(PLOT), str(measurement)], check=True)
    info = read(output / "metadata.json")
    require(info["points"] == len(events) and info["input_md5"] ==
            hashlib.md5((measurement / "events.json").read_bytes()).hexdigest(), "Plots are incomplete or stale")
    require(all((output / (name + ext)).stat().st_size > 1000
                for name in ("posting_ablation_recall_qps", "posting_ablation_recall_qps_80_100")
                for ext in (".png", ".pdf")), "Plot outputs missing")
    require((output / "recall_qps.csv").stat().st_size > 0, "Plot CSV missing")
    return output


def publication_events(plan, before, records, events):
    require(all(e["study"] == STUDY and e["variant"] == VARIANT and
                e["point"]["cohort_queries"] == 1000 and e["point"]["repeats"] == 3 for e in events),
            "Only complete formal ablations may publish")
    catalog = copy.deepcopy(before["catalog"]) if before else dict(
        datasets=["SIFT1B"], studies=[], expected_cells=[], selected_indexes=[
            dict(dataset="SIFT1B", algorithm="SPTAG", index=plan["reference_policy"]["indexversion"])])
    require(STUDY not in catalog.get("experiment_views", {}).get("main_studies", []),
            "Ablation study must not participate in the main curve selection")
    additions = list(events)
    if not any(study["id"] == STUDY for study in catalog["studies"]):
        catalog["studies"].append(dict(id=STUDY, description="Matched global/limited/full posting path"))
        catalog["expected_cells"].extend(dict(study=STUDY, variant=VARIANT, dataset="SIFT1B",
            algorithm="SPTAG", scenario=scenario, phase="single", threads=1) for scenario in SCENARIOS)
        additions.insert(0, dict(schema_version=1, kind="catalog", catalog=catalog,
            revision=1 + max((r["event"]["revision"] for r in records if r["event"]["kind"] == "catalog"), default=0),
            completed_at=events[0]["completed_at"], source=dict(description="Register independent posting-path ablations")))
    return additions


def _publish(root):
    plan = verify(root)
    native.verify_files(read(root / "measurement-registration.json")["files"])
    complete = root / "completion.json"
    if complete.exists():
        verify_seal(root, read(complete)["artifacts"],
                    {"publication-receipt.json", "measurement/plots/metadata.json",
                     "measurement/plots/recall_qps.csv"})
    events = native.accepted_events(root / "measurement-configs/experiment.ini")
    grid = read(root / "grid-selection.json")
    selected = choose_grids(plan, native.accepted_events(root / "pilot-configs/experiment.ini"))
    require(all(grid[key] == value for key, value in selected.items()) and
            grid["pilot_events_sha256"] == store.file_sha(root / "pilot/events.json"),
            "Formal grid no longer matches the accepted pilot")
    require(len(events) == grid["full_points"], "Formal grid is incomplete")
    plots = plot(root, events)
    registry = Path(plan["registry"])
    with store.locked(registry):
        records = store.load_history(registry / "history.jsonl")
        before = store.project(records) if records else None
        if records:
            require(before == read(registry / "current.json"), "Registry projection is stale; run refresh")
        else:
            require(not (registry / "current.json").exists(), "Current JSON exists without history")
    additions = publication_events(plan, before, records, events)
    after = prospective(records, additions)
    require({store.digest(e) for e in events} <= {p["event_id"] for p in after["points"]},
            "Selected-index, retirement or version policy excludes new ablations")
    if before:
        old_points = [p for p in before["points"] if (p["study"], p["variant"]) != (STUDY, VARIANT)]
        require(all(point in after["points"] for point in old_points) and
                before["tables"] == after["tables"] and
                before.get("experiments", {}).get("main") == after.get("experiments", {}).get("main") and
                before.get("retired_indexes") == after.get("retired_indexes") and
                all(cell in after["coverage"] for cell in before["coverage"]
                    if (cell.get("study"), cell.get("variant")) != (STUDY, VARIANT)),
                "Publication changes unrelated results")
    current = store.update(registry, additions, expected_history_head=before["history"]["head"] if before else "")
    require(current == after, "Publication differs from preservation preview")
    receipt = root / "publication-receipt.json"
    if not receipt.exists():
        save(receipt, dict(status="complete", current=str(registry / "current.json"), history=current["history"],
                           event_ids=[store.digest(e) for e in events], points=len(events), plots=str(plots)))
    if not (root / "completion.json").exists():
        save(root / "completion.json", dict(status="complete", completed_at=utc(), points=len(events),
            artifacts={str(path.relative_to(root)): store.file_sha(path)
                       for path in (receipt, *sorted(plots.iterdir())) if path.is_file()}))
    status(root, "published", points=len(events), current=str(registry / "current.json"), plots=str(plots))


def publish(root):
    try:
        _publish(root)
    except Exception as error:
        save(root / "publication-failure.json", dict(error=str(error), failed_at=utc()))
        status(root, "publication_failed", error=str(error))
        raise


def run(root):
    plan = verify(root)
    require(not (root / "started.json").exists() and not (root / "failure.json").exists(),
            "Fresh-only execution; preserve failed campaigns")
    save(root / "started.json", dict(started_at=utc(), pid=os.getpid(), **preflight(root, plan)))
    try:
        status(root, "pilot_running", native_log=str(root / "pilot/native.log"), queries=32)
        native.run(root / "pilot-configs/experiment.ini")
        selection = choose_grids(plan, native.accepted_events(root / "pilot-configs/experiment.ini"))
        save(root / "grid-selection.json", dict(**selection, selected_at=utc(),
            pilot_events_sha256=store.file_sha(root / "pilot/events.json")))
        config = write_experiment(plan, "measurement", selection["grids"])
        paths = [root / "grid-selection.json", *sorted(config.parent.glob("*.ini"))]
        save(root / "measurement-registration.json",
             dict(files={str(path): native.frozen(path) for path in paths}))
        verify(root)
        preflight(root, plan)
        status(root, "measurement_running", native_log=str(root / "measurement/native.log"),
               points=selection["full_points"], queries=1000, repetitions=3)
        native.run(config)
        status(root, "measurement_complete", points=selection["full_points"])
    except Exception as error:
        save(root / "failure.json", dict(error=str(error), failed_at=utc()))
        status(root, "failed", error=str(error))
        raise
    publish(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("prepare").add_argument("--config", type=Path, required=True)
    for action in ("run", "publish"):
        sub.add_parser(action).add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.config)
    else:
        root = args.directory.resolve(strict=True)
        with (root / "run.lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            run(root) if args.action == "run" else publish(root)


if __name__ == "__main__":
    main()
