"""Four same-core controls, two reversed repetitions, then two sampled windows."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from prepare import HERE,DATA,TOOL,OUTPUT,sha,write,protect
from run import freeze,validate,require,PAYLOADS,CONTROL_TRACES
from process import process

OUT=OUTPUT
ROOT=TOOL
BINARY=TOOL/"harness/postfilter-bench"
CONTROLS={"A":("graph","shortcut"),"Aprime":("graph","predicate-before-shortcut"),
          "B":("graph","original"),"bit":("match","original")}
NAVIGATION=PAYLOADS+CONTROL_TRACES+(".visited.u64",".evaluated.f64")

def main():
    require(not (OUT/"experiment_plan.json").exists(),"One-shot bounded experiment; never silently rerun")
    freeze()
    configs=OUT/"cost_configs";configs.mkdir()
    for label,(mode,admission) in CONTROLS.items():
        text=(HERE/"configs"/f"broad_tag_visited_{mode}_ratio_0.01.ini").read_text()
        text=text.replace("DiagnosticAdmission=original","DiagnosticAdmission="+admission)
        (configs/(label+".ini")).write_text(text)
    plan=[("control",label,rep) for rep,order in ((1,list(CONTROLS)),(2,list(CONTROLS)[::-1]))
          for label in order]+[("profile","Aprime",1),("profile","bit",1)]
    write(OUT/"experiment_plan.json",{"points":plan,"ordinary_processes":8,"sampled_windows":2,
        "ablation":"A-prime changes only actual predicate evaluation before A's early return",
        "sampling":"query thread only, ordinary1000 window, warmup/storage/capture excluded",
        "same_core":True,"no_sparse1000":True,"no_tuning":True,"no_environment_overrides":True})
    symbols=subprocess.check_output(["nm","-C",str(BINARY)],text=True)
    offsets={key:int(next(l.split()[0] for l in symbols.splitlines()
                         if l.endswith("ShortcutFull::"+key)),16) for key in ("capture","profile")}
    interpreter="/lib64/ld-linux-x86-64.so.2"
    require(interpreter in subprocess.check_output(["readelf","-l",str(BINARY)],text=True),"ELF loader")
    libraries={}
    for kind,period in (("control",0),("profile",200000)):
        library=TOOL/f"sampler_{kind}.so"
        command=["g++","-std=c++17","-O2","-shared","-fPIC",f"-DPERIOD_NS={period}",
            f"-DCAPTURE_OFFSET={offsets['capture']}",f"-DPROFILE_OFFSET={offsets['profile']}",
            str(HERE/"ClockAudit.cpp"),"-ldl","-lrt","-o",str(library)]
        subprocess.run(command,check=True)
        libraries[kind]=library
        write(OUT/f"sampler_{kind}_build.json",{"command":command,"sha256":sha(library),"offsets":offsets})
    write(OUT/"sampling_availability.json",{
        "perf_event_paranoid":Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip(),
        "perf":shutil.which("perf"),"permissions_changed":False,
        "interpreter":interpreter,"interpreter_sha256":sha(interpreter),
        "preload":"explicit diagnostic ELF loader option only; no search or data environment override"})
    records=[]
    common=None
    for kind,label,rep in plan:
        mode,_=CONTROLS[label]
        directory=OUT/f"{kind}_{label}_r{rep}";directory.mkdir()
        cfg=configs/(label+".ini")
        command=["numactl","--cpunodebind=2","--membind=2",interpreter,"--preload",
                 str(libraries[kind]),str(BINARY),"--config",str(cfg)]
        elapsed=process(command,directory,max_seconds=180)
        record=validate(directory,"broad_tag",mode,1000,elapsed)
        flags=(directory/"profile_flags.tsv").read_text().splitlines()
        boundaries=[l.split("\t") for l in flags if l.startswith("boundary\t")]
        fields=dict(l.split("\t") for l in flags if not l.startswith("boundary\t"))
        require(len(boundaries)==8 and all(b[2:4]==["0","0"] for b in boundaries[:6]),"Incorrect sample window")
        require(fields["dropped"]==fields["wrong_thread_samples"]=="0" and
                fields["query_tid"]==fields["pid"],"Sampling thread/drop error")
        require(int(fields["samples"])>500 if kind=="profile" else fields["samples"]=="0","Sample count")
        prefix=directory/"nprobe_24"
        payload={s:sha(str(prefix)+s) for s in NAVIGATION}
        if common is None: common=payload
        require(payload==common,"A/A-prime/B/bit native navigation, SSD or result mismatch; no causal claim")
        expected=221.616 if label=="A" else (23.640 if label=="bit" else 1892.887)
        require(record["h1_admission_mean"]["result_predicate_evaluations"]==expected,
                "Actual callback count disagrees with independent original-order proof")
        frozen="h1_postfilter_native_predicate_20260918" if label=="A" else "h1_postfilter_predicate_order_20260918"
        old=DATA/"comparisons"/frozen/f"broad_tag_visited_{mode}_ratio_0.01_r1/nprobe_24"
        for suffix in NAVIGATION:
            require(sha(str(prefix)+suffix)==sha(str(old)+suffix),"Frozen native trajectory changed")
        record.update(kind=kind,control=label,rep=rep,flags=fields,config_sha256=sha(cfg),
            sampler_sha256=sha(libraries[kind]),all_control_trajectory_exact=True,
            actual_predicate_callbacks_per_query=record["h1_admission_mean"]["result_predicate_evaluations"]+
                record["match_mean"]["all_match_evaluations"])
        write(directory/"record.json",record);records.append(record)
        write(OUT/"cost_runs.json",records)
    write(OUT/"cross_control_trajectory.json",{"all_ten_runs_exact":True,"hashes":common})
    protect()

if __name__=="__main__":main()
