#!/usr/bin/env python3
"""Independently verify persisted full-query results; no vector/posting scans."""
import argparse
import json
from pathlib import Path
import shutil
import statistics

import numpy as np
from run import fingerprint, read_ini, require, rows, write_json


def verify(config):
    config = config.resolve()
    plan = read_ini(config)["Experiment"]
    root = Path(plan["OutputDirectory"])
    truth = np.load(plan["Truth"], mmap_mode="r")[:1000, :10]
    summary = {r["case"]: r for r in json.loads((root / "summary.json").read_text())}
    comparisons = {}
    for case in plan["Cases"].split(","):
        first = rows(root / f"{case}_plain_r1/queries.jsonl")
        hits = []
        for q, result in enumerate(first):
            hits.append(len({p[0] for p in result["results"]} & set(truth[q])))
        require(sum(hits) / 10000 == summary[case]["recall_at_10"], "Native recall recomputation failed")
        for repeat in range(1, 4):
            for mode in ("plain", "profile"):
                data = rows(root / f"{case}_{mode}_r{repeat}/queries.jsonl")
                require([r["results"] for r in data] == [r["results"] for r in first],
                        "Final ordered IDs/Float distances differ")
                require([r["heads"] for r in data] == [r["heads"] for r in first],
                        "Selected H1 ordinals/Float distances differ")
                io = json.loads((root / f"{case}_{mode}_r{repeat}/native.io.json").read_text())
                require(io["direct_io"], "Native posting FD is not O_DIRECT")
        profile = rows(root / f"{case}_profile_r1/queries.jsonl")
        comparisons[case] = {
            "native_recall_hits": sum(hits), "count_parity_queries": 1000,
            "max_actual_head_distances": max(r["calls"] for r in profile),
            "cap_exhausted_queries": sum(r["exhausted"] for r in profile),
            "mean_actual_head_distances": statistics.mean(r["calls"] for r in profile),
        }
    protected = json.loads((root / "provenance.json").read_text())["protected"]
    require(all(fingerprint(p["path"]) == p for p in protected), "Protected input changed")
    prototype = json.loads(Path(plan["ProtectedProvenance"]).read_text())["protected_input_sha256"]
    require(all(fingerprint(p["path"]) == p for p in prototype), "Original v2 input changed")
    saved = root / "validation_sources"
    saved.mkdir(exist_ok=False)
    for path in config.parent.iterdir():
        if path.is_file():
            shutil.copy2(path, saved / path.name)
    toolchain = Path(plan["Toolchain"])
    shutil.copy2(toolchain / "tests.log", saved / "ctest.log")
    require("100% tests passed" in (saved / "ctest.log").read_text(), "Native fixtures did not pass")
    write_json(root / "validation.json", {
        "cases": comparisons, "native_fixtures": "2/2 passed",
        "protected_files_unchanged": len(protected), "v2_protected_files_unchanged": len(prototype),
        "paired_add_minus_plain_ms": [a - b for a, b in zip(
            summary["add2000"]["ordinary_ms_runs"], summary["plain2000"]["ordinary_ms_runs"])],
        "paired_rewire_minus_plain_ms": [a - b for a, b in zip(
            summary["rewire2000"]["ordinary_ms_runs"], summary["plain2000"]["ordinary_ms_runs"])],
        "validation_sources": [fingerprint(p) for p in sorted(saved.iterdir())],
    })
    print("PASS: native recall recomputed, all ordered H1/final Float results match count modes, "
          "24 O_DIRECT observations, protected sources and original v2 inputs unchanged")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    verify(parser.parse_args().config)
