# Typed native benchmark client

This client-only build links an existing corrected native core archive and its
matching compiled `CoreInterface.cpp` object. It never configures or rebuilds
the core. Use separate new build directories for ordinary and diagnostic clients;
`CLIENT_DIAGNOSTIC` must match the frozen core's CMake cache.

```sh
cmake -S CLIENT_BUILD_SOURCE -B NEW_CLIENT_BUILD \
  -DCMAKE_BUILD_TYPE=Release \
  -DSPTAG_SOURCE_ROOT=FROZEN_CLIENT_AND_HEADER_SNAPSHOT \
  -DCORRECTED_CORE_DIR=EXISTING_CORRECTED_CORE_BUILD \
  -DCLIENT_DIAGNOSTIC=OFF
cmake --build NEW_CLIENT_BUILD -j2
```

`[Benchmark] ValueType=Float|UInt8` selects the native template once, before
loading/querying. The default remains `Float`. Float queries require NPY
`<f4`; UInt8 queries require `|u1`. Both require C-order rank-two `[N,128]`
arrays and must match both saved `[Index] ValueType` and `[Base] ValueType`.
There is no conversion from integral Float queries to UInt8 in this client.
Predicates retain their existing `<u4` representation.

NPY v1.0/v2.0 headers, rank, dimensions and exact payload extent are validated.
Truncation, trailing bytes, unsupported dtype/order/version and extra dimensions
fail explicitly. `[SearchSSDIndex] SearchPostingPageLimit` is a required positive
integer, not fixed to 15. Other native budgets and protocol controls are unchanged.

Point JSON adds `value_type` and `search_posting_page_limit`. Existing navigation
schema 6 / 57 columns and result/distance/work payload formats remain unchanged.
Warmup, timing boundaries and deterministic replay remain the same. Query bytes
are `128 * sizeof(T)`, and the tenant manager uses the declared native type.

The client closes/checks output payload files, then emits and flushes one JSON
summary after each completed nprobe point, outside timing. There is no per-query
flush and no filesystem `fsync` durability claim. The Float-only own-record
fixture rejects UInt8 instead of interpreting its vectors as Float.

`Test/NativeBenchClientTest.py` runs bounded Float regression and UInt8 ordinary/
diagnostic fixtures, including mixed DNF, wrong dtype/index/page-limit failures,
NPY rejection cases, exact output/work parity, immutable input checks and live
point visibility. It refuses fixture indexes with more than 200,000 heads.

## Optional load-once batch INI

The same `nativeBench NATIVE_INI` entrypoint also accepts a registration INI:

```ini
[Batch]
CaseCount=2
[Case1]
Config=/absolute/configs/broad-h1-2048.ini
OutputDirectory=/absolute/new-results/r1-broad-h1-2048
[Case2]
Config=/absolute/configs/mixed-postgraph-2048-extra2048.ini
OutputDirectory=/absolute/new-results/r1-mixed-postgraph-2048-extra2048
```

`Case1` through `CaseCount` (1..10000) execute in that exact order. Unknown,
duplicate or missing registration sections/keys fail. Each case is an ordinary
native benchmark INI, not an override/delta; nested batches are unsupported.
Config/output paths and case index/query/used-predicate paths must be absolute.
All configs and input arrays are validated before loading the index. Cases must
share the canonical index directory, value type, canonical query file, and
MaxQueries/Warmup setting. Predicates and probe sweeps may differ. Query data and
one tenant manager are loaded once; predicate arrays are read before that load.
No search results are cached or reused.

Every batch case must explicitly supply all supported `[SearchSSDIndex]` keys:
`isExecute`, `BuildSsdIndex`, `InternalResultNum`, `ResultNum`, `NumberOfThreads`,
`HashTableExponent`, `MaxCheck`, `MaxDistRatio`, `SearchPostingPageLimit`,
`DisableCrossEdges`, `LogPhaseTime`, `LogPathStats`, `DumpHeads`,
`EnableHybridDistance`, `EnablePostingNavigation`, `PostingAnchorCount`, and
`PostingAdditionalMaxCheck`. Native `SPANN::Index::SetParameter` validates these
on an unloaded temporary index before any data load; the existing public tenant
manager setters apply complete settings to the loaded manager between cases.
`isExecute=true` and `BuildSsdIndex=false` are required. HashTableExponent and
DisableCrossEdges must remain invariant across cases, as do the existing fixed
single-query-thread/top-10/no-hybrid controls. Phase logging has the separate
explicit diagnostic opt-in below; other logging remains disabled.
When increasing MaxCheck and decreasing the extra budget, the declared extra
is applied first to avoid a transient base-plus-extra integer overflow.
There are no undeclared parameter values, workspace resets or core changes.

Output directories must not exist, overlap one another, or lie within the
immutable index. They are checked again at case start. Every batch probe writes
the usual payloads to `OutputDirectory/nprobe_N`, including a case without a
SearchSweep. Each probe executes its own full warmup, measured window, and
deterministic replay; supported windows remain 32, 1000, or all corpus rows.
Case transitions do not skip warmup. NumberOfThreads=1 controls native query
execution; the client does not restrict process affinity to one CPU.

The stdout JSON event sequence is:
`batch_begin`, `batch_loaded`, then `case_begin`, one `point` per probe,
`case_end` for each case, followed by `batch_end`. Case/point events carry
`case_id`, canonical `config`, and `output_directory`. Case begin also records
the predicate source, complete declared search settings and query-window counts.
Point JSON still begins with `mode` and preserves ordinary/diagnostic schemas;
it adds `event=point` and completed warmup/measured/replay counts. Existing
single-config point fields remain compatible, with an added `phase_timing`
boolean. All events flush outside query timing;
points appear only after replay and payload close. A failure exits nonzero,
retains prior completed output, and never emits success for unfinished cases or
the batch. Retrying requires a new manifest with fresh output directories.
There is no automatic reload/fallback or filesystem-fsync durability claim.

