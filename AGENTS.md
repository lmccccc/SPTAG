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

## Unified Spatial Query Pipeline (DO NOT REGRESS)
Current attribute SPANN navigates the native H1 graph with result-only
post-filtering. Spatial H2..H5 catalogs and signed CSR are auxiliary posting
data, not independently searched upper ANN graphs. Attribute
pivot planning, per-tag head selection, grouping files and tag-to-bundle routing
are removed; removed INI/environment interfaces fail explicitly. Do not restore
them. Nonmatching ordinary nodes remain navigable; predicates admit matching
H1 results and exact final records without an additional own-result heap.
`[SearchSSDIndex] EnablePostingNavigation=false` is the library default.
When enabled, run the original native H1 result-only post-filter graph to
normal completion first, without any auxiliary calls inside graph expansion.
The same four-byte visited/match workspace survives into supplementation.
Count actual valid H1 results C against the configured head-result capacity
(nprobe, not final top10). If C is sufficient, perform no posting-owner,
upper-signature, representative or CSR work. Otherwise at most one query-level
supplement phase may fill the missing slots, without evicting original heads.
`PostingAnchorCount=8` (positive) bounds nearest actually scored H1 anchors,
including negatives and tree candidates promoted to native navigation.
Reuse their distances; do not scan the visited directory or re-score anchors.
Merge/dedup stored owners across anchors. All discovered signature-admitted
H2+ postings share one query-to-representative-distance priority frontier.
Each selected posting exposes its owners once; complete upper rows register
children in that same frontier rather than eagerly draining lower levels.
Rejected anchor-owner entries and explicitly promoted owners may expose their
owners once to reach matching siblings, without scoring their representatives
or reading their CSR. A rejected child discovered by descending a selected
upper row is cached but does not expose its other owners. This prevents
negative-child fanout from turning pruning into eager upward exploration.
A cached rejection must still permit later entry/owner promotion; signature
caching must not suppress that recovery. Pending admitted children remain
reachable; every selected CSR row completes before checking the budget.
Filling the missing-slot heap is not a stopping condition: continue replacing
worse supplementary heads until frontier convergence, checked-leaf budget, or
reachable-posting exhaustion. BKT supplies the native navigation-pool width
`max(effective MaxCheck / 16, head-result capacity)` only on activation.
Reuse `COMMON::DistPriorityQueue` for discovered signature-admitted H2 row
representatives; H3+ nodes participate in expansion priority but cannot consume
H2 convergence slots. Stop when the nearest pending representative is worse
than that pool. This ANN convergence heuristic works even with underfilled results;
it is not proof that unseen posting members cannot be closer. Never describe
representative distance as a certified member lower bound. Original H1 heads
remain protected, and no new INI knob or full-catalog query allocation is added.
`PostingAdditionalMaxCheck=0` is the nonnegative default. Original graph
MaxCheck is unchanged; only supplementation can spend unused budget plus the
explicit extra. Zero extra with an exhausted graph budget means no supplement.
After graph completion, fresh auxiliary predicate negatives are cached as
terminal visits and skipped without vector prefetch, distance or checked-leaf
cost. A rejected collapsed representative still checks its aliases; its shared
vector is scored once only if a live matching alias needs admission. Ordinary
H1 graph negatives remain genuinely scored navigation bridges. Do not resume
graph traversal after terminal rejection visits or enqueue supplementary
members into its unused frontier. Matching fresh members retain native
distance/checked-leaf accounting; complete-row budget overshoot is unchanged.
Native result admission, aliases and liveness determine filled missing slots;
fresh may-matches alone do not. The temporary missing-slot container is a
bounded H1 candidate container, never a supplementary own-record heap.
`PostingMinCandidates` is retired and rejected with a migration error.
The old row-percentage, fresh-floor and cumulative-density policies exist
only in archived experiments. No rate threshold or recall controller is added.
Keep posting state query-touched; no catalog clear, second ANN search,
whole-index predicate cache or eager own/liveness/alias qualification.
Keep exact categorical/numeric filtering, support assignments, H/O regions and
native final VID/liveness/dedup handling. Upper query state is proportional to
touched IDs, not total catalog size; no full-layer allocation, clear or scan
belongs in a query. Signature may-match is not exact predicate truth.
Unfiltered queries retain native navigation.

