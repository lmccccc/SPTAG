# Native H1 effective-degree16 completion

This is a new isolated experiment. Earlier zero-fresh/fixed8 supplementation,
parent-queue online policies, fixed overlays, their code/docs/results, production
sources and original data are preserved.

## Measured outcome

**Completed216 certified native processes; the joint goal is not met.**
Unfiltered S2000/S3200 recall is90.72%/90.79% versus original H1's90.73%,
but full latency is1.426/1.528ms versus0.957ms (1.49x/1.60x).
Matched-control comparisons separate qualification/accounting overhead from
supplementation: the new C2000/C3200 control itself costs1.316/1.416ms.

Effective-degree16 is genuinely active, not the old zero-fresh trigger.
Every query triggers supply. For unfilter S2000,45.957 calls/query produce
45.582 filled deficits. Extreme and mixed use one much longer helper per query
and never fill their16-slot deficit: at cap2000 they inspect1994.037 CSR
entries and submit1500.322 fresh H1s on average, but only0.532/3.154 qualify.
357 queries stop that helper at the distance cap and643 at the member ceiling.
At cap3200 all1000 extreme/mixed queries reach the2048-member ceiling.
These are actual partial returns, not claims that the entire hierarchy was
exhausted or that16 qualified neighbors were found.

Extreme recall is17.41%/19.49% versus historical H3's90.07%; underfill is
74.4%/71.5%. Mixed recall is30.57%/35.05% versus76.10%, with26.6%/19.5%
underfill. Compared with the same-budget control, medium recall decreases
5.77/3.26 percentage points and mixed4.91/3.53 points. There was no tuning
after these results. Historical H3's different predicate-guided discovery is
still relevant; this is not evidence about hierarchy geometry alone.

## Preregistered semantics

The sole search frontier is the original native H1 BKT frontier, including its
tree continuation, visited state, distance-bound heap and negative sentinels.
After an H1 adjacency expansion, count distinct fresh neighbors that are
**posting-admissible OR own-point-admissible**. If this effective degree is below
16, synchronously request exactly `16 - effective_degree` qualified neighbors.
Degree16 does not trigger; degree15 requests one, not another16.

Fresh means unseen in the native traversal's visited set. All valid fresh
neighbors are scored before qualification. Ineligible neighbors remain eligible
for spatial frontier insertion as bridges; they do not fill a missing slot.
Native distance-bound rejection does not disqualify an otherwise admissible
neighbor from this degree counter. Raw fresh, qualified and actually enqueued
counts are separate. A node satisfying both admission branches fills only one
slot. Parent-representative scoring alone never fills a neighbor slot.

### Exact callback meaning

The previous native `admitHeadPoint(head, knownDistance)` return value indicates
whether it computed a distance, **not** own-point eligibility; it always returns
false when reusing a supplied distance. It cannot faithfully measure degree.
This version therefore adds a separate post-score qualification callback:

* Posting bit1: the existing H3 head-admission rule, native nonempty-posting
  validity and the same native H/O posting predicate used by this static
  non-hybrid full path. Anchored filters use limited-tag support; numeric full-O
  uses the native tail-posting filter when available, otherwise the ordinary
  posting filter. These metadata tests are conservative admission, not an SSD
  scan or an oracle guaranteeing an exact matching record.
* Own bit2: valid canonical VID, not deleted, and the exact native point
  predicate. This is admissibility, independent of current top10 competition
  or already-admitted dedup state.

All evaluated H1s, including canonical H1 identities of H2/H3 representatives,
still receive the original native own-point and posting-result admission.
The existing nearest24 posting-head result selection and final native SSD setup
are unchanged. In particular, qualification does not introduce a new
numeric-filtered posting-head result heap. The explicit static/non-hybrid guard
fails instead of approximating an unsupported callback contract.

### Tiered scope and ceilings

Native INIs fix `ShortcutEffectiveDegree=16`, `ShortcutMemberLimit=2048`
(**shared per query**, H1 and H2 member entries combined), actual distance cap
2000/3200, native MaxCheck2048, target24 heads and page limit15.

1. Query-rank all eight direct H2 owners of the current H1. Walk their existing
   CSR rows lazily in that order, preserving per-query row cursors. Stop only
   when the qualified deficit is filled, all eight rows are genuinely
   exhausted, or a shared ceiling stops work. There is no4-row/8-entry cutoff.
