#!/usr/bin/env python3
"""Build matched ordinary/timing clients in a fresh directory; never rebuild an index."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sources(root):
    paths = {root / "CMakeLists.txt"}
    for name in ("AnnService", "Wrappers", "ThirdParty", "Tools/benchmarks/hierarchical_shortcut_native"):
        paths.update(path for path in (root / name).rglob("*")
                     if path.is_file() and (path.suffix in (".cpp", ".h", ".hpp", ".c", ".cmake")
                                           or path.name == "CMakeLists.txt"))
    return {str(path): sha(path) for path in sorted(paths)}


def build(source, output, jobs):
    source, output = source.resolve(), output.resolve()
    if output.exists() or jobs < 1 or source == output or source in output.parents:
        raise ValueError("Use a fresh build directory outside the source checkout and positive jobs")
    before = sources(source)
    output.mkdir(parents=True)
    clients = {}
    for name, timing in (("ordinary", "OFF"), ("timing", "ON")):
        directory, binaries = output / (name + "-build"), output / (name + "-bin")
        commands = [
            ["cmake", "-S", str(source), "-B", str(directory), "-DCMAKE_BUILD_TYPE=Release",
             "-DSPDK=OFF", "-DROCKSDB=OFF", "-DURING=OFF", "-DSPTAG_QUERY_WORK_DIAGNOSTICS=OFF",
             "-DSPTAG_QUERY_STEP_TIMING=" + timing, "-DSPTAG_OUTPUT_DIRECTORY=" + str(binaries)],
            ["cmake", "--build", str(directory), "--parallel", str(jobs), "--target", "nativeBench"],
        ]
        with (output / (name + ".log")).open("x") as log:
            for command in commands:
                subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
        binary = binaries / "nativeBench"
        clients[name] = dict(binary=str(binary), sha256=sha(binary),
                             cache=str(directory / "CMakeCache.txt"), commands=commands)
    if sources(source) != before:
        raise ValueError("Sources changed while building the matched pair")
    record = dict(schema_version=1, status="complete", sources=before, clients=clients,
                  build_policy="same-source; work counters OFF; legacy phase logging OFF; raw STATIC libaio")
    (output / "build.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps(dict(status="complete", build=str(output / "build.json"))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    build(args.source, args.output, args.jobs)
