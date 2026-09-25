"""Isolated native preparation for the checked-in PipeANN benchmark profile."""

from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import struct
import subprocess
import sys

from official_benchmark_config import (
    identity, read_json, reject_environment_overrides, require, sha256_file,
    verify_identities, write_json,
)


VERSION = 1
BLOCK = 4 * 1024 * 1024
SECTOR = 4096
TARGETS = {
    "search_disk_index": "tests/search_disk_index",
    "search_disk_index_filtered": "tests/search_disk_index_filtered",
    "build_memory_index": "tests/build_memory_index",
    "gen_random_slice": "tests/utils/gen_random_slice",
}
ALIASES = (
    "_disk.index", "_disk.index.tags", "_pq_compressed.bin", "_pq_pivots.bin",
    ".label.0", ".label.0.filter", ".label.1", ".label.1.quantize",
)
PROFILE = {
    "CMAKE_BUILD_TYPE": ("STRING", "Release"),
    "CMAKE_CXX_FLAGS": ("STRING", "-DREAD_ONLY_TESTS -DNO_MAPPING"),
    "IO_ENGINE": ("STRING", "uring"),
    "USE_TCMALLOC": ("BOOL", "ON"),
    "BUILD_PYTHON_INTERFACE": ("BOOL", "OFF"),
    "BUILD_MILVUS_SERVER": ("BOOL", "OFF"),
}
DEFINITIONS = {"READ_ONLY_TESTS", "NO_MAPPING", "USE_URING", "USE_TCMALLOC", "NDEBUG"}
SOURCE_PATHS = (
    "CMakeLists.txt", "cmake", "include", "src", "tests", "third_party/liburing",
    "README.md", "docs/cpp-interface.md", "scripts/tests-pipeann/fig13.sh",
)
GENERATED_URING = {
    "third_party/liburing/config-host.h", "third_party/liburing/config-host.mak",
    "third_party/liburing/src/include/liburing/compat.h",
    "third_party/liburing/src/include/liburing/io_uring_version.h",
}


def _environment():
    reject_environment_overrides()
    forbidden = {
        "CC", "CXX", "CFLAGS", "LDFLAGS", "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH",
        "LIBRARY_PATH", "LD_PRELOAD", "MAKEFLAGS", "MFLAGS", "CMAKE_ARGS",
        "CMAKE_TOOLCHAIN_FILE", "CMAKE_GENERATOR", "CMAKE_PREFIX_PATH",
    }.intersection(os.environ)
    require(not forbidden, "Remove native build overrides: " + ", ".join(sorted(forbidden)))


def _profile(config):
    path = config.path_value("PipeANN", "CMakeProfile")
    found = {}
    for line in path.read_text(encoding="ascii").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        require(line.startswith("set(") and line.endswith(")"),
                f"Only fixed initial-cache set() statements are allowed: {path}")
        words = shlex.split(line[4:-1])
        require(len(words) == 6 and words[2] == "CACHE" and words[5] == "FORCE",
                f"Not a fixed CMake cache assignment: {line}")
        key, value, _, kind, _, _ = words
        require(key not in found, f"Duplicate CMake profile key: {key}")
        found[key] = (kind, value)
    require(all(found.get(key) == value for key, value in PROFILE.items())
            and set(found) - PROFILE.keys() <= {"CMAKE_EXE_LINKER_FLAGS"},
            "CMake profile must retain Release/read-only/NO_MAPPING/uring/tcmalloc")
    if "CMAKE_EXE_LINKER_FLAGS" in found:
        kind, value = found["CMAKE_EXE_LINKER_FLAGS"]
        flags = shlex.split(value)
        require(kind == "STRING" and len(flags) == 2 and flags[0].startswith("-L")
                and Path(flags[0][2:]).is_absolute()
                and flags[1] == "-Wl,-rpath," + flags[0][2:],
                "Only one fixed, matching dependency link/RPATH directory is supported")
    return {**identity(path, hash_content=True), "cache": {key: value for key, (_, value) in found.items()}}


def _same_profile(recorded, current):
    return isinstance(recorded, dict) and all(
        recorded.get(key) == current[key] for key in ("bytes", "sha256", "cache"))


def _output(config, section, key):
    declared = Path(config.section(section)[key])
    if not declared.is_absolute():
        declared = config.path.parent / declared
    declared = Path(os.path.abspath(declared))
    require(declared == declared.resolve(), f"Output path must not traverse symlinks: {declared}")
    return declared


def _paths(config):
    tools = _output(config, "PipeANN", "ToolchainDirectory")
    prefix = _output(config, "PipeANN", "IndexPrefix")
    sample = _output(config, "MemoryIndex", "SamplePrefix")
    roots = (tools, prefix.parent, sample.parent)
    source = config.path_value("PipeANN", "SourceDirectory")
    original = config.path_value("PipeANN", "SourceIndexPrefix")
    protected = (source, original.parent)
    for root in roots:
        require(root.parent != root, f"Not a preparation directory: {root}")
        for other in protected:
            require(not root.is_relative_to(other) and not other.is_relative_to(root),
                    f"Preparation must not overlap native source or original index: {root}")
        for path in (config.path, config.path_value("Dataset", "VectorFile")):
            require(not path.is_relative_to(root), f"Preparation would contain a protected input: {root}")
    for index, root in enumerate(roots):
        for other in roots[index + 1:]:
            require(not root.is_relative_to(other) and not other.is_relative_to(root),
                    "Toolchain, alias and sample directories must be separate")
    return tools, prefix, sample, original


def _regular(path, owned=False):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), f"Missing or non-regular artifact: {path}")
    if owned:
        require(path.stat().st_nlink == 1, f"Refusing hard-linked writable artifact: {path}")
    return path


