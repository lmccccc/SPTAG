"""Install repository-owned fused sources into the new private toolchain."""
import json
import shutil
from prepare import HERE, TOOL, FILES, sha

proof = json.loads((TOOL / "parent_provenance.json").read_text())
after = dict(proof["source"]["after"])
for local, dest in FILES.items():
    shutil.copyfile(HERE / local, TOOL / "source" / dest)
    after[dest] = sha(TOOL / "source" / dest)
with (TOOL / "diagnostic_provenance.json").open("w") as out:
    json.dump({**proof["source"], "after": after, "fused_parent": proof,
               "scope": "ordinary neighbor degree/eligibility fusion and local result handoff only"},
              out, indent=2)
