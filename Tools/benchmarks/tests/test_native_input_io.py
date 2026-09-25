"""Tiny on-repository fixtures for native preparation input contracts."""

import configparser
from pathlib import Path
import shutil
import struct
import sys
import unittest
import uuid

import numpy as np


BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS))
from native_input_io import column_types, open_attributes, open_vectors, read_config


class NativeInputTest(unittest.TestCase):
    def setUp(self):
        directory = Path("build") / f"native_input_io_test_{uuid.uuid4().hex}"
        directory.mkdir(parents=True)
        self.root = directory.resolve()
        self.addCleanup(shutil.rmtree, directory)

    def vectors(self, dtype="<f4", rows=7, dimension=3):
        values = np.arange(rows * dimension, dtype=dtype).reshape(rows, dimension)
        path = self.root / "arbitrary-vector-name.data"
        path.write_bytes(struct.pack("<ii", rows, dimension) + values.tobytes())
        return path, values

    def test_all_native_types_are_readonly_and_dimension_independent(self):
        for value_type, dtype, dimension in (
            ("Float", "<f4", 3), ("Int8", "i1", 1),
            ("UInt8", "u1", 11), ("Int16", "<i2", 5),
        ):
            with self.subTest(value_type=value_type):
                path, values = self.vectors(dtype, dimension=dimension)
                original = path.read_bytes()
                opened = open_vectors(path, value_type.swapcase())
                self.assertEqual(opened.path, path.resolve())
                self.assertEqual(opened.value_type, value_type)
                self.assertEqual(opened.source_rows, len(values))
                self.assertEqual(opened.dimension, dimension)
                self.assertEqual(opened.data.dtype, np.dtype(dtype))
                self.assertFalse(opened.data.flags.writeable)
                np.testing.assert_array_equal(opened.data, values)
                with self.assertRaises(ValueError):
                    opened.data[0, 0] = 100
                self.assertEqual(path.read_bytes(), original)
                del opened

    def test_prefix_retains_validated_full_source_extent(self):
        path, values = self.vectors("<i2", rows=9, dimension=4)
        opened = open_vectors(path, "Int16", dimension=4, limit=3)
        self.assertEqual(opened.source_rows, 9)
        self.assertEqual(len(opened.data), 3)
        np.testing.assert_array_equal(opened.data, values[:3])
        del opened
        original = path.read_bytes()
        for payload in (original[:-1], original + b"\0", original[:8 + 3 * 4 * 2]):
            with self.subTest(bytes=len(payload)):
                path.write_bytes(payload)
                with self.assertRaisesRegex(ValueError, "exactly"):
                    open_vectors(path, "Int16", limit=3)

    def test_bad_headers_shapes_and_value_types(self):
        path = self.root / "invalid.data"
        headers = (
            b"", b"\0" * 7,
            struct.pack("<ii", 0, 3), struct.pack("<ii", -1, 3),
            struct.pack("<ii", 2, 0), struct.pack("<ii", 2, -1),
            struct.pack("<ii", 1, 2**31 - 1),
        )
        for payload in headers:
            with self.subTest(header=payload):
                path.write_bytes(payload)
                with self.assertRaises(ValueError):
                    open_vectors(path, "Float")
        path, _ = self.vectors()
        for value_type in ("float64", "RAW", "", None):
            with self.subTest(value_type=value_type), self.assertRaisesRegex(ValueError, "ValueType"):
                open_vectors(path, value_type)
        with self.assertRaisesRegex(ValueError, "header/ValueType"):
            open_vectors(path, "UInt8")

    def test_invalid_dimensions_and_prefixes(self):
        path, _ = self.vectors()
        for dimension in (0, -1, 2, 2**31, True, 3.0, "3"):
            with self.subTest(dimension=dimension), self.assertRaises(ValueError):
                open_vectors(path, "Float", dimension=dimension)
        for limit in (-2, 0, 8, 2**31, True, 1.5, "2"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                open_vectors(path, "Float", limit=limit)
        opened = open_vectors(path, "Float", limit=np.int32(2))
        self.assertEqual(len(opened.data), 2)

    def test_attributes_allow_only_selected_or_full_source(self):
        path = self.root / "attributes.arbitrary"
        values = np.arange(21, dtype="<u4").reshape(7, 3)
        path.write_bytes(values.tobytes())
        original = path.read_bytes()
        data = open_attributes(path, 3, 3, source_rows=7)
        self.assertEqual(data.shape, (3, 3))
        self.assertEqual(data.dtype, np.dtype("<u4"))
        self.assertEqual(data.offset, 0)
        self.assertFalse(data.flags.writeable)
        np.testing.assert_array_equal(data, values[:3])
        with self.assertRaises(ValueError):
            data[0, 0] = 9
        self.assertEqual(path.read_bytes(), original)
        del data
        path.write_bytes(values[:3].tobytes())
        data = open_attributes(path, 3, 3, source_rows=7)
        np.testing.assert_array_equal(data, values[:3])
        del data
        for payload in (
            original[:-1], original + b"\0", values[:4].tobytes(),
            struct.pack("<ii", 7, 3) + original, b"",
        ):
            with self.subTest(size=len(payload)):
                path.write_bytes(payload)
                with self.assertRaisesRegex(ValueError, "headerless uint32"):
                    open_attributes(path, 3, 3, source_rows=7)
        path.write_bytes(original)
        with self.assertRaises(ValueError):
            open_attributes(path, 3, 3)
        with self.assertRaises(ValueError):
            open_attributes(path, 3, 3, source_rows=8)
        npy_path = self.root / "not-raw.npy"
        np.save(npy_path, values)
        with self.assertRaises(ValueError):
            open_attributes(npy_path, 7, 3)

    def test_attribute_shape_is_explicit_and_positive(self):
        path = self.root / "attributes"
        path.write_bytes(np.zeros((5, 2), dtype="<u4").tobytes())
        for count, columns, source in (
            (0, 2, None), (-1, 2, None), (5, 0, None), (5, -2, None),
            (5, 2**31, None), (5, 2, 4), (5, 2, 0), (5, 2, 2**31),
            (5, ("categorical", "numeric"), None), (5.0, 2, None),
            (5, True, None),
        ):
            with self.subTest(count=count, columns=columns, source=source):
                with self.assertRaises(ValueError):
                    open_attributes(path, count, columns, source)

    def config(self, text):
        path = self.root / "build.ini"
        path.write_text(text, encoding="utf-8")
        return read_config(path)

    def test_config_canonicalizes_without_opening_any_inputs(self):
        config = self.config(
            "; only native whole-line comments\n"
            " [ BaSe ] \nVectorPath=absent.native\nValueType=Float\n"
            "QueryPath=also-absent\nLiteral=%(name)s ${ROOT} ~ ; retained\n"
            "[TAGS]\nTagFile=absent.attributes\nColumnTypes=CaTe,NuM\n"
        )
        self.assertEqual(set(config), {"base", "tags"})
        self.assertEqual(config["base"]["vectorpath"], "absent.native")
        self.assertEqual(config["base"]["literal"], "%(name)s ${ROOT} ~ ; retained")
        self.assertEqual(column_types(config), ("categorical", "numeric"))

    def test_default_section_does_not_invent_inherited_native_settings(self):
        config = self.config("[DEFAULT]\nValueType=UInt8\n[Base]\nVectorPath=no-file\n")
        self.assertEqual(config["default"], {"valuetype": "UInt8"})
        self.assertNotIn("valuetype", config["base"])

    def test_duplicate_sections_and_options_are_explicit_errors(self):
        for text in (
            "[Tags]\nColumnTypes=numeric\n[tags]\nTagFile=x\n",
            "[Tags]\nColumnTypes=numeric\n[ TAGS ]\nTagFile=x\n",
        ):
            with self.subTest(text=text), self.assertRaises(configparser.DuplicateSectionError):
                self.config(text)
        with self.assertRaises(configparser.DuplicateOptionError):
            self.config("[Base]\nVectorPath=x\nvectorpath=y\n")

    def test_non_native_comments_multiline_and_invalid_syntax_fail(self):
        for text in (
            "# not a native comment\n[Base]\nValueType=Float\n",
            "[Base] ; no inline comments\nValueType=Float\n",
            "[ ]\nx=y\n", "[Base\nx=y\n", "[Base]\n=bad\n",
            "[Base]\nDim:3\n", "[Base]\nValueType=Float\n continuation\n",
        ):
            with self.subTest(text=text), self.assertRaises(configparser.Error):
                self.config(text)

    def test_column_schema_preserves_original_order_and_optional_width(self):
        self.assertEqual(column_types({"tags": {
            "columntypes": "NUM, cate, categorical, numeric", "numtagspervec": "4",
        }}), ("numeric", "categorical", "categorical", "numeric"))
        for tags in (
            {}, {"columntypes": ""}, {"columntypes": " "},
            {"columntypes": "categorical,"}, {"columntypes": "text,numeric"},
            {"columntypes": "categorical,numeric", "numtagspervec": "1"},
            {"numtagspervec": "2"},
        ):
            with self.subTest(tags=tags), self.assertRaises(ValueError):
                column_types({"tags": tags})


if __name__ == "__main__":
    unittest.main()
