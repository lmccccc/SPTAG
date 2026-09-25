# Native H1 signature-first full-posting supplier

This new isolated version supersedes none of the preserved experiments. Production,
original indexes, old source/runtime snapshots and all old results remain untouched.
There is one fixed native configuration per mode, not a distance-cap grid.

## Four corrections

| Contract | Previous degree16 | This implementation / fixture |
|---|---|---|
| No added search budgets | Actual cap2000/3200 and member2048 | `Supplier.h` has no distance/member/row quota. Removed INI keys are rejected, not converted to an unlimited sentinel. An8192-point fixture processes4096 members and exceeds3200 real calls. |
| Signature before parent distance/scan | No parent signature pruning | `Engine::RowAllowed` precedes `Rank` and every child-H2 entry. Native `BuildHierarchyQuerySignature` plus `MayIntersect`; cached pass/reject/completed states. Fixtures reject rows with zero scans/parent calls. |
| Predicate before H1 candidate distance | Scored first | Native traversal filter and `Engine::Qualify` precede ordinary edge, leaf and CSR-child scoring. `Score` throws if an ineligible ordinary/child candidate reaches it. Posting OR own eligibility, not own attributes alone. |
| Complete selected posting, visited afterward | Stopped at exact deficit; partial cursors | Full H2 rows and full selected H3 rows, including their valid unvisited H2 children. Row completion is recorded only after its end. A closer last member beyond16 and no-rescan fixtures exercise this. |

## Fixed semantics and structural exceptions

The original native BKT H1 frontier, tree queues and visited state drive search.
Effective degree still means **distinct fresh** native H1 adjacency candidates
passing posting-admissible OR own-point-admissible checks; previously visited
eligible neighbors do not count. Raw fresh, eligible and enqueued counts differ.
Degree below16 triggers supply;16 is neither an output quota nor a row-scan stop.

The eight direct H2 owners are signature-checked before representative scoring,
then ordered by current-query distance, with ID tie-breaking. A selected row is
processed completely. Only at a row boundary, if the deficit remains and native
continuation permits work, select another valid unvisited owner. Exhausting that
direct scope permits H3: query-rank the union of the direct parents' eight owners.
A selected H3 row enumerates **all** children in immutable CSR order; every child
H2 signature is checked and every valid unvisited H2 row is fully processed.
Even if degree16/native MaxCheck is crossed, the selected H3 row and its children
finish before returning. A completed/rejected row is not rescanned. There is no
persistent upper queue, graph traversal upstairs, restart, whole-index rescue or
final-result-underfill trigger. Finite visited owner-derived scopes bound work.

Only native `MaxCheck=2048` remains, with target24 and SSD page limit15. It counts
native checked leaves/scored graph neighbors, **not** all distance calls. Rejected
tree leaves retain native checked-leaf accounting, despite skipping their distance.
Internal routing-center and parent-choice distances do not increment this counter.
An already-selected supplier row ignores MaxCheck until fully processed; checked
overshoot is recorded, and the native frontier/termination then resumes unchanged.
There is no artificial distance/member limit, including in ordinary timing mode.

The existing native result/traversal-filter API is used, including its saved-tree
continuation when admitted spatial results are insufficient. No new retry is added.
Internal BKT centers represent subtrees, not just their own predicate: their real
distances remain necessary and are separately recorded as `supplyRouting`.
Terminal singleton leaves are prechecked; invalid collapsed-group representatives
remain structural centers, with eligible sibling leaves exposed through the same
tree queue. An invalid H1 is not expanded as an ordinary graph bridge.

Qualification reuses native nonempty-posting and H/O metadata admission (numeric
full-O uses `tailPostingFilter`) OR valid/not-deleted canonical VID satisfying the
exact native predicate. It is independent of top-k competition and result dedup.
Compared with the earlier version, the nearest24 posting-result heap also requires
the posting eligibility bit: an own-only or structural/upper representative cannot
consume a posting slot merely because the coarse head callback returns true.
Every actually evaluated canonical H1, including routing/parent identities, is
offered to native own-point admission using its known distance. No anonymous upper
work or lost parent own points.

