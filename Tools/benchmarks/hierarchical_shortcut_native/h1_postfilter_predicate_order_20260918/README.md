# Original H1 postfilter predicate order

This successor corrects a false cost-baseline claim in frozen
`h1_postfilter_native_predicate_20260918`. That version retained a nonnative
`distance > output.worstDist()` early return inside `admitFilteredResult`.
Its Broad graph's 221.616 predicate calls/query were **not** the authenticated
original H1 postfilter call pattern. Output parity alone did not establish
predicate-work parity. Its 0.108262468 ms graph/bit difference remains historical
evidence for that distance-shortcut control, not a fair original-order bit tax.

## Narrow delta

Remove that early return and its collapsed-alias exception from both graph and
bit controls. After native invalid-ID/result-dedup guards, evaluate the routing
predicate, then native deletion, metadata filter and result insertion in the
original short-circuit order. Also restore the original alias traversal-filter
dedup guard (inactive here because no traversal filter is passed).

The independent authority is the authenticated file at
`toolchains/matched_baseline_20260917/original/source/AnnService/src/Core/BKT/BKTIndex.cpp`:
original helper lines552-570 and ordinary fresh-neighbor admission lines947-1040.
It calls the predicate for fresh ordinary candidates before `m_Results.insert`,
not just for candidates competitive with the output heap.

Retain matching H1 output, nonmatching graph bridges, native distance frontier,
native per-edge budget, tree continuation, aliases and exact final filtering.
No supplementary own heap, eager liveness/VID/alias qualification, duplicate
posting-validity check, new cache, queue, policy or threshold is added.
The four-byte visited bit means native routing-support may-match, not exact
record eligibility. The head's own tag is already represented. Empty-posting
matching own heads still use native selected-head handling.

For a fresh ordinary neighbor, the already-computed visited predicate value is
passed locally to native admission, without another lookup or callback.
Tree visited initialization is additional predicate work. Later popped/alias
admission has no such local value and retains the original callback. The report
counts those overlaps explicitly rather than claiming every original tree
visited insertion evaluated a predicate. No secondary cache is introduced.

## Independent ordered proof

`OrderBench.cpp` links the untouched authenticated library as the callback-order
authority. Separate diagnostic BKT objects add event recording to copied original
and current sources; the immutable original sources/libraries are never edited.
`order_trace.py` records all generation/build commands and source hashes.
The ordinary benchmark binary does not include these diagnostic objects or
`OrderTrace.h`. No timing from diagnostic executables is reported as performance.

Events distinguish tree initial/continuation, ordinary rows, popped heads and
aliases: actual callbacks, visited initialization, native helper invocations,
logical predicate values, admission decisions, visited probes and distances.
`verify_order.py` requires original/current graph ordered-event equality and
bit native input/value/admission/visited/distance equality. It separately checks
the unmodified original library's actual callback sequence, and reconciles bit
initializations, local admission reuse, remaining callbacks and tree-only work.

## Bounded protocol

Exactly four Broad ordinary processes: graph, bit, bit, graph. Each uses the
unchanged native INI protocol: 1000 warmup/1000 measure, nprobe24/topk10,
MaxCheck2048, one query thread, NUMA CPU/memory2. Sparse/posting and unfilter
are32-query functional checks only. No sparse1000 run, tuning or profiling.
Additional1000-query Broad ordered replay is untimed diagnostic evidence.

Build and run once:
```
python3 prepare.py --initialize
python3 build.py
python3 provenance.py
python3 order_trace.py --build
python3 order_trace.py --replay
python3 verify_order.py
python3 run.py
python3 verify_head_reference.py
python3 finalize.py
```

Minimal independent checks after sealing:
```
python3 test_protocol.py
python3 verify_order.py
ctest --test-dir <toolchain>/harness --output-on-failure
```

Existing nearest24-unmatched, real empty-posting own head, native aliases,
single-bit/reset/generic-ID/rehash, negative auxiliary, signature/full-CSR,
capture parity and zero warmed backend-allocation fixtures remain.
The snapshot is static/read-only; numeric/DNF mapping is not newly benchmarked.
Source/INI/runtime hashes and all raw traces are sealed with the report.
No production, datasets, old proofs, plots or public GettingStart changes.
