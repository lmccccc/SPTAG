# Reproducible native logs and result JSON

This is the repository-owned path from an ordinary `nativeBench` batch to
validated repetitions, append-only `history.jsonl`, and atomic `current.json`.
It has no dependency on a Copilot session, a paper checkout, dated campaign
directories, or downloaded datasets. The registry preserves the existing
**schema 1** event/history/current formats, including exact-index retirement.
Native log and binary payload formats are not changed.

For offline SSD posting-label composition (conditional means, 100-bin
distributions and separate H/O statistics), use
[`SSD_LABEL_DISTRIBUTION.md`](SSD_LABEL_DISTRIBUTION.md). It documents the
standalone read-only scanner, portable report export and publication into the
same `tables` registry. Generated statistics and index/data files are not
included in the repository.

For the fastest observed recall >= 90% single-worker point in each of the six
SIFT1B scenarios, use [`R90_TIMING.md`](R90_TIMING.md). It provides matched
ordinary/timing builds, three paired clock-off/on repetitions, scenario-once
full-cohort warmup, exclusive application-stage accounting and diagnostic-table
publication without replacing formal QPS. It requires exact accepted portable
reference experiments, not relabelled historical measurements.

The executable runner currently supports **UInt8, squared L2, dimension 128,
one query worker**, arbitrary positive top-k, and the schema-2 output of
`generate_spann_predicate_groundtruth.py`. Empty, categorical, OR, AND and
numeric DNF predicates are supported. The underlying client still supports
Float; this publication runner does not claim Float or baseline-native capture
validation. PipeANN/DiskANN producers can submit validated schema-1 events
through the common registry, but their historical controllers are not silently
converted into this SPANN runner.

## Prepare inputs, index and client

Use Python 3.10+, the packages in `requirements.txt`, `numactl`, and Linux
Landlock ABI >= 3. `ldd` must resolve every native shared library. The native
child is write-confined to its new experiment directory. Do not supply search
overrides through `SPTAG_*`, `SPANN_*`, `OMP_*`, or `LD_PRELOAD`.

```sh
python3 -m pip install -r Tools/benchmarks/results/requirements.txt
```

Use [GettingStart](../../../docs/GettingStart.md) and
[`AdaptiveSpann.ini`](../../../docs/AdaptiveSpann.ini) for the canonical index,
then [`LocalLabelHierarchy.ini`](../../../docs/LocalLabelHierarchy.ini) for V5
target512/window4096 reconstruction. Use `ValueType=UInt8` for SIFT1B. Keep the
canonical source and completed derived root: reconstruction uses read-only
links and is not an in-place update. A new build is not necessarily bitwise
identical because native construction can use randomness. Record the exact
source revision, build INIs and completed artifacts.

Construction emits `[BuildProgress]` at stage start, every 30 seconds and end.
`stageEtaSeconds` covers the current stage only; unknown totals/ETAs use `-1`.
Neither 100% progress nor a file's existence authorizes queries: require builder
success, completion provenance and successful native reload.

Build the ordinary client from the matching core using the
[client build instructions](../hierarchical_shortcut_native/native_postfilter/client_build/README.md).
Do not replace an existing core/client or label a new executable as an old client.

Copy `examples/prepare.ini` outside the repository and replace its absolute
paths. Vectors/queries are native DEFAULT containers: an 8-byte little-endian
`int32 rows, int32 dimensions` header, followed by UInt8 elements. Attributes
are headerless little-endian uint32, categorical column 0 and numeric column 1.
Reuse real attributes; synthesize them with
`generate_spann_attributes.py --config BUILD_INI` only when that is explicitly
the intended dataset. Never regenerate attributes underneath an existing index.

```sh
python3 Tools/benchmarks/generate_spann_predicate_groundtruth.py \
  --config /absolute/configs/prepare.ini
```

This exhaustive offline operation can be expensive at 1B scale. Reuse its
unchanged completed outputs instead of recomputing them for every search grid.
The example defines the six current predicates; names such as `sel50` are not
selectivity estimates. Exact counts in `workloads.json` determine selectivity.
On the current SIFT1B attributes, sel50/20/10/5/1/01 are approximately
49.8291%, 19.8479%, 10.0528%, 4.9928%, 1.0007%, and 0.1001%.

## Register and run without hidden query overrides