2. Only if all eight H2 rows are exhausted, a deficit remains, and work is still
   permitted, query-rank the union of their eight direct H3 owners (at most64
   alternatives). Walk these H3 rows lazily and descend through H2 members.
   Minimal scheduling choice: H2 children inside an H3 row follow immutable CSR
   order, one row at a time, not an independently sorted upper frontier.
   An H3 cursor advances past an H2 child only when that child's H1 row is
   exhausted. Revisiting a partially consumed child resumes its H1 cursor;
   the reread H3 member is charged again.
3. Immediately return to the same H1 search. No upper queue survives a call.
   A distance/member/native-checked ceiling returns partial supply; it is
   **not labeled H2 exhaustion and never causes escalation**. Exhaustion means
   only the current owner-derived candidate scope, never the entire hierarchy.

All real native tree/graph, parent and child distance calls count, including
repeats. Cache hits are reported separately. All representative vectors are
byte-checked against their composed canonical H1 mapping at initialization.
No global scan, new graph, predicate-selected parent ranking, signature-guided
upper search, final-result-underfill retry or scenario-specific tuning occurs.
This deliberately allows predicate-dependent traces through the newly requested
qualified-degree trigger/quota; same-predicate count-off/repeat equality remains
mandatory. The matched control performs the same qualification/accounting but
does not supplement.

The unchanged loader still loads the historical H3 graph as vector backing and
for the separate H3 baseline. Experimental queries do not navigate it, but its
memory/file dependency is **not removed**. Reverse owners/caches initialize in
untimed initial capture.

## Audit and comparison

Per-call `degree_audits` are
`[raw_before, effective_before, deficit, qualified_supplied, raw_supplied,
enqueued_supplied, member_entries, stop_reason, h2_scope_exhausted]`.
Stop reasons:0 filled,1 distance cap,2 member ceiling,3 native checked limit,
4 exhausted owner-derived scope. Per-query totals also include raw/effective
before/after degree sums, trigger opportunities, calls, qualified fills, H2-only
fills, H2 exhaustion, H3 row use and each stopping cause. Parent scoring may
admit points but contributes no degree unless that H1 is subsequently a fresh
native neighbor. Partial final native adjacency work is counted; no helper is
started after a distance exception interrupts that expansion.

Same first1000 queries,1000 warmups, one query thread, CPU/memory NUMA node2;
all six existing unfilter/broad/medium/extreme/numeric/mixed workloads. Two
rotating paired repetitions of ordinary/profile modes. Original H1/H3 frozen
parity and first-eight-query native fixtures precede the main comparison.

Historical H3 still uses predicate-filtered top results, pre-score signature
compaction and16-to24 widening. This comparison does not isolate geometry or
attribute its sparse advantage solely to supplier distance spending.
Recall-mismatched cost ratios are descriptive; no weighted workload mixture.

New source/build: `toolchains/h1_degree16_20260916/`; new output:
`comparisons/h1_degree16_20260916/`, under
`datasets/sift1m_zipf200_sparse193_numeric/`.

```sh
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/full/reconstruct.py \
  --repo SPTAG \
  --snapshot datasets/sift1m_zipf200_sparse193_numeric/build_runs/20260909T141212Z_lightweight_rescue \
  --output datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_degree16_20260916 \
  --authenticated-cache /home/baotonglu/.copilot/session-state/40fc7ebb-e617-4ec3-a17b-610068e95e5e/files
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/degree16_native/integrate.py \
  datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_degree16_20260916/source
cmake -S datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_degree16_20260916/source \
  -B datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_degree16_20260916/build \
  -DCMAKE_BUILD_TYPE=Release -DSPDK=OFF -DROCKSDB=OFF -DTBB=ON -DLIBRARYONLY=ON
cmake --build datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_degree16_20260916/build \
  --target spannaclbench --parallel 4
cmake -S SPTAG/Tools/benchmarks/hierarchical_shortcut_native/degree16_native \
  -B datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_degree16_20260916/tests \
  -DSPANN_ROOT=/mnt/nvme/baotonglu/mocheng/datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_degree16_20260916/source
cmake --build datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_degree16_20260916/tests --parallel 2
datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_degree16_20260916/tests/suppliertests
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/degree16_native/run.py \
  --config SPTAG/Tools/benchmarks/hierarchical_shortcut_native/degree16_native/experiment.ini
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/degree16_native/verify.py \
  --config SPTAG/Tools/benchmarks/hierarchical_shortcut_native/degree16_native/experiment.ini
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/degree16_native/report.py \
  --results datasets/sift1m_zipf200_sparse193_numeric/comparisons/h1_degree16_20260916
```
## Completed paired experiment

