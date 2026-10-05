"""Extend all three top100 frontiers with diagnosed, shared native search profiles."""

import argparse
import copy
import fcntl
import json
from pathlib import Path
import shutil
import statistics
import subprocess
from types import SimpleNamespace

import diagnose_sift1b_medium as diagnosis
import run_sift1b_top100 as shared
from native_input_io import read_config
from official_benchmark_config import read_json, require, sha256_file, write_json


def read_cases(batch_path, root, reference):
    batch = read_config(batch_path)
    require(batch["batch"]["warmuppolicy"] == "once", "Normal extension requires once warmup")
    count = int(batch["batch"]["casecount"])
    require(set(batch) == {"batch"} | {f"case{i}" for i in range(1, count + 1)}, "Unexpected batch sections")
    original = read_json(reference.root / "SPTAG.raw.json")
    templates = {
        scenario: read_config(next(p["config"] for p in original
                                   if p["scenario"] == scenario and p["setting"] == "base"))
        for scenario in shared.SCENARIOS
    }
    cases, outputs = [], set()
    profiles = {scenario: {} for scenario in shared.SCENARIOS}
    for number in range(1, count + 1):
        item = batch[f"case{number}"]
        require(set(item) == {"config", "outputdirectory"}, "Unexpected case fields")
        path, output = Path(item["config"]), Path(item["outputdirectory"])
        require(path.is_absolute() and output.is_absolute() and output.parent == root / "native/SPTAG" and
                output not in outputs and not output.exists(), "Invalid or existing native output")
        outputs.add(output)
        native = read_config(path)
        search, inputs = native["searchssdindex"], native["benchmark"]
        matches = [scenario for scenario, template in templates.items()
                   if inputs["predicatefile"] == template["benchmark"]["predicatefile"]]
        require(len(matches) == 1, "Original scenario predicate required")
        scenario = matches[0]
        template = templates[scenario]
        require(inputs == template["benchmark"] and inputs["maxqueries"] == inputs["warmup"] == "1000",
                "Normal measurement must retain the full original query cohort and index")
        mutable = {"maxcheck", "internalresultnum"}
        require({k: v for k, v in search.items() if k not in mutable} ==
                {k: v for k, v in template["searchssdindex"].items() if k not in mutable},
                "Only the diagnosed graph budget and nprobe may change")
        suffix = "_" + scenario
        require(output.name.endswith(suffix), "Output scenario differs")
        repeat, setting = output.name[:-len(suffix)].split("_", 1)
        require(repeat in ("r1", "r2"), "Exactly two ordinary repetitions required")
        probes = json.loads(native["searchsweep"]["nprobe"])
        require(probes and probes == sorted(set(probes)) and min(probes) >= 100, "Invalid top100 nprobe sweep")
        signature = (tuple(probes), tuple(sorted(search.items())))
        if setting == "control":
            require(scenario == "broad_tag" and probes == [100] and search == template["searchssdindex"],
                    "Control must preserve the original first-case warmup configuration")
        else:
            require(setting == "graph" + search["maxcheck"] and
                    int(search["maxcheck"]) > int(template["searchssdindex"]["maxcheck"]),
                    "Incorrect candidate profile label or graph budget")
            key = (int(repeat[1]), setting)
            require(key not in profiles[scenario], "Duplicate scenario/profile/repetition")
            profiles[scenario][key] = signature
        cases.append(dict(id=f"Case{number}", config=str(path), output=str(output), scenario=scenario,
                          setting=setting, probes=probes, repeat=int(repeat[1])))
    require(profiles["broad_tag"] and all(profiles[s] == profiles["broad_tag"] for s in shared.SCENARIOS),
            "Candidate parameter grids must be identical across all three scenarios")
    for (repeat, setting), signature in profiles["broad_tag"].items():
        require(profiles["broad_tag"].get((3 - repeat, setting)) == signature, "Missing matching repetition")
    controls = [case for case in cases if case["setting"] == "control"]
    require(len(controls) == 2 and controls[0] == cases[0] and controls[1] == cases[-1] and
            [case["repeat"] for case in controls] == [1, 2], "Controls must bracket the candidate measurements")
    first = [(c["scenario"], c["setting"], c["probes"]) for c in cases if c["repeat"] == 1]
    second = [(c["scenario"], c["setting"], c["probes"]) for c in cases if c["repeat"] == 2]
    require(first == list(reversed(second)), "Second repetition must reverse case order")
    return cases


