"""Preserve the first measured native reuse and isolate the collapsed-admission correction."""
from pathlib import Path
import json
import shutil

here = Path(__file__).resolve().parent
new = here.with_name("native_reuse_20260917_v2")
if new.exists():
    raise RuntimeError("Preserve native-reuse v2")
shutil.copytree(here, new, ignore=shutil.ignore_patterns("__pycache__"))
data = here.parents[4] / "datasets/sift1m_zipf200_sparse193_numeric"
status = data / "comparisons/h1_native_reuse_20260917/status.json"
with status.open("x") as stream:
    json.dump({"state": "superseded_collapsed_admission_fix", "resume_allowed": False,
        "completed_bounded_work": False,
        "reason": "Additional collapsed own-only fixture exposed duplicate CheckResultAndSet before native admission.",
        "replacement": "h1_native_reuse_20260917_v2", "raw_measurements_preserved": True}, stream, indent=2)