The caller supplies the balanced scenario/variant schedule, including reversed
repetitions. Ordinary and diagnostic schedules run in separate processes using
their respective binaries. Inputs must remain immutable throughout execution.
Do not splice historical fresh-process throughput into a load-once campaign.

### Reuse validation and retained memory history

`Test/NativeBenchBatchTest.py` compares bounded existing UInt8 and Float fixtures
against fresh processes over all six probes. It covers empty/categorical/mixed
DNF transitions, MaxCheck 2048 -> 4096 -> 2048, page limits 1/3/15, posting
on/off and extra 0/2048, reversed order, full windows and pre-load rejection.
IDs, distances, SSD work, graph heads and traversal counters must match exactly.
Allocation/initialization/clear-byte counters are recorded separately, without
tolerance or silently normalizing raw diagnostic files.

In the UInt8 fixture, only navigation column 16 (zero-based),
`m_nativeHashClearedBytes`, differs: fresh 4096 cases clear 1,572,864 bytes/query
versus 786,432 in the batch initialized at 2048. The native workspace initializes
its hash at creation; later `Reset` clears the retained table and updates queue
and result limits without reinitializing that hash. All traversal counters and
results agree. This is observable memory-history dependence, not byte-identical
whole-navigation-work parity, and no internal reset is introduced to hide it.
The batch fixture also checks full 1000-query windows using a private repeated
small corpus; that synthetic fixture is not a real 1B cohort or performance run.

## Native phase-timing diagnostic opt-in

Use a client linked to the corrected **normal** core (`CLIENT_DIAGNOSTIC=OFF`).
In each case's native INI, set both:

```ini
[Benchmark]
PhaseTiming=true

[SearchSSDIndex]
LogPhaseTime=true
```

These are additions to a complete ordinary case INI, not replacement sections.
`PhaseTiming` defaults to `false`, accepts only literal `true`/`false`, and must
match `LogPhaseTime`. Unlabelled logging, an enabled label with disabled logging,
and mixed phase modes within one batch fail before index loading. Work-counter
diagnostic builds reject phase timing to keep the instrumentation classes
separate. Existing frozen clients/binaries do not change.

Points retain `mode`, `queries`, `nprobe`, `qps` and all existing payloads.
`phase_timing=true` identifies logging-contaminated diagnostic timing, **not
ordinary QPS**. The existing `diagnostic` field describes compile-time work
instrumentation, so it remains `false` for this normal-core phase client.
Consumers must exclude points where either instrumentation flag is true from
ordinary throughput. `batch_begin` and `case_begin` also record `phase_timing`.
Default runs emit `phase_timing=false` and no native PhaseTime rows.

No timer, query algorithm, per-query marker, or timing boundary is added.
The existing native `PhaseTime:` log appears once per successful query. With
one query thread, for each case and each nprobe in declared order the rows are:
first `Warmup` warmup queries, then `MaxQueries` measured queries, then the same
`MaxQueries` replay queries, all in corpus order. The point JSON follows replay
and closed payloads. Thus a 32-query point has 96 native rows; a 1000-query point
has 3000. Parse the combined stdout/stderr stream in order, associate rows with
the surrounding case and following point, and use only the middle window for
measured phase attribution. Do not infer query IDs from the native `tag` field.
There are no log-window boundary events; incomplete windows or missing rows
must fail attribution rather than be silently reassigned. The fixture records
exact log line numbers for all three windows and the following flushed point.

The native log's `bkt + pq + graphOther` reconstructs `(T1-T0)`: H1 plus any
postgraph supplementation in RAM, not just H1 graph work. `post` is `(T2-T1)`,
the final posting/SSD path. `io`, `scan`, and `postOther` retain native meanings;
`io` is retrieval minus scan time, not a direct physical-device delay
measurement. Native `bkt` and `pq` may be zero in this frozen normal core; do
not invent a seed/graph/supplement breakdown from them. Fields are printed
rounded to milliseconds with three decimals, so reconstructed sums need not
exactly equal separately rounded `total`. QPS and per-query latency include
native logging overhead; native phase totals are narrower than the whole
wrapper/client query interval.

`Test/NativeBenchPhaseTest.py` uses only existing small Float/UInt8 indexes and
32-query windows. It checks phase logs in unfiltered graph and filtered posting
batch cases, exact final ID/distance/SSD-work parity against the frozen ordinary
client, default behavior, six pre-load mode rejections, payload completion,
live point visibility, and unchanged input-index identities. It does not load
the 1B index or provide performance acceptance evidence.

The fixed `Tools/benchmarks/configs/sift1b_sparse_cost/` profiles isolate the
current adaptive SIFT1B runtime's sparse-query costs without rebuilding its
index. `phase_batch.ini` uses the normal client for H1-only and posting modes;
`diagnostic_batch.ini` uses the separately frozen counter client for posting
mode only. Run them serially through the existing output-only Landlock helper.
Both use the same first32 queries, equal warmup/measured/replay windows, and
nprobe `[16,96,384]` at MaxCheck2048; graph-only uses extra0 and posting mode
uses extra2048. Only the middle32 phase rows per96-row point are measured.
Require exact ID, distance, and SSD-work parity with the corresponding first32
ordinary-curve payloads. Neither batch provides ordinary QPS or full1000-query
phase attribution. Compare representative distances and owner references with
completed rows: discovery costs and signature-rejected-row ascent are not
bounded by the number of expanded H2 rows or charged H1 checked leaves.
