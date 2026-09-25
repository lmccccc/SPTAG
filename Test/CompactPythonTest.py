"""Exercise the isolated Python module's native ownership and persistence ABI."""
import pathlib
import sys
import tempfile

import numpy as np

binary = pathlib.Path(sys.argv[1]).resolve()
parent = pathlib.Path(sys.argv[2]).resolve()
sys.path.insert(0, str(binary))
import SPTAG  # noqa: E402
import _SPTAG  # noqa: E402

assert pathlib.Path(_SPTAG.__file__).resolve().parent == binary
values = np.arange(64 * 128, dtype=np.float32).reshape(64, 128)
index = SPTAG.AnnIndex("BKT", "Float", 128)
index.SetBuildParam("NumberOfThreads", "1", "Index")
index.SetBuildParam("DistCalcMethod", "L2", "Index")
assert index.Build(values, len(values), False)
before = index.Search(values[7], 10)
with tempfile.TemporaryDirectory(prefix="compact-python-", dir=parent) as folder:
    assert index.Save(folder)
    restored = SPTAG.AnnIndex.Load(folder)
    assert restored is not None
    after = restored.Search(values[7], 10)
    assert len(before) == len(after)
    assert all(np.array_equal(left, right) for left, right in zip(before, after))
print("PASS isolated Python import and native save/load/search parity")
