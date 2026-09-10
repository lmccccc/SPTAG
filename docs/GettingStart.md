## **Quick start**

### **Memory SPTAG Index Build**
 ```bash
 Usage:
 ./IndexBuiler [options]
 Options:
  -d, --dimension <value>       Dimension of vector, required.
  -v, --vectortype <value>      Input vector data type (e.g. Float, Int8, Int16), required.
  -f, --filetype <value>        Input file type (DEFAULT, TXT, XVEC). Default is DEFAULT.
  -i, --input <value>           Input raw data, required.
  -o, --outputfolder <value>    Output folder, required.
  -a, --algo <value>            Index Algorithm type (e.g. BKT, KDT), required.

  -t, --thread <value>          Thread Number.
  -dl, --delimiter <value>      Vector delimiter.
  -norm, --normalized <value>   Vector is normalized.
  -c, --config <value>          Config file for builder.
  -pq, --quantizer <value>      Quantizer File
  -m, --metaindex <value>       Enable delete vectors through metadata
  Index.<ArgName>=<ArgValue>    Set the algorithm parameter ArgName with value ArgValue.
  ```

  ### **Memory SPTAG Index Search**
  ```bash
  Usage:
  ./IndexSearcher [options]
  Options:
  -d, --dimension <value>       Dimension of vector.
  -v, --vectortype <value>      Input vector data type. Default is float.
  -i, --input <value>           Input query data.
  -f, --filetype <value>        Input file type (DEFAULT, TXT, XVEC). Default is DEFAULT.
  -x, --index <value>           Index folder.

  -t, --thread <value>          Thread Number.
  --delimiter <value>           Vector delimiter.
  -norm, --normalized <value>   Vector is normalized.
  -r, --truth <value>           Truth file.
  -o, --result <value>          Output result file.
  -m, --maxcheck <value>        MaxCheck for index.
  -a, --withmeta <value>        Output metadata instead of vector id.
  -k, --KNN <value>             K nearest neighbors for search.
  -tk, --truthKNN <value>       truth set number.
  -df, --data <value>           original data file.
  -dft, --dataFileType <value>  original data file type. (TXT, or DEFAULT)
  -b, --batchsize <value>       Batch query size.
  -g, --gentruth <value>        Generate truth file.
  -q, --debugquery <value>      Debug query number.
  -adc, --adc <value>           Enable ADC Distance computation
  Index.<ArgName>=<ArgValue>    Set the algorithm parameter ArgName with value ArgValue.
  ```

   ### **SPANN Index Build**
   Create a configure file buildconfig.ini as follows:
   ```
[Base]
ValueType=UInt8
DistCalcMethod=L2
IndexAlgoType=BKT
Dim=128
VectorPath=sift1b/base.1B.u8bin
VectorType=DEFAULT
QueryPath=sift1b/query.public.10K.u8bin
QueryType=DEFAULT
WarmupPath=sift1b/query.public.10K.u8bin
WarmupType=DEFAULT
TruthPath=sift1b/public_query_gt100.bin
TruthType=DEFAULT
IndexDirectory=sift1b
QuantizerFilePath=

[SelectHead]
isExecute=true
TreeNumber=1
BKTKmeansK=32
BKTLeafSize=8
SamplesNumber=1000
SelectThreshold=10
SplitFactor=6
SplitThreshold=25
Ratio=0.12
NumberOfThreads=45

[BuildHead]
isExecute=true
NeighborhoodSize=32
TPTNumber=32
TPTLeafSize=2000
MaxCheck=16324
MaxCheckForRefineGraph=16324
RefineIterations=3
NumberOfThreads=45

[BuildSSDIndex]
isExecute=true
BuildSsdIndex=true
InternalResultNum=64
ReplicaCount=8
PostingPageLimit=3
NumberOfThreads=45
MaxCheck=16324
TmpDir=/tmp/
SearchInternalResultNum=32
SearchPostingPageLimit=3
SearchResult=result.txt
ResultNum=10
MaxDistRatio=8.0
   ```
Then run ".\IndexBuilder.exe -c buildconfig.ini -d 128 -v UInt8 -f DEFAULT -i FromFile -o sift1b -a SPANN" to build the index.

Another build and search combined executable is SSDServing.exe which is used in the paper experiments.

