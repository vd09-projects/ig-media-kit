# Freeze the four-tool MCP surface as the public contract (top_reels removed, batch_fetch → start_batch_fetch)

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-16       |
| Status   | accepted         |
| Category | architecture/api-contract |
| Tags     | mcp-surface, public-contract, four-tools, rename, top_reels, start_batch_fetch, snapshot-test, t5 |

## Context

The four tools (`list_reels`, `download_reel`, `start_batch_fetch`, `get_batch_status`) were built and merged as separate modules, but the `mcp_server.py` skeleton still shipped a stale `top_reels` stub tool and mis-named the batch launcher `batch_fetch`. T5 was the capstone ship ticket: assemble the modules into one runnable FastMCP server and settle what the public MCP surface actually is. The MCP tool set (names + input schemas + return envelope) is the contract an MCP client (Claude Desktop / `mcp` CLI) consumes; there was no test asserting the *registered surface as a whole*, so accidental additions/renames could drift silently.

## Options considered

### Option A: Freeze exactly four tools, remove the stub, rename to canonical names, guard with a snapshot test
- **Pros**: One unambiguous public contract; `top_reels` (never-implemented stub) can't confuse consumers; `start_batch_fetch` reads correctly for an async launcher; a name+param snapshot test guards against accidental surface drift on future edits.
- **Cons**: Breaking rename vs the skeleton (`batch_fetch`, `top_reels`); the snapshot test reaches into FastMCP internals (`mcp._tool_manager.list_tools()`), a brittleness point if `mcp[cli]` is bumped.

### Option B: Keep names as-is, defer the freeze
- **Pros**: No rename churn.
- **Cons**: Ships a non-functional `top_reels` in the public surface; leaves the contract undefined at the exact moment it becomes consumable; future callers would bind to names slated to change.

## Decision

Freeze the surface at exactly four tools — `list_reels`, `download_reel`, `start_batch_fetch`, `get_batch_status` — each with an explicit typed input schema. Delete the stale `top_reels` stub outright and rename `batch_fetch` → `start_batch_fetch`. Because the project is pre-1.0 (`0.1.0`) with no tagged release and no external consumers of the skeleton names, this is a total, alias-free rename (surface cleanup), classified as a minor pre-release change. A snapshot test (`test_four_tool_surface_snapshot`) pins the exact tool names + param sets and asserts `top_reels`/`batch_fetch` are gone, making the frozen surface a tested acceptance criterion rather than advice. From this PR forward, any schema change to the four-tool set is treated as a real semver event, and the README "Tools" section is the canonical reference.

## Consequences

- Establishes the baseline contract that all subsequent changes are measured against; pilots #10/#14 validate this surface against live IG.
- The snapshot test couples to FastMCP internals — noted as a brittleness point (asserts names/params, not full serialized JSON, to limit fragility across SDK bumps).
- Any internal/test caller of the old `batch_fetch` name breaks; there are none external (pre-1.0), so no dual-support window was provided.

## Related decisions

- [Never-raise typed envelope on all four MCP tools](2026-07-16-never-raise-typed-envelope-all-four-tools.md) — the return-shape half of this same frozen contract.
- [Aged-out typed error vs stop-signal partial](2026-07-16-aged-out-typed-error-vs-stop-signal-partial.md) — a per-tool envelope distinction the frozen surface must preserve.

## Revisit trigger

Any post-1.0 change to a tool name, parameter set, or return envelope — treat as a semver-visible contract change, not an in-place edit.
