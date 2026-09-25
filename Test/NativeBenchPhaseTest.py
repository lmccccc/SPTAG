#!/usr/bin/env python3
"""32-query native phase-log controls; never load a billion-row index."""
import argparse
import configparser
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

from NativeBenchClientTest import identities, read_ini, require


def write_ini(path, config):
    with path.open("w") as stream:
        config.write(stream, space_around_delimiters=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("client", "ordinary", "fixtures", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir()
    configs, indexes, before = {}, set(), {}
    for dtype, seed in (("Float", "float-explicit"), ("UInt8", "uint8")):
        base = read_ini(args.fixtures / seed / "native.ini")
        index = Path(base["Benchmark"]["Index"])
        with (index / "tenant_0/HeadIndex/vectors.bin").open("rb") as stream:
            rows, dimension = struct.unpack("<ii", stream.read(8))
        require(0 < rows <= 200000 and dimension == 128, "Only small existing indexes are allowed")
        indexes.add(index)
        before[str(index)] = identities(index)
        for phase in (False, True):
            paths = []
            for case in (1, 2):
                config = read_ini(args.fixtures / seed / "native.ini")
                config["Benchmark"].update(MaxQueries="32", Warmup="32")
                if phase:
                    config["Benchmark"]["PhaseTiming"] = "true"
                config["SearchSSDIndex"].update(LogPhaseTime=str(phase).lower(),
                    EnablePostingNavigation="true" if case == 2 else "false")
                if case == 2:
                    predicate = "categorical" if dtype == "Float" else "dnf"
                    config["Benchmark"].update(Predicate=predicate,
                        PredicateFile=str((args.fixtures /
                            ("categorical.npy" if dtype == "Float" else "mixed.npy")).resolve()))
                config["SearchSweep"]["NProbe"] = "[16,24]"
                path = output / f"{dtype}-{phase}-{case}.ini"
                write_ini(path, config)
                paths.append(path)
            configs[dtype, phase] = paths

    runs, comparisons = {}, []
    def run(name, binary, paths, phase=False, error=None):
        directory = output / name
        directory.mkdir()
        batch = configparser.ConfigParser(interpolation=None)
        batch.optionxform = str
        batch["Batch"] = dict(CaseCount=str(len(paths)))
        for i, path in enumerate(paths, 1):
            batch[f"Case{i}"] = dict(Config=str(path), OutputDirectory=str(directory / f"case{i}"))
        manifest = directory / "batch.ini"
        write_ini(manifest, batch)
        command = ["numactl", "--cpunodebind=0", "--membind=0", str(binary.resolve()), str(manifest)]
        events, phase_rows, windows, pending = [], [], [], []
        active = None
        with (directory / "native.log").open("w") as log:
            process = subprocess.Popen(command, cwd=directory, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True)
            for line_no, line in enumerate(process.stdout, 1):
                log.write(line)
                if "PhaseTime:" in line:
                    require(phase and active is not None, "Unexpected/unattributed phase log")
                    fields = dict(re.findall(r"(\w+)=([^\s]+)", line.split("PhaseTime:", 1)[1]))
                    require({"nprobe", "heads", "headScanned", "headMaxCheck", "postIO",
                             "postPages", "bkt", "pq", "graphOther", "post", "io", "scan",
                             "postOther", "total"} <= fields.keys(), "Incomplete native phase row")
                    pending.append(dict(line=line_no, case_id=active, fields=fields))
                    phase_rows.append(pending[-1])
                if not (line.startswith('{"event":') or line.startswith('{"mode":')):
                    continue
                event = json.loads(line)
                events.append(event)
                if event.get("event") == "case_begin":
                    require(not pending, "Phase rows crossed a case boundary")
                    active = event["case_id"]
                elif event.get("event") == "point":
                    require(event["case_id"] == active and event["queries"] == 32, "Wrong case/cohort")
                    if binary == args.client:
                        require(event["phase_timing"] == phase, "Wrong phase label")
                        require(not event["diagnostic"], "Phase client must use the normal core")
                    folder = Path(event["output_directory"]) / f"nprobe_{event['nprobe']}"
                    for filename, size in (("ids.i32", 1280), ("dist.f32", 1280),
                                           ("work.u64", 2048), ("latency_us.f64", 256)):
                        require((folder / filename).stat().st_size == size,
                                "Point emitted before complete payloads")
                    require(not (folder / "navigation.u64").exists(), "Unexpected work-counter payload")
                    require(len(pending) == (96 if phase else 0), "Missing/extra phase rows per point")
                    if phase:
                        require(all(int(row["fields"]["nprobe"]) == event["nprobe"] for row in pending),
                                "Phase rows crossed probe boundaries")
                        windows.append(dict(case_id=active, nprobe=event["nprobe"],
                            warmup_lines=[row["line"] for row in pending[:32]],
                            measured_lines=[row["line"] for row in pending[32:64]],
                            replay_lines=[row["line"] for row in pending[64:]],
                            point_line=line_no, point_observed_live=process.poll() is None))
                    pending = []
                elif event.get("event") == "case_end":
                    require(not pending, "Unaccounted phase rows")
                    active = None
            code = process.wait()
        text = (directory / "native.log").read_text()
        if error:
            require(code != 0 and error in text and not events and not phase_rows,
                    f"Expected rejection: {name}: {text[-2000:]}")
            require("Load Vector (" not in text, "Invalid phase config reached index loading")
        else:
            require(code == 0 and events[-1]["event"] == "batch_end", f"Native failure: {text[-2000:]}")
            require(len([e for e in events if e.get("event") == "point"]) == 4, "Missing points")
            require(len(phase_rows) == (384 if phase else 0), "Wrong phase-row total")
            if phase:
                require(any(w["point_observed_live"] for w in windows), "No live point visibility")
        runs[name] = dict(command=command, returncode=code, events=events, phase_rows=phase_rows,
                          attribution=windows, expected_error=error)
        return directory

    for dtype in ("Float", "UInt8"):
        ordinary = run(dtype + "-ordinary", args.ordinary, configs[dtype, False])
        default = run(dtype + "-default", args.client, configs[dtype, False])
        phase = run(dtype + "-phase", args.client, configs[dtype, True], phase=True)
        for candidate in (default, phase):
            for case in (1, 2):
                for probe in (16, 24):
                    for filename in ("ids.i32", "dist.f32", "work.u64"):
                        relative = Path(f"case{case}/nprobe_{probe}/{filename}")
                        require((ordinary / relative).read_bytes() == (candidate / relative).read_bytes(),
                                f"Phase/default payload mismatch: {candidate}/{relative}")
            comparisons.append(dict(reference=str(ordinary), candidate=str(candidate),
                                    exact_ids_distances_ssd_work=True))
    for name, setting, log in (("unlabelled", None, "true"), ("explicit-false", "false", "true"),
                               ("missing-log", "true", "false"), ("invalid-flag", "yes", "true")):
        config = read_ini(configs["UInt8", False][0])
        if setting is not None:
            config["Benchmark"]["PhaseTiming"] = setting
        config["SearchSSDIndex"]["LogPhaseTime"] = log
        path = output / (name + ".ini")
        write_ini(path, config)
        run("reject-" + name, args.client, [path],
            error="must be true or false" if name == "invalid-flag" else "LogPhaseTime must match")
    for reverse in (False, True):
        paths = [configs["UInt8", False][0], configs["UInt8", True][0]]
        run("reject-mixed-" + str(reverse), args.client, paths[::-1] if reverse else paths,
            error="Mixed batch PhaseTiming modes")
    for index in indexes:
        require(before[str(index)] == identities(index), "Existing small index changed")
    report = dict(passed=True, runs=runs, comparisons=comparisons,
        native_phase_rows=sum(len(r["phase_rows"]) for r in runs.values()),
        only_32_query_fixtures=True, existing_indexes_unchanged=True,
        scope="No 1B load, no performance claim; native timers unchanged",
        binary_sha256={str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (args.client, args.ordinary)})
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"PASS {len(runs)} processes, {len(comparisons)} exact comparisons, "
          f"{report['native_phase_rows']} native phase rows")


if __name__ == "__main__":
    main()
