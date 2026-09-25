# Native H1 post-filter replay clients

The implementation lives exclusively in main `AnnService`: BKT result admission,
`Common/NavigationVisited.h`, `Common/PostingNavigation.h` and
`SPANN/PostingNavigation.h`. This directory contains only clients of the main
native library. There is no generated replacement core or independent build.

Build from the repository root into a separate output directory:

```sh
cmake -S . -B build-native -DCMAKE_BUILD_TYPE=Release -DSPDK=OFF -DROCKSDB=OFF \
  -DSPTAG_OUTPUT_DIRECTORY="$PWD/build-native/bin"
cmake --build build-native --target NativePostingTest NativePostingUBSan SPTAGTest nativeBench nativeOwnOnlyTest
ctest --test-dir build-native -R '^NativePosting\.' --output-on-failure
```

`nativeBench workload.ini` performs equal warmup and measured query windows,
then checks deterministic IDs, distances and SSD work by replay. It writes
`ids.i32`, `dist.f32`, `work.u64` and per-query `latency_us.f64`.
`MaxQueries=all` and `Warmup=all` consume the entire provided query cohort.
The existing `[SearchSweep] NProbe=[...]` API loads the index once and runs
every registered probe, writing separate `nprobe_N/` output directories.
`nativeOwnOnlyTest workload.ini` exercises a real selected own head with an
empty posting. Both link the normal main `SPTAGLibStatic` target.

## Native behavior and configuration

Filtered queries admit H1 heads during native navigation using region-aware
categorical/numeric/DNF may-match signatures (and support membership for H);
nonmatches remain navigable. They do not obtain
unfiltered nearest24 and subsequently drop nonmatches. Final categorical,
numeric and DNF record filtering, liveness, deduplication, and selected-head VID
translation remain native. Support/signatures are may-match summaries, not
exact record truth. Own attributes participate in the summary without a
supplementary own-point heap.

`[SearchSSDIndex] EnablePostingNavigation=false` is the default. Setting it to
`true` enables bounded **post-graph** signed-posting completion. The ordinary
native graph phase runs first without in-row posting activation. Its native
head results are retained. Completion uses `PostingAnchorCount=8` (positive
integer) and `PostingAdditionalMaxCheck=0` (nonnegative integer), not the old
density/floor policies. Additional zero shares remaining native checked work;
it is not permission to start a row after budget exhaustion or a promise to
fill nprobe. Once selected, a whole CSR row completes even across the budget.
The sum of `MaxCheck` and the additional budget must fit a signed native int.
Both new fields reject malformed/overflowing values even while disabled.

`PostingMinCandidates` is **removed**, including explicit zero and disabled
configurations. Native build/search/saved-index setters and the current
client reject it with a migration error. Remove the key from a writable
configuration and use the new controls. Do not rewrite a historical index
or INI to make a frozen experiment load with the current implementation.
The old in-row one-percent trigger and fresh-candidate floor are not retained
as alternative policies. Complete-row budget overshoot remains intentional.

Upper state remains query-touched, not catalog-sized; hierarchy signatures
precede representative/member work. There is no independent upper ANN,
global tag scan, graph restart, extra own-result heap, or target-recall
guarantee. Unfiltered queries retain the ordinary native path.

The nearest-anchor heap uses already-scored native navigation candidates,
including promoted tree seeds and negatives, never arbitrary internal-tree
checks or a visited-directory scan. At most one phase merges/deduplicates
their owners.
All discovered H2+ postings now compete in one representative-distance heap,
with deterministic level/ID tie-breaking. Selecting a posting exposes its
owners once. Complete upper rows register their children into the same heap;
lower rows do not bypass competing upper branches. Signature-rejected rows
may expose owners to discover siblings, without representative or CSR access.
A native result container has capacity equal to the original missing slots;
exact native admission (including aliases and deletion) determines its filled
count. Filling it no longer stops exploration: later better candidates replace
worse supplementary heads until frontier convergence, checked-leaf budget or
reachable exhaustion. BKT supplies the same native navigation-pool capacity
`max(effective MaxCheck / 16, head-result capacity)` after deciding to activate.
The existing native distance-pool helper retains discovered signature-admitted
H2 representative distances. H3+ nodes share expansion priority but do not
consume target-layer convergence slots. A nearest pending representative worse than that
pool ends exploration, even if the requested number of heads is unavailable.
This is an approximate ANN convergence heuristic, not a geometric proof that
unseen members cannot improve recall. It avoids exhausting every reachable
posting merely because rare predicates have fewer heads than nprobe. Original
graph heads are never evicted. Inside an admitted H2 row, the shared
visited/match lookup evaluates an unseen member's predicate before any vector
prefetch or distance. A rejection is cached as a terminal visit, so replicas
do not repeat the predicate and negatives do not consume checked-leaf budget.
A rejected collapsed representative still checks its aliases: if a live
matching alias needs admission, its shared representative distance is computed
once. All-rejected or deleted-only alias groups need no distance. Matching
fresh members retain native distance accounting. No supplemental member enters
the unused graph frontier, and graph traversal must not resume after these
terminal visits. Ordinary H1 negative graph bridges are unchanged.

