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
  - Generated file (do not hand-edit): `Wrappers/inc/CoreInterface_pwrap.cpp`
- Python module stubs:
  - `Wrappers/inc/SPTAG.py`, `Wrappers/inc/SPTAGClient.py`

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
- CMake build:
```bash
mkdir -p build && cd build
cmake -DSPDK=OFF -DROCKSDB=OFF ..
make -j
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
- Dataset destructor safety patch location:
  - `AnnService/inc/Core/Common/Dataset.h`

## Unfilter Enhancement Pipeline (DO NOT DROP)
Current attribute SPANN uses one global spatial H1..H5 hierarchy. Attribute
pivot planning, per-tag head selection, grouping files and tag-to-bundle routing
are removed; removed INI/environment interfaces fail explicitly. Do not restore
them. Keep exact categorical/numeric filtering, support assignments, H/O
regions, signatures and signed spatial CSR. Generic physical bundle APIs and
their cross-graph/unfilter-tail machinery remain for geometry and upstream CRUD,
not attribute organization. U_extra remains optional and defaults OFF.

| Layer | What it builds | How to enable | Code |
| ----- | -------------- | ------------- | ---- |
| ① cross-graph | `head_cross_edges.bin` stitching the bundle nodes | existing `CrossEdges=1` + `CrossExtraEdges=N` build the sidecar atomically **before** STATIC Phase 4. The builder derives K as `max(15, N)` and reuses `BuildSSDIndex.NumberOfThreads`; the launcher reuses the sidecar and `augmentheadgraph` remains the fallback/rebuild tool. `CrossEdges=0` skips it. Search-time kill switch: env `SPTAG_DISABLE_CROSS_EDGES=1`; filter queries skip cross edges unless `SPTAG_FILTER_KEEP_CROSS=1` | shared builder `HeadCrossEdgeBuilder.cpp`; build-time tail callback and runtime traversal in `SPANNIndex.cpp` |
| ② U_extra (~10% extra unfilter-only heads; optional, default OFF) | `head_role.bin` | ini `[SelectHead] DualPoolAugment=1` (+ `DualPoolExtraRatio=0.1`) if explicitly needed; canonical SPACEV config uses `DualPoolAugment=0` | `SPANNIndex.cpp` DualPoolAugment (~3098-3192) |
| ③ unfilter-tail (K nearest-head tail copies/vector) | tail edges appended after each pure prefix, ordered by true head distance | build: native SSD params `[BuildSSDIndex] TailReplicaCount=K` + `UnfilterTailBufferLength=P`, where P is max extra physical tail pages beyond pure pages (tail may fill pure-page slack); search env `SPTAG_UNFILTER_TAIL=1` | `ExtraDynamicSearcher.h` Phase 4 (~4035-4090); SSD params `TailReplicaCount`/`UnfilterTailBufferLength` |

### Billion-scale build options (resume / pin-balance / in-place)

**SelectHead resume checkpoint** (avoid re-running the expensive BKT head selection when a later BuildHead/BuildSSDIndex fails): ini `[MultiTenant] PersistSelectHead=1` makes `SelectHeadInternal` write `head_select_state.bin` (the spatial head-selection state: node head selections, per-bundle U_extra, node/primary vector assignments, head-vector owners, head roles) into the SPANN **work dir** and keep the per-node head vector files (normally deleted in BuildHead). To resume, re-run the launcher with `[MultiTenant] ResumeBuild=1` (→ env `SPTAG_RESUME_BUILD=1`): CoreInterface keeps the work dir (`CoreInterface.cpp` ~2342, guards `RemovePathRecursive`) and `BuildIndexInternal` (`SPANNIndex.cpp` ~3450) loads the checkpoint and reports `select head time: 0.00s`, going straight to BuildHead. The checkpoint lives in `$SPTAG_SPANN_WORK_DIR/sptag_spann_tenant_<id>` (default `/tmp`); **set `SPTAG_SPANN_WORK_DIR` to a persistent disk** so the checkpoint survives across runs (`/tmp` is wiped on reboot). Impl: `SaveHeadSelectState`/`LoadHeadSelectState` in `SPANNIndex.cpp`; magic `'HSST'`.

**Pin BKT balance factor (skip DynamicFactorSelect)** (the SelectHead I/O bottleneck at billion scale): SPANN SelectHead defaults `BalanceFactor=-1`, which makes `BKTree::BuildTrees` run `DynamicFactorSelect` — an auto-search that does ~14 full `KmeansAssign` scans **per spatial candidate set** to pick the most-balanced lambda. On a ~250M-vector group over a slow disk this dominates SelectHead. Set `[SelectHead] BKTLambdaFactor` explicitly; it is staged directly into `m_options.m_fBalanceFactor`, so `m_fBalanceFactor >= 0` skips the auto-search. The official SIFT1B GettingStart configuration uses `1.0`; legacy comparison configs used `100`. The INI is authoritative—do not substitute either through an environment override. Independent of BKTLambdaFactor, keep the SelectHead vector file on fast storage (NVMe) — the BKT tree recursion still scans the group vectors repeatedly.

**In-place build (no final copy)** (avoid the transient 2× disk footprint + copy time at billion scale): by default the SPANN index is staged in a per-tenant **work dir** (`$SPTAG_SPANN_WORK_DIR/sptag_spann_tenant_<id>`, default `/tmp`) and `SaveAll` copies it to `IndexDirectory/tenant_<id>` at the end — which needs room for the postings *twice* (work + final) and re-writes the whole block pool. ini `[MultiTenant] InPlaceBuild=1` (→ launcher exports `SPTAG_SPANN_INPLACE_DIR=$IndexDirectory`) makes the build write the head index + SSD block pool **directly** into `IndexDirectory/tenant_<id>`. `SaveAll`/`SaveUnifiedStorage` then hit the `srcDir == dstDir` branch (`CoreInterface.cpp` ~3405, logs "already saved in place") and skip the copy. Note: the `StartFileSizeGB` block pool is pre-allocated in `IndexDirectory`'s filesystem, so that disk must hold it (for SPACEV-1B: `/datadisk`, 420–560GB). The SSD postings are already flushed incrementally to the FILEIO block pool during BuildSSDIndex, so in-place gives true streaming-to-final with no extra disk. Impl: work-dir computation in `CoreInterface.cpp` (~2334, honors `SPTAG_SPANN_INPLACE_DIR`).

Search side: unfilter routes to all bundle nodes via cross-edge unified
traversal (`SPANNIndex.cpp` ~1618; `m_globalHeadGraph` is no longer used for
navigation). Cross-edge search toggles: `SPTAG_DISABLE_CROSS_EDGES`,
`SPTAG_CROSSEDGE_UNFILTER`, `SPTAG_FILTER_KEEP_CROSS`.

Full mode matrix and reproduce commands: `docs/MultiTenant_DualPool_Usage.md`,
`docs/MultiTenant_SIFT1M_UnfilterTail.md`.

## Build Config — Native `.ini` (single source of truth, DO NOT use env-soup)
The attribute-aware SPANN build is driven by a **native SPANN sectioned `.ini`**
read by `Helper::IniReader` (the same loader the classic `IndexBuilder` uses) —
**not** a pile of `SPTAG_*` env exports in a shell script. The canonical config
+ launcher (both committed, so they survive `/tmp` wipes) are:
- `Tools/benchmarks/build_spann_attr_sift1b_zipf200_limited_tag_h5.ini` — the
  current single-label config (production is stopped; do not restart implicitly). Run new builds with
  `Release/spannbuilder -c <config.ini>`.
- `Tools/benchmarks/run_spann_attr_build.sh` — thin launcher. Carries ONLY what is
  not a build param (process-loader env + cross-edge fallback/reuse + copying
  `opq_quantizer.bin`); derives every path FROM the ini via `sed`.
  SPACEV/four-categorical templates declare their known original schema.
  Legacy headerless vector recipes still need explicit native input migration.

How the `.ini` maps to the engine (`Wrappers/src/SpannAttrBuilder.cpp` `-c` reader):
- `[Base]/[Tags]/[Build]` → data-layout args (Resolve: CLI flag > ini > default).
  Vector IO uses native `VectorSetReader`/`ReaderOptions` and core DiskIO:
  `ValueType` is the element type, `VectorType=DEFAULT|TXT|XVEC` the container.
  DEFAULT validates its row/column header and exact file size, then optionally
  maps read-only with VectorSet-owned lifetime (no duplicate L2 bulk corpus).
  `Dim` is optional for DEFAULT and must match if supplied.
  `VectorOffset`/`VectorCount` and vector `--vec-offset`/`--n` are removed;
  use native `VectorSize`/`--vector-size` only for intentional bounded prefixes.
  Legacy headerless Float profiles use an unsupported RAW migration marker;
  do not mechanically call their files DEFAULT or rewrite historical inputs.
  TagFile remains headerless row-major native uint32, starting at byte zero
  implicitly. TagOffset/--tags-offset/--tag-offset and offset environment
  overrides are removed and rejected, including explicit zero.
  `[Tags] ColumnTypes` lists every original column as `categorical` or `numeric`;
  `cate,num` aliases canonicalize to full names. Width is derived; optional
  NumTagsPerVec must match. Multiple categorical columns and arbitrary
  interleaving are supported without input reordering. LimitedTagColumn is
  an absolute original column index and must identify a categorical column.
  Single-label means one value per categorical column, not one per entire record.
  Exact file size must match either
  the selected native vector prefix or the full validated vector source;
  arbitrary trailing rows/bytes, headers and truncated records are not accepted.
  Raw files cannot self-identify a same-width wrong dtype; callers must supply
  uint32, not floats/int32 bit patterns. See benchmark README.
  Bulk input no longer exposes Tenant, WithMetaIndex or ShareBuildOwnership
  (including the ownership environment override): tenant 0/no line metadata are
  fixed and native ownership is automatic. L2/normalized Cosine borrows; native
  unnormalized Cosine uses one mutable copy, never modifies the mapped source.
  Base.Normalized retains native reader semantics. BuildSSDIndex.NumTagsPerVec
  is derived from Tags.ColumnTypes, not a second build input (runtime persists it).
  Real library tenant/metadata APIs and maintenance tenant selection remain.
  BuildSignatures remains an explicit, deferrable phase; MinHeadsPerTag remains
  active outside support-expansion mode rather than being removed as a default-zero knob.
- `[BuildSSDIndex]` → native `mgr.SetSSDBuildParam(k,v)` staging path (the ONLY
  section with a native pre-build hook): `Storage`, `ReplicaCount`,
  `PostingQuantizer`/`PostingQuantM`/`PostingQuantizerFile`/`PipePQPivotsFile`,
  `FullVectorFile`, `RerankL`, `StartFileSizeGB`/`MaxFileSizeGB`.
- `[SelectHead]` and `[BuildHead]` → staged directly into the native SPANN
  parameter system after wrapper defaults, so explicit values override
  tenant-size heuristics. Use the native `SelectHeadType` key; the legacy
  `SelectType` alias is accepted only when the native key is absent.
  `Ratio`, `BKTLambdaFactor`, BKT threads, thresholds, and every BuildHead graph
  option (including `RefineIterations`, `MaxCheckForRefineGraph`, and
  `TPTBalanceFactor`) are direct INI settings. U_extra controls belong only in `[SelectHead]`; cross-edge controls belong
  only in `[BuildSSDIndex]`. The duplicate `[MultiTenant]` aliases are rejected.
  Central `Core/TagSchema.h` maps original categorical indices and dense numeric
  lanes. Native library setters use BuildSSDIndex.ColumnTypes. Saved explicit
  ColumnTypes, TagSchemaVersion=1 and TagSchemaFingerprint validate schema on reload;
  posting rows and existing numeric/support formats are unchanged.
  Legacy saved prefix-count metadata remains readable only as its authentic
  layout. Never reinterpret old raw fields using a guessed new schema.
  Known repository recipes now carry explicit types; old RAW vector markers remain.
  HierarchySignatureMinSelectivity/MaxSelectivity and their SecondLevel aliases
  are removed. New signatures include the full categorical domain (0,1];
  every DNF clause still needs an equality anchor for signed H descent.
  Legacy V2/V3 CSR domain fields remain authenticated, read-only metadata,
  preserved on load/repair/save. Legacy INI domain keys are read only during
  saved-index loading, never accepted as fresh build/search tuning.
  BKTSeed/TPTSeed were additions, not upstream knobs, and are removed from
  native setters/serialization. CPU BKT/TPT construction follows upstream
  `5619bb1`: BKT centers use global `Utils::rand`/`std::rand`, shuffles use
  default-constructed `std::mt19937`; TPT keeps one shuffle engine per worker,
  calls `Sleep(i * 100)` then `std::srand(clock())` per tree, and uses global
  `rand()` for projections. No custom fixed seed or per-tree seed remains.
  Saved seed declarations are load-only provenance with an explicit warning;
  they do not change existing tree/graph bytes. Rebuilds are not guaranteed
  reproducible, even with one thread. Historical fixed-seed experiment
  manifests/results are frozen provenance, not the current RNG contract.
  `ACLCols`, `HierLevelWidths`, `PivotForceNodeCount`, `DisablePivotEstimator`,
  `RoutingCols`, `PerVectorTagsFile`, `NumericCols` and `PerTagBKT` are removed and rejected.
  `LimitedTagVoteHeadCount` is also removed and rejected. Base tag support comes
  only from retained post-RNG/post-cut O prefixes: protect own tags, then keep
  nearest distinct external tags up to `LimitedTagSlotsPerHead`. No extra O
  search or N-times-candidate buffer is used. Preserve the O-derived expansion
  floor and full H/O records; legacy support provenance is documented
  in `Tools/benchmarks/README.md`.
  H and O now share the native distance-ordered construction cut
  (`PostingPageLimit`, uplifted by `PostingVectorLimit`) and independently use
  `SearchPostingPageLimit` on the selected region at runtime. Cutting every H
  replica of a non-head vector triggers a lightweight pre-write rescue: append
  exactly its nearest already-computed valid H assignment to that head's H
  tail, before O. No new search, support expansion, replica refill or query
  budget override occurs. H may exceed the normal cut; O remains unchanged.
  Build logs report zero-H before/after and rescued records/postings/pages;
  saved-index audits report beyond-cut H, not an inferred rescue count.
  The independent
  `LimitedTagMaxExpandedPostingPages` option is removed and rejected.
  Hierarchy query navigation is one top-graph search followed by signed CSR
  descent and saved-frontier budget widening, independently of selectivity.
  HierarchyRouteSelectivityThreshold and
  SecondLevelRouteSelectivityThreshold are removed/rejected. Auto always uses
  an enabled hierarchy, including numeric-only full-O and unfiltered queries;
  disabled hierarchies retain native H1. Explicit H1Only requires an H1 graph,
  and H2Only requires a loaded hierarchy. Legacy partial CSR signature domains
  only guard pruning: any unrepresented query anchor clears the signature,
  preserving navigation and exact admission. Migrate retired route INI keys
  only in a separate writable clone; never rewrite historical index artifacts.
  Anchored queries admit top
  results using that highest layer's CSR signatures. Underfilled matching
  results continue the same native graph/tree frontier within the shared
  HierarchyMaxCheck and InternalResultNum ceiling, before any descent; no
  second graph search or whole-layer scan. BKT/KDT result-only admission keeps
  nonmatching bridges traversable. Explicit HierarchyGraphSignaturePruning
  remains a separate BKT traversal restriction. Filtered initial pivots share
  MaxCheck; BKT counts checked leaves/graph-neighbor evaluations, not every internal
  tree-pivot distance (do not report it as an exact distance ceiling).
  Direct tag-to-H1 completion,
  its early-widening stop callback, and `SparseFallbackMaxHeads`/
  `SparseFallbackMaxPostingPages` are removed and rejected (even explicit zero).
  Only visited H1 children may contribute own points, including empty-posting
  heads. Budget-limited queries can return fewer matches; never repair this
  with a global support-head scan. TagHeads remains for construction,
  maintenance and audit. `LimitedTagMaxExtraSupports` is removed/rejected,
  including saved INIs (remove only in a writable clone). Expansion state grows
  incrementally from distinct retained-O heads, bounded by per-tag floor
  deficits. New V5 support files use the 80-byte V4 layout with an authenticated
  exact source-capped deficit sum in the old cap word and a new fingerprint
  discriminator. V4 files retain their opaque cap/provenance/hash byte-for-byte
  on read/export; `LegacyExtraSupportCap()` is read-only provenance, not tuning.
  EST sidecar serving and its four knobs are removed. `BuildSignatures` uses tags
  plus persisted support/VID metadata; it no longer accepts an unused vector buffer.
  The unfilter-tail K/buffer and pre-tail cross-edge settings are
  native SSD params (`[BuildSSDIndex] TailReplicaCount`/
  `UnfilterTailBufferLength`/`CrossEdges`/`CrossExtraEdges`), read straight from
  the ini — no environment override.
- `[SearchSSDIndex]` → applied only after BuildHead/BuildSSDIndex complete, then
  retained as a separate native section in the generated `indexloader.ini`.
  This keeps runtime values such as `InternalResultNum`, `MaxCheck`, and
  `NumberOfThreads` from changing construction behavior while making the
  documented search overlay the source of truth on subsequent loads.

Gotchas (`Helper/SimpleIniReader.cpp`): comments MUST start with `;` (lines
starting with `#` are parsed as params → `ReadIni_FailedParseParam`); inline
comments are NOT stripped, so a value line must contain only the value; sections
and keys are lowercased (case-insensitive). An explicit CLI flag still overrides
any ini value (later `SetSSDBuildParam` push wins).

