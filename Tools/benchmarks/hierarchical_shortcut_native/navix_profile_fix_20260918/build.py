import os
import subprocess
import time
from prepare import HERE,TOOL,OUTPUT,install,protect,sha,write

def main():
    install()
    (TOOL/"compiler-work").mkdir(exist_ok=True)
    env=dict(os.environ,TMPDIR=str(TOOL/"compiler-work"))
    commands=[
        ("configure",["cmake","-S",str(TOOL/"source"),"-B",str(TOOL/"build"),
            "-DCMAKE_BUILD_TYPE=Release","-DSPDK=OFF","-DROCKSDB=OFF"]),
        ("build",["cmake","--build",str(TOOL/"build"),"--target","SPTAGLibStatic","-j2"]),
        ("harness-configure",["cmake","-S",str(HERE),"-B",str(TOOL/"harness"),
            "-DCMAKE_BUILD_TYPE=Release","-DSPANN_ROOT="+str(TOOL/"source")]),
        ("harness-build",["cmake","--build",str(TOOL/"harness"),"-j2"]),
        ("native-tests",["ctest","--test-dir",str(TOOL/"harness"),"--output-on-failure"]),
        ("protocol-tests",["python3",str(HERE/"test_protocol.py")])]
    records=[]
    for name,command in commands:
        start=time.monotonic()
        with (OUTPUT/(name+".log")).open("w") as log:
            result=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT)
        records.append({"name":name,"command":command,"seconds":time.monotonic()-start,
            "returncode":result.returncode,"compiler_work":env["TMPDIR"]})
        write(OUTPUT/"build_commands.json",records)
        assert result.returncode==0,(name,result.returncode)
    protect()
    write(OUTPUT/"build_artifacts.json",{
        "binary_sha256":sha(TOOL/"harness/navix-bench"),
        "native_tests_sha256":sha(TOOL/"harness/native-tests"),
        "libraries":{str(p):sha(p) for p in (TOOL/"source/Release").glob("*.a")}})
if __name__=="__main__":main()