Diagnostic schema v6 appends before/after/target head counts, graph S/M,
anchor count, stop reason, graph/supplement checked leaves and distances,
and preserved original-head count. S/M are observed in enabled filtered
queries; disabled/unfiltered paths do not run that observer. `graph_ids.i32`
and `graph_dist.f32` capture the pre-supplement native heads for exact phase
parity. Stop reasons are 0 disabled/bypass, 1 sufficient, 2 exhausted initial
budget, 3 no anchor, 5 reachable exhaustion, 6 supplement budget, 7 frontier
convergence.
Code 4 is retained only for historical first-fill runs. Current filled heaps
can terminate with 5, 6 or 7; `posting_target_met` records outcome separately,
and convergence does not require a full result heap. The binary remains 57
columns/v6; the stop-reason decoder includes the new value 7 explicitly.
The existing `auxiliary_unvisited_negative_skips` column counts initially
unvisited rejections cached without scoring. Together with
`auxiliary_first_visits` (distance-scored members) and
`auxiliary_visited_skips`, it partitions the full physical H2 rows.
`auxiliary_negative_first_visits` now describes rejected representatives
scored for live matching aliases. These counters keep the v6 binary shape;
frozen all-scored campaigns retain their original zero-skip behavior and must
not be relabelled as predicate-first measurements.

## Historical first-fill post-graph campaign

`postgraph.ini` / `run_postgraph.py` use only the new
`comparisons/main_postgraph_posting_20260921` output root. Normal and diagnostic
executables live respectively in `toolchains/main_postgraph_posting_build`
and `toolchains/main_postgraph_posting_diagnostics`, both under the existing
SIFT1M sparse-numeric dataset root. Those artifacts retain their original
first-fill policy; new policy comparisons require new output/build directories,
never replacements of `Release`, these frozen builds or their registrations.

| Variant | MaxCheck | Posting | Additional | Anchors |
| --- | ---: | --- | ---: | ---: |
| `graph` | 2048 | off | 0 | 8 |
| `postgraph_shared` | 2048 | on | 0 | 8 |
| `postgraph_extra` | 2048 | on | 2048 | 8 |
| `graph_total` | 4096 | off | 0 | 8 |

The same index, queries, predicates and truth as `member_postfilter.ini` are
read without modification. Six scenarios run in order Medium, Extreme,
Mixed, Numeric, Broad, Unfilter. Probe counts **16,24,48,96,192,384 remain
ascending in both repetitions**; only scenarios and variants reverse in
repetition two. Each case/variant/repetition loads once and executes native
arrays: 1000 warmup, 1000 measured, deterministic replay at every probe.
This is 48 normal processes and 288 fresh points, not spliced old timings.

```sh
python3 run_postgraph.py postgraph.ini --stage prepare
python3 run_postgraph.py postgraph.ini --stage functional
python3 run_postgraph.py postgraph.ini --stage plain
python3 run_postgraph.py postgraph.ini --stage publish
# Publish normal curves with the parent plotting command's --postgraph mode.
python3 run_postgraph.py postgraph.ini --stage diagnostic
python3 run_postgraph.py postgraph.ini --stage finish
```

The functional gate uses only the first32 queries at nprobe24 in all six
scenarios: six plain postgraph controls and twelve diagnostic graph/postgraph
controls. These are correctness controls, never throughput points. Focused
native tests additionally cover shared-budget underfill and preservation of
full native head results. Timed diagnostics follow the published normal
results, at nprobe24 only: six scenarios × four variants =24 points.
Diagnostic timing is explicitly excluded from throughput.

Registration seals current relevant sources, generated native INIs, workload
metadata, truth, the current index files and binaries, not every old campaign.
Atomic per-process `validated.json` records allow completed processes to
resume without rerunning them. An incomplete process is retained; after
inspection `--retry-incomplete` archives it in this campaign and reruns only
that process. Repetitions and diagnostic replays must match native IDs,
distances and SSD work. `plain-results.json` retains scenario, variant,
nprobe, repetition, queries, recall, QPS, underfill and budget metadata.
Normal publication does not wait for the final diagnostic campaign.
Shared-budget underfill and regressions remain visible. There are no
target-recall or billion-vector performance claims.

