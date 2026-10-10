# Sampled upper posting sizing (V6)

This opt-in upper-only reconstruction keeps the native O assignment, retained-O
label selection/deficit expansion, H construction search, RNG replica ceiling,
and adjacent H2..H5 layout. It does not rebuild H1/SSD, change search policy,
or replace the source Ratio used by the local admission census.

Set both native `[SelectHead]` parameters:

```ini
HierarchyLocalTarget=512
HierarchyLocalWindow=4096
HierarchyTargetPostingSize=128
HierarchySizingSampleHeads=262144
```

Both new settings default to zero, preserving V4/V5 construction. The first
version requires local admission. They are authenticated, immutable build
settings, not search overlays. The posting target is the mean number of child
IDs in a **nonempty single-label row**, not all labels of a physical parent,
not unique transitive H1 coverage, and not a minimum or a hard row-size limit.

## Algorithm and bounds

At each transition, count the actual eligible physical children and their
admitted `(child,label)` pairs. The next transition uses the newly built lower
rows, not original-vector selectivity as a substitute for amplified support.

Start with at most `HierarchySizingSampleHeads` different physical children
from a stable, uniform, without-replacement sampler. Estimator version 2 then
ensures at least min(16, full label population) sampled children per admitted
label. For deficient labels only, one metadata-only pass selects bounded
bottom-hash candidates. Replace surplus uniform-core children without losing
any label's minimum. Keep the physical sample budget fixed; overlapping labels
do not duplicate physical children. An insufficient budget fails explicitly.
No vector distances or lower ANN searches are added by this coverage step.
Keep every admitted label of each sampled child. Sampling does not reseed or
replace upstream BKT/TPT RNGs and never builds separate per-label ANN indexes.
The first uniform-only version remains readable as estimator version 1.

Run a bounded pilot through the **same** native spatial center selection,
temporary BKT, O support selection and H/RNG assignment as production. Start at
the source-capped legacy physical ratio. For each label `t`, measure its mean
actual copies `r[t]`; weight these by the exact full-input pair populations:

```text
estimated references E = sum(full_pair_population[t] * pilot_copies[t])
rows per parent s      = pilot_nonempty_label_rows / pilot_physical_parents
requested parents P    = ceil(E / (target * s))
effective ratio        = P / eligible_physical_children
```

Clamp P to [1, min(eligible physical children, source tier cap)]. If the count
changes, perform at most one more pilot on the same sample at the revised
ratio. There is no unbounded tuning loop or full-layer trial construction.
A small layer fully covered by the sample can reuse an exact-count pilot,
including its temporary assignment index for H1 spatial entries.
Otherwise build the full layer once at the final estimated count.
The two pilots together search at most twice the configured sampled physical
heads through O and twice their admitted logical pairs through H.

Downsampling changes spatial density, minimum coverage enriches rare labels,
finite support floors affect small pilots, and r/s themselves depend on the
ratio. Exact full-label populations weight replica estimates; they do not make
the enriched pilot's parent/label distribution identical to the full layer.
The estimate is not an
unbiased prediction of final ANN membership, a convergence guarantee, or a
promise of faster total construction. Locality still comes from native spatial
selection and assignment; no distant same-label rows are concatenated merely
to fill the target. H1 spatial-entry assignment remains a separate full-input
stage. Normal native assignment failures remain explicit.

## Provenance and verification

V6 retains V5 local admission and appends a fixed 664-byte sizing record, covered
by the hierarchy fingerprint. It records settings, exact per-tier input/output
counts, both pilot counts, sampled physical-ID hashes and estimated references.
Its estimator version distinguishes uniform-only and bounded coverage sampling.
Load validates the recorded count calculation against source caps, actual CSR
rows, references, complete admitted pair coverage and native replica limits.
V1..V5 bytes and their query behavior are not reinterpreted.

The rebuild completion JSON contains a `sizing` section. Build logs additionally
report per-label pilot populations/replicas and actual row counts/means, so a
global average cannot conceal tiny sparse-label rows. Compare these actual
measurements with the target before treating a build as a successful density
experiment. A completed build is not a recall/QPS result.