def _stamp(path):
    path = _regular(path)
    before = path.stat()
    with path.open("rb") as stream:
        header = stream.read(SECTOR)
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
            f"Input changed while inspecting it: {path}")
    return {
        **identity(path), "device": after.st_dev, "inode": after.st_ino,
        "ctime_ns": after.st_ctime_ns, "header_sha256": hashlib.sha256(header).hexdigest(),
    }


def _git(root, *args):
    return subprocess.check_output(
        ["git", "--no-optional-locks", "-C", str(root), *args], stderr=subprocess.PIPE,
    )


def _source_provenance(config):
    root = config.path_value("PipeANN", "SourceDirectory")
    require(root.is_dir(), f"Missing PipeANN source: {root}")
    require(Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel")).strip()).resolve() == root,
            "SourceDirectory must identify the PipeANN repository itself")
    revision = _git(root, "rev-parse", "HEAD").decode().strip()
    require(revision == config.section("PipeANN")["SourceRevision"], "PipeANN source revision changed")
    files, repositories = {}, {}

    def collect(repository, prefix, scopes):
        head = _git(repository, "rev-parse", "HEAD").decode().strip()
        repositories[prefix] = {
            "directory": str(repository), "revision": head,
            "status": os.fsdecode(_git(repository, "status", "--porcelain=v1", "--untracked-files=all",
                                     "--", *scopes)),
            "diff_sha256": hashlib.sha256(_git(
                repository, "diff", "--no-ext-diff", "--no-textconv", "--binary", "HEAD", "--", *scopes,
            )).hexdigest(),
        }
        entries = {}
        for entry in _git(repository, "ls-files", "--stage", "-z", "--", *scopes).split(b"\0"):
            if not entry:
                continue
            metadata, name = entry.split(b"\t", 1)
            mode, blob, stage = metadata.decode().split()
            require(stage == "0", "Cannot freeze unmerged native source")
            entries[os.fsdecode(name)] = (mode, blob)
        for name in _git(repository, "ls-files", "--others", "--exclude-standard", "-z",
                         "--", *scopes).split(b"\0"):
            if name:
                relative = os.fsdecode(name)
                if Path(relative).suffix in {".c", ".cc", ".cpp", ".h", ".hh", ".hpp", ".cmake", ".in"}:
                    entries[relative] = ("untracked", None)
        for relative, (mode, blob) in sorted(entries.items()):
            name = (Path(prefix) / relative).as_posix()
            path = repository / relative
            require(not Path(relative).is_absolute() and ".." not in Path(relative).parts,
                    f"Unsafe native source path: {relative}")
            if mode == "160000":
                require(path.is_dir(), f"Missing source submodule: {path}")
                collect(path.resolve(), name, ())
                repositories[name]["gitlink"] = blob
                continue
            if name in GENERATED_URING or any(part in {
                ".git", "build", "data", "indices", "__pycache__",
            } for part in Path(relative).parts) or re.search(r"\.(a|o|ol|os|so(\.\d+)*|bin|pyc)$", name):
                require(mode == "untracked", f"Build/data output is tracked as a native input: {name}")
                continue
            if not os.path.lexists(path):
                require(mode != "untracked", f"Native input disappeared: {path}")
                files[name] = {"origin": str(path), "git_blob": blob, "deleted": True}
                continue
            resolved = path.resolve(strict=True)
            require(resolved.is_relative_to(repository), f"Native source symlink escapes its repository: {path}")
            require(resolved.is_file() and resolved.stat().st_size <= 32 * 1024 * 1024,
                    f"Refusing non-source or oversized snapshot input: {path}")
            before = _stamp(resolved)
            digest = sha256_file(resolved)
            require(before == _stamp(resolved), f"Native source changed while freezing: {path}")
            record = {
                "origin": str(path), "git_blob": blob, "sha256": digest, "bytes": before["bytes"],
                "executable": bool(resolved.stat().st_mode & stat.S_IXUSR),
            }
            if path.is_symlink():
                record["symlink"] = os.readlink(path)
            files[name] = record

    collect(root, "", SOURCE_PATHS)
    require(sum(record.get("bytes", 0) for record in files.values()) <= 256 * 1024 * 1024,
            "Native source snapshot unexpectedly exceeds 256 MiB; refusing to copy possible datasets")
    for name in ("CMakeLists.txt", "src/CMakeLists.txt", "tests/CMakeLists.txt",
                 "tests/utils/CMakeLists.txt", "third_party/liburing/configure",
                 "third_party/liburing/Makefile", "third_party/liburing/src/Makefile"):
        require(name in files and not files[name].get("deleted"), f"Missing native build input: {name}")
    return {"directory": str(root), "revision": revision, "repositories": repositories, "files": files}


def _freeze_source(config, root, provenance):
    snapshot = root / "source"
    snapshot.mkdir()
    for name, record in provenance["files"].items():
        if record.get("deleted"):
            continue
        destination = snapshot / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(record["origin"], destination, follow_symlinks=True)
        require(sha256_file(destination) == record["sha256"], f"Source changed during snapshot: {name}")
    require(_source_provenance(config) == provenance, "Native source changed during snapshot")
    write_json(root / "source-manifest.json", provenance)
    return snapshot


def _verify_snapshot(root, provenance):
    for name, record in provenance["files"].items():
        path = root / "source" / name
        require(path.is_relative_to(root / "source") and ".." not in Path(name).parts
                and path.resolve() == path,
                "Invalid snapshot path in manifest")
        if record.get("deleted"):
            require(not os.path.lexists(path), f"Deleted source reappeared in snapshot: {path}")
        else:
            _regular(path, owned=True)
            require(path.stat().st_size == record["bytes"] and sha256_file(path) == record["sha256"]
                    and bool(path.stat().st_mode & stat.S_IXUSR) == record["executable"],
                    f"Frozen native source changed: {path}")