Create six full copies of `examples/native.ini`, with the following bindings.
Use the generated `workloads.json` as the authoritative path/encoding source.
All other search settings, including nprobe sweeps, remain explicit in each INI.

| Native INI | `Predicate` | `PredicateFile` under the truth directory |
| --- | --- | --- |
| `sel50.ini` | `dnf` | `query_dnf_sel50.npy` |
| `sel20.ini` | `dnf` | `query_dnf_sel20.npy` |
| `sel10.ini` | `dnf` | `query_dnf_sel10.npy` |
| `sel5.ini` | `dnf` | `query_dnf_sel5.npy` |
| `sel1.ini` | `categorical` | `query_tags_sel1.npy` |
| `sel01.ini` | `categorical` | `query_tags_sel01.npy` |

Copy and edit `examples/batch.ini` and `examples/experiment.ini`. Paths are
absolute, case-insensitive INI keys use native parsing, and only whole-line
semicolon comments are supported. No environment/home expansion or inheritance
is performed. The batch lists 12 cases: six scenarios, then reversed order for
the second repetition. Both repetitions point to the same immutable native INI.
Use available CPU/memory nodes; example node 0 is not a universal performance
prescription. `TimeoutSeconds` bounds the whole native batch, not each point.
`MinimumQPS` filters the main presentation view only; it neither skips slow
queries nor removes observations from the registry.

Every case must share one physical index, one query cohort, warmup size and
value type, as enforced by the native client. All case outputs must be new,
nonoverlapping children of `Experiment.Directory`; that directory must not
exist before `run`. `IndexVersion` must uniquely identify this actual index,
not just the word "V5"; `ClientVersion` identifies this executable build.
Increase `VersionOrder` when replacing a client within a study/variant. Index
inventory fingerprints prevent reusing an index identifier for a different
physical artifact. They are local file identities, not cross-machine content
digests; a copied/rebuilt index on another host needs a new index identifier.

```sh
python3 Tools/benchmarks/results/native_results.py check-config \
  --config /absolute/configs/experiment.ini
python3 Tools/benchmarks/results/native_results.py init \
  --config /absolute/configs/experiment.ini --data-dir /absolute/results
python3 Tools/benchmarks/results/native_results.py run \
  --config /absolute/configs/experiment.ini
python3 Tools/benchmarks/results/native_results.py publish \
  --config /absolute/configs/experiment.ini --data-dir /absolute/results
python3 Tools/benchmarks/results/update_results.py --data-dir /absolute/results check
```

`init` is only for a new registry; omit it for later experiments. It registers
the study, expected scenarios, presentation protocol and selected physical
SPTAG index. To add another study/dataset, append a new complete `catalog` event
with a higher revision; do not edit `current.json`. The runner executes the
registered batch unchanged. It does not rebuild an index, generate a replacement
search algorithm, add hidden query settings or tune based on measured recall.

## Global / limited / hierarchical posting ablations

For exact reuse of previous parameters, complete-cohort three-repeat measurement,
six-panel plots and independent JSON publication, use
[`posting_ablation.py`](posting_ablation.py) with
[`examples/posting-ablation.ini`](examples/posting-ablation.ini).
[`POSTING_ABLATION.md`](POSTING_ABLATION.md) documents the portable
prepare/run/publish commands, reference INIs, resource checks and failure retries.

`SearchSSDIndex.GlobalPostingSearch=true` is an explicit, default-off ablation
for a loaded raw STATIC H/O index. It requires `EnablePostingNavigation=false`.
H1 selects heads by distance without predicate result admission, the scanner
reads only the global O suffix without posting-signature pruning, and exact
record/own-head filtering remains mandatory. This models the original SPANN
search structure with final filtering; it is **not an upstream SPANN rebuild**.
It shares the same head representatives, graph, persisted H/O file, O alignment,
page cap and native distance/scan implementation with the limited control.
Do not disable the immutable `EnableLimitedTagPosting` build flag to select O.

| Mode | GlobalPostingSearch | EnablePostingNavigation | H1 admission / SSD region |
| --- | --- | --- | --- |
| Global | true | false | Distance only / O |
| Limited | false | false | Native label support and signatures / H when eligible |
| Full | false | true | Native label support plus hierarchical supplement / H when eligible |

