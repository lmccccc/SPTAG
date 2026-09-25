# Native H1 signature-first full-posting supplier

This new isolated version supersedes none of the preserved experiments. Production,
original indexes, old source/runtime snapshots and all old results remain untouched.
There is one fixed native configuration per mode, not a distance-cap grid.

## Four corrections

| Contract | Previous degree16 | This implementation / fixture |
|---|---|---|
| No added search budgets | Actual cap2000/3200 and member2048 | `Supplier.h` has no distance/member/row quota. Removed INI keys are rejected, not converted to an unlimited sentinel. An8192-point fixture processes4096 members and exceeds3200 real calls. |
| Signature before parent distance/scan | No parent signature pruning | `Engine::RowAllowed` precedes `Rank` and every child-H2 entry. Native `BuildHierarchyQuerySignature` plus `MayIntersect`; cached pass/reject/completed states. Fixtures reject rows with zero scans/parent calls. |
| Predicate before H1 candidate distance | Scored first | Native traversal filter and `Engine::Qualify` precede ordinary edge, leaf and CSR-child scoring. `Score` throws if an ineligible ordinary/child candidate reaches it. Posting OR own eligibility, not own attributes alone. |
| Complete selected posting, visited afterward | Stopped at exact deficit; partial cursors | Full H2 rows and full selected H3 rows, including their valid unvisited H2 children. Row completion is recorded only after its end. A closer last member beyond16 and no-rescan fixtures exercise this. |

## Fixed semantics and structural exceptions

The original native BKT H1 frontier, tree queues and visited state drive search.
Effective degree still means **distinct fresh** native H1 adjacency candidates
passing posting-admissible OR own-point-admissible checks; previously visited
eligible neighbors do not count. Raw fresh, eligible and enqueued counts differ.
Degree below16 triggers supply;16 is neither an output quota nor a row-scan stop.

The eight direct H2 owners are signature-checked before representative scoring,
then ordered by current-query distance, with ID tie-breaking. A selected row is
processed completely. Only at a row boundary, if the deficit remains and native
continuation permits work, select another valid unvisited owner. Exhausting that
direct scope permits H3: query-rank the union of the direct parents' eight owners.
A selected H3 row enumerates **all** children in immutable CSR order; every child
H2 signature is checked and every valid unvisited H2 row is fully processed.
Even if degree16/native MaxCheck is crossed, the selected H3 row and its children
finish before returning. A completed/rejected row is not rescanned. There is no
persistent upper queue, graph traversal upstairs, restart, whole-index rescue or
final-result-underfill trigger. Finite visited owner-derived scopes bound work.

Only native `MaxCheck=2048` remains, with target24 and SSD page limit15. It counts
native checked leaves/scored graph neighbors, **not** all distance calls. Rejected
singleton tree leaves are counted as checked at predicate inspection and are not
enqueued or scored; valid leaves retain the native pop-time checked accounting.
Internal routing-center and parent-choice distances do not increment this counter.
An already-selected supplier row ignores MaxCheck until fully processed; checked
overshoot is recorded, and the native frontier/termination then resumes unchanged.
There is no artificial distance/member limit, including in ordinary timing mode.

The existing native result/traversal-filter API is used, including its saved-tree
continuation when admitted spatial results are insufficient. No new retry is added.
Internal BKT centers represent subtrees, not just their own predicate: their real
distances remain necessary and are separately recorded as `supplyRouting`.
Terminal singleton leaves are prechecked; invalid collapsed-group representatives
remain structural centers, with eligible sibling leaves exposed through the same
tree queue. An invalid H1 is not expanded as an ordinary graph bridge.

Qualification reuses native nonempty-posting and H/O metadata admission (numeric
full-O uses `tailPostingFilter`) OR valid/not-deleted canonical VID satisfying the
exact native predicate. It is independent of top-k competition and result dedup.
Compared with the earlier version, the nearest24 posting-result heap also requires
the posting eligibility bit: an own-only or structural/upper representative cannot
consume a posting slot merely because the coarse head callback returns true.
Every actually evaluated canonical H1, including routing/parent identities, is
offered to native own-point admission using its known distance. No anonymous upper
work or lost parent own points.

