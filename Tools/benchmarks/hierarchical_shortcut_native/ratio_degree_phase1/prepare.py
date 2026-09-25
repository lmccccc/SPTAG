"""Freeze only the bounded ratio Phase1 implementation and focused native protocol."""
import configparser
import hashlib
import json
from pathlib import Path
import re
import shutil

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[4] / "datasets/sift1m_zipf200_sparse193_numeric"
PARENT = DATA / "toolchains/h1_predicate_degree_searchsweep_20260916"
NEW = DATA / "toolchains/h1_ratio_phase1_20260917"
OUTPUT = DATA / "comparisons/h1_ratio_phase1_20260917"


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
        raise RuntimeError("Preserve existing Phase1 artifacts")
    proof = json.loads((PARENT / "batch_provenance.json").read_text())
    for name, digest in proof["after"].items():
        assert sha(PARENT / "source" / name) == digest, name
    NEW.mkdir()
    shutil.copytree(PARENT / "source", NEW / "source", symlinks=True,
                    ignore=shutil.ignore_patterns("Release"))
    files = {f"AnnService/{name}": HERE / name for name in
             ("Supplier.h", "NativeAdapter.h", "FullHooks.h", "Signature.h")}
    files["AnnService/src/Core/BKT/BKTIndex.cpp"] = HERE / "BKTIndex.cpp"
    files["Tools/benchmarks/SpannAclBench.cpp"] = HERE / "SpannAclBench.cpp"
    for name, source in files.items():
        shutil.copy2(source, NEW / "source" / name)
    after = {name: sha(NEW / "source" / name) for name in proof["after"]}
    changed = sorted(name for name in after if after[name] != proof["after"][name])
    assert changed == sorted(set(files) - {"AnnService/Signature.h"}), changed
    write(NEW / "ratio_provenance.json", {
        "parent_toolchain": str(PARENT), "parent_binary_sha256": sha(PARENT / "source/Release/spannaclbench"),
        "before": proof["after"], "after": after, "changed_files": changed,
        "shared_parser_unchanged": True, "source_policy": "d>=16 and e/d<0.5, target ceil(0.5*d)",
        "optimization": "No ordinary per-pop unordered-set/vector allocations; no unconditional upper-state "
                        "clearing; no redundant Score/admission qualification calls; use exact native distance "
                        "function pointer when present. Keep all result/own-point heaps and native queue bounds.",
        "no_graph_degree_cache_or_vector_copy": True,
    })
    tests = (HERE.parent / "predicate_degree_native/SupplierTests.cpp").read_text()
    tests = re.sub(r'e\.Supply\(([^,\n]+), ([^,\n]+), ([^,\n]+),',
                   r'e.Supply(\1, \2, \3, 32,', tests)
    start = tests.index("void ConnectivityFixtures() {")
    end = tests.index("void NativeFixtures() {", start)
    tests = tests[:start] + (HERE / "RatioFixtures.inc").read_text() + "\n" + tests[end:]
    needle = "    Check(sentinels > 0);\n"
    assert tests.count(needle) == 1
    tests = tests.replace(needle, needle + """
    const int startupWidth = index.GetNeighborhoodSize();
    std::vector<int> beforeStartup(count * startupWidth);
    for (int id = 0; id < count; ++id)
        for (int slot = 0; slot < startupWidth; ++slot) {
            beforeStartup[id * startupWidth + slot] = index.GetGraph()[id][slot];
            index.GetMutableGraph()[id][slot] = (id + slot + 1) % count;
        }
""")
    needle = "    COMMON::QueryResultSet<float> restored(data.data() + 20 * 8, 24);"
    assert tests.count(needle) == 1
    tests = tests.replace(needle, """    for (int id = 0; id < count; ++id)
        for (int slot = 0; slot < startupWidth; ++slot)
            index.GetMutableGraph()[id][slot] = beforeStartup[id * startupWidth + slot];
""" + needle)
    needle = "    for (int id = 0; id < count; ++id)\n        for (int slot = 0; slot < width; ++slot)\n            index.GetMutableGraph()[id][slot] = saved[id * width + slot];"
    assert tests.count(needle) == 1
    tests = tests.replace(needle, """
    for (int id = 0; id < count; ++id)
        for (int slot = 0; slot < width; ++slot)
            index.GetMutableGraph()[id][slot] = slot < 15 ? (id + slot + 1) % count : -1;
    ShortcutFull::Last() = {};
    COMMON::QueryResultSet<float> shortScope(data.data() + 20 * 8, 24);
    H1Supplier::NativeSearch(&index, &shortScope, catalogs, postings, maps,
        [](int) { return false; }, [](int, const float*) { return false; },
        [](int, bool) { return 0; }, [](int, int) { return true; });
    Check(ShortcutFull::Last().supplyCalls == 0 && ShortcutFull::Last().supplyMembers == 0 &&
          shortScope.GetResult(0)->VID == -1 && ShortcutFull::Last().supplyStarts == 1);
""" + needle)
    tests = tests.replace('"predicate-low, short, duplicate, self, invalid and sentinel rows; "',
                          '"ratio boundaries, short-row suppression, duplicates, self, invalid and sentinels; "')
    (HERE / "SupplierTests.cpp").write_text(tests)
    shutil.copy2(HERE.parent / "predicate_degree_native/CMakeLists.txt", HERE / "CMakeLists.txt")
    configs = HERE / "configs"
    configs.mkdir()
    for case in ("h1", "control", "supplier", "reference"):
        original = "h1" if case == "h1" else "supplier"
        text = (HERE.parent / f"predicate_degree_native/configs/{original}_24_plain.ini").read_text()
        if case != "h1":
            text = text.replace("ShortcutEffectiveDegree=16\n",
                                "ShortcutRetainedRatio=0.5\nShortcutMinBaseDegree=16\n"
                                f"ShortcutHotPath={'reference' if case == 'reference' else 'optimized'}\n")
            assert "ShortcutEffectiveDegree" not in text
        if case == "control":
            text = text.replace("ShortcutMode=supplier", "ShortcutMode=control")
        for profile in (False, True):
            content = text
            if profile:
                content = content.replace("ShortcutProfile=false", "ShortcutProfile=true")
                content = content.replace("LogPhaseTime=false", "LogPhaseTime=true")
            (configs / f"{case}_{'profile' if profile else 'plain'}.ini").write_text(
                content + "\n[SearchSweep]\nNProbe=[24]\n")
        if case in ("h1", "control", "supplier"):
            for suffix, probes in (("asc", [16,24,384]), ("desc", [384,24,16])):
                (configs / f"{case}_{suffix}.ini").write_text(
                    text + "\n[SearchSweep]\nNProbe=[" + ",".join(map(str, probes)) + "]\n")
    config = configparser.ConfigParser()
    config.read(HERE.parent / "predicate_degree_searchsweep/experiment.ini")
    plan = dict(config["Experiment"])
    plan.update(binary=str(NEW / "source/Release/spannaclbench"), toolchain=str(NEW),
                outputdirectory=str(OUTPUT), previousresults=str(DATA / "comparisons/h1_predicate_degree_searchsweep_20260916"),
                cases="h1,control,supplier,reference", scenarios="unfilter", grid="24")
    config = configparser.ConfigParser()
    config["Experiment"] = plan
    with (HERE / "experiment.ini").open("x") as stream:
        config.write(stream)
    write(HERE / "preregistration.json", {
        "phase": "1_only_no_full_sweep", "ordinary_scenario": "unfilter", "ordinary_nprobe": 24,
        "required_cases": ["h1", "control", "supplier"],
        "causal_reference": "One unoptimized implementation of the SAME ratio rule, not another policy.",
        "ordinary_repetitions": 2, "ordinary_native_processes": 8,
        "ordinary_order": [["h1", "control", "supplier", "reference"],
                           ["reference", "supplier", "control", "h1"]],
        "profile_native_processes": 4, "profile_queries": 1000,
        "warmup": 1000, "measured_queries": 1000, "query_threads": 1, "numa_cpu_memory_node": 2,
        "small_fixtures": "h1/control/supplier unfilter [16,24,384] and reverse; "
                          "supplier medium/extreme/numeric/mixed [24];8 queries and8 warmups.",
        "supplier_min_base_degree": 16, "supplier_retained_ratio": 0.5,
        "native_maxcheck": 2048, "hierarchy_maxcheck": 512, "result_num": 10, "page_limit": 15,
        "profile_attribution": "Nested instrumented nanosecond scopes are not additive and not ordinary latency. "
                               "Use same-policy reference/optimized ordinary comparison for removed overhead.",
        "configs": {p.name: sha(p) for p in sorted(configs.iterdir())},
        "stop": "Return after Phase1; do not launch any198-point matrix.",
    })
    print("Prepared bounded Phase1:8 ordinary unfilter24 processes,4 separate profiles,10 tiny fixtures.")


if __name__ == "__main__":
    main()
