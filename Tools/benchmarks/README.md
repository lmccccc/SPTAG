# Benchmark Scripts

## Main posting Recall-QPS figures

`plot_posting_min_sweep.R` renders registered native measurements with R.
Historical modes retain the fixed six-panel layout: unfiltered, broad, medium,
extreme, numeric and mixed DNF; `--selectivity` plots only registered scenarios.
It reads `registration.json` and `plain-results.json`, requires
1,000 queries per run, and writes PNG/PDF, plotted CSVs and a
source-data snapshot to a new output directory.
Posting-policy comparison modes require two repetitions.

```bash
Rscript Tools/benchmarks/plot_posting_min_sweep.R CAMPAIGN NEW_OUTPUT
Rscript Tools/benchmarks/plot_posting_min_sweep.R CAMPAIGN NEW_OUTPUT --partial
Rscript Tools/benchmarks/plot_posting_min_sweep.R CAMPAIGN NEW_OUTPUT --member-postfilter
Rscript Tools/benchmarks/plot_posting_min_sweep.R CAMPAIGN NEW_OUTPUT --postgraph
Rscript Tools/benchmarks/plot_posting_min_sweep.R CAMPAIGN NEW_OUTPUT --selectivity
Rscript Tools/benchmarks/plot_posting_min_sweep.R CAMPAIGN NEW_OUTPUT --compact-storage
```

The default compares `graph`, `min1`, `min3`, `min5` and `min10` across all six
scenarios. `--member-postfilter` compares `graph`, the frozen `row_min1`
member-filter policy, and `all_members_min1`/`all_members_min10`. The latter
mode permits a registered scenario subset containing `medium_tag`; other
panels explicitly say **not measured**, with no historical timings substituted.
That historical member policy scores unvisited nonmatches too; its minimum counts
only fresh matching H1 candidates. Keep its entry rule and native search budget
unchanged when isolating that policy change.

`--postgraph` compares `graph`, `postgraph_shared`, `postgraph_extra` and
`graph_total` across all six registered scenarios. The registration must include
`posting_anchor_count` and a `budgets` object keyed by those four variant IDs.
Each budget declares integer `graph_maxcheck` and
`posting_additional_maxcheck`. The shared variant has no extra budget; the extra
variant retains the base graph budget and declares a positive supplement; the
H1-only `graph_total` control uses their combined nominal ceiling. Labels are
derived from these declarations, not guessed from variant names. They describe
configured checked-leaf budgets, not a hard limit on all distance evaluations.
The restored unfiltered path retains upstream adaptive stopping, including
soft-cap overshoot and the `max(MaxCheck / 16, resultNum)` navigation distance
pool. Changing MaxCheck can therefore change navigation breadth before the
nominal limit is reached. Filtered H1 needs an independent hard checked-leaf
guard because its matching-result heap can remain underfilled. Complete
auxiliary rows may cross the remaining-budget boundary; equal nominal ceilings
do not imply equal actual work.

H1-only means posting supplementation is disabled in the same project runtime;
it is not a pristine Microsoft SPTAG binary. Record the search-runtime revision
and stopping policy with each campaign. After a search-policy correction, rerun
every method against the same unchanged index, query cohort and runtime on the
same CPU/NUMA placement. Do not splice historical H1-only timings into new
postgraph curves. Search-only corrections do not require index reconstruction.

`--selectivity` reuses the four `--postgraph` variants, fixed styles, per-variant
`budgets` and positive `posting_anchor_count`, but accepts an arbitrary nonempty,
duplicate-free ordered `scenarios` array. It requires
`nprobe: [16, 24, 48, 96, 192, 384]` in that order, `query_count: 1000` and
`repetitions: 2`. For the requested no-filter-through-approximately-0.1%
categorical comparison, plus mixed DNF as a separate workload, register:

```json
["unfilter", "broad_tag", "medium_tag", "sel_01pct", "mixed_dnf"]
```

Reuse the authentic SIFT1B `broad_tag` and `medium_tag` predicates and exact
truth: their actual fractions are `0.170124930` (17.012493%) and `0.017012493`
(1.7012493%), not fabricated 10%/1% targets. Measure the new categorical
`sel_01pct` fraction from its exact truth rather than assuming exactly `0.001`.
The separate `mixed_dnf` fraction is `0.000425710` (0.042571%); give it an
explicit mixed-DNF title and its own actual-density label, not a categorical
0.1% label. These scenario IDs are registration choices, not renderer constants.

There are no automatic numeric-only, extreme-sparse or historical placeholder
panels in this mode. Layout follows the registered order. Add
`scenario_metadata`, an object with exactly those registered scenario keys.
Each value must contain a nonempty scalar string `title` and a finite scalar
`selectivity` fraction in `[0, 1]`. Supply **measured actual density**, not the
nominal percentage suggested by an ID. Titles visibly include
**Actual selectivity**; `unfilter` must declare `1` and is labeled
**100% (no filter)**. Scalar metadata describes a static predicate per scenario;
query-varying densities must not be disguised as one nominal fraction.

Optional `eligible_count` and `corpus_count` must be supplied together as exact
JSON integers (`0 <= eligible_count <= corpus_count`, `corpus_count > 0`,
both at most `2^53 - 1`). Their ratio must match `selectivity` within `1e-12`.
For example, this **synthetic schema illustration is not SIFT1B evidence**:

```json
{
  "broad_tag": {
    "title": "Synthetic categorical predicate",
    "selectivity": 0.1234,
    "eligible_count": 1234,
    "corpus_count": 10000
  }
}
```

The real `scenario_metadata` must also include every other registered scenario.
The ordinary `plain-results.json` array retains fields `scenario`, `variant`,
`nprobe`, `repetition`, `queries`, `recall` and `qps`; optional `diagnostic` must
be boolean false for every record. No historical timings or interpolated
recall points are inserted. Keep the registration frozen: this mode rejects
registration changes during rendering and records its hash and full-precision
scenario metadata in `plot_metadata.json`. It emits the same `recall_qps.png`,
`recall_qps.pdf`, `plotted_points.csv`, `nprobe24.csv` and `source_points.json`
as the historical modes. Never write synthetic test fixtures to actual campaign
output directories.

`--compact-storage` compares `original_layout` and `compact_layout` across all
six registered scenarios. Register an even number of repetitions, at least
two, and balance method execution order in the benchmark runner. The renderer
checks complete grids and repetition counts, not execution order. Additional
runs may be combined only when the protocol and frozen runtime/index for each
variant are unchanged; retain every run, including slow outliers. Do not pool
different compact implementations under one variant.
Use the original runtime and original index for the
first variant, not a legacy file converted to compact RAM by the new loader.
The registration declares one common `search_settings` object, for example:

```json
{
  "graph_maxcheck": 2048,
  "posting_additional_maxcheck": 2048,
  "posting_anchor_count": 8
}
```

These fields must be valid native integer settings, with a combined budget
within the native integer limit. Variant-specific `budgets` are rejected; if
individual measurement records also contain these settings, they must agree
with the common declaration. Native result names `max_check` and
`posting_additional_max_check` are checked against the same declared values.
Keep all other search INI settings, the query
cohort and index memberships unchanged. Exact result/head/work parity and
persisted/resident/peak storage measurements are separate acceptance evidence;
Recall-QPS curves alone cannot prove a lossless layout change or 1B scalability.

`--partial` uses only complete method-by-nprobe grids within each repetition
and can be combined with any comparison mode. In `--selectivity`, registered
scenarios with no complete repetition remain visibly pending; unregistered
scenarios never acquire panels. The four explicit comparison flags are mutually
exclusive. Diagnostic timings, duplicate
points, unregistered records and incomplete final grids are rejected. QPS is
the arithmetic mean of complete repetitions; bars show their range, not a
confidence interval. Lines connect measured points, without recall interpolation.

## Bounded H1 graph shortcuts from the complete H2/H3 hierarchy

The isolated native prototype in
[`hierarchical_shortcut_native/README.md`](hierarchical_shortcut_native/README.md)
has been implemented and executed on the first 1,000 SIFT1M queries. It keeps
the original H1 BKT/RNG32 graph, builds at most eight deterministic H1 shortcut
candidates from **both** original adjacent-layer CSRs, and shares the native
graph frontier, visited set and a hard total-distance-call budget. Plain,
added-edge and degree-preserving rewired searches use the same implementation.
No production algorithm, original index, posting, binary or `_SPTAG.so` is changed.

The corrected, fully validated results are in dataset comparisons
`h1_hierarchy_shortcut_20260915_v2/`; the first run is explicitly superseded.
At a 2,000-call cap, plain/added/rewired exact-H1 top24 recall was
98.9542%/98.9167%/98.9167%, with ordinary navigation
329.10/360.50/358.16 µs. At a 1,200-call cap, rewiring improved exact-head
recall by 0.2375 percentage points but cost 15.1% more time. This initial
shortcut policy did **not** demonstrate an advantage over widening H1 search
at near-equal high exact-head recall.

**Native full-query integration is now separately complete**, under
`hierarchical_shortcut_native/full/`, with results in
`h1_hierarchy_shortcut_full_20260915/`. An authenticated reconstruction of the
frozen runtime keeps ordinary H1 navigation plus its actual own-head/H/O/dedup
and SSD stages; it does not call the incompatible generic disk-search helper.
Frozen baseline H1 IDs/distances and recall/work match on all1,000 queries.
Final Recall@10 is natural/plain2000/add2000/rewire2000:
**90.73%/90.68%/90.71%/90.69%**. Ordinary full latency over three rotated paired
repetitions is **0.9656/0.9628/0.9905/0.9884 ms**. Add/rewire are 2.88%/2.67%
slower than capped plain, despite similar SSD work (~23.83 postings,
~143.7 pages/query). No full-query advantage is demonstrated.

The dedicated README documents reconstruction, native integration boundaries,
actual distance work, per-query counted/uncounted final-result parity, 2/2 native
fixtures, provenance and exact commands. This remains an offline H2/H3-derived
shortcut overlay, not a runtime hierarchical frontier or billion-scale result.
Do not mix preserved standalone timings with the fresh native full-query runs.

**Clarified joint acceptance has now been evaluated**, not just unfiltered:
`hierarchical_shortcut_native/full_scenarios/` runs original H1, original H3
and both existing hybrids on all six authentic SIFT1M workloads (unfilter,
broad/medium/extreme categorical, numeric, mixed DNF). Results are in
`h1_hierarchy_shortcut_scenarios_20260915/`: 168 native processes, three rotated
paired repetitions, frozen H1/H3 baseline parity, exact-filter/result/work
validation, and 535 protected files unchanged.

Unfiltered hybrid latency is 1.037×/1.029× H1 with near-equal final recall.
**Filtering does not meet the H3-like quality/cost goal**: hybrid versus H3
Recall@10 is approximately broad72.2% vs87.7%, medium19.8% vs86.3%,
extreme0.28% vs90.1%, numeric30.4% vs42.2%, and mixed1.04% vs76.1%.
Extreme/mixed hybrid queries underfill99.7% of the time. Lower latency or fewer
SSD reads at that quality is not a speedup claim. All scenario inputs exist;
the remaining limitation is the flattened policy's candidate/own-head admission
gap, not source availability. The dedicated README has per-scenario H1/H3
ratios, underfill and graph/CSR/SSD work tables. No weighted mixture, adaptive
predicate-dependent hybrid budget, fallback or broader tuning was introduced.

**Policy interpretation:** these results evaluate the existing **offline
flattened H1 overlay**, not the subsequently clarified online upward fallback.
That intended rule would take the current item's eight direct parent postings,
rank their representatives by **current-query-to-parent** distance, and expand
nearest first; the chosen upper item has its own eight parents at the next
layer. The prototype instead uses offline H1-item-to-owner distances and fixed
H1 edges. Online nearest-of-eight navigation was not implemented or benchmarked
**in that fixed-overlay matrix**, which must not be cited as evidence
about its effectiveness.

**A subsequent online parent-queue implementation is preserved**:
[`online_native/README.md`](hierarchical_shortcut_native/online_native/README.md)
defines its query-ranked eight-parent fallback, predicate-independent spatial
stall trigger, lazy16-member CSR cursors and unified2000/3200 distance budgets.
It offers every evaluated H1 to the original H3 own-point/posting admission
callbacks, with separate matched graph-only admission controls. Original H1/H3
baselines are unchanged. Results are in
`comparisons/online_owners_admission_20260915/`: 216 certified native processes,
six scenarios, two paired repetitions, full-path fixtures and1,749 unchanged
protected files.

The later architectural correction, **H1-only native traversal with synchronous
posting-derived neighbor supply**, is documented separately in
[`supplier_native/README.md`](hierarchical_shortcut_native/supplier_native/README.md).
It returns to the same native H1 frontier after each bounded helper call; no
upper navigation queue survives. Its new matched graph-only control and
six-scenario comparison do not rename or overwrite the parent-queue results.

Admission correction, **not online parents**, explains most gains: at budget2000
medium Recall@10 is original H1 19.77%, matched control94.03%, online92.63%.
Numeric is30.44%/49.08%/47.87%, with verified native own points from outside the
selected posting heads. Online2000 unfiltered recall90.69% is near H1's90.73%,
but latency is1.117×. Extreme/mixed online recall remains14.75%/31.54% versus
H3's90.07%/76.10%; budget3200 helps but still misses the joint goal and costs more.
Full tables, own-point proof, counters and the preserved launcher-race retry are
documented. This is now a measured result for an actual online policy, not a
reinterpretation of the failed fixed overlay.

## Same-posting H1/H3 navigation ablation on SIFT1M

`configs/sift1m_same_posting/` isolates navigation quality, rather than comparing
independently constructed SPANN posting stores. The native upstream builder
builds only a flat BKT on the preserved H3's **ordered 160,091 H1 vectors**.
Its saved `vectors.bin` must hash-identically to `SPTAGHeadVectors.bin`.
There is no head reselection, posting assignment, replica change, or SSD build.

```bash
python3 Tools/benchmarks/build_same_posting_head.py \
  --config Tools/benchmarks/configs/sift1m_same_posting/head_build.ini
python3 Tools/benchmarks/run_same_posting_head.py \
  --config Tools/benchmarks/configs/sift1m_same_posting/experiment.ini
python3 Tools/benchmarks/run_same_posting_head.py \
  --config Tools/benchmarks/configs/sift1m_same_posting/recall90.ini
```

Both modes use the **same frozen H3 executable and same search view**.
Equal-budget native INIs differ only in the historical `HeadNavigationMode`:
`H1Only` versus `H2Only` (the latter selects the complete preserved H3).
This is an offline historical ablation; it does not restore those retired
switches in production. H1 IDs, H/O regions, support metadata, hierarchy CSR,
top graph, 524-byte records and every SSD posting byte stay unchanged.
The complete original file inventory, including the posting payload hash,
is checked before and after execution.

The isolated view enables `BuildH1Graph`, replaces the dummy metadata-only
KDT with the real BKT, and omits its two graphless-root sidecars. The graphful
loader requires an explicit H3 catalog path; that path is only a symlink to
the existing top graph's `vectors.bin`. It adds no vector data or new samples.
The original graphless H3 and H3 in this graphful view must produce identical
recall, scans, distances and requested I/O counters. Their execution/storage
plumbing differs, so use the two modes **within the new view** for timing.

The first attempt without the top-catalog filename alias failed loading and
was stopped; its logs remain in `same_posting_head_20260915`. The successful
run is `same_posting_head_20260915_catalog_alias`; the near-equal-recall run
is `same_posting_head_20260915_recall90`. One-query preflights now reject
native load failures before launching the full warmup/cohort.

Ordinary runs alternate modes and reverse order on the second repetition.
They use 1,000 warmup + 1,000 measured queries, one query thread, NUMA2,
direct IO and search page15, matching the earlier comparison. Separate
`DumpHeads=2000` diagnostic runs capture the selected local H1 IDs before
posting selection; their logging-heavy QPS must not replace ordinary QPS.
`quality.ini` describes a separate offline exact-H1-neighbor audit of these
logs. Exact enumeration is diagnostic only and never a query fallback.

The standalone exact-head analyzer uses native `VectorSetReader` and native
Float squared-L2 distance functions. It computes all H1 distances once per
query and validates each logged distance before comparing the two selected
head sets. Run it separately from timed search:

```bash
prefix=/mnt/nvme/baotonglu/mocheng/datasets/sift1m_zipf200_sparse193_numeric/toolchains/spann_upstream_2ac3ebc
cmake -S Tools/benchmarks/head_quality_native -B "$prefix/head-quality-build" \
  -DSPANN_UPSTREAM_BUILD="$prefix/build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$prefix/head-quality-build" --parallel 1
ctest --test-dir "$prefix/head-quality-build" --output-on-failure
numactl --cpunodebind=2 --membind=2 "$prefix/bin/headqualitybench" \
  --config Tools/benchmarks/configs/sift1m_same_posting/quality.ini
```

