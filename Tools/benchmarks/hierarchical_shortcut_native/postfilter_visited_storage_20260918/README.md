# Native visited storage successor — bounded, isolated

Successor to sealed `postfilter_visited_match_20260918`. No threshold, policy,
predicate or admission change. `VisitedMatchMode=graph|match|posting`,
`PostingNeighborMatchRatio=0.01`. Production, predecessor, modules, index,
plots and OPERATOR_STOP are protected by verified hashes. No promotion.

## Root cause and storage

Predecessor `WorkSpace::Reset` selected compact storage, freeing the uint64
allocation and allocating uint32. Each filtered match/posting query immediately
reversed that transition in `SearchIndexWithNativeHooks`: **two backend
allocations/conversions per steady query**. Reset now clears occupied storage,
disables query matching and releases callbacks without changing its allocation.
Enabling query matching only changes key interpretation and validates the bound.

Native physical IDs are `[0,GetNumSamples())`; `SizeType` and the sample count
are signed32. Thus even count INT_MAX has highest valid ID INT_MAX-1:
unsigned `id+1` fits low31. Bit31 is the match flag in the **same uint32 slot**.
The whole flagged slot is never hashed or compared as a key. Both native hash
blocks, collision growth, and all occupied bits are retained correctly.
Unsigned constants/shifts avoid signed encoding overflow.

`EnableNativeMatch(count)` validates the explicit signed sample bound; it
rejects negative/out-of-bound IDs, including INT_MAX, before insertion.
This is not a new restriction on the generic table:
`EnableMatch(true)` accepts the entire old nonnegative signed32 domain.
It promotes lazily to uint64 only when matching INT_MAX itself, converts existing
key/bit pairs once, and keeps that sole wide allocation across future resets
and mode changes. Generic unqualified compact32 still accepts INT_MAX directly.
No simultaneous persistent compact/wide allocations and no widening of
`resultCheckStatus` or unrelated tables. Allocation addresses stay stable unless
capacity grows, an explicit Init occurs, or generic full-range promotion is needed.

Build/load use signed32 dataset row headers and `GetNumSamples()` returns
`Dataset::R()`. `Dataset::At()` enforces `0 <= id < R()`. Cross-node query
locator encoding explicitly rejects `encoded >= MaxSize`; this experiment
uses physical non-cross H1. Not all legacy malformed negative-row/overflow
build/load inputs are comprehensively validated; that unrelated hardening is
not claimed. Packed enable rejects a negative count and every match insertion
checks the validated bound. No successful valid native ID is narrowed.

## Unchanged semantics

One probe returns visited plus static posting-OR-own match, including live
collapsed aliases under the same immutable snapshot guards. First insertion
evaluates once; repeated bit reads and growth do not evaluate. Tree seed,
ordinary and accepted auxiliary insertion use the same path. Rejected absent
auxiliary entries stay absent and can be reevaluated. Native own/posting
component admission and independent compact result dedup remain unchanged.

Finish ordinary rows first, using physical full-neighbor d/e including visited
IDs. Only completed rows with d>0 and e<.01*d may activate posting. Ordinary
per-edge budget, queue, bridge and native result callback behavior is unchanged.
Signature precedes representative/CSR access; selected CSR rows complete even
across budget. No new caches, prescan, eligibility array, tuning or heuristic.
Native unfilter bypasses matching; graph requires no supplier metadata.
The match callback remains type-erased only at first insertions (including tree
entry); there is no callback dispatch on cached bit reads. No unrelated native
distance/result callback rewrite was attempted.

## Diagnostic protocol

`OptHashPosVector::StorageDiagnostics` is explicitly enabled only for untimed
queries. Counts cover **navigation visited backend arrays only**, not all query
allocations, not result tables, heaps, callback objects or trace vectors.
Allocation means a successful backend `new[]` (Init, growth/retry, promotion);
conversion means compact key/bit data converted to wide. A nonempty clear is
counted as a reset; scalar match-mode switches are separate and are NOT backend
conversions. Capture-off timing does not increment these counters or run oracles.

Each process has 1000 warmup + an independent 1000 capture-off untimed storage
proof + 1000 timed + 1000 untimed semantic capture queries (32 each for functional
replays). Timing is entered only after every proof query has zero allocation and
conversion. `.storage.u64` records allocations, conversions, allocated bytes,
nonempty clears, scalar mode changes, slot bytes, persistent bytes, total
two-block capacity, occupied slots, independent result bytes and result width.
Load factor is occupied/total two-block capacity. Logical probe counts are
not collision-slot reads; collision iterations are not instrumented.

Exactly **SIX** ordinary processes: Broad graph, match, posting; reversed second
repetition. Sparse32/unfilter32 only, no full sparse campaign. Native INIs alone:
n24, top10, offset0, thread1, NUMA CPU/memory2, MaxCheck2048, hierarchy512,
ratio .666666, pages15, unchanged buffered index. Captures do not feed policy.
Timed-body SHA remains
`9f412c63b71f7310e09180dff0858ccef3957bd20d7a96c34fdb440e3d559d53`.

```sh
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_visited_storage_20260918/prepare.py --initialize
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_visited_storage_20260918/build.py
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_visited_storage_20260918/run.py
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_visited_storage_20260918/finalize.py
```

Fresh Release static build, SPDK/ROCKSDB off, -j2. Run fixtures before timing.
Final results, exact source references, measurements and limitations are sealed
under `datasets/sift1m_zipf200_sparse193_numeric/comparisons/postfilter_visited_storage_20260918`.
Only same-core graph/match is an exact-trace tax; posting trajectories differ.
Predecessor timings are historical, not fresh-paired. Two repetitions are
descriptive, not a universal speedup or significance claim. After seal: **IDLE**.
