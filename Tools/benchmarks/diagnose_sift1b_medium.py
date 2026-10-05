"""Read-only, once-warmup native diagnosis of the top100 medium-label plateau."""

import argparse
import copy
import fcntl
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

import run_sift1b_top100 as shared
from native_input_io import read_config
from official_benchmark_config import read_json, require, sha256_file, write_json

REASONS = {
    0: "posting_not_attached", 1: "heads_sufficient", 2: "no_remaining_budget",
    3: "no_anchors", 5: "reachable_frontier_exhausted", 6: "checked_leaf_budget",
    7: "convergence_or_navigation_pruning",
}
MUTABLE = {"internalresultnum", "maxcheck", "postingadditionalmaxcheck",
           "postingnavigationwidth", "enablepostingnavigation"}


def frozen(path):
    return dict(identity=shared.identity(path), sha256=sha256_file(path))


def read_cases(batch_path, destination, reference):
    batch = read_config(batch_path)
    require(batch["batch"]["warmuppolicy"] == "once", "Diagnosis requires one batch warmup")
    count = int(batch["batch"]["casecount"])
    require(set(batch) == {"batch"} | {f"case{i}" for i in range(1, count + 1)}, "Unexpected batch sections")
    reference_points = read_json(reference.root / "SPTAG.raw.json")
    templates = {}
    for scenario in shared.SCENARIOS:
        point = next(p for p in reference_points if p["scenario"] == scenario and p["setting"] == "base")
        templates[scenario] = read_config(point["config"])
    cases, outputs = [], set()
    for number in range(1, count + 1):
        item = batch[f"case{number}"]
        require(set(item) == {"config", "outputdirectory"}, "Unexpected case fields")
        path, output = Path(item["config"]), Path(item["outputdirectory"])
        require(path.is_absolute() and output.is_absolute() and output.parent == destination / "native/SPTAG",
                "Case must use absolute INI and fresh campaign output paths")
        require(output not in outputs and not output.exists(), "Duplicate or existing native output")
        outputs.add(output)
        native = read_config(path)
        inputs, search = native["benchmark"], native["searchssdindex"]
        matches = [scenario for scenario, template in templates.items()
                   if inputs["predicatefile"] == template["benchmark"]["predicatefile"]]
        require(len(matches) == 1, "Case predicate does not match the original workload")
        scenario = matches[0]
        template = templates[scenario]
        require({k: v for k, v in inputs.items() if k not in ("maxqueries", "warmup")} ==
                {k: v for k, v in template["benchmark"].items() if k not in ("maxqueries", "warmup")},
                "Diagnostic inputs differ from the measured top100 workload")
        require({k: v for k, v in search.items() if k not in MUTABLE} ==
                {k: v for k, v in template["searchssdindex"].items() if k not in MUTABLE},
                "An unrelated search parameter changed")
        require(inputs["maxqueries"] in ("32", "1000") and inputs["maxqueries"] == inputs["warmup"],
                "Invalid diagnostic cohort; the native client requires 32 or 1000 equal warmup/measured queries")
        queries = int(inputs["maxqueries"])
        probes = json.loads(native["searchsweep"]["nprobe"])
        require(probes and probes == sorted(set(probes)) and min(probes) >= int(search["resultnum"]),
                "Invalid native top100 probe sweep")
        prefix, suffix = "r1_", "_" + scenario
        require(output.name.startswith(prefix) and output.name.endswith(suffix), "Invalid diagnostic case name")
        setting = output.name[len(prefix):-len(suffix)]
        cases.append(dict(id=f"Case{number}", config=str(path), output=str(output), scenario=scenario,
                          setting=setting, probes=probes, queries=queries, search=search))
    require(len({case["queries"] for case in cases}) == 1, "Mixed query cohorts in one batch")
    return cases


