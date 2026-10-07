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
### Explicit online match-rate policy (2026-10-06)

The `local-match-union-v2` correction to the user-authorized online policy is opt-in through native
`SearchSSDIndex.PostingMatchRatePercent` (integer 1..100; zero retains the
legacy/default policy below) and `PostingMatchWindow` (positive, default 1024).
It supersedes completion-first/protected-head rules only when opted in.
Count each fresh scored H1 navigation candidate once, including tree candidates,
using the shared OR-union head-admission cache. This is support/may-match yield,
not final-record truth or representative-own-label selectivity. Every complete
nonoverlapping window compares `matches * 100 < window * percent`; equality
continues H1, and incomplete windows cannot trigger. Never sample label-pure
posting members, use global selectivity, result fill, or MaxCheck as a trigger.

The first low-yield window latches supplementation. A sparse hierarchy may
replace H1 early only when every requested categorical label is admitted to
its posting domain. A mixed OR containing an H1-only label must instead finish
the original union-filtered H1 search before supplementation, even after the
rate latches. This is a coverage check, not a selectivity estimate or per-label
search. Complete-domain queries retain handoff at a graph-row/tree-call boundary.
Do not clip native tree calls or reintroduce a per-edge hard cap.
Drain already-scored pending H1 candidates into the bounded matching heap,
without expanding edges or recomputing distances, before entering posting.
Posting candidates then compete with existing H1 heads in a capacity-nprobe
heap even when full. The existing unused-plus-preserved-extra budget, complete
rows, convergence, exact final filtering, and no-graph-resumption rule remain.
Unfiltered, policy-off, and nontriggered searches retain upstream stopping.
Diagnostic dispatch schema 2 appends `deferred` to the original five counters:
a low-yield latch can be true while H1 continues to its native completion.
The original worktree, registered source proofs, indexes and binaries are
immutable controls; implement this policy in the isolated worktree's main
AnnService sources, not generated benchmark code.

### Legacy/default completion-first policy

The adjacent, full-domain catalogs described below remain the legacy/default
layout when `HierarchyLabelSelectivity` is empty. Explicit upper-only sparse
label reconstruction is described in the next subsection; its build-time
admission policy intentionally supersedes the full-domain catalog topology,
not the native H1-first search order or SSD/H1 immutability.

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
`PostingAnchorCount=0` is the default: resolve the anchor limit from the current
head-result capacity (nprobe, not final top10), separately for every query.
A positive value retains the explicit fixed limit for historical controls.
Collect up to that many nearest actually scored H1 anchors, including negatives
and tree candidates promoted to native navigation; use fewer if H1 scored fewer.
Reuse their distances; do not scan the visited directory or re-score anchors.
Merge/dedup stored owners across all selected anchors before consuming rows.
All discovered signature-admitted
H2+ postings share one query-to-representative-distance priority frontier.
Each selected posting exposes its owners once; complete upper rows register
children in that same frontier rather than eagerly draining lower levels.
Rejected anchor-owner entries and explicitly promoted owners may expose their
owners once to reach matching siblings, without scoring their representatives
or reading their CSR. A rejected child discovered by descending a selected
upper row is cached but does not expose its other owners. This prevents
negative-child fanout from turning pruning into eager upward exploration.
Sparse terminal children whose immutable label is outside the query's admitted
label union are excluded before query-state allocation or signature lookup.
This exact exclusion need not be cached; it never applies to entry/owner
promotion, which must still permit ascent through a rejected terminal.
Prefetch sparse node descriptors before this label test rather than eagerly
fetching signatures/numeric summaries for provably irrelevant terminal children.
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
remain protected, with no full-catalog query allocation.
`PostingNavigationWidth=0` preserves this legacy convergence policy. A positive
native `[SearchSSDIndex] PostingNavigationWidth` additionally bounds navigation
representative distances independently per logical tier, using that many nearest
discovered navigation heads rather than H1 nprobe. This shared policy applies to
both adjacent and sparse layouts; sparse physical storage level is not its tier.
Before expanding a navigation row, discard a head strictly farther than its
tier's current bound without reading its CSR or exposing its owners/children.
Continue the shared frontier for other tiers. Equal distances remain eligible;
the width defines a distance beam, not a hard row-count quota. Terminal rows
retain their original convergence/work budgets. Width 8 is the initial explicit
cutoff experiment, not a label-specific or recall-tuned constant. A nearby
selected parent still exposes its complete child row; this is approximate ANN
pruning, not a certified subtree bound. No index reconstruction is needed.
`PostingAdditionalMaxCheck=0` is the nonnegative default. Original graph
MaxCheck retains upstream adaptive stopping, including nominal overshoot.
Only supplementation can spend unused nominal graph budget plus the explicit
extra; graph overshoot must not consume that extra. Its checked-leaf ceiling is
`max(MaxCheck, actual completed H1 checks) + PostingAdditionalMaxCheck`, computed
in 64 bits. Zero extra with an exhausted graph budget means no supplement.
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