Upper signatures use the exact frozen H3 categorical-anchor construction and
persisted selectivity domains. OR/DNF anchors are unioned, not ANDed. A missing or
out-of-domain represented anchor disables categorical signature pruning; numeric
queries without categorical anchors are all-pass upstairs, then use native H1
numeric eligibility. Missing expected persisted signatures fail explicitly.
`HierarchyGraphSignaturePruning=false` controls the unchanged historical H3 graph;
it does not disable the newly authorized supplier posting-signature checks.

Counters are read-only observations, never work limits. Ordinary C/S timing still
includes shared lightweight telemetry, eligibility/cache and admission bookkeeping;
profile additionally enables phase clocks and the independent real-call counter.
It is not a claim of zero-instrumentation prototype overhead. `supplyPredicateRejects`
counts distinct ineligible H1 identities, which can include structural/parent
identities whose distances are legitimate exceptions. Ordinary/CSR-child scoring
of an ineligible H1 is separately prohibited by a runtime assertion. Real graph,
routing, parent and child callbacks sum to actual head work; repeats are charged
and cached distances are reported separately.

`degree_audits` retain `[raw_before,effective_before,deficit,qualified_supplied,
raw_supplied,enqueued_supplied,members,stop,h2_scope_exhausted]`. Stops are0 enough
after a full row,3 native boundary,4 exhausted owner scope. Supplied counts may
exceed deficit and16. `row_audits` contain `[level,id,row_length,processed_entries]`;
H3 lengths count H2 children, with each child's H1 row audited separately.
Rejected rows and unique predicate-rejected identities are captured separately.

The discarded structural trial queued rejected leaves at `MaxDist`, delaying
their checked-leaf accounting until after almost all internal centers. Full-path
fixtures exposed this before completion; that partial matrix, source and binary
remain under `h1_fullrow_20260916` and `fullrow_initial_native`. The completed
variant uses eager checked accounting for rejected terminal leaves, measured as
`supplyRejectedLeaves`. It does not substitute another priority/distance or add
a budget. This is an explicit tree-filter integration choice, not threshold tuning.

The original top graph remains loaded as vector backing/reference baseline but
is not navigated by the supplier; its memory dependency has not been removed.
Historical H3 still has predicate-filtered top results, pre-score signature/support
compaction and saved-frontier16-to24 widening. Comparing it with this supplier is
not a geometry-only comparison, and latency ratios are not equal-recall claims.

## Reproduction

Authenticated648 native files from revision3552194536cb01dd70099e955a29e235a0cf4d2e
plus the saved original source diff/manifest. New toolchain:
`datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916_v2/`.
New output: `comparisons/h1_fullrow_20260916_v2/` in the same dataset root.

```sh
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/full/reconstruct.py \
  --repo SPTAG \
  --snapshot datasets/sift1m_zipf200_sparse193_numeric/build_runs/20260909T141212Z_lightweight_rescue \
  --output datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916_v2 \
  --authenticated-cache /home/baotonglu/.copilot/session-state/40fc7ebb-e617-4ec3-a17b-610068e95e5e/files
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/fullrow_native/integrate.py \
  datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916_v2/source
cmake -S datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916_v2/source \
  -B datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916_v2/build \
  -DCMAKE_BUILD_TYPE=Release -DSPDK=OFF -DROCKSDB=OFF -DTBB=ON -DLIBRARYONLY=ON
cmake --build datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916_v2/build \
  --target spannaclbench --parallel 4
cmake -S SPTAG/Tools/benchmarks/hierarchical_shortcut_native/fullrow_native \
  -B datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916_v2/tests \
  -DSPANN_ROOT=/mnt/nvme/baotonglu/mocheng/datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916_v2/source
cmake --build datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916_v2/tests --parallel 2
datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916_v2/tests/suppliertests
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/fullrow_native/finalize_source.py \
  datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916_v2/source
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/fullrow_native/run.py \
  --config SPTAG/Tools/benchmarks/hierarchical_shortcut_native/fullrow_native/experiment.ini
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/fullrow_native/verify.py \
  --config SPTAG/Tools/benchmarks/hierarchical_shortcut_native/fullrow_native/experiment.ini
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/fullrow_native/report.py \
  --results datasets/sift1m_zipf200_sparse193_numeric/comparisons/h1_fullrow_20260916_v2
```

