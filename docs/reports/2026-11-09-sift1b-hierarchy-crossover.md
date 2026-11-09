# SIFT1B: Hierarchy Cost, R90 Tuning, and the Unresolved Trigger Crossover

Date: 2026-11-09. Measurements: 2026-11-08.

**Status: experimental findings, not recommended default settings.**
The bounded comparison establishes modest hierarchy gains for two predicates
near 17% record selectivity. It does **not** establish a universal SIFT1B
handoff threshold. The approximately 18% estimate below is conditional
end-to-end interpolation, not a measured crossover or a value to put into
`PostingMatchRatePercent`.

## 1. Quantities that must not be conflated

| Quantity | Meaning in this experiment |
| --- | --- |
| Construction admission | V5 `HierarchyLocalTarget=2048`, `HierarchyLocalWindow=4096`: a 50% base-window capacity ratio, not a global query-selectivity cutoff. Effective windows grow at higher transitions. |
| Physical sampling | Source `Ratio=0.12`, with source-tier caps and support expansion; independent of the 50% admission ratio. |
| Record selectivity, `s` | Fraction of the 1B records satisfying the complete predicate. |
| Observed match rate, `r_w` | Predicate-compatible H1 support among fresh scored navigation candidates in a complete window; not final-record truth or representative-own-label frequency. |
| Online threshold | `PostingMatchRatePercent`: a complete window triggers when `matches * 100 < window * threshold`; equality does not trigger. |
| Posting size | Members in a selected hierarchy row. The current V5 build does not enforce a fixed row length: `HierarchyTargetPostingSize=0`. |
| Supplement budget | `PostingAdditionalMaxCheck`, spent on additional checked H1 leaves; not the number of postings, upper representatives, or SSD reads. |
| `nprobe` | Head-result capacity / SSD posting-probe setting, not the final result count of 100. |

`MaxCheck=1024` is a soft native graph stopping control. Supplementation uses
the ceiling `max(MaxCheck, completed graph checks) + additional budget`;
complete rows can overshoot it. This is not a hard cap on all hierarchy work.
Coverage and a valid label-compatible entry are required for early handoff.
There is no H1 graph resumption after posting consumption.

The SPANN BKT/KDT distinction is separate again:
`[SelectHead] SelectHeadType=BKT` uses a tree to elect heads from the candidate
data; the current selector also supports `Random`, not a BKT/KDT selector pair.
`[Base] IndexAlgoType=BKT/KDT` selects the ANN backend built over the elected
heads, including its query-entry tree and graph. The experiment used BKT in
both stages, but these are different trees. Neither is the H2-H5 posting
hierarchy; temporary upper assignment ANN indexes are not online upper graphs.

## 2. Build identity and storage observations

The new upper-only V5 hierarchy preserved the canonical H1 vectors, graph,
metadata and SSD postings, as well as the old M512 control. It used 96 build
threads and the unchanged source sampling ratio. H1 has 120,040,156 heads over
1B 128-dimensional UInt8 SIFT records. The attribute schema has one categorical
key and one numeric column; the categorical predicates below use native labels
from the Zipf200 plus rare-label workload.

| Component | Old M512, base ratio 12.5% | New M2048, base ratio 50% |
| --- | ---: | ---: |
| `HeadIndex/` serialized files | 49.749 GiB | 49.749 GiB, same files |
| Serialized upper artifact | 5.874 GiB | 8.172 GiB |
| Upper physical nodes | 15,129,357 | 16,035,216 |
| Upper label rows | 30,028,762 | 32,040,857 |
| Adjacent member references | 1,094,094,398 | 1,695,858,230 |

Physical nodes increased about 6%, while references increased about 55% and
the upper file grew about 39%. Shared physical nodes do not make admission
free: extra label rows and replicated references matter. These are storage
measurements, not a matched serving-RSS comparison.

The new build authenticated H2 coverage of all 37,771,281 H1 heads supporting
label 0. Coverage supplies a route; it is not an R90 or performance guarantee.
No measured old-versus-new speedup should be inferred from this storage table.

## 3. Measurement protocol

