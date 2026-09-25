"""Native preparation contracts using isolated, small fixtures and build stubs."""

from array import array
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS))
from official_benchmark_config import Config, DEFAULT_CONFIG, read_json, sha256_file, write_json

SPEC = importlib.util.spec_from_file_location(
    "pipeann_official_prepare", BENCHMARKS / "pipeann_official_prepare.py",
)
PREP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREP)


class FixtureConfig(Config):
    """Keep control files fixed; substitute only in-memory fixture input paths."""

    def __init__(self, root):
        super().__init__(DEFAULT_CONFIG)
        overrides = {
            "Dataset": {"VectorFile": str(root / "data/base.bin"), "VectorCount": "1000"},
            "PipeANN": {
                "SourceDirectory": str(root / "native"), "SourceRevision": "f" * 40,
                "SourceIndexPrefix": str(root / "original/full"), "IndexPrefix": str(root / "alias/full"),
                "ToolchainDirectory": str(root / "toolchain"),
            },
            "MemoryIndex": {"SamplePrefix": str(root / "preparation/sample")},
            "Build": {"Threads": "4", "Jobs": "2"},
        }
        for section, values in overrides.items():
            for key, value in values.items():
                self.parser[section][key] = value

    def digest(self):
        values = {section: dict(self.parser[section]) for section in self.parser.sections()}
        return hashlib.sha256((super().digest() + json.dumps(values, sort_keys=True)).encode()).hexdigest()


