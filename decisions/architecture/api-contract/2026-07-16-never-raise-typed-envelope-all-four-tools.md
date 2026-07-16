# Never-raise typed envelope on all four MCP tools (list_reels included)

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-16       |
| Status   | accepted         |
| Category | architecture/api-contract |
| Tags     | never-raise, error-envelope, mcp-contract, list_reels, resilience, contract-test, t5 |

## Context

Three of the four tools (`download_reel`, `start_batch_fetch`, `get_batch_status`) already terminated in a typed dict envelope, but `list_reels` (`mcp_server.py:24–57` in the skeleton) returned `run_list_reels(...)` directly with no try/except — so an unexpected throw in the implementation would propagate past the MCP client. An MCP tool that raises gives the consumer an opaque transport-level failure instead of a parseable result. The round-1 plan review flagged this as a blocking gap: the never-raise guarantee has to hold for *all four* tools or it isn't a contract.

## Options considered

### Option A: Wrap every tool in a typed never-raise envelope, backstopped by a per-tool try/except
- **Pros**: A consumer parses failure the same way it parses success (same dict shape, empty/zeroed fields + `error`); no exception ever reaches the MCP client; a single behavioral contract test can assert it across all four tools.
- **Cons**: The outer `except` can mask a real bug if it swallows too eagerly — must be a genuine last-resort backstop, not the primary error path.

### Option B: Leave list_reels bare; rely on implementations not throwing
- **Pros**: Less wrapping code.
- **Cons**: Any unhandled throw becomes a client-visible crash; the never-raise property is unverifiable and un-guaranteed; inconsistent with the other three tools.

## Decision

Wrap `list_reels` in the same typed never-raise envelope the other three tools use — its failure dict mirrors the success shape (empty `reels`, `partial=False`, zeroed coverage, `error=...`) so consumers branch uniformly. The outer `except Exception → return {typed dict}` is deliberately a *last resort*: for `download_reel`, the meaningful partial-vs-terminal distinction is still produced *inside* `run_download_reel` and passes through untouched; the backstop only catches truly unexpected throws. The guarantee is enforced behaviorally, not by inspection: `test_all_four_tools_never_raise_when_run_throws` stubs each `run_*` to raise and asserts every tool returns a `dict` carrying the failure detail — `list_reels` included. `ContextMismatch` (divergent-store rejection) is raised then caught by the same envelope, so a divergent store is a *typed refusal to serve*, not a crash.

## Consequences

- Every MCP tool now has a uniform, parseable failure contract; the never-raise property is a tested acceptance criterion.
- The last-resort backstop must not collapse meaningful typed distinctions (e.g. download's partial vs aged-out) — a preserve test guards this.
- Future tools added to the surface must adopt the same envelope to keep the guarantee whole.

## Related decisions

- [Freeze the four-tool MCP surface as the public contract](2026-07-16-freeze-four-tool-mcp-surface-public-contract.md) — the name/param half of the same frozen contract.
- [Aged-out typed error vs stop-signal partial](2026-07-16-aged-out-typed-error-vs-stop-signal-partial.md) — the typed distinction the outer backstop must not flatten.

## Revisit trigger

Adding a fifth MCP tool, or any change that lets an exception escape a tool to the MCP client.
