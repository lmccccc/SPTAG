# Benchmark Scripts

## STM1 Static Metadata Demo

The native INI is the source of truth for build and search settings. The
committed Float SIFT-1M fixture demonstrates node-pure STM1 postings, unbounded
unfilter tails, and the exact member-OR posting prefilter:

```bash
cmake --build build --target spannbuilder spannaclbench -j

CFG=Tools/benchmarks/build_spann_attr_sift1m_tagged_4node_static_fullfloat_tail_unbounded_ordered_page.ini
Tools/benchmarks/run_spann_attr_build.sh "$CFG"

IDX=/datadisk/yfcc_fast/sptag_sift1m_tagged_vs_upstream/index_tagged_4node_static_fullfloat_tail_unbounded_ordered_page
QDIR=/home/v-mochengli/datasets/sift1m/multitenant/query

Release/spannaclbench \
  --index "$IDX" \
  --queries "$QDIR/query_vectors.npy" \
  --truth "$QDIR/groundtruth_project_local_ids.npy" \
  --query-tags "$QDIR/query_tags.npy" \
  --tag-column 3 \
  --warmup 200 --max-queries 1000
```

`[SearchSSDIndex]` in the INI controls the persisted search behavior:
`InternalResultNum`, `MaxCheck`, `EnableUnfilterTail`, and
`EnableHierPostingFilter`. Do not override these with `SPTAG_*` environment
variables. The JSON output includes recall/QPS and loaded-posting contribution
metrics when `CollectPostingContributionStats=true` is enabled in a diagnostic
search overlay.

For a reload-only sweep, pass a separate native runtime overlay instead of
modifying the persisted index or using environment variables:

```bash
Release/spannaclbench ... \
  --search-ini Tools/benchmarks/search_turbopuffer_sift1m_tenant0_n20.ini
```

## BKT-Head Hybrid Distance Routing

`build_spann_attr_sift1m_global_static_hybrid_distance.ini` is the hybrid-on
experiment; `build_spann_attr_sift1m_global_static_bkt_control.ini` is its
matched hybrid-off control. Both retain the canonical SIFT1M BKT head
selection and degree-32 vector graph. Hybrid mode appends degree-16
hybrid-distance edges through the standard `head_cross_edges.bin` runtime
suffix; its marked version-2 extension binds the suffix to both the build
generation and deterministic hybrid content, including the ordered serialized
edge body. It does not create a second graph store or attribute subset. Its
sole STM1 posting is:

```text
H | O
```

Here `H` is the hybrid-distance pure prefix and `O` is the complete original
vector-distance pure+tail posting. Each region is internally unique, while a
VID may intentionally occur once in each region. The `O` suffix remains sorted
by vector distance:

```text
D_hybrid = w_v D_vector
         + sum_i w_cat,i [query_i != head_i]
         + sum_j w_num,j |query_j - head_j|
```

Unfiltered queries always navigate the original degree-32 graph and read only
the self-contained `O` suffix. Before filtered graph search, the router
computes pure-vector and predicate distances to a deterministic sample of head
vectors. It chooses hybrid navigation plus the pure prefix only when the
predicate is selective and its attribute penalty is large relative to the
near-sample vector-distance span:

```text
Phi(q, P) = RMS_i(D_attribute(P, h_i))
            / (Q90(D_vector(q, h_near)) - Q10(D_vector(q, h_near)) + epsilon)

Hybrid iff selectivity(P) <= HybridRouteSelectivityThreshold
           and Phi(q, P) >= HybridRouteDeformationThreshold
```

`HybridRouteSampleCount` defaults to 64 and performs no graph traversal.
The selected graph runs exactly once, and nprobe changes only the operating
point on that route. No second posting, overlap bitmap, sparse exact-set
reconstruction, or single-attribute partition exists. Exact record-level flat
or DNF filtering remains authoritative on either route. All routing thresholds
and distance weights are native INI parameters.

## Limited-Tag Static Postings

`build_spann_attr_sift1m_zipf200_limited_tag.ini` builds the two-column
categorical/numeric limited-tag experiment. Its canonical H3 -> H2 -> H1
pipeline keeps only the H3 graph; lower layers use ID-only CSR postings and
logical vector catalogs. It does not create attribute subsets, hybrid edges,
or cross edges.
The record schema is driven by native `NumTagsPerVec`, `ACLCols`,
`NumericCols`, and `LimitedTagColumn` values rather than a fixed attribute
count. Limited-tag placement and H1/H2 routing use only categorical equality
anchors on `LimitedTagColumn`; all categorical and numeric DNF3 literals are
still evaluated exactly on posting records. Native
`[BuildSSDIndex] LimitedTagSlotsPerHead` accepts any positive integer (`2` is
the default).
The default fixed-slot mode persists that many support values per head in generation-bound
`limited_tag_support.bin`:

```text
support[head][0] = source vector attribute
support[head][1..N-1] = top-(N-1) external attributes
```

In the default mode, the requested count cannot exceed the number of distinct key-column tags,
because every support value on a head is distinct. With one slot, a head
supports only its source vector's tag.

The canonical INI sets `LimitedTagSlotsPerHead=2` explicitly. Use
`build_spann_attr_sift1m_zipf200_limited_tag4.ini` for the isolated four-slot
experiment.

### O-derived low-coverage support expansion

The opt-in expansion mode keeps the geometrically selected real H1 heads fixed.
It adds support relationships, not heads, vectors, a sparse-only posting, or a
new query route. Configure it entirely through the native INI:

```ini
[SelectHead]
MinHeadsPerTag=0

[BuildSSDIndex]
EnableLimitedTagSupportExpansion=true
LimitedTagSlotsPerHead=2
LimitedTagVoteHeadCount=2
LimitedTagMinHeadCount=16
LimitedTagMaxExtraSupports=262144
LimitedTagMaxExpandedPostingPages=32
```

