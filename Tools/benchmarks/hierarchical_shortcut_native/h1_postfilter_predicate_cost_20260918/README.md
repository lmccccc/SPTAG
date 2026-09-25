# Predicate cost controls and bounded CPU diagnosis

Diagnostic successor only. No production optimization or accepted search policy
change. The frozen original-order correction and faster historical
distance-shortcut control are both preserved.

All four controls use **one executable/static core**, the same native INIs and
the same immutable index:

| Control | VisitedMatchMode | DiagnosticAdmission | Meaning |
|---|---|---|---|
| A | graph | shortcut | Historical output-distance shortcut, before predicate |
| A-prime | graph | predicate-before-shortcut | Real same predicate before shortcut; skip subsequent admission on distant candidates |
| B | graph | original | Authenticated original result-admission order |
| bit | match | original | B plus existing visited bit and full physical-row accounting |

`DiagnosticAdmission` exists only in this isolated diagnostic core. It is not
a proposed production parameter. Non-original settings are rejected outside
graph mode. All controls retain matching H1 output, unpruned ordinary distance
navigation, native queues/budgets, selected-head own handling and exact final
filtering. No supplementary own heap, eager own/alias/liveness qualification,
mask, new cache, alternative predicate or new threshold.

A-prime is the sole new ablation shape. It executes the actual std::function
routing predicate after distance, reuses that value for competitive admission,
and retains A's early return before notDeleted/checkFilter/isDup. This tests
the implemented predicate/callback/support path, not the cost of an isolated
integer comparison. There are no per-call clocks or volatile bookkeeping writes.
Untimed actual callback counters and a side-effecting native regression prove
execution; all controls must have identical IDs/distances/navigation/SSD work.

## Bounded execution

Eight CPU-clock-only controls: A, A-prime, B, bit, bit, B, A-prime, A.
Then exactly two sampled1000-query windows: A-prime and bit. Same1000 warmup,
1000 measured, one query thread, NUMA CPU/memory2, nprobe24/topk10/MaxCheck2048.
No sparse1000, full curves, additional variants or optimization.

The reused query-thread SIGPROF/ucontext sampler is loaded by an explicit ELF
interpreter diagnostic preload, never by a search/data environment override.
Only steady-clock boundaries5-6 (the ordinary body) are sampled. All warmup,
storage diagnostics and semantic capture are excluded. Controls run the same
boundary audit with timer disabled. Raw native elapsed and actual thread CPU
are both retained. The native elapsed includes finish-boundary map-audit
overhead; the reported CPU window excludes map dumps.

The sampler has a preallocated buffer and no handler allocation/unwinding.
Requested200us; actual cadence is measured. Zero buffer overflow does not
mean zero timer coalescing. No perf permissions are changed.
Debug objects are never executed: their executable bytes and symbol tables,
then linked executable sections, must match the sampled binary exactly.
ASLR/module maps, raw PCs, decoded lines and sampled-function disassembly persist.

Reproduction, once in a fresh successor:
```
python3 prepare.py --initialize
python3 build.py
python3 experiment.py
python3 symbolize.py
python3 analyze_cost.py
python3 test_cost.py
python3 seal_cost.py
```

`run.py` is inherited solely for validation helpers; its campaign entrypoint is
not used. The predecessor's order-trace scripts are historical helpers, not
executed or claimed as new measurements here.

## Interpretation boundaries

A-prime minus A measures the real extra predicate path (including its dispatch
and guard scaffolding), without bit/row accounting or later admission.
B minus A-prime is remaining admission/branch work, not pure predicate cost.
Bit minus A-prime includes extra tree callbacks, bit plumbing, physical-row
accounting, native admission and ordering interactions.

PC tables contain mutually exclusive leaf residence, scaled by actual thread
CPU. They are not an additive causal instruction-cost model. Only one sampled
window per control; periodic sampling can alias. Conditional counting error
bars are illustrative, not validated confidence intervals.

The source confirms capture-off updates of full-row degree/eligibility and
legacy `ordinaryObservation->checks/passes`. Nothing is disabled to claim speed.
The sampled graph-marker site is original collapsed-group admission, not an
added eager alias check. Attribute support rows are separate from distance
vector payloads; source/PC evidence does not establish cache misses or DRAM.

No production/index/plot/public GettingStart changes. Preserve frozen history.
After sealing: STOP / IDLE.