The global-to-limited comparison measures the **label-aware posting path**,
including head admission, not exclusively the physical SSD arrangement.
Limited-to-full isolates hierarchy only when other settings and the native
client agree. Hierarchy files can remain loaded but are not traversed by the
disabled modes; this is not a resident-memory saving measurement.
Keep all defaults, historical indexes and accepted observations unchanged.
The native client resets the optional global flag between batch cases and
records `mode=global` plus `global_posting_search=true` in each global point.

Use `WarmupPolicy=once_per_mode_predicate` to share a single resident index
across the three modes: each (global/graph/posting mode, predicate) receives
one complete query-cohort warmup, then three fixed-cohort
repetitions of every declared nprobe, with no minimum time floor. Loading,
warmup, replay and validation are outside ordinary QPS. Use a separate ablation
study and explicit client/version provenance rather than replacing main curves.
Do not pool multiple MaxCheck configurations into a fixed-configuration curve.
Separate resident processes can instead use `once_per_predicate`. Existing
`once`, `per_point` and `once_per_predicate` behavior is unchanged.

Reuse the original complete configurations and their exact nprobe sweeps;
do not select R90-only controls, run a pilot, extend a grid or tune for a recall
target. Multiple original configurations per scenario remain separate curves.
Register the three modes as distinct complete native INIs per configuration in one
experiment (same binary/index and one study/variant, with only the two mode
switches differing). Give every INI all three repetitions. After `run` accepts
the captures, `Rscript Tools/benchmarks/results/plot_posting_ablation.R /absolute/run`
uses the existing ggplot2/jsonlite plotting environment to write six-panel
PNG/PDF curves, an 80-100% zoom, repeat ranges and a CSV under a fresh `plots/`.
It requires 1,000 queries, Recall@100 and identical non-mode parameter grids; it
does not plot small-cohort pilots or substitute historical clients.

## Stable files and acceptance rules

| File | Contract |
| --- | --- |
| `native.log` | Original merged stdout/stderr; JSON lifecycle records coexist with native text logs. |
| `registration.json` | Wrapper schema 1; command, INI/client/source/library identities and index inventory. |
| `CASE/nprobe_N/ids.i32`, `dist.f32` | Headerless little-endian `[Q,K]` int32 and float32 arrays. |
| `CASE/nprobe_N/work.u64` | Headerless little-endian `[Q,8]` uint64 work counters. |
| `CASE/nprobe_N/latency_us.f64` | Headerless little-endian `[Q]` float64 latency in microseconds. |
| `execution.json` | Executed argv and native exit code. |
| `single_raw.json` | Array preserving every accepted native point, its repetition, computed recall and capture hashes. |
| `measurement-analysis.json` | Wrapper schema 1, explicit acceptance flags and point/repetition counts. |
| `events.json` | Array of normalized schema-1 measurement events; no automatic registry write during execution. |
| `completion.json` | Wrapper schema 1, success timestamp and SHA256 seal of all preceding output files. |
| `failure.json` | Explicit failed execution/validation; never admitted as successful publication. |
| `history.jsonl` | Append-only sequence/hash-chain/event-digest envelopes. Never truncate or rewrite history. |
| `current.json` | Schema 1, deterministic projection of accepted history; atomic replacement. |

Successful lifecycle order is `batch_begin`, `batch_loaded`, optional
`batch_warmup_begin/end`, then `case_begin`, all registered `point` records and
`case_end`, followed by `batch_end`. Missing, duplicate, reordered or mismatched
events fail. A successful process exit alone is insufficient. Raw point fields
retain native mode/type/budgets, schema/policy versions, QPS, mean/percentile
latency and completed warmup/measured/replay counts. Ordinary current clients
report navigation schema 7/58 columns and dispatch schema 2/6 columns; diagnostic
payloads are not ordinary performance observations.

Native QPS times the measured cohort only. Loading, warmup, deterministic replay,
payload writes and Python acceptance are excluded. `WarmupPolicy=once` means one
full cohort before the first case; `per_point` warms every probe. This difference
is retained in the normalized parameters. Native point records are emitted after
replay and payload close/flush, not per query, and do not claim per-point fsync.
The wrapper trusts the registered native client's replay checks, then separately
checks exact original-vector UInt8 L2 distances, predicates, ID range/uniqueness,
sorted distances, underfilled suffixes and exact capture extents.

