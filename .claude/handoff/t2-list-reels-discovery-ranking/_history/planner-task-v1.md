---
artifact_type: handoff
artifact_version: 2
producer_role: planner
consumer_role: implementation
plan_type: task
slug: t2-list-reels-discovery-ranking
scope_hint: T2 — list_reels: anonymous discovery + ranking (call-driven fill)
canonical_name: planner-task
task_title: T2 — list_reels: anonymous discovery + ranking (call-driven fill)
owner: vd
overlays: []
status: draft
version: 1
created: 2026-07-15T14:22:41Z
updated: 2026-07-15T14:22:41Z
prior_versions: []
---

# Task plan: T2 — list_reels: anonymous discovery + ranking (call-driven fill)

Task-level breakdown for the first full MCP tool: `list_reels(handle, count, sort_by, min_views, min_duration, max_age_days, scan_depth=90, fresh_fetch=false)` — the interactive, fast, NEVER-BLOCKING surface that returns a channel's top-N reels ranked, filling the store call-by-call up to `scan_depth`. This is Flow A on top of the merged T1 foundation (PR #7): a serve-from-store fast path, a top-check phase (T1 `top_scan`), a deepen phase (T1 `deep_resume`), coverage-segment tracking so a gap opened by a burst of new posts is remembered, partial-on-stop_signal handling, and rank-over-the-full-pool. No downloads.

## Problem

T1 shipped the shared fetch primitive, the durable-first CSV+YAML store, and a `run_window()` sync path, but `mcp_server.list_reels` is only a T1 skeleton that runs a single `top_scan` window. T2 must deliver the real tool: on a new handle, call 1 returns ~40 top reels and anchors the store; repeated spaced calls fill the pool toward `scan_depth=90`; once the pool is deep enough, calls become a cheap top-refresh (or, with `fresh_fetch=false`, no network at all). Every call must apply the filters + sort over the full accumulated pool and return the top `count` — while never sleeping, never exceeding the metered page budget, and always degrading to a clean partial with a note if IG throttles mid-call.

## Constraints

