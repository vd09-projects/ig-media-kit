# Uniform machine-branchable error contract: error_kind + retryable across list_reels and download_reel

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-18       |
| Status   | accepted         |
| Category | architecture/api-contract |
| Tags     | error-kind, retryable, typed-error, error-contract, list_reels, download_reel, machine-branchable, mcp-contract, t17 |

## Context

The T17 CQRS split added a new typed `not_analyzed` error to `list_reels`. The
plan review flagged an asymmetry: `download_reel`'s existing `_error` carried no
`error_kind`/`retryable` fields — a consumer had to **infer** retryability from
the `partial` flag (`partial=True` cooldown = retry soon; `partial=False`
aged-out = don't retry). Introducing `error_kind`/`retryable` on only the new
`list_reels` error would leave the two tools with different error shapes, so an
LLM/MCP consumer would need per-tool branching logic to answer the single
question "should I retry?".

## Options considered

### Option A: Add error_kind/retryable to the new list_reels error only
- **Pros**: Smallest diff; touches only the tool being changed.
- **Cons**: Perpetuates the contract asymmetry — `download_reel` still signals retryability via `partial`, so a consumer can't branch uniformly across tools.

### Option B: Add error_kind + retryable to list_reels AND backfill download's _error/_partial
- **Pros**: One uniform, machine-branchable error contract across both tools; a consumer branches on a single `retryable` boolean everywhere; `error_kind` gives a stable discriminator for each terminal condition.
- **Cons**: Touches `download_reel`'s error path (already-shipped code) and both frozen snapshots; must confirm no discriminator collision across tools.

## Decision

Adopt **Option B** — a uniform typed-error contract. Every typed error across
both tools now carries `error_kind: str` + `retryable: bool`:

- **`list_reels`**: `error_kind ∈ {not_analyzed, invalid_params}`,
  `retryable=False`; the `mcp_server` last-resort fallback adds
  `error_kind="internal_error"`, `retryable=False`.
- **`download_reel`**: `_error` backfilled with
  `error_kind ∈ {not_in_store, aged_out, download_failed}`, `retryable=False`;
  the retryable sibling `_partial` gains `error_kind="rate_limited"`,
  `retryable=True`.

A consumer now branches on `retryable` **uniformly** across both tools instead
of inferring it from `partial`. The taxonomy is coherent: `retryable=True` only
for the transient rate-limited cooldown; every terminal/unknown condition is
`retryable=False`. Both frozen-surface snapshots were updated deliberately with
justifying comments.

## Consequences

- Symmetric, machine-branchable error contract across the MCP surface — the key
  ergonomic win for an LLM-driven consumer.
- `download_reel`'s `partial` / `stop_reason` / `pages_fetched` fields are
  retained for shape stability, now redundant with `retryable` for the
  retry-decision but still describing the partial-result mechanics.
- `download._error`'s `error_kind` is a keyword-only arg with no default; all
  three (module-private) call sites were updated — a missed one would have been
  a `TypeError`, and none exist.

## Related decisions

- [Aged-out re-resolve returns a typed error with partial=False, disambiguated from stop_signal partial](2026-07-16-aged-out-typed-error-vs-stop-signal-partial.md) — this generalizes that partial-vs-error distinction into an explicit `retryable` flag shared with `list_reels`.
- [Never-raise typed envelope on all four MCP tools](2026-07-16-never-raise-typed-envelope-all-four-tools.md) — the typed errors ride that never-raise envelope; this adds the branchable `error_kind`/`retryable` fields to it.
- [list_reels is READ-ONLY over the store (CQRS split)](2026-07-16-list-reels-is-read-only-over-the-store-cqrs-split.md) — the change that introduced the `not_analyzed` error and surfaced the asymmetry.
