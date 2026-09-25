"""Preserve completed shared-API evidence and verify physical component load counts."""
import collections
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("shared_api_runner", HERE / "run.py")
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


def main():
    plan = batch.native.read_ini(HERE / "experiment.ini")["Experiment"]
    root = Path(plan["OutputDirectory"])
    assert json.loads((root / "status.json").read_text())["state"] == "completed"
    reconciliation = json.loads((root / "independent_reconciliation.json").read_text())
    assert batch.fingerprint(root / "summary.json")["sha256"] == reconciliation["summary_sha256"]
    expected = {
        "Load Vector (160091,128) Finish!": 1, "Load BKT (1,160093) Finish!": 1,
        "Load RNG (160091,32) Finish!": 1, "Load Vector (4098,128) Finish!": 1,
        "Load BKT (1,4100) Finish!": 1, "Load RNG (4098,32) Finish!": 1,
    }
    physical = []
    for run in json.loads((root / "runs.json").read_text()):
        counts = collections.Counter()
        for part in ("stdout", "stderr"):
            with (root / run["directory"] / f"native.{part}.log").open() as stream:
                for line in stream:
                    counts.update(re.findall(r"Load (?:Vector|BKT|RNG) \([^\n]+?\) Finish!", line))
        assert dict(counts) == expected, (run["directory"], counts)
        physical.append({"directory": run["directory"], "physical_load_messages": dict(counts)})
    assert len(physical) == 36
    batch.write(root / "physical_load_evidence.json", physical)
    destination = root / "final_provenance"
    destination.mkdir()
    sources = {p for p in HERE.iterdir() if p.is_file() and p.suffix in (".py", ".cpp", ".h", ".md", ".ini")}
    sources.update(Path(m.__file__).resolve() for m in (batch, batch.audit, batch.native))
    benchmarks = HERE.parents[1]
    for module in tuple(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if filename:
            path = Path(filename).resolve()
            if path.is_relative_to(benchmarks) and path.suffix == ".py":
                sources.add(path)
    manifests = []
    for source in sorted(sources):
        target = destination / "harness" / source.relative_to(benchmarks)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifests.append({"original": batch.fingerprint(source), "preserved_copy": batch.fingerprint(target)})
    native_parser = Path(plan["Toolchain"]) / "source/Tools/benchmarks/NativeNProbeSweep.h"
    proof = json.loads((Path(plan["Toolchain"]) / "batch_provenance.json").read_text())
    assert batch.fingerprint(native_parser)["sha256"] == proof["shared_parser"]["sha256"]
    shutil.copy2(native_parser, destination / "NativeNProbeSweep.h")
    for name in ("parser-tests.log", "verification.log", "api-failure-tests.log"):
        shutil.copy2(Path(plan["Toolchain"]) / name, destination / name)
    artifacts = ("summary.json", "summary.csv", "thresholds.json", "report.md", "validation.json",
                 "independent_reconciliation.json", "unfilter_degree_audit.json", "physical_load_evidence.json",
                 "provenance.json", "fixture_validation.json", "api_failure_fixtures/validation.json", "status.json")
    batch.write(destination / "manifest.json", {
        "harness_sources": manifests, "completed_artifacts": [batch.fingerprint(root / n) for n in artifacts],
        "shared_native_parser": batch.fingerprint(destination / "NativeNProbeSweep.h"),
        "ordinary_native_index_loads": 36, "ordinary_measurement_batches": 396, "points": 198,
        "successful_native_fixture_loads": 45, "invalid_api_fixture_loads": 0,
        "invalid_api_processes": 4,
        "sweep_execution": "single_load_nprobe_array", "nprobe_ini_api": "SearchSweep.NProbe",
        "supplier_degree_semantics": "predicate_valid_neighbors",
        "all36_groups_loaded_each_physical_H1_and_H3_vector_tree_graph_once": True,
        "no_previous_latency_splicing": True,
    })
    print("Frozen shared parser, harness and completed reports; all36 physical component load ledgers passed.")


if __name__ == "__main__":
    main()
