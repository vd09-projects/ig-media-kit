---
artifact_type: handoff
artifact_version: 2
producer_role: implementation
consumer_role: review
plan_type: build
slug: fetch-engine-store-foundation
scope_hint: T1 — Fetch engine + store foundation
canonical_name: implementation-build
overlays: []
status: draft
version: 1
created: 2026-07-15T07:44:06Z
updated: 2026-07-15T07:44:06Z
prior_versions: []
---

# Build summary: T1 — Fetch engine + store foundation (anonymous IG reel fetcher)

Built the full T1 foundation per approved `planner-task.md` v3: a `src`-layout
`ig_media_kit` package with a config loader, an anonymous `curl_cffi` HTTP client
carrying the load-bearing `stop_signal` classifier + anonymity guard, the shared
fetch primitive (user_id resolution + paced feed pagination + normalization +
`top_scan`/`deep_resume` modes), a CSV+YAML store with skip-seen dedupe and
durable-first partial persistence, a bootable FastMCP server skeleton wiring
`list_reels` to the synchronous window path, a throwaway live probe (T1.2 gate),
and 43 offline unit tests — all passing. The T1.2 probe ran LIVE against natgeo
and confirmed every relied-upon IG shape, captured a real 401 stop_signal, and
surfaced one round-2-anticipated deviation (feed not strictly newest-first under
pinning) which is flagged transparently, not silently absorbed.

## Files created

Source (`src/ig_media_kit/`):
- `__init__.py` — package marker; `IG_APP_ID = "936619743392459"` constant.
- `config.py` — T1.1. `load_config()` parses `config.yaml`, resolves
  `$IG_MK_CONFIG`, and deep-merges per-call overrides (call args win); typed
  frozen dataclasses (`Config`/`TopReelsFilter`/`FetchSettings`/`OutputSettings`).
- `http_client.py` — T1.3. `classify_response()` (the single stop_signal
  classifier — maps 401/429→`rate_limited`, 403→`forbidden`, 302→login/challenge,
  200+challenge-body→stop; **fails closed** to `stop(unknown)`); `assert_anonymous()`
  (auth-cookie/param/header-keyed guard; benign cookies permitted); `AnonymousClient`
  (impersonate=chrome, mandatory `x-ig-app-id`, metadata calls do NOT follow 302,
  `get_cdn` follows redirects for the later downloader; injectable transport).
- `fetch.py` — T1.4+T1.5. `resolve_user_id()` (clean typed failure on stop, never
  raises); `normalize_item()` (`product_type=="clips"` dispatch switch,
  play_count/ig_play_count, `video_versions[0].url`, distinct shortcode vs numeric
  `media_id`); `fetch_window()` (≤4 pages, top_scan short-circuit on seen-membership
  / numeric watermark, deep_resume mode, emits `pages_fetched`, stops on the first
  stop_signal, sleeps ONLY if a `sleep` callable is supplied).
- `store.py` — T1.6. `Store` with CSV manifest (both `shortcode` and `media_id`,
  proper quoting), YAML state (`user_id`/`high_water_media_id`/`deep_cursor`/
  `last_stop_reason`), `seen` DERIVED from the CSV, and durable-first
  `write_window()` (CSV fsync FIRST → advance anchors for persisted items only →
  atomic temp-file + `os.replace` state write; `_after_csv_hook` test seam).
- `window.py` — T1.7. `run_window()` composes fetch (top_scan) + store; SYNC path
  passes `sleep=None` so it never sleeps; returns a valid partial on any stop.
- `mcp_server.py` — T1.8. FastMCP instance, `list_reels` wired to `run_window`,
  three registered stubs (`top_reels`/`batch_fetch`/`download_reel`), `main()` +
  `__main__` so `python -m ig_media_kit.mcp_server` boots.

Packaging / config: `pyproject.toml` (Python≥3.12, pins curl_cffi/mcp[cli]/PyYAML,
src-layout, pytest config, `ig-media-kit` entry point), `config.yaml` (sample
mirroring yt-media-kit).

Probe (throwaway, not shipped): `probe/probe_spike.py` — the T1.2 live probe plus
a durable LIVE-FINDINGS comment block.

Tests (`tests/`, offline, 43 tests): `conftest.py` (FakeTransport records what was
sent), `fixtures/feed_sample.json` (captured feed shape), `test_normalizer.py`,
`test_stop_signal.py`, `test_cursor.py`, `test_dedupe.py`, `test_config.py`,
`test_anonymity.py`.

## Tests

43 tests, all passing (`pytest -q` → `43 passed`). Coverage maps to the plan's
required surfaces:
- Normalizer — clips filter drops carousel, play_count/ig_play_count/video_url/
  fetched_at extracted, shortcode≠media_id (numeric), malformed dropped.
- Skip-seen dedupe — zero duplicates on an overlapping re-write; caption-with-comma
  survives the CSV round-trip.
- Durable-first persistence — injected fault after CSV fsync / before state write
  leaves anchor+cursor un-advanced; rows re-appear and dedupe on retry (none lost,
  none duped); no leftover `.tmp`; state only ever observed fully-written.
