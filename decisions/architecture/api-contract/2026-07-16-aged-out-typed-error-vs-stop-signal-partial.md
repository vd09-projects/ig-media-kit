# Aged-out / not-found-in-budget re-resolve returns a typed error with partial=False, disambiguated from stop_signal partial

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-16       |
| Status   | accepted         |
| Category | architecture/api-contract |
| Tags     | download_reel, error-envelope, partial, retryability, mcp-contract, cooldown, t3 |

## Context

The find-by-identity re-resolve traversal can end two ways that both mean "no fresh URL," but for very different reasons an MCP consumer cares about:

1. The metered metadata API returns a 401 mid-traversal (cooldown) — the polite path stops and returns partial, retryable in minutes once the window reopens.
2. The traversal exhausts its polite page budget without finding the target — the reel has aged out of the reachable pages; retrying immediately will not help.

If both surfaced identically, a consumer branching on retryability could not tell "wait out a cooldown" from "this reel is no longer reachable."

Scope: t3-download-reel-signed-url-refresh (issue #3, branch feat/t3-download-reel).

## Decision

The aged-out / not-found-in-budget outcome returns a **typed error with `partial=False`**, deliberately disambiguated from the stop-signal case which returns **`partial=True` + a `stop_reason`**. The two are distinct contract shapes:

- **Metered cooldown (401):** `partial=True`, `stop_reason` set → "retry in minutes, the window will reopen."
- **Aged out of budget:** typed error, `partial=False` → "the reel fell out of the reachable page budget; retrying now won't help."

This lets an MCP consumer branch on retryability directly from the envelope, without guessing.

## Consequences

- `download_reel`'s error contract carries two clearly separable failure modes; the `partial` flag is the primary discriminator, `stop_reason` the secondary detail for the retryable case.
- Consumers can implement correct backoff: retry-after-cooldown for `partial=True`, give-up-or-escalate for the `partial=False` aged-out error.
- The distinction must be preserved by any future refactor of the download_reel envelope — collapsing them would re-break retryability signaling.

## Related decisions

- [Targeted owner-feed re-resolve is a distinct find-by-identity traversal](../2026-07-16-targeted-re-resolve-find-by-identity-traversal.md) — the traversal whose two termination modes this contract disambiguates.
- [Unknown shortcode returns a typed error envelope — no IG-wide search fallback](../2026-07-16-unknown-shortcode-typed-error-no-search.md) — the third typed-error outcome of download_reel.

## Revisit trigger

If the MCP surface adopts a unified error taxonomy (e.g., a `retryable: bool` field), fold this partial-vs-typed-error distinction into it rather than keeping two envelope shapes.
