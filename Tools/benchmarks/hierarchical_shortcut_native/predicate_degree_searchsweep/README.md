# Shared native SearchSweep.NProbe integration

This isolated experiment uses MAIN's repository-owned
`Tools/benchmarks/NativeNProbeSweep.h` **verbatim**. MAIN's benchmark, parser,
tests and documentation are not edited. The frozen full-query capture/admission
harness is adapted around that parser, rather than replacing its authentic
SPANN query path with a generic SSD query or MAIN's non-instrumented benchmark.

The sole array declaration is:

```ini
[SearchSSDIndex]
InternalResultNum=24
ResultNum=10

[SearchSweep]
NProbe=[16,24,32,48,62,80,96,128,192,256,384]
```

All other native controls appear explicitly in `configs/`. The array works in
`--search-ini` or the active `--search-sweep-ini`. Unknown SearchSweep keys,
duplicate/malformed/nonpositive/below-topk entries, and a SearchSweep section in
a base INI alongside an active sweep INI fail explicitly. InternalResultNum
remains scalar; its previous experimental array spelling is rejected.

The existing native post-load loop applies each resolved scalar parameter map
through `SetSearchParam`, resets the transient cached native search workspace,
and independently captures, warms and measures each probe. The workspace reset
prevents a preceding larger grow-only result heap from changing a smaller probe.
It does not reload any index. Actual `TenantIndexManager::LoadAll` entry logs,
probe boundaries, `index_load_count` and `native_index_load_calls` verify one
load throughout each invocation. Native results also identify `nprobe` and
`sweep_execution`.

All supplier, BKT, SPANN, eligibility, own-point admission, H/O posting, signature,
whole-row and row-visited algorithm sources remain byte-identical to the
authenticated parent. Predicate-valid degree16 remains independent of query
visited state; startup stays within the same native H1 search. H3 rows enumerate
H2 candidates without forcing a full descendant subtree. No extra cap, restart,
global rescue, new upper frontier or predicate-specific bypass is introduced.
Native MaxCheck2048, HierarchyMaxCheck512, ratio0.666666, page15 and ResultNum10
remain fixed. The historical top graph remains loaded as backing, not navigated
by the supplier. Existing qualification/callback/admission overhead is not removed.

## Preregistered execution

Six scenarios, three modes and the exact eleven-point grid yield198 points.
There are36 ordinary native processes and396 measurement batches: two repeats,
rotated scenario/mode groups, and reversed probe order on repeat2. Each probe
has1000 untimed captures,1000 warmups and the first1000 measured queries, one query
thread, NUMA CPU/memory node2 and native O_DIRECT. Index/cache state persists
between probes. No previous timings are reused.

Before the full matrix,36 native array fixtures cover all modes/scenarios at
16/24/384 and384/24/16. Forward fixtures use `--search-sweep-ini`; reverse fixtures
use `--search-ini`. Nine new scalar executions use `--search-ini`. Each probe has
8 captured,8 warmup and8 measured queries, with full semantic/physical-degree
audits and exact ordered ID/distance/work comparisons.

For the full matrix, every byte of every fresh native1000-query capture must
match its independently semantically audited parent array capture. The prior
validity verdict is reused **only after this exact SHA256 equality**, not merely
matching recall or aggregate counts. Fresh native SSD work must also agree.
Native capture-versus-timed ID/work checks remain active. This avoids repeating
millions of identical Python frame checks; it does not omit native captures,
warmup or measured queries. New raw captures are preserved losslessly and
independently rehashed after execution.

Every summary row includes
`sweep_execution: "single_load_nprobe_array"` and
`nprobe_ini_api: "SearchSweep.NProbe"`; every supplier row additionally includes
`supplier_degree_semantics: "predicate_valid_neighbors"`.
`summary.json`/CSV retain ordinary timings, QPS bounds, recall, underfill and work.
`report.md`/`thresholds.json` use only observed90%/95% threshold points.
Older published curves have different IO/budgets/query ranges and are not
same-protocol baselines. No plot or published figure is modified here.

## Reproduction

