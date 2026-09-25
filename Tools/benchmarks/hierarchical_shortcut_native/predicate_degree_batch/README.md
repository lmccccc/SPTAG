# Native load-once nprobe arrays

This isolated benchmark preserves the corrected `predicate_degree_native`
algorithm byte-for-byte. It changes execution architecture, not search policy.
The previously completed per-process sweep remains intact; these fresh
load-once measurements supersede its timing protocol, not its correctness evidence.

## Native interface

```ini
[SearchSSDIndex]
ResultNum=10
InternalResultNum=[16,24,32,48,62,80,96,128,192,256,384]
```

The remaining native search controls are required as shown in `configs/`.
Scalar `InternalResultNum=24` remains supported. Comma-separated lists with or
without brackets preserve order. Empty, malformed, duplicate (including `016,16`),
nonpositive, overflowing, or below-ResultNum entries fail explicitly before
loading the index. Arrays append `.nprobe_N` to `ShortcutOutput`; a scalar
invocation retains its original capture filename.

`SpannAclBench.cpp` expands the INI into scalar native parameter maps and reuses
the benchmark's existing post-`LoadAll` loop. Each iteration calls the real
`SetSearchParam` path, performs its own untimed capture, warmup and measurement,
and owns separate result buffers, counters and capture output. The public
thread-local native search workspace is released between probes: its heap's
`clear` only grows capacity, so retaining a preceding384 capacity would otherwise
change later16 traversal. This releases transient search storage, not index data.
Instrumentation at the actual `TenantIndexManager::LoadAll` entry, probe boundary
logs and native JSON counters establish exactly one load throughout an invocation.

All supplier/BKT/SPANN algorithm files remain byte-identical to the authenticated
parent. Degree16 counts distinct predicate-valid physical ordinary neighbors,
independent of query visited state. The existing native startup continuation,
conservative signatures, predicate-before-candidate scoring (except structural
BKT routing), complete selected rows, query row-visited state, and H3-row discovery
versus H2-row selection are unchanged. No custom cap, graph restart, upper search
frontier or result-dependent repair is introduced. Existing qualification,
callback and admission overhead remains. The historical top graph is loaded as
vector backing but is not navigated by the supplier.

## Preregistered protocol and reproduction

Run from `/mnt/nvme/baotonglu/mocheng`. `prepare.py` refuses existing destinations.

```sh
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_batch/prepare.py
cmake -S datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_batch_20260916/source \
  -B datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_batch_20260916/source/Release \
  -DCMAKE_BUILD_TYPE=Release -DSPDK=OFF -DROCKSDB=OFF \
  > datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_batch_20260916/configure.log 2>&1
cmake --build datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_batch_20260916/source/Release \
  --target spannaclbench --parallel 2 \
  > datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_batch_20260916/build.log 2>&1
g++ -std=c++17 -O2 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_batch/BatchTests.cpp \
  -o /tmp/sptag-native-batch-parser-tests
/tmp/sptag-native-batch-parser-tests
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_batch/run.py fixtures
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_batch/run.py sweep
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_batch/verify.py \
  --results datasets/sift1m_zipf200_sparse193_numeric/comparisons/h1_predicate_degree_batch_20260916
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_batch/finalize.py
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_batch/tag_execution.py
```

`experiment.ini`, checked-in search INIs and `schedule.json` are authoritative.
The process command and all resolved native probe outputs are preserved per group.
The fixed grid is16/24/32/48/62/80/96/128/192/256/384; modes are original H1,
historical H3 and corrected supplier; scenarios are unfilter, broad_tag,
medium_tag, extreme_tag, numeric and mixed_dnf.

There are36 ordinary invocations,396 native probe batches and198 unique points.
Each probe independently uses1000 untimed validation queries,1000 warmups and
the first1000 measured queries, one query thread, NUMA CPU/memory node2 and
O_DIRECT. ResultNum10, MaxCheck2048, HierarchyMaxCheck512, ratio0.666666,
page15 and EffectiveDegree16 remain fixed. Only InternalResultNum varies.
Mode/scenario groups rotate; the second repetition reverses probe order.
Index and hardware caches persist across probes, unlike the old per-point
process protocol. Ordinary latency excludes capture/profile overhead.

