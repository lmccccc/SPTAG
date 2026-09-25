# H1 post-filter with sparse posting navigation

## Current implementation

The implementation is now part of main `AnnService` and the normal root
CMake library targets. [`native_postfilter/`](native_postfilter/README.md)
contains only native replay clients and the registered full-campaign runner;
it no longer owns a generated core, helper copies or independent build.
Main code preserves H1 result-level post-filter and unpruned navigation,
adding attribute/signature checks, the visited match bit and sparse posting
adjacency. The old diagnostic/qualification framework, supplementary own heap
and upper ANN query implementation have been removed.

The directories and build recipes documented below are historical evidence,
not alternative current implementations. They remain intact for reproduction;
do not chain them as inputs to the canonical implementation or interpret their
old "latest" descriptions as current guidance. The parent `CMakeLists.txt`
belongs to the original frozen shortcut prototype, not the canonical build.
Main build and replay instructions are in `native_postfilter/README.md`.
`EnablePostingNavigation=false` retains main H1 post-filter;
`true` enables signed auxiliary posting. Upper representative/CSR/signature
catalogs remain, but no independent upper ANN graph is loaded or queried.
Existing production binaries and dataset indexes have not been replaced.
The former isolated cleanup is historical evidence under
`comparisons/native_postfilter/20260918_cleanup/`; removed source bytes and
archive mappings are under `comparisons/main_postfilter_integration_20260919/`.
The all-query, six-scenario main comparison has a separate registration at
`comparisons/main_postfilter_full_20260919/` and is not a restart of old sweeps.

## Historical experiments and scope

**Historical architecture: synchronous H1 neighbor supplementation.**
[`supplier_native/README.md`](supplier_native/README.md) describes the separate
native BKT implementation: one genuine H1 frontier, a zero-fresh-neighbor
trigger, bounded helper-local H2/H3 posting choices, then immediate H1
continuation. It is not the alternating parent-queue policy below. Its own
matched native graph-only control separates supplementation from admission;
all previous policies and measurements remain distinct.

**The earlier online policy was implemented and measured separately:**
[`online_native/README.md`](online_native/README.md) documents query-ranked
eight-owner H2/H3 traversal, lazy CSR cursors, unified actual-distance budgets,
and the corrected all-evaluated-H1 native admission path. Its matched graph-only
admission controls isolate parent-routing effects. The new216-process certified
experiment improves filtered quality over original H1, but does **not** meet the
joint H1/H3 quality/cost goal; online routing generally loses to its matched
admission control. This is distinct from every fixed-overlay result below.

**Updated user criterion: evaluate the workloads jointly, not unfiltered speed
alone.** Unfiltered hybrid should be close to original H1; each filtered workload
should approach original H3 cost **at comparable final recall and underfill**.
The completed six-scenario matrix below does **not** meet that joint goal.
Unfiltered is close to H1, but the flattened shortcut policy retains H1-like,
poor filtered recall. No weighted workload mix or new tuning was introduced.

**Historical design mismatch with the subsequently clarified online fallback:** the
earlier fixed-shortcut experiments test an offline-flattened overlay, **not** online
nearest-of-eight parent navigation. Their negative filtered results must not
be presented as an evaluation of the later online implementation.

