"""Separate diagnostic objects only. Ordinary binaries never include OrderTrace."""
import json
import shutil
import subprocess
import time
from prepare import HERE,DATA,TOOL,OUTPUT,sha,write

ORIGINAL=DATA/"toolchains/matched_baseline_20260917/original/source"
STAGES=["tree_initial","tree_continuation","ordinary","popped_head","aliases"]

def replace(text,old,new,count=1):
    assert text.count(old)==count,(old,text.count(old),count)
    return text.replace(old,new)

def instrument_bkt(text,current):
    text='#include "OrderTrace.h"\n'+text
    start=text.index("void Index<T>::Search(COMMON::QueryResultSet")
    end=text.index("int Index<T>::SearchIterative",start)
    body=text[start:end]
    pos=body.index("{")
    body=body[:pos+1]+"\n    OrderTrace::StageScope searchStage(OrderTrace::TreeInitial);"+body[pos+1:]
    body=replace(body,"    std::chrono::high_resolution_clock::time_point graphStart;",
        "    OrderTrace::stage=OrderTrace::Popped;\n    std::chrono::high_resolution_clock::time_point graphStart;")
    body=replace(body,"            float p_distance) {",
        "            float p_distance) {\n            OrderTrace::Emit('H',p_result,p_key,OrderTrace::bits(p_distance));")
    if not current:
        body=replace(body,"            return p_resultFilter(p_result) &&",
            "            const bool valid=p_resultFilter(p_result);\n"
            "            OrderTrace::Emit('P',p_result,valid,p_distance>p_query.worstDist());\n"
            "            const bool admitted=valid &&")
    else:
        body=replace(body,"            return valid &&",
            "            OrderTrace::Emit('P',p_result,valid,(p_distance>p_query.worstDist())+2*reused);\n"
            "            const bool admitted=valid &&")
    body=replace(body,"                    p_query, p_result, p_distance);",
        "                    p_query, p_result, p_distance);\n"
        "            OrderTrace::Emit('A',p_result,admitted,OrderTrace::bits(p_distance));\n"
        "            return admitted;")
    body=replace(body,"            float p_representativeDistance) {",
        "            float p_representativeDistance) {\n"
        "            OrderTrace::StageScope aliasStage(OrderTrace::Alias);")
    marker="            for (DimensionType edge = p_begin;"
    body=replace(body,marker,"            OrderTrace::StageScope edgeStage(OrderTrace::Ordinary);\n"+marker)
    body=replace(body,"                const float routeDistance =",
        "                OrderTrace::Emit('D',targetLocal,OrderTrace::bits(distance));\n"
        "                const float routeDistance =")
    text=text[:start]+body+text[end:]
    if current:
        text=replace(text,"        const auto matchPhysical=[&](SizeType id) {",
            "        const auto matchPhysical=[&](SizeType id) {\n            OrderTrace::InitScope initializing;")
    return text

def instrument_tree(text,current):
    text='#include "OrderTrace.h"\n'+text
    start=text.index("            void InitSearchTrees(")
    end=text.index("            std::string GetPriorityID(",start)
    body=text[start:end]
    signature="const std::function<bool(SizeType)>& p_graphFilter = nullptr) const\n            {"
    assert body.count(signature)==2
    a,b=body.split("            void SearchTrees(",1)
    a=replace(a,signature,signature+"\n                OrderTrace::StageScope traceStage(OrderTrace::TreeInitial);")
    b=replace(b,signature,signature+"\n                OrderTrace::StageScope traceStage("
        "OrderTrace::stage==OrderTrace::TreeInitial ? OrderTrace::TreeInitial : OrderTrace::TreeContinuation);")
    body=a+"            void SearchTrees("+b
    if current:
        body=replace(body,"                    auto* hook = p_space.m_nativeHooks;",
            "                    OrderTrace::Emit('D',node.centerid,OrderTrace::bits(d));\n"
            "                    auto* hook = p_space.m_nativeHooks;",2)
    else:
        body=replace(body,"                    return fComputeDistance(p_query.GetQuantizedTarget(), data[id], data.C());",
            "                    const float d=fComputeDistance(p_query.GetQuantizedTarget(), data[id], data.C());\n"
            "                    OrderTrace::Emit('D',id,OrderTrace::bits(d));\n                    return d;",2)
    return text[:start]+body+text[end:]

def instrument_workspace(text,current):
    text='#include "OrderTrace.h"\n'+text
    start=text.index("        struct WorkSpace :")
    before,body=text[:start],text[start:]
    if current:
        body=replace(body,"                return visited;",
            "                OrderTrace::Emit('V',idx,visited);\n                return visited;")
        body=replace(body,"                return result;\n            }\n\n            inline bool CheckResultAndSet",
            "                OrderTrace::Emit('V',id,result.first);\n                return result;\n"
            "            }\n\n            inline bool CheckResultAndSet")
    else:
        body=replace(body,"                return nodeCheckStatus.CheckAndSet(idx);",
            "                const bool visited=nodeCheckStatus.CheckAndSet(idx);\n"
            "                OrderTrace::Emit('V',idx,visited);\n                return visited;")
    return before+body

