"""Map frozen PCs via byte-identical O3 debug objects, never a foreign image."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import time
from profile import CONTROLS, DATA, TOOL, OUT, sha, write

def elf(path):
    data=path.read_bytes()
    assert data[:6]==b"\x7fELF\x02\x01"
    header=struct.unpack_from("<16sHHIQQQIHHHHHH",data)
    sections=[struct.unpack_from("<IIQQQQIIQQ",data,header[6]+i*header[11])
        for i in range(header[12])]
    strings=sections[header[13]]
    names=data[strings[4]:strings[4]+strings[5]]
    def string(raw,offset): return raw[offset:raw.index(b"\0",offset)].decode()
    result={}; symbols={}
    for i,section in enumerate(sections):
        name=string(names,section[0])
        body=data[section[4]:section[4]+section[5]]
        result[name]={"index":i,"flags":section[2],"body":body}
        if section[1]==2:
            strings=sections[section[6]]
            raw=data[strings[4]:strings[4]+strings[5]]
            for pos in range(0,len(body),24):
                n,info,other,ndx,value,size=struct.unpack_from("<IBBHQQ",body,pos)
                if info&15==2 and 0<ndx<len(sections):
                    symbols[string(raw,n)]=(string(names,sections[ndx][0]),value,size)
    return result,symbols

def compile_object(version,kind):
    root=DATA/"toolchains"/version
    build=root/"build/AnnService/CMakeFiles/SPTAGLibStatic.dir"
    flags=dict(line.split(" = ",1) for line in (build/"flags.make").read_text().splitlines()
        if " = " in line)
    rel=f"src/Core/{kind}/{kind}Index.cpp"
    frozen=build/(rel+".o")
    output=TOOL/"debug_objects"/version/(kind+"Index.cpp.o")
    output.parent.mkdir(parents=True,exist_ok=True)
    compiler_work=TOOL/"compiler-work"
    compiler_work.mkdir(exist_ok=True)
    command=["/usr/bin/c++",*shlex.split(flags["CXX_DEFINES"]),
        *shlex.split(flags["CXX_INCLUDES"]),*shlex.split(flags["CXX_FLAGS"]),
        "-g1","-c",str(root/"source/AnnService"/rel),"-o",str(output)]
    start=time.monotonic()
    previous=output.with_suffix(".json")
    reused=previous.exists() and output.exists() and sha(output)==json.loads(previous.read_text())["debug_object_sha256"]
    if not reused:
        with output.with_suffix(".log").open("w") as log:
            subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,
                env=dict(os.environ,TMPDIR=str(compiler_work)))
    original,oldsyms=elf(frozen); debug,newsyms=elf(output)
    executable={name:value for name,value in original.items() if value["flags"]&4}
    mismatches=[name for name,value in executable.items()
        if name not in debug or value["body"]!=debug[name]["body"]]
    record={"command":command,"seconds":time.monotonic()-start,"frozen_object_sha256":sha(frozen),
        "debug_object_sha256":sha(output),"executable_sections":len(executable),
        "instruction_byte_mismatches":mismatches,"semantics":"O3 unchanged, added -g1 only"}
    if not reused: write(output.with_suffix(".json"),record)
    assert not mismatches,(version,kind,mismatches)
    assert oldsyms==newsyms
    return output,newsyms

def main():
    allsummary={}
    for label,(version,exe,_) in CONTROLS.items():
        with ThreadPoolExecutor(max_workers=2) as pool:
            objects=list(pool.map(lambda kind:compile_object(version,kind),("SPANN","BKT")))
        binary=DATA/"toolchains"/version/"harness"/exe
        debugdir=TOOL/"debug_objects"/version
        library=debugdir/"libSPTAGLibStatic.a"
        shutil.copyfile(DATA/"toolchains"/version/"source/Release/libSPTAGLibStatic.a",library)
        subprocess.run(["ar","r",str(library),*[str(o[0]) for o in objects]],check=True)
        linked=debugdir/exe
        harness=binary.parent
        link=shlex.split((harness/f"CMakeFiles/{exe}.dir/link.txt").read_text())
        link[link.index("-o")+1]=str(linked)
        link=[str(library) if arg.endswith("/libSPTAGLibStatic.a") else arg for arg in link]
        start=time.monotonic()
        with (debugdir/"link.log").open("w") as log:
            subprocess.run(link,cwd=harness,stdout=log,stderr=subprocess.STDOUT,check=True,
                env=dict(os.environ,TMPDIR=str(TOOL/"compiler-work")))
        write(debugdir/"link.json",{"command":link,"cwd":str(harness),"seconds":time.monotonic()-start,
            "sha256":sha(linked),"diagnostic_only":True,"not_executed":True})
        debug_symbols={}
        for line in subprocess.check_output(["nm","-n",str(linked)],text=True).splitlines():
            parts=line.split(maxsplit=2)
            if len(parts)==3 and parts[1] in "tTWw":
                debug_symbols[parts[2]]=int(parts[0],16)
        native=subprocess.check_output(["nm","-n",str(binary)],text=True).splitlines()
        syms=[]
        for line in native:
            parts=line.split(maxsplit=2)
            if len(parts)==3 and parts[1] in "tTWw":
                syms.append((int(parts[0],16),parts[2]))
        import bisect
        addresses=[s[0] for s in syms]
        name_counts=Counter(s[1] for s in syms)
        linked_counts=Counter(); unknown=Counter()
        linked_names={}
        for run in range(1,4):
            directory=OUT/f"profile_{label}_r{run}"
            for line in (directory/"raw_pcs.tsv").read_text().splitlines():
                _,pc,image,base,offset=line.split("\t")
                if Path(image).resolve()!=binary.resolve(): continue
                address=int(offset,16); pos=bisect.bisect_right(addresses,address)-1
                name=syms[pos][1]; delta=address-addresses[pos]
                if name_counts[name]!=1:
                    unknown[name+" [ambiguous local ELF name]"]+=1
                    continue
                found=False
                for obj,table in objects:
                    if name in table:
                        section,value,size=table[name]
                        if delta<size:
                            linked_counts[debug_symbols[name]+delta]+=1
                            linked_names[debug_symbols[name]+delta]=name
                            found=True
                            break
                if not found: unknown[name]+=1
        lines=Counter(); resolved=[]
        # GNU addr2line repeats expensive lookups in this very large template TU.
        # Decode the line matrix once and resolve half-open instruction ranges.
        targets=sorted(linked_counts); locations={}
        command=["readelf","--debug-dump=decodedline",str(linked)]
        child=subprocess.Popen(command,stdout=subprocess.PIPE,text=True)
        pattern=re.compile(r"^\s*(\S+)\s+(\d+|-)\s+(0x[0-9a-f]+)\b")
        previous=None
        for line in child.stdout:
            match=pattern.match(line)
            if not match: continue
            file,number,address=match.groups(); address=int(address,16)
            if previous and address>=previous[0]:
                lo=bisect.bisect_left(targets,previous[0])
                hi=bisect.bisect_left(targets,address)
                for target in targets[lo:hi]: locations[target]=previous[1]
            previous=None if number=="-" else (address,file+":"+number)
        assert child.wait()==0
        for address,n in linked_counts.items():
            location=locations.get(address,"unresolved-line")
            lines[location]+=n
            resolved.append({"debug_image":str(linked),"mapped_address":hex(address),
                "samples":n,"function_mangled":linked_names[address],"source":location})
        write(OUT/f"profile_{label}_lines.json",{"resolved":resolved,
            "lines":lines.most_common(),"unmapped_native_functions":unknown.most_common()})
        allsummary[label]={"mapped_samples":sum(lines.values()),"lines":lines.most_common(30),
            "unmapped_samples":sum(unknown.values()),"mapping":"exact named ELF function + byte offset; all executable object sections byte-identical"}
        write(OUT/"profile_lines_summary.json",allsummary)
        print(label,"mapped",sum(lines.values()),"unmapped",sum(unknown.values()),flush=True)
if __name__=="__main__":main()
