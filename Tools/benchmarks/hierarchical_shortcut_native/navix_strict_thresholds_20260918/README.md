# Strict posting sparsity correction (2026-09-18)

Isolated successor of frozen `navix_thresholds_20260918`. Active posting
requires `0 <= NavixPostingThreshold < NavixTwoHopThreshold`. Equality and
reversal fail descriptively through shared native validation in the INI parser,
supplier, injectable hooks and public route helper. Finite/range/unknown/trailing
junk/environment rejection remains. Disabled NaviX-only posting may retain an
irrelevant equal cutoff; zero posting threshold explicitly disables posting.

Fixed **untuned** posting cutoff `0.01`, with only two-hop cutoffs `0.1` and
`0.05`. No optimum or reduction in posting branch count is claimed:
discrete ratios can make both `0.01` and `0.05` select only `e=0`.
Broad labels do not preclude locally empty eligible neighborhoods.

`d` counts native neighbors before sentinel; `e` includes eligible visited
neighbors. Route precedence is d0/no-neighbors, then `e/d >= T2` existing
FILTERED one-hop, then enabled `e/d < Tpost` posting. Otherwise immutable
`.4*(d*e+e)>2*d-e` selects directed, else full two-hop. Failed posting falls
back to graph two-hop in the same sparse domain. Native result-only postfilter
is not restored. Candidate order, queues, budgets, full selected rows, CSR,
own points/gains, aliases, ties, mutable deletion and qualification cache are
unchanged. Empty predicates retain their native no-added-work fast path.

## Bounded protocol

Exactly three fresh Broad n24 points: proper frozen result-only postfilter
graph and the two new combined settings. Two reverse repetitions: **six**
single-load processes. Native `[24]`; 1000 warmup, 1000 measured, offset0,
topk10; matched buffered flat view; CPU+memory NUMA2; querythread1;
MaxCheck2048, Hierarchy512, initialratio0.666666, pages15.
Capture/profiler off during ordinary timing. Unchanged timed-body SHA256:
`9f412c63b71f7310e09180dff0858ccef3957bd20d7a96c34fdb440e3d559d53`.
Separate untimed capture checks IDs/distances/SSD parity. Four diagnostic-only
32-query sparse/empty replays validate positive posting and zero added work.
No old invalid configuration rerun, extra baseline/NaviX-only campaign or sweep.

Historical `.05` posting output/work/recall comparisons are not paired timing
comparisons. Semantic correction pass is independent of latency performance.

## Reproduction and sealing

From the project root, into an unused namespace only:

```sh
H=SPTAG/Tools/benchmarks/hierarchical_shortcut_native/navix_strict_thresholds_20260918
python3 -B "$H/prepare.py" --initialize
python3 -B "$H/build.py"
python3 -B "$H/small_replay.py"
python3 -B "$H/run.py"
python3 -B "$H/finalize.py"
```

Release, SPDK/ROCKSDB off, `-j2`; private static source/build/harness.
Reports, exact commands, full traces and hashes live under the matching
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/` namespace.
Old sources/builds/configurations/evidence, production libraries, plots and
`OPERATOR_STOP` remain unchanged. Main-owned GettingStart docs are excluded
from this task. Stop IDLE after this milestone; no tuning or promotion.