First1000 queries,1000 warmups, one query thread, native NUMA2 affinity,
two rotating ordinary/profile repetitions for all four modes and six scenarios.
An untimed capture precedes warmup. Original frozen H1/H3 parity precedes the
custom fixtures and paired matrix. No new dataset, benchmark graph build,
per-scenario tuning or prior-result timing substitution.

## Completed paired experiment

All144 native processes certified; 7756 protected files unchanged. All24 summary rows cross-checked against96 ordinary/profile final native result lines. Frozen H1/H3 selected-head/native-work parity passed; frozen final IDs were unavailable.

### Recall@10 (%) / ordinary full latency (ms)

| Scenario | Original H1 | Historical H3 | Matched predicate-first C | Full-row S |
|---|---:|---:|---:|---:|
| unfilter | 90.73 / 0.960993 | 88.60 / 0.796117 | 90.74 / 1.684730 | 90.81 / 1.832050 |
| broad_tag | 72.25 / 0.704017 | 87.67 / 0.809743 | 90.55 / 1.351105 | 91.75 / 2.796830 |
| medium_tag | 19.77 / 0.486336 | 86.28 / 0.715298 | 77.63 / 0.960519 | 96.31 / 12.519450 |
| extreme_tag | 0.28 / 0.377800 | 90.07 / 0.440005 | 24.35 / 0.603500 | 50.33 / 1.727190 |
| numeric | 30.44 / 0.756255 | 42.18 / 0.706552 | 68.53 / 1.793275 | 70.48 / 3.284120 |
| mixed_dnf | 1.04 / 0.390736 | 76.10 / 0.511011 | 41.99 / 0.764543 | 88.80 / 7.847150 |

**The joint cost/quality goal is not met.** Unfilter S/H1 latency is 1.906x. Medium/mixed improve quality but incur large full-row CSR work; extreme still underfills. Ratios below are descriptive, not equal-recall costs.


### Underfilled queries (%) / mean returned count

| Scenario | H1 | H3 | C | S |
|---|---:|---:|---:|---:|
| unfilter | 0.0 / 10.000 | 0.0 / 10.000 | 0.0 / 10.000 | 0.0 / 10.000 |
| broad_tag | 0.0 / 10.000 | 0.0 / 10.000 | 0.0 / 10.000 | 0.0 / 10.000 |
| medium_tag | 45.9 / 5.583 | 0.0 / 10.000 | 0.0 / 10.000 | 0.0 / 10.000 |
| extreme_tag | 99.7 / 0.066 | 0.6 / 9.982 | 62.1 / 5.739 | 43.3 / 6.363 |
| numeric | 54.1 / 8.139 | 8.3 / 9.794 | 0.0 / 10.000 | 0.0 / 10.000 |
| mixed_dnf | 99.7 / 0.264 | 0.0 / 10.000 | 7.4 / 9.737 | 1.5 / 9.944 |

### Native postings / pages / SSD distance calls

| Scenario | H1 | H3 | C | S |
|---|---:|---:|---:|---:|
| unfilter | 23.828 / 143.651 / 918.673 | 23.809 / 143.101 / 913.684 | 23.843 / 143.757 / 919.340 | 23.843 / 143.707 / 918.998 |
| broad_tag | 7.333 / 40.823 / 153.371 | 24.000 / 133.697 / 469.746 | 24.000 / 133.891 / 470.432 | 24.000 / 133.806 / 471.257 |
| medium_tag | 0.814 / 4.212 / 14.284 | 23.912 / 119.365 / 333.056 | 24.000 / 120.262 / 333.924 | 24.000 / 122.360 / 339.470 |
| extreme_tag | 0.011 / 0.042 / 0.069 | 8.613 / 35.044 / 49.855 | 1.088 / 4.397 / 7.041 | 10.921 / 42.995 / 45.448 |
| numeric | 9.019 / 60.189 / 9.072 | 8.993 / 59.929 / 9.058 | 23.983 / 157.318 / 22.285 | 23.983 / 156.610 / 22.242 |
| mixed_dnf | 0.066 / 0.261 / 0.257 | 14.162 / 53.374 / 45.810 | 5.849 / 22.043 / 20.015 | 23.237 / 89.637 / 72.058 |

### Actual head / SSD / total distance calls

