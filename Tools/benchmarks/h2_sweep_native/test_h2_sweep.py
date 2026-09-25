"""Bounded synthetic save/load, counter parity, assignment and accounting checks."""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

counter, ordinary = map(lambda s: str(Path(s).resolve()), sys.argv[1:])
root = Path.cwd() / f"h2-sweep-fixture-{os.getpid()}"

def run(args, success=True):
    result = subprocess.run(args, cwd=Path.cwd(), capture_output=True, text=True, timeout=120)
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return result

try:
    run([ordinary, "--make-fixture", str(root)])
    base = f"""[Input]
HeadVectors={root}/heads.bin
Queries={root}/queries.fvecs
QueryCount=4
Dim=128
HeadIndexDirectory={root}/h2
HeadIDs={root}/ids.bin
FlatIndexDirectory={root}/flat
[Sweep]
Replicas=2,4,8,16,32
Beams=4,16,64,128
ResultNum=24
AssignmentCandidates=64
AssignmentMaxCheck=128
SearchMaxCheck=128
FlatMaxCheck=128
RNGFactor=1
Threads=1
BuildThreads=2
Warmup=1
Repeats=2
[Output]
Directory=OUTPUT
"""
    ini = root / "sweep.ini"
    def execute(binary, output, text=base, success=True):
        ini.write_text(text.replace("OUTPUT", str(root/output)))
        return run([binary, "--config", str(ini)], success)
    execute(counter, "counted")
    execute(ordinary, "ordinary")
    def rows(folder, name):
        return [json.loads(s) for s in (root/folder/name).read_text().splitlines()]
    counted = rows("counted", "queries.jsonl")
    normal = rows("ordinary", "queries.jsonl")
    assert len(counted) == len(normal) == 4 * (1 + 5*4)
    for a, b in zip(counted, normal):
        for key in ("mode", "query", "ids", "recall"):
            assert a[key] == b[key], (a,b)
        assert a["graph_distances"] > 0 and b["graph_distances"] is None
        if a["mode"] == "h2":
            assert a["total_distances"] == a["graph_distances"] + a["h1_unique_distances"]
            assert a["assignment_entries"] >= a["h1_unique_distances"]
            if a["beam"] == 128:
                assert a["h1_unique_distances"] == 256 and a["recall"] == 1
            assert a["oracle_nonruntime_recall"] == b["oracle_nonruntime_recall"]
    assert (root/"counted/COMPLETE").exists()
    for r in (2,4,8,16,32):
        name = f"replicas_{r}.csr"
        raw = (root/"counted"/name).read_bytes()
        assert raw == (root/"ordinary"/name).read_bytes()
        assert raw[:8] == b"H2SWCSR1"
        lower,upper,replicas,entries = struct.unpack_from("<iiiQ",raw,8)
        assert (lower,upper,replicas,entries) == (256,128,r,256*r)
        offsets=struct.unpack_from(f"<{upper+1}Q",raw,28)
        members=struct.unpack_from(f"<{entries}i",raw,28+8*(upper+1))
        owners=struct.unpack_from(f"<{entries}i",raw,28+8*(upper+1)+entries*4)
        assert offsets[0] == 0 and offsets[-1] == entries
        for child in range(lower):
            own=owners[child*r:(child+1)*r]
            assert len(set(own)) == r
            if child < upper:
                assert own[0] == child
            for parent in own:
                assert child in members[offsets[parent]:offsets[parent+1]]
    assert (root/"counted/assignment_candidates.bin").read_bytes() == (root/"ordinary/assignment_candidates.bin").read_bytes()
    bounds=rows("counted","boundary.jsonl")
    assert len(bounds) == 5*4*24
    for b in bounds:
        assert b["nearest_owner_rank"] == min(o["rank"] for o in b["owners"])
    for summary in rows("counted","boundary_summary.jsonl"):
        ranks=sorted(b["nearest_owner_rank"] for b in bounds if b["replicas"] == summary["replicas"])
        assert summary["targets"] == len(ranks)
        assert summary["nearest_owner_rank_max"] == ranks[-1]
        for threshold in (16,24,32,64,128):
            fraction=sum(rank<=threshold for rank in ranks)/len(ranks)
            assert abs(summary[f"fraction_rank_le_{threshold}"]-fraction)<1e-10
    # Exact native-distance results are unchanged both by enabling the callback
    # and by compiling the isolated object instead of the pristine archive object.
    execute(counter, "counted", success=False)
    execute(counter, "bad", base.replace("Threads=1\n", "Threads=2\n"), False)
    execute(counter, "bad", base.replace("[Sweep]", "[Sweep]\nUnknown=1"), False)
    execute(counter, "bad", base.replace("ResultNum=24", "ResultNum=23"), False)
    execute(counter, "bad", base.replace("Replicas=2,4,8,16,32", "Replicas=64"), False)
    original=(root/"ids.bin").read_bytes()
    (root/"ids.bin").write_bytes(struct.pack("<i",128)+original)
    execute(counter, "headered")
    (root/"ids.bin").write_bytes(struct.pack("<ii",128,1)+original)
    execute(counter, "dataset_headered")
    assert (root/"dataset_headered/replicas_8.csr").read_bytes() == (root/"counted/replicas_8.csr").read_bytes()
    (root/"ids.bin").write_bytes(struct.pack("<Q",128)+original)
    execute(counter, "uint64_headered")
    (root/"ids.bin").write_bytes(struct.pack("<ii",128,2)+original)
    execute(counter, "badshape", success=False)
    (root/"ids.bin").write_bytes(struct.pack("<128Q", *([999]*128)))
    execute(counter, "badmap", success=False)
    assert not (root/"badmap/COMPLETE").exists()
    print("PASS: native save/load, pristine-object parity, counter accounting, 16 points, CSR/self pin, boundary, strict INI, map validation")
finally:
    if root.exists():
        shutil.rmtree(root)
