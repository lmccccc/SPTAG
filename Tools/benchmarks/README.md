# Benchmark Scripts

## Native vector input migration

`spannbuilder` now uses the original `VectorSetReader` dispatch and core DiskIO:

```ini
[Base]
ValueType=UInt8
VectorType=DEFAULT
VectorPath=/path/to/headered_base.u8bin
; Dim is optional for DEFAULT; when present it must match the header.
Dim=128
; Optional bounded prefix, min(header rows, VectorSize); omit for the full input.
VectorSize=2000000
```

`DEFAULT` is `[int32 rows][int32 dimensions][row-major elements]`, not a filename
extension heuristic. Negative/invalid dimensions, short headers, size overflow,
truncated payloads, trailing bytes, and conflicting `Dim`/`ValueType` are rejected
before payload allocation. The file itself must remain immutable during a build.
`ValueType` specifies `UInt8`, `Int8`, `Int16`, or `Float`; it is **not**
`VectorType`. `TXT` (native metadata-tab-vector syntax) and `XVEC` (per-record
dimension header) still use their native converters, require `Dim`, and validate
dimensions before exposing their converted DEFAULT vector set.

`VectorOffset`, `VectorCount`, `--vec-offset`, `--vector-offset`,
`--vector-count`, and vector `--n` are removed and fail explicitly.
Use `--vector-type`, `--value-type`, and optional `--vector-size` instead.
`VectorSize=-1`/omission means all; zero and values outside native signed
`SizeType` are rejected. This applies to normal build, signatures-only,
primary-head backfill, and OPQ/PipePQ prep. `--merge-tags5 --n` remains an
explicit prefix for the separate NPY-to-native-attributes preparation command.

`TagFile` is **headerless row-major native uint32 `[N, NumTagsPerVec]`**.
Its first row always begins at byte zero, implicitly. `TagOffset`,
`--tags-offset`, `--tag-offset`, `SPTAG_TAG_OFFSET` and `SPTAG_TAGS_OFFSET`
are removed and rejected even when explicitly zero (including CLI `=0` forms).
No format or replacement offset knob is introduced. Native DiskIO owns the
read-only attribute mapping, with a checked native read fallback when unavailable.
For all-row builds the file must be exactly `N * NumTagsPerVec * 4` bytes.
For `VectorSize` prefixes it must have exactly the selected number of rows
**or** the full validated vector-source count; only the selected rows are used.
Arbitrary extra rows, truncated/misaligned data and normally sized NPY/headered
files fail rather than being skipped or guessed. The production 1B/2-column
sidecar remains the same 8,000,000,000-byte file, unchanged.

Raw bytes have no embedded dtype/shape/endian marker: uint32 is the contract,
not a detectable property of same-size bytes. Same-size float/int32 data or a
header substituted for payload cannot be identified reliably and must not be
passed as TagFile. Declare **every column in original order** with
`[Tags] ColumnTypes=categorical,numeric` (CLI `--column-types`).
`cate,num` aliases are accepted; saved configs use full canonical names.
Width is derived; optional `NumTagsPerVec` must equal it. Single-label means
one value per categorical column, not one categorical column per record.
For example `numeric,categorical,numeric,categorical` uses numeric lanes
for original columns 0 and 2 and independent category values at 1 and 3.
`[BuildSSDIndex] LimitedTagColumn=3` selects the original key column; it must
be in range and categorical. Inputs and posting records are never reordered.
`spannaclbench --tag-column N` reads column N of the supplied query-tag matrix
and emits categorical equality on that same original column, rather than
matching the value across all columns. `--or-tag-count` retains flat tag-value
OR semantics; numeric predicates use `--query-dnf`.

Native APIs use `SetSSDBuildParam("ColumnTypes", ...)` (or the SPANN
`BuildSSDIndex` setter). Saved `TagSchemaVersion=1`, `ColumnTypes` and
`TagSchemaFingerprint` check the canonical original-order schema on load.
The existing posting/support formats remain unchanged and contain full raw rows.
Explicit-schema snapshots require a schema-aware reader; do not open interleaved
snapshots with older binaries that only understand categorical prefixes.
Numeric metadata retains its category-count field and densely packed domains;
the schema maps absolute query indices to numeric lanes everywhere.
Old authentic prefix layouts without explicit types remain readable through
private legacy count metadata; they are never reinterpreted as one-category
inputs. Signature regeneration requires a matching explicit input schema.

Repository recipes with known four-category or four-category-plus-numeric
inputs now declare their actual schema. Unsupported headerless `VectorType=RAW`
markers still require deliberate native-vector migration. No existing datasets,
historical inputs or historical run configurations are rewritten.

Only `--merge-tags5 --tags-npy ... --num-npy ...` accepts NPY: v1.0,
C-order little-endian uint32 `[N,acl-cols]` plus nonnegative int32 `[N]`,
with matching source row counts, checked shape/type/header/size and bounded
`--n`. Unsupported versions/order/dtypes and mismatches fail explicitly.
It writes a new native raw sidecar; never point TagFile at those NPY inputs or
rewrite historical datasets to make a config appear compatible.

On POSIX, the native DEFAULT reader optionally maps a validated file read-only.
The vector set owns the mapping through build/save, and the single-tenant bulk
path borrows it without a wrapper/core corpus copy for L2 or normalized Cosine.
Non-mapping DiskIO backends fall back to one native allocation/read.
Unnormalized Cosine automatically needs one mutable copy, which the core borrows
through normalization, build and save; the mapped source is never mutated.
Signatures-only reads header/stat and maps without scanning the
corpus, validates against saved metadata, and uses tags plus persisted support.
TXT/XVEC conversion may materialize a native intermediate on disk; it does not
silently treat those formats as DEFAULT.

