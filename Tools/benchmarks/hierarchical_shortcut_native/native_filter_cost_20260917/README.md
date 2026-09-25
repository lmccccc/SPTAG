# Bounded native real-filter cost diagnostic

This experiment does not resume any curve stage. It preserves the accepted
native-default implementation and the completed unfilter/broad curve stage.

The real predicate is DNF3 numeric `column1 <=4294966124`, derived from the
persisted `ColumnTypes=categorical,numeric` and generation-bound NUM2 numeric
metadata. The bound equals the actual encoded uint32 maximum. Python checks
the existing attribute-domain bounds; `nativecoverage` then uses native
`DNFPredicate::Matches` over all1,000,000 attributes and loads the actual
`LimitedTagSupport`, checking all160,091 persisted own attributes against
their canonical dataset IDs and the same predicate. The actual benchmark
passes the nonempty DNF3 buffer through `SearchWithPredicate`. Native flags
are checked in capture and ordinary execution: B/C remain nonempty numeric
predicates with filtered admission, never the empty-predicate certificate.
Unfilter truth is used only after this coverage proof. No synthetic Boolean
callback replaces the real filter.

Cases: A is original native H1 with an empty predicate. B keeps the same
native posting-result/traversal predicates and own-point admission as C,
but installs no degree/injection observer. C uses the unchanged ratio
supplier. In this new diagnostic only, `ShortcutMode=control` means B;
older completed control semantics and binaries are untouched.

All runs use native `SearchSweep.NProbe=[24]`, topk10, MaxCheck2048,
HierarchyMaxCheck512, ratio0.666666, page15,1000 warmup+1000 measured,
one query thread, NUMA CPU/memory2 and O_DIRECT. Ordinary repetitions rotate
A/B/C then C/A/B. Capture counters separately count actual posting,
traversal, own-notification and exact-head predicate calls plus degree rows.
They do not install clocks, caches, score wrappers or result heaps.

`ClockAudit.cpp` is used only in separately labeled instrumentation runs.
Actual interposition finds eight steady-clock calls, all coarse benchmark
Run boundaries, and zero fine query clocks. Its optional1ms SIGPROF sampler
is enabled only between coarse calls6/7 (the ordinary measured block).
Sample counts locate CPU hotspots; they are not milliseconds or complete
wall-time attribution. Instrumented timings are never substituted for
ordinary measurements. Symbol resolution is mostly function-level;
the native CMake release recipe overrides the requested release `-g1`.

## One causal fix, separate version

Reference measurements showed repeated posting qualification and sampled
posting-validity/metadata work. `bound_fix/BKTIndex.cpp` adds only a strict
current-native-result-bound rejection **after own notification**, before
posting-result qualification, for noncollapsed admissions. Equality still
takes the old path. Collapsed admission explicitly retains its old return
contract: `CheckDup` returns `!AddPoint`, and that value controls alias
enumeration, so a blanket early false return would be wrong. The fix does
not skip own-only points, change native queues/stopping, add a radius or
truncate supplier rows. A new native fixture verifies own notifications
survive while impossible result-predicate calls are skipped; all existing
collapsed/tie/deletion/ratio/full-row tests remain.

Only this fix was attempted. The degree observer's repeated qualifications
and two passes are not claimed removed. No new H1/epoch cache, shadow heap,
parent frontier, graph selection or generic always-true bypass was added.

Reference runtime/results:
`toolchains/h1_native_filter_cost_20260917` /
`comparisons/h1_native_filter_cost_20260917`.
Fixed runtime/results:
`toolchains/h1_native_filter_cost_bound_20260917` /
`comparisons/h1_native_filter_cost_bound_20260917`.
All are under `datasets/sift1m_zipf200_sparse193_numeric`.

`prepare.py`/`install.py` materialize the diagnostic; build its isolated
source with the existing CMake Release/SPDK OFF/ROCKSDB OFF recipe and target
spannaclbench, parallelism2. Build this directory's native tests with
SPANN_ROOT pointing to that source; execute `nativecoverage coverage.ini`.
`run.py fixtures`, `measure`, `clocks`, and `sample` are separate bounded
phases. `prepare_fix.py`/`install_fix.py` create the separate bound version;
pass `--experiment bound_experiment.ini` to its runs, and use
`-DNATIVE_TEST_SOURCE=<this directory>/bound_fix/NativeTests.cpp` for its unit
target. `report.py` reconciles both versions without running queries.

The real-predicate B/C native outputs/core work must match exactly. A/B may
differ: B/C use posting-only head selection, all-evaluated own admission and
the existing nonempty-predicate O-tail signature filter. Thus A-B is not a
pure comparison-operator cost, even though all records satisfy the predicate.
Failed fixture development and the numeric-predicate audit assertion
correction are preserved; no completed native process was rerun to hide them.
The bounded report ends this task; the fix is not silently promoted into
the frozen curve dataset.
