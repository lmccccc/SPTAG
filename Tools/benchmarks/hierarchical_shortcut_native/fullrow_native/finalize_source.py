"""Verify generated source and record the premeasurement final source manifest."""
import ast
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
here = Path(__file__).resolve().parent
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
auth = json.loads((root.parent / "authentication.json").read_text())
manifest = json.loads((root.parent / "supplier_integration.json").read_text())
name = "AnnService/inc/Core/Common/BKTree.h"
original = root.parent.parent / "h1_degree16_20260916/source" / name
assert sha(original) == auth["verified"][name], "Tree regeneration input is not authenticated"
syntax = ast.parse((here / "integrate.py").read_text())
functions = ast.Module(body=[n for n in syntax.body
                            if isinstance(n, ast.FunctionDef) and n.name in ("sub", "tree")],
                       type_ignores=[])
scope = {}
exec(compile(functions, "checked-tree-generator", "exec"), scope)
assert scope["tree"](original.read_text()).encode() == (root / name).read_bytes()
for path, record in manifest.items():
    if path != name:
        assert sha(root / path) == record["after"], path
for header in ("Supplier.h", "NativeAdapter.h", "Signature.h"):
    assert (root / "AnnService" / header).read_bytes() == (here / header).read_bytes(), header
destination = root.parent / "final_source_manifest.json"
assert not destination.exists(), "Preserve the completed source manifest"
paths = set(auth["verified"]) | set(manifest) | {
    "AnnService/" + h for h in ("Supplier.h", "NativeAdapter.h", "Signature.h", "FullHooks.h")}
destination.write_text(json.dumps({
    "before_measurement": True,
    "tree_generator_matches_authenticated_input": True,
    "initial_integration_manifest_preserved": True,
    "tree_leaf_checked_accounting_fixed_before_measurement": True,
    "source_sha256": {p: sha(root / p) for p in sorted(paths)},
    "binary_sha256": sha(root / "Release/spannaclbench"),
    "generator_sha256": sha(here / "integrate.py"),
}, indent=2) + "\n")
print("PASS: final native source, authenticated tree regeneration and binary manifest")