Both unfiltered and filtered BKT search retain the pinned upstream adaptive stop:
the distance pool stays `max(MaxCheck / 16, resultNum)`, pivots/refills are
unclipped, and the checked-leaf budget is tested on a live popped candidate
worse than the result boundary. Nominal MaxCheck overshoot is intentional.
Result and metadata predicates use the matching result boundary, not a separate
unfiltered result heap. Ordinary graph candidates are admitted on native queue
pop, not eagerly when their neighbor distance is computed; early admission
changes the result boundary and therefore convergence. There is no filtered per-edge hard cap, clipped pivot
call, special empty-frontier refill, or boundary-time graph/posting dispatcher.
`SearchTrees` processes the popped leaf before checking its per-call pivot
limit, including a call entered at that limit. Do not discard a popped leaf or
expand internal cells past that post-leaf stopping point. A true predicate must
preserve unfiltered IDs, distances and checked-leaf work on the same graph.
The older filtered hard-cap policy and isolated OR dispatcher are archived
experiments, not upstream semantics and not defaults to restore.
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

### Sparse label upper-only reconstruction

Global-threshold reconstruction writes **V4 adjacent limited-label postings**. V1/V2/V3
remain readable with their historical topology and query behavior described
below; do not reinterpret or rewrite those artifacts.

`[SelectHead] HierarchyLocalTarget` and `HierarchyLocalWindow`, both positive,
instead select **V5 local admission**; do not combine them with
`HierarchyLabelSelectivity`. They are immutable native build metadata, not
query-time controls. The target counts matching H1 support heads; the window
is an initial H1 spatial mass, enlarged by the source Ratio at each transition.
Density windows are nested primary-ownership spatial regions. If a region
exceeds the requested mass, scale its label count by requested/actual mass:
coarse-region overshoot must not invent extra search capacity. Once a label
stops, it cannot restart above the missing lower posting. This remains a
capacity estimate, not an asserted distance/check budget or recall guarantee.

V5 builds a label-unfiltered census from the preserved full-domain source.
Each physical child contributes to exactly one canonical spatial owner;
successive levels merge only adjacent summaries. Keep all support labels in
the census, including labels stopped or not selected in runtime postings.
Never sum replicated row lengths as distinct H1 mass. Only source H5
representatives enter a small native binary BKT that groups coarse statistics
into expanded windows; no per-label ANN or H1 vector neighborhood sweep is
added. The primary owner is the first canonical owner, not a claimed nearest
owner. Shared region masks apply per `(child physical representative,label)`,
not globally per label or to all labels of a parent.

V5 appends authenticated local addresses, label masks and actual window masses
to the layered artifact. Coverage validation uses these local decisions.
Serving releases this construction payload after authentication/validation,
retaining the actual per-tier label domains and the authenticated fingerprint. Query activation
must not consult old original-vector selectivity thresholds. H/O assignment,
native replica limits, H1-first search and all immutable-source rules below
remain unchanged. Census logs separate metadata transfers/merges from its small
coarse-vector organization and from subsequent native H/O construction cost.

V4/V5 also derive small exact per-tier label domains while building reverse
owners at load/construction time. On the first attempted owner ascent after
activation, resolve the highest tier containing any requested auxiliary label.
Do not expose owners above that tier: no admitted row there can affect the
frontier or results. A rejected current node still ascends if any higher tier
has a query label; current-node rejection is not an ancestor bound. This is
exact label-domain pruning, not density estimation, a fill-based stop, or a
new search parameter. Historical V1/V2/V3 and adjacent layouts are unchanged.

Each V4 H(k+1) posting contains Hk IDs, never direct H1 IDs above H2.
Admit a physical child only when it has at least one supported label whose
original-vector selectivity is **strictly below** that transition's INI
threshold. Filter its labels separately; dense labels cannot hitchhike.
Count unique physical eligible children for the native shared Ratio and cap
the resulting head count by the source tier. Never count incoming replicas or
virtual label copies when selecting the next layer's physical heads.
Representatives address the original H1 vector owner, without persisted vector
copies. Each selected representative anchors one admitted label. Fill its base
support from the nearest retained spatial candidate prefix, allowing repeated
labels to occupy candidate positions; store the resulting distinct labels with
the anchor first. This intentionally differs from H1's nearest-distinct-label
selection. Reuse the source LimitedTagSlotsPerHead, LimitedTagMinHeadCount and
EnableLimitedTagSupportExpansion. Native retained-O deficit expansion, if
already enabled, retains its existing semantics; no new coverage repair or
additional replica guarantee is introduced.

Assign each canonical (physical child, admitted label) independently using
the shared native H-posting ANN/RNG selector. HierarchyReplicaCount is a maximum,
not an exact count; do not fill RNG-rejected replicas. A selected anchor's own
logical item is represented by one explicit self-child, analogous to the
implicit own record in H1. Only an empty constrained result gets the native
exact-support fallback, and an unplaceable item is an explicit build failure.
Multiple labels can increase references, not physical vectors. Per-label
rows index only relevant children; selected OR rows are merged/deduplicated
before shared frontier expansion or H1 consumption. Reverse owners deduplicate
physical parent-child pairs. Sparse numeric masks remain conservative;
query label support is the selected label set, never the union of all child
labels. An H2 spatial entry preserves negative-anchor entry without a mixed
global root. There is no query-time upper ANN or catalog scan.