Ordinary unfiltered BKT search retains the pinned upstream adaptive stop:
the distance pool stays `max(MaxCheck / 16, resultNum)`, pivots/refills are
unclipped, and the checked-leaf budget is tested on a live popped candidate
worse than the result boundary. Nominal MaxCheck overshoot is intentional.
Nonempty result/metadata predicates separately retain a hard graph checked-leaf
cap and bounded tree calls, including when results remain underfilled.
`SearchTrees` processes the popped leaf before checking its limit; filtered
callers must not call it with an already-exhausted budget. Do not discard a
popped leaf or expand internal cells past that post-leaf stopping point.
Query-sized workspace initialization is a separate retained project optimization.

Numeric/DNF H1 admission uses authenticated region signatures in both baseline
and auxiliary modes; pure numeric and unanchored OR still select O. Own values
are merged once, not qualified per visit. Upper H/O unions are shared and built
only at load/build/explicit metadata refresh. No duplicate H1 numeric mask array
or query-time catalog scan is allowed. Missing/stale domains are logged and
treated as conservative unknown. Keep wrapper and native metadata refresh
lifecycle paths consistent.

Implementation belongs only in main `AnnService`, including
`Common/NavigationVisited.h`, `Common/PostingNavigation.h` and
`SPANN/PostingNavigation.h`. Benchmark clients link the main native library;
do not restore the retired generated-core prototype or its hook framework.
New builds persist H1 navigation and upper vector/CSR/signature catalogs.
Temporary native upper ANN indexes remain only for the unchanged construction
assignment algorithm; never save, load or query them as runtime navigation.
Legacy upper directories may supply vectors without loading their graph/tree.
Graphless-H1 layouts require explicit migration to a new output directory,
not an implicit rebuild or fallback to old upper ANN search.

| Layer | What it builds | How to enable | Code |
| ----- | -------------- | ------------- | ---- |
| ① cross-graph | `head_cross_edges.bin` stitching physical bundle nodes | Native `[BuildSSDIndex] CrossEdges=1` plus `CrossExtraEdges=N`; `CrossEdges=0` skips it. Runtime uses the same spatial policy for filtered and unfiltered requests. | `HeadCrossEdgeBuilder.cpp`, `SPANNIndex.cpp` |
| ② supplemental heads (optional, default OFF) | `head_role.bin` | Native `[SelectHead] DualPoolAugment=1` plus `DualPoolExtraRatio`; all configured bundle nodes participate regardless of predicates. | `SPANNIndex.cpp` |
| ③ legacy tail layout | distance-ordered tail records after each pure prefix | Native `[BuildSSDIndex] TailReplicaCount=K` plus `UnfilterTailBufferLength=P` retain their construction meaning. All predicates scan the same native `SearchPostingPageLimit` prefix, including tail records within it. | `ExtraDynamicSearcher.h`, `ExtraStaticSearcher.h` |

`EnableUnfilterTail`, `UnfilterPurePages`, `UnfilterExtraTailPages`,
`UnfilterPureDistanceScanPercent`, `AblateUExtra` and `AblateTail` are removed
and rejected, including explicit false/zero. Constrained H/O membership still
selects its region, with the same native page limit in either region. Legacy
ordinary pure+tail files remain readable without changing their construction.
Ordered-page directories are layout compatibility metadata, never query-time
pruning; their construction settings cannot be used as search overlays.
`SPTAG_OPQ_PREFILTER`, `SPTAG_PAGE_SELECT`, `SPTAG_PAGE_DIAG`,
`SPTAG_DNF_NODROP`, and `SPTAG_RBQ_EXHAUSTIVE` are rejected on presence.
No predicate diagnostic may trigger a whole-posting-store scan during search.

### Fixed SIFT1B comparison configuration

Use `Tools/benchmarks/run_sift1b_official.py` and the repository-owned
`Tools/benchmarks/configs/sift1b_official/benchmark.ini` for PipeANN/SPANN
comparisons. Native search INIs, filter JSON and the read-only CMake profile
are checked in beside it. Launchers may copy them unchanged for provenance;
they must not render temporary configurations, invent parameters, or use
data/search environment overrides. Unfiltered PipeANN requires its matching
1% memory-entry index and `mem_L=10`; missing prerequisites fail instead of
silently using zero. Filtered PipeANN retains its documented auto/mem_L=0.
Pure query benchmarking requires READ_ONLY_TESTS and NO_MAPPING, and must
not conflate one query thread with whole-process single-CPU confinement.
Keep historical results and index bytes unchanged.

