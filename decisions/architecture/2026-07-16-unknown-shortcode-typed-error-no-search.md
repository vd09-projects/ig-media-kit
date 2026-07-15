# Unknown shortcode returns a typed error envelope — no IG-wide search fallback

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-16       |
| Status   | accepted         |
| Category | architecture     |
| Tags     | download_reel, shortcode, error-envelope, anonymous, store-as-index, t3 |

## Context

`download_reel` takes a shortcode. If that shortcode is not found in any store CSV, the tool has no owner handle to re-resolve against. The question: should it attempt to resolve an arbitrary shortcode's owner from IG (a search fallback), or fail with a typed error?

The anonymous-only invariant is load-bearing here, and a known gotcha constrains it: the per-media anonymous endpoint `/api/v1/media/{id}/info/` is **DEAD** (302 → login). There is no confirmed anonymous path from a bare shortcode to its owner.

Scope: t3-download-reel-signed-url-refresh (issue #3, branch feat/t3-download-reel).

## Options considered

### Option A: IG-wide search / resolve fallback for unknown shortcodes
- **Pros**: `download_reel` would work on any shortcode, not just stored ones.
- **Cons**: No safe **anonymous** way to do it — the per-media endpoint 302s to login; any working path would require auth, violating the anonymous-only invariant. Adds an unbounded, unmetered IG surface.

### Option B: Typed error envelope; store is the only index
- **Pros**: Honors the anonymous invariant; keeps the tool's IG surface bounded to owner feeds it already knows; predictable, branchable failure.
- **Cons**: `download_reel` only works on shortcodes already discovered into the store.

## Decision

Chose (B). An unknown shortcode — not found in any store CSV — returns a **typed error envelope**. There is no IG-wide search fallback. This is forced, not merely preferred: because `/api/v1/media/{id}/info/` is dead anonymously, there is no safe anonymous way to resolve an arbitrary shortcode's owner, so the **store is the only index** from shortcode to owner. `download_reel` resolves owner (hence re-resolve target) exclusively through the store.

## Consequences

- `download_reel` is a store-scoped operation: you can only download reels that discovery has already put in a manifest.
- The failure is a distinct typed error (unknown shortcode), letting an MCP consumer distinguish "not in store" from other failure modes.
- If IG ever exposes an anonymous shortcode→owner resolution, this constraint could relax — but only via a live-probed, anonymous path.

## Related decisions

- [Serve-from-store no-network gate keys on coverage contiguity](2026-07-15-serve-from-store-no-network-gate-keys-on.md) — same store-as-source-of-truth posture on the read path.
- [Aged-out / not-found-in-budget re-resolve returns a typed error with partial=False](../architecture/api-contract/2026-07-16-aged-out-typed-error-vs-stop-signal-partial.md) — the other typed-error outcome of download_reel.

## Revisit trigger

If a live probe confirms an anonymous endpoint that resolves an arbitrary shortcode to its owner without login, reconsider whether a bounded resolve fallback is worth adding.