- Cursor/anchor round-trip — `high_water_media_id` persists numeric; deep_cursor
  round-trips; **caught-up top_scan short-circuits with `pages_fetched == 1`**
  (both via membership and via the numeric watermark) — the round-2 anti-regression
  check; new-posts case returns only the new reel and stops at the first known
  shortcode; deep_resume stops at its depth target.
- Stop-signal classifier — each of 401/403/429/302-login/302-challenge/200-login/
  200-challenge → typed stop; 200-feed → ok; unknown status → fail-closed stop; 5xx
  → error; fetch loop returns a partial (cursor intact) on 401/403/429; sync path
  never sleeps.
- Config override merge — parse, `$IG_MK_CONFIG` resolution, explicit-beats-env,
  per-call deep-merge wins while preserving siblings, missing-file error names path.
- Anonymity guard — required header sent, benign cookies allowed, auth
  cookie/param/header rejected (construction + update), a server-set `sessionid`
  is refused rather than stored, metadata calls don't auto-follow redirects.

## Quality gate

PASS. `pip install -e .` succeeds and `import ig_media_kit` works (T1.0). `pytest -q`
→ 43 passed in ~0.06s (deterministic, no network, injectable clock/transport).
`python -m ig_media_kit.mcp_server` boots with all four tools registered
(`list_reels`, `top_reels`, `batch_fetch`, `download_reel`) and no traceback. An
offline end-to-end `run_window` smoke demonstrated all ACs: run 1 (fresh handle) →
3 reels, user_id cached, `high_water_media_id` set; run 2 (no new posts) → 0 rows,
`pages_fetched == 1`, `caught_up`; run 3 (new reel above anchor) → 1 new reel, rest
deduped, anchor advanced. No debug artifacts; the one TODO is self-describing and
reported below (no minted issue number).

## Probe outcome — LIVE (T1.2 gate satisfied)

Ran `probe/probe_spike.py natgeo` against real Instagram, anonymously. CONFIRMED:
web_profile_info → `user_id` 787132; feed page → HTTP 200 with exactly 12 items
(12/page cap); reels are `product_type=="clips"` carrying `play_count`+`ig_play_count`
(e.g. `DZpQwxqimz2` play_count=4,540,702) and a real `video_versions[0].url`;
`next_max_id == "{media_id}_{user_id}"` with its left half equal to the last item's
pk; pk (numeric) and code/shortcode (opaque) are distinct fields; benign anonymous
cookies set were `csrftoken/mid/ig_did/ig_nrcb` with NO auth cookie, and the feed
worked carrying them (validates the auth-cookie-keyed anonymity definition).
STOP-SIGNAL captured LIVE: the second feed page in the same short window returned
**HTTP 401** (the metadata IP-rate-limit), exactly the `rate_limited` case the
classifier maps and the reason the sync path stops-and-returns rather than polling.
Findings recorded durably in the probe file's LIVE-FINDINGS block; the offline
fixture (`tests/fixtures/feed_sample.json`) is shaped to match.

## Deviations from plan / discovered work

1. **Feed is NOT strictly newest-first (pinned reels).** The plan's T1.2 explicitly
   asked to confirm newest-first ordering and to flag it if not. The live probe
   showed `pks_descending == false` for natgeo: sampled pks `3920.. , 3900.. , 3941..`
   — a reel created AFTER two others appears below them, i.e. natgeo pins reels
   (pinned = older/smaller-pk, shown on top). Consequence: the plan's
   "first-known == caught-up" premise weakens for pinned accounts — a subsequent
   `top_scan` can hit a pinned (already-seen) reel first and stop above genuinely
   newer reels, under-collecting them. The caught-up `pages_fetched == 1`
   short-circuit is unaffected and correct; only the "new reels below a pinned
   block" case is impacted. Per sindri discipline this build-time discovery is
   SURFACED, not silently redesigned (redesigning the stop condition risks
   reintroducing the exact budget-burn regression the caught-up==1-page invariant
   guards). Left a self-describing code marker in `fetch._consume_page` and filed
   as the top discovered followup. This is within the plan's own contingency
   ("if ordering is NOT strictly newest-first, flag it").

2. **`seen` derivation choice (Open Question resolved).** Stored as DERIVED from
   the CSV `shortcode` column on load (one source of truth), NOT duplicated in
   YAML — matching the plan's leaning. Membership is O(1) against an in-memory set.

3. **TSV fallback not needed (Open Question resolved).** Used the `csv` module with
   proper (minimal) quoting; captions with commas/newlines round-trip correctly, so
   the conditional TSV fork was dropped (single format, robust).

4. **Deep-backfill caller deferred** exactly as scoped — the primitive SUPPORTS
   `deep_resume` (tested), but no dedicated deep-walk caller ships in T1.

Scope note: this is a medium/large, multi-module greenfield foundation (config,
http, fetch, store, server) with concurrency-adjacent politeness + anonymity
invariants — recommend multi-perspective-review before merge.

Ready for review — recommend multi-perspective-review.