For sift1b dataset, use the default configuration below (buildconfig.ini) and run .\SSDServing.exe buildconfig.ini:
```
[Base]
ValueType=UInt8
DistCalcMethod=L2
IndexAlgoType=BKT
Dim=128
VectorPath=sift1b/base.1B.u8bin
VectorType=DEFAULT
QueryPath==sift1b/query.public.10K.u8bin
QueryType=DEFAULT
WarmupPath==sift1b/query.public.10K.u8bin
WarmupType=DEFAULT
TruthPath==sift1b/public_query_gt100.bin
TruthType=DEFAULT
IndexDirectory=sift1b

[SelectHead]
isExecute=true
TreeNumber=1
BKTKmeansK=32
BKTLeafSize=8
SamplesNumber=1000
SaveBKT=false
SelectThreshold=10
SplitFactor=6
SplitThreshold=25
Ratio=0.12
NumberOfThreads=45
BKTLambdaFactor=1.0

[BuildHead]
isExecute=true
NeighborhoodSize=32
TPTNumber=32
TPTLeafSize=2000
MaxCheck=16324
MaxCheckForRefineGraph=16324
RefineIterations=3
NumberOfThreads=45
BKTLambdaFactor=-1.0

[BuildSSDIndex]
isExecute=true
BuildSsdIndex=true
InternalResultNum=64
ReplicaCount=8
PostingPageLimit=3
NumberOfThreads=45
MaxCheck=16324
TmpDir=/tmp/

[SearchSSDIndex]
isExecute=true
BuildSsdIndex=false
InternalResultNum=96
NumberOfThreads=1
HashTableExponent=4
ResultNum=10
MaxCheck=1024
MaxDistRatio=8.0
SearchPostingPageLimit=3

```

#### **SIFT1B with categorical and numeric attributes**

The current SIFT1B recipe uses two row-major `uint32` columns:
`[categorical tag, numeric]`. Column types are explicit schema, not navigation
partitions; there is no attribute hierarchy or PerTagBKT routing-key file.
If these inputs do not already exist, generate them in bounded-memory chunks with:

```bash
Tools/benchmarks/prep_sift1b_inputs.sh
```

The categorical column contains 200 Zipf-distributed regular values and one
extreme value. The generator reads the native INI and computes the largest
rare-label dataset count as
`ceil(L/(SelectHead.Ratio*Slots))-1`. The canonical
`.12` ratio, two slots, and `L=96` produce 399 vectors; there is no EST serving route.
The generator writes both the headerless SPTAG input
`sift1b_zipf200_sparse399_numeric_attrs.u32` and a shape-preserving NumPy copy,
plus exact counts, the policy inputs, and a hash-bound manifest.
The prep script and tracked INI use
`/mnt/nvme/baotonglu/mocheng/datasets/sift1b` by default; when the dataset is
elsewhere, set `SIFT1B_ROOT` for generation and update the native INI paths to
the same root. The native builder consumes the files as a tenant-0 bulk view,
so it does not synthesize per-vector metadata/routing objects or copy the
mapped two-column attribute table before STATIC construction. Limited-tag
STATIC placement also retains only emitted RNG edges for both `H` and `O`
instead of allocating fixed `N * ReplicaCount` arrays.

For the current five-level spatial hierarchy, use
`Tools/benchmarks/build_spann_attr_sift1b_zipf200_limited_tag_h5.ini`.
It retains the SIFT1B baseline above: UInt8/L2, H1 ratio `.12`, SelectHead
thresholds `10/25`, 45 build threads, build beam 64, MaxCheck 16324, replica
cap 8, and posting page limit 3. As in the original native reader,
`ValueType=UInt8` selects elements and `VectorType=DEFAULT` selects the
row/column-headered container. The reader derives the count and dimension from
the header and checks the entire file size. `Dim`, if specified, must match.
`VectorOffset` and `VectorCount` are removed (explicit settings fail);
`VectorSize` optionally selects a bounded prefix, with `-1`/omission meaning all.
The bulk-only `Tenant`, `WithMetaIndex`, and `ShareBuildOwnership` settings are
also removed: tenant 0/no metadata index are model invariants, and ownership is
derived safely from the native metric/reader normalization state. `Normalized`
remains meaningful for Cosine; L2 need not specify its default false value.
`[Tags] ColumnTypes` declares the type of every original column and determines
the native runtime width. Optional `[Tags] NumTagsPerVec` must agree with that
width; do not duplicate it in the input `[BuildSSDIndex]` section. `TagFile` is
headerless `uint32` data starting at byte zero, with validated row count and
stride; `TagOffset` is removed.

