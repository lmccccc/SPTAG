# Exact SSD posting-label distributions

These tools generate descriptive statistics from an immutable local index and
optionally merge them into the same `history.jsonl` / `current.json` registry as
query results. **No generated distributions, vectors, datasets or indexes are
included in the repository.** Each machine supplies its own completed index.
This is an offline scan, not query instrumentation or a new performance run.

## Definition and supported layout

For each label `l` and nonempty posting `p`, compute
`count(l in p) / record_count(p)`. The label's distribution and **unweighted
mean** include only postings where `count(l in p) > 0`. Absent-label postings
are excluded, not assigned zero. A label absent from an entire group retains
zero counts and `null` mean/min/max.

The primary group `physical_H_plus_O` is each complete persisted SSD directory
entry. `H_prefix` and `O_suffix` use their own region sizes as denominators;
their means are not pooled into the primary mean. Every stored replica counts
in every posting where it occurs. The scan does not deduplicate globally,
include implicit own-head records, or truncate at query-time page limits.
This measures original-vector composition in SSD postings, **not** label
support on upper-hierarchy rows.

Supported inputs are little-endian Linux STATIC **UInt8, dimension 128**, raw
140-byte records: original uint32 ID, categorical uint32 column 0, numeric
uint32 column 1, then 128 vector bytes. The scanner requires STM1 version 3,
LTS1 version 5, no legacy tails, one SSD file, and dense declared label IDs
`0..N-1` with `1 <= N <= 4096`. Compression, delta encoding, rearranged postings,
hybrid distance, in-posting quantization and other schemas fail explicitly.
The saved `indexloader.ini` must declare `ColumnTypes=categorical,numeric`.
This is intentionally a bounded format reader, not a generic SPTAG index loader.

The scanner checks matching SSD/support/INI generations, file extents, original
ID/label ranges, nonoverlapping payload intervals and H/O/label accounting.
Directory entries are sorted by physical offset for sequential reads. The
payload is opened read-only with `O_DIRECT`; there is no buffered fallback,
cache drop, index rewrite, or connection to a running benchmark.

## Build and scan

Use Python 3.10+, the existing
[`requirements.txt`](requirements.txt), a C++17 compiler, and Linux `nice` /
`ionice`. The source has no SPTAG library linkage and does not rebuild the core.
The input filesystem must support 4 KiB-aligned direct reads.

From the repository root, choose writable output paths **outside the index**:

```sh
mkdir -p /absolute/tools
g++ -O3 -std=c++17 -Wall -Wextra -Werror \
  Tools/benchmarks/results/ssd_label_distribution_scan.cpp \
  -o /absolute/tools/ssd_label_distribution_scan

python3 Tools/benchmarks/results/ssd_label_distribution.py scan \
  --tenant-dir /absolute/index/tenant_0 \
  --binary /absolute/tools/ssd_label_distribution_scan \
  --index-version my-physical-index-run1 \
  --min-available-mib 1024 \
  --output-dir /absolute/results/label-scan-run1
```

`--tenant-dir` contains `indexloader.ini`; SSD/support filenames are read from
that INI and resolved relative to the supplied tenant, not its historical
`Base.IndexDirectory`. Preserve symlink targets when copying an index.
`--index-version` must identify this physical snapshot; a rebuilt/different
index must not be labeled as an old measured one. All scan output directories
must be new.

There is one scanner worker at nice 10 and best-effort IO class 2/priority 7.
It retains a compact 16-byte directory record per posting and a 64 MiB read
buffer. At 120 million postings this needs about 1.9 GB plus overhead and scans
the full posting payload, potentially terabytes. The explicit memory reserve
defaults to 1024 MiB; use `262144` to retain 256 GiB for a concurrent large build.
This is an available-memory check, not a cgroup quota or an IO bandwidth cap.
Allocate adequate resources and avoid performance measurements during a scan.
No claim of zero interference with other workloads is made.

The controller records source/binary hashes and local input identities in
`registration.json`, the process in `execution.json`, progress JSON lines in
`scan-progress.log`, and native output in `scan-result.json`. It writes
`posting_label_distribution.json` and `completion.json` only after successful
exit, accounting checks and unchanged-source checks. Failures retain
`failure.json` and logs; never import partial native stdout. Large index files
are guarded by device/inode/size/mtime/ctime and matching generations, **not a
full-content SHA256**. This is not authentication against hostile input mutation.