Legacy `tenant0_base*.f32` profiles are deliberately marked `VectorType=RAW`,
an **unsupported migration marker**, not a new reader. Their files have no native
header; changing only the container to DEFAULT would corrupt interpretation.
Before rerunning those examples, explicitly create a **new** native headered file
with the known shape and unchanged row ordering/normalization (or select a
matching existing XVEC/TXT source), then update `VectorPath`/`VectorType` in a new
run configuration. Do not rewrite historical files or substitute a full SIFT
corpus for a tenant-local 404819-row source: tags and local VIDs must still match.
Headered UInt8/Int8 profiles are migrated directly, and intentional SPACEV
prefixes retain `VectorSize`. Production H5 uses `ValueType=UInt8`,
`VectorType=DEFAULT`, no offset/count/full-size override; all hierarchy,
thread-count, support, numeric, and H/O page settings are unchanged.

### Other redundant bulk input settings

The bulk command also removes `[Tags] Tenant`, `[Build] WithMetaIndex`,
`[Build] ShareBuildOwnership`, `--tenant`, `--with-meta-index`,
`--share-build-ownership`, and the `SPTAG_BUILD_SHARE_OWNERSHIP` environment
override. Explicit values fail even if they used to be `0`/`false`. Tenant 0
and no line-metadata index are invariants of this bulk model, not choices.
Ownership now follows the native metric and reader's `Normalized` state:
L2/normalized Cosine borrows; unnormalized Cosine copies once before native
normalization. The library's real tenant, metadata-index and ownership APIs
remain intact. `--tenant` still selects a real tenant for hierarchy maintenance
and existing-index posting transforms; it is removed only from bulk input.

`[BuildSSDIndex]`/`[SearchSSDIndex] NumTagsPerVec` is a redundant **bulk-input** setting: it is
derived from `[Tags] ColumnTypes` and still persisted
as the native runtime option. Keep only the schema setting in builder INIs.
Schema width remains required. The bulk categorical prefix and target column
are fixed to one and zero respectively; a tag byte offset is not a setting.

Retained deliberately:
- `Normalized` is the native reader assertion that Cosine input is already
  normalized; it cannot safely be inferred from a file header. It uses native
  boolean parsing, including `--normalized true|false` (native `-norm` alias);
  a bare checkbox-style `--normalized` is no longer accepted.
  Omitted means false (the redundant L2 production declaration
  was removed).
- `Dim` is the native consistency constraint (and required TXT/XVEC dimension),
  not a second format-specific offset/count. DEFAULT can derive it.
- `BuildSignatures` schedules real work: the launcher defers it to a fresh process
  to control peak memory, and no-tag/unfiltered builds need not generate filtering
  sidecars. `false` is also useful for a build followed by signatures-only.
  It does not promise filtered serving before required sidecars exist.
- `MinHeadsPerTag` has an active native representative-head promotion algorithm.
  Support expansion requires it to be zero, but non-expansion experiments can
  use it meaningfully; a mode constraint is not grounds to remove the native knob.
- Native head selection, storage, compression, H/O and search budgets remain
  native options. No meaningful budgets were replaced with wrapper constants.

## STM1 Static Metadata Demo

First migrate this legacy headerless Float input as described above.
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
`InternalResultNum`, `MaxCheck`, and `SearchPostingPageLimit`. Exact attributes
admit results and choose H/O membership; signatures do not prune posting reads.
Do not override these with `SPTAG_*` environment
variables. The JSON output includes recall/QPS and loaded-posting contribution
metrics when `CollectPostingContributionStats=true` is enabled in a diagnostic
search overlay.

For a reload-only sweep, pass a separate native runtime overlay instead of
modifying the persisted index or using environment variables:

```bash
Release/spannaclbench ... \
  --search-ini Tools/benchmarks/search_turbopuffer_sift1m_tenant0_n20.ini
```

## Hybrid Build Metadata

`build_spann_attr_sift1m_global_static_hybrid_distance.ini` is the hybrid-on
experiment; `build_spann_attr_sift1m_global_static_bkt_control.ini` is its
matched hybrid-off control. Both retain the canonical SIFT1M BKT head
selection and degree-32 vector graph. Hybrid-format indexes may retain
generation-bound posting-layout statistics, head attributes, pure-prefix
metadata, and a marked cross-edge artifact for compatibility and diagnostics.
These artifacts do not select a query graph or alter a search budget. The sole
STM1 posting remains:

```text
H | O
```

Here `H` is the pure prefix and `O` is the complete original vector-distance
pure+tail posting. Each region is internally unique, while a VID may
intentionally occur once in each region. The `O` suffix remains sorted by
vector distance.

All filtered and unfiltered requests navigate the same spatial graph with the
same configured `InternalResultNum`, `MaxCheck`, hierarchy beam, and cross-edge
policy. Predicates only choose safe posting-region membership and perform exact
record admission. `HybridRouteSampleCount`,
`HybridRouteSelectivityThreshold`, `HybridRouteDeformationThreshold`, and
`LogHybridRoute` are removed and rejected.

## Limited-Tag Static Postings