```ini
[SelectHead]
Ratio=0.12
HierarchyEnabled=true
HierarchyLevels=5
HierarchyReplicaCount=8
BuildH1Graph=false
CompactHierarchyVectors=false
MinHeadsPerTag=0
ParallelBKTBuild=true

[Tags]
TagFile=sift1b/sift1b_build/sift1b_zipf200_sparse399_numeric_attrs.u32
ColumnTypes=categorical,numeric

[BuildSSDIndex]
Storage=STATIC
EnableLimitedTagPosting=true
LimitedTagColumn=0
LimitedTagSlotsPerHead=2
EnableLimitedTagSupportExpansion=true
LimitedTagMinHeadCount=16
PostingPageLimit=3
PostingVectorLimit=118
TailReplicaCount=0
UnfilterTailBufferLength=0

[SearchSSDIndex]
SearchPostingPageLimit=3

```

`Ratio` is the only selection ratio: every level uses `.12` relative to its input.
Attribute partition settings (`ACLCols`, `HierLevelWidths`,
`PivotForceNodeCount`, `PerVectorTagsFile`, and `PerTagBKT`) have been removed
and are rejected. `NumericCols` and bulk `StaticACLTagCols` are also removed;
use `ColumnTypes` instead. Multiple categorical columns and interleaved numeric
columns are supported without reordering, for example
`ColumnTypes=numeric,categorical,numeric,categorical` with `LimitedTagColumn=3`.
The selected key must be categorical. Single-label means one value per vector
in that key column, not that every input must have only one categorical column.
Saved schema/version/fingerprint metadata binds the original column layout.
Hierarchy signatures are not consulted by query traversal; the historical
signature min/max selectivity pair is removed. CPU BKT/TPT RNG follows upstream `5619bb1`:
BKT centers and TPT projections use the global C RNG; TPT workers shuffle with
default `std::mt19937` engines and call `Sleep(i * 100)` then `std::srand(clock())`
per tree. There is no custom fixed seed or external seed parameter, and even
single-threaded rebuilds need not match. Historical fixed-seed artifacts remain
unchanged. `Hierarchy*` describes spatial layers; categorical and numeric
columns remain exact-filter record fields, not partitions.
`HierarchyLevels` counts H1, so `5` selects H1 through H5. Do not add a separate
upper-level ratio. `HierarchyInitialProbeRatio` is a search beam fraction, not
a head-selection ratio.
The production SIFT1B recipe enables `ParallelBKTBuild=true` so sibling BKT
nodes are processed concurrently instead of serializing the long recursive
H1 selection. This increases temporary memory because each concurrent node
owns k-means workspace; confirm host headroom before launch.
The expected layer sizes are approximately 120M / 14.4M / 1.728M / 207.36K / 24.88K;
BKT selection determines the actual counts. Only H5 retains a navigation
graph. H4/H3/H2/H1 use CSR descent and independent vector catalogs; intermediate
graphs are temporary construction aids. This SIFT1B recipe selects categorical
column 0 as its key, with column 1 used for exact numeric filtering.
Every filtered and unfiltered request runs the same distance-only highest-layer
search and one fixed-beam CSR descent. `HierarchyInitialProbeRatio` fixes the
beam fraction and `HierarchyMaxCheck` fixes the shared graph budget. Every
reached H1 child is distance-scored before exact membership admission.
Predicates do not prune graph/CSR traversal, choose another graph, widen the
beam, retry search, or enumerate tag supports. Consequently a sparse predicate
may return fewer than top-k. `HeadNavigationMode`,
`HierarchyGraphSignaturePruning`, `SparseFallbackMaxHeads`,
`SparseFallbackMaxPostingPages`, and hierarchy route-selectivity settings are
removed and rejected. Both software prefetch modes remain available through
`HierarchyPrefetchMode`: `Rolling16` (default) and `Batch64`.
O-derived support expansion adds support relationships rather than real heads.
H and O each independently apply the same native construction parameters
(`PostingPageLimit`, raised by `PostingVectorLimit`), not a combined H+O limit:
with 140-byte records, 3 pages plus 118 vectors means
an effective 5 pages / 146 nearest records in each region. The independent
runtime `SearchPostingPageLimit=3` reads at most 3 physical pages from the
selected H or O region, scanning only complete records. A construction cut
that removes every H copy of a non-head vector triggers a lightweight rescue
of its nearest original H assignment. Exactly one full record is appended to
that head's H tail before O, without new search or support expansion. H may
exceed the normal cut; O is unchanged, and the search page limit still applies
even when it excludes rescued tail records.
The separate `LimitedTagMaxExpandedPostingPages` option has been removed.
The existing 399-vector extreme tag remains part of the input, but there is no
dedicated EST query route. The earlier INI without the `_h5` suffix is retained
as a legacy profile, not the five-level launch configuration.

