# Portable single-worker posting ablations

`posting_ablation.py` prepares, runs, plots and publishes three matched
controls: global O without hierarchy, limited H without hierarchy, and full
H plus hierarchy. All use one ordinary native executable and one immutable
raw STATIC H/O index. No dataset or index download/rebuild is performed.
The [mode definitions and attribution limits](README.md#global--limited--hierarchical-posting-ablations)
apply: global-to-limited changes H1 admission as well as SSD region, while
limited-to-full changes hierarchy activation. This is not a rebuilt upstream
SPANN baseline, an SSD-byte-only isolation, or a resident-memory experiment.

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

Create the six native INIs, `batch.ini` and `experiment.ini` using the
[existing examples](examples/experiment.ini) and README input bindings.
`ReferenceExperiment` is only a **configuration template**: it need not have
been run or published. It must pass the ordinary native input checks and
specify exactly one distinct configuration per scenario, with
`EnablePostingNavigation=true` and `GlobalPostingSearch=false` or omitted.
Existing repeated cases may refer to that same configuration.
Set `Binary` to the freshly built `nativeBench`; use unique index/client
identifiers for the actual local artifacts, available CPU/memory nodes, and
an adequate whole-batch `TimeoutSeconds` (the local campaign uses 172800).
Do not reuse another host's physical-index identifier.

Choose and freeze all non-mode controls explicitly. To reproduce the current
campaign's controls, change these fields in copies of `examples/native.ini`:

| Scenarios | MaxCheck | PostingMatchRatePercent | PostingNavigationWidth |
| --- | --- | --- | --- |
| sel50, sel1, sel01 | 16324 | 12.5 | 8 |
| sel20, sel10 | 16324 | 12.5 | 32 |
| sel5 | 2048 | 15 | 8 |

The remaining native example settings already match: one worker,
HashTableExponent=4, MaxDistRatio=8, SearchPostingPageLimit=3,
PostingAnchorCount=0, PostingAdditionalMaxCheck=2048 and PostingMatchWindow=1024.
The controller inherits those controls unchanged, even when inactive in a mode.
It replaces only the two mode flags, the nprobe sweep, query/warmup counts and
truth-subset bindings. It does not choose MaxCheck/width from past QPS results.

## Prepare and execute

Copy [examples/posting-ablation.ini](examples/posting-ablation.ini) outside the
checkout. Replace the three absolute paths, select resource reserves for the
local machine, and retain or explicitly revise the pilot/grid policy.
Campaign output must be fresh and separate from the reference output, registry,
index and protected inputs. `DataDirectory` may be a new registry or an
existing compatible one; do **not** initialize it with a pilot experiment.

```sh
python3 -B Tools/benchmarks/results/posting_ablation.py prepare \
  --config /absolute/configs/posting-ablation.ini
python3 -B Tools/benchmarks/results/posting_ablation.py run \
  --directory /absolute/posting-ablation-run1
```

Preparation validates full truth, creates an authenticated first-32-query
subset, writes all 18 pilot profiles, and freezes input/configuration/source
identities. The binary has a full digest; large immutable data/index files use
the documented identity guards, not a repeated 1B-vector scan. Source binding
records preparation-time bytes, not an independent compiler attestation.
Keep the source checkout, binaries, dependencies, INIs and index unchanged until
publication. Git commit/push without changing those file bytes is safe.

The default pilot has 90 points, one repetition, and is **never published**.
For each scenario/mode, select the smallest pilot nprobe reaching 0.97 recall,
with a minimum cap of 512. If none reaches the target, retain 16384 and record
`pilot_target_reached=false`; a small-cohort cap is not a guarantee of full-cohort
recall. Run all declared grid values through that cap on the complete cohort,
with three repetitions and reversed case order in repetition 2.

Each phase loads the index once. Each mode/predicate receives one full
phase-cohort warmup (32 pilot queries or **1,000 formal queries**), not a partial
warmup before each point. A completed cohort advances immediately to the next
nprobe; there is no 30-second minimum. Load, warmup, replay, payload writes and
validation are outside QPS. Memory/disk/AIO reserves are checked before each phase.

## Outputs, publication and failures

`status.json` reports the current phase. `pilot/native.log` and
`measurement/native.log` preserve native progress. `grid-selection.json`
records all 18 selected caps, target-reached flags and the accepted pilot hash.
The full grid and generated INIs are frozen again before measurement.
Both phases reuse `native_results.py`'s exact predicate/distance/capture,
repetition, lifecycle, executable and index checks.

After acceptance, `run` automatically creates:

- `measurement/plots/posting_ablation_recall_qps.{png,pdf}` and the
  corresponding `_80_100` zoom files.
- `measurement/plots/recall_qps.csv` and `metadata.json`, retaining every
  measured point, QPS median and repeat range.
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
