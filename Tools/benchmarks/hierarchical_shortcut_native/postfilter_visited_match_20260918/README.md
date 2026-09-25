# Native co-located visited match: bounded milestone

Isolated from production, previous experiments, index bytes, and main-owned
GettingStart documentation. The parent is `postfilter_native_switch_20260918`;
its observed admission pass-rate is **replaced**, not retained as an option.

Native INI controls are `VisitedMatchMode=graph|match|posting` and the fixed,
untuned `PostingNeighborMatchRatio=0.01`. Graph is the original compact visited
control, match is the bit-only/posting-disabled ablation, posting enables the
full ordinary-neighbor match-ratio gate.

## Storage and semantics

Actual `WorkSpace::OptHashPosVector` is a two-table hash set, not a bitmap.
Graph/unfiltered use four-byte unsigned slots (the same native key encoding
without signed `id+1` overflow). Match mode replaces that allocation with
eight-byte slots: low32 key=`uint32(id)+1`, bit32 match, zero empty.
There is one semantic predicate bit and no second eligibility array/map.
Storage supports all nonnegative signed32 IDs, including INT_MAX; native array
bounds remain separately enforced.

One native probe returns visited and match. An empty ordinary slot evaluates
the physical predicate before distance/result competition, then stores key
and bit together. Tree initialization and continuation use the same workspace
method. Repeated reads and growth do not evaluate the predicate. Legacy
unqualified insertion into extended storage throws. Rehash copies both old
blocks and the bit, retries collision overflow, and keeps no slot pointers.
Result deduplication remains separate.

The bit is posting OR own static eligibility, ORed across native-live collapsed
aliases for physical representatives. It does **not** imply either component:
native posting/result and own checks still run. Own-only matches never become
posting admissions solely because the OR bit is true. False ordinary matches
still visit, distance-score, enqueue, and navigate under the original policy.

Auxiliary probes insert only positive matches. Negative uninserted candidates
are deliberately not cached and may be evaluated again in another selected
CSR row. There is no hidden negative cache.

## Snapshot capability

This is a single-thread, STATIC, immutable-index benchmark capability, **not**
concurrent-update support. Enabled raw hooks require explicit
`immutableSnapshot=true`, a result filter and no traversal filter; otherwise
they throw. Match/posting modes reject BKT/SPANN Add/Delete entrypoints.
Same-thread mutation during a match query also throws. Preexisting deletions
participate in first matching; native result/own liveness checks remain.
Do not mutate raw backing arrays, change configuration/filter/index during a
query, or run concurrent maintenance. No guarantee is claimed for those
unsupported uses. Graph retains its original mutable behavior.

Reset clears match state/callbacks and restores compact storage. A subsequent
filtered enabled query selects extended slots. This implementation reallocates
on compact/extended transitions; its measured same-trace tax includes that
cost. No unfiltered predicate/hierarchy hook is installed.

## Gate and evidence

Finish the ordinary row first. With `d>0`, `e<0.01*d` and remaining native
budget, allow the supplier action. Valid already-visited entries count in
both `e` and `d`; truncated rows cannot activate. Signature precedes upper
representative/CSR access. Every selected CSR row is completed even if it
crosses MaxCheck; ordinary rows retain their per-edge cutoff. No next action
starts after exhaustion. No quotas, caps, two-hop, restart or extra frontier.

Independent real-query full-row oracle evaluation occurs only in untimed
capture and never feeds the switch. Captures distinguish first physical
match evaluations, reused-bit reads, rejected uninserted auxiliary evaluations,
alias component calls, and unchanged native result predicate calls.
Off/on results/distances/SSD work must match; graph versus bit-only additionally
requires exact head/own/queue/checked/visited/distance trajectory parity.

Run from the workspace root (initialization refuses existing destinations):

```sh
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_visited_match_20260918/prepare.py --initialize
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_visited_match_20260918/build.py
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_visited_match_20260918/run.py
python3 SPTAG/Tools/benchmarks/hierarchical_shortcut_native/postfilter_visited_match_20260918/finalize.py
```

Build is fresh Release, SPDK/ROCKSDB off, static core `-j2`, private headers.
Six Broad processes: graph/match/posting and reverse-order repetition.
Each has 1000 warm, 1000 measured, 1000 untimed diagnostic queries.
Sparse32/unfilter32 cover all three modes. Only if all sparse means are below
10ms, add four sparse graph/posting processes. Max ten ordinary processes.
NUMA CPU+memory2, thread1, native `[24]`, top10, offset0, MaxCheck2048,
Hierarchy512, initial ratio .666666, pages15, buffered matched flat index.
The timed body SHA remains
`9f412c63b71f7310e09180dff0858ccef3957bd20d7a96c34fdb440e3d559d53`.

Sealed report and raw evidence live under
`datasets/sift1m_zipf200_sparse193_numeric/comparisons/postfilter_visited_match_20260918`.
No performance promise, promotion, curves, tuning or automatic next step.
After sealing: **STOP / IDLE**.