`HEAD_QUALITY_QUERY` records include exact ranks, boundary ties and cross-mode
overlap. `HEAD_QUALITY_SUMMARY` is emitted only after all queries validate;
discard partial output on a nonzero exit. Exact head recall is **not** final
dataset Recall@10. Deterministic ranks use distance then local H1 ID; the
tie-adjusted recall separately caps boundary credit so extra tied heads
cannot hide omitted strictly nearer heads.

The completed same-posting L24 audit selected 24 heads for every query.
Flat H1 recovered 99.3333% of exact nearest24 H1 IDs, versus H3's 88.1417%;
tie-adjusted values were identical. Mean missing nearest24 heads were
0.160 versus 2.846 per query. These measurements establish selection loss
before disk scanning, but do not attribute it to an individual hierarchy
stage. Results and per-query ranks are preserved in the successful
comparison directory's `quality/` subdirectory.

### Offline H3 routing and parent-coverage audit

Build the `headroutingbench` target in a separate build directory and use
`configs/sift1m_same_posting/routing_quality.ini`. The optional `[Hierarchy]`
section specifies the ordered H2/H3 native catalogs, the two preserved V3
CSRs and the fixed parent beam. The tool neither loads a graph nor scans SSD
postings: it replaces upper query selection with exhaustive distance oracles,
then follows the saved CSR rows. It also measures exact-KNN parent coverage
for oracle target heads, without constructing or saving replacement CSRs.
All these exhaustive operations are diagnostics, never production fallbacks.

```bash
cmake -S Tools/benchmarks/head_quality_native -B "$prefix/head-routing-build" \
  -DSPANN_UPSTREAM_BUILD="$prefix/build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$prefix/head-routing-build" --target headroutingbench --parallel 1
python3 Tools/benchmarks/head_quality_native/test_head_quality.py \
  "$prefix/head-routing-build/bin/headroutingbench"
ctest --test-dir "$prefix/head-routing-build" -R head_routing_fixtures --output-on-failure
numactl --cpunodebind=2 --membind=2 "$prefix/head-routing-build/bin/headroutingbench" \
  --config Tools/benchmarks/configs/sift1m_same_posting/routing_quality.ini
```

The measured L24 search uses a routing beam of 16 at both H3 and H2, not 24.
The authenticated frozen construction source selects diversified parents with
RNGFactor=1 from 64 approximate candidates, then nearest-fills to exactly eight
replicas. This affects both H1-to-H2 and H2-to-H3 assignment; it is distinct
from RNG pruning of the top graph's lateral edges.

Completed results are in `same_posting_head_20260915_catalog_alias/routing_quality/`.
Exact-top replay matches all 24 logged H3-selected IDs for 998/1000 queries
and 23998/24000 selected IDs overall. Exact-H1 top24 coverage is 21156/24000
(88.1500%), versus logged H3's 21154/24000 (88.1417%). Bypassing H3 and directly
selecting the globally nearest 16 H2 nodes raises saved-CSR coverage only to
21270/24000 (88.6250%). These are oracle head-coverage metrics, not final data
recall or timed searches; differences between oracle modes are net changes,
not a disjoint per-stage accounting of the original misses.

With these same exact query-parent sets, replacing saved eight-parent
assignments by exact nearest-eight parents reduces H1 target coverage from
21270 to 20835 out of 24000: 877 targets are rescued but 1312 regress.
For the exact top16 H2 targets, H3-parent coverage falls from 15451 to 15209
out of 16000: 232 rescues versus 474 regressions. Thus this experiment does
not support removing diversity outright. The KNN comparison also removes
construction ANN approximation, so it is not an isolated RNG-on/off ablation
over identical historical candidate lists. The principal observed limitation
is coverage through a small fixed parent beam, especially the final H2-to-H1
transition; an individual parent's proximity is not a lower bound on all
of its children's query distances.

### H2-only ratio and replica experiments

`build_h2_only.py` runs the pinned native SPANN selector and head-graph builder
on the preserved ordered H1 catalog, with both SSD execution/build flags off.
It checks every selected H2 vector against its recorded H1 ordinal, verifies
that graph-vector bytes equal the selected catalog, and rejects source-vector
deletion or BKT side effects. No dataset or original posting file is rebuilt.

```bash
python3 Tools/benchmarks/build_h2_only.py \
  --config Tools/benchmarks/configs/sift1m_h2_sweep/build_r08.ini
python3 Tools/benchmarks/build_h2_only.py \
  --config Tools/benchmarks/configs/sift1m_h2_sweep/build_r16.ini
python3 Tools/benchmarks/build_h2_only.py \
  --config Tools/benchmarks/configs/sift1m_h2_sweep/build_r32.ini
```

The 0.16 case reuses the exact historical H2 selection and builds only its
independent BKT graph. The 0.08 and 0.32 cases make new native selections.
The upstream dynamic selector does not guarantee the requested count: these
builds produced 12813, 25607 and 44475 H2 nodes respectively, over 160091 H1
nodes. Report the actual ratio (the requested 0.32 case is about 0.2778).
Each ratio has one selection/graph build; this exploratory comparison does
not estimate construction-seed variability.

For head-only builds, generic upstream SPANN `SaveIndex` would overwrite the
selected ordinal map with its still-unloaded translation map if saved into
the same directory. Fixed `Execution.ExportDirectory` therefore keeps that
final generic export separate from `Base.IndexDirectory`; only the latter's
verified `HeadIndex` and selected vector/ordinal files are experiment inputs.
Successful builds are under `build_runs/h2_ratio_sweep_20260915_fixedsave/`.
The stopped first attempt under `h2_ratio_sweep_20260915/r08/` is preserved
as failed-run provenance and must not be consumed as a completed index.

The fixed sweep uses replicas 2/4/8/16 and H2 beams
4/8/12/16/24/32/48/64/96/128, while keeping the final H1 result count at 24:

```bash
python3 Tools/benchmarks/run_h2_sweep.py \
  --config Tools/benchmarks/configs/sift1m_h2_sweep/experiment.ini
python3 Tools/benchmarks/analyze_h2_sweep.py \
  --config Tools/benchmarks/configs/sift1m_h2_sweep/experiment.ini
```

See `h2_sweep_native/INTERFACE.md` for the independent native build and strict
schema. An isolated copy of upstream BKT wraps the native distance callback,
counting tree-center and graph distances, including repeated evaluations.
Counted and ordinary ordered IDs/distances must match; ordinary timing restores
the original callback. The pristine-library binary is also fixture-compared.
Original upstream sources, libraries and existing binaries are never changed.
The runner fingerprints all input graphs, H1 vectors, queries and the untouched
disk posting store before and after the run.

Completed results: `comparisons/h2_ratio_replica_20260915/`. All 120 points and
three flat references completed; 123000 query recalls were recomputed from
saved selected IDs and the shared exact-H1 oracle. The rebuilt 0.16/R8
assignments share 1280721/1280728 edges with the preserved CSR, with identical
owner sets for 160084/160091 H1 nodes.

The flat reference recovers 99.3333% of exact H1 top24 with 2232.031 mean
native distance calls (p95 2531). Best measured points not exceeding that
**mean** work:

| Actual H2 ratio | Replicas | Beam | Mean total distances | H1 recall |
| --- | ---: | ---: | ---: | ---: |
| 0.08004 | 4 | 32 | 2130.164 | 92.6208% |
| 0.15995 | 8 | 32 | 2156.467 | 95.4042% |
| 0.27781 | 8 | 48 | 2157.337 | 96.5458% |

The last point has p95 work 2763, above the flat reference. Capping both mean
and p95 instead yields best recall 95.6708% at ratio 0.27781/R16/beam24.
The cheapest observed point recovering flat recall is ratio 0.27781/R16/beam64:
99.3875%, mean 3674.756 distances, p95 4920. This is 1.646x flat mean work.
Its ordinary navigation mean is 386.504 microseconds versus its same-run flat
reference's 132.122 microseconds (2.925x). These are standalone H2-graph-plus-CSR
navigation measurements, **not** full-H3 production timings or dataset recall.
There is no interpolation between tested points and no global-optimum claim.

Owner-rank diagnostics explain the persistent misses: at ratio 0.16/R8, the
nearest assigned parent's query rank is at most 16 for only 88.625% of exact
H1 targets; the p95/p99 ranks are 29/67 and the maximum is 404. For query 0,
the second-nearest H1 (116651) has parent ranks
18/173/554/81/197/1014/291/2031, so the nearest 16 H2 postings cannot contain
it. R16 moves the p99 nearest-owner rank down to 38, but also doubles total
assignment entries and mean posting length. More replicas do not supply
cost-free query coverage.

#### Replica32 continuation

`configs/sift1m_h2_sweep/replica32.ini` runs replicas 8/16/32 over the same
three H2 graphs and ten beams using a new isolated `h2-replica32-build` binary.
The old executable/results are preserved. AssignmentCandidates remains 64;
only the allowed replica range and the explicit new replica list change.

```bash
python3 Tools/benchmarks/run_h2_sweep.py \
  --config Tools/benchmarks/configs/sift1m_h2_sweep/replica32.ini
python3 Tools/benchmarks/analyze_h2_sweep.py \
  --config Tools/benchmarks/configs/sift1m_h2_sweep/replica32.ini
python3 Tools/benchmarks/validate_h2_extension.py \
  --config Tools/benchmarks/configs/sift1m_h2_sweep/replica32.ini \
  --baseline Tools/benchmarks/configs/sift1m_h2_sweep/experiment.ini
```

All 90 points completed under `comparisons/h2_replica32_20260915/`.
The 93000 selected-ID recalls were checked; all 63000 repeated flat/R8/R16
query records match the previous experiment exactly except timing. Assignment
candidate banks, exact-H1 oracle files and repeated R8/R16 CSR bytes also match.

At actual ratio 0.15995 and beam16:

| Replicas | Exact-H2 oracle coverage | Actual head recall | Mean H1 distances | Mean total distances |
| ---: | ---: | ---: | ---: | ---: |
| 8 | 88.6250% | 88.2458% | 777.604 | 1503.889 |
| 16 | 94.8417% | 94.4833% | 1386.572 | 2112.857 |
| 32 | 97.7917% | 97.4750% | 2428.022 | 3154.307 |

R32 lowers the p99 nearest-owner query rank from R8's 67 to 23. At beam32,
R32's exact-parent coverage reaches 99.4958%, while actual head recall is
99.3042%, with 4723.192 mean distance calls and 540.666 microseconds.
This is near flat recall (99.3333%) but about 2.12x its mean distance work.

Under the strict flat mean-work cap (2232.031), the best tested R32 point is
actual ratio0.27781/beam12: 94.5583% recall and 1978.410 calls. The nearby beam16
point uses 2279.395 calls (+2.12%) and yields 96.2417%, still below R8/beam48
at the same ratio (96.5458%, 2157.337 calls). This comparison explicitly
reports the grid gap rather than implying an interpolated exact work match.
The lowest-work tested R32 point exceeding flat recall is ratio0.27781/beam48:
99.5542%, 4338.011 calls, versus R16/beam64's 99.3875% and 3674.756 calls.
Thus R32 substantially improves fixed-beam coverage, but this grid does not
show an equal-work advantage over the smaller replica counts.

#### Half-density H2 with replica32

`build_r50.ini` requests Ratio=0.5 and native automatic SplitFactor=0, which
the upstream builder resolves to 2. This is an explicit additional construction
change: the earlier fixed SplitFactor=6 restricted attainable selection density.
The new native BKT selection contains 81539 of 160091 H1 vectors, an actual
ratio of 0.509329. All selected ordinals and vector copies are verified.

```bash
python3 Tools/benchmarks/build_h2_only.py \
  --config Tools/benchmarks/configs/sift1m_h2_sweep/build_r50.ini
python3 Tools/benchmarks/run_h2_sweep.py \
  --config Tools/benchmarks/configs/sift1m_h2_sweep/ratio50.ini
python3 Tools/benchmarks/analyze_h2_sweep.py \
  --config Tools/benchmarks/configs/sift1m_h2_sweep/ratio50.ini
python3 Tools/benchmarks/run_h2_sweep.py \
  --config Tools/benchmarks/configs/sift1m_h2_sweep/ratio50_fine.ini
python3 Tools/benchmarks/analyze_h2_sweep.py \
  --config Tools/benchmarks/configs/sift1m_h2_sweep/ratio50_fine.ini
python3 Tools/benchmarks/validate_h2_extension.py \
  --config Tools/benchmarks/configs/sift1m_h2_sweep/ratio50_fine.ini \
  --baseline Tools/benchmarks/configs/sift1m_h2_sweep/ratio50.ini
```

The initial and refined grids cover 30 distinct beams. Fine points refine both
the equal-work boundary and the flat-recall crossing; four shared points and
the flat reference match exactly except timing. The single-ratio analyzer
reports the optional r16/R8/beam16 baseline as null when absent, but still
measures and compares the flat H1 reference.

The combined result is preserved in
`comparisons/h2_ratio50_replica32_fine_20260915/combined_comparison.json`.
Fine-run timing replaces coarse-run timing for shared points deterministically;
all non-timing fields must match. Both runs retain their original raw evidence.

| Navigation | H2 beam | Mean total distances | Head recall | Ordinary navigation mean |
| --- | ---: | ---: | ---: | ---: |
| Flat H1 | - | 2232.031 | 99.3333% | 131.993 us |
| H2 ratio0.509329/R32 | 29 | 2231.827 | 97.1958% | 216.119 us |
| H2 ratio0.509329/R32 | 54 | 3356.561 | 99.3042% | 345.766 us |
| H2 ratio0.509329/R32 | 56 | 3441.369 | 99.3792% | 355.513 us |

Thus near-identical mean distance work still loses about 2.14 recall percentage
points. At beam29, p95 work is 3155 versus flat's 2531; capping both mean and
p95 instead selects beam20 with 95.3125% recall. The cheapest tested point
exceeding flat recall uses 54.18% more mean distance calls and 2.693x ordinary
navigation time. It is not a claim of an untested global minimum.

At beam29 the exact-H2 oracle covers 98.5542% of exact H1 targets, versus the
actual route's 97.1958%. Both upper graph approximation and parent coverage
remain relevant; giving the route exact H2 neighbors alone still does not
match flat recall. These remain H2-graph-plus-one-CSR navigation measurements,
not full H3 execution or final dataset recall. Original H1 and disk postings
remain unchanged.

## Same-runtime full-query phase attribution on SIFT1M

`run_full_phase_ab.py` and `configs/sift1m_full_phase/experiment.ini` compare
flat24, H3_24 and H3_29 using the same frozen executable, ordered H1 catalog,
flat graph loader view and unchanged SSD postings described above. These are
the original three-level ratio0.16/replica8 routes, not the new H2-only/R32
sweep. The full-query metric is dataset Recall@10, not exact-head recall.

```bash
python3 Tools/benchmarks/run_full_phase_ab.py \
  --config Tools/benchmarks/configs/sift1m_full_phase/experiment.ini
```

The completed output is
`comparisons/full_phase_cost_20260915/` under the SIFT1M data root. An existing
output directory is never overwritten. Five rotating case rounds alternate
ordinary/profile order, with 1000 warmup and 1000 measured queries per process,
one query thread and verified O_DIRECT descriptors. All six native INIs disable
head dumps and path logging; each pair differs only in `LogPhaseTime`.
`runs.json`, `summary.json`, native logs, IO/resource evidence, configuration
snapshots and full input hashes preserve the evidence. Recall and native work
match across repetitions, profiling modes and the prior full-QPS experiment.

| Full-query metric | Flat24 | H3_24 | H3_29 |
| --- | ---: | ---: | ---: |
| Dataset Recall@10 | 90.73% | 88.60% | 90.78% |
| Ordinary mean latency, ms | 0.929780 | 0.778303 | 0.892789 |
| Ordinary median QPS | 1074.89 | 1286.68 | 1125.84 |
| Profile navigation, ms | 0.348450 | 0.233101 | 0.270430 |
| Profile posting retrieval/non-scan, ms | 0.507169 | 0.493322 | 0.548451 |
| Profile posting scan, ms | 0.063936 | 0.063499 | 0.076535 |
| Profile other posting work, ms | 0.014983 | 0.005195 | 0.006231 |
| Profile core total, ms | 0.934531 | 0.795121 | 0.901637 |
| Profile outside-core residual, ms | 0.034055 | 0.032110 | 0.034238 |
| Profile outer mean latency, ms | 0.968586 | 0.827231 | 0.935875 |

