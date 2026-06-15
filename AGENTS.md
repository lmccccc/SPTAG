# SPTAG Agent Navigation

This file is a navigation map for coding agents working in this repository.

## Scope
- Repository: `SPTAG`
- Primary language: C++ (core), SWIG wrappers (Python/Java/C#), Python packaging
- Build systems: CMake + `setup.py`

## High-Level Architecture
- Core ANN library: `AnnService/inc/Core/**`, `AnnService/src/Core/**`
- Service binaries: `AnnService/src/{Server,Client,Aggregator,IndexBuilder,IndexSearcher,...}`
- Wrappers and bridge layer: `Wrappers/inc/**`, `Wrappers/src/**`
- Python package output: `sptag/`

## First Files To Read
- Project overview: `README.md`
- Top-level build config: `CMakeLists.txt`
- Core build targets: `AnnService/CMakeLists.txt`
- Wrapper build and SWIG invocation: `Wrappers/CMakeLists.txt`
- Python packaging: `setup.py`

## Core Code Map
- Abstract index interface: `AnnService/inc/Core/VectorIndex.h`
- Core index orchestration (build/load/save): `AnnService/src/Core/VectorIndex.cpp`
- Search query/result types: `AnnService/inc/Core/SearchQuery.h`, `AnnService/inc/Core/SearchResult.h`
- Algorithm implementations:
  - BKT: `AnnService/inc/Core/BKT/**`, `AnnService/src/Core/BKT/**`
  - KDT: `AnnService/inc/Core/KDT/**`, `AnnService/src/Core/KDT/**`
  - SPANN: `AnnService/inc/Core/SPANN/**`, `AnnService/src/Core/SPANN/**`

## IO and Cache Ownership
- Core Disk IO abstraction and load/save path live in core, not wrappers.
- SPANN file IO and cache implementation:
  - `AnnService/inc/Core/SPANN/ExtraFileController.h`
  - `AnnService/src/Core/SPANN/SPANNIndex.cpp`
- SPANN cache tuning params:
  - `AnnService/inc/Core/SPANN/ParameterDefinitionList.h`
  - Important keys: `CacheSizeGB`, `CacheShards`

## Wrapper Layer Map
- Main wrapper API surface:
  - Header: `Wrappers/inc/CoreInterface.h`
  - Impl: `Wrappers/src/CoreInterface.cpp`
- Python SWIG interface:
  - `Wrappers/inc/PythonCore.i`
  - SWIG-generated outputs (do not hand-edit, absent in a fresh checkout, produced by the build):
    `Wrappers/inc/CoreInterface_pwrap.cpp`, `Wrappers/inc/SPTAG.py`, `Wrappers/inc/SPTAGClient.py`

## Build/Save Responsibilities (Quick)
- Wrapper entry for build/save:
  - `Wrappers/src/CoreInterface.cpp` (`AnnIndex::Build*`, `AnnIndex::Save`)
- Actual core build/save implementation:
  - `AnnService/src/Core/VectorIndex.cpp` (`VectorIndex::BuildIndex`, `VectorIndex::SaveIndex`, `VectorIndex::LoadIndex`)

## Service Entrypoints
- Server: `AnnService/src/Server/**`
- Client: `AnnService/src/Client/**`
- Aggregator: `AnnService/src/Aggregator/**`
- Offline builder/search tools: `AnnService/src/IndexBuilder/**`, `AnnService/src/IndexSearcher/**`
- SSD/SPFresh tools: `AnnService/src/SSDServing/**`, `AnnService/src/SPFresh/**`

## Typical Task Routing
- Add/modify search algorithm behavior:
  - Start in `AnnService/inc/Core/VectorIndex.h` and target algorithm folder (`BKT/KDT/SPANN`).
- Modify metadata filtering behavior:
  - Check `VectorIndex` interface and SPANN/BKT/KDT implementations.
  - Wrapper exposure in `Wrappers/inc/CoreInterface.h` and `Wrappers/inc/PythonCore.i`.
- Add Python API:
  - Update `Wrappers/inc/CoreInterface.h/.cpp` and `Wrappers/inc/PythonCore.i`.
  - Rebuild SWIG wrapper and `_SPTAG` module.
- Investigate IO/cache:
  - Use `AnnService/inc/Core/SPANN/ExtraFileController.h` and SPANN options.

## Build Commands (Linux)
- Submodules first (required — the build aborts at `add_subdirectory(ThirdParty/zstd/build/cmake)` otherwise):
```bash
git submodule update --init ThirdParty/zstd
# SPDK/RocksDB submodules are only needed for -DSPDK=ON / -DROCKSDB=ON builds
```
- CMake build:
```bash
mkdir -p build && cd build
cmake -DSPDK=OFF -DROCKSDB=OFF ..
make -j
```
- Build only the core static lib (fast compile check for `AnnService/**` and SPANN headers):
```bash
make -j SPTAGLibStatic
```
- Python wrapper build in place:
```bash
python setup.py build_ext --inplace
```

## Agent Guardrails
- Prefer editing source files, not generated SWIG outputs.
- Avoid changing third-party dependencies under `ThirdParty/` unless task explicitly requires it.
- If modifying wrapper APIs, verify both C++ compile and Python import path.
- Keep cache policy in core SPANN IO controller (`ExtraFileController`) instead of duplicating policy in wrappers.

## Current Repo Notes
- Multi-tenant wrapper logic is in `Wrappers/src/CoreInterface.cpp` (`TenantIndexManager`).
- Keep tenant routing in wrapper, but rely on core IO/cache infrastructure for cache policy.
- Shared-RocksDB-across-tenants IO helper: `AnnService/inc/Helper/TenantPrefixedKeyValueIO.h`
  (prefixes keys per tenant so one RocksDB instance backs many tenants).
- Dataset destructor safety patch location:
  - `AnnService/inc/Core/Common/Dataset.h`

## Experimental Branch Features (`archive/unfiltered-head-filtered-tail`)
This is a research/ablation branch. Several filtered-ANN experiments are layered on top of stock SPANN:
- Per-tag / tag-pure head selection and metadata filtering, sparse-tag flat-scan posting sidecar,
  PerTag BKT + greedy K-way merge, Huffman best-of, cross-subgraph head shortcut edges,
  adaptive/unified nprobe routing, independent unfiltered-tail buffer.
  Most live in `Wrappers/src/CoreInterface.cpp` (build/route side) and
  `AnnService/src/Core/SPANN/SPANNIndex.cpp` + `AnnService/inc/Core/SPANN/ExtraDynamicSearcher.h` (search/build side).
- These behaviors are gated by ~30 `SPTAG_*` environment variables read via `getenv` (ablation knobs),
  e.g. `SPTAG_UNFILTER_TAIL*`, `SPTAG_TAG_PURE_THRESHOLD`, `SPTAG_DISABLE_TAG_PURE_PATH`,
  `SPTAG_DISABLE_SPARSE_PATH`, `SPTAG_SPARSE_MAX_POSTINGS`, `SPTAG_PERTAG_*`, `SPTAG_MERGE_*`,
  `SPTAG_*_OVERRIDE`, `SPTAG_UNIFIED_NPROBE_BUDGET`, `SPTAG_FIXED_NPROBE`, `SPTAG_DISABLE_CROSS_EDGES`,
  and diagnostic toggles `SPTAG_LOG_*` / `SPTAG_DUMP_HEADS`.
  To enumerate them: `grep -rn 'getenv("SPTAG_' AnnService Wrappers`.
  Treat these as intentional experiment infrastructure — do not remove without confirming the ablation is retired.

## Benchmark / Tooling Notes
- Multi-tenant benchmark scripts live in `Tools/benchmarks/` (stress, groundtruth, selectivity-latency, pivot planning).
- Their default data/output paths are driven by env vars, not hardcoded user paths:
  `SPTAG_BENCH_ROOT` (default `~/sptag_bench`) and `SPTAG_BENCH_QUERY_FILE`;
  the stress runner also honors `SPTAG_STRESS_*` overrides.

## Agent Guardrails (cruft)
- Do not re-introduce build/editor cruft that was removed and is `.gitignore`d:
  `SPTAG.sdf` (`*.sdf`), `.venv/`, and ad-hoc `tmp_*` debug scripts.
- Avoid committing machine-specific absolute paths; prefer the `SPTAG_BENCH_ROOT` / `SPTAG_*` env conventions above.
