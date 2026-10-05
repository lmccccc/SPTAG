"""Bounded proof-reader contracts; ordinary discovery never loads a native index."""

import difflib
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from threeway_native import pipeann_page_lifetime as repair


class PageLifetimeProofTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.original, self.patched = self.root / "original", self.root / "patched"
        self.patch_root = self.root / "reader"
        self.patch_root.mkdir()
        for directory, text in ((self.original, "old lifetime\n"), (self.patched, "owned lifetime\n")):
            (directory / repair.HEADER).parent.mkdir(parents=True)
            (directory / repair.HEADER).write_text(text)
            (directory / "unchanged.h").write_text("preserved native header\n")
        difference = "".join(difflib.unified_diff(
            (self.original / repair.HEADER).read_text().splitlines(True),
            (self.patched / repair.HEADER).read_text().splitlines(True),
            fromfile="a/" + repair.HEADER, tofile="b/" + repair.HEADER))
        (self.patch_root / "pipeann_page_lifetime.patch").write_text(difference)

    def test_exact_header_patch_and_all_other_sources_are_authenticated(self):
        with mock.patch.object(repair, "HERE", self.patch_root):
            records = repair.source_delta(self.original, self.patched)
            self.assertEqual(2, len(records))
            for extra in ("extra.cpp", "unchanged.h"):
                path = self.patched / extra
                previous = path.read_bytes() if path.exists() else None
                path.write_bytes(b"unexpected native code\n")
                with self.assertRaises(ValueError):
                    repair.source_delta(self.original, self.patched)
                if previous is None:
                    path.unlink()
                else:
                    path.write_bytes(previous)

    def test_modified_patch_and_symlink_are_rejected(self):
        with mock.patch.object(repair, "HERE", self.patch_root):
            header = self.patched / repair.HEADER
            header.write_text(header.read_text() + "additional policy change\n")
            with self.assertRaisesRegex(ValueError, "approved page-lifetime-only patch"):
                repair.source_delta(self.original, self.patched)
        link = self.patched / "escape.h"
        link.symlink_to(self.original / "unchanged.h")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            repair.source_files(self.patched)

    def test_compiler_projection_preserves_native_flags(self):
        source = self.original / "src/search/pipe_search.cpp"
        template = {"command": shlex.join([
            "/usr/bin/c++", "-O3", "-DNDEBUG", "-DREAD_ONLY_TESTS", "-DNO_MAPPING",
            "-I" + str(self.original / "include"), "-g", "-o", "old-object.o", "-c", str(source)])}
        output = self.root / "new-object.o"
        reference = repair.compile_command(template, self.original, self.original, output)
        expected = shlex.split(template["command"])
        expected[expected.index("-o") + 1] = str(output)
        self.assertEqual(expected, reference)
        corrected = repair.compile_command(template, self.original, self.patched, output)
        self.assertEqual("-ffile-prefix-map=" + str(self.patched) + "=" + str(self.original), corrected[1])
        self.assertEqual(reference, [corrected[0], *[
            value.replace(str(self.patched), str(self.original)) for value in corrected[2:]]])


if __name__ == "__main__":
    unittest.main()