All216 native processes completed; 5314 protected files retain their original hashes/size/mtime. Original H1/H3 match frozen selected heads/distances and native recall/work. Frozen final IDs were unavailable and are not claimed. Counted/uncounted ordered outputs and repeated same-predicate traces match. Native own heaps equal the exact nearest10 matches among **all** evaluated H1s, including parent-first representatives.

### Final Recall@10 (%) / ordinary full latency (ms)

| Scenario | H1 | H3 | C2000 | S2000 | C3200 | S3200 |
|---|---:|---:|---:|---:|---:|---:|
| unfilter | 90.73 / 0.956695 | 88.60 / 0.804433 | 90.68 / 1.315500 | 90.72 / 1.426475 | 90.73 / 1.415880 | 90.79 / 1.528060 |
| broad_tag | 72.25 / 0.708998 | 87.67 / 0.816426 | 91.42 / 1.428325 | 91.13 / 1.563795 | 91.54 / 1.520110 | 91.47 / 1.655345 |
| medium_tag | 19.77 / 0.490302 | 86.28 / 0.726954 | 93.48 / 1.378275 | 87.71 / 1.476630 | 94.53 / 1.461295 | 91.27 / 1.594795 |
| extreme_tag | 0.28 / 0.369166 | 90.07 / 0.445346 | 17.26 / 0.861138 | 17.41 / 0.948640 | 19.18 / 0.951533 | 19.49 / 1.084010 |
| numeric | 30.44 / 0.748865 | 42.18 / 0.707575 | 48.67 / 1.465360 | 48.46 / 1.558975 | 49.30 / 1.556245 | 49.04 / 1.747580 |
| mixed_dnf | 1.04 / 0.390359 | 76.10 / 0.508366 | 35.48 / 1.001520 | 30.57 / 1.107160 | 38.58 / 1.103810 | 35.05 / 1.252305 |

`C` is the matched native BKT admission/budget control; `S` is synchronous supply. These are not the old manual-frontier G/O cases. Timings are two fresh ordinary repetitions, not profile timings or historical spliced values.

### Underfilled queries (%)

| Scenario | H1 | H3 | C2000 | S2000 | C3200 | S3200 |
|---|---:|---:|---:|---:|---:|---:|
| unfilter | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| broad_tag | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| medium_tag | 45.9 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| extreme_tag | 99.7 | 0.6 | 77.8 | 74.4 | 74.7 | 71.5 |
| numeric | 54.1 | 8.3 | 0.3 | 0.0 | 0.3 | 0.0 |
| mixed_dnf | 99.7 | 0.0 | 28.5 | 26.6 | 22.2 | 19.5 |

### Native navigation and helper work by scenario

Per-query means; upper work is supplier row choice, never an upper graph search. All parent distances also represent canonical H1 candidates offered to native admission.

