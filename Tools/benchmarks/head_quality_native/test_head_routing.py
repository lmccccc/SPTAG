"""Exact-parent coverage fixtures; all vectors and CSRs are synthetic."""
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile


def main():
    binary = str(Path(sys.argv[1]).resolve())
    with tempfile.TemporaryDirectory(prefix="head-routing-fixtures-", dir=Path.cwd()) as temp:
        root = Path(temp)

        def vector(x):
            return struct.pack("<128f", x, *([0] * 127))

        def catalog(name, xs):
            path = root / name
            path.write_bytes(struct.pack("<ii", len(xs), 128) + b"".join(map(vector, xs)))
            return path

        def csr(name, rows, lower):
            offsets = [0]
            for row in rows:
                offsets.append(offsets[-1] + len(row))
            header = struct.pack("<Q8I2d6Q", 0x325253324E4E4153, 3, 104, lower,
                                 len(rows), 1, 4, 32, 0, 0.0, 1.0,
                                 lower, 0, 0, 0, 0, 0)
            path = root / name
            path.write_bytes(header + struct.pack(f"<{len(offsets)}Q", *offsets)
                             + struct.pack(f"<{lower}I", *(x for row in rows for x in row))
                             + bytes(32 * len(rows)))
            return path

        h1 = catalog("h1.bin", [0, 1, 2, 3])
        h2 = catalog("h2.bin", [0, 3])
        h3 = catalog("h3.bin", [0, 3])
        query = root / "query.fvecs"
        query.write_bytes(struct.pack("<i", 128) + vector(0))
        lo = csr("lower.csr", [[0, 2], [1, 3]], 4)
        up = csr("upper.csr", [[0], [1]], 2)
        flat, hierarchy = root / "flat.log", root / "h3.log"
        flat.write_text("DUMPHEADS q=0 bundle=0 twoLayer=0 n=2 : 0:0.000000 1:1.000000\n")
        hierarchy.write_text("DUMPHEADS q=0 bundle=1 twoLayer=1 n=2 : 0:0.000000 2:4.000000\n")
        text = (
            f"[Quality]\nHeadVectors={h1}\nQueries={query}\nQueryCount=1\nWarmup=0\n"
            f"Dim=128\nNprobe=2\nThreads=1\n[Logs]\nNames=flat,h3\nPaths={flat},{hierarchy}\n"
            f"[Hierarchy]\nCatalogs={h2},{h3}\nPostings={lo},{up}\nRoutingBeam=1\n"
        )
        config = root / "quality.ini"

        def run(text=text, ok=True):
            config.write_text(text)
            process = subprocess.run([binary, "--config", str(config)], cwd=root,
                                     capture_output=True, text=True, timeout=30)
            assert (process.returncode == 0) == ok, process.stdout + process.stderr
            if not ok:
                assert "HEAD_QUALITY_ERROR" in process.stderr
                assert "HEAD_ROUTING_SUMMARY" not in process.stdout
                return None
            return next(json.loads(s.split(" ", 1)[1]) for s in process.stdout.splitlines()
                        if s.startswith("HEAD_ROUTING_SUMMARY "))

        summary = run()
        assert summary["h1_exact_h2_saved_hits"] == 1
        assert summary["h1_exact_h2_knn_hits"] == 2
        assert summary["h1_knn_rescues"] == 1 and summary["h1_knn_regressions"] == 0
        assert summary["h1_exact_h3_saved_hits"] == 1
        assert summary["h2_exact_h3_saved_hits"] == summary["h2_exact_h3_knn_hits"] == 1
        assert summary["exact_top_replay_matches_h3"] == 1
        assert summary["exact_top_replay_matches_flat"] == 0
        assert summary["exact_top_replay_overlap_flat"] == 1
        for old, new in [("RoutingBeam=1", "RoutingBeam=0"),
                         ("RoutingBeam=1", "RoutingBeam=3"),
                         ("RoutingBeam=1", "RoutingBeam=1\nBad=1"),
                         (f"Catalogs={h2},{h3}", f"Catalogs={h2}"),
                         (f"Postings={lo},{up}", f"Postings={lo}")]:
            run(text.replace(old, new), False)
        original = lo.read_bytes()
        lo.write_bytes(original[:-1])
        run(ok=False)
        lo.write_bytes(original)
        csr("lower.csr", [[0, 0], [1, 3]], 4)
        run(ok=False)
        csr("lower.csr", [[], [0, 1, 2, 3]], 4)
        run(ok=False)
        csr("lower.csr", [[0], [1, 2, 3]], 4)
        run(ok=False)
    print("head_routing_fixtures: 10 executions passed")


if __name__ == "__main__":
    main()
