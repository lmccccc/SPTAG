"""Freeze only the diagnosed collapsed-alias admission correction."""
import configparser
import json
from pathlib import Path
import shutil
from prepare import sha, write

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[4] / "datasets/sift1m_zipf200_sparse193_numeric"
old = DATA / "toolchains/h1_native_reuse_20260917"
target = DATA / "toolchains/h1_native_reuse_20260917_v2"
output = DATA / "comparisons/h1_native_reuse_20260917_v2"
if target.exists() or output.exists():
    raise RuntimeError("Preserve native-reuse v2")
proof = json.loads((old / "native_reuse_provenance.json").read_text())
for name, digest in proof["after"].items():
    assert sha(old / "source" / name) == digest, name
target.mkdir()
shutil.copytree(old / "source", target / "source", symlinks=True, ignore=shutil.ignore_patterns("Release"))
name = "AnnService/src/Core/BKT/BKTIndex.cpp"
shutil.copy2(HERE / "BKTIndex.cpp", target / "source" / name)
after = dict(proof["after"])
after[name] = sha(target / "source" / name)
write(target / "native_reuse_provenance.json", {
    **proof, "parent": str(old), "parent_source_hashes": proof["after"], "after": after,
    "revision2_only_change": name,
    "correction": "Native result admission owns CheckResultAndSet; do not pre-mark a collapsed alias.",
    "parent_binary_sha256": sha(old / "source/Release/spannaclbench"),
})
c = configparser.ConfigParser()
c.read(HERE / "experiment.ini")
c["Experiment"].update(binary=str(target / "source/Release/spannaclbench"),
    toolchain=str(target), outputdirectory=str(output))
with (HERE / "experiment.ini").open("w") as stream:
    c.write(stream)
p = json.loads((HERE / "preregistration.json").read_text())
p["phase"] = "bounded_native_reuse_v2"
p["reason"] = "Correct double result de-duplication of collapsed aliases; same bounded protocol, no new tuning."
(HERE / "preregistration.json").write_text(json.dumps(p, indent=2) + "\n")
