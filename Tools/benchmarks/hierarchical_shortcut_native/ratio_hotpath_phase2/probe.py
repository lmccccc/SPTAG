"""Observe clocks and user-space CPU PCs in the original Phase1 binary, never its timing."""
import configparser
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("phase1_diagnostics", HERE.parent / "ratio_degree_phase1/run.py")
phase = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase)
DATA = HERE.parents[4] / "datasets/sift1m_zipf200_sparse193_numeric"
ROOT = DATA / "comparisons/h1_ratio_phase2_20260917"


def symbolize(path):
    records = []
    for line in path.read_text().splitlines():
        kind, binary, address, count = line.split("\t")
        records.append({"kind": kind, "binary": binary, "address": address, "count": int(count)})
    for binary in {r["binary"] for r in records if r["binary"] not in ("unknown", "-")}:
        selected = [r for r in records if r["binary"] == binary]
        text = subprocess.check_output(
            ["addr2line", "-f", "-C", "-e", binary, *[r["address"] for r in selected]], text=True).splitlines()
        assert len(text) == len(selected) * 2
        for i, row in enumerate(selected):
            row["function"], row["line"] = text[i * 2:i * 2 + 2]
    return records


def main():
    phase.native.reject_environment_overrides()
    target = ROOT / "diagnostics"
    sampling_only = "--sampling-only" in sys.argv
    target.mkdir(parents=True, exist_ok=sampling_only)
    config = configparser.ConfigParser()
    config.read(HERE.parent / "ratio_degree_phase1/experiment.ini")
    plan = config["Experiment"]
    fixtures = HERE / "diagnostic_configs"
    fixtures.mkdir(exist_ok=sampling_only)
    for case in ("h1", "supplier"):
        for profile in (False, True):
            text = (HERE.parent / f"ratio_degree_phase1/configs/{case}_{'profile' if profile else 'plain'}.ini").read_text()
            text = text.replace("ShortcutCapture=true", "ShortcutCapture=false")
            text = text.replace("LogPhaseTime=true", "LogPhaseTime=false")
            path = fixtures / f"{case}_{'profile' if profile else 'plain'}.ini"
            if sampling_only:
                assert path.read_text() == text
            else:
                path.write_text(text)
    registration = {
        "purpose": "Observe actual chrono symbol entries and ordinary-path CPU PCs, not performance measurements.",
        "parent_binary": phase.fp(Path(plan["Binary"])),
        "configs": [phase.fp(p) for p in sorted(fixtures.iterdir())],
        "sampling": "Own-process SIGPROF at1ms CPU intervals; no kernel privilege changes; dropped samples explicit.",
        "search_environment_overrides": "None. LD_PRELOAD is declared instrumentation only and absent from benchmark runs.",
    }
    if not sampling_only:
        phase.write(target / "preregistration.json", registration)
    records = json.loads((target / "runs.json").read_text()) if sampling_only else []
    jobs = (
        ("clock_supplier_plain", "supplier", False, 8, False),
        ("clock_supplier_profile", "supplier", True, 8, False),
        ("clock_h1_plain", "h1", False, 8, False),
        ("sample_supplier_plain_v2", "supplier", False, 1000, True),
        ("sample_h1_plain_v2", "h1", False, 1000, True),
    )
    for name, case, profile, count, sampling in jobs:
        if sampling_only and not sampling:
            continue
        directory = target / name
        directory.mkdir()
        library = target / ("sample_audit_v2.so" if sampling else "clock_audit.so")
        if not library.exists():
            subprocess.run(["g++", "-std=c++17", "-O2", "-shared", "-fPIC",
                            *(["-DAUDIT_SAMPLE"] if sampling else []),
                            str(HERE / "ClockAudit.cpp"), "-o", str(library), "-ldl", "-pthread"], check=True)
        command = ["numactl", "--cpunodebind=2", "--membind=2", plan["Binary"],
                   "--index", plan["Index"], "--queries", plan["Queries"],
                   "--truth", str(DATA / "query/groundtruth_unfilter_local_ids.npy"),
                   "--search-sweep-ini", str(fixtures / f"{case}_{'profile' if profile else 'plain'}.ini"),
                   "--value-type", "Float", "--topk", "10", "--warmup", str(count),
                   "--measure-offset", "0", "--max-queries", str(count)]
        previous = os.environ.get("LD_PRELOAD")
        os.environ["LD_PRELOAD"] = str(library)
        try:
            logs = phase.native.run_native(command, directory, "native", 0.2, True)
        finally:
            if previous is None:
                del os.environ["LD_PRELOAD"]
            else:
                os.environ["LD_PRELOAD"] = previous
        clocks = symbolize(directory / "clock_audit.tsv")
        samples = symbolize(directory / "cpu_samples.tsv")
        phase.write(directory / "symbols.json", {"clocks": clocks, "samples": samples})
        native_rows = [json.loads(line) for p in logs for line in p.read_text().splitlines()
                       if line.startswith('{"engine":')]
        assert len(native_rows) == 1 and native_rows[0]["failed_queries"] == 0
        records.append({"directory": name, "case": case, "profile": profile, "queries": count,
                        "instrumentation": phase.fp(library), "native": native_rows[0],
                        "clocks": clocks, "samples": samples})
        phase.write(target / "runs.json", records)
        print(name, "chrono calls", sum(r["count"] for r in clocks),
              "samples", sum(r["count"] for r in samples if r["kind"] == "pc"), flush=True)


if __name__ == "__main__":
    main()