The base row keeps the own tag and up to the configured number of nearest
external O-search-vote tags. Unlike legacy mode, it may remain underfilled;
centroid-neighbor filling and per-tag global coverage repair are skipped.
Original vector counts include real H1 representatives and count each VID
once. A single pass over the **retained O prefixes after posting cuts** finds
distinct candidate `(tag, head)` relationships for tags below the support floor.
Discarded scratch-array suffixes and pre-RNG votes are not expansion sources.
Each deficient tag retains a bounded deterministic pseudorandom subset of its
new O heads; already supported heads and repeated members of one posting do
not consume additional slots.

The required support count is the smaller of the floor and the number of
feasible base-plus-O heads. Thus a head-only singleton needs its one own head,
not sixteen invented postings. A tag with neither a support head nor a retained
O source fails construction. `LimitedTagMaxExtraSupports` bounds the total
additional relationships; exceeding it fails explicitly rather than silently
reducing targets. The planner does not scan the entire head catalog once per
tag.

All non-head records still use support-filtered placement and the same RNG
replica rule. Empty-result recovery ranks supported heads and applies that
same RNG rule; it does not impose a special one-replica policy on sparse tags.
The O assignments remain unchanged. For heads receiving extra support,
`LimitedTagMaxExpandedPostingPages` bounds the complete H-prefix **payload**
in native pages; page-aligned physical reads may include one additional
boundary page. Exceeding this limit fails without trimming H replicas.

Expanded support uses generation-bound V4 storage: the original base rows,
sparse `uint64` head offsets and sorted `uint32` extra tags, plus authenticated
per-tag feasible targets and the global extra-support budget. The serialized
overflow costs `8*(H1+1) + 4*extraSupports` bytes, plus 16 header bytes; runtime
lookup indexes have additional memory costs. The full-width offset path uses
a derived one-bit-per-head presence map; zero-overflow tables skip that lookup
altogether. If the nonzero extra-support count fits a uint32 offset and the
local-row allocation is representable, all admission fields share one row:
`[uint32 offset, first extra tag, base tags..., own attributes...]`. With two
base slots and two own attributes this is 24 bytes per head, the same retained
size as the former base, attribute, and CSR-offset arrays together. Those
separate allocations and the presence bitmap are released; the first extra
tag does not require another payload load. Empty
rows cache the reserved empty tag rather than taking a per-head presence
branch. Larger tables retain full-width offsets. This is a
runtime representation only: validation, fingerprints, and saving decode the
original base rows and uint64 CSR offsets in bounded chunks, so V4 files remain
byte-compatible. Loading reserves the local rows before conversion and releases
the original attribute and offset buffers afterwards. Hierarchy admission prefetches the
whole small lookup row directly; the full-width path additionally
prefetches out-of-line tags in a bounded stack batch. Neither path duplicates
the tag table or changes navigation according to query selectivity.
The hierarchy resolves row pointers and strides once per query, rather than
redispatching the storage layout for every candidate head. Separate small
lookup kernels keep compact-row register pressure out of the full-width path.
V1/V2/V3 remain readable and
legacy builds retain their existing format. Complete support rows are used
by H1 admission and upper-layer signatures. Expanded snapshots support
directory save/export/reload but reject insertion, deletion, and topology
maintenance.

The floor is a **support planning target**, not a guarantee of that many
nonempty H postings after RNG. Build logs distinguish support targets from
realized H/own-head coverage. The native audit reads the persisted H/O records,
checks all non-head VIDs survive in H, verifies every extra support has an
actual O source, and emits per-tag/per-posting CSVs and a JSON summary:

```bash
cmake --build build --target spannsupportaudit -j2
Release/spannsupportaudit /path/to/index/tenant_0 /path/to/report/coverage
```

Compare `original_membership_fingerprint` and H1 source-VID catalogs for matched
builds; the audit reports actual H replication, effective coverage, unused
extra supports, and payload sizes rather than assuming the requested floor
was realized. The feature does not remove the STATIC builder's existing
single-batch, global-selection-sort, or raw-vector limitations at billion scale.

For a floor-only comparison, keep O-derived mode enabled on both sides and
vary `LimitedTagMinHeadCount` (for example, 1 versus 16). A legacy-mode
comparison also changes base-row filling and empty-search RNG recovery;
even a run with zero added supports can change replication and I/O. Independently
rebuilt BKT trees can vary even with one head-build thread: the legacy KDT/TPT
builders reseed the process-global random generator from the clock, affecting
subsequent BKT k-means initialization. For the controlled CPU head builds, use
native `[BuildHead] BKTSeed=0`, `TPTSeed=0`, and `NumberOfThreads=1`.
Nonnegative seeds select local generators (TPT streams are keyed by tree
ordinal); negative/default `-1` retains legacy behavior. Seeded TPT construction
does not need the legacy clock-separation sleeps. These parameters do not
change saved-index query behavior. Require matching source-ID, tree/graph,
CSR-geometry, base-support, and O fingerprints before attributing differences
solely to the support floor.

#### Controlled SIFT1M / 8192-tag experiment

The completed run is
`../datasets/sift1m_zipf8192_numeric_support_expansion/limited_tag_support_expansion_runs/sift1m_zipf8192_numeric_support_expansion_measured_seeded_heads_final/`.
It uses the existing local SIFT vectors, Zipf-1 tags (minimum tag size 13),
exact top-10 truth, and the same three-layer routing-only hierarchy in every
variant. H1/H2/H3 source IDs, top tree/graph/vectors, lower CSR geometry, and all
5,998,317 retained O assignments match. The two O-derived variants also have
identical base support rows. Earlier unseeded runs are diagnostic artifacts,
not the matched comparison.