- **Blast radius:** single MCP tool + additive store-state fields (`coverage_segments`). Builds on T1's frozen primitive/store contracts — must NOT modify T1's fetch loop, stop_signal classifier, or durable-first write ordering.
- **ANONYMOUS ONLY** — no login/cookies/session/account, ever. No code path may authenticate (T1's `assert_anonymous` guard stays intact; T2 adds no new request path that bypasses `AnonymousClient`).
- **Politeness is load-bearing** — one `list_reels` call spends AT MOST the ~4-page / ~40-item metered budget across BOTH phases combined; paces pages ~1-2s (via the T1 primitive); STOPS + returns partial on the first stop_signal; NEVER sleeps (passes `sleep=None` — the sync path); NEVER polls during a cooldown.
- **Metadata metered, CDN not** — `list_reels` touches only the metered metadata API; it returns metadata and resolvable URLs, performs NO downloads.
- **Single dev, small scale** — no multi-tenant, no concurrency-safety requirement beyond T1's atomic state write.
- **Mirror yt-media-kit** — filter names/semantics (`min_views`, `min_duration`, `max_age_days`) and `sort_by` field set mirror yt-media-kit where they translate.

## Success Metric

- **Primary metric:** Against a real public handle with a cold store, **call 1** of `list_reels` returns ~40 ranked reels (default `play_count` desc), writes CSV + state with `high_water_media_id`, `deep_cursor`, and `coverage_segments` set, and issues at most the ~4-page budget. A sequence of **spaced repeat calls** grows the stored pool monotonically toward `scan_depth=90` (each call adds older reels via deepen and/or newer reels via top-check) until `depth >= 90`. Once `depth >= 90`, a call with `fresh_fetch=false` returns the ranked top-`count` with **ZERO network requests**. A call with `fresh_fetch=true` (or on a not-yet-deep pool) runs a top-check that surfaces any genuinely new reels. All filters (`min_views`/`min_duration`/`max_age_days`) and every supported `sort_by` produce a correctly filtered+ordered top-`count` computed over the FULL pool. Verified end-to-end against a live handle before T2 is done.
- **Counter-metric (must not regress):** The tool NEVER sleeps and NEVER issues more than the ~4-page budget per call (top-check + deepen combined). Zero authenticated requests on any path. A stop_signal (any kind, per T1's classifier) mid-call yields a valid partial result — the already-persisted pool, ranked, returned with a clear "budget cooling" note and the typed reason — with no traceback and no cursor advanced past unpersisted rows (T1 durable-first upheld). The store is NEVER destructively capped: `scan_depth` bounds fetch effort only; the pool and top-N are computed over everything ever fetched. A steady-state top-check on a caught-up handle still fetches at most 1 page (T1's `pages_fetched == 1` anti-regression check is not defeated by T2's orchestration).
- **Evaluation window:** one live cold-start run + a spaced repeat-call sequence to depth + one serve-from-store run + one injected-stop_signal run, within the build session.
- **Evaluator:** sindri's verify step / build-session review, against a real handle.

## Mode

- Modification (fleshes out the T1 `list_reels` skeleton; adds one orchestration module + additive state fields).

## Existing Code Shape (modification only)

From T1 (PR #7), consumed as-is:
- **`config.py`** — loader; `top_reels` filter defaults + per-call override merge (call args shallow-merge over config).
- **`http_client.py`** — `AnonymousClient` with the `stop_signal` classifier (`ok | stop(reason) | error`) and `assert_anonymous` guard. Used only through T1; T2 adds no new request path.
- **`fetch.py`** — `resolve_user_id`, `normalize_item` (clips dispatch — captures `shortcode`, `media_id`/`pk`, `play_count`, `video_url`, `fetched_at`, and — to confirm in T2.0 — `taken_at`/duration), `fetch_window(mode=top_scan | deep_resume)` capped `<=4 pages/call`, stops on first stop_signal, sleeps only if a `sleep` callable is supplied. Emits normalized list, newest `media_id` + shortcode, `deep_cursor` (next_max_id), `pages_fetched`, partial flag, typed stop reason (`caught_up` / stop_signal reason / `page_cap` / `end_of_feed`).
- **`store.py`** — CSV manifest + YAML state per handle; per-shortcode skip-seen dedupe; `high_water_media_id`, `seen`, `deep_cursor`, `last_stop_reason`; durable-first `write_window` (CSV fsync'd before anchors advance; state via temp-file + os.replace).
- **`window.py`** — `run_window()` sync path (`sleep=None`) composing fetch → normalize → store for one `top_scan` window.
- **`mcp_server.py`** — `list_reels` wired to `run_window` as the T1 skeleton (to be replaced in T2.9).

What changes: `list_reels` grows from a single `top_scan` window into the full two-phase, budget-governed, rank-returning Flow A. State gains `coverage_segments`. A new `list_reels` orchestration module (e.g. `list_reels.py`) and a `ranking.py` filter+sort module are added.

## Integration Points

- **T1 `fetch_window`** — reused unchanged for both phases (`top_scan` and `deep_resume`). T2 must not alter its page cap, stop conditions, or output contract.
- **T1 `store.write_window` + state** — reused for durable-first persistence; T2 extends state with `coverage_segments` (additive; existing fields untouched).
- **T1 `run_window`** — T2 either calls it per phase or inlines its compose; either way passes `sleep=None`.
- **`config.py` filter defaults** — `list_reels` args override config `top_reels` defaults via the T1 merge rule.
- **FastMCP tool registration** — `list_reels` is the public MCP surface; its return envelope is the tool's published output shape (consumed by MCP clients; the future LLM layer reads the CSV).
- **yt-media-kit** — reference for `sort_by` field names, filter units, and result-record columns.

## Steps

1. **T2.0 — Confirm rank/filter input fields exist (verify-by-pilot micro-gate)** — Before building filters/sort, confirm from a live feed item (or T1's recorded probe output) that each field the filters/sort need is present and typed: `play_count` (int, may be 0/null for non-clips — already filtered out), a duration field (seconds) for `min_duration`, and a post timestamp (`taken_at`, epoch) for `max_age_days`. Confirm the yt-media-kit `sort_by` vocabulary to mirror.
   - Acceptance: a written list of the exact normalized field names backing each of `min_views`, `min_duration`, `max_age_days`, and each `sort_by` value; any missing field is either added to `normalize_item` (T2.8) or flagged as unsupported with a documented fallback. No filter/sort is built on an assumed field.
   - Parallel-safe with: none (gates T2.8).
2. **T2.1 — Define the `list_reels` call contract + result envelope** — Specify the full signature `list_reels(handle, count, sort_by, min_views, min_duration, max_age_days, scan_depth=90, fresh_fetch=false)` and the return envelope: the ranked top-`count` reel records, a `partial` flag, a human-readable `note` (empty on a clean full run; "budget cooling"/typed reason on a stop; "served from store" on the no-network path), `pool_depth`, `pages_fetched`, and a coverage summary (segments + whether the pool is `complete` to `scan_depth`). Args merge over config `top_reels` defaults (T1 rule).
   - Acceptance: a documented envelope schema; unknown/invalid `sort_by` rejected with a clear error (or defaulted per T2.8's whitelist) rather than silently mis-sorting; `count`/`scan_depth`/filter args validated (non-negative; `count` may exceed pool → return whatever the pool yields).
   - Parallel-safe with: T2.0.
3. **T2.2 — Serve-from-store fast path + pool-depth accounting** — Load state + pool. Define `pool_depth` = count of stored clips for the handle. Gate: if `fresh_fetch == false` AND `pool_depth >= scan_depth`, skip ALL network — go straight to rank (T2.8) and return with a "served from store" note. This is the "thereafter cheap top-refresh" degenerating to zero-cost when the pool is already deep and the caller didn't force freshness.
   - Acceptance: with a pre-populated store where `depth >= scan_depth` and `fresh_fetch=false`, `list_reels` issues ZERO metadata requests (assert via the T1 client call count / a no-network spy) and returns the ranked top-`count`; with `fresh_fetch=true` the gate is bypassed and a top-check runs.
   - Parallel-safe with: T2.1.
4. **T2.3 — Single-call page-budget governor** — A per-call budget object (default ~4 pages ≈ ~40 items, sourced so it stays in lockstep with T1's page cap) shared across BOTH phases. Top-check draws first; deepen draws from what remains. Reserve a minimum deepen allotment (e.g. at least 1 page for deepen when the pool is not yet at `scan_depth` and top-check did not itself exhaust the budget) so a busy handle's top-check cannot starve backfill forever — but the combined draw NEVER exceeds the ~4-page cap. The governor passes `sleep=None` (sync, never blocks) and stops feeding phases the moment a stop_signal returns or the budget hits zero.
   - Acceptance: across a call, total `pages_fetched` (top-check + deepen) `<= 4`; on a not-yet-deep pool with a caught-up top (top-check spends 1 page) the remaining budget flows to deepen; the governor never invokes a sleep; a stop_signal in phase 1 means phase 2 is not started.
   - Parallel-safe with: none (governs T2.4 + T2.6).
5. **T2.4 — Top-check phase (wire T1 `top_scan`)** — Run `fetch_window(mode=top_scan)` from the newest item, short-circuiting on seen-set membership / `high_water_media_id` watermark (T1 semantics), merging genuinely-new reels into the pool durable-first and bumping `high_water_media_id`. On a COLD handle (no anchors) this is the initial fill that walks the budget once and returns ~40 top reels + anchors the store (matches AC "call 1 ~= 40 top reels"). On a caught-up handle it fetches 1 page and adds zero rows (T1's `pages_fetched == 1` preserved).
   - Acceptance: cold handle → ~40 reels persisted, `high_water_media_id` + `deep_cursor` set; caught-up handle → 1 page, zero new rows, `caught_up`; handle with N (< one window) new posts → exactly those N merged, no duplicates.
   - Parallel-safe with: none (runs before T2.6 within a call).
6. **T2.5 — Coverage-segment tracking** — Extend state with `coverage_segments`: an ordered list of contiguous `[newest_media_id, oldest_media_id, resume_cursor]` spans the store has actually covered. Normally ONE segment (top → deep_cursor). When a top-check hits the page cap WITHOUT catching up to the prior `high_water_media_id` (i.e. more than ~one window of new posts appeared since the last call, `stop_reason == page_cap` and the batch's oldest item is still newer than the prior segment's newest), a GAP exists between the freshly-fetched newest span and the previously-stored span → record a NEW segment with its own `resume_cursor`. Deepen (T2.6) always works the OLDEST not-yet-joined segment's cursor so segments converge over successive calls. Merge adjacent segments when a deepen pass bridges them.
   - Acceptance: a simulated burst of > one window of new posts opens a 2nd segment with a valid resume cursor (no reel silently skipped — the gap is recorded, not lost); a later deepen pass that reaches the older segment merges the two into one; steady state stays single-segment. Round-trips through state.yaml (additive; T1 fields untouched).
   - Parallel-safe with: none (feeds T2.6).
7. **T2.6 — Deepen phase (wire T1 `deep_resume`)** — If `pool_depth < scan_depth` AND there is more to fetch (the oldest open segment's `resume_cursor` is set / `more_available`) AND budget remains, run `fetch_window(mode=deep_resume)` from that segment's cursor, paging OLDER, persisting durable-first, until `pool_depth >= scan_depth` OR the shared budget is spent OR `end_of_feed`. Advances the worked segment's `oldest`/`resume_cursor`; leaves `high_water_media_id` untouched (deepen never moves the top anchor). This is the across-calls fill toward 90.
   - Acceptance: on a store with `depth < scan_depth` and budget remaining after top-check, deepen adds older reels and advances the segment cursor; repeated spaced calls drive `pool_depth` monotonically up to `>= scan_depth`; once `depth >= scan_depth`, deepen is skipped (no network from this phase); `end_of_feed` on a short account stops cleanly and marks the segment terminal.
   - Parallel-safe with: none.
8. **T2.7 — Partial-result handling on stop_signal** — In EITHER phase, on the first stop_signal (per T1's classifier), stop immediately: the T1 primitive already returns the partial + typed reason and the store already persisted rows durable-first. T2 catches this, does NOT start/continue further phases, does NOT sleep or poll, sets `partial=true`, and returns the ranked top-`count` over whatever pool exists WITH a clear note (e.g. "budget cooling — IG rate limit hit; returned N of the stored pool; retry after a few minutes") carrying the typed reason. No exception escapes to the MCP client.
   - Acceptance: injecting each stop_signal kind mid-top-check and mid-deepen yields a ranked partial + note + `partial=true`, zero traceback, zero sleep, and the persisted pool is intact (cursor/anchor not advanced past unpersisted rows).
   - Parallel-safe with: none.
9. **T2.8 — Filter + rank over the full pool** — A `ranking` module: load the FULL stored pool (not just this call's fetch), apply `min_views` (play_count >=), `min_duration` (duration secs >=), `max_age_days` (now - taken_at <= days) — skipping filters left unset — then sort by `sort_by` from a validated whitelist (default `play_count` desc; others mirror yt-media-kit, e.g. `taken_at`/recency, `duration`), then take the top `count`. NO downloads. Top-N is computed over the pool AFTER filtering; `count > pool` returns whatever remains. Filters that exclude everything return an empty list with a note, not an error.
   - Acceptance: golden-style checks — a fixed pool + each filter in isolation + each `sort_by` produces the expected ordered subset; default sort is `play_count` desc; an unknown `sort_by` is rejected/defaulted per T2.1; `count` larger than the filtered pool returns the whole filtered pool; no `video_url` is fetched/downloaded during ranking.
   - Parallel-safe with: T2.2 (both are store-read/rank concerns), after T2.0.
10. **T2.9 — Wire the full flow into `mcp_server.list_reels`** — Replace the T1 skeleton: compose T2.2 (serve-from-store gate) → T2.3 governor over T2.4 (top-check) → T2.5 (coverage) → T2.6 (deepen) → T2.7 (partial guard) → T2.8 (rank) → return the T2.1 envelope. Ensure `list_reels` remains the only MCP entry point touched (other three tools stay later tickets).
    - Acceptance: `python -m ig_media_kit.mcp_server` boots; calling the registered `list_reels` end-to-end on a live handle returns the envelope; the batch runner / downloader tools are untouched.
    - Parallel-safe with: none (integration).
11. **T2.10 — Live acceptance pass (all ACs)** — Against a real public handle, in one sitting: (a) cold call → ~40 ranked reels + anchors; (b) spaced repeat calls grow `pool_depth` toward 90; (c) `fresh_fetch=false` at `depth >= 90` → zero network, ranked return; (d) `fresh_fetch=true` top-check surfaces new posts; (e) each filter + each `sort_by` correct over the pool; (f) an injected/observed stop_signal → ranked partial + "budget cooling" note, no crash; (g) total `pages_fetched <= 4` per call and no sleep anywhere.
    - Acceptance: every AC above observed live; the counter-metrics (no sleep, `<= 4` pages/call, no auth, pool never capped) hold.
    - Parallel-safe with: none.

## Test Strategy

- **Serve-from-store no-network path** — unit/integration with a no-network spy on `AnonymousClient`: `fresh_fetch=false` + `depth >= scan_depth` issues zero requests; asserts the "served from store" note.
- **Cold-start initial fill** — integration against a live handle (or a recorded fixture feed): call 1 yields ~40 reels, sets anchors + `coverage_segments`.
- **Call-driven fill monotonicity** — property/integration: over a sequence of spaced calls, `pool_depth` is non-decreasing and reaches `>= scan_depth`, with deepen skipped once deep enough.
- **Budget governor cap** — unit: across top-check + deepen, total `pages_fetched <= 4` for all phase splits; deepen gets a reserved page when the pool is shallow and top is caught up; no sleep invoked (assert the sleep callable is never called).
- **Caught-up top-check anti-regression** — integration: a caught-up handle top-check fetches exactly 1 page, adds 0 rows (T1's `pages_fetched == 1` still holds through T2's orchestration).
- **Coverage-segment gap** — unit with a simulated > one-window burst: a 2nd segment with a valid resume cursor is recorded (no reel lost); a subsequent deepen bridges and merges segments; state round-trips.
- **Filter correctness** — golden: fixed pool, each of `min_views`/`min_duration`/`max_age_days` in isolation and combined yields the exact expected subset; unset filters are no-ops; filters excluding all → empty + note.
- **Sort correctness** — golden: each `sort_by` (default `play_count` desc + the mirrored set) yields the expected order over a fixed pool; unknown `sort_by` handled per contract.
- **Top-N over pool** — unit: top-`count` computed over the full filtered pool; `count > pool` returns the whole pool; not destructively capped by `scan_depth`.
- **Partial on stop_signal** — integration with each injected stop_signal kind in top-check and in deepen: ranked partial + typed "budget cooling" note + `partial=true`, no traceback, no sleep, persisted pool intact.
- **No-download invariant** — unit: assert no fbcdn/`video_url` GET occurs anywhere in `list_reels`.

## Out of Scope

- The async **batch runner** and any sleeping/cooldown-waiting path — `list_reels` is the sync, never-sleeping surface; the batch tool (which may sleep) is a separate ticket.
- The **downloader** tool (fbcdn mp4 GET, redirect-follow) — `list_reels` returns metadata + resolvable URLs only, downloads nothing.
- **Signed-URL re-resolution** (~24-36 h TTL) — store carries `fetched_at`; re-resolve is a downloader/consumer concern, not `list_reels`.
- Changing any **T1 primitive/store contract** — T2 reuses `fetch_window`, `write_window`, the stop_signal classifier, and durable-first ordering unchanged; only additive state (`coverage_segments`) and new orchestration/ranking modules are introduced.
- The **other three MCP tools' surfaces** and multi-handle/all-channels iteration — T2 is single-handle `list_reels`.
- **Full backfill of every coverage gap in one call** — gaps are recorded and converged across calls/the future batch pass, not force-closed synchronously within one budget.
- **Product types beyond clips** (image/carousel/story) — dispatch stays a switch; only clips are ranked now.

## Risks

- **Budget starvation of deepen on an active handle** — likelihood medium, impact medium (pool never reaches `scan_depth` because top-check eats the whole budget every call). Mitigation: T2.3 reserves a minimum deepen allotment when the pool is shallow; coverage segments record forward progress so no work is lost between calls; the future batch runner can finish deep fills.
- **Coverage gaps that never converge** — likelihood low, impact medium (a persistent burst rate keeps opening new top segments faster than deepen bridges them). Mitigation: segments are explicit + resumable; deepen always works the oldest open segment; if convergence stalls it is visible in the coverage summary and handed to the batch runner (out of scope to force-close here).
- **`max_age_days` / `min_duration` field absence or unit mismatch** — likelihood medium, impact medium (filter silently wrong if `taken_at`/duration missing or in ms vs s). Mitigation: T2.0 micro-gate confirms the exact normalized fields + units before any filter is built; a missing field is added to `normalize_item` or the filter is flagged unsupported, never assumed.
- **`sort_by` injection / unknown value** — likelihood medium, impact low (mis-sort or crash). Mitigation: T2.1 validates against a whitelist; unknown → clear error or documented default, never arbitrary attribute access.
- **Accidental never-blocking violation** — likelihood low, impact high (a stray `sleep`/retry re-introduces blocking and can extend a cooldown). Mitigation: the governor passes `sleep=None`; a test asserts the sleep callable is never invoked; code review treats any sleep/poll in the `list_reels` path as a blocker.
- **Budget cap drift from T1** — likelihood low, impact medium (T2 hard-codes a page number that diverges from T1's cap). Mitigation: T2.3 sources the cap from the same constant/config T1 uses, so the two stay in lockstep.
- **Serve-from-store staleness** — likelihood low, impact low (returned `video_url`s may be past TTL when served with no network). Mitigation: `list_reels` returns metadata + `fetched_at`; URL freshness is the downloader's concern; the note can hint freshness if the pool is old (optional).
- **Pool < scan_depth on a short account** — likelihood medium, impact low (deepen hits `end_of_feed` before 90). Mitigation: `end_of_feed` marks the segment terminal; the pool is "complete" at whatever the account has; ranking works over the real pool; not treated as an error.

## Open Questions

- Exact yt-media-kit `sort_by` vocabulary + filter units to mirror (seconds for `min_duration`? days for `max_age_days`?) — blocks the T2.1 whitelist and T2.8 semantics until confirmed against yt-media-kit.
- Deepen budget reservation policy: reserve a fixed 1 page for deepen when the pool is shallow, or split the remaining budget proportionally? — blocks T2.3's exact governor rule (leaning: reserve at least 1 page for deepen when `depth < scan_depth` and top-check was cheap).
- Should `list_reels` re-rank/return even when the top-check surfaced nothing and the pool is unchanged (cheap re-rank) or short-circuit to serve-from-store semantics? (Leaning: always re-rank over the current pool — ranking is cheap and unmetered.)
- Is `count` defaulted from config `top_reels` when omitted, and does `scan_depth` accept a per-call override or stay a fixed 90? (Leaning: both merge from config per the T1 override rule; `scan_depth` overridable but defaults 90.)
- Does the coverage summary need to surface in the MCP return envelope for the user, or is it internal state only consumed by the future batch runner? (Leaning: expose a compact `complete: bool` + segment count; keep raw cursors internal.)
- Live pilot handle for T2.10 acceptance — reuse T1's designated handle or pick a busier one to exercise the multi-segment gap path?

## Handoff Notes

This artifact targets `consumer_role: implementation`. The consumer should:
- Honor the step order — T2.0 (field micro-gate) precedes any filter/sort work; T2.3 (budget governor) must exist before T2.4/T2.6 are wired so the combined page cap is enforced from the start.
- Treat "Out of Scope" as hard — no batch runner, no downloads, no T1-contract edits, no forced gap-closing within one call.
- Treat the test strategy as the minimum: the no-network serve path, the `<= 4` pages/call combined cap, the caught-up `pages_fetched == 1` preservation, the partial-on-stop_signal path, and the filter/sort goldens are all non-optional.
- Uphold the T1 invariants T2 rides on: durable-first persistence, stop-on-first-stop_signal, never-sleep in the sync path, anonymous-only, the numeric `high_water_media_id` (never compare shortcodes), and the store-never-capped rule. `scan_depth` is a fetch-effort target, not a pool cap — top-N is always computed over the full pool.
- Re-plan (own plan mode or a fresh plan-skill invocation) if discovery during build invalidates >= 2 steps — in particular if T2.0 finds `taken_at`/duration absent (which reshapes T2.8 and the filters).
