# Native H1 startup deficiency and whole-row supplier

This is a separate correction of `fullrow_native` / `h1_fullrow_20260916_v2`.
The v2 sources, binary and measurements are preserved. In v2, 545 extreme
queries had no H1 pop, so its expansion-only supplier was unreachable. Also,
v2 `Supply` called `openH2` inside the complete H3-member loop: completing an
upper row incorrectly implied scanning its whole eligible descendant subtree.
Both issues are corrected here; no thresholds were searched.

## Startup inside the original native search

The original BKT tree initialization and initial search run once. Before the
first native H1 pop, supplier mode checks the size of that same native H1
priority queue once. If it is below the INI's effective degree16, there is a
reachable anchor, and native checked leaves are below MaxCheck2048, it invokes
the same `Engine::Supply` used for ordinary H1 expansions. The same extracted
native `expandEdges` lambda performs visited checks, qualification, distances,
admission, spatial-bound checks and queue insertion. The original H1 loop then
continues on the same workspace; there is no second search or result retry.

The anchor minimizes `(already-computed query distance, H1 ID)` among original
initial BKT candidates actually scored. If none were scored, use the first
reachable BKT candidate ID observed before predicate rejection. Invalid leaves
are not scored merely to discover ownership. Only that anchor's eight direct
H2 memberships and adjacent H3 ownership are available to this call.
`entry_ids`, `entry_scored` and `startup_audits` make this choice auditable.

Startup degree counts eligible entries actually in the initial native queue.
Regular expansion degree retains the previous definition: distinct **fresh**
ordinary H1 neighbors qualifying through native posting OR exact own-point
admission, whether or not the spatial bound queues them. Neither counts all
eligible adjacency or final results. Separate raw/qualified/queued counters
prevent conflating these definitions. This startup distinction is intentional.

If initial seeding already consumed native MaxCheck, startup is blocked and
recorded; it receives no extra search budget. A selected H2 row is nevertheless
completed if it crosses that native boundary, with overshoot measured, before
native continuation/termination. All-signature-rejected reachable scopes return
empty; ordinary native tree/search termination proceeds without global rescue.
The predicate-first graph-only C records entry evidence but never supplies.

Native MaxCheck counts checked terminal leaves/ordinary graph-neighbor
evaluations, including predicate-rejected terminal leaves and qualified supplied
H1 neighbors. It is not an actual-distance ceiling: internal routing and parent
representative distances are reported separately. Original native spatial
termination and finite priority-queue capacity are unchanged. `Queued` counts
insertion calls that pass the native spatial bound, not every qualified/scored
node; the existing bounded heap can drop/replace an insertion when full.
`StartupAfter` records the actual queue size, independently of this counter.

## Whole row, not atomic descendant subtree

Signature-valid direct H2 owners are query-ranked. Each selected H2 row's full
H1 range is examined and only then marked completed. Check degree/native
termination **between** complete rows. If direct scope is exhausted and still
deficient, query-rank adjacent H3 owners.

A selected H3 row is completely enumerated: check each H2 member's signature,
score valid unvisited H2 representatives, and cache their query-ranked choices.
Mark H3 completed only after this enumeration. Then select cached H2 rows in
query-distance order, completing each selected H2 row and checking whether to
resume H1 between rows. H3 completion does not mean descendant completion.
Remaining choices persist per query and are reusable on a later request whose
ownership scope includes that H3 row; completed H3 CSR is never reread.
These are cached adjacency lists, not an autonomous upper frontier.
`row_audits`, `discoveries`, `supplyUpperMembers`, `supplyLowerMembers`, and
`supplyDiscoveryReuse` distinguish upper reads from descendant H1 work.

## Four-rule compliance and retained caveats