The complete INI names `sift1b_spann_zipf200_limited_tag_h5` and
`sift1b_spann_zipf200_limited_tag_h5_tmp`; these may already contain a stopped
experiment. **Do not launch a fresh build against existing output paths:**
the launcher clears `IndexDirectory` when `ResumeBuild=0`.
For a restart after model changes, copy the current repository INI into a new
run directory and set only `Base.IndexDirectory` and `BuildSSDIndex.TmpDir`
to new, absent paths. Preserve previous run INIs, executables, logs and partial
indexes. Freeze the new INI, builder, benchmark, auditor, launcher, validator
and source commit/hashes before starting. The old stopped run's INI is
historical evidence, not the current configuration.

The recipe enables a SelectHead checkpoint, not arbitrary O/H-stage resume;
do not resume an incompatible old checkpoint. On the local 96-core host,
45 build threads can use socket-1 CPUs 48-95 with allocations interleaved over
NUMA nodes 2 and 3. Check current load, memory and disk first, and adapt CPU
placement on other hosts. Given the prepared run-local INI:

```bash
CFG=/absolute/path/to/new-run/build.ini
python3 Tools/benchmarks/validate_spann_hierarchy_config.py "$CFG"
numactl --physcpubind=48-95 --interleave=2,3 \
  bash Tools/benchmarks/run_spann_attr_build.sh "$CFG"
```

Data, model and search settings come only from the INI; CPU/NUMA placement and
process-loader settings are external execution controls. For an unattended run,
use a detached supervisor that retains PID, start/end times, exit status and
resource/log files. The launcher first builds with signatures deferred, then
runs `spannbuilder -c <run.ini> --build-signatures-only` in a fresh process and
validates the saved runtime configuration. It refuses an existing fresh-build
`IndexDirectory`, reports the actual primary-build INI path, and preserves
that INI on success or failure. Keep it with the original run configuration.
Use the matching
`spannsupportaudit <IndexDirectory>/tenant_0 <report-prefix>` after completion
before interpreting filtered-query results. No build completion or performance
claim follows merely from starting the supervisor.

The raw STATIC profile stores the original 128-byte UInt8 vector, a 4-byte
vector ID, and two inline `uint32` attributes: 140 bytes per posting record,
not the Float128 profile's 524 bytes. Query and warm-up input are the local
10K-query `query.u8bin`; unfiltered truth is the existing
`raw/gnd/idx_1000M.ivecs` with `TruthType=XVEC`. No input regeneration,
Float conversion, posting quantization, or dataset download is required.
The native coverage auditor uses the persisted vector type to compute record
width for both UInt8 and Float indexes. The original
SPANN placement is built first. Only its actual post-RNG, post-cut retained O
records supply base tag candidates: protect the own tag, then retain nearest
distinct external tags by minimum member distance up to
`LimitedTagSlotsPerHead - 1`. Underfilled rows are allowed; the existing
O-derived rare-tag expansion still applies its source-capped floor. There is no
global extra-support budget: state grows only for actual retained-O heads up
to per-tag deficits. `LimitedTagMaxExtraSupports` is removed and rejected.
New expansion uses V5 storage; V4 caps/hashes remain immutable provenance.
There is no
candidate vote-count setting, no pre-RNG vote buffer, and no additional O
navigation search. Explicit `LimitedTagVoteHeadCount` settings are rejected.
For immutable legacy read/export compatibility, see the support provenance
policy in `Tools/benchmarks/README.md`; old support is not silently relabeled.
`H` and `O` retain separate
`(head distance, VID)` order. The persisted boundary lets predicates safely anchored on the limited-tag key
scan `H`, while unfiltered and other predicates read the self-contained
original `O` suffix directly. This is posting-region membership, not graph
routing: both paths use the same spatial traversal and fixed budgets.
Limited-tag mode sets `TailReplicaCount=0` because
it does not append supplemental unfilter-tail replicas.

