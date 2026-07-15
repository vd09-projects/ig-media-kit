# URL-refresh reuse window = 24h named constant, a safety margin under the measured ~36h fbcdn signed-URL TTL

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-16       |
| Status   | accepted         |
| Category | architecture     |
| Tags     | download_reel, signed-url, ttl, freshness, fbcdn, constant, t3 |

## Context

fbcdn `video_versions[0].url` is a signed URL whose `oe=` parameter expires — the measured TTL is ~36h. `download_reel` must decide, for a given stored reel, whether the `video_url` already in the manifest is still fetchable or must be re-resolved via the owner feed before the mp4 GET. The store already records `video_url` + `fetched_at` per row (per the store invariant), so the age of the stored URL is known.

Scope: t3-download-reel-signed-url-refresh (issue #3, branch feat/t3-download-reel).

## Options considered

### Option A: Reuse stored URL until it actually 302s, then re-resolve reactively
- **Pros**: No wasted re-resolves; only pays the metered owner-feed cost on a real miss.
- **Cons**: Couples download success to a metered IG round-trip discovered mid-download; a failed GET is a worse signal than a cheap age check; races the TTL boundary.

### Option B: Refresh window as a config knob
- **Pros**: Operator-tunable.
- **Cons**: Premature surface area; no evidence anyone needs to tune it; a knob invites mis-tuning above the real TTL.

### Option C: Fixed named module constant below the measured TTL
- **Pros**: Simple, predictable, comfortably under the ~36h ceiling; proactive (re-resolve before expiry, not after a failed GET).
- **Cons**: Not runtime-tunable; the margin is a judgement call.

## Decision

Chose (C). A named module constant sets the URL-refresh reuse window to **24h**. `download_reel` reuses a stored `video_url` when `now - fetched_at < 24h`; otherwise it re-resolves the URL via the owner feed before downloading. 24h is a comfortable safety margin under the measured ~36h signed-URL TTL — it absorbs clock skew and slow-drip staleness without racing the `oe=` boundary. A config knob was **deliberately deferred**: the constant can be promoted to config later if a real need appears, but it is not exposed now.

## Consequences

- A stored URL between 24h and ~36h old is still technically valid but gets re-resolved anyway — a small, deliberate waste traded for TTL safety.
- The margin is coupled to the measured ~36h figure; if IG shortens the signed-URL TTL below 24h, the constant must drop (see revisit trigger).
- Keeping it a constant (not config) keeps the `download_reel` contract narrow.

## Related decisions

- [On owner-feed re-resolve, persist the fresh video_url and fetched_at back into the manifest row](2026-07-16-persist-refreshed-url-to-manifest.md) — what happens after the 24h check fires and a re-resolve occurs.

## Revisit trigger

If a live probe ever measures the fbcdn signed-URL TTL below ~30h, or if downloads start failing with 302s inside the 24h window, drop the constant and/or promote it to a config knob.
