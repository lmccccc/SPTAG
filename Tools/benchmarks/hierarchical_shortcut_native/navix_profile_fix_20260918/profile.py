"""Diagnostic-only preload; never routed through the ordinary-run guard."""
import bisect
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

HERE=Path(__file__).resolve().parent
DATA=HERE.parents[4]/"datasets/sift1m_zipf200_sparse193_numeric"
TOOL=DATA/"toolchains"/HERE.name
OUT=DATA/"comparisons"/HERE.name
CONTROLS={
    "old":("navix_posting_20260917","navix-bench","broad_tag_navix.ini"),
    "graph":("cost_arbitration_v2_20260917","cost-bench","broad_tag_graph.ini"),
    "failed":("navix_hotpath_20260918","navix-bench","broad_tag_navix.ini")}

def sha(path):
    value=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b""): value.update(block)
    return value.hexdigest()
def write(path,value): path.write_text(json.dumps(value,indent=2)+"\n")

def symbols(binary):
    lines=subprocess.check_output(["nm","-n","-C",str(binary)],text=True).splitlines()
    result=[]
    for line in lines:
        fields=line.split(maxsplit=2)
        if len(fields)==3 and fields[1] in "tTWw":
            result.append((int(fields[0],16),fields[2]))
    return result

def main():
    assert not any(k.startswith(("SPTAG_","SPANN_","SHORTCUT_")) or
        k in ("LD_PRELOAD","OMP_NUM_THREADS","OMP_PROC_BIND","OMP_PLACES") for k in os.environ)
    TOOL.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(parents=True,exist_ok=True)
    plan={"repetitions":3,"interval_ns":250000,"bounded_pc_capacity":100000,
        "controls":CONTROLS,"timing_kind":"diagnostic only, not ordinary latency",
        "exemption":"LD_PRELOAD exclusively PC sampling, no search/data overrides",
        "scope":"main/query thread CPU timer; steady boundaries 5-6 only; capture/profile false",
        "input":"identical frozen Broad n24 native INIs; 1000 warmup/1000 measured/untimed replay"}
    write(OUT/"profile_plan.json",plan)
    summary={}
    for label,(version,exe,config) in CONTROLS.items():
        binary=DATA/"toolchains"/version/"harness"/exe
        cfg=HERE.with_name(version)/"configs"/config
        nm=subprocess.check_output(["nm","-C",str(binary)],text=True)
        offsets={key:int(next(l.split()[0] for l in nm.splitlines()
            if l.endswith("ShortcutFull::"+key)),16) for key in ("capture","profile")}
        library=TOOL/f"sampler_{label}.so"
        command=["g++","-std=c++17","-O2","-shared","-fPIC",
            f"-DCAPTURE_OFFSET={offsets['capture']}",f"-DPROFILE_OFFSET={offsets['profile']}",
            str(HERE/"ClockAudit.cpp"),"-ldl","-lrt","-o",str(library)]
        compiler_work=TOOL/"compiler-work"
        compiler_work.mkdir(exist_ok=True)
        subprocess.run(command,check=True,env=dict(os.environ,TMPDIR=str(compiler_work)))
        syms=symbols(binary); addresses=[s[0] for s in syms]
        functions=Counter(); images=Counter(); total=0; runs=[]
        for rep in range(1,4):
            directory=OUT/f"profile_{label}_r{rep}"
            recovered=directory.exists()
            directory.mkdir(exist_ok=True)
            env=dict(os.environ,LD_PRELOAD=str(library))
            argv=["numactl","--cpunodebind=2","--membind=2",str(binary),"--config",str(cfg)]
            start=time.monotonic()
            if not recovered:
                with (directory/"stdout.log").open("x") as stdout,(directory/"stderr.log").open("x") as stderr:
                    result=subprocess.run(argv,cwd=directory,env=env,stdout=stdout,stderr=stderr,timeout=180)
                assert result.returncode==0,(label,result.returncode)
            assert '{"engine":' in (directory/"stdout.log").read_text()
            flags=(directory/"profile_flags.tsv").read_text()
            boundaries=[l.split("\t") for l in flags.splitlines() if l.startswith("boundary\t")]
            assert len(boundaries)==8 and all(b[2:4]==["0","0"] for b in boundaries[:6]),flags
            fields=dict(l.split("\t") for l in flags.splitlines() if not l.startswith("boundary\t"))
            assert fields["dropped"]=="0" and fields["query_tid"]==fields["pid"],flags
            rows=[]
            for line in (directory/"raw_pcs.tsv").read_text().splitlines():
                index,pc,image,base,offset=line.split("\t")
                images[image]+=1; total+=1
                if Path(image).resolve()==binary.resolve():
                    address=int(offset,16); pos=bisect.bisect_right(addresses,address)-1
                    function=syms[pos][1] if pos>=0 else "unknown"
                    functions[function]+=1
                    rows.append(f"{offset}\t{function}\t0x{address-addresses[pos]:x}")
                else: rows.append(f"{offset}\tFOREIGN:{image}\t-")
            (directory/"symbolized_functions.tsv").write_text("\n".join(rows)+"\n")
            record={"command":argv,"environment_exemption":{"LD_PRELOAD":str(library)},
                "binary_sha256":sha(binary),"config_sha256":sha(cfg),"sampler_sha256":sha(library),
                "compile_command":command,"offsets":offsets,"flags":fields,
                "process_seconds":None if recovered else time.monotonic()-start,
                "recovered_after_python_hashlib_compatibility_error":recovered,"diagnostic_only":True}
            write(directory/"record.json",record); runs.append(record)
        assert total>=1500,(label,total)
        summary[label]={"samples":total,"functions":functions.most_common(),"images":images,
            "runs":runs,"attribution":"instruction residence only, not hardware-stall attribution"}
        write(OUT/"profile_summary.json",summary)
        print(label,total,functions.most_common(12),flush=True)
if __name__=="__main__": main()
