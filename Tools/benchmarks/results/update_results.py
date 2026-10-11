#!/usr/bin/env python3
"""Append schema-1 experiment events and atomically materialize current.json."""
import argparse
import configparser
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
SCHEMA = 1


def require(condition, message):
    if not condition:
        raise ValueError(message)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def file_sha(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            result.update(block)
    return result.hexdigest()


def timestamp(value):
    require(isinstance(value, str), "A completion timestamp is required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "Completion timestamps must include their timezone")
    return parsed.astimezone(timezone.utc)


def finite(value, name, positive=False):
    require(type(value) in (int, float) and math.isfinite(value), f"Invalid numeric {name}")
    require(not positive or value > 0, f"{name} must be positive")


def validate_event(event):
    require(isinstance(event, dict) and event.get("schema_version") == SCHEMA, "Unknown event schema")
    require(event.get("kind") in ("catalog", "measurement", "table", "index_retirement"), "Unknown event kind")
    timestamp(event["completed_at"])
    require(isinstance(event.get("source"), dict) and event["source"].get("description"), "Missing provenance")
    if event["kind"] == "catalog":
        require(type(event["revision"]) is int and event["revision"] > 0, "Invalid catalog revision")
        require(isinstance(event["catalog"]["studies"], list), "Missing paper study catalog")
        return
    if event["kind"] == "index_retirement":
        for field in ("dataset", "algorithm", "index", "replacement_index"):
            require(isinstance(event.get(field), str) and event[field], f"Missing retirement {field}")
        require(event["index"] != event["replacement_index"], "Retirement cannot replace itself")
        ids = event.get("replacement_event_ids")
        require(isinstance(ids, list) and ids and all(isinstance(i, str) and len(i) == 64 for i in ids)
                and len(set(ids)) == len(ids), "Distinct replacement event IDs required")
        cells = event.get("required_cells")
        require(isinstance(cells, list) and cells, "Explicit retirement replacement cells required")
        fields = {"study", "variant", "scenario", "topk", "cohort_queries", "phase", "threads", "expected_repeats"}
        for cell_spec in cells:
            require(isinstance(cell_spec, dict) and set(cell_spec) == fields, "Invalid replacement cell")
            for field in ("study", "variant", "scenario"):
                require(isinstance(cell_spec[field], str) and cell_spec[field], "Invalid replacement cell identity")
            require(cell_spec["phase"] in ("single", "throughput"), "Invalid replacement phase")
            for field in ("topk", "cohort_queries", "threads", "expected_repeats"):
                require(type(cell_spec[field]) is int and cell_spec[field] > 0, "Invalid replacement protocol")
        require(len({digest(c) for c in cells}) == len(cells), "Duplicate replacement cell")
        return
    for name in ("study", "variant", "dataset", "algorithm"):
        require(isinstance(event.get(name), str) and event[name], f"Missing experiment {name}")
    version = event["version"]
    require(type(version["order"]) is int and version["order"] >= 0 and
            isinstance(version["id"], str) and version["id"], "Explicit version ID/order required")
    if event["kind"] == "table":
        require(event.get("table_name") and isinstance(event["rows"], list) and event["rows"],
                "A table event needs named rows")
        require(event.get("status") in ("validated", "reported_not_revalidated"), "Unknown table status")
        require(all(isinstance(row, dict) for row in event["rows"]), "Table rows must be objects")
        accounting = {"io-scale": ("4KiB/query", [str(i) for i in range(1, 7)]),
                      "overhead": ("ms/query", ["Graph", "Hier.", "SSD", "Filter", "Top-$k$"]),
                      "space-cost": ("GiB", ["Graph", "Hier.", "Limited", "General"])}
        if event["study"] in accounting:
            unit, categories = accounting[event["study"]]
            cells = set()
            for row in event["rows"]:
                require(row.get("unit") == unit and row.get("category") in categories,
                        "Unknown accounting category/unit")
                finite(row["value"], "accounting value")
                require(row["value"] >= 0, "Negative accounting value")
                variant = row.get("variant", event["variant"])
                require(isinstance(variant, str) and variant, "Missing accounting variant")
                key = (row["category"], variant)
                require(key not in cells, "Duplicate accounting bar")
                cells.add(key)
        return
    point = event["point"]
    for name in ("scenario", "predicate", "phase", "parameters", "search_parameter", "curve_id"):
        require(isinstance(point.get(name), str) and point[name], f"Missing point {name}")
    require(point["phase"] in ("single", "throughput"), "Diagnostics/warmups are not paper performance")
    require(isinstance(json.loads(point["parameters"]), dict), "Parameters must encode an object")
    require(type(point["threads"]) is int and point["threads"] > 0, "Invalid query-worker count")
    if point["phase"] == "single":
        require(point["threads"] == 1, "Single-worker points require one thread")
    for name in ("search_value", "cohort_queries", "repeats", "expected_repeats"):
        require(type(point[name]) is int and point[name] > 0, f"Invalid {name}")
    finite(point["selectivity"], "selectivity")
    require(0 <= point["selectivity"] <= 1, "Selectivity outside [0,1]")
    for name in ("recall", "recall_min_observed", "recall_max_repeat"):
        finite(point[name], name)
    require(0 <= point["recall_min_observed"] <= point["recall"] <= point["recall_max_repeat"] <= 1,
            "Invalid recall interval")
    for name in ("qps", "qps_min", "qps_max"):
        finite(point[name], name, positive=True)
    require(point["qps_min"] <= point["qps"] <= point["qps_max"], "Invalid QPS interval")
    require(point["repeats"] <= point["expected_repeats"], "Too many repetitions")
    complete = point["repeats"] == point["expected_repeats"]
    require(type(point["complete_repeats_same_client"]) is bool and
            point["complete_repeats_same_client"] == complete, "Incorrect repetition-completeness flag")
    measurements = event["measurements"]
    require(len(measurements) == point["repeats"], "Every repetition must be retained")
    repeat_ids = []
    recalls, qps = [], []
    for measurement in measurements:
        require(not measurement.get("diagnostic", False) and not measurement.get("phase_timing", False),
                "Instrumented QPS is forbidden")
        require(measurement.get("stage", "measured") == "measured", "Warmup record is not a measurement")
        for field in ("invalid_queries", "invalid_ids", "duplicate_ids", "nonmatching_ids",
                      "nonfinite_distances", "query_exceptions"):
            require(measurement.get(field, 0) == 0, f"Native validation failed: {field}")
        finite(measurement["recall"], "repetition recall")
        finite(measurement["qps"], "repetition QPS", positive=True)
        require(0 <= measurement["recall"] <= 1, "Invalid repetition recall")
        require(measurement["cohort_queries"] == point["cohort_queries"] and
                measurement["threads"] == point["threads"] and
                measurement["search_value"] == point["search_value"] and
                measurement["client_version"] == version["client"],
                "Mixed cohorts, controls, threads or clients within a repetition group")
        repeat_ids.append(measurement["repeat"])
        recalls.append(measurement["recall"])
        qps.append(measurement["qps"])
    require(len(set(repeat_ids)) == len(repeat_ids), "Duplicate repetition")
    require(all(type(value) is int and 1 <= value <= point["expected_repeats"] for value in repeat_ids),
            "Invalid repetition identity")
    import statistics
    for name, actual in (("recall", statistics.median(recalls)), ("qps", statistics.median(qps)),
                         ("qps_min", min(qps)), ("qps_max", max(qps))):
        require(math.isclose(point[name], actual, rel_tol=1e-11, abs_tol=1e-12),
                f"Aggregate {name} does not reproduce from repetitions")


def scope(event):
    return tuple(event[name] for name in ("study", "variant", "dataset", "algorithm"))


def cell(event):
    if event["kind"] == "table":
        return scope(event) + ("table", event["table_name"])
    point = event["point"]
    return scope(event) + tuple(point[name] for name in (
        "scenario", "predicate", "phase", "threads", "cohort_queries",
        "search_parameter", "search_value")) + (digest(json.loads(point["parameters"])),)


def load_history(path):
    if not path.exists():
        return []
    records, head, ids = [], "", set()
    with path.open("rb") as stream:
        for number, line in enumerate(stream, 1):
            require(line.endswith(b"\n"), f"History line {number} is incomplete; current JSON was not changed")
            record = json.loads(line)
            require(record["sequence"] == number and record["previous_hash"] == head, "History chain mismatch")
            payload = record["event"]
            validate_event(payload)
            require(record["event_id"] == digest(payload), "History event digest mismatch")
            unsigned = {key: value for key, value in record.items() if key != "hash"}
            require(record["hash"] == digest(unsigned), "History envelope digest mismatch")
            require(record["event_id"] not in ids, "Duplicate history event")
            ids.add(record["event_id"])
            records.append(record)
            head = record["hash"]
    return records


def retired_indexes(records):
    retired, seen = set(), {}
    for record in records:
        event = record["event"]
        if event["kind"] == "index_retirement":
            key = (event["dataset"], event["algorithm"], event["index"])
            require(any(index_key(r["event"]) == key for r in seen.values()),
                    "Retirement target has no historical observations")
            refs = event["replacement_event_ids"]
            require(all(ref in seen for ref in refs), "Retirement replacements must precede retirement")
            replacements = [seen[ref]["event"] for ref in refs]
            for replacement in replacements:
                require(replacement["kind"] == "measurement" and
                        index_key(replacement) == (event["dataset"], event["algorithm"], event["replacement_index"]),
                        "Retirement replacement index/algorithm/dataset mismatch")
                require(replacement["point"]["complete_repeats_same_client"],
                        "Incomplete retirement replacement")
                require(timestamp(replacement["completed_at"]) <= timestamp(event["completed_at"]),
                        "Retirement predates replacement")
            for required in event["required_cells"]:
                matches = [r for r in replacements if all(
                    (r[k] if k in ("study", "variant") else r["point"].get(k)) == v
                    for k, v in required.items())]
                require(len({r["point"]["search_value"] for r in matches}) >= 2,
                        "Retirement requires a complete replacement curve for every required cell")
            retired.add(key)
        seen[record["event_id"]] = record
    return retired


def index_key(event):
    if event["kind"] not in ("measurement", "table"):
        return None
    return event["dataset"], event["algorithm"], event["version"].get("index")


def project(records):
    require(records, "No experiment history has been registered")
    catalog_records = [r for r in records if r["event"]["kind"] == "catalog"]
    require(catalog_records, "Paper study catalog is missing")
    newest_catalog = max(catalog_records, key=lambda r: r["event"]["revision"])
    revisions = {}
    for record in catalog_records:
        revision = record["event"]["revision"]
        require(revision not in revisions or revisions[revision] == record["event_id"],
                "Different catalogs share a revision")
        revisions[revision] = record["event_id"]
    catalog = newest_catalog["event"]["catalog"]
    allowed_studies = {study["id"] for study in catalog["studies"]}
    retired = retired_indexes(records)
    selected = {}
    for entry in catalog.get("selected_indexes", []):
        require(isinstance(entry, dict) and set(entry) == {"dataset", "algorithm", "index"} and
                all(isinstance(value, str) and value for value in entry.values()),
                "Invalid selected index policy")
        key = entry["dataset"], entry["algorithm"]
        require(key not in selected, "Duplicate selected index scope")
        require((*key, entry["index"]) not in retired, "Selected index has been retired")
        selected[key] = entry["index"]

    def excluded(event):
        key = index_key(event)
        return key in retired or (key[:2] in selected and key[2] != selected[key[:2]])

    versions, bindings, orders, index_identities = {}, {}, {}, {}
    for record in records:
        event = record["event"]
        if event["kind"] in ("catalog", "index_retirement"):
            continue
        require(event["study"] in allowed_studies, "Experiment has no registered paper study")
        require(event["dataset"] in catalog["datasets"], "Experiment dataset is not in the paper catalog")
        identity = scope(event)
        version = event["version"]
        if "index_identity" in version:
            fingerprint = version["index_identity"]
            require(isinstance(fingerprint, str) and len(fingerprint) == 64 and
                    all(c in "0123456789abcdef" for c in fingerprint), "Invalid physical index identity")
            key = index_key(event)
            require(key not in index_identities or index_identities[key] == fingerprint,
                    "An index version cannot name different physical index inventories")
            index_identities[key] = fingerprint
        binding = identity + (version["order"],)
        require(binding not in bindings or bindings[binding] == version,
                "Conflicting version IDs/metadata at one version order")
        bindings[binding] = version
        version_key = identity + (version["id"],)
        require(version_key not in orders or orders[version_key] == version["order"],
                "A version ID cannot change its order")
        orders[version_key] = version["order"]
        if excluded(event):
            continue
        if identity not in versions or version["order"] > versions[identity]["order"]:
            versions[identity] = version
    candidates, pending = defaultdict_list(), defaultdict_list()
    for record in records:
        event = record["event"]
        if (event["kind"] in ("catalog", "index_retirement") or excluded(event) or
                event["version"] != versions[scope(event)]):
            continue
        target = candidates
        if event["kind"] == "measurement" and not event["point"]["complete_repeats_same_client"]:
            target = pending
        target[cell(event)].append(record)
    chosen = []
    for key, group in candidates.items():
        latest = max(timestamp(record["event"]["completed_at"]) for record in group)
        matches = [record for record in group if timestamp(record["event"]["completed_at"]) == latest]
        require(len(matches) == 1, f"Ambiguous same-time completed results for {key}")
        chosen.append(matches[0])
    chosen.sort(key=lambda record: encoded(cell(record["event"])))
    points, tables = [], []
    for record in chosen:
        event = record["event"]
        common = {name: event[name] for name in
                  ("study", "variant", "dataset", "algorithm", "version", "completed_at", "source")}
        common["event_id"] = record["event_id"]
        if event["kind"] == "measurement":
            curve_key = scope(event) + (event["version"]["id"],) + tuple(event["point"][name] for name in (
                "scenario", "predicate", "phase", "threads", "cohort_queries", "search_parameter")) + (
                digest(json.loads(event["point"]["parameters"])),)
            points.append({**event["point"], **common, "curve_id": "current-" + digest(curve_key)[:20],
                           "measurements": event["measurements"]})
        else:
            tables.append({**common, "table_name": event["table_name"],
                           "rows": event["rows"], "status": event["status"]})
    missing = []
    for planned in catalog["expected_cells"]:
        matches = [point for point in points if all(point.get(k) == v for k, v in planned.items())]
        table_rows = sum(len(entry["rows"]) for entry in tables if entry["status"] == "validated" and
                         all(entry.get(k) == v for k, v in planned.items()))
        count = len(matches) + table_rows
        status = "measured" if count >= 2 else "single_point_only" if count else "not_measured"
        if matches and planned.get("phase") == "throughput":
            status = "operating_points_only"
        missing.append({**planned, "status": status, "points": count})
    for key, group in pending.items():
        if key in candidates:
            continue
        event = max(group, key=lambda record: timestamp(record["event"]["completed_at"]))["event"]
        missing.append({**{name: event[name] for name in ("study", "variant", "dataset", "algorithm")},
                        **{name: event["point"][name] for name in ("scenario", "phase", "threads", "search_value")},
                        "status": "incomplete_repetitions", "points": 0})
    current = {"schema_version": SCHEMA, "policy": "strict-latest-version/latest-complete-repetition-group",
            "history": {"file": "history.jsonl", "records": len(records), "head": records[-1]["hash"]},
            "catalog": catalog, "latest_versions": [
                dict(zip(("study", "variant", "dataset", "algorithm"), identity), version=version)
                for identity, version in sorted(versions.items())],
            "points": points, "tables": tables, "coverage": missing}
    if "experiment_views" in catalog:
        current["experiments"] = experiment_views(points, catalog["experiment_views"])
    if retired:
        current["retired_indexes"] = [
            dict(dataset=dataset, algorithm=algorithm, index=index)
            for dataset, algorithm, index in sorted(retired)]
    return current


def experiment_views(points, policy):
    """Keep the observation registry intact; derive explicit presentation views."""
    require(policy["main_selection"] == "observed-pareto-envelope", "Unknown main selection policy")
    protocol = policy["main_protocol"]
    require(set(protocol) == {"topk", "cohort_queries", "phase", "threads"},
            "Main selection needs an explicit comparable protocol")
    finite(policy["minimum_qps"], "main QPS floor", positive=True)
    require(policy["main_studies"] and all(isinstance(s, str) for s in policy["main_studies"]),
            "Main selection needs explicit eligible studies")
    groups, fixed = defaultdict_list(), defaultdict_list()
    workload_fields = ("dataset", "algorithm", "scenario", "predicate", "selectivity",
                       "candidate_count", "phase", "threads", "cohort_queries", "topk")
    for point in points:
        depths = [int(value) for value in (
            point.get("topk"), point["version"].get("topk"),
            json.loads(point["parameters"]).get("ResultNum"),
            *(m.get("topk") for m in point["measurements"])) if value is not None]
        require(depths and len(set(depths)) == 1 and depths[0] > 0,
                "Missing/conflicting recall depths in experiment view")
        point = dict(point, topk=depths[0])
        fixed[point["curve_id"]].append(point)
        if (point["study"] in policy["main_studies"] and
                all(point[k] == value for k, value in protocol.items()) and
                point["qps"] >= policy["minimum_qps"]):
            groups[tuple(point[k] for k in workload_fields)].append(point)

    def observation(point, include_configuration=False):
        fields = ("event_id", "recall", "qps", "qps_min", "qps_max", "recall_min_observed",
                  "recall_max_repeat", "search_parameter", "search_value", "repeats", "expected_repeats")
        result = {key: point[key] for key in fields}
        if include_configuration:
            result.update(source_curve_id=point["curve_id"], study=point["study"],
                          variant=point["variant"], version=point["version"],
                          parameters=json.loads(point["parameters"]))
        return result

    main = []
    for key, candidates in sorted(groups.items(), key=lambda item: encoded(item[0])):
        frontier, best_qps = [], -math.inf
        for point in sorted(candidates, key=lambda p: (-p["recall"], -p["qps"], p["event_id"])):
            if point["qps"] > best_qps:
                frontier.append(point)
                best_qps = point["qps"]
        main.append(dict(zip(workload_fields, key),
                         curve_id="pareto-" + digest(key)[:20], candidate_points=len(candidates),
                         points=[observation(p, True) for p in reversed(frontier)]))
    ablations = []
    for curve_id, observations in sorted(fixed.items()):
        first = observations[0]
        fields = workload_fields + ("study", "variant", "version", "search_parameter", "parameters")
        require(all(all(p[k] == first[k] for k in fields) for p in observations),
                "A fixed-configuration curve mixes workloads or parameters")
        ablations.append(dict({k: first[k] for k in fields}, curve_id=curve_id,
                              parameters=json.loads(first["parameters"]),
                              points=[observation(p) for p in sorted(
                                  observations, key=lambda p: (p["search_value"], p["event_id"]))]))
    return {"schema_version": 1, "point_reference": "points[].event_id",
            "main": {"selection": policy["main_selection"], "protocol": protocol,
                     "minimum_qps": policy["minimum_qps"], "cross_configuration": True,
                     "cross_version": True, "interpolation": False,
                     "tie_break": "lexicographically smallest event_id for identical recall/QPS",
                     "curves": main},
            "ablations": {"selection": "all-complete-fixed-configuration-curves",
                          "includes_main_selected_points": True, "curves": ablations},
            "disclosures": policy["disclosures"]}


def defaultdict_list():
    from collections import defaultdict
    return defaultdict(list)


@contextmanager
def locked(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".results.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def atomic_json(path, value):
    data = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False).encode() + b"\n"
    if path.exists() and path.read_bytes() == data:
        return False
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".current-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink()
    return True


def update(directory=HERE, events=(), check=False, expected_history_head=None):
    with locked(directory):
        history = directory / "history.jsonl"
        records = load_history(history)
        if expected_history_head is not None:
            require((records[-1]["hash"] if records else "") == expected_history_head,
                    "History changed since publication preflight")
        old_count = len(records)
        existing = {record["event_id"] for record in records}
        observed = {measurement_identity(record["event"]) for record in records
                    if record["event"]["kind"] == "measurement"}
        for event in events:
            validate_event(event)
            event_id = digest(event)
            if event_id in existing:
                continue
            if event["kind"] == "measurement" and measurement_identity(event) in observed:
                continue
            envelope = {"sequence": len(records) + 1, "previous_hash": records[-1]["hash"] if records else "",
                        "event_id": event_id, "event": event}
            envelope["hash"] = digest(envelope)
            records.append(envelope)
            existing.add(event_id)
            if event["kind"] == "measurement":
                observed.add(measurement_identity(event))
        current = project(records)
        if check:
            require(json.loads((directory / "current.json").read_text()) == current, "current.json is stale")
            return current
        if len(records) > old_count:
            with history.open("ab") as stream:
                for record in records[old_count:]:
                    stream.write(encoded(record) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        atomic_json(directory / "current.json", current)
        return current


def measurement_identity(event):
    values = {key: event["point"][key] for key in (
        "recall", "recall_min_observed", "recall_max_repeat", "qps", "qps_min", "qps_max",
        "candidate_count", "selectivity", "expected_repeats")}
    return digest([cell(event), event["version"], timestamp(event["completed_at"]).isoformat(),
                   event["measurements"], values])


def predicate_label(predicate):
    if predicate is None:
        return "TRUE"
    if set(predicate) == {"categorical_eq"}:
        column, value = predicate["categorical_eq"]
        require(column == 0, "Publication adapter requires the declared categorical column 0")
        return f"tag = {value}"
    if set(predicate) == {"numeric_le"}:
        column, value = predicate["numeric_le"]
        require(column == 1, "Publication adapter requires the declared numeric column 1")
        return f"num <= {value}"
    for operator in ("and", "or"):
        if set(predicate) == {operator}:
            terms = []
            for child in predicate[operator]:
                text = predicate_label(child)
                terms.append(f"({text})" if any(name in child for name in ("and", "or")) else text)
            require(terms, "Empty compound predicate")
            return f" {operator.upper()} ".join(terms)
    raise ValueError("Unsupported native predicate in publication adapter")


def publish_native(campaign, publication, directory=HERE):
    """Publish a finalized native campaign without changing its INIs or artifacts."""
    manifest = configparser.ConfigParser(interpolation=None)
    manifest.optionxform = str
    require(manifest.read(publication) == [str(publication)], "Missing publication metadata INI")
    policy = manifest["Publication"]
    complete_path = campaign / "completion.json"
    complete = json.loads(complete_path.read_text())
    require(complete["exit_code"] == 0, "Native campaign did not finish successfully")
    for relative, expected in complete["artifacts"].items():
        require(file_sha(campaign / relative) == expected, f"Completed native artifact changed: {relative}")
    analysis = json.loads((campaign / "measurement-analysis.json").read_text())
    require(analysis["exact_predicates_distances_dedup"] and analysis["repetitions_byte_identical"],
            "Native campaign has not passed result acceptance")
    records = json.loads((campaign / "single_raw.json").read_text())
    workload = json.loads(Path(policy["Workloads"]).read_text())
    require(workload["query_count"] == int(policy["Queries"]) and workload["queries"] == policy["QueryFile"],
            "Publication workload/query file mismatch")
    groups = defaultdict_list()
    role_sections = [name for name in manifest.sections() if name.startswith("Role:")]
    for row in records:
        role = row.get("role")
        if role is None:
            require(len(role_sections) == 1, "A role is required for multi-index publication")
            role = role_sections[0].split(":", 1)[1]
        require(f"Role:{role}" in manifest, f"Unregistered native index role: {role}")
        groups[(role, row["scenario"], row["config"], row["nprobe"])].append(row)
    events = []
    for (role, scenario, config_path, probe), repetitions in groups.items():
        declared = manifest[f"Role:{role}"]
        config = configparser.ConfigParser(interpolation=None)
        config.optionxform = str
        require(config.read(config_path) == [config_path], "Missing measured native INI")
        require(config["Benchmark"]["Index"] == declared["IndexPath"], "Publication role/index mismatch")
        require(config["Benchmark"]["MaxQueries"] == policy["Queries"] and
                config["Benchmark"]["Queries"] == policy["QueryFile"], "Publication query cohort changed")
        require(config["Benchmark"]["PredicateFile"] == workload["native_predicates"][scenario]["file"] and
                config["Benchmark"]["Predicate"] == workload["native_predicates"][scenario]["kind"],
                "Native predicate file changed")
        search = dict(config["SearchSSDIndex"])
        for key in ("InternalResultNum", "SearchInternalResultNum", "isExecute", "BuildSsdIndex",
                    "LogPhaseTime", "LogPathStats", "DumpHeads"):
            search.pop(key, None)
        for name, key in (("max_check", "MaxCheck"), ("posting_anchor_count", "PostingAnchorCount"),
                          ("posting_additional_max_check", "PostingAdditionalMaxCheck"),
                          ("posting_navigation_width", "PostingNavigationWidth"),
                          ("search_posting_page_limit", "SearchPostingPageLimit")):
            if name in repetitions[0]:
                require(all(row[name] == repetitions[0][name] for row in repetitions),
                        "Native parameters differ between repetitions")
                require(key not in search or int(search[key]) == repetitions[0][name], "Native parameter mismatch")
                search[key] = str(repetitions[0][name])
        version = {"id": declared["IndexVersion"] + "/" + declared["ClientVersion"],
                   "order": int(declared["VersionOrder"]), "index": declared["IndexVersion"],
                   "client": declared["ClientVersion"]}
        metadata = workload["scenario_metadata"][scenario]
        measurements = [{**row, "repeat": row["repetition"], "threads": int(search["NumberOfThreads"]),
                         "cohort_queries": int(policy["Queries"]), "search_value": probe,
                         "client_version": version["client"]} for row in repetitions]
        import statistics
        rates = [row["qps"] for row in repetitions]
        recalls = [row["recall"] for row in repetitions]
        point = {"dataset": policy["Dataset"], "algorithm": policy["Algorithm"], "scenario": scenario,
                 "predicate": predicate_label(workload["predicates"][scenario]), "selectivity": metadata["selectivity"],
                 "candidate_count": metadata["eligible_count"], "phase": "single",
                 "threads": int(search["NumberOfThreads"]), "cohort_queries": int(policy["Queries"]),
                 "search_parameter": "nprobe", "search_value": probe,
                 "parameters": json.dumps(search, sort_keys=True, separators=(",", ":")),
                 "setting": repetitions[0].get("setting", "native"), "curve_id": f"{role}-{scenario}",
                 "client_version": version["client"], "index_version": version["index"],
                 "recall": statistics.median(recalls), "recall_min_observed": min(recalls),
                 "recall_max_repeat": max(recalls), "qps": statistics.median(rates),
                 "qps_min": min(rates), "qps_max": max(rates), "repeats": len(repetitions),
                 "expected_repeats": int(policy["Repeats"]),
                 "complete_repeats_same_client": len(repetitions) == int(policy["Repeats"])}
        events.append({"schema_version": SCHEMA, "kind": "measurement", "study": policy["Study"],
                       "variant": policy["Variant"], "dataset": policy["Dataset"], "algorithm": policy["Algorithm"],
                       "version": version, "completed_at": analysis["completed_utc"],
                       "point": point, "measurements": measurements,
                       "source": {"description": "Finalized native experiment; exact results and repetitions accepted",
                                  "path": str(campaign / "single_raw.json"),
                                  "sha256": file_sha(campaign / "single_raw.json"),
                                  "completion_path": str(complete_path), "completion_sha256": file_sha(complete_path),
                                  "publication_ini": str(publication), "publication_sha256": file_sha(publication)}})
    return update(directory, events)


def read_csv(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def render_auxiliary(current, paper):
    def table(name):
        values = [entry for entry in current["tables"] if entry["table_name"] == name]
        require(len(values) == 1, f"Missing or ambiguous current paper table: {name}")
        return values[0]["rows"]
    rows = table("recall90-budget-io")
    require(len(rows) == 5 and rows[0]["selectivity"] == 100, "Motivation layout requires five ordered levels")
    for row in rows:
        for name in ("selectivity", "n", "L", "posting_pages", "graph_pages"):
            finite(row[name], name, positive=True)
    target = paper / "figures/sift1b/recall90-budget-io.csv"
    with target.open("w", newline="") as stream:
        fields = ("selectivity", "n", "L", "posting_pages", "graph_pages", "posting_recall", "graph_recall")
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: format(row[key], ".12g") for key in fields} for row in rows)
    claims = table("recall95-speedup")
    require(len(claims) == 1 and claims[0]["recall_target"] == .95, "Unexpected motivation claim target")
    first, last = rows[0], rows[-1]
    values = {
        "lionMotivationPostingBudgetRatio": f'{last["n"] / first["n"]:.1f}',
        "lionMotivationGraphBudgetRatio": f'{last["L"] / first["L"]:.2f}',
        "lionMotivationPostingIORatio": f'{last["posting_pages"] / first["posting_pages"]:.2f}',
        "lionMotivationGraphIORatio": f'{last["graph_pages"] / first["graph_pages"]:.2f}',
        "lionMotivationPostingFirstPages": f'{first["posting_pages"]:.0f}',
        "lionMotivationPostingLastPages": f'{last["posting_pages"]:.0f}',
        "lionMotivationGraphFirstPages": f'{first["graph_pages"]:.1f}',
        "lionMotivationGraphLastPages": f'{last["graph_pages"]:.0f}',
        "lionMotivationSpeedupMin": f'{claims[0]["speedup_min"]:.2f}',
        "lionMotivationSpeedupMax": f'{claims[0]["speedup_max"]:.2f}',
        "lionMotivationBroadSel": format(rows[1]["selectivity"], "g"),
        "lionMotivationSparseSel": format(last["selectivity"], "g")}
    text = "% Generated from data/current.json; do not edit empirical values here.\n"
    text += "".join(f"\\newcommand{{\\{name}}}{{{value}}}\n" for name, value in values.items())
    (paper / "data/generated-values.tex").write_text(text)


def render_pdfs(paper):
    output = paper / "figures/evaluation/preliminary"
    manifest = json.loads((output / "plot-manifest.json").read_text())
    with tempfile.TemporaryDirectory(prefix=".render-", dir=output) as temporary:
        build = Path(temporary)
        for name in manifest["figures"]:
            for _ in range(3):
                result = subprocess.run(
                    ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", f"-jobname={name}",
                     f"-output-directory={build}", "figures/evaluation/native-standalone.tex"],
                    cwd=paper, capture_output=True, text=True)
                require(result.returncode == 0, f"Native figure build failed: {name}\n{result.stdout}\n{result.stderr}")
        for name in manifest["figures"]:
            os.replace(build / f"{name}.pdf", output / f"{name}.pdf")
    print(f'Built {len(manifest["figures"])} current native figure PDFs.')


def version_for(point):
    algorithm, client, index = point["algorithm"], point["client_version"], point["index_version"]
    if algorithm == "SPTAG":
        if index == "compact_H5":
            order = {"controlled_ascent_20260924": 1, "throughput_native_20260930": 2,
                     "optimized_20261001": 3}[client]
        else:
            generation = int(index.rsplit("v", 1)[1])
            code = 9 if client == "V9_adjacent_limited_labels" else 8 if client == "V8_child_fastpath" else \
                   7 if client == "V7_reference" else int(client.split("_v", 1)[1].split("_", 1)[0])
            order = generation * 1000 + code * 10 + (1 if client == "V7_reference" else 0)
    elif algorithm == "PipeANN":
        order = {"historical_pre_page_lifetime_fix": 1, "page_lifetime_legacy_return": 2,
                 "page_lifetime_native_prefix": 3}[client]
    else:
        require(algorithm == "Filtered_DiskANN" and client == "linear_label_loader", "Unknown algorithm version")
        order = 1
    return {"id": f"{index}/{client}", "order": order, "index": index, "client": client}


def bootstrap(archive, directory=HERE):
    proof = json.loads((archive / "provenance.json").read_text())
    for relative, entry in proof["outputs"].items():
        require(file_sha(archive / relative) == entry["sha256"], f"Archive changed: {relative}")
    paper = HERE.parent
    comparisons = archive.parent
    protocol = json.loads((comparisons / "three_index_results_20261003/protocol.json").read_text())
    layout_path = paper / "figures/evaluation/label-layout.json"
    layout = json.loads(layout_path.read_text())
    count_path = layout_path.parent / layout["counts_file"]
    require(file_sha(count_path) == layout["counts_sha256"], "Label-count metadata changed")
    with count_path.open() as stream:
        counts = {int(row["attribute_id"]): row for row in csv.DictReader(stream, delimiter="\t")}
    labels = []
    for label in layout["labels"]:
        known = next((name for name, value in protocol["scenario_metadata"].items()
                      if value["predicate"] == f"tag = {label}"), f"label_{label}")
        labels.append({"label": label, "scenario": known, "predicate": f"tag = {label}",
                       "candidate_count": int(counts[label]["count"]),
                       "selectivity": int(counts[label]["count"]) / 1000000000})
    study_ids = ["main", "or", "and", "io-scale", "overhead", "ablation-sequence", "ablation-label-balance",
                 "ablation-hierarchy", "ablation-adaptive", "ablation-sensitivity", "space-cost",
                 "ablation-fast-path", "motivation-io", "motivation-claims"]
    expected = []
    for dataset in ("SIFT1B", "SPACEV1B", "YFCC10M"):
        for algorithm in protocol["engines"]:
            algorithm = "SPTAG" if algorithm == "SPTAG_adaptive" else algorithm
            for label in labels:
                for phase, threads in (("single", 1), ("throughput", layout["parallel_display_workers"])):
                    expected.append({"study": "main", "variant": "default", "dataset": dataset,
                                     "algorithm": algorithm, "scenario": label["scenario"],
                                     "phase": phase, "threads": threads})
    for study in study_ids:
        if study == "main" or study.startswith("motivation"):
            continue
        for dataset in ("SIFT1B", "SPACEV1B"):
            expected.append({"study": study, "dataset": dataset})
    catalog = {"datasets": ["SIFT1B", "SPACEV1B", "YFCC10M"], "protocol": protocol,
               "label_layout": layout, "labels": labels, "studies": [{"id": name} for name in study_ids],
               "expected_cells": expected,
               "historical_case_policy": "Motivation and explicit ablation variants are separate studies, not current main baselines."}
    events = [{"schema_version": SCHEMA, "kind": "catalog", "revision": 1, "catalog": catalog,
               "completed_at": proof["completed_utc"], "source": {"description": "Existing paper experiment catalog",
               "path": str(layout_path), "sha256": file_sha(layout_path)}}]
    raw = {}
    for line in (archive / "raw_records.jsonl").read_text().splitlines():
        record = json.loads(line)
        raw[record["record_id"]] = record["original"]
    normalized = {row["record_id"]: row for row in read_csv(archive / "raw_repetitions.csv")}
    for point in read_csv(archive / "recall_qps.csv"):
        for name in ("threads", "search_value", "cohort_queries", "repeats", "expected_repeats",
                     "candidate_count", "measured_queries_total"):
            point[name] = int(point[name])
        for name in ("recall", "recall_percent", "qps", "selectivity", "recall_min_observed",
                     "recall_max_repeat", "qps_min", "qps_max"):
            point[name] = float(point[name])
        for name in ("complete_repeats_same_client", "qualifies_R90", "qualifies_R95"):
            point[name] = {"True": True, "False": False}[point[name]]
        measurements = []
        for record_id in point["source_record_ids"].split(";"):
            original = raw[record_id]
            row = normalized[record_id]
            measurements.append({**original, "repeat": int(row["repeat"]), "threads": int(row["threads"]),
                                 "cohort_queries": int(row["cohort_queries"]), "search_value": int(row["search_value"]),
                                 "client_version": point["client_version"]})
        source_path = Path(point["source_raw"].split(";")[0])
        parent = source_path.parent
        candidates = [parent / "measurement-analysis.json", parent / "completion.json", parent / "status.json"]
        if parent.name == "threeway_categorical_loader_linear_20260930_v2":
            candidates = [comparisons / "threeway_categorical_native_short_20260930/completion.json"]
        if parent.name == "pipeann_adaptive_curves_20260924_v2":
            candidates = [comparisons / "pipeann_controlled_ascent_curves_20260924/plot_input/plot_registration.json"]
        completed = None
        for candidate in candidates:
            if not candidate.exists():
                continue
            document = json.loads(candidate.read_text())
            if candidate.name == "plot_registration.json":
                value = document["engines"]["PipeANN"]["source_completed_at_utc"]
            else:
                value = next((document[key] for key in ("completed_utc", "at_utc", "completed_at", "updated_at")
                              if key in document), None)
            if value:
                timestamp(value)
                completed = value
                break
        require(completed is not None, f"No accepted publication timestamp: {source_path}")
        event = {"schema_version": SCHEMA, "kind": "measurement", "study": "main", "variant": "default",
                 "dataset": point["dataset"], "algorithm": point["algorithm"], "version": version_for(point),
                 "completed_at": completed, "point": point, "measurements": measurements,
                 "source": {"description": "Accepted native results from the archived comparison lineage",
                            "path": str(source_path), "sha256": file_sha(source_path),
                            "completion_path": str(candidate), "timestamp_basis": "accepted_publication"}}
        events.append(event)
        if point["measurement_series"] == "spann_child_label_fastpath_v8":
            events.append({**event, "study": "ablation-fast-path",
                           "variant": "reference" if point["client_version"] == "V7_reference" else "optimized"})
    motivation = paper / "figures/sift1b/recall90-budget-io.csv"
    rows = [{key: float(value) for key, value in row.items()} for row in read_csv(motivation)]
    events.append({"schema_version": SCHEMA, "kind": "table", "study": "motivation-io",
                   "variant": "static-baseline", "dataset": "SIFT1B", "algorithm": "posting-vs-graph",
                   "version": {"id": "static-IN_FILTER-20260811", "order": 1, "index": "static", "client": "20260811"},
                   "completed_at": "2026-08-11T00:00:00Z", "table_name": "recall90-budget-io",
                   "status": "reported_not_revalidated", "rows": rows,
                   "source": {"description": "Existing paper motivation table; original date has day precision",
                              "path": str(motivation), "sha256": file_sha(motivation)}})
    events.append({"schema_version": SCHEMA, "kind": "table", "study": "motivation-claims",
                   "variant": "static-baseline", "dataset": "SIFT1B", "algorithm": "posting-vs-graph",
                   "version": {"id": "static-IN_FILTER-20260811", "order": 1, "index": "static", "client": "20260811"},
                   "completed_at": "2026-08-11T00:00:00Z", "table_name": "recall95-speedup",
                   "status": "reported_not_revalidated",
                   "rows": [{"recall_target": .95, "speedup_min": 2.59, "speedup_max": 21.44}],
                   "source": {"description": "Already reported in the paper; raw-source revalidation is not claimed",
                              "path": str(paper / "sections/introduction/section.tex"),
                              "sha256": file_sha(paper / "sections/introduction/section.tex")}})
    return update(directory, events)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=HERE, help="Registry directory; use an explicit output path")
    sub = parser.add_subparsers(dest="action")
    sub.add_parser("refresh", help="Rebuild current.json from append-only history; default action")
    sub.add_parser("check", help="Verify history integrity and that current.json is up to date")
    sub.add_parser("render", help="Legacy paper-layout-only R/TikZ renderer (not a standalone checkout workflow)")
    record = sub.add_parser("record", help="Append a completed experiment event or array and refresh current.json")
    record.add_argument("event_file", type=Path)
    initial = sub.add_parser("bootstrap", help="Legacy paper-layout-only historical migration")
    initial.add_argument("archive", type=Path)
    native = sub.add_parser("publish-native", help="Publish a finalized native campaign and refresh current.json")
    native.add_argument("campaign", type=Path)
    native.add_argument("--publication", required=True, type=Path)
    runner = sub.add_parser("run", help="Run an experiment, then publish its emitted event only on success")
    outputs = runner.add_mutually_exclusive_group(required=True)
    outputs.add_argument("--event", type=Path)
    outputs.add_argument("--native-campaign", type=Path)
    runner.add_argument("--publication", type=Path)
    runner.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "bootstrap":
        current = bootstrap(args.archive.resolve(), args.data_dir)
    elif args.action == "publish-native":
        current = publish_native(args.campaign.resolve(), args.publication.resolve(), args.data_dir)
    else:
        events = []
        if args.action == "run":
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            require(command, "A foreground experiment command is required")
            if args.event:
                require(not args.event.exists(), "Experiment event file must be new; refusing stale-success publication")
            else:
                require(args.publication is not None, "Native publication metadata INI is required")
                require(not (args.native_campaign / "completion.json").exists(), "Native campaign must be new")
            subprocess.run(command, check=True)
            if args.native_campaign:
                current = publish_native(args.native_campaign.resolve(), args.publication.resolve(), args.data_dir)
                print(json.dumps({"current": str(args.data_dir / "current.json"),
                                  "events": current["history"]["records"], "current_points": len(current["points"])}))
                return
            args.event_file = args.event
        if args.action in ("record", "run"):
            value = json.loads(args.event_file.read_text())
            events = value if isinstance(value, list) else [value]
            require(events, "No completed experiment event was emitted")
        current = update(args.data_dir, events, check=args.action == "check")
    if args.action == "render":
        paper = args.data_dir.resolve().parent
        require((paper / "figures/evaluation/prepare-preliminary.R").is_file(), "Not a paper data directory")
        with locked(args.data_dir):
            current = project(load_history(args.data_dir / "history.jsonl"))
            atomic_json(args.data_dir / "current.json", current)
            render_auxiliary(current, paper)
            subprocess.run(["Rscript", "--vanilla", "figures/evaluation/prepare-preliminary.R",
                            "--current-json", str((args.data_dir / "current.json").resolve()),
                            "--output-dir", "figures/evaluation/preliminary"], cwd=paper, check=True)
            render_pdfs(paper)
    print(json.dumps({"current": str(args.data_dir / "current.json"), "events": current["history"]["records"],
                      "current_points": len(current["points"]), "tables": len(current["tables"])}, indent=2))


if __name__ == "__main__":
    main()
