"""Freeze a scalar-proof native-default admission experiment, preserving v2."""
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


def write(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def main():
    parent = DATA / "toolchains/h1_native_reuse_20260917_v2"
    target = DATA / "toolchains/h1_native_default_admission_20260917"
    output = DATA / "comparisons/h1_native_default_admission_20260917"
    if target.exists() or output.exists():
        raise RuntimeError("Preserve native-default admission artifacts")
    old = json.loads((parent / "native_reuse_provenance.json").read_text())
    for name, digest in old["after"].items():
        assert sha(parent / "source" / name) == digest, name
    target.mkdir()
    shutil.copytree(parent / "source", target / "source", symlinks=True,
                    ignore=shutil.ignore_patterns("Release"))
    paths = {f"AnnService/{name}": name for name in (
        "DefaultAdmission.h", "PostingSupplier.h", "NativeNeighborHooks.h", "NativeSupplier.h")}
    paths.update({"AnnService/src/Core/BKT/BKTIndex.cpp": "BKTIndex.cpp",
                  "AnnService/src/Core/SPANN/SPANNIndex.cpp": "SPANNIndex.cpp",
                  "Tools/benchmarks/SpannAclBench.cpp": "SpannAclBench.cpp"})
    for name, source in paths.items():
        shutil.copy2(HERE / source, target / "source" / name)
    after = {name: sha(target / "source" / name) for name in set(old["after"]) | set(paths)}
    write(target / "native_reuse_provenance.json", {
        **old, "parent": str(parent), "parent_source_hashes": old["after"], "after": after,
        "changed_files": sorted(paths), "parent_binary_sha256": sha(parent / "source/Release/spannaclbench"),
        "proof": "Native immutable H/O generation; load-verified valid live own point for every physical H1; "
                 "actual empty predicate; unchanged head/vector domain; native nprobe>=topk; ratio<=1.",
        "metadata": "One Boolean and vector-count scalar in existing posting model; no per-head cache, "
                    "new mutation locks, distance wrapper or ordinary qualification pass.",
    })
    c = configparser.ConfigParser()
    c.read(HERE / "experiment.ini")
    c["Experiment"].update(Binary=str(target / "source/Release/spannaclbench"), Toolchain=str(target),
                           OutputDirectory=str(output))
    with (HERE / "experiment.ini").open("w") as stream:
        c.write(stream)
    registration = json.loads((HERE / "preregistration.json").read_text())
    registration.update(phase="bounded_native_default_admission",
        reason="Restore native predicate-free selected-head/own admission only when e=d is proven.",
        expected_unfilter="Exact original H1 heads/final IDs/distances/native core work, including q915.",
        filtered_reference="Native-reuse v2 unchanged interfaces and policy.")
    (HERE / "preregistration.json").write_text(json.dumps(registration, indent=2) + "\n")


if __name__ == "__main__":
    main()