### Billion-scale build options (resume / pin-balance / in-place)

**SelectHead resume checkpoint** (avoid re-running the expensive BKT head selection when a later BuildHead/BuildSSDIndex fails): ini `[MultiTenant] PersistSelectHead=1` makes `SelectHeadInternal` write `head_select_state.bin` (the spatial head-selection state: node head selections, per-bundle U_extra, node/primary vector assignments, head-vector owners, head roles) into the SPANN **work dir** and keep the per-node head vector files (normally deleted in BuildHead). To resume, re-run the launcher with `[MultiTenant] ResumeBuild=1` (→ env `SPTAG_RESUME_BUILD=1`): CoreInterface keeps the work dir (`CoreInterface.cpp` ~2342, guards `RemovePathRecursive`) and `BuildIndexInternal` (`SPANNIndex.cpp` ~3450) loads the checkpoint and reports `select head time: 0.00s`, going straight to BuildHead. The checkpoint lives in `$SPTAG_SPANN_WORK_DIR/sptag_spann_tenant_<id>` (default `/tmp`); **set `SPTAG_SPANN_WORK_DIR` to a persistent disk** so the checkpoint survives across runs (`/tmp` is wiped on reboot). Impl: `SaveHeadSelectState`/`LoadHeadSelectState` in `SPANNIndex.cpp`; magic `'HSST'`.

**Pin BKT balance factor (skip DynamicFactorSelect)** (the SelectHead I/O bottleneck at billion scale): SPANN SelectHead defaults `BalanceFactor=-1`, which makes `BKTree::BuildTrees` run `DynamicFactorSelect` — an auto-search that does ~14 full `KmeansAssign` scans **per spatial candidate set** to pick the most-balanced lambda. On a ~250M-vector group over a slow disk this dominates SelectHead. Set `[SelectHead] BKTLambdaFactor` explicitly; it is staged directly into `m_options.m_fBalanceFactor`, so `m_fBalanceFactor >= 0` skips the auto-search. The official SIFT1B GettingStart configuration uses `1.0`; legacy comparison configs used `100`. The INI is authoritative—do not substitute either through an environment override. Independent of BKTLambdaFactor, keep the SelectHead vector file on fast storage (NVMe) — the BKT tree recursion still scans the group vectors repeatedly.

**In-place build (no final copy)** (avoid the transient 2× disk footprint + copy time at billion scale): by default the SPANN index is staged in a per-tenant **work dir** (`$SPTAG_SPANN_WORK_DIR/sptag_spann_tenant_<id>`, default `/tmp`) and `SaveAll` copies it to `IndexDirectory/tenant_<id>` at the end — which needs room for the postings *twice* (work + final) and re-writes the whole block pool. ini `[MultiTenant] InPlaceBuild=1` (→ launcher exports `SPTAG_SPANN_INPLACE_DIR=$IndexDirectory`) makes the build write the head index + SSD block pool **directly** into `IndexDirectory/tenant_<id>`. `SaveAll`/`SaveUnifiedStorage` then hit the `srcDir == dstDir` branch (`CoreInterface.cpp` ~3405, logs "already saved in place") and skip the copy. Note: the `StartFileSizeGB` block pool is pre-allocated in `IndexDirectory`'s filesystem, so that disk must hold it (for SPACEV-1B: `/datadisk`, 420–560GB). The SSD postings are already flushed incrementally to the FILEIO block pool during BuildSSDIndex, so in-place gives true streaming-to-final with no extra disk. Impl: work-dir computation in `CoreInterface.cpp` (~2334, honors `SPTAG_SPANN_INPLACE_DIR`).