At near-equal data recall, H3_29 saves 78.020 us of navigation, adds 41.282 us
of retrieval/non-scan and 12.598 us of posting scan, and saves 8.752 us of other
posting work. This closes to a 32.894 us profiled core saving (rounding aside).
Ordinary end-to-end latency independently improves by 36.991 us, or 3.978%.
All five matched ordinary rounds favor H3_29: flat run means span
0.923985--0.934469 ms and H3_29 spans 0.878154--0.912538 ms. Median QPS is
aggregated separately and is not the reciprocal of mean latency.

H3_29's 270.430 us navigation includes 77.803 us of top-graph search,
140.856 us of CSR vector work, 32.214 us of merge, 4.078 us of tag handling,
5.456 us of sorting and 10.024 us of remaining navigation. These are nested
components, not extra charges to add to navigation. Native work counters such
as `headScanned`/`h2Upper` are not exact total graph distance-callback counts;
they cannot establish that full H3 does fewer or more distance calculations.

Timer interpretation follows the authenticated frozen source, not today's
production implementation. Navigation is `bkt + pq + graphOther`; flat BKT
search is inside `graphOther` despite zero `bkt`/`pq` fields. `io` subtracts
scan callback time from the asynchronous retrieval interval: it includes
scheduling, waiting and handling, and is not pure device latency. `scan`
includes record processing, deduplication, distance evaluation and heap work.
`postOther` includes posting preparation and final result handling. The
outside-core residual includes wrapper work and phase-log emission, not just
logging. Per-query phase sums are checked with 0.001 ms print rounding allowed;
profile sums must not be forced to equal ordinary latency.

The standalone pristine-upstream flat timer (~132 us) and H2-only/R32 timer
(~216 us at ratio0.509329/beam29) are a different experiment from these frozen
full-runtime navigation measurements. The full flat load log confirms
MaxCheck=2048 and HashTableExponent=4; the standalone tool retains the saved
flat graph's HashTableExponent=2. These observations do not isolate the cause
of the ~216 us full-versus-standalone flat timing gap. Do not attribute that
gap to hash configuration, count it entirely as wrapper overhead, splice the
two timers into one cost balance, or claim a general hierarchy speed advantage
over pristine BKT from this full-runtime comparison.

## Original SPANN versus preserved H3 on SIFT1M

`configs/sift1m_vanilla_spann/` pins a clean Microsoft/SPTAG checkout at
`2ac3ebcab562bc81cdb8c7c98b35ea72f2703c3b`. It uses the original
`docs/GettingStart.md` SIFT1M recipe, with 24 rather than 64 build threads.
Source, static libraries, executables, index/staging data, and comparison
outputs are isolated from this fork and the historical H3 artifacts. Only
existing local SIFT files are used; no dataset checkout or download is needed.

Build with the original `indexbuilder` using the fixed native INI:

```bash
python3 Tools/benchmarks/run_vanilla_spann_build.py \
  --config Tools/benchmarks/configs/sift1m_vanilla_spann/build.ini
```

The launcher derives its native arguments from the INI and deliberately omits
`-i`: at this revision a literal `-i FromFile` tries to open a file named
`FromFile`. `IndexBuilder` does not consume `[SearchSSDIndex]`; the benchmark
applies the separate fixed search controls and forwards their MaxCheck/hash
settings to the memory index. The upstream `UpdateIndex()` alone does not
forward those two settings.

Two original-reader details matter for this comparison. STATIC hard-codes
`O_DIRECT`, even when the default `UseDirectIO` option says false. Also, the
default `PostingVectorLimit=118` raises the example's page12 build/search
limit to page15 for 516-byte Float+VID records. The saved original index
therefore reports page15. The comparison search INIs explicitly use page15
for **both** engines rather than pretending that a late page12 setter
re-truncates the original reader's cached list metadata. Page-aligned physical
requests can include an additional boundary page.

The H3 preparation makes a separate loader/view; its only loader changes are
`IndexDirectory` and `UseDirectIO=true`. Every payload remains a symlink to
the unchanged frozen H3 index. Its build cap16, 524-byte attribute-bearing
records, representatives and memberships are retained. The fresh original
and preserved H3 do **not** share their H1 representative sets, so the result
is a comparison of the two concrete indices, not a pure structural ablation.

```bash
python3 Tools/benchmarks/run_vanilla_spann_comparison.py \
  --config Tools/benchmarks/configs/sift1m_vanilla_spann/h3.ini
python3 Tools/benchmarks/run_vanilla_spann_comparison.py \
  --config Tools/benchmarks/configs/sift1m_vanilla_spann/benchmark.ini
```

Run these serially, after the build and adapter compilation have finished.
Both use the same first 1,000 queries/top10 truth, full 1,000-query warmup
before each measurement, one query thread, CPU/memory NUMA node2, and three
ordinary/profile pairs. The original and H3 input containers differ, but
their query vectors and groundtruth IDs were compared exactly. Do not mix in
the old 100-warmup/900-query buffered-IO curve. The launcher records actual
posting descriptor flags, resource/I/O samples, commands, native logs and
immutable input identities; it never drops system caches or changes THP.

The original adapter calls the untouched public `SearchIndex` for ordinary
timing and the official memory-index/`SearchDiskIndex` split for phase timing.
Original STATIC does not populate separate scan/read latency fields:
posting access and scanning must be reported **together**, not as zero
scan cost or pure device latency. H3's old `h2` timing denotes the entire
three-level navigation; it does not expose separate H2/H1 timers.

The adapter is built separately against the original static libraries and
copies their generated compiler flags and ordered link dependencies:

```bash
prefix=/mnt/nvme/baotonglu/mocheng/datasets/sift1m_zipf200_sparse193_numeric/toolchains/spann_upstream_2ac3ebc
cmake -S Tools/benchmarks/vanilla_native -B "$prefix/adapter-build" \
  -DSPANN_UPSTREAM_BUILD="$prefix/build" \
  -DCMAKE_CXX_COMPILER=/usr/bin/c++ -DCMAKE_BUILD_TYPE=Release
cmake --build "$prefix/adapter-build" --target vanillaspannbench --parallel 1
```

`h3_recall90.ini` adds one fixed L29 measurement after the coarse curve,
bringing its recall close to original SPANN L24. Its outputs remain separate.
The read-only analysis and R rendering commands are:

```bash
python3 Tools/benchmarks/run_vanilla_spann_comparison.py \
  --config Tools/benchmarks/configs/sift1m_vanilla_spann/h3_recall90.ini
python3 Tools/benchmarks/analyze_vanilla_spann_comparison.py \
  --config Tools/benchmarks/configs/sift1m_vanilla_spann/analysis.ini
Rscript Tools/benchmarks/plot_vanilla_spann_comparison.R \
  /path/to/comparison/curve.csv /path/to/comparison/figures
```

The comparison uses H3's complete `post` phase, not just its `io+scan`
subphases. Ordinary vanilla timing surrounds `SearchIndex`; H3's historical
outer timer also includes its manager wrapper, native counter collection and
returned-ID copies. Both exclude recall calculation. Percent-level total
differences therefore should not be called isolated structural gains/losses.

H3 `contributing_postings_per_query` counts first-visit ownership after
cross-posting deduplication, so asynchronous completion order can change
that attribution while total reads, scans, distances, and recall remain
unchanged. Only that attribution field is excluded from the strict core-work
comparison; its observed range is retained. `--resume` reuses successful
native H3 logs only when the binary, inputs, payloads, commands, and fixed
search controls still match. Failed/incomplete native logs are never
overwritten. Old raw logs and the original analysis source remain intact.

`plot_vanilla_spann_comparison.R` renders a new `curve.csv` using R/ggplot2
and refuses to overwrite an existing comparison figure.

## Native vector input migration

`spannbuilder` now uses the original `VectorSetReader` dispatch and core DiskIO:

```ini
[Base]
ValueType=UInt8
VectorType=DEFAULT
VectorPath=/path/to/headered_base.u8bin
; Dim is optional for DEFAULT; when present it must match the header.
Dim=128
; Optional bounded prefix, min(header rows, VectorSize); omit for the full input.
VectorSize=2000000
```

`DEFAULT` is `[int32 rows][int32 dimensions][row-major elements]`, not a filename
extension heuristic. Negative/invalid dimensions, short headers, size overflow,
truncated payloads, trailing bytes, and conflicting `Dim`/`ValueType` are rejected
before payload allocation. The file itself must remain immutable during a build.
`ValueType` specifies `UInt8`, `Int8`, `Int16`, or `Float`; it is **not**
`VectorType`. `TXT` (native metadata-tab-vector syntax) and `XVEC` (per-record
dimension header) still use their native converters, require `Dim`, and validate
dimensions before exposing their converted DEFAULT vector set.

`VectorOffset`, `VectorCount`, `--vec-offset`, `--vector-offset`,
`--vector-count`, and vector `--n` are removed and fail explicitly.
Use `--vector-type`, `--value-type`, and optional `--vector-size` instead.
`VectorSize=-1`/omission means all; zero and values outside native signed
`SizeType` are rejected. This applies to normal build, signatures-only,
primary-head backfill, and OPQ/PipePQ prep. `--merge-tags5 --n` remains an
explicit prefix for the separate NPY-to-native-attributes preparation command.

`TagFile` is **headerless row-major native uint32 `[N, NumTagsPerVec]`**.
Its first row always begins at byte zero, implicitly. `TagOffset`,
`--tags-offset`, `--tag-offset`, `SPTAG_TAG_OFFSET` and `SPTAG_TAGS_OFFSET`
are removed and rejected even when explicitly zero (including CLI `=0` forms).
No format or replacement offset knob is introduced. Native DiskIO owns the
read-only attribute mapping, with a checked native read fallback when unavailable.
For all-row builds the file must be exactly `N * NumTagsPerVec * 4` bytes.
For `VectorSize` prefixes it must have exactly the selected number of rows
**or** the full validated vector-source count; only the selected rows are used.
Arbitrary extra rows, truncated/misaligned data and normally sized NPY/headered
files fail rather than being skipped or guessed. The production 1B/2-column
sidecar remains the same 8,000,000,000-byte file, unchanged.

Raw bytes have no embedded dtype/shape/endian marker: uint32 is the contract,
not a detectable property of same-size bytes. Same-size float/int32 data or a
header substituted for payload cannot be identified reliably and must not be
passed as TagFile. Declare **every column in original order** with
`[Tags] ColumnTypes=categorical,numeric` (CLI `--column-types`).
`cate,num` aliases are accepted; saved configs use full canonical names.
Width is derived; optional `NumTagsPerVec` must equal it. Single-label means
one value per categorical column, not one categorical column per record.
For example `numeric,categorical,numeric,categorical` uses numeric lanes
for original columns 0 and 2 and independent category values at 1 and 3.
`[BuildSSDIndex] LimitedTagColumn=3` selects the original key column; it must
be in range and categorical. Inputs and posting records are never reordered.
`spannaclbench --tag-column N` reads column N of the supplied query-tag matrix
and emits categorical equality on that same original column, rather than
matching the value across all columns. `--or-tag-count` retains flat tag-value
OR semantics; numeric predicates use `--query-dnf`.

Native APIs use `SetSSDBuildParam("ColumnTypes", ...)` (or the SPANN
`BuildSSDIndex` setter). Saved `TagSchemaVersion=1`, `ColumnTypes` and
`TagSchemaFingerprint` check the canonical original-order schema on load.
The existing posting/support formats remain unchanged and contain full raw rows.
Explicit-schema snapshots require a schema-aware reader; do not open interleaved
snapshots with older binaries that only understand categorical prefixes.
Numeric metadata retains its category-count field and densely packed domains;
the schema maps absolute query indices to numeric lanes everywhere.
Old authentic prefix layouts without explicit types remain readable through
private legacy count metadata; they are never reinterpreted as one-category
inputs. Signature regeneration requires a matching explicit input schema.

Repository recipes with known four-category or four-category-plus-numeric
inputs now declare their actual schema. Unsupported headerless `VectorType=RAW`
markers still require deliberate native-vector migration. No existing datasets,
historical inputs or historical run configurations are rewritten.

Only `--merge-tags5 --tags-npy ... --num-npy ...` accepts NPY: v1.0,
C-order little-endian uint32 `[N,acl-cols]` plus nonnegative int32 `[N]`,
with matching source row counts, checked shape/type/header/size and bounded
`--n`. Unsupported versions/order/dtypes and mismatches fail explicitly.
It writes a new native raw sidecar; never point TagFile at those NPY inputs or
rewrite historical datasets to make a config appear compatible.

On POSIX, the native DEFAULT reader optionally maps a validated file read-only.
The vector set owns the mapping through build/save, and the single-tenant bulk
path borrows it without a wrapper/core corpus copy for L2 or normalized Cosine.
Non-mapping DiskIO backends fall back to one native allocation/read.
Unnormalized Cosine automatically needs one mutable copy, which the core borrows
through normalization, build and save; the mapped source is never mutated.
Signatures-only reads header/stat and maps without scanning the
corpus, validates against saved metadata, and uses tags plus persisted support.
TXT/XVEC conversion may materialize a native intermediate on disk; it does not
silently treat those formats as DEFAULT.

Legacy `tenant0_base*.f32` profiles are deliberately marked `VectorType=RAW`,
an **unsupported migration marker**, not a new reader. Their files have no native
header; changing only the container to DEFAULT would corrupt interpretation.
Before rerunning those examples, explicitly create a **new** native headered file
with the known shape and unchanged row ordering/normalization (or select a
matching existing XVEC/TXT source), then update `VectorPath`/`VectorType` in a new
run configuration. Do not rewrite historical files or substitute a full SIFT
corpus for a tenant-local 404819-row source: tags and local VIDs must still match.
Headered UInt8/Int8 profiles are migrated directly, and intentional SPACEV
prefixes retain `VectorSize`. Production H5 uses `ValueType=UInt8`,
`VectorType=DEFAULT`, no offset/count/full-size override; all hierarchy,
thread-count, support, numeric, and H/O page settings are unchanged.

### Other redundant bulk input settings

The bulk command also removes `[Tags] Tenant`, `[Build] WithMetaIndex`,
`[Build] ShareBuildOwnership`, `--tenant`, `--with-meta-index`,
`--share-build-ownership`, and the `SPTAG_BUILD_SHARE_OWNERSHIP` environment
override. Explicit values fail even if they used to be `0`/`false`. Tenant 0
and no line-metadata index are invariants of this bulk model, not choices.
Ownership now follows the native metric and reader's `Normalized` state:
L2/normalized Cosine borrows; unnormalized Cosine copies once before native
normalization. The library's real tenant, metadata-index and ownership APIs
remain intact. `--tenant` still selects a real tenant for hierarchy maintenance
and existing-index posting transforms; it is removed only from bulk input.

`[BuildSSDIndex]`/`[SearchSSDIndex] NumTagsPerVec` is a redundant **bulk-input** setting: it is
derived from `[Tags] ColumnTypes` and still persisted
as the native runtime option. Keep only the schema setting in builder INIs.
Schema width remains required. The bulk categorical prefix and target column
are fixed to one and zero respectively; a tag byte offset is not a setting.

Retained deliberately:
- `Normalized` is the native reader assertion that Cosine input is already
  normalized; it cannot safely be inferred from a file header. It uses native
  boolean parsing, including `--normalized true|false` (native `-norm` alias);
  a bare checkbox-style `--normalized` is no longer accepted.
  Omitted means false (the redundant L2 production declaration
  was removed).
- `Dim` is the native consistency constraint (and required TXT/XVEC dimension),
  not a second format-specific offset/count. DEFAULT can derive it.
- `BuildSignatures` schedules real work: the launcher defers it to a fresh process
  to control peak memory, and no-tag/unfiltered builds need not generate filtering
  sidecars. `false` is also useful for a build followed by signatures-only.
  It does not promise filtered serving before required sidecars exist.
- `MinHeadsPerTag` has an active native representative-head promotion algorithm.
  Support expansion requires it to be zero, but non-expansion experiments can
  use it meaningfully; a mode constraint is not grounds to remove the native knob.
- Native head selection, storage, compression, H/O and search budgets remain
  native options. No meaningful budgets were replaced with wrapper constants.

## STM1 Static Metadata Demo

First migrate this legacy headerless Float input as described above.
The native INI is the source of truth for build and search settings. The
committed Float SIFT-1M fixture demonstrates node-pure STM1 postings, unbounded
unfilter tails, and the exact member-OR posting prefilter:

```bash
cmake --build build --target spannbuilder spannaclbench -j

CFG=Tools/benchmarks/build_spann_attr_sift1m_tagged_4node_static_fullfloat_tail_unbounded_ordered_page.ini
Tools/benchmarks/run_spann_attr_build.sh "$CFG"

IDX=/datadisk/yfcc_fast/sptag_sift1m_tagged_vs_upstream/index_tagged_4node_static_fullfloat_tail_unbounded_ordered_page
QDIR=/home/v-mochengli/datasets/sift1m/multitenant/query

Release/spannaclbench \
  --index "$IDX" \
  --queries "$QDIR/query_vectors.npy" \
  --truth "$QDIR/groundtruth_project_local_ids.npy" \
  --query-tags "$QDIR/query_tags.npy" \
  --tag-column 3 \
  --warmup 200 --max-queries 1000
```

