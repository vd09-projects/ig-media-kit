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
version: 2
created: 2026-07-15T14:22:41Z
updated: 2026-07-15T20:10:00Z
prior_versions:
  - _history/planner-task-v1.md
---

# Task plan: T2 — list_reels: anonymous discovery + ranking (call-driven fill)

Task-level breakdown for the first full MCP tool: `list_reels(handle, count, sort_by, min_views, min_duration, max_age_days, scan_depth=90, fresh_fetch=false)` — the interactive, fast, NEVER-BLOCKING surface that returns a channel's top-N reels ranked, filling the store call-by-call up to `scan_depth`. This is Flow A on top of the merged T1 foundation (PR #7): a serve-from-store fast path, a top-check phase (T1 `top_scan`, with a scoped pinned-prefix hardening), a deepen phase (T1 `deep_resume`), coverage-segment tracking so a gap opened by a burst of new posts is remembered, partial-on-stop_signal handling, and rank-over-the-full-pool. No downloads.

## Plan-Review Response (round 1)

- **B1 (pinned reel silently drops newer reels) — ACCEPTED.** The v1 plan leaned on "first-seen == caught up," which the T1.2 natgeo live probe already disproved (`pks_descending == false`; pins sit above newer reels). A pin at feed position 0 hard-stops the scan and the watermark then hides the genuinely-newer un-seen reels below it forever. This is silent data loss in the tool's primary discovery path, not a cosmetic issue. Fixed via a new explicit step **T2.4a** (skip-not-stop across the pinned prefix) with its own AC and anti-regression guard.
- **B2 (collision with "don't touch frozen top_scan") — RESOLVED, branch (b), sub-choice (b-i).** Verified against merged source: `fetch._consume_page` in TOP_SCAN mode `return StopKind.CAUGHT_UP` on the **first** seen/watermarked item — it HARD-STOPS mid-page (branch b, not a). A lever analysis confirms B1 **cannot** be fixed purely T2-side: the only T2 inputs to `fetch_window` are `seen` and `high_water_media_id`, and a pin's LOW media_id trips the SECONDARY numeric watermark even if its shortcode is withheld from `seen`; suppressing that requires `high_water=None` + `seen=∅`, which destroys the caught-up 1-page short-circuit and burns the full 4-page budget every call. Therefore T2 **promotes a scoped, minimal change to `fetch._consume_page`** (the exact fix the T1 author reserved in the in-code TODO) out of "Out of Scope" — bounded, with the caught-up==1-page anti-regression kept green and a new pinned-prefix test. The plan's "must NOT modify T1's frozen top_scan" constraint is explicitly amended to carve out this one bounded change; every other T1 contract stays frozen. Rationale for (b-i) over (b-ii): shipping `list_reels` — the discovery deliverable — with "newer reels below a pin vanish forever" is a correctness hole in the very thing T2 exists to build; deferring it as debt is not acceptable for the primary path.
- **B3 (high_water bumped to positionally-top item moves watermark backward) — ACCEPTED.** Made explicit: `high_water_media_id` advances to the **MAX NUMERIC** `media_id` among newly-persisted items, never the positionally-top one. Confirmed T1's `store.write_window` already does exactly `state.high_water_media_id = max(existing, max(persisted_media_ids))`; T2 **relies on that** and must not compute its own positional bump. Added to T2.4 acceptance.
- **B4 (pool_depth conflates count with contiguity → frozen invisible gap) — ACCEPTED; recommend CONTIGUITY.** Split into two fields: `pool_depth` (raw count, an effort metric) and `coverage_contiguous` (a single joined segment reaching `scan_depth` or terminal). The serve-from-store zero-network gate keys on **contiguity**, not count — so a `count>=90`-with-gap pool does NOT freeze; it keeps deepening to bridge the gap. The envelope's `complete` surfaces contiguity, not raw count. When a gap exists, the coverage note records the incomplete-coverage debt visibly.
- **Non-blocking suggestions:** folded in — x-ig-app-id-only-via-`AnonymousClient` AC (T2.9), contract-visible open questions frozen before T2.1 publishes the envelope, stop_signal-in-top-check aborts the whole call with the reserved deepen page unspent (T2.7), T2.5 gap predicate restated in numeric-media_id terms with an explicit "a top pin cannot open a phantom segment" confirmation, and the incomplete-coverage debt recorded in the coverage note.

