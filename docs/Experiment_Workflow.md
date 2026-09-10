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
| `opq_codes_m25.bin` `(N,25)` uint8 | `--gen-opq-codes` | raw OPQ codes (raw-widen, ADC=false, header-less) — **not** the normalizing `Release/quantizer` |
| `opq_quantizer.bin` | copied | the OPQ codebook (search-time ADC) |

Declare the five original columns in `[Tags]` as
`ColumnTypes=categorical,categorical,categorical,categorical,numeric`.
No attribute-partition grouping file is a build input. The tag file remains
headerless row-major `uint32`, beginning at byte zero; vector input must use
its genuine native container (`DEFAULT`, `TXT`, or `XVEC`), not a relabeled
headerless `RAW` file. The raw OPQ codes above are a separate codec sidecar.

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

Thin launcher over the native `.ini`. It validates removed interfaces before
building and derives paths from their native INI sections. It runs
`spannbuilder -c <config>`, reuses any configured cross-edge sidecar built
before STATIC tail construction (or runs the legacy `augmentheadgraph`
fallback gated by `[BuildSSDIndex] CrossEdges`), and copies the OPQ codebook
into `tenant_0/`.

```bash
Tools/benchmarks/run_spann_attr_build.sh Tools/benchmarks/build_spann_attr_spacev1b_opq25.ini
#   internally: Release/spannbuilder -c <ini>
#             + Release/augmentheadgraph -d $IDX/tenant_0/HeadIndex -k 15 -m N -t T -w true
```

Fresh builds refuse an existing `IndexDirectory`, even an empty directory;
they never wipe it. Select a new output path, or explicitly enable
`[MultiTenant] ResumeBuild=true` only for a compatible SelectHead checkpoint.
This does not provide arbitrary O/H-stage resume. Inherited resume/in-place
environment flags cannot override the INI.

When `[Build] BuildSignatures=true`, the launcher creates a unique primary-build
INI that changes only this entry to `false`. It prints the exact path and retains
the file on success or failure in the configured `TmpDir` (or the repository's
`build/primary-build-configs` directory when `TmpDir` is absent). Signature
construction then runs in a fresh process with the original INI. Keep both
INIs with the run's logs, binaries and source hashes; no stage-preservation
patch to a frozen launcher is needed.

Build phases in the log: global spatial `BKT` head selection → optional
`[SelectHead] DualPoolAugment` (U_extra) → `Begin Build Head` (BKT + RNG graph
over the heads) → `BuildSSDIndex` (in-posting vectors/codes and attributes) →
`SaveAll`, in place when configured. Cross edges, U_extra and legacy tail
replicas are construction/layout choices, not an unfiltered-only query
pipeline. Any retained cross-edge policy and spatial candidate/page budgets
apply to every predicate. Attribute partitioning and its grouping files are
removed; the current limited-tag hierarchy uses one spatial traversal with
H/O posting membership.
Billion-scale knobs (resume checkpoint, pinned BKT
balance factor, in-place build, slim SSD block-pool sizing) are documented in
**AGENTS.md → "Billion-scale build options"**.

The 3M-scale sibling config is `Script_AE/iniFile/build_spann_attr_spacev_opq25.ini`.

---

## (5) Query / benchmark  —  native persisted search config

Build and search parameters are persisted in the same native `.ini`:
`[SearchSSDIndex] InternalResultNum`, `MaxCheck`, and `SearchPostingPageLimit`.
All predicates use the same spatial traversal and page budget; exact attributes
only admit results and select H/O membership. Ordinary legacy tails remain in
the common readable prefix. Signatures do not choose a navigation path, prune
graph/posting candidates, or enlarge a query budget. BKT/KDT stopping uses an
independent unfiltered distance heap and the same initial-pivot/neighbor caps,
so an underfilled exact result set does not continue the tree frontier.
Sparse or budget-limited queries may legitimately return fewer than top-k,
including no results. Count these queries in recall; do not retry or complete
them through a support-head scan.

Unfiltered-only tail/page controls (including `EnableUnfilterTail`) and
`SPTAG_OPQ_PREFILTER` are removed and rejected, including false/zero.
`TailReplicaCount` and `UnfilterTailBufferLength` remain construction settings,
not runtime exceptions. Do **not** override build or search parameters through
environment variables.

For the committed Float STM1 SIFT-1M fixture, build the native benchmark target
and run it directly against `.npy` queries and local-ID groundtruth:

```bash
cmake --build build --target spannaclbench -j

CFG=Tools/benchmarks/build_spann_attr_sift1m_tagged_4node_static_fullfloat_tail_unbounded_prefilter.ini
Tools/benchmarks/run_spann_attr_build.sh "$CFG"

IDX=/datadisk/yfcc_fast/sptag_sift1m_tagged_vs_upstream/index_tagged_4node_static_fullfloat_tail_unbounded_prefilter
QDIR=/home/v-mochengli/datasets/sift1m/multitenant/query

# Exact project ACL admission. Spatial candidate/check and posting-page budgets
# come from persisted [SearchSSDIndex] values in $IDX/tenant_0/indexloader.ini.
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
| 2. builder inputs | `prep_spacev1b_inputs.sh` | `*_tags5.u32`, `opq_codes_m25.bin`, `opq_quantizer.bin` |
| 3. groundtruth | `generate_query_tenant_tag_groundtruth.py` | `groundtruth_{unfilter,org,dept,team,project}_local_ids.npy` |
| 4. build | `run_spann_attr_build.sh <ini>` | `$IDX/tenant_0/` (HeadIndex + SSD postings + cross-edges) |
| 5. query | `spannaclbench` | JSON `{recall, qps, latency, posting metrics}` |
