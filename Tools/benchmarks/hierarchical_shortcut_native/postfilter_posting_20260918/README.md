# Native result-only postfilter, fused observation and signed posting

Isolated successor of frozen `postfilter_twohop_20260918`. Production, prior
source/builds/evidence, `_SPTAG.so`, index bytes, plots and `OPERATOR_STOP` stay
unchanged. GettingStart documentation belongs to the parent.

## Native semantics

Native `[SearchSSDIndex]` alone selects `PostFilterPostingMode=graph|observe|posting`
and `PostingActivationRatio=0.01` (fixed, untuned). Zero disables activation.
All NaviX, TwoHop, Arbitration, degree and unknown controls are rejected.

* **graph** is the original result-only BKT ordinary adjacency, including
  nonmatching bridges, original order, visited/distance/queue/results/bounds,
  own points, aliases and per-edge native budget. No ratio qualification/cache.
* **observe** gathers d/e in that same ordinary loop, before visited continuation.
  One qualification request per processed ordinary encounter; eligible visited
  entries count. There is no classifier prescan or second row walk. It never
  inspects hierarchy metadata and needs no PostingModel/catalogs.
* **posting** runs that exact fused ordinary path **first**. Only a fully
  completed row with d>0, e/d<0.01 and remaining native MaxCheck can request one
  signed adjacency action anchored at the current already-expanded H1 head.
  Current row work has already been paid; this does not avoid it.

After the gate, inspect the relevant static owner metadata and check signatures
before representative distance or CSR access. Select nearest eligible H2
(distance then ID), with signed H3 discovery and retained H2 descriptors when
needed. Consume the selected H2 CSR directly into the **same** native frontier,
under the original per-edge MaxCheck boundary, with no arbitrary row cap.
Rejected auxiliary candidates are qualified before visited, so rejection cannot
poison later original graph edges. No useful row means continue native graph;
there is no restart, fallback graph operator, upper ANN queue, benefit ranking,
CostModel, degree quota, refill or independent distance cap.

Static owner inversion occurs once at index load only in posting mode, not per
query; graph/observe do not build it. Query supplier state is lazy until a sparse
gate. Fixed owner arrays and reserved retained descriptors remove the inherited
per-expansion temporary vectors; a retained flag avoids linear duplicate searches.
Immutable posting/exact qualification bytes are reused; mutable deletion and
own heap-gain stay uncached. Own-only matches never become posting-head results.
Empty predicates directly call native `SearchIndex`, even with capture enabled:
no supplier, qualification cache, statistics or additional native query hooks.

## Evidence and interpretation

Untimed captures use the same schema in all modes:

* `.operators.u64`: outer heads, actually processed ordinary row slots, ordinary
  qualification requests. No truncated suffix is counted as physical degree.
* `.native.u64`: original 18 native distance/checked/queue/admission fields.
* `.posting.u64`: sparse activations, selected actions, **zero second-hop loops**,
  **zero classifier prescan entries**, graph members/fresh/predicate/valid/useful,
  posting members/fresh-qualified/predicate/valid/useful.
* `.qualification.u64`: eligible, posting request/evaluation, exact
  request/evaluation and storage growth.
* `.decisions.f64`: query, head, processed d, e, completed, activated,
  checked before/after, signature checks, representative distances, CSR members.
* `.rows.u64`: query, level, row ID, full size, consumed size.

Original native vector prefetch is not predicate classification. This experiment
does not measure cache misses or DRAM traffic; the random-memory hypothesis is
not hardware evidence. Observe minus graph is an observation tax **only with
exact same graph trace**. Posting minus observe is not a pure decomposition when
their trajectories differ. Broad signature rejection near zero provides no
evidence of signature pruning savings.

## Bounded reproduction

Only in fresh destinations, from the workspace root:

```sh
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_posting_20260918/prepare.py --initialize
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_posting_20260918/build.py
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_posting_20260918/run.py
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_posting_20260918/finalize.py
```

The fresh symlink-preserving source clone excludes Release/build caches.
Private headers are installed with `copyfile`; tests/harness include the
installed private headers. Release SPDK/ROCKSDB OFF, `SPTAGLibStatic -j2`.
Compiler scratch stays inside the project.

Native fixtures precede 32-query sparse and unfiltered functional preflights.
Exactly three Broad points (graph/observe/posting), two reverse-order
repetitions = six ordinary processes. If all sparse32 native means are below
the predetermined 10ms runtime guard, exactly graph/posting sparse points with
two reverse repetitions are allowed; this decision never uses recall.
Each ordinary process: 1000 warmup +1000 measured +1000 untimed captures,
offset0, top-k10, native `[24]`, MaxCheck2048, Hierarchy512, initial ratio
.666666, pages15, buffered matched index, CPU+memory NUMA2, one query thread,
one physical load. Shared timed-body SHA:
`9f412c63b71f7310e09180dff0858ccef3957bd20d7a96c34fdb440e3d559d53`.

The same-core proper postfilter oracle is the frozen prior graph run, not the
matched-original unfiltered-H1 control. No new sweep, threshold tuning, curve,
algorithm variant or promotion. Final report and sealed native artifacts live
in `datasets/sift1m_zipf200_sparse193_numeric/comparisons/postfilter_posting_20260918`.
Report speed/recall success or failure honestly, then **STOP IDLE**.
