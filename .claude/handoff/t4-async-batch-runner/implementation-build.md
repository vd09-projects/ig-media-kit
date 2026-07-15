---
artifact_type: handoff
artifact_version: 2
producer_role: implementation
consumer_role: review
plan_type: build
slug: t4-async-batch-runner
scope_hint: T4 async batch runner
canonical_name: implementation-build
overlays: []
status: draft
version: 2
created: 2026-07-16T17:15:00Z
updated: 2026-07-16T23:45:00Z
prior_versions: [implementation-build-v1]
---

# Build summary: T4 async batch runner

Iteration 2 — addresses the round-1 build review (review-findings v3). One BLOCKING
finding (F1) fixed, the cheap correctness/doc suggestions (F2, F4, F5) folded in,
the accepted-debt marker (F7) left in place, and two regression tests added
(F6 + the F2 crash case). Full suite green: **145 passed** (143 prior + 2 new),
0 failed, still fully offline (injected transports + fake clock, zero real
sleeps, zero real IG/callback network).

## Findings addressed

- **F1 (BLOCKING) — atomic back-off inside the gate.** In `batch._fill_handle`,
  the metered/401 outcome is now classified and the escalated cooldown applied
  **inside** the `with gate.acquire()` critical section, before the gate releases
  — `gate.note_metered_stop(stop_reason)` (and the symmetric `gate.note_success()`
  on a clean unit) now run while the worker still holds the gate. Previously they
  ran after the block released, opening a window where a second worker (resume
  relaunches every non-terminal job on its own thread; a new `start_batch_fetch`
  can overlap) could acquire the just-released gate and hit the still-401'd IP
  before `cooldown_until` was set — its `_sleep_out_cooldown` reading a stale 0
  and sleeping nothing. Now the next holder observes the fresh `cooldown_until`
  and sleeps it out. The post-gate bookkeeping (`job.sleep_until`, stall
  accounting, checkpoint) still runs outside the gate; only the shared-state
  mutation moved in. Behavior on the error/complete early-return paths is
  unchanged (they touch neither `note_metered_stop` nor `note_success`, as before).

- **F2 (correctness) — liveness checks the worker before the cooldown clock.**
  `_classify_liveness` now evaluates `_worker_alive(job.job_id)` FIRST: a live
  daemon that is sleeping out a cooldown is still an alive thread, so
  `alive + future sleep_until → "cooldown-sleeping"`, `alive + no cooldown →
  "fetching"`. A worker that CRASHED mid-cooldown (thread gone, checkpoint still
  shows a future `sleep_until`) no longer masks as `cooldown-sleeping` for up to
  `cooldown_cap_s` — with no live thread it falls through to the staleness check
  and surfaces as `dead-worker`.

- **F5 (doc) — `retries` also bounds total hard-block wait.** `BatchSettings.retries`
  docstring now notes that the fill loop's stall guard is `retries + 2`, so a
  permanently rate-limited handle sleeps out at most ~`retries` escalating
  cooldowns before giving up — raising `retries` raises total block-wait, not
  only callback attempts.

- **F4 (FYI) — IPv6 pin formatting comment.** Added a code comment in
  `_default_poster` noting the curl `resolve` map is exercised only with IPv4
  literals today; if an IPv6 literal is ever pinned, some curl builds require it
  bracketed (`host:port:[addr]`) and a v6 SSRF-guard test should be added. No
  behavior change (IPv4-only pinning is acceptable).

- **F7 (accepted debt — NOT fixed here, by direction).** The `run_window` vs
  `run_list_reels` divergence stays an out-of-scope follow-up. The self-describing
  TODO marker in `_fill_handle` is left in place (what/where/why, no issue number
  minted).

## Tests added / changed

- `tests/test_batch.py::test_two_workers_no_window_opens_after_401_until_cooldown_slept`
  (**new, F6**) — two threads share one process `FetchGate` + fake clock. Worker B
  starts only after worker A serves a 401; a recording transport captures the
  virtual-clock time of every IG call. Asserts every B IG call (incl. its profile
  resolve) lands at `≥ NOW + cooldown_base_s` — never at `NOW` — and the cooldown
  was slept exactly once before any post-401 window. Fails against the pre-fix
  mutate-after-release ordering; passes now.
- `tests/test_batch.py::test_liveness_crash_mid_cooldown_is_not_masked_as_sleeping`
  (**new, F2**) — a checkpoint with a future `sleep_until` but no live worker and a
  stale heartbeat reads as `dead-worker`, not `cooldown-sleeping`.
- `tests/test_batch.py::test_liveness_cooldown_vs_dead_worker` (**updated, F2**) —
  the `cooldown-sleeping` case now registers a live blocker thread for the job so
  liveness reflects the new worker-first ordering.

## Files
- `src/ig_media_kit/batch.py` — F1 (metered/clean classification + gate mutation
  moved inside `_fill_handle`'s `with gate.acquire()` block), F2 (`_classify_liveness`
  reordered), F4 (comment in `_default_poster`). TODO marker retained.
- `src/ig_media_kit/config.py` — F5 (`BatchSettings.retries` docstring).
- `tests/test_batch.py` — F6 test added, F2 crash-case test added, liveness test updated.

## Invariants honored
Anonymous-only (every IG hit through `AnonymousClient`; callback on a bare non-IG
transport); politeness now atomic — the back-off lands inside the serialization
gate so no concurrent worker can open a window on a rate-limited IP before the
cooldown is set (never poll during cooldown, batch is the only sleeper); store
never destructively capped; dedupe + watermark unchanged; callback SSRF hardening
(https-only, re-validate + IP-pin before each POST, redirects off) unchanged.

## Discovered follow-ups
- **Reconcile the batch per-unit primitive with `window.run_window`** (unchanged
  from v1, accepted debt). `_fill_handle` loops `run_list_reels` (top-check +
  deepen + cursor-resume) rather than the plan's TOP_SCAN-only `run_window`,
  leaving `run_window` unreferenced — retire it or extend it to deepen so batch +
  T5 share one compose path. Self-describing marker: `src/ig_media_kit/batch.py`,
  `_fill_handle` docstring (no issue number minted, per review direction).
</content>
</invoke>