Implemented and executed, not merely proposed. The completed, corrected run is
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/h1_hierarchy_shortcut_20260915_v2/`.
There are 12,000 measured query/case observations, each returning 24 distinct
H1 ordinals, with counted/uncounted ordered IDs **and Float distances** equal.
All 39 protected input files retain their initial SHA-256/size/mtime.

**This initial policy did not beat ordinary wider H1 search at near-equal
high exact-head recall.** Rewiring gives a small quality gain at the low work
cap but is slower. This result does not rule out better shortcut construction.

**Native full-query integration is now validated separately**, in
`comparisons/h1_hierarchy_shortcut_full_20260915/`. See the full-query section
below. At cap2000, dataset Recall@10 is plain **90.68%**, add **90.71%**, rewire
**90.69%**, versus natural flat24 **90.73%**. Added/rewired shortcuts are
**2.88%/2.67% slower than capped plain** in three fresh paired repetitions.
There is no demonstrated end-to-end advantage. The original v2 navigation-only
results are preserved unchanged; their timings are not combined with full runs.
No Python substitute scans vectors or postings. Attributes do not influence
shortcut construction or H1 navigation/budgets; native exact filtering still
governs final-result admission in the scenario matrix.

### Clarified intended rule and the earlier design mismatch

For a current-layer item `x`, reverse posting membership gives its **eight
direct parents** `P(x)`. With the current query `q`, order those parents by
`squared L2(q, representative(parent))` and expand the nearest first.
This is **query-to-parent** distance, not `L2(x, parent)`. On moving upward to
parent item `p`, use **p's own eight direct parents** at the next layer, again
ranked against the same query `q`. The eight owners are an adjacent-layer
membership relation, not a fixed set of eight ancestors reused at every level.

The implemented policy instead chooses the nearest **two** H2 owners offline
using `x`-to-owner distances, chooses an H3 owner using distance from the same
H1 `x`, and flattens the resulting routes into fixed H1 edges. At runtime,
ordinary and shortcut H1 neighbors are still scored against `q` by native BKT;
there is **no online q-to-H2/H3 owner ranking or upward fallback**.

Also distinguish **eight replicas/owners** from the experiment's **at-most-eight
shortcut edges**: these are different quantities. The latter average only2.95
edges per H1 in the measured overlay. The original H3 reference is its existing
top-graph/CSR-descent implementation, not the proposed online upward policy.
No algorithm or benchmark was silently replaced after this clarification.
The later `online_native/` experiment implements a disclosed bounded one-pass
version of this online rule in a new source/binary/output tree. The original
`policy_interpretation.json` is a historical statement about the fixed-overlay
matrix, not the implementation status of that later experiment.

## Files and isolation

* `../HierarchicalShortcutBench.cpp`: native IO, CSR reverse ownership,
  deterministic portal construction, exact diagnostic oracle, search and output.
* `ShortcutHooks.h`, `instrument.py`: generated-source-only BKT adjacency,
  hard-distance-budget and profiling hooks. No class fields/virtuals change.
* `CMakeLists.txt`: reuses the existing clean pinned
  `2ac3ebcab562bc81cdb8c7c98b35ea72f2703c3b` compiler/Release/static-library recipe.
* `test_shortcut.py`, `HookTests.cpp`: native synthetic regression tests.
* `sift1m.ini`, `experiment.ini`: preregistered grid, input and execution controls.
* `PristineFlat.cpp`, `pristine.ini`: independently linked pristine-archive
  control for all-query result parity and current-host timing.
* `analyze.ini`, `../analyze_hierarchical_shortcut.py`: validation and paired
  exact-head diagnostics; `../run_hierarchical_shortcut.py`: provenance runner.

The existing `H2SweepBench.cpp` is included with its entry point renamed only
to reuse native readers, strict parsing, distance helpers and fixture creation;
its H2 sweep algorithm is not executed. The prototype operates on the existing
same-ordered **160,091 × Float128 H1 graph**, not an H2-only graph.
The complete existing H2=25,607 / H3=4,098 hierarchy supplies construction.
Both original CSRs declare exactly eight owners per lower node.

All sources and binaries used in the successful run are snapshotted. Production
files and clean upstream sources are untouched; new objects stay under
`toolchains/spann_upstream_2ac3ebc/shortcut-build`. No rebuild of the original
H1 graph or SSD index, downloads, commits or pushes occurred.

## Exact shortcut definition

CSR files are authenticated structurally as the existing V3 format: header104,
uint64 offsets, uint32 sorted unique members, and 32-byte signatures per parent.
Signatures and domain fields are never used for routing. Reverse membership
is built from CSR members, **not** the upper-to-lower representative maps.
Every lower node must have exactly its declared number of distinct parents.
Representative map sizes, ID uniqueness, byte-identical catalog vectors and
self ownership are validated at both levels.

For every H1 node `x`, offline:

1. Order its eight H2 owners by `(squared L2(x,H2[parent]), parent ordinal)`;
   retain the nearest two.
2. For each retained H2 owner `p`, propose `representative(p)` and the nearest
   non-`x` H1 member of `p`.
3. Select the nearest H3 owner `g` of `p` using distance from **x** to H3.
4. Within `g`'s H2 posting, select the nearest H2 member `s` which is **not**
   any of x's original H2 owners. Propose `representative(s)` and the nearest
   non-`x` H1 member of `s`. Skip this pair if no eligible sibling exists.
5. Keep the resulting deterministic proposal order; reject self edges,
   duplicates and every existing ordinary H1 adjacency. Never refill rejected
   slots by searching more parents.

Thus there are at most eight edges per H1 source, including genuine descent
to member H1s and H3-mediated crossing into a different H2 posting. The nearest
member scans are **offline construction only**. No query enumerates a parent
posting, runs an upper oracle, restarts a search or falls back to exhaustive
navigation. Both CSR layers participate; H3 is not replaced with an H2 sweep.
This is a flattened hierarchy-derived shortcut overlay, not a query-time
multilayer graph with separately scored H2/H3 frontier nodes.

Actual construction: 472,280 edges (2.9501 per H1); 808,440 self/original/duplicate
proposals rejected; 320,178 H3 sibling routes considered; 11.01 seconds.
The simple closest-parent/closest-member policy often proposes already-local
neighbors, leaving relatively few new edges. No broader tuning was performed.

## Search semantics, accounting and memory

The pristine BKT tree initialization, dynamic tree pivots, graph frontier,
visited hash, result queue and native termination remain in use. There is
one shared **graph** distance-priority frontier for ordinary and shortcut H1s,
not a second hierarchical frontier. BKT's pre-existing tree queue is retained.
All distances, including repeated tree-center evaluations, consume the same
hard callback budget in capped cases.

* `plain`: original adjacency.
* `add8`: original adjacency plus the retained bounded shortcuts.
* `rewire8`: replace the last `min(original positive degree, shortcut count)`
  ordinary neighbors with that many shortcuts. This preserves the original
  positive adjacency count even for short/empty rows; native negative tree
  sentinels are not interpreted as normal edges or overwritten.

Iteration interleaves four retained ordinary neighbors then one shortcut,
repeating until exhausted. All candidates share `CheckAndSet`, the same native
distance callback and the same graph queue insertion/termination rules.
Rewiring has equal **per-node** adjacency allowance, not guaranteed equal total
adjacency entries or distance calls across queries. Total entries/calls are
reported separately. Added mode is intentionally not claimed degree-neutral.

The hard cap counts every actual distance callback, not native `GetScanned` or
`MaxCheck`. At exhaustion, a private exception stops before the next distance;
already-scored graph frontier candidates are drained into the result queue
without further distances, then sorted. Native workspace is returned normally.
No fallback starts. This cap-stop rule is identical for plain/add/rewire, but
is explicitly an experimental extension of native natural termination.

Unprofiled searches disable the separate counter/adjacency instrumentation.
Decrementing the remaining work allowance is intrinsic algorithm cost and
remains enabled. An independent callback counter verifies the allowance
counter in profiled runs. All count-mode and timed-result parity checks occur
outside the timer. The timer includes wrapper setup/restoration, result
allocation/extraction, native workspace/tree/graph processing, required budget
checks and frontier draining. It excludes construction, exact oracle,
profiling, file output and diagnostic parity comparisons. Inherited native
result-extraction validity checks remain common to every mode.

Ordinary time is the mean of three repetitions per query; reported p95 in
JSON is across per-query means. Cases run sequentially after 1,000 warmups,
with one query thread and NUMA CPU/memory node2, not whole-process single-CPU
confinement. Timing order is not randomized, so small latency differences
must not be overinterpreted.

Edge file size: **3,169,872 bytes** (3.02 MiB); live vector-of-vectors capacity:
**6,132,616 bytes** (5.85 MiB), excluding allocator metadata. This overlay is
resident even in plain controls. **Rewire mode does not reclaim original
adjacency storage**, so it incurs this same physical extra memory despite
preserving logical degree. Hierarchy catalogs/CSR are released before search;
the diagnostic H1 vector copy is released after oracle construction.

Graph-only directed reachability from H1 ordinal0 is 160,088/160,091 for
plain and add, and 160,074/160,091 for rewiring. All nodes can reach ordinal0.
These diagnostics exclude BKT tree entry/sentinel links; they are not proof
that a query cannot find those nodes. They do show that the shortcut overlay
does **not** establish guaranteed graph connectivity, and rewiring can remove
useful connections.

## Preregistered grid and measured results

All cases use the first 1,000 queries, byte-verified between original NPY and
native XVEC, fixed graph/vector ordering, L2, top24, one query thread,
warmup1000/repeats3.

* Natural native controls: MaxCheck = 512, 2048, 4096.
* Capped grid: actual distance limit = 1200, 2000, 3200, each with
  plain/add/rewire; common native MaxCheck=2048.
* Shortcut degree bound8 / parent count2 fixed before evaluation.

**Recall below is exact-H1 top24 recall, NOT dataset Recall@10.**

| Case | Actual distance calls/query | Exact-H1 recall | Ordinary navigation µs |
|---|---:|---:|---:|
| Native MaxCheck512 | 821.566 | 90.2125% | 114.687 |
| Native MaxCheck2048 | 2232.031 | 99.3333% | 318.837 |
| Native MaxCheck4096 | 3923.522 | 99.9542% | 582.926 |
| Plain cap1200 | 1199.767 | 95.6583% | 212.769 |
| Add8 cap1200 | 1199.830 | 95.7292% | 233.497 |
| Rewire8 cap1200 | 1198.720 | 95.8958% | 244.914 |
| Plain cap2000 | 1948.164 | 98.9542% | 329.104 |
| Add8 cap2000 | 1956.172 | 98.9167% | 360.500 |
| Rewire8 cap2000 | 1919.489 | 98.9167% | 358.159 |
| Plain cap3200 | 2232.031 | 99.3333% | 340.387 |
| Add8 cap3200 | 2251.592 | 99.3208% | 401.604 |
| Rewire8 cap3200 | 2186.457 | 99.2667% | 401.105 |

At cap2000, near-equal head recall favors plain by 8.8–9.5% latency. At cap3200,
add/rewire are about 18% slower than the common-budget plain control. Natural
MaxCheck2048 is another 21.55 µs faster than capped plain, with exactly identical
ordered results and work. Compared with that natural control, hybrid cap3200
is about 26% slower at similar head recall; rewiring saves only 2.0% distances.

At cap1200, rewiring adds 0.2375 percentage points of head recall at essentially
equal distance work, but costs 15.1% more latency. A diagnostic paired-query
bootstrap (2,000 draws, fixed seed0) gives a 95% interval
[0.0583, 0.4043] percentage points for that gain; it is not a latency or
final-recall significance claim. Other paired intervals are in `analysis.json`.

The separately linked pristine archive returned **exactly equal ordered IDs
and Float distances on all 1,000 queries** at MaxCheck2048 and measured
306.106 µs, versus 318.837 µs for the instrumentable native control
(4.16% overhead in this run). This supports baseline semantic parity.
The previously measured ~132 µs standalone timing was from another run;
even the untouched archive now measures ~306 µs. Do not attribute that
cross-run change to shortcuts, or mix either with the frozen full-runtime
0.348 ms navigation timer.

## Native full-query integration — completed 2026-09-15

The initial prototype correctly rejected generic `SearchDiskIndex` as a
substitute: that helper omits the full query's H/O initialization, selected-own
point admission and deduplication state. Its earlier claim that matching frozen
source was unavailable was incorrect. Revision
`3552194536cb01dd70099e955a29e235a0cf4d2e`, the original build's `source.diff`
and `source.sha256.json`, and authenticated saved files reconstruct it.
**648 native source files were hash-verified**, with none missing. The clean
zstd submodule matches frozen revision `e47e674cd09583ff0503f0f6defd6d23d8b718d3`.
Critical reconstructed hashes:

* `SPANNIndex.cpp`: `d5fb4cee80efcef694730191c49147315ba2b423b23be1679dc319b890444fe2`
* `ExtraStaticSearcher.h`: `dd0430a22d30b82d4280173162930a5cbe3693b6fa3bf4c6c78221b4e9d8bef9`
* `SpannAclBench.cpp`: `95e700e3464cf81c0c974b4ceed30e8dda6a085b59d59c422efb5672e8384d0e`

### Integration boundaries and isolation

New implementation lives under `hierarchical_shortcut_native/full/`:

* `reconstruct.py`: fail-closed reconstruction into a **new** source directory.
* `integrate.py`: authenticated, idempotent generated-source patching.
* `FullHooks.h`: experiment-only native INI controls, overlay validation and
  query observations. The original `ShortcutHooks.h` policy is reused unchanged.
* `run.py`, `experiment.ini`, eight native search INIs: fixed paired protocol.
* `FullNativeTests.cpp`, `CMakeLists.txt`, `verify.py`: fixtures and independent
  validation of native outputs, including dataset recall.

Isolated source, static libraries, executable and compiler logs are under
`datasets/sift1m_zipf200_sparse193_numeric/toolchains/shortcut_full_20260915/`.
The benchmark links its **own** `source/Release/libSPTAGLibStatic.a`; it neither
rebuilds nor loads production `_SPTAG.so`. The measured executable and the
complete source archive are saved in the new result directory's `snapshot/`.
No original source, H1 graph, hierarchy CSR, posting payload or v2 result was
rewritten. All **226 protected files**, including current dirty production
core sources and the original v2 inputs, remain unchanged.

The authentic call chain remains:

```text
SpannAclBench -> TenantIndexManager::SearchWithPredicate
  -> SPANN::Index::SearchIndex
     -> existing flat m_index->SearchIndex(*p_queryResults)
        -> frozen native BKT tree, visited state, frontier and result queue
     -> unchanged head translation / own-point admission / dedup
     -> unchanged H/O posting setup and native ExtraStaticSearcher::SearchIndex