`build_spann_attr_sift1m_zipf200_limited_tag.ini` builds the two-column
categorical/numeric limited-tag experiment. Its canonical H3 -> H2 -> H1
pipeline keeps only the H3 graph; lower layers use ID-only CSR postings and
logical vector catalogs. It does not create attribute subsets, hybrid edges,
or cross edges.
For local SIFT1B, use
`build_spann_attr_sift1b_zipf200_limited_tag_h5.ini`: it keeps the
[`GettingStart.md`](../../docs/GettingStart.md) SIFT1B baseline budgets and
extends this spatial hierarchy to five total levels. Its raw UInt8 records
are 140 bytes, and it uses the existing two-column single-label/numeric
attributes, O-derived floor-16 support, and new `_h5` output directories.
Only H5 retains a graph; every level uses the same `.12` selection ratio,
configured solely by native `[SelectHead] Ratio=.12`, `HierarchyEnabled=true`,
and `HierarchyLevels=5`.
The CSR replica count remains `8`. This profile does not overwrite the older H1/H2 index.

The native hierarchy interface uses `[SelectHead] HierarchyEnabled`,
`HierarchyLevels` (total levels, including H1), and **one** `Ratio` for all
selection stages. `Count` must remain zero for hierarchy builds. Small layers
round up to at least one head without changing the saved ratio. The remaining
selection keys are `HierarchyReplicaCount`, `HierarchyHeadVectors`,
`HierarchyHeadVectorIDs`, `HierarchyHeadIndexFolder`, `HierarchyPostingFile`,
and the generated `HierarchyGenerationFingerprint`.
Runtime hierarchy keys are `HierarchyInitialProbeRatio`, `HierarchyMaxCheck`,
and `HierarchyPrefetchMode`. The initial probe ratio is one fixed CSR beam
fraction, not a head-selection ratio or the first step of a widening loop.
`BuildH1Graph=false` retains just the top graph and uses CSR descent below it.
When a hierarchy is enabled, every sparse, dense, numeric-only, and unfiltered
request uses it. Without a hierarchy, requests use the native H1 graph.
Hierarchy queries never enumerate the global tag-to-H1 support map.

`HierarchyRouteSelectivityThreshold` and its `SecondLevelRouteSelectivityThreshold`
alias are removed and rejected, including zero, empty and old-default values.
There is no replacement dispatch knob. `HeadNavigationMode` and
`HierarchyGraphSignaturePruning` are also removed and rejected. Persisted
legacy signatures and their authenticated domains are compatibility metadata
only; queries do not use them for result admission or traversal pruning.
Hierarchy traversal does not require wrapper selectivity estimates or
`tag_routing_stats.bin`. The tag-statistics sidecar may still be produced for
diagnostics and public statistics consumers, but it never controls search and
its absence does not disable filtered queries.

Except for the removed route threshold and signature-domain pair, legacy `SelectSecondLevel` and `SecondLevel*` names remain explicit read/set
aliases, with canonical keys taking precedence in INI files. Saving emits only
canonical keys. Old artifact filenames and binary formats are unchanged.
`SecondLevelRatio` is **not** a selection option: it is accepted only as a
compatibility constraint and is omitted on save. An enabled legacy hierarchy
whose ratio differs from `Ratio` fails build/load/save explicitly; use the
legacy reader for that historical index, never edit its frozen config to
pretend it was built with shared ratios. Disabled old hierarchies can ignore
their unused legacy ratio. Repository templates now describe new shared-ratio
builds; historical benchmark results and run INIs are not rewritten.
New hierarchy SelectHead checkpoints also record the shared ratio and total
levels. Resume rejects missing legacy provenance or a changed ratio/depth rather
than silently reusing heads selected under a different model. Use
`ResumeBuild=0` and fresh output paths for such migrations; non-hierarchy legacy
checkpoints remain readable.
The launcher validates these settings before any build, and checks canonical
persisted settings afterwards (including STATIC graphless indexes). A read-only
preflight is also available:
`python3 Tools/benchmarks/validate_spann_hierarchy_config.py <build.ini> [indexloader.ini]`.

The bulk schema lists every original column in `ColumnTypes`; categorical and
numeric columns may be interleaved. Limited-tag H-region eligibility uses only
categorical equality anchors on the target column; all categorical and numeric
DNF3 literals are still evaluated exactly on posting records. Native
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

The current SIFT1M `build_spann_attr_sift1m_zipf200_limited_tag.ini` uses the
**retained-O expansion model**: shared `Ratio=.16`, H3, `MinHeadsPerTag=0` and
`LimitedTagMinHeadCount=16`. It declares native DEFAULT Float input and original
`categorical,numeric` columns, with expansion enabled and no global cap. Fresh output
uses `index_limited_tag_h3_current`, never the historical
`index_limited_tag_h3_placement_fixed`. Derive unique run directories for repeat
experiments. Its 24-thread native build, beam 64, build MaxCheck 8192,
construction page limit 16 and search page limit 12 remain unchanged.
The old eight-head non-expanded control remains historical provenance, not the
current build recipe.
The launcher completes both the primary build and the separate signature phase
when `BuildSignatures=true`; a primary-only build is not an equivalent benchmark.
Comparisons against historical results measure the broader accumulated cleanup,
not cap removal alone. A causal cap-only comparison also needs frozen compatible
executables/configuration and identical retained assignments; upstream RNG does
not guarantee identical independent rebuilds.

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
LimitedTagMinHeadCount=16
PostingPageLimit=3
PostingVectorLimit=118

