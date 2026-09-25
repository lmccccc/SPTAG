"""Synthetic native-file fixtures; no production vectors, indexes or searches.

JSON contract: QUERY rows use zero-based queryid and original dump_queryid.
Recall denominators are Nprobe even for underfill. Ranks are one-based
(distance, local ID); rank_min/rank_max include all exactly equal Float distances.
threshold_coverage counts selected distances <= the Lth distance, whereas
tie_adjusted_head_recall caps boundary credit at boundary_slots, preserving
penalties for missing strictly closer heads. Empty-set means/Jaccards are null.
SUMMARY distance/rank means are selected-head-weighted; same-count excess is
query-averaged over nonempty rows. Summaries appear only after every row validates.
"""
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys


def main():
    binary = str(Path(sys.argv[1]).resolve())
    root = Path.cwd() / f"head-quality-fixtures-{os.getpid()}"
    root.mkdir(exist_ok=False)
    checks = 0
    try:
        def vector(x):
            return struct.pack("<128f", x, *([0] * 127))

        head_data = struct.pack("<ii", 6, 128) + b"".join(
            vector(x) for x in [0, 1, -1, 2, -2, 3]
        )
        query_data = b"".join(struct.pack("<i", 128) + vector(x) for x in [0, .5])
        heads, queries = root / "heads.bin", root / "queries.fvecs"
        heads.write_bytes(head_data)
        queries.write_bytes(query_data)
        flat, h3 = root / "flat.log", root / "h3.log"
        flat_text = (
            "ordinary native logging\n"
            "[info] DUMPHEADS q=0 bundle=0 twoLayer=0 n=2 : 0:0.000000\n"
            "[info] DUMPHEADS q=1 bundle=0 twoLayer=0 n=2 : 0:0.000000 2:1.000000\n"
            "[info] DUMPHEADS q=2 bundle=0 twoLayer=0 n=2 :\n"
        )
        h3_text = (
            "DUMPHEADS q=0 bundle=1 twoLayer=1 n=2 : 0:0.000000\n"
            "DUMPHEADS q=1 bundle=1 twoLayer=1 n=2 : 1:1.000000 2:1.000000\n"
            "DUMPHEADS q=2 bundle=1 twoLayer=1 n=1 : 1:0.250000\n"
        )
        flat.write_text(flat_text)
        h3.write_text(h3_text)
        config_text = (
            f"[Quality]\nHeadVectors={heads}\nQueries={queries}\nQueryCount=2\n"
            f"Dim=128\nNprobe=2\nThreads=1\nWarmup=1\nMeasureOffset=0\n"
            f"[Logs]\nNames=flat,h3\nPaths={flat},{h3}\n"
        )
        config = root / "quality.ini"
        config.write_text(config_text)

        def run(ok=True, text=None, args=None):
            nonlocal checks
            config.write_text(config_text if text is None else text)
            result = subprocess.run(
                [binary] + (["--config", str(config)] if args is None else args),
                cwd=root, text=True, capture_output=True, timeout=30,
            )
            checks += 1
            if ok:
                assert result.returncode == 0, result.stderr + result.stdout
            else:
                assert result.returncode != 0, result.stdout
                assert "HEAD_QUALITY_ERROR" in result.stderr, result.stderr
                assert "HEAD_QUALITY_SUMMARY " not in result.stdout
            return [
                (line.split(" ", 1)[0], json.loads(line.split(" ", 1)[1]))
                for line in result.stdout.splitlines()
                if line.startswith(("HEAD_QUALITY_QUERY ", "HEAD_QUALITY_SUMMARY "))
            ]

        output = run()
        rows = {(x["name"], x["queryid"]): x for marker, x in output if marker.endswith("QUERY")}
        summaries = {x["name"]: x for marker, x in output if marker.endswith("SUMMARY")}
        assert len(rows) == 4 and len(summaries) == 2
        f, h = rows["flat", 0], rows["h3", 0]
        assert f["dump_queryid"] == 1
        assert f["exact_head_recall"] == .5 and f["tie_adjusted_head_recall"] == 1
        assert h["threshold_coverage"] == 1 and h["tie_adjusted_head_recall"] == .5
        assert h["mean_distance_excess_vs_same_count"] == .5
        assert f["boundary_tie_count"] == 2 and f["boundary_slots"] == 1
        assert f["selected"][1]["rank"] == 3
        assert (f["selected"][1]["rank_min"], f["selected"][1]["rank_max"]) == (2, 3)
        assert f["overlap"]["h3"]["intersection_count"] == 1
        assert math.isclose(f["overlap"]["h3"]["jaccard"], 1 / 3)
        assert rows["flat", 1]["selected_head_mean_distance"] is None
        assert rows["h3", 1]["count"] == 1 and rows["h3", 1]["exact_head_recall"] == .5
        assert summaries["flat"]["underfilled_queries"] == 1
        assert summaries["flat"]["empty_queries"] == 1
        assert summaries["h3"]["exact_head_recall"] == .5
        parallel = run(text=config_text.replace("Threads=1", "Threads=2"))
        assert [x for m, x in parallel if m.endswith("QUERY")] == [
            x for m, x in output if m.endswith("QUERY")
        ]
        run(text=config_text.replace("Threads=1\n", "").replace("MeasureOffset=0\n", ""))
        heads.write_bytes(struct.pack("<ii", 258, 128) + head_data[8:] + vector(100) * 252)
        maximum = run(text=config_text.replace("Nprobe=2", "Nprobe=256"))
        assert all(x["nprobe"] == 256 for _, x in maximum)
        heads.write_bytes(head_data)
        flat.write_text(flat_text.split("\n", 2)[2].replace("q=1", "q=0").replace("q=2", "q=1"))
        h3.write_text(h3_text.split("\n", 1)[1].replace("q=1", "q=0").replace("q=2", "q=1"))
        run(text=config_text.replace("Warmup=1", "Warmup=0"))
        flat.write_text(flat_text)
        h3.write_text(h3_text)
        for old, new in [
            ("Nprobe=2", "Nprobe=0"), ("Nprobe=2", "Nprobe=257"),
            ("Dim=128", "Dim=127"), ("Warmup=1", "Warmup=-1"),
            ("Threads=1", "Threads=0"), ("Threads=1", "Threads=bad"),
            ("MeasureOffset=0", "MeasureOffset=1"), ("QueryCount=2", "QueryCount=0"),
            ("Names=flat,h3", "Names=flat"), ("Names=flat,h3", "Names=flat,flat"),
            ("Names=flat,h3", "Names=flat,"), ("Names=flat,h3", 'Names=flat,h"3'),
            (str(h3), "relative.log"), ("Dim=128", "Dim=128\nUnknown=1"),
            ("[Logs]", "[Other]"), ("QueryCount=2", "QueryCount=3"),
        ]:
            run(False, config_text.replace(old, new))
        run(False, args=[])
        run(False, args=["--config", str(config), "--threads", "2"])
        bad_logs = [
            flat_text.replace("q=1", "q=0"),
            flat_text.replace("q=2", "q=3"),
            flat_text.rsplit("[info]", 1)[0],
            flat_text.split("\n", 2)[2],  # missing warmup
            flat_text.replace("0:0.000000 2:1.000000", "0:0.000000 0:0.000000"),
            flat_text.replace("2:1.000000", "6:1.000000"),
            flat_text.replace("2:1.000000", "-1:1.000000"),
            flat_text.replace("2:1.000000", "2:nan"),
            flat_text.replace("2:1.000000", "2:2.000000"),
            flat_text.replace("0:0.000000 2:1.000000", "2:1.000000 0:0.000000"),
            flat_text.replace("n=2", "n=3"),
            flat_text.replace("n=2", "n=1"),
            flat_text.replace("bundle=0", "bundle=2"),
            flat_text + "DUMPHEADS broken\n",
        ]
        for bad in bad_logs:
            flat.write_text(bad)
            run(False)
        flat.unlink()
        run(False)
        flat.write_text(flat_text)
        for bad in [
            head_data[:-1], head_data + b"x",
            struct.pack("<ii", 6, 127) + head_data[8:],
            struct.pack("<ii", 5, 128) + head_data[8:],
            struct.pack("<ii", 99, 128) + head_data[8:],
            head_data[:8] + vector(float("nan")) + head_data[520:],
        ]:
            heads.write_bytes(bad)
            run(False)
        heads.write_bytes(head_data)
        for bad in [
            query_data[:-1], query_data + b"x",
            struct.pack("<i", 127) + query_data[4:],
            query_data[:516] + struct.pack("<i", 127) + query_data[520:],
            struct.pack("<i", 128) + vector(float("inf")) + query_data[516:],
        ]:
            queries.write_bytes(bad)
            run(False)
        queries.write_bytes(query_data)
        # Six-decimal logging of a non-integral native distance must validate.
        queries.write_bytes(struct.pack("<i", 128) + vector(.0001) + query_data[516:])
        flat.write_text(flat_text.replace("0:0.000000 2:1.000000", "0:0.000000 2:1.000200"))
        h3.write_text(h3_text.replace("1:1.000000 2:1.000000", "1:0.999800 2:1.000200"))
        run()
        assert not list(root.glob("headquality-reader-*")), "Native staging leaked"
        print(f"head_quality_fixtures: {checks} executions passed")
    finally:
        shutil.rmtree(root)


if __name__ == "__main__":
    main()