| Rule | Before (degree16 capped experiment) | This version / evidence |
|---|---|---|
| No added distance/member/row/batch limits | Not followed | No added limits; native MaxCheck only. `Supplier::Supply`, removed-budget INI rejection; fixture scans beyond3200 calls/2048 members. |
| Conservative signature before row scoring/scanning | Not followed | Original frozen query-signature/support logic, cached in `RowAllowed` before `Rank`/discovery. Invalid-row zero-score/scan and DNF/numeric/domain fixtures. |
| Eligibility before candidate distance | Not followed | Native posting OR exact own eligibility, read-only qualification; `Score` rejects invalid ordinary/child calls. Native internal routing centers and signature-valid parent representatives remain disclosed structural scoring roles. |
| Complete selected row, visited once; degree16 only trigger | Not followed | Complete H2 rows and complete H3 adjacency enumeration, separate child selection. Late nearest member beyond16, no-rescan, H3 cached-choice/reuse fixtures. |

Full-row v2 already followed these four rules except its newly identified
row-versus-subtree over-expansion; this version preserves those corrections and
adds startup handling. Ordinary invalid terminal BKT leaves are counted checked
without scoring/enqueuing; internal centers still require routing distances.
Every scored canonical H1, including parent aliases, passes shared native
own/posting admission. All real distances, repeats and cache hits are measured.

Signatures retain the original persisted domains. When a categorical query
anchor is outside that domain, or a DNF branch lacks a categorical anchor
(including numeric-only queries), the original conservative fallback disables
signature rejection. This is not permission to prune using an incomplete
signature or to replace native H/O/own eligibility with head attributes alone.

Original H1/H3 are unchanged baselines. C is **not original H1**: it shares
predicate-first admission and instrumentation. Historical H3 retains signature
compaction, predicate-guided top search and saved-frontier16-to24 widening, so
comparisons are not equal-recall causal claims. Its top graph remains loaded as
vector backing in C/S; this prototype does not claim its memory was removed.

## Reproduction and evidence

Native INIs are the sole search authority. Fixed four modes (`h1`, `h3`,
`control`, `supplier`), six original scenarios, first1000 queries, warmup1000,
target24/page15, one query thread, NUMA CPU/memory2, two rotating plain/profile
repetitions. Captures are untimed; ordinary runs disable path logs/phase clocks.
Counters are read-only but ordinary C/S still include shared instrumentation
overhead. No cap grid, dataset mutation/download, production edit or commits.