| Scenario/case | Graph actual | Parent actual | Child actual | Distinct H1 | CSR entries | Calls | Raw supplied | Qualified | Enqueued | H2 rows | H3 rows |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| unfilter/control2000 | 1948.903 | 0.000 | 0.000 | 1858.085 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| unfilter/supplier2000 | 1598.555 | 93.223 | 295.090 | 1892.287 | 805.826 | 45.957 | 328.679 | 328.679 | 44.652 | 54.915 | 0.079 |
| unfilter/control3200 | 2231.960 | 0.000 | 0.000 | 2125.550 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| unfilter/supplier3200 | 1899.646 | 116.092 | 373.911 | 2268.814 | 1033.024 | 58.876 | 414.311 | 414.311 | 45.439 | 70.368 | 0.086 |
| broad_tag/control2000 | 1948.903 | 0.000 | 0.000 | 1858.085 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| broad_tag/supplier2000 | 865.196 | 75.384 | 1047.453 | 1938.308 | 1755.397 | 30.184 | 1124.724 | 352.110 | 211.804 | 52.826 | 0.344 |
| broad_tag/control3200 | 2231.960 | 0.000 | 0.000 | 2125.550 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| broad_tag/supplier3200 | 1047.828 | 83.501 | 1184.490 | 2248.996 | 1978.807 | 33.988 | 1269.188 | 397.445 | 214.231 | 59.409 | 0.345 |
| medium_tag/control2000 | 1948.903 | 0.000 | 0.000 | 1858.085 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| medium_tag/supplier2000 | 529.568 | 74.662 | 1389.640 | 1968.591 | 1956.785 | 3.825 | 1466.642 | 50.987 | 312.853 | 37.644 | 2.617 |
| medium_tag/control3200 | 2231.960 | 0.000 | 0.000 | 2125.550 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| medium_tag/supplier3200 | 787.924 | 78.497 | 1458.045 | 2278.241 | 2048.000 | 3.986 | 1537.677 | 53.401 | 315.935 | 39.352 | 2.773 |
| extreme_tag/control2000 | 1948.903 | 0.000 | 0.000 | 1858.085 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| extreme_tag/supplier2000 | 521.986 | 37.137 | 1434.892 | 1969.838 | 1994.037 | 1.000 | 1500.322 | 0.532 | 302.668 | 38.371 | 1.078 |
| extreme_tag/control3200 | 2231.960 | 0.000 | 0.000 | 2125.550 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| extreme_tag/supplier3200 | 785.154 | 37.137 | 1474.782 | 2253.712 | 2048.000 | 1.000 | 1541.292 | 0.540 | 304.564 | 39.378 | 1.082 |
| numeric/control2000 | 1948.903 | 0.000 | 0.000 | 1858.085 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| numeric/supplier2000 | 941.212 | 86.531 | 960.526 | 1933.210 | 1669.100 | 34.766 | 1035.403 | 366.319 | 189.283 | 55.819 | 0.351 |
| numeric/control3200 | 2231.960 | 0.000 | 0.000 | 2125.550 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| numeric/supplier3200 | 1128.190 | 97.156 | 1104.425 | 2257.405 | 1917.116 | 39.988 | 1188.083 | 422.115 | 191.646 | 64.034 | 0.351 |
| mixed_dnf/control2000 | 1948.903 | 0.000 | 0.000 | 1858.085 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| mixed_dnf/supplier2000 | 521.986 | 37.137 | 1434.892 | 1969.838 | 1994.037 | 1.000 | 1500.322 | 3.154 | 302.668 | 38.371 | 1.078 |
| mixed_dnf/control3200 | 2231.960 | 0.000 | 0.000 | 2125.550 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| mixed_dnf/supplier3200 | 785.154 | 37.137 | 1474.782 | 2253.712 | 2048.000 | 1.000 | 1541.292 | 3.246 | 304.564 | 39.378 | 1.082 |

### Effective degree and completion

Degrees average over native adjacency expansions, including partial final expansions. Fill/stop counts are means per query; H2 fills are a subset of all fills.

| Scenario/case | Raw before | Effective before | Effective after | Triggers | Requested | Qualified | Fills | H2 fills | H2 exhausted | Distance stop | Member stop | Native stop | Scope stop |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| unfilter/control2000 | 13.352 | 13.352 | 13.352 | 73.216 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| unfilter/supplier2000 | 12.849 | 12.849 | 16.250 | 58.219 | 330.997 | 328.679 | 45.582 | 45.504 | 0.079 | 0.225 | 0.150 | 0.000 | 0.000 |
| unfilter/control3200 | 11.087 | 11.087 | 11.087 | 115.762 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| unfilter/supplier3200 | 9.085 | 9.085 | 11.548 | 123.687 | 416.743 | 414.311 | 58.473 | 58.388 | 0.086 | 0.000 | 0.220 | 0.183 | 0.000 |
| broad_tag/control2000 | 13.352 | 4.194 | 4.194 | 118.671 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| broad_tag/supplier2000 | 8.687 | 2.726 | 8.719 | 57.820 | 358.509 | 352.110 | 29.327 | 29.008 | 0.344 | 0.476 | 0.381 | 0.000 | 0.000 |
| broad_tag/control3200 | 11.087 | 3.481 | 3.481 | 168.570 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| broad_tag/supplier3200 | 4.038 | 1.267 | 3.622 | 168.726 | 404.088 | 397.445 | 33.104 | 32.784 | 0.345 | 0.000 | 0.660 | 0.224 | 0.000 |
| medium_tag/control2000 | 13.352 | 0.467 | 0.467 | 118.702 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| medium_tag/supplier2000 | 5.571 | 0.194 | 1.743 | 31.985 | 59.304 | 50.987 | 2.846 | 0.698 | 2.616 | 0.473 | 0.506 | 0.000 | 0.000 |
| medium_tag/control3200 | 11.087 | 0.388 | 0.388 | 168.604 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| medium_tag/supplier3200 | 2.500 | 0.086 | 0.397 | 171.753 | 61.816 | 53.401 | 2.987 | 0.722 | 2.756 | 0.000 | 0.999 | 0.000 | 0.000 |
| extreme_tag/control2000 | 13.352 | 0.005 | 0.005 | 118.702 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| extreme_tag/supplier2000 | 5.596 | 0.002 | 0.019 | 30.519 | 15.997 | 0.532 | 0.000 | 0.000 | 1.000 | 0.357 | 0.643 | 0.000 | 0.000 |
| extreme_tag/control3200 | 11.087 | 0.004 | 0.004 | 168.604 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| extreme_tag/supplier3200 | 2.451 | 0.001 | 0.004 | 174.263 | 15.997 | 0.540 | 0.000 | 0.000 | 1.000 | 0.000 | 1.000 | 0.000 | 0.000 |
| numeric/control2000 | 13.352 | 5.093 | 5.093 | 118.195 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| numeric/supplier2000 | 9.317 | 3.615 | 9.434 | 61.815 | 371.887 | 366.319 | 33.980 | 33.659 | 0.351 | 0.441 | 0.345 | 0.000 | 0.000 |
| numeric/control3200 | 11.087 | 4.236 | 4.236 | 168.062 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| numeric/supplier3200 | 4.520 | 1.753 | 4.256 | 168.380 | 428.086 | 422.115 | 39.141 | 38.820 | 0.351 | 0.000 | 0.573 | 0.274 | 0.000 |
| mixed_dnf/control2000 | 13.352 | 0.028 | 0.028 | 118.702 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| mixed_dnf/supplier2000 | 5.596 | 0.011 | 0.111 | 30.519 | 15.964 | 3.154 | 0.000 | 0.000 | 1.000 | 0.357 | 0.643 | 0.000 | 0.000 |
| mixed_dnf/control3200 | 11.087 | 0.023 | 0.023 | 168.604 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| mixed_dnf/supplier3200 | 2.451 | 0.005 | 0.024 | 174.263 | 15.964 | 3.246 | 0.000 | 0.000 | 1.000 | 0.000 | 1.000 | 0.000 | 0.000 |

