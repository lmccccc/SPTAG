# Bounded NaviX two-hop thresholds (2026-09-18)

Isolated successor of `navix_profile_fix_20260918`. Only the graph two-hop
cutoff changes: native `NavixTwoHopThreshold=0.5|0.1|0.05`. Native
`NavixPostingThreshold=0.05` and all existing filtered-one-hop semantics remain
unchanged. This is **not** restoration of native result-only postfilter.
INI values are mandatory, finite and validated; active posting threshold
must not exceed two-hop threshold. Unknown controls and native environment
overrides fail before index loading.

For a native row, `d` counts neighbors before its sentinel and `e` counts
eligible entries **including visited**. Zero degree explicitly selects no
neighbors. Posting mode first selects posting when `e/d < 0.05`. Otherwise,
`e/d >= NavixTwoHopThreshold` selects filtered one-hop, including equality.
Below the threshold, `.4*(d*e+e)>2*d-e` selects directed two-hop; otherwise
full two-hop. Failed posting uses the same configured graph cutoff.
Combined mode at equal `0.05` cutoffs has **no direct two-hop interval**, but
posting failures may still execute two-hop.

No changes to qualification caching, native ordering/queues/checked budget,
own points, mutable deletion checks, aliases, tie handling, complete selected
rows or signed CSR traversal. Empty predicates retain the ordinary native
fast path with zero new qualification work. `MatchedBench.cpp` keeps the
ordinary timed body SHA256
`9f412c63b71f7310e09180dff0858ccef3957bd20d7a96c34fdb440e3d559d53`.
Diagnostics run afterward and compare exact IDs, distances and SSD work.
The existing 11-column decision schema separately records outer route and
fallback route; report counts do not conflate direct and fallback two-hop.

## Fixed scope

Seven Broad n24 points: frozen proper native H1 result-only postfilter graph,
then successor NaviX-only and combined posting at each cutoff. Two
reverse-order repetitions: exactly 14 single-load ordinary processes.
Native `[24]` array, 1000 warmup +1000 measured, offset 0, topk10, buffered
matched flat index, CPU and memory NUMA2, one query thread, MaxCheck2048,
HierarchyMaxCheck512, initial ratio0.666666, pages15. No preload or profiler.
Separate diagnostic-only 32-query sparse and unfiltered replays cover both
modes and all three settings; no Extreme1000/full-curve campaign.

## Reproduction

From `/mnt/nvme/baotonglu/mocheng`, in a new unused output namespace:

```sh
H=SPTAG/Tools/benchmarks/hierarchical_shortcut_native/navix_thresholds_20260918
python3 "$H/prepare.py" --initialize
python3 "$H/build.py"
python3 "$H/small_replay.py"
python3 "$H/run.py"
python3 "$H/finalize.py"
```

The completed namespace is sealed and intentionally refuses duplicate runs;
do not overwrite it. Exact compiler/ctest/protocol commands are retained in
`build_commands.json`, and every process retains its `command.json`.
Build uses isolated Release static libraries with SPDK/ROCKSDB off and `-j2`;
compiler scratch stays within the new toolchain directory.

Evidence:
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/navix_thresholds_20260918`.
Report includes the flat seven-point summary, repetition ranges, recall,
direct/fallback counts, second-hop rows, cache/native/SSD work, frozen `.5`
parity, and sparse branch proof. Source/config/library/binary and evidence
hashes are sealed in the report and manifests.

All previous source, binaries, measurements, production code/libraries,
plots and `OPERATOR_STOP` are read-only. Main-owned GettingStart docs are
outside this task. No automatic promotion or additional cutoff/tuning.