`[SearchSSDIndex]` in the INI controls the persisted search behavior:
`InternalResultNum`, `MaxCheck`, and `SearchPostingPageLimit`. Exact attributes
admit results and choose H/O membership; signatures do not prune posting reads.
Do not override these with `SPTAG_*` environment
variables. The JSON output includes recall/QPS and loaded-posting contribution
metrics when `CollectPostingContributionStats=true` is enabled in a diagnostic
search overlay.

For a reload-only sweep, pass a separate native runtime overlay instead of
modifying the persisted index or using environment variables:

```bash
Release/spannaclbench ... \
  --search-ini Tools/benchmarks/search_turbopuffer_sift1m_tenant0_n20.ini
```

## Hybrid Build Metadata

`build_spann_attr_sift1m_global_static_hybrid_distance.ini` is the hybrid-on
experiment; `build_spann_attr_sift1m_global_static_bkt_control.ini` is its
matched hybrid-off control. Both retain the canonical SIFT1M BKT head
selection and degree-32 vector graph. Hybrid-format indexes may retain
generation-bound posting-layout statistics, head attributes, pure-prefix
metadata, and a marked cross-edge artifact for compatibility and diagnostics.
These artifacts do not select a query graph or alter a search budget. The sole
STM1 posting remains:

```text
H | O
```

Here `H` is the pure prefix and `O` is the complete original vector-distance
pure+tail posting. Each region is internally unique, while a VID may
intentionally occur once in each region. The `O` suffix remains sorted by
vector distance.

All filtered and unfiltered requests navigate the same spatial graph with the
same configured `InternalResultNum`, `MaxCheck`, hierarchy beam, and cross-edge
policy. Predicates only choose safe posting-region membership and perform exact
record admission. `HybridRouteSampleCount`,
`HybridRouteSelectivityThreshold`, `HybridRouteDeformationThreshold`, and
`LogHybridRoute` are removed and rejected.

## Limited-Tag Static Postings

`build_spann_attr_sift1m_zipf200_limited_tag.ini` builds the two-column
categorical/numeric limited-tag experiment. Its canonical H3 -> H2 -> H1
pipeline keeps only the H3 graph; lower layers use ID-only CSR postings and
logical vector catalogs. It does not create attribute subsets, hybrid edges,
or cross edges.
For local SIFT1B, use
`build_spann_attr_sift1b_zipf200_limited_tag_h5.ini`: it keeps the
[`GettingStart.md`](../../docs/GettingStart.md) SIFT1B baseline budgets and
extends this spatial hierarchy to five total levels. Its raw UInt8 records
are 140 bytes, and it uses the existing two-column single-label/numeric
attributes, O-derived floor-16 support, and new `_h5` output directories.
H1 retains the query graph; upper levels retain representative vectors and
signed CSR, not query ANN graphs. Every level uses the same `.12` selection ratio,
configured solely by native `[SelectHead] Ratio=.12`, `HierarchyEnabled=true`,
and `HierarchyLevels=5`.
The CSR replica count remains `8`. This profile does not overwrite the older H1/H2 index.

The native hierarchy interface uses `[SelectHead] HierarchyEnabled`,
`HierarchyLevels` (total levels, including H1), and **one** `Ratio` for all
selection stages. `Count` must remain zero for hierarchy builds. Small layers
round up to at least one head without changing the saved ratio. The remaining
selection keys are `HierarchyReplicaCount`, `HierarchyHeadVectors`,
`HierarchyHeadVectorIDs`, `HierarchyHeadIndexFolder`, `HierarchyPostingFile`,
and the generated `HierarchyGenerationFingerprint`.
Runtime navigation uses native H1 `MaxCheck` and `InternalResultNum`.
`EnablePostingNavigation` defaults to false; current hierarchy recipes enable
it explicitly to add signed CSR neighbors after sparse ordinary rows.
H1 result-only post-filter remains the filtered baseline when it is disabled.
`HierarchyInitialProbeRatio`, `HierarchyMaxCheck`, `HierarchyPrefetchMode`,
`BuildH1Graph` and `CompactHierarchyVectors` are retired fresh settings.
There is no independent upper ANN query or global tag-to-H1 support scan.
Native temporary upper ANN indexes remain only during construction to preserve
the original candidate/RNG replica assignment; they are not persisted.

`HierarchyRouteSelectivityThreshold` and its `SecondLevelRouteSelectivityThreshold`
alias are removed and rejected, including zero, empty and old-default values.
There is no replacement dispatch knob. `HeadNavigationMode` and
`HierarchyGraphSignaturePruning` are also removed and rejected. Persisted
legacy signature domains remain authenticated compatibility metadata.
Signatures themselves conservatively reject auxiliary posting regions before
representative/member access, never ordinary H1 graph neighbors.
Posting navigation does not require wrapper selectivity estimates or
`tag_routing_stats.bin`. The tag-statistics sidecar may still be produced for
diagnostics and public statistics consumers, but it never controls search and
its absence does not disable filtered queries.

Use canonical hierarchy catalog/CSR names in new INIs. Required saved legacy
layout/provenance fields are decoded explicitly with warnings; retired upper
search controls do not regain setter or query semantics. Saving emits
canonical keys. Existing artifact filenames and binary formats remain readable
where their data is needed, without loading an upper ANN graph.
`SecondLevelRatio` is **not** a selection option: it is accepted only as a
compatibility constraint and is omitted on save. An enabled legacy hierarchy
whose ratio differs from `Ratio` fails build/load/save explicitly; use the
legacy reader for that historical index, never edit its frozen config to
pretend it was built with shared ratios. Disabled old hierarchies can ignore
their unused legacy ratio. Repository templates now describe new shared-ratio
builds; historical benchmark results and run INIs are not rewritten.
New hierarchy SelectHead checkpoints also record the shared ratio and total
levels. Resume rejects missing legacy provenance or a changed ratio/depth rather
than silently reusing heads selected under a different model. Use
`ResumeBuild=0` and fresh output paths for such migrations; non-hierarchy legacy
checkpoints remain readable.
The launcher validates these settings before any build, and checks canonical
persisted settings afterwards (including STATIC graphless indexes). A read-only
preflight is also available:
`python3 Tools/benchmarks/validate_spann_hierarchy_config.py <build.ini> [indexloader.ini]`.

The bulk schema lists every original column in `ColumnTypes`; categorical and
numeric columns may be interleaved. Limited-tag H-region eligibility uses only
categorical equality anchors on the target column; all categorical and numeric
DNF3 literals are still evaluated exactly on posting records. Native
`[BuildSSDIndex] LimitedTagSlotsPerHead` accepts any positive integer (`2` is
the default).
The default fixed-slot mode persists that many support values per head in generation-bound
`limited_tag_support.bin`:

```text
support[head][0] = source vector attribute
support[head][1..N-1] = top-(N-1) external attributes
```

In the default mode, the requested count cannot exceed the number of distinct key-column tags,
because every support value on a head is distinct. With one slot, a head
supports only its source vector's tag.

The canonical INI sets `LimitedTagSlotsPerHead=2` explicitly. Use
`build_spann_attr_sift1m_zipf200_limited_tag4.ini` for the isolated four-slot
experiment.

The current SIFT1M `build_spann_attr_sift1m_zipf200_limited_tag.ini` uses the
**retained-O expansion model**: shared `Ratio=.16`, H3, `MinHeadsPerTag=0` and
`LimitedTagMinHeadCount=16`. It declares native DEFAULT Float input and original
`categorical,numeric` columns, with expansion enabled and no global cap. Fresh output
uses `index_limited_tag_h3_current`, never the historical
`index_limited_tag_h3_placement_fixed`. Derive unique run directories for repeat
experiments. Its 24-thread native build, beam 64, build MaxCheck 8192,
construction page limit 16 and search page limit 12 remain unchanged.
The old eight-head non-expanded control remains historical provenance, not the
current build recipe.
The launcher completes both the primary build and the separate signature phase
when `BuildSignatures=true`; a primary-only build is not an equivalent benchmark.
Comparisons against historical results measure the broader accumulated cleanup,
not cap removal alone. A causal cap-only comparison also needs frozen compatible
executables/configuration and identical retained assignments; upstream RNG does
not guarantee identical independent rebuilds.

### O-derived low-coverage support expansion

The opt-in expansion mode keeps the geometrically selected real H1 heads fixed.
It adds support relationships, not heads, vectors, a sparse-only posting, or a
new query route. Configure it entirely through the native INI:

```ini
[SelectHead]
MinHeadsPerTag=0

[BuildSSDIndex]
EnableLimitedTagSupportExpansion=true
LimitedTagSlotsPerHead=2
LimitedTagMinHeadCount=16
PostingPageLimit=3
PostingVectorLimit=118

[SearchSSDIndex]
SearchPostingPageLimit=3
```

The base row protects the own tag and takes the nearest distinct external tags
from actual **post-RNG, post-cut retained O records**, up to
`LimitedTagSlotsPerHead - 1`. A tag's distance is its closest retained member's
distance to that head (ties use tag ID). Rows may remain underfilled.
There is no separate candidate-count option, centroid-neighbor filling, or
base-row coverage repair. With expansion disabled, an unmet support floor
fails validation rather than replacing nearer tags or inventing candidates.
Original vector counts include real H1 representatives and count each VID
once. A single pass over the **retained O prefixes after posting cuts** finds
distinct candidate `(tag, head)` relationships for tags below the support floor.
Discarded scratch-array suffixes and pre-RNG votes are not expansion sources.
Each deficient tag retains a bounded deterministic pseudorandom subset of its
new O heads; already supported heads and repeated members of one posting do
not consume additional slots.

The required support count is the smaller of the floor and the number of
feasible base-plus-O heads. Thus a head-only singleton needs its one own head,
not sixteen invented postings. A tag with neither a support head nor a retained
O source fails construction. There is no independent global extra-support cap:
candidate state grows incrementally only for distinct actual retained-O heads,
up to each tag's floor deficit. Total state is bounded by the sum of those
deficits, not a configured maximum or an eager floor-sized allocation.
Overflow and allocation failures remain explicit; targets are never truncated
to fit memory. The planner does not scan the entire head catalog once per tag.

All non-head records still use support-filtered placement and the same RNG
replica rule. Empty-result recovery ranks supported heads and applies that
same RNG rule; it does not impose a special one-replica policy on sparse tags.
The O assignments remain unchanged. Both H and O use the native construction
cut: if `PostingPageLimit>0`, the effective page count is
`max(PostingPageLimit, ceil(PostingVectorLimit*recordBytes/4096))`, and each
region initially retains at most `floor(effectivePages*4096/recordBytes)` nearest records.
This applies to every H head, not only heads with extra supports. For 140-byte
records, `PostingPageLimit=3` and `PostingVectorLimit=118` mean **5 pages /
146 records per region**, not 3 pages. Nonpositive build page limits disable
the cut. Whole records and their attributes are preserved; RNG copies beyond
the cut are intentionally dropped. If a non-head vector loses every H copy,
a lightweight pre-write rescue appends **one** of its already-computed H RNG
edges: the nearest originally assigned support-legal head, using the original
distance (head ID breaks distance ties). Vectors retaining any H copy receive
no supplement. This is not SPFresh dynamic insertion/refill: there is no second
BKT search, global support-head scan, new support, or restoration of all replicas.
Rescues are distance/VID ordered within each head's H tail, **before O**; the
normal H prefix and all O bytes/order/counts are unchanged. The H tail is not
recut and may exceed the normal build bound, subject to the existing native
uint16 page-count capacity. Full row attributes, VID and vector are serialized,
and pure counts, signatures and fingerprints include the rescued H records.
Build logs distinguish zero-H before/after, rescued vectors/records/postings,
and added H payload pages. A missing original source edge after successful
pre-cut placement remains an explicit internal inconsistency, not permission
to invent an edge. This lightweight policy is not a coverage/floor acceptance
gate or a guarantee of query recall.
Support-row membership is permission to receive records, not a guarantee that
every support has a nonempty H posting or that the realized floor is reached.

`SearchPostingPageLimit` independently bounds physical pages read from the
selected H prefix or O suffix, starting at that region's first physical page.
Only fully read records are scanned, including at an unaligned H|O boundary.
The `PostingVectorLimit` uplift is construction-only. Nonpositive search
limits mean an unrestricted region scan. `[SearchSSDIndex] PostingPageLimit`
is the native runtime alias for `SearchPostingPageLimit`; it does not change
the build cut. Numeric/flat/DNF fallback selects O but does not bypass this
budget or exact predicate checks. Rescue does not raise this budget: for
example a 12-page search can still stop before a rescued H tail.

`LimitedTagMaxExpandedPostingPages` is removed and explicit uses are rejected.
For an immutable older index, remove only that obsolete INI entry in a copy;
support binary versions/fingerprints are unchanged. Loading does not retrofit
the native H cut/rescue: rebuild to adopt it. The auditor strictly checks the
O construction bound but reports beyond-cut H records, postings and payload
pages without rejecting a legal H overflow. A saved index cannot prove that
arbitrary excess H records came from this rescue; these are **not** inferred
rescued counts. Zero-H non-heads and unobserved VIDs are diagnostic counts,
not a global coverage acceptance gate. Support legality, duplicates, replica
limits and retained-O support provenance remain validated.

New expanded support uses generation-bound V5 storage: the original base rows,
sparse `uint64` head offsets and sorted `uint32` extra tags, plus authenticated
per-tag feasible targets. V5 retains the 80-byte V4 header layout; the old
`maxExtraSupports` word is now the exact sum of
`max(RequiredHeadCount(tag) - baseCoverage(tag), 0)`, equal to the stored extra
count (including zero). It is a validated content invariant, not a build
parameter. Each tag's extras must exactly fill its source-capped deficit.
A new fingerprint discriminator authenticates these V5 semantics; V4 hashes
are not reinterpreted. File sizes/count arithmetic, requirements and CSR offsets
are checked before allocating the extra payload.

`LimitedTagMaxExtraSupports` and `--limited-tag-max-extra-supports` are removed;
explicit INI keys (even zero/empty, including saved-index metadata) and the CLI
flag are rejected. New saved INIs omit the key. `ConfigureExpansion` no longer
accepts a budget; the old `MaxExtraSupports()` API is removed.
`LegacyExtraSupportCap()` exposes **only** authenticated read-only V4 provenance,
and returns zero for V5/non-expanded tables. Old immutable V4 support files
retain their original positive cap, version, candidate-source provenance and
fingerprints on read/export, byte-for-byte; they need not satisfy V5's exact
deficit invariant. Migration means deleting only the retired INI entry in a
separate writable clone, never rewriting historical datasets, support bytes,
hierarchy/signature files or experiment manifests. A fresh rebuild creates V5.
Expansion-mode, generation, schema and expected-content compatibility guards
still apply; no runtime INI cap is needed to load either version.

The serialized
overflow costs `8*(H1+1) + 4*extraSupports` bytes, plus 16 header bytes; runtime
lookup indexes have additional memory costs. The full-width offset path uses
a derived one-bit-per-head presence map; zero-overflow tables skip that lookup
altogether. If the nonzero extra-support count fits a uint32 offset and the
local-row allocation is representable, all admission fields share one row:
`[uint32 offset, first extra tag, base tags..., own attributes...]`. With two
base slots and two own attributes this is 24 bytes per head, the same retained
size as the former base, attribute, and CSR-offset arrays together. Those
separate allocations and the presence bitmap are released; the first extra
tag does not require another payload load. Empty
rows cache the reserved empty tag rather than taking a per-head presence
branch. Larger tables retain full-width offsets. This is a
runtime representation only: validation, fingerprints, and saving decode the
original base rows and uint64 CSR offsets in bounded chunks, so legacy V4 files
remain byte-compatible and V5 remains V5. Loading reserves the local rows before conversion and releases
the original attribute and offset buffers afterwards. Hierarchy admission prefetches the
whole small lookup row directly; the full-width path additionally
prefetches out-of-line tags in a bounded stack batch. Neither path duplicates
the tag table or changes navigation according to query selectivity.
The hierarchy resolves row pointers and strides once per query, rather than
redispatching the storage layout for every candidate head. Separate small
lookup kernels keep compact-row register pressure out of the full-width path.
V1/V2/V3 remain readable and
legacy builds retain their existing format. Complete support rows are used
by H1 admission and upper-layer signatures. Expanded snapshots support
directory save/export/reload but reject insertion, deletion, and topology
maintenance.

