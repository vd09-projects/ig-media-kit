# list_reels is READ-ONLY over the store (CQRS split — hard split, no fetch escape hatch)

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-16       |
| Status   | accepted         |
| Category | architecture/api-contract |
| Tags     | list_reels, cqrs, read-only, serve-from-store, staleness-metadata, typed-error, never-sleeps, metered-paths, contract-change |

## Context

`list_reels` had historically been able to reach Instagram as part of its
call-driven fill (serve-from-store with a network fallback that deepened the
pool toward `scan_depth=90`). That coupling meant an interactive serve call
could surface cross-channel rate-limit / 401 cooldown errors that originated in
the metered fetch path — confusing for an LLM-driven MCP consumer that just
wanted to rank what was already known. It also left the "`list_reels` never
sleeps" invariant only partially true: it never slept, but it could still
*attempt* IG and fail on a cooldown.

The metadata API is IP-rate-limited with an escalating cooldown, so every path
that can touch IG is a liability during interactive use. We want clean
analyze-then-serve semantics.

## Options considered

### Option A: Keep the fetch fallback with a non-sleeping cooldown check
- **Pros**: Single tool can both discover and serve; no new "analyze first" step for the consumer.
- **Cons**: Interactive serve still emits metered-path errors; "never sleeps" stays a half-truth; command/query concerns stay entangled; a cooldown check is just papering over the coupling.

### Option B: Hard CQRS split — list_reels is pure READ-ONLY over the store
- **Pros**: `list_reels` never touches IG at all → no rate-limit/401 noise during serve; "never sleeps" becomes fully true; clean analyze-then-serve semantics; metered paths reduced to an explicit, small set.
- **Cons**: A not-yet-analyzed handle can't be served on demand — the consumer must run `start_batch_fetch` first (surfaced as a typed error). Contract change + tests + docs.

## Decision

Pivot `list_reels` to a **pure READ-ONLY query over the local store**.
`list_reels` **NEVER** hits Instagram. This is a **HARD SPLIT** (CQRS: command =
analyze/fetch, query = serve) — there is **NO opt-in fetch escape hatch** on
`list_reels`.

**Behavior:**
- For a handle that has **NOT been analyzed yet**, `list_reels` returns a
  **typed error** meaning "run `start_batch_fetch` first" — not an empty list,
  not a silent IG fetch.
- For an **analyzed** handle, `list_reels` ranks + serves **instantly** from the
  store, and includes **staleness metadata** in the response:
  `last_analyzed_at`, store-count vs the `scan_depth=90` target, and a
  signed-URL-maybe-expired hint (stored `video_url` has ~36h TTL).

**Remaining metered paths (only these hit IG):** the batch fetch
(`start_batch_fetch` / async runner), and `download_reel`'s >24h signed-URL
re-resolve (tracked as #13). Everything else serves from the store.

**Rationale:** eliminates confusing cross-channel rate-limit / 401 errors
surfacing during interactive serve; makes the "`list_reels` never sleeps"
invariant fully true (it now never even *attempts* IG); gives cleaner
analyze-then-serve semantics for an LLM-driven MCP consumer.

## Consequences

- Contract change to `list_reels` (new typed "not-analyzed" error; new staleness
  metadata block) + tests + README/CLAUDE.md doc updates — captured in a
  dedicated ticket filed alongside this decision.
- A consumer must analyze (`start_batch_fetch`) before it can serve a handle.
- `download_reel` remains the **only** sync metered path (its own #13 >24h
  re-resolve).

## Related decisions

- [Freeze the four-tool MCP surface as the public contract](2026-07-16-freeze-four-tool-mcp-surface-public-contract.md) — this refines the `list_reels` half of that frozen surface.
- [Never-raise typed envelope on all four MCP tools](2026-07-16-never-raise-typed-envelope-all-four-tools.md) — the new "not-analyzed" typed error rides that envelope.
- [FetchGate: one process-wide singleton serializes all IG-hitting work](../concurrency/2026-07-16-process-wide-fetchgate-single-ip-serialization.md) — still governs the remaining metered paths (batch, download re-resolve); `list_reels` now needs no gate because it never fetches.
- [Escalating cooldown is persisted, and note_metered_stop is applied inside the gate](../reliability/2026-07-16-persisted-cooldown-in-gate-metered-stop.md) — still governs the metered paths.
- Supersedes the earlier proposed "sync-path non-sleeping cooldown check" follow-up (disclosed as out-of-scope in the divergent-store / FetchGate decisions): with `list_reels` read-only, the sync path no longer hits IG at all, so a cooldown check on it is moot. `download_reel` remains the only sync metered path (its own #13 re-resolve).

## Revisit trigger

If a consumer use-case genuinely requires on-demand discovery-through-serve
(e.g. a "serve, fetching if unknown" convenience), revisit whether a *separate*
command tool — never a fetch escape hatch bolted onto `list_reels` — should
cover it.
