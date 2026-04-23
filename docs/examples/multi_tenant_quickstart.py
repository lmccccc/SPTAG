"""Multi-tenant SPANN quickstart example (no tag / ACL filtering).

Builds one independent SPANN index per tenant from a single numpy vector block
plus a per-row tenant id array, saves the bundle to disk, reloads it under a
bounded HeadIndex cache budget, and runs a top-k search for one tenant.

Requirements:
  - The sptag Python package must be built and importable (see README's
    "Install" section for Linux build instructions).
  - numpy.

Run:
  python docs/examples/multi_tenant_quickstart.py

This script is self-contained; it generates random vectors so it runs without
any external dataset.
"""
from __future__ import annotations

import os
import shutil
import tempfile

import numpy as np

from sptag import SPTAG


def main() -> None:
    dim = 128
    num_tenants = 4
    vectors_per_tenant = 2_000
    topk = 10

    rng = np.random.default_rng(0)

    # One flat (N, dim) vector block + one (N,) tenant id array.
    # TenantIndexManager.BuildFromNumpy will split the block per tenant
    # internally and build one SPANN index per tenant.
    vectors = rng.standard_normal(
        (num_tenants * vectors_per_tenant, dim), dtype=np.float32
    )
    tenant_ids = np.repeat(np.arange(num_tenants, dtype=np.int32),
                           vectors_per_tenant)

    workdir = tempfile.mkdtemp(prefix="sptag_multitenant_")
    index_dir = os.path.join(workdir, "tenant_index")
    try:
        print(f"[1/4] Building {num_tenants} tenants "
              f"({vectors_per_tenant} vectors each)...")
        mgr = SPTAG.CreateTenantIndexManager(dim, "SPANN", "Float")

        # SPANN params are shared across tenants; tune here if needed.
        mgr.SetBuildParam("IndexAlgoType", "BKT", "Base")
        mgr.SetBuildParam("DistCalcMethod", "L2", "Base")

        ok = mgr.BuildFromNumpy(vectors, tenant_ids,
                                with_meta_index=True, normalized=False)
        assert ok, "BuildFromNumpy failed"
        print(f"       total tenants registered: {mgr.GetTenantCount()}")

        print(f"[2/4] Saving to {index_dir}")
        assert mgr.SaveAll(index_dir), "SaveAll failed"

        print("[3/4] Reloading under a 64 MB HeadIndex cache budget")
        mgr2 = SPTAG.CreateTenantIndexManager(dim, "SPANN", "Float")
        assert mgr2.LoadAll(index_dir), "LoadAll failed"
        mgr2.SetHeadIndexCacheLimit(64 * 1024 * 1024)

        print(f"[4/4] Searching tenant 0 for top-{topk}")
        query = vectors[0]                       # query by first vector of tenant 0
        result = mgr2.Search(query, 0, topk)
        ids = [result.GetResult(i).VID for i in range(result.GetResultNum())]
        dists = [result.GetResult(i).Dist for i in range(result.GetResultNum())]
        print(f"       ids  : {ids}")
        print(f"       dists: {[round(d, 4) for d in dists]}")
        print(f"       cache in use: "
              f"{mgr2.GetHeadIndexCacheUsage() / 1024:.1f} KB")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
