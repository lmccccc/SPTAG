# Bounded ratio hot-path Phase2

This isolated integration preserves Phase1's ratio algorithm and authentic full
SPANN query semantics. It adds compact intrinsic qualification metadata and
clock-light attribution, not a new supplier policy. Production sources, the
MAIN-owned benchmark/parser, archived runtimes, indices and results are untouched.

## Native controls and semantics

The only nprobe array interface remains `[SearchSweep] NProbe=[16,24,384]`,
using the unchanged `NativeNProbeSweep::Parse` implementation. Each invocation
loads once; native workspace release/reset still handles descending capacities.
Checked-in INIs retain `MaxCheck=2048`, page15, ratio0.5 and minimum physical
degree16. There are no search/data environment overrides.

`ShortcutIntrinsicCache=true` enables the new table; `false` is the same-runtime
Phase1-optimized reference. The required Boolean is experiment-only and strict.
All other qualification, own-point admission, heaps, stopping, signatures and
complete-row behavior remain unchanged. In particular, physically short rows
never supplement, and visited nodes still count toward eligible connectivity.

`IntrinsicValidity.h` stores one posting/own-valid byte per H1 node (160091 bytes
per query thread on this dataset). The first guarded navigation initializes it
during warm-up. No vectors, graph or parent tables are duplicated. Static
canonical head identity is required; mutable limited-tag layout is rejected.
Both native `VersionLabel` and static posting loading expose instance identities,
mutation revisions and read guards. Revision-changing mutations take write
guards; navigation holds read guards. Deletion, undelete via `SetVersion`,
version increment, initialization/load, resize/add and posting reload therefore
cannot leave a reusable stale cache. Exact query predicates are never cached
across queries, and own-only/no-posting heads retain their actual bits.

`ShortcutProfile=true` now collects coarse intrinsic preparation, adapter
preparation, native search and finalization times plus native full-query phases.
Engine candidate-level clocks are disabled; legacy fine `*Ns` fields are zero
by design, not ordinary cost estimates. Ordinary execution is checked with real
chrono-symbol interposition, not inferred from those zero fields.

## Reproduction and evidence

The materialized C++ sources are repository-owned. `integrate.py` documents
their guarded transformation from the preserved Phase1 runtime; `prepare.py`
authenticates that parent, creates a separate runtime and copies these sources.
It intentionally refuses existing output/runtime paths. On an unused destination:

1. Run `prepare.py`; configure its isolated source with CMake Release,
   `SPDK=OFF`, `ROCKSDB=OFF`, and build only `spannaclbench`.
2. Configure this directory's CMake tests with `SPANN_ROOT` pointing to that
   isolated source; build and run CTest.
3. Run `run.py fixtures`, then `run.py measure`. These are bounded entrypoints:
   six small fixtures, six ordinary unfilter24 invocations and three separate
   coarse profiles. There is no full-sweep entrypoint.
4. `probe.py` records original-binary clock/CPU diagnostics;
   `probe_optimized.py` records the optimized binary separately. Diagnostic
   `LD_PRELOAD` instrumentation is explicit and is never used for ordinary QPS.
   `report.py` rehashes evidence and inputs without launching searches.

Native fixtures compare complete captured payloads and core work against
Phase1, including forward/reverse16/24/384 and four small filtered predicates.
New diagnostic fields/times are excluded explicitly from cross-version payload
digests. Actual native load-entry and physical vector/BKT/RNG messages prove
single loading; Python invocation counts alone are not accepted.

Canonical output:
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/h1_ratio_phase2_20260917/`.
The report distinguishes same-runtime cache-off causal controls from the
archived executable used for clock diagnostics. Sampling PCs are statistical
process CPU evidence, not exact wall-time fractions. The preserved failed
initial sampler is not counted as a successful benchmark.

Phase2 stops after this focused comparison. It does not authorize a full sweep
or claim the remaining H1 performance gap is solved.
