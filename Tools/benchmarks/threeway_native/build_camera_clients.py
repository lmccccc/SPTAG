#!/usr/bin/env python3
"""Link camera-OR adapters using existing matching native build recipes."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess

from build_diskann_client import artifact, runtime_dependencies, verify_inputs_unchanged

HERE = Path(__file__).resolve().parent


def build(workspace, output, engines=("lion", "pipeann", "diskann"), lion_source=None,
          lion_build=None, packed_head_layout=False):
    recipes = {
        "lion": ("SPTAG/build/AnnService", "nativeBench", "spann_bench.cpp"),
        "pipeann": ("pipeann-filter/build-readonly/tests", "search_disk_index_filtered", "pipeann_bench.cpp"),
        "diskann": ("FilteredDiskann-OR/build/tests", "diskann_or_tests", "diskann_or_bench.cpp"),
    }
    if not engines or len(set(engines)) != len(engines) or any(engine not in recipes for engine in engines):
        raise ValueError("Expected distinct native engines")
    if (lion_build or packed_head_layout) and "lion" not in engines:
        raise ValueError("Lion build/layout options require the lion engine")
    if packed_head_layout and not lion_build:
        raise ValueError("Packed heads require an explicit matching --lion-build")
    output.mkdir(parents=True, exist_ok=False)
    for engine in engines:
        relative, target, source = recipes[engine]
        directory = workspace / relative
        core_source = lion_source or workspace / "SPTAG"
        if engine == "lion":
            directory = (lion_build or core_source / "build") / "AnnService"
            if lion_build:
                cache = (lion_build / "CMakeCache.txt").read_text()
                home = next(line.split("=", 1)[1] for line in cache.splitlines()
                            if line.startswith("CMAKE_HOME_DIRECTORY:INTERNAL="))
                core_source = Path(home).resolve()
                if lion_source and core_source != lion_source:
                    raise ValueError("Lion build was configured from a different source")
                if packed_head_layout and any(
                        f"{key}:BOOL=ON\n" in cache
                        for key in ("SPTAG_QUERY_WORK_DIAGNOSTICS", "SPTAG_QUERY_STEP_TIMING", "USE_ASAN")):
                    raise ValueError("Packed performance clients require an ordinary native core")
        recipe_dir = directory / "CMakeFiles" / (target + ".dir")
        flags = {}
        for line in (recipe_dir / "flags.make").read_text().splitlines():
            if line.startswith("CXX_") and " = " in line:
                key, value = line.split(" = ", 1)
                flags[key] = shlex.split(value)
        link = shlex.split((recipe_dir / "link.txt").read_text())
        output_pos = link.index("-o")
        libraries = link[output_pos + 2:]
        objects = [arg for arg in link[1:output_pos] if arg.endswith(".o") and "CoreInterface.cpp.o" in arg]
        compiler = link[0]
        command = [compiler, *flags["CXX_DEFINES"], *flags["CXX_INCLUDES"], *flags["CXX_FLAGS"],
                   "-O3", "-fno-fast-math", "-Wno-error"]
        if engine == "lion":
            command += ['-DTHREEWAY_SPANN_CORE="' + str(directory.parent) + '"']
            if packed_head_layout:
                command += ["-DTHREEWAY_SPANN_PACKED_HEAD_LAYOUT"]
            elif lion_source:
                command += ["-DTHREEWAY_SPANN_POSTING_LAYOUT_ABLATION"]
        if engine == "pipeann":
            command += ['-DTHREEWAY_PIPEANN_SOURCE="' + str(workspace / "pipeann-filter") + '"',
                        '-DTHREEWAY_PIPEANN_LIBRARY_SHA256="' +
                        artifact(workspace / "pipeann-filter/build-readonly/src/libpipeann.a")["sha256"] + '"']
        # Linker search paths can occur before the original object/output arguments.
        command += [arg for arg in link[1:output_pos] if arg.startswith(("-L", "-Wl,"))]
        depfile = output / (engine + ".d")
        binary = output / (engine + "Bench")
        command += ["-MMD", "-MF", str(depfile), str(HERE / source), *objects,
                    *libraries, "-o", str(binary)]
        inputs = [HERE / source, HERE / "benchmark.h", Path(__file__),
                  recipe_dir / "flags.make", recipe_dir / "link.txt"]
        if engine == "lion" and lion_build:
            inputs.append(lion_build / "CMakeCache.txt")
        if engine == "lion" and (lion_source or lion_build):
            inputs += [core_source / path for path in (
                "AnnService/inc/Core/SPANN/Options.h",
                "AnnService/inc/Core/SPANN/ParameterDefinitionList.h",
                "AnnService/src/Core/SPANN/SPANNIndex.cpp", "Wrappers/src/CoreInterface.cpp",
                "Wrappers/inc/CoreInterface.h")]
        inputs += [(directory / arg).resolve() for arg in objects + libraries
                   if not arg.startswith("-") and (arg.endswith(".a") or arg.endswith(".o") or ".so" in arg)]
        before = [artifact(path) for path in dict.fromkeys(inputs)]
        record = {"engine": engine, "cwd": str(directory), "argv": command, "inputs": before}
        if engine == "lion" and (lion_source or lion_build):
            record["native_source"] = {
                "root": str(core_source),
                "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=core_source, text=True).strip(),
                "patch": subprocess.check_output(["git", "diff", "--no-ext-diff", "HEAD", "--",
                                                  "AnnService", "Wrappers",
                                                  "Tools/benchmarks/threeway_native/benchmark.h",
                                                  "Tools/benchmarks/threeway_native/spann_bench.cpp"],
                                                 cwd=core_source, text=True),
                "submodules": subprocess.check_output(["git", "submodule", "status"],
                                                       cwd=core_source, text=True).strip(),
            }
        record["packed_head_layout_supported"] = engine == "lion" and packed_head_layout
        (output / (engine + ".command.json")).write_text(json.dumps(record, indent=2) + "\n")
        with (output / (engine + ".compile.log")).open("x") as log:
            subprocess.run(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True)
        verify_inputs_unchanged(before)
        known = {item["resolved"] for item in before}
        dependencies = shlex.split(depfile.read_text().replace("\\\n", " "))[1:]
        for dependency in dependencies:
            path = (directory / dependency).resolve()
            if str(path) not in known:
                record["inputs"].append(artifact(path))
                known.add(str(path))
        record.update(status="complete", binary=artifact(binary), runtime_libraries=runtime_dependencies(binary))
        (output / (engine + ".build.json")).write_text(json.dumps(record, indent=2) + "\n")
        print(f"Built {binary}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--engines", nargs="+", choices=("lion", "pipeann", "diskann"),
                        default=("lion", "pipeann", "diskann"))
    parser.add_argument("--lion-source", type=Path,
                        help="Isolated posting-layout core source with its matching build/AnnService recipe")
    parser.add_argument("--lion-build", type=Path,
                        help="Existing CMake build containing matching nativeBench wrapper objects and core")
    parser.add_argument("--packed-head-layout", action="store_true",
                        help="Compile the checked packed-layout API against the explicit ordinary Lion build")
    args = parser.parse_args()
    build(args.workspace.resolve(), args.output_directory.resolve(), args.engines,
          args.lion_source.resolve() if args.lion_source else None,
          args.lion_build.resolve() if args.lion_build else None, args.packed_head_layout)
