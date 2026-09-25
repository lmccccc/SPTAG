"""Versioned renderer exports; never run queries or fill missing grid points."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from prepare import DATA, HERE, OUTPUT, sha, write


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def persist(path, value):
    if path.exists():
        require(json.loads(path.read_text()) == value, "Existing renderer export changed: " + str(path))
    else:
        write(path, value)


def build_protocol(root=OUTPUT):
    registration = json.loads((root / "registration.json").read_text())
    cores = json.loads((root / "cores.json").read_text())
    inputs = json.loads((root / "input_hashes.json").read_text())
    settings = registration["protocol"]
    workload_path = DATA / "query/workloads.json"
    workload = next((item for item in inputs if Path(item["path"]) == workload_path), None)
    require(workload is not None, "Missing registered workload-definition hash")
    require(sha(workload_path) == workload["sha256"], "Workload definition changed")
    original = cores["original_authentication"]
    audit = cores["original_audit"]
    require(original["revision"] == "3552194536cb01dd70099e955a29e235a0cf4d2e" and
            len(original["verified"]) == 648 and not original["unavailable"] and
            len(audit["authenticated_original_files"]) == 7, "Original source authentication missing")
    require(all(original["verified"].get(path) == value
                for path, value in audit["authenticated_original_files"].items()),
            "Independent original source audit disagrees")
    original_fp = cores["cores"]["original"]["fingerprint"]
    current_fp = cores["cores"]["current"]["fingerprint"]
    require(original_fp != current_fp, "Original/current cores are not distinct")
    source = root / "snapshot/experiment/MatchedBench.cpp"
    require(sha(source) == cores["common_source_sha256"], "Frozen common benchmark source changed")
    body = source.read_text().split("const auto start = std::chrono::steady_clock::now();", 1)[1].split(
        "const auto finish = std::chrono::steady_clock::now();", 1)[0]
    require(hashlib.sha256(body.encode()).hexdigest() == cores["common_timed_body_sha256"] and
            not any(token in body for token in ("Shortcut", "capture", "#if", "Observation")),
            "Shared timed-body evidence missing")
    binaries = {}
    for name in ("original", "current"):
        value = sha(root / "snapshot" / f"matched-{name}")
        require(value == cores["cores"][name]["binary_sha256"], "Frozen common-harness binary changed")
        binaries[name] = value
    harness = {"source_sha256": cores["common_source_sha256"],
               "timed_body_sha256": cores["common_timed_body_sha256"],
               "native_array_parser_sha256": sha(root / "snapshot/experiment/NativeNProbeSweep.h"),
               "configuration_adapter_sha256": sha(root / "snapshot/experiment/FullHooks.h"),
               "linked_executable_sha256": binaries}
    input_identity = sorted((item["path"], item["sha256"]) for item in inputs)
    protocol = {
        "schema_version": 1,
        "index_fingerprint": registration["index_fingerprint"],
        "harness_fingerprint": digest(harness),
        "original_core_fingerprint": original_fp, "current_core_fingerprint": current_fp,
        "io_mode": settings["io"], "topk": settings["topk"], "maxcheck": settings["MaxCheck"],
        "hierarchy_maxcheck": settings["HierarchyMaxCheck"],
        "hierarchy_initial_probe_ratio": settings["HierarchyInitialProbeRatio"],
        "page_limit": settings["pages"], "query_count": settings["queries"],
        "measure_offset": settings["measure_offset"], "warmup": settings["warmup"],
        "query_threads": settings["query_threads"], "numa_cpu_node": settings["numa_cpu_node"],
        "numa_memory_node": settings["numa_memory_node"], "nprobe": settings["grid"],
        "repetitions": settings["repetitions"], "retained_ratio": settings["supplier_retained_ratio"],
        "minimum_physical_degree": settings["supplier_minimum_degree"],
        "original_reference_verified": True, "timed_body_shared": True,
        "cases": settings["cases"],
        "scenarios": [scenario for group in settings["stages"].values() for scenario in group],
        "sweep_execution": "single_load_nprobe_array",
        "nprobe_ini_api": "SearchSweep.NProbe",
        "dataset_input_manifest_sha256": digest(input_identity),
        "workload_definition_sha256": workload["sha256"],
        "native_configuration_manifest_sha256": digest(registration["config_hashes"]),
        "native_loader_sha256": registration["view_loader_sha256"],
        "source_registration_sha256": sha(root / "registration.json"),
        "source_search_protocol_sha256": registration["protocol_sha256"],
        "original_authentication_manifest_sha256": digest(original),
        "original_independent_audit_sha256": digest(audit),
        "harness_identity": harness,
        "original_reference_scope": "Authenticated Sep9 pre-supplier source; not claimed to be the exact "
                                    "historical blue-curve executable",
    }
    protocol["protocol_id"] = digest(protocol)
    return protocol


def convert_rows(rows, protocol):
    output = []
    for row in rows:
        require(row["protocol_fingerprint"] == protocol["source_search_protocol_sha256"] and
                row["io"] == protocol["io_mode"], "Mixed source benchmark protocol")
        require(row["index_fingerprint"] == protocol["index_fingerprint"], "Mixed index identity")
        require(row["common_timed_body_fingerprint"] ==
                protocol["harness_identity"]["timed_body_sha256"], "Mixed timed body")
        core_key = "original_core_fingerprint" if row["case"] == "h1_original" else "current_core_fingerprint"
        require(row["case"] in protocol["cases"] and row["core_fingerprint"] == protocol[core_key],
                "Wrong case/core identity")
        output.append({**row, "ordinary_ms": row["mean_latency_ms"],
            "ordinary_ms_runs": row["mean_latency_ms_runs"],
            "protocol_id": protocol["protocol_id"],
            "harness_fingerprint": protocol["harness_fingerprint"]})
    return output


def validate_rows(rows, protocol, partial_scenarios=False):
    require(bool(rows), "Empty renderer summary")
    seen = set()
    scenarios = set()
    for row in rows:
        scenario, case, probe = row["scenario"], row["case"], row["nprobe"]
        require(scenario in protocol["scenarios"] and case in protocol["cases"] and
                probe in protocol["nprobe"], "Unknown scenario/case/probe")
        key = scenario, case, probe
        require(key not in seen, "Duplicate renderer point")
        seen.add(key)
        scenarios.add(scenario)
        for field in ("protocol_id", "index_fingerprint", "harness_fingerprint"):
            require(row[field] == protocol[field], "Mixed renderer identity: " + field)
        core = "original_core_fingerprint" if case == "h1_original" else "current_core_fingerprint"
        require(row["core_fingerprint"] == protocol[core], "Wrong renderer core")
        require(row["sweep_execution"] == "single_load_nprobe_array", "Wrong native sweep API")
        runs = row["ordinary_ms_runs"]
        require(len(runs) == protocol["repetitions"] and
                all(math.isfinite(v) and v > 0 for v in runs), "Invalid ordinary repetitions")
        require(math.isfinite(row["ordinary_ms"]) and row["ordinary_ms"] > 0 and
                math.isclose(row["ordinary_ms"], sum(runs) / len(runs), rel_tol=1e-12),
                "Invalid ordinary mean")
        require(math.isfinite(row["qps"]) and
                math.isclose(row["qps"], 1000 / row["ordinary_ms"], rel_tol=1e-12), "QPS mismatch")
        require(math.isfinite(row["recall_at_10"]) and 0 <= row["recall_at_10"] <= 1,
                "Invalid final recall")
    expected = {(s, c, n) for s in scenarios for c in protocol["cases"] for n in protocol["nprobe"]}
    require(seen == expected, "Every included scenario requires all four cases and the full declared grid")
    require(partial_scenarios or scenarios == set(protocol["scenarios"]),
            "Missing scenarios require explicit partial-scenarios mode")


def export(root=OUTPUT, summary_name=None, diagnostic_smoke=False):
    protocol = build_protocol(root)
    destination = root / "renderer_contract"
    destination.mkdir(exist_ok=True)
    persist(destination / "protocol.json", protocol)
    if summary_name is None:
        return protocol
    source = root / summary_name
    rows = convert_rows(json.loads(source.read_text()), protocol)
    if diagnostic_smoke:
        require(summary_name == "summary.smoke.json" and all(r["nprobe"] == 24 for r in rows),
                "Diagnostic export is only the existing nprobe24 smoke")
        persist(destination / "summary.smoke.incomplete.json", rows)
        persist(destination / "smoke_status.json", {
            "renderable": False, "reason": "Included scenarios lack the full declared 11-probe grid",
            "protocol_id": protocol["protocol_id"], "source_summary_sha256": sha(source),
            "actual_points": len(rows), "declared_nprobe": protocol["nprobe"],
            "padding_or_interpolation": False})
    else:
        validate_rows(rows, protocol, partial_scenarios=summary_name != "summary.json")
        persist(destination / summary_name, rows)
    return protocol


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", choices=["summary.smoke.json", "summary.json",
        "summary.stage_unfilter_broad.json", "summary.stage_medium_extreme.json",
        "summary.stage_numeric_mixed.json"])
    parser.add_argument("--diagnostic-smoke", action="store_true")
    args = parser.parse_args()
    print(export(summary_name=args.summary, diagnostic_smoke=args.diagnostic_smoke)["protocol_id"])
