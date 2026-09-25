#!/usr/bin/env python3
"""Compare two native navigation modes over identical H1 IDs and posting files."""

import argparse
import json
from pathlib import Path
import shutil
import statistics

from official_benchmark_config import identity, reject_environment_overrides, require, write_json
from run_vanilla_spann_build import read_ini
from run_vanilla_spann_comparison import h3_core_work, h3_records, loader_differences, run_native


def source_inventory(path):
    # This ablation explicitly authenticates the entire posting file, not just its metadata.
    return [identity(file, True) for file in sorted(path.rglob("*")) if file.is_file()]


def prepare_view(plan, config):
    source = Path(plan["SourceIndex"]).resolve(strict=True)
    flat = Path(plan["FlatHeadIndex"]).resolve(strict=True)
    index = Path(plan["Index"])
    loader = config.parent / plan["IndexLoader"]
    require(json.loads((flat.parent / "status.json").read_text())["state"] == "completed",
            "Flat native head graph construction is incomplete")
    differences = loader_differences(source / "tenant_0/indexloader.ini", loader)
    require(set(differences) ==
            {"Base.indexdirectory", "SelectHead.buildh1graph", "BuildSSDIndex.usedirectio"},
            f"Unexpected changes outside head graph/placement: {differences}")
    require(differences["Base.indexdirectory"][1] == str(index / "tenant_0") and
            differences["SelectHead.buildh1graph"] == ["false", "true"] and
            differences["BuildSSDIndex.usedirectio"] == ["false", "true"],
            "Invalid isolated native head loader")
    require(identity(flat / "vectors.bin", True)["sha256"] ==
            identity(source / "tenant_0/SPTAGHeadVectors.bin", True)["sha256"],
            "Flat graph H1 rows differ from the hierarchy's ordered H1 catalog")
    graph_files = {"indexloader.ini", "tree.bin", "graph.bin", "vectors.bin", "deletes.bin"}
    require(graph_files == {file.name for file in flat.iterdir()},
            "Unexpected flat graph payloads")
    native_meta = source / "tenant_0/HeadIndex/head_node_meta.bin"
    require(native_meta.is_file(), "Frozen head metadata is required")
    if not index.exists():
        index.mkdir(parents=True)
        for entry in source.iterdir():
            target = index / entry.name
            if entry.name != "tenant_0":
                target.symlink_to(entry, target_is_directory=entry.is_dir())
                continue
            target.mkdir()
            for payload in entry.iterdir():
                dest = target / payload.name
                if payload.name == "indexloader.ini":
                    shutil.copy2(loader, dest)
                elif payload.name == "HeadIndex":
                    dest.mkdir()
                    for name in graph_files:
                        (dest / name).symlink_to(flat / name)
                    (dest / native_meta.name).symlink_to(native_meta)
                else:
                    dest.symlink_to(payload, target_is_directory=payload.is_dir())
        write_json(index / "view_provenance.json", {
            "source": str(source), "flat": str(flat), "loader": identity(loader, True),
            "loader_changes": differences,
            "excluded_graphless_sidecars": ["head_metaonly.bin", "head_bundle_manifest.bin"],
            "reason": "Replace the dummy KDT root with a real monolithic BKT; native default bundle preserves H1 ordinal IDs.",
        })
    require((index / "tenant_0/indexloader.ini").read_bytes() == loader.read_bytes(),
            "Native view loader changed")
    for entry in source.iterdir():
        if entry.name == "tenant_0":
            for payload in entry.iterdir():
                if payload.name in {"HeadIndex", "indexloader.ini"}:
                    continue
                target = index / "tenant_0" / payload.name
                require(target.is_symlink() and target.resolve(strict=True) == payload.resolve(),
                        f"Posting/catalog payload no longer points to the same source: {target}")
        else:
            target = index / entry.name
            require(target.is_symlink() and target.resolve(strict=True) == entry.resolve(),
                    "Wrapper mapping changed")
    for name in graph_files:
        target = index / "tenant_0/HeadIndex" / name
        require(target.is_symlink() and target.resolve(strict=True) == (flat / name).resolve(),
                "Native graph changed")
    require({file.name for file in (index / "tenant_0/HeadIndex").iterdir()} ==
            graph_files | {native_meta.name}, "Unexpected native graph sidecars")
    # The graphless V3 loader borrowed top vectors from its graph. The graphful
    # loader expects that same full catalog at the explicit legacy path.
    alias_name = "SPTAGSecondLevelHeadVectors.bin.level2"
    top_vectors = source / "tenant_0/SecondLevelHeadIndex/vectors.bin"
    alias = index / "tenant_0" / alias_name
    require(top_vectors.is_file() and not (source / "tenant_0" / alias_name).exists(),
            "Unexpected top-catalog layout; do not guess or replace vector storage")
    if not alias.exists():
        alias.symlink_to(top_vectors)
        write_json(index / "catalog_alias.json", {
            "alias": str(alias), "source": identity(top_vectors, True),
            "policy": "Only a filename alias to unchanged original top graph vectors; no new vector values.",
        })
    require(alias.is_symlink() and alias.resolve(strict=True) == top_vectors.resolve(),
            "Top catalog alias changed")
    return index


