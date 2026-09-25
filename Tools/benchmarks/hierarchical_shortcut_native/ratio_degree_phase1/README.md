# Bounded ratio-degree Phase1

This experiment stops after focused fixtures and unfilter nprobe24 comparisons.
It has no full-sweep command. MAIN's generic benchmark, shared parser and plots
are not modified; all prior sources, binaries and results remain preserved.

Native controls for supplier/control:

```ini
[SearchSSDIndex]
ShortcutRetainedRatio=0.5
ShortcutMinBaseDegree=16
ShortcutHotPath=optimized
InternalResultNum=24
ResultNum=10

[SearchSweep]
NProbe=[24]
```

The old ShortcutEffectiveDegree control is rejected, not reinterpreted.
Let d count distinct in-range, nonself ordinary neighbors before the native first
negative sentinel; collapsed metadata is not an edge. Let e count posting-OR-own
eligible neighbors, including query-visited neighbors. Supplement iff d>=16 and
e<0.5*d. The relative connectivity goal is ceil(0.5*d), including distinct eligible
supplied neighbors; equality does not trigger. d<16, including d=0, never supplies.
No division by zero, added work cap or global rescue is introduced.

Startup uses the same rule on the existing reachable BKT anchor. It is considered
only when the entry queue is below the maximum possible relative goal for the
native row width. A physically short or sufficiently eligible anchor receives
no special edge expansion merely because few fresh candidates remain. A
ratio-deficient eligible anchor offers its ordinary edges and, while native
MaxCheck remains open, uses the same synchronous supplier in the same H1 search.
This intentionally permits sparse underfill where the physical anchor is short.

Signature pruning precedes parent representative scoring and row selection.
H1 candidate qualification precedes scoring; BKT structural centers remain the
explicit exception. Selected H2/H3 rows always finish and are then marked visited.
H3 enumeration caches H2 candidates rather than forcing a descendant subtree.
No upper search frontier, restart, result-dependent retry or arbitrary radius
is added. Native MaxCheck2048, hierarchy512, ratio0.666666, page15 and top10 remain
unchanged. The historical top graph remains loaded as backing, not supplier navigation.

## Hot-path changes and attribution

Optimized connectivity reuses a tiny per-thread ordinary-ID scratch vector.
Per-pop hash sets and audit vectors are absent in ordinary no-supplier navigation;
the supplied-ID set is initialized only if supplementation actually occurs.
There is no graph-degree cache, new large table or duplicated vector storage.
Upper posting/discovery state is initialized lazily on the first actual helper
use, instead of clearing every row each query. Score/admission reuse eligibility
already established for that exact ID/epoch. When the original native distance
callback contains a raw function pointer, the exact same pointer is invoked
directly; otherwise its original callback is retained. Posting admission,
own-point admission and the separate native heaps are not bypassed.

`ShortcutHotPath=reference` is one fixed implementation control retaining the
old per-frame hash/vector allocation, eager upper-state clearing and redundant
qualification/callback dispatch, with the **same new ratio policy**.
Its two ordinary runs provide causal timing evidence for these changes rather
than splicing old absolute-degree timings. Both implementations share the
stack-resident connectivity wrapper. This is not a policy/threshold sweep.

Ordinary runs contain no clock calls from the new scopes. Separate profiles
measure epoch/state reset, complete degree inspection, qualification, actual
distance dispatch, admission and helper time. These scopes are nested:
degree includes qualification, and helper includes scoring/admission. Their
nanoseconds are instrumented attribution, not additive ordinary latency.
Native graph-neighbor heap offers/acceptances/rejections are counted without
changing `m_Results.insert` or its actual current bound. Rejected far neighbors
have already paid qualification and distance costs; selected rows are not cut.

## Exact bounded protocol

Preregistration fixes eight ordinary processes: H1, matched control, optimized
supplier and same-policy reference, each twice in reverse order. Four separate
profile processes use the same first1000 measured queries and1000 warmups.
Each native process uses SearchSweep.NProbe=[24], one index load, one query
thread, NUMA CPU/memory node2 and O_DIRECT. Untimed captures verify IDs/distances
and work without contributing to ordinary QPS.

Ten small fixtures use8 measured queries and8 warmups: forward/reverse
16/24/384 for H1/control/supplier, and supplier24 for medium/extreme/numeric/mixed.
Native unit tests cover all specified ratio boundaries, odd/zero/short degrees,
visited-independent connectivity, duplicate/sentinel handling, sparse startup,
all-rejected termination, full rows, row reuse and >3200-distance/>2048-member
rows without custom caps.

One-time preparation: `python3 integrate.py`, then `python3 prepare.py` from this
directory. Build the isolated toolchain with the standard Release CMake settings
(`SPDK=OFF`, `ROCKSDB=OFF`) and target `spannaclbench`; build this directory's
CMake fixture target with `SPANN_ROOT` pointing at that isolated source.
Run `python3 run.py fixtures`, then `python3 run.py measure`. Exact native
commands, input/config/source/binary hashes and raw captures are persisted in
`comparisons/h1_ratio_phase1_20260917/`. These commands never launch198 points.

## Phase1 measured outcome

The ratio rule produced zero actual supplier calls, upper representative
distances and CSR scans on all1000 unfiltered queries. All native unit/small
fixtures and ordinary/profile/repetition payload checks passed.

| Case | Recall@10 | Ordinary ms | QPS |
|---|---:|---:|---:|
| Original H1 | 0.9073 | 0.951652 | 1050.805 |
| Optimized no-supplier control | 0.9074 | 1.859190 | 537.869 |
| Optimized ratio supplier | 0.9074 | 1.820610 | 549.266 |
| Same-ratio implementation reference | 0.9074 | 2.131150 | 469.230 |

The implementation changes reduce same-policy ordinary latency by14.57%, but
the optimized supplier remains1.913x H1 latency. Both zero-supplier paths remain
slow. Actual distance calls are essentially unchanged: H12231.960 versus
supplier2226.294 per query. Remaining work includes169.476 complete degree
frames,2507.154 qualification evaluations and5832.549 cache hits per query.
Separate instrumented scopes show qualification/degree inspection as substantial
costs; their nested clock overhead must not be mistaken for ordinary latency.
Native neighbor heap rejection is82.13%, but these neighbors have already paid
qualification/distance costs. Eight-query filtered fixtures still expose many
supplier calls and large complete-row reads; no full filtered sweep was run.

H1 exactly preserves its prior reference IDs/heads. Ratio supplier/control/reference
agree with one another, but do not falsely claim complete H1 equality:
17 queries change selected heads,15 involving own-only/no-valid-posting heads;
two other changes occur at the existing native2048 boundary. Only query915 changes
one final dataset result, increasing recall by0.0001. No results are rewritten.
Exact evidence and component timings are in `report.md`,
`h1_admission_difference.json` and `summary.json`.

Phase1 stops here. The joint speed/recall goal remains unmet; no full sweep is
authorized or launched by this implementation.