| Persisted quantity | Legacy floor 1 | O-source floor 1 | O-source floor 16 |
| --- | ---: | ---: | ---: |
| Real H1 heads | 160,091 | 160,091 | 160,091 |
| H records | 3,005,886 | 3,297,203 | 3,301,290 |
| Extra supports | 0 | 11 | 52,634 |
| Extras with nonempty H | 0 | 11 | 52,432 |
| Support-file bytes, not RSS | 2,692,592 | 3,973,388 | 4,183,880 |
| H + O payload bytes | 4,718,202,372 | 4,870,852,480 | 4,872,994,068 |
| Tags below their feasible target after RNG | 0 | 0 | 532 |

For tags with 13-20 vectors, median effective H/own coverage rises from 5 to 16;
the floor-16 minimum is 13, not 16. There are 202 unused extra supports.
Raising only the O-derived floor adds 210,492 support-file bytes and 2,141,588
payload bytes. Relative to legacy mode, the complete new floor-16 mode adds
1,491,288 support-file bytes and 154,791,696 payload bytes; do not attribute
that entire difference to the floor.

Queries use CPU 24, one native search thread, three serial interleaved trials,
100 warm-up queries, and 900 measured queries. All probes 16/32/64/128 are
retained in the figures and CSVs. These are the original, pre-lookup-correction
measurements; the same-index runtime correction is recorded separately below.
At the common probe of 64:

| Workload | Recall@10, floor 1 -> 16 | Median QPS, floor 1 -> 16 | QPS change |
| --- | ---: | ---: | ---: |
| Unfiltered | 0.971333 -> 0.971333 | 1101.29 -> 1104.87 | +0.33% |
| Broad tag 0 | 0.974222 -> 0.974222 | 1302.58 -> 1226.52 | -5.84% |
| Tags with 13-20 vectors | 1.000000 -> 1.000000 | 3613.30 -> 2619.53 | -27.50% |
| Tags with 21-50 vectors | 1.000000 -> 1.000000 | 3303.39 -> 2584.04 | -21.78% |
| Tags with 51-200 vectors | 0.986556 -> 0.986111 | 2579.71 -> 2250.65 | -12.76% |

The added coverage does not improve recall in these workloads. For the 13-20
bin, even probe 16 already has recall 1 in both O-derived modes; expansion
increases postings/query from 5.27 to 15.99, pages/query from 14.54 to 54.66,
and scanned records/query from 77.57 to 318.21, while useful distance
computations remain unchanged. Its QPS declines 23.9-35.8% across the probe
grid. This experiment therefore does **not** justify a universal floor of 16.
Expansion remains opt-in; the canonical 201-tag INI and active index are
unchanged.

Reproduce from the repository root using the existing fixture and a fresh run
label (the driver refuses to overwrite an existing run):

```bash
cmake --build build --target spannbuilder spannaclbench spannsupportaudit -j2
python3 -B Tools/benchmarks/run_limited_tag_support_expansion.py build \
  --fixture-root ../datasets/sift1m_zipf8192_numeric_support_expansion \
  --run-label seeded_reproduction
run=../datasets/sift1m_zipf8192_numeric_support_expansion/limited_tag_support_expansion_runs/sift1m_zipf8192_numeric_support_expansion_seeded_reproduction
python3 -B Tools/benchmarks/run_limited_tag_support_expansion.py audit --run-dir "$run"
python3 -B Tools/benchmarks/run_limited_tag_support_expansion.py benchmark \
  --run-dir "$run" --cpu-affinity 24 --trials 3
python3 -B Tools/benchmarks/run_limited_tag_support_expansion.py finalize \
  --run-dir "$run" --plot
```

The run retains native INIs, build/audit/query logs, input and binary hashes,
the identity report, 180 raw trial rows, 60 aggregate rows, per-tag coverage,
and R-rendered PNG/PDF performance and structure figures under `final/`.
Coverage uses a labeled log1p axis to retain the legacy outliers without
obscuring the small-tag region; lines are exact-size arithmetic means, not
fitted trends. Build timings use serial head construction for control and
are not a measurement of the fastest parallel production build.

#### Runtime lookup correction v2: same saved indexes

The floor-16 H1 catalog has a mean of 2.32548 support tags per head; 95.2989%
of heads have at most three. Broad tag 0 itself has no extra supports:
its 31,856 supporting heads and 592,969 H records are unchanged. The original
lookup nevertheless chased overflow metadata on base misses for heads with
unrelated extras: 35,251 such heads in floor 16 versus nine in floor 1.
This is an access-layout cost, not evidence that comparing two or three tags
intrinsically requires a 5% query slowdown.

The correction co-locates base support, own attributes, the first extra tag,
and the compressed offset, and resolves row views once per query. All nonempty
overflow fitting the compact representation uses the same layout, without an
extra-density cutoff or a query-selectivity branch. Full-width offsets remain
the representability fallback. No head, support relationship, H/O assignment,
navigation route, native search setting, or V4 serialized byte layout changes.

The accepted frozen binary is `diagnostics/support_lookup_prefetch/spannaclbench.contiguous`
(SHA256 `8bebcce64d53bae09fef81bb29a2a93e4f045516727c87b1c490d2696447462e`).
Its reference is `spannaclbench.before`
(SHA256 `c683bc56e7a40dec38f7094b55765acf8c7e5b109e89474496c1ed1850c6e74c`).
`contiguous_full_grid_ab/` contains four balanced serial trials, 320 raw rows,
and 80 summary rows on the exact saved floor-1/floor-16 indexes and native INIs.
Native recall and work fields match the original outputs for every row.
At probe 64, the uninstrumented same-run medians are:

| Workload | Floor 1 QPS, before -> after | Change | Floor 16 QPS, before -> after | Change |
| --- | ---: | ---: | ---: | ---: |
| Unfiltered | 1114.69 -> 1114.05 | -0.06% | 1116.01 -> 1117.69 | +0.15% |
| Broad tag 0 | 1306.63 -> 1326.41 | +1.51% | 1242.05 -> 1331.40 | +7.19% |
| Tags with 13-20 vectors | 3635.98 -> 3616.90 | -0.52% | 2588.55 -> 2894.34 | +11.81% |
| Tags with 21-50 vectors | 3332.30 -> 3308.16 | -0.72% | 2601.61 -> 2956.38 | +13.64% |
| Tags with 51-200 vectors | 2595.13 -> 2572.99 | -0.85% | 2241.11 -> 2530.68 | +12.92% |

Across probes 16/32/64/128, the broad-tag floor-16/floor-1 QPS gap changes from
-6.83%..-4.77% to -1.31%..+0.38%; the floor-1 broad baseline also improves.
This is not a literal zero-overhead claim: floor-1 sparse cases range from
-1.88% to +0.48% against their own original baselines. Floor-16 sparse cases
improve 9.07%..13.64%, but the additional posting reads remain; for the 13-20
bin at probe 64, floor 16 is still about 20% slower than floor 1.

`contiguous_phase_ab/` is a separate native `LogPhaseTime=true` diagnosis.
At probe 64, broad-query hierarchy tag admission changes from
113.468/154.468 us (floor 1/16) to 108.116/108.453 us. These are medians of
per-process mean phase times, excluding warm-up; instrumented QPS is not
substituted into the performance table. The main admission arrays remain
24 bytes/head plus the final eight-byte trailer for this two-slot/two-attribute
configuration, the extra-tag pool is unchanged, and the presence bitmap is
released. Measured process-peak RSS differences are about -0.35..+0.40 MiB;
these are not steady-state RSS measurements.

The old `final/` results, source manifest, and figure links are preserved.
Versioned R performance, speedup, and admission PNG/PDF figures are under
`contiguous_full_grid_ab/figures/lookup_v2_*`, with separate `lookup_v2_*`
links in the fixture's `figures/`. Rejected intermediate binaries and
measurements are retained as diagnostics, not substituted into the publication.
The replay helpers and plotting source are copied to the diagnostic `replay/`
directory. With `run` set to the measured run above, reproduce into fresh paths:

```bash
diag="$run/diagnostics/support_lookup_prefetch"
python3 -B "$diag/replay/support_lookup_ab.py" \
  --run-dir "$run" --output-dir "$diag/reproduce_full_grid_v2" \
  --before "$diag/spannaclbench.before" --after "$diag/spannaclbench.contiguous" \
  --trials 4 --workloads unfilter,broad_tag,bin13_20,bin21_50,bin51_200 \
  --nprobes 16,32,64,128
python3 -B "$diag/replay/support_lookup_ab.py" \
  --run-dir "$run" --output-dir "$diag/reproduce_phase_v2" \
  --before "$diag/spannaclbench.before" --after "$diag/spannaclbench.contiguous" \
  --trials 4 --workloads broad_tag,bin13_20 --nprobes 64 --profile
Rscript Tools/benchmarks/plot_limited_tag_support_lookup.R \
  "$diag/reproduce_full_grid_v2/summary.csv" \
  "$diag/reproduce_phase_v2/summary.csv" \
  "$diag/reproduce_full_grid_v2/figures/lookup_v2"
```

### Shared H/O placement and metadata

The normal BKT candidate search first builds the complete original SPANN
placement `O`. Its raw top-`LimitedTagVoteHeadCount` candidates are reused as
support votes before RNG pruning, so support construction does not run a second
nearest-head search. With `BuildH1Graph=0`, spatial placement uses the hierarchy
callback to reach H1; the graphless H1 catalog is not a standalone search graph.
The vote count must not exceed `InternalResultNum`, keeping
the vote window inside the unchanged original search. Non-head vectors are then
assigned only to heads supporting their attribute, using constrained BKT search
and RNG pruning for up to eight `H` replicas. The single STM1 v3 posting is
`H | O`: limited-tag queries scan `H`, while unfiltered and exact-filter fallback
queries use the same hierarchy and read only the complete `O` suffix.
Cross-region overlap is intentional. Limited-tag builds require
`TailReplicaCount=0`; `O` is already self-contained, so no supplemental
unfilter-tail replicas are built.
With `EnableHierPostingFilter=true`, V8 head metadata stores separate
categorical and quantized-numeric signatures for `H` and `O`; the selected
route consults only its matching signature before I/O. Numeric signatures use
256 uniform buckets per numeric column and remain conservative at bucket
boundaries, with exact record-level DNF evaluation removing false positives.
V8 binds the complete metadata blob to the constrained-posting generation and
a content fingerprint, records whether own-tag and hierarchical posting masks
and the tail-signature layout are actually available, and binds numeric signatures to the NUM2 v2
numeric-domain content fingerprint. Legacy or mismatched metadata falls back
to validated PBS3 categorical masks when available; missing masks and numeric
metadata fail open rather than rejecting a potentially matching posting. The PBS3
`signatures_bitmask.bin` sidecar likewise binds `H`/`O` masks to the posting
generation and body fingerprint, rejects legacy tail-bearing PBS2 files, and
is published atomically.
Monolithic and graphless H1 metadata reuse the existing local-head-to-global-VID
map; only legacy bundle-backed slim roots resolve IDs from bundle maps.
Legacy slim roots initialize that bundle runtime before binding metadata-only
samples, matching the native build path.
Generation-bound metadata cannot be saved with unresolved VIDs, and signature
generation propagates mapping failures instead of publishing an incomplete blob.
At load, the validated support sidecar builds the support metadata used for
result admission and H2 posting signatures. In H1 navigation mode, graph traversal remains
distance-only, while support-aware result admission continues collecting
eligible heads until `InternalResultNum` postings are filled before I/O. The
filtered search budget scales from support coverage and retries up to a bounded
`MaxCheck`; it does not directly enumerate a tag-to-H1 list. Empty physical
postings do not count. This gives H1 and H2 the same nprobe and posting-I/O
semantics without adding a predicate-specific H1 entry path.
`HeadNavigationMode=H1Only`, and `Auto` with `SelectSecondLevel=false`, do not
read hierarchy signatures or require upper-layer artifacts. Explicit `H2Only`
without a loaded hierarchy returns an error rather than switching to H1.

