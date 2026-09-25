# Matched original/current native baseline

This is a new matched experiment, not a continuation or reinterpretation of old
curves. All cases share the current flat H1 graph/tree, unchanged hierarchy
catalogs/CSR, SSD bytes and metadata through an owned buffered loader view.
Only that private loader's IndexDirectory and UseDirectIO differ from the
existing direct-IO view. Protected inputs and both native cores are fingerprinted.
Historical graph/tree,12/8192-budget and different-window points are excluded.

Cases are `h1_original`, `h1`, `h3`, `supplier`. Original means authenticated
Sep9 pre-supplier revision3552194536cb01dd70099e955a29e235a0cf4d2e plus saved
source.diff, not current mode=ordinary. It is not asserted to be the precise
historical blue-curve executable. The suggested `spannaclbench-frozen` array
binary contains H1Supplier symbols and modified Sep16 object paths; it cannot
serve as the original core. Because actual original linkable objects were not
recovered, a separate original core build is necessary.648 source files and
the seven independently reconstructed audit files are authenticated.

Current cases link unchanged native-default-admission libraries and wrapper
object (accepted summary6917dd...), not either later filter-cost or fusion
version. H3 uses authentic H2Only navigation through the same index view.
The supplier retains ratio0.5/minimum physical degree16.

Both executables compile exactly the same `MatchedBench.cpp`, derived from the
authenticated original benchmark. Differences are configuration and untimed
capture adapters only. The ordinary block uniformly performs native searches,
records final IDs/distances and native SSD statistics into preallocated arrays.
There is no Shortcut observation copy, trace hashing or capture dispatch in
that block. Ordinary and subsequent untimed captures must match every final
ID/distance and deterministic per-query work field. H3 contributing-posting
ownership is not treated as deterministic work. No core algorithm changes.

Every benchmark setting is in checked-in native INIs: BUFFERED IO,
1000warmup+1000measured from offset0, topk10, MaxCheck2048,
HierarchyMaxCheck512, hierarchy ratio0.666666, pages15, one query thread,
NUMA CPU/memory node2 (not one physical CPU). Native SearchSweep.NProbe is
parsed with the repository's strict parser. Each process calls LoadAll once
and resets the native cached workspace between probes, including descending
arrays. Buffered handles are observed throughout each native process; a direct
posting handle causes failure.

## Reproduction and mandatory boundaries

`prepare_original.py` reconstructs the original core. Build its isolated
`original/source` with CMake Release/SPDK OFF/ROCKSDB OFF, target spannaclbench,
parallelism2. `prepare.py` creates the owned view and registers56 native INIs.
Build this folder's CMake targets with ORIGINAL_TOOL pointing at that original
toolchain and CURRENT_TOOL pointing at the frozen native-default toolchain;
no current library rebuild is needed.

Run `ctest` and `python3 test_protocol.py`, then:

```
python3 run.py freeze
python3 run.py smoke
```

Smoke is ONLY nprobe24, unfilter+broad, four cases and two rotated repetitions:
eight points,16 ordinary batches and16 native loads. Stop for review.

Later, only when separately authorized:

```
python3 run.py stage --stage unfilter_broad
```

Other explicit groups are `medium_extreme` and `numeric_mixed`. Each group
has88 unique points,176 batches and16 native loads, using the11-probe grid
and reversed repetition2. No command chains stages. Completed job records are
reused; failed attempts remain visible and are not retried automatically.
No canonical full summary or published plot is produced by this bounded task.
After all three groups are explicitly authorized and completed, a separate
`python3 run.py finalize` checks all264 paired points before writing summary.json.

## Flat output schema

`summary.smoke.json` and `summary.stage_<group>.json` are flat lists with:
case, scenario, nprobe, recall/recall_at_10, mean_latency_ms,
qps=1000/mean_latency_ms, repetitions, mean_latency_ms_runs, min_ms/max_ms,
mean_returned, underfill, actual_work, io, protocol_fingerprint,
index_fingerprint, core_fingerprint, common_timed_body_fingerprint,
sweep_execution and nprobe_ini_api. Final-output and native-work hashes permit
exact original/current and forward/reverse parity checks.
`operations.*.json` includes per-process wall time and native warmup/ordinary/
capture durations. LoadAll may defer physical loading into warmup; its API
duration is not total loading cost. Rich supplier frame audits are not repeated.
Smoke capture explicitly verifies actual unfilter native-default admission and
zero helper/parent/child/CSR work; H3 H1-only counters are unavailable, not zero
hierarchy work.

Main owns renderer/published figures. No plot or production file is edited.

## Agreed renderer contract

`renderer_contract.py` exports the renderer-facing protocol to
`comparisons/matched_baseline_20260917/renderer_contract/protocol.json`.
It preserves the original registration and smoke summary rather than changing
their identities. Renderer rows expose `ordinary_ms`, `ordinary_ms_runs`,
`protocol_id`, `index_fingerprint`, `harness_fingerprint`, `core_fingerprint`
and the existing case/scenario/nprobe/recall_at_10/qps/sweep_execution fields.
The protocol uses the agreed lowercase setting names, full11-value `nprobe`
array, original/current core fingerprints, `io_mode=buffered`,
`original_reference_verified=true` and `timed_body_shared=true`.
Verification rests on the already completed authenticated reconstruction and
linked binaries, not the obsolete supplier-era object suggestion.

`protocol_id` is SHA256 of the canonical protocol object excluding protocol_id
itself. It includes dataset-input and workload-definition hashes, all native
configuration hashes, loader identity, authenticated original source/audit
identities, both core identities and the common source/timed-body/linked-binary
harness identity. Every exported series shares these common identifiers.

Completed stage/final summaries are automatically exported only after contract
validation. `--partial-scenarios` may omit entire scenarios, never cases or
probes within an included scenario. The existing nprobe24 smoke cannot be
rendered as curves against the full declared grid, even with that flag.
`python3 renderer_contract.py --summary summary.smoke.json --diagnostic-smoke`
produces only `summary.smoke.incomplete.json` plus an explicit non-renderable
status. It does not pad, interpolate, change the declared grid or launch queries.
No native rebuild is needed for this schema-only export.
