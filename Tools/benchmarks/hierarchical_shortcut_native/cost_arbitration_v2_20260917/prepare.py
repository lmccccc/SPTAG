"""Isolated corrected-policy milestone; old experiments and operator stop remain immutable."""
import configparser
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
DATA = REPO.parent / "datasets/sift1m_zipf200_sparse193_numeric"
BASE = DATA / "toolchains/cost_arbitration_20260917"
MATCHED = DATA / "comparisons/matched_baseline_20260917"
TOOL = DATA / "toolchains/cost_arbitration_v2_20260917"
OUTPUT = DATA / "comparisons/cost_arbitration_v2_20260917"
V1 = DATA / "comparisons/cost_arbitration_20260917"

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
    proof = json.loads((V1 / "sources.json").read_text())
    for name, digest in proof.items():
        assert sha(BASE / "source" / name) == digest
    for name, digest in {"report.json":"5784d3954bc94227544e580e01143f209327233b5dda61cac1c7bbf00c3b1112",
                         "summary.json":"f391b7efc8fb54f30c9d33e6c391dd2e85d5992f29eaee824a3f0c15b4307ad9"}.items():
        assert sha(V1/name)==digest
    runtime=json.loads((V1/"runtime.json").read_text())
    assert sha(runtime["binary"])==runtime["binary_sha256"]
    for name,digest in runtime["libraries"].items():
        assert sha(BASE/"source/Release"/name)==digest
    for core in ("original", "current"):
        shutil.copy2(MATCHED / "snapshot" / f"matched-{core}", TOOL / f"matched-{core}")
    write(OUTPUT / "parent.json", {"base_source": proof, "v1_runtime":runtime,
        "v1_report_sha256":sha(V1/"report.json"),"v1_summary_sha256":sha(V1/"summary.json"),
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