The floor is a **support planning target**, not a guarantee of that many
nonempty H postings after RNG. Build logs distinguish support targets from
realized H/own-head coverage. The native audit reads the persisted H/O records,
checks all non-head VIDs survive in H, verifies every extra support has an
actual O source, and emits per-tag/per-posting CSVs and a JSON summary:

```bash
cmake --build build --target spannsupportaudit -j2
Release/spannsupportaudit /path/to/index/tenant_0 /path/to/report/coverage
```

Compare `original_membership_fingerprint` and H1 source-VID catalogs for matched
builds; the audit reports actual H replication, effective coverage, unused
extra supports, and payload sizes rather than assuming the requested floor
was realized. The feature does not remove the STATIC builder's existing
single-batch, global-selection-sort, or raw-vector limitations at billion scale.

For a floor-only comparison, keep O-derived mode enabled on both sides and
vary `LimitedTagMinHeadCount` (for example, 1 versus 16). A legacy-mode
comparison also changes base-row filling and empty-search RNG recovery;
even a run with zero added supports can change replication and I/O. Independently
rebuilt trees can vary even with one head-build thread because clock-based
global reseeding affects BKT k-means. `BKTSeed`/`TPTSeed` were local additions
(absent upstream), and remain rejected rather than exposed as tuning parameters.
Current CPU construction matches upstream `5619bb1` RNG behavior: BKT centers
use `Utils::rand` (global `std::rand`), with default `std::mt19937` shuffles.
TPT uses one default `std::mt19937` shuffle engine per worker; each tree calls
`Sleep(i * 100)` then `std::srand(clock())`, and projection weights use global
`rand()`. The upstream sleep/reseed sequence is intentional, not a new seed
control. There is no custom fixed seed or deterministic tree-ordinal stream.
One thread does not guarantee reproducibility; parallel scheduling and
floating-point reductions add variability. For paired build tests, reuse a
single saved head/candidate realization rather than assume independent builds
match. RNG alignment does not imply full upstream build bit-identity.
Saved seed declarations are load-only provenance, logged explicitly; existing
tree/graph bytes and query behavior are unchanged. Require matching source-ID, tree/graph,
CSR-geometry, base-support, and O fingerprints before attributing differences
solely to the support floor.

Historical fixed-seed controls and recorded results below remain frozen
provenance. They do not describe current nondeterministic reconstruction;
do not rewrite their manifests or infer that a fresh build has the same results.

#### Controlled SIFT1M / 8192-tag experiment

The completed run is
`../datasets/sift1m_zipf8192_numeric_support_expansion/limited_tag_support_expansion_runs/sift1m_zipf8192_numeric_support_expansion_measured_seeded_heads_final/`.
It uses the existing local SIFT vectors, Zipf-1 tags (minimum tag size 13),
exact top-10 truth, and the same three-layer spatial hierarchy in every
variant. H1/H2/H3 source IDs, top tree/graph/vectors, lower CSR geometry, and all
5,998,317 retained O assignments match. The two O-derived variants also have
identical base support rows. Earlier unseeded runs are diagnostic artifacts,
not the matched comparison.

| Persisted quantity | Legacy floor 1 | O-source floor 1 | O-source floor 16 |
| --- | ---: | ---: | ---: |
| Real H1 heads | 160,091 | 160,091 | 160,091 |
| H records | 3,005,886 | 3,297,203 | 3,301,290 |
| Extra supports | 0 | 11 | 52,634 |
| Extras with nonempty H | 0 | 11 | 52,432 |
| Support-file bytes, not RSS | 2,692,592 | 3,973,388 | 4,183,880 |
| H + O payload bytes | 4,718,202,372 | 4,870,852,480 | 4,872,994,068 |
| Tags below their feasible target after RNG | 0 | 0 | 532 |

For tags with 13-20 vectors, median effective H/own coverage rises from 5 to 16;
the floor-16 minimum is 13, not 16. There are 202 unused extra supports.
Raising only the O-derived floor adds 210,492 support-file bytes and 2,141,588
payload bytes. Relative to legacy mode, the complete new floor-16 mode adds
1,491,288 support-file bytes and 154,791,696 payload bytes; do not attribute
that entire difference to the floor.

Queries use CPU 24, one native search thread, three serial interleaved trials,
100 warm-up queries, and 900 measured queries. All probes 16/32/64/128 are
retained in the figures and CSVs. These are the original, pre-lookup-correction
measurements; the same-index runtime correction is recorded separately below.
At the common probe of 64:

| Workload | Recall@10, floor 1 -> 16 | Median QPS, floor 1 -> 16 | QPS change |
| --- | ---: | ---: | ---: |
| Unfiltered | 0.971333 -> 0.971333 | 1101.29 -> 1104.87 | +0.33% |
| Broad tag 0 | 0.974222 -> 0.974222 | 1302.58 -> 1226.52 | -5.84% |
| Tags with 13-20 vectors | 1.000000 -> 1.000000 | 3613.30 -> 2619.53 | -27.50% |
| Tags with 21-50 vectors | 1.000000 -> 1.000000 | 3303.39 -> 2584.04 | -21.78% |
| Tags with 51-200 vectors | 0.986556 -> 0.986111 | 2579.71 -> 2250.65 | -12.76% |

The added coverage does not improve recall in these workloads. For the 13-20
bin, even probe 16 already has recall 1 in both O-derived modes; expansion
increases postings/query from 5.27 to 15.99, pages/query from 14.54 to 54.66,
and scanned records/query from 77.57 to 318.21, while useful distance
computations remain unchanged. Its QPS declines 23.9-35.8% across the probe
grid. This experiment therefore does **not** justify a universal floor of 16.
Expansion remains opt-in; the canonical 201-tag INI and active index are
unchanged.

Historical command sequence (not a current fixed-geometry reproduction recipe):
it used the existing fixture and a fresh run label, refusing to overwrite an
existing run. Current independent builds use upstream RNG and can fail the
driver's required geometry/O identity audit; a matched comparison must reuse
the same realized heads/candidates. Do not bypass that audit or relabel the
historical results as current:

```bash
cmake --build build --target spannbuilder spannaclbench spannsupportaudit -j2
python3 -B Tools/benchmarks/run_limited_tag_support_expansion.py build \
  --fixture-root ../datasets/sift1m_zipf8192_numeric_support_expansion \
  --run-label seeded_reproduction
run=../datasets/sift1m_zipf8192_numeric_support_expansion/limited_tag_support_expansion_runs/sift1m_zipf8192_numeric_support_expansion_seeded_reproduction
python3 -B Tools/benchmarks/run_limited_tag_support_expansion.py audit --run-dir "$run"
python3 -B Tools/benchmarks/run_limited_tag_support_expansion.py benchmark \
  --run-dir "$run" --cpu-affinity 24 --trials 3
python3 -B Tools/benchmarks/run_limited_tag_support_expansion.py finalize \
  --run-dir "$run" --plot
```

The run retains native INIs, build/audit/query logs, input and binary hashes,
the identity report, 180 raw trial rows, 60 aggregate rows, per-tag coverage,
and R-rendered PNG/PDF performance and structure figures under `final/`.
Coverage uses a labeled log1p axis to retain the legacy outliers without
obscuring the small-tag region; lines are exact-size arithmetic means, not
fitted trends. Build timings use serial head construction for control and
are not a measurement of the fastest parallel production build.

#### Runtime lookup correction v2: same saved indexes

The floor-16 H1 catalog has a mean of 2.32548 support tags per head; 95.2989%
of heads have at most three. Broad tag 0 itself has no extra supports:
its 31,856 supporting heads and 592,969 H records are unchanged. The original
lookup nevertheless chased overflow metadata on base misses for heads with
unrelated extras: 35,251 such heads in floor 16 versus nine in floor 1.
This is an access-layout cost, not evidence that comparing two or three tags
intrinsically requires a 5% query slowdown.

The correction co-locates base support, own attributes, the first extra tag,
and the compressed offset, and resolves row views once per query. All nonempty
overflow fitting the compact representation uses the same layout, without an
extra-density cutoff or a query-selectivity branch. Full-width offsets remain
the representability fallback. No head, support relationship, H/O assignment,
navigation route, native search setting, or V4 serialized byte layout changes.

The accepted frozen binary is `diagnostics/support_lookup_prefetch/spannaclbench.contiguous`
(SHA256 `8bebcce64d53bae09fef81bb29a2a93e4f045516727c87b1c490d2696447462e`).
Its reference is `spannaclbench.before`
(SHA256 `c683bc56e7a40dec38f7094b55765acf8c7e5b109e89474496c1ed1850c6e74c`).
`contiguous_full_grid_ab/` contains four balanced serial trials, 320 raw rows,
and 80 summary rows on the exact saved floor-1/floor-16 indexes and native INIs.
Native recall and work fields match the original outputs for every row.
At probe 64, the uninstrumented same-run medians are:

| Workload | Floor 1 QPS, before -> after | Change | Floor 16 QPS, before -> after | Change |
| --- | ---: | ---: | ---: | ---: |
| Unfiltered | 1114.69 -> 1114.05 | -0.06% | 1116.01 -> 1117.69 | +0.15% |
| Broad tag 0 | 1306.63 -> 1326.41 | +1.51% | 1242.05 -> 1331.40 | +7.19% |
| Tags with 13-20 vectors | 3635.98 -> 3616.90 | -0.52% | 2588.55 -> 2894.34 | +11.81% |
| Tags with 21-50 vectors | 3332.30 -> 3308.16 | -0.72% | 2601.61 -> 2956.38 | +13.64% |
| Tags with 51-200 vectors | 2595.13 -> 2572.99 | -0.85% | 2241.11 -> 2530.68 | +12.92% |

Across probes 16/32/64/128, the broad-tag floor-16/floor-1 QPS gap changes from
-6.83%..-4.77% to -1.31%..+0.38%; the floor-1 broad baseline also improves.
This is not a literal zero-overhead claim: floor-1 sparse cases range from
-1.88% to +0.48% against their own original baselines. Floor-16 sparse cases
improve 9.07%..13.64%, but the additional posting reads remain; for the 13-20
bin at probe 64, floor 16 is still about 20% slower than floor 1.

`contiguous_phase_ab/` is a separate native `LogPhaseTime=true` diagnosis.
At probe 64, broad-query hierarchy tag admission changes from
113.468/154.468 us (floor 1/16) to 108.116/108.453 us. These are medians of
per-process mean phase times, excluding warm-up; instrumented QPS is not
substituted into the performance table. The main admission arrays remain
24 bytes/head plus the final eight-byte trailer for this two-slot/two-attribute
configuration, the extra-tag pool is unchanged, and the presence bitmap is
released. Measured process-peak RSS differences are about -0.35..+0.40 MiB;
these are not steady-state RSS measurements.

The old `final/` results, source manifest, and figure links are preserved.
Versioned R performance, speedup, and admission PNG/PDF figures are under
`contiguous_full_grid_ab/figures/lookup_v2_*`, with separate `lookup_v2_*`
links in the fixture's `figures/`. Rejected intermediate binaries and
measurements are retained as diagnostics, not substituted into the publication.
The replay helpers and plotting source are copied to the diagnostic `replay/`
directory. With `run` set to the measured run above, reproduce into fresh paths:

```bash
diag="$run/diagnostics/support_lookup_prefetch"
python3 -B "$diag/replay/support_lookup_ab.py" \
  --run-dir "$run" --output-dir "$diag/reproduce_full_grid_v2" \
  --before "$diag/spannaclbench.before" --after "$diag/spannaclbench.contiguous" \
  --trials 4 --workloads unfilter,broad_tag,bin13_20,bin21_50,bin51_200 \
  --nprobes 16,32,64,128
python3 -B "$diag/replay/support_lookup_ab.py" \
  --run-dir "$run" --output-dir "$diag/reproduce_phase_v2" \
  --before "$diag/spannaclbench.before" --after "$diag/spannaclbench.contiguous" \
  --trials 4 --workloads broad_tag,bin13_20 --nprobes 64 --profile
Rscript Tools/benchmarks/plot_limited_tag_support_lookup.R \
  "$diag/reproduce_full_grid_v2/summary.csv" \
  "$diag/reproduce_phase_v2/summary.csv" \
  "$diag/reproduce_full_grid_v2/figures/lookup_v2"
```

### Shared H/O placement and metadata

The normal BKT candidate search first builds the complete original SPANN
placement `O`. Base support reads only its retained posting prefixes after RNG
and posting cuts; neither rejected search candidates nor discarded scratch
suffixes contribute. A retained member beyond search rank two can contribute.
Support construction runs no additional nearest-head search and needs only
O(slots) aggregation scratch space, not an N-times-candidate-count buffer.
Distance ordering of the O scratch records is not required or changed.
With `BuildH1Graph=0`, spatial placement uses the hierarchy
callback to reach H1; the graphless H1 catalog is not a standalone search graph.
The original search and RNG geometry are unchanged. Non-head vectors are then
assigned only to heads supporting their attribute, using constrained BKT search
and RNG pruning for up to eight `H` replicas. The single STM1 v3 posting is
`H | O`: predicates safely anchored on the configured limited-tag key scan
`H`, while unfiltered and other predicates read only the complete `O` suffix.
This is a posting-membership choice after the same spatial traversal, not a
query route.
Cross-region overlap is intentional. Limited-tag builds require
`TailReplicaCount=0`; `O` is already self-contained, so no supplemental
unfilter-tail replicas are built.

`LimitedTagVoteHeadCount` has been removed from every active interface:
explicit INI/API values fail even when set to the old default. For immutable
old indexes, make a copy and remove that obsolete INI entry before loading
with this reader; do not change the support binary or its generation.
The packed LTS v1/v2/v3/v4 layouts remain unchanged; new expansion uses V5's
requirement-derived invariant as described above. The former vote-count
word in v3/v4/v5 is legacy provenance: **positive** preserves an old pre-RNG
candidate source; **zero** identifies retained-O construction. New support
hashes include a retained-source discriminator, and new build generations
also include that discriminator. Loading/exporting legacy v3/v4 preserves the
positive word and original body hash; it never relabels old support as
retained-O. The existing v1/v2 reader still requires authentic vector counts
before export to v3, retaining legacy provenance. Older readers reject new
zero-marker tables instead of interpreting them as vote-built indexes.
Rebuilding is required to change an old index's candidate semantics.
V8 head metadata automatically supplies separate categorical and
quantized-numeric posting signatures for `H` and `O`; the selected posting
region may consult its matching metadata before I/O. Numeric signatures use
256 uniform buckets per numeric column and remain conservative at bucket
boundaries, with exact record-level DNF evaluation removing false positives.
V8 binds the complete metadata blob to the constrained-posting generation and
a content fingerprint, records whether own-tag and hierarchical posting masks
and the tail-signature layout are actually available, and binds numeric signatures to the NUM2 v2
numeric-domain content fingerprint. Legacy or mismatched metadata falls back
to validated PBS3 categorical masks when available; missing masks and numeric
metadata fail open rather than rejecting a potentially matching posting. The PBS3
`signatures_bitmask.bin` sidecar likewise binds `H`/`O` masks to the posting
generation and body fingerprint, rejects legacy tail-bearing PBS2 files, and
is published atomically.
Monolithic and graphless H1 metadata reuse the existing local-head-to-global-VID
map; only legacy bundle-backed slim roots resolve IDs from bundle maps.
Legacy slim roots initialize that bundle runtime before binding metadata-only
samples, matching the native build path.
Generation-bound metadata cannot be saved with unresolved VIDs, and signature
generation propagates mapping failures instead of publishing an incomplete blob.
At load, the validated support sidecar builds the metadata used for H-region
membership and exact result admission. Graph traversal remains distance-only.
It never retries or raises `MaxCheck` to fill a predicate-specific posting
quota, and it never enumerates a tag-to-H1 list. Empty physical postings do not
count as SSD results, but a selected matching head can still contribute its
own record through native handling. `HeadNavigationMode` is retired. Every
query uses native H1 navigation; optional signed posting adjacency does not
create an independent upper ANN search.

Static posting scans and iterators mark each VID in the existing workspace
deduper before evaluating its predicate, so rejected replicas are skipped too.
No separate predicate cache is needed. Match counters now describe first-visit
matches and contributing postings, not matching replica occurrences.
`spannaclbench` reports these as `matched_vectors_per_query` and
`contributing_postings_per_query`; `dedup_skipped_vectors_per_query` includes
duplicates of rejected VIDs. `unique_scanned_vectors_per_query` and `match_rate`
use first-visited records, while `scanned_occurrence_to_unique_ratio` measures
all scanned replicas. The legacy `FalsePositivePostings()` counter now includes
postings containing only already-visited matches.

`[SelectHead] HierarchyLevels` counts H1. H1 is the sole query graph;
H2 and higher levels are representative/CSR/signature catalogs. Filtered H1
results use native result-only admission: nonmatching graph nodes still
participate in distance navigation. The query retains native `MaxCheck`,
`InternalResultNum`, tree continuation and final exact-record filtering.
Upper representatives do not enter final top-k.

