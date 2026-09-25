"""Prepare the bounded Phase2 runtime without touching completed experiments."""
import configparser
import json
import shutil
from pathlib import Path
from integrate import HERE, DATA, PARENT
import sys

sys.path.insert(0, str(HERE.parent / "ratio_degree_phase1"))
from prepare import sha, write


def main():
    target = DATA / "toolchains/h1_ratio_phase2_20260917"
    if target.exists():
        raise RuntimeError("Preserve existing Phase2 runtime")
    before = json.loads((PARENT.parent / "ratio_provenance.json").read_text())["after"]
    for name, digest in before.items():
        assert sha(PARENT / name) == digest, name
    target.mkdir()
    shutil.copytree(PARENT, target / "source", symlinks=True, ignore=shutil.ignore_patterns("Release"))
    changes = {f"AnnService/{name}": name for name in
               ("Supplier.h", "NativeAdapter.h", "FullHooks.h", "IntrinsicValidity.h")}
    changes.update({
        "AnnService/src/Core/SPANN/SPANNIndex.cpp": "SPANNIndex.cpp",
        "AnnService/inc/Core/SPANN/ExtraStaticSearcher.h": "ExtraStaticSearcher.h",
        "AnnService/inc/Core/Common/VersionLabel.h": "VersionLabel.h",
        "Tools/benchmarks/SpannAclBench.cpp": "SpannAclBench.cpp",
    })
    for name, source in changes.items():
        shutil.copy2(HERE / source, target / "source" / name)
    after = {name: sha(target / "source" / name) for name in set(before) | set(changes)}
    write(target / "ratio_provenance.json", {
        "parent_toolchain": str(PARENT.parent), "parent_binary_sha256": sha(PARENT / "Release/spannaclbench"),
        "before": before, "after": after,
        "changed_files": sorted(changes), "shared_parser_unchanged": True,
        "qualification_snapshot_bytes_per_query_thread": 160091,
        "invalidation": "VersionLabel identity/revision covers Initialize/Delete/SetVersion/IncVersion/"
                        "Load(memory,file)/AddBatch/SetR; static posting LoadIndex identity/revision. "
                        "Shared guards span navigation; mutation paths take exclusive guards. "
                        "Static read-only canonical head identity required; mutable layout rejected.",
    })
    configs = HERE / "configs"
    configs.mkdir()
    for case in ("h1", "reference", "supplier"):
        parent_case = "h1" if case == "h1" else "supplier"
        for suffix in ("plain", "profile", "asc", "desc"):
            text = (HERE.parent / f"ratio_degree_phase1/configs/{parent_case}_{suffix}.ini").read_text()
            if case != "h1":
                text = text.replace("ShortcutHotPath=optimized\n",
                    f"ShortcutHotPath=optimized\nShortcutIntrinsicCache={'true' if case == 'supplier' else 'false'}\n")
            (configs / f"{case}_{suffix}.ini").write_text(text)
    config = configparser.ConfigParser()
    config.read(HERE.parent / "ratio_degree_phase1/experiment.ini")
    config["Experiment"].update(binary=str(target / "source/Release/spannaclbench"),
        toolchain=str(target), outputdirectory=str(DATA / "comparisons/h1_ratio_phase2_20260917"),
        cases="h1,reference,supplier")
    with (HERE / "experiment.ini").open("x") as stream:
        config.write(stream)
    write(HERE / "preregistration.json", {
        "phase": "2_only_no_full_sweep", "ordinary_order": [["h1", "reference", "supplier"],
                                                          ["supplier", "reference", "h1"]],
        "ordinary_native_processes": 6, "profile_native_processes": 3, "small_fixture_processes": 6,
        "warmup": 1000, "measured_queries": 1000, "nprobe": [24], "query_threads": 1,
        "numa_cpu_memory_node": 2, "O_DIRECT": True, "page_limit": 15,
        "reference": "Phase1 optimized ratio implementation, compact cache disabled; no old absolute-degree policy.",
        "coarse_profile": "Fine Engine clocks disabled; intrinsic preparation, adapter preparation, native search "
                          "and adapter finalization measured separately. Not ordinary elapsed attribution.",
        "fixtures": "supplier forward/reverse [16,24,384], medium/extreme/numeric/mixed [24],8 warm+8 measured; "
                    "exact full native payload/core work against preserved Phase1, excluding new metadata/timing.",
        "configs": {p.name: sha(p) for p in sorted(configs.iterdir())},
        "stop": "Bounded Phase2 only; no full sweep.",
    })
    print("Prepared Phase2:6 ordinary,3 coarse profiles,6 tiny fixtures; diagnostics preserved.")


if __name__ == "__main__":
    main()
