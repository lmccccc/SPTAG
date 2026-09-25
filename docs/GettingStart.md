## **Quick start**

### **Current adaptive SPANN: portable configuration**

For the current attribute-filtered implementation, start with the complete
[native INI template](AdaptiveSpann.ini), not a historical SIFT experiment.
It builds **one BKT H1 navigation graph** plus upper posting catalogs and enables
the final adaptive, controlled-ascent supplementation. H2 and above are not
separate query ANN graphs. The same index serves unfiltered, categorical,
numeric and mixed-DNF queries; do not select another graph or multiply nprobe
according to predicate selectivity.

The template is a **starting configuration, not a universal performance optimum**:
Float128/L2, two attribute columns, three hierarchy levels, eight build threads
and one query thread are editable examples. Its `2048 + 2048` query budgets come
from the current SIFT1B adaptive measurements, whose actual index used UInt8
and five levels. Changing a dataset still requires recall/latency evaluation.
All data/model/search settings belong in the native INI, not environment
overrides or hard-coded client defaults.

#### **Adapt the input and construction settings**

Copy `docs/AdaptiveSpann.ini` to a run-local INI and replace every
`/absolute/path/to/...` value. Use new, absent index/work paths for a fresh build.
Absolute paths avoid working-directory ambiguity: paths are not automatically
relative to the INI's directory. Native INI comments start with `;` on their own
line; do not use `#`, inline comments, shell variables or `~` in values.

| Section / setting | What another dataset or application must supply |
| --- | --- |
| `[Base] VectorPath`, `ValueType`, `VectorType`, `Dim` | The actual base file, element type, container and dimension. Native bulk IO supports `Float`, `Int8`, `UInt8`, `Int16`; `DEFAULT`, `TXT`, `XVEC` name containers, not element types. |
| `[Base] DistCalcMethod` | The index metric (`L2` or `Cosine`), also used for queries and truth. For Cosine, `Normalized` describes the actual input; native normalization must not modify a mapped source file. |
| `[Base] QueryPath`, `QueryType`, `QuerySize`, `TruthPath`, `TruthType` | Inputs for the querying/evaluation program. Queries must match the built vector type and dimension; `QuerySize` optionally bounds the native query prefix (1000 in the template). The bulk builder does not run a query campaign merely because these paths are present. |
| `[Base] IndexDirectory`, `[BuildSSDIndex] TmpDir` | A new output root and a run-specific work directory. The bulk build uses tenant `0`; the root contains `manifest.txt` and `tenant_0/`. |
| `[Tags] TagFile`, `ColumnTypes` | Row-aligned, headerless attributes and the type of **every original column**. Example: `numeric,categorical,numeric,categorical` preserves that order. |
| `[BuildSSDIndex] LimitedTagColumn` | The zero-based **original** categorical key column; use `3` for the preceding example if the fourth column is the key. It is not the ordinal among categorical columns. |
| `[SelectHead] Ratio`, `HierarchyLevels`, `HierarchyReplicaCount` | Spatial construction choices to size for the dataset and memory budget. `0 < Ratio < 1` applies at every level; `HierarchyLevels >= 2` includes H1. Three levels are an example, not a requirement. |
| `[SelectHead]`, `[BuildHead]`, `[BuildSSDIndex] NumberOfThreads` | Construction parallelism for the target host. Query threads are a separate `[SearchSSDIndex]` setting. |

For `VectorType=DEFAULT`, vectors are an `int32 N, int32 D` header followed by
exactly `N * D * sizeof(ValueType)` row-major bytes. The file does **not** encode
the element type. `Dim` can be omitted for this container; if present it must
match the header. TXT/XVEC require an explicit dimension. `VectorSize` optionally
limits a prefix; omit it or use `-1` for all rows. Headerless vectors, NumPy files
and renamed `.fvecs` files are not DEFAULT input; use the correct native reader
or explicitly convert to a new file. `VectorOffset` and `VectorCount` are removed.

Attributes start at byte zero and contain `N * C` native `uint32` values, with
`C` derived from `ColumnTypes`. Numeric columns hold unsigned, order-preserving
values, **not float/int32 bit patterns**. With a vector prefix, the attribute file
may describe either that exact prefix or the full validated vector source.
Do not add a header, offsets or padding, reorder columns, or derive row IDs from
tag values. Optional `[Tags] NumTagsPerVec` must equal `C`; do not repeat it in
the build SSD section. Schema/version/fingerprint metadata is persisted.
This limited-tag template requires a categorical key, but queries need not
constrain it: pure numeric and unanchored OR predicates use the original O
region. An untagged index can instead use the classic SPANN examples below;
do not manufacture categorical labels just to load a different dataset.

Construction and query controls are **not interchangeable**. In particular,
`[BuildHead] MaxCheck`, `[BuildSSDIndex] MaxCheck` and build `InternalResultNum`
control graph construction/assignment, not a query's budget or nprobe.
`[Base] IndexAlgoType=BKT` selects the head algorithm; the overall index and
`TenantIndexManager` algorithm are still `SPANN`.
`Ratio`, hierarchy depth, metric, schema and posting layout require a new build;
they are not search overlays. For raw STATIC postings the record width is
`sizeof(int32) + D * sizeof(ValueType) + C * sizeof(uint32)`. The native build
can raise `PostingPageLimit` to accommodate `PostingVectorLimit` (118 in the
template), independently for H and O. Thus a page/record budget copied from
UInt8 SIFT has a different byte cost for a higher-dimensional Float dataset.

#### **Generate attributes and predicate groundtruth from scratch**

The template also includes optional `[AttributeGeneration]`, `[GroundTruth]`
and `[Predicate.<name>]` preparation sections. They are not SPANN search
parameters. The following repository-owned tools need only the original base
and query vectors plus this INI; they do **not** require a built index, an old
`workloads.json`, precomputed truth, a local scenario file or a temporary script.
Install the dependencies in your Python environment, then run from the repository
root with the same edited INI used for building:

```bash
python3 -m pip install -r Tools/benchmarks/requirements-predicate-inputs.txt
python3 Tools/benchmarks/generate_spann_attributes.py --config /absolute/path/to/my-dataset.ini
python3 Tools/benchmarks/generate_spann_predicate_groundtruth.py --config /absolute/path/to/my-dataset.ini
```

**Skip the first generator when you already have real attributes.** It is an
optional deterministic synthetic recipe for exactly
`ColumnTypes=categorical,numeric`, not a replacement for a dataset's real labels.
It derives the vector count from the native input and writes raw `uint32`
attributes exactly to `[Tags] TagFile`, plus sibling `.npy`, `.counts.tsv` and
`.manifest.json` files. The basename need not encode SIFT or a label count.
The INI declares the regular-label cardinality, Zipf exponent, seeds and chunk
size. The rare-label count retains the native coverage recipe based on
`SelectHead.Ratio`, `LimitedTagSlotsPerHead` and search `InternalResultNum`;
the standard `.12`, two slots and 96 heads produce 399 rare-label rows.
The rare label is the regular-label cardinality (200 in the example).
Very small smoke datasets need smaller cardinality and compatible coverage
settings; invalid recipes fail rather than silently changing them.

The **groundtruth generator reads the raw TagFile**, supports all declared
categorical/numeric columns in their original order, and computes every requested
scenario from source vectors. Its current input contract is native `DEFAULT`
base/query files with `Float`, `Int8`, `UInt8` or `Int16` elements and **L2**.
Dimensions are not fixed to 128. Cosine and TXT/XVEC input are rejected by this
offline tool rather than reinterpreted; this does not restrict the native
index library's separate input/metric capabilities.

| Preparation setting | Meaning |
| --- | --- |
| `[SearchSSDIndex] ResultNum` | The single top-k source for both truth and search; there is no separate GT top-k override. |
| `[GroundTruth] QueryCount` | First N query rows, or `all`; cannot exceed the native query prefix. The template uses `all` of `[Base] QuerySize`, avoiding two independently specified cohort sizes. |
| `[GroundTruth] ChunkRows`, `QueryBatch` | Bound base-vector chunks and distance tiles; no full 1B Float corpus or corpus-by-query matrix is allocated. Query/result storage still scales with the query cohort, scenario count and top-k. |
| `[GroundTruth] Threads` | Explicit FAISS and BLAS thread limit for offline truth generation. |
| `[GroundTruth] Scenarios` | Ordered scenario names, each with one `[Predicate.<name>]` definition. |
| `[Predicate.<name>] Expression` | JSON predicate shared by that scenario's query cohort; `null` is unfiltered, `categorical_eq` is equality, and `numeric_eq/lt/le/gt/ge` compare unsigned numeric values. Nonempty `and`/`or` lists compose DNF. |
| `[GroundTruth] OutputDirectory` | A new, absent output directory. Existing directories, files, symlinks and partial runs are never overwritten. |

Each literal is `[original_column, uint32_value]` and must match `ColumnTypes`.
Categorical range operations, malformed expressions and incompatible column
kinds fail before generation. The preparation encoder bounds expanded DNF
to 64 clauses and 64 literals per clause. These are generator resource limits,
not new native ANN parameters.

The template covers unfiltered, broad/medium/rare categorical, tag169, numeric,
and mixed-DNF cases. Labels and thresholds are explicit examples, not adaptive
selection rules. `sel_01pct` is only a name: the manifest reports its **actual**
eligible count and selectivity on your data. For the final five-scenario
comparison, request exactly
`unfilter,broad_tag,medium_tag,sel_01pct,mixed_dnf`; fixed campaign clients still
enforce their own cohort/dimension/scenario contracts.

Truth uses exhaustive FAISS float32 squared-L2 distance tiles, reused across
the predicates, with ties ordered by original vector ID. This is not ANN
search or sampled truth. UInt8 SIFT128 squared distances are integer-exact;
Float data retains normal float32 distance arithmetic. The generator checks
finite inputs/results and records the FAISS version and settings. Chunking
bounds working memory, **not total work**: an unfiltered 1B truth scan can still
be expensive.

The new output contains `workloads.json`, `completion.json`, the source INI,
typed `query_vectors.native.npy`, Float32 `query_vectors.npy` for compatibility,
and a streamed `attributes.npy` copy for workload consumers. That copy needs
an additional `N * C * 4` bytes of disk space, not a full in-memory attribute
table. Each scenario gets `groundtruth_<name>_local_ids.npy`,
`groundtruth_<name>_dists.npy` and native `groundtruth_<name>.ibin`
(`int32 query_count, int32 topk`, then row-major int32 IDs). Native predicate
files are recorded under `native_predicates` in the manifest; column-sensitive
filters use the same length-prefixed DNF3 format as the native benchmark.
Fewer than top-k matches are padded with `-1` IDs and positive-infinity distances,
including zero-match cases.