```

Only the flat H1 call is wrapped, inside the original `_phT0`/`_phT1`
navigation interval. Its callback is restored before head translation and SSD
processing. The frozen BKT's non-cross `expandEdges` uses the existing bounded,
4-ordinary/1-shortcut interleave; all native termination checks remain. The
cap catch drains already-scored frontier candidates and returns its workspace.
No head replay, whole-posting fallback, Python SSD implementation, H2-only
search, or new shortcut construction is used.

Experiment-only keys are consumed by the isolated benchmark's native
`Helper::IniReader`, not production setters: `ShortcutMode`, `ShortcutCap`,
`ShortcutProfile`, `ShortcutCapture`, `ShortcutOutput`, `ShortcutEdges`.
Unknown shortcut keys and invalid policies/types fail. H1Only, Float, top24,
single-query-thread and unfiltered full-path assumptions are checked by the
controls and hook. This is **not** a general-purpose filtered/concurrent API.

### Validation and measurement protocol

The first **1,000 queries**, top10, selected H1 count24, MaxCheck2048,
MaxDistRatio8, SearchPostingPageLimit15, NUMA CPU/memory node2 and one query
thread are unchanged. Every process has 1,000 immediately preceding warmups.
`ShortcutCapture=true` requests an additional **untimed pre-warmup** validation
pass; it is disabled before warmup and measured queries. The measured final
IDs/Float distances and head-work counters must equal that validation pass.
Selected H1s are exported from this untimed pass, never logged inside the
ordinary timed navigation.

Three repetitions rotate case order, including an untouched frozen executable
control; profile/plain order reverses in repetition2. The four integrated cases
share exactly the same binary, resident overlay and SSD controls.
`DumpHeads=0`, `LogPathStats=false` throughout timing. Only separate profile
runs enable phase clocks and callback/adjacency counters. Ordinary natural
mode does not wrap the callback or activate shortcut adjacency. Capped
unprofiled modes retain only the intrinsic allowance decrement.

Passed checks:

* Frozen flat24 and integrated natural mode: **exact ordered H1 IDs and Float
  distances on all 1,000 queries**, plus identical native dataset recall,
  postings, pages, scanning/dedup work. Frozen head logging ran separately.
  The historical executable does not export final IDs, so no unavailable
  frozen-final-ID comparison is claimed.
* All four integrated cases: **ordered final top10 IDs/Float distances and
  selected H1s match counted/uncounted modes and all three repetitions**.
  Dataset Recall@10 was independently recomputed from native outputs and truth.
* All measured posting FDs were observed with **O_DIRECT** to the unchanged
  `SPTAGFullList.bin`; no page-cache SSD substitute.
* Hard actual-callback cap holds on every query. Plain/add/rewire exhaust it
  on **837/862/790** queries; each reports exactly2,000 callbacks at exhaustion.
* **2/2 CTests**: invalid controls/overlay, native caps1/8/64/100000 across
  policies, counted/uncounted parity, independent allowance/callback agreement,
  workspace/callback restoration, short-row degree and sentinel behavior.

### Full-query results

Means over three ordinary repetitions; all work is per query. “Total distances”
is **actual H1 callback calls + native SSD distance evaluations**, including
all BKT tree-center calls, not native MaxCheck or `GetScanned`.

| Native full-query case | Dataset Recall@10 | Postings | Pages | H1 distances | SSD distances | Total distances | Ordinary ms | QPS¹ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Untouched frozen flat24 | 90.73% | 23.828 | 143.651 | — | 918.673 | — | 0.950732 | 1051.82 |
| Integrated natural flat24 | 90.73% | 23.828 | 143.651 | 2231.960 | 918.673 | 3150.633 | 0.965572 | 1035.66 |
| Plain cap2000 | 90.68% | 23.830 | 143.666 | 1948.903 | 918.616 | 2867.519 | 0.962759 | 1038.68 |
| Add8 cap2000 | 90.71% | 23.829 | 143.688 | 1957.123 | 918.833 | 2875.956 | 0.990477 | 1009.61 |
| Rewire8 cap2000 | 90.69% | 23.829 | 143.695 | 1921.073 | 918.886 | 2839.959 | 0.988440 | 1011.70 |

¹ `1000 / mean ordinary milliseconds`; per-run native QPS is also retained.
The integrated natural control has **1.56%** more outer latency than the
untouched executable in this run; common hook/observation/result-export
bookkeeping is not free. The original frozen binary has no callback counter,
so its total-distance cell is deliberately not filled from another executable.

Separate **profiled** phase means (ms; never spliced into ordinary QPS):

| Case | Navigation | Posting retrieval minus scan | Posting scan |
|---|---:|---:|---:|
| Natural | 0.395119 | 0.499090 | 0.064328 |
| Plain cap2000 | 0.370644 | 0.505836 | 0.065095 |
| Add8 cap2000 | 0.419601 | 0.491958 | 0.063139 |
| Rewire8 cap2000 | 0.417219 | 0.495018 | 0.065054 |

Retrieval-minus-scan includes native scheduling and retrieval overhead; it is
**not physical SSD delay**. The exception cap interrupts the frozen BKT's
normal `GetScanned` finalization, so capped `PhaseTime.headScanned` underreports
work and must not be used as a distance count. Explicit callback counts above
remain complete; SSD work and final results are unaffected.

Add/rewire cost **+27.718/+25.681 µs** against capped plain, respectively
**+2.88%/+2.67%**. Their paired excesses across repetitions are
`[42.025,29.063,12.065]` / `[42.981,24.009,10.053]` µs. Rewire saves **0.96%**
of total distance work but does not save latency. Net final-recall gains over
capped plain are only **3/1 additional true neighbors out of10,000**; both
remain below natural flat24. Three repetitions on this host do not justify a
small-effect significance claim.

This remains an **offline flattened cross-scale overlay**, not a runtime
hierarchical frontier. Both existing H2/H3 CSRs inform its preserved472,280
edges. The existing connectivity and extra-memory limitations still apply;
rewiring can remove useful H1 links. No SIFT1B or broader tuning was performed.

### Full-query reproduction and persistent evidence

From `/mnt/nvme/baotonglu/mocheng`, rebuild only the isolated tree:

```sh
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/full/integrate.py \
  datasets/sift1m_zipf200_sparse193_numeric/toolchains/shortcut_full_20260915/source