`[SearchSSDIndex] EnablePostingNavigation=true` adds the sparse adjacency
supplier; false is the library default. The native H1 graph completes first.
Only an underfilled head result set with remaining
`MaxCheck + PostingAdditionalMaxCheck` budget starts one supplemental phase.
`PostingAnchorCount` bounds nearest already-scored anchors, including negatives;
their owners are merged and signature-qualified H2+ postings compete in one
representative-distance frontier. Each selected posting exposes its owners
once; complete upper rows add children to that same frontier. Rejected rows
may expose owners without representative or CSR access. The phase preserves
original heads and refines a bounded heap for the missing slots, replacing
worse supplementary heads even after it fills. Only checked-leaf budget or
reachable-posting exhaustion are hard work limits. Native-style frontier
convergence may stop earlier: the H2 representative-distance pool has capacity
`max(effective MaxCheck / 16, head-result capacity)`, and the nearest pending
representative must remain within that pool. Coarse H3+ nodes share expansion
priority without consuming H2 convergence slots. This approximate ANN criterion
also handles predicates with fewer eligible heads than nprobe. It is not a
proof that representative distances lower-bound all members or guarantee recall.
The old in-row percentage and `PostingMinCandidates` policies are retired.

Signature rejection precedes representative/member access. Within an admitted
H2 row, the compact visited/match lookup checks predicates before vector work.
Fresh rejected members are marked visited and skipped without prefetch, distance
or checked-leaf cost; replicas reuse that rejection. A rejected collapsed
representative still checks aliases, scoring its shared vector once only when
a live matching alias needs admission. Ordinary graph negatives remain scored
bridges. The post-graph phase does not enqueue candidates or restart the graph;
its terminal rejection visits must never be used for further graph traversal.
Frozen all-scored experiments keep their original results and source snapshots.

The entire selected auxiliary CSR row completes, even across the remaining
H1 budget. Native checked leaves do not count all tree pivots, upper distances
or CSR/owner references: report those separately, not as a hard total-distance
bound. Upper query state contains only touched IDs, and discovered candidates
use a distance-ordered cache rather than repeatedly scanning the whole list.
This avoids catalog-sized per-query allocation/clearing; it does not bound
owner fanout or the maximum CSR row length.

H1 selected heads and SSD records retain native global-VID translation,
liveness, exact predicates and deduplication. Matching heads with empty SSD
postings still use the native selected-head record path. There is no
supplementary H1 own-point heap or eager own/alias qualification for the bit.
Attribute support and signatures are may-match summaries; final numeric/DNF
checks remain exact. Numeric and unanchored DNF requests now use authenticated
O-region signatures for the same H1 result-admission/match bit. H/O selection
does not depend on whether auxiliary navigation is enabled. Own values are
merged once; upper H/O unions are refreshed at build/load or explicit metadata
replacement, never by a catalog-wide query scan. Missing or stale metadata is
logged as conservative unknown. See `native_postfilter/README.md` under
`hierarchical_shortcut_native` for the lifecycle and shared-memory costs.

Fresh builds persist H1 navigation and upper catalogs. Temporary per-layer
native ANN construction, using `[BuildHead]`, preserves the original
candidate-search/RNG replica assignment and is released after assignment;
it is not an upper query graph. Old graphless H1 indexes require explicit
materialization into a new destination before search, without a silent upper
ANN fallback. Materialization does not repair historical posting-assignment
defects; those require a separately authorized rebuild.

The previous graphless SIFT1M Zipf200 index is
`datasets/sift1m_zipf200_sparse193_numeric/index_limited_tag_h3_placement_fixed`.
The full native rebuild changed only the output and temporary locations in the
build INI; the old `index_limited_tag_h3_independent` index is retained. Pure-H
occurrences increased from 4,228,353 to 4,633,041 (+9.57%), with all 839,909
non-head VIDs still covered and no H1 head records in SSD. Sampling catalogs,
IDs, and the top graph are byte-identical; the native BKT tree and CSR artifacts
were rebuilt, so this is an end-to-end rebuild comparison, not an
architecture-only control.

Historical placement and graph-signature A/B measurements remain under the
dataset's `hierarchy_audit_checks/` directory as frozen provenance. They were
produced by retired query semantics and must not be replayed or interpreted as
current routing modes. Current comparisons use main H1 post-filter with
`EnablePostingNavigation` disabled/enabled, matching native `InternalResultNum`,
`MaxCheck`, SSD page limits, inputs, IO and query windows.

### Independent hierarchy-layer storage

Legacy unquantized STATIC graphless hierarchies use `head_metaonly.bin` version 3.
H1 and every intermediate layer own complete, contiguous native vector
catalogs, including sampled/promoted rows. Legacy top-layer vectors may be read from their old native-index vector file,
but its graph/tree is not loaded. H1 alone owns canonical result VIDs and V8
attributes; upper layers retain spatial CSR and signatures, with obsolete
upper attribute artifacts removed. Fresh main builds always retain H1
navigation and independent upper catalogs. Fresh `BuildH1Graph` and
`CompactHierarchyVectors` settings are rejected.

The V3 descriptor keeps the 28-byte prefix and 8-byte canonical-H1 fingerprint.
Loading checks exact catalog sizes, H1 V8 generation/content/VID integrity,
byte-exact sampled-vector correspondence at every adjacent layer, and
conservative descendant-signature coverage. Thus separately stored upper
vectors cannot silently diverge from their sampled lower rows.
V8 loading configures the metadata layout and adopts the content-verified
payload directly, without allocating a second full-size zero-filled blob.
Generation, layout, content, canonical-VID, and hierarchy-signature checks
remain intact; existing index files do not need rebuilding for this loader change.

An existing V1/V2 graphless index can be materialized into a new output root
without rebuilding its graph or SSD postings:

```bash
Release/spannbuilder -c <native.ini> --materialize-hierarchy \
  --output-index-dir <new-root>
```

This offline command reads the source `[Base] IndexDirectory` and `[Tags] Tenant`;
it does not read the original source dataset. The output must not exist or
overlap the source, and the source manager manifest must identify the selected
tenant and its external mapping. Conversion stages complete catalogs, reloads
and validates them, then publishes the complete manager root without replacement.
Source files, H1 V8 bytes, sampling IDs, and CSR member/offset arrays remain
unchanged. Conversion rebuilds upper signatures for H1 own keys plus the H region,
including their generation bindings, without retaining irrelevant O-only bits.
In-process callers can use
`ISPANNIndex::MaterializeHierarchyVectors(destinationTenantDirectory)` while
searches are quiesced.

V1/V2 reading and explicit `--compact-hierarchy` remain legacy compatibility
operations; they do not restore an upper ANN query path. Signature domains
remain authenticated provenance, while signature masks can conservatively
prune auxiliary postings. V3 still requires complete generation-bound metadata.

Legacy graphless STATIC directory exports copy the persisted artifacts into a private
staging directory and use native loading to validate them before publication;
they do not recursively serialize the dummy KDT or logical views over the source.
In-place saves validate a hard-linked staging view and replace only the INI.
The loader resolves the physical root descriptor against the directory actually
being loaded, not an obsolete build/export directory embedded in the INI.

The full main-code campaign records H1/upper distances, signature/owner/member
work and allocations using a separate `SPTAG_QUERY_WORK_DIAGNOSTICS` build.
Those counters are compiled out of ordinary throughput measurements, and the
diagnostic replay must match normal outputs and SSD work. Old `h2*` top-graph
phase measurements are historical evidence, not current query stages.

The retired EST4 sidecar and its split/merge serving route have been removed.
Rare labels use regular H/O support and the ordinary exact-filter path.
`HierarchySignatureMinSelectivity`/`HierarchySignatureMaxSelectivity` and their
`SecondLevel*` aliases are removed together. New builds may still persist the
complete categorical domain `(0,1]` for artifact compatibility and audit.
Legacy V3 domain endpoints remain authenticated fields in the unchanged
binary format (V2 implies full coverage). Load/repair/save preserve those
fields; old INI endpoints are read-only provenance, not overrides. Query
navigation uses signature masks for H1 result admission and auxiliary posting
regions, never to block ordinary H1 neighbors. Numeric-only and unanchored
predicates use authenticated region summaries and native budgets, with exact
final filtering. Unknown legacy numeric domains conservatively skip numeric
pruning with a warning.
They do not select an alternative upper-graph query path. Native H/O membership
continues to select O when H-region membership is not valid.
The dataset generators derive their rare-label recipe from active native budgets;
this is not a separate serving policy. Before
heads exist they use `expectedHeadCount = native input count * SelectHead.Ratio`; the
manifest records that assumption. SIFT1M therefore generates 193 extreme
vectors from `Ratio=.16`, two slots, and `InternalResultNum=62`.

Generate the reproducible Zipf-200 attribute and build with:

```bash
python3 Tools/benchmarks/generate_sift1m_zipf_attribute.py \
  --output-dir /datadisk/yfcc_fast/sptag_sift1m_zipf200_sparse_numeric \
  --extreme-tag-coverage --numeric-column \
  --config Tools/benchmarks/build_spann_attr_sift1m_zipf200_limited_tag.ini
python3 Tools/benchmarks/generate_sift1m_sparse_numeric_workloads.py \
  --attributes /datadisk/yfcc_fast/sptag_sift1m_zipf200_sparse_numeric/sift1m_zipf200_sparse193_numeric_attrs.npy \
  --output-dir /datadisk/yfcc_fast/sptag_sift1m_zipf200_sparse_numeric/query
Release/spannbuilder \
  -c Tools/benchmarks/build_spann_attr_sift1m_zipf200_limited_tag.ini
```

## SIFT1B Limited-Tag Recommendation

### Five-scenario adaptive SPTAG / PipeANN Recall-QPS figures

The explicit `--selectivity` mode of `plot_sift1b_official.R` consumes completed
ordinary measurements only. It neither runs benchmarks nor regenerates truth.
The historical one-argument invocation remains unchanged: six scenarios,
`SPANN`/`PipeANN`, and separate figures under the run directory's `plots/`.
For the current five-scenario comparison, use a **new, nonexistent output
directory**:

```bash
Rscript Tools/benchmarks/plot_sift1b_official.R RUN_DIRECTORY NEW_OUTPUT --selectivity
```

`RUN_DIRECTORY` must contain `summary.csv` and `plot_registration.json`.
The required CSV header is:

```csv
scenario,engine,L,queries,repeats,threads,cpu_nodes,recall,recall_min,recall_max,qps,qps_min,qps_max,candidate_count,selectivity,predicate
```

There is one summary row per `(scenario, engine, L)`. Every registered point
must contain all of that engine's declared repetitions. No partial mode,
missing curves, duplicated points, warmups or diagnostic timings are accepted.
`recall` and its range are fractions in `[0,1]`; all QPS/range values must be
positive and the reported point must lie within its range. A one-repetition
point must have collapsed ranges. The renderer uses the supplied statistics
verbatim: it does not average already summarized points or interpolate recall.
The native producer remains responsible for authentic per-repetition evidence;
the renderer checks the CSV's repetition counts against the frozen declaration.

The registered scenario order is exactly `unfilter`, `broad_tag`, `medium_tag`,
`sel_01pct`, `mixed_dnf`. Their current actual fractions are `1`,
`0.170124930`, `0.017012493`, `0.001000735`, `0.000425710`, respectively.
Mixed DNF is a separate workload, not the categorical 0.1% endpoint.
Numeric-only and extreme-sparse records are rejected, not silently discarded.
Every panel displays its registered actual density and eligible count;
`unfilter` explicitly says **100% (no filter)**.

`plot_registration.json` has the following contract:

| Field | Requirement |
| --- | --- |
| `schema_version`, `dataset`, `corpus_count` | `1`, `"SIFT1B"`, `1000000000` |
| `comparison` | `"fresh_paired"`, `"reused_pipeann_baseline"`, or `"historical"`; never inferred |
| `series` | Optional legacy-compatible declaration: `"adaptive_only"` requires exactly adaptive/PipeANN; `"with_h1_control"` requires all three engines |
| `scenarios` | The five ordered IDs above |
| `scenario_metadata` | Exactly those IDs, each with scalar `title`, `predicate`, integer `candidate_count`, and fractional `selectivity` |
| `engines` | Objects keyed by `SPTAG_adaptive` and `PipeANN`, optionally `SPTAG_H1`; no implicit or absent legend entries |
| `caption_note` | Optional nonempty caption text; synthetic tests visibly identify themselves here |

Each scenario's predicate, count and fraction must match every CSV row in that
panel. Its fraction must equal `candidate_count / corpus_count` within `1e-12`.
Do not round actual densities to nominal 10%, 1% or 0.1% labels.

Each engine object declares the fields below. The following is a **schema
example, not a campaign registration or performance result**; use the actual
frozen grid, controls and authenticated identities rather than copying them
into an existing campaign:

```json
{
  "native_control": "nprobe",
  "L": [16, 24, 48, 96, 192, 384, 768],
  "repeats": 2,
  "queries": 1000,
  "threads": 1,
  "cpu_nodes": "3",
  "memory_nodes": "3",
  "query_cohort_id": "REPLACE_WITH_FROZEN_ORDERED_QUERY_COHORT_ID",
  "index_id": "REPLACE_WITH_FROZEN_INDEX_ID",
  "runtime_id": "REPLACE_WITH_FROZEN_RUNTIME_ID",
  "source_date": "2026-09-24",
  "io_mode": "buffered",
  "qps_aggregation": "median",
  "search_policy": "predicate_first_adaptive_global_posting_frontier",
  "controls": {
    "graph_maxcheck": 2048,
    "posting_additional_maxcheck": 2048,
    "posting_anchor_count": 8,
    "search_posting_page_limit": 3,
    "enable_posting_navigation": true
  }
}
```

The earlier three-series SPTAG campaign uses the same compact SIFT1B index with
120,040,156 H1 heads and the frozen adaptive runtime identified by `46af429...`;
record full authenticated index/runtime identities, not just that prefix.
Its nprobe grid is `[16, 24, 48, 96, 192, 384, 768]`, with two ordinary passes,
reversed case order on the second pass, and ascending sweeps within each case.
Each point has 1,000 warmup, measured and replay queries, one query thread,
and CPU/memory NUMA 3. MaxCheck is 2048, posting extra is 2048, anchors are 8
and the search posting page limit is 3. The same-index/runtime H1-only control
uses MaxCheck 2048 with posting disabled. It is this project's baseline,
**not an unmodified Microsoft SPTAG build**.

These are native measurement protocol facts, not settings inferred or executed
by the renderer. Preserve the existing runner's case protocol as additional
per-engine provenance (for example `native_case_protocol`); engine metadata is
retained verbatim. Warmup/replay counts and reversed execution order cannot be
proved from aggregated CSV rows alone. The supplied median/min/max coordinates
are retained without re-aggregation.

`L` contains at least two strictly increasing native integer controls, each at
least ten. Grids and positive integer repetition counts may differ by engine
but must be complete in every panel; captions list each engine's grid and
repetition count. `qps_aggregation` is explicitly `arithmetic_mean` or `median`.
CPU/memory NUMA and cohort/index/runtime identities are nonempty strings.
Use frozen content identities for the ordered query cohort, runtime and index,
not a guessed common label. The common cohort is the same ordered query vectors,
not the different filtered truth files for each scenario.

For `SPTAG_adaptive`, `native_control` is `nprobe`, I/O is `buffered`, and
`search_policy` is exactly `predicate_first_adaptive_global_posting_frontier`.
For optional `SPTAG_H1`, use `nprobe`, `buffered`, `h1_only`, and
`enable_posting_navigation: false`. Both SPTAG objects declare valid native
`graph_maxcheck`, `posting_additional_maxcheck` and `posting_anchor_count`.
The H1 legend derives MaxCheck from its declaration. This prevents the earlier
full-budget failure policy from being relabeled as the current adaptive method.
Captions identify H1-only as the project baseline, never a vanilla Microsoft
build; they claim shared index/runtime only when those identities actually match.
`PipeANN` uses `native_control: "searchL"`, `io_mode: "direct"`, an explicit
nonempty `search_policy` (for example `official_search_disk_index`), and a
nonempty flat `controls` object describing its actual fixed settings, such as
pipeline and unfiltered/filtered `mem_L`. All fixed controls are retained in
metadata and printed in captions. **SPTAG nprobe and PipeANN searchL are not
equal work**, and buffered versus direct I/O is not a matched-I/O algorithm-only
comparison.

