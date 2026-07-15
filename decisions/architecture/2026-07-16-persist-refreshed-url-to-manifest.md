# On owner-feed re-resolve, persist the fresh video_url and fetched_at back into the manifest row

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-16       |
| Status   | accepted         |
| Category | architecture     |
| Tags     | download_reel, signed-url, freshness, manifest, atomic-write, store, t3 |

## Context

When `download_reel` finds a stored `video_url` older than the 24h reuse window, it re-resolves the URL via the owner feed. The question was whether to persist the freshly-resolved `video_url` + `fetched_at` back into the manifest row, or to use it once and discard it. Use-and-discard would re-resolve (a metered owner-feed traversal) on every subsequent `download_reel` call for the same aged reel.

A guardrail applied: the store invariant is "keep everything fetched, never destructively cap." Any in-place manifest rewrite had to be checked against that posture.

Scope: t3-download-reel-signed-url-refresh (issue #3, branch feat/t3-download-reel).

## Options considered

### Option A: Use-and-discard the re-resolved URL
- **Pros**: No manifest write; trivially cannot violate the store invariant.
- **Cons**: Every future `download_reel` on the same aged reel re-pays the metered re-resolve; wastes the scarce metadata budget.

### Option B: Persist fresh video_url + fetched_at back into the row via atomic CSV rewrite
- **Pros**: Subsequent calls hit the 24h fast path; spends the metered re-resolve once, not repeatedly.
- **Cons**: An in-place manifest mutation — had to be proven not a "destructive cap."

## Decision

Chose (B). On owner-feed re-resolve, `download_reel` persists the fresh `video_url` **and** `fetched_at` back into the manifest row via an atomic CSV rewrite. This was confirmed **SAFE** against the "keep everything, never destructively cap" store posture: nothing reads `fetched_at` for ranking — ranking's age filter keys on `taken_at`, not `fetched_at`. So overwriting `video_url`/`fetched_at` is a **pure freshness update**, not a cap or a data loss. It removes no reels and changes no ranking input; it only refreshes a signed URL and its resolve timestamp. The payoff is that repeat re-resolves on subsequent calls are avoided.

## Consequences

- The manifest is now mutated in-place by a read-path tool (`download_reel`), not only by fetch. The atomic CSV rewrite must preserve every other row and column intact.
- `fetched_at` semantics are affirmed as "when this row's signed URL was last resolved," decoupled from `taken_at` (the reel's real publish time used by ranking).
- Correctness of the "safe" claim depends on `fetched_at` never becoming a ranking input; if that changes, this decision must be revisited.

## Related decisions

- [URL-refresh reuse window = 24h named constant](2026-07-16-url-refresh-24h-ttl-margin.md) — the age check that triggers the re-resolve this decision persists.
- [Discovery correctness rests on per-shortcode dedupe + numeric media_id ordering](2026-07-15-discovery-correctness-rests-on-per-shortcode.md) — the store/ranking model this freshness update was checked against.

## Revisit trigger

If `fetched_at` ever becomes an input to ranking or top-N selection, re-audit whether the in-place overwrite is still a pure freshness update rather than a semantic change.
