"""Native fixtures: strict inputs, reverse ownership, edge bounds and search parity."""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

binary, pristine = (str(Path(p).resolve()) for p in sys.argv[1:])
root = Path.cwd() / f"shortcut-fixture-{os.getpid()}"


def run(args, success=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return result


def csr(path, lower, upper):
    rows = [[] for _ in range(upper)]
    for x in range(lower):
        for p in (x % upper, (x + 1) % upper):
            rows[p].append(x)
    offsets = [0]
    for row in rows:
        offsets.append(offsets[-1] + len(row))
    header = struct.pack("<Q8IddQ", 0x325253324E4E4153, 3, 104, lower, upper, 2, 4, 32, 0,
                         0.0, 1.0, lower * 2).ljust(104, b"\0")
    path.write_bytes(header + struct.pack(f"<{upper+1}Q", *offsets) +
                     struct.pack(f"<{lower*2}I", *(x for row in rows for x in row)) + bytes(upper*32))


try:
    run([pristine, "--make-fixture", str(root)])
    vectors = (root / "heads.bin").read_bytes()[8:]
    (root / "h2.bin").write_bytes(struct.pack("<ii", 128, 128) + vectors[:128*512])
    (root / "h3.bin").write_bytes(struct.pack("<ii", 32, 128) + vectors[:32*512])
    (root / "h3map.bin").write_bytes(struct.pack("<32Q", *range(32)))
    csr(root / "lower.csr", 256, 128)
    csr(root / "upper.csr", 128, 32)
    base = f"""[Input]
HeadVectors={root}/heads.bin
Queries={root}/queries.fvecs
QueryCount=4
Dim=128
FlatIndexDirectory={root}/flat
H2Vectors={root}/h2.bin
H3Vectors={root}/h3.bin
H2Map={root}/ids.bin
H3Map={root}/h3map.bin
LowerCSR={root}/lower.csr
UpperCSR={root}/upper.csr
[Search]
ResultNum=24
Threads=1
Warmup=1
Repeats=2
DistanceBudgets=64,256,1024
NativeMaxChecks=128
MaxCheck=128
ShortcutDegree=8
ParentCount=2
[Output]
Directory=OUTPUT
"""

    def execute(name, text=base, success=True):
        ini = root / f"{name}.ini"
        ini.write_text(text.replace("OUTPUT", str(root / name)))
        return run([binary, "--config", str(ini)], success)

    execute("valid")
    execute("repeat")
    rows = [json.loads(s) for s in (root/"valid/queries.jsonl").read_text().splitlines()]
    other = [json.loads(s) for s in (root/"repeat/queries.jsonl").read_text().splitlines()]
    assert len(rows) == 40
    for a, b in zip(rows, other):
        assert all(a[k] == b[k] for k in a if k != "ordinary_us")
        budget = int(a["case"].split("_")[1])
        if not a["case"].startswith("native"):
            assert 0 < a["distance_calls"] <= budget
        assert len(a["ids"]) == len(set(a["ids"]))
        assert a["shortcut_distance_calls"] <= a["distance_calls"]
    assert any(r["shortcut_distance_calls"] > 0 for r in rows)
    assert any(r["exhausted"] for r in rows)
    raw = (root/"valid/shortcuts.bin").read_bytes()
    assert raw == (root/"repeat/shortcuts.bin").read_bytes()
    assert raw[:8] == b"H13EDGE1"
    n, bound = struct.unpack_from("<ii", raw, 8)
    assert (n, bound) == (256, 8)
    offsets = struct.unpack_from(f"<{n+1}Q", raw, 16)
    edges = struct.unpack_from(f"<{offsets[-1]}i", raw, 16+8*(n+1))
    for x in range(n):
        row = edges[offsets[x]:offsets[x+1]]
        assert len(row) <= bound and len(row) == len(set(row))
        assert all(0 <= y < n and y != x for y in row)
    # Pristine archive must have exactly equal ordered flat IDs at natural termination.
    oldini = f"""[Input]
HeadVectors={root}/heads.bin
Queries={root}/queries.fvecs
QueryCount=4
Dim=128
HeadIndexDirectory={root}/h2
HeadIDs={root}/ids.bin
FlatIndexDirectory={root}/flat
[Sweep]
Replicas=2
Beams=4
ResultNum=24
AssignmentCandidates=64
AssignmentMaxCheck=128
SearchMaxCheck=128
FlatMaxCheck=128
Threads=1
BuildThreads=1
Warmup=0
Repeats=1
[Output]
Directory={root}/pristine
"""
    (root/"pristine.ini").write_text(oldini)
    run([pristine, "--config", str(root/"pristine.ini")])
    oldrows = [json.loads(s) for s in (root/"pristine/queries.jsonl").read_text().splitlines()]
    assert [r["ids"] for r in rows if r["case"] == "native_128"] == [
        r["ids"] for r in oldrows if r["mode"] == "flat"]
    execute("valid", success=False)
    for a, b in [("Threads=1", "Threads=2"), ("ShortcutDegree=8", "ShortcutDegree=99"),
                 ("DistanceBudgets=64,256,1024", "DistanceBudgets=64,64"),
                 ("[Search]", "[Search]\nUnknown=1"),
                 ("ResultNum=24", "ResultNum=24\nResultNum=24"),
                 ("[Search]", "[Unexpected]")]:
        execute("bad_ini", base.replace(a,b), False)
    original = (root/"lower.csr").read_bytes()
    corrupt = bytearray(original)
    struct.pack_into("<I", corrupt, 104 + 129*8, 999)
    (root/"lower.csr").write_bytes(corrupt)
    execute("invalid_member", success=False)
    corrupt = bytearray(original)
    struct.pack_into("<Q", corrupt, 104+8, 999999)
    (root/"lower.csr").write_bytes(corrupt)
    execute("invalid_offset", success=False)
    (root/"lower.csr").write_bytes(original[:-1])
    execute("truncated", success=False)
    corrupt = bytearray(original)
    # The first row contains [0,127,128,255]; duplicate its second member.
    struct.pack_into("<I", corrupt, 104+129*8+4, 0)
    (root/"lower.csr").write_bytes(corrupt)
    execute("duplicate_member", success=False)
    corrupt = bytearray(original)
    struct.pack_into("<I", corrupt, 104+129*8+4, 126)
    (root/"lower.csr").write_bytes(corrupt)
    execute("invalid_reverse_ownership", success=False)
    (root/"lower.csr").write_bytes(original)
    (root/"h3map.bin").write_bytes(struct.pack("<32Q", *([0]*32)))
    execute("duplicate_map", success=False)
    print("PASS: pristine native parity, count-mode parity, hard budget, deterministic bounded edges,"
          " both CSR layers, reverse owners, strict INI, invalid maps/members/offsets/truncation")
finally:
    if root.exists():
        shutil.rmtree(root)