Upper signatures use the exact frozen H3 categorical-anchor construction and
persisted selectivity domains. OR/DNF anchors are unioned, not ANDed. A missing or
out-of-domain represented anchor disables categorical signature pruning; numeric
queries without categorical anchors are all-pass upstairs, then use native H1
numeric eligibility. Missing expected persisted signatures fail explicitly.
`HierarchyGraphSignaturePruning=false` controls the unchanged historical H3 graph;
it does not disable the newly authorized supplier posting-signature checks.

Counters are read-only observations, never work limits. `supplyPredicateRejects`
counts distinct ineligible H1 identities, which can include structural/parent
identities whose distances are legitimate exceptions. Ordinary/CSR-child scoring
of an ineligible H1 is separately prohibited by a runtime assertion. Real graph,
routing, parent and child callbacks sum to actual head work; repeats are charged
and cached distances are reported separately.

`degree_audits` retain `[raw_before,effective_before,deficit,qualified_supplied,
raw_supplied,enqueued_supplied,members,stop,h2_scope_exhausted]`. Stops are0 enough
after a full row,3 native boundary,4 exhausted owner scope. Supplied counts may
exceed deficit and16. `row_audits` contain `[level,id,row_length,processed_entries]`;
H3 lengths count H2 children, with each child's H1 row audited separately.
Rejected rows and unique predicate-rejected identities are captured separately.

The original top graph remains loaded as vector backing/reference baseline but
is not navigated by the supplier; its memory dependency has not been removed.
Historical H3 still has predicate-filtered top results, pre-score signature/support
compaction and saved-frontier16-to24 widening. Comparing it with this supplier is
not a geometry-only comparison, and latency ratios are not equal-recall claims.

## Reproduction

Authenticated648 native files from revision3552194536cb01dd70099e955a29e235a0cf4d2e
plus the saved original source diff/manifest. New toolchain:
`datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916/`.
New output: `comparisons/h1_fullrow_20260916/` in the same dataset root.

```sh
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/full/reconstruct.py \
  --repo SPTAG \
  --snapshot datasets/sift1m_zipf200_sparse193_numeric/build_runs/20260909T141212Z_lightweight_rescue \
  --output datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916 \
  --authenticated-cache /home/baotonglu/.copilot/session-state/40fc7ebb-e617-4ec3-a17b-610068e95e5e/files
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/fullrow_native/integrate.py \
  datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916/source
cmake -S datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916/source \
  -B datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916/build \
  -DCMAKE_BUILD_TYPE=Release -DSPDK=OFF -DROCKSDB=OFF -DTBB=ON -DLIBRARYONLY=ON
cmake --build datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916/build \
  --target spannaclbench --parallel 4
cmake -S SPTAG/Tools/benchmarks/hierarchical_shortcut_native/fullrow_native \
  -B datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916/tests \
  -DSPANN_ROOT=/mnt/nvme/baotonglu/mocheng/datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916/source
cmake --build datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916/tests --parallel 2
datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916/tests/suppliertests
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/fullrow_native/finalize_source.py \
  datasets/sift1m_zipf200_sparse193_numeric/toolchains/h1_fullrow_20260916/source
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/fullrow_native/run.py \
  --config SPTAG/Tools/benchmarks/hierarchical_shortcut_native/fullrow_native/experiment.ini
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/fullrow_native/verify.py \
  --config SPTAG/Tools/benchmarks/hierarchical_shortcut_native/fullrow_native/experiment.ini
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/fullrow_native/report.py \
  --results datasets/sift1m_zipf200_sparse193_numeric/comparisons/h1_fullrow_20260916
```

First1000 queries,1000 warmups, one query thread, native NUMA2 affinity,
two rotating ordinary/profile repetitions for all four modes and six scenarios.
An untimed capture precedes warmup. Original frozen H1/H3 parity precedes the
custom fixtures and paired matrix. No new dataset, benchmark graph build,
per-scenario tuning or prior-result timing substitution.
