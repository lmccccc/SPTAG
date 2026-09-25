# Native H1 synchronous neighbor supplier

This is a new experiment, not a rename of fixed overlays or the earlier
alternating parent-queue online policy. Production sources, all previous
experiments and original index bytes are untouched.

## Measured outcome

**Implemented and completed216 native processes, but the joint H1/H3 goal is
not demonstrated.** S2000 unfiltered recall90.67% is close to original H1's
90.73%, at1.106x latency; S3200 matches90.73% at1.158x. Relative to the matched
native control, recall changes range from-0.03 to+0.14 percentage points across
these settings. Two repetitions do not establish subpercent timing effects.

Extreme recall remains17.40%/19.27% versus historical H3's90.07%, with
77.7%/74.6% underfill. Mixed recall is35.58%/38.71% versus76.10%, with
28.7%/22.2% underfill. Broad/medium/numeric have higher recall than H3 but
higher latency, not demonstrated recall-matched cost.

The literal trigger actually fired in563/633 of1000 queries at2000/3200;
136/144 queries used H3 supply. There were1.207/1.395 calls and9.123/10.617
fresh supplied neighbors per query, but only1.070/1.090 actual native frontier
insertions. The modest effect is not an unexercised helper or an upper scheduler:
it supplements very few retained H1 expansions. No trigger was retuned.
Parent-first representatives correctly contribute own points, including4 true
final neighbors in extreme and5 in mixed per1000 queries at each budget.
Native H3-use counters mean H3 CSR was inspected, not that every ascent yielded
an H1 insertion. The synthetic fixture additionally verifies actual H3-derived
neighbor delivery; native captures do not split returned neighbors by descent
origin.

## Preregistered policy

One original BKT H1 search owns the native tree/graph queues, visited set,
distance-bound heap, collapsed-node handling and continuation until its normal
stop or the actual-call cap. No128-call bootstrap/manual replacement frontier
is used. Posting/own-point admission uses a separate native result heap and
never feeds predicates into spatial traversal.

**Edge shortage means zero fresh valid H1 graph neighbors actually scored in
the current native adjacency expansion**, irrespective of attributes or whether
the native distance bound retained those neighbors. The helper is not called
after termination, when the native checked-leaf limit is already exhausted,
or in response to missing filtered results.
Fresh means unseen in the native traversal's visited set: an internal tree
pivot or parent can already have a cached distance without having entered that
visited set.

On shortage, synchronously supply at most8 fresh H1 neighbors:

1. Score/rank the item's eight direct H2 representatives by query distance.
   Inspect at most4 nearest H2 rows, at most8 new CSR entries per row.
2. Only if fewer than8 fresh neighbors were supplied, score/rank the H3 owners
   of the first two ranked H2 parents (up to16 distinct choices). Inspect at
   most8 H3 member entries total, nearest H3 rows first.
3. Rank those H2 children by query distance and inspect at most4 of their H1
   rows, at most8 entries each, excluding rows already opened in this call.
   Return immediately to the native H1 loop, even if fewer than8 were found.

Step3 considers the first four ranked choices; an already-opened row is skipped
without replacing that choice with a lower-ranked fifth row.

The maximum is72 member inspections/call (32+8+32). Persistent row cursors
prevent rereading entries; exhausted rows are not restarted. Choices, sorting
and candidate lists are helper-local: **no H2/H3 frontier survives the call**.
Alternatives are retained within these explicit limits, not all permanently
discarded in favor of one nearest parent. A returned neighbor enters the same
native visited/distance/queue flow and may be rejected by its spatial bound;
`supplyNeighbors` counts fresh submitted neighbors, `supplyQueued` actual
frontier insertions.

No threshold change or policy sweep is allowed after observing results.

## Distance and admission contract

Caps2000/3200 count all real native tree/graph calls, including repeats,
parent-representative calls and supplied-child calls. Native `MaxCheck=2048`
and normal convergence remain additional stopping conditions; the cap need
not be fully spent. The graph-only `control` shares every budget, callback,
negative-sentinel and admission change, with the supplier disabled.

Original adjacent-layer ID maps compose H3->H2->H1. Startup preparation
byte-compares every parent catalog vector with its canonical H1 vector and
validates all eight memberships. Parent scoring is therefore **not anonymous
upper work**: every first evaluated canonical H1, including a parent
representative, goes through the same original native exact own-point and
posting-support admission. Parent/child cache hits reuse these distances;
ordinary native graph/tree callbacks still perform and charge real repeated
calls. Collapsed-node siblings are explicitly scored/charged before native
result handling in both experimental modes rather than inheriting anonymous
representative distances.

Capture records all distinct evaluated H1 IDs/distances and parent-first H1
IDs for an independent own-heap and parent-identity audit. Ordinary timing
disables independent counters, trace hashing and capture, but necessarily
includes the shared budget/cache/admission ledger and supplier bookkeeping.