Filtered BKT/KDT searches maintain an independent unfiltered distance heap for
native stopping. Exact result admission cannot extend that stopping condition;
filtered and unfiltered calls obey the same initial-pivot and neighbor
`MaxCheck` caps. An underfilled result set does not continue a tree frontier,
widen the hierarchy beam, restart traversal, or trigger direct tag-to-head
completion. Sparse or budget-limited queries may return fewer than top-k,
including zero. These are valid ANN results, not failures to repair; keep them
in recall accounting.

`SearchPostingPageLimit` actively caps physical reads in the selected region.
In constrained `H | O` snapshots, eligible anchored predicates read H, while
unfiltered or other predicates start at the persisted H/O boundary and read O.
Neither region bypasses the positive page limit to read the complete region;
only complete records in the readable prefix are scanned.
Zero disables this read cap and must not be substituted in a capped benchmark.
`UseDirectIO=false` selects buffered I/O. To match
the SIFT1B paper protocol, warm the complete query set and then measure the same
query set so its posting working set can reside in the Linux page cache.

Ordinary legacy pure-plus-tail layouts use the same contiguous posting prefix
for filtered and unfiltered requests, including any tail records within
`SearchPostingPageLimit`. Exact categorical/numerical checks apply to every
admitted record. `TailReplicaCount` and `UnfilterTailBufferLength` retain their
construction meaning; no posting bytes or tail boundaries need rewriting.

`EnableUnfilterTail`, `UnfilterPurePages`, `UnfilterExtraTailPages`,
`UnfilterPureDistanceScanPercent`, `AblateUExtra` and `AblateTail` are removed
and rejected, including explicit false/zero/defaults. Do not replace them with
a different predicate-dependent budget. Remove retired entries only in a
separate writable configuration clone, preserving historical artifacts.
Existing ordered-page directories remain layout metadata, not permission to
jump to predicate-selected pages. Native OPQ/PipePQ load their configured
codec; `SPTAG_OPQ_PREFILTER`, `SPTAG_PAGE_SELECT` and
`SPTAG_RBQ_EXHAUSTIVE` are rejected rather than enabling alternate paths.

#### **Current SIFT1M limited-tag comparison**

Use `Tools/benchmarks/build_spann_attr_sift1m_zipf200_limited_tag.ini` for the
current three-level Float128 experiment, rather than the generic SSDServing
example below. It uses native DEFAULT input, `ColumnTypes=categorical,numeric`,
key column 0, shared `Ratio=0.16`, 24 build threads, retained-O support
expansion with a source-capped floor of 16, and no global extra-support budget.
Each H/O region has a normal build limit of 16 pages (125 full 524-byte
records); zero-H rescue can append beyond the H cut. Search remains capped at
12 pages. The lightweight rescue is not dynamic SPFresh insertion: it performs
no new search, split, support expansion or replica-count refill.

The 2026-09-09 full 1M run completed primary construction and signatures in
180.38 seconds. Two zero-H vectors received two original-candidate records at
one H tail, adding one payload page. The final index contained 160,091 / 25,607 /
4,098 heads, 4,602,621 H records and 5,994,938 O records; all 839,909 non-heads
were represented in each region. These are observations of this build, not a
guarantee of query recall or deterministic cross-build equality.

The matched comparison uses five workloads, nprobe 32/64/128/256, three serial
old/current paired repetitions, one query thread, 100 warm-ups and 900 measured
queries at offset 100. Both sides use native `MaxCheck=2048`,
`HierarchyMaxCheck=512` (the old executable uses its legacy key),
`HierarchyInitialProbeRatio=0.666666` and `SearchPostingPageLimit=12`.
The old frozen executable loads its untouched historical index; current code
loads the fresh index. This is an end-to-end comparison, not an isolated
measurement of cap removal or the two rescue records.

| Workload | Median QPS change over the four probes | Recall observation |
| --- | --- | --- |
| Unfiltered | +6.13% to +6.45% | -0.37 to -0.02 percentage points |
| Broad tag | +0.05% to +3.77% | +0.01 to +0.30 percentage points |
| Medium tag | -1.47% to +1.35% | -0.38 to +0.02 percentage points |
| Mixed DNF | -1.43% to +0.17% | +0.62 to +2.00 percentage points |
| Sparse tag | Budget-dependent trade-off | At nprobe32: QPS -24.86%, recall 79.57% to 93.42%; at nprobe256: QPS -8.89%, recall 100% to 99.97% |