All comparisons below use the new M2048 index. Pure graph disables
`EnablePostingNavigation`; hierarchy enables it and requests early handoff
with `PostingMatchRatePercent=100`. **100% is an experimental forced-handoff
control, not a proposed production threshold.**

Shared search controls:

```text
MaxCheck=1024
PostingMatchWindow=1024
PostingNavigationWidth=8
PostingAnchorCount=0
SearchPostingPageLimit=0
ResultNum=100
NumberOfThreads=1
EnablePackedHeadLayout=true
UseSharedAIOPool=false
AIOContextCount=1
```

Width-16 controls and varied supplement budgets are identified separately.
One query thread ran within CPUs 72-95 with anonymous memory bound to NUMA
node 3; this was not whole-process confinement to a single CPU.

The primary quality target is **ID Recall@100 >= 0.90**. Exact-distance
tie-aware recall is retained separately. Dense-predicate truth was derived
from authenticated official exact top-1000 neighbors only where every query
had at least 100 matching neighbors in that prefix. Lower-selectivity truths
used authenticated exhaustive component truths. ANN results were not used as
ground truth.

Ordinary binaries supply performance measurements; instrumented work binaries
supply counters only. A case performs warmup, timed search, and replay/capture
on the same cohort. Thus 1000 queries and three repeats mean 9000 searches per
setting, not 3000. Reported query latency excludes index startup but includes
head search and SSD processing; it is **not isolated head-navigation time**.

## 4. What the broad sweep did and did not establish

The original sweep completed 333 validated 200-query captures over 21
predicates spanning approximately 1.7%-55% record selectivity. This includes
252 completed initial/extension cases and 81 recovered completed refinement
cases. The remaining broad sweep was stopped; its proposed formal runs were
not completed.

At fixed extra budget 2048, increasing `nprobe` through 1536 still left
18 of the 21 hierarchy predicates below R90. Large probe sweeps were therefore
not establishing a comparable R90 hierarchy cost curve.

A genuine higher-selectivity graph win exists in that old configuration:

| OR4 predicate | Route | `nprobe` | Extra budget | ID Recall@100 | End-to-end latency |
| --- | --- | ---: | ---: | ---: | ---: |
| 19.999846% | Graph | 352 | Not used | 90.675% | 6.013 ms |
| 19.999846% | Hierarchy | 960 | 2048 | 90.065% | 10.737 ms |

These are **200-query pilot results**, not repeated 1000-query confirmation.
The hierarchy used about 2.7 times the probe setting; its total time cannot be
attributed entirely to head navigation.

The gap is not a lack of dense-query samples. Most dense predicates lacked
R90-feasible hierarchy points, and the later budget calibration was not
repeated across the whole selectivity range. Different budgets can legitimately
be optimal for different predicates, but a poorly explored old setting is not
proof of the best achievable hierarchy cost at that selectivity.

## 5. Bounded work diagnosis

One index load covered 35 diagnostic settings on the same first 200 queries.
It excluded the expensive sparse-label pure-graph reference. At `nprobe=512`
and width 8:

| Predicate | Actual record selectivity | Recall, extra 2048 | Recall, extra 4096 | Recall, extra 8192 |
| --- | ---: | ---: | ---: | ---: |
| Label 0 | 17.012493% | 90.360% | 92.475% | 93.725% |
| OR4 | 17.003073% | 86.130% | 89.040% | 91.090% |
| OR16 | 16.999855% | 83.165% | 86.285% | 88.245% |
| OR16 | 30.000251% | 84.555% | 87.090% | 88.600% |

All 800 query-predicate evaluations in the extra-2048 baseline rows stopped at the supplement
checked-leaf budget. Raising width from 8 to 16 at extra 4096 changed recall
by at most 0.025 percentage points. Increasing the budget materially improved
quality; wider navigation alone did not resolve the observed limitation.
The two OR16 predicates still failed R90 at the tested extra-8192/probe-512
setting. Do not assign them an R90 latency using these captures.

The baseline selected-row mean sizes were about 55-62 H1 references, and
additional fresh H1 distance computations were about 2100 in all four cases.
Nevertheless, recall differed substantially. A stable per-row scale and fixed
visit budget do not imply stable work **at a fixed recall target**:

