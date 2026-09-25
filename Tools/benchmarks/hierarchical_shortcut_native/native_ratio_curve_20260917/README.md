# Staged native retained-ratio curves

Preregistered matrix: six predicates, H1/H3/supplier, eleven nprobes
`[16,24,32,48,62,80,96,128,192,256,384]`, two ordinary repetitions:
198 unique points,396 measured batches,36 native index loads.
This experiment does not authorize executing the whole matrix in one turn.

The accepted native-default search implementation is frozen. This benchmark
links its **unchanged native static libraries and CoreInterface object**.
Only the executable's compatibility layer changes: allow the original H3
route with ordinary mode; correctly distinguish H3 capture from the H1
injection observer; compare capture/ordinary native SSD counters; and record
coarse load, capture, warmup, measured-batch and serialization durations.
There are no new query-level fine clocks, algorithm/cache/hook changes or
old Engine/NativeAdapter runtime. The accepted source archive, libraries,
wrapper object and executable are hashed in `accepted_provenance.json`.

H3 uses the authentic existing H2Only native setting (which navigates the
saved H3 top graph), including its predicate-guided behavior. Boundary
fixtures compare exact heads, own IDs, final IDs/distances and native SSD
work with authenticated prior H3 captures. Old timings are never reused.
H3 does not enter the H1 injection observer: its zero-valued raw observer
fields are **unavailable navigation counters**, not measured zero hierarchy
work. Summary H3 navigation work is omitted and marked unavailable; native
SSD work and final recall remain measured.

The supplier keeps retained eligible ratio0.5/minimum physical degree16,
including visited neighbors, complete rows, signature-before-upper-distance,
predicate-before-H1-distance (structural exception) and native-only stopping.
Certified actual empty predicates use the accepted default admission.
Every native process logs the actual immutable own-point certificate.
All native search parameters are checked-in INIs. The array interface remains
`[SearchSweep] NProbe`, with reverse order in repetition2 and rotated
scenario/mode order. Each probe gets1000 warmup and1000 measured queries.
One query thread, NUMA CPU/memory2, O_DIRECT/page15; topk10, MaxCheck2048,
HierarchyMaxCheck512 and HierarchyInitialProbeRatio0.666666 are unchanged.

## Explicit stages and recovery

From this directory, after the standalone CMake benchmark build:

```
python3 run.py init
python3 run.py fixtures --stage unfilter_broad
python3 run.py stage --stage unfilter_broad
```

Stop after that stage. Later, only when explicitly authorized, use
`medium_extreme` or `numeric_mixed` instead. Each stage has66 points,
132 measured batches and12 ordinary native loads. Stage selection is
mandatory; no command continues automatically into another stage.
`--retry-incomplete` explicitly permits a new numbered native attempt while
preserving failed/interrupted logs. Completed native processes can resume
their audits without another load. Completed job records are reused, not
rerun. Native exit, actual LoadAll/physical component events, O_DIRECT and
every probe/workspace lifecycle are checked.

First repetitions receive full native result validity, degree/visited,
signature, unique/full-row and native budget-boundary audits. Second
repetitions must be byte-identical to those raw audited captures and match
all non-timing native counters. Such audit reuse is explicitly labeled;
work is never inferred from invocation counts or substituted from a
different implementation. Compression is lossless and rehashed.
Protected files are hashed once at initialization, then their metadata is
checked at stage end; changed metadata requires rehashing. Native linked
objects/libraries are rehashed at stage boundaries, not at every probe.

Partial artifacts are `summary.stage_<stage>.json` and `stage_<stage>.json`.
They contain actual per-process/per-batch operation durations and audit/
compression wall costs, as well as the R-compatible recall/QPS/work fields.
No canonical `summary.json` exists until all198 points have two repetitions
and a separate explicit `python3 run.py finalize` passes the completeness
gate. Do not publish partial points as completed curves.

New output root:
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/h1_native_ratio_curve_20260917_v3`.
The first preflight and its immutable snapshot remain in the unsuffixed output.
Its generated INIs incorrectly included whitespace after `=`, which the frozen
native reader preserves; mode validation rejected it before any query.
The new registration records this formatting-only correction explicitly.
`NATIVE_OPERATION load` measures the native LoadAll entry; lazy physical
component loading, when present, is included in first capture time, not
misrepresented as measured-query compute or pure LoadAll time.
MAIN owns plotting, published figures and overall completion.

The v2 preflight is also preserved. An added per-query assertion initially
treated contributing-posting ownership as deterministic. Its H3 q7/nprobe384
diagnostic showed377 versus378 contributing postings with identical384
loaded postings,12534 scanned records,4163 dedup skips,3864 distances/matches
and1933 pages. The existing authentic `h3_core_work` already excludes this
IO-completion-order-dependent ownership count. V3 uses that same rule,
logs each capture/ordinary ownership difference after the timed loop, and
preserves both ordinary repetition values in summary rows. No deterministic
distance/page/scan/result check is relaxed and no native library changes.