cmake -S datasets/sift1m_zipf200_sparse193_numeric/toolchains/shortcut_full_20260915/source \
  -B datasets/sift1m_zipf200_sparse193_numeric/toolchains/shortcut_full_20260915/build \
  -DCMAKE_BUILD_TYPE=Release -DSPDK=OFF -DROCKSDB=OFF -DTBB=ON -DLIBRARYONLY=ON
cmake --build datasets/sift1m_zipf200_sparse193_numeric/toolchains/shortcut_full_20260915/build \
  --target spannaclbench --parallel 4
cmake -S SPTAG/Tools/benchmarks/hierarchical_shortcut_native/full \
  -B datasets/sift1m_zipf200_sparse193_numeric/toolchains/shortcut_full_20260915/tests \
  -DSPANN_ROOT=/mnt/nvme/baotonglu/mocheng/datasets/sift1m_zipf200_sparse193_numeric/toolchains/shortcut_full_20260915/source \
  -DCMAKE_BUILD_TYPE=Release
cmake --build datasets/sift1m_zipf200_sparse193_numeric/toolchains/shortcut_full_20260915/tests --parallel 2
ctest --test-dir datasets/sift1m_zipf200_sparse193_numeric/toolchains/shortcut_full_20260915/tests --output-on-failure
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/full/run.py \
  --config SPTAG/Tools/benchmarks/hierarchical_shortcut_native/full/experiment.ini
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/full/verify.py \
  --config SPTAG/Tools/benchmarks/hierarchical_shortcut_native/full/experiment.ini
