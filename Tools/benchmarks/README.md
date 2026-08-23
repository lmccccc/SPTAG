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
categorical/numeric limited-tag experiment. It keeps one canonical H1 head
graph and does not create attribute subsets, hybrid edges, or cross edges.
The record schema is driven by native `NumTagsPerVec`, `ACLCols`,
`NumericCols`, and `LimitedTagColumn` values rather than a fixed attribute
count. Limited-tag placement and H1/H2 routing use only categorical equality
anchors on `LimitedTagColumn`; all categorical and numeric DNF3 literals are
still evaluated exactly on posting records. Native
`[BuildSSDIndex] LimitedTagSlotsPerHead` accepts any positive integer (`2` is
the default).
Each head persists exactly that many support values in generation-bound
`limited_tag_support.bin`:

```text
support[head][0] = source vector attribute
support[head][1..N-1] = top-(N-1) external attributes
```

The requested count cannot exceed the number of distinct key-column tags,
because every support value on a head is distinct. With one slot, a head
supports only its source vector's tag.

The canonical INI sets `LimitedTagSlotsPerHead=2` explicitly. Use
`build_spann_attr_sift1m_zipf200_limited_tag4.ini` for the isolated four-slot
experiment.

The normal BKT candidate search first builds the complete original SPANN
placement `O`. Its raw top-`LimitedTagVoteHeadCount` candidates are reused as
support votes before RNG pruning, so support construction does not run a second
nearest-head search. The vote count must not exceed `InternalResultNum`, keeping
the vote window inside the unchanged original search. Non-head vectors are then
assigned only to heads supporting their attribute, using constrained BKT search
and RNG pruning for up to eight `H` replicas. The single STM1 v3 posting is
`H | O`: limited-tag queries scan `H`, while unfiltered and exact-filter fallback
queries navigate the original graph and read only the complete `O` suffix.
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
At load, the validated support sidecar builds only a compact tag-to-head lookup.
H1 search always performs distance-only graph navigation for exactly the
configured `InternalResultNum` heads, then applies the support predicate to
those posting IDs before I/O. Tag support never participates in the H1 graph
inner loop.

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