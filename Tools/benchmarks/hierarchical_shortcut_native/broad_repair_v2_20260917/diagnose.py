"""Separate clock/PC diagnostics on the unchanged bad Broad binary."""
import argparse
import configparser
import json
from pathlib import Path
import subprocess
from prepare import HERE, REPO, TOOL, OUTPUT, sha, write
from process import process


def main(phase, variant):
    binary = (TOOL / "matched-current" if variant == "frozen" else
              TOOL / "matched-v1" if variant == "v1" else TOOL / "harness/matched-repair")
    symbols = subprocess.check_output(["nm", "--defined-only", str(binary)], text=True)
    offsets = {}
    for line in symbols.splitlines():
        fields = line.split()
        if len(fields) == 3:
            for name in ("capture", "profile"):
                if fields[2] == f"_ZN12ShortcutFull7{name}E":
                    offsets[name] = int(fields[0], 16)
    assert set(offsets) == {"capture", "profile"}
    sample = phase == "sample"
    suffix = "" if variant == "frozen" else "_v1" if variant == "v1" else "_repair"
    library = TOOL / (("pc-sample" if sample else "clock-proof") + suffix + ".so")
    command = ["c++", "-shared", "-fPIC", "-O2", "-std=c++17", str(HERE / "ClockAudit.cpp"),
        "-o", str(library), "-ldl", "-pthread", "-lrt",
        f"-DCAPTURE_OFFSET={offsets['capture']}", f"-DPROFILE_OFFSET={offsets['profile']}"]
    if sample:
        command.append("-DAUDIT_SAMPLE")
    subprocess.run(command, check=True)
    config = HERE / "configs/broad_bad24.ini"
    if phase == "flags":
        config = HERE / "configs/flags_32_24.ini"
        cfg = configparser.ConfigParser()
        cfg.optionxform = str
        cfg.read(HERE / "configs/broad_bad24.ini")
        cfg["SearchSweep"]["NProbe"] = "[32,24]"
        if config.exists():
            existing = configparser.ConfigParser()
            existing.optionxform = str
            existing.read(config)
            assert {s: dict(existing[s]) for s in existing.sections()} == {
                s: dict(cfg[s]) for s in cfg.sections()}
        else:
            with config.open("x") as out:
                cfg.write(out, space_around_delimiters=False)
    directory = OUTPUT / f"diagnostic_{phase}{suffix}"
    directory.mkdir()
    command = ["env", f"LD_PRELOAD={library}", "numactl", "--cpunodebind=2", "--membind=2",
               str(binary), "--config", str(config)]
    wall = process(command, directory)
    clocks = [line.split("\t") for line in (directory / "clock_audit.tsv").read_text().splitlines()]
    expected = 14 if phase == "flags" else 8
    assert sum(int(row[3]) for row in clocks) == expected and all(row[0] == "steady" for row in clocks)
    locations = subprocess.check_output(["addr2line", "-Cf", "-e", str(binary),
        *[row[2] for row in clocks]], text=True).splitlines()
    assert all("Run(int, char**)" in name for name in locations[::2])
    flag_text = (directory / "ordinary_flags.tsv").read_text().splitlines()
    flags = [line.split("\t") for line in flag_text[:-1]]
    assert len(flags) == expected and all(row[1:] == ["0", "0"] for row in flags)
    result = {"ordinary_timing_source": False, "binary_sha256": sha(binary),
        "clock_calls": expected, "fine_clocks": 0, "actual_ordinary_capture_profile_false": True,
        "process_seconds": wall, "sample_thread": "only native query main thread", "requested_sample_period_us": 250,
        "sampled_thread_cpu_seconds": float(flag_text[-1].split("\t")[1]), "clock_locations": locations}
    if sample:
        lines = [line.split("\t") for line in (directory / "cpu_samples.tsv").read_text().splitlines()]
        assert int(lines[-1][3]) == 0
        resolved = []
        groups = {}
        for line in lines[:-1]:
            groups.setdefault(line[1], []).append(line)
        for image, addresses in groups.items():
            names = subprocess.check_output(["addr2line", "-Cf", "-e", image,
                *[row[2] for row in addresses]], text=True).splitlines()
            for i, row in enumerate(addresses):
                resolved.append({"image": image, "offset": row[2], "function": names[2 * i],
                                 "source": names[2 * i + 1], "samples": int(row[3])})
        grouped = {}
        for r in resolved:
            key = r["image"], r["function"], r["source"]
            grouped[key] = grouped.get(key, 0) + r["samples"]
        totals = sum(r["samples"] for r in resolved)
        result["samples"] = totals
        result["observed_samples_per_cpu_second"] = totals / result["sampled_thread_cpu_seconds"]
        result["native_hash_resize_log_events_all_phases"] = sum(
            (directory / filename).read_text().count("Hash table is full!")
            for filename in ("stdout.log", "stderr.log"))
        result["functions"] = [{"image": key[0], "function": key[1], "source": key[2],
            "samples": count, "fraction": count / totals,
            "estimated_instrumented_cpu_ms_per_query": result["sampled_thread_cpu_seconds"] * count / totals}
            for key, count in sorted(grouped.items(), key=lambda kv: -kv[1])]
        write(directory / "resolved_pcs.json", resolved)
    write(directory / "evidence.json", result)
    print(json.dumps(result if not sample else {**result, "functions": result["functions"][:18]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("clocks", "flags", "sample"))
    parser.add_argument("--variant", choices=("frozen", "v1", "repair"), default="frozen")
    args = parser.parse_args()
    main(args.phase, args.variant)