def _tree_inventory(root):
    items = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if name == "build-state.json":
            continue
        if path.is_symlink():
            require(path.resolve().is_relative_to(root), f"Isolated build link escapes output tree: {path}")
            items[name] = {"symlink": os.readlink(path)}
        elif path.is_dir():
            items[name] = {"directory": True}
        else:
            _regular(path, owned=True)
            items[name] = identity(path, hash_content=True)
    return items


def _run(argv, cwd, log):
    print(f"Native preparation: {shlex.join(argv)}\nLog: {log}", flush=True)
    with Path(log).open("x", encoding="utf-8") as stream:
        subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=stream,
                       stderr=subprocess.STDOUT, check=True)


def _cache(path):
    result = {}
    for line in _regular(path).read_text().splitlines():
        match = re.fullmatch(r"([^/#][^:]*):[^=]+=(.*)", line)
        if match:
            require(match[1] not in result, f"Duplicate CMake cache key: {match[1]}")
            result[match[1]] = match[2]
    return result


def _definitions(words):
    result = {}
    index = 0
    while index < len(words):
        word = words[index]
        if word in ("-D", "-U"):
            index += 1
            require(index < len(words), "Incomplete compiler definition")
            word += words[index]
        if word.startswith("-U"):
            require(word[2:] not in DEFINITIONS, f"Required native definition is undefined: {word}")
        elif word.startswith("-D"):
            key, _, value = word[2:].partition("=")
            require(key not in DEFINITIONS or value in ("", "1"), f"Disabled native definition: {word}")
            result[key] = value or "1"
        index += 1
    require(DEFINITIONS <= result.keys(), "Native library/target is missing read-only/NO_MAPPING/uring/tcmalloc flags")
    require(not {"USE_AIO", "USE_SPDK"}.intersection(result), "Non-uring native compile definitions")
    return result


def _effective_profile(root, profile):
    build, snapshot = root / "build", root / "source"
    cache = _cache(build / "CMakeCache.txt")
    for key, value in profile["cache"].items():
        require(cache.get(key) == value, f"Effective CMake {key} is not {value}; no backend/profile fallback")
    require(cache.get("PIPEANN_IO_URING_COMPILE_RESULT") == "TRUE"
            and cache.get("PIPEANN_IO_URING_RUN_RESULT") == "0",
            "The native io_uring/SQPOLL compile-and-run probe did not succeed")
    require(cache.get("CMAKE_HOME_DIRECTORY") == str(snapshot)
            and cache.get("CMAKE_CACHEFILE_DIR") == str(build)
            and cache.get("CMAKE_GENERATOR") == "Unix Makefiles", "CMake is not using the isolated source/build tree")
    database = read_json(build / "compile_commands.json")
    require(isinstance(database, list) and database, "Missing native compilation evidence")
    targets = {name: [] for name in ("pipeann", *TARGETS)}
    for entry in database:
        words = entry.get("arguments") or shlex.split(entry["command"])
        require("-o" in words, "Missing native object output")
        output = words[words.index("-o") + 1]
        match = re.search(r"(?:^|/)CMakeFiles/([^/]+)\.dir/", output)
        if not match or match[1] not in targets:
            continue
        directory = Path(entry["directory"]).resolve()
        source = Path(entry["file"])
        source = (source if source.is_absolute() else directory / source).resolve()
        require(directory.is_relative_to(build) and source.is_relative_to(snapshot)
                and (directory / output).resolve().is_relative_to(build),
                "Native compilation refers to a foreign source or object tree")
        require("-c" in words and (directory / words[words.index("-c") + 1]).resolve() == source,
                "Native compile command does not compile its recorded source")
        targets[match[1]].append({
            "source": str(source), "definitions": _definitions(words),
            "command_sha256": hashlib.sha256(json.dumps(words).encode()).hexdigest(),
        })
    evidence = {}
    for name, entries in targets.items():
        expected = ({str(path) for pattern in ("*.cpp", "search/*.cpp", "update/*.cpp", "utils/*.cpp")
                     for path in (snapshot / "src").glob(pattern)} if name == "pipeann" else
                    {str(snapshot / (TARGETS[name] + ".cpp"))})
        require(expected and {entry["source"] for entry in entries} == expected
                and len(entries) == len(expected), f"Missing/ambiguous native compilation for {name}")
        directory = build / ("src" if name == "pipeann" else str(Path(TARGETS[name]).parent))
        flags = directory / "CMakeFiles" / (name + ".dir") / "flags.make"
        values = {}
        for line in _regular(flags).read_text().splitlines():
            if " = " in line:
                key, value = line.split(" = ", 1)
                values[key] = value
        _definitions(shlex.split(values.get("CXX_DEFINES", "") + " " + values.get("CXX_FLAGS", "")))
        link = flags.with_name("link.txt")
        if name != "pipeann":
            words = shlex.split(_regular(link).read_text())
            require(all(flag in words for flag in shlex.split(profile["cache"].get("CMAKE_EXE_LINKER_FLAGS", ""))),
                    f"{name} does not use the fixed dependency link/RPATH directory")
            require("-luring" in words and "-ltcmalloc" in words and "-laio" not in words,
                    f"{name} must link uring and tcmalloc, never aio")
            require("-L" + str(snapshot / "third_party/liburing/src") in words,
                    f"{name} does not link the isolated liburing")
            require(any((directory / word).resolve() == build / "src/libpipeann.a"
                        for word in words if word.endswith("libpipeann.a")),
                    f"{name} does not link the rebuilt native library")
            allocator = words.index("-ltcmalloc")
            require("-Wl,--no-as-needed" in words[:allocator]
                    and "-Wl,--as-needed" in words[allocator + 1:], f"{name} can discard tcmalloc")
        evidence[name] = {
            "compile_units": sorted(entries, key=lambda entry: entry["source"]),
            "flags": identity(flags, hash_content=True), "link": identity(link, hash_content=True),
        }
    return {
        "definitions": sorted(DEFINITIONS), "backend": "uring", "allocator": "tcmalloc",
        "cache": {key: cache[key] for key in (*profile["cache"], "PIPEANN_IO_URING_COMPILE_RESULT",
                                           "PIPEANN_IO_URING_RUN_RESULT", "CMAKE_GENERATOR",
                                           "CMAKE_HOME_DIRECTORY", "CMAKE_CACHEFILE_DIR")},
        "compiler": identity(cache["CMAKE_CXX_COMPILER"], hash_content=True),
        "compile_commands": identity(build / "compile_commands.json", hash_content=True),
        "cmake_cache": identity(build / "CMakeCache.txt", hash_content=True),
        "targets": evidence,
    }


