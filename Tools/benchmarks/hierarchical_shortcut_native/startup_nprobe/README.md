# Preregistered latest-startup native nprobe sweep

This experiment executes native queries at every point; it does not relabel or
interpolate the old nprobe24 result. The fixed common grid is
`16,24,32,48,62,80,96,128,192,256,384`, for original H1, historical H3, and the
latest corrected `startup_native` supplier, across all six existing SIFT1M
scenarios: **198 points**. No fourth graph-control curve is added.

**Known trigger defect:** this frozen supplier counts only fresh, unvisited
eligible neighbors toward degree16. Visited neighbors should still count toward
predicate-valid graph degree, so this implementation can incorrectly supplement
an unfiltered node whose ordinary neighbors are all eligible. These measurements
document the existing prototype, not the intended corrected degree semantics.
The plot labels this defect; no corrected implementation or timing is mixed into
this sweep.

`configs/` contains all native INIs, materialized before execution.
`preregistration.json`, `schedule.json`, and `experiment.ini` fix the protocol.
Only `InternalResultNum` varies within each mode. ResultNum10, MaxCheck2048,
HierarchyMaxCheck512, ratio0.666666, page15, EffectiveDegree16, one query thread,
and NUMA CPU/memory node2 are unchanged. No artificial supplier limits,
eligibility changes, startup changes, signature changes, partial-row scans,
upper graph search, or new visited semantics.

## Required entry-guard correction

The authenticated latest binary rejected every InternalResultNum except24 in
`AnnService/FullHooks.h::Configure`, and its SPANN benchmark branch independently
asserted result capacity24. A separate copied source/runtime replaces these
harness-only guards with configured capacity >= ResultNum and positive actual
capacity, respectively. The sweep validates actual capacity against each INI.
`capacity_guard_provenance.json` authenticates the parent source and proves
that these are the **only native changes**, in two files. Native BKT, supplier,
signature and admission sources are byte-identical; SPANN changes only that
benchmark assertion. The original source,
binary, production library, indexes and all previous results remain untouched.
An incomplete initial copy, caused by an existing dangling virtualenv symlink,
is preserved separately; the completed copy preserves original symlinks.
The first boundary trial exposed the second guard before any curve measurement;
its six successful frozen diagnostics, failed fixture, source archive and binary
are retained in `comparisons/h1_supplier_nprobe_20260916_guard_incomplete/`.

## Fixed execution and verification

There are396 ordinary processes: first1000 queries,1000 warmups, and1000
untimed capture queries each; two ordinary repetitions per point. The native
binary compares capture versus measured final IDs/distances and navigation
work internally. Phase clocks and path logging are disabled in ordinary runs.
The native supplier's existing read-only bookkeeping remains, as in the latest
runtime. Primary QPS is1000 divided by the mean of the two ordinary latency
values in milliseconds, not profiled time or an average of reciprocals.
`qps_min` and `qps_max` are the reciprocals of the slowest and fastest ordinary
run, using the same persisted latency precision as primary QPS; they are
observed ranges, not confidence intervals.

Probe order reverses in repetition2, with preregistered scenario/case rotations.
Thirty-six frozen original baseline diagnostics cover probes16/24/384 in all
six scenarios. Another36 eight-query profiled fixtures cover probes16/384 for
all three modes; these are correctness evidence, never curve timing samples.
Ordinary captures must match the fixtures' ordered outputs and work.
At24, all three modes must additionally retain exact ordered results/work
versus the completed latest startup experiment. Variable head capacity,
final predicate validity, duplicates, underfill, supplier full-row/continuation
invariants and repeated ordinary outputs/work are checked at every point.

Capture JSONL files are losslessly gzip-compressed after each process. Their
decompressed SHA256 is verified before removing the new uncompressed copy.
Native stdout/stderr, command, exit, O_DIRECT, resource, per-run validation and
provenance records persist. No previous raw capture is modified.
Baseline head-distance counts are not claimed from unprofiled instrumentation;
ordinary recall, latency, SSD work and selected-head counts are available for
every mode/point, and read-only supplier distance roles are available for S.

The historical H3 comparator retains its predicate-guided top search and
saved-frontier widening. Supplier uses only H1 navigation; the top graph
object remains loaded as vector backing, so its memory is not removed.

## Artifacts and scope

Under `datasets/sift1m_zipf200_sparse193_numeric/`:

- New runtime: `toolchains/h1_supplier_nprobe_20260916/`.
- New results: `comparisons/h1_supplier_nprobe_20260916/`.
- Preserved parent: `toolchains/h1_startup_20260916/` and
  `comparisons/h1_startup_20260916/`.

`summary.json` and `summary.csv` are written only after all198 points have both
ordinary repetitions and every required check passes. `thresholds.json` and
`report.md` select the fastest **observed** point with recall >=90% or >=95%;
unreached thresholds report maximum observed recall instead of interpolation.
`status.json` explicitly distinguishes running/failed/completed.

This task produces native curve **data**, not plots. Existing R plotting code,
GettingStart documentation, and published PNG/PDF artifacts are not edited.
Historical published curves use different IO conditions, search budgets and/or
query ranges. They must not be spliced into this same-run O_DIRECT sweep.

From the `mocheng` working directory, on fresh output/toolchain destinations
(preparation refuses to overwrite existing evidence):
```sh
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/startup_nprobe/prepare.py
cmake -S datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_supplier_nprobe_20260916/source -B datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_supplier_nprobe_20260916/build -DCMAKE_BUILD_TYPE=Release -DSPDK=OFF -DROCKSDB=OFF
cmake --build datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_supplier_nprobe_20260916/build --target spannaclbench --parallel 2
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/startup_nprobe/run.py
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/startup_nprobe/verify.py --results datasets/sift1m_zipf200_sparse193_numeric/comparisons/h1_supplier_nprobe_20260916
```

The in-flight initial renderer omitted QPS-range columns; `finalize.py` supplies
them from the unchanged ordinary latency arrays before final reconciliation.
Its before/after report artifacts are preserved. The current runner already
emits these columns directly; no native queries or primary coordinates change.

## Completed dataset

All198 points and396 ordinary repetitions are complete, with72 additional
certified diagnostic processes. Independent reconciliation covers all468 raw
native results, the complete grid, native INIs, repeated payload/work parity,
CSV/JSON agreement, QPS ranges and the36 observed threshold selections.
All10211 protected prior files remain unchanged.

The canonical files in the result directory are `summary.json`, `summary.csv`,
`thresholds.json`, `report.md`, `validation.json` and
`independent_reconciliation.json`. The preserved native evidence occupies17GB.
`summary.json` SHA256:
`b1aabaad3fc8c1ce5e9cc16b1e414b590675363e1ee36c0c86a9218cd4c11d74`.
No historical timing or single-point coordinates were substituted.
