"""One measured-region diagnostic of the frozen Phase2 binary; never ordinary timing."""
import configparser
import json
import os
from pathlib import Path
from probe import HERE, ROOT, DATA, phase, symbolize


def main():
    phase.native.reject_environment_overrides()
    config = configparser.ConfigParser()
    config.read(HERE / "experiment.ini")
    plan = config["Experiment"]
    directory = ROOT / "diagnostics/sample_phase2_supplier_plain"
    directory.mkdir()
    ini = HERE / "diagnostic_configs/phase2_supplier_plain.ini"
    text = (HERE / "configs/supplier_plain.ini").read_text().replace(
        "ShortcutCapture=true", "ShortcutCapture=false")
    with ini.open("x") as stream:
        stream.write(text)
    library = ROOT / "diagnostics/sample_audit_v2.so"
    command = ["numactl", "--cpunodebind=2", "--membind=2", plan["Binary"],
               "--index", plan["Index"], "--queries", plan["Queries"],
               "--truth", str(DATA / "query/groundtruth_unfilter_local_ids.npy"),
               "--search-sweep-ini", str(ini), "--value-type", "Float", "--topk", "10",
               "--warmup", "1000", "--measure-offset", "0", "--max-queries", "1000"]
    phase.write(directory / "preregistration.json", {
        "binary": phase.fp(Path(plan["Binary"])), "config": phase.fp(ini),
        "instrumentation": phase.fp(library),
        "purpose": "Clock-entry proof and statistical remaining-hotspot PCs; not ordinary QPS.",
    })
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
    phase.require(sum(r["count"] for r in clocks) == 2 and
                  all(r["function"] == "Run(int, char**)" for r in clocks),
                  "Unexpected ordinary query/hot-loop clock entry")
    rows = [json.loads(line) for p in logs for line in p.read_text().splitlines()
            if line.startswith('{"engine":')]
    phase.require(len(rows) == 1 and rows[0]["failed_queries"] == 0 and rows[0]["recall"] == 0.9074,
                  "Invalid diagnostic query result")
    phase.write(directory / "symbols.json", {"clocks": clocks, "samples": samples, "native": rows[0]})
    print("Phase2 actual ordinary chrono entries:", sum(r["count"] for r in clocks))
    print("Measured-region CPU PCs:", sum(r["count"] for r in samples if r["kind"] == "pc"))


if __name__ == "__main__":
    main()
