# Discovery correctness rests on per-shortcode dedupe + numeric media_id ordering, never positional feed order

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-15       |
| Status   | accepted         |
| Category | architecture     |
| Tags     | discovery, dedupe, media_id, watermark, ordering, principle, fetch-engine |

## Context

Because the IG owner feed is not strictly `pk`-descending under pinning (confirmed by the T1.2 natgeo probe, `pks_descending == false`), no discovery logic may assume positional/newest-first order. This decision states the standing principle that governs all fetch/discovery work in the project.

## Decision

Discovery correctness rests on the following, and never on positional feed order:

- **Per-shortcode dedupe is the authoritative caught-up signal.** Whether we have seen a reel is decided by its shortcode, not its position in the feed.
- **The numeric `media_id` watermark is a monotonic backstop only.** `high_water` advances to the max numeric `media_id` seen and is never bumped backward by a low-`pk` pin.
- **Coverage-gap predicates are expressed in numeric-`media_id` terms**, so a top pin can never open a phantom segment.

This is a standing principle for all future fetch/discovery work in this project — any new discovery logic must conform to it.

## Consequences

- Any code that infers "newest" or "caught up" from feed position is a bug by this principle.
- The watermark and gap predicates operate in numeric-`media_id` space, decoupled from feed order.
- Future `product_type` handlers (image/carousel/story) inherit this constraint.

## Related decisions

- [Fold the pinned-prefix top_scan fix into T2 (step T2.4a) rather than a standalone T1.x ticket](../architecture/scope/2026-07-15-fold-the-pinned-prefix-top-scan-fix-into-t2-step.md) — the concrete fix that implements this principle in TOP_SCAN mode.
- [Serve-from-store no-network gate keys on coverage contiguity, not raw pool count](2026-07-15-serve-from-store-no-network-gate-keys-on.md) — gap predicates expressed in numeric-media_id terms per this principle.

## Revisit trigger

If Instagram ever guarantees strictly newest-first feed ordering (removing pinning-induced reordering), the positional-order prohibition could be relaxed — but only after a live probe confirms it.