Before the matrix,36 small native array fixtures cover both16/24/384 and
384/24/16 across every mode/scenario, with8 captured,8 warmup and8 measured
queries per probe. Nine additional actual scalar executions cover unfiltered
H1/H3/supplier at those probes. Exact ordered result IDs/distances, selected H1s
and work are compared against the authenticated completed scalar captures.
Every full batch point is additionally checked against its full1000-query scalar
reference, including native SSD work, and against its second ordinary repetition.
Original H1/H3 boundary and24 reference provenance retains the earlier checks
against the authenticated frozen executable; no earlier latency is reused.

Results go to
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/h1_predicate_degree_batch_20260916/`.
`summary.json` preserves plotting fields and labels every supplier row with
`supplier_degree_semantics: "predicate_valid_neighbors"`. Every H1/H3/supplier row
also has `sweep_execution: "single_load_nprobe_array"`. QPS is1000 divided by
mean ordinary milliseconds; bounds come from the two observed repetitions.
`report.md` and `thresholds.json` select only measured points reaching90%/95%;
they never interpolate. Historical published curves used different IO, budgets
and query ranges and must not be treated as same-protocol baselines.
Plotting and published figures are deliberately outside this implementation.

## Completed measurements

All198 points and396 ordinary batches completed in36 native index-load
invocations. All45 successful native validation invocations also loaded once;
an additional malformed-array rejection invoked the loader zero times.
Independent reconciliation rehashed all513 compressed captures and recomputed
the summary coordinates from native outputs. All full-query scalar-reference
and repetition payload/work comparisons passed;18826 protected files remained
unchanged. Current summary SHA256:
`72c003f315242c5b8944e72ebf0a31c48efadf0856c762c5038d301ef5b9872b`.
The metadata-only execution tag was added after checking all36 actual native
LoadAll-entry logs and396 native probe records again. All original row values
are unchanged. Pre-tag summaries and report manifests are preserved under
`schema_before_sweep_execution/`; the pre-tag summary SHA256 was
`a27ba507d61731186833c38103bfdd54a1eab08ec9854f04c9c4f5963bb7656c`.
`execution_metadata_revision.json` records the exact native evidence and the
superseded per-point execution notice. No old runner is resumed by this step.

The following is only the observed supplier nprobe24 slice, not a substitute
for the eleven-point curves in `summary.json`:

| Scenario | Recall@10 | Ordinary ms | QPS | Underfilled /1000 |
|---|---:|---:|---:|---:|
| unfilter | 0.9079 | 2.241525 | 446.125 | 0 |
| broad_tag | 0.9178 | 3.602395 | 277.593 | 0 |
| medium_tag | 0.9631 | 16.063900 | 62.251 | 0 |
| extreme_tag | 0.9986 | 3.884910 | 257.406 | 0 |
| numeric | 0.7054 | 3.897440 | 256.579 | 0 |
| mixed_dnf | 0.9161 | 15.820100 | 63.211 | 0 |

At unfilter24, original H1 measured90.73% recall,0.943436ms and1059.955QPS;
supplier measured90.79%,2.241525ms and446.125QPS:2.376x H1 latency.
The joint recall/speed objective is therefore still not achieved.
Supplier per-query calls were20.279; CSR-member reads1119.359
(upper-row H2 members5.711, descendant H1 members1113.648);
actual graph/routing/parent/child distances were1722.515/162.315/36.088/448.375.
All20279 calls came from genuine physical degrees3--15, with zero startup
calls in this unfiltered slice. All37136 degree32 frames had zero fallback,
including2267 with at least20 visited-neighbor skips.
`unfilter_degree_audit.json` preserves exact triggering node IDs and adjacency;
`report.md`/`thresholds.json` contain the observed90%/95% selections for every case.
