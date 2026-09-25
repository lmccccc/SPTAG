"""Exactly frozen binary, eight sequential processes, graph/match only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent
DATA=HERE.parents[4]/"datasets/sift1m_zipf200_sparse193_numeric"
TOOL=DATA/"toolchains"/HERE.name
OUT=DATA/"comparisons"/HERE.name
VERSION="postfilter_visited_storage_20260918"
FROZEN=DATA/"comparisons"/VERSION
ROOT=DATA/"toolchains"/VERSION
BINARY=ROOT/"harness/postfilter-bench"

def sha(path):
    value=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(8*1024*1024),b""):value.update(block)
    return value.hexdigest()
def write(path,value):Path(path).write_text(json.dumps(value,indent=2)+"\n")
def protect():
    allhashes={}
    for name in ("milestone_manifest.json","protection.json"):
        allhashes.update(json.loads((FROZEN/name).read_text()))
    errors=[p for p,h in allhashes.items() if sha(p)!=h]
    assert not errors,errors
    assert sha(FROZEN/"report.json")=="f6ab36ad7de0109a3b04bbe968ed11b284068628c6686aaf5f4a8eab71ab6bc2"
    return {"verified_files":len(allhashes),"sealed_artifacts":len(json.loads((FROZEN/"milestone_manifest.json").read_text())),
            "manifest_sha256":sha(FROZEN/"milestone_manifest.json"),"differences":[]}
def main():
    assert not OUT.exists() and not TOOL.exists(),"One-shot diagnosis; never splice or rerun"
    assert not any(k.startswith(("SPTAG_","SPANN_","SHORTCUT_","NAVIX_")) or
        k in ("LD_PRELOAD","OMP_NUM_THREADS","OMP_PROC_BIND","OMP_PLACES") for k in os.environ)
    TOOL.mkdir(parents=True);OUT.mkdir(parents=True)
    write(OUT/"protection_before.json",protect())
    (TOOL/"compiler-work").mkdir()
    (HERE/"configs").mkdir()
    (OUT/"configs").mkdir()
    for mode in ("graph","match"):
        name=f"broad_tag_visited_{mode}_ratio_0.01.ini"
        for folder in (HERE/"configs",OUT/"configs"):
            shutil.copyfile(HERE.with_name(VERSION)/"configs"/name,folder/name)
    plan=[("control","graph",1),("control","match",1),
          ("profile","graph",1),("profile","match",1),
          ("profile","match",2),("profile","graph",2),
          ("control","match",2),("control","graph",2)]
    write(OUT/"plan.json",{"points":plan,"processes":8,"requested_period_ns":200000,
        "core":"exact frozen executable and static archives; no rebuild",
        "sampling":"ordinary body boundaries 5-6 only; capture/profile off",
        "protocol":"NUMA CPU/memory2; native INI unchanged; 1000 warmup+1000 storage checks+1000 measured+1000 capture",
        "scope":"Broad graph/match ONLY; no posting, no sparse, no ablation",
        "loader_exemption":"ELF interpreter --preload diagnostic DSO; no environment override",
        "ancestor_safeguard":"No ancestor run/prepare/build/main invoked. Only read-only validate helper imported."})
    nm=subprocess.check_output(["nm","-C",str(BINARY)],text=True)
    offsets={key:int(next(l.split()[0] for l in nm.splitlines() if l.endswith("ShortcutFull::"+key)),16)
        for key in ("capture","profile")}
    interpreter="/lib64/ld-linux-x86-64.so.2"
    readelf=subprocess.check_output(["readelf","-l",str(BINARY)],text=True)
    assert interpreter in readelf
    libraries={}
    for kind,period in (("control",0),("profile",200000)):
        library=TOOL/f"sampler_{kind}.so"
        argv=["g++","-std=c++17","-O2","-shared","-fPIC",f"-DPERIOD_NS={period}",
            f"-DCAPTURE_OFFSET={offsets['capture']}",f"-DPROFILE_OFFSET={offsets['profile']}",
            str(HERE/"ClockAudit.cpp"),"-ldl","-lrt","-o",str(library)]
        subprocess.run(argv,check=True,env=dict(os.environ,TMPDIR=str(TOOL/"compiler-work")))
        libraries[kind]=library
        write(OUT/f"build_{kind}.json",{"command":argv,"sha256":sha(library),"offsets":offsets})
    # Import only validation, without executing ancestor campaign entry points.
    sys.path.insert(0,str(HERE.with_name(VERSION)))
    spec=importlib.util.spec_from_file_location("frozen_validator",HERE.with_name(VERSION)/"run.py")
    validator=importlib.util.module_from_spec(spec);spec.loader.exec_module(validator)
    write(OUT/"availability.json",{"perf_event_paranoid":Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip(),
        "perf_executable":shutil.which("perf"),"privileges_changed":False})
    runtime={"binary":str(BINARY),"binary_sha256":sha(BINARY),
        "loader":interpreter,"loader_sha256":sha(interpreter),
        "ldd":subprocess.check_output(["ldd",str(BINARY)],text=True)}
    write(OUT/"runtime.json",runtime)
    results=[]
    for kind,mode,rep in plan:
        directory=OUT/f"{kind}_{mode}_r{rep}";directory.mkdir()
        cfg=OUT/"configs"/f"broad_tag_visited_{mode}_ratio_0.01.ini"
        argv=["numactl","--cpunodebind=2","--membind=2",interpreter,"--preload",
              str(libraries[kind]),str(BINARY),"--config",str(cfg)]
        start=time.monotonic()
        with (directory/"stdout.log").open("x") as stdout,(directory/"stderr.log").open("x") as stderr:
            completed=subprocess.run(argv,cwd=directory,stdout=stdout,stderr=stderr,timeout=180)
        elapsed=time.monotonic()-start
        write(directory/"execution.json",{"command":argv,"returncode":completed.returncode,
            "process_seconds":elapsed,"config_sha256":sha(cfg),"sampler_sha256":sha(libraries[kind]),
            "environment_overrides":{},"diagnostic_only":True})
        assert completed.returncode==0,(directory,completed.returncode)
        record=validator.validate(directory,"broad_tag",mode,1000,elapsed)
        prefix=directory/"nprobe_24"
        frozen=FROZEN/f"broad_tag_visited_{mode}_ratio_0.01_r1/nprobe_24"
        for suffix in validator.PAYLOADS+validator.TRACES:
            assert sha(str(prefix)+suffix)==sha(str(frozen)+suffix),(kind,mode,suffix)
        record["all_frozen_payloads_and_traces_exact"]=True
        flags=(directory/"profile_flags.tsv").read_text().splitlines()
        boundaries=[l.split("\t") for l in flags if l.startswith("boundary\t")]
        fields=dict(l.split("\t") for l in flags if not l.startswith("boundary\t"))
        assert len(boundaries)==8 and all(b[2:4]==["0","0"] for b in boundaries[:6])
        assert fields["dropped"]==fields["wrong_thread_samples"]=="0" and fields["query_tid"]==fields["pid"]
        assert int(fields["samples"])>500 if kind=="profile" else fields["samples"]=="0"
        record.update(kind=kind,mode=mode,rep=rep,flags=fields)
        write(directory/"record.json",record);results.append(record)
        write(OUT/"runs.json",results)
    write(OUT/"protection_after_runs.json",protect())
if __name__=="__main__":main()
