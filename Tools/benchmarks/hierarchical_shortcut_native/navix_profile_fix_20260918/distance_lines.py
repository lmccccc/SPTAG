"""Supplement the graph's dominant SIMD PCs with exact optimized line tables."""
import bisect
from collections import Counter,defaultdict
import os
import shlex
import subprocess
import time
from profile import CONTROLS,DATA,TOOL,OUT,sha,write
from line_profiles import elf

def main():
    reports={}
    for label,(version,exe,_) in CONTROLS.items():
        root=DATA/"toolchains"/version
        build=root/"build/AnnService/CMakeFiles/DistanceUtils.dir"
        flags=dict(line.split(" = ",1) for line in (build/"flags.make").read_text().splitlines() if " = " in line)
        output=TOOL/"debug_objects"/version/"DistanceUtils.cpp.o"
        output.parent.mkdir(parents=True,exist_ok=True)
        frozen=build/"src/Core/Common/DistanceUtils.cpp.o"
        command=["/usr/bin/c++",*shlex.split(flags["CXX_DEFINES"]),
            *shlex.split(flags["CXX_INCLUDES"]),*shlex.split(flags["CXX_FLAGS"]),
            "-g1","-c",str(root/"source/AnnService/src/Core/Common/DistanceUtils.cpp"),"-o",str(output)]
        start=time.monotonic()
        with output.with_suffix(".log").open("w") as log:
            subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,
                env=dict(os.environ,TMPDIR=str(TOOL/"compiler-work")))
        old,oldsyms=elf(frozen);debug,syms=elf(output)
        executable={k:v for k,v in old.items() if v["flags"]&4}
        assert oldsyms==syms and all(v["body"]==debug[k]["body"] for k,v in executable.items())
        proof={"command":command,"seconds":time.monotonic()-start,
            "frozen_sha256":sha(frozen),"debug_sha256":sha(output),
            "executable_sections":len(executable),"instruction_byte_mismatches":[]}
        write(output.with_suffix(".json"),proof)
        binary=root/"harness"/exe
        native=[]
        for line in subprocess.check_output(["nm","-n",str(binary)],text=True).splitlines():
            fields=line.split(maxsplit=2)
            if len(fields)==3 and fields[1] in "tTWw":native.append((int(fields[0],16),fields[2]))
        addresses=[x[0] for x in native];groups=defaultdict(Counter)
        for rep in range(1,4):
            for line in (OUT/f"profile_{label}_r{rep}/raw_pcs.tsv").read_text().splitlines():
                _,pc,image,base,offset=line.split("\t")
                if image!=str(binary):continue
                address=int(offset,16);pos=bisect.bisect_right(addresses,address)-1
                name=native[pos][1];delta=address-addresses[pos]
                if name in syms:
                    section,value,size=syms[name]
                    if delta<size:groups[section][value+delta]+=1
        lines=Counter();resolved=[]
        for section,counts in groups.items():
            text=subprocess.check_output(["addr2line","-e",str(output),"-j",section],
                input="\n".join(hex(x) for x in counts)+"\n",text=True,timeout=60).splitlines()
            assert len(text)==len(counts)
            for (address,count),location in zip(counts.items(),text):
                lines[location]+=count
                resolved.append({"object_offset":hex(address),"section":section,"samples":count,"source":location})
        reports[label]={"samples":sum(lines.values()),"lines":lines.most_common(),
            "resolved":resolved,"proof":proof}
        print(label,"distance line samples",sum(lines.values()),flush=True)
    write(OUT/"distance_profiles.json",reports)
if __name__=="__main__":main()