def _linkage(root, binaries):
    dependencies, targets = {}, {}
    for name, item in binaries.items():
        binary = item["path"]
        dynamic = subprocess.check_output(["readelf", "-d", binary], text=True, stderr=subprocess.STDOUT)
        needed = re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", dynamic)
        require(any(re.fullmatch(r"libtcmalloc\.so(?:\.\d+)*", library) for library in needed),
                f"{name} does not actually use the required tcmalloc allocator")
        require(not any(library.startswith("libaio") for library in needed), f"{name} links the aio backend")
        output = subprocess.check_output(["ldd", binary], text=True, stderr=subprocess.STDOUT)
        require("not found" not in output, f"Unresolved runtime dependency for {name}:\n{output}")
        resolved = {}
        for line in output.splitlines():
            match = re.match(r"\s*(\S+)\s+=>\s+(/\S+)\s+\(", line)
            if match:
                library, path = match[1], Path(match[2]).resolve()
            else:
                direct = re.match(r"\s*(/\S+)\s+\(", line)
                if not direct:
                    continue
                # ldd lists the ELF loader directly, without the "name =>" prefix.
                path = Path(direct[1])
                library, path = path.name, path.resolve()
            if library.startswith("liburing"):
                require(path.is_relative_to(root / "source/third_party/liburing/src"),
                        f"{name} resolves a foreign liburing: {path}")
            require(not library.startswith("libaio"), f"{name} resolves an aio runtime dependency")
            resolved[library] = str(path)
            if str(path) not in dependencies:
                dependencies[str(path)] = identity(path, hash_content=True)
        require(set(needed) <= resolved.keys(), f"Incomplete runtime dependency evidence for {name}")
        targets[name] = {"needed": needed, "resolved": resolved}
    return {"targets": targets, "files": list(dependencies.values())}


def _tool_artifacts(root):
    binaries = {}
    for name, relative in TARGETS.items():
        path = _regular(root / "build" / relative, owned=True)
        require(os.access(path, os.X_OK), f"Native tool is not executable: {path}")
        with path.open("rb") as stream:
            require(stream.read(4) == b"\x7fELF", f"Not a native ELF executable: {path}")
        binaries[name] = identity(path, hash_content=True)
    libraries = {}
    for name, relative in {
        "pipeann": "build/src/libpipeann.a",
        "uring": "source/third_party/liburing/src/liburing.a",
        "uring_compat_header": "source/third_party/liburing/src/include/liburing/compat.h",
        "uring_version_header": "source/third_party/liburing/src/include/liburing/io_uring_version.h",
    }.items():
        path = _regular(root / relative, owned=True)
        require(path.resolve() == path, f"Native artifact traverses a symlink: {path}")
        if name in ("pipeann", "uring"):
            with path.open("rb") as stream:
                require(stream.read(8) == b"!<arch>\n", f"Not a newly built native archive: {path}")
        libraries[name] = identity(path, hash_content=True)
    return binaries, libraries