Effective degree among **started helper calls**, excluding later expansions where a shared ceiling prevents starting another helper:

| Scenario/case | Effective before | Effective after | Requested per call | Qualified per call |
|---|---:|---:|---:|---:|
| unfilter/supplier2000 | 8.798 | 15.950 | 7.202 | 7.152 |
| unfilter/supplier3200 | 8.922 | 15.959 | 7.078 | 7.037 |
| broad_tag/supplier2000 | 4.123 | 15.788 | 11.877 | 11.665 |
| broad_tag/supplier3200 | 4.111 | 15.805 | 11.889 | 11.694 |
| medium_tag/supplier2000 | 0.496 | 13.826 | 15.504 | 13.330 |
| medium_tag/supplier3200 | 0.492 | 13.889 | 15.508 | 13.397 |
| extreme_tag/supplier2000 | 0.003 | 0.535 | 15.997 | 0.532 |
| extreme_tag/supplier3200 | 0.003 | 0.543 | 15.997 | 0.540 |
| numeric/supplier2000 | 5.303 | 15.840 | 10.697 | 10.537 |
| numeric/supplier3200 | 5.295 | 15.851 | 10.705 | 10.556 |
| mixed_dnf/supplier2000 | 0.036 | 3.190 | 15.964 | 3.154 |
| mixed_dnf/supplier3200 | 0.036 | 3.282 | 15.964 | 3.246 |

### Observed query counts (out of1000)

| Scenario/case | Helper triggered | Used H3 | Distance cap exhausted | Member ceiling reached |
|---|---:|---:|---:|---:|
| unfilter/control2000 | 0 | 0 | 837 | 0 |
| unfilter/supplier2000 | 1000 | 44 | 946 | 154 |
| unfilter/control3200 | 0 | 0 | 0 | 0 |
| unfilter/supplier3200 | 1000 | 49 | 0 | 224 |
| broad_tag/control2000 | 0 | 0 | 837 | 0 |
| broad_tag/supplier2000 | 1000 | 153 | 947 | 385 |
| broad_tag/control3200 | 0 | 0 | 0 | 0 |
| broad_tag/supplier3200 | 1000 | 154 | 0 | 671 |
| medium_tag/control2000 | 0 | 0 | 837 | 0 |
| medium_tag/supplier2000 | 1000 | 991 | 966 | 507 |
| medium_tag/control3200 | 0 | 0 | 0 | 0 |
| medium_tag/supplier3200 | 1000 | 995 | 0 | 1000 |
| extreme_tag/control2000 | 0 | 0 | 837 | 0 |
| extreme_tag/supplier2000 | 1000 | 1000 | 969 | 646 |
| extreme_tag/control3200 | 0 | 0 | 0 | 0 |
| extreme_tag/supplier3200 | 1000 | 1000 | 0 | 1000 |
| numeric/control2000 | 0 | 0 | 837 | 0 |
| numeric/supplier2000 | 1000 | 147 | 946 | 350 |
| numeric/control3200 | 0 | 0 | 0 | 0 |
| numeric/supplier3200 | 1000 | 147 | 0 | 577 |
| mixed_dnf/control2000 | 0 | 0 | 837 | 0 |
| mixed_dnf/supplier2000 | 1000 | 1000 | 969 | 646 |
| mixed_dnf/control3200 | 0 | 0 | 0 | 0 |
| mixed_dnf/supplier3200 | 1000 | 1000 | 0 | 1000 |

