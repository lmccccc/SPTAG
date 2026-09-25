"""Materialize a staged benchmark; never change the accepted native search library."""
import configparser
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[4] / "datasets/sift1m_zipf200_sparse193_numeric"
ACCEPTED = HERE.parent / "native_default_admission_20260917"
GRID = [16, 24, 32, 48, 62, 80, 96, 128, 192, 256, 384]
STAGES = {"unfilter_broad": ["unfilter", "broad_tag"],
          "medium_extreme": ["medium_tag", "extreme_tag"],
          "numeric_mixed": ["numeric", "mixed_dnf"]}


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write(path, obj):
    with path.open("x") as stream:
        json.dump(obj, stream, indent=2)
        stream.write("\n")


def main():
    if (HERE / "preregistration.json").exists():
        raise RuntimeError("Already preregistered; preserve files")
    accepted_tool = DATA / "toolchains/h1_native_default_admission_20260917"
    accepted_result = DATA / "comparisons/h1_native_default_admission_20260917"
    assert sha(accepted_result / "summary.json") == "6917dd7c20901c77dce946fd81832383f0bcff8cb2aa30e7c1080433f9ef2c8d"
    proof = json.loads((accepted_tool / "native_reuse_provenance.json").read_text())
    for name, digest in proof["after"].items():
        assert sha(accepted_tool / "source" / name) == digest, name
    for name in ("SpannAclBench.cpp", "FullHooks.h"):
        shutil.copy2(ACCEPTED / name, HERE / name)
    config = configparser.ConfigParser()
    config.read(ACCEPTED / "experiment.ini")
    config["Experiment"]["OutputDirectory"] = str(DATA / "comparisons/h1_native_ratio_curve_20260917_v3")
    config["Experiment"]["Binary"] = str(DATA / "toolchains/h1_native_ratio_curve_20260917/spannaclbench")
    config["Experiment"]["AcceptedToolchain"] = str(accepted_tool)
    config["Experiment"]["AcceptedResults"] = str(accepted_result)
    config["Experiment"]["AuthenticArrayResults"] = str(DATA / "comparisons/h1_predicate_degree_searchsweep_20260916")
    config["Experiment"]["Cases"] = "h1,h3,supplier"
    config["Experiment"]["Scenarios"] = ",".join(s for group in STAGES.values() for s in group)
    config["Experiment"]["Grid"] = ",".join(map(str, GRID))
    with (HERE / "experiment.ini").open("x") as stream:
        config.write(stream)
    (HERE / "configs").mkdir()
    for case in ("h1", "h3", "supplier"):
        for order, grid in (("r1", GRID), ("r2", GRID[::-1]),
                            ("fixture_asc", [16, 24, 384]), ("fixture_desc", [384, 24, 16])):
            ini = configparser.ConfigParser()
            ini.optionxform = str
            ini.read(ACCEPTED / "configs/supplier_plain.ini")
            ini["SearchSSDIndex"]["ShortcutMode"] = "supplier" if case == "supplier" else "ordinary"
            ini["SearchSSDIndex"]["HeadNavigationMode"] = "H2Only" if case == "h3" else "H1Only"
            ini["SearchSweep"]["NProbe"] = "[" + ",".join(map(str, grid)) + "]"
            with (HERE / "configs" / f"{case}_{order}.ini").open("x") as stream:
                ini.write(stream, space_around_delimiters=False)
    schedule = []
    for stage, scenarios in STAGES.items():
        for repeat in (1, 2):
            order = scenarios if repeat == 1 else scenarios[::-1]
            for position, scenario in enumerate(order):
                cases = ["h1", "h3", "supplier"] if repeat == 1 else ["supplier", "h1", "h3"]
                cases = cases[position:] + cases[:position]
                for case in cases:
                    schedule.append(dict(stage=stage, scenario=scenario, case=case, repeat=repeat,
                                         probes=GRID if repeat == 1 else GRID[::-1]))
    write(HERE / "schedule.json", schedule)
    write(HERE / "preregistration.json", {
        "stages": STAGES, "scenarios": config["Experiment"]["Scenarios"].split(","),
        "cases": ["h1", "h3", "supplier"], "grid": GRID, "repeats": 2,
        "unique_points": 198, "ordinary_batches": 396, "ordinary_index_loads": 36,
        "stage_unique_points": 66, "stage_ordinary_batches": 132, "stage_ordinary_index_loads": 12,
        "warmup_per_probe": 1000, "measured_per_probe": 1000, "threads": 1, "numa": 2,
        "topk": 10, "MaxCheck": 2048, "HierarchyMaxCheck": 512, "HierarchyInitialProbeRatio": .666666,
        "page_limit": 15, "O_DIRECT": True, "retained_ratio": .5, "minimum_physical_degree": 16,
        "supplier_degree_semantics": "retained_eligible_ratio",
        "sweep_execution": "single_load_nprobe_array", "nprobe_ini_api": "SearchSweep.NProbe",
        "accepted_summary_sha256": sha(accepted_result / "summary.json"),
        "schedule_sha256": sha(HERE / "schedule.json"),
        "configs": {p.name: sha(p) for p in sorted((HERE / "configs").iterdir())},
        "execution": "Explicit stage only; no auto-continuation. Canonical summary only after198 complete pairs.",
        "timing": "No fine query clocks/profiles. Coarse native load/capture/warmup/batch/serialization clocks only.",
        "compatibility": "Benchmark-only H3 admission validation/observation and coarse operation logging; "
                         "link unchanged accepted native libraries and wrapper object; no algorithm/hook changes.",
    })
    runtime = DATA / "toolchains/h1_native_ratio_curve_20260917"
    runtime.mkdir()
    libraries = list((accepted_tool / "source/Release").glob("*.a"))
    wrapper = accepted_tool / "build/AnnService/CMakeFiles/spannaclbench.dir/__/Wrappers/src/CoreInterface.cpp.o"
    write(runtime / "accepted_provenance.json", {
        "native_source": proof, "accepted_toolchain": str(accepted_tool),
        "accepted_binary": {"path": str(accepted_tool / "source/Release/spannaclbench"),
                            "sha256": sha(accepted_tool / "source/Release/spannaclbench")},
        "linked_files": [{"path": str(p), "sha256": sha(p)} for p in libraries + [wrapper]],
        "source_archive": {"path": str(accepted_result / "snapshot/source.tar.gz"),
                           "sha256": sha(accepted_result / "snapshot/source.tar.gz")}})


if __name__ == "__main__":
    main()