## Billion-scale derived inputs — pure-C++ prep (no Python, no generic quantizer)
The attribute SPANN build needs three derived sidecars that are NOT in the repo
(too large). Generate them in **C++** via `spannbuilder` subcommands — mirroring
`AnnService/src/Quantizer/main.cpp` — so they match the in-posting convention the
engine trusts. Canonical driver: `Tools/benchmarks/prep_spacev1b_inputs.sh`
(optional `N` arg builds a smoke subset). Subcommands (`SpannAttrBuilder.cpp`):
- `--merge-tags5`  interleave `tags.npy[N,4]` + `num_attr.npy[N]` → `*_tags5.u32`
  `[N,5]` (4 categorical cols + numeric), with no attribute grouping sidecar.
  Uses an explicit validated `.npy` v1.0 reader (C-order little-endian uint32
  `[N,acl]` + nonnegative int32 `[N]`, matching source row counts and exact
  header/shape/payload sizes). This preparation-only format is not TagFile;
  no inferred offset, smaller-source fallback or dataset rewrite is used.
  Such outputs use ColumnTypes=categorical,categorical,categorical,categorical,numeric.
- `--gen-opq-codes`  load OPQ codebook with `IQuantizer::LoadIQuantizer`, mmap base,
  **widen raw bytes to float (NO normalization)**, `QuantizeVector(vf, code, ADC=false)`,
  write header-less `N*M` `opq_codes_m<M>.bin`. This replicates `ExtraDynamicSearcher.h`
  ~5165. **Do NOT use the generic `Release/quantizer`** — it normalizes and writes an
  8-byte `(n,d)` header, so its codes do NOT match (validated per-byte ≈ random).