V4 authenticates label directories, strictly adjacent edges, original label
admission, per-child/label coverage and replica caps, unique physical centers,
source head-count caps and exact file size. H1/SSD and historical measurements
remain immutable. Full selected label rows complete before checking the
existing supplement budget. H1-first completion, protected results, OR sharing,
native terminal convergence and per-tier navigation cutoff remain in force.

#### Historical V1/V2/V3 reconstruction

`rebuildsparselabelhierarchy -c NATIVE_INI` derives a new, read-only snapshot
from a canonical five-level STATIC BKT source. `[RebuildHierarchy]` specifies
absolute `SourceIndex` and new `OutputIndex`. Historical `[SelectHead]
HierarchyLabelSelectivity=0.01,0.001,0.0001,0.00001` specifies decreasing
maximum fractions for H2..H5: choose the highest eligible tier, with inclusive
upper/exclusive lower boundaries and no terminal postings above 1%.
This is a new build-admission parameter, not the retired signature-domain
or query-selectivity controls. Normal full-index builders reject it explicitly.

Terminal rows are label-pure lists of H1 heads whose H region supports that
label, not just heads with that own label. Each sparse (label, H1-head) pair
must remain covered; dense members do not get terminal rows. Representatives
are sparse members and reuse the unchanged H1 vector owner. Extremely sparse
labels have direct H1 terminal rows at higher tiers, not empty H2 placeholders.
Spatial parent rows contain only these sparse descendants; an otherwise empty
admission tier can still contain navigation parents.

V3 constructs a native BKT over each label's supported H1 vectors, selecting
unique centers from the largest remaining spatial subtrees up to its fixed
terminal quota. Assign all supported heads with the existing native ANN/RNG
construction helper inside that label's pool.
Temporary assignment graphs use the source native BuildHead settings and the
reconstruction INI's thread count; they are never persisted or queried at runtime.
Source coarse addresses connect representatives and retain negative-anchor
reachability, but determine neither terminal membership nor representative count.
All direct terminal rows address H1 regardless of their logical tier. Their
requested count is `max(1, ceil(supported_heads * Ratio))`, not a repeated
`Ratio^(tier-1)` compression. Reserve source-tier capacity for spatial parents,
then apportion any remaining quota shortage across labels while retaining at
least one row per label; reject an impossible cap. References are capped by
the source `HierarchyReplicaCount`. V2 and V3 authenticate exactly
`min(HierarchyReplicaCount, label_postings)` distinct copies per supported head
and unique label-local representatives; experimental V1/V2 remain readable
with their original assignment and entry policies.
Total postings per tier cannot exceed the corresponding source
catalog size. This changes upper membership/replication, never H1/SSD replicas.
No label-combination catalogs or runtime per-label ANN graph are constructed.

V3 derives a packed reverse-owner CSR once from authenticated terminal rows:
every H1 head exposes all postings that actually contain it, plus its original
label-independent spatial entry. No per-query index scan or owner construction.
Only after H1 completes, take the nearest existing H1 results first within
`PostingAnchorCount`, then fill remaining anchor slots with already-scored
spatial anchors. Zero-match queries retain negative anchors and spatial entry.
No predicates are evaluated again to select anchors.
Native H1 finishes first and at most one existing
posting supplement uses the same shared distance frontier, touched-state
allocator, convergence pool, full-row consumption and checked-leaf budget.
Direct terminal rows at *any* tier populate the convergence pool; navigation
parents do not. A single spatial root makes very rare tiers reachable through
ordinary owner ascent. OR branches share native H1 deduplication. Dense-only,
unanchored and unfiltered queries have no sparse supplementation.
Label admission and terminal tier are build-time decisions; query execution
only checks missing H1 slots, orders discovered postings by distance, merges
OR candidates and enforces the original work budgets plus the optional shared
navigation distance beam above. Do not
introduce early graph-to-posting switching, graph resumption, an online
yield controller, or eviction of protected H1 results.

The new artifact authenticates support generation/content, H1 IDs, thresholds,
shape, full supported-head coverage and exact file size. H1 graph, vectors,
metadata, H/O posting payload and old experiment evidence remain immutable
symlink targets. Reject mutation/export of these read-only snapshots rather
than writing through those links. The explicitly authorized SIFT1B operation
rebuilds only this resident upper structure, after native reconstruction and
search regressions; it is not permission to rebuild H1/SSD or the dataset.
Current SIFT1B remains scalar categorical data; arbitrary multi-label-vector
storage is not implemented by this reconstruction.

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
  `[MultiTenant] InPlaceBuild=true` stages the native `[Base] IndexDirectory`
  as the manager's output root. Each `tenant_<id>` must be new; construction
  writes there directly without a `/tmp` posting copy or environment override.
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