def write_file(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def u32(values):
    result = array("I", values)
    if sys.byteorder != "little":
        result.byteswap()
    return result.tobytes()


def bin_file(path, rows, columns, payload):
    write_file(path, struct.pack("<II", rows, columns) + payload)


def ssd_graph(path, vectors, degree=32, attributes=0):
    rows, dim = len(vectors), len(vectors[0])
    node_bytes = dim + (degree + 1) * 4 + attributes
    per_sector = PREP.SECTOR // node_bytes
    header = struct.pack("<II9Q", 9, 1, rows, dim, 0, node_bytes, per_sector, rows, attributes, degree, 0)
    payload = bytearray(PREP.SECTOR * (1 + (rows + per_sector - 1) // per_sector))
    payload[:len(header)] = header
    for row, vector in enumerate(vectors):
        offset = PREP.SECTOR * (1 + row // per_sector) + (row % per_sector) * node_bytes
        payload[offset:offset + dim] = vector
        struct.pack_into("<HHI", payload, offset + dim, int(rows > 1), 0, (row + 1) % rows)
    write_file(path, payload)


class FixtureTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="pipeann-official-contract-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.config = FixtureConfig(self.root)
        self.source = self.root / "native"
        self.source.mkdir()
        self.toolchain = self.root / "toolchain"
        self.prefix = self.root / "alias/full"
        self.original = self.root / "original/full"
        self.sample = self.root / "preparation/sample"
        self.calls = []
        environment = mock.patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)


class ToolchainContractTest(FixtureTest):
    def setUp(self):
        super().setUp()
        names = [
            "CMakeLists.txt", "src/CMakeLists.txt", "tests/CMakeLists.txt", "tests/utils/CMakeLists.txt",
            "src/core.cpp", "include/dirty.h", "include/local.h",
            "third_party/liburing/configure", "third_party/liburing/Makefile",
            "third_party/liburing/src/Makefile", "third_party/liburing/src/setup.c",
            *[relative + ".cpp" for relative in PREP.TARGETS.values()],
        ]
        for name in names:
            write_file(self.source / name, (f"native fixture: {name}\n").encode())
        (self.source / "include/dirty.h").write_text("tracked local modification\n")
        (self.source / "third_party/liburing/configure").chmod(0o755)
        self.tracked = [name for name in names if name != "include/local.h"]
        for name in PREP.GENERATED_URING:
            write_file(self.source / name, b"original generated output: never copy\n")
        write_file(self.source / "third_party/liburing/src/liburing.a", b"old archive: never copy\n")
        write_file(self.source / "build/tests/search_disk_index", b"old update-capable binary\n")
        write_file(self.source / "data/large.bin", b"dataset: never copy\n")
        self.compiler = self.root / "fixture-c++"
        self.compiler.write_bytes(b"fixture compiler identity\n")
        self.compiler.chmod(0o755)
        git = mock.patch.object(PREP, "_git", side_effect=self.fake_git)
        run = mock.patch.object(PREP, "_run", side_effect=self.native_run)
        linkage = mock.patch.object(PREP, "_linkage", return_value={"targets": {}, "files": []})
        self.git = git.start()
        self.run = run.start()
        self.linkage = linkage.start()
        for patcher in (git, run, linkage):
            self.addCleanup(patcher.stop)

    def fake_git(self, repository, *args):
        self.assertEqual(repository, self.source)
        if args[0] == "rev-parse":
            return (str(self.source) if args[1] == "--show-toplevel" else "f" * 40).encode() + b"\n"
        if args[0] == "status":
            return b" M include/dirty.h\n?? include/local.h\n"
        if args[0] == "diff":
            return b"tracked local modification\n"
        self.assertEqual(args[0], "ls-files")
        if "--stage" in args:
            return b"".join(
                f"{'100755' if name.endswith('/configure') else '100644'} {'a' * 40} 0\t{name}\0".encode()
                for name in self.tracked
            )
        return b"".join(name.encode() + b"\0" for name in ("include/local.h", *PREP.GENERATED_URING))

    def generation(self):
        source, build = self.toolchain / "source", self.toolchain / "build"
        build.mkdir(exist_ok=True)
        profile = PREP._profile(self.config)
        cache = {key: (PREP.PROFILE[key][0] if key in PREP.PROFILE else "STRING", value)
                 for key, value in profile["cache"].items()}
        cache.update({
            "CMAKE_HOME_DIRECTORY": ("INTERNAL", str(source)),
            "CMAKE_CACHEFILE_DIR": ("INTERNAL", str(build)),
            "CMAKE_GENERATOR": ("INTERNAL", "Unix Makefiles"),
            "PIPEANN_IO_URING_COMPILE_RESULT": ("INTERNAL", "TRUE"),
            "PIPEANN_IO_URING_RUN_RESULT": ("INTERNAL", "0"),
            "CMAKE_CXX_COMPILER": ("FILEPATH", str(self.compiler)),
        })
        (build / "CMakeCache.txt").write_text("".join(f"{key}:{kind}={value}\n"
                                                    for key, (kind, value) in cache.items()))
        database = []
        for target in ("pipeann", *PREP.TARGETS):
            relative = "src/core" if target == "pipeann" else PREP.TARGETS[target]
            directory = build / Path(relative).parent
            output = f"CMakeFiles/{target}.dir/{Path(relative).name}.cpp.o"
            words = [str(self.compiler), *["-D" + flag for flag in sorted(PREP.DEFINITIONS)],
                     "-O3", "-g", "-fopenmp", "-o", output, "-c", str(source / (relative + ".cpp"))]
            database.append({"directory": str(directory), "file": str(source / (relative + ".cpp")),
                             "command": shlex.join(words)})
            flags = directory / "CMakeFiles" / (target + ".dir") / "flags.make"
            write_file(flags, (
                "CXX_DEFINES = -DUSE_URING -DUSE_TCMALLOC -DNDEBUG\n"
                "CXX_FLAGS = -DREAD_ONLY_TESTS -DNO_MAPPING -O3 -g -fopenmp\n"
            ).encode())
            link = ([str(self.compiler), "-o", target,
                     *shlex.split(profile["cache"].get("CMAKE_EXE_LINKER_FLAGS", "")),
                     "-Wl,--no-as-needed", "-ltcmalloc",
                     "-Wl,--as-needed", str(build / "src/libpipeann.a"),
                     "-L" + str(source / "third_party/liburing/src"), "-luring"]
                    if target != "pipeann" else ["ar", "qc", "libpipeann.a", output])
            flags.with_name("link.txt").write_text(shlex.join(link) + "\n")
        write_json(build / "compile_commands.json", database)

    def native_run(self, argv, cwd, log):
        self.calls.append((argv, str(cwd), str(log)))
        self.assertEqual(argv[:3], self.config.affinity("Build"))
        self.assertTrue(Path(cwd).is_relative_to(self.toolchain))
        Path(log).write_text("fixture native output\n")
        command = argv[3:]
        if command[0] == "./configure":
            for name in ("compat.h", "io_uring_version.h"):
                write_file(Path(cwd) / "src/include/liburing" / name, b"isolated generated header\n")
        elif command[0] == "make":
            self.assertEqual(command, ["make", "-j", "2", "library"])
            write_file(Path(cwd) / "src/liburing.a", b"!<arch>\nisolated liburing\n")
        elif "--build" in command:
            self.assertEqual(command[command.index("--target") + 1:], ["pipeann", *PREP.TARGETS])
            for name, relative in PREP.TARGETS.items():
                path = self.toolchain / "build" / relative
                write_file(path, b"\x7fELF" + name.encode())
                path.chmod(0o755)
            write_file(self.toolchain / "build/src/libpipeann.a", b"!<arch>\nread-only library\n")
        else:
            self.assertEqual(command, [
                "cmake", "-C", str(self.config.path_value("PipeANN", "CMakeProfile")),
                "-S", str(self.toolchain / "source"), "-B", str(self.toolchain / "build"),
                "-G", "Unix Makefiles",
            ])
            self.generation()

    def build(self):
        return PREP.build_tools(self.config)

    def test_fixed_build_freezes_dirty_sources_and_rebuilds_liburing(self):
        original = {str(path): sha256_file(path) for path in self.source.rglob("*") if path.is_file()}
        manifest = self.build()
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(set(manifest["binaries"]), set(PREP.TARGETS))
        self.assertEqual(manifest["effective"]["backend"], "uring")
        self.assertEqual(manifest["effective"]["allocator"], "tcmalloc")
        self.assertEqual(manifest["source"]["revision"], "f" * 40)
        self.assertEqual(manifest["source"]["files"]["include/dirty.h"]["sha256"],
                         sha256_file(self.source / "include/dirty.h"))
        self.assertIsNone(manifest["source"]["files"]["include/local.h"]["git_blob"])
        snapshot = self.toolchain / "source"
        self.assertEqual((snapshot / "include/dirty.h").read_bytes(), (self.source / "include/dirty.h").read_bytes())
        self.assertNotEqual((snapshot / "include/dirty.h").stat().st_ino,
                            (self.source / "include/dirty.h").stat().st_ino)
        self.assertFalse((snapshot / "build").exists())
        self.assertFalse((snapshot / "data").exists())
        self.assertNotIn("third_party/liburing/src/include/liburing/compat.h", manifest["source"]["files"])
        self.assertEqual((snapshot / "third_party/liburing/src/include/liburing/compat.h").read_bytes(),
                         b"isolated generated header\n")
        self.assertEqual({str(path): sha256_file(path) for path in self.source.rglob("*") if path.is_file()}, original)
        self.assertEqual(PREP.validate_tools(self.config), manifest)
        self.assertEqual(self.build(), manifest)
        self.assertEqual(len(self.calls), 4)

    def test_environment_overrides_rejected_before_output_creation(self):
        for name in ("ADDITIONAL_DEFINITIONS", "CXXFLAGS", "CPPFLAGS", "CXX", "LD_PRELOAD", "PIPEANN_TEST"):
            with self.subTest(name=name), mock.patch.dict(os.environ, {name: ""}):
                with self.assertRaisesRegex(ValueError, "override"):
                    self.build()
        self.assertFalse(self.toolchain.exists())
        self.assertFalse(self.calls)

    def test_fixed_profile_is_validated_before_cmake(self):
        path = self.root / "invalid-profile.cmake"
        path.write_text(self.config.path_value("PipeANN", "CMakeProfile").read_text().replace(
            "set(USE_TCMALLOC ON", "set(USE_TCMALLOC OFF"))
        self.config.parser["PipeANN"]["CMakeProfile"] = str(path)
        with self.assertRaisesRegex(ValueError, "CMake profile"):
            self.build()
        self.assertFalse(self.toolchain.exists())

    def test_private_link_directory_cannot_inject_other_linker_flags(self):
        path = self.root / "invalid-profile.cmake"
        path.write_text(self.config.path_value("PipeANN", "CMakeProfile").read_text().replace(
            "-Wl,-rpath,", "-Wl,--no-as-needed,"))
        self.config.parser["PipeANN"]["CMakeProfile"] = str(path)
        with self.assertRaisesRegex(ValueError, "matching dependency"):
            self.build()
        self.assertFalse(self.toolchain.exists())

    def test_byte_identical_profile_snapshot_reuses_existing_tools(self):
        manifest = self.build()
        copied = self.root / "copied-profile.cmake"
        shutil.copy2(self.config.path_value("PipeANN", "CMakeProfile"), copied)
        self.config.parser["PipeANN"]["CMakeProfile"] = str(copied)
        with mock.patch.object(self.config, "digest", return_value=manifest["config_sha256"]):
            self.assertEqual(PREP.validate_tools(self.config), manifest)
        self.assertEqual(len(self.calls), 4)

    def test_missing_and_partial_toolchains_do_not_rebuild(self):
        with self.assertRaisesRegex(ValueError, "Missing completed"):
            PREP.validate_tools(self.config)
        self.toolchain.mkdir()
        write_json(self.toolchain / "toolchain.json", {"status": "building"})
        with self.assertRaisesRegex(ValueError, "partial"):
            PREP.validate_tools(self.config)
        with self.assertRaisesRegex(ValueError, "partial"):
            self.build()
        self.assertFalse(self.calls)

    def test_nonobject_toolchain_manifest_is_an_explicit_readiness_error(self):
        self.toolchain.mkdir()
        write_json(self.toolchain / "toolchain.json", [])
        with self.assertRaisesRegex(ValueError, "partial"):
            PREP.validate_tools(self.config)
        self.assertFalse(self.calls)

    def test_foreign_empty_toolchain_is_not_cleaned_or_adopted(self):
        self.toolchain.mkdir()
        with self.assertRaisesRegex(ValueError, "foreign/partial"):
            self.build()
        self.assertTrue(self.toolchain.is_dir())
        self.assertFalse(self.calls)

    def test_foreign_toolchain_symlink_is_rejected(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        marker = foreign / "keep"
        marker.write_text("preserve")
        self.toolchain.symlink_to(foreign, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.build()
        self.assertEqual(marker.read_text(), "preserve")
        self.assertFalse(self.calls)

    def test_missing_required_source_is_rejected_before_build(self):
        (self.source / "third_party/liburing/configure").unlink()
        with self.assertRaisesRegex(ValueError, "Missing native build input"):
            self.build()
        self.assertFalse(self.toolchain.exists())

    def test_missing_source_submodule_is_not_filled_from_an_old_build(self):
        def missing_submodule(repository, *args):
            output = self.fake_git(repository, *args)
            if args[0] == "ls-files" and "--stage" in args:
                output += f"160000 {'b' * 40} 0\tinclude/missing_dependency\0".encode()
            return output

        with mock.patch.object(PREP, "_git", side_effect=missing_submodule):
            with self.assertRaisesRegex(ValueError, "Missing source submodule"):
                self.build()
        self.assertFalse(self.toolchain.exists())

    def test_source_symlink_cannot_escape_snapshot_inputs(self):
        outside = self.root / "outside.h"
        outside.write_text("not an authorized dependency\n")
        (self.source / "include/local.h").unlink()
        (self.source / "include/local.h").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink escapes"):
            self.build()
        self.assertFalse(self.toolchain.exists())

    def test_uring_fallback_is_rejected_before_native_build(self):
        generation = self.generation

        def aio_generation():
            generation()
            path = self.toolchain / "build/CMakeCache.txt"
            path.write_text(path.read_text().replace("IO_ENGINE:STRING=uring", "IO_ENGINE:STRING=aio"))

        with mock.patch.object(self, "generation", side_effect=aio_generation):
            with self.assertRaisesRegex(RuntimeError, "no backend/profile fallback"):
                self.build()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(read_json(self.toolchain / "build-state.json")["status"], "failed")
        self.assertFalse((self.toolchain / "toolchain.json").exists())

    def test_probe_failure_does_not_count_as_effective_uring(self):
        self.build()
        path = self.toolchain / "build/CMakeCache.txt"
        path.write_text(path.read_text().replace("PIPEANN_IO_URING_RUN_RESULT:INTERNAL=0",
                                               "PIPEANN_IO_URING_RUN_RESULT:INTERNAL=1"))
        with self.assertRaisesRegex(ValueError, "probe"):
            PREP.validate_tools(self.config)
        self.assertEqual(len(self.calls), 4)

    def test_library_and_executable_flags_are_both_required(self):
        self.build()
        for relative in ("src/CMakeFiles/pipeann.dir/flags.make",
                         "tests/CMakeFiles/search_disk_index.dir/flags.make"):
            path = self.toolchain / "build" / relative
            original = path.read_bytes()
            with self.subTest(relative=relative):
                path.write_bytes(original.replace(b"-DREAD_ONLY_TESTS", b""))
                with self.assertRaisesRegex(ValueError, "missing read-only"):
                    PREP._effective_profile(self.toolchain, PREP._profile(self.config))
                path.write_bytes(original)

    def test_database_flags_cannot_disable_or_undefine_required_definitions(self):
        self.build()
        path = self.toolchain / "build/compile_commands.json"
        baseline = read_json(path)
        for flag in ("-UREAD_ONLY_TESTS", "-DNO_MAPPING=0", "-DUSE_AIO"):
            entries = [dict(entry) for entry in baseline]
            entries[0]["command"] += " " + flag
            write_json(path, entries)
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                PREP._effective_profile(self.toolchain, PREP._profile(self.config))
        self.assertEqual(len(self.calls), 4)

    def test_renamed_binary_is_never_accepted(self):
        manifest = self.build()
        path = Path(manifest["binaries"]["search_disk_index"]["path"])
        path.write_bytes(b"\x7fELFrenamed old update-capable executable")
        with self.assertRaisesRegex(ValueError, "renamed or legacy"):
            PREP.validate_tools(self.config)
        with self.assertRaisesRegex(ValueError, "renamed or legacy"):
            self.build()
        self.assertEqual(len(self.calls), 4)

    def test_missing_generated_liburing_header_is_a_readiness_error(self):
        self.build()
        (self.toolchain / "source/third_party/liburing/src/include/liburing/compat.h").unlink()
        with self.assertRaisesRegex(ValueError, "Missing or non-regular"):
            PREP.validate_tools(self.config)
        self.assertEqual(len(self.calls), 4)

    def test_changed_dirty_native_source_is_not_silently_rebuilt(self):
        self.build()
        (self.source / "include/dirty.h").write_text("another local modification\n")
        with self.assertRaisesRegex(ValueError, "provenance changed"):
            PREP.validate_tools(self.config)
        self.assertEqual(len(self.calls), 4)

    def test_only_unchanged_recorded_failed_build_can_resume(self):
        native_run = self.native_run

        def failure(argv, cwd, log):
            native_run(argv, cwd, log)
            if "--build" in argv:
                raise subprocess.CalledProcessError(2, argv)

        with mock.patch.object(PREP, "_run", side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, "No flags/backend/allocator were relaxed"):
                self.build()
        with self.assertRaisesRegex(ValueError, "Missing completed"):
            PREP.validate_tools(self.config)
        manifest = self.build()
        self.assertEqual(read_json(self.toolchain / "build-state.json")["attempt"], 2)
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(len(self.calls), 8)

    def test_modified_partial_outputs_are_never_adopted(self):
        with mock.patch.object(PREP, "_run", side_effect=FileNotFoundError("missing compiler")):
            with self.assertRaisesRegex(RuntimeError, "missing compiler"):
                self.build()
        write_file(self.toolchain / "foreign-binary", b"foreign")
        with self.assertRaisesRegex(ValueError, "ambiguous reuse"):
            self.build()
        self.assertEqual((self.toolchain / "foreign-binary").read_bytes(), b"foreign")


class LinkageContractTest(unittest.TestCase):
    def test_direct_elf_loader_is_part_of_dependency_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allocator, loader = root / "libtcmalloc.so.4", root / "ld-linux-x86-64.so.2"
            allocator.write_bytes(b"allocator fixture")
            loader.write_bytes(b"loader fixture")
            dynamic = (" (NEEDED) Shared library: [libtcmalloc.so.4]\n"
                       " (NEEDED) Shared library: [ld-linux-x86-64.so.2]\n")
            dependencies = f"libtcmalloc.so.4 => {allocator} (0x1000)\n{loader} (0x2000)\n"
            with mock.patch.object(PREP.subprocess, "check_output", side_effect=[dynamic, dependencies]):
                evidence = PREP._linkage(root, {"search": {"path": str(root / "search")}})
            self.assertEqual(str(loader), evidence["targets"]["search"]["resolved"][loader.name])
            self.assertEqual({str(allocator), str(loader)}, {item["path"] for item in evidence["files"]})

    def test_allocator_is_verified_from_binary_not_only_cache(self):
        with mock.patch.object(PREP.subprocess, "check_output", return_value=""):
            with self.assertRaisesRegex(ValueError, "actually use"):
                PREP._linkage(Path("/isolated"), {"search": {"path": "/isolated/search"}})

    def test_aio_linkage_is_rejected(self):
        output = " (NEEDED) Shared library: [libtcmalloc.so.4]\n (NEEDED) Shared library: [libaio.so.1]\n"
        with mock.patch.object(PREP.subprocess, "check_output", return_value=output):
            with self.assertRaisesRegex(ValueError, "aio backend"):
                PREP._linkage(Path("/isolated"), {"search": {"path": "/isolated/search"}})

    def test_unresolved_allocator_is_not_accepted(self):
        with mock.patch.object(PREP.subprocess, "check_output", side_effect=[
            " (NEEDED) Shared library: [libtcmalloc.so.4]\n", "libtcmalloc.so.4 => not found\n",
        ]):
            with self.assertRaisesRegex(ValueError, "Unresolved"):
                PREP._linkage(Path("/isolated"), {"search": {"path": "/isolated/search"}})

    def test_foreign_dynamic_liburing_is_not_accepted(self):
        with mock.patch.object(PREP.subprocess, "check_output", side_effect=[
            " (NEEDED) Shared library: [libtcmalloc.so.4]\n (NEEDED) Shared library: [liburing.so.2]\n",
            "liburing.so.2 => /foreign/liburing.so.2 (0x1234)\n",
        ]):
            with self.assertRaisesRegex(ValueError, "foreign liburing"):
                PREP._linkage(Path("/isolated"), {"search": {"path": "/isolated/search"}})


class MemoryContractTest(FixtureTest):
    def setUp(self):
        super().setUp()
        self.count, self.dim = 1000, 128
        self.vectors = [bytes([row % 251]) * self.dim for row in range(self.count)]
        bin_file(self.root / "data/base.bin", self.count, self.dim, b"".join(self.vectors))
        ssd_graph(Path(str(self.original) + "_disk.index"), self.vectors, degree=128, attributes=28)
        bin_file(Path(str(self.original) + "_disk.index.tags"), self.count, 1, u32(range(self.count)))
        bin_file(Path(str(self.original) + "_pq_compressed.bin"), self.count, 32, bytes(self.count * 32))
        write_file(Path(str(self.original) + "_pq_pivots.bin"), b"native pq pivot fixture\n")
        label_header = struct.pack("<QQQ", 1, PREP.SECTOR, PREP.SECTOR + self.count * 4)
        write_file(Path(str(self.original) + ".label.0"),
                   label_header + bytes(PREP.SECTOR - len(label_header)) + u32(range(self.count)))
        bin_file(Path(str(self.original) + ".label.0.filter"), self.count, 4, bytes(self.count * 4))
        write_file(Path(str(self.original) + ".label.1"), bytes(PREP.SECTOR + self.count * 4))
        write_file(Path(str(self.original) + ".label.1.quantize"),
                   struct.pack("<I", 256) + u32(range(257)) + struct.pack("<I", self.count) + bytes(self.count))
        self.ids = [3, 55, 99, 305, 457, 806, 997]
        self.tools = {"binaries": {}}
        for name in PREP.TARGETS:
            path = self.toolchain / "bin" / name
            write_file(path, b"\x7fELFfixture")
            self.tools["binaries"][name] = {"path": str(path), "sha256": sha256_file(path)}
        write_json(self.toolchain / "toolchain.json", self.tools)
        validator = mock.patch.object(PREP, "validate_tools", return_value=self.tools)
        runner = mock.patch.object(PREP, "_run", side_effect=self.native_run)
        self.validator = validator.start()
        self.runner = runner.start()
        self.addCleanup(validator.stop)
        self.addCleanup(runner.stop)

    def native_run(self, argv, cwd, log):
        self.calls.append(argv)
        Path(log).write_text("fixture native memory output\n")
        name = Path(argv[3]).name
        if name == "gen_random_slice":
            bin_file(Path(str(self.sample) + "_data.bin"), len(self.ids), self.dim,
                     b"".join(self.vectors[vid] for vid in self.ids))
            bin_file(Path(str(self.sample) + "_ids.bin"), len(self.ids), 1, u32(self.ids))
        else:
            self.assertEqual(name, "build_memory_index")
            ssd_graph(Path(str(self.prefix) + "_mem.index"), [self.vectors[vid] for vid in self.ids])
            shutil.copyfile(str(self.sample) + "_ids.bin", str(self.prefix) + "_mem.index.tags")

    def prepare(self):
        return PREP.prepare_memory(self.config)

    def test_official_commands_aliases_formats_and_provenance(self):
        protected = [self.root / "data/base.bin", *[Path(str(self.original) + suffix) for suffix in PREP.ALIASES]]
        original = {str(path): (PREP._stamp(path), sha256_file(path)) for path in protected}
        manifest = self.prepare()
        affinity = self.config.affinity("Build")
        self.assertEqual(self.calls, [
            affinity + [self.tools["binaries"]["gen_random_slice"]["path"], "uint8",
                        str(self.root / "data/base.bin"), str(self.sample), "0.01"],
            affinity + [self.tools["binaries"]["build_memory_index"]["path"], "uint8",
                        str(self.sample) + "_data.bin", str(self.sample) + "_ids.bin",
                        str(self.prefix) + "_mem.index", "32", "64", "1.2", "4", "l2"],
        ])
        self.assertEqual(manifest["sample"]["count"], 7)
        self.assertNotEqual(manifest["sample"]["count"], self.count // 100)
        self.assertEqual(manifest["sample"]["source_rows_checked"], 7)
        self.assertEqual(manifest["memory"]["rows_checked"], 7)
        self.assertEqual(manifest["vid_mapping"]["rows_checked"], self.count)
        for suffix in PREP.ALIASES:
            alias = Path(str(self.prefix) + suffix)
            self.assertTrue(alias.is_symlink())
            self.assertEqual(alias.resolve(), Path(str(self.original) + suffix))
        for suffix in ("_mem.index", "_mem.index.tags"):
            self.assertFalse(Path(str(self.prefix) + suffix).is_symlink())
            self.assertEqual(Path(str(self.prefix) + suffix).stat().st_nlink, 1)
            self.assertFalse(Path(str(self.original) + suffix).exists())
        self.assertEqual({str(path): (PREP._stamp(path), sha256_file(path)) for path in protected}, original)
        self.assertEqual(PREP.validate_memory(self.config), manifest)
        self.assertEqual(self.prepare(), manifest)
        self.assertEqual(len(self.calls), 2)

    def test_missing_entry_never_returns_mem_l_zero_or_starts_builder(self):
        with self.assertRaisesRegex(ValueError, "mem_L=0 fallback is forbidden"):
            PREP.validate_memory(self.config)
        self.assertFalse(self.calls)
        self.assertFalse(self.prefix.parent.exists())

    def test_nonobject_memory_manifest_is_an_explicit_readiness_error(self):
        self.sample.parent.mkdir()
        write_json(self.sample.parent / "memory.json", [])
        with self.assertRaisesRegex(ValueError, "Partial or incompatible"):
            PREP.validate_memory(self.config)
        self.assertFalse(self.calls)

    def test_memory_preparation_requires_completed_native_toolchain(self):
        self.validator.side_effect = ValueError("Missing completed native toolchain")
        with self.assertRaisesRegex(ValueError, "Missing completed"):
            self.prepare()
        self.assertFalse(self.prefix.parent.exists())
        self.assertFalse(self.sample.parent.exists())

    def test_recipe_change_is_not_silently_normalized(self):
        for section, key, value in (("MemoryIndex", "SamplingRate", "0.1"),
                                    ("PipeANN", "UnfilteredMemoryL", "0"),
                                    ("PipeANN", "FilteredMemoryL", "10")):
            original = self.config.parser[section][key]
            self.config.parser[section][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.prepare()
            self.config.parser[section][key] = original
        self.assertFalse(self.calls)

    def test_foreign_sample_directory_is_not_overwritten(self):
        self.sample.parent.mkdir()
        marker = self.sample.parent / "keep"
        marker.write_text("preserve")
        with self.assertRaisesRegex(ValueError, "foreign/partial"):
            self.prepare()
        self.assertEqual(marker.read_text(), "preserve")
        self.assertFalse(self.prefix.parent.exists())
        self.assertFalse(self.calls)

    def test_foreign_existing_alias_is_preserved(self):
        self.prefix.parent.mkdir()
        alias = Path(str(self.prefix) + "_disk.index")
        alias.symlink_to(self.root / "unrelated")
        with self.assertRaisesRegex(ValueError, "foreign/partial"):
            self.prepare()
        self.assertEqual(os.readlink(alias), str(self.root / "unrelated"))
        self.assertFalse(self.calls)

    def test_prefix_cannot_point_into_original_index_directory(self):
        self.config.parser["PipeANN"]["IndexPrefix"] = str(self.original)
        with self.assertRaisesRegex(ValueError, "overlap"):
            self.prepare()
        self.assertFalse(self.calls)

    def test_missing_attribute_approximation_fails_before_alias_creation(self):
        Path(str(self.original) + ".label.1.quantize").unlink()
        with self.assertRaisesRegex(ValueError, "Missing or non-regular"):
            self.prepare()
        self.assertFalse(self.prefix.parent.exists())
        self.assertFalse(self.calls)

    def test_partial_original_graph_fails_before_alias_creation(self):
        path = Path(str(self.original) + "_disk.index")
        with path.open("r+b") as stream:
            stream.truncate(path.stat().st_size - 1)
        with self.assertRaisesRegex(ValueError, "Partial native graph"):
            self.prepare()
        self.assertFalse(self.calls)

    def test_nonidentity_original_vid_map_rejected_for_no_mapping(self):
        path = Path(str(self.original) + "_disk.index.tags")
        with path.open("r+b") as stream:
            stream.seek(8 + 4 * 42)
            stream.write(struct.pack("<I", 99))
        with self.assertRaisesRegex(ValueError, "NO_MAPPING requires"):
            self.prepare()
        self.assertFalse(self.prefix.parent.exists())
        self.assertFalse(self.calls)

    def test_success_exit_without_sampler_outputs_is_not_success(self):
        with mock.patch.object(PREP, "_run", return_value=None):
            with self.assertRaisesRegex(ValueError, "Missing or non-regular"):
                self.prepare()
        with self.assertRaisesRegex(ValueError, "Partial or incompatible"):
            PREP.validate_memory(self.config)
        self.assertFalse(self.calls)

    def test_failed_native_sampler_does_not_run_builder_or_resume(self):
        with mock.patch.object(PREP, "_run", side_effect=subprocess.CalledProcessError(2, ["gen_random_slice"])):
            with self.assertRaises(subprocess.CalledProcessError):
                self.prepare()
        with self.assertRaisesRegex(ValueError, "Partial or incompatible"):
            self.prepare()
        self.assertFalse(self.calls)
        self.assertFalse(Path(str(self.prefix) + "_mem.index").exists())

    def test_truncated_sample_ids_are_rejected_before_building(self):
        native = self.native_run

        def partial(argv, cwd, log):
            native(argv, cwd, log)
            if Path(argv[3]).name == "gen_random_slice":
                path = Path(str(self.sample) + "_ids.bin")
                path.write_bytes(path.read_bytes()[:-1])

        with mock.patch.object(PREP, "_run", side_effect=partial):
            with self.assertRaisesRegex(ValueError, "header/payload size"):
                self.prepare()
        self.assertEqual(len(self.calls), 1)

    def test_duplicate_sample_vids_are_rejected(self):
        self.ids = [3, 3, 55]
        with self.assertRaisesRegex(ValueError, "unique, increasing"):
            self.prepare()
        self.assertEqual(len(self.calls), 1)

    def test_sample_vids_cannot_exceed_original_source_bounds(self):
        native = self.native_run

        def out_of_bounds(argv, cwd, log):
            native(argv, cwd, log)
            if Path(argv[3]).name == "gen_random_slice":
                with Path(str(self.sample) + "_ids.bin").open("r+b") as stream:
                    stream.seek(-4, os.SEEK_END)
                    stream.write(struct.pack("<I", self.count))

        with mock.patch.object(PREP, "_run", side_effect=out_of_bounds):
            with self.assertRaisesRegex(ValueError, "full-source bounds"):
                self.prepare()
        self.assertEqual(len(self.calls), 1)

    def test_wrong_source_sample_bytes_are_rejected(self):
        native = self.native_run

        def wrong_source(argv, cwd, log):
            native(argv, cwd, log)
            if Path(argv[3]).name == "gen_random_slice":
                with Path(str(self.sample) + "_data.bin").open("r+b") as stream:
                    stream.seek(8)
                    stream.write(b"\xff")

        with mock.patch.object(PREP, "_run", side_effect=wrong_source):
            with self.assertRaisesRegex(ValueError, "original full-source VID"):
                self.prepare()
        self.assertEqual(len(self.calls), 1)

    def test_sampler_cannot_inject_a_foreign_memory_output(self):
        native = self.native_run
        foreign = self.root / "foreign-memory"
        foreign.write_text("preserve")

        def inject(argv, cwd, log):
            native(argv, cwd, log)
            Path(str(self.prefix) + "_mem.index").symlink_to(foreign)

        with mock.patch.object(PREP, "_run", side_effect=inject):
            with self.assertRaisesRegex(ValueError, "overwrite"):
                self.prepare()
        self.assertEqual(foreign.read_text(), "preserve")
        self.assertEqual(len(self.calls), 1)

    def corrupt_memory(self, kind):
        native = self.native_run

        def corrupt(argv, cwd, log):
            native(argv, cwd, log)
            if Path(argv[3]).name != "build_memory_index":
                return
            path = Path(str(self.prefix) + "_mem.index")
            if kind == "old-format":
                with path.open("r+b") as stream:
                    stream.write(struct.pack("<I", 8))
            elif kind == "tags":
                bin_file(Path(str(path) + ".tags"), len(self.ids), 1, u32(range(len(self.ids))))
            else:
                with path.open("r+b") as stream:
                    stream.seek(PREP.SECTOR + (self.dim + 4 if kind == "edge" else 0))
                    stream.write(struct.pack("<I", len(self.ids)) if kind == "edge" else b"\xff")

        with mock.patch.object(PREP, "_run", side_effect=corrupt):
            with self.assertRaises(ValueError):
                self.prepare()
        self.assertEqual(read_json(self.sample.parent / "memory.json")["status"], "preparing")
        with self.assertRaisesRegex(ValueError, "Partial or incompatible"):
            PREP.validate_memory(self.config)

    def test_legacy_memory_format_is_rejected(self):
        self.corrupt_memory("old-format")

    def test_memory_tags_must_preserve_global_sample_vids(self):
        self.corrupt_memory("tags")

    def test_memory_edge_vids_must_be_local_and_in_bounds(self):
        self.corrupt_memory("edge")

    def test_memory_vectors_must_match_sample(self):
        self.corrupt_memory("vector")

    def test_completed_memory_missing_tags_never_falls_back(self):
        self.prepare()
        Path(str(self.prefix) + "_mem.index.tags").unlink()
        with self.assertRaisesRegex(ValueError, "Foreign/partial"):
            PREP.validate_memory(self.config)
        self.assertEqual(len(self.calls), 2)

    def test_completed_alias_replacement_is_rejected(self):
        self.prepare()
        alias = Path(str(self.prefix) + "_pq_pivots.bin")
        alias.unlink()
        alias.symlink_to(self.root / "data/base.bin")
        with self.assertRaisesRegex(ValueError, "Foreign, replaced"):
            PREP.validate_memory(self.config)
        self.assertEqual(len(self.calls), 2)

    def test_completed_memory_hash_change_is_rejected(self):
        self.prepare()
        path = Path(str(self.prefix) + "_mem.index")
        with path.open("r+b") as stream:
            stream.seek(PREP.SECTOR)
            stream.write(b"\xff")
        with self.assertRaisesRegex(ValueError, "Frozen artifact changed"):
            PREP.validate_memory(self.config)
        self.assertEqual(len(self.calls), 2)

    def test_original_data_change_is_rejected_without_resampling(self):
        self.prepare()
        path = self.root / "data/base.bin"
        before = path.stat()
        with path.open("r+b") as stream:
            stream.seek(-1, os.SEEK_END)
            stream.write(b"\xff")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "identities changed"):
            PREP.validate_memory(self.config)
        self.assertEqual(len(self.calls), 2)


if __name__ == "__main__":
    unittest.main()
