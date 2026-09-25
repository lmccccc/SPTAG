# Native compact storage

The compact representation changes storage, not search. Native H1 navigation
still completes first; the post-graph C/nprobe trigger, anchors, explicit extra
budget, full-row completion and exact final filtering are unchanged.

## Head metadata

V9 retains the V8 16-byte header shape, followed by the five index-scoped width
values, availability/region flags, numeric-domain fingerprint, generation and
content fingerprint. It appends a uint32 schema-text length and canonical
original-column schema text before the packed records. The content hash covers
the schema as well as all V8 authenticated fields and record bytes.

Records use an index-owned descriptor, not per-head headers. Own attributes are
uint32 values in original-column order. Only declared categorical lanes occupy
space; numeric lanes retain their original 256-bit bucket masks and dense lane
mapping. Numeric-only schemas need no categorical payload. H and O remain
independent, including strict numeric boundary/intersection semantics.

For one categorical and one numeric column with H/O and 256-bit masks, the
current lossless record is **176 bytes**:

| Component | Bytes/head |
|---|---:|
| Coarse H/O categorical masks | 64 |
| Own attributes | 8 |
| Column-specific H categorical mask | 32 |
| VID, bundle ID, head-only flag, alignment | 8 |
| H/O numeric masks | 64 |

The 144-byte projection is not used by blindly aliasing H's two categorical
blocks. The persisted SIFT1M blocks agree, but runtime merging of own values
changes the column-specific block for 269 heads without changing the coarse H
block. Both meanings remain independently writable and queryable.

V8 loading without an explicit schema retains the original layout. With an
explicit schema, conversion authenticates the complete original bytes while
reading at most 4096 old records at a time beside the new packed output. Inactive
categorical lanes must be empty and available own values outside the schema must
be sentinel values. Ambiguous or contradictory layouts fail rather than lose
fields. V9 rejects wrong schemas, domains/generations, sizes and content hashes.
Typed own/column views are tied to the index storage lifetime; no API returns a
scratch POD pointer covering bytes absent from a compact record.
Region access remains lazy: O admission does not construct an unused H-column
view. This preserves the original region-specific reads without changing
signature evaluation, precision or pruning.
Admission consumes the small typed view by value rather than taking the address
of a temporary view. Numeric access retains its independent row guards and is
resolved before constructing the categorical view. Bulk column projection
resolves each column offset/absence once and copies only its declared words;
absent columns remain zero. Neither change adds a per-head or query cache.

## Upper catalogs

Canonical vector files have a 32-byte header (magic `0x39435648`, version 1,
H1 count, dimension, value type, upper count and a uint64 content fingerprint),
followed by one uint32 direct H1 physical ID per upper representative. The hash
covers the header fields, IDs and referenced vector bytes. Native loading checks
type, dimension, bounds and content before exposing a read-only view. Distances
perform one direct H1 lookup, not a chain through intervening layers.

Legacy independent and disjoint catalogs remain readable. Already-shared
payloads must not be counted as new savings. Existing uint64 sampling files are
retained as authenticated geometry/provenance; their load-time temporary arrays
are distinct from the retained uint32 direct maps.
With a full unquantized H1 owner, legacy upper payloads are compared against
flattened sampling IDs in bounded approximately 1 MiB chunks and exposed as the
same direct views, not retained as hidden complete vector copies. Legacy
graphless/disjoint owners retain their original ownership model.

Upper H/O numeric and categorical summaries retain their meanings. The H
categorical allocation can reference the existing CSR signature array only when
every word of the entire layer is identical at build/refresh. Equality is checked
on contents, not inferred from a shared type or one routing column. Capacity is
released for such aliases; other H/O arrays remain owned and must be counted.

## Reconstruction

Build `compactspannindex` in an isolated output directory, then run:

```sh
compactspannindex SOURCE/tenant_0 NEW/tenant_0
```

`NEW` must exist and `NEW/tenant_0` must not. The tool loads the original full,
unquantized BKT H1, reuses its IDs/graph, authenticates metadata, validates every
upper representative against its mapped H1 vector, and writes compact native
files. Unchanged graph, posting, owner/support and SSD artifacts are referenced
with symlinks to immutable originals. The redundant selection-vector checkpoint
and unused graphless-owned/upper-ANN artifacts are not dependencies of the new
full-H1 view and are omitted from its references. Root tenant manifests can
be referenced unchanged by the enclosing reconstruction workflow.

`compact-conversion.json` records conversion/load elapsed time, peak process RSS,
metadata payload and upper-map bytes. It is reconstruction time, **not** a new
H1-selection or SSD-build measurement. A failed conversion retains its partial
destination for inspection and never rewrites the source.

Acceptance requires a real native reload, final/head/signature/SSD-work parity,
separate normal paired timing and explicit payload/capacity accounting. Neither
the stride formula nor diagnostic QPS establishes a performance pass. The 1B
incremental phase remains gated on accepted SIFT1M evidence.
