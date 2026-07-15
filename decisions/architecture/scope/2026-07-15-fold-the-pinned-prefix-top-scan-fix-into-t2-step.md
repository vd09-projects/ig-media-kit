# Fold the pinned-prefix top_scan fix into T2 (step T2.4a) rather than a standalone T1.x ticket

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-15       |
| Status   | accepted         |
| Category | architecture/scope |
| Tags     | top_scan, pinned-reels, data-loss, scoping, T2, fetch-engine |

## Context

Instagram owner feeds are NOT strictly newest-first: accounts pin reels (older, lower `pk`) above newer ones. This was confirmed by the T1.2 natgeo live probe (`pks_descending == false`).

T1's `fetch._consume_page` in `TOP_SCAN` mode hard-stops at the first already-seen item. As a result, a pinned reel sitting at feed position 0 makes `top_scan` stop ABOVE genuinely-newer un-seen reels. The numeric watermark then advances past them and hides them permanently — a silent data-loss hole in `list_reels`' primary discovery path.

The question during the T2 (list_reels) build session was where to fix it:
- (A) fold a bounded skip-not-stop fix into T2 as step T2.4a — one PR; or
- (B) split it out as a standalone T1.x pre-req ticket, honoring the T1 author's in-code TODO intent.

## Options considered

### Option A: Fold the fix into T2 as step T2.4a (one PR)
- **Pros**: Ships the discovery deliverable without a known correctness hole; the fix is a hard dependency of T2's own acceptance anyway; single PR, no cross-ticket block. Carve-out is bounded (`PINNED_PREFIX_BOUND=3`, `TOP_SCAN` only) and keeps the caught-up == 1-page anti-regression green; every other T1 contract stays frozen.
- **Cons**: Grows T2's scope slightly beyond the nominal `list_reels` surface; diverges from the T1 author's in-code TODO that anticipated a separate ticket.

### Option B: Standalone T1.x pre-req ticket
- **Pros**: Honors the T1 author's in-code TODO intent; keeps T2 scope pure.
- **Cons**: Creates a two-PR block for a fix that T2 cannot pass acceptance without — sequencing overhead for no correctness benefit.

## Decision

Chose (A): fold the bounded skip-not-stop fix into T2 as step T2.4a.

Shipping the discovery deliverable while "newer reels below a pin vanish forever" is a correctness hole in the very thing T2 builds — it must not be deferred as debt. The carve-out is deliberately bounded: `PINNED_PREFIX_BOUND=3`, active in `TOP_SCAN` mode only, and it keeps the caught-up == 1-page anti-regression green while leaving every other T1 contract frozen. Option (B) was rejected to avoid a two-PR block for a fix that is a hard dependency of T2's acceptance anyway.

## Consequences

- T2's PR now carries a change to `fetch._consume_page` (`TOP_SCAN` skip-not-stop up to `PINNED_PREFIX_BOUND=3`), not just `list_reels` code.
- The T1 author's in-code TODO is resolved inside T2 rather than by a separate ticket — cross-reference this decision from that TODO if revisited.
- Bound is a constant (`PINNED_PREFIX_BOUND=3`); if accounts pin more than 3 reels, the bound may need raising.

## Related decisions

- [Discovery correctness rests on per-shortcode dedupe + numeric media_id ordering, never positional feed order](../../architecture/2026-07-15-discovery-correctness-rests-on-per-shortcode.md) — the standing principle this fix implements.

## Revisit trigger

If an account is observed pinning more than `PINNED_PREFIX_BOUND=3` reels, or if IG changes feed ordering semantics, revisit the bound and the skip-not-stop carve-out.