Validated byte-exact vs the 3M `opq_codes_m25.bin` (per-byte 0.9999996). Reuses the
3M-trained `opq_quantizer.bin` (copied for search-time ADC).

## SSD Block-Pool Sizing (billion-scale, OPQ/RaBitQ)
The FileIO posting store (`ssdmapping_postings`) is a pre-allocated block pool:
it starts at `StartFileSizeGB`, grows by `GrowthFileSizeGB`, capped at
`MaxFileSizeGB` (`ParameterDefinitionList.h:216-218`,
`ExtraFileController.cpp:13-45`). The wrapper auto-estimates these from
`postingAssignmentCount × perVecBytes × 10` (`CoreInterface.cpp` ~2388).

Two gotchas, both fixed:
- The estimate is now **slim-aware**: when a posting quantizer is staged
  (`--posting-quantizer OPQ/RaBitQ`), `perVecBytes` uses the slim record
  (`PostingQuantM + numTags*4 + 32`) not the full vector. Otherwise it
  over-allocated ~4.7× (1B SPACEV → `StartFileSizeGB=1840` → instant ENOSPC
  before the first posting is written). Real 1B OPQ-25 posting data ≈ 388 GB.
- For reproducibility, **pin the budget in the build script** with explicit
  CLI flags (preferred over env): `--ssd-start-file-gb <GB> --ssd-max-file-gb <GB>`
  (`--ssd-growth-file-gb` optional). When provided, the estimator does NOT
  override them. 1B SPACEV example: `--ssd-start-file-gb 420 --ssd-max-file-gb 560`
  (real ~388 GB, fits the 761 GB NVMe alongside the ~120 GB head index).
  Wired in `SpannAttrBuilder.cpp` (`--ssd-*-file-gb` → `SetSSDBuildParam`).


