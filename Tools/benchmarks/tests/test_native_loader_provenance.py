"""Bounded proof-reader tests; never load a native ANN index."""

import copy
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import native_loader_provenance as loader
from official_benchmark_config import identity


class NativeLoaderProvenanceTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.original = self.root / "original"
        self.patched = self.root / "patched"
        for directory in (self.original, self.patched):
            (directory / "src").mkdir(parents=True)
            (directory / "include").mkdir()
            (directory / "include/pq_flash_index.h").write_bytes(b"unchanged header\n")
        original = b"\n".join(before + b";" for before, _ in loader.REPLACEMENTS) + b"\n"
        patched = original
        for before, after in loader.REPLACEMENTS:
            patched = patched.replace(before, after)
        (self.original / loader.SOURCE).write_bytes(original)
        (self.patched / loader.SOURCE).write_bytes(patched)

    def git_output(self, argv, **kwargs):
        if "rev-parse" in argv:
            return "base-revision\n"
        if "status" in argv:
            return b""
        if "ls-tree" in argv:
            return b"".join(
                f"100644 blob {'0' * 40}\t{name}\0".encode()
                for name in (loader.SOURCE, "include/pq_flash_index.h"))
        raise AssertionError(argv)

    def test_exact_two_delimiter_replacements_are_the_only_allowed_source_change(self):
        with mock.patch.object(loader.subprocess, "check_output", side_effect=self.git_output):
            records = loader.verify_source_delta(self.original, self.patched, "base-revision")
            self.assertEqual(4, len(records))
            with (self.patched / loader.SOURCE).open("ab") as stream:
                stream.write(b"unapproved search change\n")
            with self.assertRaisesRegex(ValueError, "two approved"):
                loader.verify_source_delta(self.original, self.patched, "base-revision")

    def test_other_source_or_header_changes_are_rejected(self):
        (self.patched / "include/pq_flash_index.h").write_bytes(b"changed header\n")
        with mock.patch.object(loader.subprocess, "check_output", side_effect=self.git_output):
            with self.assertRaisesRegex(ValueError, "other native source"):
                loader.verify_source_delta(self.original, self.patched, "base-revision")

    def test_archive_copy_cannot_introduce_untracked_sources_or_omit_inventory(self):
        with mock.patch.object(loader.subprocess, "check_output", side_effect=self.git_output):
            with self.assertRaisesRegex(ValueError, "inventory is incomplete"):
                loader.verify_source_delta(self.original, self.patched, "base-revision", [])
            (self.patched / "src/extra.cpp").write_bytes(b"not in the pinned Git tree\n")
            with self.assertRaisesRegex(ValueError, "source/header inventory"):
                loader.verify_source_delta(self.original, self.patched, "base-revision")

    def test_original_checkout_must_stay_clean(self):
        def output(argv, **kwargs):
            return b" M src/index.cpp\n" if "status" in argv else self.git_output(argv, **kwargs)

        with mock.patch.object(loader.subprocess, "check_output", side_effect=output):
            with self.assertRaisesRegex(ValueError, "remain unchanged"):
                loader.verify_source_delta(self.original, self.patched, "base-revision")

    def test_artifact_hash_path_and_size_are_authenticated(self):
        path = self.root / "library.a"
        path.write_bytes(b"bounded fixture, not an executable archive")
        record = identity(path, True)
        self.assertEqual(record, loader.artifact(record, path))
        for changed in ({**record, "sha256": "0" * 64}, {**record, "bytes": True}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                loader.artifact(changed, path)
        other = self.root / "other.a"
        other.write_bytes(path.read_bytes())
        with self.assertRaisesRegex(ValueError, "path changed"):
            loader.artifact(record, other)

    def make_archive(self, directory, parser, unrelated=b"same original member"):
        directory.mkdir()
        (directory / "pq_flash_index.cpp.o").write_bytes(parser)
        (directory / "distance.cpp.o").write_bytes(unrelated)
        archive = directory / "libdiskann.a"
        subprocess.run(["ar", "rcs", str(archive), str(directory / "pq_flash_index.cpp.o"),
                        str(directory / "distance.cpp.o")], check=True, capture_output=True)
        return archive

    def test_only_parser_archive_member_may_change(self):
        original = self.make_archive(self.root / "old-archive", b"old parser")
        patched = self.make_archive(self.root / "new-archive", b"new parser")
        self.assertEqual(["pq_flash_index.cpp.o"], loader.verify_archive_delta(original, patched))
        altered = self.make_archive(self.root / "other-archive", b"new parser", b"changed search")
        with self.assertRaisesRegex(ValueError, "unrelated native archive"):
            loader.verify_archive_delta(original, altered)
        with self.assertRaisesRegex(ValueError, "unrelated native archive"):
            loader.verify_archive_delta(original, original)

    def compile_link_fixture(self):
        build = self.root / "build"
        (build / "reference").mkdir(parents=True)
        stock_object = self.original / "build/apps/CMakeFiles/search_disk_index.dir/search_disk_index.cpp.o"
        stock_object.parent.mkdir(parents=True)
        stock_object.write_bytes(b"preserved stock search object")
        flags_path = self.original / "build/src/CMakeFiles/diskann.dir/flags.make"
        flags_path.parent.mkdir(parents=True)
        flags_path.write_text("CXX_DEFINES = -DNDEBUG\nCXX_INCLUDES = -I/original/include\nCXX_FLAGS = -O3\n")
        original_link = ["g++", "-O3", "CMakeFiles/search_disk_index.dir/search_disk_index.cpp.o",
                         "-o", "search_disk_index", "../src/libdiskann.a",
                         "../deps/usr/lib/x86_64-linux-gnu/libboost_program_options.so", "-lm"]
        stock_object.with_name("link.txt").write_text(shlex.join(original_link) + "\n")
        proof = {"original_source_tree": str(self.original), "patched_source_tree": str(self.patched),
                 "compiler": identity(Path(sys.executable).resolve(), True),
                 "stock_search_object": identity(stock_object, True),
                 "original_stock_link_command": original_link, "installed_runpath": "/original/runtime"}
        flags = ["-DNDEBUG", "-I/original/include", "-O3"]
        prefix_map = f"-ffile-prefix-map={self.patched}={self.original}"
        proof["flags"] = {"original_flags_make": identity(flags_path, True), "original": flags,
                          "isolated_path_only_addition": prefix_map}
        for key, path, payload in (
            ("reference_object", build / "reference/pq_flash_index.cpp.o", b"original parser"),
            ("patched_object", build / "pq_flash_index.cpp.o", b"fixed parser"),
            ("original_stock_binary", self.root / "original-search", b"original stock"),
            ("reference_stock", build / "reference/search_disk_index", b"original stock"),
            ("patched_stock_binary", self.root / "fixed-search", b"fixed stock"),
        ):
            path.write_bytes(payload)
            proof[key] = identity(path, True)
        proof["original_library"] = identity(
            self.make_archive(self.root / "original-library", b"original parser"), True)
        proof["patched_library"] = identity(
            self.make_archive(self.root / "patched-library", b"fixed parser"), True)
        commands = {}
        for name, key, tree, additions in (
            ("reference-object", "reference_object", self.original, []),
            ("patched-object", "patched_object", self.patched, [prefix_map]),
        ):
            output = proof[key]["path"]
            commands[name] = [proof["compiler"]["path"], *flags, *additions, "-MD", "-MT",
                              "src/CMakeFiles/diskann.dir/pq_flash_index.cpp.o", "-MF", output + ".d",
                              "-o", output, "-c", str(tree / loader.SOURCE)]
        for name, library_key, output_key in (
            ("reference-stock", "original_library", "reference_stock"),
            ("patched-stock", "patched_library", "patched_stock_binary"),
        ):
            commands[name] = [
                proof["compiler"]["path"], "-O3", str(stock_object), "-o", proof[output_key]["path"],
                proof[library_key]["path"],
                str(self.original / "build/deps/usr/lib/x86_64-linux-gnu/libboost_program_options.so"), "-lm"]
        for name in ("replace-one-member", "archive-index",
                     "reference-stock-install-rpath", "patched-stock-install-rpath"):
            commands[name] = ["bounded-proof-fixture-not-executed"]
        proof["commands"] = [{"name": name, "cwd": str(build), "argv": argv} for name, argv in commands.items()]
        return proof

    def test_compile_link_preserves_original_flags_objects_and_runtime_path(self):
        proof = self.compile_link_fixture()
        actual_output = subprocess.check_output

        def output(argv, **kwargs):
            return ("  (RUNPATH) Library runpath: [/original/runtime]\n"
                    if argv[0] == "readelf" else actual_output(argv, **kwargs))

        with mock.patch.object(loader.subprocess, "check_output", side_effect=output):
            self.assertEqual(6, len(loader.verify_compile_link(proof)))
            mutations = (
                ("patched-object", "-Ofast", "compile argv"),
                ("reference-object", "-O2", "compile argv"),
                ("patched-stock", "-static", "stock link argv"),
                ("reference-stock", "-flto", "stock link argv"),
            )
            for name, flag, message in mutations:
                changed = copy.deepcopy(proof)
                next(entry for entry in changed["commands"] if entry["name"] == name)["argv"].append(flag)
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, message):
                    loader.verify_compile_link(changed)
            changed = copy.deepcopy(proof)
            changed["flags"]["isolated_path_only_addition"] += "/wrong"
            with self.assertRaisesRegex(ValueError, "compiler flags"):
                loader.verify_compile_link(changed)
            changed = copy.deepcopy(proof)
            changed["stock_search_object"] = changed["patched_object"]
            with self.assertRaisesRegex(ValueError, "preserved original object"):
                loader.verify_compile_link(changed)
            changed = copy.deepcopy(proof)
            changed["reference_object"] = changed["patched_object"]
            with self.assertRaisesRegex(ValueError, "compile argv"):
                loader.verify_compile_link(changed)
            changed = copy.deepcopy(proof)
            changed["original_stock_binary"] = changed["patched_stock_binary"]
            with self.assertRaisesRegex(ValueError, "byte-for-byte"):
                loader.verify_compile_link(changed)
            changed = copy.deepcopy(proof)
            changed["installed_runpath"] = "/replacement/runtime"
            with self.assertRaisesRegex(ValueError, "runtime search path"):
                loader.verify_compile_link(changed)


if __name__ == "__main__":
    unittest.main()
