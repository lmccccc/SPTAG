# Frozen visited-storage CPU attribution (diagnostic only)

No optimization, production promotion, search override, or posting-mode run.
The exact sealed `postfilter_visited_storage_20260918` executable is loaded
using its ELF interpreter's `--preload` option with a diagnostic sampler.
The environment contains no `LD_PRELOAD` or search/data overrides.

Exactly eight processes: CPU-clock-only graph/match controls, sampled
graph/match, sampled match/graph, CPU-clock-only match/graph. Each retains
the frozen native INI and harness: 1000 warmup, 1000 untimed storage checks,
1000 ordinary-body queries, 1000 untimed semantic capture. Sampling covers
only ordinary-body boundaries 5–6 with capture/profile both false. Requested
period is 200us; actual delivered cadence is measured, not assumed.

The bounded preallocated, query-thread SIGPROF/ucontext sampler derives from
`navix_profile_fix_20260918/ClockAudit.cpp`. No handler allocation, locks,
unwinding or symbolization. Controls use the same boundary audit but no timer.
This is diagnostic timing, not a replacement for frozen ordinary latency.

Reproduce from workspace root:

```sh
P=SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_visited_profile_20260918
python3 "$P/profile.py"
python3 "$P/symbolize.py"
python3 "$P/analyze.py" --no-seal
python3 "$P/test_profile.py"
python3 "$P/analyze.py"
```

The runner refuses to rerun existing points. Frozen source/core are not cloned
or rebuilt; only isolated sampler DSOs and exact-flags-plus-g1 debug objects
are built. Debug objects must pass executable-section equivalence before
being used to map raw PCs. No debug binary is executed. Raw samples, module
maps, validation, commands, CPU/elapsed times, report and hashes live under
the identically named comparisons/toolchains directories. After sealing:
**STOP / IDLE**.

For an existing sealed result, only `python3 "$P/test_profile.py"` is a
read-only validation selector; the execution and sealing entry points refuse
to overwrite a completed milestone.

Measured outcome: four sampled windows collected 3528 PCs, no buffer drops,
about 1ms delivered CPU cadence. Graph/match controls use 0.735786/1.016798ms
thread CPU/query; sampled windows 0.737336/1.026733ms. Qualification accounts
for 0.368073ms in match (including shared support/validity functions whose
callers cannot be separated). Auxiliary CSR/signature/posting work is zero;
the complete query still reads 24 ordinary final SSD postings. Exact outputs
and native trajectory match frozen results. See the new comparisons
`REPORT.md`, `cpu_attribution.json`, and `completion.json` for qualifications,
absolute residence, hashes and the recommendation-only next step.
