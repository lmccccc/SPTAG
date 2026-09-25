#!/usr/bin/env python3
"""Validate recorded shortcut results and paired exact-head diagnostics, not final-data recall."""
import argparse
import configparser
import json
from pathlib import Path
import shutil

import numpy as np
from official_benchmark_config import identity, reject_environment_overrides, require, write_json


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def run(config):
    reject_environment_overrides()
    ini = configparser.ConfigParser(interpolation=None,comment_prefixes=(";",))
    ini.read(config)
    require(ini.sections() == ["Analysis"],"Unknown analysis sections")
    c = ini["Analysis"]
    require(set(c) == {k.lower() for k in ("Experiment","NativeDirectory","PristineDirectory",
        "PristineBinary","QueryCount","ResultNum","BootstrapReplicates","BootstrapSeed")},"Unknown keys")
    root, native, pristine = (Path(c[k]) for k in ("Experiment","NativeDirectory","PristineDirectory"))
    nq, k = c.getint("QueryCount"), c.getint("ResultNum")
    summary = {r["case"]:r for r in rows(native/"summary.jsonl")}
    grouped = {name:[] for name in summary}
    for row in rows(native/"queries.jsonl"):
        grouped[row["case"]].append(row)
        require(len(row["ids"]) == len(set(row["ids"])) == k,"Underfilled/duplicate H1s")
        require(len(row["distances"]) == k,"Missing distances")
        cap = summary[row["case"]]["distance_budget"]
        require(not cap or row["distance_calls"] <= cap,"Exceeded exact work cap")
    for group in grouped.values():
        require([r["query"] for r in group] == list(range(nq)),"Different query cohort")
    control = rows(pristine/"queries.jsonl")
    require(len(control) == nq,"Incomplete pristine control")
    for left,right,unlimited in zip(grouped["native_2048"],control,grouped["plain_3200"]):
        for key in ("query","ids","distances"):
            require(left[key] == right[key] == unlimited[key],"Pristine/bounded/instrumented mismatch")
    rng = np.random.default_rng(c.getint("BootstrapSeed"))
    pairs = []
    for ordinary,hybrid in (("plain_1200","add8_1200"),("plain_1200","rewire8_1200"),
                            ("plain_2000","add8_2000"),("plain_2000","rewire8_2000"),
                            ("native_2048","add8_3200"),("native_2048","rewire8_3200")):
        delta = np.array([b["exact_h1_recall"]-a["exact_h1_recall"]
                          for a,b in zip(grouped[ordinary],grouped[hybrid])])
        replicates = np.array([delta[rng.integers(0,nq,nq)].mean()
                               for _ in range(c.getint("BootstrapReplicates"))])
        pairs.append({
            "ordinary":ordinary,"hybrid":hybrid,"exact_h1_recall_delta":float(delta.mean()),
            "paired_query_bootstrap_95":np.quantile(replicates,[.025,.975]).tolist(),
            "distance_work_ratio":summary[hybrid]["distance_calls_mean"]/summary[ordinary]["distance_calls_mean"],
            "ordinary_latency_ratio":summary[hybrid]["ordinary_us_mean"]/summary[ordinary]["ordinary_us_mean"],
        })
    pristine_summary = json.loads((pristine/"summary.json").read_text())
    saved = json.loads((root/"provenance.json").read_text())
    require(all(identity(p["path"],True) == p for p in saved["protected_input_sha256"]),
            "Protected originals no longer match initial hashes")
    validation = root/"pristine_validation"
    validation.mkdir(exist_ok=False)
    for path in (Path(__file__),Path(config),Path(c["PristineBinary"])):
        shutil.copy2(path,validation/path.name)
    source = Path(__file__).parent/"hierarchical_shortcut_native"
    for name in ("PristineFlat.cpp","CMakeLists.txt","pristine.ini","test_shortcut.py"):
        shutil.copy2(source/name,validation/name)
    write_json(validation/"binary.json",identity(c["PristineBinary"],True))
    write_json(root/"analysis.json",{
        "all_12000_queries_full_24_and_budget_valid":True,
        "pristine_ordered_ids_and_float_distances_equal_all_1000":True,
        "plain_3200_equals_unbounded_native_2048":True,
        "pristine_ordinary_us":pristine_summary["ordinary_us_mean"],
        "instrumented_native_latency_ratio":summary["native_2048"]["ordinary_us_mean"]/pristine_summary["ordinary_us_mean"],
        "paired_exact_head_diagnostics":pairs,
        "input_hashes_still_unchanged":True,
        "final_data_recall_and_full_query_qps_validated":False,
    })
    for pair in pairs:
        print(pair)
    print("PASS: 12,000 full top-24 queries, budget bounds, exact pristine parity and original SHA-256s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",required=True,type=Path)
    run(parser.parse_args().config.resolve(strict=True))