| Scenario | H1 | H3 | C | S |
|---|---:|---:|---:|---:|
| unfilter | 2231.960 / 918.673 / 3150.633 | 2041.349 / 913.684 / 2955.033 | 2226.294 / 919.340 / 3145.634 | 2370.594 / 918.998 / 3289.592 |
| broad_tag | 2231.960 / 153.371 / 2385.331 | 1507.451 / 469.746 / 1977.197 | 1021.971 / 470.432 / 1492.403 | 2467.521 / 471.257 / 2938.778 |
| medium_tag | 2231.960 / 14.284 / 2246.244 | 1185.487 / 333.056 / 1518.543 | 451.467 / 333.924 / 785.391 | 3803.790 / 339.470 / 4143.260 |
| extreme_tag | 2231.960 / 0.069 / 2232.029 | 646.758 / 49.855 / 696.613 | 697.651 / 7.041 / 704.692 | 1019.154 / 45.448 / 1064.602 |
| numeric | 2231.960 / 9.072 / 2241.032 | 2041.349 / 9.058 / 2050.407 | 1195.722 / 22.285 / 1218.007 | 2433.690 / 22.242 / 2455.932 |
| mixed_dnf | 2231.960 / 0.257 / 2232.217 | 713.484 / 45.810 / 759.294 | 702.980 / 20.015 / 722.995 | 2586.749 / 72.058 / 2658.807 |

### Read-only distance ledger (per query)

| Scenario/case | Graph | Routing | Parent | Child | Cache | Repeats |
|---|---:|---:|---:|---:|---:|---:|
| unfilter/control | 2066.475 | 159.819 | 0.000 | 0.000 | 0.000 | 104.208 |
| unfilter/supplier | 928.234 | 153.380 | 65.866 | 1223.114 | 217.896 | 37.461 |
| broad_tag/control | 842.783 | 179.188 | 0.000 | 0.000 | 0.000 | 61.523 |
| broad_tag/supplier | 285.613 | 141.794 | 283.603 | 1756.511 | 264.010 | 35.496 |
| medium_tag/control | 71.872 | 379.595 | 0.000 | 0.000 | 0.000 | 5.479 |
| medium_tag/supplier | 118.803 | 945.156 | 848.053 | 1891.778 | 1699.046 | 175.284 |
| extreme_tag/control | 0.849 | 696.802 | 0.000 | 0.000 | 0.000 | 0.053 |
| extreme_tag/supplier | 0.701 | 607.372 | 399.806 | 11.275 | 13.989 | 1.041 |
| numeric/control | 1027.273 | 168.449 | 0.000 | 0.000 | 0.000 | 63.895 |
| numeric/supplier | 360.230 | 137.728 | 254.569 | 1681.163 | 272.718 | 34.695 |
| mixed_dnf/control | 4.307 | 698.673 | 0.000 | 0.000 | 0.000 | 0.082 |
| mixed_dnf/supplier | 5.220 | 813.557 | 1607.519 | 160.453 | 170.888 | 25.988 |

### Pruning and complete-row work (per query)

| Scenario/case | SignatureChecks | SignatureRejects | PredicateChecks | PredicateRejects | RejectedLeaves | H2Completed | H3Completed | PostingSkips | Members |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| unfilter/control | 0.000 | 0.000 | 2122.086 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| unfilter/supplier | 155.388 | 0.000 | 2333.133 | 0.000 | 0.000 | 41.786 | 0.246 | 68.698 | 2359.343 |
| broad_tag/control | 0.000 | 0.000 | 2654.153 | 1814.409 | 135.092 | 0.000 | 0.000 | 0.000 | 0.000 |
| broad_tag/supplier | 435.773 | 0.033 | 6700.784 | 4596.774 | 63.223 | 201.551 | 2.787 | 216.180 | 11249.966 |
| medium_tag/control | 0.000 | 0.000 | 2309.740 | 2227.721 | 813.772 | 0.000 | 0.000 | 0.000 | 0.000 |
| medium_tag/supplier | 5782.658 | 1035.495 | 53786.915 | 51866.897 | 124.997 | 3973.690 | 283.131 | 10427.903 | 227807.485 |
| extreme_tag/control | 0.000 | 0.000 | 2755.789 | 2754.701 | 2047.463 | 0.000 | 0.000 | 0.000 | 0.000 |
| extreme_tag/supplier | 5770.247 | 5289.193 | 7058.891 | 7046.656 | 1686.344 | 91.699 | 389.286 | 859.409 | 26544.610 |
| numeric/control | 0.000 | 0.000 | 2651.913 | 1617.260 | 117.216 | 0.000 | 0.000 | 0.000 | 0.000 |
| numeric/supplier | 397.898 | 0.000 | 5796.636 | 3669.588 | 59.200 | 166.641 | 2.194 | 195.216 | 9351.660 |
| mixed_dnf/control | 0.000 | 0.000 | 2801.048 | 2795.199 | 2045.452 | 0.000 | 0.000 | 0.000 | 0.000 |
| mixed_dnf/supplier | 13901.692 | 11150.675 | 45192.042 | 45028.544 | 1489.382 | 1148.557 | 1583.758 | 11533.135 | 146961.455 |

