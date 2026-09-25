"""Authenticate the source-compatible pre-supplier baseline, not a later graph."""
import json
import subprocess
import configparser
import struct
import numpy as np
from prepare import ROOT,DATA,HERE,OUTPUT,sha,write

def main():
    root=DATA/"toolchains/matched_baseline_20260917/original"
    auth=json.loads((root/"authentication.json").read_text())
    files=["AnnService/src/Core/BKT/BKTIndex.cpp","AnnService/src/Core/SPANN/SPANNIndex.cpp",
           "AnnService/inc/Core/Common/BKTree.h","AnnService/inc/Core/Common/WorkSpace.h",
           "AnnService/inc/Core/SPANN/HeadNodeMetadata.h","AnnService/inc/Core/SPANN/LimitedTagSupport.h"]
    records={}
    for name in files:
        path=root/"source"/name
        actual=sha(path)
        assert actual==auth["verified"][name],name
        records[name]={"path":str(path),"sha256":actual,"authenticated":True}
    upstream={}
    for name in files[:3]:
        body=subprocess.check_output(["git","-C",str(ROOT/"SPTAG"),"show","5619bb1:"+name])
        path=OUTPUT/("upstream_"+name.rsplit("/",1)[1])
        path.write_bytes(body)
        upstream[name]={"sha256":sha(path),"path":str(path)}
    for name in files[:4]:
        current=HERE/name.rsplit("/",1)[1]
        result=subprocess.run(["git","--no-pager","diff","--no-index",str(root/"source"/name),str(current)],
                              capture_output=True,text=True)
        assert result.returncode in (0,1),result.stderr
        (OUTPUT/(current.name+".original.diff")).write_text(result.stdout)
    write(OUTPUT/"baseline_identity.json",{
        "revision":auth["revision"],"authentication_sha256":sha(root/"authentication.json"),
        "snapshot_diff_sha256":auth["source_diff_sha256"],
        "compatible_original":records,"upstream_5619bb1_reference":upstream,
        "not_pristine_upstream":True,
        "retained_compatible_native_deltas":["existing hard per-edge budget","existing limited tree continuation",
            "H/O storage and exact categorical/numeric/DNF final filters"],
        "removed":["experimental H1 result filter/eager result checks","H1 supplemental own heap",
            "match physical deletion checks","match alias eligibility/marker reads","match own/version/VID chain",
            "CheckValidPosting for bit initialization"],
        "allowed":["signature gates","native anchor attribute routing predicate",
            "same-slot visited bit and physical d/e","existing fixed .01 signed auxiliary adjacency"],
        "baseline_output_target":"authentic pre-supplier default H1, not predecessor experimental graph"})
    cfg=configparser.ConfigParser();cfg.read(HERE/"configs/broad_tag_visited_graph_ratio_0.01.ini")
    support=__import__("pathlib").Path(cfg["Benchmark"]["Index"])/"tenant_0/limited_tag_support.bin"
    raw=support.read_bytes()
    fields=struct.unpack_from("<10I3Q",raw)
    _,version,header,count,slots,_,_,_,key,attributes,*_=fields
    assert version in (3,4,5) and count==160091 and key<attributes
    tags=np.frombuffer(raw,"<u4",count*slots,header).reshape(count,slots)
    attrs=np.frombuffer(raw,"<u4",count*attributes,header+count*slots*4).reshape(count,attributes)
    covered=np.any(tags==attrs[:,key,None],axis=1)
    assert np.all(covered),"Existing support lacks own tag; do not introduce qualification fallback"
    write(OUTPUT/"actual_own_support_proof.json",{"source":str(support),"sha256":sha(support),
        "heads":count,"own_tag_covered":int(covered.sum()),"slots":slots,"attributes":attributes,
        "key_column":key,"scope":"offline exact native persisted base-slot membership, no query-time scan",
        "signature_proof":"native fixture calls CollectHierarchyHeadSignatures with empty posting PS"})

if __name__=="__main__":main()
