# Native H1 reuse, bounded 20260917

This replaces the old experimental search adapter in a **new isolated runtime**.
It is not a production routing change or another full sweep. The H1 algorithm
comes from authenticated revision `3552194536cb01dd70099e955a29e235a0cf4d2e`
plus its saved source diff. Completed Phase1/Phase2 and all index bytes remain
unchanged.

## Removed versus retained

Removed from the runtime: `Supplier.h`/`Engine`, `NativeAdapter.h`,
`ShortcutHooks.h`, `BenchmarkRestore`/distance interception, sample-pointer
division/identity wrappers, H1 score and qualification epoch caches, custom
admission dispatch through Score, the parallel spatial H1 result heap, and the
Phase2 intrinsic cache/revision locks. There are no ordinary per-distance trace
hashes, diagnostic ledgers or fine timers.

Retained: the original BKT primitive, native visited/result-check state,
`m_NGQueue`, `m_SPTQueue`, `m_Results` bound, H1 `QueryResultSet`, native stopping
and original full SPANN SSD processing. The existing SPANN own-point top-k heap
is retained: it is a dataset-result admission helper, not a shadow H1 heap.

The necessary extensions are:

* `SearchIndexWithNativeHooks` exposes the **existing private native search
  dispatcher's separate result and traversal filters**. The old public
  traversal API binds both to one predicate, which cannot distinguish an
  own-only head from a usable posting.
* Workspace-local known-distance admission notifications reuse SPANN's
  `admitHeadPoint` at native candidate admission. The old Boolean-only public
  filter cannot supply that distance without interception or recomputation.
* A local adjacency injection hook borrows the native row. The original
  `expandEdges` lambda is lifted out of the pop loop, with explicit current-node
  arguments, so both startup and normal injection reuse the **same candidate
  processing code**, not a second scoring/enqueue implementation.
* Terminal BKT candidates use the native traversal predicate before distance.
  Structural centers retain their routing exception; the existing collapsed
  group handling and native checked-leaf accounting are retained.

Ordinary graph distances still call the unmodified `m_fComputeDistance`.
Its actual `std::function` target is checked as a native function pointer in
every query. Diagnostic observations are conditional; startup records a
reachable entry anchor after native tree distances, without intercepting them.

## Posting-only policy

`PostingSupplier.h` owns only synchronous owner/CSR expansion. There is no H1
visited mirror, H1 score cache or persistent upper search queue. Parent distance
reuse is restricted to upper representative ranking; representatives are not
automatically injected as own points.

For distinct genuine ordinary neighbors, `d<16` never supplements; otherwise
`e/d<0.5` triggers, with goal `ceil(0.5*d)`. Equality does not trigger. Visited
does not reduce eligibility. Supplied connectivity is distinct across ordinary
and supplied IDs. Signatures reject upper rows before query representative
distance or row scans. Every selected row finishes, even after satisfying the
deficit or crossing native MaxCheck; that same native budget is checked before
the next row. There are no new distance/member/row caps.

The fixed eight-owner inverse from the existing CSR is prepared once **during
native index loading**, never as a query scan/rescue. It uses185698 fixed arrays
of8 int32 IDs:5942336 bytes. This metadata also loads in the matched H1 process.
No vectors, physical graph, postings or hierarchy are rebuilt/copied. The
historical top H3 graph remains loaded backing, not an upper query frontier.

## Protocol and reproduction

Checked-in native INIs are authoritative. The sole array syntax remains
`[SearchSweep] NProbe=[16,24,384]`, using the unchanged shared parser. Old caps,
hot-path selectors and intrinsic-cache knobs are rejected in this version.

`prepare.py` authenticates the parent and creates a separate source/runtime.
Build its `spannaclbench` with CMake Release, `SPDK=OFF`, `ROCKSDB=OFF`.
Configure this directory's CMake tests with `SPANN_ROOT` pointing at that source.
`integrate.py` records the reproducible native transformations; `sync_draft.py`
is restricted to pre-measurement development and refuses any measured runtime.

`run.py fixtures` runs H1/supplier forward/reverse16/24/384 and four8-query
filtered fixtures. `run.py measure` runs only unfilter24:1000 warmup,
1000 measured queries,1 query thread, NUMA CPU/memory node2, O_DIRECT/page15,
H1->supplier then supplier->H1; two native profiles are separate. There is no
full-grid or resume entrypoint.

The benchmark verifies ordinary/capture final IDs and mandatory work parity.
Expensive ledgers come from the untimed native capture and are labelled as such.
`report.py` rehashes captures, native sources and protected inputs, checks actual
load/component logs, and records baseline/prototype result differences.
Native phase profiling is not spliced into ordinary latency; `io` is retrieval
minus scan, not physical SSD delay.

Canonical evidence:
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/h1_native_reuse_20260917/`.
The native-reuse milestone does not claim the remaining H1 latency gap is solved
and does not authorize a full sweep.
