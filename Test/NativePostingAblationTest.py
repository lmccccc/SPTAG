#!/usr/bin/env python3
"""Bounded real-native global/limited ablations over an existing H/O fixture."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

import numpy as np

from NativeBenchClientTest import identities, read_ini, require
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Tools/benchmarks/results"))
import native_results as native
from r90_timing import write_ini


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("binary", "reference", "head-test", "fixture", "vectors", "attributes", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir()
    template = read_ini(args.fixture)
    index = Path(template["Benchmark"]["Index"])
    saved = read_ini(index / "tenant_0/indexloader.ini")
    count = int(saved["Base"]["VectorSize"])
    require(0 < count <= 1000000, "Only a bounded existing fixture is allowed")
    with (index / "tenant_0/HeadIndex/vectors.bin").open("rb") as stream:
        heads, dimension = struct.unpack("<ii", stream.read(8))
    require(0 < heads < 200000 and dimension == 128, "Unexpected fixture geometry")
    before = identities(index)
    source = native.open_vectors(args.vectors, "UInt8", 128)
    base = source.data[:count]
    attrs = native.open_attributes(args.attributes, count, 2, len(source.data))
    queries = np.load(template["Benchmark"]["Queries"])[:32]
    expressions = {"empty": None, "scalar": {"categorical_eq": [0, 7]},
                   "union": {"or": [{"categorical_eq": [0, 7]}, {"categorical_eq": [0, 16]}]}}
    predicates = {}
    for scenario, expression in expressions.items():
        if expression is None:
            predicates[scenario] = ("empty", "", None)
            continue
        _, clauses = native.parse_predicate(json.dumps(expression), ("categorical", "numeric"))
        kind, payload = native.native_predicate(clauses, ("categorical", "numeric"), 32)
        path = output / f"{scenario}.npy"
        np.save(path, payload)
        predicates[scenario] = (kind, str(path), clauses)
    observations = {}
    for client, binary in (("reference", args.reference), ("current", args.binary)):
        sequence = [(s, None) for s in predicates] if client == "reference" else [
            (s, flag) for flag in ("false", "true", None) for s in predicates]
        batch = {"Batch": dict(CaseCount=str(len(sequence)),
                              WarmupPolicy="once_per_predicate" if client=="reference" else "once_per_mode_predicate")}
        cases = []
        for ordinal, (scenario, flag) in enumerate(sequence, 1):
            config = {section: dict(template[section]) for section in template.sections()}
            config["SearchSSDIndex"].update(EnablePostingNavigation="false",
                PostingAnchorCount="0", PostingAdditionalMaxCheck="0")
            if flag is not None:
                config["SearchSSDIndex"]["GlobalPostingSearch"] = flag
            kind, predicate, _ = predicates[scenario]
            config["Benchmark"].update(Predicate=kind, PredicateFile=predicate, MaxQueries="32", Warmup="32")
            config["SearchSweep"]["NProbe"] = "16,24"
            name = f"{client}-{scenario}-{flag}"
            path, directory = output / f"{name}.ini", output / name
            write_ini(path, config)
            batch[f"Case{ordinal}"] = dict(Config=str(path), OutputDirectory=str(directory))
            cases.append((scenario, flag, directory))
        manifest = output / f"{client}.ini"
        write_ini(manifest, batch)
        with (output / f"{client}.log").open("x") as log:
            subprocess.run([str(binary.resolve()), str(manifest)], cwd=output, stdout=log,
                           stderr=subprocess.STDOUT, check=True, timeout=300)
        events = [json.loads(line) for line in (output / f"{client}.log").read_text().splitlines()
                  if line.startswith("{")]
        require(events[-1]["event"] == "batch_end" and
                events[-1]["completed_warmup_queries"] == (96 if client=="reference" else 192),
                "Scenario-once warmup is incomplete")
        points = [event for event in events if event["event"] == "point"]
        require(len(points) == len(cases)*2, "Incomplete native captures")
        for ordinal, (scenario, flag, directory) in enumerate(cases):
            for offset, probe in enumerate((16, 24)):
                point = points[ordinal*2+offset]
                require(not point["diagnostic"] and point["mode"] == ("global" if flag == "true" else "graph"),
                        "Ablation mode or ordinary throughput provenance differs")
                folder = directory / f"nprobe_{probe}"
                ids = native.payload(folder / "ids.i32", "<i4", (32, 10))
                distances = native.payload(folder / "dist.f32", "<f4", (32, 10))
                work = native.payload(folder / "work.u64", "<u8", (32, 8))
                valid = native.validate_exact(ids, distances, base, queries, attrs, None, 10)
                clauses = predicates[scenario][2]
                if clauses is not None:
                    require(np.all(native.matching_rows(clauses, attrs[ids[valid]])),
                            "Global posting search bypassed exact record filtering")
                captures = tuple((folder / name).read_bytes() for name in ("ids.i32", "dist.f32", "work.u64"))
                key = (scenario, probe)
                if client == "reference":
                    observations[key] = captures
                elif flag != "true" or scenario == "empty":
                    require(captures == observations[key], "Default/reset mode or unfiltered O search changed")
                if client == "current" and flag == "true":
                    global_key = ("global", probe)
                    # GetScanned is overwritten by posting work, not an H1 counter.
                    invariant = work[:, [0, 1, 5, 6, 7]]
                    if scenario == "empty":
                        observations[global_key] = invariant.copy()
                    else:
                        require(np.array_equal(invariant, observations[global_key]),
                                "Global mode changed O reads based on the predicate")
                        require(captures != observations[key], "Global mode did not change the limited path")
    with (output / "heads.log").open("x") as log:
        subprocess.run([str(args.head_test.resolve()), *[
            str(output / f"current-{s}-true.ini") for s in predicates]],
            cwd=output, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
    heads = [line.split(" :", 1)[1] for line in (output / "heads.log").read_text().splitlines()
             if "DUMPHEADS q=" in line]
    require(len(heads) == 96 and heads[:32] == heads[32:64] == heads[64:],
            "Global head IDs/distances differ between unfiltered, scalar and OR predicates")
    require(before == identities(index), "Immutable fixture index changed")
    native.save(output / "result.json", dict(passed=True, cases=12, points=24,
        exact_distances_predicates=True, unchanged_default_and_unfiltered=True,
        predicate_independent_h1_and_o_reads=True, optional_flag_resets=True, index_unchanged=True,
        binary_sha256=hashlib.sha256(args.binary.read_bytes()).hexdigest(),
        head_test_sha256=hashlib.sha256(args.head_test.read_bytes()).hexdigest()))
    print("PASS: 24 native points, global O selection, exact filters, default parity and batch reset")


if __name__ == "__main__":
    main()