Caps are ceilings, not forced expenditure: the original checked-leaf limit and native convergence also stop search. Every query has exactly one native H1 search start. Real repeated callbacks are charged; cache hits are separate. Each helper returns synchronously with at most the requested qualified deficit and2048 CSR entries shared across the entire query. Raw supplied neighbors can exceed16 when some are ineligible. Sentinel handling was exercised by the native1024-vector duplicate fixture, not claimed from SIFT when its observed sentinel counter is zero.

### Native SSD postings / pages / distance evaluations

| Scenario | H1 | H3 | C2000 | S2000 | C3200 | S3200 |
|---|---:|---:|---:|---:|---:|---:|
| unfilter | 23.828 / 143.651 / 918.673 | 23.809 / 143.101 / 913.684 | 23.830 / 143.666 / 918.616 | 23.829 / 143.676 / 918.696 | 23.828 / 143.651 / 918.673 | 23.829 / 143.631 / 918.618 |
| broad_tag | 7.333 / 40.823 / 153.371 | 24.000 / 133.697 / 469.746 | 24.000 / 133.860 / 471.448 | 24.000 / 133.933 / 471.678 | 24.000 / 133.836 / 471.443 | 24.000 / 133.886 / 471.436 |
| medium_tag | 0.814 / 4.212 / 14.284 | 23.912 / 119.365 / 333.056 | 24.000 / 122.744 / 339.898 | 24.000 / 121.162 / 335.311 | 24.000 / 122.612 / 339.449 | 24.000 / 121.804 / 336.375 |
| extreme_tag | 0.011 / 0.042 / 0.069 | 8.613 / 35.044 / 49.855 | 0.650 / 2.691 / 4.634 | 0.708 / 2.844 / 5.578 | 0.725 / 3.020 / 5.303 | 0.795 / 3.213 / 6.206 |
| numeric | 9.019 / 60.189 / 9.072 | 8.993 / 59.929 / 9.058 | 9.009 / 60.117 / 9.064 | 9.014 / 60.043 / 9.077 | 9.019 / 60.189 / 9.072 | 9.018 / 60.115 / 9.070 |
| mixed_dnf | 0.066 / 0.261 / 0.257 | 14.162 / 53.374 / 45.810 | 3.974 / 15.496 / 13.891 | 4.203 / 15.761 / 14.907 | 4.492 / 17.526 / 15.751 | 4.797 / 18.098 / 16.825 |

The native pipeline uses unchanged H/O setup and O_DIRECT SSD access. Full actual distance work is head actual plus disk distances, both in `summary.json`. Retrieval-minus-scan time is not physical SSD delay.

### Descriptive latency ratios

Reference is original H1 for unfilter and historical H3 for every filter. Recall mismatches prevent calling these same-recall costs. No weighted mixture.

| Scenario | S2000/reference | S2000/C2000 | S3200/reference | S3200/C3200 |
|---|---:|---:|---:|---:|
| unfilter | 1.491 | 1.084 | 1.597 | 1.079 |
| broad_tag | 1.915 | 1.095 | 2.028 | 1.089 |
| medium_tag | 2.031 | 1.071 | 2.194 | 1.091 |
| extreme_tag | 2.130 | 1.102 | 2.434 | 1.139 |
| numeric | 2.203 | 1.064 | 2.470 | 1.123 |
| mixed_dnf | 2.178 | 1.105 | 2.463 | 1.135 |

`supplier_usage.json` reports actual triggering/H3-query counts. `independent_validation.json` includes parent-derived own-point contributions, exact admission checks and previous-artifact preservation. Historical H3's predicate-guided candidate discovery remains the explicit comparison confound above.
