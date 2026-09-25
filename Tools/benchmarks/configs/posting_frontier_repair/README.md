# Posting-frontier policy controls

## Controlled-ascent adaptive-only curves

`sift1b_controlled_ascent.ini` is a separate, fixed campaign using
`run_adaptive_frontier.py`. It selects `Variants=postgraph_extra`; omitting
`Variants` preserves the older graph-plus-posting design. The implementation
revision is `controlled_ascent`, while the policy label remains
`predicate_first_adaptive_distance_frontier`, schema v6 / 57 columns.
There is **no before runtime and no H1-only matrix** in this new profile.

Five scenarios (unfilter, broad_tag, medium_tag, sel_01pct, mixed_dnf) use
nprobe `[16,24,48,96,192,384,768]`, two reverse-case-order passes, 1000 warmup /
1000 measured / 1000 replay queries per point: ten cases, 70 ordinary points,
one load. Native search settings are explicit in the checked-in profile and
must exactly match the authenticated historical postgraph settings. The
first case is `r1_unfilter_postgraph_extra`; the second pass reverses the five
scenarios while retaining ascending probes within every case. Query threads
remain one, with CPU and memory binding to NUMA3, not a single CPU.

A separate diagnostic process is restricted to sel_01pct and mixed_dnf at
16/96/384, first32 queries, with all three 32-query windows at each point.
It cannot start until all 70 ordinary points have completed validation.
Its six rows contain `diagnostic_qps_not_for_throughput`, **not `qps`**, and
are excluded from the plotted ordinary series. No phase logging.

Preparation creates only the new
`sift1b/comparisons/main_posting_controlled_ascent_curves_20260924` root:

```sh
python3 -B Tools/benchmarks/hierarchical_shortcut_native/native_postfilter/run_adaptive_frontier.py \
  prepare Tools/benchmarks/configs/posting_frontier_repair/sift1b_controlled_ascent.ini
```

`series-contract.json` declares the renderer interface.
`after-normal-results.json` contains the 70 ordered rows, with scenario,
variant, repetition, case, nprobe, queries, recall and ordinary qps.
`ordinary-comparison.json` retains every old/new recall and QPS coordinate,
including regressions. The old adaptive curves are explicitly preserved
observations, not a fresh paired baseline or substituted timings.
The renderer must reuse `pipeann_adaptive_curves_20260924_v2` without rerunning it.
Its result/registration and previous adaptive payloads are pinned read-only.

Checks include reverse-pass exact payload parity, exact unfiltered
old/new IDs/distances/SSD work, predicate/top10/L2 validity, and both ordinary
first32 prefixes versus the diagnostic payloads. Diagnostic accounting retains
head protection, full-row overshoot, positive/negative/visited partitions and
reasons5/6/7. `counter-comparison.json` reports all old/new counter means,
convergence and underfill; graph-phase means must match the preserved first32
diagnostic reference. This last check is explicitly mean parity, not a claim
of unavailable old per-query graph-payload parity. There is no performance
acceptance flag inferred from passing correctness checks.

After the parent declares the core ready, build in the **new** toolchain
`main_posting_controlled_ascent_20260924/{normal,diagnostic}` only. The inspected
recipe is CMake Release, GPU/ROCKSDB/SPDK off, LIBRARYONLY off,
`SPTAG_OUTPUT_DIRECTORY=<new-kind-dir>/bin`, and
`SPTAG_QUERY_WORK_DIAGNOSTICS=OFF` for normal / `ON` for diagnostic.
Build with at most two jobs and explicit native targets, not the default
all-target build: `nativeBench`, `NativePostingTest`, `NativePostingUBSan`,
`PostingCollectionTest`, `PostingCollectionUBSan`, `PostgraphPostingTest`,
`PostgraphAliasTest`, `SearchBudgetTest`, `SearchBudgetUBSan`.
Run the matching small CTest cases in each build. Never target the production
Python module or rebuild a frozen toolchain.