## Historical full native acceptance campaign

The following recipes describe archived implementations. Use their matching
frozen clients/binaries, not the current `nativeBench`; retired knobs and
schemas are not compatibility modes in the new core.

`campaign.ini` registers all six existing workloads and the established
11-point native grid, with two reversed repetitions on NUMA node 2.
The old filtered-truth cohort contained only 1000 rows. This campaign uses
all 10000 raw held-out SIFT queries, with exact truth regenerated by the existing
`generate_sift1m_sparse_numeric_workloads.py` in a separate campaign directory.
Original queries, attributes, old truth and index files are not modified.

```sh
python3 run_full.py campaign.ini --stage prepare
python3 run_full.py campaign.ini --stage plain
python3 run_full.py campaign.ini --stage diagnostic
python3 run_full.py campaign.ini --stage summarize
```

Build the diagnostic binary from the same MAIN source in a separate output
directory with `-DSPTAG_QUERY_WORK_DIAGNOSTICS=ON`. These counters are compiled
out of normal builds. Diagnostic replay must match plain IDs, distances and
SSD work exactly and is never used for throughput. It records actual native
H1 distances, upper representative distances, signature checks, owner/member
iterations, predicate calls, touched states, native hash clear bytes, and
ordinary C++ allocation requests. Allocation counters do not include all
`malloc` or aligned IO allocations; state initialization bytes exclude
allocator-internal zeroing. Process RSS is reported separately.
Diagnostic schema v4 appends auxiliary first visits, negative first visits,
visited skips, unvisited-negative skips (zero under this policy), zero-eligible
H2 rows, zero-fresh H2 rows and vector cache-line prefetches to the original
22 columns. Fresh matching counts remain `posting_new_candidates`. Frozen v2
member-filter and v3 cumulative-density clients keep their own schemas.

Schema v5 retains those 29 columns and appends eight counters each for H2
and H3+ postings: physical owner references, physical member references,
candidate considerations, unique touched states, `ExpandRow` attempts,
already-expanded skips, completed CSR rows and representative distances.
The member-reference bucket names the destination posting layer; H1 members
of H2 rows remain the existing `auxiliary_members`, not posting references.
Owner references count each range returned by `Parents`, before fallback
sort/dedup. Candidate considerations count the `Collect` registration loop
and the upper CSR's child-registration loop before `Allowed`, including
rejected and previously seen IDs, not internal state lookups. Unique
`(level,id)` states use the existing successful `try_emplace`, with no new set.
Completion counts only the validated full row's false-to-true expanded
transition, so completed rows are distinct CSR expansions within a query.
Entry activations, references, representative scoring and CSR expansion are
different quantities. H3+ is a scalar bucket for every layer above H2, not
a restriction to a two-layer auxiliary hierarchy.

`run_posting_reference_counts.py reference_counts.ini` runs diagnostic-only
nprobe24/min1/min10 work measurement over the first1000 queries in all six
scenarios, with standard warmup and deterministic replay. Frozen schema-v4
Medium payloads and available first32 controls provide parity; only missing
min1 controls require a frozen32-query run. Per-query arrays and the summary
separate physical-reference repeats from candidate-registration repeats.
Reported means/p50/p95 are over queries. Any aggregate fraction is explicitly
a ratio of summed counts, not the mean of per-query ratios. Diagnostic QPS is
not accepted or exported as throughput.

The report retains every grid point, recall, underfill, QPS, p50/p95/p99 and
work. Measured recall-at-least threshold comparisons are explicitly distinct
from exact equal-recall comparisons. Non-overlapping recall ranges are not
comparable; no QPS interpolation or all-scenario speedup is assumed.

Posting navigation requires a full H1 BKT graph, unchanged hierarchy ownership
and supported geometry; incompatible bundle/hybrid/quantized layouts fail at
the extension boundary. Ordinary APIs remain available when it is disabled.
Unfiltered requests retain native navigation. Pure numeric and unanchored OR
predicates use O-region signatures and the same H1 admission with the extension
off or on. Creating an admission predicate does not switch an O query to H.
Numeric comparisons on one original column are intersected before quantization
and compiled once per query; AND clauses and OR alternatives remain distinct.

