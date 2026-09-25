# Fresh PipeANN five-scenario curves

This profile measures the existing read-only/no-mapping PipeANN index and
verified 1% memory-entry index. It does not build an index, download vectors,
modify historical results or substitute a missing memory entry.

Use **`benchmark_v2.ini`** for the current run. The original `benchmark.ini`
attempt is retained as invalid: the native readers requested writable fds,
Landlock rejected them, and the unfiltered binary continued with `EBADF` under
`NDEBUG`; filtered loading trapped. Neither its positive QPS nor its reported
recall is usable. The controller now treats native ERROR/FATAL/EBADF messages
as failures even when the process returns zero.

V2 pins the isolated `pipeann_query_readonly_fds_20260924` manifest and its
verifier. The two-file native patch uses `O_RDONLY` under `READ_ONLY_TESTS`
and checks failed opens explicitly. Writable/build branches, ANN/filter/distance
logic, `NO_MAPPING`, uring, tcmalloc, and all query settings remain unchanged.
Only search executables were rebuilt; no index was rebuilt or made writable.
The adaptation's fixtures and FIRST16 functional smokes are not plot data.
Final curve provenance must retain the descriptor adaptation and new binary
identities rather than relabeling them as the original archived executable.

The five predicates, first1000 UInt8 queries and exact top10 IDs match
`main_selectivity_20260923/inputs/workloads.json`. The original four shared
native truth files and query bytes are checked for exact equality.
`sel_01pct` uses the already-authenticated tag169 truth: only a small native
uint32 ID matrix and1000-row `.spmat` binding are generated. No billion-row
base/attribute scan is needed.

The reference config intentionally names the immutable archived profile that
matches the recorded native toolchain digest. Validation does not weaken that
digest check or pretend the current default profile is byte-identical.
Existing official helpers validate the toolchain, dirty-source snapshot,
read-only compilation, allocator/backend linkage and native memory entry.

All data/search controls are in `benchmark.ini` and the four checked-in filter
JSON files. The launcher never renders search configurations or uses environment
overrides. Controls are one query thread, NUMA3, native PQ/pipeline32/mode2,
unfiltered mem_L10, filtered auto/mem_L0, and the declared13-value L sweep.
Each L has1000 warmup queries followed by1000 measured queries. Two passes
reverse the L order and produce26 measured points per scenario,130 total.
The index loads once per scenario process, not once per L.

```sh
python3 -B Tools/benchmarks/run_pipeann_selectivity.py prepare \
  Tools/benchmarks/configs/sift1b_pipeann_curves/benchmark_v2.ini
```

Preparation freezes inputs, helpers and exact native executable copies under
`datasets/sift1b/comparisons/pipeann_adaptive_curves_20260924_v2`.
After the SPTAG ordinary curve batch has completed:

```sh
python3 -B datasets/sift1b/comparisons/pipeann_adaptive_curves_20260924_v2/source/run_pipeann_selectivity.py run \
  SPTAG/Tools/benchmarks/configs/sift1b_pipeann_curves/benchmark_v2.ini
```

Run the second command from the parent workspace directory, or use absolute
paths. The runner refuses to overlap the active SPTAG curve batch. Each native
child is Landlock-confined to a new scenario output directory; persisted
attributes and indexes remain read-only. Existing/partial stage outputs are
retained, not silently overwritten or retried.

`native-results.json` retains warmup and measured native rows; `results.json`
contains only the130 measured rows. Per-scenario logs/resources/commands and
native process identities are recorded. Native recall is strict top10 ID
overlap from the same IDs-only truth matrix. PipeANN retains its native direct
IO while SPTAG uses buffered IO; final figures must disclose this distinction.

## Completed publication

The corrected v2 run completed all130 ordinary measured PipeANN repetitions.
Together with the140 completed SPTAG adaptive/H1 repetitions, the assembler
produced135 median/min-max coordinates; no historical timing or failed-v1
point is included.

The combined five-panel PNG/PDF, five individual panel PNG/PDF pairs, exact
coordinate CSV, metadata and hash manifest are under:

```text
/mnt/nvme/baotonglu/mocheng/datasets/sift1b/comparisons/pipeann_adaptive_curves_20260924_v2/plots_selectivity
```

Main files are `recall_qps.png`, `recall_qps.pdf` and `plotted_points.csv`.
Renderer inputs and their raw-source provenance are in the sibling `plot_input`
directory. Captions identify the query-only descriptor adaptation, native
buffered/direct I/O difference, project H1-only baseline and unequal native
search controls. Ranges are observed repetition ranges, not confidence intervals.
