# Centroid Re-election Result (SPTAG_RESELECT_CENTROIDS)

## Hypothesis
Replace each SPANN head with the in-posting real vector closest to the posting's
geometric mean. The intuition: heads chosen by SelectHeadDynamically may sit on
posting boundaries; medoid heads would be more "central" representatives, plausibly
improving query→head distance correlation with query→posting-content relevance.

## Implementation
- `ExtraDynamicSearcher<T>::ReselectHeadCentroids` (env-gated by `SPTAG_RESELECT_CENTROIDS=1`):
  walks `selections` (sorted by head idx), computes per-posting mean (double),
  picks closest member, rewrites HeadIndex/vectors.bin and HeadVectorIDs.bin.
  Postings on disk stay keyed by head index, so they are NOT touched.
- `SPANNIndex::BuildIndexInternal`: after BuildSSDIndex, rebuilds the HeadIndex
  BKT (tree+graph) from updated vectors.bin and reloads `m_index` +
  `m_vectorTranslateMap`. Default off (bit-exact baseline preserved).

## Run
- Dataset: SIFT-1M tenant_0 (404,819 vec)
- Build: `feature/hybrid-recentroid` @ `1df6135`, `SPTAG_HYBRID_WEIGHT=0` (pure geo
  baseline), `SPTAG_RESELECT_CENTROIDS=1`.
- 80,924 heads. Recentroid swapped 80,697/80,924 (99.7%); 227 empty postings kept.
- Per-head L2 shift on (cosine-normalized) unit vectors: mean 0.34, max 0.77.

## Result (100% selectivity, full-pool sweep)

| nprobe | baseline R | recentroid R | Δ |
|--------|------------|--------------|------|
| 16  | 0.867 | 0.507 | −36 pp |
| 32  | 0.946 | 0.579 | −37 pp |
| 64  | 0.977 | 0.641 | −34 pp |
| 128 | 0.995 | 0.672 | −32 pp |
| 200 | 0.996 | 0.689 | **−31 pp (ceiling)** |

QPS curves essentially identical (same posting scan cost).

## Diagnosis
- SPANN heads are chosen by a **coverage-aware** algorithm (BKT-based head
  selection), not k-means; they intentionally include boundary representatives so
  that query→nearest-head→posting navigation is stable across the manifold.
- Medoids systematically drift toward dense regions. Sparse boundary regions
  lose their representative head; the BKT now picks a different (geometrically
  closer to medoid, but topologically wrong) head for boundary queries.
- Postings on disk are still attached to old-head positions. With heads moved,
  query→head correlates worse with query→posting content. Recall drops sharply.

## Conclusion
Naïve single-shot centroid re-election (option B) **degrades SPANN recall by
~30 pp at the ceiling** on this dataset. To make recentroid pay off, would need
either:
1. One-round E-M: re-run `ApproximateRNG` after head swap so posting members
   match new heads (doubles build cost; not tested).
2. Constrained swap: only when medoid is geometrically close to old head,
   preserving coverage (e.g., gated by L2 < ε).
3. Selective swap: only on heads with tight, unimodal postings (low variance).

Lock this in as a negative result; do not chain further work on this branch
without first trying option 1 (E-M re-RNG).