Static posting scans and iterators mark each VID in the existing workspace
deduper before evaluating its predicate, so rejected replicas are skipped too.
No separate predicate cache is needed. Match counters now describe first-visit
matches and contributing postings, not matching replica occurrences.
`spannaclbench` reports these as `matched_vectors_per_query` and
`contributing_postings_per_query`; `dedup_skipped_vectors_per_query` includes
duplicates of rejected VIDs. `unique_scanned_vectors_per_query` and `match_rate`
use first-visited records, while `scanned_occurrence_to_unique_ratio` measures
all scanned replicas. The legacy `FalsePositivePostings()` counter now includes
postings containing only already-visited matches.

`[SelectHead] SecondLevelHierarchyLevels` counts H1: `3` means H3 -> H2 -> H1,
with only H3 retaining a search graph. Every layer uses the same
`[SearchSSDIndex] InternalResultNum` nprobe ceiling. One native top-graph search
uses `SecondLevelMaxCheck` and saves its returned routing frontier. H3/H2 are
routing-only indexes: their vectors and signatures do not carry real result
attributes or enter the final top-k. Only native top-search returned nodes can
become downward routing parents. `SecondLevelInitialProbeRatio`
sets the initial downward routing beam, while H1 keeps the full posting target.
An underfilled descent widens from saved candidates even after the graph budget
is spent: it does not restart the graph or clear the H1 result heap. Previously
expanded parents remain selected, so **cumulative** parent expansion at each
layer cannot exceed nprobe. Each CSR row and each child ID are admitted once.
Selection consumes candidates from the remaining frontier in distance/local-ID
order, partially sorting only the prefix needed for the current beam rather
than building a heap over all candidates. Selected parents form an append-only
list; a cursor expands only its new suffix. There are no separate
selected/expanded bitmaps or copied parent lists.
Signature/support checks precede the bounded nearest-child selection; rejected
children cannot consume the next layer's beam.
After an underfilled initial descent, a complete bounded single-tag fallback is
considered before widening. Only a support set fitting both head/nprobe and
posting-page limits can stop that widening; otherwise the incremental descent
continues. The fallback admits real support-head points even when they have no
SSD posting, then selects its nonempty postings. It never becomes a separate
pre-entry search route.

Each layer owns its local visited state. Native BKT navigation uses its original
workspace without borrowed state or distance callbacks. CSR layers use separate
touched-word bitmaps and retain their allocated capacity across queries.
Distances and predicate state are not shared across layers; the query does not
translate upper local IDs into canonical H1 IDs.

Only H1 real heads and SSD records enter the final result set. They share the
existing global-VID deduper, including exact-predicate rejections. A routable H1 head is
scored geometrically before its own result admission; its exact attribute
predicate cannot suppress a posting containing matching records. Conversely, a
nonrouting H1 head can still return its own matching point, even with no SSD
posting. After local visited, a safe own-key signature check discards impossible
nonrouting points; potential matches then check VID/deletion/exact predicate
before computing distance. The shared VID deduper still precedes exact predicate
evaluation for both H1 points and SSD records.
The compact H1 point heap is pooled with the query workspace. Bounded fallback
reuses distances in the completed underfilled H1 routing results rather than a
cross-layer cache. SSD retains `visited -> predicate -> decode/distance`.
Legacy H1 conversion, repeated admission, and compaction are skipped when the
H1 point heap is active. Empty/partial hierarchy results do not silently switch
to H1 graph navigation.

Upper signatures cover downward H1 own routing keys and searchable H-prefix
records, not upper prototype attributes or the unfiltered O suffix. They reuse
the H1 pure PS mask and complete limited-tag support rows (base plus overflow);
own keys come from complete
H1 attributes even when the key column exceeds the fixed hierarchical lanes.
Only fully anchored pure-H queries use these signatures. Unfiltered/full-O or
unanchored queries disable this pruning; their O data remains intact.
H3/H2 head admission always checks the applicable signature, including dense
tags. Selectivity thresholds do not disable this admission or H1 own-key pruning.

An empty stored signature is a known nonmatch for a nonempty anchored query
signature. The bounded single-tag fallback
remains downstream of this shared hierarchy path and still requires both native
head/page limits and the posting nprobe to fit.
CSR expansion looks ahead four rows and prefetches their signatures. The native
`SecondLevelPrefetchMode=Rolling16` default fetches complete vector rows,
including the final cache line of unaligned rows. `Batch64` retains the original
64-vector/two-cache-line scheme for controlled comparisons. Both modes operate on
layer-local vectors and have identical admission/budget semantics.
Routing candidates use a reusable distance/ID-only bounded max-heap, avoiding
metadata allocation and swaps while preserving native distance ordering,
VID tie-breaking, and rejection of noncompetitive distances.

Graphless construction follows the same merge/deduplicate, score, then retain
ordering. Its shared placement helper serves both support voting and filtered
H-prefix assignment. The existing `max(InternalResultNum, 64)` placement beam
limits retained intermediate heads, never the raw CSR child prefix; every child
of the selected parents competes by query distance before truncation. The helper
reuses layer-local touched-word bitmaps and buffers across calls. H1 filtering,
the top graph's build-search budget, and query-time navigation remain unchanged.
Indexes built with the earlier prefix truncation must be rebuilt to repair their
posting assignments; reloading or materializing their vectors cannot do that.

