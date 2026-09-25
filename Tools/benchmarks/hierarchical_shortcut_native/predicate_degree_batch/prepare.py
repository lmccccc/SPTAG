"""Freeze a native load-once array runtime and its complete batch experiment."""
import configparser
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[4] / "datasets/sift1m_zipf200_sparse193_numeric"
PARENT = DATA / "toolchains/h1_predicate_degree_nprobe_20260916"
PREVIOUS = DATA / "comparisons/h1_predicate_degree_nprobe_20260916"
NEW = DATA / "toolchains/h1_predicate_degree_batch_20260916"
OUTPUT = DATA / "comparisons/h1_predicate_degree_batch_20260916"
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
    parent = json.loads((PARENT / "predicate_degree_provenance.json").read_text())
    for name, digest in parent["after"].items():
        assert sha(PARENT / "source" / name) == digest, name
    old_provenance = json.loads((PREVIOUS / "provenance.json").read_text())
    assert sha(PARENT / "source/Release/spannaclbench") == old_provenance["native_binary"]["sha256"]
    before = {str(p.relative_to(PARENT / "source")): sha(p)
              for p in sorted((PARENT / "source").rglob("*"))
              if p.is_file() and "Release" not in p.relative_to(PARENT / "source").parts}
    NEW.mkdir()
    shutil.copytree(PARENT / "source", NEW / "source", symlinks=True,
                    ignore=shutil.ignore_patterns("Release"))
    shutil.copy2(HERE / "SpannAclBench.cpp", NEW / "source/Tools/benchmarks/SpannAclBench.cpp")
    shutil.copy2(HERE / "NativeBatch.h", NEW / "source/AnnService/NativeBatch.h")
    wrapper = NEW / "source/Wrappers/src/CoreInterface.cpp"
    text = wrapper.read_text()
    old = "bool TenantIndexManager::LoadAll(const char* p_baseDir)\n{"
    assert text.count(old) == 1
    text = '#include "NativeBatch.h"\n' + text.replace(old, old + """
    ++NativeBatch::indexLoadCalls;
    std::cout << "NATIVE_INDEX_LOAD calls=" << NativeBatch::indexLoadCalls << "\\n";
""")
    wrapper.write_text(text)
    after = {n: sha(NEW / "source" / n) for n in before}
    after["AnnService/NativeBatch.h"] = sha(NEW / "source/AnnService/NativeBatch.h")
    changed = sorted(n for n in before if before[n] != after[n])
    assert changed == ["Tools/benchmarks/SpannAclBench.cpp", "Wrappers/src/CoreInterface.cpp"], changed
    write(NEW / "batch_provenance.json", {
        "parent_toolchain": str(PARENT), "parent_results": str(PREVIOUS),
        "parent_binary_sha256": old_provenance["native_binary"]["sha256"],
        "before": before, "after": after, "changed_files": changed,
        "added_files": ["AnnService/NativeBatch.h"],
        "change": "Native INI array expands existing in-process search loop; instrument real LoadAll; "
                  "drop cached native search workspace between probes so heap capacity cannot leak across order.",
        "supplier_degree_semantics": "predicate_valid_neighbors",
        "core_search_and_supplier_sources_byte_unchanged": True,
    })
    configs = HERE / "configs"
    configs.mkdir()
    for case in CASES:
        template = HERE.parent / f"predicate_degree_native/configs/{case}_24_plain.ini"
        text = template.read_text()
        assert text.count("InternalResultNum=24\n") == 1
        for repeat in (1, 2):
            order = GRID if repeat == 1 else GRID[::-1]
            values = "[" + ",".join(map(str, order)) + "]"
            (configs / f"{case}_r{repeat}.ini").write_text(
                text.replace("InternalResultNum=24\n", f"InternalResultNum={values}\n"))
        for suffix, order in (("asc", [16, 24, 384]), ("desc", [384, 24, 16])):
            (configs / f"{case}_fixture_{suffix}.ini").write_text(
                text.replace("InternalResultNum=24\n", "InternalResultNum=[" + ",".join(map(str, order)) + "]\n"))
        for probe in (16, 24, 384):
            (configs / f"{case}_scalar_{probe}.ini").write_text(
                text.replace("InternalResultNum=24\n", f"InternalResultNum={probe}\n"))
    schedule = []
    for repeat in (1, 2):
        scenarios = SCENARIOS if repeat == 1 else SCENARIOS[2:] + SCENARIOS[:2]
        for i, scenario in enumerate(scenarios):
            shift = (i + repeat - 1) % 3
            cases = CASES[shift:] + CASES[:shift]
            if repeat == 2:
                cases = cases[::-1]
            for case in cases:
                schedule.append({"scenario": scenario, "case": case, "repeat": repeat,
                                 "probes": GRID if repeat == 1 else GRID[::-1]})
    write(HERE / "schedule.json", schedule)
    source = configparser.ConfigParser()
    source.read(HERE.parent / "predicate_degree_native/experiment.ini")
    plan = dict(source["Experiment"])
    plan.update(binary=str(NEW / "source/Release/spannaclbench"), toolchain=str(NEW),
                outputdirectory=str(OUTPUT), previousresults=str(PREVIOUS))
    config = configparser.ConfigParser()
    config["Experiment"] = plan
    with (HERE / "experiment.ini").open("x") as stream:
        config.write(stream)
    write(HERE / "preregistration.json", {
        "grid": GRID, "scenarios": SCENARIOS, "cases": CASES,
        "ordinary_index_load_invocations": 36, "ordinary_measurement_batches": 396,
        "unique_points": 198, "repetitions": 2,
        "query_count": 1000, "warmup_per_probe": 1000, "untimed_capture_per_probe": 1000,
        "fixture_query_count": 8, "fixture_warmup_per_probe": 8,
        "array_fixture_invocations": 36, "new_scalar_fixture_invocations": 9,
        "fixture_scalar_reference": "Actual completed per-point native captures at16/24/384, first8 queries; "
                                    "nine new scalar executions additionally verify scalar compatibility.",
        "array_policy": "Ordered positive decimal integers, optional brackets; empty, malformed, duplicate, "
                        "overflowing or below-ResultNum values rejected before index load.",
        "timing_protocol": "One loaded index per fixed mode/scenario/repetition; independent warmup and "
                           "measurement for each probe; caches/index persist across probes. Fresh batch data only.",
        "supplier_degree_semantics": "predicate_valid_neighbors",
        "configs": {p.name: sha(p) for p in sorted(configs.iterdir())},
        "schedule_sha256": sha(HERE / "schedule.json"),
        "previous_status_observed": json.loads((PREVIOUS / "status.json").read_text()),
    })
    print("Prepared36 native load-once groups /396 measurement batches;45 bounded validation invocations.")


if __name__ == "__main__":
    main()