The unchanged loader still loads the original top H3 graph as vector backing
and for the separately named original H3 reference. Its memory and graph-file
dependency **have not been removed**. Experimental searches never navigate it;
a runtime guard rejects a second/native upper-graph traversal. Reverse owner
tables and caches are prepared during the untimed initial capture, not hidden
inside measured queries.

## Fixed full-query comparison

`experiment.ini` fixes the first1000 existing queries,1000 warmups, one query
thread, CPU/memory NUMA node2, target24 postings and page limit15. Cases are
original H1, historical H3, control/supplier at2000/3200. Two paired repetitions
rotate case/scenario order and reverse profile/off order across all six
existing scenarios. Baseline parity and first-eight-query fixtures precede
the matrix. Final native Recall@10, returned count/underfill, own points,
postings/pages, distance work and ordinary full latency are compared separately.

Historical H3 is **not an attribute-blind geometry control**: even with
`HierarchyGraphSignaturePruning=false`, it retains signature-filtered top
results, pre-score lower compaction and eligible-head16->24 widening. These
remain a confound, especially for sparse filters. This supplier and its
matched control score before attribute admission and never use final underfill
to repair traversal. Recall-mismatched latency ratios are descriptive only.

Isolated source/build: `toolchains/h1_supplier_20260916/`.
New results: `comparisons/h1_supplier_20260916/`, both under the existing
`datasets/sift1m_zipf200_sparse193_numeric/` root.

```sh
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/full/reconstruct.py \
  --repo SPTAG \
  --snapshot datasets/sift1m_zipf200_sparse193_numeric/build_runs/20260909T141212Z_lightweight_rescue \
  --output datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_supplier_20260916 \
  --authenticated-cache /home/baotonglu/.copilot/session-state/40fc7ebb-e617-4ec3-a17b-610068e95e5e/files
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/supplier_native/integrate.py \
  datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_supplier_20260916/source
cmake -S datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_supplier_20260916/source \
  -B datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_supplier_20260916/build \
  -DCMAKE_BUILD_TYPE=Release -DSPDK=OFF -DROCKSDB=OFF -DTBB=ON -DLIBRARYONLY=ON
cmake --build datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_supplier_20260916/build \
  --target spannaclbench --parallel 4
cmake -S SPTAG/Tools/benchmarks/hierarchical_shortcut_native/supplier_native \
  -B datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_supplier_20260916/tests \
  -DSPANN_ROOT=/mnt/nvme/baotonglu/mocheng/datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_supplier_20260916/source
cmake --build datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_supplier_20260916/tests --parallel 2
datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_supplier_20260916/tests/suppliertests
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/supplier_native/run.py \
  --config SPTAG/Tools/benchmarks/hierarchical_shortcut_native/supplier_native/experiment.ini
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/supplier_native/verify.py \
  --config SPTAG/Tools/benchmarks/hierarchical_shortcut_native/supplier_native/experiment.ini
```
## Completed paired experiment

All216 native processes completed; 3459 protected files retain their original hashes/size/mtime. Original H1/H3 match frozen selected heads/distances and native recall/work. Frozen final IDs were unavailable and are not claimed. Counted/uncounted ordered outputs match; all experimental spatial traces match across predicates. Native own heaps equal the exact nearest10 matches among **all** evaluated H1s, including parent-first representatives.

### Final Recall@10 (%) / ordinary full latency (ms)

| Scenario | H1 | H3 | C2000 | S2000 | C3200 | S3200 |
|---|---:|---:|---:|---:|---:|---:|
| unfilter | 90.73 / 0.959542 | 88.60 / 0.796996 | 90.68 / 1.060470 | 90.67 / 1.060905 | 90.73 / 1.118375 | 90.73 / 1.111530 |
| broad_tag | 72.25 / 0.700105 | 87.67 / 0.827244 | 91.42 / 1.242860 | 91.39 / 1.245205 | 91.54 / 1.318410 | 91.56 / 1.310870 |
| medium_tag | 19.77 / 0.479824 | 86.28 / 0.719785 | 93.48 / 1.277730 | 93.51 / 1.276410 | 94.53 / 1.348165 | 94.52 / 1.354695 |
| extreme_tag | 0.28 / 0.373938 | 90.07 / 0.441504 | 17.26 / 0.779337 | 17.40 / 0.804671 | 19.18 / 0.879347 | 19.27 / 0.878453 |
| numeric | 30.44 / 0.759403 | 42.18 / 0.711269 | 48.67 / 1.046330 | 48.66 / 1.053055 | 49.30 / 1.115070 | 49.29 / 1.115770 |
| mixed_dnf | 1.04 / 0.388979 | 76.10 / 0.507821 | 35.48 / 0.957267 | 35.58 / 0.982814 | 38.58 / 1.048845 | 38.71 / 1.053080 |

`C` is the matched native BKT admission/budget control; `S` is synchronous supply. These are not the old manual-frontier G/O cases. Timings are two fresh ordinary repetitions, not profile timings or historical spliced values.

### Underfilled queries (%)

