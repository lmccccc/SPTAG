# SPTAG: A library for fast approximate nearest neighbor search

[![MIT licensed](https://img.shields.io/badge/license-MIT-yellow.svg)](https://github.com/Microsoft/SPTAG/blob/master/LICENSE)
[![Build status](https://sysdnn.visualstudio.com/SPTAG/_apis/build/status/SPTAG-GITHUB)](https://sysdnn.visualstudio.com/SPTAG/_build/latest?definitionId=2)

## **SPTAG**
 SPTAG (Space Partition Tree And Graph) is a library for large scale vector approximate nearest neighbor search scenario released by [Microsoft Research (MSR)](https://www.msra.cn/) and [Microsoft Bing](http://bing.com). 

 <p align="center">
 <img src="docs/img/sptag.png" alt="architecture" width="500"/>
 </p>

## What's NEW
* **Multi-Tenant SPANN with Per-Tenant Isolation** — see [Multi-Tenant Features](#multi-tenant-features) below
* Result Iterator with Relaxed Monotonicity Signal Support
* New Research Paper [SPFresh: Incremental In-Place Update for Billion-Scale Vector Search](https://dl.acm.org/doi/10.1145/3600006.3613166) - _published in SOSP 2023_
* New Research Paper [VBASE: Unifying Online Vector Similarity Search and Relational Queries via Relaxed Monotonicity](https://www.usenix.org/system/files/osdi23-zhang-qianxi_1.pdf) - _published in OSDI 2023_

## **Introduction**
 
This library assumes that the samples are represented as vectors and that the vectors can be compared by L2 distances or cosine distances. 
Vectors returned for a query vector are the vectors that have smallest L2 distance or cosine distances with the query vector. 

SPTAG provides two methods: kd-tree and relative neighborhood graph (SPTAG-KDT) 
and balanced k-means tree and relative neighborhood graph (SPTAG-BKT).
SPTAG-KDT is advantageous in index building cost, and SPTAG-BKT is advantageous in search accuracy in very high-dimensional data.



## **How it works**

SPTAG is inspired by the NGS approach [[WangL12](#References)]. It contains two basic modules: index builder and searcher. 
The RNG is built on the k-nearest neighborhood graph [[WangWZTG12](#References), [WangWJLZZH14](#References)] 
for boosting the connectivity. Balanced k-means trees are used to replace kd-trees to avoid the inaccurate distance bound estimation in kd-trees for very high-dimensional vectors.
The search begins with the search in the space partition trees for 
finding several seeds to start the search in the RNG. 
The searches in the trees and the graph are iteratively conducted. 

 ## **Highlights**
  * Fresh update: Support online vector deletion and insertion
  * Distributed serving: Search over multiple machines

 ## **Build**

### **Requirements**

* swig >= 4.0.2
* cmake >= 3.12.0
* boost >= 1.67.0

### **Fast clone**

```
set GIT_LFS_SKIP_SMUDGE=1
git clone --recurse-submodules https://github.com/microsoft/SPTAG

OR

git config --global filter.lfs.smudge "git-lfs smudge --skip -- %f"
git config --global filter.lfs.process "git-lfs filter-process --skip"
```

### **Install**

> For Linux:
> Compile SPDK
```bash
cd ThirdParty/spdk
./scripts/pkgdep.sh
CC=gcc-9 ./configure
CC=gcc-9 make -j
```

> Compile isal-l_crypto
```bash
cd ThirdParty/isal-l_crypto
./autogen.sh
./configure
make -j
```

> Build RocksDB
```bash
mkdir build && cd build
cmake -DUSE_RTTI=1 -DWITH_JEMALLOC=1 -DWITH_SNAPPY=1 -DCMAKE_C_COMPILER=gcc-7 -DCMAKE_CXX_COMPILER=g++-7 -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_FLAGS="-fPIC" ..
make -j
sudo make install
```

> Build SPTAG
```bash
mkdir build
cd build && cmake -DSPDK=OFF -DROCKSDB=OFF .. && make
```
It will generate a Release folder in the code directory which contains all the build targets.

> For Windows:
```bash
mkdir build
cd build && cmake -A x64 -DSPDK=OFF -DROCKSDB=OFF ..
```
It will generate a SPTAGLib.sln in the build directory. 
Compiling the ALL_BUILD project in the Visual Studio (at least 2019) will generate a Release directory which contains all the build targets.

For detailed instructions on installing Windows binaries, please see [here](docs/WindowsInstallation.md)

> Using Docker:
```bash
docker build -t sptag .
```
Will build a docker container with binaries in `/app/Release/`.

### **Verify** 

Run the SPTAGTest (or Test.exe) in the Release folder to verify all the tests have passed.

### **Usage**

The detailed usage can be found in [Get started](docs/GettingStart.md). There is also an end-to-end tutorial for building vector search online service using Python Wrapper in [Python Tutorial](docs/Tutorial.ipynb).
The detailed parameters tunning can be found in [Parameters](docs/Parameters.md).

## **References**
Please cite SPTAG in your publications if it helps your research:
```
@inproceedings{xu2023spfresh,
  title={SPFresh: Incremental In-Place Update for Billion-Scale Vector Search},
  author={Xu, Yuming and Liang, Hengyu and Li, Jin and Xu, Shuotao and Chen, Qi and Zhang, Qianxi and Li, Cheng and Yang, Ziyue and Yang, Fan and Yang, Yuqing and others},
  booktitle={Proceedings of the 29th Symposium on Operating Systems Principles},
  pages={545--561},
  year={2023}
}

@inproceedings{zhang2023vbase,
  title={$\{$VBASE$\}$: Unifying Online Vector Similarity Search and Relational Queries via Relaxed Monotonicity},
  author={Zhang, Qianxi and Xu, Shuotao and Chen, Qi and Sui, Guoxin and Xie, Jiadong and Cai, Zhizhen and Chen, Yaoqi and He, Yinxuan and Yang, Yuqing and Yang, Fan and others},
  booktitle={17th USENIX Symposium on Operating Systems Design and Implementation (OSDI 23)},
  year={2023}
}

@inproceedings{ChenW21,
  author = {Qi Chen and 
            Bing Zhao and 
            Haidong Wang and 
            Mingqin Li and 
            Chuanjie Liu and 
            Zengzhong Li and 
            Mao Yang and 
            Jingdong Wang},
  title = {SPANN: Highly-efficient Billion-scale Approximate Nearest Neighbor Search},
  booktitle = {35th Conference on Neural Information Processing Systems (NeurIPS 2021)},
  year = {2021}
}

@manual{ChenW18,
  author    = {Qi Chen and
               Haidong Wang and
               Mingqin Li and 
               Gang Ren and
               Scarlett Li and
               Jeffery Zhu and
               Jason Li and
               Chuanjie Liu and
               Lintao Zhang and
               Jingdong Wang},
  title     = {SPTAG: A library for fast approximate nearest neighbor search},
  url       = {https://github.com/Microsoft/SPTAG},
  year      = {2018}
}

@inproceedings{WangL12,
  author    = {Jingdong Wang and
               Shipeng Li},
  title     = {Query-driven iterated neighborhood graph search for large scale indexing},
  booktitle = {ACM Multimedia 2012},
  pages     = {179--188},
  year      = {2012}
}

@inproceedings{WangWZTGL12,
  author    = {Jing Wang and
               Jingdong Wang and
               Gang Zeng and
               Zhuowen Tu and
               Rui Gan and
               Shipeng Li},
  title     = {Scalable k-NN graph construction for visual descriptors},
  booktitle = {CVPR 2012},
  pages     = {1106--1113},
  year      = {2012}
}

@article{WangWJLZZH14,
  author    = {Jingdong Wang and
               Naiyan Wang and
               You Jia and
               Jian Li and
               Gang Zeng and
               Hongbin Zha and
               Xian{-}Sheng Hua},
  title     = {Trinary-Projection Trees for Approximate Nearest Neighbor Search},
  journal   = {{IEEE} Trans. Pattern Anal. Mach. Intell.},
  volume    = {36},
  number    = {2},
  pages     = {388--403},
  year      = {2014
}
```

## **Contribute**

This project welcomes contributions and suggestions from all the users.

We use [GitHub issues](https://github.com/Microsoft/SPTAG/issues) for tracking suggestions and bugs.

## **Multi-Tenant Features**

This fork adds production-grade multi-tenant support on top of SPANN:

### Architecture
- **Per-tenant SPANN index**: each tenant has independent HeadIndex + posting files, query isolation guaranteed
- **LRU HeadIndex cache** with configurable memory budget (`SetHeadIndexCacheLimit`)
- **SharedAIOPool**: global AIO context pool eliminates `io_destroy` overhead on eviction (930ms → 2ms)
- **Dirty flag checkpoint**: `ShutDown` only writes back when data was modified (`Put/Delete/Merge`)
- **Lazy-load**: tenants loaded on first query, evicted under memory pressure

### ACL/Tag Filtered Search
- **STM1 static metadata**: static records store `[VID | tags[N] | vector]`;
  flat ACL checks are exact and scan only each posting's pure prefix.
- **Hybrid STM1 v3 posting**: let `O` be the original vector-distance
  pure+tail posting and `H` the hybrid-distance pure prefix. The single
  posting stores `H | O`; each region is internally unique, while cross-region
  overlap is intentional. Filtered pure routing scans only `H`; unfiltered and
  exact-filter fallback routing read and scan only the self-contained `O`
  suffix. Older constrained STM1 versions fail closed and require rebuilding.
- **Hybrid BKT navigation**: the original degree-32 vector graph is unchanged;
  degree-16 hybrid edges use the generation- and content-bound
  `head_cross_edges.bin` runtime suffix rather than a second graph store.
  Hybrid mode requires `ExcludeHead=true` so global head VIDs remain
  available for deterministic suffix validation after reload.
- **Original-order tag schema**: `[Tags] ColumnTypes` lists every column as
  `categorical` or `numeric`, with no categorical-prefix requirement.
  Library setters use `BuildSSDIndex.ColumnTypes`. Flat value matching examines
  categorical columns only; column-qualified DNF uses original indices.
  Existing prefix-layout metadata remains readable without reinterpreting old rows.
- **Automatic posting prefilter**: validated metadata supplies independent
  member-OR signatures for `H` and `O`, so each route is
  pruned against the records it actually scans. Flat ACL uses column-agnostic
  masks because categorical domains may overlap or appear in any order.
  Column-qualified DNF uses per-column masks; numeric columns use independent 256-bucket quantized
  masks. Both are conservative I/O hints; exact per-vector DNF checking
  remains authoritative.
- **Spatial hierarchical routing**: one global H1..H5 hierarchy uses a top-level
  graph and signed CSR descent. Attribute signatures provide conservative
  filtering hints, not attribute-owned partitions; sparse predicates retain
  ordinary support/H/O fallback.
- **No unsupported predicate fallback**: static STM1 rejects arbitrary metadata
  callbacks and DNF predicates rather than returning approximate filter results.

### API
```python
from sptag import SPTAG

# Create manager
mgr = SPTAG.CreateTenantIndexManager(128, "SPANN", "Float")

# Build with tags
mgr.BuildFromDataWithTags(vectors, metadata, n, tags, num_tags_per_vec, True, False)
mgr.SaveAll("/path/to/index")

# Load and search
mgr.LoadAll("/path/to/index")
result = mgr.Search(query, tenant_id, topk)                              # unfiltered
result = mgr.SearchWithACL(query, tenant_id, topk, query_tags, num_tags) # filtered

# Cache control
mgr.SetHeadIndexCacheLimit(64 * 1024 * 1024)  # 64MB HeadIndex budget
```

### Historical performance note

The older multi-tenant numbers previously listed here used a different
workload and storage path. They are not a performance contract for the current
native-INI STM1/static or in-posting-quantized configurations. Reproduce
current numbers with the committed INI and native benchmark commands above.

### Global Spatial Hierarchy and Filter Metadata

Current attribute indexes use one global spatial hierarchy. Per-tag head
selection, attribute pivot planning and tag-to-bundle routing have been removed.
Use the native INI workflow in [docs/Experiment_Workflow.md](docs/Experiment_Workflow.md).
Old organization settings fail explicitly; see the migration note in
[Tools/benchmarks/README.md](Tools/benchmarks/README.md).

Categorical/numeric records, exact filters, support assignments, H/O regions
and signed spatial hierarchy CSR remain supported. Generic physical bundles
and optional cross-edges/tails remain for geometry and upstream CRUD.

#### Spatial enhancement layers

For good **unfiltered** recall/QPS on a physical multi-bundle
index, build the cross-graph stitch plus H1 unfilter-tail replicas. U_extra is
optional and defaults OFF in canonical SPACEV configs after ablation showed no
recall gain once H1 tails are enabled. Without cross-graph/tail, unfilter
degrades to a bare per-node fan-out across the ACL bundle nodes. See
**AGENTS.md → "Unified Spatial Query Pipeline"** and
**[docs/MultiTenant_SIFT1M_UnfilterTail.md](docs/MultiTenant_SIFT1M_UnfilterTail.md)**.

| Layer | Enable (build) | Enable (search) |
| ----- | -------------- | --------------- |
| ① cross-graph | post-build: `Release/augmentheadgraph -d <index>/tenant_0/HeadIndex -k 15 -m 10 -t N -w true` | (auto) |
| ② U_extra (~10% extra spatial heads; optional) | `[SelectHead] DualPoolAugment=1` `DualPoolExtraRatio=0.1` | same spatial policy for all predicates |
| ③ legacy tail (K nearest-head tail copies/vector) | `[BuildSSDIndex] TailReplicaCount=K` `UnfilterTailBufferLength=P` (P=max extra physical tail pages beyond pure pages) | common `SearchPostingPageLimit` prefix, independent of predicates |

#### Native `.ini` build config (recommended — single source of truth)

Rather than exporting the `SPTAG_*` knobs above by hand, the attribute-aware build
is driven by a **native SPANN sectioned `.ini`** (read by `Helper::IniReader`, the
same loader the classic `IndexBuilder` uses). All build parameters — standard SPANN
posting knobs *and* the multi-tenant/unfilter extensions — live in one committed
file, so nothing is lost when `/tmp` is wiped:

```bash
# 3M config = Script_AE/iniFile/build_spann_attr_spacev_opq25.ini
# 1B config = Tools/benchmarks/build_spann_attr_spacev1b_opq25.ini
Tools/benchmarks/run_spann_attr_build.sh [config.ini]   # launcher derives paths from the ini
#   internally: Release/spannbuilder -c <config.ini>  +  post-build augmentheadgraph
```

- `[BuildSSDIndex]` (`Storage`, `ReplicaCount`, `PostingQuantizer`/`PostingQuantM`/
  `PostingQuantizerFile`/`PipePQPivotsFile`, `FullVectorFile`, `RerankL`,
  `StartFileSizeGB`/`MaxFileSizeGB`)
  flows through the native `SetSSDBuildParam` path.
- `[SelectHead]`/`[BuildHead]` carry spatial hierarchy and graph settings.
  `[Tags] ColumnTypes=categorical,numeric` defines every original column;
  types may be interleaved and width is derived. `LimitedTagColumn` is an
  absolute categorical column index, not a partition identifier. `[SearchSSDIndex]`
  carries persisted query behavior such as `InternalResultNum`
  (internally `SearchInternalResultNum`), `MaxCheck`, and
  `SearchPostingPageLimit`. Exact attributes admit results and choose H/O
  membership only; posting signatures do not prune reads.
  Unfiltered-only tail/page controls and `SPTAG_OPQ_PREFILTER` are removed
  and rejected even when explicitly disabled.
  Retired partition/EST settings and duplicate `[MultiTenant]` aliases are
  rejected; see the [reduced configuration](Tools/benchmarks/README.md#reduced-filtering-configuration).
- Comments MUST start with `;`; an explicit CLI flag overrides any ini value.

See **AGENTS.md → "Build Config — Native `.ini`"** for the full key→engine mapping.

#### End-to-end experiment workflow

For the full reproducible pipeline — generate attributes → derived builder inputs
→ groundtruth → build index → query/benchmark — see
**[docs/Experiment_Workflow.md](docs/Experiment_Workflow.md)** (worked example:
SPACEV-1B).

#### In-posting quantization + deep-queue rerank

Postings can store a compact **RaBitQ/OPQ code** per vector instead of the full
vector (~4× fewer bytes/scan); the top-`L` survivors are exact-reranked by cold
O_DIRECT reads from the full-precision base, batched through a **deep-queue libaio**
reader (one `io_submit` for all `L`, ~12µs/read). Enable at build via
`[BuildSSDIndex] PostingQuantizer=OPQ|RaBitQ|PipePQ` + `PostingQuantM` +
`FullVectorFile` + `RerankL`. PipePQ additionally uses a native PipeANN
`PostingQuantizerFile=*_pq_compressed.bin` and
`PipePQPivotsFile=*_pq_pivots.bin`. Keep benchmark build/search parameters in
the native `.ini`; legacy `SPTAG_*` experiment toggles are not part of the
reproducible demo contract. See
**AGENTS.md → "In-posting Quantization + Deep-queue Rerank"**.

### Build
```bash
mkdir build && cd build && cmake ..
bash rebuild_and_editable_install.sh  # builds + pip install -e .
```

## **License**
The entire codebase is under [MIT license](https://github.com/Microsoft/SPTAG/blob/master/LICENSE)