def combine(reference_points, fresh_points):
    controls = [p for p in fresh_points if p["setting"] == "control"]
    candidates = [p for p in fresh_points if p["setting"] != "control"]
    require(len(controls) == 2 and {p["repeat"] for p in controls} == {1, 2}, "Incomplete controls")
    original = [p for p in reference_points if p["engine"] == "SPTAG" and p["scenario"] == "broad_tag" and
                p["setting"] == "base" and p["search_value"] == 100]
    require(len(original) == 2, "Missing original warmup/control point")
    require(all(p["payload_hashes"] == original[0]["payload_hashes"] for p in controls),
            "Control IDs/distances/work changed")
    old_keys = {(p["engine"], p["scenario"], p["setting"], p["search_value"]) for p in reference_points}
    require(not any((p["engine"], p["scenario"], p["setting"], p["search_value"]) in old_keys for p in candidates),
            "Do not cherry-pick repeated measurements of an existing configuration")
    control = dict(
        original_qps=statistics.median(p["qps"] for p in original),
        fresh_qps=statistics.median(p["qps"] for p in controls),
        identical_ids_distances_work=True, included_in_frontier=False)
    control["qps_ratio"] = control["fresh_qps"] / control["original_qps"]
    all_points = reference_points + candidates
    rows = shared.aggregate(all_points)
    for row in rows:
        point = next(p for p in all_points if (p["engine"], p["scenario"], p["setting"], p["search_value"]) ==
                     (row["engine"], row["scenario"], row["setting"], row["search_value"]))
        for field in ("max_check", "posting_additional_max_check", "posting_navigation_width"):
            row[field] = point[field] if row["engine"] == "SPTAG" else None
    return all_points, rows, control


