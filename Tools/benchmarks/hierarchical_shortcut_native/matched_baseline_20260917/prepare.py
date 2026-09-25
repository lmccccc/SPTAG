"""Register one matched protocol and an owned buffered loader view."""
import configparser
import hashlib
import json
from pathlib import Path
import shutil
from prepare_original import HERE, REPO, DATA, TOOL, CACHE

GRID = [16, 24, 32, 48, 62, 80, 96, 128, 192, 256, 384]
CASES = ["h1_original", "h1", "h3", "supplier"]
STAGES = {"unfilter_broad": ["unfilter", "broad_tag"],
          "medium_extreme": ["medium_tag", "extreme_tag"],
          "numeric_mixed": ["numeric", "mixed_dnf"]}
OUTPUT = DATA / "comparisons/matched_baseline_20260917"
CURRENT = DATA / "toolchains/h1_native_default_admission_20260917"


def sha(path):
    out = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            out.update(block)
    return out.hexdigest()


def write(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def native_ini(path, config):
    with path.open("x") as stream:
        config.write(stream, space_around_delimiters=False)


def main():
    OUTPUT.mkdir()
    view = TOOL / "buffered_view"
    view.mkdir()
    source = DATA / "build_runs/h1_same_posting_20260915/search_view"
    for p in source.iterdir():
        if p.name != "tenant_0":
            (view / p.name).symlink_to(p.resolve())
    tenant = view / "tenant_0"
    tenant.mkdir()
    for p in (source / "tenant_0").iterdir():
        if p.name != "indexloader.ini":
            (tenant / p.name).symlink_to(p.resolve())
    loader = configparser.ConfigParser()
    loader.optionxform = str
    loader.read(source / "tenant_0/indexloader.ini")
    loader["Base"]["IndexDirectory"] = str(tenant)
    loader["BuildSSDIndex"]["UseDirectIO"] = "false"
    native_ini(tenant / "indexloader.ini", loader)
    audit = configparser.ConfigParser()
    audit.read(CACHE / "original_h1_audit_20260917/buffered_view/tenant_0/indexloader.ini")
    actual = configparser.ConfigParser()
    actual.read(tenant / "indexloader.ini")
    audit["Base"]["indexdirectory"] = str(tenant)
    assert {s: dict(audit[s]) for s in audit.sections()} == {s: dict(actual[s]) for s in actual.sections()}
    workloads = json.loads((DATA / "query/workloads.json").read_text())
    protocol = {"index": str(view), "io": "buffered", "warmup": 1000, "queries": 1000,
        "measure_offset": 0, "topk": 10, "MaxCheck": 2048, "HierarchyMaxCheck": 512,
        "HierarchyInitialProbeRatio": .666666, "pages": 15, "query_threads": 1,
        "numa_cpu_node": 2, "numa_memory_node": 2, "grid": GRID, "repetitions": 2,
        "sweep_execution": "single_load_nprobe_array", "nprobe_ini_api": "SearchSweep.NProbe",
        "cases": CASES, "stages": STAGES, "supplier_retained_ratio": .5, "supplier_minimum_degree": 16}
    write(HERE / "protocol.json", protocol)
    configs = HERE / "configs"
    configs.mkdir()
    template = configparser.ConfigParser()
    template.optionxform = str
    template.read(HERE.parent / "native_ratio_curve_20260917/configs/h1_r1.ini")
    for stage, scenarios in STAGES.items():
        for scenario in scenarios:
            for case in CASES:
                for label, probes in (("r1", GRID), ("r2", GRID[::-1]), ("smoke", [24])):
                    if label == "smoke" and stage != "unfilter_broad":
                        continue
                    cfg = configparser.ConfigParser()
                    cfg.optionxform = str
                    cfg["Benchmark"] = {
                        "Index": str(view), "Queries": str(DATA / "query/query_vectors.npy"),
                        "Truth": workloads["truth"][scenario]["ids"], "Case": case, "Scenario": scenario,
                        "Warmup": "1000", "MaxQueries": "1000", "MeasureOffset": "0",
                        "NumaNode": "2", "IO": "buffered", "Predicate": "empty", "PredicateFile": ""}
                    if scenario in workloads["flat_query_tags"]:
                        cfg["Benchmark"].update(Predicate="categorical",
                            PredicateFile=workloads["flat_query_tags"][scenario])
                    elif scenario != "unfilter":
                        cfg["Benchmark"].update(Predicate="dnf", PredicateFile=workloads["query_dnf"][
                            "numeric" if scenario == "numeric" else "mixed"])
                    cfg["SearchSSDIndex"] = dict(template["SearchSSDIndex"])
                    cfg["SearchSSDIndex"]["HeadNavigationMode"] = "H2Only" if case == "h3" else "H1Only"
                    if case == "h1_original":
                        for key in list(cfg["SearchSSDIndex"]):
                            if key.startswith("Shortcut"):
                                del cfg["SearchSSDIndex"][key]
                    else:
                        cfg["SearchSSDIndex"]["ShortcutMode"] = "supplier" if case == "supplier" else "ordinary"
                        cfg["SearchSSDIndex"]["ShortcutCapture"] = "false"
                    cfg["SearchSweep"] = {"NProbe": "[" + ",".join(map(str, probes)) + "]"}
                    native_ini(configs / f"{scenario}_{case}_{label}.ini", cfg)
    paths = json.loads((DATA / "comparisons/h1_native_ratio_curve_20260917_v3/input_hashes.json").read_text())
    protected = []
    for item in paths:
        path = Path(item["path"])
        digest = sha(path)
        assert digest == item["sha256"], path
        stat = path.stat()
        protected.append(dict(path=str(path), sha256=digest, bytes=stat.st_size, mtime_ns=stat.st_mtime_ns))
    write(OUTPUT / "input_hashes.json", protected)
    fingerprint = hashlib.sha256(json.dumps(
        [(v["path"], v["sha256"]) for v in protected], sort_keys=True).encode()).hexdigest()
    write(OUTPUT / "registration.json", {"protocol": protocol, "protocol_sha256": sha(HERE / "protocol.json"),
        "index_fingerprint": fingerprint, "view_loader_sha256": sha(tenant / "indexloader.ini"),
        "view_targets": {p.name: str(p.resolve()) for p in tenant.iterdir() if p.is_symlink()},
        "config_hashes": {p.name: sha(p) for p in sorted(configs.iterdir())},
        "historical_points_excluded": True, "original_scope": "Authenticated Sep9 pre-supplier source, "
        "not claimed to be the precise historical blue-curve executable",
        "unique_full_points": 264, "full_ordinary_batches": 528, "full_native_loads": 48,
        "smoke_points": 8, "smoke_ordinary_batches": 16, "smoke_native_loads": 16})
    write(OUTPUT / "status.json", {"completed_stages": [], "smoke_complete": False,
                                 "automatic_continuation": False})


if __name__ == "__main__":
    main()