def validate_controls(config, plan):
    cases = [item.strip() for item in plan["Cases"].split(",")]
    diagnostics = [item.strip() for item in plan["DiagnosticCases"].split(",") if item.strip()]
    require(len(set(cases)) == len(cases) and cases, "Invalid fixed cases")
    controls = {}
    for case in cases:
        require(case.replace("_", "").isalnum(), "Invalid case name")
        native = read_ini(config.parent / f"{case}.ini")["SearchSSDIndex"]
        require(native.getint("NumberOfThreads") == 1 and
                native.getint("SearchPostingPageLimit") == 15 and
                native["HeadNavigationMode"] in {"H1Only", "H2Only"}, "Unexpected search controls")
        controls[case] = native
    by_probe = {}
    for native in controls.values():
        by_probe.setdefault(native.getint("InternalResultNum"), []).append(native)
    for natives in by_probe.values():
        if len(natives) == 2:
            left, right = [dict(native) for native in natives]
            require(left.pop("headnavigationmode") != right.pop("headnavigationmode")
                    and left == right, "Equal-budget controls differ outside navigation mode")
    for case in diagnostics:
        require(case in controls, "Diagnostic lacks an ordinary case")
        native = dict(read_ini(config.parent / f"{case}_diagnostic.ini")["SearchSSDIndex"])
        require(native.pop("logphasetime") == native.pop("logpathstats") == "true" and
                int(native.pop("dumpheads")) == plan.getint("Warmup") + plan.getint("QueryCount")
                and native == dict(controls[case]), "Diagnostic changes native search behavior")
    return cases, diagnostics, controls


