# IG-fetch primitive extracted out of list_reels into fill.py::run_fill (shared command-side engine)

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-18       |
| Status   | accepted         |
| Category | architecture     |
| Tags     | fill, run_fill, cqrs, command-side, fetch-engine, batch, extraction, forced-deviation, ratified, t17 |

## Context

The accepted CQRS decision mandated deleting the metered IG-fetch path out of
`list_reels` so it becomes a pure read-only query. During the T17 build,
discovery found that the async batch runner (`batch._fill_handle`) reused
`run_list_reels` as its **only** fetch engine — it called `run_list_reels` to
advance coverage toward `scan_depth=90`. Simply deleting the network path from
`run_list_reels` would therefore have broken the batch runner. This tripped the
plan's own re-plan trigger ("excision touches ≥ 2 modules"), forcing an
explicit decision on where the fetch primitive should live.

## Options considered

### Option A: Delete the network path from list_reels and re-implement fetch inside batch
- **Pros**: Keeps the change nominally scoped to the two tools that need it.
- **Cons**: Duplicates the whole `PageBudget` / top-check / deepen / stop-signal control flow; a real drift hazard between the query's old logic and the runner's copy; contradicts "one shared fetch engine".

### Option B: Relocate the network path verbatim into a new fill.py::run_fill and re-point batch to it
- **Pros**: One shared command-side fetch primitive; batch keeps identical fetch semantics; list_reels retains none of it; the module boundary literally mirrors the CQRS command/query split (command = `fill`, query = `list_reels`).
- **Cons**: A new module appears mid-build (not in the original plan); param-plumbing helpers end up duplicated across the query and command modules (filed as debt).

## Decision

Lift the IG-fetch primitive **verbatim** out of `run_list_reels` into a new
module **`fill.py::run_fill`**, and re-point `batch._fill_handle` to call
`run_fill`. `run_fill` is now the **single shared command-side fetch engine**;
`list_reels` keeps none of the fetch path.

The relocation preserved the fetch axis byte-for-byte: same `PageBudget`
(cap `max_pages_per_call`), same `reserve_deepen` (≥1 page held for deepen),
same `sleep=None` on both `fetch_window` calls (sync path never sleeps), same
"first `stop_signal` aborts the whole unit" control flow. The **only**
non-verbatim delta is threading `now=now` into `store.write_window` (three call
sites), which feeds the additive `last_analyzed_at` stamp and fixes a latent
wall-clock issue — not a fetch-semantic change.

The multi-perspective review panel explicitly **RATIFIED** this extraction as
behavior-preserving on the fetch axis, as the literal realization of the CQRS
decision, and as satisfying both hard invariants (`list_reels` zero-IG AND the
batch runner remaining the only coverage-advancing writer).

## Consequences

- `fill.py` now exists as the command-side fetch engine; a future reader knows
  it holds the primitive that used to live inside `list_reels`.
- Both hard invariants hold: `list_reels` issues zero IG requests, and the batch
  runner (via `run_fill` under the `FetchGate`) remains the only writer that
  advances coverage.
- **Tracked debt #1:** `_Params` / `_resolve_params` / `_validate` are now
  duplicated verbatim between `list_reels.py` (query) and `fill.py` (command),
  marked with a `# DUP:` pointer — extract into `ig_media_kit/params.py` before
  the next param/validation change to prevent query/command drift.
- **Tracked debt #2:** `window.py::run_window` is now fully unreferenced (the
  batch loops `run_fill`) — retire it or route the batch through it; do not let
  two divergent compose paths linger.

## Related decisions

- [list_reels is READ-ONLY over the store (CQRS split)](api-contract/2026-07-16-list-reels-is-read-only-over-the-store-cqrs-split.md) — this extraction is the implementation-level realization of that decision's command/query split.
- [Batch execution = daemon thread + durable checkpoint + explicit resume](2026-07-16-daemon-thread-batch-runner-with-explicit-resume.md) — the runner whose `_fill_handle` was re-pointed from `run_list_reels` to `run_fill`.

## Revisit trigger

When the tracked debt is paid: if `params.py` is extracted or `window.py` is
retired/re-routed, revisit whether this note still reflects the module layout.