```text
terminal-member processing at R90 ~= rows needed for R90 * mean row length
```

The number of useful rows, duplicate visits, representative navigation,
entry quality and label composition can all change. If these too remain
stable, hierarchy cost may be approximately selectivity-insensitive, and the
crossover can arise from graph search becoming cheaper with higher matching
yield. That remains a conditional model, not an invariant of bounded postings.

All 35 native cases completed with exit 0. An offline collector initially
compared integer counter-column counts with column-name arrays. A separate
corrected collector recovered the captures without rerunning native search or
changing the original logs/failure record. Sixteen matched settings preserved
the earlier ordinary IDs, distances and SSD-work payloads exactly.

## 6. Formal, bounded R90 comparison

Nine settings were measured on 1000 queries, three ordinary repetitions each,
using one index load: 27 captures and 81,000 searches including warmup/replay.
The following are the fastest measured R90-qualified candidates per route,
not proven global tuning optima. Latency is the median across repeats of
`1000 / QPS` in milliseconds.

| Predicate | Route | `nprobe` | Extra budget | ID Recall@100 | Median latency | Median QPS |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Label 0, 17.012493% | Graph | 288 | Not used | 90.075% | 4.579 ms | 218.375 |
| Label 0, 17.012493% | Hierarchy | 320 | 4096 | 90.746% | 4.031 ms | 248.108 |
| OR4, 17.003073% | Graph | 480 | Not used | 90.828% | 7.967 ms | 125.519 |
| OR4, 17.003073% | Hierarchy | 480 | 8192 | 90.905% | 7.410 ms | 134.946 |

Hierarchy QPS increased **13.616% for label 0** and **7.511% for OR4**;
latency decreased 11.984% and 6.986%, respectively. Both routes meet the same
R90 target, but their recalls are not identical.

For label 0, the old extra-2048/probe-512 hierarchy configuration cost
5.037 ms at 90.696% recall, slower than the selected graph point. Moving to
extra 4096/probe 320 cost 4.031 ms at 90.746% recall. Increasing extra again
to 8192 at probe 320 cost 5.041 ms while raising recall to 91.924%.
The best R90 tradeoff is not necessarily the smallest supplement budget or
the largest probe setting.

All repetitions preserved deterministic result/SSD-work payloads; 18 captures
also matched their earlier 200-query prefixes. These are finite-grid,
same-cohort empirical selections, not held-out policy validation. No OR16
formal R90 cost or universal crossover follows from them.

## 7. Conditional OR4 estimate: approximately 18%, not a trigger setting

For a cohort-aligned estimate, take the first 200 queries from each formal
OR4 capture and the same 200 queries from the old OR4-20% graph pilot.
For the formal captures, use the median across three repeats of their
first-200 per-query mean latencies. The 20% graph point has one pilot capture.
This differs from the full-cohort QPS aggregation above.

| Point | ID Recall@100 on the 200 queries | Per-query mean latency used |
| --- | ---: | ---: |
| Graph, `s0=0.17003073`, probe 480 | 90.600% | `G0=8.011655605 ms` |
| Hierarchy, `s0`, probe 480, extra 8192 | 90.660% | `H0=7.467998775 ms` |
| Graph, `s1=0.199998457`, probe 352 | 90.675% | `G1=6.011830675 ms` |

**Additional assumptions:** the entire R90 hierarchy route, including SSD
processing, stays at `H0` between the two selectivities; graph cost is locally
linear. Under those assumptions:

```text
s* = s0 + (s1 - s0) * (G0 - H0) / (G0 - G1)
   = 0.1781775229
   ~= 17.82% record selectivity
```

This is a candidate for a narrow experiment, not a confidence interval or a
measured root. The OR4 labels also differ: `[1,2,6,42]` at 17% versus
`[0,5,111,200]` at 20%. Matching the query cohort does not hold label geometry,
cache history, warmup count or the number of repetitions fixed.
The new-budget hierarchy cost at 18%-20% remains unmeasured.
Stable hierarchy posting length alone does not justify constant end-to-end
cost, especially when the R90 probe setting changes.

