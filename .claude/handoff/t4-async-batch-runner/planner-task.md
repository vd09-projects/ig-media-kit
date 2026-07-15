---
artifact_type: handoff
artifact_version: 2
producer_role: planner
consumer_role: implementation
plan_type: task
slug: t4-async-batch-runner
scope_hint: T4 async batch runner — start_batch_fetch + get_batch_status + callback
canonical_name: planner-task
overlays: [concurrency]
status: draft
version: 2
created: 2026-07-16T03:18:00Z
updated: 2026-07-16T22:15:00Z
prior_versions: [planner-task-v1]
---

# Task plan: T4 async batch runner — start_batch_fetch + get_batch_status + callback

**Overlays:** concurrency

> **Revision — round 1 review response.** All three blocking findings are resolved in the design body (not deferred):
> - **F1 (cross-job serialization):** promoted from an Open Question to a **hard invariant + its own foundational step (Step 3, the process-wide `FetchGate`)**. A second concurrent fetch **queues behind** (serializes), never rejects. The gate serializes *all* IG-hitting work — batch windows now, and the sync `list_reels`/`download` tools once T5 wraps them at the same gate.
> - **F2 (restart resume):** added `resume_pending_jobs(config)` as a **first-class entrypoint (Step 9)** — idempotent sweep of `store/_batch/` that relaunches non-terminal jobs from checkpoint. T4 exposes it; T5 calls it on server startup; it also runs at `batch` module import/init so an in-process `start_batch_fetch` re-adopts orphans.
> - **F3 (callback SSRF):** resolved in Step 8 — **https-only, DNS-resolve + block private/link-local/loopback/metadata ranges, redirect-follow disabled**, bare anonymous transport.
> - Non-blocking suggestions folded in: cooldown sleep is sourced from `stop_reason` + a persisted escalation counter; `get_batch_status` distinguishes *fetching / cooldown-sleeping / dead-worker*; a single stable result envelope spans both scopes; orphan `*.tmp` sweep is a defined step; `start_batch_fetch` param contract is pinned; new tests added for serialization, restart-resume, SSRF/redirect rejection, and unknown-`job_id`.
> - **No disagreements.** One clarification carried into the design: because editing T2/T3 is out of scope, T4 *ships* the `FetchGate` primitive and the batch acquires it; wrapping the sync `list_reels`/`download` entrypoints in the same gate is the T5 wiring step (consistent with "T4 exposes, T5 wires"). The gate itself is complete and testable in T4.

## Problem

