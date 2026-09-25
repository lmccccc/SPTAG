"""Separate main-query-thread C++ allocation evidence; never an ordinary timing source."""
import argparse
import json
import subprocess
from prepare import HERE, TOOL, OUTPUT, sha, write
from process import process


def main(variant, scenario):
    binary = TOOL / "matched-v1" if variant == "v1" else TOOL / "harness/matched-repair"
    symbols = subprocess.check_output(["nm", "--defined-only", str(binary)], text=True)
    offsets = {}
    for line in symbols.splitlines():
        fields = line.split()
        if len(fields) == 3:
            for key in ("capture", "profile"):
                if fields[2] == f"_ZN12ShortcutFull7{key}E":
                    offsets[key] = int(fields[0], 16)
    assert len(offsets) == 2
    library = TOOL / f"allocations-{variant}-{scenario}.so"
    subprocess.run(["c++", "-O2", "-shared", "-fPIC", "-std=c++17",
        str(HERE / "AllocationAudit.cpp"), "-o", str(library), "-ldl",
        *[f"-D{k.upper()}_OFFSET={v}" for k, v in offsets.items()]], check=True)
    directory = OUTPUT / f"allocation_{variant}_{scenario}"
    directory.mkdir()
    config = HERE / "configs" / ("broad_bad24.ini" if scenario == "broad" else "unfilter24.ini")
    wall = process(["env", f"LD_PRELOAD={library}", "numactl", "--cpunodebind=2", "--membind=2",
                    str(binary), "--config", str(config)], directory)
    raw = [line.split("\t") for line in (directory / "allocations.tsv").read_text().splitlines()]
    assert raw[-1][0] == "boundaries" and int(raw[-1][3]) == 8
    groups = {}
    for line in raw[:-1]:
        groups.setdefault(line[1], []).append(line)
    records = []
    for image, rows in groups.items():
        names = subprocess.check_output(["addr2line", "-Cf", "-e", image, *[r[2] for r in rows]],
                                        text=True).splitlines()
        for i, row in enumerate(rows):
            records.append({"phase": row[0], "image": image, "offset": row[2],
                "function": names[2 * i], "source": names[2 * i + 1],
                "calls_per_query": int(row[3]) / 1000, "bytes_per_query": int(row[4]) / 1000})
    result = {"variant": variant, "scenario": scenario, "ordinary_timing_source": False,
        "binary_sha256": sha(binary), "collector_sha256": sha(library), "process_seconds": wall,
        "scope": "Main query thread operator new/new[] including aligned forms; excludes C malloc and other threads. "
                 "Preallocated callsite counters allocate nothing. Capture is a separate diagnostic-bookkeeping phase.",
        "clock_boundaries": 8, "capture_profile_false_at_ordinary_start": True,
        "totals": {p: {"calls_per_query": sum(r["calls_per_query"] for r in records if r["phase"] == p),
                      "bytes_per_query": sum(r["bytes_per_query"] for r in records if r["phase"] == p)}
                   for p in ("ordinary", "capture")},
        "sites": sorted(records, key=lambda r: (r["phase"], -r["calls_per_query"]))}
    write(directory / "evidence.json", result)
    print(json.dumps({**result, "sites": [r for r in result["sites"] if r["phase"] == "ordinary"][:16]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("variant", choices=("v1", "v2"))
    parser.add_argument("scenario", choices=("broad", "unfilter"))
    args = parser.parse_args()
    main(args.variant, args.scenario)