def build_tools(config):
    """Build the fixed profile; only an authenticated failed attempt may resume."""
    _environment()
    profile = _profile(config)
    root, _, _, _ = _paths(config)
    if os.path.lexists(root / "toolchain.json"):
        return validate_tools(config)
    provenance = _source_provenance(config)
    digest = config.digest()
    if os.path.lexists(root):
        require(root.is_dir() and (root / "build-state.json").is_file(),
                f"Refusing foreign/partial toolchain directory: {root}")
        state = read_json(_regular(root / "build-state.json", owned=True))
        require(isinstance(state, dict) and state.get("kind") == "pipeann-official-build"
                and state.get("version") == VERSION
                and state.get("status") == "failed" and state.get("config_sha256") == digest
                and _same_profile(state.get("profile"), profile),
                "Cannot resume an incompatible or incomplete native build")
        require(read_json(root / "source-manifest.json") == provenance, "Native source changed since failed build")
        _verify_snapshot(root, provenance)
        require(state.get("inventory") == _tree_inventory(root),
                "Failed native build outputs changed; refusing ambiguous reuse")
    else:
        root.mkdir(parents=True, mode=0o700)
        _freeze_source(config, root, provenance)
        (root / "logs").mkdir()
        state = {"kind": "pipeann-official-build", "version": VERSION, "config_sha256": digest,
                 "profile": profile, "attempt": 0, "commands": []}
    state.update(status="building", attempt=state["attempt"] + 1)
    state.pop("inventory", None)
    write_json(root / "build-state.json", state)
    snapshot, build = root / "source", root / "build"
    uring = snapshot / "third_party/liburing"

    def execute(phase, argv, cwd):
        state["phase"] = phase
        log = root / "logs" / f"{state['attempt']:02d}-{phase}.log"
        command = config.affinity("Build") + [str(value) for value in argv]
        state["commands"].append({"argv": command, "cwd": str(cwd), "log": str(log)})
        write_json(root / "build-state.json", state)
        _run(command, cwd, log)

    try:
        execute("liburing-configure", ["./configure"], uring)
        execute("liburing-build", ["make", "-j", config.section("Build")["Jobs"], "library"], uring)
        for name in ("compat.h", "io_uring_version.h"):
            _regular(uring / "src/include/liburing" / name, owned=True)
        _regular(uring / "src/liburing.a", owned=True)
        execute("cmake-configure", ["cmake", "-C", profile["path"], "-S", snapshot,
                                   "-B", build, "-G", "Unix Makefiles"], root)
        _effective_profile(root, profile)
        execute("native-build", ["cmake", "--build", build, "--parallel", config.section("Build")["Jobs"],
                                 "--target", "pipeann", *TARGETS], root)
        effective = _effective_profile(root, profile)
        binaries, libraries = _tool_artifacts(root)
        linkage = _linkage(root, binaries)
        _verify_snapshot(root, provenance)
        require(_source_provenance(config) == provenance, "Native source changed during the isolated build")
        require(config.digest() == digest and _profile(config) == profile, "Fixed profile changed during build")
    except (subprocess.CalledProcessError, OSError, ValueError) as error:
        state.update(status="failed", error=str(error), inventory=_tree_inventory(root))
        write_json(root / "build-state.json", state)
        log = state["commands"][-1]["log"] if state["commands"] else str(root)
        raise RuntimeError(f"Fixed native build failed ({state.get('phase')}): {error}. Log: {log}. "
                           "No flags/backend/allocator were relaxed; the isolated failed tree is preserved.") from error
    state["status"] = "complete"
    state.pop("error", None)
    write_json(root / "build-state.json", state)
    manifest = {
        "kind": "pipeann-official-toolchain", "version": VERSION, "status": "complete",
        "config_sha256": digest, "profile": profile, "source": provenance,
        "source_manifest": identity(root / "source-manifest.json", hash_content=True),
        "build_state": identity(root / "build-state.json", hash_content=True),
        "effective": effective, "binaries": binaries, "libraries": libraries,
        "linkage": linkage, "commands": state["commands"],
    }
    write_json(root / "toolchain.json", manifest)
    return manifest


def validate_tools(config):
    """Strict readiness: never configures, builds, replaces, or falls back."""
    _environment()
    profile = _profile(config)
    root, _, _, _ = _paths(config)
    path = root / "toolchain.json"
    require(path.is_file(), f"Missing completed native toolchain manifest: {path}; run build_tools explicitly")
    manifest = read_json(_regular(path, owned=True))
    require(isinstance(manifest, dict) and manifest.get("kind") == "pipeann-official-toolchain"
            and manifest.get("version") == VERSION
            and manifest.get("status") == "complete" and manifest.get("config_sha256") == config.digest()
            and _same_profile(manifest.get("profile"), profile),
            "Incompatible or partial native toolchain manifest")
    require({path.name for path in root.iterdir()} ==
            {"source", "build", "logs", "source-manifest.json", "build-state.json", "toolchain.json"},
            "Foreign output in native toolchain directory")
    provenance = _source_provenance(config)
    require(manifest.get("source") == provenance, "Native source/dirty-content provenance changed")
    _verify_snapshot(root, provenance)
    require(manifest.get("source_manifest") == identity(root / "source-manifest.json", hash_content=True)
            and manifest.get("build_state") == identity(root / "build-state.json", hash_content=True),
            "Native build/snapshot manifest identities changed")
    require(read_json(root / "source-manifest.json") == provenance, "Snapshot manifest does not match its source")
    binaries, libraries = _tool_artifacts(root)
    require(manifest.get("binaries") == binaries and manifest.get("libraries") == libraries,
            "Native binary/library hashes changed; renamed or legacy binaries cannot be reused")
    require(manifest.get("effective") == _effective_profile(root, profile), "Effective native compilation evidence changed")
    require(manifest.get("linkage") == _linkage(root, binaries), "Native allocator/backend runtime linkage changed")
    return manifest


def _read_exact(stream, size):
    value = stream.read(size)
    require(len(value) == size, f"Truncated native file: {stream.name}")
    return value


def _bin(path, width, count=None, dimension=None):
    path = _regular(path)
    with path.open("rb") as stream:
        rows, columns = struct.unpack("<II", _read_exact(stream, 8))
    require(rows > 0 and columns > 0 and path.stat().st_size == 8 + rows * columns * width,
            f"Invalid native bin header/payload size: {path}")
    require(count is None or rows == count, f"Native row count mismatch: {path}")
    require(dimension is None or columns == dimension, f"Native dimension mismatch: {path}")
    return rows, columns


