"""Preregister the shared-API native array experiment without touching prior evidence."""
import configparser
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[4] / "datasets/sift1m_zipf200_sparse193_numeric"
PARENT = DATA / "toolchains/h1_predicate_degree_batch_20260916"
PREVIOUS = DATA / "comparisons/h1_predicate_degree_batch_20260916"
NEW = DATA / "toolchains/h1_predicate_degree_searchsweep_20260916"
OUTPUT = DATA / "comparisons/h1_predicate_degree_searchsweep_20260916"
MAIN_HEADER = HERE.parents[1] / "NativeNProbeSweep.h"
GRID = [16, 24, 32, 48, 62, 80, 96, 128, 192, 256, 384]
SCENARIOS = ["unfilter", "broad_tag", "medium_tag", "extreme_tag", "numeric", "mixed_dnf"]
CASES = ["h1", "h3", "supplier"]


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def main():
    if NEW.exists() or OUTPUT.exists() or (HERE / "preregistration.json").exists():
        raise RuntimeError("Preserve existing runtime, preregistration and results")
    parent = json.loads((PARENT / "batch_provenance.json").read_text())
    for name, digest in parent["after"].items():
        assert sha(PARENT / "source" / name) == digest, name
    old_proof = json.loads((PREVIOUS / "provenance.json").read_text())
    assert sha(PARENT / "source/Release/spannaclbench") == old_proof["native_binary"]["sha256"]
    before = {str(p.relative_to(PARENT / "source")): sha(p)
              for p in sorted((PARENT / "source").rglob("*"))
              if p.is_file() and "Release" not in p.relative_to(PARENT / "source").parts}
    NEW.mkdir()
    shutil.copytree(PARENT / "source", NEW / "source", symlinks=True,
                    ignore=shutil.ignore_patterns("Release"))
    shutil.copy2(HERE / "SpannAclBench.cpp", NEW / "source/Tools/benchmarks/SpannAclBench.cpp")
    shutil.copy2(HERE / "NativeBatch.h", NEW / "source/AnnService/NativeBatch.h")
    shutil.copy2(MAIN_HEADER, NEW / "source/Tools/benchmarks/NativeNProbeSweep.h")
    after = {name: sha(NEW / "source" / name) for name in before}
    added = "Tools/benchmarks/NativeNProbeSweep.h"
    after[added] = sha(NEW / "source" / added)
    assert after[added] == sha(MAIN_HEADER)
    changed = sorted(name for name in before if before[name] != after[name])
    assert changed == ["AnnService/NativeBatch.h", "Tools/benchmarks/SpannAclBench.cpp"], changed
    write(NEW / "batch_provenance.json", {
        "parent_toolchain": str(PARENT), "parent_results": str(PREVIOUS),
        "parent_binary_sha256": old_proof["native_binary"]["sha256"],
        "before": before, "after": after, "changed_files": changed, "added_files": [added],
        "shared_parser": {"path": str(MAIN_HEADER), "sha256": sha(MAIN_HEADER), "verbatim_copy": True},
        "change": "Use MAIN NativeNProbeSweep::Parse and exclusive SearchSweep.NProbe array API. "
                  "Retain full-query capture/admission hooks, actual LoadAll instrumentation and "
                  "between-probe transient workspace reset. No core algorithm source changes.",
        "supplier_degree_semantics": "predicate_valid_neighbors",
        "sweep_execution": "single_load_nprobe_array", "nprobe_ini_api": "SearchSweep.NProbe",
    })
    configs = HERE / "configs"
    configs.mkdir()
    for case in CASES:
        text = (HERE.parent / f"predicate_degree_native/configs/{case}_24_plain.ini").read_text()
        assert text.count("InternalResultNum=24\n") == 1 and "[SearchSweep]" not in text
        for repeat in (1, 2):
            order = GRID if repeat == 1 else GRID[::-1]
            (configs / f"{case}_r{repeat}.ini").write_text(
                text + "\n[SearchSweep]\nNProbe=[" + ",".join(map(str, order)) + "]\n")
        for suffix, order in (("asc", [16, 24, 384]), ("desc", [384, 24, 16])):
            (configs / f"{case}_fixture_{suffix}.ini").write_text(
                text + "\n[SearchSweep]\nNProbe=[" + ",".join(map(str, order)) + "]\n")
        for probe in (16, 24, 384):
            (configs / f"{case}_scalar_{probe}.ini").write_text(
                text.replace("InternalResultNum=24\n", f"InternalResultNum={probe}\n"))
    schedule = json.loads((HERE.parent / "predicate_degree_batch/schedule.json").read_text())
    write(HERE / "schedule.json", schedule)
    source = configparser.ConfigParser()
    source.read(HERE.parent / "predicate_degree_batch/experiment.ini")
    plan = dict(source["Experiment"])
    plan.update(binary=str(NEW / "source/Release/spannaclbench"), toolchain=str(NEW),
                outputdirectory=str(OUTPUT), previousresults=str(PREVIOUS))
    config = configparser.ConfigParser()
    config["Experiment"] = plan
    with (HERE / "experiment.ini").open("x") as stream:
        config.write(stream)
    write(HERE / "preregistration.json", {
        "grid": GRID, "scenarios": SCENARIOS, "cases": CASES, "unique_points": 198,
        "ordinary_index_load_invocations": 36, "ordinary_measurement_batches": 396,
        "repetitions": 2, "query_count": 1000, "warmup_per_probe": 1000, "untimed_capture_per_probe": 1000,
        "array_fixture_invocations": 36, "new_scalar_fixture_invocations": 9,
        "fixture_query_count": 8, "fixture_warmup_per_probe": 8,
        "fixture_entrypoints": "Forward arrays use --search-sweep-ini; reverse arrays and scalars use --search-ini.",
        "array_policy": "Only [SearchSweep] NProbe uses MAIN NativeNProbeSweep::Parse verbatim; "
                        "InternalResultNum remains scalar. Reject unknown SearchSweep keys and arrays in base "
                        "--search-ini when active --search-sweep-ini is present.",
        "full_capture_validation": "Stream SHA256 of every fresh1000-query native capture and require exact "
                                   "byte equality to the independently semantically audited parent batch capture. "
                                   "Reuse its validity verdict only after equality; compare all fresh native SSD work. "
                                   "All8-query fixtures receive fresh full semantic audits. Native timed/capture "
                                   "IDs and work checks remain unchanged. No parent timings reused.",
        "timing_protocol": "One load per fixed case/scenario/repetition; independent capture/warmup/measurement "
                           "at each native probe, rotated groups and reversed probes in repetition2.",
        "supplier_degree_semantics": "predicate_valid_neighbors",
        "sweep_execution": "single_load_nprobe_array", "nprobe_ini_api": "SearchSweep.NProbe",
        "configs": {p.name: sha(p) for p in sorted(configs.iterdir())},
        "schedule_sha256": sha(HERE / "schedule.json"),
        "experiment_sha256": sha(HERE / "experiment.ini"),
        "parent_summary_sha256": sha(PREVIOUS / "summary.json"),
        "main_parser_sha256": sha(MAIN_HEADER),
    })
    print("Preregistered shared SearchSweep.NProbe API:36 groups /396 batches /198 points.")


if __name__ == "__main__":
    main()
