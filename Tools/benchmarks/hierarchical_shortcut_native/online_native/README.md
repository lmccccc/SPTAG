# Online eight-owner navigation with native evaluated-candidate admission

This is a new policy, not the preserved fixed-overlay experiment. Native search
controls are preregistered in `experiment.ini` and eight search INIs: budgets
2000/3200, seed128, batch16, selected postings at most24, SSD pages15, two paired
repetitions on the same six original scenarios. No scenario chooses a different
algorithm or budget. Original H1 and H3 are separate, unchanged baselines.

## Exact one-pass policy

This first implementation deliberately uses a simpler explicit frontier, not a
claim to preserve every native BKT termination/detail:

1. Run native H1 BKT **once** as a bootstrap, stopping before its129th actual
   distance computation. Every callback, including repeated tree-center
   evaluations, executes the native distance function and consumes the shared
   budget. Return the BKT workspace normally; do not restart it. Its tree
   frontier is not resumed. Every distinct evaluated H1, including internal
   tree centers, enters a distance-priority H1 queue and native admission.
2. Pop the nearest queued H1 and score its previously unscored positive graph
   neighbors. Native negative tree sentinels are not graph edges; the manual
   phase does not expand collapsed-tree siblings. All new H1s enter the same
   queue. If **none of the fresh neighbors is strictly closer to the query than
   the popped item**, this local spatial step is stalled. This condition never
   consults filters, result count, recall or SSD state.
3. On a stall, reverse membership supplies the current H1's eight H2 owners.
   Score/rank all eight representatives by **query-to-parent squared L2**;
   retain all alternatives in a priority queue of parent rows. Ties use layer
   and parent ordinal. If the budget ends while ranking, stop the query without
   expanding a partially ranked list.
4. Alternate one H1 graph expansion and one nearest pending parent-row batch.
   A batch consumes at most16 original CSR member entries, retaining the cursor
   for the next visit. Children are scored lazily in existing CSR order, not
   exhaustively sorted in advance. An H1 child enters the shared graph frontier
   and admission; an H2 child schedules its own H1 row.
5. A batch with no fresh child closer than its parent representative triggers
   upward lookup for that **current H2 item**: its own eight H3 parents are
   ranked against the same query and retained. H3 is the top; its rows descend
   to H2, whose rows descend to H1. Already scheduled/finished rows and already
   requested upward lookups are not restarted. Nearest unfinished alternatives
   remain available after earlier rows finish.
6. Stop at the unified actual-distance cap, empty queues, or the fixed defensive
   CSR-entry ceiling `16 × distance cap`. Nothing retries or scans unvisited
   heads/postings to repair filtered underfill.

The graph-only `admit2000/admit3200` controls use exactly this seed, H1 frontier,
scoring/admission and stopping policy but never request parents or CSR rows.
They differ from original natural BKT and are **not relabeled original H1**.
Online-versus-admit isolates the hierarchy addition within the same admission
and graph policy. Original-versus-admit includes both admission and the disclosed
one-pass traversal changes; it is not a pure single-instruction ablation.

## Mapping, caching and accounting

Both original eight-replica CSRs are retained byte-for-byte. Reverse ownership
is prepared once, validated for exactly eight distinct direct parents, and
contains no query-specific ranking. H2/H3 ordinals index their native catalogs;
only H1 ordinals are translated to dataset VIDs by native SPANN.

The cache key is `(layer, ordinal)` and is invalidated with a new epoch for every
query. Native BKT bootstrap callbacks are **never discounted** by this cache:
even repeated evaluations are executed and charged. Subsequent graph/CSR/owner
lookups reuse already computed distances without invoking the distance function;
cache hits are explicitly reported. Representatives duplicated across layers
are not silently aliased and their separate computations are charged.

`graph + parent + child = total head distance calls` on every query; a separate
profile counter and an independent fixture callback ledger verify the charges.
CSR entries, batches, cache hits, H1/H2 upward requests, H2/H3 rows and distinct
evaluated H1s are reported separately. Spatial trace hashes include scored items
and row cursors. Counted/uncounted outputs and per-predicate spatial traces must
match. Ordinary timing does not compute the trace hash; the untimed capture
pass supplies its audited hash.
The ordinary implementation retains its lightweight distance-kind, cache and
cursor ledger (including the defensive work ceiling); the independent counter,
spatial hashing and phase clocks are disabled. These timings therefore include
the first implementation's ledger, admission and scheduler overhead, not a claim
of an optimized, bookkeeping-free kernel.

