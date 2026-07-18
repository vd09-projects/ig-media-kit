# last_analyzed_at is a stamped State field written in write_window, not derived from max(fetched_at)

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-18       |
| Status   | accepted         |
| Category | architecture/data-model |
| Tags     | last_analyzed_at, state, write_window, staleness, fetched_at, backward-compatible, data-model, t17, d1 |

## Context

`list_reels`' new staleness metadata block needs a `last_analyzed_at` value —
when was this handle last analyzed. The T17 plan raised this as Decision D1:
where does that timestamp come from? Two sources were available. This matters
because analysis time (a fetch window that persisted) is semantically distinct
from a CSV row's per-row `fetched_at` (which records when that row's signed
`video_url` was resolved), and the two diverge in edge cases.

## Options considered

### Option A: Add last_analyzed_at to State, stamped by store.write_window
- **Pros**: Records genuine *analysis* time — the last window that persisted, including an empty-but-attempted window that wrote 0 rows; one clean writer (`write_window`); `list_reels` reads it read-only.
- **Cons**: A small additive schema bump to the per-handle State YAML.

### Option B: Derive last_analyzed_at = max(fetched_at) across CSV rows at read time
- **Pros**: No schema change; purely computed on read.
- **Cons**: Conflates analysis time with per-row URL-resolve time; cannot represent a window that persisted 0 rows (an attempted-but-empty analysis has no rows, so `max(fetched_at)` is undefined/stale); recomputed on every read.

## Decision

Adopt **Option A**. Add `last_analyzed_at: int | None` to `State`, stamped by
`store.write_window` on **every** window persist — including a zero-row window.
`list_reels` reads it read-only for the staleness block.

Rationale: it records genuine analysis time (the last window that persisted),
which is semantically distinct from a row's `fetched_at` (URL-resolve time) and,
critically, **survives an empty-but-attempted window** that persisted no rows —
a state `max(fetched_at)` cannot represent. Backward compatibility is preserved:
the field is keyword-defaulted `None` and loaded via
`_as_int(data.get("last_analyzed_at"))`, so pre-T17 State YAMLs deserialize
cleanly with `None` and the handle still serves — no migration script needed.

## Consequences

- One-writer / read-only-reader split: only `write_window` writes
  `last_analyzed_at`; `list_reels` only reads it. Clean and testable.
- The field round-trips through the existing atomic tmp-then-rename state write;
  no change to the write mechanics.
- **Revisit hazard this entry guards:** a future "simplification" to derive
  `last_analyzed_at` from `max(fetched_at)` at read time would silently drop the
  empty-window-analysis-time semantic and mislead the staleness hint. Keep the
  explicit stamped field.

## Related decisions

- [list_reels is READ-ONLY over the store (CQRS split)](../api-contract/2026-07-16-list-reels-is-read-only-over-the-store-cqrs-split.md) — the staleness metadata block that consumes this field.
- [Fetch primitive extracted into fill.py::run_fill](../2026-07-18-fetch-primitive-extracted-into-fill-run-fill.md) — `run_fill` threads `now` into `write_window` to feed this stamp (the sole non-verbatim delta of that extraction).
