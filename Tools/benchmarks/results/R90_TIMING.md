# Portable single-worker R90 step timing

This pipeline selects the fastest **observed recall >= 0.90** main SPANN point
in each of `sel50`, `sel20`, `sel10`, `sel5`, `sel1`, and `sel01`, for one explicit
physical index. It does not interpolate to exactly 90%, change search settings,
rebuild an index, download data, or overwrite formal QPS observations.

It supports Linux, raw STATIC SPANN/BKT postings, UInt8/128D/squared L2,
top-100, the original 1000-query cohort, and one query worker. Quantized/rerank,
SPDK and io_uring paths are not supported. Follow the input, immutable-index,
Landlock, Python dependency and NUMA requirements in [README.md](README.md).

## 1. Build a matched pair, then accept ordinary reference curves

From the repository root:

```sh
python3 Tools/benchmarks/results/build_r90_timing.py \
  --output /absolute/new-matched-build --jobs 4
```

The output must be fresh and outside the checkout. This builds the existing
`nativeBench` target twice from identical sources, with work diagnostics off.
Only the timing build enables `SPTAG_QUERY_STEP_TIMING`. `build.json` records
source and binary hashes and build commands; `ordinary.log` and `timing.log`
retain build output. No existing `Release/` binary is overwritten.

Use `/absolute/new-matched-build/ordinary-bin/nativeBench` as `Binary` in the
[ordinary experiment workflow](README.md#register-and-run-without-hidden-query-overrides).
Set top-k and query/warmup windows to 100 and 1000 respectively, and use
`Study=main`. Run and publish the reference curves for all six scenarios.
Every scenario must contain an accepted recall >= 0.90 point. The timing
pipeline does not automatically enlarge a search grid that fails this threshold.

Each reference must be a completed, sealed `native_results.py` experiment
using the **ordinary executable from its own matched build**. Keep its
experiment INI, inputs, captures, build manifest and source tree unchanged.
Different search variants or native source versions may use separate matched
builds and reference experiments.
All supplied references must share source-vector/attribute identities, identical
query bytes and the same CPU/memory-node policy.

Historical/session-specific producers are **not** automatically adapted.
An old event cannot be attributed to a newly compiled core just because its
parameters or index name match. If a fastest selected point has no supplied
exact reference, preparation fails rather than silently selecting another
point or substituting a client. A copied/rebuilt index on another host needs
its own physical `IndexVersion` and locally accepted reference experiments;
do not relabel it as another machine's index.

## 2. Prepare and run diagnostics

Copy [examples/r90-timing.ini](examples/r90-timing.ini) outside the checkout,
then fill in absolute local paths. `DataDirectory` is the registry containing
the accepted reference observations; `Directory` is a fresh, separate output
directory. It must not overlap the registry or immutable index.

```sh
python3 Tools/benchmarks/results/r90_timing.py prepare \
  --config /absolute/configs/r90-timing.ini
python3 Tools/benchmarks/results/r90_timing.py run \
  --directory /absolute/new-r90-diagnostics
```

`prepare` selects the six points and freezes complete generated INIs,
native binaries/libraries, source references and the execution plan.
`selected.json` retains the selected event IDs, variants, recall, QPS and
parameters. Each generated native INI selects exactly the original nprobe.
`run` inherits each reference's CPU/memory-node policy and batch timeout.
Adjust a reference timeout before registering the reference experiment, not
after sealing it. One query worker does not imply binding the whole process
to one CPU.

The protocol is fixed:

- One native index/query-corpus load per matched reference group.
- One full 1000-query warmup per scenario within that resident client, not
  before every parameter or repetition (`WarmupPolicy=once_per_predicate`).
- Three complete 1000-query timing repetitions and three paired clock-off
  control repetitions per scenario, alternating pair order.
- Each measurement is followed by a separate untimed 1000-query native replay.
  There is **no minimum 30-second duration** or repeated cycling.
- IDs, distances and SSD work must be byte-identical to the accepted ordinary
  reference in every repetition; all six scenarios must complete.

Clock-off controls use the **timing-enabled executable with scopes inactive**.
They are diagnostic controls, not new ordinary QPS. Both modes are explicitly
marked diagnostic and rejected by the ordinary results publisher. Legacy
`PhaseTiming` logging and work-counter builds remain separate.

Logs, captures and explicit failure records are retained. Failed/interrupted
runs are not publishable and are not resumed in place; use a fresh output
directory. The native process is write-confined to its output group.

## 3. Publish a diagnostic table

```sh
python3 Tools/benchmarks/results/r90_timing.py publish \
  --directory /absolute/new-r90-diagnostics \
  --data-dir /absolute/results --version-order 1
```

Use a strictly newer version order when replacing a prior timing report in
the same dataset/study/variant/algorithm scope. Publication rechecks sealed
artifacts, reference provenance, exact output parity and the entire report.
Selected source events must still be active. History compare-and-swap rejects
a concurrent registry update; rerunning publication after inspecting that
update is safe and idempotent.

The append-only table has study `query-step-timing` and table/variant
`single-r90-step-timing`. Formal points, experiment views, coverage, index
retirements and unrelated tables remain unchanged. Each scenario includes
its source event/variant/parameters, observed recall and reference QPS;
per-stage means/shares; per-repetition mean, p50/p95/p99; and three paired
clock-on/off latency ratios. `publication-receipt.json` records the accepted
event and history head. Never hand-edit `current.json`.

## Timing meaning and limitations

`steps.ns.u64` is headerless little-endian uint64 `[1000,15]`, in the order
below. Nested scopes account **exclusive application wall time**, without
double-counting asynchronous overlap. `latency_us.f64` records the enclosing
per-query interval. `report.json` adds `timer_boundary` so all stages sum to
that interval for every query.

| Stage | Exclusive interval |
| --- | --- |
| `wrapper` | Predicate preparation, return adaptation and capture copying outside native stages |
| `head_prepare` | Native routing/workspace setup outside BKT search |
| `h1_search` | Ordinary BKT/H1 traversal, excluding hierarchy supplementation |
| `hierarchy_supplement` | Hierarchy expansion, including its filtering, distances and heap work |
| `posting_prepare` | Posting selection/setup outside retrieval and finalization |
| `aio_prepare` | Request preparation and callback/dispatch bookkeeping outside nested stages |
| `aio_lock_wait` | Acquisition of the shared AIO-context mutex |
| `aio_submit` | `io_submit` syscall wall time |
| `aio_completion_wait` | `io_getevents` syscall wall time |
| `scan_other` | Final posting scan parsing/bookkeeping outside the operations below |
| `filter_dedup` | Final raw posting predicate and deduplication checks |
| `distance` | Final raw posting decode/distance computation |
| `topk_update` | Final raw posting result-heap updates |
| `finalize` | Native result/statistics/workspace finalization |
| `decompress` | Posting decompression, when used |
| `timer_boundary` | Enclosing per-query timer boundary overhead |

Per-record clocks can materially perturb performance and cache behavior.
Always report the paired overhead ratios alongside the breakdown; do not
substitute clock-on QPS for ordinary QPS or automatically subtract that ratio
from individual stages. Nanosecond storage is not a nanosecond-accuracy claim.
Per-query intervals exclude result destruction and between-query loop overhead;
their sum is not the cohort interval used for formal QPS.

Buffered reads may execute synchronously during `io_submit`, so report both
submit and completion-wait time. Page-cache hits need not touch the SSD.
Completion batching does not expose exact per-request completion timestamps.
These numbers are **not physical SSD/flash service latency** and do not require
cache dropping, block-device tracing or conversion to direct I/O. Head/hierarchy
work is intentionally separate from final-posting filter/distance/top-k work.
