"""Exact optimized executable-section proof and frozen-PC source resolution."""
import bisect
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import time
from experiment import HERE,DATA,TOOL,OUT,ROOT,BINARY,sha,write

def elf(path):
    data=path.read_bytes()
    assert data[:6]==b"\x7fELF\x02\x01"
    h=struct.unpack_from("<16sHHIQQQIHHHHHH",data)
    sections=[struct.unpack_from("<IIQQQQIIQQ",data,h[6]+i*h[11]) for i in range(h[12])]
    strings=sections[h[13]];names=data[strings[4]:strings[4]+strings[5]]
    def text(raw,offset):return raw[offset:raw.index(b"\0",offset)].decode()
    result={};symbols={}
    for i,s in enumerate(sections):
        name=text(names,s[0]);body=data[s[4]:s[4]+s[5]]
        result[name]={"index":i,"flags":s[2],"body":body}
        if s[1]==2:
            strings=sections[s[6]];raw=data[strings[4]:strings[4]+strings[5]]
            for pos in range(0,len(body),24):
                n,info,other,ndx,value,size=struct.unpack_from("<IBBHQQ",body,pos)
                if info&15==2 and 0<ndx<len(sections):
                    symbols[text(raw,n)]=(text(names,sections[ndx][0]),value,size)
    return result,symbols

def compile_object(kind):
    target="DistanceUtils" if kind=="DistanceUtils" else "SPTAGLibStatic"
    rel=f"src/Core/Common/{kind}.cpp" if kind=="DistanceUtils" else f"src/Core/{kind}/{kind}Index.cpp"
    build=ROOT/f"build/AnnService/CMakeFiles/{target}.dir"
    flags=dict(l.split(" = ",1) for l in (build/"flags.make").read_text().splitlines() if " = " in l)
    frozen=build/(rel+".o")
    output=TOOL/"debug_objects"/(Path(rel).name+".o")
    output.parent.mkdir(exist_ok=True)
    command=["/usr/bin/c++",*shlex.split(flags["CXX_DEFINES"]),*shlex.split(flags["CXX_INCLUDES"]),
        *shlex.split(flags["CXX_FLAGS"]),"-g1","-c",str(ROOT/"source/AnnService"/rel),"-o",str(output)]
    start=time.monotonic()
    if not output.exists():
        with output.with_suffix(".log").open("x") as log:
            subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,
                env=dict(os.environ,TMPDIR=str(TOOL/"compiler-work")))
    old,oldsyms=elf(frozen);new,newsyms=elf(output)
    executable={k:v for k,v in old.items() if v["flags"]&4}
    mismatches=[k for k,v in executable.items() if k not in new or v["body"]!=new[k]["body"]]
    assert not mismatches,(kind,mismatches)
    assert oldsyms==newsyms
    proof={"command":command,"frozen":str(frozen),
        "frozen_sha256":sha(frozen),"debug_sha256":sha(output),"seconds":time.monotonic()-start,
        "executable_sections":len(executable),"instruction_byte_mismatches":mismatches,
        "symbol_tables_equal":True,"source_sha256":sha(ROOT/"source/AnnService"/rel)}
    if not output.with_suffix(".json").exists():write(output.with_suffix(".json"),proof)
    return output,newsyms

def nm(binary,demangle=False):
    argv=["nm","-n","-S"]+(["-C"] if demangle else [])+[str(binary)]
    result=[]
    for line in subprocess.check_output(argv,text=True).splitlines():
        f=line.split(maxsplit=3)
        if len(f)==4 and f[2] in "tTWw":
            result.append((int(f[0],16),int(f[1],16),f[3]))
    return result

