# Explicit native postfilter + two-hop: bounded milestone

Isolated successor of frozen `navix_strict_thresholds_20260918`. Nothing installs
into production, old toolchains, historical evidence, `_SPTAG.so`, or plots.
GettingStart documentation belongs to the parent; `OPERATOR_STOP` remains sealed.

## Semantics

* `NavixMode=postfilter_graph`, `NavixTwoHopThreshold=0`,
  `NavixPostingThreshold=0`: same-core proper native result-only postfilter plus
  native own points. No classifier and no qualification cache.
* `NavixMode=postfilter_twohop`, `NavixTwoHopThreshold=0.1|0.05`,
  `NavixPostingThreshold=0`: initial eligible count includes visited entries.
  For `r>=threshold`, use **original ordinary native adjacency expansion**,
  with no pre-distance valid-mask gate, no traversal filter and the original
  per-edge budget. Nonmatching neighbors participate normally in distances,
  queues and `m_Results` bounds. Empty rows retain ordinary behavior.
  Otherwise use the retained directed/full operator selected by
  `.4*(d*e+e)>2*d-e`. Two-hop useful destinations remain filtered; this is not an
  unfiltered quadratic scan. Chosen second rows finish with native accounting.
* Standalone **filtered one-hop is disabled in the new mode**. Old mode names
  retain their old semantics.
* Posting is disabled entirely, including `e=0`: no callback, required model,
  owner construction, signature, representative, or CSR query work. H1-only
  graph/two-hop works with empty hierarchy catalogs and a null model.
* Empty predicates retain true ordinary `SearchIndex` bypass.

The three benchmark points share the same binary and core. Frozen cost-v2
proper postfilter outputs are the control oracle, never a timed fourth point.

## Diagnostic schema

Untimed `.operators.u64` is `N x 3` uint64:
`outer_expansions`, `ordinary_neighbor_entries`, `qualification_requests`.
Both controls and hybrids emit the same schema.

`ordinary_neighbor_entries` counts nonnegative valid physical row entries
actually reached after the original per-edge budget gate and before visited
checks. It includes already-visited/nonmatching entries and excludes sentinels,
budget-truncated suffixes and two-hop useful-destination offers.
Classifier initial scan volume is separately `sum(decisions.d)`, not this count.
Qualification requests also include selected second-row predicate requests.
No counter increment or capture allocation occurs in ordinary timing.

The retained route-code-1 field `one_hop` denotes ordinary postfilter in this
explicit new mode; it does **not** mean the historical filtered-onehop operator.
`.native.u64` retains 18 fields, `.navix.u64` 19, `.qualification.u64` 6,
and `.decisions.f64` 11. Raw native checked/distances/queues and head/own/SSD
captures remain independent units. No phase-time attribution is inferred.

## Reproduction

From the workspace root, in a **fresh** milestone destination:

```sh
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_twohop_20260918/prepare.py --initialize
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_twohop_20260918/build.py
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_twohop_20260918/small_replay.py
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_twohop_20260918/run.py
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_twohop_20260918/finalize.py
```

Initialization refuses existing toolchain/evidence destinations. Source clone
preserves symlinks and excludes old Release/build caches. Release CMake uses
SPDK/ROCKSDB OFF and `-j2`; headers come from the private installed clone.
Compiler scratch stays inside the project.

Actual native BKT fixtures cover ordinary/classifier graph full encounter
parity, bridge, boundaries, directed/full, budgets, e0/no callbacks,
hierarchy-free operation, own/alias/tie/deletion, diagnostics and empty bypass.
Protocol rejection tests execute before bounded dataset replays.

Exactly four 32-query sparse/unfiltered functional replays precede six Broad
processes: graph/.1/.05 then .05/.1/graph. Each uses 1000 warmup, 1000 measured,
1000 untimed capture queries, offset0, native `[24]`, top-k10, MaxCheck2048,
Hierarchy512, initial ratio .666666, pages15, one query thread and CPU+memory
NUMA2. Every process loads the matched buffered index once. Ordinary loop SHA
is `9f412c63b71f7310e09180dff0858ccef3957bd20d7a96c34fdb440e3d559d53`.

`report.json`, exact per-process commands, raw captures, library/binary/source
hashes, and the sealed manifest live under
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/postfilter_twohop_20260918`.
Report speed/recall failure honestly; no old filtered-graph trace equality,
long Extreme latency claim, curve, tuning, optimization, or promotion.
**Stop IDLE after this milestone.**