The canonical SIFT1M Zipf200 index now uses
`datasets/sift1m_zipf200_sparse193_numeric/index_limited_tag_h3_placement_fixed`.
The full native rebuild changed only the output and temporary locations in the
build INI; the old `index_limited_tag_h3_independent` index is retained. Pure-H
occurrences increased from 4,228,353 to 4,633,041 (+9.57%), with all 839,909
non-head VIDs still covered and no H1 head records in SSD. Sampling catalogs,
IDs, and the top graph are byte-identical; the native BKT tree and CSR artifacts
were rebuilt, so this is an end-to-end rebuild comparison, not an
architecture-only control.

Fresh-process, interleaved three-trial broad-tag medians improve QPS from
1781.12 to 2004.32 near 91.2% recall, 1181.81 to 1452.18 near 95.4%, and
445.61 to 619.54 near 99.4%. Each pair differs by at most 0.1 percentage point
of recall; the original `max(128, 2*nprobe)` top budget rule is unchanged.
At identical nprobe, medium/mixed recall rises with slightly lower QPS, while
the extreme workload retains recall 1.0 with a small fresh-process QPS decrease.
Build provenance, native content counts, paired results, and the report are in
`hierarchy_audit_checks/placement_beam_rebuild/` under the dataset directory.
The refreshed `h1_h2_curve_h2_15pct_r8/h3_placement_fixed/` series contains the
same 79 operating points and preserves the 110 historical H1/H2 rows.

`[SearchSSDIndex] SecondLevelGraphSignaturePruning=false` independently controls
the native top graph. With the default `false`, nonmatching graph bridges remain
traversable; returned H3 heads are still signature-filtered before descent.
With `true`, the same signature filters graph seeds and neighbors after native
visited checks, without reading upper attributes. BKT tree partitions themselves
remain traversable. This can save graph work but can also lose routes through
nonmatching graph bridges, so compare recall as well as QPS at unchanged
`SecondLevelMaxCheck` and `InternalResultNum`. H2/H3 head admission, H1/SSD
semantics, and cumulative layer budgets are identical in both modes. Empty query
signatures bypass the graph filter in either mode.

The controlled SIFT1M Zipf200 comparison in
`datasets/sift1m_zipf200_sparse193_numeric/hierarchy_audit_checks/graph_signature_ab/`
replays the same 79 INI operating points in both modes, with three serial trials
per mode on the pre-rebuild posting layout. The canonical default remains
`false`. At the representative broad
and medium points, the H3 graph filter rejects no nodes, so it adds checks without
removing graph distance work. At extreme-tag nprobe 62, both modes reach recall
1.0, but graph pruning reduces median QPS from 2481.92 to 2365.15. At smaller
extreme-tag budgets it improves recall instead: nprobe 32 changes recall from
0.777222 to 0.866222 and QPS from 5767.45 to 4508.08. This is a quality/cost
tradeoff, not a uniformly faster traversal. Both modes commonly exhaust the
unchanged MaxCheck ceiling; pruning can admit more matching H3 heads and increase
downstream CSR work.

The separate interleaved before/after replay isolates always-on dense head
admission with graph pruning off: broad-tag nprobe 36 preserves recall 0.905444
and changes median QPS from 1634.93 to 1847.63. This improvement is not attributed
to graph pruning or a changed MaxCheck. Raw paired runs, profiles, and the final
report are retained in the experiment directory. The published comparison uses
only the selected graph-off H3 series and preserves historical H1/H2 rows.

### Independent routing-layer storage

New unquantized STATIC graphless hierarchies use `head_metaonly.bin` version 3.
H1 and every intermediate layer own complete, contiguous native vector
catalogs, including sampled/promoted rows. The top layer keeps only the vectors
inside its native BKT index, not an additional selection-catalog copy. H1 alone
owns canonical result VIDs and V8 attributes; upper layers retain signatures
and routing structures, with obsolete upper attribute artifacts removed.
The canonical build sets `[SelectHead] CompactHierarchyVectors=false`;
requesting legacy compaction during a fresh build is rejected explicitly.

The V3 descriptor keeps the 28-byte prefix and 8-byte canonical-H1 fingerprint.
Loading checks exact catalog sizes, H1 V8 generation/content/VID integrity,
byte-exact sampled-vector correspondence at every adjacent layer, and
conservative descendant-signature coverage. Thus separately stored upper
vectors cannot silently diverge from their sampled lower rows.
V8 loading configures the metadata layout and adopts the content-verified
payload directly, without allocating a second full-size zero-filled blob.
Generation, layout, content, canonical-VID, and hierarchy-signature checks
remain intact; existing index files do not need rebuilding for this loader change.

An existing V1/V2 graphless index can be materialized into a new output root
without rebuilding its graph or SSD postings:

```bash
Release/spannbuilder -c <native.ini> --materialize-hierarchy \
  --output-index-dir <new-root>
```

This offline command reads the source `[Base] IndexDirectory` and `[Tags] Tenant`;
it does not read the original source dataset. The output must not exist or
overlap the source, and the source manager manifest must identify the selected
tenant and its external mapping. Conversion stages complete catalogs, reloads
and validates them, then publishes the complete manager root without replacement.
Source files, H1 V8 bytes, sampling IDs, and CSR member/offset arrays remain
unchanged. Conversion rebuilds upper signatures for H1 own keys plus the H region,
including their generation bindings, without retaining irrelevant O-only bits.
In-process callers can use
`ISPANNIndex::MaterializeHierarchyVectors(destinationTenantDirectory)` while
searches are quiesced.

V1/V2 reading and explicit `--compact-hierarchy` remain legacy compatibility
operations. They do not change the routing-only query policy. Legacy missing
signature information disables unsafe pruning with a warning; V3 requires
authenticated H1 metadata and rejects incomplete signatures.

