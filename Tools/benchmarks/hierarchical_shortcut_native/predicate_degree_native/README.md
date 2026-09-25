# Predicate-valid physical H1 degree: isolated native rerun

This is a separate implementation and dataset, not a relabeling of
`startup_native` or `startup_nprobe`. The user explicitly authorizes
predicate-guided supplementation in this isolated experiment; production rules
and every previous source, binary, INI, index and result remain unchanged.

## Corrected degree

`Connectivity` in `Supplier.h` examines the **entire native ordinary adjacency
prefix before any visited, spatial-bound or checked-budget exit**. It counts
distinct in-range, nonself H1 IDs whose unchanged native qualification is
posting-admissible OR own-point-admissible. An ID remains part of degree when
already query-visited. Qualification is read-only and independent of heap
competition; no candidate distance is required to measure degree.

The non-cross native BKT loop stops at its first negative graph entry. A final
value below-1 is a collapsed-group tree marker, not a neighbor ID. Its aliases
are handled by the unchanged native collapsed-result path, not invented as
ordinary adjacency edges. Consequently32 physical slots do not imply32 edges.
Duplicate IDs, self IDs and out-of-range IDs do not contribute. The actual
`FlatHeadIndex/graph.bin` is audited independently, row by row.

For an actual helper call, connectivity is the union of the ordinary IDs and
distinct eligible IDs supplied by selected H2 rows during that call, including
already query-visited H1s. Ordinary/supplied duplicates and self never inflate
degree. Visited still suppresses repeated distance/enqueue work. Degree16 only
decides whether to select another row/ascend; every selected row finishes.
Previously completed posting rows remain unselectable and are not rescanned;
this does not invent persistent H1 overlay edges or count unexamined ancestor
descendants as connectivity.

## Startup and native continuation

The original initial BKT stage runs once. When its H1 queue has fewer than16
entries, retain the previous reachable-anchor rule: nearest already-scored
initial BKT candidate, or first reachable candidate if none was scored.
Inspect that anchor's complete ordinary adjacency regardless of visited/budget.
Within remaining native MaxCheck, offer its genuine ordinary neighbors through
the same native expansion/visited/qualification/distance/heap flow first.
Invoke the same supplier only if the anchor's **predicate-valid ordinary
degree is below16** and native budget remains. An adequately connected anchor
does not call the supplier merely because few entries are fresh or queued.

Offering the anchor's ordinary edges is the necessary startup counterpart of
this correction: refusing inappropriate posting fallback must not ignore
reachable ordinary edges that can seed the original queue. This is not a new
search, a global scan, or a final-result retry. The original H1 workspace and
frontier continue. Native MaxCheck2048 is unchanged; selected H2 rows finish
before checking that boundary and their overshoot is reported.

## Preserved semantics and overhead

No custom distance/member/row/batch ceilings. Conservative native signatures
precede invalid upper-row scoring/scanning. Native posting OR exact own
eligibility precedes ordinary and CSR-child distances; internal BKT routing
centers remain explicitly scored structural exceptions. All scored canonical
H1 identities, including upper representatives, retain own/posting admission.

H3 completion still means complete H2-member enumeration, with cached
query-ranked child choices. It does not force a complete descendant subtree:
selected H2 rows finish individually and H1 can resume between them.
No persistent upper navigation frontier or top-graph search is introduced.
The top graph object remains loaded as vector backing; memory is not removed.

Common callback, qualification/cache, all-evaluated own-point and posting-heap
overhead is **not optimized away**. Complete degree inspection itself adds
qualification checks for visited adjacency. This is a semantic correction,
not an arbitrary unfiltered fast path or broad performance optimization.

## Evidence and fixed experiment

`SupplierTests.cpp` covers32 eligible neighbors with0/20/32 visited, native
connected startup with a deficient initial queue, filtered degree below16,
duplicate/self/invalid/short/sentinel rows, visited supplied connectivity,
full rows beyond3200 distances/2048 members, no row rescans, conservative
signatures, cached H3 choices, sparse startup, all-rejected termination, native
continuation and count-on/off equality.

Every captured native query includes `connectivity_audits`: phase, H1 node,
actual row width/end marker, ordinary IDs and qualification flags, before/after
connectivity, distinct supplied IDs, helper calls, fresh work, visited skips and
native checked counts. `supplyConn*` are connectivity counters.
`supplyRawBefore`, `supplyEffectiveBefore/After` and `supplyQualified` retain
their **fresh-work** meanings and are not the new trigger. In legacy-shaped
`degree_audits`, field1 is now true predicate-valid degree; field0 and field3
remain fresh-work counts. New connectivity evidence disambiguates them.