Build T4, the async batch subsystem — the *only* background-job path in the IG Media Kit. `start_batch_fetch` must hand back a `job_id` instantly and continue working detached: fill each configured (or requested) handle toward `scan_depth` across escalating IG rate-limit cooldowns (the runner is the system's only sleeper), checkpoint durably after every page so a kill/restart resumes rather than restarts, aggregate a top-N over the full stored pool (cross-channel `global` or `per_channel`), optionally download the top-N mp4s via the T3 downloader, and POST the aggregated result to an arbitrary callback URL with bounded retry+backoff. `get_batch_status` reads job state throughout and returns the final result even if the callback never lands. All IG-hitting work in the process — batch windows and, once T5 wires them, the sync `list_reels`/`download` tools — is serialized through one process-wide fetch gate so two callers can never race the single IP. This is the last core tool before T5 wires all four into the FastMCP server.

## Constraints

- **ANONYMOUS ONLY.** Every IG hit goes through the existing `AnonymousClient` (sole owner of `x-ig-app-id`, `assert_anonymous` on send). The callback POST goes to a *non-IG* URL — it must carry **no** `x-ig-app-id`, no cookies, no credentials, and must use a separate plain transport (not `AnonymousClient`, which impersonates Chrome for IG). No code path authenticates.
- **Politeness is load-bearing and the runner is the ONLY sleeper.** Single IP ⇒ **at most one IG window in flight process-wide**, enforced by the process-wide `FetchGate` (Step 3) — never two handles, never two jobs, never a job racing a sync tool. Reuse the T2 window discipline: pace pages ~1-2 s, cap ~`max_pages_per_call` per window, **stop + persist partial on first 401/stop_signal**, and **never poll during a cooldown** (it escalates ~6.6→13 min). On a metered stop, sleep a duration **derived from the `stop_reason` + a persisted escalation counter** (never a blind fixed guess), then resume from checkpoint.
- **Cross-job/cross-tool serialization is a hard invariant, not a runtime option.** A second `start_batch_fetch` while one runs is accepted, gets its own `job_id`, and its worker **queues behind the gate** (serialize) — it does not reject and does not fetch concurrently.
- **Store is never destructively capped.** `scan_depth=90` is a fetch-effort target; top-N is computed over the full stored pool via T2 `ranking`.
- **Signed-URL TTL ≈ 36 h.** `download_top` re-resolves any row older than ~24 h — already handled inside `run_download_reel`; the runner just calls it.
- **Standing orders (honored):** per-shortcode dedupe + monotonic numeric `media_id` watermark, **never** positional/newest-first (the fetch/resume/aggregation inherits this from `write_window`/`ranking`); the per-channel "needs more fetch" decision keys on coverage **contiguity** (`coverage.is_contiguous` / `has_more_to_fetch`), not raw pool count; aged-out/not-found = **typed error `partial=False`** (retry won't help) vs metered 401 = **`partial=True` + `stop_reason`** (retryable in minutes); `PINNED_PREFIX_BOUND=3` top_scan watermark protection stays intact through reuse of the existing window/ranking paths.
- Single developer, small scale, flat-file storage only (no DB, no external queue/broker).

## Success Metric

- **Primary metric:** A batch over ≥ 3 configured handles run against live IG (verify-by-pilot) reaches a terminal `done` state with a correct top-N — `global` returns the true cross-channel top-N by the requested sort key, `per_channel` returns top-N per handle — and the callback endpoint receives exactly that aggregated payload; when the runner is **killed mid-job and the process fully restarted**, `resume_pending_jobs(config)` re-adopts the job from its last checkpoint and completes without re-fetching already-covered pages (measured: cursor/coverage advances monotonically across the restart, no duplicate shortcodes appended, and no second worker ever fetches concurrently).
- **Counter-metric (must not regress):** No new anonymity leak (0 credential/`x-ig-app-id` bytes on the callback path — asserted by test); no politeness regression (≤ `max_pages_per_call` pages per window per handle, at most one IG window in flight process-wide even with two jobs started, never polls IG during a cooldown, and the existing 106-test suite stays green); no store corruption (manifest rows/columns preserved, dedupe + watermark discipline intact); no SSRF (callback rejects private/link-local/loopback/metadata targets and does not follow redirects — asserted by test).
- **Evaluation window:** One live pilot batch run plus the offline unit/integration suite in CI at merge time; observed until the job reaches `done`, the full-process-restart resume assertion passes, and the second-start-serialization assertion passes.
- **Evaluator:** vd (project owner) at PR review.

## Mode

- Greenfield (new `batch.py` module + a small `fetch_gate` primitive + a new `store/_batch/` artifact family) built on **modification-free reuse** of shipped T2/T3 code — the reused modules are not edited, only called and (in T5) wrapped at the gate.

## Existing Code Shape (reuse surface — not modified)

- `window.run_window(handle, *, config, client, store) -> WindowOutcome` — fetches and persists exactly one paced top_scan window for a handle, synchronous, **no sleep**, returns a typed `WindowOutcome` (carries `stop_reason`, persisted partial) even on stop_signal. This is the runner's per-page/window primitive; the runner owns the loop, the gate acquisition, and the sleeping around it.
- `list_reels._run_deepen` / `run_list_reels(...)` — the two-phase call-driven fill (top-check → deepen toward `scan_depth`), synchronous, never sleeps, capped at `max_pages_per_call`, returns a ranked partial on stop_signal. The batch per-handle fill mirrors this control flow but *loops across windows with gate-gated sleeps between them* instead of returning a partial after one call. (T5 will wrap this entrypoint at the same `FetchGate`.)
- `coverage.is_contiguous(segments, *, pool_depth, scan_depth)`, `coverage.has_more_to_fetch(segments)`, `coverage.segment_to_deepen(...)`, `coverage.apply_deepen(...)` — the contiguity + resume-cursor logic. The runner's "this handle still needs fetch" gate is `has_more_to_fetch(...) and not is_contiguous(...)`.
- `ranking.load_pool(csv_path)`, `filter_pool(...)`, `validate_sort_by(...)`, `rank(pool, sort_by)`, `select_top(...)` — pool load + filter + rank + top-N over the full manifest. Aggregation composes these; `global` merges pools across handles before `rank`, `per_channel` ranks each handle's pool independently.
- `store.Store` — `load_state`, `load_seen`, `write_window` (atomic append + state, dedupe + watermark), `save_coverage_segments`, `count_reels`, `handles_on_disk`, `find_reel`, `update_local_mp4`, `_write_state_atomic` (temp + fsync + `os.replace`). The batch checkpoint file reuses this atomic-write discipline (new helper on `Store`, same pattern).
- `download.run_download_reel(shortcode, *, config, client, store, now) -> dict` — never raises, typed envelope, cached-hit no-network gate, 24 h TTL re-resolve. `download_top` calls it per top-N shortcode (any internal re-resolve is IG-metered and must therefore run under the gate — see Step 7).
- `config.Config` — `channels[]`, `fetch.max_pages_per_call`, `output.store_dir`, `top_reels` filters. Batch adds a `batch:` config sub-block (schema pinned in Step 1).
- `http_client.AnonymousClient` — IG-only, owns `x-ig-app-id` + `assert_anonymous`. **Not** reused for the callback; the callback needs a separate bare `curl_cffi`/`requests`-style POST with no IG headers and redirect-follow disabled.

## Integration Points

- **FastMCP server (`mcp_server.py`)** — T5 will (a) register `start_batch_fetch` and `get_batch_status` as tools, (b) call `resume_pending_jobs(config)` once on server startup, and (c) wrap the sync `run_list_reels`/`run_download_reel` entrypoints so they acquire the same `FetchGate`. T4 exposes all of these as plain callables (`run_start_batch_fetch`, `run_get_batch_status`, `resume_pending_jobs`, and the `FetchGate` primitive) with envelope returns, mirroring how `run_list_reels`/`run_download_reel` are wired. T4 does not itself edit the server beyond a stub if needed.
- **Store directory** — new `store/_batch/<job_id>.json` checkpoint + `store/_batch/<job_id>.result.json`. Coexists with per-handle `store/<handle>.csv|.state.yaml`; the batch never writes into per-handle files except *through* `run_window`/`run_download_reel`.
- **Background execution** — a **daemon thread** started by `start_batch_fetch` (decision resolved below), plus the `resume_pending_jobs` relaunch path for after a restart. Must survive the return of the MCP call; a lost thread is recoverable from the durable checkpoint.
- **Process-wide fetch gate** — a module-level `FetchGate` singleton (Step 3): the single serialization + global-cooldown point for every IG hit in the process.
- **Callback endpoint** — arbitrary external HTTPS URL supplied by the caller; the only outbound non-IG network in the system, and the one SSRF surface (guarded in Step 8).

## Resolved Design Decisions (were Open Questions)

- **Background execution mechanism → daemon thread + durable checkpoint + `resume_pending_jobs`.** Simplest, resume-safe, no subprocess state-sharing complexity. Contract: *the server must stay up for a job to make progress*; if it dies, the checkpoint is durable and the job is re-adopted on next startup by `resume_pending_jobs`. (The "auto-resume daemon that watches for orphans while running" remains out of scope; the startup sweep is the resume mechanism.)
- **Cross-job / cross-tool fetch serialization → process-wide `FetchGate` (hard invariant).** One module-level gate serializes every IG window and carries the shared cooldown-until + escalation counter. A second `start_batch_fetch` queues behind it. (Step 3.)
- **Callback URL safety → resolved now:** https-only, DNS-resolve-and-block private/link-local/loopback/metadata ranges, redirect-follow disabled. (Step 8.)
- **`batch` config block → add a `batch:` block** (schema in Step 1), mirroring yt-media-kit ergonomics.
- **Checkpoint/result format → JSON** for the machine-written checkpoint + result (token-lean, exact round-trip), consistent with the "CSV/JSON for machine data, YAML for human state" split. Per-handle state files stay YAML (unchanged, owned by T1).

## Steps

1. **Define the batch job state model + on-disk schema + config block + result envelope** (`batch.py`: `BatchJob`, `JobPhase` enum, `HandleProgress`) — `job_id` (uuid4), `phase` (`queued|fetching|aggregating|downloading|calling_back|done|failed`), `params` (the pinned `start_batch_fetch` contract: `handles: list[str] | None`, `scope: "global"|"per_channel"`, `count: int`, `sort_by: str`, `filters: dict`, `download_top: bool`, `callback_url: str | None`), `per_handle` progress (last resume cursor, coverage segments snapshot, pages_this_window, outcome-flag keyed on contiguity), `sleep_until` epoch + `escalation_count` (for cooldown resume, **not** a poll), `heartbeat_at` (updated every window/phase for dead-worker detection), `result` ref, `callback` sub-state (attempts, next_retry_at, last_status), `created/updated`. Also define the **single stable result/callback envelope** used by both scopes: `{job_id, scope, sort_by, count, filters, status, generated_at, results, per_handle_fetch, downloads, errors}`, where `results` is *always* a map keyed by handle — `per_channel` uses real handle keys; `global` uses the one reserved key `"*"` → the merged top-N list (identical shape across scopes). Add the `batch:` config block: `retries` (default 5), `backoff_base_s`, `backoff_cap_s`, `cooldown_base_s`, `cooldown_escalation_factor`, `per_job_page_budget`, `heartbeat_stale_s`. Serialize `BatchJob` to `store/_batch/<job_id>.json`.
   - Acceptance: a `BatchJob` round-trips to disk and back; the result envelope validates for both scopes with byte-identical top-level keys; enum covers every phase used downstream; `batch:` config keys have documented defaults.
   - Parallel-safe with: none (foundational).

2. **Add atomic checkpoint read/write + orphan-tmp sweep to `Store`** (`Store.save_batch_job`, `Store.load_batch_job`, `Store.list_batch_jobs`, `Store.sweep_batch_tmp`) — reuse the existing temp-file + fsync + `os.replace` pattern from `_write_state_atomic`; write to `store/_batch/`. Checkpoint is rewritten **after every window** and every phase transition. `sweep_batch_tmp` removes inert leftover `*.tmp` files in `store/_batch/` (invoked from `resume_pending_jobs` and module init).
   - Acceptance: an interrupted write (simulated crash between temp-write and replace) never leaves a torn checkpoint — either the old or the new full file is present; `sweep_batch_tmp` removes a planted orphan `*.tmp` and leaves canonical files untouched; unit tests assert both via injected failure.
   - Parallel-safe with: Step 1's schema must exist first; otherwise independent.

3. **Build the process-wide fetch gate** (`fetch_gate.FetchGate`, a module-level singleton) — the single serialization + global-cooldown point for **all** IG-hitting work in the process. Exposes a context manager `acquire()` that: (a) blocks until it is the sole holder (mutual exclusion — at most one IG window in flight process-wide); (b) before yielding, if `now < cooldown_until`, **sleeps** until then (never polls IG during cooldown); (c) provides `note_metered_stop(stop_reason)` which advances a persisted/shared `escalation_count` and sets `cooldown_until = now + cooldown_base_s * factor**escalation_count` (bounded), and `note_success()` which decays the escalation counter. The batch runner wraps every `run_window` call in `acquire()`. Fairness: waiters are served FIFO so a second job queues behind rather than starving. The gate is a T4-owned primitive; T5 wraps the sync `list_reels`/`download` entrypoints in the same `acquire()`.
   - Acceptance: with two concurrent simulated fetchers, a call-log shows their windows strictly interleaved — never overlapping — and total in-flight count never exceeds 1 (fake clock); after an injected metered stop, the next `acquire()` sleeps the escalated duration before yielding and no IG call occurs during that interval; `note_success` decays the counter.
   - Parallel-safe with: independent of Steps 4-9 but they depend on it.

4. **Build the serialized per-handle fill loop** (`batch._fill_handle`) — for one handle, loop `run_window` calls **each wrapped in `FetchGate.acquire()`**: after each window, snapshot coverage via `save_coverage_segments`/state, update `heartbeat_at`, checkpoint (Step 2). Continue while `coverage.has_more_to_fetch(segments) and not coverage.is_contiguous(...)` **and** under `batch.per_job_page_budget`. On a `WindowOutcome` with a terminal `stop_reason` (metered 401): call `gate.note_metered_stop(stop_reason)` (which sets the escalated cooldown), persist `sleep_until` + `escalation_count`, checkpoint, and let the gate's cooldown sleep on the next `acquire()` do the waiting — never poll during the wait. Honor the per-window page cap from `config.fetch.max_pages_per_call`.
   - Acceptance: given an injected transport that returns a stop_signal on window 2, the loop checkpoints with an escalated `sleep_until`, the gate sleeps once, resumes from the exact persisted cursor on window 3, and terminates on contiguity — asserted with a fake clock (no real sleep) and a call-log showing no IG hit during the sleep interval.
   - Parallel-safe with: none — core sequential engine (depends on Step 3).

5. **Build the whole-job fetch driver** (`batch._run_job`) — iterate the job's handles **strictly sequentially**, calling `_fill_handle` per handle, checkpointing between handles so a restart resumes at the right handle + cursor. Classify per-handle terminal outcome into the envelope's `per_handle_fetch`: `covered` (contiguous), `partial` (budget/stop exhausted), or `error` (e.g., handle resolve not-found → `partial=False`). Transition job `phase` fetching→aggregating when all handles are drained.
   - Acceptance: a 3-handle job killed after handle 1 completes resumes at handle 2 (not handle 1) with handle 1's pool untouched; asserted via checkpoint inspection + resume run.
   - Parallel-safe with: none.

6. **Build aggregation** (`batch._aggregate`) — `validate_sort_by` once; for `scope="global"`, `load_pool` every handle's manifest, concatenate, `filter_pool`, `rank`, `select_top(count)` → the merged list stored under `results["*"]`; for `scope="per_channel"`, run load→filter→rank→`select_top` per handle → `results[handle]`. Both fill the **one stable envelope** from Step 1. Aggregation reads only the persisted manifests (dedupe + watermark already hold there), so it is order-safe by construction. Write `store/_batch/<job_id>.result.json` as the **first** action of the aggregating→(downloading|calling_back) transition, before any callback attempt.
   - Acceptance: golden-fixture test with 3 handles' seeded manifests asserts `global` `results["*"]` equals the true merged ranking and `per_channel` `results[handle]` equals each handle's independent top-N by the sort key; top-level envelope keys are identical for both scopes; ties + filter thresholds covered.
   - Parallel-safe with: independent of Step 7's design but runs after Step 5.

7. **Optional top-N download** (`batch._download_top`, gated on `download_top=true`) — phase aggregating→downloading; for each top-N shortcode call `run_download_reel(...)` (never raises; cached-hit no-network gate + 24 h TTL re-resolve already inside). CDN downloads are unmetered — no gate needed for the bytes — but because a *re-resolve* inside the downloader is IG-metered, the call is wrapped in `FetchGate.acquire()` so any re-resolve still serializes and respects cooldown. Record per-shortcode download outcome into the envelope's `downloads` map; a failed download is a typed note (`partial=False` entry), not a job failure.
   - Acceptance: with `download_top=true` and seeded fresh URLs, top-N mp4s land on disk and `downloads` records each path; a deliberately aged-out row yields a typed `partial=False` entry without failing the job; a re-resolve occurs only while holding the gate. Asserted with injected transport.
   - Parallel-safe with: Step 6 (needs the top-N list) — sequential after it.

8. **Build the callback poster with SSRF guard + retry+backoff** (`batch._post_callback`) — phase downloading→calling_back; POST the result envelope JSON to `callback_url` via a **bare non-IG transport** (no `x-ig-app-id`, no cookies). **SSRF guard, applied at validation (Step 9) and re-checked immediately before each POST:** require `https` scheme; **resolve the hostname and reject** if any resolved address is private (RFC1918), link-local (169.254.0.0/16, incl. the 169.254.169.254 cloud-metadata address), loopback (127.0.0.0/8, ::1), unique-local (fc00::/7), or otherwise non-global; **disable redirect-follow** so a 3xx cannot bounce the POST to an internal host or back to `instagram.com`. Bounded retries (`batch.retries`, default 5) with exponential backoff + jitter (`backoff_base_s`/`backoff_cap_s`); each attempt updates `callback.attempts`/`next_retry_at`/`last_status` and checkpoints. On success or exhausted retries, transition to `done` — the result is already persisted, so callback delivery is at-least-once, best-effort, and never blocks `done`.
   - Acceptance: a callback endpoint that 500s twice then 200s is hit exactly 3 times with growing gaps; a permanently-failing endpoint exhausts retries and the job still reaches `done` with the result intact and fetchable via `get_batch_status`; a callback pointing at `http://…`, at `169.254.169.254`, at a private/loopback host, or issuing a 302 to an internal host is **rejected without a POST reaching the internal target**; the outbound request carries zero IG headers/credentials.
   - Parallel-safe with: sequential after Step 7.

9. **Wire the entrypoints + background launch + restart resume** (`batch.run_start_batch_fetch`, `batch.run_get_batch_status`, `batch.resume_pending_jobs`) —
   - `start_batch_fetch` validates the pinned param contract (scope ∈ {global, per_channel}, `sort_by` via `validate_sort_by`, `count > 0`, `callback_url` via the Step 8 SSRF guard when present, handles ⊆ config∪on-disk), creates + checkpoints a `queued` job, launches `_run_job` on a **daemon thread** (which queues behind the `FetchGate` if another job is fetching), and returns `{job_id, phase: queued}` **immediately**.
   - `get_batch_status` loads the checkpoint and returns phase + per-handle progress + the final result if present — pure read, **no IG network, never triggers a fetch**, safe to call during a cooldown. It **classifies liveness**: `fetching` (worker alive, recent `heartbeat_at`), `cooldown-sleeping` (reports `sleep_until`), or `dead-worker` (non-terminal phase but `heartbeat_at` older than `batch.heartbeat_stale_s` and no live thread) so callers can tell a sleeping job from a crashed one. Unknown `job_id` → typed not-found envelope (never raises).
   - `resume_pending_jobs(config)` — **the restart-resume entrypoint.** Runs `Store.sweep_batch_tmp`, then sweeps `store/_batch/` for jobs in a non-terminal phase, and relaunches each from its checkpoint on a fresh daemon thread. **Idempotent:** it takes a per-`job_id` in-process launch guard so a job already running (or already relaunched) is not double-launched; re-entry re-fetches from the persisted cursor (dedupe + watermark make re-adoption safe). T4 exposes it; **T5 calls it once on server startup**; it also runs at `batch` module import/init so an in-process `start_batch_fetch` re-adopts orphans left by a prior crash.
   - Acceptance: `start_batch_fetch` returns a `job_id` in well under a second while a fake-clocked job continues; a **full-process-restart** simulation (discard all threads + in-memory state, call `resume_pending_jobs`) re-adopts a mid-fetch job and completes it from checkpoint with no duplicate shortcodes and no concurrent fetch; a second `start_batch_fetch` during an active job returns its own `job_id` and its worker serializes behind the gate; `get_batch_status` distinguishes fetching/cooldown/dead-worker and returns a typed not-found for an unknown `job_id`. All return typed envelopes, never raise.
   - Parallel-safe with: none — final integration.

10. **Offline unit suite + live pilot probe** — table-driven tests per behavior below on injected transports + fake clock (zero real sleeps, zero real IG hits in CI); plus a `probe/probe_batch.py` (written, **not** run in CI) for one real 3-handle pilot per verify-by-pilot.
    - Acceptance: full suite green alongside the existing 106; probe exists and is documented as manual-only.
    - Parallel-safe with: written alongside each step's code.

## Test Strategy

- **Instant return / detached continuation** — unit with fake clock: `start_batch_fetch` returns before `_run_job` completes; job advances independently. Asserts AC-1.
- **Cross-job / cross-tool serialization** — unit: two jobs (or a job + a simulated sync-tool acquirer) started against one `FetchGate`; a call-log shows windows strictly interleaved, in-flight count never exceeds 1, and the second start returns its own `job_id` (queued, not rejected). Guards the single-IP invariant across jobs.
- **Full-process-restart resume** — integration: run a job to a mid-fetch checkpoint, **discard all threads + in-memory state**, call `resume_pending_jobs(config)`, and assert the job re-adopts from checkpoint, cursor/coverage advance monotonically, no duplicate shortcodes, resume at the correct handle, and completes. Asserts the PRIMARY success metric + idempotency (double-call of `resume_pending_jobs` launches the job only once).
- **Checkpoint-resume after kill (in-loop)** — integration: run `_fill_handle`/`_run_job` to a checkpoint, discard the in-memory job, reload from disk, continue; assert standing-order watermark holds.
- **Cooldown discipline (escalation-sourced)** — behavioral: on injected stop_signal, the gate sets `cooldown_until` from `stop_reason` + `escalation_count`, exactly one sleep of that duration occurs, the call-log shows **no** IG request during the sleep interval, resume uses the persisted cursor, and `note_success` decays the counter. Guards the politeness invariant.
- **Aggregation correctness + stable envelope** — golden fixtures: `global` `results["*"]` = true merged top-N; `per_channel` `results[handle]` = per-handle top-N; top-level envelope keys byte-identical across scopes; tie-breaking + filter thresholds + sort-key validation. Asserts AC-3.
- **Callback retry + result durability** — unit with a stubbed HTTP endpoint: 500→500→200 hits exactly 3× with backoff growth; permanent-fail exhausts retries yet job reaches `done` and `get_batch_status` still returns the result.
- **Callback SSRF + redirect rejection** — assertion test: `http://` scheme, `169.254.169.254`, a private/loopback host, and a 302-to-internal-host are each rejected **without a POST reaching the internal target**; redirect-follow is off. Guards the SSRF surface.
- **Callback anonymity** — assertion test: the outbound callback request carries no `x-ig-app-id`, no cookies, no IG credentials, and does not go through `AnonymousClient`.
- **download_top** — unit with injected transport: top-N mp4s written + paths recorded in `downloads`; aged-out row → typed `partial=False` note without failing the job; any re-resolve occurs under the gate.
- **get_batch_status liveness + unknown id** — unit: status reports `fetching` vs `cooldown-sleeping` (with `sleep_until`) vs `dead-worker` (stale heartbeat), and returns a typed not-found envelope for an unknown `job_id`.
- **Atomic checkpoint write + tmp sweep** — fault-injection unit: crash between temp-write and `os.replace` leaves either the whole old or whole new file, never a torn one; `sweep_batch_tmp` clears a planted orphan.

### Shared State Inventory

| State | Type | Accessors (read/write paths) | Protection mechanism |
|---|---|---|---|
| `FetchGate` (process-wide mutex + `cooldown_until` + `escalation_count`) | in-memory singleton | acquired by every `run_window`/re-resolve in batch; (T5) by sync list_reels/download | mutual exclusion (one holder), FIFO waiters, cooldown sleep before yield — **the single-IP invariant lives here** |
| `store/_batch/<job_id>.json` (checkpoint) | persistent file | write: `_run_job`/`_fill_handle`/`_post_callback` after every window + phase change; read: `get_batch_status`, `resume_pending_jobs` | atomic temp+fsync+`os.replace`; **single writer** (the one job worker) — status reads are read-only |
| `store/<handle>.csv` + `.state.yaml` | persistent files | write: `run_window`/`run_download_reel` (via Store); read: aggregation `load_pool` | existing atomic write + dedupe + monotonic `media_id` watermark; the `FetchGate` guarantees no concurrent writer to the same handle |
| `store/_batch/<job_id>.result.json` | persistent file | write once by `_aggregate`; read by `_post_callback` + `get_batch_status` | atomic write; written before phase→calling_back so it's durable independent of callback |
| Per-`job_id` in-process launch guard | in-memory set/dict | written by `start_batch_fetch` + `resume_pending_jobs` | prevents double-launch of the same job across the two entrypoints (idempotent resume) |
| Background worker thread | in-memory / OS | created by `start_batch_fetch`/`resume_pending_jobs` | daemon thread; lost thread recoverable from durable checkpoint via `resume_pending_jobs` |
| IG rate-limit budget (external, per-IP) | external resource | every gated `run_window` call | the `FetchGate` (one holder + cooldown) + ≤ max_pages/window IS the protection |

### Invariants Under Concurrency

- **Single serialized IG fetcher (process-wide)** — at most one window in flight against IG at any instant across all jobs *and* the sync tools. Enforced by: the `FetchGate` mutex; the strictly sequential handle loop within a job; a second `start_batch_fetch` queuing behind the gate rather than fetching concurrently.
- **Global cooldown respected across callers** — a metered stop by any acquirer sets `cooldown_until` on the shared gate; every subsequent acquirer sleeps until then. No caller polls IG during a cooldown.
- **Checkpoint is never torn** — a reader (status/resume) always sees a fully-written prior or full new checkpoint. Enforced by: `os.replace` atomicity, single writer.
- **Restart is idempotent** — `resume_pending_jobs` never double-launches a job (launch guard) and re-adoption re-enters at the persisted cursor with no re-page and no positional recompute.
- **Monotonic `media_id` watermark + per-shortcode dedupe survive resume** — reuse of `write_window`/state + cursor, never recomputing from newest-first.
- **Result durability ⟂ callback delivery** — the aggregated result is persisted before any callback attempt; `done` does not require callback success.
- **No poll during cooldown** — no IG request is issued while `now < cooldown_until`; the gate sleeps rather than loops.

### Failure Mode Enumeration

| Failure point | What partial state results | Detection | Recovery |
|---|---|---|---|
| Crash mid-window (during `run_window`) | Store partial persisted by `write_window` (valid, deduped); checkpoint one window stale | on restart, checkpoint cursor vs store state | `resume_pending_jobs` relaunches; re-fetch of the in-flight page is idempotent (dedupe drops repeats) |
| Crash between `_fill_handle` handles | earlier handles covered, later untouched, checkpoint names next handle | checkpoint `per_handle` outcome-flags | resume at first not-covered handle |
| **Full process restart (daemon threads gone)** | all in-flight jobs orphaned but checkpoints durable | `resume_pending_jobs` sweep finds non-terminal phases | relaunch each from checkpoint on a fresh thread; launch guard prevents double-adopt |
| Crash after aggregate, before callback | `result.json` present, phase = calling_back | checkpoint phase | resume at callback (result already durable) |
| Crash during download_top | some mp4s on disk, others not | checkpoint per-shortcode download flags + cached-hit gate | resume; `run_download_reel` cached-hit gate skips already-downloaded (no network) |
| Callback endpoint permanently down | result durable, callback exhausted | `callback.attempts` == max | job → `done`; consumer retrieves result via `get_batch_status` |
| Callback URL points at internal/metadata host or redirects there | none — request refused before send | SSRF guard at validation + pre-POST re-check; redirect-follow disabled | reject with typed error; no POST reaches the target |
| Worker thread dies but process lives (unhandled fault) | checkpoint stale, phase non-terminal | `get_batch_status` reports `dead-worker` (stale `heartbeat_at`) | operator/T5 re-invokes `resume_pending_jobs` (or next start re-adopts) |
| Process killed during checkpoint write | temp file orphaned, canonical intact | leftover `*.tmp` in `_batch/` | `os.replace` guarantees canonical is old-or-new; `sweep_batch_tmp` clears the orphan on start |

### Idempotency Stance

- **Delivery semantics (callback):** at-least-once, best-effort — bounded retries; duplicates possible if the endpoint 200s but the ack is lost. Payload carries `job_id` so the receiver can dedupe.
- **Idempotency keys:** `job_id` (uuid4) for the job + callback + resume launch-guard; per-reel `shortcode` for dedupe/download (existing store discipline).
- **Retry policy (callback):** bounded (`batch.retries`, default 5), exponential backoff + jitter, no dead-letter (result stays durable and queryable via `get_batch_status` — that *is* the fallback).
- **Resume idempotency:** `resume_pending_jobs` guards against double-launch and re-enters at the persisted cursor; re-running a window at that cursor is safe — `write_window` dedupes by shortcode and only advances the watermark.

### Test Strategy for Races

- **Fault injection:** kill-and-restart at each checkpoint boundary (mid-window, between handles, post-aggregate, mid-download, mid-callback) via injected exceptions + reload-from-disk; assert the recovery-path row above — including the **full-process-restart** row via `resume_pending_jobs`.
- **Fake clock:** all sleeps/backoffs (gate cooldown + callback backoff) use an injectable clock so timing is asserted without real waits (mirrors T2/T3 `now=` injection).
- **Serialization assertion:** two acquirers against one `FetchGate` never overlap; a second `start_batch_fetch` queues behind rather than rejecting or racing.
- **Idempotent-resume assertion:** calling `resume_pending_jobs` twice launches each pending job exactly once.
- **No property-based race detector needed** — the design is single-fetcher by construction (the gate); the residual risk is crash-recovery correctness, covered by fault injection.

## Out of Scope

- **Multi-worker / parallel fetching** — the single-IP politeness invariant forbids it; explicitly not built.
- **A real job queue / broker (Celery, Redis, etc.)** — flat-file checkpoints + the in-process `FetchGate` only, per the no-DB stack rule.
- **Editing T2/T3 modules** — `window`/`coverage`/`ranking`/`store`/`download` are reused as-is. T4 *ships* the `FetchGate`; **wrapping the sync `list_reels`/`download` entrypoints at that gate is T5's wiring**, not a T2/T3 edit folded in here.
- **A continuously-running orphan-watcher daemon** — resume is triggered by `resume_pending_jobs` at server startup / module init (the defined entrypoint), not a background supervisor that polls for orphans while running.
- **Callback authentication / signing (HMAC, bearer tokens)** — deferred; the callback is an unauthenticated (but SSRF-guarded) best-effort POST unless a later ticket adds signing.
- **Job cancellation / deletion API** — not in T4 (candidate follow-up).
- **New MCP server registration wiring + the T5-side `resume_pending_jobs`/gate-wrap calls** — that is T5's job; T4 only exposes the callables.

## Risks

- **Background execution model in an MCP/stdio server** — likelihood med, impact med (was high; mitigated). A daemon thread dies if the server exits; **`resume_pending_jobs` on startup closes that gap**, so a lost thread is recoverable from the durable checkpoint. Residual: the server must be up for a job to progress (documented contract).
- **Gate contention starves a small job behind a long one** — likelihood low, impact low. Mitigation: the gate serializes per *window*, not per whole job, and waiters are FIFO, so a long job yields the gate between windows; a second job's windows interleave rather than blocking wholesale.
- **Cooldown escalation curve is approximate** — likelihood med, impact med. The 6.6→13 min escalation means the sleep formula (`cooldown_base_s * factor**escalation_count`) is an estimate. Mitigation: source from `stop_reason` + the persisted escalation counter with margin, decay on success, and confirm the exact curve by probe (Open Question). Never poll regardless of the number.
- **SSRF via DNS rebinding** — likelihood low, impact med. A hostname could resolve to a safe IP at validation and a metadata IP at POST time. Mitigation: re-resolve-and-check immediately before each POST (not only at validation) and disable redirect-follow.
- **Crash between aggregate and result-persist** — likelihood low, impact med. Mitigation: persist `result.json` as the *first* action of the aggregating transition (Step 6 ordering).
- **Resume re-fetches an in-flight page** — likelihood high, impact low. Mitigation: idempotent by dedupe + watermark; asserted by the restart-resume test.

## Open Questions

- **Exact cooldown escalation curve** — the precise `cooldown_base_s` / `cooldown_escalation_factor` that matches IG's observed 6.6→13 min (budget 48→36→12) escalation. The *mechanism* (stop_reason + persisted escalation counter, decay on success) is fixed; only the constants are pending a live probe. Ship conservative defaults with margin; tune from `probe/probe_batch.py`.
- **`heartbeat_stale_s` threshold for dead-worker detection** — how long a non-terminal job may go without a heartbeat before `get_batch_status` reports `dead-worker`. Needs one real run to size against a normal cooldown sleep (must exceed the longest expected cooldown so a legitimately sleeping job isn't misreported as dead). Proposal: `heartbeat_stale_s > max_expected_cooldown`; confirm after the escalation-curve probe.

## Handoff Notes

This artifact targets `consumer_role: implementation`. The consumer should:
- Honor the step order — Steps 1-3 (schema + atomic checkpoint + **`FetchGate`**) are foundational; the fetch engine (4-5), aggregation (6), download (7), callback (8), entrypoints+resume (9) build on them in sequence. **The `FetchGate` (Step 3) is a hard invariant, not optional** — every IG hit in the process goes through it.
- Treat "Out of Scope" as hard — no parallel fetching, no broker, no edits to T2/T3 modules (the sync-tool gate-wrap is T5's).
- Treat the test strategy + both concurrency sections as the **minimum** acceptance — the failure-mode recovery table (including full-process-restart), the SSRF/redirect rejection, the cross-job serialization, and the callback-anonymity + no-poll-during-cooldown invariants are required, not advisory.
- Only two genuinely-open items remain (cooldown constants + heartbeat-stale threshold), both tunable from the pilot probe and non-blocking to the build — implement with the conservative defaults in the `batch:` config block and tune after the probe.
- Re-plan only if the daemon-thread + `resume_pending_jobs` execution model proves unviable under FastMCP's process/stdio lifetime (would touch Steps 3/9).

— Plan ready.