[SearchSSDIndex]
SearchPostingPageLimit=3
```

The base row protects the own tag and takes the nearest distinct external tags
from actual **post-RNG, post-cut retained O records**, up to
`LimitedTagSlotsPerHead - 1`. A tag's distance is its closest retained member's
distance to that head (ties use tag ID). Rows may remain underfilled.
There is no separate candidate-count option, centroid-neighbor filling, or
base-row coverage repair. With expansion disabled, an unmet support floor
fails validation rather than replacing nearer tags or inventing candidates.
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
O source fails construction. There is no independent global extra-support cap:
candidate state grows incrementally only for distinct actual retained-O heads,
up to each tag's floor deficit. Total state is bounded by the sum of those
deficits, not a configured maximum or an eager floor-sized allocation.
Overflow and allocation failures remain explicit; targets are never truncated
to fit memory. The planner does not scan the entire head catalog once per tag.

All non-head records still use support-filtered placement and the same RNG
replica rule. Empty-result recovery ranks supported heads and applies that
same RNG rule; it does not impose a special one-replica policy on sparse tags.
The O assignments remain unchanged. Both H and O use the native construction
cut: if `PostingPageLimit>0`, the effective page count is
`max(PostingPageLimit, ceil(PostingVectorLimit*recordBytes/4096))`, and each
region initially retains at most `floor(effectivePages*4096/recordBytes)` nearest records.
This applies to every H head, not only heads with extra supports. For 140-byte
records, `PostingPageLimit=3` and `PostingVectorLimit=118` mean **5 pages /
146 records per region**, not 3 pages. Nonpositive build page limits disable
the cut. Whole records and their attributes are preserved; RNG copies beyond
the cut are intentionally dropped. If a non-head vector loses every H copy,
a lightweight pre-write rescue appends **one** of its already-computed H RNG
edges: the nearest originally assigned support-legal head, using the original
distance (head ID breaks distance ties). Vectors retaining any H copy receive
no supplement. This is not SPFresh dynamic insertion/refill: there is no second
BKT search, global support-head scan, new support, or restoration of all replicas.
Rescues are distance/VID ordered within each head's H tail, **before O**; the
normal H prefix and all O bytes/order/counts are unchanged. The H tail is not
recut and may exceed the normal build bound, subject to the existing native
uint16 page-count capacity. Full row attributes, VID and vector are serialized,
and pure counts, signatures and fingerprints include the rescued H records.
Build logs distinguish zero-H before/after, rescued vectors/records/postings,
and added H payload pages. A missing original source edge after successful
pre-cut placement remains an explicit internal inconsistency, not permission
to invent an edge. This lightweight policy is not a coverage/floor acceptance
gate or a guarantee of query recall.
Support-row membership is permission to receive records, not a guarantee that
every support has a nonempty H posting or that the realized floor is reached.

`SearchPostingPageLimit` independently bounds physical pages read from the
selected H prefix or O suffix, starting at that region's first physical page.
Only fully read records are scanned, including at an unaligned H|O boundary.
The `PostingVectorLimit` uplift is construction-only. Nonpositive search
limits mean an unrestricted region scan. `[SearchSSDIndex] PostingPageLimit`
is the native runtime alias for `SearchPostingPageLimit`; it does not change
the build cut. Numeric/flat/DNF fallback selects O but does not bypass this
budget or exact predicate checks. Rescue does not raise this budget: for
example a 12-page search can still stop before a rescued H tail.

`LimitedTagMaxExpandedPostingPages` is removed and explicit uses are rejected.
For an immutable older index, remove only that obsolete INI entry in a copy;
support binary versions/fingerprints are unchanged. Loading does not retrofit
the native H cut/rescue: rebuild to adopt it. The auditor strictly checks the
O construction bound but reports beyond-cut H records, postings and payload
pages without rejecting a legal H overflow. A saved index cannot prove that
arbitrary excess H records came from this rescue; these are **not** inferred
rescued counts. Zero-H non-heads and unobserved VIDs are diagnostic counts,
not a global coverage acceptance gate. Support legality, duplicates, replica
limits and retained-O support provenance remain validated.

New expanded support uses generation-bound V5 storage: the original base rows,
sparse `uint64` head offsets and sorted `uint32` extra tags, plus authenticated
per-tag feasible targets. V5 retains the 80-byte V4 header layout; the old
`maxExtraSupports` word is now the exact sum of
`max(RequiredHeadCount(tag) - baseCoverage(tag), 0)`, equal to the stored extra
count (including zero). It is a validated content invariant, not a build
parameter. Each tag's extras must exactly fill its source-capped deficit.
A new fingerprint discriminator authenticates these V5 semantics; V4 hashes
are not reinterpreted. File sizes/count arithmetic, requirements and CSR offsets
are checked before allocating the extra payload.

`LimitedTagMaxExtraSupports` and `--limited-tag-max-extra-supports` are removed;
explicit INI keys (even zero/empty, including saved-index metadata) and the CLI
flag are rejected. New saved INIs omit the key. `ConfigureExpansion` no longer
accepts a budget; the old `MaxExtraSupports()` API is removed.
`LegacyExtraSupportCap()` exposes **only** authenticated read-only V4 provenance,
and returns zero for V5/non-expanded tables. Old immutable V4 support files
retain their original positive cap, version, candidate-source provenance and
fingerprints on read/export, byte-for-byte; they need not satisfy V5's exact
deficit invariant. Migration means deleting only the retired INI entry in a
separate writable clone, never rewriting historical datasets, support bytes,
hierarchy/signature files or experiment manifests. A fresh rebuild creates V5.
Expansion-mode, generation, schema and expected-content compatibility guards
still apply; no runtime INI cap is needed to load either version.

The serialized
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
original base rows and uint64 CSR offsets in bounded chunks, so legacy V4 files
remain byte-compatible and V5 remains V5. Loading reserves the local rows before conversion and releases
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
rebuilt trees can vary even with one head-build thread because clock-based
global reseeding affects BKT k-means. `BKTSeed`/`TPTSeed` were local additions
(absent upstream), and remain rejected rather than exposed as tuning parameters.
Current CPU construction matches upstream `5619bb1` RNG behavior: BKT centers
use `Utils::rand` (global `std::rand`), with default `std::mt19937` shuffles.
TPT uses one default `std::mt19937` shuffle engine per worker; each tree calls
`Sleep(i * 100)` then `std::srand(clock())`, and projection weights use global
`rand()`. The upstream sleep/reseed sequence is intentional, not a new seed
control. There is no custom fixed seed or deterministic tree-ordinal stream.
One thread does not guarantee reproducibility; parallel scheduling and
floating-point reductions add variability. For paired build tests, reuse a
single saved head/candidate realization rather than assume independent builds
match. RNG alignment does not imply full upstream build bit-identity.
Saved seed declarations are load-only provenance, logged explicitly; existing
tree/graph bytes and query behavior are unchanged. Require matching source-ID, tree/graph,
CSR-geometry, base-support, and O fingerprints before attributing differences
solely to the support floor.

Historical fixed-seed controls and recorded results below remain frozen
provenance. They do not describe current nondeterministic reconstruction;
do not rewrite their manifests or infer that a fresh build has the same results.

#### Controlled SIFT1M / 8192-tag experiment

The completed run is
`../datasets/sift1m_zipf8192_numeric_support_expansion/limited_tag_support_expansion_runs/sift1m_zipf8192_numeric_support_expansion_measured_seeded_heads_final/`.
It uses the existing local SIFT vectors, Zipf-1 tags (minimum tag size 13),
exact top-10 truth, and the same three-layer spatial hierarchy in every
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

Historical command sequence (not a current fixed-geometry reproduction recipe):
it used the existing fixture and a fresh run label, refusing to overwrite an
existing run. Current independent builds use upstream RNG and can fail the
driver's required geometry/O identity audit; a matched comparison must reuse
the same realized heads/candidates. Do not bypass that audit or relabel the
historical results as current:

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
placement `O`. Base support reads only its retained posting prefixes after RNG
and posting cuts; neither rejected search candidates nor discarded scratch
suffixes contribute. A retained member beyond search rank two can contribute.
Support construction runs no additional nearest-head search and needs only
O(slots) aggregation scratch space, not an N-times-candidate-count buffer.
Distance ordering of the O scratch records is not required or changed.
With `BuildH1Graph=0`, spatial placement uses the hierarchy
callback to reach H1; the graphless H1 catalog is not a standalone search graph.
The original search and RNG geometry are unchanged. Non-head vectors are then
assigned only to heads supporting their attribute, using constrained BKT search
and RNG pruning for up to eight `H` replicas. The single STM1 v3 posting is
`H | O`: predicates safely anchored on the configured limited-tag key scan
`H`, while unfiltered and other predicates read only the complete `O` suffix.
This is a posting-membership choice after the same spatial traversal, not a
query route.
Cross-region overlap is intentional. Limited-tag builds require
`TailReplicaCount=0`; `O` is already self-contained, so no supplemental
unfilter-tail replicas are built.

`LimitedTagVoteHeadCount` has been removed from every active interface:
explicit INI/API values fail even when set to the old default. For immutable
old indexes, make a copy and remove that obsolete INI entry before loading
with this reader; do not change the support binary or its generation.
The packed LTS v1/v2/v3/v4 layouts remain unchanged; new expansion uses V5's
requirement-derived invariant as described above. The former vote-count
word in v3/v4/v5 is legacy provenance: **positive** preserves an old pre-RNG
candidate source; **zero** identifies retained-O construction. New support
hashes include a retained-source discriminator, and new build generations
also include that discriminator. Loading/exporting legacy v3/v4 preserves the
positive word and original body hash; it never relabels old support as
retained-O. The existing v1/v2 reader still requires authentic vector counts
before export to v3, retaining legacy provenance. Older readers reject new
zero-marker tables instead of interpreting them as vote-built indexes.
Rebuilding is required to change an old index's candidate semantics.
V8 head metadata automatically supplies separate categorical and
quantized-numeric posting signatures for `H` and `O`; the selected posting
region may consult its matching metadata before I/O. Numeric signatures use
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
At load, the validated support sidecar builds the metadata used for H-region
membership and exact result admission. Graph traversal remains distance-only.
It never retries or raises `MaxCheck` to fill a predicate-specific posting
quota, and it never enumerates a tag-to-H1 list. Empty physical postings do not
count as results. The same configured graph and posting budgets apply whether
the request is filtered or unfiltered. `HeadNavigationMode` is retired; an
enabled hierarchy is always used, while a disabled hierarchy uses native H1.

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

`[SelectHead] HierarchyLevels` counts H1: `3` means H3 -> H2 -> H1,
with only H3 retaining a search graph. Every layer uses the same
`[SearchSSDIndex] InternalResultNum` nprobe ceiling. One native,
distance-only top-graph search uses the fixed `HierarchyMaxCheck` budget.
`HierarchyInitialProbeRatio` then selects one fixed downward beam at every CSR
layer. There is exactly one hierarchy iteration: no saved-frontier widening,
underfill continuation, second graph pass, retry, or global scan.

H3/H2 are spatial routing-only indexes: their vectors do not enter the final
top-k. Only nodes returned by the top search become downward parents. At H1,
every unique reached child is distance-scored before support and exact-predicate
membership decides whether its posting or own vector may contribute. A matching
head outside the reached fixed beam remains unvisited, so sparse predicates may
return fewer than k. The global tag-to-H1 map remains for construction,
maintenance, and audit only.

`HierarchyMaxCheck` retains the native checked-work meaning. BKT counts checked
leaves/graph-neighbor evaluations, not every internal tree-pivot distance, so
it is not an exact total-distance ceiling. The same value is used regardless of
predicate presence or density. Existing saved top graph/tree settings still
apply; no signature or predicate state is passed into graph traversal.

Each layer owns its local visited state. Native BKT navigation uses its original
workspace without borrowed state or distance callbacks. CSR layers use separate
touched-word bitmaps and retain their allocated capacity across queries.
Distances and predicate state are not shared across layers; the query does not
translate upper local IDs into canonical H1 IDs.

Only H1 real heads and SSD records enter the final result set. They share the
existing global-VID deduper, including exact-predicate rejections. Each reached
H1 head is scored geometrically before its own-vector membership check; rejecting
that own vector cannot suppress a posting containing matching records. Exact
VID/deletion/predicate checks remain authoritative for both H1 points and SSD
records.
The compact H1 point heap is pooled with the query workspace. Only H1 children
reached through CSR descent are offered to it, including reachable heads with
empty postings. SSD retains `visited -> predicate -> decode/distance`.
Legacy H1 conversion, repeated admission, and compaction are skipped when the
H1 point heap is active. Empty/partial hierarchy results do not silently switch
to H1 graph navigation.

Persisted upper-layer signatures remain load/save compatibility metadata only.
They do not admit results, reject seeds or neighbors, or prune CSR children.
`HierarchyPrefetchMode=Rolling16` and `Batch64` prefetch layer-local vector
rows only and have identical distance-ordering, admission, and budget semantics.
Candidates use a reusable distance/ID-only bounded heap with local-ID
tie-breaking.

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

Historical placement and graph-signature A/B measurements remain under the
dataset's `hierarchy_audit_checks/` directory as frozen provenance. They were
produced by retired query semantics and must not be replayed or interpreted as
current routing modes. Current comparisons vary only native fixed budgets such
as `InternalResultNum`, `MaxCheck`, `HierarchyInitialProbeRatio`, and
`HierarchyMaxCheck`.

### Independent hierarchy-layer storage

New unquantized STATIC graphless hierarchies use `head_metaonly.bin` version 3.
H1 and every intermediate layer own complete, contiguous native vector
catalogs, including sampled/promoted rows. The top layer keeps only the vectors
inside its native BKT index, not an additional selection-catalog copy. H1 alone owns canonical result VIDs and V8 attributes; upper layers retain
spatial CSR structures and compatibility signatures, with obsolete upper
attribute artifacts removed.
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
operations. They do not change the unified spatial query policy. Legacy
signature fields remain authenticated compatibility data; they do not alter
current traversal. V3 still requires complete generation-bound metadata.

Graphless STATIC directory exports copy the persisted artifacts into a private
staging directory and use native loading to validate them before publication;
they do not recursively serialize the dummy KDT or logical views over the source.
In-place saves validate a hard-linked staging view and replace only the INI.
The loader resolves the physical root descriptor against the directory actually
being loaded, not an obsolete build/export directory embedded in the INI.

`LogPhaseTime=true` separates top-graph search, CSR gathering/deduplication,
admission, vector scoring, and sorting; the legacy `h2*` field names also apply
to deeper hierarchies. The same diagnostic lines report per-layer graph, merge,
admission, vector-scoring, and sorting times without enabling a separate
adaptive-search mode.

The retired EST4 sidecar and its split/merge serving route have been removed.
Rare labels use regular H/O support and the ordinary exact-filter path.
`HierarchySignatureMinSelectivity`/`HierarchySignatureMaxSelectivity` and their
`SecondLevel*` aliases are removed together. New builds may still persist the
complete categorical domain `(0,1]` for artifact compatibility and audit.
Legacy V3 domain endpoints remain authenticated fields in the unchanged
binary format (V2 implies full coverage). Load/repair/save preserve those
fields; old INI endpoints are read-only provenance, not overrides. Query
traversal ignores both new and legacy upper-layer signatures. Numeric-only and
unanchored predicates use the same hierarchy and fixed budgets as every other
request, selecting O when H-region membership is not valid.
The dataset generators derive their rare-label recipe from active native budgets;
this is not a separate serving policy. Before
heads exist they use `expectedHeadCount = native input count * SelectHead.Ratio`; the
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
`Ratio=.12`, two support slots, `InternalResultNum=96`, and the rare-label
dataset coverage formula. `spannbuilder` uses its tenant-0 bulk path for these inputs: mapped
vectors and attributes are borrowed through the synchronous build instead of
materializing one metadata string, pointer pair, and global ID per vector.
Both original and constrained placement retain only emitted RNG edges rather
than initializing `N * ReplicaCount` slots.

```bash
Tools/benchmarks/prep_sift1b_inputs.sh
CFG=Tools/benchmarks/build_spann_attr_sift1b_zipf200_limited_tag.ini
Tools/benchmarks/run_spann_attr_build.sh "$CFG"
```

The canonical build uses one global spatial hierarchy,
`[Tags] ColumnTypes=categorical,numeric`, and explicit `LimitedTagColumn=0`. Categorical mask layout is internal filter metadata, not
an attribute hierarchy or a partition plan. Signed support and hierarchy
artifacts retain generation checks; upper-layer signatures are compatibility
metadata and do not participate in query traversal. Historical four-category SIFT1B recipes explicitly retain the archived
`sift1b_tags5.u32` schema; old `4node` filenames do not authorize schema reinterpretation.
These inputs are not produced by the current generator.

Attribute organization has been removed, not merely disabled. `ACLCols`,
`HierLevelWidths`, `PivotForceNodeCount`, `DisablePivotEstimator`, `RoutingCols`,
`PerVectorTagsFile`, `SelectHeadType=PerTagBKT`, their retired environment
overrides, and `--routing-only` are rejected before a native build. Remove
them from current configurations; use `SelectHeadType=BKT`. The planner API
`EstimatePivotBuildPlan` / `EstimatePivotPlan`, grouping utilities, and
`tag_node_index.bin` production/consumption are gone. `--merge-tags5` retains
`--acl-cols` as an input **column count**, not a routing projection, and no
longer writes a grouping file.

`Hierarchy*` still configures the spatial H1..H5 hierarchy. Attribute records,
exact categorical/numeric filtering, tag support, H/O placement, signed
hierarchy CSR and head/posting signatures remain. Generic physical bundle
APIs remain for geometry and upstream CRUD; tagged inserts into such bundles
choose their owner by vector distance rather than an attribute pivot.

### Reduced filtering configuration

The current H5 recipe has **86 explicit entries, down from 138** after the
attribute-partition cleanup. Most remaining entries are original SPANN
construction/search settings or input/output configuration, not filtering
tunables. The filtering/hierarchy-specific overrides are:

```ini
[Tags]
ColumnTypes=categorical,numeric