## Native admission and full-query path

Every distinct evaluated H1 is offered, regardless of how it was reached, to:

* The original H3 `admitHeadPoint(head, knownDistance)` callback, using its exact
  predicate, deletion check, native canonical VID mapping, deduper and own-point
  result heap. Even an empty-posting head may contribute its own point.
* The original `secondLevelHeadAdmission` predicate for posting eligibility,
  with nearest24 eligible heads retained in a separate result heap.

Both callbacks run **after scoring** and return no routing decision to the
spatial scheduler. The same native hierarchy workspace then continues through
the original H/O-region setup, posting validation, distance-ratio/page controls,
own-point merge and `ExtraStaticSearcher::SearchIndex`. There is no generic
disk-search substitute, enlarged posting budget or scan of all heads.

The benchmark captures the native pre-SSD own-point IDs and verifies their exact
predicates, final recall, returned count and underfill. All original H1/H3
baseline head IDs/distances and native work must first match the untouched
frozen executable. Small native full-path fixtures use the existing first eight
queries under every original predicate before the measured matrix.

## Isolation and execution

Only a new authenticated tree under
`datasets/sift1m_zipf200_sparse193_numeric/toolchains/online_owners_20260915/`
is patched. The complete frozen runtime is reused; no production source,
previous toolchain, fixed-overlay result, original graph/CSR/posting or binary
is overwritten. `integrate.py` builds on the preserved full-path generators.

```sh
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/full/reconstruct.py \
  --repo SPTAG \
  --snapshot datasets/sift1m_zipf200_sparse193_numeric/build_runs/20260909T141212Z_lightweight_rescue \
  --output datasets/sift1m_zipf200_sparse193_numeric/toolchains/online_owners_20260915 \
  --authenticated-cache /home/baotonglu/.copilot/session-state/40fc7ebb-e617-4ec3-a17b-610068e95e5e/files
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/online_native/integrate.py \
  datasets/sift1m_zipf200_sparse193_numeric/toolchains/online_owners_20260915/source
cmake -S datasets/sift1m_zipf200_sparse193_numeric/toolchains/online_owners_20260915/source \
  -B datasets/sift1m_zipf200_sparse193_numeric/toolchains/online_owners_20260915/build \
  -DCMAKE_BUILD_TYPE=Release -DSPDK=OFF -DROCKSDB=OFF -DTBB=ON -DLIBRARYONLY=ON
cmake --build datasets/sift1m_zipf200_sparse193_numeric/toolchains/online_owners_20260915/build \
  --target spannaclbench --parallel 4
c++ -std=c++17 -O2 -I SPTAG/Tools/benchmarks/hierarchical_shortcut_native/online_native \
  SPTAG/Tools/benchmarks/hierarchical_shortcut_native/online_native/OnlineTests.cpp \
  -o datasets/sift1m_zipf200_sparse193_numeric/toolchains/online_owners_20260915/online-tests
datasets/sift1m_zipf200_sparse193_numeric/toolchains/online_owners_20260915/online-tests
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/online_native/run.py \
  --config SPTAG/Tools/benchmarks/hierarchical_shortcut_native/online_native/experiment.ini
```

Existing source/output directories are refused. Reproductions require explicit
new directory paths in the repository-owned INI. The first-run output is
`comparisons/online_owners_admission_20260915/`; its native snapshots and
per-query evidence distinguish the online policy from the earlier fixed overlay.

## Completed SIFT1M result

**Implemented and executed online, but the joint quality/cost goal is not met.**
The admission correction works and accounts for most filtered improvements.
At the same budget, online parents generally reduce recall relative to the
matched admission control and add latency. This is a result about this disclosed
one-pass policy, not proof that every online upward-navigation design fails.

