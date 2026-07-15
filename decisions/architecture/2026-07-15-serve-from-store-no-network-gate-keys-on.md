# Serve-from-store no-network gate keys on coverage contiguity, not raw pool count

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-15       |
| Status   | accepted         |
| Category | architecture     |
| Tags     | serve-from-store, coverage-contiguity, pool-depth, scan_depth, gaps, list_reels |

## Context

A stored pool can reach `count >= scan_depth` (90) while still containing an internal coverage gap — a second segment opened by a burst of more than one window of new posts. The zero-network "serve-from-store" fast path in `list_reels` needs a gate that decides when the store is complete enough to answer without hitting IG.

If that gate keyed on raw pool count, a `count >= 90`-but-gapped pool would be frozen permanently: the gap reels become invisible to `list_reels` forever, while the envelope falsely reports "complete."

## Options considered

### Option A: Gate on raw pool count (`count >= scan_depth`)
- **Pros**: Trivial to compute; one integer.
- **Cons**: A pool that hits 90 with an internal gap freezes the gap permanently and reports a false "complete." Silent data loss.

### Option B: Gate on coverage contiguity
- **Pros**: Distinguishes "enough items" from "enough contiguous coverage"; a gapped pool keeps deepening to bridge the gap; envelope "complete" reflects reality.
- **Cons**: Requires tracking two metrics and a contiguity predicate rather than one count.

## Decision

Chose (B). Split the two concepts:
- `pool_depth` = raw count, an effort metric.
- `coverage_contiguous` = a single joined segment reaching `scan_depth` OR the account's real end.

Serve-from-store fires only when `coverage_contiguous`. A `count >= 90`-with-gap pool keeps deepening to bridge the gap rather than being served. The envelope's `complete` flag surfaces contiguity, not count.

## Consequences

- State must track segment boundaries / contiguity, not just a running count.
- Envelope semantics change: `complete` means "contiguous coverage to depth or account end," not "90+ items stored."
- A burst of >1 window of new posts opens a second segment that the fetcher must later bridge before the fast path re-engages.

## Related decisions

- [Discovery correctness rests on per-shortcode dedupe + numeric media_id ordering, never positional feed order](2026-07-15-discovery-correctness-rests-on-per-shortcode.md) — coverage-gap predicates are expressed in numeric-media_id terms per that principle.

## Revisit trigger

If tracking segment contiguity proves too costly, or if IG feed semantics change such that gaps cannot occur, revisit whether a simpler count-based gate suffices.
