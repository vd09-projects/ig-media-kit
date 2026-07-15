# Persisted cooldown; in-gate metered-stop

| Field    | Value            |
|----------|------------------|
| Date     | 2026-07-16       |
| Status   | accepted         |
| Category | architecture/reliability |
| Tags     | fetchgate, cooldown, escalation, 401, back-off, persistence, restart-safety, t4 |

## Context

The politeness invariant requires that every IG-hitting path stop and back off on
the first 401, that the cooldown **escalates under abuse**, and that the process
**never polls during a cooldown** (polling extends it). With the FetchGate
serializing all fetch work (paired decision), two failure modes had to be closed:
(1) a process restart mid-cooldown must not forget the cooldown and immediately
re-hit IG, and (2) a second worker must not be able to open a fresh window on an IP
that another worker just got 401'd on, before the cooldown is recorded.

## Options considered

### Option A: In-memory cooldown, register back-off after releasing the gate
- **Pros**: less code; no disk I/O on the hot path.
- **Cons**: a restart loses the cooldown and re-hits IG during the very window IG
  is penalizing — the worst possible time. And a release-then-register ordering
  opens a race where a waiting worker acquires the gate and opens a window on the
  just-401'd IP before the back-off is set.

### Option B: Persist cooldown to disk; register metered-stop inside the critical section
- **Pros**: restart mid-cooldown sleeps out the remainder instead of re-hitting IG;
  the back-off is set before any other worker can acquire the gate, so no worker can
  open a window on a just-401'd IP; the whole "stop → back off → escalate → don't
  poll" sequence becomes atomic.
- **Cons**: a small amount of disk state (`_gate.json`) to keep in sync.

## Decision

The gate persists `cooldown_until` + `escalation_count` to
`store/_batch/_gate.json`, so a process that restarts mid-cooldown sleeps out the
remaining cooldown rather than re-hitting IG. And `note_metered_stop` (the
metered-stop back-off registration) is applied **inside the gate's critical
section** — while the worker still holds the gate — so a second worker cannot open a
window on a just-401'd IP before the cooldown is set. Together this makes the
"stop and back off on the first 401, escalate under abuse, never poll during a
cooldown" politeness invariant both atomic (no inter-worker race) and
restart-durable (survives a process crash).

## Consequences

- There is durable gate state on disk that must be read at startup and kept
  consistent with the in-memory gate; corruption/absence of `_gate.json` must
  degrade safely (treat as no active cooldown).
- The back-off is held under the gate lock, so the 401 path briefly serializes all
  waiters — intended: that is exactly the moment nobody should be fetching.
- Escalation state is persisted, so repeated abuse across restarts still ratchets
  the cooldown up rather than resetting to the base window.

## Related decisions

- [Process-wide FetchGate single-IP serialization](../concurrency/2026-07-16-process-wide-fetchgate-single-ip-serialization.md) — the single-lane gate is the critical section this back-off is applied inside.

## Revisit trigger

If cooldown state ever needs to be shared across processes (multiple runner
processes on one IP), a single JSON file under one process's control is no longer
sufficient and this needs a cross-process lock or shared store.