def execute(command,log):
    if log.exists():
        backup=log.with_suffix(log.suffix+".previous")
        assert not backup.exists(),backup
        shutil.copyfile(log,backup)
    start=time.monotonic()
    with log.open("w") as stream:
        result=subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT)
    record={"command":command,"returncode":result.returncode,"seconds":time.monotonic()-start,"log":str(log)}
    assert result.returncode==0,record
    return record

def build():
    assert not (OUTPUT/"milestone_manifest.json").exists(),"Sealed milestone"
    root=TOOL/"order_diagnostic";root.mkdir(exist_ok=True)
    records=[]
    auth=json.loads((ORIGINAL.parent/"authentication.json").read_text())
    for current,label,source in ((False,"original",ORIGINAL),(True,"current",TOOL/"source")):
        overlay=root/label
        shutil.copytree(source/"AnnService",overlay/"AnnService",symlinks=True,dirs_exist_ok=True)
        shutil.copyfile(HERE/"OrderTrace.h",overlay/"AnnService/OrderTrace.h")
        paths={"src/Core/BKT/BKTIndex.cpp":instrument_bkt,
               "inc/Core/Common/BKTree.h":instrument_tree,
               "inc/Core/Common/WorkSpace.h":instrument_workspace}
        hashes={}
        for relative,fn in paths.items():
            original=source/"AnnService"/relative;target=overlay/"AnnService"/relative
            if not current: assert sha(original)==auth["verified"]["AnnService/"+relative]
            target.write_text(fn(original.read_text(),current))
            hashes[relative]={"source":str(original),"source_sha256":sha(original),"diagnostic_sha256":sha(target)}
        write(OUTPUT/("order_"+label+"_source.json"),hashes)
        obj=overlay/"BKTIndex.o"
        flags=["/usr/bin/c++","-std=c++17","-O3","-DNDEBUG","-DTBB","-DNUMA","-fopenmp",
               "-fPIC","-fno-omit-frame-pointer","-Wno-unknown-pragmas",
               "-I"+str(overlay/"AnnService"),"-I"+str(source/"ThirdParty/zstd/lib")]
        records.append(execute(flags+["-c",str(overlay/"AnnService/src/Core/BKT/BKTIndex.cpp"),"-o",str(obj)],
                               OUTPUT/("order_"+label+"_compile.log")))
        for clean in ((True,False) if not current else (False,)):
            name="order-"+label+("-clean" if clean else "")
            includes=source/"AnnService" if clean else overlay/"AnnService"
            command=["/usr/bin/c++","-std=c++17","-O3","-DNDEBUG","-DTBB","-DNUMA","-fopenmp",
                "-I"+str(includes),"-I"+str(source/"Wrappers"),"-I"+str(HERE)]
            if current: command+=["-DORDER_CURRENT","-DMATCHED_CURRENT"]
            command += [str(HERE/"OrderBench.cpp")]
            if not clean: command += [str(obj)]
            command += [str(ORIGINAL.parent/"build/AnnService/CMakeFiles/spannaclbench.dir/__/Wrappers/src/CoreInterface.cpp.o")]
            command += [str(source/"Release"/("lib"+lib+".a")) for lib in
                        ("SPTAGLibStatic","DistanceUtils","RaBitQ2Lib","zstd")]
            command += ["-lboost_system","-lboost_thread","-lboost_filesystem","-lboost_regex",
                        "-lboost_serialization","-lboost_wserialization","-ltbb","-lnuma","-ldl","-lrt",
                        "-o",str(root/name)]
            records.append(execute(command,OUTPUT/(name+"_link.log")))
        write(OUTPUT/"order_build_commands.json",records)
    write(OUTPUT/"order_binaries.json",{str(p):sha(p) for p in root.glob("order-*")})

def replay():
    assert not (OUTPUT/"milestone_manifest.json").exists(),"Sealed milestone"
    root=TOOL/"order_diagnostic"
    configs=OUTPUT/"order_reference_configs";configs.mkdir()
    for scenario,count in (("broad_tag",1000),("extreme_tag",32)):
        folder=HERE/("configs" if count==1000 else "diagnostic_configs")
        text=(folder/f"{scenario}_visited_graph_ratio_0.01.ini").read_text()
        original_cfg=configs/(scenario+".ini")
        original_cfg.write_text(text.replace("Case=visited_graph_ratio_0.01","Case=h1_original")
            .replace("VisitedMatchMode=graph\n","").replace("PostingNeighborMatchRatio=0.01\n",""))
        for label,binary,mode in (("clean","order-original-clean","graph"),("original","order-original","graph"),
                                  ("graph","order-current","graph"),("match","order-current","match")):
            cfg=original_cfg if label in ("clean","original") else folder/f"{scenario}_visited_{mode}_ratio_0.01.ini"
            directory=OUTPUT/f"order_{scenario}_{label}";directory.mkdir()
            command=["numactl","--cpunodebind=2","--membind=2",str(root/binary),str(cfg),mode]
            with (directory/"stdout.log").open("w") as out,(directory/"stderr.log").open("w") as err:
                result=subprocess.run(command,cwd=directory,stdout=out,stderr=err,timeout=180)
            write(directory/"command.json",{"command":command,"returncode":result.returncode,
                                          "untimed":True,"queries":count})
            assert result.returncode==0,directory

if __name__=="__main__":
    import sys
    if sys.argv[1:]==["--build"]: build()
    elif sys.argv[1:]==["--replay"]: replay()
    else: raise SystemExit("Expected --build or --replay")