| Scenario | H1 | H3 | C2000 | S2000 | C3200 | S3200 |
|---|---:|---:|---:|---:|---:|---:|
| unfilter | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| broad_tag | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| medium_tag | 45.9 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| extreme_tag | 99.7 | 0.6 | 77.8 | 77.7 | 74.7 | 74.6 |
| numeric | 54.1 | 8.3 | 0.3 | 0.2 | 0.3 | 0.2 |
| mixed_dnf | 99.7 | 0.0 | 28.5 | 28.7 | 22.2 | 22.2 |

### Predicate-invariant native navigation and helper work

Per-query means; upper work is supplier row choice, never an upper graph search. All parent distances also represent real, admitted canonical H1 candidates.

| Case | Graph actual | Parent actual | Child actual | Distinct H1 | CSR entries | Calls | Fresh supplied | Enqueued | H2 rows | H3 rows |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| control2000 | 1948.903 | 0.000 | 0.000 | 1858.085 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| supplier2000 | 1940.605 | 3.803 | 8.212 | 1861.168 | 39.317 | 1.207 | 9.123 | 1.070 | 4.958 | 0.458 |
| control3200 | 2231.960 | 0.000 | 0.000 | 2125.550 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| supplier3200 | 2224.861 | 4.292 | 9.612 | 2131.434 | 44.193 | 1.395 | 10.617 | 1.090 | 5.618 | 0.490 |

Caps are ceilings, not forced expenditure: the original checked-leaf limit and native convergence also stop search. Every query has exactly one native H1 search start. Real repeated callbacks are charged; cache hits are separate. Each helper returns synchronously with <=8 fresh submitted neighbors and <=72 CSR entries. Sentinel handling was exercised by the native1024-vector duplicate fixture, not claimed from SIFT when its observed sentinel counter is zero.

### Native SSD postings / pages / distance evaluations

| Scenario | H1 | H3 | C2000 | S2000 | C3200 | S3200 |
|---|---:|---:|---:|---:|---:|---:|
| unfilter | 23.828 / 143.651 / 918.673 | 23.809 / 143.101 / 913.684 | 23.830 / 143.666 / 918.616 | 23.830 / 143.665 / 918.600 | 23.828 / 143.651 / 918.673 | 23.828 / 143.639 / 918.587 |
| broad_tag | 7.333 / 40.823 / 153.371 | 24.000 / 133.697 / 469.746 | 24.000 / 133.860 / 471.448 | 24.000 / 133.841 / 471.371 | 24.000 / 133.836 / 471.443 | 24.000 / 133.858 / 471.466 |
| medium_tag | 0.814 / 4.212 / 14.284 | 23.912 / 119.365 / 333.056 | 24.000 / 122.744 / 339.898 | 24.000 / 122.717 / 339.720 | 24.000 / 122.612 / 339.449 | 24.000 / 122.635 / 339.446 |
| extreme_tag | 0.011 / 0.042 / 0.069 | 8.613 / 35.044 / 49.855 | 0.650 / 2.691 / 4.634 | 0.652 / 2.698 / 4.674 | 0.725 / 3.020 / 5.303 | 0.725 / 3.022 / 5.338 |
| numeric | 9.019 / 60.189 / 9.072 | 8.993 / 59.929 / 9.058 | 9.009 / 60.117 / 9.064 | 9.011 / 60.135 / 9.068 | 9.019 / 60.189 / 9.072 | 9.017 / 60.170 / 9.068 |
| mixed_dnf | 0.066 / 0.261 / 0.257 | 14.162 / 53.374 / 45.810 | 3.974 / 15.496 / 13.891 | 3.979 / 15.498 / 13.926 | 4.492 / 17.526 / 15.751 | 4.504 / 17.561 / 15.810 |

The native pipeline uses unchanged H/O setup and O_DIRECT SSD access. Full actual distance work is head actual plus disk distances, both in `summary.json`. Retrieval-minus-scan time is not physical SSD delay.

### Descriptive latency ratios

Reference is original H1 for unfilter and historical H3 for every filter. Recall mismatches prevent calling these same-recall costs. No weighted mixture.

| Scenario | S2000/reference | S2000/C2000 | S3200/reference | S3200/C3200 |
|---|---:|---:|---:|---:|
| unfilter | 1.106 | 1.000 | 1.158 | 0.994 |
| broad_tag | 1.505 | 1.002 | 1.585 | 0.994 |
| medium_tag | 1.773 | 0.999 | 1.882 | 1.005 |
| extreme_tag | 1.823 | 1.033 | 1.990 | 0.999 |
| numeric | 1.481 | 1.006 | 1.569 | 1.001 |
| mixed_dnf | 1.935 | 1.027 | 2.074 | 1.004 |

`supplier_usage.json` reports actual triggering/H3-query counts. `independent_validation.json` includes parent-derived own-point contributions, exact admission checks and previous-artifact preservation. Historical H3's predicate-guided candidate discovery remains the explicit comparison confound above.