```

`run.py` refuses an existing output directory: explicitly choose a new
`OutputDirectory` in the repository-owned experiment INI before repeating.
Do not rerun into or modify the preserved result. `verify.py` creates a new
validation snapshot and likewise refuses to overwrite it.
For reconstruction from scratch, `full/reconstruct.py --help` exposes explicit
repo, original build-snapshot, new output and optional authenticated-cache paths;
the measured source is also self-contained in `snapshot/source.tar.gz`.

The completed directory contains `summary.json`, all **29 native runs** in
`runs.json`, native commands/stdout/stderr/usage/O_DIRECT observations per run,
`queries.jsonl` with selected heads and final results, `frozen_head_parity.json`,
`provenance.json`, `input_verification.json`, and `validation.json`.
`snapshot/` preserves the measured binary, generated/frozen source and build
logs; `validation_sources/` preserves the later regression tests and verifier.

## Joint unfiltered/filter acceptance matrix — completed 2026-09-15

The clarified criterion supersedes judging this prototype by whether it beats
H1 on unfiltered queries. The earlier navigation-only v2 and completed
unfiltered full-query experiment remain unchanged. This new experiment uses
`full_scenarios/` and
`comparisons/h1_hierarchy_shortcut_scenarios_20260915/`.

### Actual available workloads

All six requested types already exist in the original `query/workloads.json`;
all use the same first1,000 Float128 query vectors and independent top10 truth.
No predicates, labels, data or groundtruth were generated for this experiment.

| Scenario | Existing predicate | Eligible dataset points |
|---|---|---:|
| unfilter | no predicate | 1,000,000 |
| broad_tag | categorical column0 = 0 | 170,092 |
| medium_tag | categorical column0 = 9 | 17,009 |
| extreme_tag | categorical column0 = 200 | 193 |
| numeric | numeric column1 ≤ 42,940,696 | 10,000 |
| mixed_dnf | tag200 OR (tag199 AND numeric column1 ≤ 2,147,483,647) | 608 |

Categorical fixtures are `query_tags_{broad,medium,extreme}.npy`; numeric and
mixed fixtures are `query_dnf_{numeric,mixed}.npy`. Truth filenames and hashes
come from the original manifest. The “sparse” representative is named
**extreme_tag** in these actual artifacts; it is not a newly fabricated task.

### Native implementation and validation

A **second** authenticated source/library/executable was built under
`toolchains/shortcut_scenarios_20260915/`. The prior toolchain and measured
binary were not overwritten. `full_scenarios/integrate.py` extends the already
validated integration by:

* Allowing native filtered requests through the same flat H1 hook, without
  changing its selected heads, adjacency, budget or termination by predicate.
* Capturing head results at the common pre-translation boundary, including the
  original H3 path, only during the untimed validation pass.
* Counting original H3 top-BKT and CSR/own-head distance callbacks in separate
  profile runs. Both distance functions are restored before the SSD stage.
  H3's native CSR member-entry/distance counters are retained, not replaced.

Original H1, original H3, add8-cap2000 and rewire8-cap2000 run **every scenario**.
H3's legacy INI spelling `HeadNavigationMode=H2Only` selects the highest loaded
**H3 graph plus H3→H2→H1 descent**, not an H2-only index. H1 and both hybrids use
`H1Only`; the hybrid overlay still derives from both original H2/H3 CSRs.

All cases share top10, posting/result budget24, MaxCheck2048,
HierarchyMaxCheck512, HierarchyInitialProbeRatio0.666666, MaxDistRatio8,
SearchPostingPageLimit15, one query thread and NUMA CPU/memory node2.
Hybrid cap2000 is unchanged across every predicate. Original H3 retains its
**historical native** signature/result-admission and saved-frontier behavior;
the experiment does not alter it or add such predicate-dependent navigation
to the hybrid. `HierarchyGraphSignaturePruning=false` in every control.

There are **168 native processes**: 24 initial baseline/parity controls and
144 measured plain/profile runs (six scenarios × four cases × three paired
repetitions × two instrumentation modes). Scenario/case order rotates and
profile/plain order alternates. Each measured run has an untimed1,000-query
capture pass, then1,000 warmups, then1,000 measured queries. Main timing has
`DumpHeads=0` and `LogPathStats=false`; phase profiling remains separate.

Validation passed:

* Reconstructed H1 **and H3** match untouched frozen **ordered selected-H1
  IDs/Float distances and native final recall/work** for all six scenarios.
  Frozen final-result IDs are not exported, so their parity is not invented.
* All24 scenario/case combinations have identical ordered selected heads and
  final IDs/Float distances across counted/uncounted runs and repetitions.
  Profiled actual callback/adjacency/CSR counts also repeat exactly.
* H1 and both hybrids have **identical per-query head IDs, distances, callback
  counts, adjacency entries and exhaustion across all six predicates**.
  No predicate-dependent hybrid navigation, budget, retry or fallback appeared.
* Final Recall@10 was independently recomputed. Every returned ID satisfies its
  original exact predicate; duplicate and invalid results fail validation.
  Underfill counts retain the top10 denominator rather than hiding misses.
* All144 main runs observe native O_DIRECT posting FDs. **535 protected files**
  remain unchanged, including both prior completed result directories and their
  binaries, production sources, graphs/postings, queries and truth.
* Three focused scenario fixtures pass: numeric/DNF truth tables, underfilled
  and empty-result accounting, exact-filter and duplicate rejection. The
  preceding integration's two native hard-cap/restoration fixtures remain
  preserved; full-scenario count-mode and frozen parity additionally exercise
  the new H3 callback wrapper on real queries.

### Quality and full-query latency

Each cell is **dataset Recall@10 (%) / ordinary full-query mean ms** over three
fresh repetitions. QPS and individual repetitions are in `summary.json`.
These numbers are not spliced with either earlier experiment.

| Scenario | Original H1 | Original H3 | Add8 cap2000 | Rewire8 cap2000 |
|---|---:|---:|---:|---:|
| unfilter | 90.73 / 0.953832 | 88.60 / 0.791462 | 90.71 / 0.989143 | 90.69 / 0.981509 |
| broad_tag | 72.25 / 0.700055 | 87.67 / 0.812352 | 72.16 / 0.741236 | 72.15 / 0.736709 |
| medium_tag | 19.77 / 0.485224 | 86.28 / 0.721291 | 19.81 / 0.521748 | 19.77 / 0.517271 |
| extreme_tag | 0.28 / 0.372542 | 90.07 / 0.437247 | 0.28 / 0.414026 | 0.28 / 0.410155 |
| numeric | 30.44 / 0.750557 | 42.18 / 0.707073 | 30.45 / 0.805272 | 30.40 / 0.789619 |
| mixed_dnf | 1.04 / 0.388125 | 76.10 / 0.503805 | 1.04 / 0.423787 | 1.04 / 0.424607 |

### Per-scenario acceptance comparisons and underfill

Reference = **H1 for unfilter; H3 for every filter**. Ratios below are descriptive,
**not matched-recall speedups** where recall differs. No user-defined mixture
or numeric “similar enough” tolerance was supplied, so there is no aggregate
score or invented acceptance threshold.

| Scenario | Add latency/reference | Rewire latency/reference | Underfilled queries: H1 / H3 / Add / Rewire |
|---|---:|---:|---:|
| unfilter | 1.037× | 1.029× | 0 / 0 / 0 / 0% |
| broad_tag | 0.912× | 0.907× | 0 / 0 / 0 / 0% |
| medium_tag | 0.723× | 0.717× | 45.9 / 0 / 45.9 / 46.0% |
| extreme_tag | 0.947× | 0.938× | 99.7 / 0.6 / 99.7 / 99.7% |
| numeric | 1.139× | 1.117× | 54.1 / 8.3 / 53.6 / 54.1% |
| mixed_dnf | 0.841× | 0.843× | 99.7 / 0 / 99.7 / 99.7% |

Unfiltered is close to H1: add/rewire lose only0.02/0.04 percentage points of
recall with3.7%/2.9% additional latency and no underfill. That is compatible with
the requested direction, although no formal tolerance was specified.

**The filtering requirement is not met.** Relative to H3, hybrid recall loses
15.51–15.52pp on broad, 66.47–66.51pp on medium, 89.79pp on extreme,
11.73–11.78pp on numeric, and75.06pp on mixed DNF. Some hybrids read fewer
postings and finish sooner only because they fail to discover relevant results.
Numeric is slower **and** less accurate; its H3 reference itself reaches only
42.18% recall at this fixed budget, so this is not a high-recall numeric claim.

### Native graph, CSR and SSD work

Per-query means. **G** = actual graph distance callbacks, including tree centers;
**C** = actual CSR/own-head distances; **E** = traversed CSR member entries.
H3 G is the independently counted head total minus native C. Hybrid C/E are
zero because its overlay is flattened offline, not a query-time CSR descent.
Total = G + C + native SSD distance evaluations. None of these columns use
the capped BKT's incomplete `PhaseTime.headScanned` telemetry.

| Scenario / case | G | C | E | Postings | Pages | SSD distances | Total distances |
|---|---:|---:|---:|---:|---:|---:|---:|
| unfilter H1 | 2231.960 | 0 | 0 | 23.828 | 143.651 | 918.673 | 3150.633 |
| unfilter H3 | 607.998 | 1433.351 | 1852.928 | 23.809 | 143.101 | 913.684 | 2955.033 |
| unfilter Add | 1957.123 | 0 | 0 | 23.829 | 143.688 | 918.833 | 2875.956 |
| unfilter Rewire | 1921.073 | 0 | 0 | 23.829 | 143.695 | 918.886 | 2839.959 |
| broad H1 | 2231.960 | 0 | 0 | 7.333 | 40.823 | 153.371 | 2385.331 |
| broad H3 | 607.998 | 899.453 | 1852.981 | 24.000 | 133.697 | 469.746 | 1977.197 |
| broad Add | 1957.123 | 0 | 0 | 7.331 | 40.784 | 153.204 | 2110.327 |
| broad Rewire | 1921.073 | 0 | 0 | 7.338 | 40.855 | 153.521 | 2074.594 |
| medium H1 | 2231.960 | 0 | 0 | 0.814 | 4.212 | 14.284 | 2246.244 |
| medium H3 | 607.998 | 577.489 | 2005.356 | 23.912 | 119.365 | 333.056 | 1518.543 |
| medium Add | 1957.123 | 0 | 0 | 0.816 | 4.228 | 14.326 | 1971.449 |
| medium Rewire | 1921.073 | 0 | 0 | 0.814 | 4.215 | 14.290 | 1935.363 |
| extreme H1 | 2231.960 | 0 | 0 | 0.011 | 0.042 | 0.069 | 2232.029 |
| extreme H3 | 611.737 | 35.021 | 2880.968 | 8.613 | 35.044 | 49.855 | 696.613 |
| extreme Add | 1957.123 | 0 | 0 | 0.011 | 0.042 | 0.069 | 1957.192 |
| extreme Rewire | 1921.073 | 0 | 0 | 0.011 | 0.042 | 0.069 | 1921.142 |
| numeric H1 | 2231.960 | 0 | 0 | 9.019 | 60.189 | 9.072 | 2241.032 |
| numeric H3 | 607.998 | 1433.351 | 1852.928 | 8.993 | 59.929 | 9.058 | 2050.407 |
| numeric Add | 1957.123 | 0 | 0 | 9.016 | 60.166 | 9.076 | 1966.199 |
| numeric Rewire | 1921.073 | 0 | 0 | 9.003 | 60.101 | 9.054 | 1930.127 |
| mixed H1 | 2231.960 | 0 | 0 | 0.066 | 0.261 | 0.257 | 2232.217 |
| mixed H3 | 607.998 | 105.486 | 2902.353 | 14.162 | 53.374 | 45.810 | 759.294 |
| mixed Add | 1957.123 | 0 | 0 | 0.066 | 0.261 | 0.257 | 1957.380 |
| mixed Rewire | 1921.073 | 0 | 0 | 0.066 | 0.261 | 0.257 | 1921.330 |

Separate profile navigation/CSR/scan/retrieval phases, graph adjacency entries,
selected-H1 counts, scanned records and dedup counts are retained per case in
JSON. Retrieval-minus-scan remains a native software interval, not physical
SSD delay; profile timings are not substituted for ordinary full-query timing.

### Remaining limitation and reproduction

There are **no missing scenario, source, build or native-API prerequisites** for
this bounded matrix. The blocker is algorithmic: a geometric H1 shortcut overlay
does not reproduce H3's predicate-relevant candidate discovery and admission of
own-head points encountered during CSR descent. Normal H1 semantics admit only
its selected head results before SSD search. For rare/mixed predicates, almost
none of the24 selected spatial H1s supply relevant postings; the native H3 path
finds many more. Similar latency with99.7% underfill is not comparable quality.

No shortcut redesign, per-predicate budget, routing switch, retry, whole-posting
fallback or extra parameter search was added to hide that difference. A future
design must address this candidate/admission gap; the present flattened policy
is **not** a successful implementation of the clarified joint acceptance goal.

Exact executed build/run sequence, from the repository's parent directory:

```sh
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/full/reconstruct.py \
  --repo SPTAG \
  --snapshot datasets/sift1m_zipf200_sparse193_numeric/build_runs/20260909T141212Z_lightweight_rescue \
  --output datasets/sift1m_zipf200_sparse193_numeric/toolchains/shortcut_scenarios_20260915 \
  --authenticated-cache /home/baotonglu/.copilot/session-state/40fc7ebb-e617-4ec3-a17b-610068e95e5e/files
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/full_scenarios/integrate.py \
  datasets/sift1m_zipf200_sparse193_numeric/toolchains/shortcut_scenarios_20260915/source
