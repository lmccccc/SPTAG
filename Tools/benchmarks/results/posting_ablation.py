#!/usr/bin/env python3
"""Portable global/limited/full posting ablations on one immutable local index."""
import argparse
from collections import Counter
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import native_results as native
import update_results as store
from build_r90_timing import sources
from r90_timing import SCENARIOS, utc, verify_seal, write_ini
from ssd_label_distribution import prospective

require, read, save = native.require, native.read_json, native.save
STUDY, VARIANT = "ablation-posting-path", "global-limited-full"
MODES = ("global", "limited", "full")
MODE_KEYS = {"globalpostingsearch", "enablepostingnavigation"}
PARAMETER_POLICY = "exact-reference-grid"
HERE = Path(__file__).resolve().parent
PLOT = HERE / "plot_posting_ablation.R"
PLOT_SELECTIVITY = dict(zip(SCENARIOS, ("49.829", "19.848", "10.053", "4.993", "1.001", "0.100")))


def status(root, state, **fields):
    value = dict(state=state, utc=utc(), pid=os.getpid(), **fields)
    store.atomic_json(root / "status.json", value)
    print(json.dumps(value), flush=True)


def controls(search):
    excluded = MODE_KEYS | {"internalresultnum", "isexecute", "buildssdindex",
                            "logphasetime", "logpathstats", "dumpheads"}
    return {native.SEARCH_NAMES[key]: value for key, value in search.items() if key not in excluded}


def load_config(config):
    config = native.absolute(str(config))
    values = native.read_config(config)
    keys = {"directory", "datadirectory", "referenceexperiment", "minimumavailablememorygib",
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
    templates, seen_configs, seen_points = [], set(), set()
    for case in reference["cases"]:
        scenario, search = case["scenario"], case["profile"]["searchssdindex"]
        require(search["enablepostingnavigation"] == "true" and
                search.get("globalpostingsearch", "false") == "false",
                "Reference profiles must describe the full hierarchical method")
        require({"postingnavigationwidth", "postingmatchratepercent", "postingmatchwindow"} <= search.keys(),
                "Reference profiles must declare all native posting controls")
        key = scenario, str(case["config"])
        if key in seen_configs:
            continue
        seen_configs.add(key)
        parameters = controls(search)
        for probe in case["probes"]:
            point = scenario, store.digest(parameters), probe
            require(point not in seen_points, "Duplicate reference parameter point under different configs")
            seen_points.add(point)
        templates.append(dict(id=f"p{len(templates):03d}-{scenario}", scenario=scenario,
            config=str(case["config"]), probes=list(case["probes"]), parameters=parameters))
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
                 *map(Path, inputs["required"]), *(Path(t["config"]) for t in templates)}
    require(all(root != path and root not in path.parents for path in protected),
            "Campaign output overlaps a protected input")
    require(all(registry != path and registry not in path.parents for path in protected),
            "Registry output overlaps a protected input")
    plan = dict(schema_version=2, parameter_policy=PARAMETER_POLICY,
        root=str(root), registry=str(registry), reference=str(reference_path),
        index=str(reference["index"]), binary=str(reference["binary"]), templates=templates,
        workloads=str(reference["workloads"]), reference_policy=dict(reference["policy"]),
        reference_points=len(seen_points), full_points=len(MODES) * len(seen_points),
        reserves=reserves, modes=list(MODES), scenarios=list(SCENARIOS))
    return plan, inputs, protected


def write_experiment(plan):
    root = Path(plan["root"])
    configs = root / "measurement-configs"
    configs.mkdir()
    profiles = []
    for template in plan["templates"]:
        for mode in MODES:
            profile = native.read_config(template["config"])
            profile["searchssdindex"].update(globalpostingsearch=str(mode == "global").lower(),
                enablepostingnavigation=str(mode == "full").lower())
            name = f"{template['id']}-{mode}"
            path = configs / f"{name}.ini"
            write_ini(path, profile)
            profiles.append(dict(name=name, path=path, scenario=template["scenario"]))
    sequence = [(repeat, profile) for repeat in (1, 2, 3)
                for profile in (profiles if repeat % 2 else list(reversed(profiles)))]
    batch = {"Batch": dict(CaseCount=str(len(sequence)), WarmupPolicy="once_per_mode_predicate")}
    policy = dict(plan["reference_policy"])
    policy.update(directory=str(root / "measurement"), batch=str(configs / "batch.ini"),
                  study=STUDY, variant=VARIANT, repeats="3")
    experiment = {"Experiment": policy}
    for ordinal, (repeat, profile) in enumerate(sequence, 1):
        name = f"Case{ordinal}"
        batch[name] = dict(Config=str(profile["path"]),
            OutputDirectory=str(root / "measurement" / f"{profile['name']}-r{repeat}"))
        experiment[name] = dict(Scenario=profile["scenario"], Repetition=str(repeat))
    write_ini(configs / "batch.ini", batch)
    config = configs / "experiment.ini"
    write_ini(config, experiment)
    native.load_inputs(native.load_plan(config))
    return config


