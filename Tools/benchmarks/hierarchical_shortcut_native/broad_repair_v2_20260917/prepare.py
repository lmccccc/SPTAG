"""New bounded iteration; never mutates the v1 runtime or stopped curves."""
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
DATA = REPO.parent / "datasets/sift1m_zipf200_sparse193_numeric"
MATCHED = DATA / "comparisons/matched_baseline_20260917"
BASE = DATA / "toolchains/h1_native_default_admission_20260917"
TOOL = DATA / "toolchains/broad_repair_v2_20260917"
OUTPUT = DATA / "comparisons/broad_repair_v2_20260917"
PARENT = DATA / "toolchains/broad_repair_20260917"
PARENT_OUT = DATA / "comparisons/broad_repair_20260917"
V1 = HERE.parent / "broad_repair_20260917"


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def main():
    assert sha(PARENT_OUT / "report.json") == "f88e775beb79adeea96d32349ce22ce2957d6c151367e50acdfff9cefd85b1bd"
    assert not json.loads((MATCHED / "OPERATOR_STOP.json").read_text())["resume_allowed"]
    TOOL.mkdir()
    OUTPUT.mkdir()
    sources = json.loads((PARENT_OUT / "repair_sources.json").read_text())
    for target, digest in sources.items():
        assert sha(PARENT / "source" / target) == digest
        shutil.copyfile(PARENT / "source" / target, HERE / Path(target).name)
    for name in ("MatchedBench.cpp", "NativeNProbeSweep.h", "NativeTests.cpp", "CMakeLists.txt",
                 "ClockAudit.cpp", "process.py", "diagnose.py", "run.py", "test_runner.py"):
        shutil.copyfile(V1 / name, HERE / name)
    configs = HERE / "configs"
    configs.mkdir()
    for name in ("broad_bad24", "broad_original80", "broad_h3_32", "unfilter24", "flags_32_24"):
        shutil.copyfile(V1 / "configs" / (name + ".ini"), configs / (name + ".ini"))
    for name, source in (
        ("matched-current", PARENT / "matched-current"),
        ("matched-original", PARENT / "matched-original"),
        ("matched-v1", PARENT / "harness/matched-repair"),
        ("matched-reference-bad", PARENT / "harness/matched-bad")):
        shutil.copy2(source, TOOL / name)
    write(OUTPUT / "parent.json", {"report_sha256": sha(PARENT_OUT / "report.json"),
        "source_hashes": sources, "runtime": json.loads((PARENT_OUT / "runtime.json").read_text()),
        "stop_marker_sha256": sha(MATCHED / "OPERATOR_STOP.json"),
        "scope": "One allocation/qualification-state iteration; no curves, immutable predicate and budget."})


if __name__ == "__main__":
    main()