## In-posting Quantization + Deep-queue Rerank (search & build)
SPANN postings can store a compact **in-posting quantization code** (RaBitQ / OPQ)
per vector instead of the full-precision vector, so a posting scan reads ~4× fewer
bytes. The top-`L` survivors are then **exact-reranked** by cold O_DIRECT reads from
the full-precision base file (vid-indexed, never page-cache resident). This is the
billion-scale path: the ~1TB full-vector posting store is never materialized — only
the slim `[meta | code]` end-state hits disk.

- **Build** (one source of truth = the `.ini`): `[BuildSSDIndex] PostingQuantizer=OPQ|RaBitQ|PipePQ`
  + `PostingQuantM=<bytes>` + `PostingQuantizerFile=<codebook>` + `FullVectorFile=<base>`
  (rerank source) + `RerankL`. PipePQ additionally requires
  `PipePQPivotsFile=<PipeANN *_pq_pivots.bin>`; use PipeANN's native
  `*_pq_compressed.bin` as `PostingQuantizerFile` for byte-identical code assignment.
  The native single-pass writer streams slim postings directly (no full-vector
  intermediate). RaBitQ code sidecars are pre-encoded with
  `Release/rabitq2_encode_stream` (value-type-aware, scales to 1B); OPQ codes with
  `spannbuilder --gen-opq-codes` (see prep script). Internals: `TransformInPostings*`
  / build-slim writers in `ExtraDynamicSearcher.h` (markers `inpost_rbq.bin`,
  `inpost_opq.bin`, `inpost_pipepq.bin`).
- **Search**: RaBitQ async path = env `SPTAG_INPOST_RBQ=1` (+ `SPTAG_INPOST_RBQ_FILE`),
  with rerank via the **deep-queue libaio** reader (`SPTAG_INPOST_LIBAIO_RERANK=1`,
  `RerankBaseDirectBatch()` — one `io_submit` for all `L` candidates, ~12µs/read vs
  ~56µs serial). **Do NOT set `SPTAG_OPQ_PREFILTER`** with the RaBitQ async path — it
  routes to the serial `SearchIndexOPQ` path (~6× slower cold). OPQ in-posting uses the
  matching codebook (`opq_quantizer.bin`, ADC) on the same libaio rerank fast path.