The following records the one-time authoring/build sequence from
`/mnt/nvme/baotonglu/mocheng`. Integration files and preregistration are now
materialized: do not regenerate them in place. To replay a measured group,
use its exact `native.command.json` command from a new empty working directory;
the command references the frozen binary and native INI, and loops inside C++.

```sh
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_searchsweep/integrate.py
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_searchsweep/prepare.py
cmake -S datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_searchsweep_20260916/source \
  -B datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_searchsweep_20260916/source/Release \
  -DCMAKE_BUILD_TYPE=Release -DSPDK=OFF -DROCKSDB=OFF \
  > datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_searchsweep_20260916/configure.log 2>&1
cmake --build datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_searchsweep_20260916/source/Release \
  --target spannaclbench --parallel 2 \
  > datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_searchsweep_20260916/build.log 2>&1
g++ -std=c++17 -O2 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_searchsweep/BatchTests.cpp \
  -o /tmp/sptag-shared-nprobe-parser-tests
/tmp/sptag-shared-nprobe-parser-tests \
  > datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_searchsweep_20260916/parser-tests.log
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_searchsweep/run.py fixtures
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_searchsweep/run.py sweep
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_searchsweep/verify.py \
  --results datasets/sift1m_zipf200_sparse193_numeric/comparisons/h1_predicate_degree_searchsweep_20260916 \
  > datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_searchsweep_20260916/verification.log 2>&1
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_searchsweep/api_failure_tests.py \
  > datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_searchsweep_20260916/api-failure-tests.log 2>&1
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_searchsweep/finalize.py
```

Preparations refuse existing destinations. Integration-generated repository
sources are materialized and retained, not transient source edits. The source
archive preserves the exact shared parser version and frozen library sources;
original-compatible libraries avoid the current production library's rejection
of old saved-index keys. No dataset, old binary or old output is modified.

## Completed dataset

All198 points/396 ordinary batches completed with exactly36 native LoadAll
entries. Every group also logged each physical H1 and H3 vector, BKT and RNG
component loading exactly once: no hidden per-probe component reloads.
The45 successful native fixtures passed; empty arrays, legacy InternalResultNum
arrays, unknown SearchSweep keys and misplaced base arrays failed before loading.
Independent reconciliation rehashed513 captures;20070 protected files were unchanged.

Results: `datasets/sift1m_zipf200_sparse193_numeric/comparisons/h1_predicate_degree_searchsweep_20260916/`.
Summary SHA256:
`14087ecd79471a0bc728f0f78bfb750e2be89fb5956739eda57a278a806a44c0`.
Shared MAIN parser SHA256:
`94dc8cf2e9313e683cb7a5d4e160990ab69d2fe54c0e9dc66cc5f4fd3f1b0c41`.

This is the observed supplier nprobe24 slice; the complete eleven-point curves
and observed90%/95% thresholds remain in `summary.json` and `report.md`:

| Scenario | Recall@10 | Ordinary ms | QPS | Underfilled /1000 |
|---|---:|---:|---:|---:|
| unfilter | 0.9079 | 2.251655 | 444.118 | 0 |
| broad_tag | 0.9178 | 3.483315 | 287.083 | 0 |
| medium_tag | 0.9631 | 15.168800 | 65.925 | 0 |
| extreme_tag | 0.9986 | 3.963885 | 252.278 | 0 |
| numeric | 0.7054 | 3.910720 | 255.707 | 0 |
| mixed_dnf | 0.9161 | 15.770250 | 63.411 | 0 |

Original H1 at unfilter24 measured90.73% recall,0.9487365ms and1054.033QPS;
supplier measured90.79%,2.251655ms and444.118QPS. The joint recall/speed
objective remains unachieved. Its per-query20.279 supplier calls and1119.359 CSR
member reads (5.711 upper H2 members,1113.648 descendant H1 members), with
graph/routing/parent/child distances1722.515/162.315/36.088/448.375, are unchanged
from the predicate-valid implementation. No eligible degree32 frame triggered
fallback. Exact node/frame evidence is preserved in `unfilter_degree_audit.json`.