def run(config):
    reject_environment_overrides()
    config = config.resolve(strict=True)
    plan = read_ini(config)["Experiment"]
    cases, diagnostics, controls = validate_controls(config, plan)
    index = prepare_view(plan, config)
    binary = identity(Path(plan["Binary"]), True)
    require(binary["sha256"] == plan["BinarySHA256"], "Frozen native executable differs")
    count, warmup, repeats = [plan.getint(key) for key in ("QueryCount", "Warmup", "Repeats")]
    require(count == warmup == 1000 and repeats == 3 and plan.getint("MeasureOffset") == 0,
            "Require the same fully warmed first1000 query protocol")
    source = Path(plan["SourceIndex"])
    before = source_inventory(source)
    graph_before = source_inventory(Path(plan["FlatHeadIndex"]))
    output = Path(plan["OutputDirectory"])
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "config"
    snapshot.mkdir()
    for file in [config, config.parent / plan["IndexLoader"], *
                 [config.parent / f"{case}.ini" for case in cases], *
                 [config.parent / f"{case}_diagnostic.ini" for case in diagnostics]]:
        shutil.copy2(file, snapshot / file.name)
    shutil.copy2(Path(__file__), output / Path(__file__).name)
    reference = json.loads(Path(plan["ReferenceH3"]).read_text())
    write_json(output / "provenance.json", {
        "source_inventory": before, "flat_graph_inventory": graph_before, "binary": binary,
        "config": identity(config, True), "queries": identity(Path(plan["Queries"]), True),
        "truth": identity(Path(plan["Truth"]), True), "reference": identity(Path(plan["ReferenceH3"]), True),
        "posting_policy": "Both native modes open the same symlink-resolved posting file and share the same scan path.",
    })
    write_json(output / "status.json", {"state": "running"})
    runs, works = [], {}
    sequence = [(case, repeat, False)
                for repeat in range(1, repeats + 1)
                for case in (cases if repeat % 2 else list(reversed(cases)))]
    sequence += [(case, 0, True) for case in diagnostics]
    try:
        preflight_count = plan.getint("PreflightQueryCount")
        require(preflight_count == 1, "Loader preflight must be exactly one native query")
        for case in cases[:2]:
            native = controls[case]
            command = [
                "numactl", f"--cpunodebind={plan.getint('CPUNode')}",
                f"--membind={plan.getint('MemoryNode')}", plan["Binary"],
                "--index", str(index), "--queries", plan["Queries"], "--truth", plan["Truth"],
                "--search-sweep-ini", str(snapshot / f"{case}.ini"), "--value-type", plan["ValueType"],
                "--topk", str(native.getint("ResultNum")), "--warmup", "0",
                "--measure-offset", "0", "--max-queries", str(preflight_count),
            ]
            logs = run_native(command, output, f"preflight_{case}", plan.getfloat("SampleSeconds"),
                              plan.getboolean("ExpectedDirectIO"))
            h3_records(logs, preflight_count, 0, native.getint("InternalResultNum"), False)
        for case, repeat, diagnostic in sequence:
            native = controls[case]
            search_name = f"{case}_diagnostic.ini" if diagnostic else f"{case}.ini"
            search = snapshot / search_name
            command = [
                "numactl", f"--cpunodebind={plan.getint('CPUNode')}",
                f"--membind={plan.getint('MemoryNode')}", plan["Binary"],
                "--index", str(index), "--queries", plan["Queries"], "--truth", plan["Truth"],
                "--search-sweep-ini", str(search), "--value-type", plan["ValueType"],
                "--topk", str(native.getint("ResultNum")), "--warmup", str(warmup),
                "--measure-offset", "0", "--max-queries", str(count),
            ]
            name = f"{case}_diagnostic" if diagnostic else f"{case}_r{repeat}"
            logs = run_native(command, output, name, plan.getfloat("SampleSeconds"),
                              plan.getboolean("ExpectedDirectIO"))
            row, phase = h3_records(logs, count, warmup, native.getint("InternalResultNum"), diagnostic)
            work = h3_core_work(row)
            if case in works:
                require(work == works[case], f"Native work changed between runs: {name}")
            else:
                works[case] = work
            if native["HeadNavigationMode"] == "H2Only":
                prior = [item["native"] for item in reference if
                         item["nprobe"] == native.getint("InternalResultNum")]
                if prior:
                    require(all(h3_core_work(item) == work for item in prior),
                            "Adding the flat graph changed H3's original recall or work")
            if diagnostic:
                require(phase["twoLayer"] == int(native["HeadNavigationMode"] == "H2Only"),
                        "Native path differs from the requested graph")
                if native["HeadNavigationMode"] == "H2Only":
                    require(phase["h2Iter"] == 1, "Hierarchy unexpectedly retried")
            runs.append({"case": case, "repeat": repeat, "diagnostic": diagnostic,
                         "nprobe": native.getint("InternalResultNum"),
                         "mode": native["HeadNavigationMode"], "native": row, "phases": phase})
            write_json(output / "runs.json", runs)
            print(f"{name}: recall={row['recall']:.6f} postings={row['postings_per_query']} "
                  f"pages={row['posting_page_reads_per_query']} qps={row['qps']:.3f}", flush=True)
        require(source_inventory(source) == before, "Original index payload changed")
        require(source_inventory(Path(plan["FlatHeadIndex"])) == graph_before, "Flat graph changed")
        require(identity(Path(plan["Binary"]), True) == binary, "Native executable changed")
        summary = []
        for case in cases:
            rows = [item["native"] for item in runs if item["case"] == case and not item["diagnostic"]]
            summary.append({
                "case": case, "nprobe": controls[case].getint("InternalResultNum"),
                "mode": controls[case]["HeadNavigationMode"], "recall": rows[0]["recall"],
                "mean_ms": statistics.mean(row["mean_latency_ms"] for row in rows),
                "median_qps": statistics.median(row["qps"] for row in rows),
                "postings": rows[0]["postings_per_query"], "pages": rows[0]["posting_page_reads_per_query"],
                "scanned_vectors": rows[0]["scanned_vectors_per_query"],
                "distance_computations": rows[0]["distance_computations_per_query"],
                "requested_bytes": rows[0]["posting_physical_bytes_per_query"],
                "phases": next((item["phases"] for item in runs
                                if item["case"] == case and item["diagnostic"]), None),
            })
        write_json(output / "summary.json", summary)
        write_json(output / "status.json", {"state": "completed", "runs": len(runs)})
    except (OSError, ValueError) as error:
        write_json(output / "status.json", {"state": "failed", "error": str(error)})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.prepare_only:
        config = args.config.resolve(strict=True)
        print(prepare_view(read_ini(config)["Experiment"], config))
    else:
        run(args.config)
