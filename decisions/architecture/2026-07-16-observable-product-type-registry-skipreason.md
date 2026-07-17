# product_type dispatch = named handler registry + typed SkipReason (observable switch, not a silent drop)

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-16       |
| Status   | accepted         |
| Category | architecture     |
| Tags     | product_type, extensibility, registry, skip-reason, normalize, clips, observable, stub, t5 |

## Context

The feed normalizer hard-dropped any item whose `product_type != "clips"` by returning `None` (`fetch.py:182`) — indistinguishable from a clips-path drop of a malformed item. CLAUDE.md commits to `product_type` dispatch being "a switch, not a rewrite" (clips now; image/carousel/story later). But a switch whose only output is an untestable `None` can't demonstrate the extension seam, and a test can't tell "routed to a stub handler" from "dropped." T5 had to formalize the seam *and make its outcome observable*, without changing what actually reaches the store (clips-only today).

## Options considered

### Option A: Named `_PRODUCT_HANDLERS` registry + typed `SkipReason` returned in a `NormalizeResult`
- **Pros**: Dispatch outcome is observable — a test distinguishes `UNSUPPORTED_PRODUCT_TYPE` (routed to stub) from `MALFORMED` (bad clip) from a real reel; adding a type is a single localized diff (register a handler); a disabled `image` stub demonstrates the seam while shipping clips-only; a self-describing follow-up marker tracks the disabled seam.
- **Cons**: Two return shapes to maintain (`normalize_item_routed` for observability + `normalize_item` thin wrapper for the byte-compatible None contract); slightly more surface than a bare `if`.

### Option B: Keep the hard `!= "clips"` drop
- **Pros**: Minimal code.
- **Cons**: Not observable (both outcomes are `None`); the "switch, not a rewrite" claim is unprovable; no demonstrated extension path; adding a type later is a normalizer edit, not a registration.

## Decision

Convert the hard drop into a named `_PRODUCT_HANDLERS` registry keyed by `product_type`, returning a typed `SkipReason` (`UNSUPPORTED_PRODUCT_TYPE` / `MALFORMED`) inside a `NormalizeResult`. `normalize_item_routed` exposes the routing; `normalize_item` stays a byte-for-byte-compatible thin wrapper preserving the original None-vs-`ReelRecord` contract for existing callers. A disabled `image` demonstrator stub (`_handle_unsupported`, `STUB_PRODUCT_TYPE`) is registered with a self-describing follow-up marker (marker text only, no minted issue number) — it proves the seam and ships disabled; only `CLIP_PRODUCT_TYPE` is enabled, so what reaches the store is unchanged. A shuffled-mixed-page test asserts non-clips never leak into the pool and the numeric-`media_id` watermark is unaffected (routing stays non-positional).

## Consequences

- Adding image/carousel/story later is a localized registration + a non-clip store contract, not a normalizer rewrite; reviewers can point at one diff as "how you'd add a type."
- Two normalize entry points must stay in sync (routed vs thin-wrapper); the wrapper's backward-compat None contract is test-pinned.
- Enabling any non-clip type is gated behind the follow-up marker (real normalization + a non-clip store contract are prerequisites), tracked as debt rather than silently disabled code.

## Related decisions

- [Discovery correctness rests on per-shortcode dedupe + numeric media_id ordering](2026-07-15-discovery-correctness-rests-on-per-shortcode.md) — the watermark the routing must not perturb.

## Revisit trigger

When the first non-clip product type (image/carousel/story) is actually enabled — needs a non-clip store contract and real normalization, not just registry activation.
