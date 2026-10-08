# BKT query budget boundaries

## Offline construction exception

`SearchIndexForConstruction` is a separate BKT entrypoint for offline H-posting
placement. It enforces the native index's MaxCheck on fresh graph/tree
navigation admissions, including internal tree centers promoted to navigation.
At the cap it drains already-scored candidates without more edges or tree
refills. This is not a cap on internal routing distance calls, collapsed-alias
checks, RNG distances, or explicit support-set exact scans.
It uses a separate workspace cache; budget changes or grown visited/result
tables cause replacement before reuse. Online queries and graph refinement
never enable this policy.

Both ordinary H construction and layered reconstruction cap candidate capacity
by the label's actual supported-parent count. Small support sets that fit the
candidate request are scored directly. Other labels use bounded ANN, with the
existing exact-support fallback only on an empty result. RNG/no-replica-fill
rules remain unchanged. Build progress reports checked and exact work separately.

## Online query semantics

The project engine is not an unmodified Microsoft/SPTAG binary. Both filtered
and unfiltered H1 search restore the stopping boundaries in upstream commit
`2ac3ebcab562bc81cdb8c7c98b35ea72f2703c3b`: the distance pool remains
`max(MaxCheck / 16, resultNum)`, initial pivots and tree refill are not clipped,
and a live candidate worse than the result bound stops on either the adaptive
distance threshold or `checkedLeaves > MaxCheck`. An adjacency expansion,
initial tree search, or refill can overshoot nominal MaxCheck. This is not a
pure hard-cap search, and the distance-pool width is not replaced with nprobe.

Filtered result admission keeps ordinary nonmatching graph bridges and uses
the matching result boundary, not an additional unfiltered-result heap.
Nonempty predicates do not impose a per-edge hard cap, clip tree calls or
reseed an empty graph frontier. An all-match predicate preserves ordinary
unfiltered IDs, distances and checked-leaf work with equal workspace capacities.

`SearchTrees` now processes a popped leaf, including its visited check, before
testing the limit. It stops immediately at that leaf instead of expanding
intervening internal cells and discarding the next popped leaf. Ordinary and
iterator callers preserve upstream exact-limit entry behavior, which may
process one fresh leaf even when entered at the limit. Filtered callers retain
that same boundary. Checked leaves are not identical to all distance
callbacks: internal BKT routing centers retain their native accounting.

With `PostingMatchRatePercent=0`, supplementation remains one separate phase after the graph phase,
only for an actual result deficit. Original heads are protected, anchors reuse
scored distances, visited/match state is shared, and selected CSR rows complete
before checking the supplemental budget. Discovered H2+ postings share one
representative-distance frontier: selecting a posting exposes its owners once,
and upper rows register children without an eager lower-level drain. Rejected
anchor-owner entries and explicitly promoted owners may ascend once, but
neither their representative nor CSR is accessed. Rejected children discovered
while descending an upper row do not expose their other owners. Their cached
rejection can still ascend if later encountered as an entry or promoted owner.
This controls negative-child fanout without shrinking the distance pool or
changing checked-leaf budgets. Upper discovery and representative distances
are still distinct from charged H1 checked leaves.
The bounded supplemental result heap keeps its original missing-slot capacity
and may replace its own worst heads after filling; it never replaces original
graph heads. Fullness is not a stopping condition. Budget/reachable exhaustion
are hard limits; adaptive frontier convergence can stop earlier even with an
underfilled result heap. Only on activation, BKT configures an H2 representative
distance pool with the native width `max(effective MaxCheck / 16, head-result
capacity)`. H3+ nodes retain expansion priority but cannot consume H2 pool slots.
It reuses `COMMON::DistPriorityQueue`; a nearest pending posting
worse than this pool terminates the phase (diagnostic reason 7). This is an
approximate ANN stopping heuristic, not a certified lower bound on members or
a high-recall guarantee. Extra budget remains a checked-leaf ceiling and does
not widen the original graph phase. Within signature-admitted rows, fresh
predicate negatives are marked
visited without vector prefetch, distance or checked-leaf cost. Collapsed
representatives first qualify aliases and score their shared vector only if
a live matching alias needs it. Matching fresh members retain native accounting.
No graph traversal follows these terminal rejection visits, and supplemental
members are not added to the unused graph frontier. This does not change the
ordinary graph's negative bridge semantics or the zero-extra exhausted-budget
entry rule. V9/176-byte records, precision, schemas and canonical catalogs are
unchanged. Query-sized workspace initialization is intentionally retained.

The opt-in `local-match-union-v2` policy sets `PostingMatchRatePercent` to an
integer 1..100 and uses positive `PostingMatchWindow` (default1024).
Count fresh scored H1 candidates, including tree candidates, using their cached
support/may-match result. A complete nonoverlapping window latches when
`matches * 100 < window * percent`. Equality and incomplete windows do not
trigger. This is neither original-record selectivity nor result-heap fill.

A sparse posting phase may replace H1 early only if every requested
categorical label exists in its domain. Partial-domain OR queries defer a
latched supplement until native union-filtered H1 completion. Handoff occurs
at a graph-row/tree-call boundary, not through a new hard cap. Already-scored
pending candidates are drained without graph expansion; one posting phase
then competes with existing H1 heads in the same bounded nprobe heap.
Unlike the legacy policy, these original heads can be replaced even when
the heap was full. No graph resumption follows posting-side terminal rejects.

For either policy, the checked-leaf ceiling is
`max(MaxCheck, actual H1 checked leaves) + PostingAdditionalMaxCheck` in 64 bits.
Native H1 overshoot does not consume the separately promised extra budget.
Zero extra with exhausted H1 budget still prevents supplementation.
`PostingAnchorCount=0` derives its limit from nprobe. A positive
`PostingNavigationWidth` bounds navigation distances per upper tier separately
from the terminal-H2 convergence pool; it is not a row quota.
Exact per-tier label domains prevent ascent above the highest relevant tier.
These controls do not adapt MaxCheck before a query. Unfiltered, policy-off
and nontriggered searches retain the native stopping rules described above.

`NativePosting.SearchBudgets` and its UBSan fixture exercise an actual serialized
small BKT tree and native queues, including exact/over-limit entry, duplicate
leaves, pending internal cells, visitation, and filtered all/none/sparse cases.
Existing postgraph, alias, numeric/DNF, catalog and persistence fixtures remain
applicable.

For the independent ordinary-search control, download the three pinned upstream
files listed in `prepare_bkt_reference.py`, run that extractor, and configure
`SPTAG_PINNED_BKT_REFERENCE_DIR` to its output. `BKTReferenceTest HEAD_DIRECTORY
QUERY_DEFAULT_BIN` compiles the authenticated upstream tree and search function
bodies against the same graph and project common containers/SIMD implementation.
Only the search class qualification is renamed. This is an isolated pinned
native reference, not a claim to have built the whole pristine upstream engine.
It compares exact IDs, distances, checked leaves and distance-call counts,
and requires both adaptive early termination and nominal-budget overshoot.
The iterator control also compares the pinned iterator body over successive
batches, with equal freshly initialized workspace capacities. Pooled project
queues retain grown capacity; that existing allocation-history behavior is not
an upstream allocation-parity claim and is not changed here.