def analyze(destination, proof, reference, columns):
    require(len(columns) == 58 and len(set(columns)) == 58, "Expected navigation schema 7")
    events = [json.loads(line) for line in (destination / "native.log").read_text().splitlines()
              if line.startswith("{")]
    cases, query_count = proof["diagnostic_cases"], proof["diagnostic_cases"][0]["queries"]
    require(events[0]["event"] == "batch_begin" and events[0]["cases"] == len(cases) and
            events[0]["warmup_policy"] == "once" and events[1]["event"] == "batch_loaded" and
            events[1]["index_load_count"] == events[1]["query_corpus_load_count"] == 1,
            "Expected exactly one resident index and query corpus")
    require(events[2]["event"] == "batch_warmup_begin" and events[2]["queries"] == query_count and
            events[2]["case_id"] == cases[0]["id"] and events[2]["nprobe"] == cases[0]["probes"][0] and
            events[3]["event"] == "batch_warmup_end" and events[3]["completed_queries"] == query_count,
            "Incorrect one-time warmup")
    require(events[-1]["event"] == "batch_end" and events[-1]["completed_cases"] == len(cases) and
            events[-1]["completed_warmup_queries"] == query_count, "Incomplete diagnostic batch")
    base, original_queries, attrs = shared.load_inputs(reference)
    queries = original_queries[:query_count]
    reference_points = read_json(reference.root / "SPTAG.raw.json")
    points, position = [], 4
    for case in cases:
        begin = events[position]
        require(begin["event"] == "case_begin" and begin["case_id"] == case["id"] and
                begin["config"] == case["config"] and begin["output_directory"] == case["output"] and
                begin["warmup_queries"] == 0, "Unexpected diagnostic case")
        position += 1
        truth = np.load(reference.path_value("Scenario." + case["scenario"], "Truth"))[:query_count]
        for probe in case["probes"]:
            point = events[position]
            position += 1
            require(point["event"] == "point" and point["case_id"] == case["id"] and
                    point["config"] == case["config"] and point["output_directory"] == case["output"] and
                    point["diagnostic"] and not point["phase_timing"] and point["topk"] == 100 and
                    point["nprobe"] == probe and point["queries"] == point["measured_queries"] ==
                    point["replay_queries"] == query_count and point["warmup_queries"] == 0 and
                    point["warmup_policy"] == "once" and point["navigation_schema_version"] == 7 and
                    point["navigation_columns"] == len(columns), "Unexpected diagnostic point protocol")
            for field, key in (("max_check", "maxcheck"), ("posting_anchor_count", "postinganchorcount"),
                               ("posting_additional_max_check", "postingadditionalmaxcheck"),
                               ("posting_navigation_width", "postingnavigationwidth"),
                               ("search_posting_page_limit", "searchpostingpagelimit")):
                require(point[field] == int(case["search"][key]), f"Native parameter differs: {key}")
            require(point["mode"] == ("posting" if case["search"]["enablepostingnavigation"] == "true" else "graph"),
                    "Native posting enablement differs")
            folder = Path(case["output"]) / f"nprobe_{probe}"
            ids = np.fromfile(folder / "ids.i32", dtype="<i4").reshape(query_count, 100)
            distances = np.fromfile(folder / "dist.f32", dtype="<f4").reshape(query_count, 100)
            valid = shared.validate_exact(ids, distances, base, queries, attrs,
                                          reference.section("Scenario." + case["scenario"]).getint("Tag"), 100)
            require(np.all(distances[~valid] == np.finfo(np.float32).max / np.float32(10)),
                    "Invalid missing-result distance")
            work = np.fromfile(folder / "work.u64", dtype="<u8").reshape(query_count, 8)
            nav = np.fromfile(folder / "navigation.u64", dtype="<u8").reshape(query_count, len(columns))
            counters = {name: nav[:, i] for i, name in enumerate(columns)}
            before, after = counters["m_headBefore"], counters["m_headAfter"]
            require(np.all(before <= after) and np.all(after <= probe) and
                    np.all(counters["m_preservedHeads"] == before) and
                    np.all(counters["m_headTarget"] == probe) and
                    np.all(counters["m_postingActivations"] <= 1), "Native H1/supplement invariant violated")
            reasons, counts = np.unique(counters["m_supplementReason"], return_counts=True)
            require(set(reasons) <= set(REASONS), "Unknown native stop reason")
            parity = None
            if case["setting"] in ("base", "wide"):
                prior = next(p for p in reference_points if p["scenario"] == case["scenario"] and
                             p["setting"] == case["setting"] and p["nprobe"] == probe and p["repeat"] == 1)
                old = Path(prior["output_directory"]) / f"nprobe_{probe}"
                for name, dtype, width in (("ids.i32", "<i4", 100), ("dist.f32", "<f4", 100),
                                           ("work.u64", "<u8", 8)):
                    require(np.array_equal(np.fromfile(folder / name, dtype=dtype),
                                           np.fromfile(old / name, dtype=dtype, count=query_count * width)),
                            f"Diagnostic/normal payload differs: {case['scenario']} {name}")
                parity = True
            points.append(dict(
                scenario=case["scenario"], setting=case["setting"], nprobe=probe, queries=query_count,
                topk=100, search=case["search"],
                recall=sum(len(set(row[row >= 0]) & set(correct)) for row, correct in zip(ids, truth)) / ids.size,
                mean_postings=float(work[:, 0].mean()), mean_scanned_vectors=float(work[:, 1].mean()),
                head_underfilled_queries=int(np.count_nonzero(after < probe)),
                zero_supplement_leaf_queries=int(np.count_nonzero(counters["m_supplementLeaves"] == 0)),
                reasons={REASONS[int(reason)]: int(count) for reason, count in zip(reasons, counts)},
                means={name: float(values.mean()) for name, values in counters.items()},
                normal_prefix_parity=parity,
                payload_hashes={name: sha256_file(folder / name) for name in
                                ("ids.i32", "dist.f32", "work.u64", "graph_ids.i32", "graph_dist.f32")},
                output=str(folder)))
        require(events[position]["event"] == "case_end" and events[position]["case_id"] == case["id"],
                "Missing native case completion")
        position += 1
    require(position == len(events) - 1, "Unexpected extra native events")
    return dict(points=points, diagnostic_qps_excluded=True, columns=columns,
                stop_reasons=REASONS, total_warmup_queries=query_count,
                warning="Diagnostic observations only; use normal-client measurements for the performance frontier.")


