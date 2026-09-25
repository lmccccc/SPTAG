"""Freeze one measured-cause fix: skip impossible noncollapsed posting-result admission."""
import configparser
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[4] / "datasets/sift1m_zipf200_sparse193_numeric"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    parent = DATA / "toolchains/h1_native_filter_cost_20260917"
    target = DATA / "toolchains/h1_native_filter_cost_bound_20260917"
    if target.exists():
        raise RuntimeError("Preserve fixed runtime")
    proof = json.loads((parent / "diagnostic_provenance.json").read_text())
    for path, digest in proof["after"].items():
        assert sha(parent / "source" / path) == digest
    target.mkdir()
    shutil.copytree(parent / "source", target / "source", symlinks=True,
                    ignore=shutil.ignore_patterns("Release"))
    (HERE / "bound_fix").mkdir()
    shutil.copy2(parent / "source/AnnService/src/Core/BKT/BKTIndex.cpp", HERE / "bound_fix/BKTIndex.cpp")
    shutil.copy2(HERE / "NativeTests.cpp", HERE / "bound_fix/NativeTests.cpp")
    for name in ("predicate_coverage.json", "native-coverage.log"):
        shutil.copy2(parent / name, target / name)
    config = configparser.ConfigParser()
    config.read(HERE / "experiment.ini")
    config["Experiment"].update(Toolchain=str(target), Binary=str(target / "source/Release/spannaclbench"),
        OutputDirectory=str(DATA / "comparisons/h1_native_filter_cost_bound_20260917"))
    with (HERE / "bound_experiment.ini").open("x") as output:
        config.write(output)
    with (target / "reference_provenance.json").open("x") as output:
        json.dump({"reference": str(parent), "reference_source": proof,
                   "reference_binary_sha256": sha(parent / "source/Release/spannaclbench"),
                   "decision": "Native ordinary samples concentrate in CheckValidPosting and metadata callbacks. "
                               "Result admission repeats posting checks even after native bound proves rejection. "
                               "Preserve own notifications and all collapsed stop behavior."}, output, indent=2)


if __name__ == "__main__":
    main()