Search side: every request routes through all configured bundle nodes using the
same spatial traversal (`SPANNIndex.cpp`; `m_globalHeadGraph` is no longer used
for navigation). Do not introduce environment-variable search overrides.

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
  `opq_quantizer.bin`); derives every path from its native INI section.
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
  Hierarchy levels now describe posting catalogs, not a top-graph query path.
  H1 result-only post-filter is the query baseline; the optional native
  `EnablePostingNavigation` extension uses signed CSR neighbors in that same
  frontier. `BuildH1Graph`, `CompactHierarchyVectors`,
  `HierarchyInitialProbeRatio`, `HierarchyMaxCheck`, `HierarchyPrefetchMode`
  and obsolete upper routing/beam/dedup controls are rejected by fresh setters.
  `HeadNavigationMode`, `HierarchyGraphSignaturePruning`,
  `HierarchyRouteSelectivityThreshold` and their retired aliases must not
  restore an alternative query algorithm. Required saved layout/provenance
  metadata is decoded explicitly with warnings. Native H1 `MaxCheck` retains
  its checked-work meaning, not a bound on all upper references or complete
  auxiliary CSR rows. Migrate old graphless layouts only into a writable clone;
  never rewrite historical index artifacts.
  Direct tag-to-H1 completion,
  its early-widening stop callback, and `SparseFallbackMaxHeads`/
  `SparseFallbackMaxPostingPages` are removed and rejected (even explicit zero).
  Only native selected H1 heads may contribute their own records, including
  matching empty-posting heads; no supplementary own heap is added.
  Budget-limited queries can return fewer matches; never repair this
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

## Reproducible offline predicate inputs

`Tools/benchmarks/generate_spann_attributes.py --config <native.ini>` is the
INI-only synthetic categorical/numeric recipe; existing real attributes can
skip it. `generate_spann_predicate_groundtruth.py --config <native.ini>` builds
all configured unfiltered/categorical/numeric/DNF truths directly from native
DEFAULT vectors and raw uint32 attributes, without a previous workload or GT.
Shared native file/schema readers live in `native_input_io.py`; predicate
validation, DNF3 encoding and stable top-k helpers live in `predicate_groundtruth.py`.
The offline GT tool currently supports L2 and the four native element types,
with bounded streamed base/query distance tiles and source inputs kept read-only.
Its preparation sections are in `docs/AdaptiveSpann.ini`, not core search knobs.
Keep top-k sourced from `SearchSSDIndex.ResultNum`, original column indices,
explicit `-1`/infinity underfill and fresh-output refusal. Do not restore the
removed four-level SIFT1B ACL generator or make fresh preparation depend on
`native_postfilter/prepare_selectivity.py`'s authenticated historical inputs.

## Compact storage reconstruction

Explicit-schema head metadata uses authenticated V9 records with one index-owned
layout descriptor. Store only declared own columns, categorical lanes and numeric
lanes; H/O remain distinct. Access packed own/column blocks through typed views,
never fixed five-column POD pointers. V8 without an explicit schema retains its
legacy layout; V8 with an authenticated explicit schema converts with a bounded
4096-record input chunk. Schema, widths, region flags, numeric domain, generation
and payload are covered by authentication. Do not reinterpret a mismatched schema.

Canonical upper vector catalogs store direct uint32 H1 physical IDs and retain
the existing full H1 vector owner. Validate exact representative bytes and mapping
bounds; do not follow an inter-layer mapping chain during a distance evaluation.
CSR memberships, owners, H1 graph and SSD records are unchanged. Distinct support
and H/O signatures cannot be assumed equivalent: sharing requires an exact
whole-layer comparison at build/refresh, with owned capacity actually released.

`compactspannindex SOURCE_TENANT NEW_TENANT` reconstructs into a new directory,
authenticates V8/V9 metadata and canonical vectors, and references immutable
geometry/payload files. It never selects heads or rebuilds the SSD index.
SIFT1M acceptance is required before any incremental 1B writes. Storage changes
must not alter the post-graph search policy or native INI budget settings.

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
- **Search**: native `PostingQuantizer`, `PostingQuantizerFile`, `FullVectorFile`
  and `RerankL` select the posting codec and exact-rerank source. RaBitQ uses
  its in-posting codes; OPQ uses the matching `opq_quantizer.bin` ADC codebook,
  not a neighboring RaBitQ sidecar. The deep-queue libaio reader
  (`RerankBaseDirectBatch()`) batches survivor reads. Missing or incompatible
  native OPQ/PipePQ initialization fails loading instead of treating code bytes
  as full vectors. No prefilter environment switch is needed or accepted.