There are **216 certified native processes**: 24 original H1/H3 parity controls,
48 small native full-path fixtures, and144 measured profile/off runs. Two paired
repetitions rotate scenario/case order and reverse profile/off order. A procfs
exit-sampling race interrupted the launcher after one native invocation finished;
that uncertified attempt is preserved and excluded. A certified retry has
**byte-identical query outputs**. Completed certified runs were reused, not
overwritten; no native binary, policy, input or INI changed during resumption.
Only the local launcher was made robust to an exited child's disappearing
procfs data, with an explicit exit0 check.

### Dataset recall and ordinary full latency

Cells are **Recall@10 (%) / mean milliseconds**, not navigation-only timing.
`G` is the matched graph-only evaluated-admission control; `O` is online owners.
Neither is relabeled the original H1 baseline.

| Scenario | Original H1 | Original H3 | G2000 | O2000 | G3200 | O3200 |
|---|---:|---:|---:|---:|---:|---:|
| unfilter | 90.73 / 0.968128 | 88.60 / 0.800639 | 90.73 / 1.007680 | 90.69 / 1.081505 | 90.84 / 1.291720 | 90.86 / 1.433405 |
| broad_tag | 72.25 / 0.704837 | 87.67 / 0.818221 | 91.47 / 1.175740 | 91.44 / 1.247795 | 91.83 / 1.541455 | 91.69 / 1.624480 |
| medium_tag | 19.77 / 0.487916 | 86.28 / 0.724859 | 94.03 / 1.223060 | 92.63 / 1.299505 | 95.56 / 1.575090 | 94.97 / 1.726730 |
| extreme_tag | 0.28 / 0.377276 | 90.07 / 0.447106 | 18.81 / 0.779238 | 14.75 / 0.804398 | 27.98 / 1.233280 | 23.31 / 1.300845 |
| numeric | 30.44 / 0.763050 | 42.18 / 0.713383 | 49.08 / 1.005235 | 47.87 / 1.077925 | 50.14 / 1.339130 | 49.52 / 1.491245 |
| mixed_dnf | 1.04 / 0.391754 | 76.10 / 0.511532 | 38.28 / 0.944280 | 31.54 / 0.968807 | 51.90 / 1.412915 | 44.90 / 1.480195 |

Unfiltered O2000 nearly preserves original H1 recall but costs **1.117×** its
latency; O3200 costs **1.481×** for a small recall improvement. Against H3,
O2000/O3200 latency ratios are broad **1.525/1.985×**, medium **1.793/2.382×**,
extreme **1.799/2.909×**, numeric **1.511/2.090×**, mixed **1.894/2.894×**.
Broad/medium/numeric quality is higher than H3 at these budgets, so these are
descriptive ratios, not recall-matched cost estimates. Extreme/mixed remain
both substantially less accurate and slower. No tolerance or weighted workload
mix was invented; two repetitions do not support a strong small-effect claim.

### Underfill and native admission proof

Percentage of queries returning fewer than10 results:

| Scenario | H1 | H3 | G2000 | O2000 | G3200 | O3200 |
|---|---:|---:|---:|---:|---:|---:|
| unfilter | 0 | 0 | 0 | 0 | 0 | 0 |
| broad_tag | 0 | 0 | 0 | 0 | 0 | 0 |
| medium_tag | 45.9 | 0 | 0 | 0 | 0 | 0 |
| extreme_tag | 99.7 | 0.6 | 75.8 | 82.8 | 60.0 | 68.9 |
| numeric | 54.1 | 8.3 | 0 | 0 | 0 | 0 |
| mixed_dnf | 99.7 | 0 | 23.1 | 38.8 | 5.5 | 13.2 |

Returned-count means and empty-query counts are preserved in `summary.json`.
For example, extreme O2000/O3200 return3.255/5.016 results/query; original H3
returns9.982. Mixed O2000/O3200 return8.158/9.472 versus H3's10.

The numeric admission audit demonstrates the previously missing behavior:
G2000 returns own-head points **outside the selected posting-head set** on
998/1,000 queries. Those pre-SSD own-heap points supply **1,859 true neighbors**
across the cohort; O2000 supplies1,747. Native H3 supplies1,218. These points
were explicitly captured in the native own heap before disk search, matched
through the canonical H1→VID map, and independently checked against the exact
predicate and truth. No all-head scan or additional posting budget supplied them.

