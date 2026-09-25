"""Prepare bounded Broad evidence without touching the operator-stopped curves."""
import configparser
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
DATA = REPO.parent / "datasets/sift1m_zipf200_sparse193_numeric"
MATCHED = DATA / "comparisons/matched_baseline_20260917"
TOOL = DATA / "toolchains/broad_repair_20260917"
OUTPUT = DATA / "comparisons/broad_repair_20260917"
BASE = DATA / "toolchains/h1_native_default_admission_20260917"


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
    stop = json.loads((MATCHED / "OPERATOR_STOP.json").read_text())
    assert stop["state"] == "operator_stopped" and not stop["resume_allowed"]
    TOOL.mkdir()
    OUTPUT.mkdir()
    proof = json.loads((MATCHED / "cores.json").read_text())
    for core in proof["cores"].values():
        for item in core["linked_files"]:
            assert sha(item["path"]) == item["sha256"]
    for name in ("original", "current"):
        source = MATCHED / "snapshot" / f"matched-{name}"
        assert sha(source) == proof["cores"][name]["binary_sha256"]
        shutil.copy2(source, TOOL / f"matched-{name}")
    configs = HERE / "configs"
    configs.mkdir()
    for name, source, probe in (
        ("broad_bad24", "broad_tag_supplier_smoke.ini", 24),
        ("broad_original80", "broad_tag_h1_original_smoke.ini", 80),
        ("broad_h3_32", "broad_tag_h3_smoke.ini", 32),
        ("unfilter24", "unfilter_supplier_smoke.ini", 24)):
        cfg = configparser.ConfigParser()
        cfg.optionxform = str
        cfg.read(MATCHED / "snapshot/experiment/configs" / source)
        cfg["SearchSweep"]["NProbe"] = f"[{probe}]"
        with (configs / f"{name}.ini").open("x") as stream:
            cfg.write(stream, space_around_delimiters=False)
    write(OUTPUT / "baseline_provenance.json", {
        "matched_cores": proof, "operator_stop_sha256": sha(MATCHED / "OPERATOR_STOP.json"),
        "matched_protocol_sha256": sha(MATCHED / "renderer_contract/protocol.json"),
        "input_manifest_sha256": sha(MATCHED / "input_hashes.json"),
        "benchmark_source_sha256": sha(MATCHED / "snapshot/experiment/MatchedBench.cpp"),
        "configs": {p.name: sha(p) for p in sorted(configs.iterdir())},
        "diagnostic_scope": "Main-query-thread PC-only CPU samples between ordinary chrono boundaries5/6; "
                            "separate clock-only proof first. No instrumented latency used as ordinary.",
        "bounded_ordinary_plan": [
            ["bad24", "repair24", "original80", "h3_32"],
            ["h3_32", "original80", "repair24", "bad24"]],
        "unfilter_plan": [["bad", "repair"], ["repair", "bad"]],
        "no_curves": True})
    shutil.copyfile(HERE.parent / "native_filter_cost_20260917/ClockAudit.cpp", HERE / "ClockAudit.cpp")


if __name__ == "__main__":
    main()