[SelectHead]
HierarchyEnabled=true
HierarchyLevels=5
HierarchyReplicaCount=8
BuildH1Graph=false
ParallelBKTBuild=true

[BuildSSDIndex]
EnableLimitedTagPosting=true
LimitedTagColumn=0
LimitedTagMinHeadCount=16
EnableLimitedTagSupportExpansion=true

[SearchSSDIndex]
HierarchyInitialProbeRatio=0.666666
HierarchyMaxCheck=192
```

This is an excerpt, not a standalone build INI: shared native `Ratio=.12`,
posting nprobe `InternalResultNum=96`, construction threads 45, search threads
1, data paths and original graph/storage settings remain in the full recipe.
`ParallelBKTBuild=true` is intentional for the 1B profile: it processes sibling
BKT nodes concurrently. It consumes more temporary k-means memory than the
serial implementation, so launch preflight must verify sufficient RAM.

| Removed interface | Replacement or reason |
| --- | --- |
| `LimitedTagMaxExtraSupports` | Per-tag source-capped deficits bound incremental candidate storage; no independent global cap. V4 caps remain read-only provenance. |
| `NumericCols` / `SPTAG_NUMERIC_COLS`, old bulk prefix-count option | Use `[Tags] ColumnTypes` for every original column; width and numeric lane mapping are derived. Legacy stored prefix metadata remains readable. |
| `HierarchySignatureMinSelectivity`, `HierarchySignatureMaxSelectivity` and `SecondLevel*` aliases | New signatures cover all labels; legacy domain metadata remains authenticated and read-only. |
| `BKTSeed`, `TPTSeed` | Upstream global/clock RNG behavior; no custom fixed seed or external seed knob. Loaded historical geometry stays unchanged. |
| `HeadNavigationMode`, `HierarchyGraphSignaturePruning` | One enabled hierarchy is used for every query; graph and CSR traversal are distance-only. |
| `HierarchyRouteSelectivityThreshold`, `SecondLevelRouteSelectivityThreshold` | Predicate selectivity never chooses a graph or budget. |
| `SparseFallbackMaxHeads`, `SparseFallbackMaxPostingPages` | Direct H1 completion and widening are removed; one fixed top-graph search is followed by one fixed-beam CSR descent. |
| `ForceDenseTagSearch`, `DirectSparseMaxPostings` | Dense/sparse route selection and direct posting scans are removed. |
| `FilteredSearchNprobeSafety`, `FilteredSearchTargetRecall`, `FilteredSearchCoverageExponent`, `EnableAdaptiveFilteredNprobe`, `LogAdaptiveNprobe` | Filtered and unfiltered requests share the configured native posting and graph budgets. |
| `FilterKeepUExtra` | All configured bundle nodes participate independently of predicate presence. |
| `HybridRouteSampleCount`, `HybridRouteSelectivityThreshold`, `HybridRouteDeformationThreshold`, `LogHybridRoute` | Hybrid query-route selection is removed; hybrid-format metadata is diagnostic/compatibility data only. |
| `EnablePrimaryHeadBypass`, `BuildPrimaryHeadCSR`, `PrimaryHeadBypassRerankL` | Primary-head direct bypass and its sidecar are removed. |
| `EnableExtremeSparseTag`, `ExtremeSparseTagMinCount`, `ExtremeSparseTagFile`, `LogExtremeSparseTagRoute` | Unreachable EST builder/reader/query/merge route and sidecar store removed. Rare labels use ordinary H/O support. |
| `FilterKeepCross`, `LogUExtra`, `PostingQuantBits` / `--posting-quant-bits` | No effective consumer. Actual codec metadata determines its representation. |
| `DisableCrossSubgraph` | Use the existing `DisableCrossEdges` gate. |
| `UnifiedNprobeBudget`, `MultiNodeBudgetKeepRatio` | One aggregate native posting budget; no per-bundle budget multiplication. |
| `HybridGraphDegree` | Existing fixed format degree 16, not an adjustable capacity. |
| `EnableHierPostingFilter` | Validated posting/tail metadata is used automatically; missing or unusable metadata does not authorize dropping matches. |
| `[MultiTenant] CrossEdges`, `CrossExtraEdges` | Canonical `[BuildSSDIndex]` settings only. |
| `[MultiTenant] DualPoolAugment`, `DualPoolExtraRatio`, `UExtraIDFile` | Canonical `[SelectHead]` settings only. |

These retired options and duplicate section aliases, in addition to the
retired attribute-organization interfaces, are rejected
even when supplied as `false` or `0`, including launcher/native preflight.
`BuildSignaturesWithVectors` and `LoadAllForSignatureRepair` are removed;
use `BuildSignatures` and ordinary `LoadAll`.

The 42 removed H5 entries are **10 occurrences of retired options, four
section-alias entries, 27 omitted defaults/inactive-mode settings, and one
ineffective mis-sectioned storage default**. Recipe omissions do not remove
their native interfaces unless explicitly retired below. Flat ACL exact
admission is column-agnostic across categorical columns; DNF uses its stated
columns. Neither sorted categorical ranges nor disjoint domains are required.
Posting signatures do not prune the shared spatial posting prefix.

Other omissions from the H5 recipe are defaults or controls for inactive
alternative layouts, not deleted functionality. Support slot counts and
support floor remain independent; the total expansion budget is removed.
H/O construction uses native
`PostingPageLimit`/`PostingVectorLimit`; selected-region runtime reads use
`SearchPostingPageLimit`. Hierarchy beam ratio, hierarchy scoring work, native
graph `MaxCheck` and posting nprobe measure different work and remain separate.
Neither hierarchy signatures nor predicate density modify those budgets.
**O/suffix reading is preserved** under the same native region page limit.
Ordinary legacy pure+tail postings use one contiguous prefix for every
predicate. `EnableUnfilterTail`, `UnfilterPurePages`, `UnfilterExtraTailPages`,
`UnfilterPureDistanceScanPercent`, `AblateUExtra` and `AblateTail` are now
removed and rejected, including false/zero/default values. Tail construction
parameters and persisted boundaries are unchanged. Actual hybrid, cross-edge and upstream
CRUD modes remain available through their canonical native settings.
The ineffective `[BuildSSDIndex] SSDIndexFileNum=1` copy is also omitted;
the original native `[Base] SSDIndexFileNum` storage option is not removed.

Old saved INIs containing retired keys are intentionally rejected. Use their
historical reader or rebuild from a canonical configuration; do not rewrite
immutable historical build artifacts in place.

For retired query-routing settings, adopting the new query policy can use a
separate writable clone with just those INI entries removed.
Verify native reload/search and unchanged geometry/support/posting hashes.
This intentionally changes query behavior, not an inert-default migration:
the enabled hierarchy now uses one fixed traversal for every query, and
limited-budget recall or result count can decrease without global completion.

An audited **inert serialized default**, such as `PerVectorTagsFile=` in an
otherwise compatible BKT index, does not require rebuilding vector/posting data:
create a separate writable clone, remove only the verified inert retired INI
entries there, and require strict native load/export/reload validation before
publication. Keep source artifacts unchanged and verify native artifact hashes,
generation bindings, sampling IDs, CSR and VID mappings are unchanged. Do not
blindly strip nondefault settings: active grouping or different query-budget/
cross-edge semantics require a separate compatibility decision. New `SaveConfig`
output no longer contains these removed parameter definitions. Empty defaults
are not silently accepted by the new reader.

## Ordered Static STM1 Layout Compatibility

Build-only `EnableOrderedPageStart=true` together with `OrderedPageStartAttrs`
sorts each STM1 pure
posting prefix by the hierarchy tuple and persists `ordered_page_starts.bin`:
one `int32` page-start signature ordinal per configured attribute per posting
page.

```ini
[BuildSSDIndex]
Storage=STATIC
EnableOrderedPageStart=true
OrderedPageStartAttrs=2,3
```

For the SIFT hierarchy, columns `2,3` are team and project. Existing sorted
postings and their authenticated directories remain readable, but queries do
not use the directory to select, skip or widen pages. All predicates scan the
same native `SearchPostingPageLimit` prefix. The layout settings are rejected
in `[SearchSSDIndex]`, not interpreted as runtime filtering switches.

For the distance-order path, set `EnableOrderedPageStart=false`. The builder
does not apply the attribute tuple sort: pure records retain the selection
order `(head distance, VID)`, while tail records retain their separate
`(head distance, VID)` order. It removes any stale
`ordered_page_starts.bin`. This distance-order layout is the canonical SIFT1B
recommendation. The retired percentage and tail-page runtime controls must not
be used to restore predicate-dependent scans. `SPTAG_OPQ_PREFILTER`,
`SPTAG_PAGE_SELECT`, `SPTAG_PAGE_DIAG`, `SPTAG_DNF_NODROP`, and
`SPTAG_RBQ_EXHAUSTIVE` are also rejected on presence.
Compressed postings that cannot fit within a positive native page cap are
not read: the decoder requires the complete compressed payload. Rearranged
postings admit only records whose vector and VID both fit in the read prefix.

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
- `run_multitenant_tag_cache_stress.sh`: reproducible runner with fixed
  workload defaults and environment controls for the harness only.

Default workload:

- `1000` queries split into `10` batches of `100`
- sequential workload: tenants `0 -> 9`, one tenant per batch
- random workload: tenants mixed within each batch
- single-tag filter per query, sampled from the tenant's true tag distribution
- `topk=10`
- `seed=20260413`
- cache limit policy: `max(2 * largest HeadIndex, total HeadIndex / 4)` rounded up to MB

Search behavior comes from each index's native INI. The harness does not set
predicate-density, direct-posting, adaptive-nprobe, or forced-dense controls.

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