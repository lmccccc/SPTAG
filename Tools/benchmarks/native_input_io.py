"""Strict, read-only native INI, DEFAULT vector and raw attribute inputs.

Paths have native working-directory semantics; no environment or home expansion
is performed. DEFAULT vectors carry two little-endian int32 header fields.
Attribute inputs are explicitly headerless little-endian uint32 at byte zero.
"""

import configparser
from dataclasses import dataclass
import operator
import os
from pathlib import Path
import struct

import numpy as np

from validate_spann_hierarchy_config import column_types as _column_types


SIZE_TYPE_MAX = 2**31 - 1
VALUE_TYPES = {
    "float": ("Float", np.dtype("<f4")),
    "int8": ("Int8", np.dtype("i1")),
    "uint8": ("UInt8", np.dtype("u1")),
    "int16": ("Int16", np.dtype("<i2")),
}


def read_config(path) -> dict[str, dict[str, str]]:
    """Read native, case-insensitive INI without interpolation or defaults.

    Only whole-line semicolon comments are ignored. Unlike ConfigParser's
    default dialect, native INI has neither multiline values nor inheritance
    from a special DEFAULT section.
    """
    result: dict[str, dict[str, str]] = {}
    section = ""
    source = str(path)
    with Path(path).open("r", encoding="utf-8") as stream:
        for lineno, original in enumerate(stream, 1):
            line = original.strip()
            if not line or line.startswith(";"):
                continue
            if line.startswith("["):
                if not line.endswith("]") or not line[1:-1].strip():
                    error = configparser.ParsingError(source)
                    error.append(lineno, original.rstrip("\n"))
                    raise error
                section = line[1:-1].strip().lower()
                if section in result:
                    raise configparser.DuplicateSectionError(section, source, lineno)
                result[section] = {}
                continue
            key, separator, value = line.partition("=")
            if not separator or not key.strip():
                error = configparser.ParsingError(source)
                error.append(lineno, original.rstrip("\n"))
                raise error
            key = key.strip().lower()
            values = result.setdefault(section, {})
            if key in values:
                raise configparser.DuplicateOptionError(section, key, source, lineno)
            values[key] = value.strip()
    return result


def column_types(config) -> tuple[str, ...]:
    """Require the explicit, original-column Tags.ColumnTypes schema."""
    types = tuple(_column_types(config))
    if not types:
        raise ValueError("[Tags] ColumnTypes must declare a nonempty attribute schema")
    return types


def _integer(value, name: str) -> int:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{name} must be an integer")
    try:
        return operator.index(value)
    except TypeError as error:
        raise ValueError(f"{name} must be an integer") from error


def _positive_size(value, name: str) -> int:
    value = _integer(value, name)
    if not 0 < value <= SIZE_TYPE_MAX:
        raise ValueError(f"{name} must be a positive native SizeType")
    return value


@dataclass(frozen=True)
class NativeVectors:
    path: Path
    value_type: str
    source_rows: int
    dimension: int
    data: np.memmap


def open_vectors(path, value_type, dimension=None, limit=-1) -> NativeVectors:
    """Map a validated DEFAULT vector source or positive prefix read-only."""
    try:
        canonical_type, dtype = VALUE_TYPES[value_type.strip().lower()]
    except (AttributeError, KeyError) as error:
        raise ValueError("ValueType must be Float, Int8, UInt8 or Int16") from error
    limit = _integer(limit, "VectorSize")
    if limit != -1 and not 0 < limit <= SIZE_TYPE_MAX:
        raise ValueError("VectorSize must be -1 or a positive native SizeType prefix")
    if dimension is not None:
        dimension = _positive_size(dimension, "Dim")
    path = Path(path).resolve()
    with path.open("rb") as stream:
        header = stream.read(8)
        if len(header) != 8:
            raise ValueError(f"{path}: truncated DEFAULT vector header")
        rows, cols = struct.unpack("<ii", header)
        _positive_size(rows, "DEFAULT row count")
        _positive_size(cols, "DEFAULT dimension")
        if cols * dtype.itemsize > SIZE_TYPE_MAX:
            raise ValueError(f"{path}: DEFAULT row bytes exceed native SizeType")
        if dimension is not None and dimension != cols:
            raise ValueError(f"{path}: Dim={dimension} disagrees with DEFAULT dimension {cols}")
        expected = 8 + rows * cols * dtype.itemsize
        actual = os.fstat(stream.fileno()).st_size
        if actual != expected:
            raise ValueError(
                f"{path}: DEFAULT header/ValueType requires exactly {expected} bytes, got {actual}"
            )
        if limit > rows:
            raise ValueError(f"{path}: VectorSize={limit} exceeds source row count {rows}")
        count = rows if limit == -1 else limit
        data = np.memmap(stream, mode="r", dtype=dtype, offset=8, shape=(count, cols))
    return NativeVectors(path, canonical_type, rows, cols, data)


def open_attributes(path, count, columns, source_rows=None) -> np.memmap:
    """Map count rows of explicit uint32 attributes; columns is an integer width.

    The exact file extent may describe the selected count or the full native
    source_rows, but no other trailing rows, offsets or headers are inferred.
    """
    count = _positive_size(count, "Attribute row count")
    columns = _positive_size(columns, "Attribute column count")
    if columns > SIZE_TYPE_MAX // 4:
        raise ValueError("Attribute row bytes exceed native SizeType")
    allowed = {count * columns * 4}
    if source_rows is not None:
        source_rows = _positive_size(source_rows, "Source row count")
        if source_rows < count:
            raise ValueError("Source row count cannot be smaller than the selected count")
        allowed.add(source_rows * columns * 4)
    path = Path(path).resolve()
    with path.open("rb") as stream:
        actual = os.fstat(stream.fileno()).st_size
        if actual not in allowed:
            raise ValueError(
                f"{path}: headerless uint32 attributes require exactly "
                f"{' or '.join(str(size) for size in sorted(allowed))} bytes, got {actual}"
            )
        return np.memmap(stream, mode="r", dtype="<u4", offset=0, shape=(count, columns))