`RoutingSignatures.h` reuses existing H1 packed metadata, merging own values
once at build/load/explicit refresh. It aggregates separate H/O categorical
and numeric unions over unchanged upper CSR. Upper checks precede representative
distances and member access. No H1 numeric-mask copy or catalog-sized query
state is allocated. With M authenticated numeric lanes the additional upper
shared payload is `2 * (32 + 32*M) * sum(upper node counts)` bytes, excluding
vector-container/allocator overhead; existing persisted categorical CSR
signatures remain present. Fresh native H1 metadata includes the existing
format's `64*M` numeric bytes per head (H and O), plus its normal packed fields.
Thus the no-extra-H1-copy claim is not a claim that numeric metadata is free.

`NumericMetadata.h` shares the existing NUM2 domain/provenance format with the
wrapper. Missing, invalid or mismatched legacy domains are logged and numeric
tests become conservative unknown, never narrower guessed masks. No saved
index is rewritten on load. Metadata replacement invalidates upper summaries
in O(1); stale summaries are logged and not used. Native callers must serialize
metadata mutation with searches and call `ISPANNIndex::RefreshRoutingSignatures`
after completing replacement, without retaining mutable metadata views across
refresh. Wrapper load and both `BuildSignatures` paths, including the idempotent
path, perform that refresh. Refresh is not an implicit query-time rebuild.

## Bounded numeric integration smoke

`numeric_smoke.ini` / `run_numeric_smoke.py` use separate numeric normal and
diagnostic builds and a new result directory. They register all six scenarios,
1000 equal warmup/measured queries, nprobe24, two reversed normal repetitions
and one diagnostic replay. Run `--stage prepare`, `plain`, `diagnostic`, then
`summarize`. The runner checks exact filters, result/SSD-work parity and protected
input/runtime hashes. Diagnostic timings are not throughput measurements.
This is not a rerun of the sealed 264-point September19 campaign and does not
establish all-scenario performance or billion-vector scalability acceptance.

`connectivity_smoke.ini` reuses this bounded runner with new connectivity
normal/diagnostic build roots and a separate evidence directory. The same
1000-query, nprobe24, six-scenario protocol applies. September19 full-campaign
and September20 numeric binaries/configurations/evidence remain immutable.
`PostingConnectivityTest` and its UBSan target cover per-layer boundaries,
visited positives, empty/dense/exhausted rows, cached metrics, signature
pruning, direct owners, single descent and full-row budget feedback.

Those connectivity tests describe the archived fraction policy. Historical
`PostingCollectionTest` / `PostingCollectionUBSan` cover the replacement floor:
9/10, new/visited/replicated candidates, overshoot, distance order, arbitrary
depth, live budget feedback, per-activation reset and explicit underfill.
`min10.ini` and `run_posting_collection.py` run all six scenarios against both
same-main H1-only and freshly timed frozen fraction posting, with reversed
repetitions. The frozen executable's INI omits only the unsupported new key.
Diagnostic schema v2 has 22 columns: the prior 17 plus activation count, fresh
H1 count, target-met count, underfilled count and budget-underfilled count.
The new runner explicitly decodes both v2 and the frozen v1 (17 columns);
diagnostic times are never treated as QPS.

Fresh hierarchy builds retain representative catalogs, sampling IDs, signed
CSR and H1 navigation. CSR owner assignment uses the original native ANN
candidate search and RNG replica selection, with `[BuildHead]` parameters.
Its temporary per-layer ANN exists only during construction: it is neither
saved nor retained for query/load. No tree-only or brute-force replacement
assignment algorithm is used. Legacy saved
upper-index directories are read only for representative vectors when the
standalone catalog is absent. Old graphless H1 layouts require explicit
`spannbuilder --materialize-hierarchy --output-index-dir NEW_ROOT` migration;
they cannot silently fall back to upper ANN search.

Retired search/build controls (including `BuildH1Graph`,
`CompactHierarchyVectors`, upper-beam/max-check/prefetch/dedup/routing-budget
controls and `HeadNavigationMode`) are rejected by fresh setters. Required
saved layout/provenance keys are explicitly decoded with warnings on load;
they do not activate another algorithm. Native INIs are the only authority.

## Historical evidence

The previous generator, duplicate helper headers, independent CMake recipe
and original-source proof instrumentation have been removed from the active
source tree. Their exact pre-edit bytes and original-path/SHA256 mapping are
preserved under
`comparisons/main_postfilter_integration_20260919/pre_edit/` and
`pre_edit_manifest.json` in the SIFT1M dataset artifact root.
Earlier sealed reports and manifests are unchanged and describe their
historical implementation, not the current main source.

The old .769/.877 ms comparison used a residual distance-shortcut baseline,
not same-work native predicate semantics. It is not evidence for a predicate
comparison costing 10%, nor a valid baseline for claiming a cleanup speedup.
