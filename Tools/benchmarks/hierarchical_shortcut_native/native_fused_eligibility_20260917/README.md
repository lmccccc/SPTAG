# Bounded native fused eligibility

This isolated experiment inherits the accepted bound-fix runtime. It does not
modify the completed curve stage or run any other curve group.

Ordinary physical d/e observation is now inside the original BKT neighbor loop,
before the visited skip. The first visited members are deferred in graph-width
local scratch until d reaches the minimum, so short ordinary rows do not
eagerly evaluate otherwise unnecessary own predicates. Capture-only short-row
proof evaluations are separately labeled, not claimed ordinary work.
Native CheckAndSet still executes for rejected valid
candidates, and at its original position relative to scoring. Invalid IDs,
self edges, duplicates and negative sentinels retain their original traversal
semantics; only distinct valid nonself ordinary members contribute d/e.
A budget-incomplete ordinary row cannot invoke the supplier or publish a
complete degree frame. Selected injected rows still finish past MaxCheck.

A local NativeEligibility value carries posting/traversal eligibility from
the existing SPANN predicate implementation to native result admission.
Posting success still short-circuits own qualification. Existing Boolean APIs,
tree entry handling, native queues, distance functions and own notifications
remain. Collapsed aliases retain individual qualification and the native
CheckDup/alias stopping contract; equality is not pruned by the inherited bound.
No query-wide H1 eligibility cache or shadow heap is introduced.

Startup observation remains separately counted: it must decide whether to
expand the anchor's edges before doing so. Ordinary rows no longer enter that
callback scan. Visited members still need eligibility evaluations for d/e;
capture-only counters distinguish those from fresh-member handoffs.
Unique/repeated qualification IDs are tracked only in untimed capture.
Ordinary execution has no fine clocks, trace hashing or qualification ledger.

`prepare.py`, then repository edits and `install.py`, create the private source.
Build Release/SPDK OFF/ROCKSDB OFF, spannaclbench with parallelism2; use this
folder's CMake native fixtures against the new SPANN_ROOT. `paired.py configure`
preregisters a rotated12-process comparison using the unchanged parent binary
and new fused binary. `run.py fixtures`, `paired.py measure`, and `run.py clocks`
are separate bounded phases. Both versions use the same certified real DNF3
numeric predicate, native SearchSweep.NProbe=[24],1000warmup+1000measured,
NUMA2, one query thread, O_DIRECT and page15.

A is original empty-predicate H1; B is genuine nonempty filtered native
admission without a ratio observer; C adds the ratio observer. A-B includes
different posting-head/all-evaluated-own/O-tail admission semantics and is not
pure predicate cost. Before/after and B/C final recall/work must match exactly.
The four tiny real-filter fixtures may differ only in explicit diagnostic
qualification counts and omission of budget-incomplete degree frames.
Further optimization and all curve continuation require separate review.

The unsuffixed output preserves an unsuccessful first full-query preflight and
its GDB trace. The installer originally preserved edited files' old mtimes with
copy2; those mtimes preceded completion of a concurrent initial compilation,
so the next incremental build retained incompatible header-dependent objects.
The installer now updates destination mtimes with copyfile. The fully rebuilt
runtime is measured only under the distinct `_v2` output; no failed timing is
used or overwritten.