Recall is `sum(|returned IDs intersect exact top-K IDs|)/(Q*K)`, including
underfilled queries; it is never normalized by the returned count. Truth is
authenticated against its generator seal/source identities and exact returned
truth distances, not recomputed over 1B vectors by the publication wrapper.
Repetitions must have identical IDs, distances and work; latency may differ.
Every declared repetition must finish before `events.json`/completion is
accepted. This runner does not salvage partial batches or automatically retry.

Normalized events contain `schema_version`, `kind`, study/variant/dataset/
algorithm, explicit `version`, `completed_at`, `point`, `measurements` and
`source`. `point.parameters` is a sorted JSON-encoded native-settings object,
with nprobe carried separately as `search_parameter`/`search_value`. Recall/QPS
are medians, with minima/maxima and all original repetitions retained. Rates
are positive finite queries/second; recall is a fraction in `[0,1]`; top-k and
cohort size are explicit. JSON serialization rejects NaN/Infinity.

Files below 64 MiB have identity and SHA256 guards; larger immutable inputs and
index files have device/inode/size/mtime/ctime guards, avoiding repeated full
1B scans. The executable also has a full digest. Keep the original inputs,
INIs, source checkout and libraries immutable until publication. This records
`ldd`-resolved libraries, not an exhaustive closure of future `dlopen` providers.
Publication rechecks the seal, provenance, lifecycle, captures and normalized
events before appending anything. Copying a completion marker over failed data
does not bypass these checks.

## Single-index selection and retirement

The new registry pins SPTAG to the `IndexVersion` registered by `init`. Query
settings and explicitly versioned clients can differ; V5-target128, V5-target512
and V6 must never share an index identifier. Main curves are observed Pareto
envelopes within the selected index/protocol, not interpolated results or a
mixture of the best physical indexes. Fixed-configuration ablation curves retain
all accepted observations. `cross_version=true` can refer to different clients.

For an existing unpinned registry, after publishing complete replacement curves:

```sh
python3 Tools/benchmarks/results/select_index.py \
  --data-dir /absolute/results --dataset SIFT1B --algorithm SPTAG \
  --index sift1b-v5-target512-run1 \
  --required-cells Tools/benchmarks/results/examples/required-cells.json
```

Each required cell needs at least two distinct search values with all declared
repetitions. The command appends a catalog revision and exact-index retirement
events in one compare-and-swap transaction. Prior observations stay in history;
retired indexes cannot reappear through late imports. The selected-index catalog
policy also excludes previously unknown other indexes from active views.
Unselected imports are retained as history, not silently promoted to main data.
Other algorithms and their selection policies are not changed.

Registry updates use an advisory lock, validate the existing hash chain and the
entire prospective projection before append, fsync history, then atomically
replace `current.json`. An interrupted current-file replacement is recoverable:

```sh
python3 Tools/benchmarks/results/update_results.py --data-dir /absolute/results refresh
python3 Tools/benchmarks/results/update_results.py --data-dir /absolute/results check
```

Reimporting the same accepted experiment is idempotent. Current observation
selection remains latest explicit version/latest complete repetition group,
not best-ever QPS. Incomplete groups from external producers are history-only;
an incomplete latest client version does not fall back to an older version.
Baseline exporters can use `record EVENTS.json`, or `run --event EVENTS.json
-- PRODUCER ...` to publish only after successful producer exit. They remain
responsible for native baseline acceptance; `record` is not raw-log validation.
Historical `publish-native`, `bootstrap`, and `render` functions are retained
for format/API compatibility. The latter two require the original paper layout
and are **not** standalone reproduction commands; use the workflow above.

## Regression commands

```sh
python3 -m unittest discover -s Tools/benchmarks/results -p 'test_*.py'
python3 -m unittest discover -s Tools/benchmarks/tests -p 'test_sift1b_*.py'
python3 -m unittest discover -s Tools/benchmarks/tests -p 'test_predicate_groundtruth.py'
```

The new end-to-end Python fixtures use a synthetic native-protocol producer,
explicitly not performance measurements. They cover real serialization and
capture validation, lifecycle failure, exact OR/distance checking, schema
shapes, index identity, retirement, idempotence and mutation rejection. Existing
`Test/NativeBenchClientTest.py` / `NativeBenchBatchTest.py` provide real bounded
native-client fixtures. No dataset, index, binary, local experiment output or
credential is part of this source package.