Thus the large changes from original H1 (for example medium19.77→94.03% and
numeric30.44→49.08% for G2000) must **not** be attributed to online parents.
The corrected control shares admission and the one-pass graph policy with O.
At cap2000, O versus G changes recall by unfilter−0.04pp, broad−0.03pp,
medium−1.40pp, extreme−4.06pp, numeric−1.21pp and mixed−6.74pp.

### Unified work, real upper traversal and SSD budget

The following navigation means are identical across every predicate, verified
by per-query spatial trace and work equality:

| Policy | Graph distances | Parent distances | Child distances | Total | Distinct H1 evaluated | CSR entries | H2 rows | H3 rows |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| G2000 | 2000 | 0 | 0 | 2000 | 2000 | 0 | 0 | 0 |
| O2000 | 1121.780 | 301.804 | 576.416 | 2000 | 1594.845 | 1165.606 | 17.576 | 3.425 |
| G3200 | 3200 | 0 | 0 | 3200 | 3200 | 0 | 0 | 0 |
| O3200 | 1753.933 | 473.223 | 972.844 | 3200 | 2547.865 | 2238.770 | 33.255 | 6.603 |

O2000 performs76.559 H1 upward requests and17.430 H2 upward requests/query;
O3200 performs150.722/33.141. Its parents therefore really are selected online
through **both** levels. It spends roughly20% of its actual-distance allowance
on upper items, leaving fewer distinct H1 candidates than G at the same cap.
The fixture also verifies that all eight alternatives can be reached, rather
than silently retaining only the closest parent.

Native SSD work below is **postings / pages / disk distance evaluations**:

| Scenario | H3 reference | G2000 | O2000 | G3200 | O3200 |
|---|---:|---:|---:|---:|---:|
| unfilter | 23.809 / 143.101 / 913.684 | 23.827 / 143.694 / 918.966 | 23.828 / 143.668 / 918.708 | 23.829 / 143.626 / 918.628 | 23.828 / 143.631 / 918.689 |
| broad_tag | 24 / 133.697 / 469.746 | 24 / 133.874 / 471.753 | 24 / 133.822 / 471.315 | 24 / 133.871 / 471.668 | 24 / 133.875 / 471.542 |
| medium_tag | 23.912 / 119.365 / 333.056 | 24 / 122.729 / 339.653 | 24 / 121.921 / 337.045 | 24 / 122.604 / 338.965 | 24 / 122.188 / 337.417 |
| extreme_tag | 8.613 / 35.044 / 49.855 | 0.693 / 2.867 / 5.009 | 0.539 / 2.204 / 3.922 | 1.103 / 4.549 / 7.869 | 0.891 / 3.651 / 6.339 |
| numeric | 8.993 / 59.929 / 9.058 | 9.031 / 60.269 / 9.083 | 9.009 / 60.085 / 9.058 | 9.011 / 60.095 / 9.057 | 9.014 / 60.118 / 9.061 |
| mixed_dnf | 14.162 / 53.374 / 45.810 | 4.250 / 16.534 / 14.907 | 3.300 / 12.751 / 11.651 | 6.605 / 25.491 / 22.692 | 5.317 / 20.415 / 18.342 |

Full actual work is the navigation total plus native SSD distance evaluations;
all totals, original H1/H3 graph/CSR work, adjacency/cache hits and per-run
phases/QPS are in JSON. The old `PhaseTime.twoLayer/h2*` fields describe only the
original H3 path; online upper work is in the explicit `online*` counters, while
its complete navigation time remains inside the native navigation interval.
Retrieval-minus-scan is not physical SSD delay.

### Interpretation caveat: original H3 is not a geometry-only control

An interpretation-only audit of the completed runs (no rerun or policy change)
confirmed that `HierarchyGraphSignaturePruning=false` disables **top-graph
traversal filtering**, not all predicate-guided navigation. In authenticated
`SecondLevelHierarchy.h:636–651`, anchored queries still use
`SearchIndexWithResultFilter`; nonmatching graph bridges remain traversable,
but only signature-compatible results seed descent. At `:748–818`, H2 signature
checks and H1 posting-support admission compact CSR children **before distance
scoring**. Only surviving H2 children enter the distance frontier and become
parents (`:859–885`). Fewer than24 retained eligible H1s triggers saved-frontier
widening from16 to24 (`:891–894`), without restarting the top graph. This is
eligible-head underfill, not final Recall@10/result-count repair.