def run(path):
    experiment = read_config(path)["experiment"]
    reference = shared.shared.Profile(Path(experiment["referenceconfig"]))
    root = Path(experiment["outputdirectory"])
    root.mkdir()
    lock = (root / "run.lock").open("x")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        write_json(root / "status.json", dict(state="preparing", started_utc=shared.shared.utc_now()))
        diagnostic = Path(experiment["diagnosticdirectory"])
        complete = read_json(diagnostic / "completion.json")
        require(complete["analysis_sha256"] == sha256_file(diagnostic / "analysis.json") and
                complete["registration_sha256"] == sha256_file(diagnostic / "registration.json") and
                complete["diagnostic_qps_excluded"], "Diagnostic acceptance changed")
        report = read_json(diagnostic / "analysis.json")
        require(all(p["normal_prefix_parity"] for p in report["points"] if p["setting"] in ("base", "wide")),
                "Diagnostic controls lack normal-client parity")
        proof = copy.deepcopy(read_json(diagnostic / "registration.json"))
        shared.verify(proof)
        batch = Path(experiment["nativebatch"])
        cases = read_cases(batch, root, reference)
        for case in cases:
            if case["setting"] != "control" and case["scenario"] == "medium_tag":
                require(all(any(p["scenario"] == "medium_tag" and p["setting"] == case["setting"] and
                                p["nprobe"] == probe and p["queries"] == 1000 for p in report["points"])
                            for probe in case["probes"]), "Candidate lacks a full-cohort diagnostic point")
        for directory in ("config", "logs", "native/SPTAG"):
            (root / directory).mkdir(parents=True)
        copied_batch = root / "config/spann_batch.ini"
        shutil.copyfile(batch, copied_batch)
        proof["cases"], proof["warmup_policy"] = cases, "once"
        proof["extension"] = dict(
            reference=str(reference.root), reference_completion_sha256=sha256_file(reference.root / "completion.json"),
            diagnostic=str(diagnostic), diagnostic_analysis_sha256=sha256_file(diagnostic / "analysis.json"),
            identical_scenario_parameter_grids=True, normal_core_rebuilt=False, index_rebuilt=False,
            reference_measurements_reused=True, control_included_in_frontier=False,
            interpretation="Post-hoc parameter exploration on the same cohort, not held-out evaluation.")
        files = [path.resolve(), batch, copied_batch, Path(__file__).resolve(), Path(experiment["plotscript"]),
                 diagnostic / "completion.json", diagnostic / "analysis.json", diagnostic / "registration.json"]
        files += [Path(c["config"]) for c in cases]
        proof["files"].update({str(p): diagnosis.frozen(p) for p in files})
        shared.save(root / "registration.json", proof)
        config = SimpleNamespace(root=root, section=reference.section, path_value=reference.path_value,
                                 affinity=reference.affinity)
        shared.invoke(config, proof, "SPTAG")
        fresh = shared.analyze_spann(config, proof)
        shared.save(root / "SPTAG.fresh.raw.json", fresh)
        original = [point for engine in shared.VERSIONS
                    for point in read_json(reference.root / (engine + ".raw.json"))]
        points, rows, control = combine(original, fresh)
        shared.save(root / "combined.raw.json", points)
        shared.table(root / "summary.csv", rows)
        shared.table(root / "pareto_frontiers.csv", [r for r in rows if r["algorithm_frontier"]])
        shared.save(root / "control.json", control)
        thresholds = []
        for scenario in shared.SCENARIOS:
            for target in (.8, .85, .9, .93, .95, .97, .99):
                best = {}
                for engine in shared.VERSIONS:
                    eligible = [r for r in rows if r["engine"] == engine and r["scenario"] == scenario and
                                r["recall"] >= target]
                    best[engine] = max(eligible, key=lambda r: r["qps"]) if eligible else None
                thresholds.append(dict(scenario=scenario, recall_target=target, best_observed=best))
        shared.save(root / "analysis.json", dict(
            topk=100, aggregate_points=len(rows), fresh_measurements=len(fresh), combined_measurements=len(points),
            thresholds=thresholds, control=control, sptag_warmup_policy="once",
            source_registration_sha256=sha256_file(root / "registration.json"), no_interpolation=True,
            reference_completion_sha256=sha256_file(reference.root / "completion.json"),
            caveats=["Same-cohort parameter exploration; not held-out evaluation.",
                     "Prior completed same-cohort measurements reused, not rerun.",
                     "SPTAG buffered IO, baselines direct IO; baseline per-point warmup retained.",
                     "Controls bracket the new points and are excluded from frontier selection.",
                     "No native algorithm or index rebuild."]))
        subprocess.run(["Rscript", experiment["plotscript"], str(root)], check=True)
        shared.verify(proof)
        artifacts = [p for p in root.iterdir() if p.is_file() and p.name not in ("run.lock", "status.json")]
        artifacts += list((root / "plots").iterdir())
        shared.save(root / "completion.json", dict(
            exit_code=0, completed_utc=shared.shared.utc_now(), fresh_measurements=len(fresh),
            combined_measurements=len(points), aggregate_points=len(rows),
            artifacts={str(p.relative_to(root)): sha256_file(p) for p in artifacts}))
        write_json(root / "status.json", dict(state="complete", completed_utc=shared.shared.utc_now()))
        print(f"Completed {len(rows)} combined top100 operating points; control QPS ratio {control['qps_ratio']:.4f}",
              flush=True)
    except Exception as error:
        write_json(root / "status.json", dict(state="failed", error=str(error)))
        shared.save(root / "failure.json", dict(error=str(error), failed_utc=shared.shared.utc_now()))
        raise
    finally:
        lock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    run(parser.parse_args().config)
