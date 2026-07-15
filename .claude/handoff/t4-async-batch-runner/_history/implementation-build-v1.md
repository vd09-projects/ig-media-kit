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
version: 1
created: 2026-07-16T17:15:00Z
updated: 2026-07-16T17:15:00Z
prior_versions: []
---

# Build summary: T4 async batch runner

Implemented T4, the async batch subsystem — the only background-job path in the IG Media Kit — following planner-task.md v2 across all 10 steps, on modification-free reuse of the shipped T2/T3 code. `run_start_batch_fetch` hands back a `job_id` instantly and continues detached on a daemon thread: it fills each configured (or requested) handle toward `scan_depth` across escalating IG rate-limit cooldowns (serialized through a single process-wide `FetchGate`, the system's only sleeper), checkpoints durably after every window, aggregates a top-N over the full deduped pool (`global` or `per_channel`), optionally downloads the top-N mp4s via the T3 downloader, and POSTs the aggregated result to an SSRF-guarded callback with bounded retry+backoff. `run_get_batch_status` is a pure checkpoint read (safe during a cooldown), and `resume_pending_jobs` re-adopts orphaned jobs from checkpoint after a restart. The full suite is green: 143 passed (106 baseline + 37 new), 0 failed.

## Files
- `src/ig_media_kit/fetch_gate.py` (new) — process-wide `FetchGate` singleton: FIFO-fair mutual exclusion, escalating+persisted cooldown, sleep-out-never-poll.
- `src/ig_media_kit/batch.py` (new) — `BatchJob`/`JobPhase`/`HandleProgress` state model + stable result envelope, `_fill_handle`, `_run_job` (phase state machine), `_aggregate`, `_download_top`, `_post_callback` + `validate_callback_url`, `run_start_batch_fetch`, `run_get_batch_status`, `resume_pending_jobs`, `BatchDeps` injection.
- `src/ig_media_kit/store.py` — additive only: `save_batch_job`/`load_batch_job`/`save_batch_result`/`load_batch_result`/`list_batch_jobs`/`sweep_batch_tmp` + `_write_json_atomic`/`_read_json`, reusing the existing temp+fsync+`os.replace` discipline. No existing method changed.
- `src/ig_media_kit/config.py` — additive `BatchSettings` block (retries, backoff_base/cap_s, cooldown_base_s, cooldown_escalation_factor, cooldown_cap_s, per_job_page_budget, heartbeat_stale_s) with documented conservative defaults.
- `src/ig_media_kit/mcp_server.py` — wired `batch_fetch` + new `get_batch_status` tools to the callables (T5 still owns startup-resume + gate-wrapping the sync tools).
- `tests/test_fetch_gate.py` (new, 8 tests), `tests/test_batch.py` (new, 29 tests).
- `probe/probe_batch.py` (new) — manual-only live pilot, not imported by CI.

## The 4 required refinements
1. **Persisted cooldown** — `FetchGate` writes `cooldown_until`+`escalation_count` to `store/_batch/_gate.json` atomically on every metered stop/success; a fresh gate loads it on construction, so a restart mid-cooldown sleeps out the remainder instead of re-hitting IG (`test_cooldown_persists_across_restart`).
2. **Explicit resume** — `resume_pending_jobs` is a plain callable; invoked once by the first `run_start_batch_fetch` via `_maybe_resume_once` and by T5 at startup. No module-import side-effect.
3. **FIFO fairness** — gate uses a `threading.Condition` + ticket queue (not a bare Lock); `test_two_acquirers_never_overlap_and_are_fifo_fair` asserts strict interleaving, max in-flight == 1.
4. **DNS-rebind TOCTOU** — `validate_callback_url` resolves + validates every IP is public and is re-run immediately before each POST; `_default_poster` pins the connection to the validated IP via curl's `resolve` map, redirect-follow disabled, zero IG headers/cookies (`test_default_poster_is_anonymous_no_redirect_and_pinned`).

## Invariants honored
Anonymous-only (every IG hit through `AnonymousClient`; callback on a bare non-IG transport); politeness (single serialized fetcher, cap at `max_pages_per_call`, stop+persist on 401, never poll during cooldown, batch is the only sleeper); store never destructively capped; completion keyed on `coverage.complete` (contiguity), not raw count; T2/T3 modules reused unedited. All tests use injected transports + a fake clock — zero real sleeps, zero real IG/callback network.

## Discovered follow-ups
- **Reconcile the batch per-unit primitive with `window.run_window`.** The plan's Step 4 named `run_window` as the per-window call, but `run_window` is TOP_SCAN-only (no deepen, no cursor-resume toward `scan_depth`), so `_fill_handle` loops `run_list_reels` instead (top-check + deepen + cursor-resume — the same entrypoint T5 wraps at the gate). This leaves `run_window` unreferenced; either retire it or extend it to deepen so the batch can route through it, avoiding two divergent compose paths. Code marker: `src/ig_media_kit/batch.py`, `_fill_handle` docstring.
