"""Reconstruct the pre-supplier core, not a disabled supplier implementation."""
import importlib.util
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
DATA = REPO.parent / "datasets/sift1m_zipf200_sparse193_numeric"
CACHE = Path("/home/baotonglu/.copilot/session-state/40fc7ebb-e617-4ec3-a17b-610068e95e5e/files")
TOOL = DATA / "toolchains/matched_baseline_20260917"


def main():
    spec = importlib.util.spec_from_file_location("authenticated_source_reconstruction",
                                                  HERE.parent / "full/reconstruct.py")
    reconstruction = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reconstruction)
    original = TOOL / "original"
    reconstruction.reconstruct(REPO, DATA / "build_runs/20260909T141212Z_lightweight_rescue",
                               original, CACHE)
    authenticated = CACHE / "original_h1_audit_20260917/authenticated"
    checked = {}
    for path in authenticated.rglob("*"):
        if path.is_file():
            relative = path.relative_to(authenticated)
            digest = reconstruction.sha(path)
            assert reconstruction.sha(original / "source" / relative) == digest, relative
            checked[str(relative)] = digest
    assert len(checked) == 7
    with (TOOL / "original_audit_crosscheck.json").open("x") as stream:
        json.dump({"authenticated_original_files": checked,
                   "array_frozen_rejected": "native_nprobe_array_20260916/spannaclbench-frozen contains "
                   "H1Supplier::NativeSearch symbols and h1_predicate_degree_nprobe_20260916 object paths; "
                   "not a pre-supplier core. Original linkable objects were not recovered.",
                   "full_original_build_required": True}, stream, indent=2)
    shutil.copyfile(original / "source/Tools/benchmarks/SpannAclBench.cpp", HERE / "MatchedBench.cpp")
    shutil.copyfile(REPO / "Tools/benchmarks/NativeNProbeSweep.h", HERE / "NativeNProbeSweep.h")
    shutil.copyfile(HERE.parent / "native_ratio_curve_20260917/FullHooks.h", HERE / "FullHooks.h")


if __name__ == "__main__":
    main()