`fresh_paired` requires matching query counts, ordered `query_cohort_id`, query
threads, CPU NUMA and memory NUMA across engines. The optional H1 control must
also share SPTAG's frozen index and runtime identities. `historical` permits
different cohort/execution declarations, but every engine must provide a valid
`source_date` (`YYYY-MM-DD`) and explicit CPU/memory NUMA strings. Figures then
say **Historical comparison - not a fresh paired run** and show each source
date and placement. Predicate/count/density matching still applies.
`reused_pipeann_baseline` retains the same strict cohort and placement matching,
requires source dates and explicit per-engine `measurement_reused` flags
(PipeANN true, SPTAG false), and labels the figures as an SPTAG rerun with a
preserved PipeANN baseline, not a fresh paired measurement.

Optional CSV provenance columns (`query_cohort_id`, `memory_nodes`, `io_mode`,
`index_id`, `runtime_id`, `search_policy`, `qps_aggregation`, `native_control`,
`source_date`, `corpus_count`) are checked against the registration when
present. Optional `diagnostic` must be false and `stage` must be `measured`.
No cohort or placement is inferred from QPS or a directory name.

Outputs are combined `recall_qps.png`/`.pdf`, five
`SCENARIO_recall_qps.png`/`.pdf` pairs, `plotted_points.csv`,
`plot_metadata.json`, byte-identical `source_summary.csv` and
`source_registration.json`, plus `hash_manifest.csv`. The coordinate CSV
preserves every input field, including numeric text precision; only row order
changes to registered scenario/engine/native-control order. Recall regressions
remain connected in that order, not sorted by recall. Every figure uses the
full Recall@10 axis `[0,1]`, log-QPS, common engine styles, and observed recall/QPS
repetition ranges rather than confidence intervals.

Metadata records engine controls, pairing declarations, axes, and MD5 hashes
of both input files and generated figures/coordinates/snapshots. The hash
manifest also hashes `plot_metadata.json`; it excludes only itself. Inputs are
checked for mutation during rendering, and failed output is removed rather than
published. Existing output directories are never overwritten.

#### Assembling the completed native campaigns

`assemble_sift1b_selectivity.py` is a bounded, read-only input assembler, not a
measurement runner. Only the parent should invoke it, **after declaring both
fresh campaigns complete**:

```bash
B=/mnt/nvme/baotonglu/mocheng/datasets/sift1b/comparisons/main_posting_adaptive_curves_20260924
P=/mnt/nvme/baotonglu/mocheng/datasets/sift1b/comparisons/pipeann_adaptive_curves_20260924_v2
python3 -B Tools/benchmarks/assemble_sift1b_selectivity.py "$B" "$P" --completed
```

For the final adaptive-only rerun, the producer must explicitly register
`variants: ["postgraph_extra"]`, ten cases and 70 ordinary measured points.
The controlled-ascent campaign declares `implementation_revision:
"controlled_ascent"` and two native processes: its 70-point ordinary process
plus a separate six-point, first32 counter-only process. The explicit
`points_by_kind` and `cases_by_kind` separate these grids; counters are never
publication throughput. Either completed ordinary or subsequent completed
diagnostic stage status is accepted, but the ordinary log, exit status and
all 70 measured payloads must independently pass the full checks.
The final combined `state: "complete"` also requires matching
`ordinary-completion.json`/`after-normal-status.json` evidence binding the
70-point report hash and native command. A reported performance regression
does not suppress valid measured curves or turn a partial run into completion.
No H1-only performance matrix is required or silently dropped. Reuse the
authenticated, unchanged PipeANN baseline only with explicit flags and a new
publication directory outside both source campaigns:

```bash
python3 -B Tools/benchmarks/assemble_sift1b_selectivity.py \
  NEW_ADAPTIVE_CAMPAIGN COMPLETED_PIPEANN_BASELINE --completed --adaptive-only \
  --reuse-pipeann-baseline --output-directory NEW_PUBLICATION/plot_input
Rscript Tools/benchmarks/plot_sift1b_official.R \
  NEW_PUBLICATION/plot_input NEW_PUBLICATION/plots_selectivity --selectivity
```

This produces exactly 100 coordinates from 70 adaptive and 130 PipeANN measured
repetitions, with only adaptive/PipeANN series in every panel. The baseline's
original SPTAG campaign association is retained in provenance, while the
current query payload, predicates, truth, native controls and execution
placement are independently matched. Existing baseline `plot_input` and
figures remain untouched. The default assembler still requires the complete
older 140-point SPTAG matrix and produces its original 135 coordinates.

The original `pipeann_adaptive_curves_20260924` attempt is **failed and not
plottable**: unfiltered execution reported EBADF errors despite exit zero, and
broad categorical execution exited 132. Preserve those logs. A partial
`unfilter/results.json` is not campaign completion. Use only a fresh replacement
path approved by the parent; the exporter takes both campaign paths through its
CLI and requires the replacement's registered INI to name that same output
directory and the existing SPTAG campaign. It does not require a particular
PipeANN directory or INI basename.

The corrected `_v2` campaign uses `benchmark_v2.ini` and the parent-verified
`readonly_descriptor_adaptation` registration. **Running is not complete**:
do not invoke assembly or rendering until all 130 measured points have completed
successfully and the parent approves them. The adaptation fixes query-only
`O_RDWR` opens to `O_RDONLY` under `READ_ONLY_TESTS`, with explicit open-failure
handling. It does not change ANN/search/filter/distance code, PQ, pipeline 32,
uring, tcmalloc or `NO_MAPPING`; failed original-attempt timings remain excluded.

The exporter authenticates the adaptation manifest against both the registered
INI and registration hash, binds its two binary identities and original-toolchain
lineage, and checks its declared file-access-only scope, unchanged flags/backend/
allocator and completed validation evidence. It records the full manifest in
`assembly_manifest.json`, retains the registered adaptation record and scope in
the PipeANN engine metadata, and emits a visible `caption_note` disclosing the
file-access-only change and unchanged ANN behavior. The existing renderer already
prints that note on combined and per-scenario figures. The exporter does **not**
invoke the toolchain verifier, rerun its native fixtures/smokes, or use their
first-16-query functional timings as curve points.

The completion flag does not bypass validation. SPTAG must report completed
`after/normal`; PipeANN must report five completed native processes and 130
measured points. Missing, running, failed, partial or reordered inputs fail
without producing a usable assembly. The exporter requires exactly 140 SPTAG
ordinary records and 260 PipeANN native records (130 warmups, 130 measured).
It reuses existing helpers to check the registered job/control order and
reparse every PipeANN `RESULT` line, then compares all scenario and campaign
reports. Only the two registered measured repetitions enter each median/min/max.
The current controller's native-error guard also rejects error/fatal/bad-file-
descriptor diagnostics even when valid-looking `RESULT` lines exist. Controller
and frozen launcher hashes are recorded separately; unrelated controller failure
handling may change, but the parser/input helpers and registered jobs must match.
Signalled/nonzero-exit resource reports are rejected, including GNU-time reports
that also contain a misleading `Exit status: 0`.

SPTAG `graph` maps to `SPTAG_H1`; `postgraph_extra` maps to `SPTAG_adaptive`.
Both retain the same full authorized runtime hash and a canonical registered
index-inventory identity. The PipeANN runtime identity combines its two verified
binary hashes. Query identity hashes the **ordered logical UInt8 payload**:
the SPTAG `.npy` and PipeANN headered `query.u8bin` must contain identical
`1000 x 128` bytes despite different container hashes. Each PipeANN truth input
must be an exact `1000 x 10` IDs-only uint32 matrix with its eight-byte header,
matching the authenticated SPTAG int64 truth IDs. Predicates, counts and actual
densities must agree with the authenticated workload and fixed filter profile.

The assembler checks SPTAG's native case boundaries, controls, warmup/measured/
replay counts, raw payload hashes, GT-ID recall, returned-result/SSD-work
summaries and deterministic IDs/distances/SSD-work parity between repetitions.
Unfiltered H1/adaptive payloads must also agree. It preserves whole-window
native QPS, **not** inverse mean per-query latency: the outer measured window
includes loop/result-release overhead absent from individual latency samples.
Latency bounds and native nearest-rank percentiles are checked separately.
No attribute/base-vector rescanning or new exact-distance computation occurs;
the completed native campaign remains the source of that semantic evidence.

Source dates come from completed, successful GNU-time resource files bound to
their registered commands, using UTC file-completion timestamps, not directory
names or the assembly date. The declared comparison is `fresh_paired`, while
retaining SPTAG buffered versus PipeANN direct I/O and their different native
warmup/replay protocols. The native report's legacy `timing_accepted` field is
preserved as source evidence, not used to mix stages or discard ordinary runs.

The only output directory is fresh `P/plot_input`, containing `summary.csv`
(135 points), renderer-compatible `plot_registration.json`, and
`assembly_manifest.json`. The manifest includes source/raw SHA-256 hashes,
completion evidence, native commands/case/job protocols, all 270 measured
records, excluded-warmup counts and output hashes. Registered index/base files
are checked by identity (`stat`) only, with no directory inventory walk or
large payload hash; other content reads are capped at 128 MiB per file.
Existing assemblies are never overwritten. Source changes during assembly
fail and remove only the newly created output files.

The assembler never executes native binaries or invokes R. After separately
reviewing the completed assembly, the parent may render to the intended new
directory:

```bash
Rscript Tools/benchmarks/plot_sift1b_official.R "$P/plot_input" "$P/plots_selectivity" --selectivity
```

### Fixed PipeANN / SPANN comparison profile

The repository-owned comparison configuration is
`configs/sift1b_official/benchmark.ini`, with checked-in native SPANN search
INIs, native PipeANN filter JSON files and `pipeann_readonly.cmake` beside it.
Use `run_sift1b_official.py`; do not synthesize a new configuration in an
experiment directory or pass data/search overrides through the environment.
Run directories contain byte-for-byte configuration snapshots, not rendered
or patched configurations.

```bash
python3 Tools/benchmarks/run_sift1b_official.py check-config
python3 Tools/benchmarks/run_sift1b_official.py build-tools
python3 Tools/benchmarks/run_sift1b_official.py prepare-inputs
python3 Tools/benchmarks/run_sift1b_official.py prepare-memory
python3 Tools/benchmarks/run_sift1b_official.py check-ready
python3 Tools/benchmarks/run_sift1b_official.py run
```

The PipeANN profile follows its `docs/cpp-interface.md` and the
`scripts/tests-pipeann/fig13.sh` / `eval_f.sh` latency recipe: read-only
`READ_ONLY_TESTS` plus `NO_MAPPING`, `io_uring`, PQ32, pipeline width 32,
mode 2, top-10 and one query thread. Unfiltered search requires a compatible
1% memory-entry index (`R=32`, `L=64`, `alpha=1.2`) and `mem_L=10`;
filtered search keeps native `auto` and `mem_L=0`. Missing memory files are
an error, never permission to downgrade unfiltered search to zero.
The official low-budget grid starts at 10 and includes 15 and 20.

The configured NUMA placement is a host policy, not a claim that the whole
process uses one CPU. Do not confine the query thread, SQPOLL and helper work
to a single CPU. Build thread counts are adapted to this host and are fixed
in the same INI. Native toolchain preparation is isolated from the PipeANN
working tree; memory preparation adds a separate alias prefix and does not
rebuild or replace the billion-vector SSD graph.

This host lacks the system tcmalloc development package. The fixed CMake
profile links and locates the unmodified Ubuntu `gperftools` 2.9.1-0ubuntu3
packages under `datasets/sift1b/toolchains/dependencies/gperftools`, rather
than disabling the allocator or requiring a global library override.
The downloaded `.deb` files and their SHA-256 list are retained in `packages/`;
the toolchain manifest records the actual resolved runtime-library hashes.
On this host the private prefix was populated with these commands:

```bash
mkdir -p /mnt/nvme/baotonglu/mocheng/datasets/sift1b/toolchains/dependencies/gperftools/packages
cd /mnt/nvme/baotonglu/mocheng/datasets/sift1b/toolchains/dependencies/gperftools/packages
apt-get download libgoogle-perftools-dev=2.9.1-0ubuntu3 libgoogle-perftools4=2.9.1-0ubuntu3 libtcmalloc-minimal4=2.9.1-0ubuntu3
dpkg-deb --extract libgoogle-perftools-dev_2.9.1-0ubuntu3_amd64.deb ..
dpkg-deb --extract libgoogle-perftools4_2.9.1-0ubuntu3_amd64.deb ..
dpkg-deb --extract libtcmalloc-minimal4_2.9.1-0ubuntu3_amd64.deb ..
```

Query preparation converts the existing matching 1K query/GT suite into
native data files only. Filter expressions and budgets remain in the
checked-in files; relative filter bindings resolve in the configured prepared
data directory. Both engines warm the complete query set before each timed
pass. The fixed SPANN sweep differs only in `InternalResultNum`; empty and
underfilled results remain in recall. Existing full saved-index audit evidence
is reused only when its index file identities still match.

This remains a 1K-query experiment, not the separate formal 10K protocol.
The figures disclose buffered SPANN versus direct-I/O PipeANN; do not present
them as a matched-I/O algorithm-only comparison. Historical ad-hoc runs,
including `pipeann_spann_h5_20260913T080606Z`, are not the recommended PipeANN
pure-search baseline and must not replace this entrypoint.

For a SPANN placement/page-size regression, the fixed diagnostic entrypoint is
`python3 Tools/benchmarks/run_spann_memory_diagnostics.py run`, using
`configs/sift1b_spann_memory/diagnostic.ini`. It reuses the same frozen binary,
index, query cohort, warmup and repetition count; the native L96 INI adds only
phase timing. Cases run serially: local NUMA/default THP, the same placement
with process-local THP disabled, the historical CPU48/interleaved placement,
then the local/default case again. This is diagnostic data, not replacement
benchmark curves. The driver records native phase timings and its own child's
CPU, NUMA page counts, RSS, `AnonHugePages` and faults. It never changes global
THP settings, drops the host page cache, or changes other users' processes.

For H5..H1 per-layer costs, use the same entrypoint with
`--config Tools/benchmarks/configs/sift1b_spann_layers/diagnostic_L0064.ini`
or `diagnostic_L0096.ini` in that directory. Each runs one canonical search
point, adding only native `LogPhaseTime` and `LogPathStats`. Per-layer means
exclude every warmup block and retain the native distinction between graph
checked counts and downward distance evaluations. Vector-row lookups, CSR
row expansions and visited-bitmap checks are logical access counts, not
hardware cache misses or physical DRAM transactions.

The `diagnostic_access_L0064.ini` and `diagnostic_access_L0096.ini` plans pin
a separate instrumented executable. Build it with the checked-in
`access_profile.cmake`; its output directories are outside the production
`Release` tree. H5 counters cover all ordinary BKT search distance callbacks,
including tree-centroid calculations omitted from `graph_checked`, graph
queue row visits, visited-table calls and BKT root/queue node visits. They
exclude prefetch-only lookups and are not instruction-level load/store counts.
The scoped thread-local collector is enabled only around a profiled top-layer
search with path logging; it never changes admission, traversal or budgets.
Keep original-binary timing results separate from these counting runs.

For implementation/beam A/B, run
`python3 Tools/benchmarks/run_spann_latency_ab.py run`. Its fixed
`configs/sift1b_spann_latency/experiment.ini` runs all variants and repeats
inside one native process, loading the billion-vector index only once.
Implementation variants must preserve every returned ID, work counter and
parent budget. Explicit routing variants report lost and gained groundtruth
hits separately; an unchanged aggregate recall does not establish identical
results. The native `--dump-results` flag emits timed-query IDs only after
the timer stops. `HierarchyDedupMode` selects Bitmap, LocalHash or Auto;
Auto uses a candidate-sized hash only when its estimated storage is smaller
than the layer bitmap. `HierarchyRoutingBudgets` is Auto for the original
ratio-based rule, or a top-to-bottom list of parent beams (H5,H4,H3,H2 for H5).
It never changes the H1 posting target or depends on a query predicate.
Do not report a beam-change speedup as a same-work implementation speedup.
`--config Tools/benchmarks/configs/sift1b_spann_latency/confirm.ini` repeats
the baseline/Auto comparison and the declared beam variants with Rolling16
throughout. Preserve failed-run status if only its completed measurements
are recovered for analysis; confirmation requires a normal native exit.

`--config Tools/benchmarks/configs/sift1b_spann_latency/recall90.ini` fixes
the canonical L128 point (90.26% recall in the reference cohort). It pairs
Bitmap/Auto phase profiles with explicit `Kind=throughput` variants whose
phase/path logging is disabled. All four retain identical search budgets and
must return identical IDs; throughput variants deliberately have no phase means.
Process samples also retain `/proc/PID/io` counters, and final usage includes
filesystem input/output block counts. These include loading and warmup, not
query-only I/O. Native requested posting bytes are not device-read bytes.