Historical direct-H1 completion is absent in current traversal; the older
upper-layer ratio also differs. Do not attribute every difference to rescue
or claim uniformly unchanged performance. The local run and raw measurements
are preserved under
`datasets/sift1m_zipf200_sparse193_numeric/build_runs/20260909T141212Z_lightweight_rescue`.
The updated local `h1_h2_curve_h2_15pct_r8/sift1m_h1_h2_recall_qps.pdf` keeps
historical H1/H2/H3 curves and adds both matched old/current H3 curves, with
three-run QPS ranges and explicit budget labels. Reproduce that plot using R:

```bash
Rscript Tools/benchmarks/plot_sift1m_h1_h2_curve.R \
  <results_h1_h2_h3_placement_fixed.jsonl> <output-prefix> \
  <20260909T141212Z_results.summary.csv>
```

The JSONL/CSV, indexes and datasets are local experiment artifacts, not required
repository downloads. Back up an existing figure before replacing it.

For sift1m dataset, use the default configuration below (buildconfig.ini) and run .\SSDServing.exe buildconfig.ini:
```
[Base]
ValueType=Float
DistCalcMethod=L2
IndexAlgoType=BKT
Dim=128
VectorPath=sift1m/sift_base.fvecs
VectorType=XVEC
QueryPath=sift1m/sift_query.fvecs
QueryType=XVEC
WarmupPath=sift1m/sift_query.fvecs
WarmupType=XVEC
TruthPath=sift1m/sift_groundtruth.ivecs
TruthType=XVEC
IndexDirectory=sift1m

[SelectHead]
isExecute=true
TreeNumber=1
BKTKmeansK=32
BKTLeafSize=8
SamplesNumber=1000
SaveBKT=false
SelectThreshold=50
SplitFactor=6
SplitThreshold=100
Ratio=0.16
NumberOfThreads=64
BKTLambdaFactor=-1

[BuildHead]
isExecute=true
NeighborhoodSize=32
TPTNumber=32
TPTLeafSize=2000
MaxCheck=8192
MaxCheckForRefineGraph=8192
RefineIterations=3
NumberOfThreads=64
BKTLambdaFactor=-1

[BuildSSDIndex]
isExecute=true
BuildSsdIndex=true
InternalResultNum=64
ReplicaCount=8
PostingPageLimit=12
NumberOfThreads=64
MaxCheck=8192
TmpDir=/tmp/

[SearchSSDIndex]
isExecute=true
BuildSsdIndex=false
InternalResultNum=32
NumberOfThreads=1
HashTableExponent=4
ResultNum=10
MaxCheck=2048
MaxDistRatio=8.0
SearchPostingPageLimit=12
```

### **Quantizer Training and Quantizing Vectors**
> Use Quantizer.exe to train PQQuantizer and output quantizer & quantized vectors:

  ```bash
  Usage:
  ./Quantizer [options]
  Options:
  -d, --dimension <value>                 Dimension of vector.
  -v, --vectortype <value>                Input vector data type. Default is float.
  -f, --filetype <value>                  Input file type (DEFAULT, TXT, XVEC). Default is DEFAULT.
  -i, --input <value>                     Input raw data.
  -o, --output <value>                    Output quantized vectors.
  -om, --outputmeta <value>               Output metadata.
  -omi, --outputmetaindex <value>         Output metadata index.

  -t, --thread <value>                    Thread Number.
  -dl, --delimiter <value>                Vector delimiter.
  -norm, --normalized <value>             Vector is normalized.
  -oq, --outputquantizer <value>          Output quantizer.
  -qt, --quantizer <value>                Quantizer type.
  -qd, --quantizeddim <value>             Quantized Dimension.
  -ts, --train_samples <value>            Number of samples for training.
  -debug, --debug <value>                 Print debug information.
  -kml, --lambda <value>                  Kmeans lambda parameter.
  ```

### **Input File Format**

#### DEFAULT (Binary)
> Input raw data for index build and input query file for index search (suppose vector dimension is 3):

```
<4 bytes int representing num_vectors><4 bytes int representing num_dimension>
<num_vectors * num_dimension * sizeof(data type) bytes raw data>
```

> Truth file to calculate recall (suppose K is 2):
```
< 4 bytes int representing num_queries><4 bytes int representing K>
<num_queries * K * sizeof(int) representing truth neighbor ids>
```

#### TXT
> Input raw data for index build and input query file for index search (suppose vector dimension is 3):