### Native continuation and degree (per query)

| Scenario/case | Calls | Qualified | Neighbors | Queued | NativeOvershoot | ContinuationPops | Fills | NativeStops | ScopeStops |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| unfilter/control | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| unfilter/supplier | 32.821 | 1300.175 | 1300.175 | 143.902 | 58.473 | 81.514 | 32.812 | 0.009 | 0.000 |
| broad_tag/control | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| broad_tag/supplier | 67.975 | 1808.992 | 5740.514 | 232.105 | 65.517 | 74.954 | 67.795 | 0.180 | 0.000 |
| medium_tag/control | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| medium_tag/supplier | 81.136 | 1910.135 | 53440.583 | 261.373 | 3.029 | 94.239 | 69.999 | 0.537 | 10.600 |
| extreme_tag/control | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| extreme_tag/supplier | 11.544 | 11.429 | 4409.185 | 11.429 | 0.000 | 11.503 | 0.000 | 0.005 | 11.539 |
| numeric/control | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| numeric/supplier | 66.742 | 1737.093 | 4807.655 | 221.182 | 64.815 | 76.497 | 66.582 | 0.160 | 0.000 |
| mixed_dnf/control | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| mixed_dnf/supplier | 111.377 | 162.186 | 42158.426 | 136.474 | 0.007 | 123.406 | 5.622 | 0.018 | 105.737 |

### Started-helper effective degree and observed query counts

| Scenario | Before -> after | Triggered | H3 used | Native overshoot |
|---|---:|---:|---:|---:|
| unfilter | 7.933 -> 47.547 | 1000 | 127 | 705 |
| broad_tag | 3.467 -> 30.080 | 1000 | 608 | 880 |
| medium_tag | 0.056 -> 23.599 | 1000 | 1000 | 660 |
| extreme_tag | 0.006 -> 0.996 | 449 | 449 | 0 |
| numeric | 4.576 -> 30.603 | 1000 | 481 | 846 |
| mixed_dnf | 0.002 -> 1.458 | 963 | 963 | 4 |

### Sparse helper availability

The helper is called only inside a native H1 expansion, never as a final underfill repair. Predicate-first traversal can exhaust native checked leaves without an H1 pop, although structural-center scoring can still admit valid own points/postings. Conditional subsets are diagnostics, **not** substitutes for overall recall.

| Scenario | Helper called | Queries | Recall (%) | Underfilled | Zero H1 pops |
|---|---|---:|---:|---:|---:|
| extreme_tag | False | 551 | 10.09 | 433 | 545 |
| extreme_tag | True | 449 | 99.71 | 0 | 0 |
| mixed_dnf | False | 37 | 14.59 | 15 | 37 |
| mixed_dnf | True | 963 | 91.65 | 0 | 0 |

No-helper ordered results, selected heads, evaluations and actual calls exactly match the predicate-first graph-only control. No result-triggered repair was added.


### Descriptive comparisons, not recall-matched costs

| Scenario | Reference | S/reference latency | S/C latency | S-C recall pp |
|---|---|---:|---:|---:|
| unfilter | h1 | 1.906 | 1.087 | +0.07 |
| broad_tag | h3 | 3.454 | 2.070 | +1.20 |
| medium_tag | h3 | 17.502 | 13.034 | +18.68 |
| extreme_tag | h3 | 3.925 | 2.862 | +25.98 |
| numeric | h3 | 4.648 | 1.831 | +1.95 |
| mixed_dnf | h3 | 15.356 | 10.264 | +46.81 |

No weighted workload mixture or post-result policy tuning. Original H3 retains its distinct predicate-guided top search and widening. Native routing centers remain explicit pre-predicate-distance exceptions, not hidden supplier candidates.