## Problem

T1 shipped the shared fetch primitive, the durable-first CSV+YAML store, and a `run_window()` sync path, but `mcp_server.list_reels` is only a T1 skeleton that runs a single `top_scan` window. T2 must deliver the real tool: on a new handle, call 1 returns ~40 top reels and anchors the store; repeated spaced calls fill the pool toward `scan_depth=90`; once coverage is deep AND contiguous, calls become a cheap top-refresh (or, with `fresh_fetch=false`, no network at all). Every call must apply the filters + sort over the full accumulated pool and return the top `count` — while never sleeping, never exceeding the metered page budget, and always degrading to a clean partial with a note if IG throttles mid-call. Because the owner feed is NOT strictly newest-first (pinned reels float above newer ones), discovery correctness rests on per-shortcode dedupe and numeric media_id ordering, never on positional order.

## Constraints

- **Blast radius:** single MCP tool + additive store-state fields (`coverage_segments`) + **one scoped, bounded change to `fetch._consume_page`'s TOP_SCAN pinned-prefix handling** (see T2.4a; the exact fix the T1 in-code TODO reserved). Every OTHER T1 contract stays frozen: the page cap, the stop_signal classifier, the deep_resume path, and the durable-first write ordering must NOT change.
- **The one carve-out is bounded and guarded:** the T2.4a change may only make TOP_SCAN skip a small bounded prefix of leading already-seen/pinned items before applying the caught-up stop; it must NOT weaken the short-circuit (the caught-up==1-page anti-regression test stays green) and must NOT touch deep_resume.
- **ANONYMOUS ONLY** — no login/cookies/session/account, ever. No new request path; every IG call goes through T1's `AnonymousClient` (which is the sole place `x-ig-app-id` is set).
- **Politeness is load-bearing** — one `list_reels` call spends AT MOST the ~4-page / ~40-item metered budget across BOTH phases combined; paces pages ~1-2s (via the T1 primitive); STOPS + returns partial on the first stop_signal; NEVER sleeps (`sleep=None`); NEVER polls during a cooldown.
- **Metadata metered, CDN not** — `list_reels` touches only the metered metadata API; returns metadata + resolvable URLs, performs NO downloads.
- **Single dev, small scale.**
- **Mirror yt-media-kit** — filter names/semantics and `sort_by` field set.

## Success Metric