cmake -S datasets/sift1m_zipf200_sparse193_numeric/toolchains/shortcut_scenarios_20260915/source \
  -B datasets/sift1m_zipf200_sparse193_numeric/toolchains/shortcut_scenarios_20260915/build \
  -DCMAKE_BUILD_TYPE=Release -DSPDK=OFF -DROCKSDB=OFF -DTBB=ON -DLIBRARYONLY=ON
cmake --build datasets/sift1m_zipf200_sparse193_numeric/toolchains/shortcut_scenarios_20260915/build \
  --target spannaclbench --parallel 4
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/full_scenarios/test_scenarios.py
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/full_scenarios/run.py \
  --config SPTAG/Tools/benchmarks/hierarchical_shortcut_native/full_scenarios/experiment.ini
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/full_scenarios/verify.py \
  --config SPTAG/Tools/benchmarks/hierarchical_shortcut_native/full_scenarios/experiment.ini
```

The source and output directories already exist and are deliberately protected
against overwrite. Use explicit **new** directories and corresponding
repository-owned experiment INI paths for any reproduction. Original native
search INIs are copied unchanged; no environment data/search overrides are used.
The result directory preserves `snapshot/`, all native commands/logs/IO proofs,
`runs.json`, `summary.json`, `validation.json`, `independent_validation.json`
and `acceptance.json`. No weighted aggregate is produced.

## Reproduce

Run from `/mnt/nvme/baotonglu/mocheng`. Existing output directories are refused;
to repeat, explicitly change only output directories in repository-owned INIs
to new paths. Never overwrite the preserved experiment. No environment data
or search overrides are accepted by the launchers.

```sh
cmake -S SPTAG/Tools/benchmarks/hierarchical_shortcut_native \
  -B datasets/sift1m_zipf200_sparse193_numeric/toolchains/spann_upstream_2ac3ebc/shortcut-build \
  -DSPANN_UPSTREAM_BUILD=/mnt/nvme/baotonglu/mocheng/datasets/sift1m_zipf200_sparse193_numeric/toolchains/spann_upstream_2ac3ebc/build \
  -DCMAKE_BUILD_TYPE=Release
