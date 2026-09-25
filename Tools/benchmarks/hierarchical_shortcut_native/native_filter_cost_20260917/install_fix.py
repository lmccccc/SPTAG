from pathlib import Path
import hashlib
import json
import shutil

HERE = Path(__file__).resolve().parent
TOOL = HERE.parents[4] / "datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_native_filter_cost_bound_20260917"
reference = json.loads((TOOL / "reference_provenance.json").read_text())
path = "AnnService/src/Core/BKT/BKTIndex.cpp"
shutil.copy2(HERE / "bound_fix/BKTIndex.cpp", TOOL / "source" / path)
proof = reference["reference_source"]
after = dict(proof["after"])
after[path] = hashlib.sha256((TOOL / "source" / path).read_bytes()).hexdigest()
with (TOOL / "diagnostic_provenance.json").open("x") as output:
    json.dump({**proof, "after": after, "causal_fix": {
        "changed_native_file": path, "strict_current_native_worst_distance_only": True,
        "own_notifications_preserved_before_bound": True, "collapsed_stop_contract_unchanged": True,
        "ties_not_skipped": True, "new_budget_cache_or_frontier": False},
        "reference_binary_sha256": reference["reference_binary_sha256"]}, output, indent=2)
