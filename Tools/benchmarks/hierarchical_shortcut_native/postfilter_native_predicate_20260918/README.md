# Original native H1 lifecycle with an attribute routing bit

This isolated successor removes the experimental own-result heap, eager alias
qualification, deletion/version/VID checks in match-bit initialization, and
posting-validity checks used to qualify that bit. Previous milestones stay frozen.

## Authoritative baseline and allowed-delta inventory

The executable-compatible original is the authenticated pre-supplier reconstruction
at `toolchains/matched_baseline_20260917/original/source`: revision
`3552194536cb01dd70099e955a29e235a0cf4d2e` plus authenticated build snapshot diff.
It is **not pristine upstream**, nor the previous experimental result-filtered
graph. Git `5619bb1` independently establishes the original BKT popped-node
admission/alias lifecycle and SPANN head-result translation followed by SSD search.
The compatible reconstruction retains its existing per-edge checked-leaf budget
and tree-continuation limits; these differ from pristine upstream and are not
silently rewritten in this experiment.

| Surface | Restored/retained behavior | Allowed addition |
| --- | --- | --- |
| BKT seeds, tree, distance and frontier | Native distance-only search, no traversal filter or result filter | Visited initializes the routing bit once |
| BKT result heap and aliases | Original popped-node admission and native collapsed-alias handling | None |
| Termination/budget | Native no-result-filter branch; no underfill continuation | Complete an already-selected auxiliary CSR row |
| H1 record handling | Only selected native heads translated, exactly filtered, deduplicated and merged before SSD | None; no supplemental own heap |
| Ordinary adjacency | Original order/distance/queue, nonmatching bridges allowed | Same-probe bit reuse and physical row d/e |
| Auxiliary adjacency | Same native frontier/visited | Fixed .01 sparse gate, existing signed H2/H3 rows |
| Final SSD | Existing H/O setup, page budget, exact attribute filter, liveness and dedup | None |

Graph means authentic distance-only H1 navigation plus existing exact final
filtering. Match adds the bit/statistics, not a result-admission filter. Thus
posting-disabled graph and match must have identical native trajectories.
Historical experimental graph recall is not a parity target.

## Bit semantics

For an existing limited-tag anchored route, the bit is the OR of
`LimitedTagSupport::Supports(head, compiledAnchor)` over native compiled anchors.
This is exact membership in the persisted support summary, **not exact record
predicate truth** or a promise of a live result/nonempty posting. Retained own tags
are protected by construction; hierarchy signatures additionally insert the
head's own attribute. No own-record or alias walk is needed to fill the bit.

For anchored multi-attribute/DNF requests the anchor summary is only a necessary
may-match condition; final native DNF checks remain exact. Requests without a
supported anchor (including numeric-only) retain the existing unrestricted
route meaning: all nodes may match. This is not a new fallback or scan, and no
sparse activation is inferred from missing metadata. Signature may-match is
not reported as exact attribute truth.

The new call chain is visited miss -> routing predicate -> native support row.
It does not touch the vectorTranslateMap, versionMap, deletion set, graph alias
marker, or CheckValidPosting merely to initialize the bit. Existing native
liveness/alias/final admission still runs at its normal locations.

Native slots stay four bytes; generic INT_MAX fallback, masked rehash and
allocation-free query reuse are unchanged. Scope remains the read-only static
benchmark, not concurrent mutation support.

## Commands

`python3 prepare.py --initialize`, `python3 build.py`, `python3 run.py`.
Native selectors: `ctest --test-dir <toolchain>/harness --output-on-failure`.
Fixed Broad graph/match/posting reversed pairs and sparse/unfilter32 functional
checks run first. `python3 sparse_pair.py` executes the separately authorized
fixed sparse graph/posting reversed pair only after Broad and a sub-10ms native
preflight; the admission rule never reads recall. `python3 finalize.py` seals
the milestone. No profiling, parameter search or promotion.
