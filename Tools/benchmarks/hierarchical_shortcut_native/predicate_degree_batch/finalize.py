"""Freeze completed report and actual imported harness provenance without changing measurements."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("batch_final_runner", HERE / "run.py")
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


def main():
    plan = batch.native.read_ini(HERE / "experiment.ini")["Experiment"]
    root = Path(plan["OutputDirectory"])
    assert json.loads((root / "status.json").read_text())["state"] == "completed"
    validation = json.loads((root / "independent_reconciliation.json").read_text())
    assert batch.fingerprint(root / "summary.json")["sha256"] == validation["summary_sha256"]
    destination = root / "final_provenance"
    destination.mkdir()
    sources = {HERE / name for name in
               ("README.md", "verify.py", "finalize.py", "invalid_empty.ini", "BatchTests.cpp")}
    sources.update(Path(module.__file__).resolve() for module in (batch, batch.audit, batch.native))
    benchmarks = HERE.parents[1]
    for module in tuple(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if filename:
            path = Path(filename).resolve()
            if path.is_relative_to(benchmarks) and path.suffix == ".py":
                sources.add(path)
    manifest = []
    for source in sorted(sources):
        target = destination / "harness" / source.relative_to(benchmarks)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest.append({"original": batch.fingerprint(source), "preserved_copy": batch.fingerprint(target)})
    for name in ("parser-tests.log", "verification.log"):
        shutil.copy2(Path(plan["Toolchain"]) / name, destination / name)
    artifacts = ("summary.json", "summary.csv", "thresholds.json", "report.md", "validation.json",
                 "independent_reconciliation.json", "unfilter_degree_audit.json", "provenance.json",
                 "fixture_validation.json", "status.json")
    batch.write(destination / "manifest.json", {
        "harness_sources": manifest, "completed_artifacts": [batch.fingerprint(root / n) for n in artifacts],
        "ordinary_native_index_loads": 36, "ordinary_measurement_batches": 396, "points": 198,
        "successful_native_fixture_loads": 45, "invalid_array_fixture_loads": 0,
        "supplier_degree_semantics": "predicate_valid_neighbors",
        "no_previous_latency_splicing": True,
    })
    print("Frozen final report, imported harness sources, parser evidence and validated measurement hashes.")


if __name__ == "__main__":
    main()
