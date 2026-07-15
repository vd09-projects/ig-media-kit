# Decision Index

<!-- 
  This file is maintained by the decision-journal skill.
  Entries are in YAML format for machine-friendly querying.
  Newest entries go at the top. Do not manually reorder.
-->

```yaml
decisions:
  - id: 2026-07-15-fold-the-pinned-prefix-top-scan-fix-into-t2-step
    title: "Fold the pinned-prefix top_scan fix into T2 (step T2.4a) rather than a standalone T1.x ticket"
    date: 2026-07-15
    status: accepted
    category: architecture/scope
    tags: [top_scan, pinned-reels, data-loss, scoping, T2, fetch-engine]
    path: architecture/scope/2026-07-15-fold-the-pinned-prefix-top-scan-fix-into-t2-step.md
    summary: "Fixed the pinned-prefix top_scan data-loss hole inside T2 as bounded step T2.4a (PINNED_PREFIX_BOUND=3, TOP_SCAN only) rather than splitting a separate T1.x ticket, because it is a hard dependency of T2's acceptance."

  - id: 2026-07-15-serve-from-store-no-network-gate-keys-on
    title: "Serve-from-store no-network gate keys on coverage contiguity, not raw pool count"
    date: 2026-07-15
    status: accepted
    category: architecture
    tags: [serve-from-store, coverage-contiguity, pool-depth, scan_depth, gaps, list_reels]
    path: architecture/2026-07-15-serve-from-store-no-network-gate-keys-on.md
    summary: "Gate the zero-network serve-from-store path on coverage_contiguous (one joined segment reaching scan_depth or account end), splitting it from pool_depth (raw count), so a count>=90-with-gap pool keeps deepening instead of freezing the gap forever."

  - id: 2026-07-15-discovery-correctness-rests-on-per-shortcode
    title: "Discovery correctness rests on per-shortcode dedupe + numeric media_id ordering, never positional feed order"
    date: 2026-07-15
    status: accepted
    category: architecture
    tags: [discovery, dedupe, media_id, watermark, ordering, principle, fetch-engine]
    path: architecture/2026-07-15-discovery-correctness-rests-on-per-shortcode.md
    summary: "Standing principle: since IG feeds are not strictly pk-descending under pinning, discovery relies on per-shortcode dedupe (authoritative caught-up signal) and a monotonic numeric media_id watermark/gap predicates, never positional/newest-first feed order."
```