def main():
    assert not (OUT/"milestone_manifest.json").exists(),"Sealed"
    with ThreadPoolExecutor(max_workers=2) as pool:
        objects=list(pool.map(compile_object,("SPANN","BKT","DistanceUtils")))
    debugdir=TOOL/"debug_objects"
    for archive,selected in (("libSPTAGLibStatic.a",objects[:2]),("libDistanceUtils.a",objects[2:])):
        dest=debugdir/archive
        shutil.copyfile(ROOT/"source/Release"/archive,dest)
        subprocess.run(["ar","r",str(dest),*[str(o[0]) for o in selected]],check=True)
    linked=debugdir/"postfilter-bench.debug"
    command=shlex.split((ROOT/"harness/CMakeFiles/postfilter-bench.dir/link.txt").read_text())
    command[command.index("-o")+1]=str(linked)
    command=[str(debugdir/Path(a).name) if Path(a).name in ("libSPTAGLibStatic.a","libDistanceUtils.a") else a for a in command]
    with (debugdir/"link.log").open("w") as log:
        subprocess.run(command,cwd=ROOT/"harness",stdout=log,stderr=subprocess.STDOUT,check=True,
            env=dict(os.environ,TMPDIR=str(TOOL/"compiler-work")))
    write(debugdir/"link.json",{"command":command,"cwd":str(ROOT/"harness"),
        "sha256":sha(linked),"never_executed":True})
    old,_=elf(BINARY);new,_=elf(linked)
    executable={k:v for k,v in old.items() if v["flags"]&4}
    mismatches=[k for k,v in executable.items() if k not in new or v["body"]!=new[k]["body"]]
    write(OUT/"linked_executable_equivalence.json",{"sections":list(executable),
        "instruction_byte_mismatches":mismatches,"frozen_sha256":sha(BINARY),"debug_sha256":sha(linked)})
    assert not mismatches
    native=nm(BINARY);addresses=[s[0] for s in native];counts=Counter(s[2] for s in native)
    debug=nm(linked);debugcounts=Counter(s[2] for s in debug);debugnames={s[2]:s for s in debug}
    demangled={(a,size):name for a,size,name in nm(BINARY,True)}
    (OUT/"symbols_native.tsv").write_text("\n".join(f"{a:x}\t{s:x}\t{n}\t{demangled.get((a,s),'')}" for a,s,n in native)+"\n")
    samples=[];targets=set()
    for mode in ("Aprime","bit"):
        for rep in (1,):
            directory=OUT/f"profile_{mode}_r{rep}"
            maps=[]
            for line in (directory/"module_maps_start.txt").read_text().splitlines():
                fields=line.split(maxsplit=5)
                lo,hi=[int(v,16) for v in fields[0].split("-")]
                maps.append((lo,hi,fields[1],int(fields[2],16),fields[5] if len(fields)>5 else ""))
            for line in (directory/"raw_pcs.tsv").read_text().splitlines():
                i,pc,image,base,offset=line.split("\t")
                pc,base,offset=int(pc,16),int(base,16),int(offset,16)
                assert pc==base+offset
                region=next((m for m in maps if m[0]<=pc<m[1] and "x" in m[2]),None)
                assert region is not None,(directory,i,hex(pc))
                row={"run":directory.name,"sample":int(i),"pc":hex(pc),"image":image,"base":hex(base),
                    "offset":hex(offset),"map":region,"function":"FOREIGN","source":"unresolved","mapped_address":None}
                if Path(image).resolve()==BINARY.resolve():
                    assert Path(region[4]).resolve()==BINARY.resolve()
                    pos=bisect.bisect_right(addresses,offset)-1
                    if pos>=0:
                        a,size,name=native[pos];delta=offset-a
                        if delta<size:
                            row.update(function=demangled.get((a,size),name),mangled=name,function_offset=delta)
                            if counts[name]==debugcounts[name]==1:
                                for obj,syms in objects:
                                    if name in syms and delta<syms[name][2]:
                                        mapped=debugnames[name][0]+delta
                                        row.update(mapped_address=mapped,debug_object=str(obj))
                                        targets.add(mapped);break
                samples.append(row)
    locations={};targets=sorted(targets)
    pattern=re.compile(r"^\s*(\S+)\s+(\d+|-)\s+(0x[0-9a-f]+)\b")
    previous=None
    with (OUT/"decoded_lines.txt").open("w") as dump:
        child=subprocess.Popen(["readelf","--debug-dump=decodedline",str(linked)],stdout=subprocess.PIPE,text=True)
        for line in child.stdout:
            dump.write(line)
            match=pattern.match(line)
            if not match:continue
            file,number,address=match.groups();address=int(address,16)
            if previous and address>=previous[0]:
                lo=bisect.bisect_left(targets,previous[0]);hi=bisect.bisect_left(targets,address)
                for target in targets[lo:hi]:locations[target]=previous[1]
            previous=None if number=="-" else (address,file+":"+number)
        assert child.wait()==0
    for row in samples:
        row["source"]=locations.get(row["mapped_address"],"unresolved")
        if row["mapped_address"] is not None:row["mapped_address"]=hex(row["mapped_address"])
    foreign={}
    for row in samples:
        if row["function"]=="FOREIGN" and Path(row["image"]).is_file():
            foreign.setdefault(row["image"],set()).add(row["offset"])
    proofs={}
    for image,offsets in foreign.items():
        offsets=sorted(offsets,key=lambda x:int(x,16))
        text=subprocess.check_output(["addr2line","-f","-e",image],
            input="\n".join(offsets)+"\n",text=True).splitlines()
        assert len(text)==2*len(offsets)
        resolved={offset:(text[2*i],text[2*i+1]) for i,offset in enumerate(offsets)}
        proofs[image]={"sha256":sha(image),"offsets":resolved,
            "build_id":subprocess.check_output(["readelf","-n",image],text=True)}
        for row in samples:
            if row["image"]==image and row["function"]=="FOREIGN":
                row["foreign_function"],row["foreign_source"]=resolved[row["offset"]]
    write(OUT/"foreign_symbols.json",proofs)
    write(OUT/"symbolized_samples.json",samples)
    for mode in ("Aprime","bit"):
        rows=[r for r in samples if r["run"].startswith("profile_"+mode)]
        write(OUT/f"{mode}_symbol_summary.json",{
            "samples":len(rows),"source":Counter(r["source"] for r in rows).most_common(),
            "functions":Counter(r["function"] for r in rows).most_common(),
            "images":Counter(r["image"] for r in rows).most_common(),
            "executable_map_coverage":len(rows),"mapped_source":sum(r["source"]!="unresolved" for r in rows)})
    # Preserve sampled-function disassembly (no full 20MB template assembly dump).
    names=Counter(r.get("mangled") for r in samples if r["source"]!="unresolved")
    with (OUT/"sampled_disassembly.txt").open("w") as output:
        for name,count in names.most_common():
            if name is None:continue
            subprocess.run(["objdump","-d","--disassemble="+name,str(linked)],stdout=output,check=True)
    with (OUT/"foreign_disassembly.txt").open("w") as output:
        for image,offsets in foreign.items():
            pages=sorted(set(int(x,16)//256*256 for x in offsets))
            for address in pages:
                subprocess.run(["objdump","-d","--start-address="+hex(address),
                    "--stop-address="+hex(address+256),image],stdout=output,check=True)
    write(OUT/"symbolization.json",{"samples":len(samples),"executable_map_matches":len(samples),
        "source_matches":sum(r["source"]!="unresolved" for r in samples),
        "mapping":"unique ELF name + bounded function offset; optimized object executable bytes and symbol tables identical",
        "unmapped":"foreign images, unbuilt translation units, duplicate ELF names or no line range remain unresolved"})
if __name__=="__main__":main()