def prepare(config):
    plan, _, protected = load_config(config)
    root = Path(plan["root"])
    require(not os.path.lexists(root), "Use a fresh campaign directory")
    root.mkdir(parents=True)
    try:
        save(root / "plan.json", plan)
        write_experiment(plan)
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
        verify(root)
        status(root, "prepared", scenarios=6, modes=3, reference_points=plan["reference_points"],
               points=plan["full_points"], measurement_queries=1000, repeats=3, parameter_policy=PARAMETER_POLICY)
    except Exception as error:
        save(root / "failure.json", dict(phase="prepare", error=str(error), failed_at=utc()))
        raise


def verify(root):
    proof, plan = read(root / "registration.json"), read(root / "plan.json")
    require(plan["schema_version"] == 2 and plan["parameter_policy"] == PARAMETER_POLICY,
            "Use a fresh exact-reference-grid campaign; adaptive pilot campaigns are not compatible")
    require(not (root / "abort.json").exists(), "Aborted campaigns cannot run or publish")
    native.verify_files(proof["files"])
    native.verify_files(proof["index_small"])
    require(store.file_sha(plan["binary"]) == proof["binary_sha256"], "Frozen native executable changed")
    require(all(store.file_sha(path) == digest for path, digest in proof["native_sources"].items()),
            "Frozen native sources changed")
    require(native.inventory(Path(plan["index"])) == proof["index"], "Immutable index changed")
    for template in plan["templates"]:
        expected = native.read_config(template["config"])
        for mode in MODES:
            path = root / "measurement-configs" / f"{template['id']}-{mode}.ini"
            actual = native.read_config(path)
            require(actual["searchssdindex"]["globalpostingsearch"] == str(mode == "global").lower() and
                    actual["searchssdindex"]["enablepostingnavigation"] == str(mode == "full").lower(),
                    "Incorrect ablation mode flags")
            for key in MODE_KEYS:
                actual["searchssdindex"].pop(key)
                expected["searchssdindex"].pop(key, None)
            require(actual == expected, "Only the two ablation mode flags may differ from the reference")
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


def verify_reference_points(plan, events):
    expected = Counter()
    for template in plan["templates"]:
        for mode in MODES:
            parameters = dict(template["parameters"], GlobalPostingSearch=str(mode == "global").lower(),
                EnablePostingNavigation=str(mode == "full").lower(), WarmupPolicy="once_per_mode_predicate")
            for probe in template["probes"]:
                expected[template["scenario"], store.digest(parameters), probe] += 1
    actual = Counter()
    for event in events:
        point = event["point"]
        actual[point["scenario"], store.digest(json.loads(point["parameters"])), point["search_value"]] += 1
    require(actual == expected and len(events) == plan["full_points"],
            "Measurements must reproduce every reference parameter point in all three modes, with no additions or omissions")


def plot(root, events):
    measurement = root / "measurement"
    output = measurement / "plots"
    if not output.exists():
        subprocess.run(["Rscript", str(PLOT), str(measurement)], check=True)
    info = read(output / "metadata.json")
    require(info["points"] == len(events) and info["matched_parameter_grids"] and info["input_md5"] ==
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
    complete = root / "completion.json"
    if complete.exists():
        verify_seal(root, read(complete)["artifacts"],
                    {"publication-receipt.json", "measurement/plots/metadata.json",
                     "measurement/plots/recall_qps.csv"})
    events = native.accepted_events(root / "measurement-configs/experiment.ini")
    verify_reference_points(plan, events)
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
        status(root, "measurement_running", native_log=str(root / "measurement/native.log"),
               points=plan["full_points"], queries=1000, repetitions=3, parameter_policy=PARAMETER_POLICY)
        native.run(root / "measurement-configs/experiment.ini")
        status(root, "measurement_complete", points=plan["full_points"])
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
