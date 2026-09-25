"""Verify the authentic result-filter helper, not unfiltered nearest24."""
import json
import numpy as np
from prepare import OUTPUT,TOOL,sha,write

def main():
    records=[]
    for scenario in ("broad_tag","extreme_tag"):
        ref=OUTPUT/"original_reference"/scenario
        current=OUTPUT/f"small_{scenario}_visited_graph_ratio_0.01/nprobe_24"
        for suffix in ("i32","f32"):
            assert sha(ref/("head."+suffix))==sha(str(current)+".head."+suffix)
        heads=np.fromfile(str(current)+".head.i32","<i4").reshape(32,25)
        assert np.all(heads[:,0]==24)
        records.append({"scenario":scenario,"queries":32,"head_ids_distances_exact":True,
            "matching_heads_mean":float(np.count_nonzero(heads[:,1:]>=0)/32),
            "reference":"authenticated SearchIndexWithResultFilter with same native Supports predicate",
            "reference_binary_sha256":sha(TOOL/"harness/head-reference")})
    runtime=json.loads((OUTPUT/"runtime.json").read_text())
    assert sha(TOOL/"harness/postfilter-bench")==runtime["binary"]["sha256"]
    write(OUTPUT/"head_reference_proof.json",{"records":records,"native_head_ids_distances_exact":True,
        "capture_format":"one independent 24-slot frame per query; inherited cumulative diagnostic format fixed",
        "scope":"native H1 output trace; same-core fixtures additionally compare queues and visited"})
    print("PASS authentic native result-filter selected-head parity for Broad and sparse32")

if __name__=="__main__":main()
