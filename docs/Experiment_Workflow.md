# Standard Experiment Workflow — Attribute-aware SPANN (multi-tenant)

End-to-end reference for running a filtered/unfiltered ANN experiment on the
attribute-aware (multi-tenant) SPANN index. The worked example is **SPACEV-1B**
(1 000 000 000 × 100 int8); the same five stages apply to SIFT-1M / YFCC-10M by
swapping the dataset paths.

```
(1) generate attributes  ->  (2) derived builder inputs  ->  (3) groundtruth
                                                                    |
(5) query / benchmark  <-  (4) build index  <------------------------+
```

All scripts below are committed under `Tools/benchmarks/`. Every billion-scale
build knob lives in the native `.ini` (see **AGENTS.md → "Build Config — Native
`.ini`"**); the launcher carries only process-loader setup and reuses the
pre-BuildSSD cross-edge sidecar (with a legacy fallback when necessary).

Conventions used in the commands:

```bash
DS=/path/to/MSSPACEV1B                 # dataset root: spacev1b_base.i8bin + query.i8bin
OUT=/datadisk/yfcc_fast/spacev1b_build # derived builder inputs (tags5, opq codes)
IDX=/datadisk/yfcc_fast/spacev1b_opq25 # IndexDirectory from the .ini ([Base] IndexDirectory)
REL=$PWD/Release                       # built binaries + SPTAG.py python binding
```

---

## (1) Generate attributes  —  `gen_spacev_attrs.py`

Synthesizes the per-vector attributes in the same layout the SPANN build/search
trust (single tenant 0 = all vectors):

| File (under `$DS/multitenant/`) | Shape / dtype | Meaning |
| --- | --- | --- |
| `tags.npy` | `(N,4)` uint32 | ACL 4-level hierarchy `[org,dept,team,project]`, globally-unique ids, perfect 4-ary tree (card `[4,16,64,256]`) |
| `num_attr.npy` | `(N,)` int32 | numeric **price** in `[0,100000)`, range predicate `price < X` |
| `tenant_ids.npy` | `(N,)` int32 | all 0 (single tenant) |
| `query/query_tags.npy`, `query/query_vectors.npy`, `query/query_tenant_ids.npy` | per-query | one random ACL path + the query vectors |
| `tenant_tag_scenario.json` | — | describes both attributes + the numeric selectivity sweep grid |

```bash
SPACEV1B_ROOT=$DS python3 Tools/benchmarks/gen_spacev_attrs.py
# (ROOT is also accepted as argv[1]: python3 gen_spacev_attrs.py $DS)
```

Deterministic (`SEED=20260615`). The ACL leaf is drawn uniformly per vector and
the team/dept/org columns are derived by nesting, so the four columns are always
mutually consistent.

---

## (2) Derived builder inputs  —  `prep_spacev1b_inputs.sh`

Pure-C++ (no Python, no generic quantizer) prep of the three sidecars the build
consumes, via `spannbuilder` subcommands that mirror `Quantizer/main.cpp` so the
artifacts are byte-exact with the in-posting convention:

| Output (under `$OUT`) | Built by | Meaning |
| --- | --- | --- |
| `spacev1b_tags5.u32` `(N,5)` uint32 | `--merge-tags5` | `[org,dept,team,project \| price]` — interleaves `tags.npy` + `num_attr.npy` |
| `spacev1b_group_tags.txt` | `--merge-tags5` | ACL col 0 (org), one int/line — the PerTagBKT routing key |
| `opq_codes_m25.bin` `(N,25)` uint8 | `--gen-opq-codes` | raw OPQ codes (raw-widen, ADC=false, header-less) — **not** the normalizing `Release/quantizer` |
| `opq_quantizer.bin` | copied | the OPQ codebook (search-time ADC) |

```bash
Tools/benchmarks/prep_spacev1b_inputs.sh        # full 1B
Tools/benchmarks/prep_spacev1b_inputs.sh 2000000  # smoke subset (first N vectors)
```

> The OPQ codebook is trained once on a small subset (3M) and reused; RaBitQ code
> sidecars are produced instead by `Release/rabitq2_encode_stream` (value-type
> aware, scales to 1B). Pick OPQ **or** RaBitQ in the `.ini`'s `[BuildSSDIndex]`.

---

## (3) Build groundtruth  —  `generate_query_tenant_tag_groundtruth.py`

Computes the **exact** top-k for every query, five ways (matmul-batched on the
tenant-0 base), and writes them next to the query vectors:

* `groundtruth_unfilter_local_ids.npy` — all tenant-0 vectors
* `groundtruth_{org,dept,team,project}_local_ids.npy` — vectors whose ACL tag at
  that level matches the query (the filtered cases)

Neighbor ids are tenant-0 **local** row indices (`groundtruth_local_ids`
convention). `--metric` MUST match the index build (`l2` for SPACEV/SIFT int8).

```bash
python3 Tools/benchmarks/generate_query_tenant_tag_groundtruth.py \
  --scenario-file $DS/multitenant/tenant_tag_scenario.json \
  --query-file    $DS/query.i8bin \
  --output-dir    $DS/multitenant/query \
  --topk 10 --metric l2
```

> **Scale caveat:** exact GT is `O(Nq × N)`. For 1B base this is GPU/large-RAM
> territory; run it on a subset of the base (or a GPU brute-force) when full-scale
> exact GT is infeasible, and report recall against that. The ACL-level GTs are
> cheap (they filter the base first). The numeric `price < X` predicate is
> described in `tenant_tag_scenario.json` (`sweep` grid) for selectivity studies.

---

## (4) Build index  —  `run_spann_attr_build.sh`

Thin launcher over the native `.ini`. It derives all paths FROM the `.ini` via
`sed`, runs `spannbuilder -c <config>`, then reuses the cross-edge sidecar built
before STATIC tail construction (or, for older builders, runs the fallback gated by
`[BuildSSDIndex] CrossEdges`)
runs the post-build `augmentheadgraph` cross-graph step and copies the OPQ
codebook into `tenant_0/`.

```bash
Tools/benchmarks/run_spann_attr_build.sh Tools/benchmarks/build_spann_attr_spacev1b_opq25.ini
#   internally: Release/spannbuilder -c <ini>
#             + Release/augmentheadgraph -d $IDX/tenant_0/HeadIndex -k 15 -m N -t T -w true
```

An optional predicate-workload bootstrap runs before index construction:

```ini
[Tags]
NumTagsPerVec=5
AttributeTypes=label,label,label,label,range

[PredicateWorkload]
KeyAttribute=0,1,2,3
PredicateColumns=0,1,2,3,4
TrainSetFile=/path/to/predicate_train.tsv
QueryCount=2048
```

If `TrainSetFile` already exists, an external workload is retained unchanged;
generated workloads are reused only when their source and configuration
metadata and complete data rows match. Otherwise the builder writes the train
set atomically. One categorical key column is a label key, multiple categorical
key columns form a hierarchy, and one column declared `range` is a range key.
Label and range columns may be interleaved. `PredicateColumns` is the complete
synthetic predicate-column
whitelist and defaults to all tag columns when omitted. It must include the key
columns.

With `QueryCount>0`, each output row is one complete DNF query. `KeyAttribute`
marks the routing/planning key but does not constrain query syntax: any clause
may include or omit the key, and keyless clauses remain part of the workload for
global-fallback costing.
The remaining generation policy is intentionally fixed rather than exposed as
index tuning:

- 16 representative candidates per attribute and a fixed recorded seed;
- feasible default clause-count strata from `{1,2}`, with query frequency
  inversely proportional to clause count (`2:1`) so single-clause queries
  dominate while both strata contribute the same total clause budget;
- uniform sampling over all non-empty logical-attribute masks;
- a full tag scan for datasets up to 1M rows, otherwise one 65,536-row
  discovery sample and one disjoint 65,536-row evaluation sample.

Categorical predicates use equality, numeric predicates use sampled ranges, and
DNF clauses may overlap, but every retained clause must contribute at least one
evaluation-sample row beyond the union of the other clauses. This prevents
nominal multi-clause shapes from collapsing semantically. Dense predicates are
retained; the later subset cost planner decides whether optimizing them is
beneficial. Generated DNF caches use the v5 format with grammar, shape,
selectivity, and content-hash validation. `QueryCount=0` retains the internal
atomic-key compatibility mode. Omitting the section preserves the previous
build path. Unknown keys and malformed values are rejected.

Four-clause DNF remains supported by the generator and external train-set
format for explicit stress workloads, but is excluded from the default
synthetic prior.

`QueryCount>0` also enables workload-aware subset planning. After the train set
is ready and before `SelectHead`, the builder parses all workload rows into
canonical weighted DNF and precomputes each query's exact sampled bitmap. These
bitmaps form query hyperedges over mutually exclusive partition cells. A
deterministic sampled vector-neighbor graph supplies the physical boundary
penalty using the configured ANN distance metric.

The planner performs global beam search over complete partition states. At each
leaf count it compares all legal `leaf × atom` splits, retains up to eight
deduplicated topologies, and permits an intermediate split to increase cost so
multi-step DNF refinements remain reachable. Training cost combines touched
population, subset startup/coordination, vector-boundary, and subset-overhead
terms. A deterministic held-out workload selects both topology and node count.
The selected partition is then compiled into a decision tree; every vector
receives exactly one primary leaf.

Planner scores are normalized structural work rather than dataset-calibrated
milliseconds. Query work combines routed population and normalized subset
fanout; the objective then multiplies this by vector-boundary and leaf-count
factors. Planning uses deterministic samples and an independent held-out
workload split, and declares convergence after eight consecutive leaf counts
fail to improve the held-out incumbent. It otherwise continues until no legal
refinement remains or the 64-leaf physical safety limit is reached; neither
eight nor 64 is used as the selected node count. The planner therefore runs
before index construction and does not depend on measurements or statistics
from an already-built index.

Complete `(kind,column,op,value)` atom-to-node mappings are persisted in
`predicate_node_index.bin` for DNF routing.
`predicate_subset_attributes.bin` records each selected subset's complete
root-to-leaf attribute path, including whether every atom is true or false, so
multi-attribute subset semantics remain inspectable without reconstructing the
planner.
`predicate_subset_plan.bin` stores the exact build-time decision tree for
signature-only and routing-repair runs; `predicate_subset_plan.tsv` records the
selected beam path and objective components for analysis. Repair refuses to
infer a replacement tree when the binary plan is missing, because an equal leaf
count does not imply the same physical partition.

At query time, exact DNF literals retain their attribute columns. If routing
resolves to exactly one subset, head search is restricted to that subset's
local graph. If the predicate intersects several subsets, or cannot be safely
resolved to one subset, head search uses all bundle nodes plus the persisted
cross-subset edges as one global best-first graph; the original DNF remains the
authoritative posting post-filter. This avoids assuming that the induced union
of several local graphs is connected. A filtered global query fails explicitly
if its cross-edge sidecar is unavailable or dirty rather than silently falling
back to independent local searches.

Categorical posting signatures also retain physical attribute columns. Their
lane count is derived from all `label` entries in `AttributeTypes` and persisted
per index in `head_node_meta.bin` V7; it is no longer limited to five columns.
Numeric range signatures use a separate physical-column map in
`numeric_meta.bin`. Older metadata versions remain loadable, but signatures
must be rebuilt to accelerate columns absent from the legacy layout.

Attribute-aware builds use degree 20 for each subset-local head graph and keep
32 cross-subset edges per head. Local graphs serve only uniquely routed
predicates; multi-subset, unroutable, and unfiltered queries depend on the
global graph, so the larger connectivity budget belongs to the cross edges.

For a predicate routed to \(r\) subsets covering population fraction \(p\), the
runtime also decides whether posting scans should stop at each subset-pure
prefix or include the global unfilter-tail. The decision reuses build-time
bundle `assignmentCount` values and accounts for the head over-fetch needed to
obtain the configured number of predicate-valid postings:

```
local_pure_cost  = head + posting * p                         (r = 1)
global_pure_cost = head / p + posting * p + coordination*(r-1)
global_tail_cost = head + posting
```

The full-precision SIFT1M calibration uses `head=0.607 ms`,
`posting=2.141 ms`, and `coordination=0.080 ms`. The common nprobe multiplier
cancels in the route comparison, so the decision remains nprobe-independent.
The `head/p` term is required because a multi-subset pure query retains only
approximately fraction \(p\) of globally selected heads after its safe posting
signature filter. Omitting this term made `nprobe=96` behave like roughly 24
postings for an org predicate and reduced Recall@10 from about 0.98 to 0.87.
In global-tail mode posting and page-signature prefilters are disabled because
those signatures describe the pure prefix and cannot safely reject matching
tail records.

Workload-planned multi-bundle
indexes are currently build-time immutable; tagged inserts must rebuild the
index so assignments and routing sidecars remain consistent.

For workload-enabled builds, this planner replaces the hierarchy pivot
estimator and ignores the legacy fixed `PivotForceNodeCount` choice. Without a
positive `QueryCount`, the existing hierarchy/fixed-pivot build path is
unchanged.

To generate and inspect the workload without constructing an ANN index:

```bash
Release/spannbuilder -c Tools/benchmarks/generate_sift1m_predicate_workload.ini
```

Build phases in the log: `PerTagBKT` head selection → `DualPoolAugment` (U_extra)
→ `Begin Build Head` (BKT + RNG graph over the heads) → `BuildSSDIndex` (slim
in-posting postings) → in-place `SaveAll`. For the three **unfilter-enhancement
layers** (cross-graph / U_extra / unfilter-tail) — which must be enabled together
or unfilter degrades to a per-node fan-out — see **AGENTS.md → "Unfilter
Enhancement Pipeline"**. Billion-scale knobs (resume checkpoint, pinned BKT
balance factor, in-place build, slim SSD block-pool sizing) are documented in
**AGENTS.md → "Billion-scale build options"**.