Graphless STATIC directory exports copy the persisted artifacts into a private
staging directory and use native loading to validate them before publication;
they do not recursively serialize the dummy KDT or logical views over the source.
In-place saves validate a hard-linked staging view and replace only the INI.
The loader resolves the physical root descriptor against the directory actually
being loaded, not an obsolete build/export directory embedded in the INI.

`LogPhaseTime=true` separates top-graph search, CSR gathering/deduplication,
admission, vector scoring, and sorting; the legacy `h2*` field names also apply
to deeper hierarchies. `LogAdaptiveNprobe=true` adds `HierarchyWork` counts for
each named head layer, including eligible children, retained children, and
distance computations. With `LogPhaseTime=true`, the same lines also report
per-layer graph, merge, admission, vector-scoring, and sorting times in milliseconds.

EST4 classifies a tag by absolute expected head coverage, not selectivity. A
tag uses the exact contiguous `[VID | all attributes | vector]` route when
`tagCount < ExtremeSparseTagMinCount` or
`tagCount * actualHeadCount * LimitedTagSlotsPerHead <
SearchInternalResultNum * vectorCount`. The strict inequality makes the largest
coverage-qualified count `ceil(L*N/(H*S))-1`. The same vectors remain in
ordinary postings for unfiltered, numeric-only, and other-attribute predicates.
The sidecar stores every tag eligible up to the build-time
`max(SearchInternalResultNum, MaxCheck)` ceiling, then each query re-evaluates
eligibility with its current `InternalResultNum`.
A partially covered DNF is split into an exact EST scan and a dense search of
only its uncovered clauses, then deduplicated by VID. H2 posting signatures
include only tags in
`(SecondLevelSignatureMinSelectivity, SecondLevelSignatureMaxSelectivity]`.
H2 signatures are categorical only; numeric pruning applies to H1 posting
selection and the self-contained `O` fallback route.
Every dense DNF clause must have a signature-represented equality anchor before H2 is
used; otherwise search falls back safely to H1 or complete ordinary postings.
The generators read this policy directly from the canonical native INI. Before
heads exist they use `expectedHeadCount = VectorCount * SelectHead.Ratio`; the
manifest records that assumption. SIFT1M therefore generates 193 extreme
vectors from `Ratio=.16`, two slots, and `InternalResultNum=62`.

Generate the reproducible Zipf-200 attribute and build with:

```bash
python3 Tools/benchmarks/generate_sift1m_zipf_attribute.py \
  --output-dir /datadisk/yfcc_fast/sptag_sift1m_zipf200_sparse_numeric \
  --extreme-tag-coverage --numeric-column \
  --config Tools/benchmarks/build_spann_attr_sift1m_zipf200_limited_tag.ini
python3 Tools/benchmarks/generate_sift1m_sparse_numeric_workloads.py \
  --attributes /datadisk/yfcc_fast/sptag_sift1m_zipf200_sparse_numeric/sift1m_zipf200_sparse193_numeric_attrs.npy \
  --output-dir /datadisk/yfcc_fast/sptag_sift1m_zipf200_sparse_numeric/query
Release/spannbuilder \
  -c Tools/benchmarks/build_spann_attr_sift1m_zipf200_limited_tag.ini
```

## SIFT1B Limited-Tag Recommendation

The SIFT1B generator replaces the old four-level ACL hierarchy with exactly
two attributes: one Zipf-200 categorical tag and one deterministic numeric
value. It writes the final row-major `uint32 [N,2]` SPTAG input directly in
bounded-memory chunks; no `tags5` merge or per-vector routing-key text file is
used. The categorical assignment is an exact affine permutation of the Zipf
counts. The generator reads the native INI and derives 399 extreme vectors from
`Ratio=.12`, two support slots, `InternalResultNum=96`, and the strict EST
coverage inequality. `spannbuilder` uses its tenant-0 bulk path for these inputs: mapped
vectors and attributes are borrowed through the synchronous build instead of
materializing one metadata string, pointer pair, and global ID per vector.
Both original and constrained placement retain only emitted RNG edges rather
than initializing `N * ReplicaCount` slots.

```bash
Tools/benchmarks/prep_sift1b_inputs.sh
CFG=Tools/benchmarks/build_spann_attr_sift1b_zipf200_limited_tag.ini
Tools/benchmarks/run_spann_attr_build.sh "$CFG"
```

The canonical build uses one global BKT graph, `ACLCols=0`, `NumericCols=1`,
`LimitedTagColumn=0`, and `HierLevelWidths=201,64,64,64,64` for the 201
active values plus minimum-width inactive lanes. EST4 binds the actual vector
and head counts, slot width, coverage target, minimum count, and generation;
H2 covers the configured intermediate range. The historical four-ACL SIFT1B
INIs remain only as reproduction
controls for archived `sift1b_tags5.u32` inputs and are not produced by the
current generator.

## Ordered ACL Page Starts for Static STM1

`EnableOrderedPageStart=true` together with `OrderedPageStartAttrs` enables
sparse static reads for ordered hierarchy filters. It sorts each STM1 pure
posting prefix by the hierarchy tuple and persists `ordered_page_starts.bin`:
one `int32` page-start signature ordinal per configured attribute per posting
page.

```ini
[BuildSSDIndex]
Storage=STATIC
EnableOrderedPageStart=true
OrderedPageStartAttrs=2,3
```

For the SIFT hierarchy, columns `2,3` are team and project. The directory is
used only for a single-clause DNF AND query containing a categorical equality
on team or project; project takes precedence when both are present. Unfilter,
flat ACL queries, multi-clause DNF, and unordered facets retain the normal
full-posting path. The configured attributes must remain globally monotonic
after ACL tuple sorting; the builder rejects an incompatible schema rather than
allowing a range lookup to drop matches.