## Portable export and unified JSON

Export on the source machine while the scan's original inputs and registered
code/binary still exist unchanged:

```sh
python3 Tools/benchmarks/results/ssd_label_distribution.py export \
  --scan-dir /absolute/results/label-scan-run1 \
  --dataset SIFT1B \
  --output-dir /absolute/results/label-report-run1
```

This rechecks the completed scan and copies all statistics into a small portable
report with input basenames/sizes, original result/registration/completion
hashes, scanner-source hashes and explicit export provenance. It removes local
absolute paths and filesystem identities. The two files to transfer are:

```text
label-report-run1/posting_label_distribution.json
label-report-run1/completion.json
```

On any machine, including one without the index, inspect or import this report:

```sh
python3 Tools/benchmarks/results/ssd_label_distribution.py check \
  --report-dir /absolute/transferred/label-report-run1

python3 Tools/benchmarks/results/ssd_label_distribution.py publish \
  --report-dir /absolute/transferred/label-report-run1 \
  --data-dir /absolute/paper/data \
  --version-order 1

python3 Tools/benchmarks/results/update_results.py \
  --data-dir /absolute/paper/data check
```

`check` verifies the transferred report's digest, schema and accounting. It
does not rescan the original index; trust comes from the source report and its
provenance, not from a completion file being a cryptographic signature.
Publication requires no C++ compiler, NumPy import or original machine paths.

The publisher uses the repository's `update_results.py`, not a hardcoded paper
checkout or frozen campaign updater. It creates a minimal statistics-only
catalog for a new registry, or adds the `index-statistics` study to an existing
one. The dataset must already be registered in an existing catalog. Existing
selected-index/retirement policies are honored without alteration.
Use an increasing `--version-order` when replacing this statistics table with
a different physical index; one version ID cannot change order.

Publication appends a validated table event, not a performance measurement:
`current.json` -> `tables[]` -> `table_name="ssd-posting-label-proportions"`.
It preserves query points, performance views, coverage, retirements and unrelated
tables, and retains prior statistics in hash-chained history. Importing the
same exported report with the same order is idempotent even after moving the
report directory. A concurrent registry change between preflight and append
fails explicitly; rerun the command against the updated history.
Never hand-edit `current.json` or replace a live campaign's pinned updater.

## Stable result fields

`posting_label_distribution.json` uses schema 1 with `groups` containing the
three group names above. Every group has `nonempty_postings`, `stored_records`
and a `labels` array. Each label row contains:

| Field | Meaning |
| --- | --- |
| `label` | Original categorical label ID. |
| `source_vector_count` | Original corpus count from matching support metadata. |
| `postings_with_label` | Denominator of the conditional mean: positive-label postings. |
| `stored_label_occurrences` | Stored occurrences, including replication. |
| `pure_postings` | Postings whose records all have this label. |
| `mean_proportion` | Equal-posting-weight mean of unbinned positive ratios. |
| `min_proportion`, `max_proportion` | Extremes over the same positive population. |
| `histogram_counts` | 100 exact integer bin counts; their sum is `postings_with_label`. |

`distribution.edges` is `[0, 0.01, ..., 1]`. Bins are `[left,right)` except that
the last bin includes 1. Native bin assignment is
`min(99, label_count * 100 / posting_record_count)` using integer arithmetic.
Values are fractions, not percentages. The mean accumulates original ratios
in long double, not histogram midpoints; serialized means have floating-point
rounding. Counts are exact.

In unified JSON, rows add `group` and otherwise preserve these label fields.
`source` retains histogram edges, mean/zero policy, index/generation provenance,
disclosures and `group_totals`. With 201 declared labels, the table has 603 rows.
This is descriptive composition, not evidence of a causal query-speedup ablation.

## Regression command

```sh
python3 -B -m unittest discover \
  -s Tools/benchmarks/results -p 'test_ssd_label_distribution.py'
```

Tests compile the real scanner and use tiny native-format files, not a
performance run or downloaded dataset. They exercise conditional means, exact
bin edges, empty regions, absent labels, invalid generation/payload rejection,
portable transfer without original paths, index-selection guards, publication
preservation and idempotence. No production scan is needed to run the tests.
