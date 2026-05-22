# Tail Segmentation Experiment — Failure Note

## Context

Building on the **K_replica=2 + independent tail buffer cap** baseline (`feature/pertag-sparse @ 4c610d0`),
this branch investigated whether **per-subset segmented tail routing** could push K_replica higher (≥ 8)
without losing the filter-side QPS that small K_replica delivers on SIFT-1M / tenant_0.

The hypothesis was:

> Larger K_replica boosts recall ceiling, but causes posting-mask saturation and tail-region pollution.
> If we partition each head's tail region by **subset (hierarchical ACL bucket)** and let the query read
> only the relevant segment at search time, we should preserve K=4's pruning effect while keeping K=8's
> recall headroom.

## What was built

Code lives in this commit (un-merged):

- `AnnService/inc/Core/SPANN/ExtraDynamicSearcher.h`
  - **Phase 4 build (lines ~3260-3575)**: subset-aware tail routing using a per-subset centroid map
    (`m_headSelectCb`). For each non-head vector in subset `S`, find 2K nearest centroids inside `S`,
    map back to global heads, dedupe to K distinct heads. Tail edges are encoded with
    `e.distance = 1e30 + S` so `SortSelections` groups tail entries by subset within each head's region.
  - **Sidecars**: `posting_segment_lengths.bin` (per-head per-subset segment length),
    `tail_subindex.bin` (subset-local k-NN graph), `tag_subset_mask.bin`.
  - **Per-subset tail cap** (final iteration): atomic counter array
    `postingTailSegLen[h * N_sub + S]`, with optimistic add-time check + authoritative CAS at flush,
    sized via `SPTAG_TAIL_SEGMENT_CAP` (default `m_tailBufferSizeLimit / N_sub`).
  - **Search path (lines ~2200-2400)**: three new branches gated by env vars
    - `SPTAG_TAIL_ONLY_FILTER=1` — skip pure region, byte-range read only target segment.
    - `SPTAG_PURE_PLUS_SEG_FILTER=1` — page-aligned read of `[0, target_seg_end]`, scan pure + target seg.
    - `useSegmentedTailScan` (default when sidecars present) — full I/O but CPU scan limited to pure + target seg.
  - **HierarchicalPostingMask** prefilter (was already present in 4c610d0): OR of all member-vector tags
    per head; drop postings whose mask doesn't intersect query mask.

- `AnnService/inc/Helper/KeyValueIO.h`, `AnnService/inc/Core/SPANN/ExtraFileController.{h,cpp}`:
  `MultiGet`/`ReadBlocks` overloads with per-key `skipBytes` + `readBytes` (so we can byte-range read
  just a posting suffix without loading the head).

- `Wrappers/src/CoreInterface.cpp`: plumbing for the new sidecars + segment-aware search path.

## Result — does not work

Best filter-side QPS at R ≥ 0.95 (SIFT-1M, tenant_0, ratio=0.069):

| level    | K=4 baseline (4c610d0) | K=8 no-seg | K=8 segmented + global cap | **K=8 segmented + per-subset cap** |
|----------|------------------------|------------|----------------------------|-------------------------------------|
| org      | 194 (R=0.95)           | 179 (R=0.954) | 107 (R=0.96)            | **200 (R=0.951)**                   |
| dept     | 126 (R=0.959)          | 102 (R=0.95)  |  84 (R=0.952)           | **129 (R=0.950)**                   |
| team     |  98 (R=0.951)          |  48 (R=0.966) |  53 (R=0.950)           |  **87 (R=0.959)**                   |
| project  | 330 (R=1.00)           | 343 (R=1.00)  | 337 (R=1.00)            | 337 (R=1.00)                        |
| unfilter | 327 (R=0.952)          | 325 (R=0.951) | 293 (R=0.962)           | 283 (R=0.969)                       |

Posting size distribution:

| index                       | total tail records | mean | p99 | max |
|----------------------------|--------------------|------|-----|-----|
| K=8 no-seg                  | 4.02M              | ~71  | -   | -   |
| K=8 segmented (global cap)  | 4.39M              | 77.9 | 183 | 183 (saturated)|
| K=8 segmented (per-sub cap) | 2.61M              | 46.4 | 100 | 170 |

**The per-subset cap successfully eliminates cap saturation** (p99 183 → 100) and substantially
recovers QPS over the segmented-with-global-cap configuration (org +87 %, dept +53 %, team +65 %).
However, the final QPS only **ties or marginally beats K=8 no-seg** and remains **at-or-below the
small-K (K=2-4) baseline** on org/dept/team. The whole detour produced no usable win.

## Why it failed

1. **K_replica=8 inflates HierarchicalPostingMask saturation.** With more tail edges per head, each
   head's union mask gets denser, and the prefilter drop rate falls. Even with subset-coherent routing,
   the mask is built head-wide, not segment-wide, so segmentation does nothing for prefilter pruning.

2. **Per-subset cap is just total-tail throttling in disguise.** Cutting per-(head, subset) to
   `tailBuf/N_sub` reduces total tail edges from 4.4M to 2.6M — i.e. below K=4's natural tail volume.
   At that point the recall ceiling that motivated K=8 is gone; we are paying K=8's prefilter cost
   without K=8's recall payoff.

3. **Segment-restricted I/O barely saves bytes.** Once the cap brings per-posting size down to
   `max ≤ 170` records, the target-segment read is only a constant-factor fraction smaller than the
   full read. The PCIe MultiGet cost is dominated by per-key seek overhead, not bytes transferred,
   so trimming the suffix doesn't move the needle.

4. **Subset-coherent routing concentrates tail mass on a few "subset-popular" heads.** Without the
   per-subset cap the global cap saturates at 183, and *with* the per-subset cap we just throw the
   over-cap edges away — there is no benign place for them to land.

5. **The bottleneck for large K is head overlap, not tail layout.** Segmentation targets the tail
   region, but pure-region mask saturation is what is actually killing K=8 filter QPS.
   Segmentation can't fix that.

## Conclusion

Per-subset tail segmentation is **abandoned** for this workload. The maintained baseline reverts to
`feature/pertag-sparse @ 4c610d0` — small K_replica (2-4), no segmentation, with the independent tail
buffer cap from `bdc414f`. This experimental branch is preserved for archival reference only.

Subsequent ideas, if revisited, should focus on **prefilter quality at large K** (e.g. per-segment
posting masks, learned head clustering, or routing that explicitly minimizes mask overlap) rather
than tail I/O shape.