- **Primary:** cold store, call 1 returns ~40 ranked reels (default `play_count` desc), writes CSV + state with `high_water_media_id` (= max numeric media_id persisted), `deep_cursor`, and a single `coverage_segments` entry; spaced repeat calls grow the pool monotonically toward `scan_depth=90`; once coverage is **contiguous AND deep** (`coverage_contiguous == true` and the covered span reaches `scan_depth` or is terminal), `fresh_fetch=false` returns the ranked top-`count` with ZERO network; `fresh_fetch=true` (or a not-yet-contiguous/not-yet-deep pool) runs a top-check that surfaces genuinely new reels — including reels sitting BELOW a pinned prefix; all filters + every `sort_by` correct over the FULL pool. Verified live, including on a handle that pins reels.
- **Counter-metric (must not regress):** NEVER sleeps; NEVER exceeds the ~4-page budget/call (top-check + deepen combined); zero authenticated requests on any path; a stop_signal mid-call yields a valid partial (persisted pool, ranked, "budget cooling" note + typed reason, no traceback, cursor/anchor not advanced past unpersisted rows); the store is NEVER destructively capped; **a caught-up top-check still fetches exactly 1 page and adds 0 rows even after the T2.4a pinned-prefix change** (T1's `pages_fetched == 1` anti-regression holds); `high_water_media_id` is monotonic non-decreasing (never bumped backward by a low-pk pin).
- **Evaluation window:** one live cold-start run + a spaced repeat-call sequence to depth + one serve-from-store run + one pinned-account top-check run + one injected-stop_signal run, within the build session.
- **Evaluator:** sindri's verify step / build-session review, against a real handle (at least one that pins reels).

## Mode

- Modification (fleshes out the T1 `list_reels` skeleton; adds one orchestration module + one ranking module + additive state fields; plus one bounded, guarded change to `fetch._consume_page`).

## Existing Code Shape

From T1 (PR #7):
- **`config.py`** — loader; `top_reels` filter defaults + per-call override merge.
- **`http_client.py`** — `AnonymousClient` with the stop_signal classifier (`ok | stop(reason) | error`) and `assert_anonymous` guard. **Sole owner of the `x-ig-app-id: 936619743392459` header.** Used only through T1; T2 adds no new request path.
- **`fetch.py`** — `resolve_user_id`, `normalize_item` (clips dispatch capturing `shortcode`, `media_id`/`pk`, `play_count`, `ig_play_count`, `like_count`, `comment_count`, `caption`, `taken_at`, `duration` (from `video_duration`), `video_url`, `fetched_at`), `fetch_window(mode=top_scan | deep_resume)` capped `<=4 pages/call`, stops on first stop_signal, sleeps only if a `sleep` callable is supplied. **Observed:** `_consume_page` in TOP_SCAN mode HARD-STOPS on the first seen-shortcode or first `media_id <= high_water_media_id` item (the behavior T2.4a hardens). It carries an in-code TODO reserving exactly the pinned-prefix fix.
- **`store.py`** — CSV manifest + YAML state per handle; per-shortcode skip-seen dedupe; `high_water_media_id`, `seen` (derived from CSV), `deep_cursor`, `last_stop_reason`; durable-first `write_window`. **Confirmed:** `write_window` advances `high_water_media_id = max(existing, max(persisted_media_ids))` — the max-numeric behavior B3 requires; deep_cursor is seeded once by top_scan (when absent) and always advanced by deep_resume.
- **`window.py`** — `run_window()` sync path (`sleep=None`) composing fetch → normalize → store for one `top_scan` window.
- **`mcp_server.py`** — `list_reels` wired to `run_window` as the T1 skeleton (replaced in T2.9).

What changes: `list_reels` grows into the full two-phase, budget-governed, rank-returning Flow A. `fetch._consume_page` gets the bounded pinned-prefix skip (T2.4a). State gains `coverage_segments`. New `list_reels` orchestration module and `ranking.py` are added.

## Integration Points

- **T1 `fetch_window`** — reused for both phases; the ONLY internal change is the T2.4a pinned-prefix skip in `_consume_page` (TOP_SCAN only). deep_resume, page cap, stop conditions, and output contract are unchanged.
- **T1 `store.write_window` + state** — reused for durable-first persistence and the max-numeric `high_water_media_id` advance (B3); T2 extends state with `coverage_segments` (additive; existing fields untouched).
- **T1 `run_window`** — T2 either calls it per phase or inlines its compose; either way passes `sleep=None`.
- **`config.py` filter defaults** — `list_reels` args override config `top_reels` defaults via the T1 merge rule.
- **FastMCP tool registration** — `list_reels` is the public MCP surface; its return envelope is the tool's published output shape.
- **yt-media-kit** — reference for `sort_by` field names, filter units, and result-record columns.

## Steps

1. **T2.0 — Confirm rank/filter input fields exist (verify-by-pilot micro-gate).** Before building filters/sort, confirm from a live feed item (or T1's recorded probe output) that each needed field is present and typed: `play_count` (int, may be 0/null for non-clips — already filtered out), `duration` (seconds, from `video_duration`) for `min_duration`, and `taken_at` (epoch) for `max_age_days`. Confirm the yt-media-kit `sort_by` vocabulary to mirror.
   - Acceptance: a written list of the exact normalized field names backing each of `min_views`, `min_duration`, `max_age_days`, and each `sort_by` value; any missing field is either added to `normalize_item` (T2.8) or flagged as unsupported with a documented fallback. No filter/sort is built on an assumed field.
   - Parallel-safe with: none (gates T2.8).

2. **T2.1 — Define the `list_reels` call contract + result envelope (FREEZE the contract-visible decisions here).** Specify the full signature and the return envelope: the ranked top-`count` reel records, a `partial` flag, a human-readable `note`, `pool_depth` (raw stored-clip count), `coverage` (see below), and `pages_fetched`. **The following previously-open, contract-visible questions are now FROZEN before the envelope is published** (they shape the public surface, so they cannot stay open):
   - `count` and `scan_depth` both merge from config `top_reels` defaults per the T1 override rule; `scan_depth` is overridable but defaults 90; `count` may exceed the pool (returns whatever the pool yields).
   - `fresh_fetch=true` semantics: bypass the serve-from-store gate and run a top-check (and deepen if the pool is not yet contiguous-to-depth and budget remains); `fresh_fetch=false` is the serve-from-store-eligible path.
   - Coverage exposure: the envelope surfaces a compact `coverage` object = `{ complete: bool, segments: int, pool_depth: int }`, where **`complete` == `coverage_contiguous`** (a single segment reaching `scan_depth` or terminal — NOT raw count). Raw resume cursors stay internal.
   - Acceptance: a documented envelope schema; unknown/invalid `sort_by` rejected with a clear error (or defaulted per T2.8's whitelist) rather than silently mis-sorting; `count`/`scan_depth`/filter args validated (non-negative); `complete` is defined as contiguity, never as `pool_depth >= scan_depth`.
   - Parallel-safe with: T2.0.

3. **T2.2 — Serve-from-store fast path + coverage accounting (gate on CONTIGUITY, per B4).** Load state + pool. Define `pool_depth` = count of stored clips (effort metric) and `coverage_contiguous` = (`len(coverage_segments) == 1` AND that single segment reaches `scan_depth` OR is terminal at `end_of_feed`). Gate: if `fresh_fetch == false` AND `coverage_contiguous`, skip ALL network — go straight to rank (T2.8) and return with a "served from store" note. **A pool with `pool_depth >= scan_depth` but a gap (>1 segment) does NOT serve-from-store** — it proceeds to the phases so deepen can bridge the gap; the note flags "incomplete coverage: N segments — converging."
   - Acceptance: with a pre-populated single-segment store contiguous to `scan_depth` and `fresh_fetch=false`, `list_reels` issues ZERO metadata requests (assert via a no-network spy on `AnonymousClient`) and returns the ranked top-`count`; with a 2-segment store of `count>=scan_depth`, the gate does NOT fire (network path taken to converge); `fresh_fetch=true` always bypasses the gate.
   - Parallel-safe with: T2.1.

4. **T2.3 — Single-call page-budget governor.** A per-call budget object (default ~4 pages ≈ ~40 items, sourced from the SAME T1 constant/config as `fetch`'s page cap so the two never drift) shared across BOTH phases. Top-check draws first; deepen draws from what remains. Reserve a minimum deepen allotment (≥1 page for deepen when the pool is not yet contiguous-to-`scan_depth` and top-check did not itself exhaust the budget) so a busy handle's top-check cannot starve backfill forever — but the combined draw NEVER exceeds the ~4-page cap. The governor passes `sleep=None` and stops feeding phases the moment a stop_signal returns or the budget hits zero.
   - Acceptance: across a call, total `pages_fetched` (top-check + deepen) `<= 4`; on a not-yet-contiguous pool with a caught-up top (top-check spends 1 page) the remaining budget flows to deepen; the governor never invokes a sleep; a stop_signal in phase 1 means phase 2 is not started (the reserved deepen page is NOT spent).
   - Parallel-safe with: none (governs T2.4 + T2.6).

5. **T2.4 — Top-check phase (wire T1 `top_scan`).** Run `fetch_window(mode=top_scan)` from the newest item with the real `seen` set + `high_water_media_id` (preserving the caught-up short-circuit), merging genuinely-new reels into the pool durable-first. **`high_water_media_id` advances via T1's `store.write_window` to the MAX NUMERIC `media_id` among newly-persisted items — never the positionally-top item (B3).** On a COLD handle (no anchors) this is the initial fill that walks the budget once and returns ~40 top reels + anchors the store. On a caught-up handle it fetches 1 page and adds zero rows.
   - Acceptance: cold handle → ~40 reels persisted, `high_water_media_id` (= max numeric media_id) + `deep_cursor` set; caught-up handle → 1 page, zero new rows, `caught_up`; handle with N (< one window) new posts → exactly those N merged, no duplicates; **`high_water_media_id` after any merge equals `max(prior, max(persisted media_ids))` and never decreases even when a low-pk pinned reel is present in the page.**
   - Parallel-safe with: none (runs before T2.6 within a call; depends on T2.4a landing first).

6. **T2.4a — Pinned-prefix hardening of `fetch._consume_page` (SCOPED T1 carve-out, resolves B1/B2 branch b-i).** Change TOP_SCAN's stop logic so it **skips-not-stops** across a bounded leading prefix of already-seen/pinned items (bound = IG's observed pin cap, ~3, sourced as a small named constant), collects every un-seen clip on the page, and treats **"the page yielded zero new un-seen clips"** as the caught-up signal instead of "the first seen item was encountered." The numeric watermark is used only to BOUND paging (stop a page that yields nothing new), not to hard-stop mid-page. This is the exact fix the T1 in-code TODO reserved; it must NOT weaken the short-circuit and must NOT touch `deep_resume`.
   - Acceptance: (a) a page whose first ~3 items are pinned/already-seen but which contains newer un-seen clips below the pins → those un-seen clips ARE collected (no silent drop); (b) **anti-regression: a genuinely caught-up handle (all page-1 items already-seen, pins included) still fetches exactly 1 page and adds 0 rows — `pages_fetched == 1`, `stop_reason == caught_up`** (the T1 test stays green); (c) `deep_resume` behavior is byte-for-byte unchanged; (d) correctness rests on per-shortcode dedupe, not positional order. This step is reviewed on its own for the budget-burn regression risk the caught-up==1-page invariant guards.
   - Parallel-safe with: none (must land before T2.4 is exercised; if the reviewer prefers, it may be split to a standalone T1.x pre-req ticket, but it is a HARD dependency of T2.10 acceptance — it cannot be deferred as debt).

7. **T2.5 — Coverage-segment tracking (gap predicate in numeric media_id terms).** Extend state with `coverage_segments`: an ordered list of contiguous `[newest_media_id, oldest_media_id, resume_cursor]` spans the store has actually covered. Normally ONE segment (top → deep_cursor). A NEW segment is opened ONLY when top-check returns `stop_reason == page_cap` (walked the full budget without catching up) AND the **MIN numeric `media_id` among the newly-collected items is still strictly greater than the prior segment's `newest_media_id` (the old `high_water`)** — i.e., an entire budget-window of genuinely-newer posts appeared and a numeric gap provably remains between the fresh span's oldest and the prior span's newest. Deepen (T2.6) always works the OLDEST not-yet-joined segment's cursor so segments converge. Merge adjacent segments when a deepen pass bridges them (the worked segment's `oldest_media_id` crosses at/below the next segment's `newest_media_id`).
   - **A top pin CANNOT open a phantom segment:** pins are already-seen, so they are skipped by dedupe and never enter the newly-collected set; and even if a pin were un-seen (cold first fetch), its LOW `media_id` would lower the batch MIN and make the `batch_min > prior_newest` predicate FALSE — so a pin can never satisfy the gap predicate.
   - Acceptance: a simulated burst of > one window of genuinely-newer posts opens a 2nd segment with a valid resume cursor (no reel silently skipped); a later deepen pass that reaches the older segment merges the two into one; steady state stays single-segment; a page led by pins does NOT open a segment; round-trips through state.yaml (additive; T1 fields untouched).
   - Parallel-safe with: none (feeds T2.6).

8. **T2.6 — Deepen phase (wire T1 `deep_resume`).** If coverage is not yet contiguous-to-`scan_depth` (i.e. `pool_depth < scan_depth` on the working segment OR >1 segment with a bridgeable gap) AND there is more to fetch (the oldest open segment's `resume_cursor` / `more_available` is set) AND budget remains, run `fetch_window(mode=deep_resume)` from that segment's cursor, paging OLDER, persisting durable-first, until coverage reaches `scan_depth` OR the shared budget is spent OR `end_of_feed`. Advances the worked segment's `oldest`/`resume_cursor`; leaves `high_water_media_id` untouched (deepen never moves the top anchor). Bridges/merges segments per T2.5 when it reaches the next span.
   - Acceptance: on a store not yet contiguous-to-depth with budget remaining after top-check, deepen adds older reels and advances the segment cursor; repeated spaced calls drive coverage monotonically to contiguous-and-deep; once contiguous-to-`scan_depth`, deepen is skipped (no network from this phase); `end_of_feed` on a short account stops cleanly and marks the segment terminal (coverage is "complete" at the account's real depth).
   - Parallel-safe with: none.

9. **T2.7 — Partial-result handling on stop_signal.** In EITHER phase, on the first stop_signal (per T1's classifier), stop immediately: the T1 primitive already returns the partial + typed reason and the store already persisted rows durable-first. T2 catches this, does NOT start/continue further phases, does NOT sleep or poll, sets `partial=true`, and returns the ranked top-`count` over whatever pool exists WITH a clear note (e.g. "budget cooling — IG rate limit hit; returned N of the stored pool; retry after a few minutes") carrying the typed reason. **A stop_signal during top-check aborts the WHOLE call — the reserved deepen page is NOT spent** (no further metered request after a stop). No exception escapes to the MCP client.
   - Acceptance: injecting each stop_signal kind mid-top-check and mid-deepen yields a ranked partial + note + `partial=true`, zero traceback, zero sleep, the persisted pool intact (cursor/anchor not advanced past unpersisted rows); after a top-check stop, `pages_fetched` reflects no deepen page was issued.
   - Parallel-safe with: none.

10. **T2.8 — Filter + rank over the full pool.** A `ranking` module: load the FULL stored pool (not just this call's fetch), apply `min_views` (`play_count >=`), `min_duration` (`duration` secs `>=`), `max_age_days` (`now - taken_at <= days`) — skipping filters left unset — then sort by `sort_by` from a validated whitelist (default `play_count` desc; others mirror yt-media-kit, e.g. recency/`taken_at`, `duration`), then take the top `count`. NO downloads. Top-N is computed over the pool AFTER filtering; `count > pool` returns whatever remains. Filters that exclude everything return an empty list with a note, not an error.
    - Acceptance: golden checks — a fixed pool + each filter in isolation + each `sort_by` produces the expected ordered subset; default sort is `play_count` desc; an unknown `sort_by` is rejected/defaulted per T2.1; `count` larger than the filtered pool returns the whole filtered pool; no `video_url` is fetched/downloaded during ranking.
    - Parallel-safe with: T2.2 (both are store-read/rank concerns), after T2.0.

11. **T2.9 — Wire the full flow into `mcp_server.list_reels`.** Replace the T1 skeleton: compose T2.2 (serve-from-store contiguity gate) → T2.3 governor over T2.4 (top-check) → T2.5 (coverage) → T2.6 (deepen) → T2.7 (partial guard) → T2.8 (rank) → return the T2.1 envelope. Ensure `list_reels` remains the only MCP entry point touched.
    - Acceptance: `python -m ig_media_kit.mcp_server` boots; calling the registered `list_reels` end-to-end on a live handle returns the envelope; the batch runner / downloader tools are untouched; **the `x-ig-app-id` header is set ONLY inside `AnonymousClient` — T2 adds no request path that sets headers directly (assert no header-setting outside the T1 client).**
    - Parallel-safe with: none (integration).

12. **T2.10 — Live acceptance pass (all ACs).** Against a real public handle (at least one that PINS reels), in one sitting: (a) cold call → ~40 ranked reels + anchors; (b) spaced repeat calls grow coverage to contiguous-and-deep toward 90; (c) `fresh_fetch=false` at contiguous-to-depth → zero network, ranked return; (d) `fresh_fetch=true` top-check surfaces new posts **including reels below a pinned prefix** (T2.4a proven live); (e) each filter + each `sort_by` correct over the pool; (f) an injected/observed stop_signal → ranked partial + "budget cooling" note, no crash, no deepen page after the stop; (g) total `pages_fetched <= 4` per call and no sleep anywhere; (h) `high_water_media_id` never moves backward across the run.
    - Acceptance: every AC above observed live; the counter-metrics (no sleep, `<= 4` pages/call, no auth, pool never capped, monotonic `high_water`) hold; the caught-up==1-page anti-regression still passes after T2.4a.
    - Parallel-safe with: none.

## Test Strategy

- **Serve-from-store no-network path** — no-network spy on `AnonymousClient`: `fresh_fetch=false` + `coverage_contiguous` issues zero requests; a `count>=scan_depth`-with-gap pool does NOT serve-from-store (network taken to converge); asserts the "served from store" vs "incomplete coverage" notes.
- **Pinned-prefix hardening (T2.4a)** — unit: a fixture page with ~3 leading pinned/seen items and newer un-seen clips below → all un-seen clips collected; and the anti-regression: a fully caught-up page (pins included) still returns `pages_fetched == 1`, 0 rows, `caught_up`. `deep_resume` unaffected (byte-for-byte behavior).
- **high_water monotonicity (B3)** — unit: after merging a page containing a low-pk pin plus newer reels, `high_water_media_id == max(prior, max(persisted media_ids))` and never decreases.
- **Cold-start initial fill** — integration (live handle or recorded fixture): call 1 yields ~40 reels, sets anchors + single `coverage_segments`.
- **Call-driven fill monotonicity** — property/integration: over spaced calls, `pool_depth` is non-decreasing and coverage becomes contiguous-and-deep; deepen skipped once contiguous-to-depth.
- **Budget governor cap** — unit: across top-check + deepen, total `pages_fetched <= 4` for all phase splits; deepen gets a reserved page when the pool is not-yet-contiguous and top is caught up; no sleep invoked; a stop in phase 1 leaves the reserved deepen page unspent.
- **Caught-up top-check anti-regression** — integration: a caught-up handle top-check fetches exactly 1 page, adds 0 rows (holds through T2.4a + T2's orchestration).
- **Coverage-segment gap (numeric predicate)** — unit with a simulated > one-window burst: a 2nd segment with a valid resume cursor is recorded (`batch_min_media_id > prior_newest`); a page led by pins does NOT open a segment (phantom-segment guard); a subsequent deepen bridges and merges; state round-trips.
- **Filter correctness** — golden: fixed pool, each filter in isolation and combined yields the exact expected subset; unset filters are no-ops; filters excluding all → empty + note.
- **Sort correctness** — golden: each `sort_by` (default `play_count` desc + the mirrored set) yields the expected order; unknown `sort_by` handled per contract.
- **Top-N over pool** — unit: top-`count` over the full filtered pool; `count > pool` returns the whole pool; not destructively capped by `scan_depth`.
- **Partial on stop_signal** — integration with each injected stop_signal kind in top-check and in deepen: ranked partial + typed "budget cooling" note + `partial=true`, no traceback, no sleep, persisted pool intact, no deepen page after a top-check stop.
- **No-download invariant** — unit: no fbcdn/`video_url` GET occurs anywhere in `list_reels`.
- **Header provenance** — unit: `x-ig-app-id` is set only within `AnonymousClient`; no T2 code path sets request headers directly.

## Out of Scope

- The async **batch runner** and any sleeping/cooldown-waiting path — `list_reels` is the sync, never-sleeping surface.
- The **downloader** tool (fbcdn mp4 GET, redirect-follow) — metadata + resolvable URLs only.
- **Signed-URL re-resolution** (~24–36 h TTL) — store carries `fetched_at`; re-resolve is a downloader/consumer concern.
- **Changing any T1 primitive/store contract OTHER than the one bounded T2.4a carve-out** — the page cap, stop_signal classifier, `deep_resume`, and durable-first ordering stay frozen; only additive state (`coverage_segments`), the T2.4a pinned-prefix skip, and new orchestration/ranking modules are introduced.
- The **other three MCP tools' surfaces** and multi-handle/all-channels iteration.
- **Full backfill of every coverage gap in one call** — gaps are recorded and converged across calls (contiguity gate keeps them from freezing), not force-closed synchronously within one budget.
- **Product types beyond clips** (image/carousel/story) — dispatch stays a switch.

## Risks

- **Pinned-prefix change reintroduces budget burn** — likelihood low, impact high (a too-loose skip turns every caught-up call into a full 4-page walk). Mitigation: T2.4a bounds the skip to IG's pin cap (~3) and keys caught-up on "page yielded zero new," keeping the `pages_fetched == 1` anti-regression green; the step is reviewed on its own for exactly this regression.
- **Budget starvation of deepen on an active handle** — likelihood medium, impact medium. Mitigation: T2.3 reserves a minimum deepen allotment; coverage segments record forward progress; the future batch runner finishes deep fills.
- **Coverage gaps that never converge** — likelihood low, impact medium. Mitigation: segments are explicit + resumable; deepen always works the oldest open segment; the contiguity gate keeps a gapped pool on the network path instead of freezing it; if convergence stalls it is visible in the coverage note + `complete=false` and handed to the batch runner.
- **`max_age_days` / `min_duration` field absence or unit mismatch** — likelihood medium, impact medium. Mitigation: T2.0 micro-gate confirms the exact normalized fields + units before any filter is built.
- **`sort_by` injection / unknown value** — likelihood medium, impact low. Mitigation: T2.1 whitelist; unknown → clear error or documented default.
- **Accidental never-blocking violation** — likelihood low, impact high. Mitigation: governor passes `sleep=None`; a test asserts the sleep callable is never invoked; review treats any sleep/poll in the `list_reels` path as a blocker.
- **Budget cap drift from T1** — likelihood low, impact medium. Mitigation: T2.3 sources the cap from the same T1 constant/config.
- **Serve-from-store staleness** — likelihood low, impact low. Mitigation: `list_reels` returns metadata + `fetched_at`; URL freshness is the downloader's concern; the note may hint freshness if the pool is old.
- **Pool < scan_depth on a short account** — likelihood medium, impact low. Mitigation: `end_of_feed` marks the segment terminal; coverage is "complete" (contiguous + terminal) at whatever the account has; not an error.

## Open Questions

*(The contract-visible questions from v1 — count/scan_depth defaults, `fresh_fetch=true` semantics, coverage exposure — are now FROZEN in T2.1 and removed from this list.)*

- Exact yt-media-kit `sort_by` vocabulary + filter units (seconds for `min_duration`? days for `max_age_days`?) — blocks the T2.1 whitelist and T2.8 semantics until confirmed against yt-media-kit (resolved in T2.0).
- Deepen budget reservation policy: reserve a fixed 1 page for deepen when the pool is not-yet-contiguous, or split the remaining budget proportionally? (Leaning: reserve at least 1 page when not-yet-contiguous-to-depth and top-check was cheap.)
- T2.4a packaging: land the pinned-prefix skip as an in-T2 step (current plan) or split it to a standalone T1.x pre-req ticket? Either way it is a HARD dependency of T2.10 — the only open part is packaging, not whether it ships.
- The pinned-prefix bound constant (~3): fixed literal, or config-surfaced? (Leaning: a small named constant in `fetch`, not user config — it tracks IG's pin cap, an implementation detail.)
- Live pilot handle for T2.10 — reuse T1's natgeo (known to pin reels, good for the T2.4a live proof) or add a busier one to also exercise the multi-segment gap path? (Leaning: natgeo for pins + one busier handle for the gap.)

## Handoff Notes

This artifact targets `consumer_role: implementation`. The consumer should:
- Honor the step order — T2.0 (field micro-gate) precedes any filter/sort work; **T2.4a (pinned-prefix skip) must land and stay green on the caught-up==1-page anti-regression before T2.4 is exercised**; T2.3 (budget governor) must exist before T2.4/T2.6 so the combined page cap is enforced from the start.
- Treat "Out of Scope" as hard — the ONLY permitted T1 change is the bounded T2.4a `_consume_page` pinned-prefix skip (TOP_SCAN only); no other T1-contract edits, no batch runner, no downloads, no forced gap-closing within one call.
- Treat the test strategy as the minimum: the no-network serve path (contiguity-gated), the `<= 4` pages/call combined cap, the caught-up `pages_fetched == 1` preservation THROUGH T2.4a, the pinned-prefix collection test, the numeric gap predicate + phantom-segment guard, the partial-on-stop_signal path, and the filter/sort goldens are all non-optional.
- Uphold the T1 invariants T2 rides on: durable-first persistence, stop-on-first-stop_signal, never-sleep in the sync path, anonymous-only (x-ig-app-id only via `AnonymousClient`), the numeric `high_water_media_id` advanced to the MAX numeric media_id (never a positional or shortcode comparison, never moved backward), and the store-never-capped rule. `scan_depth` is a fetch-effort target, not a pool cap — top-N is always computed over the full pool; `complete` means contiguity, not raw count.
- Re-plan (own plan mode or a fresh plan-skill invocation) if discovery during build invalidates ≥ 2 steps — in particular if T2.0 finds `taken_at`/duration absent (reshapes T2.8 + the filters), or if T2.4a cannot preserve the caught-up==1-page invariant (which would force B2 back to a batch-only deferral).
