"""Freeze a bounded real-predicate diagnostic; preserve native-default and curve artifacts."""
import configparser
import hashlib
import json
from pathlib import Path
import shutil
import struct
import numpy as np

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[4] / "datasets/sift1m_zipf200_sparse193_numeric"
PARENT = HERE.parent / "native_default_admission_20260917"


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
    runtime = DATA / "toolchains/h1_native_filter_cost_20260917"
    output = DATA / "comparisons/h1_native_filter_cost_20260917"
    if runtime.exists() or output.exists():
        raise RuntimeError("Preserve diagnostic artifacts")
    parent = DATA / "toolchains/h1_native_default_admission_20260917"
    proof = json.loads((parent / "native_reuse_provenance.json").read_text())
    for name, digest in proof["after"].items():
        assert sha(parent / "source" / name) == digest, name
    runtime.mkdir()
    shutil.copytree(parent / "source", runtime / "source", symlinks=True,
                    ignore=shutil.ignore_patterns("Release"))
    for name in ("NativeSupplier.h", "SPANNIndex.cpp", "NativeTests.cpp", "CMakeLists.txt"):
        shutil.copy2(PARENT / name, HERE / name)
    shutil.copy2(HERE.parent / "native_ratio_curve_20260917/SpannAclBench.cpp", HERE / "SpannAclBench.cpp")
    shutil.copy2(HERE.parent / "ratio_hotpath_phase2/ClockAudit.cpp", HERE / "ClockAudit.cpp")
    index = DATA / "build_runs/h1_same_posting_20260915/search_view/tenant_0"
    ini = configparser.ConfigParser()
    ini.read(index / "indexloader.ini")
    assert ini["BuildSSDIndex"]["ColumnTypes"] == "categorical,numeric"
    meta = (index / "numeric_meta.bin").read_bytes()
    magic, version, base, numeric, count, columns, generation, fingerprint = struct.unpack("<IIiiiiQQ", meta[:40])
    assert (magic, version, base, numeric, count, columns, len(meta)) == (0x324d554e, 2, 1, 1, 1000000, 2, 48)
    lo, hi = struct.unpack("<II", meta[40:])
    workloads = json.loads((DATA / "query/workloads.json").read_text())
    attrs = np.load(workloads["attributes"], mmap_mode="r")
    assert attrs.dtype == np.dtype("<u4") and attrs.shape == (count, columns)
    assert (int(attrs[:, base].min()), int(attrs[:, base].max())) == (lo, hi)
    ids = np.fromfile(index / "SPTAGHeadVectorIDs.bin", dtype="<u8", offset=8)
    assert len(ids) == 160091 and np.all(ids < count)
    assert np.all(attrs[:, base] <= hi) and np.all(attrs[ids, base] <= hi)
    query = runtime / "alltrue_numeric_dnf.npy"
    np.save(query, np.tile(np.array([7, 0x444e4633, 1, 1, 1, base, 2, hi], dtype="<u4"), (1000, 1)))
    write(runtime / "predicate_coverage.json", {
        "native_schema": ini["BuildSSDIndex"]["ColumnTypes"], "numeric_column": base,
        "native_unsigned_operator": "DNF_LE", "global_lo": lo, "global_hi": hi,
        "native_generation": generation, "numeric_content_fingerprint": fingerprint,
        "dataset_records": count, "own_heads": len(ids), "all_records_match": True, "all_own_points_match": True,
        "attributes": {"path": workloads["attributes"], "sha256": sha(Path(workloads["attributes"]))},
        "numeric_meta": {"path": str(index / "numeric_meta.bin"), "sha256": sha(index / "numeric_meta.bin")},
        "predicate": {"path": str(query), "sha256": sha(query)},
        "native_coverage_check_required_before_measurement": True})
    c = configparser.ConfigParser()
    c.read(PARENT / "experiment.ini")
    c["Experiment"].update(Binary=str(runtime / "source/Release/spannaclbench"), Toolchain=str(runtime),
        OutputDirectory=str(output), AllTruePredicate=str(query),
        AcceptedResults=str(DATA / "comparisons/h1_native_default_admission_20260917"))
    with (HERE / "experiment.ini").open("x") as stream:
        c.write(stream)
    (HERE / "configs").mkdir()
    for case, mode in (("A", "ordinary"), ("B", "control"), ("C", "supplier")):
        config = configparser.ConfigParser()
        config.optionxform = str
        config.read(PARENT / "configs/supplier_plain.ini")
        config["SearchSSDIndex"]["ShortcutMode"] = mode
        with (HERE / "configs" / f"{case}.ini").open("x") as stream:
            config.write(stream, space_around_delimiters=False)
    native_test = configparser.ConfigParser()
    native_test["Coverage"] = dict(Index=str(index), Attributes=workloads["attributes"],
        Predicate=str(query), Generation=str(generation), Column=str(base), Bound=str(hi))
    with (HERE / "coverage.ini").open("x") as stream:
        native_test.write(stream, space_around_delimiters=False)
    write(HERE / "preregistration.json", {"scope": "bounded_real_filter_cost_no_curve_stages",
        "cases": {"A": "native H1 empty predicate", "B": "same filtered admission, no ratio observer",
                  "C": "same real nonempty alltrue predicate plus unchanged ratio supplier"},
        "ordinary_order": [["A", "B", "C"], ["C", "A", "B"]], "nprobe": [24],
        "warmup": 1000, "queries": 1000, "repeats": 2, "threads": 1, "numa": 2, "O_DIRECT": True,
        "MaxCheck": 2048, "HierarchyMaxCheck": 512, "page_limit": 15,
        "retained_ratio": .5, "minimum_physical_degree": 16,
        "configs": {p.name: sha(p) for p in (HERE / "configs").iterdir()},
        "predicate_sha256": sha(query), "one_causal_fix_only_if_measured": True})
    write(runtime / "parent_provenance.json", {"parent": str(parent), "parent_source_hashes": proof["after"],
        "parent_binary_sha256": sha(parent / "source/Release/spannaclbench")})


if __name__ == "__main__":
    main()
