# One-gate enforcement = reject a divergent-store_dir config_path (not object-identity theater) around the arg-ignoring FetchGate singleton

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-16       |
| Status   | accepted         |
| Category | architecture/concurrency |
| Tags     | fetchgate, singleton, config_path, divergent-store, one-gate, context-mismatch, single-ip, t5 |

## Context

The one-process-wide `FetchGate` invariant serializes all IG-hitting work so at most one metadata window is in flight against the process's single IP. The skeleton let each tool call resolve the gate per-invocation via `get_gate(config)`; the plan initially feared a tool given a `config_path` pointing at a different `store_dir` would resolve a *second* gate keyed on a different `store_dir/_batch/_gate.json`, splitting the process-wide gate and permitting two concurrent IG windows. During the build, `get_gate` was confirmed to be an **argument-ignoring process singleton** (`_SINGLETON`, "same instance regardless of args") — so a naive "assert the two gates are the same object" test would be *vacuously true* and prove nothing. The real risk isn't two gate objects; it's a tool operating against a store that diverges from the one the single gate's persisted cooldown (`store/_batch/_gate.json`) is pinned to.

## Options considered

### Option A: Reject a config_path whose resolved store_dir diverges from the installed server context (ContextMismatch → typed envelope)
- **Pros**: Load-bearing, non-vacuous guard — it defends the actual invariant (all tools + the daemon adoption path share one store-pinned gate/cooldown); a divergent store is a typed *refusal to serve*, not a silent wrong-store operation; same-`store_dir` overrides (the test seam) reuse the server context; framed correctly around config/store divergence sharing the arg-ignoring singleton.
- **Cons**: Keeps a constrained `config_path` arg alive (store-compatible/test-only) rather than deleting it; requires the server to install a single context at startup that tools read.

### Option B: Assert object identity across the tools' gate and the daemon's gate
- **Pros**: Looks like a concurrency test.
- **Cons**: Vacuous — `get_gate` ignores its argument and always returns the one `_SINGLETON`, so identity is trivially true and catches nothing; provides false assurance.

### Option C: Delete the config_path arg entirely
- **Pros**: No divergence possible.
- **Cons**: Breaks existing per-tool tests that rely on a store-compatible override; the invariant is "one gate per process," achievable via same-`store_dir` validation without removing the seam.

## Decision

Enforce the one-gate invariant by **rejecting divergence**, not by asserting identity. The server installs a single `ServerContext` (config + `Store` + the one `FetchGate`, resolved once inside `startup`'s `build_context` so the singleton's persisted `store_dir/_batch/_gate.json` is pinned from the server config *before* serving). `_resolve_context` rejects any `config_path` whose resolved `store_dir` differs from the installed context by raising `ContextMismatch`, caught into the typed never-raise envelope (`"store_dir" in error`); a same-`store_dir` override reuses the server context and serves from store with zero pages. The guard's docstrings state the load-bearing *why*: because `get_gate` is arg-ignoring, the defense is store-divergence rejection, not object-identity theater. `test_divergent_store_dir_config_path_is_rejected` checks the envelope shape and that `current_context()` is untouched.

## Consequences

- The single persisted cooldown always tracks the exact store the tools operate on — no split-gate window against the shared IP.
- A constrained `config_path` (store-compatible/test-only) survives for test seams; divergent-store callers get a typed refusal, a deliberate breaking change with no external callers.
- Disclosed, out-of-scope tension carried forward: the synchronous tools (`list_reels`/`download_reel`) share the gate *object* for cooldown state + this guard but do not `gate.acquire()` (acquiring would sleep, and `list_reels` must never sleep) — so a sync tool can still fire an IG window during an active batch cooldown. A non-sleeping cooldown *check* is a real follow-up design question, explicitly scoped out of T5.

## Related decisions

- [FetchGate: one process-wide singleton serializes all IG-hitting work](2026-07-16-process-wide-fetchgate-single-ip-serialization.md) — the invariant this guard enforces at the wired-server boundary.
- [Escalating cooldown is persisted inside the gate](../reliability/2026-07-16-persisted-cooldown-in-gate-metered-stop.md) — the store-pinned cooldown state divergence would corrupt.

## Revisit trigger

If the synchronous tools are ever made to honor cooldowns (a non-sleeping gate check), or if `get_gate` stops being an arg-ignoring singleton — either changes what this guard must assert.