cmake --build datasets/sift1m_zipf200_sparse193_numeric/toolchains/spann_upstream_2ac3ebc/shortcut-build --parallel 2
ctest --test-dir datasets/sift1m_zipf200_sparse193_numeric/toolchains/spann_upstream_2ac3ebc/shortcut-build --output-on-failure
python3 SPTAG/Tools/benchmarks/run_hierarchical_shortcut.py \
  --config SPTAG/Tools/benchmarks/hierarchical_shortcut_native/experiment.ini
numactl --cpunodebind=2 --membind=2 \
  datasets/sift1m_zipf200_sparse193_numeric/toolchains/spann_upstream_2ac3ebc/shortcut-build/bin/pristineflatbench \
  --config /mnt/nvme/baotonglu/mocheng/SPTAG/Tools/benchmarks/hierarchical_shortcut_native/pristine.ini
python3 SPTAG/Tools/benchmarks/analyze_hierarchical_shortcut.py \
  --config SPTAG/Tools/benchmarks/hierarchical_shortcut_native/analyze.ini
```

Validation passed: **2/2 CTests**, plus all-query SIFT1M parity/accounting and
protected-file hash validation. Fixtures cover reverse ownership, both CSR
layers, map identity, deterministic/self-free/deduplicated bounded edges,
short/empty-row degree-preserving rewiring, native-object and count-mode
parity, hard caps, malformed/truncated CSR, invalid offsets/members/maps and
unknown/duplicate/invalid INI inputs.

The original run directory without `_v2` is retained with `SUPERSEDED.json`.
Its post-run audit exposed a short-row rewiring bound bug on 62 original graph
rows; the hook was corrected, an explicit regression added, and the **same**
preregistered grid rerun. Results were not cherry-picked or tuned between runs.

## Persistent outputs

* `native/input.ini`, `snapshot/`: immutable run configuration and source/binary.
* `native/shortcuts.bin`: magic `H13EDGE1`, int32 H1 count, int32 bound8,
  uint64 offsets[H1+1], then int32 destination ordinals. Rewire consumes only
  the prefix permitted by each source's original positive degree.
* `native/structure.json`: both hierarchy counts, owner replicas, memory,
  construction statistics and graph-only reachability.
* `native/queries.jsonl`: selected H1 ordinals, exact native distances,
  total callback calls, actual adjacency entries, shortcut-specific distance
  calls, cap-exhaustion flag, exact-head recall and ordinary microseconds.
* `native/oracle_h1.jsonl`: offline exact-H1 top24, never a search input.
* `native/summary.jsonl`, `summary.json`: means and per-query-mean p95.
* `provenance.json`, `input_verification.json`: original H1/H2/H3 files, maps,
  SSD/index files, existing binaries, queries/truth and protected `_SPTAG.so`
  hashes; 39 files unchanged.
* `pristine/`, `pristine_validation/`, `analysis.json`: untouched-archive
  control, code/binary provenance, all-query Float parity and paired diagnostics.
* `native/COMPLETE`, `status.json`: explicitly **navigation-only** completion.