```
<metadata1>\t<v11>|<v12>|<v13>|
<metadata2>\t<v21>|<v22>|<v23>|
... 
```
where each line represents a vector with its metadata and its value separated by a tab space. Each dimension of a vector is separated by | or use --delimiter to define the separator.

> Truth file to calculate recall (suppose K is 2):
```
<t11> <t12>
<t21> <t22>
...
```
where each line represents the K nearest neighbors of a query separated by a blank space. Each neighbor is given by its vector id.

### **Meta Files Format**
> Data for index build to provide the metadata of the vectors. There are two files:

#### meta.bin
```
<vector 1 meta><vector 2 meta>...
```

#### metaindex.bin
```
<4 bytes int representing num_vectors><sizeof(uint64_t)*(num_vectors + 1) bytes representing position_array where the meta start and end positions in meta.bin for vector i is position_array[i] and position_array[i+1] respectively> 
```

### **Quantizer File Format**
> Data for using PQ quantizer in index build and index search
```
<1 byte uint8 representing QuantizerType -- 0: NONE, 1: PQ, 2: OPQ><1 byte uint8 representing ReconstructDataType -- 0: int8, 1: uint8, 2: int16, 3: float><4 bytes int representing num_codebooks><4 bytes int representing entries_per_codebook><4 bytes int representing codebook_dim>
<sizeof(ReconstructType)*num_codebooks*entries_per_codebook*codebook_dim representing codebook entries>[<sizeof(float) * reconstruct_dim * reconstruct_dim representing OPQ rotation matrix, row major order>]
```

Note that `num_codebooks*codebook_dim=full_dim`. The current PQ implementation only supports `entries_per_codebook <= 256` (i.e. quantizing to `byte`).

### **Server**
```bash
Usage:
./Server [options]
Options: 
  -m, --mode <value>              Service mode, interactive or socket.
  -c, --config <value>            Configure file of the index

Write a server configuration file service.ini as follows:

[Service]
ListenAddr=0.0.0.0
ListenPort=8000
ThreadNumber=8
SocketThreadNumber=8

[QueryConfig]
DefaultMaxResultNumber=6
DefaultSeparator=|

[Index]
List=BKT

[Index_BKT]
IndexFolder=BKT_gist
```

### **Client**
```bash
Usage:
./Client [options]
Options:
-s, --server                       Server address
-p, --port                         Server port
-t,                                Search timeout
-cth,                              Client Thread Number
-sth                               Socket Thread Number
```

### **Aggregator**
```bash
Usage:
./Aggregator

Write Aggregator.ini as follows:

[Service]
ListenAddr=0.0.0.0
ListenPort=8100
ThreadNumber=8
SocketThreadNumber=8

[Servers]
Number=2

[Server_0]
Address=127.0.0.1
Port=8000

[Server_1]
Address=127.0.0.1
Port=8010
```

