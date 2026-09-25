# Clean H1 post-filter, attribute bit, signed posting

This corrects the frozen `postfilter_native_predicate_20260918` over-restoration.
The baseline is **H1 result-level post-filter without a supplemental own heap**.
It is neither the previous own-heap experiment nor unfiltered nearest24 heads
followed by dropping nonmatches.

All three modes pass the same native support predicate to BKT result admission.
Ordinary nonmatching nodes still get native distance/frontier/visited processing.
The native `m_Results` distance heap, budgets, alias handling, underfilled
postfilter tree continuation and termination come from the existing authenticated
`SearchIndexWithResultFilter` path. No traversal filter is passed.

| Surface | Versus previous postfilter experiment | Versus over-restoration |
|---|---|---|
| H1 output | Matching heads, now unified support predicate | Restore result-level postfilter |
| Own results | No supplemental heap or eager own callback | Keep original selected-head handling |
| Match initialization | No deletion/version/VID/alias/CheckValidPosting | Keep stripped support-only chain |
| Final SSD | Native validity, exact filtering, dedup, head-record merge | Unchanged |
| Sparse adjacency | Same fixed .01 H2/H3 supplier, complete selected CSR | Unchanged |

## Predicate and bit

For native anchored requests, eligibility is OR membership of the compiled
anchors in `LimitedTagSupport::Supports`. The head's own tag is already covered.
Unlike old `evaluatePosting`, a matching own-only head is not rejected because
its posting is empty. Actual posting validity stays in SSD setup.
For compound predicates this is routing may-match, not exact record truth;
native final DNF/numeric checks remain. Unanchored routing retains the native
unrestricted meaning. No own OR, alias walk or new full-index fallback is added.

Graph evaluates this predicate at native result admission. Match/posting also
initialize its four-byte visited bit once on insertion and read it in the same
ordinary probe. An already-available fresh-neighbor bit is reused for admission
of that same ID, without another lookup/cache. Alias and seed admissions use
the normal predicate when no matching local bit is available. `nativePredicateCalls`
in inherited capture means logical result checks; `.admission.u64` separately
reports actual callback evaluations and local-bit reuse.
The capture-only `nativeSelectedHeadRecords` snapshot records the already-existing
native pre-SSD result heap for the empty-posting own-head regression; it does not
admit, rank or collect additional search candidates.

The single ordinary row computes full physical d/e including visited entries.
Only a complete row with remaining native budget may activate the fixed .01
supplier. Match-false bridges remain reachable; rejected auxiliary members do
not poison visited. Signature gates precede representative/member access.
Unfilter bypasses the additions. Static read-only scope remains.

## Evidence and commands

`python3 prepare.py --initialize`; `python3 provenance.py`; `python3 build.py`;
`python3 run.py`; `python3 verify_head_reference.py`; `python3 sparse_pair.py`;
`python3 finalize.py`.

Independent selectors: `ctest --test-dir <toolchain>/harness --output-on-failure`,
`python3 test_protocol.py`, `python3 verify_head_reference.py`.
`ctest --test-dir <toolchain>/harness -R native-empty-posting-own --output-on-failure`
checks the first actual zero-length posting head from the unchanged native index,
including its canonical selected-head record before SSD and exact final result.
The reference H1 executable links the authenticated original library and calls
`SearchIndexWithResultFilter` with the same support predicate. Its Broad/sparse32
H1 output must match exactly. Unfilter additionally compares original full results.

Six Broad ordinary processes, then the permitted fixed sparse graph/posting pair
only after a sub-10ms functional preflight. All configs remain native INIs.
No profiling, tuning, production changes, new own collector or promotion.
