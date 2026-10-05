"""Real-index client regression: unchanged top10 and complete top100 payloads."""

import argparse
import configparser
import json
from pathlib import Path
import subprocess

import numpy as np


def check(reference, binary, fixture, output):
    output.mkdir(parents=True, exist_ok=False)
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config.read(fixture)
    config["Benchmark"]["MaxQueries"] = config["Benchmark"]["Warmup"] = "32"
    config["SearchSSDIndex"]["InternalResultNum"] = "192"
    config["SearchSweep"]["NProbe"] = "[192]"
    for name, executable, topk in (("reference", reference, 10), ("current", binary, 10),
                                   ("top100", binary, 100)):
        destination = output / name
        destination.mkdir()
        config["SearchSSDIndex"]["ResultNum"] = str(topk)
        path = destination / "native.ini"
        with path.open("x") as stream:
            config.write(stream, space_around_delimiters=False)
        with (destination / "native.log").open("x") as stream:
            subprocess.run([str(executable), str(path)], cwd=destination,
                           stdout=stream, stderr=subprocess.STDOUT, check=True)
        for filename, dtype in (("ids.i32", "<i4"), ("dist.f32", "<f4")):
            values = np.fromfile(destination / "nprobe_192" / filename, dtype=dtype)
            assert values.shape == (32 * topk,), (name, filename, values.shape)
    for filename in ("ids.i32", "dist.f32", "work.u64"):
        assert ((output / "reference/nprobe_192" / filename).read_bytes() ==
                (output / "current/nprobe_192" / filename).read_bytes()), filename
    rows = np.fromfile(output / "top100/nprobe_192/ids.i32", dtype="<i4").reshape(32, 100)
    distances = np.fromfile(output / "top100/nprobe_192/dist.f32", dtype="<f4").reshape(32, 100)
    assert np.all(rows >= 0) and all(len(set(row)) == 100 for row in rows)
    assert np.all(np.isfinite(distances)) and np.all(distances[:, 1:] >= distances[:, :-1])
    events = [json.loads(line) for line in (output / "top100/native.log").read_text().splitlines()
              if line.startswith('{"mode"')]
    assert len(events) == 1 and events[0]["topk"] == 100
    predicate, predicate_file = config["Benchmark"]["Predicate"], config["Benchmark"]["PredicateFile"]
    for policy in ("per_point", "once"):
        destination = output / ("batch_" + policy)
        destination.mkdir()
        batch = configparser.ConfigParser(interpolation=None)
        batch.optionxform = str
        batch["Batch"] = {"CaseCount": "3", "WarmupPolicy": policy}
        for case in range(3):
            config["SearchSweep"]["NProbe"] = "[100,192]"
            config["Benchmark"]["Predicate"] = "empty" if case == 1 else predicate
            config["Benchmark"]["PredicateFile"] = "" if case == 1 else predicate_file
            path = destination / f"case{case}.ini"
            with path.open("x") as stream:
                config.write(stream, space_around_delimiters=False)
            batch[f"Case{case + 1}"] = {"Config": str(path),
                                      "OutputDirectory": str(destination / f"case{case}")}
        path = destination / "batch.ini"
        with path.open("x") as stream:
            batch.write(stream, space_around_delimiters=False)
        with (destination / "native.log").open("x") as stream:
            subprocess.run([str(binary), str(path)], cwd=destination,
                           stdout=stream, stderr=subprocess.STDOUT, check=True)
        events = [json.loads(line) for line in (destination / "native.log").read_text().splitlines()
                  if line.startswith("{")]
        points = [event for event in events if event.get("event") == "point"]
        assert len(points) == 6
        assert all(point["warmup_queries"] == (0 if policy == "once" else 32) for point in points)
        assert events[-1]["completed_warmup_queries"] == (32 if policy == "once" else 192)
        warmups = [event for event in events if event["event"] == "batch_warmup_end"]
        assert len(warmups) == (1 if policy == "once" else 0)
        if warmups:
            assert warmups[0]["completed_queries"] == 32
    for case in range(3):
        for probe in (100, 192):
            for filename in ("ids.i32", "dist.f32", "work.u64"):
                relative = Path(f"case{case}/nprobe_{probe}") / filename
                assert ((output / "batch_once" / relative).read_bytes() ==
                        (output / "batch_per_point" / relative).read_bytes()), relative
    proof = dict(top10_ids_distances_work_identical=True, top100_shape=[32, 100],
                 top100_unique_valid_neighbors=True, replay_checked_by_native_client=True,
                 once_warmup_batch_verified=True, once_warmup_queries=32, per_point_warmup_queries=192,
                 batch_cases=3, probes_per_case=2, batch_result_and_work_parity=True)
    with (output / "completion.json").open("x") as stream:
        json.dump(proof, stream, indent=2)
    print(json.dumps(proof), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("reference", "binary", "fixture", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    arguments = parser.parse_args()
    check(**vars(arguments))
