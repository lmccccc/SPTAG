# Native SIFT1B selectivity comparison

`workloads.ini` prepares the existing 1000-query exact-truth cohort as native
UInt8 without changing a value. It authenticates the original categorical and
numeric attributes, preserves existing exact truth, and computes exhaustive
truth for categorical tag169 (1,000,735 matches). The requested panels are
unfiltered100%, categorical17.012493%, categorical1.7012493%,
categorical0.1000735%, and the original mixed DNF0.042571%. Mixed is a separate
predicate case, not the lower endpoint of the categorical sweep. Numeric-only
and the399-point extreme categorical case are excluded.

`benchmark.ini` is authoritative for all runtime and orchestration settings.
It compares the same corrected native core and unchanged compact index with
H1-only2048, shared-budget posting2048, posting2048+2048, and H1-only4096.
Use one query thread, the same NUMA3 placement, page limit3, anchors8 and
nprobe16/24/48/96/192/384 for every method. These are checked-leaf budgets, not
limits on all distance work. The ordinary unfiltered path retains its upstream
adaptive soft budget; filtered H1 is hard-bounded and supplementary rows finish
before stopping.

From the repository root:

```bash
client=Tools/benchmarks/hierarchical_shortcut_native/native_postfilter
config=Tools/benchmarks/configs/sift1b_selectivity
python3 -B "$client/prepare_selectivity.py" "$config/workloads.ini"
python3 -B "$client/run_selectivity.py" "$config/benchmark.ini" --stage prepare
python3 -B "$client/run_selectivity.py" "$config/benchmark.ini" --stage all
```

Preparation and measurement outputs must be new. Existing or partial native
batches are retained and rejected, never silently restarted or overwritten.
Each of the40 ordinary cases performs1000 warmup,1000 measured and1000 replay
queries at every probe value. Method/scenario order reverses in repetition2.
The native client loads the index once per instrumentation class, avoiding
repeated twelve-minute index loads. The20 diagnostic cases are separate and
their timing is excluded from QPS.

Load-once execution retains native workspace history. Small fresh/batch
controls reproduce results, distances, SSD work, graph heads and traversal,
but native hash-clear bytes can differ after a budget change. Registration
records this difference; raw diagnostics retain it. No workspace reset,
algorithm change or fresh-process allocation equivalence is claimed.
Do not splice historical process-per-case timings into these curves.

The controller checks only returned IDs against attributes, rather than
allocating billion-row predicate masks. It freezes source, binaries, native
INIs and truth digests, checks large-file identities without hashing the1.8TB
posting store, and confines native writes to the new campaign directory.

After all240 ordinary points, the frozen R `--selectivity` renderer writes
`plots_selectivity/recall_qps.png`, PDF and source CSVs beside the campaign.
QPS is the arithmetic mean of the two runs with their full range, not a
confidence interval. Curves connect measured points without interpolation.
Read-only summaries report the best measured points reaching80%,90% and95%
recall, or explicitly `not_reached`; these targets never change search settings.
Completion certifies the measured grid and validation, not a claim that
posting supplementation wins every scenario.

## Recorded acceptance

The2026-09-23 run completed240 ordinary and120 diagnostic points in
`datasets/sift1b/comparisons/main_selectivity_20260923/campaign_v2`.
The sibling `plots_selectivity` directory contains the figures and CSVs.
`acceptance-diagnostics.json` records the independent raw-payload and
plot-coordinate audit. The first `campaign` directory preserves a controller
logging error that occurred before any native load or measurement.

Query behavior checks passed: ordinary/diagnostic results and SSD work agree,
the original H1 phase is unchanged,96,000 filtered observations have no graph
budget overshoot, and30,004 filled-head posting observations do no auxiliary
owner/signature/representative/CSR work. **Overall performance did not pass
the high-recall/non-regression objective under this budget grid.**

At nprobe96, broad categorical has unchanged recall and graph/SSD work but
8.33% lower ordinary QPS with posting enabled; no posting expansion occurs.
The exact CPU hotspot was not profiled. For the0.1% and mixed cases, all1000
queries exhaust the extra budget while averaging only29.692 and28.336 valid
H1 heads out of96. Their measured maximum Recall@10 is49.25% and41.08%.
Returning ten final records does not establish high recall, and raising
nprobe alone within this grid does not fill those head deficits.

## Read-only recall-cause probes

`recall_probe.ini` changes only supplemental checked-leaf budgets
(2048/8192/32768/131072), nprobe (96/384/768), and a separate15-page control.
The graph budget remains2048 and the same index,1000 queries, anchors8 and
NUMA3 placement are retained. Run the independent diagnostic and ordinary
batches sequentially; the latter has one repetition and is not pooled into
the earlier acceptance curves.

```bash
python3 -B "$client/run_recall_probe.py" "$config/recall_probe.ini" --stage prepare
python3 -B "$client/run_recall_probe.py" "$config/recall_probe.ini" --stage diagnostic
python3 -B "$client/run_recall_probe.py" "$config/recall_probe.ini" --stage plain
python3 -B "$client/run_recall_phases.py" "$config/recall_phases.ini" --stage prepare
python3 -B "$client/run_recall_phases.py" "$config/recall_phases.ini" --stage run
```

The phase client explicitly enables native `LogPhaseTime` under
`Benchmark.PhaseTiming=true`. Its logged QPS is diagnostic, never throughput.
Each point emits1000 warmup,1000 measured and1000 replay phase lines; only
the middle window is aggregated. RAM navigation includes H1 plus posting
supplementation; the final posting phase includes buffered reads and scans.
Native phase sums exclude setup before head search and the logging itself.

Recorded results under `comparisons/recall_causes_20260923` show that larger
budgets alone do not restore high recall. At supplemental131072/nprobe768,
0.1% and mixed reach68.99%/65.29%, despite all1000 and998 queries respectively
filling the head target. Scanning15 pages improves only to69.30%/65.62%.
At nprobe96,32768 and131072 give identical head work and56.96%/51.89% recall:
the fill-target stopping condition, not the remaining budget, stops search.

| Scenario, nprobe | RAM navigation ms | Final posting phase ms |
| --- | ---: | ---: |
| 0.1%,96, extra2048 | 1.468 | 0.241 |
| Mixed,96, extra2048 | 1.557 | 0.235 |
| 0.1%,768, extra131072 | 27.310 | 5.634 |
| Mixed,768, extra131072 | 28.926 | 5.631 |

These are mean native phase diagnostics, not a fresh cross-system throughput
comparison. The high-budget cases score about77000/80000 supplementary H1
members, approximately99% predicate-negative, with about10000/12000 measured
C++ allocations per query. Allocation counters establish work volume, not a
profile proving its exact CPU share.

Current `HierarchyPostingQuery::Collect` ascends through the last selected
posting after its current-level queue empties, and drains lower discovered
postings before comparing alternative upper branches. A retained native
counterexample demonstrates a farther branch filling the target while a
nearer branch remains undiscovered. This establishes a search-order limitation,
not a quantified attribution of the complete1B recall gap. The supplemental
head heap stops after filling vacancies, rather than after candidate-quality
convergence. No search-core or index change was made during this diagnosis.

The phase native process completed all9 points and27000 phase lines. Its first
reporting pass failed only in the final dictionary point-count expression;
the original analyzer and failure are preserved. The explicit
`--stage summarize --allow-analyzer-update` recovery verifies unchanged native
inputs and records both analyzer hashes; it cannot launch new measurements.