Core readiness is not execution approval. Once builds and native gates pass,
the parent must separately authorize execution using the generated template.
Only `[Runtime.after]` is required, with normal/diagnostic binary hashes,
matching core/source provenance and
`implementation_revision: "controlled_ascent"` in its provenance JSON.
Then, using `OUT` for the fresh campaign and `PLAN` for the fixed profile:

```sh
python3 -B "$OUT/source/run_adaptive_frontier.py" authorize "$PLAN" --approval /absolute/approval.ini
python3 -B "$OUT/source/run_adaptive_frontier.py" run "$PLAN" --runtime after --kind normal
python3 -B "$OUT/source/run_adaptive_frontier.py" run "$PLAN" --runtime after --kind diagnostic
python3 -B "$OUT/source/run_adaptive_frontier.py" compare "$PLAN"
```

Run these stages serially. The runner retains native PID/start/log records,
Landlock confinement, no-overwrite guards and input/index identities. Preparing
or testing the controller does not load the 1B index, scan/hash its SSD payload,
configure/build changing native sources, or grant execution permission.

## Historical before/after controls

This is a new preparation/validation harness, not a change to native search,
indexes, historical clients, or historical runners. **Preparation never starts
a native executable.** Production runs require a later explicit parent
authorization with ready binary hashes and matching frozen build provenance.
The parent has fixed schema v6 / 57 columns. Policy labels are provenance-only;
no `Bench.cpp` policy tag is required.

Profiles:

* `sift1m.ini`: same compact index and first 1000 query/truth rows as
  `main_search_boundaries_20260922/campaign`, all six original scenarios.
  The original query file has 10000 rows; the registered comparison deliberately
  retains the historical first-1000 window, not a new 10000-query campaign.
* `sift1b.ini`: current accepted compact preserved-H1 index and authenticated
  `main_selectivity_20260923` UInt8 inputs; unfilter, sel_01pct, mixed_dnf.

Both use graph2048 and postgraph2048+2048, anchors8, nprobe96/384, and
1000 warmup + 1000 measured + 1000 replay queries at every point. Each runtime
has one ordinary batch and one diagnostic batch, with the same case order
starting at unfilter/graph. Both run on NUMA3 CPU/memory as explicitly declared
in the new profiles. Search settings, including page limits 15 for 1M and 3
for 1B, come from authenticated **actual generated historical INIs**. Only
SearchSweep and explicit ValueType are added/changed. No phase logging.

The before runtime is the frozen **predicate-first intermediate** build, not
the original all-scored 1B acceptance runtime. The after runtime is the parent's
new posting-frontier build. This isolates policy repair from negative skipping.
QPS is labelled **single-run controls**, never formal performance acceptance.
All regressions and improvements remain in the per-coordinate output.

## Prepare now

From the repository root:

```sh
python3 -B Tools/benchmarks/hierarchical_shortcut_native/native_postfilter/run_frontier_repair.py \
  prepare Tools/benchmarks/configs/posting_frontier_repair/sift1m.ini
python3 -B Tools/benchmarks/hierarchical_shortcut_native/native_postfilter/run_frontier_repair.py \
  prepare Tools/benchmarks/configs/posting_frontier_repair/sift1b.ini
```

Each profile creates its dataset's fresh `comparisons/main_posting_frontier_20260924`
directory. It contains frozen helper sources, generated case/batch INIs, a
registration, identity guards and `authorization.v2.template.ini` with
`AllowNativeRuns=false`. No binary is executed, copied or assumed ready.
Do not rerun prepare over an existing output. The updated contract uses
`registration.v2.json` and `source_v2/`; the original registration, helper
snapshot, authorization template and preparation evidence remain preserved.

## Authorize and run only after parent release

Copy the generated authorization template to a new approval INI; retain the
template. After the parent explicitly approves execution, supply:

```ini
[Authorization]
AllowNativeRuns=true
[Runtime.before]
NormalSHA256=EXACT_READY_NORMAL_BINARY_SHA256
DiagnosticSHA256=EXACT_READY_DIAGNOSTIC_BINARY_SHA256
NavigationSchemaVersion=6
PolicyLabel=predicate_first_legacy_posting_order
Provenance=/absolute/frozen-before-build-provenance.json
ProvenanceSHA256=EXACT_PROVENANCE_SHA256
[Runtime.after]
NormalSHA256=EXACT_READY_NORMAL_BINARY_SHA256
DiagnosticSHA256=EXACT_READY_DIAGNOSTIC_BINARY_SHA256
NavigationSchemaVersion=6
PolicyLabel=predicate_first_global_distance_frontier
Provenance=/absolute/frozen-after-build-provenance.json
ProvenanceSHA256=EXACT_PROVENANCE_SHA256
```

Labels identify approved binary/core/source hashes, not new native JSON fields.
An optional legacy `NativePolicyField` entry must be empty. Each provenance JSON
must contain `policy_label`, `navigation_schema_version:6`,
`navigation_columns:57`, `binary_hashes` mapping `normal`/`diagnostic` to their
SHA256 values, `core_hashes` mapping absolute frozen library paths to SHA256,
and nonempty `source_hashes` mapping source paths to SHA256. Both corresponding
`bin/libSPTAGLibStatic.a` files must appear in `core_hashes`. Core files and
binaries are checked; source hashes are the parent's authenticated build-time
attestation, **not hashes of the now-changing production source tree**.
Both policies use the same predicate-first charged/skip partition semantics.

Use the **frozen** runner in the prepared output for all subsequent stages:

```sh
# Set OUT and PLAN to one of the prepared output/profile paths; approval is new.
python3 -B "$OUT/source_v2/run_frontier_repair.py" authorize "$PLAN" --approval /absolute/approval.ini
python3 -B "$OUT/source_v2/run_frontier_repair.py" run "$PLAN" --runtime before --kind normal
python3 -B "$OUT/source_v2/run_frontier_repair.py" run "$PLAN" --runtime after --kind normal
python3 -B "$OUT/source_v2/run_frontier_repair.py" run "$PLAN" --runtime before --kind diagnostic
python3 -B "$OUT/source_v2/run_frontier_repair.py" run "$PLAN" --runtime after --kind diagnostic
python3 -B "$OUT/source_v2/run_frontier_repair.py" compare "$PLAN"
```

Shell variables here select INI/file paths, not native search overrides.
Authorization seals private copies of the four approved executables. Native
children run through the existing Landlock helper with write access only to
their new stage directory. Resource records use `/usr/bin/time -v`; status
records identify the owned wrapper/native processes. Environment overrides
remain forbidden. Existing stage directories/logs are never overwritten;
partial outputs and failures are retained, not labelled complete.

## Checks and evidence

The runner verifies registered event order, case paths, budgets, type, schema,
full windows, no phase timing, final payload sizes, normal/diagnostic exact
ID/distance/SSD-work parity per version, and graph-only old/new final parity.
Diagnostic original graph IDs/distances and dedicated graph work must match
graph/postgraph within each version and before/after. Graph-only total H1
distance, visited-check and head-predicate counters must also match.
Auxiliary owner/member counters may legitimately change with the global
frontier: they are reported, not used as cross-policy equality gates.

Returned top10 IDs must be in range, unique, predicate-valid and distance-sorted.
Distances are compared exactly with original SIFT vectors at **returned IDs
only**, using bounded selected-row access; no whole-base or attribute scan.
Recall uses the authenticated same-cohort exact truth. Reports retain every
coordinate's recall, underfill, SSD work, all 57 mean/max counters, candidates,
heads, reasons and complete-row budget overruns.

Fresh members partition into scored fresh visits, cached visited skips and
unvisited terminal negative skips. Scored fresh visits partition into matching
candidates and scored negative representatives (the admissible-alias exception).
Supplement distances/checked leaves equal **scored** fresh visits, not skipped
negatives. Head count/protection, single activation, no auxiliary work when
already full, filtered graph hard bounds and row-attempt accounting are checked.
Ordinary unfiltered nominal budget overshoot remains legal.

