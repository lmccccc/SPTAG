"""Isolated corrected-policy milestone; old experiments and operator stop remain immutable."""
import configparser
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
DATA = REPO.parent / "datasets/sift1m_zipf200_sparse193_numeric"
BASE = DATA / "toolchains/h1_native_default_admission_20260917"
MATCHED = DATA / "comparisons/matched_baseline_20260917"
TOOL = DATA / "toolchains/cost_arbitration_20260917"
OUTPUT = DATA / "comparisons/cost_arbitration_20260917"
OLD = HERE.parent / "broad_repair_v2_20260917"

DEFAULTS = {
    "ArbitrationGraphSetupCost": "0.25", "ArbitrationPostingSetupCost": "0.25",
    "ArbitrationMemberCost": "0.05", "ArbitrationDistanceCost": "1",
    "ArbitrationPredicateCost": "0.2", "ArbitrationSignatureCost": "0.05",
    "ArbitrationPriorWeight": "8", "ArbitrationGraphNoveltyPrior": "0.5",
    "ArbitrationPostingNoveltyPrior": "0.5", "ArbitrationDiscoveryNoveltyPrior": "0.75",
    "ArbitrationPredicatePrior": "0.25", "ArbitrationCompetitivePrior": "0.25"}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    with path.open("x") as out:
        json.dump(value, out, indent=2)
        out.write("\n")


def main():
    assert not json.loads((MATCHED / "OPERATOR_STOP.json").read_text())["resume_allowed"]
    TOOL.mkdir()
    OUTPUT.mkdir()
    proof = json.loads((BASE / "native_reuse_provenance.json").read_text())
    for name, digest in proof["after"].items():
        assert sha(BASE / "source" / name) == digest
    for name in ("SPANNIndex.cpp",):
        shutil.copyfile(BASE / "source/AnnService/src/Core/SPANN" / name, HERE / name)
    shutil.copyfile(HERE.parent / "native_filter_cost_20260917/bound_fix/BKTIndex.cpp", HERE / "BKTIndex.cpp")
    for name in ("NativeFunctionRef.h", "RelativeNeighborhoodGraph.h", "process.py", "ClockAudit.cpp",
                 "MatchedBench.cpp", "NativeNProbeSweep.h", "CMakeLists.txt"):
        shutil.copyfile(OLD / name, HERE / name)
    configs = HERE / "configs"
    configs.mkdir()
    for scenario in ("broad_tag", "unfilter", "extreme_tag"):
        for mode in ("auto", "graph"):
            cfg = configparser.ConfigParser()
            cfg.optionxform = str
            cfg.read(MATCHED / f"snapshot/experiment/configs/{scenario}_supplier_r1.ini")
            cfg["Benchmark"]["Case"] = mode
            for key in list(cfg["SearchSSDIndex"]):
                if key.startswith("Shortcut"): del cfg["SearchSSDIndex"][key]
            cfg["SearchSSDIndex"].update(DEFAULTS)
            cfg["SearchSSDIndex"].update(ArbitrationMode=mode, ArbitrationCapture="false",
                EnableHybridDistance="false", EnableAdaptiveFilteredNprobe="false")
            cfg["SearchSweep"]["NProbe"] = "[24]"
            with (configs / f"{scenario}_{mode}.ini").open("x") as out:
                cfg.write(out, space_around_delimiters=False)
    for source, target in (("broad_original80", "broad_original80"), ("broad_h3_32", "broad_h3_32")):
        shutil.copyfile(OLD / "configs" / (source + ".ini"), configs / (target + ".ini"))
    for core in ("original", "current"):
        shutil.copy2(MATCHED / "snapshot" / f"matched-{core}", TOOL / f"matched-{core}")
    write(OUTPUT / "parent.json", {"base_source": proof,
        "original_current_provenance": json.loads((MATCHED / "cores.json").read_text()),
        "bound_BKT_source_sha256": sha(HERE / "BKTIndex.cpp"),
        "stop_sha256": sha(MATCHED / "OPERATOR_STOP.json"),
        "semantics": "native result-only H1; cost-selected full auxiliary rows, not degree deficit",
        "defaults": DEFAULTS, "default_units": "uncalibrated operation-count units; one vector distance=1",
        "ordinary_plan": ["Broad auto/graph n24 + originalH1 n80/H3 n32",
                          "unfilter auto/graph n24", "extreme_tag auto/graph n24"],
        "repetitions": 2, "warmup": 1000, "queries": 1000, "no_tuning_or_curves": True})


if __name__ == "__main__":
    main()
