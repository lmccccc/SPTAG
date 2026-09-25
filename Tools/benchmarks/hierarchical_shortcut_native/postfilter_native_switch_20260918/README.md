# Native observed-head postfilter switch

Isolated successor of the **final** `postfilter_posting_fullrow_20260918`.
Production, frozen sources/builds/configurations/evidence, index, modules, plots
and operator stop remain untouched. GettingStart is main-owned.

## Input and scope

Only `NativePostfilterMode=graph|posting` and `PostingFilterHitRatio=0.01`
(fixed, untuned; zero disables) are new native search controls. Obsolete
Observe, PostingActivationRatio, PostFilterPostingMode, NaviX, degree and
arbitration controls fail. Unknown keys, malformed/nonfinite ratios and search
environment overrides fail before loading an index.

At the existing BKT `const bool valid=p_resultFilter(p_result)` evaluation,
two local integers record actual calls and true returns while **one ordinary
edge expansion** is running, including its naturally encountered aliases.
Tree initialization and popped-head admission outside that expansion are
excluded. Visited, deduplicated, short-circuited or noncompetitive candidates
without that evaluation supply **no observation**, never an invented failure.
Own gain, AddPoint success and deletion checks are not the recorded boolean.
No additional predicate, eligibility, support, VID or live check is made to
choose the switch. No Qualification lease, per-head cache, degree gathering,
second walk or second-hop loop remains.

After the ordinary row completes, and only if native budget remains,
`checks>0 && passes<0.01*checks` permits signed posting anchored at that
already-expanded head. Zero checks means unknown: continue the graph.
Equality does not trigger. The current row has already executed; its work
was not avoided. This is **native-observed head predicate acceptance**, not
physical-neighbor degree or population selectivity. It deliberately does not
preserve the old threshold's trajectory or statistical meaning.

The counter pointer is cleared before auxiliary processing. Necessary
auxiliary eligibility checks cannot feed ordinary counters or recursively
activate posting. Auxiliary rejection precedes visited marking, preserving
nonmatching native graph bridges. Static own/posting separation, mutable
deletion, native alias/result-dedup guards and native tree own callbacks remain.
Final native admission uses direct callbacks, not a qualification cache.

Graph has no owner model. Posting retains the baseline's one-time index-load
owner inversion; **query-time** owner/signature/representative/CSR access and
lazy supplier allocation occur only after activation. Signatures precede all
selected representative/member reads. Nearest signed H2/H3 selection is
unchanged. Every selected CSR must be consumed completely, even across
MaxCheck; the next action cannot start after budget exhaustion. Ordinary
per-edge budget truncation is unchanged. No restart, global scan, quota,
upper ANN frontier or CostModel is introduced.

Empty predicates bypass new hooks and directly call native search. Graph
and disabled/no-trigger posting have no extra ordinary predicate calls.
Diagnostic capture alone records existing calls; it is not a third policy.

## Uniform untimed evidence

* `.operators.u64`: outer rows, ordinary slots, existing head-predicate calls,
  passes, failures, zero-check rows, auxiliary predicate/eligibility calls.
* `.decisions.f64`: query, head, calls, passes, completed, activated,
  checked before/after, signatures, representatives, CSR members.
* `.rows.u64`: query, level, ID, size, consumed. Every row must satisfy
  consumed==size; the supplier also checks this at runtime.
* `.native.u64`: unchanged native distance/checked/queue/admission schema.
* `.posting.u64`: activation/selection and graph/auxiliary work. The historical
  second-hop and classifier slots are literal zero; those mechanisms are
  removed, not renamed. Work's useful/valid counters are not switch inputs.

Native fixtures check actual callback order and outputs/queue/visited/checks,
unknown/all-fail/mixed/equality cases, ratio zero, own-gain independence,
aliases/deletion, capture on/off, unfiltered bypass, signed H2/H3, budget-eight
full CSR crossing and ordinary truncation, and rejected auxiliary bridges.

## Bounded reproduction

From the workspace root, in fresh destinations only:

```sh
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_native_switch_20260918/prepare.py --initialize
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_native_switch_20260918/build.py
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_native_switch_20260918/run.py
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_native_switch_20260918/finalize.py
```

Fresh source uses symlink-preserving copy, no Release/cache/old configuration
outputs, and copyfile for edited headers. The zstd `build/cmake` directory is
source, not a build cache. Release SPDK/ROCKSDB OFF; static library and private
harness built with -j2. Compiler scratch remains inside this project.

Sparse32 and unfilter32 graph/posting preflight precede four Broad processes
(two reverse-order repetitions). If the fixed sparse32 runtime guard is below
10ms, four sparse processes are allowed, **eight ordinary processes maximum**.
No recall-dependent choice, ratio search, curves or promotion.

Each ordinary process: 1000 warmup +1000 measured +1000 untimed replay, offset0,
topk10, native [24], MaxCheck2048, Hierarchy512, initial ratio .666666, pages15,
same buffered index, CPU/memory NUMA2 and one query thread. Capture/profiler
and preload are off in timing. The shared timed-body SHA is
`9f412c63b71f7310e09180dff0858ccef3957bd20d7a96c34fdb440e3d559d53`.

Graph must match the final fullrow Broad graph oracle exactly. Triggered
posting is a different trajectory, not a same-trace observation-tax estimate.
Reports honestly distinguish speed, recall and signature pruning evidence.
Artifacts live in the correspondingly named dataset comparison directory.
Finish **STOP IDLE**, with no automatic promotion.
