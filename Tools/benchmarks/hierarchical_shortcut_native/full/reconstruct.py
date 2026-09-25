#!/usr/bin/env python3
"""Reconstruct a new isolated frozen runtime, authenticating every native source."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

REVISION = "3552194536cb01dd70099e955a29e235a0cf4d2e"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reconstruct(repo, snapshot, output, cache):
    repo, snapshot, output = (p.resolve() for p in (repo, snapshot, output))
    if not output.is_relative_to(Path.cwd().resolve()):
        raise ValueError("Output must be below the current working directory")
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source"
    source.mkdir()
    archive = output / "revision.tar"
    with archive.open("wb") as stream:
        subprocess.run(["git", "-C", str(repo), "archive", REVISION],
                       stdout=stream, check=True)
    subprocess.run(["tar", "-xf", str(archive), "-C", str(source)], check=True)
    archive.unlink()
    subprocess.run(["git", "apply", "--exclude=Release/*", str(snapshot / "source.diff")],
                   cwd=source, check=True)
    manifest = json.loads((snapshot / "source.sha256.json").read_text())
    verified, origins = {}, {}
    for name, digest in manifest.items():
        if not name.startswith(("AnnService/", "Wrappers/", "ThirdParty/",
                                "Tools/benchmarks/SpannAclBench", "CMakeLists", "cmake/")):
            continue
        destination = source / name
        candidates = [destination, snapshot / "snapshot" / name, repo / name]
        if cache:
            candidates[1:1] = [cache / "h3_rng_source" / name, cache / "h3_phase_source" / name]
        origin = next((p for p in candidates if p.is_file() and sha(p) == digest), None)
        if origin is None:
            raise ValueError(f"Authenticated source unavailable: {name}")
        if origin != destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, destination)
        verified[name], origins[name] = digest, str(origin)
    vendor = repo / "ThirdParty/zstd"
    expected = subprocess.check_output(
        ["git", "-C", str(repo), "ls-tree", REVISION, "ThirdParty/zstd"], text=True).split()[2]
    actual = subprocess.check_output(["git", "-C", str(vendor), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(vendor), "status", "--porcelain"], text=True)
    if actual != expected or dirty:
        raise ValueError("zstd differs from the frozen submodule")
    shutil.copytree(vendor, source / "ThirdParty/zstd", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".git"))
    (output / "authentication.json").write_text(json.dumps({
        "revision": REVISION, "verified": verified, "origins": origins,
        "unavailable": [], "zstd_revision": actual,
        "source_diff_sha256": sha(snapshot / "source.diff"),
        "source_manifest_sha256": sha(snapshot / "source.sha256.json"),
    }, indent=2) + "\n")
    print(f"Authenticated {len(verified)} frozen sources; zstd {actual}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--authenticated-cache", type=Path)
    args = parser.parse_args()
    reconstruct(args.repo, args.snapshot, args.output, args.authenticated_cache)
