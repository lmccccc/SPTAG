# Certified native default admission: bounded SIFT1M experiment

The predicate-free experiment now uses the original public BKT search and
SPANN selected-head/own-point result handling. It does not preserve the old
prototype's all-evaluated posting-only head selection or its 17-head-query /
one-final-query difference. No result is rewritten to match the baseline.

## Proof and scope

An empty exact predicate alone is insufficient. At native index load, the
existing posting model records one Boolean and the native vector count:
every physical H1 has a valid canonical VID, an existing native head, and a
live own point according to the native version map. This is usable only for
an existing **immutable** limited-tag generation, unchanged head/vector
domains, zero native H1 deletions, actual empty DNF/tags, no primary bypass,
and native nprobe >= topk. Mutable or invalid/deleted-own generations retain
v2's filtered interfaces. No per-head table, version lock framework, or
distance wrapper is added. Native `Deleted` is checked, not inferred from
the deletion counter (`SetVersion(0xfe)` can leave that counter unchanged).

With this certificate every genuine ordinary neighbor is own-admissible,
irrespective of whether it has a nonempty posting: e=d, including visited
neighbors. Thus the unchanged ratio test cannot trigger for any ratio <=1.
Short, empty, odd, duplicate-containing and collapsed rows do not require a
duplicate eligibility walk. Ordinary certified calls install no result,
traversal, own-notification or neighbor-expansion callback. Untimed capture
can observe degrees with constant eligibility and is checked against ordinary
IDs, distances, native stopping and actual admission flags.

Original SPANN independently translates each selected head to its own point,
even when `CheckValidPosting` rejects that head. Its remaining-head loop also
handles posting cutoff; native version/deletion checks, deduplication, result
heap reversal and SSD merging remain unchanged. This preserves native
approximate selected-head semantics, not an invented exact top-k over every
evaluated head or a new tie policy. Own-only selected points are not discarded.

Filtered requests keep v2's separate native posting/traversal admission and
known-distance own-point helper. d<16 never supplements; otherwise e/d<0.5
triggers with target ceil(0.5*d). Visited does not reduce eligibility.
Signature rejection precedes upper distance/CSR access; predicate admission
precedes H1 distance except structural centers; every selected row finishes;
only native MaxCheck bounds the next row. No upper frontier/restart is added.

## Evidence and reproduction

All artifacts are new:

* Source: this directory, separate from immutable `native_reuse_20260917_v2`.
* Runtime: `datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_native_default_admission_20260917`.
* Results: `datasets/sift1m_zipf200_sparse193_numeric/comparisons/h1_native_default_admission_20260917`.

`prepare.py` authenticates/clones the parent, excluding its Release artifacts.
Build its `source` with CMake Release, SPDK/ROCKSDB OFF, target spannaclbench,
parallelism 2. Build this directory's CMake native tests with
`SPANN_ROOT=<new runtime>/source`, and preserve ctest output as
`<new runtime>/tests.log`. Preparation and phase execution reject existing
outputs rather than resuming obsolete experiments.

`python3 run.py fixtures` executes eight small native processes: H1/supplier
forward/reverse `[16,24,384]` and four filtered eight-query fixtures.
`python3 run.py measure` executes only H1->supplier / supplier->H1 at `[24]`,
1000 warmup and 1000 measured queries, one query thread, NUMA CPU/memory 2,
O_DIRECT/page15, plus separate native profiles. The sole array declaration
is native `[SearchSweep] NProbe`; existing INIs and parser are unchanged.
`python3 report.py` reconciles raw captures, actual native/component load
events, baseline/filtered parity and protected input/source/binary hashes.

The native unit executable covers deletion-certificate rejection, selected
own-only admission without callbacks, actual collapsed aliases/ties and
native MaxCheck boundaries, plus all v2 ratio/signature/full-row tests.
The 1000-query comparison explicitly retains q915 and boundary queries
661/772. Rich work comes from untimed capture, not invented ordinary counters.
Separate profiled navigation time is not an ordinary-time attribution.

This is a bounded unfilter/default-admission result, not a filtered performance
claim or authorization for a full sweep. Historical H3 remains loaded backing
metadata, not an independent upper search frontier. Production routing,
generic benchmark/parser files, original indices and earlier evidence remain
untouched.