For the after policy, reason 6 denotes checked-leaf budget termination and
reason 5 reachable exhaustion, including when `head_after == head_target`.
Reason 4 is rejected for after and remains legal for historical first-fill
before. A filled result is not a stopping rule. For activated supplementation,
`posting_target_met` must equal the final filled-result status; it is not
interpreted as a termination counter. Filled reason5/reason6 cases and
legitimate auxiliary-work differences have dedicated synthetic tests.

**Observability limits:** 57 columns plus graph-before dumps do not expose final
supplementary head IDs, last-row size/order, or the complete frontier. Therefore
protected-head counts and unchanged original graph IDs are verified, but
ID-level membership in the final head heap and exact stop-at-row-boundary order
still require the parent's focused native semantic fixtures. Complete-row
overruns are reported and must have completed H2 work; they are not hidden by
a false strict4096 cap. The harness cannot prove a last-row overshoot bound
from unavailable data and makes no such claim.

Index guards use device/inode/size/timestamps and existing fingerprints.
Preparation follows index aliases for metadata inventory but does not hash or
scan the SSD. New small-file authentication has a128MiB ceiling; large base/
attribute inputs use existing identities. 1B index identities must match the
accepted registration. Historical 1M SSD fingerprints are retained, not
misrepresented as newly rehashed.

Pure-Python tests (no native measurement):

```sh
cd Tools/benchmarks/hierarchical_shortcut_native/native_postfilter
python3 -B -m unittest -v test_frontier_repair
```

## Recorded SIFT1M outcomes

The full-budget global-frontier campaign completed all 96 points but failed
performance acceptance. Its original comparison also incorrectly compared
observer-only `graph_unique_h1`/`graph_may_matches` against disabled-path zeros.
The preserved `analysis_v3/` recovery corrects only that observation rule:
disabled observers must be zero, enabled before/after observations must match,
and actual graph IDs, distances and observable work keep their exact checks.
No native measurement was rerun during recovery. The recovered parity result
does not excuse the measured throughput regression.

`sift1m_adaptive.ini` and the frozen `source/run_adaptive_frontier.py` under
`comparisons/main_posting_adaptive_frontier_20260924` describe a separate
completed four-batch/96-point experiment. Its H2-only distance-pool convergence
uses reason 7, accepted only for the adaptive after policy. All original graph
and ordinary/diagnostic parity checks passed, but performance acceptance remains
false. The baseline still uses predicate-first first-fill, not the original
all-scored implementation.

| Scenario / nprobe | Before recall / QPS | Adaptive recall / QPS |
| --- | ---: | ---: |
| Medium / 96 | 98.84% / 704 | 99.65% / 409 |
| Medium / 384 | 99.60% / 207 | 99.96% / 147 |
| Mixed / 96 | 99.39% / 337 | 98.11% / 283 |
| Mixed / 384 | 99.82% / 240 | 99.92% / 141 |

These QPS values are fresh single-run controls, not a repeated performance
acceptance. At similar medium recall, adaptive nprobe96 improves on before
nprobe384, but Mixed/nprobe96 regresses on both axes. Its average head count
falls from 87.359 to 49.206; all 1000 queries stop by convergence while the head
target remains underfilled. Every final result still contains ten valid records,
so result cardinality alone does not establish adequate recall.

The four adaptive SIFT1M native batches each took 2:46--3:48 and completed at
2026-09-24 05:18:21 UTC. `completion.json`, `comparison.json`,
`results-report.md`, `work-costs.csv` and `execution-events.jsonl` retain the
results and actual PID/timing evidence. Neither experimental policy is an
accepted replacement on the basis of these near-ceiling SIFT1M results.

## Billion-scale ordinary follow-up

The low-recall investigation now uses the original SIFT1B index/cohort directly.
`sift1b.ini` supplies the frozen full-budget global-frontier after runtime;
`sift1b_adaptive.ini` supplies the frozen adaptive after runtime. The latter uses
`run_adaptive_frontier.py`, with the same existing strict UInt8 and 1B identity
checks. SIFT1M near-ceiling recall is a regression observation, not a substitute
for the billion-scale question.