Input identities, selected-attribute hashes, output hashes, predicates and
candidate counts are recorded. A reported error retains `failure.json` and partial
files; an interruption may leave only `started.json` and partial output.
Only a complete `completion.json` with a matching workload hash marks success.
Source vectors and
attributes are never modified. The template's `TruthPath` points to the newly
generated **unfiltered** native truth; filtered evaluations must select their
matching per-scenario truth, not that unfiltered file.

The obsolete four-level SIFT1B ACL generator has been removed.
`native_postfilter/prepare_selectivity.py` remains only for authenticated reuse
of older campaigns; it is **not required** for this fresh-input workflow.

#### **Build once, with the native INI**

After the normal [repository build setup](../README.md), build the native
attribute builder and launch from the repository root:

```bash
cmake --build build --target spannbuilder --parallel 8
python3 Tools/benchmarks/validate_spann_hierarchy_config.py /absolute/path/to/my-dataset.ini
bash Tools/benchmarks/run_spann_attr_build.sh /absolute/path/to/my-dataset.ini
```

The preflight reads configuration only; it does not open the dataset or start
a build. The launcher invokes `Release/spannbuilder -c <ini>`, stages construction
sections before building, and applies `[SearchSSDIndex]` only afterward.
With `BuildSignatures=true`, it defers the signature pass to a separate process
and preserves the primary-build INI. Keep that INI and the original run INI.
`InPlaceBuild=true` avoids a final index copy; `PersistSelectHead=true` keeps
a selection checkpoint. Neither authorizes overwriting an existing index.
The launcher refuses an existing fresh-build `IndexDirectory`; use
`ResumeBuild=true` only for a compatible checkpoint, never to change the model.

The adaptive path requires a native BKT H1 graph, compatible loaded hierarchy
CSR and `Storage=STATIC`, without hybrid distance or a quantized H1 geometry.
The template uses raw vectors and leaves these alternate modes disabled.
Do not add `BuildH1Graph` or `CompactHierarchyVectors`: new builds already persist
the required native H1 graph and upper catalogs. Unsupported old graphless
layouts need explicit migration into a new directory, not a query-time rebuild.
Retired `HierarchyInitialProbeRatio`, `HierarchyMaxCheck`,
`HierarchyPrefetchMode`, `HeadNavigationMode` and `PostingMinCandidates` are
also invalid fresh settings, even when supplied as false or zero.

#### **Query settings and adaptive budgets**

Only three settings extend ordinary native SPANN search:
`EnablePostingNavigation`, `PostingAnchorCount`, `PostingAdditionalMaxCheck`.
The complete template's search section is deliberately small:

```ini
[SearchSSDIndex]
isExecute=true
BuildSsdIndex=false
InternalResultNum=96
NumberOfThreads=1
ResultNum=10
MaxCheck=2048
MaxDistRatio=8
SearchPostingPageLimit=3
DisableCrossEdges=true
EnablePostingNavigation=true
PostingAnchorCount=8
PostingAdditionalMaxCheck=2048
```

| Search setting | Native library default | Template | Meaning |
| --- | --- | --- | --- |
| `InternalResultNum` | 64 | 96 | nprobe: requested H1 head-result capacity, not final top-k or an exact SSD read count. Effective capacity is at least the requested final top-k. |
| `ResultNum` | 5 | 10 | Final top-k; an API client must pass this value to its search call. |
| `MaxCheck` | 4096 | 2048 | Original H1 checked-leaf budget. It is not a count of every distance computation or all upper-catalog work. |
| `EnablePostingNavigation` | false | true | Enable one supplementary phase **after** filtered H1 search, only when valid heads underfill the head-result capacity. |
| `PostingAnchorCount` | 8 | 8 | Maximum nearest already-scored H1 anchors, including predicate negatives. Must be positive. |
| `PostingAdditionalMaxCheck` | 0 | 2048 | Nonnegative extra checked-leaf allowance for supplementation only; does not enlarge the graph phase. |
| `SearchPostingPageLimit` | 3 | 3 | Physical-page cap on each selected H/O posting region. Zero means uncapped, not "no reads". |
| `MaxDistRatio` | 10000 | 8 | Native distance-ratio cutoff for selected posting candidates; not a filter-selectivity controller. |
| `NumberOfThreads` | 2 | 1 | Aliases native `SearchThreadNum`; separate from client concurrency and build threads (whose SSD thread default is 16). |

`DisableCrossEdges=true` keeps the current single-H1 query profile explicit.
Phase/path logging, head dumps, hybrid distance and quantization are not
needed by this recipe; their disabled defaults need no extra search entries.

**Enabling the flag alone is insufficient:** if graph work has reached
`MaxCheck` and `PostingAdditionalMaxCheck=0`, supplementation does not start.
Its ceiling is `MaxCheck + PostingAdditionalMaxCheck`; the sum must fit a
signed native integer. A selected CSR row completes before the budget is
checked again, so full-row overshoot is possible.

The current sequence is: finish native H1 result-only post-filter search;
protect its admitted heads; use already-scored anchors to explore one global
signature-pruned H2+ representative frontier; fill or replace only the
originally missing slots. A failed signature skips representative/member
access. Within an admitted H2 row, fresh predicate-negative H1 members become
terminal visited rejects without vector prefetch, distance or checked-leaf cost.
Ordinary H1 graph negatives still serve as scored navigation bridges.
Rejected downward children do not expose all their other owners; explicit
entry/owner promotion may still ascend once. No graph restart follows.

Adaptive convergence derives, rather than configures, an H2 representative
pool width `W = max(effective graph MaxCheck / 16, head-result capacity)`.
For `MaxCheck=2048`, top-k 10 and nprobe 96, `W=128`; for nprobe 384, `W=384`.
H3+ share expansion priority without occupying H2 pool slots. Once this pool
is populated, a nearest pending representative worse than its boundary ends
the supplementary phase. Filling the result heap alone does not stop it.
Convergence can also stop **underfilled**: representative distance is an ANN
heuristic, not a lower bound on all unseen members. There is no separate H2
nprobe, convergence-window knob, selectivity multiplier or recall controller.

Unfiltered BKT preserves native adaptive/soft stopping and can overshoot nominal
`MaxCheck`; nonempty predicates use the separate hard graph cap and guarded
tree refill. See [search budget boundaries](SearchBudgetBoundaries.md).
Tune nprobe first on a fixed index, then examine graph work, supplementary work
and SSD page limits separately. Do not rewrite construction settings or expand
parameters by predicate selectivity in the client. Preserve sparse/empty results
in recall accounting instead of silently dropping those queries.

#### **Load and search from another program**

Use the actual [wrapper API](../Wrappers/inc/CoreInterface.h) and native
`Helper::IniReader`; do not translate this recipe into environment variables.
For a built bulk index, load the **root** `IndexDirectory` with `LoadAll`, not
its `tenant_0` subdirectory. Read persisted dimension/type from
`tenant_0/indexloader.ini`; the saved index, not a guessed dataset name, determines
query-buffer layout. Use native vector IO for the application's query file,
with that type/dimension, and keep the buffer alive through the call.

