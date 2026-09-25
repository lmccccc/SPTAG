#!/usr/bin/env python3
"""Bounded load-once client tests; existing indexes must have <=200,000 heads."""
import argparse
import configparser
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess

import numpy as np

from NativeBenchClientTest import identities, read_ini, require


def write_ini(path, config):
    with path.open("w") as stream:
        config.write(stream, space_around_delimiters=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("normal", "diagnostic", "fixtures", "uint8-index", "float-index", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir()
    before = {}
    for root in (args.uint8_index, args.float_index):
        with (root / "tenant_0/HeadIndex/vectors.bin").open("rb") as stream:
            rows, dimension = struct.unpack("<ii", stream.read(8))
        require(0 < rows <= 200000 and dimension == 128, "Only small immutable fixtures are allowed")
        before[str(root)] = identities(root)
    for name in ("uint8", "float", "mixed", "categorical"):
        shutil.copyfile(args.fixtures / (name + ".npy"), output / (name + ".npy"))
    probes = [16, 24, 48, 96, 192, 384]
    specs = [
        ("u2048", "empty", 2048, False, 0, 3),
        ("c2048", "categorical", 2048, False, 0, 3),
        ("d_extra", "dnf", 2048, True, 2048, 3),
        ("c4096", "categorical", 4096, False, 0, 15),
        ("c2048_again", "categorical", 2048, False, 0, 3),
        ("d_zero", "dnf", 2048, True, 0, 1),
        ("d_graph", "dnf", 2048, False, 0, 3),
        ("d_extra_again", "dnf", 2048, True, 2048, 3),
        ("u4096", "empty", 4096, False, 0, 15),
        ("u2048_again", "empty", 2048, False, 0, 3),
    ]
    configs = {}
    for name, predicate, cap, posting, extra, pages in specs:
        config = read_ini(args.fixtures / "uint8/native.ini")
        config["Benchmark"].update(Index=str(args.uint8_index.resolve()), ValueType="UInt8",
            Queries=str(output / "uint8.npy"), Predicate=predicate,
            PredicateFile=str(output / ("mixed.npy" if predicate == "dnf" else "categorical.npy"))
                if predicate != "empty" else "")
        config["SearchSSDIndex"].update(MaxCheck=str(cap), EnablePostingNavigation=str(posting).lower(),
            PostingAdditionalMaxCheck=str(extra), SearchPostingPageLimit=str(pages))
        config["SearchSweep"]["NProbe"] = json.dumps(probes)
        path = output / (name + ".ini")
        write_ini(path, config)
        configs[name] = path
    float_config = read_ini(configs["u2048"])
    float_config["Benchmark"].update(Index=str(args.float_index.resolve()), ValueType="Float",
        Queries=str(output / "float.npy"))
    write_ini(output / "float.ini", float_config)
    commands, runs, comparisons = [], {}, []

    def run(name, binary, ini, directory, error=None, on_event=None, partial=False, count=32):
        directory.mkdir()
        command = ["numactl", "--cpunodebind=0", "--membind=0", str(binary.resolve()), str(ini)]
        commands.append(command)
        events = []
        with (directory / "native.log").open("w") as log:
            process = subprocess.Popen(command, cwd=directory, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True)
            for line in process.stdout:
                log.write(line)
                if not (line.startswith('{"event":') or line.startswith('{"mode":')):
                    continue
                event = json.loads(line)
                event["observed_child_live"] = process.poll() is None
                events.append(event)
                if on_event:
                    on_event(event)
                if event.get("event") == "point" or "mode" in event:
                    folder = Path(event.get("output_directory", directory)) / f"nprobe_{event['nprobe']}"
                    for filename, size in (("ids.i32", count * 40), ("dist.f32", count * 40),
                                           ("work.u64", count * 64), ("latency_us.f64", count * 8)):
                        require((folder / filename).stat().st_size == size,
                                f"Point reported incomplete payload: {name}")
                    if event["diagnostic"]:
                        require((folder / "navigation.u64").stat().st_size == count * 57 * 8,
                                "Incomplete navigation payload")
                        for filename in ("graph_ids.i32", "graph_dist.f32"):
                            require((folder / filename).stat().st_size == count * event["nprobe"] * 4,
                                    "Incomplete graph payload")
            code = process.wait()
        text = (directory / "native.log").read_text()
        if error:
            require(code != 0 and error in text and (partial or not events),
                    f"Expected rejection: {name}: {text[-2000:]}")
            if not partial:
                require("Load Vector (" not in text, "Invalid batch reached index loading")
        else:
            require(code == 0, f"Native failure: {name}: {text[-2000:]}")
        runs[name] = dict(command=command, returncode=code, events=events, expected_error=error)
        return events

    def manifest(name, cases):
        config = configparser.ConfigParser(interpolation=None)
        config.optionxform = str
        config["Batch"] = dict(CaseCount=str(len(cases)))
        for i, (ini, directory) in enumerate(cases, 1):
            config[f"Case{i}"] = dict(Config=str(ini), OutputDirectory=str(directory))
        path = output / (name + ".ini")
        write_ini(path, config)
        return path

    def compare(left, right, diagnostic):
        memory_differences = []
        for probe in probes:
            a, b = (p / f"nprobe_{probe}" for p in (left, right))
            for filename in ("ids.i32", "dist.f32", "work.u64") + (
                    ("graph_ids.i32", "graph_dist.f32") if diagnostic else ()):
                require((a / filename).read_bytes() == (b / filename).read_bytes(),
                        f"Exact native payload mismatch: {left}/{right}/{probe}/{filename}")
            if diagnostic:
                x = np.fromfile(a / "navigation.u64", dtype="<u8").reshape(32, 57)
                y = np.fromfile(b / "navigation.u64", dtype="<u8").reshape(32, 57)
                differing = np.flatnonzero(np.any(x != y, axis=0)).tolist()
                # Allocation/initialized/cleared bytes are retained separately, not graph/SSD work.
                require(set(differing) <= {11, 14, 15, 16},
                        f"Graph work mismatch at {left}/{right}/{probe}: columns {differing}")
                if differing:
                    memory_differences.append(dict(nprobe=probe, columns=differing,
                        fresh_sum=[int(x[:, c].sum()) for c in differing],
                        batch_sum=[int(y[:, c].sum()) for c in differing]))
        comparisons.append(dict(left=str(left), right=str(right), exact_results_ssd_graph_work=True,
                                memory_counter_differences=memory_differences))

    for kind, binary in (("normal", args.normal), ("diagnostic", args.diagnostic)):
        root = output / kind
        root.mkdir()
        for name, *_ in specs:
            run(kind + "-fresh-" + name, binary, configs[name], root / ("fresh-" + name))
        schedule = [s[0] for s in specs] + [s[0] for s in reversed(specs)]
        cases = [(configs[name], root / f"case-{i:02d}-{name}") for i, name in enumerate(schedule, 1)]
        ini = manifest(kind + "-batch", cases)
        events = run(kind + "-batch", binary, ini, root / "batch-process")
        expected = ["batch_begin", "batch_loaded"]
        for _ in cases:
            expected += ["case_begin"] + ["point"] * len(probes) + ["case_end"]
        expected += ["batch_end"]
        require([e.get("event") for e in events] == expected, "Wrong durable event sequence")
        require(events[1]["index_load_count"] == events[1]["query_corpus_load_count"] == 1,
                "Batch did not report one manager/corpus load")
        require(any(e["observed_child_live"] for e in events if e["event"] == "point"),
                "No live point visibility")
        for i, ((ini, directory), name) in enumerate(zip(cases, schedule), 1):
            selected = [e for e in events if e.get("case_id") == f"Case{i}"]
            require(all(e["config"] == str(ini) and e["output_directory"] == str(directory)
                        for e in selected), "Case identity mismatch")
            points = [e for e in selected if e["event"] == "point"]
            require([e["nprobe"] for e in points] == probes, "Case probe order changed")
            require(all(e["warmup_queries"] == e["measured_queries"] == e["replay_queries"] == 32
                        for e in points), "Missing complete per-case windows")
            compare(root / ("fresh-" + name), directory, kind == "diagnostic")
        run(kind + "-fresh-float", binary, output / "float.ini", root / "fresh-float")
        float_cases = [(output / "float.ini", root / f"float-case-{i}") for i in (1, 2)]
        run(kind + "-batch-float", binary, manifest(kind + "-float-batch", float_cases),
            root / "float-batch-process")
        for _, directory in float_cases:
            compare(root / "fresh-float", directory, kind == "diagnostic")
    for name, *_ in specs:
        compare(output / "normal" / ("fresh-" + name), output / "diagnostic" / ("fresh-" + name), False)
    work_effects = {}
    for name in ("d_extra", "d_zero", "d_graph"):
        folder = output / "normal" / ("fresh-" + name) / "nprobe_384"
        work_effects[name] = [int(x) for x in
                              np.fromfile(folder / "work.u64", dtype="<u8").reshape(32, 8).sum(axis=0)]
    require(work_effects["d_zero"][0] == work_effects["d_graph"][0] and
            work_effects["d_zero"][5] < work_effects["d_graph"][5],
            "Fixture did not exercise changed page limits with the same selected posting count")
    require(work_effects["d_extra"][0] > work_effects["d_graph"][0],
            "Fixture did not exercise enabled additional posting work")

    long_config = read_ini(configs["d_extra"])
    long_config["SearchSweep"]["NProbe"] = "[16]"
    for name in ("uint8", "mixed"):
        source = np.load(output / (name + ".npy"))
        np.save(output / (name + "-1000.npy"), np.tile(source, (32, 1))[:1000])
    long_config["Benchmark"].update(Queries=str(output / "uint8-1000.npy"),
        PredicateFile=str(output / "mixed-1000.npy"), MaxQueries="1000", Warmup="1000")
    write_ini(output / "long.ini", long_config)
    long_cases = [(output / "long.ini", output / f"long-case-{i}") for i in (1, 2)]
    long_events = run("1000-window", args.normal, manifest("long-batch", long_cases),
                      output / "long-process", count=1000)
    long_points = [e for e in long_events if e.get("event") == "point"]
    require(len(long_points) == 2 and all(
        e["warmup_queries"] == e["measured_queries"] == e["replay_queries"] == 1000 for e in long_points),
        "Incomplete 1000-query windows")
    for filename in ("ids.i32", "dist.f32", "work.u64"):
        require((long_cases[0][1] / "nprobe_16" / filename).read_bytes() ==
                (long_cases[1][1] / "nprobe_16" / filename).read_bytes(), "Repeated 1000-window mismatch")
    late_cases = [(configs["c2048"], output / f"late-case-{i}") for i in (1, 2)]
    def collide(event):
        if event.get("event") == "batch_begin":
            late_cases[1][1].mkdir()
    late_events = run("late-collision", args.normal, manifest("late-batch", late_cases),
        output / "late-process", error="Batch output collision at case start", on_event=collide, partial=True)
    require([e["case_id"] for e in late_events if e.get("event") == "case_end"] == ["Case1"] and
            not any(e.get("event") == "batch_end" for e in late_events),
            "Partial batch was incorrectly marked complete")

    rejection_cases = []
    def reject(name, cases, message, mutate=None):
        ini = manifest("reject-" + name, cases)
        if mutate:
            config = read_ini(ini)
            mutate(config)
            write_ini(ini, config)
        run("reject-" + name, args.normal, ini, output / ("reject-" + name), message)
        rejection_cases.append(name)

    valid = configs["c2048"]
    target = output / "must-not-create"
    reject("duplicate-output", [(valid, target), (valid, target)], "directories overlap")
    reject("nested-output", [(valid, target), (valid, target / "nested")], "directories overlap")
    reject("existing-output", [(valid, output)], "already exists")
    reject("index-output", [(valid, args.uint8_index.resolve() / "must-not-create")],
           "inside the immutable index")
    reject("mixed-type", [(valid, target), (output / "float.ini", output / "must-not-create2")],
           "Mixed batch index, value type or query cohort")
    other_index = output / "other-index"
    (other_index / "tenant_0").mkdir(parents=True)
    shutil.copyfile(args.uint8_index / "tenant_0/indexloader.ini", other_index / "tenant_0/indexloader.ini")
    for name, section, key, value, message in (
        ("index", "Benchmark", "Index", str(other_index), "Mixed batch index"),
        ("cohort", "Benchmark", "Queries", str(output / "uint8-copy.npy"), "Mixed batch index"),
        ("window", "Benchmark", "MaxQueries", "1000", "Mixed batch index"),
        ("missing-setting", "SearchSSDIndex", "MaxDistRatio", None, "complete SearchSSDIndex"),
        ("hash-invariant", "SearchSSDIndex", "HashTableExponent", "5", "Batch invariant differs"),
        ("invalid-setting", "SearchSSDIndex", "MaxDistRatio", "garbage", "Invalid batch search setting"),
        ("invalid-bool", "SearchSSDIndex", "EnablePostingNavigation", "garbage", "Invalid batch search setting"),
        ("dtype", "Benchmark", "Queries", str(output / "float.npy"), "NPY type/order mismatch"),
    ):
        config = read_ini(valid)
        if value is None:
            del config[section][key]
        else:
            config[section][key] = value
        if name == "window":
            config["Benchmark"]["Warmup"] = "1000"
        if name == "cohort":
            shutil.copyfile(output / "uint8.npy", output / "uint8-copy.npy")
        path = output / ("invalid-" + name + ".ini")
        write_ini(path, config)
        cases = [(path, target)] if name == "dtype" else [(valid, target), (path, output / "must-not-create2")]
        reject(name, cases, message)
    reject("missing-case", [(valid, target)], "Incomplete batch registration",
           lambda c: c["Batch"].update(CaseCount="2"))
    reject("extra-case", [(valid, target)], "Unexpected or duplicate batch section",
           lambda c: c.add_section("Case2"))
    reject("unknown-key", [(valid, target)], "requires only Config and OutputDirectory",
           lambda c: c["Case1"].update(Unknown="1"))
    reject("bad-count", [(valid, target)], "CaseCount must be", lambda c: c["Batch"].update(CaseCount="1x"))
    reject("relative-output", [(valid, target)], "output paths must be absolute",
           lambda c: c["Case1"].update(OutputDirectory="relative"))
    reject("relative-config", [(valid, target)], "input paths must be absolute",
           lambda c: c["Case1"].update(Config="relative.ini"))
    require(not target.exists() and not (output / "must-not-create2").exists(), "Rejected batch wrote output")
    for root in (args.uint8_index, args.float_index):
        require(before[str(root)] == identities(root), "Existing index fixture changed")
    report = dict(passed=True, commands=commands, runs=runs, comparisons=comparisons,
                  work_effects=work_effects,
                  rejected=rejection_cases, existing_indexes_unchanged=True,
                  scope="32-query fixtures only; no billion-row load or performance campaign",
                  binary_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in (args.normal, args.diagnostic)})
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"PASS {len(runs)} native processes; {len(comparisons)} six-probe exact comparisons; "
          f"{len(rejection_cases)} pre-load rejections")


if __name__ == "__main__":
    main()