Only `run --runtime after --kind normal` is launched for each profile, in that
order and sequentially. Each process loads once and runs the prepared 12 points:
unfiltered, actual0.1000735% and mixed predicates; graph/postgraph controls;
nprobe96/384; unchanged1000-query warmup/measured/replay windows. There are no
new before or diagnostic runs in this follow-up. The process is parent-owned;
separate analysis must not duplicate it. Existing ordinary results are the
historical recall reference, not a pooled or newly paired throughput baseline.
Index bytes, selected-head order, source data and historical campaigns remain
unchanged and native writes remain Landlock-confined to new outputs.

Both ordinary batches have now completed with native exit 0. The full-budget
batch took 1:06:06 including its index load; adaptive took 17:45.83. All 24
points retain complete 1000-query windows and deterministic native replay.
The parent rechecked raw payload hashes, exact returned-vector distances,
predicates, recall, frozen runtimes and unchanged index identities.
Graph-only and unfiltered IDs/distances/SSD work match each other and the
historical acceptance byte-for-byte.

| Scenario / nprobe | Historical all-scored recall | Full-budget recall / QPS | Adaptive recall / QPS |
| --- | ---: | ---: | ---: |
| 0.1000735% / 96 | 49.25% | 90.94% / 4.25 | 89.06% / 66.73 |
| 0.1000735% / 384 | 49.24% | 98.17% / 4.07 | 96.81% / 28.43 |
| Mixed / 96 | 41.08% | 88.05% / 3.42 | 85.42% / 56.23 |
| Mixed / 384 | 41.08% | 97.08% / 3.43 | 95.05% / 24.53 |

Thus the unchanged index supports substantially higher filtered recall than
the old search achieved. The current adaptive policy trades 1.36/2.03 recall
percentage points at nprobe384 for about 7x the full-budget QPS in these
single-run controls. These are not equal-recall speedups. Native settings
remain MaxCheck2048, additional2048, anchors8 and page3, but predicate-first
skipping changes the amount of metadata/member work a checked-leaf budget
allows; nominal equal budgets are not equal work. Neither a repeated
performance acceptance nor a paired PipeANN speed comparison is claimed.

The ordinary-only report is
`comparisons/main_posting_adaptive_frontier_20260924/ordinary-focused-comparison.json`
under SIFT1B. It explicitly does not claim completion of the prepared four-way
diagnostic campaign. No new diagnostic counters or phase attribution were
collected, and mean SSD postings read are not counts of all selected H1 heads.
QPS/mean latency use the native whole measured loop; per-query latency samples
exclude its loop/destructor overhead. Both native processes have ended.

## Complete adaptive/H1 curve sweep

`sift1b_curves.ini` selects `Mode=selectivity_curve` in the existing adaptive
runner. It registers the five requested scenarios (including broad and medium),
the explicit native nprobe grid16/24/48/96/192/384/768, and two ordinary passes.
The second pass reverses case order; each case retains its complete ascending
native sweep and1000 warmup/measured/replay windows. This is20 cases/140 points
in one load-once native process, not a splice of earlier two-point controls.

The two measured methods are the current adaptive posting policy
(MaxCheck2048 plus additional2048) and its same-index H1-only control
(MaxCheck2048, posting disabled). Both keep anchors8, page3, one query thread
and NUMA3. The H1 control is not presented as an unmodified Microsoft binary.
Curve mode permits only `run --runtime after --kind normal`; it does not
claim a four-runtime diagnostic comparison or schedule unused before/diagnostic
processes. Query/index/runtime protections and exact result checks are reused.

Outputs are isolated under SIFT1B
`comparisons/main_posting_adaptive_curves_20260924`. The declared grid and
repetition schedule are registered before launch, and summary validation checks
every native case boundary and point rather than assuming two points per case.
PipeANN measurements and R publication use their own retained provenance;
historical measurements must not be silently pooled with this sweep.