### **Python Support**
> Singlebox PythonWrapper
 ```python
 
import SPTAG
import numpy as np

n = 100
k = 3
r = 3

def testBuild(algo, distmethod, x, out):
    i = SPTAG.AnnIndex(algo, 'Float', x.shape[1])
    i.SetBuildParam("NumberOfThreads", '4', "Index")
    i.SetBuildParam("DistCalcMethod", distmethod, "Index")
    if i.Build(x, x.shape[0], False):
        i.Save(out)

def testBuildWithMetaData(algo, distmethod, x, s, out):
    i = SPTAG.AnnIndex(algo, 'Float', x.shape[1])
    i.SetBuildParam("NumberOfThreads", '4', "Index")
    i.SetBuildParam("DistCalcMethod", distmethod, "Index")
    if i.BuildWithMetaData(x, s, x.shape[0], False, False):
        i.Save(out)

def testSearch(index, q, k):
    j = SPTAG.AnnIndex.Load(index)
    for t in range(q.shape[0]):
        result = j.Search(q[t], k)
        print (result[0]) # ids
        print (result[1]) # distances

def testSearchWithMetaData(index, q, k):
    j = SPTAG.AnnIndex.Load(index)
    j.SetSearchParam("MaxCheck", '1024', "Index")
    for t in range(q.shape[0]):
        result = j.SearchWithMetaData(q[t], k)
        print (result[0]) # ids
        print (result[1]) # distances
        print (result[2]) # metadata

def testAdd(index, x, out, algo, distmethod):
    if index != None:
        i = SPTAG.AnnIndex.Load(index)
    else:
        i = SPTAG.AnnIndex(algo, 'Float', x.shape[1])
    i.SetBuildParam("NumberOfThreads", '4', "Index")
    i.SetBuildParam("DistCalcMethod", distmethod, "Index")
    if i.Add(x, x.shape[0], False):
        i.Save(out)

def testAddWithMetaData(index, x, s, out, algo, distmethod):
    if index != None:
        i = SPTAG.AnnIndex.Load(index)
    else:
        i = SPTAG.AnnIndex(algo, 'Float', x.shape[1])
    i.SetBuildParam("NumberOfThreads", '4', "Index")
    i.SetBuildParam("DistCalcMethod", distmethod, "Index")
    if i.AddWithMetaData(x, s, x.shape[0], False, False):
        i.Save(out)

def testDelete(index, x, out):
    i = SPTAG.AnnIndex.Load(index)
    ret = i.Delete(x, x.shape[0])
    print (ret)
    i.Save(out)
    
def Test(algo, distmethod):
    x = np.ones((n, 10), dtype=np.float32) * np.reshape(np.arange(n, dtype=np.float32), (n, 1))
    q = np.ones((r, 10), dtype=np.float32) * np.reshape(np.arange(r, dtype=np.float32), (r, 1)) * 2
    m = ''
    for i in range(n):
        m += str(i) + '\n'

    m = m.encode()

    print ("Build.............................")
    testBuild(algo, distmethod, x, 'testindices')
    testSearch('testindices', q, k)
    print ("Add.............................")
    testAdd('testindices', x, 'testindices', algo, distmethod)
    testSearch('testindices', q, k)
    print ("Delete.............................")
    testDelete('testindices', q, 'testindices')
    testSearch('testindices', q, k)

    print ("AddWithMetaData.............................")
    testAddWithMetaData(None, x, m, 'testindices', algo, distmethod)
    testSearchWithMetaData('testindices', q, k)
    print ("Delete.............................")
    testDelete('testindices', q, 'testindices')
    testSearchWithMetaData('testindices', q, k)

if __name__ == '__main__':
    Test('BKT', 'L2')
    Test('KDT', 'L2')

 ```

 > Python Client Wrapper, Suppose there is a sever run at 127.0.0.1:8000 serving ten-dimensional vector datasets:
 ```python
import SPTAGClient
import numpy as np
import time

def testSPTAGClient():
    index = SPTAGClient.AnnClient('127.0.0.1', '8000')
    while not index.IsConnected():
        time.sleep(1)
    index.SetTimeoutMilliseconds(18000)

    q = np.ones((10, 10), dtype=np.float32)
    for t in range(q.shape[0]):
        result = index.Search(q[t], 6, 'Float', False)
        print (result[0])
        print (result[1])

if __name__ == '__main__':
    testSPTAGClient()

 ```
 
 ### **C# Support**
> Singlebox CsharpWrapper
 ```C#
using System;
using System.Text;

public class test
{
    static int dimension = 10;
    static int n = 10;
    static int k = 3;

    static byte[] createFloatArray(int n)
    {
        byte[] data = new byte[n * dimension * sizeof(float)];
        for (int i = 0; i < n; i++)
            for (int j = 0; j < dimension; j++)
                Array.Copy(BitConverter.GetBytes((float)i), 0, data, (i * dimension + j) * sizeof(float), 4);
        return data;
    }

    static byte[] createMetadata(int n)
    {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < n; i++)
            sb.Append(i.ToString() + '\n');
        return Encoding.ASCII.GetBytes(sb.ToString());
    }

    static void Main()
    {
        {
            AnnIndex idx = new AnnIndex("BKT", "Float", dimension);
            idx.SetBuildParam("DistCalcMethod", "L2", "Index");
            byte[] data = createFloatArray(n);
            byte[] meta = createMetadata(n);
            idx.BuildWithMetaData(data, meta, n, false, false);
            idx.Save("testcsharp");
        }

        AnnIndex index = AnnIndex.Load("testcsharp");
        BasicResult[] res = index.SearchWithMetaData(createFloatArray(1), k);
        for (int i = 0; i < res.Length; i++)
            Console.WriteLine("result " + i.ToString() + ":" + res[i].Dist.ToString() + "@(" + res[i].VID.ToString() + "," + Encoding.ASCII.GetString(res[i].Meta) + ")"); 
        Console.WriteLine("test finish!");
    }
}

 ```

  
  
