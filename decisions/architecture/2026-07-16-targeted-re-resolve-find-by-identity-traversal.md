# Targeted owner-feed re-resolve is a distinct find-by-identity traversal, not a reuse of fetch_window(TOP_SCAN)

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-16       |
| Status   | accepted         |
| Category | architecture     |
| Tags     | download_reel, re-resolve, fetch-engine, top_scan, identity-match, media_id, t3 |

## Context

When `download_reel` needs to re-resolve an aged-out `video_url`, it must walk the owner's feed to find that one specific reel and read its fresh signed URL. The tempting reuse was `fetch_window(TOP_SCAN)` — the existing paced owner-feed traversal. But TOP_SCAN's discovery semantics do the wrong thing here: it treats an already-seen reel as the caught-up boundary (per-shortcode dedupe is its caught-up signal) and collects nothing, so the specific target — which is by definition already seen — would never be returned.

Scope: t3-download-reel-signed-url-refresh (issue #3, branch feat/t3-download-reel).

## Options considered

### Option A: Reuse fetch_window(TOP_SCAN)
- **Pros**: One traversal primitive, no new code path.
- **Cons**: TOP_SCAN stops at the caught-up boundary; the target reel (already seen) sits behind that boundary and is never collected. Semantically incompatible with "find this specific already-known item."

### Option B: Distinct find-by-identity traversal
- **Pros**: Correct semantics — walks pages until identity match; independent of discovery's caught-up logic.
- **Cons**: A second traversal mode to maintain alongside TOP_SCAN.

## Decision

Chose (B). Targeted re-resolve is a **distinct find-by-identity traversal**, not a reuse of `fetch_window(TOP_SCAN)`. It walks owner-feed pages matching each item by **shortcode / numeric media_id** — never by positional feed order (honoring the standing discovery principle) — until it hits the identity match or exhausts the polite page budget. It is a "find-by-identity" walk, orthogonal to TOP_SCAN's "collect-until-caught-up" walk. Both share the paced, page-capped, stop-on-401 politeness envelope, but their stop conditions differ: TOP_SCAN stops at the caught-up boundary; find-by-identity stops at the matched item (or the budget).

## Consequences

- The fetch engine now has two traversal intents over the same paced primitive: collect-until-caught-up (TOP_SCAN) and find-by-identity (re-resolve).
- Identity matching keys on shortcode / numeric media_id, never feed position — consistent with the project-wide ordering principle.
- Both traversals still obey the same politeness rules (pacing, page cap, stop + partial on first 401).

## Related decisions

- [Discovery correctness rests on per-shortcode dedupe + numeric media_id ordering, never positional feed order](2026-07-15-discovery-correctness-rests-on-per-shortcode.md) — the principle that forbids positional matching, which this traversal honors.
- [Aged-out / not-found-in-budget re-resolve returns a typed error with partial=False](../architecture/api-contract/2026-07-16-aged-out-typed-error-vs-stop-signal-partial.md) — what happens when the find-by-identity walk exhausts its budget.

## Revisit trigger

If the two traversal modes accumulate enough shared machinery that a single parameterized primitive is cleaner, revisit unifying them behind one paced walker with a pluggable stop condition.