The runner reconciles every degree frame with the immutable physical graph and
native qualification evidence. For unfilter, every valid own point qualifies,
so eligible degree equals independently reconstructed physical degree. Every
actual helper call must have degree<16; no qualifying32-neighbor row may call
it. Summaries include actual trigger-node/physical-degree histograms rather
than assuming unfiltered helper calls are zero.

The common grid is `16,24,32,48,62,80,96,128,192,256,384`, all six original
scenarios, three modes h1/h3/supplier:198 points, two rotating/reversed ordinary
repetitions each. First1000 measured queries,1000 warmups,1000 untimed captures,
one query thread, NUMA CPU/memory2, O_DIRECT, page15, ResultNum10,
MaxCheck2048, HierarchyMaxCheck512 and ratio0.666666. Only InternalResultNum
varies within each mode. Explicit native INIs and the schedule are materialized
before execution. The variable-capacity guard fixes from `startup_nprobe` are
retained unchanged.

Original H1/H3 are rerun in this matrix, never spliced from prior timing.
Thirty-six frozen diagnostics cover16/24/384, and36 eight-query profiled
fixtures cover16/384; profile timing is never used for curves. Corrected
supplier results need not match the defective predecessor. Every supplier
summary row has `supplier_degree_semantics: "predicate_valid_neighbors"`.
Threshold reports choose only observed points meeting90%/95%, with explicit
unreached thresholds and no interpolation.

New runtime and result directories, under the original SIFT1M dataset root:
`toolchains/h1_predicate_degree_nprobe_20260916/` and
`comparisons/h1_predicate_degree_nprobe_20260916/`.
Historical curves have different IO/budgets/cohorts; do not splice them.
R plotting code, GettingStart, PNG/PDF and `predicate-degree-plot` are out of
scope and untouched.

From `mocheng`, with fresh output/toolchain destinations:

```sh
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_native/prepare.py
cmake -S datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_nprobe_20260916/source -B datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_nprobe_20260916/build -DCMAKE_BUILD_TYPE=Release -DSPDK=OFF -DROCKSDB=OFF
cmake --build datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_nprobe_20260916/build --target spannaclbench --parallel 2
cmake -S SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_native -B datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_nprobe_20260916/tests -DSPANN_ROOT="$PWD/datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_nprobe_20260916/source"
cmake --build datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_nprobe_20260916/tests --parallel 2
datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_nprobe_20260916/tests/suppliertests > datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_predicate_degree_nprobe_20260916/supplier-tests.log
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_native/run.py
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_native/verify.py --results datasets/sift1m_zipf200_sparse193_numeric/comparisons/h1_predicate_degree_nprobe_20260916
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/predicate_degree_native/degree_report.py --results datasets/sift1m_zipf200_sparse193_numeric/comparisons/h1_predicate_degree_nprobe_20260916
```

## Completed corrected curves

All198 points,396 ordinary repetitions and72 diagnostic processes are complete
and independently reconciled. Every one of the66 supplier summary rows carries
`supplier_degree_semantics: "predicate_valid_neighbors"`. All14540 protected
prior files remain unchanged. The canonical summary SHA256 is
`a9dc898ff3ad9db8a5258c1c4250ec75a5589819496653bd5fb18878c770cfd5`.

`summary.json` / `summary.csv` contain all measured curve coordinates and QPS
ranges. `thresholds.json` / `report.md` contain observed threshold selections.
`physical_degree.json`, `unfilter_degree_audit.json` and `degree_report.md`
substantiate real low-degree triggers, with node IDs and actual adjacency.
`independent_reconciliation.json` records the completed raw-result audit.

The graph has46697 of160091 nodes with physical degree below16;32 is a row
capacity, not its minimum degree. At unfilter nprobe24, all37136 observed
degree32 frames had zero fallback calls, including2267 frames with at least20
visited-neighbor skips. The20279 genuine calls across1000 queries all came
from physical degrees3 through15, not from lost fresh degree.

Unfilter nprobe24: original H1 is90.73% recall,0.968290ms,1032.748QPS;
corrected S is90.79%,2.332270ms,428.767QPS. S averages20.279 helper calls,
1119.359 CSR-member inspections,36.088 parent and448.375 child distance calls
per query. Correct trigger semantics therefore do **not** establish the
desired near-H1 latency: this implementation remains2.409x slower, with common
callback/admission overhead retained and complete degree inspection added.

Two driver SIGTERM interruptions are documented and preserved. Completed
native records were reused unchanged; the one native attempt lacking exit
evidence was preserved and rerun with the full prescribed warmup. No binary,
policy, INI, grid, or budget changed between repetitions or resumes. No
interrupted attempt is included as a successful curve observation.