The SIFT1B generator replaces the old four-level ACL hierarchy with exactly
two attributes: one Zipf-200 categorical tag and one deterministic numeric
value. It writes the final row-major `uint32 [N,2]` SPTAG input directly in
bounded-memory chunks; no `tags5` merge or per-vector routing-key text file is
used. The categorical assignment is an exact affine permutation of the Zipf
counts. The generator reads the native INI and derives 399 extreme vectors from
`Ratio=.12`, two support slots, `InternalResultNum=96`, and the rare-label
dataset coverage formula. `spannbuilder` uses its tenant-0 bulk path for these inputs: mapped
vectors and attributes are borrowed through the synchronous build instead of
materializing one metadata string, pointer pair, and global ID per vector.
Both original and constrained placement retain only emitted RNG edges rather
than initializing `N * ReplicaCount` slots.

```bash
Tools/benchmarks/prep_sift1b_inputs.sh
CFG=Tools/benchmarks/build_spann_attr_sift1b_zipf200_limited_tag.ini
Tools/benchmarks/run_spann_attr_build.sh "$CFG"
```

The canonical build uses one global spatial hierarchy,
`[Tags] ColumnTypes=categorical,numeric`, and explicit `LimitedTagColumn=0`. Categorical mask layout is internal filter metadata, not
an attribute hierarchy or a partition plan. Signed support and hierarchy
artifacts retain generation checks; upper-layer signatures are compatibility
metadata and do not participate in query traversal. Historical four-category SIFT1B recipes explicitly retain the archived
`sift1b_tags5.u32` schema; old `4node` filenames do not authorize schema reinterpretation.
These inputs are not produced by the current generator.

Attribute organization has been removed, not merely disabled. `ACLCols`,
`HierLevelWidths`, `PivotForceNodeCount`, `DisablePivotEstimator`, `RoutingCols`,
`PerVectorTagsFile`, `SelectHeadType=PerTagBKT`, their retired environment
overrides, and `--routing-only` are rejected before a native build. Remove
them from current configurations; use `SelectHeadType=BKT`. The planner API
`EstimatePivotBuildPlan` / `EstimatePivotPlan`, grouping utilities, and
`tag_node_index.bin` production/consumption are gone. `--merge-tags5` retains
`--acl-cols` as an input **column count**, not a routing projection, and no
longer writes a grouping file.

`Hierarchy*` still configures the spatial H1..H5 hierarchy. Attribute records,
exact categorical/numeric filtering, tag support, H/O placement, signed
hierarchy CSR and head/posting signatures remain. Generic physical bundle
APIs remain for geometry and upstream CRUD; tagged inserts into such bundles
choose their owner by vector distance rather than an attribute pivot.

### Reduced filtering configuration

The current H5 recipe uses H1 navigation with upper posting catalogs. Most
entries are original SPANN construction/search settings or input/output
configuration, not filtering tunables. The filtering/hierarchy-specific
overrides are:

```ini
[Tags]
ColumnTypes=categorical,numeric

[SelectHead]
HierarchyEnabled=true
HierarchyLevels=5
HierarchyReplicaCount=8
ParallelBKTBuild=true

[BuildSSDIndex]
EnableLimitedTagPosting=true
LimitedTagColumn=0
LimitedTagMinHeadCount=16
EnableLimitedTagSupportExpansion=true

[SearchSSDIndex]
EnablePostingNavigation=true
```

This is an excerpt, not a standalone build INI: shared native `Ratio=.12`,
posting nprobe `InternalResultNum=96`, construction threads 45, search threads
1, data paths and original graph/storage settings remain in the full recipe.
`ParallelBKTBuild=true` is intentional for the 1B profile: it processes sibling
BKT nodes concurrently. It consumes more temporary k-means memory than the
serial implementation, so launch preflight must verify sufficient RAM.

| Removed interface | Replacement or reason |
| --- | --- |
| `LimitedTagMaxExtraSupports` | Per-tag source-capped deficits bound incremental candidate storage; no independent global cap. V4 caps remain read-only provenance. |
| `NumericCols` / `SPTAG_NUMERIC_COLS`, old bulk prefix-count option | Use `[Tags] ColumnTypes` for every original column; width and numeric lane mapping are derived. Legacy stored prefix metadata remains readable. |
| `HierarchySignatureMinSelectivity`, `HierarchySignatureMaxSelectivity` and `SecondLevel*` aliases | New signatures cover all labels; legacy domain metadata remains authenticated and read-only. |
| `BKTSeed`, `TPTSeed` | Upstream global/clock RNG behavior; no custom fixed seed or external seed knob. Loaded historical geometry stays unchanged. |
| `HeadNavigationMode`, `HierarchyGraphSignaturePruning` | One native H1 query graph; result-only post-filter does not prune ordinary neighbors. |
| `BuildH1Graph`, `CompactHierarchyVectors`, `HierarchyInitialProbeRatio`, `HierarchyMaxCheck`, `HierarchyPrefetchMode` | Fresh builds retain H1 navigation; no top-graph beam/descent query remains. Required saved layout metadata is decoded explicitly for migration. |
| `HierarchyRouteSelectivityThreshold`, `SecondLevelRouteSelectivityThreshold` | Predicate selectivity never chooses a graph or budget. |
| `SparseFallbackMaxHeads`, `SparseFallbackMaxPostingPages` | No direct/global completion or restart; optional signed posting extends the same H1 frontier. |
| `ForceDenseTagSearch`, `DirectSparseMaxPostings` | Dense/sparse route selection and direct posting scans are removed. |
| `FilteredSearchNprobeSafety`, `FilteredSearchTargetRecall`, `FilteredSearchCoverageExponent`, `EnableAdaptiveFilteredNprobe`, `LogAdaptiveNprobe` | Filtered and unfiltered requests share the configured native posting and graph budgets. |
| `FilterKeepUExtra` | All configured bundle nodes participate independently of predicate presence. |
| `HybridRouteSampleCount`, `HybridRouteSelectivityThreshold`, `HybridRouteDeformationThreshold`, `LogHybridRoute` | Hybrid query-route selection is removed; hybrid-format metadata is diagnostic/compatibility data only. |
| `EnablePrimaryHeadBypass`, `BuildPrimaryHeadCSR`, `PrimaryHeadBypassRerankL` | Primary-head direct bypass and its sidecar are removed. |
| `EnableExtremeSparseTag`, `ExtremeSparseTagMinCount`, `ExtremeSparseTagFile`, `LogExtremeSparseTagRoute` | Unreachable EST builder/reader/query/merge route and sidecar store removed. Rare labels use ordinary H/O support. |
| `FilterKeepCross`, `LogUExtra`, `PostingQuantBits` / `--posting-quant-bits` | No effective consumer. Actual codec metadata determines its representation. |
| `DisableCrossSubgraph` | Use the existing `DisableCrossEdges` gate. |
| `UnifiedNprobeBudget`, `MultiNodeBudgetKeepRatio` | One aggregate native posting budget; no per-bundle budget multiplication. |
| `HybridGraphDegree` | Existing fixed format degree 16, not an adjustable capacity. |
| `EnableHierPostingFilter` | Validated posting/tail metadata is used automatically; missing or unusable metadata does not authorize dropping matches. |
| `[MultiTenant] CrossEdges`, `CrossExtraEdges` | Canonical `[BuildSSDIndex]` settings only. |
| `[MultiTenant] DualPoolAugment`, `DualPoolExtraRatio`, `UExtraIDFile` | Canonical `[SelectHead]` settings only. |

These retired options and duplicate section aliases, in addition to the
retired attribute-organization interfaces, are rejected
even when supplied as `false` or `0`, including launcher/native preflight.
`BuildSignaturesWithVectors` and `LoadAllForSignatureRepair` are removed;
use `BuildSignatures` and ordinary `LoadAll`.

The 42 removed H5 entries are **10 occurrences of retired options, four
section-alias entries, 27 omitted defaults/inactive-mode settings, and one
ineffective mis-sectioned storage default**. Recipe omissions do not remove
their native interfaces unless explicitly retired below. Flat ACL exact
admission is column-agnostic across categorical columns; DNF uses its stated
columns. Neither sorted categorical ranges nor disjoint domains are required.
Posting signatures do not prune the shared spatial posting prefix.

Other omissions from the H5 recipe are defaults or controls for inactive
alternative layouts, not deleted functionality. Support slot counts and
support floor remain independent; the total expansion budget is removed.
H/O construction uses native
`PostingPageLimit`/`PostingVectorLimit`; selected-region runtime reads use
`SearchPostingPageLimit`. Hierarchy beam ratio, hierarchy scoring work, native
graph `MaxCheck` and posting nprobe measure different work and remain separate.
Neither hierarchy signatures nor predicate density modify those budgets.
**O/suffix reading is preserved** under the same native region page limit.
Ordinary legacy pure+tail postings use one contiguous prefix for every
predicate. `EnableUnfilterTail`, `UnfilterPurePages`, `UnfilterExtraTailPages`,
`UnfilterPureDistanceScanPercent`, `AblateUExtra` and `AblateTail` are now
removed and rejected, including false/zero/default values. Tail construction
parameters and persisted boundaries are unchanged. Actual hybrid, cross-edge and upstream
CRUD modes remain available through their canonical native settings.
The ineffective `[BuildSSDIndex] SSDIndexFileNum=1` copy is also omitted;
the original native `[Base] SSDIndexFileNum` storage option is not removed.

Old saved INIs containing retired keys are intentionally rejected. Use their
historical reader or rebuild from a canonical configuration; do not rewrite
immutable historical build artifacts in place.

For retired query-routing settings, adopting the new query policy can use a
separate writable clone with just those INI entries removed.
Verify native reload/search and unchanged geometry/support/posting hashes.
This intentionally changes query behavior, not an inert-default migration:
the enabled hierarchy now uses one fixed traversal for every query, and
limited-budget recall or result count can decrease without global completion.

An audited **inert serialized default**, such as `PerVectorTagsFile=` in an
otherwise compatible BKT index, does not require rebuilding vector/posting data:
create a separate writable clone, remove only the verified inert retired INI
entries there, and require strict native load/export/reload validation before
publication. Keep source artifacts unchanged and verify native artifact hashes,
generation bindings, sampling IDs, CSR and VID mappings are unchanged. Do not
blindly strip nondefault settings: active grouping or different query-budget/
cross-edge semantics require a separate compatibility decision. New `SaveConfig`
output no longer contains these removed parameter definitions. Empty defaults
are not silently accepted by the new reader.

## Ordered Static STM1 Layout Compatibility

Build-only `EnableOrderedPageStart=true` together with `OrderedPageStartAttrs`
sorts each STM1 pure
posting prefix by the hierarchy tuple and persists `ordered_page_starts.bin`:
one `int32` page-start signature ordinal per configured attribute per posting
page.

```ini
[BuildSSDIndex]
Storage=STATIC
EnableOrderedPageStart=true
OrderedPageStartAttrs=2,3
```

For the SIFT hierarchy, columns `2,3` are team and project. Existing sorted
postings and their authenticated directories remain readable, but queries do
not use the directory to select, skip or widen pages. All predicates scan the
same native `SearchPostingPageLimit` prefix. The layout settings are rejected
in `[SearchSSDIndex]`, not interpreted as runtime filtering switches.

For the distance-order path, set `EnableOrderedPageStart=false`. The builder
does not apply the attribute tuple sort: pure records retain the selection
order `(head distance, VID)`, while tail records retain their separate
`(head distance, VID)` order. It removes any stale
`ordered_page_starts.bin`. This distance-order layout is the canonical SIFT1B
recommendation. The retired percentage and tail-page runtime controls must not
be used to restore predicate-dependent scans. `SPTAG_OPQ_PREFILTER`,
`SPTAG_PAGE_SELECT`, `SPTAG_PAGE_DIAG`, `SPTAG_DNF_NODROP`, and
`SPTAG_RBQ_EXHAUSTIVE` are also rejected on presence.
Compressed postings that cannot fit within a positive native page cap are
not read: the decoder requires the complete compressed payload. Rearranged
postings admit only records whose vector and VID both fit in the read prefix.

`build_spann_attr_sift1m_tagged_4node_static_fullfloat_tail_unbounded.ini`
is the matching SIFT1M no-order control; it explicitly sets this parameter to
`false`.

The native benchmark can issue this DNF form directly:

```bash
Release/spannaclbench \
  --index /path/to/index \
  --queries "$QDIR/query_vectors.npy" \
  --truth "$QDIR/groundtruth_project_local_ids.npy" \
  --query-tags "$QDIR/query_tags.npy" \
  --dnf-and-cols 2,3 \
  --warmup 200 --max-queries 1000
```

## Multi-Tenant Tag Cache Stress

Files:

- `multitenant_tag_cache_stress.py`: benchmark logic, exact recall computation, result summarization.
- `run_multitenant_tag_cache_stress.sh`: reproducible runner with fixed
  workload defaults and environment controls for the harness only.

Default workload:

- `1000` queries split into `10` batches of `100`
- sequential workload: tenants `0 -> 9`, one tenant per batch
- random workload: tenants mixed within each batch
- single-tag filter per query, sampled from the tenant's true tag distribution
- `topk=10`
- `seed=20260413`
- cache limit policy: `max(2 * largest HeadIndex, total HeadIndex / 4)` rounded up to MB

Search behavior comes from each index's native INI. The harness does not set
predicate-density, direct-posting, adaptive-nprobe, or forced-dense controls.

Run with defaults:

```bash
bash Tools/benchmarks/run_multitenant_tag_cache_stress.sh
```

Run a small smoke test:

```bash
SPTAG_STRESS_NUM_QUERIES=20 \
SPTAG_STRESS_BATCH_SIZE=10 \
SPTAG_STRESS_TENANT_RANGE=0,1 \
bash Tools/benchmarks/run_multitenant_tag_cache_stress.sh
```

Run a small RSS high-water sweep relative to the benchmark process baseline RSS:

```bash
SPTAG_STRESS_NUM_QUERIES=20 \
SPTAG_STRESS_BATCH_SIZE=10 \
SPTAG_STRESS_TENANT_RANGE=0,1 \
SPTAG_STRESS_RSS_HIGH_WATER_SWEEP_MB=off,+64,+128 \
bash Tools/benchmarks/run_multitenant_tag_cache_stress.sh
```

Run with an absolute RSS high-water cap:

```bash
python Tools/benchmarks/multitenant_tag_cache_stress.py \
	--rss-high-water-mb 2048
```

Useful environment overrides for the runner:

- `SPTAG_STRESS_SCENARIO_FILE`
- `SPTAG_STRESS_QUERY_FILE`
- `SPTAG_STRESS_OUTPUT_ROOT`
- `SPTAG_STRESS_NUM_QUERIES`
- `SPTAG_STRESS_BATCH_SIZE`
- `SPTAG_STRESS_TOPK`
- `SPTAG_STRESS_TENANT_RANGE`
- `SPTAG_STRESS_SEED`
- `SPTAG_STRESS_CACHE_LIMIT_MB`
- `SPTAG_STRESS_RSS_HIGH_WATER_MB`
- `SPTAG_STRESS_RSS_HIGH_WATER_SWEEP_MB`
- `SPTAG_STRESS_DROP_PAGE_CACHE_ON_EVICT`
- `SPTAG_STRESS_FORCE_DENSE_TAG_SEARCH`
- `SPTAG_STRESS_DIRECT_SPARSE_MAX_POSTINGS`
- `SPTAG_STRESS_FILTERED_SEARCH_NPROBE_SAFETY`
- `SPTAG_STRESS_FILTERED_SEARCH_TARGET_RECALL`
- `SPTAG_STRESS_FILTERED_SEARCH_COVERAGE_EXPONENT`
- `SPTAG_STRESS_PYTHON`
- `SPTAG_STRESS_LD_PRELOAD`

Artifacts written per run:

- `benchmark.log`: full stdout/stderr
- `status.txt`: `running`, `success`, or `failed`
- `meta.txt`: human-readable run configuration
- `meta.json`: structured run metadata
- `summary.json`: machine-readable result summary
- `summary.md`: human-readable summary table
- `batch_summary.csv`: batch-level metrics

Artifacts written for RSS sweep mode:

- root `summary.json` / `summary.md`: aggregated per-budget summary
- root `budget_summary.csv`: one row per `(rss_budget, scenario)`
- one child directory per RSS budget, each containing the normal per-run artifacts above

Notes:

- The benchmark uses exact recall computed from the base vectors referenced by the scenario file.
- The runner records seed, search parameters, git commit, and runtime environment so the workload is reproducible.
- Latency is statistically reproducible, not bitwise identical, because OS scheduling and file cache state can vary.
- `--rss-high-water-mb` accepts `off`, an absolute MB value like `1024`, or a relative headroom like `+128` measured above the benchmark process RSS right before workloads start.
- `--rss-high-water-sweep-mb` accepts a comma-separated list in the same format and runs each budget in a fresh child process so process-level RSS measurements do not drift across sweep points.