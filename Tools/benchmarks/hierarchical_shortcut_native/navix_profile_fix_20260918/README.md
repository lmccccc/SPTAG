# One bounded, profile-led qualification milestone

This is an isolated successor of **frozen OLD NaviX posting**, not a promotion.
Production, previous experiments, input/index bytes, policy, thresholds, budgets,
and `OPERATOR_STOP` are unchanged. Main documentation and plots are not edited.
The proper graph control is frozen cost-v2 **H1 result-only postfilter**, not the
matched-original unfiltered H1 control.

## Evidence and implementation

`profile.py` samples the three **actual frozen binaries** (OLD, proper graph,
failed hotpath), three predetermined Broad windows each. The only preload is
`ClockAudit.cpp`: a thread-CPU timer directed to the query/main TID, gated by
steady-clock boundaries 5 and 6. The signal handler only stores ucontext RIPs in
a bounded preallocated array; no allocation, locks, unwinding, or symbolization.
Load, warmup, untimed capture, and other threads are excluded. Raw PCs, image
bases, TID, flags, interval, drop count, and absolute native work are retained.
These runs are explicitly **diagnostic**, never ordinary latency measurements.
PC residence does not establish hardware cache misses or memory stalls.

`line_profiles.py` rebuilds only the relevant frozen translation units with
their exact optimized flags plus `-g1`. It verifies **every executable ELF object
section is byte-identical** to the frozen object. A separate diagnostic link is
used only for offline line resolution, mapping each frozen symbol plus offset.
Foreign-image PCs are kept separate. Diagnostic binaries are not used as timed
controls.

The initial profiles identify repeated posting/support admission and native
qualification callbacks as the principal extra residence. The failed variant's
component callback remains hot. The successor starts from OLD, removing all
failed component-generation-cache, extra prefetch, emitter, and scratch changes.
One byte per physical H1 head now lazily holds distinct immutable final posting
and exact-own qualifications. A direct eligibility path reads that byte once;
support is not a separately cached component. Bytes reset per query. Query
leases handle recursion without sharing a live predicate. Mutable deletion,
visited state, own-result competitiveness and gain, collapsed alias enumeration,
whole-row consumption, and all native routing/admission policy remain at their
original sites. Additional counters are untimed head-phase diagnostic snapshots.

## Reproduction

From the workspace root, with no search/data environment overrides:

```sh
P=SPTAG/Tools/benchmarks/hierarchical_shortcut_native/navix_profile_fix_20260918
python3 "$P/profile.py"
python3 "$P/line_profiles.py"
python3 "$P/distance_lines.py"
# Initialization is one-shot and must precede application of the successor diff:
# python3 "$P/prepare.py" --initialize
python3 "$P/build.py"
python3 "$P/small_replay.py"
python3 "$P/run.py"
python3 "$P/finalize.py"
```

Native ordinary INIs are the sole authority: Broad, n24, 1000 warmup + 1000
measured, offset0, topk10, MaxCheck2048, HierarchyMaxCheck512, ratio0.666666,
pages15, one query thread, buffered index, CPU and memory NUMA2. `run.py` uses
two reverse/interleaved rounds of proper graph, OLD NaviX, successor NaviX, OLD
combined, successor combined. No preload and no in-window capture/profile.
`small_replay.py` runs only 32-query sparse/empty parity fixtures against frozen
prefix outputs and native work. It makes **no extreme latency claim**.

Artifacts and the final measured outcome live under
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/navix_profile_fix_20260918`.
Compilers, archives, sampler DSOs, and debug-only links are isolated under the
matching `toolchains` directory. The final report, not reduced diagnostic
counters, determines whether the latency objective was met.

## Verified outcome: goal not met

| Broad mode | Frozen OLD mean ms | Successor mean ms | Recall@10 |
|---|---:|---:|---:|
| NaviX-only | 1.557223 | 1.535581 | 0.9177 |
| Combined | 1.567943 | 1.567105 | 0.9176 |

The proper postfilter graph measured **0.711161 ms**, recall **0.9154**.
NaviX's average reduction is only 1.39%, and its second paired repetition is
slower; combined is effectively unchanged. This is **not** a reliable working
hotpath improvement. Lower static evaluation counts do not override that result.
All native fixtures and exact frozen output/work comparisons passed, including
bounded sparse and unfiltered parity. The successor remains isolated,
unpromoted, and stopped after this one implementation. No further cache,
policy change, or expensive extreme campaign was attempted.
