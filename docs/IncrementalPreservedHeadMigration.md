# Preserved-head native migration

`Tools/IncrementalSpannMigration.py` orchestrates an explicitly authorized,
graphless UInt8 SPANN migration into a new directory. It uses the official
native `indexbuilder` for the existing ordered H1 vectors and
`compactspannindex` for authenticated metadata and canonical upper catalogs.
It does not select heads, read the base corpus for selection, or construct or
rewrite SSD postings. The complete existing UInt8 prefix index must pass
graph construction, byte-exact H1 order, conversion, native reload and native
V8/V9 result/work parity before the production stage can start.

The graph recipe comes from the source build's `BuildHead`, not its dummy
head index. Only the explicitly recorded worker count changes. An isolated
official-builder frontend propagates `SaveIndex` failures and links frozen,
accepted native archives. No query implementation is copied into this driver.
The old H5 vector file receives an explicit `.level4` alias in the new view.
Configuration migration records each changed or removed key; obsolete upper
ANN options are not query fallbacks.

## Source protection and resources

Every native subprocess inherits unprivileged Linux Landlock confinement.
ABI 3 or later is required; writes, truncation and cross-boundary hard links
outside the new run are prohibited. Preparation tests symbolic-link writes,
truncation and hard-link escape using a private disposable sentinel, without
attempting to modify any original artifact. This changes neither host mounts
nor original permissions. Read-only source links remain real external
dependencies, not newly owned bytes. Consumers started outside this controller
must apply equivalent confinement before using these views for maintenance.

Source device/inode/length/mtime/ctime inventories are checked before and after
each stage. The TB posting payload is never hashed for a manifest. Saved H1
vectors are compared byte-for-byte against the canonical source in one
streaming pass, computing their digest during that required identity check.
Native conversion authenticates metadata and checks every upper representative
against its flattened direct H1 mapping.

Workers, CPU affinity and NUMA memory placement are explicit. The controller
records available host memory/disk, large 64-bit extents and a 256 GiB address
space ceiling. Native INIs hold graph/search parameters; there are no data or
search environment overrides. Query smoke reports are not a scalability
benchmark or recall acceptance threshold.

## Stages and restart

Preparation creates `plan.json`, frozen runtime/source artifacts, source
inventories, the graph INIs and resource/protection records. Build the isolated
frontend with the provided `Tools/incremental_h1/CMakeLists.txt`, using the
frozen source and archive paths. Its authenticated path is recorded in
`builder-runtime.json`.

Run the persisted controller with `preflight --run RUN`, then `run --run RUN`.
Keep the controller attached to the CLI session. `status.json` and each
`*.stage.json` report the exact native child PID, command, log size and RSS
heartbeat. `*.resources.txt` is GNU time's post-exit child high-water
measurement, distinct from the loader's earlier self-sampled RSS/HWM.

Completed stages are reused only with source/runtime identity checks and
saved graph output identity checks. A failed or interrupted stage retains its
output and refuses automatic overwrite. Resolve and archive that specific
partial stage explicitly before retrying; do not delete the run or rerun head
selection. A single-controller lock prevents simultaneous writers.
Only successful native conversion, load/query checks and final source safety
checks produce `completion.json`. Partial graph output is never a ready index.
