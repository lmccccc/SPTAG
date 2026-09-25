#!/usr/bin/env python3
"""Validate extended grids and exact equivalence of repeated baseline points."""
import argparse
import json
import math
from pathlib import Path

from official_benchmark_config import identity, require, write_json
from run_vanilla_spann_build import read_ini


def records(path):
    with path.open() as stream:
        for line in stream:
            yield json.loads(line)


def key(row):
    return row["mode"], row.get("replicas", 0), row.get("beam", 0), row["query"]


def run(config, baseline):
    new_plan, old_plan = read_ini(config)["Experiment"], read_ini(baseline)["Experiment"]
    root = Path(new_plan["OutputDirectory"])
    require(json.loads((root / "status.json").read_text())["state"] == "completed", "Extension incomplete")
    require(not (root / "extension_validation.json").exists(), "Refusing to overwrite validation")
    old_cases = {}
    for path in old_plan["Configs"].split(","):
        native = read_ini(Path(path))
        old_cases[Path(native["Output"]["Directory"]).name] = native
    verified = repeated = 0
    cases = []
    for path in new_plan["Configs"].split(","):
        new = read_ini(Path(path))
        directory = Path(new["Output"]["Directory"])
        old = old_cases[directory.name]
        previous = Path(old["Output"]["Directory"])
        require(dict(new["Input"]) == dict(old["Input"]), "Input catalogs/cohort changed")
        for k in old["Sweep"]:
            if k not in {"replicas", "beams"}:
                require(new["Sweep"][k] == old["Sweep"][k], f"Control changed: {k}")
        common = set(new["Sweep"]["Replicas"].split(",")) & set(old["Sweep"]["Replicas"].split(","))
        names = ["assignment_candidates.bin", "oracle_h1.jsonl"] + [f"replicas_{r}.csr" for r in sorted(common)]
        fingerprints = []
        for name in names:
            a, b = identity(previous / name, True), identity(directory / name, True)
            require(a["sha256"] == b["sha256"], f"Baseline artifact changed: {directory.name}/{name}")
            fingerprints.append(b)
        truth = {r["query"]: set(r["ids"]) for r in records(directory / "oracle_h1.jsonl")}
        nq, k = new["Input"].getint("QueryCount"), new["Sweep"].getint("ResultNum")
        require(set(truth) == set(range(nq)) and all(len(ids) == k for ids in truth.values()),
                "Wrong oracle shape")
        old_queries = {
            key(r): {name: value for name, value in r.items() if name != "ordinary_us"}
            for r in records(previous / "queries.jsonl")
        }
        seen = set()
        for row in records(directory / "queries.jsonl"):
            record_key = key(row)
            require(record_key not in seen, "Duplicate query record")
            seen.add(record_key)
            recall = len(set(row["ids"]) & truth[row["query"]]) / k
            require(math.isclose(row["recall"], recall, abs_tol=1e-11), "Selected-ID recall differs")
            verified += 1
            if record_key in old_queries:
                require({name: value for name, value in row.items() if name != "ordinary_us"} ==
                        old_queries[record_key], "Repeated baseline query work or IDs changed")
                repeated += 1
        expected = {("flat", 0, 0, q) for q in range(nq)} | {
            ("h2", int(r), int(b), q) for r in new["Sweep"]["Replicas"].split(",")
            for b in new["Sweep"]["Beams"].split(",") for q in range(nq)
        }
        require(seen == expected, "Missing or unexpected parameter/query records")
        cases.append({"case": directory.name, "identical_artifacts": fingerprints})
    write_json(root / "extension_validation.json", {
        "selected_ID_recalls_verified": verified, "repeated_query_rows_identical_except_timing": repeated,
        "cases": cases, "validator": identity(Path(__file__), True),
    })
    print(f"Verified {verified} head recalls; {repeated} repeated query records match exactly except timing.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    args = parser.parse_args()
    run(args.config, args.baseline)
