"""Materialize the fixed native nprobe sweep and isolate the capacity-guard correction."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
DATA = ROOT / "datasets/sift1m_zipf200_sparse193_numeric"
OLD = DATA / "toolchains/h1_supplier_nprobe_20260916"
NEW = DATA / "toolchains/h1_predicate_degree_nprobe_20260916"
OUTPUT = DATA / "comparisons/h1_predicate_degree_nprobe_20260916"
GRID = [16, 24, 32, 48, 62, 80, 96, 128, 192, 256, 384]
SCENARIOS = ["unfilter", "broad_tag", "medium_tag", "extreme_tag", "numeric", "mixed_dnf"]
CASES = ["h1", "h3", "supplier"]


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"Expected exactly one {old!r}")
    return text.replace(old, new)


def main():
    if NEW.exists() or OUTPUT.exists() or (HERE / "preregistration.json").exists():
        raise RuntimeError("Refusing to overwrite any existing sweep evidence")
    manifest = json.loads((OLD / "capacity_guard_provenance.json").read_text())
    for name, expected in manifest["after"].items():
        assert sha(OLD / "source" / name) == expected, name
    old_results = DATA / "comparisons/h1_supplier_nprobe_20260916"
    parent_provenance = json.loads((old_results / "provenance.json").read_text())
    assert sha(OLD / "source/Release/spannaclbench") == parent_provenance["native_binary"]["sha256"]
    old_sources = {str(p.relative_to(OLD / "source")): sha(p)
                   for p in sorted((OLD / "source").rglob("*"))
                   if p.is_file() and "Release" not in p.relative_to(OLD / "source").parts}
    NEW.mkdir()
    shutil.copytree(OLD / "source", NEW / "source", symlinks=True,
                    ignore=shutil.ignore_patterns("Release"))
    subprocess.run([sys.executable, str(HERE / "integrate.py"), str(NEW / "source")], check=True)
    changed = {name for name, digest in old_sources.items() if sha(NEW / "source" / name) != digest}
    assert changed == {"AnnService/FullHooks.h", "AnnService/Supplier.h", "AnnService/NativeAdapter.h",
                       "AnnService/src/Core/BKT/BKTIndex.cpp", "Tools/benchmarks/SpannAclBench.cpp"}, changed
    for name in ("capacity_guard_provenance.json", "parent_final_source_manifest.json",
                 "parent_authentication.json", "parent_supplier-tests-pass.log"):
        shutil.copy2(OLD / name, NEW / f"parent_{name}")
    write_json(NEW / "predicate_degree_provenance.json", {
        "parent_toolchain": str(OLD), "parent_binary_sha256": parent_provenance["native_binary"]["sha256"],
        "parent_source_manifest_sha256": sha(OLD / "capacity_guard_provenance.json"),
        "before": old_sources,
        "after": {name: sha(NEW / "source" / name) for name in old_sources},
        "changed_files": sorted(changed),
        "change": "Predicate-valid distinct ordinary adjacency controls degree16 independent of visited. "
                  "Supplied connectivity deduplicates ordinary/supplied IDs including visited eligible nodes. "
                  "Startup examines and offers anchor ordinary edges before the same low-degree supplier. "
                  "Qualification, budgets, whole-row semantics and variable-capacity harness unchanged.",
        "supplier_degree_semantics": "predicate_valid_neighbors",
    })
    source = HERE.parent
    templates = {"h1": source / "full/ordinary_plain.ini",
                 "h3": source / "full_scenarios/h3_plain.ini",
                 "supplier": source / "startup_native/supplier_plain.ini"}
    configs = HERE / "configs"
    configs.mkdir()
    for case, template in templates.items():
        text = template.read_text()
        for probe in GRID:
            plain = replace_once(text, "InternalResultNum=24\n", f"InternalResultNum={probe}\n")
            (configs / f"{case}_{probe}_plain.ini").write_text(plain)
            if probe in (16, 384):
                profile = replace_once(plain, "LogPhaseTime=false\n", "LogPhaseTime=true\n")
                profile = replace_once(profile, "ShortcutProfile=false\n", "ShortcutProfile=true\n")
                (configs / f"{case}_{probe}_fixture.ini").write_text(profile)
    for case in ("h1", "h3"):
        name = "flat24" if case == "h1" else "h3_24"
        text = (source.parent / f"configs/sift1m_same_posting/{name}_diagnostic.ini").read_text()
        for probe in (16, 24, 384):
            (configs / f"{case}_{probe}_frozen.ini").write_text(
                replace_once(text, "InternalResultNum=24\n", f"InternalResultNum={probe}\n"))
    schedule = []
    for scenario in SCENARIOS:
        for probe in (16, 24, 384):
            for case in ("h1", "h3"):
                schedule.append(dict(kind="frozen", scenario=scenario, case=case, nprobe=probe, repeat=0))
        for probe in (16, 384):
            for case in CASES:
                schedule.append(dict(kind="fixture", scenario=scenario, case=case, nprobe=probe, repeat=0))
    for repeat in (1, 2):
        for pi, probe in enumerate(GRID if repeat == 1 else GRID[::-1]):
            shift = (pi + 2 * (repeat - 1)) % len(SCENARIOS)
            scenarios = SCENARIOS[shift:] + SCENARIOS[:shift]
            if repeat == 2:
                scenarios = scenarios[::-1]
            for si, scenario in enumerate(scenarios):
                shift = (pi + si) % len(CASES)
                cases = CASES[shift:] + CASES[:shift]
                if repeat == 2:
                    cases = cases[::-1]
                for case in cases:
                    schedule.append(dict(kind="plain", scenario=scenario, case=case,
                                         nprobe=probe, repeat=repeat))
    assert len(schedule) == 468
    write_json(HERE / "schedule.json", schedule)
    parent_plan = source / "startup_native/experiment.ini"
    import configparser
    ini = configparser.ConfigParser()
    ini.read(parent_plan)
    plan = dict(ini["Experiment"])
    plan.update(binary=str(NEW / "source/Release/spannaclbench"),
                toolchain=str(NEW), outputdirectory=str(OUTPUT),
                priorresults=str(DATA / "comparisons/h1_startup_20260916"),
                grid=",".join(map(str, GRID)), cases=",".join(CASES),
                previoussweep=str(old_results),
                physicalgraph=str(DATA / "build_runs/h1_same_posting_20260915/FlatHeadIndex/graph.bin"))
    ini = configparser.ConfigParser()
    ini["Experiment"] = plan
    with (HERE / "experiment.ini").open("x") as stream:
        ini.write(stream)
    write_json(HERE / "preregistration.json", {
        "grid": GRID, "scenarios": SCENARIOS, "cases": CASES, "points": 198,
        "ordinary_repetitions": 2, "ordinary_processes": 396,
        "frozen_boundary_and_24_processes": 36, "profile_boundary_fixture_processes": 36,
        "ordinary_queries": 1000, "ordinary_warmup": 1000, "untimed_capture_queries": 1000,
        "fixture_queries": 8, "fixture_warmup": 0, "query_threads": 1, "numa_cpu_and_memory": 2,
        "only_search_parameter_swept": "InternalResultNum",
        "supplier_degree_semantics": "predicate_valid_neighbors",
        "native_maxcheck": 2048, "hierarchy_maxcheck": 512, "initial_ratio": "0.666666",
        "result_num": 10, "page_limit": 15, "effective_degree": 16,
        "primary_qps": "1000 / arithmetic mean of the two ordinary mean_latency_ms values",
        "order": "Probe order reverses in repetition2; scenario and case rotations are fixed in schedule.json.",
        "threshold_selection": "Fastest observed mean-QPS point reaching Recall@10 >=90% or >=95%; "
                               "no interpolation, missing thresholds explicitly unreached.",
        "capture_storage": "Lossless gzip of native JSONL after validation; no native data substituted.",
        "configs": {p.name: sha(p) for p in sorted(configs.iterdir())},
        "schedule_sha256": sha(HERE / "schedule.json"),
        "experiment_sha256": sha(HERE / "experiment.ini"),
        "templates": {k: {"path": str(v), "sha256": sha(v)} for k, v in templates.items()},
    })
    print(f"Preregistered 198 points / 396 ordinary runs, 72 diagnostics; isolated source: {NEW}")


if __name__ == "__main__":
    main()
