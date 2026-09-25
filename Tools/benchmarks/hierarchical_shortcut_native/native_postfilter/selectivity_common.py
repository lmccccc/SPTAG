"""Bounded, read-only input validation for large native comparison campaigns."""
import ctypes
import os
from pathlib import Path
import sys

import numpy as np

from run_full import predicate_mask, require


def identity(path):
    path = Path(path)
    stat = path.stat()
    return dict(path=str(path.resolve()), device=stat.st_dev, inode=stat.st_ino,
                size=stat.st_size, mtime_ns=stat.st_mtime_ns, ctime_ns=stat.st_ctime_ns)


def inventory(root):
    root = Path(root)
    result, visited = {}, set()
    for directory, children, files in os.walk(root, followlinks=True):
        resolved = Path(directory).resolve()
        if resolved in visited:
            children.clear()
            continue
        visited.add(resolved)
        children.sort()
        for name in sorted(files):
            path = Path(directory) / name
            result[str(path.relative_to(root))] = identity(path)
    return result


def validate_identities(expected):
    for path, recorded in expected.items():
        require(identity(path) == recorded, f"Registered input identity changed: {path}")


def confine_outputs(root):
    """Use the preserved-head controller's Landlock policy for native children."""
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(444, 0, 0, 1)
    require(abi >= 3, f"Landlock ABI >=3 required, got {abi}")
    access = sum(1 << bit for bit in (1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14))
    rule_set = ctypes.c_uint64(access)
    descriptor = libc.syscall(444, ctypes.byref(rule_set), ctypes.sizeof(rule_set), 0)
    require(descriptor >= 0, f"Landlock create failed: errno={ctypes.get_errno()}")

    class Beneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("access", ctypes.c_uint64), ("parent", ctypes.c_int32)]

    directory = os.open(root, os.O_PATH | os.O_DIRECTORY)
    try:
        rule = Beneath(access, directory)
        require(libc.syscall(445, descriptor, 1, ctypes.byref(rule), 0) == 0,
                f"Landlock rule failed: errno={ctypes.get_errno()}")
        require(libc.prctl(38, 1, 0, 0, 0) == 0, "Cannot set no_new_privs")
        require(libc.syscall(446, descriptor, 0) == 0,
                f"Landlock restriction failed: errno={ctypes.get_errno()}")
    finally:
        os.close(directory)
        os.close(descriptor)


class SelectedPredicate:
    """Evaluate only returned IDs, never allocate a corpus-sized query mask."""

    def __init__(self, attributes, predicate):
        self.attributes = attributes
        self.predicate = predicate

    def __len__(self):
        return len(self.attributes)

    def __getitem__(self, ids):
        ids = np.asarray(ids)
        require(np.issubdtype(ids.dtype, np.integer), "Result IDs must be integers")
        require(np.all((ids >= 0) & (ids < len(self))), "Result ID outside corpus")
        if self.predicate is None:
            return np.ones(ids.shape, dtype=bool)
        return predicate_mask(self.predicate, self.attributes[ids])


def verify_truth(ids, distances, base, queries, selection):
    require(ids.ndim == 2 and ids.shape == (len(queries), 10), "Expected complete top10 truth")
    require(np.issubdtype(ids.dtype, np.integer) and np.all(ids >= 0), "Invalid truth IDs")
    require(np.all(ids < len(base)) and np.all(selection[ids.ravel()]), "Truth filter violation")
    require(all(len(set(row)) == 10 for row in ids), "Duplicate truth IDs")
    delta = base[ids].astype(np.int32) - queries[:, None, :].astype(np.int32)
    exact = np.einsum("qki,qki->qk", delta, delta, dtype=np.int64)
    require(np.all(exact[:, 1:] >= exact[:, :-1]), "Truth is not distance ordered")
    if distances is not None:
        require(distances.shape == ids.shape and np.array_equal(exact, distances),
                "Truth distance differs from exact UInt8 squared L2")
    return exact


if __name__ == "__main__":
    require(len(sys.argv) == 4, "Expected output directory, frozen executable and native INI")
    require(all(Path(value).is_absolute() for value in sys.argv[1:]), "Confinement paths must be absolute")
    confine_outputs(sys.argv[1])
    os.execv(sys.argv[2], sys.argv[2:])