The 3M-scale sibling config is `Script_AE/iniFile/build_spann_attr_spacev_opq25.ini`.

---

## (5) Query / benchmark  —  native persisted search config

Build and search parameters are persisted in the same native `.ini`:
`[SearchSSDIndex] InternalResultNum`, `MaxCheck`, `EnableUnfilterTail`,
and `EnableHierPostingFilter`. Do **not** override build or search parameters
through environment variables.

For the committed Float STM1 SIFT-1M fixture, build the native benchmark target
and run it directly against `.npy` queries and local-ID groundtruth:

```bash
cmake --build build --target spannaclbench -j

CFG=Tools/benchmarks/build_spann_attr_sift1m_tagged_4node_static_fullfloat_tail_unbounded_prefilter.ini
Tools/benchmarks/run_spann_attr_build.sh "$CFG"

IDX=/datadisk/yfcc_fast/sptag_sift1m_tagged_vs_upstream/index_tagged_4node_static_fullfloat_tail_unbounded_prefilter
QDIR=/home/v-mochengli/datasets/sift1m/multitenant/query

# Exact project ACL check. nprobe and the STM1 posting-mask prefilter come from
# the persisted [SearchSSDIndex] values in $IDX/tenant_0/indexloader.ini.
Release/spannaclbench \
  --index "$IDX" \
  --queries "$QDIR/query_vectors.npy" \
  --truth "$QDIR/groundtruth_project_local_ids.npy" \
  --query-tags "$QDIR/query_tags.npy" \
  --tag-column 3 \
  --warmup 200 --max-queries 1000
```

The command emits one JSON object with recall/QPS plus posting-efficiency
metrics. For a curve, create one native INI/`indexloader.ini` overlay per
point; set `InternalResultNum` and tune `MaxCheck` as the graph
candidate/check budget. Keep the index files immutable and use local overlays,
as done by the benchmark artifacts under `Tools/benchmarks/`.

---

### Quick checklist

| Stage | Script | Key output |
| --- | --- | --- |
| 1. attributes | `gen_spacev_attrs.py` | `tags.npy`, `num_attr.npy`, `query/` |
| 2. builder inputs | `prep_spacev1b_inputs.sh` | `*_tags5.u32`, `*_group_tags.txt`, `opq_codes_m25.bin` |
| 3. groundtruth | `generate_query_tenant_tag_groundtruth.py` | `groundtruth_{unfilter,org,dept,team,project}_local_ids.npy` |
| 4. build | `run_spann_attr_build.sh <ini>` | `$IDX/tenant_0/` (HeadIndex + SSD postings + cross-edges) |
| 5. query | `spannaclbench` | JSON `{recall, qps, latency, posting metrics}` |
