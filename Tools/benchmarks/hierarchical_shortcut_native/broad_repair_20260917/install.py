import shutil
from prepare import HERE, TOOL, OUTPUT, sha, write
from prepare_repair import FILES

installed = {}
for local, target in FILES.items():
    shutil.copyfile(HERE / local, TOOL / "source" / target)
    installed[target] = sha(TOOL / "source" / target)
write(OUTPUT / "repair_sources.json", installed)