def _ssd_header(path, count, dimension, memory=False):
    path = _regular(path, owned=memory)
    with path.open("rb") as stream:
        values = struct.unpack("<II9Q", _read_exact(stream, 80))
    nr, nc, rows, dim, entry, node_bytes, per_sector, shard_rows, attr_bytes, degree, ood = values
    require(nr == 9 and nc == 1 and rows == count and dim == dimension and entry < rows
            and shard_rows == rows, f"Not the matching current native SSD-format index: {path}")
    require(node_bytes >= dim + attr_bytes + 4 and (node_bytes - dim - attr_bytes) % 4 == 0
            and per_sector == SECTOR // node_bytes and 0 < degree <= (node_bytes - dim - attr_bytes) // 4 - 1
            and ood <= degree, f"Invalid native graph geometry: {path}")
    groups = (rows + per_sector - 1) // per_sector if per_sector else rows
    group_bytes = SECTOR if per_sector else ((node_bytes + SECTOR - 1) // SECTOR) * SECTOR
    require(path.stat().st_size == SECTOR + groups * group_bytes, f"Partial native graph: {path}")
    if memory:
        require(degree == 32 and node_bytes == dim + 33 * 4 and attr_bytes == 0 and ood == 0,
                "Memory entry is not the native R32 SSD layout; old in-memory formats are not accepted")
    return {"rows": rows, "dimension": dim, "entry": entry, "node_bytes": node_bytes,
            "per_sector": per_sector, "degree": degree, "group_bytes": group_bytes}


def _attributes(prefix, count):
    label = Path(str(prefix) + ".label.0")
    with label.open("rb") as stream:
        labels, = struct.unpack("<Q", _read_exact(stream, 8))
        require(0 < labels <= min(1000000, (label.stat().st_size - 8) // 16), "Invalid label directory")
        previous = ((8 + labels * 16 + SECTOR - 1) // SECTOR) * SECTOR
        members = 0
        for _ in range(labels):
            start, end = struct.unpack("<QQ", _read_exact(stream, 16))
            require(start == previous and start <= end <= label.stat().st_size and (end - start) % 4 == 0,
                    "Partial or incompatible native label index")
            members += (end - start) // 4
            previous = ((end + SECTOR - 1) // SECTOR) * SECTOR
        require(members == count, "The fixed source must have one categorical label per original VID")
    _bin(Path(str(prefix) + ".label.0.filter"), 1, count, 4)
    numeric = Path(str(prefix) + ".label.1")
    require(numeric.stat().st_size == ((count * 4 + SECTOR - 1) // SECTOR) * SECTOR + count * 4,
            "Partial native numeric attribute index")
    quantized = Path(str(prefix) + ".label.1.quantize")
    with quantized.open("rb") as stream:
        buckets, = struct.unpack("<I", _read_exact(stream, 4))
        require(buckets == 256, "Invalid native numeric bucket header")
        boundaries = struct.unpack("<257I", _read_exact(stream, 257 * 4))
        rows, = struct.unpack("<I", _read_exact(stream, 4))
    require(rows == count and quantized.stat().st_size == 4 + 257 * 4 + 4 + count
            and all(left <= right for left, right in zip(boundaries, boundaries[1:])),
            "Partial or incompatible numeric bucket file")


def _memory_recipe(config):
    dataset, memory, pipeann = config.section("Dataset"), config.section("MemoryIndex"), config.section("PipeANN")
    require(dataset["ValueType"] == "UInt8" and dataset["Metric"] == "L2"
            and 0 < dataset.getint("VectorCount") < 2**32 and dataset.getint("Dimension") == 128,
            "Native memory preparation requires the configured UInt8/L2 source and original uint32 VIDs")
    require(memory.getfloat("SamplingRate") == 0.01 and memory.getint("R") == 32
            and memory.getint("L") == 64 and memory.getfloat("Alpha") == 1.2,
            "Memory preparation requires native 1% / R32 / L64 / alpha1.2")
    require(pipeann.getint("UnfilteredMemoryL") == 10 and pipeann.getint("FilteredMemoryL") == 0,
            "Missing unfiltered memory entries must not fall back to mem_L=0")
    require(config.section("Build").getint("Threads") > 0, "Invalid native memory build thread count")


def _original_inputs(config, prefix):
    count, dim = config.section("Dataset").getint("VectorCount"), config.section("Dataset").getint("Dimension")
    vector = config.path_value("Dataset", "VectorFile")
    _bin(vector, 1, count, dim)
    files = {suffix: _stamp(Path(str(prefix) + suffix)) for suffix in ALIASES}
    _ssd_header(Path(str(prefix) + "_disk.index"), count, dim)
    _bin(Path(str(prefix) + "_disk.index.tags"), 4, count, 1)
    _bin(Path(str(prefix) + "_pq_compressed.bin"), 1, count, 32)
    require(files["_pq_pivots.bin"]["bytes"] > 8, "Missing native PQ pivots")
    _attributes(prefix, count)
    return {"vector": _stamp(vector), "prefix": str(prefix), "files": files}


def _identity_vids(path, count):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        digest.update(_read_exact(stream, 8))
        for offset in range(0, count, BLOCK // 4):
            length = min(BLOCK // 4, count - offset)
            block = _read_exact(stream, length * 4)
            expected = array("I", range(offset, offset + length))
            if sys.byteorder != "little":
                expected.byteswap()
            require(block == expected.tobytes(), "NO_MAPPING requires the original full VID map to be identity")
            digest.update(block)
    return {"kind": "identity-uint32", "rows_checked": count, "sha256": digest.hexdigest()}


def _memory_commands(config, tools, prefix, sample):
    memory = config.section("MemoryIndex")
    return [
        config.affinity("Build") + [
            tools["binaries"]["gen_random_slice"]["path"], "uint8",
            str(config.path_value("Dataset", "VectorFile")), str(sample), memory["SamplingRate"],
        ],
        config.affinity("Build") + [
            tools["binaries"]["build_memory_index"]["path"], "uint8",
            str(sample) + "_data.bin", str(sample) + "_ids.bin", str(prefix) + "_mem.index",
            memory["R"], memory["L"], memory["Alpha"], config.section("Build")["Threads"], "l2",
        ],
    ]


def _sample_headers(config, sample):
    total = config.section("Dataset").getint("VectorCount")
    dim = config.section("Dataset").getint("Dimension")
    count, _ = _bin(Path(str(sample) + "_data.bin"), 1, dimension=dim)
    _bin(Path(str(sample) + "_ids.bin"), 4, count, 1)
    mean = total * 0.01
    require(count <= total and abs(count - mean) <= max(1, 12 * math.sqrt(mean * 0.99)),
            "Native sample count is implausible for the configured 1% probability (not an exact-size sample)")
    return count, dim


def _validate_sample(config, sample):
    count, dim = _sample_headers(config, sample)
    data_path, ids_path = Path(str(sample) + "_data.bin"), Path(str(sample) + "_ids.bin")
    _regular(data_path, owned=True)
    _regular(ids_path, owned=True)
    data_digest, ids_digest = hashlib.sha256(), hashlib.sha256()
    total = config.section("Dataset").getint("VectorCount")
    previous = -1
    with data_path.open("rb") as data, ids_path.open("rb") as ids, config.path_value(
            "Dataset", "VectorFile").open("rb") as source:
        data_digest.update(_read_exact(data, 8))
        ids_digest.update(_read_exact(ids, 8))
        for offset in range(0, count, BLOCK // dim):
            length = min(BLOCK // dim, count - offset)
            raw_ids, vectors = _read_exact(ids, length * 4), _read_exact(data, length * dim)
            data_digest.update(vectors)
            ids_digest.update(raw_ids)
            values = array("I")
            values.frombytes(raw_ids)
            if sys.byteorder != "little":
                values.byteswap()
            window_start, window = -1, b""
            for index, vid in enumerate(values):
                require(previous < vid < total, "Native sample VIDs must be unique, increasing and in full-source bounds")
                previous = vid
                position = 8 + vid * dim
                if not window_start <= position or position + dim > window_start + len(window):
                    window_start = position
                    source.seek(position)
                    # Read bounded source windows, never mmap/copy the 128 GB corpus.
                    window = _read_exact(source, min(BLOCK, 8 + total * dim - position))
                local = position - window_start
                require(window[local:local + dim] == vectors[index * dim:(index + 1) * dim],
                        f"Native sample vector does not match original full-source VID {vid}")
    return {
        "count": count, "dimension": dim, "source_rows_checked": count,
        "data": {**identity(data_path), "sha256": data_digest.hexdigest()},
        "ids": {**identity(ids_path), "sha256": ids_digest.hexdigest()},
    }


def _validate_memory_graph(prefix, sample):
    path = Path(str(prefix) + "_mem.index")
    tags = _regular(Path(str(path) + ".tags"), owned=True)
    count, dim = sample["count"], sample["dimension"]
    header = _ssd_header(path, count, dim, memory=True)
    _bin(tags, 4, count, 1)
    require(sha256_file(tags) == sample["ids"]["sha256"], "Memory-entry tags do not preserve sampled original VIDs")
    digest = hashlib.sha256()
    checked = 0
    with path.open("rb") as graph, Path(sample["data"]["path"]).open("rb") as vectors:
        digest.update(_read_exact(graph, SECTOR))
        vectors.seek(8)
        per_group = header["per_sector"] or 1
        batch_groups = max(1, BLOCK // header["group_bytes"])
        while checked < count:
            rows = min(count - checked, batch_groups * per_group)
            groups = (rows + per_group - 1) // per_group
            block = _read_exact(graph, groups * header["group_bytes"])
            digest.update(block)
            data = _read_exact(vectors, rows * dim)
            for index in range(rows):
                position = (index // per_group) * header["group_bytes"] + (index % per_group) * header["node_bytes"]
                require(block[position:position + dim] == data[index * dim:(index + 1) * dim],
                        f"Memory-entry vector differs from native sample row {checked + index}")
                degree, dense = struct.unpack_from("<HH", block, position + dim)
                require(degree <= 32 and dense == 0, "Invalid native memory-entry degree counters")
                neighbors = struct.unpack_from(f"<{degree}I", block, position + dim + 4)
                require(all(vid < count for vid in neighbors), "Out-of-bounds local memory-entry edge")
            checked += rows
    return {
        "format": "pipeann-ssd-nr9-uint8-r32", "rows_checked": checked, "header": header,
        "index": {**identity(path), "sha256": digest.hexdigest()},
        "tags": identity(tags, hash_content=True),
    }


def _check_aliases(prefix, original, records):
    require(isinstance(records, dict) and set(records) == set(ALIASES), "Missing/foreign native index aliases")
    for suffix in ALIASES:
        path, target = Path(str(prefix) + suffix), Path(str(original) + suffix)
        require(path.is_symlink() and os.readlink(path) == str(target) and path.resolve(strict=True) == target,
                f"Foreign, replaced or dangling native alias: {path}")
        require(records[suffix] == {"path": str(path), "target": str(target)},
                f"Alias manifest does not identify the configured full index: {path}")


def prepare_memory(config):
    """Create aliases and the native 1% entry; never rebuild or copy the full index."""
    _environment()
    _memory_recipe(config)
    tools = validate_tools(config)
    root, prefix, sample, original = _paths(config)
    manifest_path = sample.parent / "memory.json"
    if os.path.lexists(manifest_path):
        return validate_memory(config)
    for directory in (prefix.parent, sample.parent):
        require(not os.path.lexists(directory), f"Refusing foreign/partial memory preparation directory: {directory}")
    inputs = _original_inputs(config, original)
    digest = config.digest()
    mapping = _identity_vids(Path(str(original) + "_disk.index.tags"),
                            config.section("Dataset").getint("VectorCount"))
    require(_original_inputs(config, original) == inputs, "Original inputs changed during VID validation")
    prefix.parent.mkdir(parents=True, mode=0o700)
    sample.parent.mkdir(parents=True, mode=0o700)
    aliases = {}
    for suffix in ALIASES:
        path, target = Path(str(prefix) + suffix), Path(str(original) + suffix)
        path.symlink_to(target)
        aliases[suffix] = {"path": str(path), "target": str(target)}
    commands = _memory_commands(config, tools, prefix, sample)
    state = {"kind": "pipeann-official-memory", "version": VERSION, "status": "preparing",
             "config_sha256": digest, "commands": commands}
    write_json(manifest_path, state)
    _check_aliases(prefix, original, aliases)
    _run(commands[0], sample.parent, sample.parent / "sampling.log")
    sampled = _validate_sample(config, sample)
    for suffix in ("_mem.index", "_mem.index.tags"):
        require(not os.path.lexists(Path(str(prefix) + suffix)), "Refusing to overwrite a memory-entry output")
    _check_aliases(prefix, original, aliases)
    _run(commands[1], sample.parent, sample.parent / "build-memory.log")
    memory = _validate_memory_graph(prefix, sampled)
    require(_original_inputs(config, original) == inputs, "Original inputs changed during native preparation")
    verify_identities([sampled["data"], sampled["ids"]])
    _check_aliases(prefix, original, aliases)
    require(config.digest() == digest, "Fixed configuration changed during memory preparation")
    require(validate_tools(config) == tools, "Native toolchain changed during memory preparation")
    manifest = {
        **state, "status": "complete", "inputs": inputs, "vid_mapping": mapping, "aliases": aliases,
        "sample": sampled, "memory": memory,
        "toolchain": identity(root / "toolchain.json", hash_content=True),
    }
    write_json(manifest_path, manifest)
    return manifest


def validate_memory(config):
    """Require matching native memory files; missing unfiltered entry is always an error."""
    _environment()
    _memory_recipe(config)
    root, prefix, sample, original = _paths(config)
    path = sample.parent / "memory.json"
    require(path.is_file(), f"Missing native memory-entry manifest: {path}; unfiltered mem_L=0 fallback is forbidden")
    manifest = read_json(_regular(path, owned=True))
    require(isinstance(manifest, dict) and manifest.get("kind") == "pipeann-official-memory"
            and manifest.get("version") == VERSION
            and manifest.get("status") == "complete" and manifest.get("config_sha256") == config.digest(),
            "Partial or incompatible native memory preparation")
    tools = validate_tools(config)
    require(manifest.get("toolchain") == identity(root / "toolchain.json", hash_content=True),
            "Memory entry was built by a different native toolchain")
    require(manifest.get("commands") == _memory_commands(config, tools, prefix, sample),
            "Memory entry does not have the fixed native sampler/builder provenance")
    require(manifest.get("inputs") == _original_inputs(config, original), "Original full-source/index identities changed")
    _check_aliases(prefix, original, manifest.get("aliases"))
    require({path.name for path in prefix.parent.iterdir()} ==
            {prefix.name + suffix for suffix in (*ALIASES, "_mem.index", "_mem.index.tags")},
            "Foreign/partial files in the memory-entry alias directory")
    require({path.name for path in sample.parent.iterdir()} ==
            {sample.name + "_data.bin", sample.name + "_ids.bin", "memory.json", "sampling.log", "build-memory.log"},
            "Foreign/partial files in the native sample directory")
    count, dim = _sample_headers(config, sample)
    sampled, memory = manifest.get("sample"), manifest.get("memory")
    require(isinstance(sampled, dict)
            and {"count", "dimension", "source_rows_checked", "data", "ids"} <= sampled.keys()
            and isinstance(memory, dict)
            and {"rows_checked", "format", "header", "index", "tags"} <= memory.keys(),
            "Partial native memory artifact manifest")
    require(sampled["count"] == count and sampled["dimension"] == dim and sampled["source_rows_checked"] == count
            and memory["rows_checked"] == count and memory["format"] == "pipeann-ssd-nr9-uint8-r32",
            "Missing complete native sample/vector/edge validation")
    mapping = manifest.get("vid_mapping")
    require(isinstance(mapping, dict) and mapping.get("kind") == "identity-uint32"
            and mapping.get("rows_checked") == config.section("Dataset").getint("VectorCount")
            and isinstance(mapping.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", mapping["sha256"]),
            "Missing original full-VID identity evidence for NO_MAPPING")
    expected = (
        (sampled["data"], Path(str(sample) + "_data.bin")), (sampled["ids"], Path(str(sample) + "_ids.bin")),
        (memory["index"], Path(str(prefix) + "_mem.index")), (memory["tags"], Path(str(prefix) + "_mem.index.tags")),
    )
    for item, expected_path in expected:
        _regular(expected_path, owned=True)
        require(isinstance(item, dict) and item.get("path") == str(expected_path) and "sha256" in item,
                "Foreign native memory artifact")
        verify_identities([item])
    require(memory["tags"]["sha256"] == sampled["ids"]["sha256"], "Native memory tags differ from sampled VIDs")
    require(memory["header"] == _ssd_header(Path(str(prefix) + "_mem.index"), count, dim, memory=True),
            "Native memory-entry format changed")
    _bin(Path(str(prefix) + "_mem.index.tags"), 4, count, 1)
    return manifest