The following C++ integration sketch assumes `configPath` names the run/search
INI and `queryBytes` / `predicateBytes` are validated `ByteArray` inputs.
Include `Wrappers/inc/CoreInterface.h`, `inc/Helper/SimpleIniReader.h` and
`<stdexcept>`. Match the [native builder's CMake target](../AnnService/CMakeLists.txt):
compile the wrapper implementation `Wrappers/src/CoreInterface.cpp`, link
`SPTAGLibStatic` and its dependencies, and use the same include paths and
compile definitions (`TBB`/`NUMA` in the Linux build). This is not a header-only
API. Initialize/load once outside the timed query loop; only repeat the final
search call for each query.

```cpp
SPTAG::Helper::IniReader config, saved;
if (config.LoadIniFile(configPath) != SPTAG::ErrorCode::Success)
    throw std::runtime_error("Cannot read search INI");
const auto root = config.GetParameter<std::string>("Base", "IndexDirectory", "");
if (root.empty() || saved.LoadIniFile(root + "/tenant_0/indexloader.ini") != SPTAG::ErrorCode::Success)
    throw std::runtime_error("Cannot read saved index configuration");
const int dimension = saved.GetParameter<int>("Base", "Dim", 0);
const auto valueType = saved.GetParameter<std::string>("Base", "ValueType", "");
SPTAG::VectorValueType valueEnum;
if (dimension <= 0 || !SPTAG::Helper::Convert::ConvertStringTo(valueType.c_str(), valueEnum))
    throw std::runtime_error("Invalid saved vector layout");
const int topK = config.GetParameter<int>("SearchSSDIndex", "ResultNum", 0);
if (topK <= 0)
    throw std::runtime_error("A positive SearchSSDIndex.ResultNum is required");

auto parameterCheck = SPTAG::VectorIndex::CreateInstance(SPTAG::IndexAlgoType::SPANN, valueEnum);
if (!parameterCheck)
    throw std::runtime_error("Unsupported vector type");
TenantIndexManager manager(dimension, "SPANN", valueType.c_str());
for (const auto& item : config.GetParameters("SearchSSDIndex")) {
    if (parameterCheck->SetParameter(item.first.c_str(), item.second.c_str(), "SearchSSDIndex")
        != SPTAG::ErrorCode::Success)
        throw std::runtime_error("Invalid SearchSSDIndex parameter: " + item.first);
    manager.SetSearchParam(item.first.c_str(), item.second.c_str(), "SearchSSDIndex");
}
if (!manager.LoadAll(root.c_str()))
    throw std::runtime_error("Cannot load index");

auto results = manager.SearchWithPredicate(queryBytes, 0, topK, predicateBytes, -1);
if (!results)
    throw std::runtime_error("Filtered search failed; inspect native diagnostics");
```

The native `VectorIndex::SetParameter` validation is intentional:
`TenantIndexManager::SetSearchParam` returns `void`, not an acceptance status.
Keep `[Base]`, construction sections and application-only orchestration out of
that search-parameter loop. For unfiltered requests, call
`manager.SearchWithPredicate(queryBytes, 0, topK, ByteArray(), 0)`.
The query buffer is **one vector without a file header**, of
`dimension * sizeof(ValueType)` bytes; do not always cast it to Float.

For explicit column-aware filtering, use DNF3 `uint32` words:
`[0x444E4633, clause_count, {literal_count, {kind, column, op, value}...}...]`.
Clauses are ORed and literals within a clause are ANDed. `kind=0` is categorical
equality; `kind=1` is numeric. Columns are original schema indices, and operators
are `EQ=0`, `LT=1`, `LE=2`, `GT=3`, `GE=4`. With the template's schema,
`[0x444E4633, 1, 2, 0, 0, 0, 17, 1, 1, 4, 500]` means
`column0 == 17 AND column1 >= 500`. Pass `-1` as the last API argument for DNF,
not the number of words. Invalid kinds, columns or buffers fail explicitly;
do not substitute a legacy flat tag list for numeric/DNF predicates.

For evaluation, truth must use the same base-row IDs, metric, query cohort
and **exact per-query predicate**; unfiltered truth is not filtered truth.
The specialized `native_postfilter/nativeBench` campaign client restricts its
workload to Float/UInt8 and 128 dimensions, but these are **client restrictions,
not native-library dataset limits**. Its `[Validation]`, `[CounterProbe]` and
`[Runtime.*]` sections are orchestration, not SPANN parameters. The preserved
[controlled-ascent benchmark profile](../Tools/benchmarks/configs/posting_frontier_repair/sift1b_controlled_ascent.ini)
explicitly pins additional diagnostic/default values for reproducibility;
do not replace it with this lean template or remove its required keys.

### **Other native entry points**

The memory-index and classic SSDServing examples below describe separate tools;
they are not substitutes for the current adaptive attribute-build recipe above.

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
QueryPath=sift1b/query.public.10K.u8bin
QueryType=DEFAULT
WarmupPath=sift1b/query.public.10K.u8bin
WarmupType=DEFAULT
TruthPath=sift1b/public_query_gt100.bin
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

<a id="sift1b-with-categorical-and-numeric-attributes"></a>

#### **SIFT1B construction details (dataset-specific)**

This section records the local SIFT1B construction recipe. For another dataset,
use [the portable template](AdaptiveSpann.ini) and the configuration contract
above. The local `_h5` build INI retains its earlier search settings
(`MaxCheck=1024`, no explicit additional budget); it is **not** the final adaptive
query profile. In a new writable run INI, replace its entire `[SearchSSDIndex]`
section with the current search section above. Do not edit frozen run copies.

The current SIFT1B recipe uses two row-major `uint32` columns:
`[categorical tag, numeric]`. Column types are explicit schema, not navigation
partitions; there is no attribute hierarchy or PerTagBKT routing-key file.
If these inputs do not already exist, use a new run INI with the preparation
sections from [the portable template](AdaptiveSpann.ini), the actual SIFT1B
UInt8/128 input paths and fresh attribute/GT destinations:

```bash
python3 Tools/benchmarks/generate_spann_attributes.py --config /absolute/path/to/new-run/build.ini
python3 Tools/benchmarks/generate_spann_predicate_groundtruth.py --config /absolute/path/to/new-run/build.ini
```

The categorical column contains 200 Zipf-distributed regular values and one
extreme value. The generator reads the native INI and computes the largest
rare-label dataset count as
`ceil(L/(SelectHead.Ratio*Slots))-1`. The canonical
`.12` ratio, two slots, and `L=96` produce 399 vectors; there is no EST serving route.
The generator writes both the headerless SPTAG input
`sift1b_zipf200_sparse399_numeric_attrs.u32` and a shape-preserving NumPy copy,
plus exact counts, the policy inputs, and a hash-bound manifest.
The older `prep_sift1b_inputs.sh` and tracked SIFT1B build recipes retain local
paths for historical reproductions. New preparation reads all paths from the
provided INI; it does not use those machine defaults. Synthetic attributes are
not a required preparation step for other datasets; declare their actual
vector/attribute files in their own INI.
The native builder consumes the files as a tenant-0 bulk view,
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
InternalResultNum=96
MaxCheck=2048
EnablePostingNavigation=true
PostingAnchorCount=8
PostingAdditionalMaxCheck=2048
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
Hierarchy signatures conservatively reject auxiliary posting regions before
representative/member access; they do not prune ordinary H1 graph neighbors.
The historical signature min/max selectivity pair is removed.
CPU BKT/TPT RNG follows upstream `5619bb1`:
BKT centers and TPT projections use the global C RNG; TPT workers shuffle with
default `std::mt19937` engines and call `Sleep(i * 100)` then `std::srand(clock())`
per tree. There is no custom fixed seed or external seed parameter, and even
single-threaded rebuilds need not match. Historical fixed-seed artifacts remain
unchanged. `Hierarchy*` describes spatial layers; categorical and numeric
columns remain exact-filter record fields, not partitions.
`HierarchyLevels` counts H1, so `5` selects H1 through H5. Do not add a separate
upper-level ratio. The old upper-graph beam and budget controls are retired;
H1 uses the native search `MaxCheck` and `InternalResultNum`.
The production SIFT1B recipe enables `ParallelBKTBuild=true` so sibling BKT
nodes are processed concurrently instead of serializing the long recursive
H1 selection. This increases temporary memory because each concurrent node
owns k-means workspace; confirm host headroom before launch.
The expected layer sizes are approximately 120M / 14.4M / 1.728M / 207.36K / 24.88K;
BKT selection determines the actual counts. H1 retains the query graph;
H2..H5 retain representative vectors and signed CSR. Native temporary ANN
indexes use the original `[BuildHead]` settings for CSR assignment and are
then released, not saved or loaded as query graphs. This SIFT1B recipe selects
categorical column 0 as its key, with column 1 used for exact numeric filtering.
Filtered queries use H1 result-only post-filter: nonmatching ordinary nodes
remain navigable, while matching heads enter the native result collector.
Unfiltered queries retain native navigation. The native
`[SearchSSDIndex] EnablePostingNavigation` flag defaults to `false`; current
hierarchy recipes explicitly enable it. The current design completes the
ordinary native graph phase first, preserving its native result collector.
Only afterward may bounded signed-posting completion use anchors from that
phase. There is no in-row density trigger or fresh-candidate floor.
`PostingAnchorCount=8` is a positive integer and
`PostingAdditionalMaxCheck` is a nonnegative integer, defaulting to zero.
The current query example explicitly uses `2048` additional checked leaves.
Zero shares only the remaining native budget; an exhausted H1 graph budget
then prevents supplementation entirely. `MaxCheck` plus the additional
budget must fit a native signed integer. Invalid values are rejected even
when posting navigation is disabled.
`PostingMinCandidates` is retired and rejected in build, search and saved
INIs, including explicit zero and OFF configurations. Remove that key from
a writable configuration and use the new controls; never edit frozen
historical snapshots. Older row-density and candidate-floor policies are
historical evidence, not active policies. Complete-row budget overshoot remains
part of the current supplementary phase.
This is not a second upper ANN search, an additional own-result
heap, a graph restart or a global tag scan. Sparse results can still underfill.
`HeadNavigationMode`, `HierarchyGraphSignaturePruning`, `SparseFallbackMaxHeads`,
`SparseFallbackMaxPostingPages`, and hierarchy route-selectivity settings are
removed and rejected. Fresh `BuildH1Graph`, `CompactHierarchyVectors`,
`HierarchyInitialProbeRatio`, `HierarchyMaxCheck` and `HierarchyPrefetchMode`
settings are also retired. Existing layout metadata is decoded explicitly
when loading old artifacts, without restoring their query policy.
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
the launcher refuses an existing `IndexDirectory` when `ResumeBuild=0`.
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

Ordinary unfiltered BKT retains native adaptive/soft stopping: initial pivots,
adjacency expansion and tree refill may overshoot nominal `MaxCheck`. Nonempty
predicates instead use a hard graph checked-leaf cap, with bounded seed/refill
calls; filtered underfilled queries may resume pending tree cells while budget
remains. After graph completion, the current adaptive policy may supplement
missing heads once, without restarting the graph or scanning all tag heads.
Its representative convergence is not a recall guarantee. Sparse or
budget-limited queries may return fewer than top-k, including zero; retain these
queries in recall accounting. See [SearchBudgetBoundaries.md](SearchBudgetBoundaries.md)
for the exact distinction between graph and supplementary budgets.

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

<details>
<summary>Archived SIFT1M experiments and superseded search policies (not current configuration)</summary>

The following records preserve historical measurements, executable identities
and parameter names. They are **not copyable settings for the current engine**:
some named knobs were subsequently removed, and pending/acceptance statements
refer to those campaigns only. Use the portable adaptive configuration above
for new applications; preserve historical INIs and artifacts unchanged.

<a id="current-sift1m-limited-tag-comparison"></a>

#### **Historical SIFT1M limited-tag comparison**

Use `Tools/benchmarks/build_spann_attr_sift1m_zipf200_limited_tag.ini` for the
historical three-level Float128 experiment, rather than the generic SSDServing
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
The historical-format `h1_h2_curve_h2_15pct_r8/sift1m_h1_h2_recall_qps.pdf` kept
historical H1/H2/H3 curves and added both matched old/current H3 curves, with
three-run QPS ranges and explicit budget labels. Reproduce that plot using R:

```bash
Rscript Tools/benchmarks/plot_sift1m_h1_h2_curve.R \
  <results_h1_h2_h3_placement_fixed.jsonl> <output-prefix> \
  <20260909T141212Z_results.summary.csv>
```

The JSONL/CSV, indexes and datasets are local experiment artifacts, not required
repository downloads. Back up an existing figure before replacing it.

**This historical overlay is not a controlled supplier-overhead comparison.**
The historical H1 and rebuilt flat H1 have identical vectors but different
graph and BKT tree bytes. Historical buffered IO also differs from the later
direct-IO view, as do search budgets, page limits and query windows. A current
H1 control that shares modified code with the supplier cannot establish
parity with the pre-supplier implementation. Use the matched-only renderer
below for that comparison; do not substitute historical points for missing
matched measurements.

An optional fourth argument adds the completed full-row H1 neighbor supplier
and its same-run H1, H3 and predicate-first graph controls:

```bash
Rscript Tools/benchmarks/plot_sift1m_h1_h2_curve.R \
  <results_h1_h2_h3_placement_fixed.jsonl> <output-prefix> \
  <20260909T141212Z_results.summary.csv> \
  <comparisons/h1_startup_20260916/summary.json>
```

These are single nprobe24 points, not a new sweep curve. The added numeric
panel contains only the new measurements. Current QPS is the reciprocal of
mean ordinary latency across two runs; bars show the per-run QPS range.
The figure distinguishes the current O_DIRECT/page15/1000-query measurements
from historical curves with different IO, budgets and query windows.
`<output-prefix>.plot-data.csv` preserves all plotted coordinates.

For measured curves, pass a sweep `summary.json` containing an explicit
`nprobe` for each measurement as the fourth argument instead. The plotter
requires the complete scenario/case/nprobe grid, connects measurements in
nprobe order without fitting or interpolation, and maps point size to nprobe.
The same-run H1/H3 references are required; the predicate-first graph control
is optional. Single-point inputs remain unconnected. No benchmark is run by
the plotting script.

To replace obsolete overlays with a completed **partial scenario stage**, append
`--partial-scenarios` as the fifth argument after the measured stage summary.
Each included scenario must still contain all comparison cases at every measured
nprobe, with at least two probe values; missing individual points are rejected.
Unavailable scenarios show historical baselines only, or an explicit empty
panel when no historical data exists. The figure and provenance identify the
missing scenarios. This option does not fabricate points or label later
single-point optimizations as measured curves of the frozen stage.
The exported current-series identifiers are `H1Reference`, `H3Reference`,
`GraphControl` and `PostingSupplier`; their actual input identity is in the
provenance rather than a hardcoded measurement date.

Corrected sweeps must declare
`"supplier_degree_semantics": "predicate_valid_neighbors"` on every supplier
row. The plot then labels the degree as independent of query visited state.
Legacy summaries without this field retain the explicit fresh-degree defect
warning; missing, mixed or unknown declarations within a declared sweep are
rejected. Keep corrected and legacy runs in separate summaries and preserve
the previous figure before publishing a rerun. Degree semantics are recorded
in `<output-prefix>.plot-provenance.json`.

Proportional-degree sweeps instead declare
`"supplier_degree_semantics": "retained_eligible_ratio"` together with
`"retained_ratio": 0.5` and `"minimum_physical_degree": 16` on every supplier
row. These parameters must be valid and constant across the sweep. The figure
labels the relative trigger separately from the older absolute-degree policy:
physical degree below the floor never supplements, and otherwise the eligible
fraction must be strictly below the ratio. Visited does not reduce eligibility.

**Current main implementation.** The implementation lives in `AnnService`,
including native BKT/SPANN search, `Common/NavigationVisited.h`,
`Common/PostingNavigation.h` and `SPANN/PostingNavigation.h`. Normal root CMake
builds compile it into the main library; there is no generated replacement
core. `Tools/benchmarks/hierarchical_shortcut_native/native_postfilter/README.md`
now documents only thin clients linked to that library. H1 result-only
post-filter is the baseline; `EnablePostingNavigation=true` adds the signed
posting extension. The experiment history below is preserved provenance,
not a menu of current implementations. No running production deployment or
existing dataset index was replaced.

**Frozen degree and cost-arbitration experiments.** These degree-based modes
are frozen connectivity-deficit experiments, not a
cost-arbitrated auxiliary-edge policy. Do not relabel their measurements as
evidence for the latter. The isolated cost-based design preserves ordinary
graph navigation and filters result admission; a node that fails the result
predicate can still be an intermediate point on a path to a matching node.
The native `SearchIndexWithResultFilter` interface separates these concerns,
whereas `SearchIndexWithTraversalFilter` restricts both.

The two candidate actions are lazy continuation of the native graph search
(which can reach second-hop nodes) and expansion through signed multilevel
posting auxiliary edges. Both feed the same native frontier and visited state.
Compare their incremental work for the same remaining useful-result objective,
including novelty, predicate acceptance, distance competitiveness, posting
setup and complete-row costs. Do not charge the graph for eagerly enumerating
an entire squared-degree neighborhood that its best-first search would not
necessarily explore. Do not scan both alternatives merely to estimate them.
An auxiliary member rejection must not mark a point visited in a way that
prevents later ordinary graph navigation through that point.

Cost estimates are decision inputs, not correctness predicates. Keep final
exact filtering, conservative signature rejection, native distance/budget
semantics and full selected-row handling independently enforced. A degree
ratio was not used to select routes in this historical cost model. The older
hybrid-distance router and adaptive-nprobe estimator perform different jobs;
enabling them does not implement this policy. Any operation weights or priors
belong in the native INI, with their units and calibration assumptions explicit.

Changing the expansion policy can intentionally change visited heads and
expansion counts. Its acceptance checks therefore cover native bridge
reachability, no auxiliary visited-state poisoning, both estimator choices,
result correctness and recall at fixed budgets, rather than requiring the
old deficit supplier's exact work trace. Use a post-filter graph-only control
with the same admission semantics as well as authenticated H1/H3 references.
Diagnostic decision records must remain outside ordinary timings. A focused
policy experiment does not authorize resuming an operator-stopped full sweep.

The isolated implementation is in
`Tools/benchmarks/hierarchical_shortcut_native/cost_arbitration_v2_20260917/`.
`CostModel.h` estimates novelty and useful gain from query-local observations
and explicit priors; `PostingSupplier.h` selects signed H2/H3 actions;
`NativeSupplier.h` connects them to native result-only graph search. Native
representative distance orders economically eligible parents within a level,
then unpaid cost per expected useful gain selects between level winners and
graph continuation. H3-discovered H2 adjacency is retained for later decisions,
not placed in a second navigation frontier. A lack of previous graph success
does not override an otherwise favorable posting estimate.

The checked-in native INIs expose `ArbitrationDistanceCost`,
`ArbitrationPredicateCost`, `ArbitrationMemberCost`,
`ArbitrationSignatureCost`, graph/posting setup costs and the history priors.
These are **uncalibrated operation units**, normalized to distance cost 1,
not predicted microseconds. The estimator accounts for full-row overshoot
against `max(1, unfilled H1 result slots)`; its useful-head proxy is not a
guarantee of final dataset recall. Removed degree-quota controls are rejected.

The focused milestone is recorded locally under
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/cost_arbitration_v2_20260917/`.
Its `report.json` contains the precise formulas, native configuration values,
decision evidence and reproduction commands; `runtime.json` and
`milestone_manifest.json` pin the implementation and inputs. This is a
semantically exercised experimental implementation, **not a claim that the
Broad performance target has been achieved**. The estimate-only control
measures observation/estimation overhead on an unchanged graph trace.
Single-point data must not replace the stopped full curves.

Run its bounded native fixtures with:

```bash
ctest --test-dir \
  ../datasets/sift1m_zipf200_sparse193_numeric/toolchains/cost_arbitration_v2_20260917/harness \
  --output-on-failure
python3 -m unittest discover \
  -s Tools/benchmarks/hierarchical_shortcut_native/cost_arbitration_v2_20260917 \
  -p test_protocol.py
```

These commands assume the repository root and an existing isolated build.
For a performance reproduction, use the experiment's native configuration
with a fresh authorized output location; existing evidence must not be
overwritten and the stopped sweep must not be resumed implicitly.

**NaviX-derived local routing (replacement policy).** The reference is
[gaurav8297/faiss-navix](https://github.com/gaurav8297/faiss-navix), pinned to
commit `192fafbff7ead780185891e6056bbf67cbb19606` (MIT license, copyright
Facebook, Inc. and its affiliates). Its
[`faiss/impl/HNSW.cpp`, lines 1421-1469](https://github.com/gaurav8297/faiss-navix/blob/192fafbff7ead780185891e6056bbf67cbb19606/faiss/impl/HNSW.cpp#L1421-L1469)
selects the local expansion operator using physical neighbor count `d` and
predicate-valid neighbor count `e`. Visited neighbors still contribute to
both counts; visited is work deduplication, not predicate failure.

For nonempty rows, the upstream decision is:

| Condition | Expansion |
| --- | --- |
| `e / d >= 0.5` | Filtered one-hop |
| Otherwise, `0.4 * (d * e + e) > 2 * d - e` | Directed two-hop |
| Otherwise | Complete two-hop |

These are cheap local operation estimates, not calibrated latency predictions.
Despite the upstream comment "Blind Two Hop", the active call is
`navix_full_two_hop`; `navix_blind` is commented out. Directed expansion ranks
fresh first-hop intermediates by ascending query distance and follows
nonmatching intermediates, stopping between complete neighbor rows once its
valid-encounter count reaches `d`. That encounter count includes valid visited
neighbors and repeated encounters; it is not a quota of fresh results.
Complete two-hop scans every first-hop adjacency, including visited
intermediates, while deduplicating eligible candidate offers.

The requested extension adds a most-sparse posting route **before** the
upstream branches. Its separate native INI `NavixPostingThreshold` initially
specifies `e / d < 0.05`; this is a new design choice, not a NaviX constant or an
empirically established optimum. Only a selected posting route may enumerate
upper owners, check signatures or compute representative distances. An
unusable posting action falls back to the applicable NaviX graph operator.
The ratio selects an expansion route, never a connectivity deficit to fill.
An empty ordinary row must be handled explicitly without dividing by zero.

This is an adaptation of local expansion, not a complete Faiss search port.
Keep native BKT initialization, the shared H1 candidate/result domain,
alias/deletion handling, own-point admission and row-boundary budgets.
Do not import upstream's scan for the first ten matching global IDs or its
fixed 4096-candidate scratch array. Nonmatching intermediates are necessary
for the two-hop operators; filtered one-hop is not equivalent to native
result-only post-filter traversal. Compare native post-filter, NaviX-only and
NaviX-plus-posting controls separately, at fixed settings. The stopped full
sweep and the already published curves remain unchanged.

The isolated implementation is
`Tools/benchmarks/hierarchical_shortcut_native/navix_posting_20260917/`.
`NavixMode=navix` disables only the posting extension for the NaviX-only
control. Native eligibility means posting-region/support eligibility **or**
exact own-head eligibility, rather than Faiss's per-vector filter byte.
The native checked-leaf budget does not bound repeated predicate checks or
second-hop adjacency scans; completing selected expansions can also cross its
boundary. These differences matter for both work and performance comparisons.

The bounded milestone is **not promoted: its performance goal failed**.
At nprobe 24, measured Broad latency/recall were 0.702878 ms/0.9154 for the
result-only post-filter control, 1.525711 ms/0.9177 for NaviX-only and
1.522814 ms/0.9176 for NaviX plus posting. Extreme-sparse measurements were
0.904761 ms/0.2033, 76.179772 ms/0.9783 and 89.857407 ms/0.9985 respectively.
The post-filter controls were freshly measured after the NaviX runs, not
interleaved; do not describe these as simultaneous paired-core measurements.
The initial unfiltered-head-admission controls are retained as diagnostics,
not mislabeled as result-only post-filter.

In 1,000 extreme-sparse measured queries, 2,581,938 of 2,644,028 posting
decisions fell back to two-hop expansion. Combined search averaged about
1.46 million qualification checks and 59,098 second-hop rows per query.
Removing predecision upper work therefore did not resolve the repeated
graph-expansion cost. Native unfiltered bypass retained exact original
results and SSD work with zero hierarchy work.

The measured implementation also has avoidable inner-loop overhead; these
numbers must not be interpreted as the intrinsic cost of NaviX. Unlike
upstream's filter-byte reads, every neighbor encounter recomputes native
support/posting/own eligibility. Broad averages 9,668 qualification calls
for 1,296 fresh graph candidates per query. Its two-hop loop prefetches
vectors before eligibility/visited rejection, emits candidates individually
through the generic admission path with observation bookkeeping even when
diagnostics are disabled, and allocates new local scratch on each expansion.
The audit confirms these code paths, not a measured latency share for each.

Hot-path optimization must preserve the routing constants, posting threshold,
native budgets, exact outputs and search-work trajectory. Cache query-local
predicate results lazily, keeping posting eligibility distinct from own-only
eligibility and from result improvement. Reuse scratch without fixed candidate
caps; prefetch vectors only where distance work is needed. Count qualification
requests separately from expensive cache misses. A visited valid neighbor
still contributes to local selectivity, and a full two-hop row must not be
silently skipped to make the work counters look better. Performance controls
must use result-only post-filter admission, not the historical unfiltered
head-admission control; keep ordinary timings separate from diagnostics.

The first isolated repair,
`Tools/benchmarks/hierarchical_shortcut_native/navix_hotpath_20260918/`,
preserves recorded outputs and search work but **fails Broad latency
acceptance**. Fresh reversed-order comparisons measured old/new NaviX at
1.551611/1.599377 ms and old/new combined at 1.557548/1.635945 ms, against
0.714231 ms for result-only post-filter. Recall remained unchanged.
Component-cache hits were approximately 45%; a single qualification can
request several components, so the 20,669 component requests and 11,388 misses
per query must not be compared as the same unit as 9,668 qualification calls.
Reduced evaluation or allocation counts alone do not establish an optimization.
This variant is frozen and not promoted. Its report is
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/navix_hotpath_20260918/report.json`.
Further hot-path changes require CPU attribution rather than assuming that
any one of the audited code patterns explains the latency gap.

The profiled successor,
`Tools/benchmarks/hierarchical_shortcut_native/navix_profile_fix_20260918/`,
starts from the original NaviX variant rather than accumulating the failed
component-cache changes. It lazily caches separate final posting and exact-own
qualification bytes; mutable deletion, visited state, own gains and
competitiveness remain uncached. Recorded outputs and search work are unchanged.

Query-thread CPU sampling of the frozen controls places 44.2% of original
NaviX samples in selected nonoverlapping qualification/admission functions,
including support-slot reads, alias qualification and posting validity.
This represents about 0.682 ms/query of gross diagnostic CPU residence, not
an exact attribution of the latency difference or proof of memory stalls.
L2-distance sample counts were similar in absolute terms for NaviX and the
post-filter control. Sampling was outside ordinary timings, with load,
warmup and other threads excluded.

This targeted fix **also fails reliable Broad speedup acceptance**. Fresh
old/successor NaviX means were 1.557223/1.535581 ms, but the second paired
repetition regressed. Combined means were 1.567943/1.567105 ms, effectively
unchanged; the proper graph control measured 0.711161 ms. The isolated fix is
not promoted. Qualification evaluations decreased, but the remaining CPU cost
has not been attributed for the successor, so further speedup is not established.
Sparse and unfiltered parity checks cover only 32-query replays, not a fresh
full timing campaign. Raw profiles, source patches, ordinary measurements,
limitations and reproduction records are retained under
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/navix_profile_fix_20260918/`.

The subsequent requested threshold comparison exposes the local two-hop
trigger as native `NavixTwoHopThreshold`, comparing the upstream 0.5 cutoff
with fixed candidates 0.1 and 0.05. Unlike the hot-path repairs, this is an
intentional expansion-policy change: preserve the original trajectory only
for the 0.5 control, not for the two requested candidates. That historical
comparison kept the posting threshold at 0.05, nprobe at 24 and every other
native setting fixed. Retain both thresholds separately in native INIs,
reports and case identifiers.

At or above the two-hop threshold, use the existing **filtered one-hop**
operator, not native result-only post-filter. Below it, use the original
directed/full cost comparison unless the posting branch takes precedence.
With both thresholds equal to 0.05, combined search has no directly selected
two-hop interval, but unavailable posting expansion can still fall back to
two-hop. Report these fallbacks and their adjacency work separately; zero
direct two-hop decisions does not imply zero two-hop work.
**This equal-threshold combined point does not satisfy the intended strict
hierarchy:** posting must require a lower local eligible fraction than
two-hop (`postingThreshold < twoHopThreshold`). Retain its measurements as
diagnostic evidence only, not as an acceptable combined-policy candidate.
The experiment's parser admitted equality and its fixed posting cutoff was
not lowered with the 0.05 two-hop cutoff. The NaviX-only measurements are
unaffected by this configuration error.
Use fresh Broad NaviX-only and combined points at all three thresholds plus
the proper post-filter control, with two reversed-order repetitions.
This comparison does not authorize a full sweep, additional threshold search,
automatic promotion or replacement of published plots.

The completed isolated comparison is
`Tools/benchmarks/hierarchical_shortcut_native/navix_thresholds_20260918/`.
All seven Broad points used nprobe 24 and two reversed-order repetitions:

| Mode | Two-hop threshold | Mean ms/query | Recall@10 | Explicit second-hop rows/query |
| --- | --- | --- | --- | --- |
| Native result-only post-filter | Not applicable | 0.720401 | 0.9154 | 0 |
| NaviX-only | 0.5 | 1.543136 | 0.9177 | 393.053 |
| NaviX-only | 0.1 | 1.222632 | 0.9134 | 55.179 |
| NaviX-only | 0.05 | 1.133370 | 0.9127 | 10.386 |
| NaviX plus posting | 0.5 | 1.574572 | 0.9176 | 391.040 |
| NaviX plus posting | 0.1 | 1.229055 | 0.9134 | 46.389 |
| NaviX plus posting | 0.05 | 1.113048 | 0.9127 | 1.453 |

At 0.05 the combined variant reduces latency by about 29.3% relative to its
0.5 control, but loses 0.49 recall percentage points. It remains slower and
has lower recall than native post-filter. Its remaining second-hop rows come
from 144 posting fallbacks across 1,000 queries; directly selected two-hop
decisions are zero. Native graph navigation can naturally reach second-hop
nodes and is not counted as an explicit two-hop operator in this table.
The 0.5 controls exactly preserve frozen outputs and recorded work.
Lower-threshold trajectories intentionally differ, and no additional cutoff
was selected using recall. Full measurements, repetition ranges, route counts,
native configurations and provenance are retained under
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/navix_thresholds_20260918/`.
No variant was promoted and no published curve was changed.

**Strict posting hierarchy correction.** Active combined search must enforce
`0 <= NavixPostingThreshold < NavixTwoHopThreshold`; equality and reversed
thresholds are invalid, not alternative policies. For the correction, the
posting cutoff is explicitly 0.01 with the requested two-hop cutoffs 0.05
and 0.1. This is an untuned fixed choice, not a proven optimum. Posting cutoff
zero disables posting under the strict `r < cutoff` comparison; it must not
be reinterpreted as a special `r == 0` trigger.

For nonempty rows, select one-hop at or above the two-hop cutoff; below it,
select posting only below the strictly smaller posting cutoff, otherwise
apply the original directed/full two-hop comparison. Posting failure can
still fall back to two-hop within that sparse domain. A Broad workload can
contain locally sparse rows: no global scenario-name bypass is implied.
Because `e/d` is discrete, lowering a positive posting cutoff may leave
zero-valid-neighbor triggers unchanged. Keep this distinct from violating
the strict threshold hierarchy. Preserve all historical measurements,
including the invalid equal-threshold diagnostic point.

The corrected isolated implementation is
`Tools/benchmarks/hierarchical_shortcut_native/navix_strict_thresholds_20260918/`.
One shared validator enforces the strict relation in native configuration
parsing, route helpers, supplier setup and injectable hooks. The disabled
posting extension in NaviX-only mode does not reject irrelevant equal cutoffs.
The correction passes independently of the still-unmet performance goal.
Fresh Broad measurements, each with two reversed-order repetitions, are:

| Mode | Two-hop / posting cutoffs | Mean ms/query | Recall@10 |
| --- | --- | --- | --- |
| Native result-only post-filter | Not applicable | 0.713467 | 0.9154 |
| Combined | 0.1 / 0.01 | 1.224404 | 0.9134 |
| Combined | 0.05 / 0.01 | 1.119857 | 0.9127 |

Across 1,000 queries, the corrected 0.05/0.01 configuration made 99 direct
full-two-hop decisions, 721 posting decisions and 140 full-two-hop fallbacks;
the intermediate interval is no longer absent. The 0.1/0.01 configuration
made 2,746 direct full-two-hop decisions, 720 posting decisions and 164
fallbacks. Both had zero directed-two-hop decisions, and every observed
Broad posting decision had local `e=0`. Explicit second-hop rows averaged
3.583 and 48.689 per query respectively. Older timings are historical
comparisons only, not fresh paired speedups. Full evidence is retained under
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/navix_strict_thresholds_20260918/`.
Production binaries and published curves remain unchanged.

**Post-filter plus two-hop isolation.** The requested isolation suspends
both filtered one-hop and hierarchical posting. The ordinary operator must
be native result-only post-filter: predicate-invalid neighbors remain eligible
for graph navigation and for updating the native search-distance bound.
Merely reducing auxiliary trigger frequency does not restore this behavior.
For example, the strict 0.05/0.01 variant expanded 230.612 heads per Broad
query versus 168.604 for post-filter, even though its explicit second-hop
work was only 3.583 rows. These counts come from captured expansion decisions,
not queue offers or distance evaluations.

Use local valid-neighbor ratio only to select native ordinary expansion or
the existing sparse directed/full two-hop operator, at the fixed 0.05 and
0.1 cutoffs. Ordinary expansion must retain native per-edge budget handling,
visited state, result/own admission, candidate heap and termination semantics.
The sparse two-hop operator still uses predicate eligibility for candidate
destinations and allows nonmatching intermediates; disabling filtered one-hop
does not mean unfiltered squared-degree distance evaluation.
There must be no posting callback, upper signature/distance/CSR work, or
hierarchy-metadata requirement in this graph-only mode. Verify the
auxiliary-disabled path against native post-filter, and record outer graph
expansions, ordinary neighbor entries and qualification requests separately
before attributing any remaining overhead to convergence or per-step cost.

The completed implementation is
`Tools/benchmarks/hierarchical_shortcut_native/postfilter_twohop_20260918/`.
Native `NavixMode=postfilter_graph` provides a same-core result-only control
without ratio classification; `NavixMode=postfilter_twohop` enables only
the restored ordinary operator and sparse two-hop. Both modes disable posting
and its owner/model requirement. The control preserves all 1,000 frozen
post-filter outputs and recorded native/SSD work exactly.

Fresh Broad results at nprobe 24, with two reversed-order repetitions:

| Mode | Mean ms/query | Recall@10 | Outer expansions/query | Qualification requests/query |
| --- | --- | --- | --- | --- |
| Post-filter control | 0.711638 | 0.9154 | 168.604 | 0 |
| Post-filter plus two-hop, 0.1 | 1.229461 | 0.9164 | 167.241 | 4583.078 |
| Post-filter plus two-hop, 0.05 | 1.168747 | 0.9156 | 168.406 | 4064.870 |

At 0.05, 99.671% of expansions select ordinary post-filter, with 2,075.922
H1 distances per query versus 2,070.680 for the control. Navigation work
returns to graph-like levels, but latency remains 64.2% higher. The hybrid
still performs 3,939.882 initial classification entries and 6.854 explicit
second-hop rows per query. These establish remaining additional work, not
an exact per-phase attribution of latency. Qualification requests in this
table describe the extra classifier, not all final-result predicate checks.
All posting/upper work is zero. The semantic isolation passes, but neither
hybrid meets the speed goal; no production or plotting promotion occurred.
Full measurements, native configurations and validation evidence are under
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/postfilter_twohop_20260918/`.

**Replacement: native post-filter with signed posting adjacency.** The
requested replacement abandons NaviX's explicit two-hop operators and
standalone filtered one-hop. Preserve native graph navigation, including
nonmatching intermediates. Collect local physical/eligible neighbor counts
inside ordinary adjacency processing rather than a separate classification
scan. Valid visited neighbors still count; visited controls duplicate work,
not eligibility. Do not reintroduce degree-deficit filling.

The row's observed ratio is available after ordinary processing. At that
point the next action is either native graph continuation or a signed
posting expansion anchored at that head, feeding the same native frontier.
This does not skip the already-completed ordinary row. A budget-truncated
row must not masquerade as a complete-degree observation or start a new
posting action after the budget is exhausted. Keep the initial activation
cutoff fixed at 0.01 in native INI, explicitly untuned.

Only after selecting posting may the implementation inspect its upper
candidates. Reject signatures before representative distances or CSR member
access, complete selected rows under native budget rules, and preserve
result/own admission and auxiliary rejection without visited-state poisoning.
If no usable posting action exists, continue the original frontier; there
is no two-hop fallback, graph restart or global support scan.

Use same-core native graph, observe-only and posting controls. Observe-only
collects exactly the fused statistics but performs no upper work, and must
preserve graph outputs and navigation work. This isolates statistics overhead;
posting can change the trajectory, so its latency difference is not a
same-trace causal decomposition. Record classification prescan and explicit
second-hop work as zero. A repeated small-row access is not automatically
a second DRAM access; neither cache behavior nor signature savings should
be claimed without evidence.

The accepted semantic implementation is
`Tools/benchmarks/hierarchical_shortcut_native/postfilter_posting_fullrow_20260918/`.
The earlier `postfilter_posting_20260918` attempt incorrectly truncated four
selected CSR rows at the ordinary per-edge budget boundary; its evidence
is preserved but superseded. The corrected runtime completes an already
selected auxiliary row, retains ordinary per-edge truncation and starts no
new action after budget exhaustion. Corrected captures contain zero partial
CSR rows. No activation threshold or search budget changed for this repair.

Fresh same-core measurements, with two reversed-order repetitions:

| Scenario | Mode | Mean ms/query | Recall@10 |
| --- | --- | --- | --- |
| Broad | Native graph | 0.730224 | 0.9154 |
| Broad | Observe-only | 1.049193 | 0.9154 |
| Broad | Graph plus posting | 1.029568 | 0.9154 |
| Extreme-sparse | Native graph | 0.950633 | 0.2033 |
| Extreme-sparse | Graph plus posting | 2.261953 | 0.9929 |

Graph and observe preserve identical outputs, head/own results, distance,
queue, checked and SSD work. Observe therefore exposes 0.318968 ms/query
(43.7%) of added qualification/cache/observation-path cost on the same
navigation trajectory. This is not an attribution to predicate comparisons
alone or to DRAM misses. Broad ordinary neighbor entries remain 3,196.670
per query in both controls; observe adds one qualification request per
entry, without an extra classification traversal. Explicit second-hop and
classification prescan counts are zero in all three modes.

Broad posting checks 3.196 signatures per query and rejects none, so this
scenario establishes no signature-pruning savings. Sparse posting checks
4,064.623 signatures and rejects 3,698.608 per query, while adding 17.403
fresh eligible auxiliary neighbors. These operation counts are not elapsed
time savings, and the sparse latency comparison has very different recalls.
The native-speed goal remains unmet and the runtime is not promoted.
The final report, corrected captures and reproduction commands are retained
under
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/postfilter_posting_fullrow_20260918/`.

**User-confirmed simplification: reuse native filter outcomes.** The requested
successor removes the extra eligibility-query, cache and observe path.
Switching may use only predicate pass/fail results that native head
post-filter admission already evaluated during the ordinary expansion.
Do not invoke support, posting validity, attributes, deletion or alias checks
merely to obtain a switching input. Keep ordinary navigation and all its
existing checks intact.

This deliberately replaces the earlier full-physical-neighbor `e/d`
requirement. The signal is the observed native head-filter acceptance rate,
not the eligible fraction of all neighbors, not result-heap improvement and
not the number of fresh vertices. Skipped or previously visited neighbors
without a native predicate evaluation are unknown, not failures. An
expansion with no observed filter result continues ordinary graph search.
Do not synthesize a zero hit rate or perform extra work to fill the sample.

After a completed ordinary row, a simple comparison of naturally observed
passes/checks may select a signed posting action, within the existing budget.
Use the fixed initial 0.01 cutoff under an explicitly named native
filter-hit-ratio control; the number has a different meaning from the old
full-neighbor cutoff and is not an established optimum. Auxiliary predicate
evaluations must not feed their own activation decision. Only native graph
and graph-plus-posting modes belong in this successor, with no observe mode,
classification prescan, per-H1 qualification cache or two-hop operator.
Verify predicate call order/count parity as well as navigation parity when
the auxiliary action is disabled; fewer auxiliary branches alone are not
proof that the extra eligibility path has disappeared.

This simplification is implemented in
`Tools/benchmarks/hierarchical_shortcut_native/postfilter_native_switch_20260918/`,
with native `NativePostfilterMode=graph|posting` and
`PostingFilterHitRatio=0.01`. The original predicate evaluation updates two
local counters while ordinary expansion is active. Auxiliary checks do not
update those counters. Only a complete ordinary row with remaining budget,
at least one observed check and `passes < ratio * checks` can request a
posting action. There is no extra ordinary eligibility query or qualification
cache. Fixtures verify predicate/support/posting-validity/own callback order
and count parity, as well as navigation parity, when posting is disabled or
does not trigger.

Fresh same-core results, with two reversed-order repetitions:

| Scenario | Mode | Mean ms/query | Recall@10 |
| --- | --- | --- | --- |
| Broad | Native graph | 0.727615 | 0.9154 |
| Broad | Native switch plus posting | 0.934268 | 0.9170 |
| Extreme-sparse | Native graph | 0.962601 | 0.2033 |
| Extreme-sparse | Native switch plus posting | 2.129577 | 0.9932 |

Broad requests posting 17.104 times per query; its trajectory changes, so
the 28.4% latency difference is not a same-trace observation tax. It checks
102.352 signatures and rejects only 0.003 per query, establishing no
substantial Broad signature-pruning benefit. The switch input intentionally
differs from full-neighbor eligibility, and its fixed cutoff remains untuned.
Native-level Broad speed is still unmet. All selected CSR rows are complete,
unfiltered requests retain the native bypass, and production/plots remain
unchanged. Final source/config/runtime identities, measurements and
reproduction evidence are retained under
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/postfilter_native_switch_20260918/`.

**Visited-entry match bit (isolated successor).** The per-expansion
native-outcome signal has insufficient evidence in many Broad rows: of
17,104 posting activations in 1,000 captured queries, 11,644 followed just
one failed predicate check and 14,832 followed at most two. Every activation
had zero observed passes. Native result-admission short-circuits mean that
these samples do not describe the entire physical neighborhood.

The requested successor restores full local neighbor eligibility by storing
a query-specific match bit in the native visited entry itself. The same
lookup must return both previously-visited status and match. First insertion
must initialize match; later encounters, including visited neighbors, reuse
it when collecting local degree in ordinary adjacency processing. Do not add
another qualification table, whole-index mask or row prescan. Match-false
nodes must still participate in ordinary post-filter navigation.

One bit is sufficient only with the invariant that every occupied
navigation entry has initialized match state. Cover tree initialization,
tree continuation, graph edges and auxiliary insertions, and preserve the
state through collision handling and rehash. Clear it with query reset.
Do not sacrifice valid node-ID bits or silently treat unknown as false.
An auxiliary rejection must not create a navigation-visited entry just to
cache the rejection. The match definition must remain distinct from result
competitiveness and own-heap gain; a combined posting-or-own bit cannot stand
in for its individual components during result admission. Mutable deletion
checks must retain their native semantics.

This design pays for first predicate evaluation rather than waiting for
distance-competitive result admission, but avoids repeated evaluations for
visited entries. Measure that cost against a native graph control, verify
full local ratios against an independent untimed oracle, and report reused
match reads separately from first evaluations. Do not assume co-location
alone proves a speedup.

The first isolated implementation,
`Tools/benchmarks/hierarchical_shortcut_native/postfilter_visited_match_20260918/`,
initializes match on every navigation insertion and validates complete local
ratios, including visited neighbors, against an untimed oracle. Broad bit-only
preserves the graph trajectory and averages 1,943.817 first match evaluations
and 1,345.058 reused bit reads per query. It has no separate eligibility array.

However, this first version uses 8-byte match slots versus 4-byte native
slots and reallocates when switching compact/extended storage; workspace
reset selects compact before enabling match again. Its Broad graph/bit-only/
posting means are 0.733459/1.055550/1.061783 ms, so it does not establish a
successful speed optimization. The measured 43.9% same-trace bit-only tax
includes storage width, transitions, dispatch, first predicate evaluation
and row bookkeeping, not just predicate cost. Preserve this version's
evidence while eliminating introduced storage-transition overhead and
checking whether compact packing can preserve the actual valid ID domain.
Never narrow IDs merely to make a match flag fit.

This first implementation explicitly supports only the isolated read-only,
query-consistent index snapshot. Native result/own liveness checks remain,
but match-enabled concurrent maintenance is not supported or silently
declared safe. Its report and capability limits are retained under
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/postfilter_visited_match_20260918/`.

**Storage correction, same algorithm.** The isolated successor
`Tools/benchmarks/hierarchical_shortcut_native/postfilter_visited_storage_20260918/`
keeps native navigation entries at 4 bytes. Since the native sample count is
signed 32-bit and valid IDs are strictly below that count, unsigned `id+1`
fits in 31 bits; bit 31 holds match. Comparisons and rehash mask the flag.
The generic hash-table interface still accepts `INT_MAX`, promoting to
8-byte storage only when that ID actually requires it. Reset retains the
allocated backend rather than switching compact/wide each query. There is
one active array, no separate match array, and result dedup stays 4-byte.

Outside timing, 6,000 warmed real Broad queries recorded zero navigation
backend allocations or conversions; mixed-mode and cross-index workspace
reuse also retained stable allocations. This is not a claim that whole
queries allocate nothing. Each native navigation table has 131,072 slots
(512 KiB) in all three modes.

| Broad mode | Mean ms, two reversed-order repetitions | Recall@10 |
| --- | ---: | ---: |
| Native graph | 0.733207 | 0.9154 |
| Match bit, posting disabled | 1.023004 | 0.9154 |
| Match bit plus posting | 1.049887 | 0.9154 |

Graph and bit-only retain identical native results and trajectory. The
remaining same-trace tax is 0.289797 ms (39.5%); its CPU causes had not yet
been separated at this milestone. First match evaluations remain 1,943.817/query,
with 1,345.058 reused reads and unchanged native result-admission calls.
Co-location removes repeated switch-predicate evaluation for occupied
entries, not the first evaluation or distinct posting/own admission.
Do not claim a native-speed result or a fresh paired speedup over the
historical 8-byte version. The full-neighbor oracle and storage/UBSan
fixtures pass; sparse and unfiltered checks are only 32-query functional
replays in this successor. The read-only snapshot limitation remains.
No production promotion, curve update or stopped-sweep restart occurred.
The corrected report and provenance are under
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/postfilter_visited_storage_20260918/`.

**Posting-disabled CPU attribution.** The subsequent
`postfilter_visited_profile_20260918` investigation profiles the frozen
storage-corrected executable, without search changes or an ablation. Two
reversed-order query-thread sampling windows per mode give mean thread CPU
times of 0.7373 ms/query (graph) and 1.0267 ms/query (bit-only). Auxiliary
H2/H3 posting calls, signatures, representative distances and CSR members
are all zero; normal final SSD postings still execute identically.

| Mutually exclusive sampled residence, ms/query | Graph | Bit-only |
| --- | ---: | ---: |
| First-match-only callback, own/liveness and physical-node checks | 0.0000 | 0.1770 |
| Shared support, posting-validity, predicate and liveness functions | 0.0350 | 0.1910 |
| Visited/result-dedup probe, hash, flag and dispatch | 0.0815 | 0.0845 |
| Ordinary adjacency, prefetch, row statistics and state | 0.0315 | 0.0460 |

The added hotspot is first-match qualification, not repeated allocation or
auxiliary posting expansion. Its path includes physical-head/deletion
checks, posting-support OR live-own eligibility, then an alias-marker check
when necessary. Samples land in support-tag comparisons, own VID/version/
deletion access and the alias-marker read. No additional alias candidate
evaluations occur in this workload. Posting qualification also repeats
`CheckValidPosting`; that reads metadata, not a CSR traversal.

These are sampled residence estimates, not independently removable costs:
the qualification categories grow by about 0.3331 ms while total CPU grows
by 0.2894 ms and other categories decrease. Shared leaf functions cannot
identify their caller, and PC sampling does not establish cache misses or
DRAM stalls. There are 3,528 retained samples, no recorded buffer drops or
wrong-thread samples, and roughly 1 ms actual sampling intervals despite
a requested 200 us. Only two windows per mode were run; periodic aliasing
and process drift remain limitations. Full categories, raw PCs, symbol
mapping, control timings and source references are preserved in
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/postfilter_visited_profile_20260918/`.
No optimization or production promotion accompanies this diagnosis.

**Over-restored native lifecycle (superseded diagnostic).** The isolated successor
`Tools/benchmarks/hierarchical_shortcut_native/postfilter_native_predicate_20260918/`
removes the added H1 own-result heap, H1 result-admission filter and eager
own/VID/version/deletion/alias qualification. It restores the authenticated,
index-compatible pre-supplier SPANN/BKT path, not the preceding experimental
graph control. The baseline is revision
`3552194536cb01dd70099e955a29e235a0cf4d2e` plus its authenticated build snapshot;
upstream `5619bb1` independently establishes the native result/alias lifecycle.
This is not pristine upstream: existing hard per-edge budgets, bounded tree
continuation, H/O storage and exact final attribute filters remain.

Only signature gates, attribute routing checks and fixed `.01` sparse
posting adjacency are added to that lifecycle. The retained 4-byte visited
bit caches native anchor-support membership, with no posting-validity,
own-record or alias checks to initialize it. All 160,091 actual H1 own tags
are present in support; hierarchy signatures also explicitly insert the
head's own attribute. Native alias processing, liveness, head translation
and final exact filtering remain in their original places. Bit-only and
graph have identical native trajectories; warmed visited backends still
perform no allocations or conversions.

Support membership is exact for the support summary, not exact record
selectivity or live-result eligibility. Anchored DNF routing remains a
necessary may-match condition. Numeric-only/unanchored requests retain
unrestricted routing and exact final filtering; their mapping is
source-reviewed, not measured by this categorical/unfiltered input set.
This historical snapshot predates main numeric-signature routing; current
numeric/unanchored queries use O-region may-match admission, not unrestricted
admission when authenticated metadata is available.

| Scene / mode | Mean ms, two reversed-order repetitions | Recall@10 |
| --- | ---: | ---: |
| Broad native graph | 0.444646 | 0.7225 |
| Broad predicate bit only | 0.516506 | 0.7225 |
| Broad bit plus posting | 0.532721 | 0.7225 |
| Sparse native graph | 0.412776 | 0.0028 |
| Sparse bit plus posting | 1.102340 | 0.0028 |

The Broad same-trajectory bit tax is 0.071860 ms (16.2%). Do not compare
these latencies to the earlier 0.9154-recall experimental graph as an
equal-recall improvement. Native selection retains the nearest 24 heads
before final filtering, yielding only 7.333 matching SSD postings/query
in Broad, rather than selecting 24 matching heads and supplementing own
results. Auxiliary posting provided no recall improvement in this bounded
restored-native experiment; no new collector, underfill continuation or
threshold tuning was added to recover that loss. The original reference
matches selected-head IDs/distances for 32 queries and final IDs/distances/
SSD work for Broad, sparse and unfiltered 32-query cases. Full raw visited
traces are unavailable from the original executable, so broader raw-trace
equivalence is not claimed. This remains a read-only static experiment,
without production promotion or changes to old evidence, plots or the
stopped sweep. Reports and baseline authentication are under
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/postfilter_native_predicate_20260918/`.

The user subsequently clarified that the intended conservative baseline is
**H1 post-filter**: ordinary navigation remains unpruned, while H1 result
admission accepts matching heads. Removing the H1 result predicate above
went too far. The nearest-24-then-filter experiment is therefore not the
accepted correction, and its lower latency is not a successful replacement.
The corrective successor below restores H1 result-level filtering without
reintroducing the supplementary own heap or eager own/liveness/alias
qualification. Its clean graph, predicate-bit and posting controls all
share the same H1 admission predicate; final exact filtering stays native.

**Clean H1 post-filter correction.**
`Tools/benchmarks/hierarchical_shortcut_native/h1_postfilter_native_predicate_20260918/`
restores authenticated `SearchIndexWithResultFilter` output semantics:
matching heads enter the H1 result collector, while ordinary nonmatching
nodes remain traversable under the native distance frontier and budget.
There is no supplementary own heap. All three modes use native support
membership for H1 admission, including a matching head with an empty SSD
posting; native selected-head translation and exact final filtering handle
its own record. This differs deliberately from the earlier `evaluatePosting`
predicate, which required a nonempty posting. No own OR, alias-marker,
version-map or posting-validity work is added to initialize the bit.

| Scene / mode | Mean ms | QPS at mean | Recall@10 |
| --- | ---: | ---: | ---: |
| Broad clean H1 post-filter | 0.769049 | 1300 | 0.9153 |
| Broad plus predicate bit | 0.877312 | 1140 | 0.9153 |
| Broad plus bit and signed posting | 0.918764 | 1088 | 0.9153 |
| Sparse clean H1 post-filter | 0.771574 | 1296 | 0.2033 |
| Sparse plus bit and signed posting | 1.692806 | 591 | 0.9929 |

These are fresh same-core `nprobe=24` measurements, each with 1,000 warmup
and 1,000 measured queries and two reversed-order repetitions. Broad
graph/bit-only outputs, navigation and SSD traces are identical; the
remaining bit tax is 0.108262 ms (14.08%), not a speed improvement.
Broad returns 24 matching H1 heads and reads 23.979 SSD postings/query.
Sparse matching heads and SSD postings rise from 0.750 to 17.423/query
with auxiliary navigation, still subject to the native budget. This is a
higher-latency, higher-recall result, not an equal-recall speedup; sparse
timings vary substantially between the two repetitions.

The nearest-24-unmatched regression returns farther matching heads,
preventing another post-hoc-filter restoration. An actual empty-posting
head returns its own exact record through the native selected-head path
in all three modes without a supplemental heap. Original result-filter
H1 IDs/distances match Broad and sparse 32-query references; unfiltered
output/SSD behavior matches the original 32-query reference. Same-core
fixtures additionally check navigation, visited, bit reuse, full CSR and
zero warmed backend allocations. The bit remains support-summary
may-match, not exact record selectivity. The scope is static read-only;
numeric/DNF behavior is source-reviewed rather than measured here.
No production promotion, old-evidence rewrite, plot update or sweep
restart occurred. Reports and manifests are under
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/h1_postfilter_native_predicate_20260918/`.

**Predicate-order discrepancy found after this comparison.** Although the
restored H1 outputs match, the experimental `admitFilteredResult` still
contains a distance-to-output-worst early return before the predicate.
The authenticated pre-supplier helper at
`matched_baseline_20260917/original/source/AnnService/src/Core/BKT/BKTIndex.cpp:552-570`
has no such shortcut: after result dedup it calls the predicate directly.
Its ordinary fresh-neighbor path calls that helper before frontier insertion.
Output parity therefore did not establish original predicate-work parity.

The recorded 221.616 actual result-predicate evaluations/query describe
the experimental distance-shortcut control, not the authenticated original
H1 post-filter. The bit variant performs 1,943.817 first evaluations plus
23.640 remaining result evaluations, and reuses the bit for 197.976 result
checks. These counts are valid but cannot justify attributing the 0.108262 ms
difference to bit storage or to an inherent eightfold increase over the
original algorithm. The canonical cleanup below removes the residual
shortcut consistently from both controls and compares actual predicate
order/counts against the unmodified reference. Tree seeds and ordinary
neighbors are accounted separately; not every visited insertion can
be assumed to reach native result admission. Preserve the earlier times
as measurements of their actual shortcut baseline, not as a native-faithful
cost comparison or evidence of an equal-work bit penalty.

**Historical isolated cleanup, superseded by main integration.** This
milestone used `Tools/benchmarks/hierarchical_shortcut_native/native_postfilter/`.
At that time, `generate.py` applied the authoritative transformation directly
to authenticated original source; generated core copies were not edited.
Only five original core files change, plus the two native helpers `Visited.h`
and `Posting.h`. The complete original BKTree and prefetch code, collapsed
alias admission, CRUD methods and final record handling are retained.
The common hash backend and result-dedup path remain original apart from
unsigned ID encoding that avoids signed overflow; match probing/flagged
rehash are confined to navigation visited storage.

Normal query code no longer contains the distance shortcut, old observation/
Outcome/Decision accounting, duplicate liveness accounting, extra erased
predicate wrapper, tree in-filtering changes or process-global CRUD guards.
Independent proof instrumentation is generated separately and is not linked
into the timed library. The read-only STATIC/BKT scope is enforced by the
benchmark entrypoint, not by disabling unrelated native APIs.

| Broad control, nprobe24 | Mean ms/query | Recall@10 |
| --- | ---: | ---: |
| Original-order H1 post-filter | 0.709805 | 0.9153 |
| Same plus predicate bit and d/e | 0.694566 | 0.9153 |
| Same plus signed posting | 0.700786 | 0.9153 |

Two reversed pairs show no positive bit penalty in this run; they do not
prove zero cost or a general speedup. Graph/bit results, distances and SSD
work agree exactly, with 23.979 SSD postings/query. Original-order predicate
calls are 1,892.887/query, not 221.616. Bit performs 1,967.457: all 1,869.247
ordinary values are reused, while tree initialization adds 74.570 calls,
including 23.640 whose native popped-head predicate later runs again.
These counts are not a CPU attribution.

Source allowlist witnesses, independent original predicate-order comparisons,
native alias/deletion and empty-posting own-head cases, packed storage/UBSan,
full-CSR boundaries and numeric/DNF/unfiltered functional checks are sealed in
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/native_postfilter/20260918_cleanup/`.
The normal library contains the required predicate/signature/supplier work
inside query timing. Historical variants, plots, production files and the
operator-stopped sweep remain untouched and are not active build inputs.

**Main integration and current acceptance campaign.** The independent
generator, duplicate helpers and CMake recipe were subsequently removed
after their exact bytes and paths were archived. The old
`SecondLevelHierarchy.h` upper-query implementation was removed; main
`HierarchyPostingBuilder.h` retains native construction assignment only.
Upper ANN graphs are neither persisted nor loaded for query. H1 and upper
vector/CSR/signature catalogs are built and saved by the main engine.
Legacy graphless H1 layouts must be explicitly materialized into a new
output directory before search; loading never silently rebuilds them or
falls back to an upper graph.

The main static/shared libraries, service, builder and Python binding are
built in a separate output directory to preserve existing `Release` files.
Main integration evidence and archived deleted sources are in
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/main_postfilter_integration_20260919/`.

The full campaign is registered separately in
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/main_postfilter_full_20260919/`.
It uses all 10,000 held-out queries for unfilter, Broad/medium/extreme
categorical, numeric and mixed-DNF cases; 11 native nprobe values and two
reversed repetitions compare the same main binary with auxiliary posting
disabled/enabled. Separate compiled diagnostics are not used for throughput.
The complete curve and per-scenario acceptance remain pending until that
campaign finishes; 32-query functional replays do not establish performance.

One-billion projections distinguish UInt8 production configuration from the
Float SIFT1M campaign. Query-local upper state now scales with touched nodes,
not whole catalog size, but recursive owner fanout and full CSR row lengths
remain relevant work bounds. Removing dense query-state initialization alone
does not prove acceptable 1B latency or recall. The quantitative assumptions,
payload lower bounds and unresolved limits are recorded in
`parent-one-billion-projection.json` beside the campaign registration.

Frozen original NaviX measurements and provenance are
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/navix_posting_20260917/completion_report.json`
and `summary_with_postfilter_controls.json`; `report.json` records source
identities, implementation details and reproduction commands. Run only the
bounded fixtures, from the repository root with its existing isolated build:

```bash
../datasets/sift1m_zipf200_sparse193_numeric/toolchains/navix_posting_20260917/harness/native-tests
python3 -m unittest discover \
  -s Tools/benchmarks/hierarchical_shortcut_native/navix_posting_20260917 \
  -p test_protocol.py
```

For a controlled comparison, use a separate matched summary and protocol:

```bash
Rscript Tools/benchmarks/plot_sift1m_matched_curve.R \
  <matched-summary.json> <protocol.json> <output-prefix>
```

This renderer accepts no historical input. It requires four separately labeled
cases: `h1_original` (authenticated pre-supplier core), `h1` (current core with
supplier off), `h3` (native hierarchy), and `supplier`. Original and current
cores must have distinct verified fingerprints and share the ordinary timed
benchmark body. Each row must match the protocol's index, harness and protocol
identities and use its expected core identity. The common protocol covers the
input/workload definitions, buffered IO, native search budgets, query window,
warmup, thread/NUMA settings, repetitions and declared nprobe array. Index
views may change private loader paths but must share actual index-data bytes.
Neither an implementation change nor a different I/O policy may be hidden
inside a same-run reference.

Every included scenario must have all four cases at every declared nprobe,
with all declared ordinary repetitions. QPS is `1000 / mean latency_ms`, not
the arithmetic mean of QPS; error bars are observed min/max, not confidence
intervals. Curves follow nprobe order without fitting. An explicit final
`--partial-scenarios` permits completed scenario subsets only; missing panels
say pending and never contain historical substitutes. Single-point diagnostics
are not accepted as curve inputs. PNG/PDF, exact plotted coordinates and protocol
provenance are emitted together. Preserve earlier figures in an archive and
publish only successfully rendered, complete artifacts.

To overlay existing cost-arbitration measurements without running a new sweep:

```bash
Rscript Tools/benchmarks/plot_sift1m_matched_curve.R \
  <matched-summary.json> <protocol.json> <output-prefix> \
  --partial-scenarios --points <cost-points.json>
```

The optional points file has `label`, `common`, `provenance` and `rows`.
`common` records the index fingerprint, ordinary timed-body fingerprint and
native IO/search/query-window/thread/repetition settings; these must match the
curve evidence. `rows` contains the measured scenario, case, nprobe, recall,
latency, latency repetitions and QPS. Cases are `auto`, `graph`, `estimate_only`,
`original` and `h3`; each included scenario requires auto and graph controls.
`provenance` retains the original report/summary/runtime/config identities.
Use only source-hash-verified measured rows, with nprobe taken from the active
native array rather than its scalar default.

These are independent scatter points and repetition ranges, never joined to
each other or to the frozen curves. Fresh H1/H3 controls remain separate from
older curve points. CSV exports distinguish `measurement_kind`, `source_case`
and `source_file`; point runtime identities live in the overlay provenance,
not the old curve's core-fingerprint columns. Missing measurements are not
filled, and the base curve completeness checks remain enforced. The existing
v2 overlay input is preserved in the local experiment's
`plot_overlay/points.json`.

Single-load native sweeps declare
`"sweep_execution": "single_load_nprobe_array"` on every summary row.
The figure and provenance distinguish these runs from the older per-point
process protocol. Do not splice the two protocols into one current sweep;
mixed or partial execution declarations are rejected. This metadata describes
the benchmark evidence, not an instruction to load or search an index.

</details>

#### **Optional native benchmark nprobe sweeps**

The native `spannaclbench` accepts an array in the same search INI.
Keep the complete current `[SearchSSDIndex]` section above and add:

```ini
[SearchSweep]
NProbe=[16,24,32,48,62,80,96,128,192,256,384]
```

Pass this file with `--search-ini` (or as an active `--search-sweep-ini`).
`LoadAll` runs once, then the native loop sets `InternalResultNum` for each
array entry in order. Every entry has separate warmup, result buffers, counters
and timed queries, and emits a JSON row with `nprobe`, `index_load_count` and
`sweep_execution`. Brackets are optional. Values must be distinct positive
integers at least `--topk`; malformed and empty arrays fail before index loading.
Without `[SearchSweep]`, existing scalar INIs retain their behavior. When both
INI command-line options are used, the array belongs to the active sweep INI,
not the base INI. This benchmark section is not a new core search parameter.

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

  
  