New authenticated toolchain:
`datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_startup_20260916/`.
Separate output:
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/h1_startup_20260916/`.
`SupplierTests.cpp` uses the generated native adapter, not a duplicate header.
`supplier-tests-pass.log` records completed focused fixtures; failed development
logs remain in the new toolchain. `final_source_manifest.json` authenticates the
generated native sources. Run from this directory:

```sh
python3 run.py --config experiment.ini
python3 verify.py --config experiment.ini
python3 report.py --results ../../../../../datasets/sift1m_zipf200_sparse193_numeric/comparisons/h1_startup_20260916
```

The completed report below is generated only from persisted summary and raw
native result lines. Numerical ratios are descriptive unless recalls match.

## Completed paired experiment

All144 native processes certified; 8996 protected files unchanged. All24 summary rows cross-checked against96 ordinary/profile final native result lines. Frozen H1/H3 selected-head/native-work parity passed; frozen final IDs were unavailable.

Original H1/H3 and matched C also retain exact final IDs and work versus completed v2. For this matrix, the conservative score/cache insertion-event bound remains below the original native heap capacity61440 in every C/S query; queued insertion calls therefore cannot have been discarded by a full heap.

### Recall@10 (%) / ordinary full latency (ms)

| Scenario | Original H1 | Historical H3 | Matched predicate-first C | Startup whole-row S |
|---|---:|---:|---:|---:|
| unfilter | 90.73 / 0.959699 | 88.60 / 0.783497 | 90.74 / 1.674365 | 90.81 / 1.849575 |
| broad_tag | 72.25 / 0.698352 | 87.67 / 0.802736 | 90.55 / 1.394980 | 91.78 / 2.781055 |
| medium_tag | 19.77 / 0.483963 | 86.28 / 0.725045 | 77.63 / 0.952488 | 96.31 / 14.265150 |
| extreme_tag | 0.28 / 0.374618 | 90.07 / 0.440910 | 24.35 / 0.614466 | 99.86 / 2.970095 |
| numeric | 30.44 / 0.763992 | 42.18 / 0.709207 | 68.53 / 1.804165 | 70.50 / 3.344650 |
| mixed_dnf | 1.04 / 0.390771 | 76.10 / 0.509332 | 41.99 / 0.787300 | 91.61 / 9.993830 |

**The joint cost/quality goal is not met.** Unfilter S/H1 latency is 1.927x. Extreme S underfills 0/1000 queries. Quality and work are reported independently; ratios below are descriptive, not equal-recall costs.


### Underfilled queries (%) / mean returned count

| Scenario | H1 | H3 | C | S |
|---|---:|---:|---:|---:|
| unfilter | 0.0 / 10.000 | 0.0 / 10.000 | 0.0 / 10.000 | 0.0 / 10.000 |
| broad_tag | 0.0 / 10.000 | 0.0 / 10.000 | 0.0 / 10.000 | 0.0 / 10.000 |
| medium_tag | 45.9 / 5.583 | 0.0 / 10.000 | 0.0 / 10.000 | 0.0 / 10.000 |
| extreme_tag | 99.7 / 0.066 | 0.6 / 9.982 | 62.1 / 5.739 | 0.0 / 10.000 |
| numeric | 54.1 / 8.139 | 8.3 / 9.794 | 0.0 / 10.000 | 0.0 / 10.000 |
| mixed_dnf | 99.7 / 0.264 | 0.0 / 10.000 | 7.4 / 9.737 | 0.0 / 10.000 |

### Native postings / pages / SSD distance calls

| Scenario | H1 | H3 | C | S |
|---|---:|---:|---:|---:|
| unfilter | 23.828 / 143.651 / 918.673 | 23.809 / 143.101 / 913.684 | 23.843 / 143.757 / 919.340 | 23.843 / 143.708 / 919.008 |
| broad_tag | 7.333 / 40.823 / 153.371 | 24.000 / 133.697 / 469.746 | 24.000 / 133.891 / 470.432 | 24.000 / 133.825 / 471.368 |
| medium_tag | 0.814 / 4.212 / 14.284 | 23.912 / 119.365 / 333.056 | 24.000 / 120.262 / 333.924 | 24.000 / 122.360 / 339.470 |
| extreme_tag | 0.011 / 0.042 / 0.069 | 8.613 / 35.044 / 49.855 | 1.088 / 4.397 / 7.041 | 24.000 / 94.381 / 97.965 |
| numeric | 9.019 / 60.189 / 9.072 | 8.993 / 59.929 / 9.058 | 23.983 / 157.318 / 22.285 | 23.983 / 156.607 / 22.243 |
| mixed_dnf | 0.066 / 0.261 / 0.257 | 14.162 / 53.374 / 45.810 | 5.849 / 22.043 / 20.015 | 24.000 / 92.566 / 74.457 |

### Actual head / SSD / total distance calls

| Scenario | H1 | H3 | C | S |
|---|---:|---:|---:|---:|
| unfilter | 2231.960 / 918.673 / 3150.633 | 2041.349 / 913.684 / 2955.033 | 2226.294 / 919.340 / 3145.634 | 2330.164 / 919.008 / 3249.172 |
| broad_tag | 2231.960 / 153.371 / 2385.331 | 1507.451 / 469.746 / 1977.197 | 1021.971 / 470.432 / 1492.403 | 2523.410 / 471.368 / 2994.778 |
| medium_tag | 2231.960 / 14.284 / 2246.244 | 1185.487 / 333.056 / 1518.543 | 451.467 / 333.924 / 785.391 | 7139.203 / 339.470 / 7478.673 |
| extreme_tag | 2231.960 / 0.069 / 2232.029 | 646.758 / 49.855 / 696.613 | 697.651 / 7.041 / 704.692 | 1313.955 / 97.965 / 1411.920 |
| numeric | 2231.960 / 9.072 / 2241.032 | 2041.349 / 9.058 / 2050.407 | 1195.722 / 22.285 / 1218.007 | 2463.954 / 22.243 / 2486.197 |
| mixed_dnf | 2231.960 / 0.257 / 2232.217 | 713.484 / 45.810 / 759.294 | 702.980 / 20.015 / 722.995 | 3660.289 / 74.457 / 3734.746 |

### Read-only distance ledger (per query)

| Scenario/case | Graph | Routing | Parent | Child | Cache | Repeats |
|---|---:|---:|---:|---:|---:|---:|
| unfilter/control | 2066.475 | 159.819 | 0.000 | 0.000 | 0.000 | 104.208 |
| unfilter/supplier | 935.487 | 153.755 | 70.089 | 1170.833 | 250.230 | 39.433 |
| broad_tag/control | 842.783 | 179.188 | 0.000 | 0.000 | 0.000 | 61.523 |
| broad_tag/supplier | 320.782 | 157.658 | 377.477 | 1667.493 | 566.138 | 58.332 |
| medium_tag/control | 71.872 | 379.595 | 0.000 | 0.000 | 0.000 | 5.479 |
| medium_tag/supplier | 141.360 | 1059.293 | 4141.894 | 1796.656 | 4252.126 | 297.738 |
| extreme_tag/control | 0.849 | 696.802 | 0.000 | 0.000 | 0.000 | 0.053 |
| extreme_tag/supplier | 0.173 | 225.450 | 1061.813 | 26.519 | 59.189 | 2.962 |
| numeric/control | 1027.273 | 168.449 | 0.000 | 0.000 | 0.000 | 63.895 |
| numeric/supplier | 390.506 | 152.469 | 321.896 | 1599.083 | 504.855 | 55.936 |
| mixed_dnf/control | 4.307 | 698.673 | 0.000 | 0.000 | 0.000 | 0.082 |
| mixed_dnf/supplier | 4.425 | 727.002 | 2760.779 | 168.083 | 422.628 | 41.714 |

### Pruning and complete-row work (per query)

| Scenario/case | SignatureChecks | SignatureRejects | PredicateChecks | PredicateRejects | RejectedLeaves | H2Completed | H3Completed | PostingSkips | Members |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| unfilter/control | 0.000 | 0.000 | 2122.086 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| unfilter/supplier | 160.173 | 0.000 | 2290.731 | 0.000 | 0.000 | 39.525 | 0.178 | 96.830 | 2271.939 |
| broad_tag/control | 0.000 | 0.000 | 2654.153 | 1814.409 | 135.092 | 0.000 | 0.000 | 0.000 | 0.000 |
| broad_tag/supplier | 497.845 | 0.027 | 6525.233 | 4476.058 | 66.671 | 190.308 | 2.482 | 450.704 | 10961.540 |
| medium_tag/control | 0.000 | 0.000 | 2309.740 | 2227.721 | 813.772 | 0.000 | 0.000 | 0.000 | 0.000 |
| medium_tag/supplier | 5894.382 | 1020.630 | 52234.646 | 50369.445 | 151.961 | 3833.555 | 278.511 | 12082.415 | 220535.721 |
| extreme_tag/control | 0.000 | 0.000 | 2755.789 | 2754.701 | 2047.463 | 0.000 | 0.000 | 0.000 | 0.000 |
| extreme_tag/supplier | 13387.117 | 12266.779 | 11660.939 | 11633.935 | 334.122 | 210.746 | 909.592 | 2026.492 | 61734.318 |
| numeric/control | 0.000 | 0.000 | 2651.913 | 1617.260 | 117.216 | 0.000 | 0.000 | 0.000 | 0.000 |
| numeric/supplier | 442.578 | 0.000 | 5644.059 | 3571.166 | 62.009 | 157.567 | 1.967 | 377.564 | 9102.285 |
| mixed_dnf/control | 0.000 | 0.000 | 2801.048 | 2795.199 | 2045.452 | 0.000 | 0.000 | 0.000 | 0.000 |
| mixed_dnf/supplier | 15012.628 | 11990.422 | 47996.313 | 47823.794 | 968.743 | 1232.653 | 1775.134 | 13144.726 | 161277.911 |

### Native continuation and degree (per query)

| Scenario/case | Calls | Qualified | Neighbors | Queued | NativeOvershoot | ContinuationPops | Fills | NativeStops | ScopeStops |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| unfilter/control | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| unfilter/supplier | 35.937 | 1249.225 | 1249.225 | 143.906 | 12.957 | 85.273 | 35.911 | 0.026 | 0.000 |
| broad_tag/control | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| broad_tag/supplier | 88.946 | 1728.170 | 5479.459 | 237.786 | 5.072 | 98.812 | 88.589 | 0.357 | 0.000 |
| medium_tag/control | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| medium_tag/supplier | 119.344 | 1847.224 | 51600.141 | 236.295 | 0.185 | 127.914 | 111.017 | 0.708 | 7.619 |
| extreme_tag/control | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| extreme_tag/supplier | 28.004 | 26.978 | 10242.216 | 26.978 | 0.000 | 27.004 | 0.040 | 0.000 | 27.964 |
| numeric/control | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| numeric/supplier | 83.119 | 1661.593 | 4596.166 | 228.437 | 5.602 | 95.656 | 82.787 | 0.332 | 0.000 |
| mixed_dnf/control | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| mixed_dnf/supplier | 129.079 | 172.372 | 45444.436 | 143.147 | 0.000 | 129.079 | 6.152 | 0.000 | 122.927 |

### Upper-row enumeration versus descendant H1 reads (per query)

| Scenario/case | UpperMembers | LowerMembers | Discovered | DiscoveryReuse |
|---|---:|---:|---:|---:|
| unfilter/control | 0.000 | 0.000 | 0.000 | 0.000 |
| unfilter/supplier | 9.407 | 2262.532 | 5.407 | 0.858 |
| broad_tag/control | 0.000 | 0.000 | 0.000 | 0.000 |
| broad_tag/supplier | 136.467 | 10825.073 | 67.536 | 8.366 |
| medium_tag/control | 0.000 | 0.000 | 0.000 | 0.000 |
| medium_tag/supplier | 15318.136 | 205217.585 | 3768.626 | 88.294 |
| extreme_tag/control | 0.000 | 0.000 | 0.000 | 0.000 |
| extreme_tag/supplier | 49355.636 | 12378.682 | 161.819 | 0.003 |
| numeric/control | 0.000 | 0.000 | 0.000 | 0.000 |
| numeric/supplier | 108.141 | 8994.144 | 51.387 | 6.376 |
| mixed_dnf/control | 0.000 | 0.000 | 0.000 | 0.000 |
| mixed_dnf/supplier | 91567.845 | 69710.066 | 1201.811 | 2.643 |

### Initial native frontier (per query)

| Scenario/case | EntryCandidates | EntryCalls | EntryBefore | StartupCalls | StartupBefore | StartupAfter | StartupQualified | StartupBlocked |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| unfilter/control | 323.354 | 323.354 | 72.562 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| unfilter/supplier | 323.354 | 323.354 | 72.562 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| broad_tag/control | 186.516 | 137.386 | 7.877 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| broad_tag/supplier | 186.516 | 137.386 | 7.877 | 0.977 | 7.472 | 25.968 | 18.496 | 0.000 |
| medium_tag/control | 166.912 | 110.966 | 0.664 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| medium_tag/supplier | 166.912 | 110.966 | 0.664 | 1.000 | 0.664 | 16.919 | 16.255 | 0.000 |
| extreme_tag/control | 165.098 | 108.537 | 0.005 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| extreme_tag/supplier | 165.098 | 108.537 | 0.005 | 1.000 | 0.005 | 9.429 | 9.424 | 0.000 |
| numeric/control | 190.314 | 142.361 | 9.947 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| numeric/supplier | 190.314 | 142.361 | 9.947 | 0.889 | 7.950 | 27.057 | 19.107 | 0.000 |
| mixed_dnf/control | 165.164 | 108.664 | 0.046 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| mixed_dnf/supplier | 165.164 | 108.664 | 0.046 | 1.000 | 0.046 | 16.020 | 15.974 | 0.000 |

### Started-helper effective degree and observed query counts

| Scenario | Before -> after | Triggered | H3 used | Native overshoot |
|---|---:|---:|---:|---:|
| unfilter | 7.392 -> 42.153 | 1000 | 127 | 699 |
| broad_tag | 2.925 -> 22.354 | 1000 | 606 | 797 |
| medium_tag | 0.102 -> 15.580 | 1000 | 1000 | 153 |
| extreme_tag | 0.001 -> 0.964 | 1000 | 1000 | 0 |
| numeric | 3.943 -> 23.933 | 1000 | 485 | 777 |
| mixed_dnf | 0.001 -> 1.336 | 1000 | 1000 | 0 |

### Startup deficiency and original H1 continuation

| Scenario | Startup calls | Empty before | Empty -> nonempty | Blocked by native boundary | Zero H1 pops |
|---|---:|---:|---:|---:|---:|
| unfilter | 0 | 0 | 0 | 0 | 0 |
| broad_tag | 977 | 2 | 2 | 0 | 0 |
| medium_tag | 1000 | 532 | 532 | 0 | 0 |
| extreme_tag | 1000 | 995 | 995 | 0 | 0 |
| numeric | 889 | 2 | 2 | 0 | 0 |
| mixed_dnf | 1000 | 960 | 960 | 0 | 0 |

### Sparse helper availability

The helper is available before the first H1 pop on initial frontier deficiency, and inside subsequent native H1 expansions, never as a final-result repair. Conditional subsets are diagnostics, **not** substitutes for overall recall.

| Scenario | Helper called | Queries | Recall (%) | Underfilled | Zero H1 pops |
|---|---|---:|---:|---:|---:|
| extreme_tag | False | 0 | - | 0 | 0 |
| extreme_tag | True | 1000 | 99.86 | 0 | 0 |
| mixed_dnf | False | 0 | - | 0 | 0 |
| mixed_dnf | True | 1000 | 91.61 | 0 | 0 |

No-helper ordered results, selected heads, evaluations and actual calls exactly match the predicate-first graph-only control. No result-triggered repair was added.


### Descriptive comparisons, not recall-matched costs

| Scenario | Reference | S/reference latency | S/C latency | S-C recall pp |
|---|---|---:|---:|---:|
| unfilter | h1 | 1.927 | 1.105 | +0.07 |
| broad_tag | h3 | 3.464 | 1.994 | +1.23 |
| medium_tag | h3 | 19.675 | 14.977 | +18.68 |
| extreme_tag | h3 | 6.736 | 4.834 | +75.51 |
| numeric | h3 | 4.716 | 1.854 | +1.97 |
| mixed_dnf | h3 | 19.621 | 12.694 | +49.62 |

No weighted workload mixture or post-result policy tuning. Original H3 retains its distinct predicate-guided top search and widening. Native routing centers remain explicit pre-predicate-distance exceptions, not hidden supplier candidates.
