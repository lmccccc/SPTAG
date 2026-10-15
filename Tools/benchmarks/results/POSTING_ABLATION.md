# Portable single-worker posting ablations

`posting_ablation.py` prepares, runs, plots and publishes three matched
controls: global O without hierarchy, limited H without hierarchy, and full
H plus hierarchy. All use one ordinary native executable and one immutable
raw STATIC H/O index. No dataset or index download/rebuild is performed.
The [mode definitions and attribution limits](README.md#global--limited--hierarchical-posting-ablations)
apply: global-to-limited changes H1 admission as well as SSD region, while
limited-to-full changes hierarchy activation. This is not a rebuilt upstream
SPANN baseline, an SSD-byte-only isolation, or a resident-memory experiment.

**Reuse the previous parameter table exactly.** The controller changes only
`GlobalPostingSearch` and `EnablePostingNavigation` in each native profile.
There is no pilot, R90 selection, target-recall rule, new nprobe grid, automatic
expansion or automatic truncation. Slow original points remain original points;
changing their coverage requires a separately agreed experiment.

## Local prerequisites

Use the Python/Linux/native dependencies in [README.md](README.md), plus
`Rscript` with `ggplot2` and `jsonlite`. Build a **fresh ordinary Release client**
outside the checkout and index directories; do not overwrite a historical
executable, library or active build:

```sh
cmake -S /absolute/SPTAG -B /absolute/ablation-client/build \
  -DCMAKE_BUILD_TYPE=Release -DSPDK=OFF -DROCKSDB=OFF -DURING=OFF \
  -DSPTAG_QUERY_WORK_DIAGNOSTICS=OFF -DSPTAG_QUERY_STEP_TIMING=OFF \
  -DSPTAG_OUTPUT_DIRECTORY=/absolute/ablation-client/bin
cmake --build /absolute/ablation-client/build --parallel 4 --target nativeBench
```

Supply the unchanged completed schema-2 truth described in README. This
workflow uses all 1,000 queries and Recall@100 for `sel50`, `sel20`, `sel10`,
`sel5`, `sel1`, `sel01`. The existing six-panel plot labels describe the current
SIFT1B attribute workload (49.829%, 19.848%, 10.053%, 4.993%, 1.001%, 0.100%).
Preparation rejects truth whose exact selectivities round to different labels;
it never relabels another attribute distribution as this experiment.

Supply the original native INIs, `batch.ini` and `experiment.ini`.
The [existing examples](examples/experiment.ini) show the format and input
bindings; their illustrative values are not a replacement for an existing
experiment's approved parameter table.
`ReferenceExperiment` is only a **configuration template**: it need not have
been run or published. It must pass the ordinary native input checks and
cover the six scenarios, with
`EnablePostingNavigation=true` and `GlobalPostingSearch=false` or omitted.
Every original configuration and its original `SearchSweep.NProbe` belong in
the reference. Multiple MaxCheck/width/rate configurations per scenario are
supported and retained separately. Existing repeated cases may refer to the
same configuration; identical parameter points under different configuration
files are rejected rather than silently duplicated.
Set `Binary` to the freshly built `nativeBench`; use unique index/client
identifiers for the actual local artifacts, available CPU/memory nodes, and
an explicitly agreed whole-batch `TimeoutSeconds`.
Do not reuse another host's physical-index identifier.

The source cohort and warmup must already cover all 1,000 queries. Each generated
native profile must equal its original profile after removing just the two mode
flags: even the original nprobe order, initial InternalResultNum, inactive
posting controls, diagnostic flags and input bindings are preserved.
Preparing and publishing both verify that constraint. The full method is
remeasured with the same ablation executable, not spliced from historical QPS.

## Prepare and execute

Copy [examples/posting-ablation.ini](examples/posting-ablation.ini) outside the
checkout. Replace the three absolute paths, select resource reserves for the
local machine, and bind the complete original reference experiment.
Campaign output must be fresh and separate from the reference output, registry,
index and protected inputs. `DataDirectory` may be a new registry or an
existing compatible one.

```sh
python3 -B Tools/benchmarks/results/posting_ablation.py prepare \
  --config /absolute/configs/posting-ablation.ini
python3 -B Tools/benchmarks/results/posting_ablation.py run \
  --directory /absolute/posting-ablation-run1
```

Preparation validates full truth, writes the three exact mode copies of each
original profile, and freezes input/configuration/source identities.
`plan.json` records each reference configuration, all original probes and both
reference/ablation point counts. The binary has a full digest; large immutable data/index files use
the documented identity guards, not a repeated 1B-vector scan. Source binding
records preparation-time bytes, not an independent compiler attestation.
Keep the source checkout, binaries, dependencies, INIs and index unchanged until
publication. Git commit/push without changing those file bytes is safe.

For N distinct original parameter points, there are exactly 3N ablation points
and 9N measured point-repetitions: three modes, each measured three times.
Repetition 2 reverses profile order, without changing any profile's probe order.

The index loads once. Each mode/predicate receives one full **1,000-query**
warmup, shared across that mode's configurations and repetitions, not a partial
warmup before each point. A completed measured cohort advances to replay and the next
nprobe; there is no 30-second minimum. Load, warmup, replay, payload writes and
validation are outside QPS. The existing native client performs a separate
1,000-query deterministic replay per point; this is not an additional warmup.
Memory/disk/AIO reserves are checked before execution. Reusing an original
parameter does not guarantee similar runtime after disabling hierarchy.

## Outputs, publication and failures

`status.json` reports the current phase and `measurement/native.log` preserves
native progress. The complete grid and generated INIs are frozen at preparation;
there is no runtime grid selection. Execution reuses `native_results.py`'s exact predicate/distance/capture,
repetition, lifecycle, executable and index checks.

After acceptance, `run` automatically creates:

- `measurement/plots/posting_ablation_recall_qps.{png,pdf}` and the
  corresponding `_80_100` zoom files.
- `measurement/plots/recall_qps.csv` and `metadata.json`, retaining every
  measured point, QPS median and repeat range. Separate lines retain each
  original control configuration; MaxCheck/width/rate configurations are not
  pooled into a single fixed-configuration curve. CSV includes complete controls.
- `publication-receipt.json` and sealed campaign `completion.json`.

Formal events append under `study=ablation-posting-path`,
`variant=global-limited-full` in `DataDirectory/history.jsonl` and atomic
`current.json`. An existing registry must permit this physical index/version.
Publication previews and preserves unrelated observations, main curves, tables,
retirements and coverage, then uses a history-head compare-and-swap. It does not
silently change selected indexes or retire a baseline. Explicit newer ablation
versions follow the registry's normal selection policy.

Execution is fresh-only and protected by a nonblocking campaign lock.
`failure.json` retains execution failures; partial measurements never publish.
Start a separate fresh campaign after correcting an execution failure.
If measurement completed but plotting/publication failed, retain all native
evidence and retry without remeasurement:

```sh
python3 -B Tools/benchmarks/results/posting_ablation.py publish \
  --directory /absolute/posting-ablation-run1
```

`publication-failure.json` records the failed attempt and remains as evidence
after a successful retry. If plotting left a partial `measurement/plots`,
move that directory to a named backup outside `measurement/plots` before retry;
the plotter intentionally refuses to overwrite artifacts. Repair a stale
registry with the existing `update_results.py refresh` command first.
Successful re-publication is idempotent and verifies the campaign output seal.

The earlier adaptive-pilot configuration is superseded. `PilotNProbe`,
`NProbeGrid`, `PilotTargetRecall` and `MinimumMaxNProbe` are rejected, not ignored.
Use a fresh schema-2 exact-reference campaign; do not mutate or relabel an
existing adaptive run or publish its partial captures as the corrected experiment.