For the distance-order path, set `EnableOrderedPageStart=false`. The builder
does not apply the attribute tuple sort: pure records retain the selection
order `(head distance, VID)`, while tail records retain their separate
`(head distance, VID)` order. It removes any stale
`ordered_page_starts.bin`, and the query path cannot perform ordered page
pruning. This is the canonical SIFT1B recommendation; ordered page starts
remain an optional sparse-filter experiment.

`UnfilterPureDistanceScanPercent` can benchmark computation reduction on this
distance-ordered layout. Values below `100` retain the nearest pure prefix and
the complete tail suffix. The runtime rejects this setting on attribute-ordered
snapshots and when bounded-tail page controls are active.

`build_spann_attr_sift1m_tagged_4node_static_fullfloat_tail_unbounded.ini`
is the matching SIFT1M no-order control; it explicitly sets this parameter to
`false`.

The native benchmark can issue this DNF form directly:

```bash
Release/spannaclbench \
  --index /path/to/index \
  --queries "$QDIR/query_vectors.npy" \
  --truth "$QDIR/groundtruth_project_local_ids.npy" \
  --query-tags "$QDIR/query_tags.npy" \
  --dnf-and-cols 2,3 \
  --warmup 200 --max-queries 1000
```

## Multi-Tenant Tag Cache Stress

Files:

- `multitenant_tag_cache_stress.py`: benchmark logic, exact recall computation, result summarization.
- `run_multitenant_tag_cache_stress.sh`: reproducible runner with fixed defaults and env-based overrides.

Default workload:

- `1000` queries split into `10` batches of `100`
- sequential workload: tenants `0 -> 9`, one tenant per batch
- random workload: tenants mixed within each batch
- single-tag filter per query, sampled from the tenant's true tag distribution
- `topk=10`
- `seed=20260413`
- cache limit policy: `max(2 * largest HeadIndex, total HeadIndex / 4)` rounded up to MB

Default search params:

- `ForceDenseTagSearch=false`
- `DirectSparseMaxPostings=320`
- `FilteredSearchNprobeSafety=1.0`
- `FilteredSearchTargetRecall=1.0`
- `FilteredSearchCoverageExponent=0.5`

Run with defaults:

```bash
bash Tools/benchmarks/run_multitenant_tag_cache_stress.sh
```

Run a small smoke test:

```bash
SPTAG_STRESS_NUM_QUERIES=20 \
SPTAG_STRESS_BATCH_SIZE=10 \
SPTAG_STRESS_TENANT_RANGE=0,1 \
bash Tools/benchmarks/run_multitenant_tag_cache_stress.sh
```

Run a small RSS high-water sweep relative to the benchmark process baseline RSS:

```bash
SPTAG_STRESS_NUM_QUERIES=20 \
SPTAG_STRESS_BATCH_SIZE=10 \
SPTAG_STRESS_TENANT_RANGE=0,1 \
SPTAG_STRESS_RSS_HIGH_WATER_SWEEP_MB=off,+64,+128 \
bash Tools/benchmarks/run_multitenant_tag_cache_stress.sh
```

Run with an absolute RSS high-water cap:

```bash
python Tools/benchmarks/multitenant_tag_cache_stress.py \
	--rss-high-water-mb 2048
```

Useful environment overrides for the runner:

- `SPTAG_STRESS_SCENARIO_FILE`
- `SPTAG_STRESS_QUERY_FILE`
- `SPTAG_STRESS_OUTPUT_ROOT`
- `SPTAG_STRESS_NUM_QUERIES`
- `SPTAG_STRESS_BATCH_SIZE`
- `SPTAG_STRESS_TOPK`
- `SPTAG_STRESS_TENANT_RANGE`
- `SPTAG_STRESS_SEED`
- `SPTAG_STRESS_CACHE_LIMIT_MB`
- `SPTAG_STRESS_RSS_HIGH_WATER_MB`
- `SPTAG_STRESS_RSS_HIGH_WATER_SWEEP_MB`
- `SPTAG_STRESS_DROP_PAGE_CACHE_ON_EVICT`
- `SPTAG_STRESS_FORCE_DENSE_TAG_SEARCH`
- `SPTAG_STRESS_DIRECT_SPARSE_MAX_POSTINGS`
- `SPTAG_STRESS_FILTERED_SEARCH_NPROBE_SAFETY`
- `SPTAG_STRESS_FILTERED_SEARCH_TARGET_RECALL`
- `SPTAG_STRESS_FILTERED_SEARCH_COVERAGE_EXPONENT`
- `SPTAG_STRESS_PYTHON`
- `SPTAG_STRESS_LD_PRELOAD`

Artifacts written per run:

- `benchmark.log`: full stdout/stderr
- `status.txt`: `running`, `success`, or `failed`
- `meta.txt`: human-readable run configuration
- `meta.json`: structured run metadata
- `summary.json`: machine-readable result summary
- `summary.md`: human-readable summary table
- `batch_summary.csv`: batch-level metrics

Artifacts written for RSS sweep mode:

- root `summary.json` / `summary.md`: aggregated per-budget summary
- root `budget_summary.csv`: one row per `(rss_budget, scenario)`
- one child directory per RSS budget, each containing the normal per-run artifacts above

Notes:

- The benchmark uses exact recall computed from the base vectors referenced by the scenario file.
- The runner records seed, search parameters, git commit, and runtime environment so the workload is reproducible.
- Latency is statistically reproducible, not bitwise identical, because OS scheduling and file cache state can vary.
- `--rss-high-water-mb` accepts `off`, an absolute MB value like `1024`, or a relative headroom like `+128` measured above the benchmark process RSS right before workloads start.
- `--rss-high-water-sweep-mb` accepts a comma-separated list in the same format and runs each budget in a fresh child process so process-level RSS measurements do not drift across sweep points.