The diagnosed first-window **mean** local match rates were 31.453% for label 0
and 31.948% for OR4 near 17% record selectivity. These are workload observations,
not validated decision boundaries. Do not use global `s*` as
`PostingMatchRatePercent=17.82`, or call approximately 32% a calibrated trigger.

An online decision must compare the remaining costs of continuing H1 and
switching to the hierarchy from the same decision state, subject to the same
quality target and coverage requirements. The already-paid graph prefix is
sunk cost; differences in subsequent SSD work must still be accounted for.
Separately tuned end-to-end routes do not by themselves validate this policy.

## 8. Lessons and remaining gap

1. Establish R90 feasibility before large probe sweeps. Work/stop counters
   distinguish exhausted leaf budget from convergence and width pruning.
2. Keep ordinary latency separate from diagnostic latency. Stable node work
   is not evidence of stable fixed-recall cost, nor does node count alone
   determine memory use.
3. Keep construction admission, record selectivity and online observed-match
   thresholds separate. Higher admission supplies coverage, not automatic
   speedup; a previous 1M crossover is not a calibrated 1B trigger.
4. Account for load, warmup and replay costs when designing the experiment.
   Index startup was about 30 minutes per native process. The sparse
   1.7% graph reference was expensive and unnecessary for the dense boundary.
5. Reuse the broad pilots to choose a few candidates. The next missing
   evidence is R90-qualified hierarchy cost around the OR4 18%-20% candidate
   region, extending toward 25% only as needed, with local match rates
   collected separately. Diagnose OR16 feasibility separately rather than
   hiding its quality shortfall inside a scalar threshold fit.

This report does not change native defaults, reconstruction metadata or
runtime policy. It does not authorize or start a new benchmark or rebuild.

## 9. Evidence locations and identities

Raw inputs, indexes, binaries and captures remain external to Git. Paths below
are relative to the experiment's SIFT1B dataset root (`datasets/sift1b` in the
measurement workspace); they are provenance locators, not bundled downloads.
Run manifests identify exact binaries and modified core sources. Checking out
the documentation revision alone does not reproduce the measured executable.

| Artifact | Relative location |
| --- | --- |
| New upper build, configuration and audit | `build_runs/local_label_hierarchy_v5_target2048_fullcpu_20261106/` (`native-rebuild.ini`, `build-completion.json`, `hierarchy-audit.json`) |
| Old upper control | `build_runs/local_label_hierarchy_v5_target512_bounded_v2_fullcpu_20261008/` |
| Original exact inputs and frozen preparation | `comparisons/m2048_r90_crossover_20261108/` |
| Stopped broad sweep and original captures | `comparisons/m2048_r90_crossover_v2_20261108/` |
| All 333 recovered pilot result records | `comparisons/m2048_diagnosis_20261108/recovered-pilot-results.json` |
| Diagnostic native captures and frozen settings | `comparisons/m2048_diagnosis_20261108/runs/work-diagnosis/` |
| Recovered diagnostic results and decision | `comparisons/m2048_diagnosis_20261108/offline-analysis/` (`results.json`, `completion.json`, `decision-summary.json`) |
| Formal settings and results | `comparisons/m2048_r90_targeted_20261108/` (`plan.json`, `report.json`, `completion.json`, `interpretation.json`) |
| Formal per-query payloads for the conditional estimate | `comparisons/m2048_r90_targeted_20261108/runs/ordinary-confirmation/` |

SHA-256 identities:

```text
New upper artifact:
166238539498ac2ac3740ef0372cbd2802bd0f96868cabded748dc58a4e87d78
Ordinary client (label-entry-qps-20261103):
f78b8bd7047175f68df3fd379c62439c35d9d89e6abdd1bdcc4052d3389b7e43
Work client (label-entry-singleton-20261102):
0d0eb4ff0f73f6e0f30777c54faed1590a6e2255b10f2f1c2a69147217126996
Formal report.json:
68b47e3e88198a88ab1b30e67cb8d7ad5927490387477245860619c91e46db7c
Recovered diagnostic results.json:
084dc1403f3c053a0654b34f6a50bff0a00c4ee9215ec3d2ad5c19d7026e3298
```
