# Standalone H2 navigation sweep

This tool is **not a production or disk benchmark**. Recall means intersection
with exact native-Float-L2 H1 top-24, not final dataset recall. H1 vectors and
flat graph must have identical ordinal IDs and bytes. No input is written.

## Build and run

```sh
cmake -S SPTAG/Tools/benchmarks/h2_sweep_native -B <new-build> \
  -DSPANN_UPSTREAM_BUILD=<toolchain>/build -DCMAKE_BUILD_TYPE=Release
cmake --build <new-build> --parallel 2
ctest --test-dir <new-build> --output-on-failure
<new-build>/bin/h2sweepbench --config /absolute/sweep.ini
```

Clean pinned upstream `2ac3ebcab562bc81cdb8c7c98b35ea72f2703c3b`, existing
native compiler/flags/static libraries are required. All new binaries and
generated copies stay in `<new-build>`. Existing build targets are not invoked.
`h2sweepbench_ordinary` uses the pristine archive without the isolated BKT object;
it emits null graph/total costs and is for parity checks, not same-work claims.

## Native INI

All paths are absolute, one file/directory per key. Unknown sections and keys
fail. Parameters never come from environment variables.

```ini
[Input]
HeadVectors=/absolute/fixed160091x128.bin
Queries=/absolute/queries.fvecs
QueryCount=1000
Dim=128
HeadIndexDirectory=/absolute/h2/HeadIndex
HeadIDs=/absolute/h2/headID.bin
FlatIndexDirectory=/absolute/h1_same_posting/FlatHeadIndex
[Sweep]
Replicas=2,4,8,16
Beams=4,8,12,16,24,32,48,64,96,128
ResultNum=24
AssignmentCandidates=64
AssignmentMaxCheck=8192
SearchMaxCheck=512
FlatMaxCheck=2048
RNGFactor=1
Threads=1
BuildThreads=24
Warmup=1000
Repeats=3
[Output]
Directory=/absolute/existing-parent/NEW-output
```

`HeadVectors` is native DEFAULT Float `(int32 rows, int32 dim, floats)`;
`Queries` is native Float XVEC. Native VectorSetReader and native distance
selector are used. Inputs are bounded to 200,000 rows, at most 1,000 measured
queries, dimension 128, result 24, candidates 64, beam 1–128, replicas from
2/4/8/16/32. Each index directory contains the official BKT `indexloader.ini`.
`HeadIDs` accepts upstream native Dataset `(int32 count, int32 columns=1)`
followed by uint64 H2-to-H1 ordinals, historical headerless uint64 ordinals,
or count-prefixed uint64 with a 4- or 8-byte count, distinguished by exact
size and header validation. Every mapping
entry is checked against byte-equal H2/H1 vectors, and IDs must be distinct.
The output directory must not exist; its parent must exist.

## Operator, work and timing

Construction searches the fixed H2 graph once per H1 child at top-64, then
uses **the same candidate list for every replica count**. Frozen hierarchy
assignment semantics: pin self, accept candidates when no chosen parent has
`distance(candidate,chosen) < distance(child,candidate)`, then nearest-fill.
The callback count excludes this construction work.

Each `(replicas, beam)` performs native `SearchIndex(top=beam)` at the fixed
SearchMaxCheck. It visits those H2 rows, deduplicates H1 IDs, evaluates one
native L2 per unique H1 ID, and keeps top-24 by `(distance, ordinal ID)`.
**Graph work changes with result queue size**: no reused top-128 graph search
is represented as a top-4 graph search. Every graph distance callback invocation
(including tree centers and repeated evaluations) is counted. Runtime work is
`graph_distances + h1_unique_distances`, never native GetScanned. Flat reference
uses native top-24 and FlatMaxCheck on the identical H1 graph.

The isolated generated header adds only nonvirtual methods, no fields/layout
changes. A wrapper increments a query-local counter then invokes the original
`m_fComputeDistance` callback. The unchanged upstream BKTIndex.cpp is compiled
as a direct object before the archive. Native BKT Search/SearchTree route all
distance calls through this callback, including BKTree search helpers (which
receive a copy of the same function). No quantizer, deleted IDs, concurrent
query counting or alternative search API is supported.

Every counted query is compared to a callback-disabled search for exactly equal
ordered IDs **and Float distances**. Ordinary repetitions restore the original
function before timing, and check results afterwards. Per-query `ordinary_us`
is the mean of Repeats; p95 is across these per-query means, not across all
individual repetitions. Warmup cycles through measured queries before each
point. Search/result allocation, deduplication and scoring are included;
oracle, diagnostics, counting, output and parity checks are excluded.
Each loaded graph also checks exactly three known callback invocations and
verifies that restoration stops counting.

Equal callback counts mean equal native distance-evaluation work, **not equal
CPU cycles**: assignment-entry iteration, sorting, cache misses and tree/graph
bookkeeping differ. Compare measured latency separately. The grid does not
force identical budgets; use true costs to choose matched-work points or a
Pareto frontier, and report residual cost differences rather than claiming
unmeasured exact equality.

## Persistent output

* `input.ini`: exact input configuration.
* `assignment_candidates.bin`: int32 lower count, int32 candidate count,
  row-major int32 candidate IDs followed by row-major float distances.
  Invalid native results are `-1`/MaxDist.
* `replicas_R.csr`: magic `H2SWCSR1`, int32 lower/upper/R, uint64 entries,
  uint64 offsets[upper+1], int32 members[entries], int32 owners[lower*R].
  Owners retain pinned/diverse/fill ordering; each member row is child-ID ordered.
  This is a standalone diagnostic format, **not production hierarchy CSR**.
* `assignments.jsonl`: assignment counts, min/max row size and self-pin checks.
* `queries.jsonl`: per-query flat/H2 recall, exact graph count, unique H1 count,
  assignment entries, total work, ordinary latency, and selected final IDs.
* `summary.jsonl`: mean and nearest-rank p95, actual H2 ratio, mean work ratio
  against flat, and oracle diagnostics.
* `oracle_h1.jsonl`: exact native H1 top-24, computed once per query per run.
* `boundary.jsonl`: for each truth target and R, every owner's exact query-H2
  rank/distance and nearest owner's rank; `boundary_histogram.jsonl` aggregates
  nearest-owner ranks. Ranks are one-based `(distance, ID)` order.
* `boundary_summary.jsonl`: per-R nearest-owner rank median/p90/p95/p99/max
  (nearest-rank percentiles), and target fractions with rank <=16/24/32/64/128.
  Target-weighted over every measured query's exact H1 top-24.
* `COMPLETE`: written only after successful validation and output generation.

The full exact H2 ordering is a **non-runtime diagnostic**. Expansion of its
first beam rows is reported separately (`oracle_nonruntime_*`); it never
changes actual routes and is not guaranteed a theoretical upper bound.
Exact-H1/H2 work is excluded from runtime costs/timing. Oracles are reused
across every point in one run; separate graph runs recompute them.

`isolated/instrumentation.json` in the build directory records SHA-256 of
original and generated BKT sources. The synthetic CTest builds only 256/128-node
graphs, tests pristine/instrumented binary parity, CSR memberships, self
pinning, full-beam recall, accounting, map formats and strict failures.
