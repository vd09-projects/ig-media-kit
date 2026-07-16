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
version: 1
created: 2026-07-16T03:18:00Z
updated: 2026-07-16T03:18:00Z
prior_versions: []
---

# Task plan: T4 async batch runner — start_batch_fetch + get_batch_status + callback

**Overlays:** concurrency

## Problem

Build T4, the async batch subsystem — the *only* background-job path in the IG Media Kit. `start_batch_fetch` must hand back a `job_id` instantly and continue working detached: fill each configured (or requested) handle toward `scan_depth` across escalating IG rate-limit cooldowns (the runner is the system's only sleeper), checkpoint durably after every page so a kill/restart resumes rather than restarts, aggregate a top-N over the full stored pool (cross-channel `global` or `per_channel`), optionally download the top-N mp4s via the T3 downloader, and POST the aggregated result to an arbitrary callback URL with bounded retry+backoff. `get_batch_status` reads job state throughout and returns the final result even if the callback never lands. This is the last core tool before T5 wires all four into the FastMCP server.

## Constraints

- **ANONYMOUS ONLY.** Every IG hit goes through the existing `AnonymousClient` (sole owner of `x-ig-app-id`, `assert_anonymous` on send). The callback POST goes to a *non-IG* URL — it must carry **no** `x-ig-app-id`, no cookies, no credentials, and must use a separate plain transport (not `AnonymousClient`, which impersonates Chrome for IG). No code path authenticates.
- **Politeness is load-bearing and the runner is the ONLY sleeper.** Single IP ⇒ a single serialized fetch worker across the whole job — never fetch two handles concurrently. Reuse the T2 window discipline: pace pages ~1-2 s, cap ~`max_pages_per_call` per window, **stop + persist partial on first 401/stop_signal**, and **never poll during a cooldown** (it escalates ~6.6→13 min). On a metered stop, sleep **≥ 1 cooldown window**, then resume from checkpoint.
- **Store is never destructively capped.** `scan_depth=90` is a fetch-effort target; top-N is computed over the full stored pool via T2 `ranking`.
- **Signed-URL TTL ≈ 36 h.** `download_top` re-resolves any row older than ~24 h — already handled inside `run_download_reel`; the runner just calls it.
- **Standing orders (honored):** per-shortcode dedupe + monotonic numeric `media_id` watermark, **never** positional/newest-first (the fetch/resume/aggregation inherits this from `write_window`/`ranking`); the per-channel "needs more fetch" decision keys on coverage **contiguity** (`coverage.is_contiguous` / `has_more_to_fetch`), not raw pool count; aged-out/not-found = **typed error `partial=False`** (retry won't help) vs metered 401 = **`partial=True` + `stop_reason`** (retryable in minutes); `PINNED_PREFIX_BOUND=3` top_scan watermark protection stays intact through reuse of the existing window/ranking paths.
- Single developer, small scale, flat-file storage only (no DB, no external queue/broker).

## Success Metric

- **Primary metric:** A batch over ≥ 3 configured handles run against live IG (verify-by-pilot) reaches a terminal `done` state with a correct top-N — `global` returns the true cross-channel top-N by the requested sort key, `per_channel` returns top-N per handle — and the callback endpoint receives exactly that aggregated payload; when the runner is killed mid-job and restarted, it resumes from the last checkpoint and completes without re-fetching already-covered pages (measured: cursor/coverage advances monotonically across the restart, no duplicate shortcodes appended).
- **Counter-metric (must not regress):** No new anonymity leak (0 credential/`x-ig-app-id` bytes on the callback path — asserted by test); no politeness regression (the job issues ≤ `max_pages_per_call` pages per window per handle, never polls IG during a cooldown, and the existing 106-test suite stays green); no store corruption (manifest rows/columns preserved, dedupe + watermark discipline intact).
- **Evaluation window:** One live pilot batch run plus the offline unit/integration suite in CI at merge time; observed until the job reaches `done` and the checkpoint-resume assertion passes.
- **Evaluator:** vd (project owner) at PR review.

## Mode

- Greenfield (new `batch.py` module + a new `store/_batch/` artifact family) built on **modification-free reuse** of shipped T2/T3 code — the reused modules are not edited, only called.

## Existing Code Shape (reuse surface — not modified)

- `window.run_window(handle, *, config, client, store) -> WindowOutcome` — fetches and persists exactly one paced top_scan window for a handle, synchronous, **no sleep**, returns a typed `WindowOutcome` (carries `stop_reason`, persisted partial) even on stop_signal. This is the runner's per-page/window primitive; the runner owns the loop + the sleeping around it.
- `list_reels._run_deepen` / `run_list_reels(...)` — the two-phase call-driven fill (top-check → deepen toward `scan_depth`), synchronous, never sleeps, capped at `max_pages_per_call`, returns a ranked partial on stop_signal. The batch per-handle fill mirrors this control flow but *loops across windows with sleeps between them* instead of returning a partial after one call.
- `coverage.is_contiguous(segments, *, pool_depth, scan_depth)`, `coverage.has_more_to_fetch(segments)`, `coverage.segment_to_deepen(...)`, `coverage.apply_deepen(...)` — the contiguity + resume-cursor logic. The runner's "this handle still needs fetch" gate is `has_more_to_fetch(...) and not is_contiguous(...)`.
- `ranking.load_pool(csv_path)`, `filter_pool(...)`, `validate_sort_by(...)`, `rank(pool, sort_by)`, `select_top(...)` — pool load + filter + rank + top-N over the full manifest. Aggregation composes these; `global` merges pools across handles before `rank`, `per_channel` ranks each handle's pool independently.
- `store.Store` — `load_state`, `load_seen`, `write_window` (atomic append + state, dedupe + watermark), `save_coverage_segments`, `count_reels`, `handles_on_disk`, `find_reel`, `update_local_mp4`, `_write_state_atomic` (temp + fsync + `os.replace`). The batch checkpoint file reuses this atomic-write discipline (new helper on `Store`, same pattern).
- `download.run_download_reel(shortcode, *, config, client, store, now) -> dict` — never raises, typed envelope, cached-hit no-network gate, 24 h TTL re-resolve. `download_top` calls it per top-N shortcode.
- `config.Config` — `channels[]`, `fetch.max_pages_per_call`, `output.store_dir`, `top_reels` filters. Batch adds a small `batch` config sub-block (see Open Questions).
- `http_client.AnonymousClient` — IG-only, owns `x-ig-app-id` + `assert_anonymous`. **Not** reused for the callback; the callback needs a separate bare `curl_cffi`/`requests`-style POST with no IG headers.

## Integration Points

- **FastMCP server (`mcp_server.py`)** — T5 will register `start_batch_fetch` and `get_batch_status` as tools; T4 exposes them as plain callables (`run_start_batch_fetch`, `run_get_batch_status`) with envelope returns, mirroring how `run_list_reels`/`run_download_reel` are wired. T4 does not itself edit the server beyond a stub if needed.
- **Store directory** — new `store/_batch/<job_id>.json` (or `.yaml`) checkpoint + `store/_batch/<job_id>.result.json`. Coexists with per-handle `store/<handle>.csv|.state.yaml`; the batch never writes into per-handle files except *through* `run_window`/`run_download_reel`.
- **Background execution** — a detached worker (thread or subprocess — see Open Questions) started by `start_batch_fetch`. Must survive the return of the MCP call.
- **Callback endpoint** — arbitrary external HTTP(S) URL supplied by the caller; the only outbound non-IG network in the system.

## Steps

1. **Define the batch job state model + on-disk schema** (`batch.py`: `BatchJob`, `JobPhase` enum, `HandleProgress`) — `job_id` (uuid4), `phase` (`queued|fetching|aggregating|downloading|calling_back|done|failed`), `params` (handles, scope, top-N count, sort_by, filters, download_top, callback_url), `per_handle` progress (last resume cursor, coverage segments snapshot, pages_this_window, done-flag keyed on contiguity), `sleep_until` epoch (for cooldown resume, **not** a poll), `result` ref, `callback` sub-state (attempts, next_retry_at, last_status), `created/updated`. Serialize to `store/_batch/<job_id>.json`.
   - Acceptance: a `BatchJob` round-trips to disk and back; schema documented; enum covers every phase used downstream.
   - Parallel-safe with: none (foundational).

2. **Add atomic checkpoint read/write to `Store`** (`Store.save_batch_job`, `Store.load_batch_job`, `Store.list_batch_jobs`) — reuse the existing temp-file + fsync + `os.replace` pattern from `_write_state_atomic`; write to `store/_batch/`. Checkpoint is rewritten **after every window** and every phase transition.
   - Acceptance: an interrupted write (simulated crash between temp-write and replace) never leaves a torn checkpoint — either the old or the new full file is present; unit test asserts atomicity via injected failure.
   - Parallel-safe with: Step 1's schema must exist first; otherwise independent.

3. **Build the serialized per-handle fill loop** (`batch._fill_handle`) — for one handle, loop `run_window` calls: after each window, snapshot coverage via `save_coverage_segments`/state, checkpoint (Step 2). Continue while `coverage.has_more_to_fetch(segments) and not coverage.is_contiguous(...)` **and** under a page/window budget. On a `WindowOutcome` with a terminal `stop_reason` (metered 401): set `sleep_until = now + cooldown_window`, checkpoint, **sleep ≥ 1 window** (the only sleep site), then resume from the persisted cursor — never poll during the wait. Honor per-window page cap from `config.fetch.max_pages_per_call`.
   - Acceptance: given an injected transport that returns a stop_signal on window 2, the loop checkpoints, sleeps once, resumes from the exact persisted cursor on window 3, and terminates on contiguity — asserted with a fake clock (no real sleep) and a call-log showing no IG hit during the sleep interval.
   - Parallel-safe with: none — this is the core sequential engine.

4. **Build the whole-job fetch driver** (`batch._run_job`) — iterate the job's handles **strictly sequentially** (single IP invariant), calling `_fill_handle` per handle, checkpointing between handles so a restart resumes at the right handle + cursor. Classify per-handle terminal outcome: `covered` (contiguous), `partial` (budget/stop exhausted), or `typed-error` (e.g., handle resolve not-found → `partial=False`). Transition job `phase` fetching→aggregating when all handles are drained.
   - Acceptance: a 3-handle job killed after handle 1 completes resumes at handle 2 (not handle 1) with handle 1's pool untouched; asserted via checkpoint inspection + resume run.
   - Parallel-safe with: none.

5. **Build aggregation** (`batch._aggregate`) — `validate_sort_by` once; for `scope="global"`, `load_pool` every handle's manifest, concatenate, `filter_pool`, `rank`, `select_top(count)` → one cross-channel list; for `scope="per_channel"`, run load→filter→rank→`select_top` per handle → a dict keyed by handle. Aggregation reads only the persisted manifests (the standing-order dedupe + watermark already hold there), so it is order-safe by construction. Write `store/_batch/<job_id>.result.json`.
   - Acceptance: golden-fixture test with 3 handles' seeded manifests asserts `global` top-N equals the true merged ranking and `per_channel` equals each handle's independent top-N by the sort key; ties + filter thresholds covered.
   - Parallel-safe with: independent of Step 6's design but runs after Step 4.

6. **Optional top-N download** (`batch._download_top`, gated on `download_top=true`) — phase aggregating→downloading; for each top-N shortcode call `run_download_reel(...)` (never raises; cached-hit no-network gate + 24 h TTL re-resolve already inside). CDN downloads are unmetered — no sleep — but any *re-resolve* inside the downloader is metered and self-limits. Record per-shortcode download outcome into the result; a failed download is a typed note, not a job failure.
   - Acceptance: with `download_top=true` and seeded fresh URLs, top-N mp4s land on disk and the result records each path; a deliberately aged-out row yields a typed `partial=False` error entry without failing the job. Asserted with injected transport.
   - Parallel-safe with: Step 5 (needs the top-N list) — sequential after it.

7. **Build the callback poster with retry+backoff** (`batch._post_callback`) — phase downloading→calling_back; POST the aggregated result JSON to `callback_url` via a **bare non-IG transport** (no `x-ig-app-id`, no cookies). Bounded retries (config, e.g. 5) with exponential backoff + jitter; each attempt updates `callback.attempts`/`next_retry_at`/`last_status` and checkpoints. On success or exhausted retries, transition to `done` (the result is already persisted regardless — callback delivery is at-least-once, best-effort, and never blocks `done`).
   - Acceptance: a callback endpoint that 500s twice then 200s is hit exactly 3 times with growing gaps; a permanently-failing endpoint exhausts retries and the job still reaches `done` with the result intact and fetchable via `get_batch_status`. Anonymity test asserts the outbound request carries zero IG headers/credentials.
   - Parallel-safe with: sequential after Step 6.

8. **Wire the two entrypoints + background launch** (`batch.run_start_batch_fetch`, `batch.run_get_batch_status`) — `start_batch_fetch` validates params (scope ∈ {global, per_channel}, sort_by via `validate_sort_by`, callback_url shape, handles ⊆ config∪on-disk), creates + checkpoints a `queued` job, launches `_run_job` detached (background thread/subprocess), and returns `{job_id, phase: queued}` **immediately**. `get_batch_status` loads the checkpoint and returns phase + per-handle progress + the final result if present — pure read, **no IG network, never triggers a fetch**, safe to call during a cooldown.
   - Acceptance: `start_batch_fetch` returns a `job_id` in well under a second while a fake-clocked job continues; `get_batch_status` returns live phase transitions and, after completion, the full result even when the callback was configured to always fail. Both return typed envelopes, never raise.
   - Parallel-safe with: none — final integration.

9. **Offline unit suite + live pilot probe** — table-driven tests per behavior below on injected transports + fake clock (zero real sleeps, zero real IG hits in CI); plus a `probe/probe_batch.py` (written, **not** run in CI) for one real 3-handle pilot per verify-by-pilot.
   - Acceptance: full suite green alongside the existing 106; probe exists and is documented as manual-only.
   - Parallel-safe with: written alongside each step's code.

## Test Strategy

- **Instant return / detached continuation** — unit with fake clock: `start_batch_fetch` returns before `_run_job` completes; job advances independently. Asserts AC-1.
- **Checkpoint-resume after kill** — integration: run `_fill_handle`/`_run_job` to a checkpoint, discard the in-memory job, reload from disk, continue; assert cursor/coverage advance monotonically, no duplicate shortcodes, resume at the correct handle. Asserts AC-2 + standing-order watermark.
- **Cooldown discipline** — property/behavioral: on injected stop_signal, exactly one sleep ≥ one window occurs, the call-log shows **no** IG request during the sleep interval, and resume uses the persisted cursor. Guards the politeness invariant.
- **Aggregation correctness** — golden fixtures: `global` = true merged top-N; `per_channel` = per-handle top-N; tie-breaking + filter thresholds + sort-key validation. Asserts AC-3.
- **Callback retry + result durability** — unit with a stubbed HTTP endpoint: 500→500→200 hits exactly 3× with backoff growth; permanent-fail exhausts retries yet job reaches `done` and `get_batch_status` still returns the result. Asserts AC-4.
- **Callback anonymity** — assertion test: the outbound callback request carries no `x-ig-app-id`, no cookies, no IG credentials, and does not go through `AnonymousClient`. Guards the anonymity invariant on the one non-IG network path.
- **download_top** — unit with injected transport: top-N mp4s written + paths recorded; aged-out row → typed `partial=False` note without failing the job. Asserts AC-5.
- **Atomic checkpoint write** — fault-injection unit: crash between temp-write and `os.replace` leaves either the whole old or whole new file, never a torn one.

### Shared State Inventory

| State | Type | Accessors (read/write paths) | Protection mechanism |
|---|---|---|---|
| `store/_batch/<job_id>.json` (checkpoint) | persistent file | write: `_run_job`/`_fill_handle`/`_post_callback` after every window + phase change; read: `get_batch_status`, resume-on-restart | atomic temp+fsync+`os.replace`; **single writer** (the one job worker) — status reads are read-only |
| `store/<handle>.csv` + `.state.yaml` | persistent files | write: `run_window`/`run_download_reel` (via Store); read: aggregation `load_pool` | existing atomic write + dedupe + monotonic `media_id` watermark (unchanged); serialized single fetch worker means no concurrent writer to the same handle |
| `store/_batch/<job_id>.result.json` | persistent file | write once by `_aggregate`; read by `_post_callback` + `get_batch_status` | atomic write; written before phase→calling_back so it's durable independent of callback |
| Background worker handle (thread/subprocess) | in-memory / OS | created by `start_batch_fetch`; not shared across jobs | one worker per job; single-IP invariant forbids parallel fetch even across jobs (see Open Questions on cross-job serialization) |
| IG rate-limit budget (external, per-IP) | external resource | every `run_window` call | the single serialized worker + ≤ max_pages/window + sleep-on-cooldown IS the protection |

### Invariants Under Concurrency

- **Single serialized IG fetcher** — at most one window in flight against IG at any instant for the whole process. Enforced by: a strictly sequential handle loop within a job, and (Open Question) a cross-job lock/queue so two jobs can't fetch simultaneously on the one IP.
- **Checkpoint is never torn** — a reader (status/resume) always sees a fully-written prior or full new checkpoint. Enforced by: `os.replace` atomicity, single writer.
- **Monotonic `media_id` watermark + per-shortcode dedupe survive resume** — resume re-enters at the persisted cursor; no re-page, no positional ordering. Enforced by: reuse of `write_window`/state + cursor, never recomputing from newest-first.
- **Result durability ⟂ callback delivery** — the aggregated result is persisted before any callback attempt; `done` does not require callback success. Enforced by: phase ordering (aggregate+persist → then calling_back).
- **No poll during cooldown** — no IG request is issued while `now < sleep_until`. Enforced by: the sleep site is the only place that waits, and it sleeps rather than loops.

### Failure Mode Enumeration

| Failure point | What partial state results | Detection | Recovery |
|---|---|---|---|
| Crash mid-window (during `run_window`) | Store partial persisted by `write_window` (valid, deduped); checkpoint one window stale | on restart, checkpoint cursor vs store state | resume from checkpoint cursor; `run_window` re-fetch of the in-flight page is idempotent (dedupe drops repeats) |
| Crash between `_fill_handle` handles | earlier handles covered, later untouched, checkpoint names next handle | checkpoint `per_handle` done-flags | resume at first not-done handle |
| Crash after aggregate, before callback | `result.json` present, phase = calling_back | checkpoint phase | restart resumes at callback (result already durable) |
| Crash during download_top | some mp4s on disk, others not | checkpoint per-shortcode download flags + cached-hit gate | resume; `run_download_reel` cached-hit gate skips already-downloaded (no network) |
| Callback endpoint permanently down | result durable, callback exhausted | `callback.attempts` == max | job → `done`; consumer retrieves result via `get_batch_status` |
| Process killed during checkpoint write | temp file orphaned, canonical intact | leftover `*.tmp` in `_batch/` | `os.replace` guarantees canonical is old-or-new; orphan tmp is inert (optional sweep on start) |

### Idempotency Stance

- **Delivery semantics (callback):** at-least-once, best-effort — bounded retries; duplicates possible if the endpoint 200s but the ack is lost. Payload carries `job_id` so the receiver can dedupe.
- **Idempotency key:** `job_id` (uuid4) for the job + callback; per-reel `shortcode` for dedupe/download (existing store discipline).
- **Retry policy (callback):** bounded (config, default ~5), exponential backoff + jitter, no dead-letter (result stays durable and queryable via `get_batch_status` — that *is* the fallback).
- **Fetch idempotency:** re-running a window at a persisted cursor is safe — `write_window` dedupes by shortcode and only advances the watermark; no positional recompute.

### Test Strategy for Races

- **Fault injection:** kill-and-restart at each checkpoint boundary (mid-window, between handles, post-aggregate, mid-download, mid-callback) via injected exceptions + reload-from-disk; assert the recovery-path row above.
- **Fake clock:** all sleeps/backoffs use an injectable clock so cooldown + retry timing is asserted without real waits (mirrors T2/T3 `now=` injection).
- **Single-writer assertion:** a test that attempts a second concurrent fetch is rejected/serialized (guards the single-IP invariant) — depends on the cross-job serialization decision.
- **No property-based race detector needed** — the design is single-writer by construction; the risk is crash-recovery correctness, covered by fault injection, not data-race detection.

## Out of Scope

- **Multi-worker / parallel fetching** — the single-IP politeness invariant forbids it; explicitly not built.
- **A real job queue / broker (Celery, Redis, etc.)** — flat-file checkpoints only, per the no-DB stack rule.
- **Editing T2/T3 modules** — `window`/`coverage`/`ranking`/`store`/`download` are reused as-is; any needed change is a separate ticket, not folded in here.
- **Callback authentication / signing (HMAC, bearer tokens)** — deferred; the callback is an unauthenticated best-effort POST unless a later ticket adds signing.
- **Cross-restart auto-resume daemon** — resume is triggered by a fresh `start`/explicit resume call or process relaunch, not a background supervisor that watches for orphaned jobs (can be a follow-up).
- **Job cancellation / deletion API** — not in T4 (candidate follow-up).
- **New MCP server registration wiring** — that is T5's job; T4 only exposes the callables.

## Risks

- **Background execution model in an MCP/stdio server** — likelihood med, impact high. A thread inside the server process dies if the server exits; a subprocess survives but complicates state sharing. Mitigation: decide explicitly (Open Question 1); default to a daemon thread with durable checkpoints so a lost thread is recoverable by resume, and document the "server must stay up for the job to progress" contract.
- **Two jobs racing the single IP** — likelihood med, impact high (cooldown escalation). Mitigation: a process-wide fetch lock / single job-runner queue so only one job fetches at a time; second job waits (Open Question 1).
- **Cooldown window length is a guess** — likelihood med, impact med. The 6.6→13 min escalation means a fixed sleep may under-wait. Mitigation: sleep the *observed/expected* window with margin, and treat a repeat immediate 401 as an escalation signal → longer sleep; never poll.
- **Callback SSRF / arbitrary-URL POST** — likelihood low, impact med. Posting to a user-supplied URL is an SSRF surface. Mitigation: basic scheme/host validation (https, no internal/metadata IPs) — flagged as Open Question 3; accepted-minimal for single-dev use if deferred.
- **Crash between aggregate and result-persist** — likelihood low, impact med (lost aggregation work). Mitigation: persist `result.json` as the *first* action of the aggregating→calling_back transition, before any callback attempt (covered in Step 5/7 ordering).
- **Resume re-fetches an in-flight page** — likelihood high, impact low. Mitigation: idempotent by dedupe + watermark; asserted by the checkpoint-resume test.

## Open Questions

- **Background execution mechanism** — daemon thread vs subprocess vs `asyncio` task? Blocks Step 8 launch design and the cross-job serialization risk. Leaning daemon thread + durable checkpoint (simplest, resume-safe) — needs confirmation given FastMCP's process/stdio model.
- **Cross-job fetch serialization** — one global fetch lock (only one job fetches IG at a time, others queue) vs forbidding a second `start_batch_fetch` while one runs? Blocks the single-IP invariant enforcement in Steps 4/8.
- **Callback URL safety policy** — enforce https + block private/link-local/metadata addresses now, or defer to a follow-up? Blocks Step 7 validation scope.
- **`batch` config block shape** — retry count, backoff base/cap, cooldown window seconds, per-job page budget: new `config.batch.*` keys vs reuse `fetch.*`? Blocks Steps 1/3/7 defaults. Proposal: add a small `batch:` block mirroring yt-media-kit ergonomics.
- **Checkpoint format** — JSON vs YAML? State files are YAML today; batch artifacts were speced as `store/_batch/<job_id>.*`. Proposal: JSON for the machine-written checkpoint/result (token-lean, exact round-trip), consistent with the "CSV/JSON for machine data, YAML for human state" split — confirm.

## Handoff Notes

This artifact targets `consumer_role: implementation`. The consumer should:
- Honor the step order — Steps 1-2 (schema + atomic checkpoint) are foundational; the fetch engine (3-4), aggregation (5), download (6), callback (7), entrypoints (8) build on them in sequence.
- Treat "Out of Scope" as hard — no parallel fetching, no broker, no edits to T2/T3 modules.
- Treat the test strategy + both concurrency sections as the **minimum** acceptance — the failure-mode recovery table and the callback-anonymity + no-poll-during-cooldown invariants are required, not advisory.
- Resolve the five Open Questions (execution model, cross-job serialization, callback URL policy, config shape, checkpoint format) before or early in Step 1 — three of them block foundational steps.
- Re-plan if the background-execution decision invalidates the checkpoint/resume design (≥ 2 steps).