def run(path):
    experiment = read_config(path)["experiment"]
    reference = shared.shared.Profile(Path(experiment["referenceconfig"]))
    destination = Path(experiment["outputdirectory"]) / "diagnostic"
    destination.mkdir()
    lock = (destination / "run.lock").open("x")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        write_json(destination / "status.json", dict(state="preparing", started_utc=shared.shared.utc_now()))
        parent = read_json(reference.root / "recovery/registration.json")
        shared.verify(parent)
        completion = read_json(reference.root / "completion.json")
        require(completion["exit_code"] == 0 and completion["ordinary_measurements"] == 168,
                "Reference comparison is incomplete")
        for name, digest in completion["artifacts"].items():
            require(sha256_file(reference.root / name) == digest, f"Reference artifact changed: {name}")
        smoke = read_json(experiment["smokeacceptance"])
        require(smoke["top10_ids_distances_work_identical"] and smoke["once_warmup_batch_verified"] and
                smoke["batch_result_and_work_parity"] and smoke["top100_shape"] == [32, 100],
                "Diagnostic client lacks native parity acceptance")
        batch_path, binary = Path(experiment["diagnosticbatch"]), Path(experiment["diagnosticbinary"])
        cases = read_cases(batch_path, destination, reference)
        (destination / "native/SPTAG").mkdir(parents=True)
        columns = read_json(experiment["diagnosticcolumns"])["columns"]
        proof = copy.deepcopy(parent)
        proof["diagnostic_cases"] = cases
        files = [path.resolve(), batch_path, binary, Path(__file__).resolve(),
                 reference.root / "completion.json", Path(experiment["diagnosticcolumns"]),
                 Path(experiment["smokeacceptance"])]
        files += [Path(case["config"]) for case in cases]
        files += [reference.root / name for name in completion["artifacts"]]
        core = Path(experiment["diagnosticcore"])
        cache = (core / "CMakeCache.txt").read_text().splitlines()
        require("SPTAG_QUERY_WORK_DIAGNOSTICS:BOOL=ON" in cache, "Expected diagnostic native core")
        library = Path(next(line.split("=", 1)[1] for line in cache if line.startswith("SPTAG_OUTPUT_DIRECTORY:PATH=")))
        files += list(library.glob("*.a")) + [core / "CMakeCache.txt",
                    core / "AnnService/CMakeFiles/nativeBench.dir/__/Wrappers/src/CoreInterface.cpp.o"]
        client = binary.parent.parent
        files += [client / "CMakeCache.txt", client / "CMakeFiles/nativeBench.dir/link.txt"]
        proof["files"].update({str(p): frozen(p) for p in files})
        proof["purpose"] = "Read-only top100 expansion diagnosis; unchanged V5 core/index; no measured QPS publication."
        shared.save(destination / "registration.json", proof)
        shared.verify(proof)
        command = reference.affinity("single") + [
            sys.executable, "-B", str(shared.POSTFILTER / "selectivity_common.py"),
            str(destination), str(binary), str(batch_path)]
        with (destination / "native.log").open("x") as stream:
            process = subprocess.Popen(command, cwd=destination, stdout=stream, stderr=subprocess.STDOUT)
            shared.save(destination / "execution.json", dict(pid=process.pid, command=command,
                        started_utc=shared.shared.utc_now()))
            write_json(destination / "status.json", dict(state="running", pid=process.pid,
                       started_utc=shared.shared.utc_now(), command=command))
            code = process.wait()
            require(code == 0, f"Diagnostic native child exit {code}; see {destination / 'native.log'}")
        shared.verify(proof)
        report = analyze(destination, proof, reference, columns)
        shared.save(destination / "analysis.json", report)
        shared.save(destination / "completion.json", dict(
            completed_utc=shared.shared.utc_now(), points=len(report["points"]), diagnostic_qps_excluded=True,
            registration_sha256=sha256_file(destination / "registration.json"),
            analysis_sha256=sha256_file(destination / "analysis.json")))
        write_json(destination / "status.json", dict(state="complete", completed_utc=shared.shared.utc_now()))
        print(json.dumps(report, indent=2), flush=True)
    except Exception as error:
        write_json(destination / "status.json", dict(state="failed", error=str(error)))
        shared.save(destination / "failure.json", dict(error=str(error), failed_utc=shared.shared.utc_now()))
        raise
    finally:
        lock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    run(parser.parse_args().config)