Original H3 means per query, using the same two completed profile repetitions:

| Scenario | h2Graph ms | Actual graph distances | h2Upper checked | h2Unique distances | h2Assign CSR entries | h2Iter | Selected H1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| unfilter | 0.083290 | 607.998 | 442.731 | 1433.351 | 1852.928 | 1.000 | 24 |
| broad_tag | 0.096942 | 607.998 | 442.731 | 899.453 | 1852.981 | 1.000 | 24 |
| medium_tag | 0.093098 | 607.998 | 442.731 | 577.489 | 2005.356 | 1.143 | 23.912 |
| extreme_tag | 0.086130 | 611.737 | 446.262 | 35.021 | 2880.968 | 2.000 | 8.613 |
| numeric | 0.082928 | 607.998 | 442.731 | 1433.351 | 1852.928 | 1.000 | 24 |
| mixed_dnf | 0.083319 | 607.998 | 442.731 | 105.486 | 2902.353 | 2.000 | 14.162 |

`h2Graph` is time; `h2Upper` omits some actual tree-pivot distance calls.
`h2Unique` combines lower-layer and own-point distance computations;
`h2Assign` counts member entries across layers, including replica duplicates,
not distinct H1s. Per-layer eligible/frontier/retained and signature-reject
counters were not logged (`LogAdaptiveNprobe` gates `HierarchyWork`); their
absence is not zero work. Numeric has no categorical anchor: all1000 captured
selected heads/distances and navigation-work counters equal unfilter.

For extreme, H3 uses **646.758**, not2000, actual head distances. It reads8.613
postings/35.044 pages versus G2000's0.693/2.867, and returns9.982 versus4.042
results, yielding90.07% versus18.81% recall. The source demonstrates
predicate-guided candidate discovery/frontier retention and pre-score filtering,
not merely different final admission. G2000 spends **zero** distances upstairs:
the huge H3/G gap cannot be explained by O's approximately20% upper-layer spend.
O versus G remains the matched online-policy comparison; H3 versus either also
compares different discovery contracts. These measurements do not isolate the
individual causal contribution of signatures, widening and geometry.

Detailed source hashes, exact native INI controls, raw-log references and
counter definitions are in the result directory's `h3_comparison_caveat.json`,
referenced by `acceptance.json`. The original `report.md` and all raw evidence
remain unchanged; this is an additive interpretation correction.

### Verification and limitations

The native parent-order/cursor/cache/budget fixtures passed, including query
reversal selecting a different nearest parent at **each** layer, forced repeated
BKT callback charging, all eight alternatives, exact per-entry cursor coverage,
and all-evaluated-H1 admission independent of predicates. The native full-path
fixtures and all36 scenario/case combinations pass exact-filter, own-heap,
ordered-result, count-mode and repeated-work checks. Original H1/H3 match frozen
head IDs/distances and native recall/work; unavailable frozen final IDs are not
invented. **1,749 protected files remain unchanged**, including all previous
results, binaries and index/query inputs.

The original H3 baseline retains its historical signature/result-admission
behavior. The new online/G policies always score before admission and are
predicate-invariant; they do not secretly switch to that H3 path. This
implementation's explicit queues, lack of the full BKT continuation/prefetch
machinery, admission work and ledger also add cost. No universal connectivity,
all-descendant coverage, high-recall numeric guarantee or billion-scale claim
follows from this bounded experiment.

Validation and the operational resume command:

```sh
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/online_native/run.py \
  --config SPTAG/Tools/benchmarks/hierarchical_shortcut_native/online_native/experiment.ini --resume
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/online_native/verify.py \
  --config SPTAG/Tools/benchmarks/hierarchical_shortcut_native/online_native/experiment.ini
```

The preserved run contains `native_fixtures.json`, `independent_validation.json`,
`acceptance.json`, native commands/exit-status/IO observations, the excluded
sampler-race attempt, certified retry, pre-resume records and source snapshots.
There are no missing source, data or native integration prerequisites. The
remaining issue is policy quality/cost, especially extreme and mixed filters;
no extra policy search was performed after observing these outcomes.
