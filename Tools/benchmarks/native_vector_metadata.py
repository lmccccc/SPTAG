"""Header/stat-only DEFAULT metadata for offline fixture sizing (never reads payloads)."""

import struct
from pathlib import Path


def default_vector_shape(config):
    base = config["Base"]
    if "VectorOffset" in base or "VectorCount" in base:
        raise ValueError("VectorOffset/VectorCount were removed; use native VectorSize for a prefix")
    if base.get("VectorType", "DEFAULT").upper() != "DEFAULT":
        raise ValueError("this fixture-sizing tool requires native DEFAULT input")
    sizes = {"int8": 1, "uint8": 1, "int16": 2, "float": 4}
    value_size = sizes.get(base.get("ValueType", "Float").lower())
    if value_size is None:
        raise ValueError("invalid ValueType")
    path = Path(base["VectorPath"])
    with path.open("rb") as source:
        header = source.read(8)
        size = source.seek(0, 2)
    if len(header) != 8:
        raise ValueError("truncated DEFAULT header")
    rows, cols = struct.unpack("<ii", header)
    if rows <= 0 or cols <= 0 or cols * value_size > 2147483647:
        raise ValueError("invalid DEFAULT dimensions")
    if size != 8 + rows * cols * value_size:
        raise ValueError("DEFAULT header/ValueType disagrees with file size")
    dimension = int(base.get("Dim", "0"))
    if dimension not in (0, cols):
        raise ValueError("Dim disagrees with DEFAULT header")
    limit = int(base.get("VectorSize", "-1"))
    if limit == 0 or limit < -1 or limit > 2147483647:
        raise ValueError("VectorSize must be -1 or a positive SizeType")
    return (min(rows, limit) if limit > 0 else rows), cols


if __name__ == "__main__":
    import configparser
    import sys
    parser = configparser.ConfigParser(interpolation=None)
    with open(sys.argv[1]) as stream:
        parser.read_file(stream)
    print(*default_vector_shape(parser))
