"""Compare bounded authenticated heads, decoding the inherited cumulative capture."""
import json
import numpy as np
from prepare import OUTPUT,TOOL,sha,write

def main():
    reference=OUTPUT/"original_head_reference"
    current=OUTPUT/"small_broad_tag_visited_graph_ratio_0.01/nprobe_24"
    ids=np.fromfile(str(current)+".head.i32","<i4")
    distances=np.fromfile(str(current)+".head.f32","<f4")
    expected=np.fromfile(reference/"head.i32","<i4").reshape(32,25)
    expected_dist=np.fromfile(reference/"head.f32","<f4").reshape(32,24)
    pos=offset=0
    prior_ids=np.array([],dtype=np.int32);prior_dist=np.array([],dtype=np.float32)
    for q in range(32):
        n=int(ids[pos]);pos+=1
        assert n==24*(q+1)
        frame=ids[pos:pos+n];dist=distances[offset:offset+n]
        assert np.array_equal(frame[:-24],prior_ids) and np.array_equal(dist[:-24],prior_dist)
        assert expected[q,0]==24 and np.array_equal(frame[-24:],expected[q,1:])
        assert np.array_equal(dist[-24:],expected_dist[q])
        prior_ids=frame;prior_dist=dist;pos+=n;offset+=n
    assert pos==len(ids) and offset==len(distances)
    runtime=json.loads((OUTPUT/"runtime.json").read_text())
    assert sha(TOOL/"harness/postfilter-bench")==runtime["binary"]["sha256"]
    write(OUTPUT/"head_reference_proof.json",{
        "queries":32,"native_head_ids_distances_exact":True,
        "reference_binary_sha256":sha(TOOL/"harness/head-reference"),
        "measured_binary_unchanged":runtime["binary"]["sha256"],
        "capture_format":"inherited cumulative heads; verify preceding prefix then compare current 24-tail",
        "scope":"original frozen BKT selected H1 IDs/distances; not raw visited/queue trace"})
    print("PASS original H1 selected IDs/distances 32 queries; inherited cumulative capture decoded")

if __name__=="__main__":main()
