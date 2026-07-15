---
artifact_type: handoff
artifact_version: 2
producer_role: planner
consumer_role: implementation
plan_type: task
slug: fetch-engine-store-foundation
scope_hint: T1 — Fetch engine + store foundation (anonymous IG reel fetcher)
canonical_name: planner-task
task_title: T1 — Fetch engine + store foundation
owner: vd
overlays: []
status: draft
version: 1
created: 2026-07-14T17:50:46Z
updated: 2026-07-14T17:50:46Z
prior_versions: []
---

# Task plan: T1 — Fetch engine + store foundation (anonymous IG reel fetcher)

Task-level breakdown for the foundational plumbing all four MCP tools depend on: an anonymous curl_cffi fetch primitive, a persistent CSV+YAML store with skip-seen dedupe and cursor resume, load-bearing politeness baked into the fetch loop, a config schema mirroring yt-media-kit, and a bootable FastMCP skeleton.

## Success Metric
- Primary: Against a real public handle, a single synchronous window call writes store/<handle>.csv with >=1 reel row with non-null real play_count and a resolvable video_url, plus store/<handle>.state.yaml carrying user_id, high_water_id, and the deep cursor. A second call adds only new rows (zero duplicate shortcodes). Verified end-to-end against a live handle before T1 is done.
- Counter-metric (must not regress): Zero authenticated requests on any path (no cookies/session/login); the synchronous path never sleeps and never issues >4 feed pages per call; a mid-fetch 401 leaves a valid partial store + saved cursor with no traceback.
- Evaluation window: one live pilot run + one resume run, within the build session. Evaluator: sindri's verify step / build-session review, against a real handle.

## Assumptions & Grounding
- IG behavior claims (endpoint shapes, play_count location, 12/page cap, cursor format, 401-under-abuse) are HYPOTHESES to confirm by probe (T1.2) before the fetch primitive is built on them.
- yt-media-kit is the ergonomics reference for config schema, filter shape, manifest columns; mirror where it translates.
- "Window" = the paced ~40-item span assembled from ~4 feed pages (12/page hard cap).

## Task Breakdown (ordered)
### T1.0 — Package scaffold & dependencies
Create ig_media_kit package (src layout), pyproject.toml pinning Python 3.12+, deps: curl_cffi, mcp[cli], PyYAML. Add store/ and media/ dir conventions (git-ignored data). Depends on: nothing. Done when: pip install -e . succeeds and import ig_media_kit works.
### T1.1 — Config schema + loader (AC5)
config.yaml mirroring yt-media-kit: channels[], top_reels filters, output dirs. Loader resolves $IG_MK_CONFIG override, parses via PyYAML, exposes a typed config object. Per-call-override-merge rule (call args shallow-merge over config defaults). Depends on: T1.0. Done when: sample config.yaml parses; channels + filters readable; a per-call override dict merges over config and wins.
### T1.2 — Probe spike (verify-by-pilot gate)
Throwaway probe (not shipped) hitting web_profile_info and /api/v1/feed/user/{id}/ anonymously with x-ig-app-id: 936619743392459 + impersonate="chrome". Confirm: user_id resolution, feed items carry play_count/ig_play_count, product_type=="clips" for reels, 12/page cap, next_max_id = {media_id}_{user_id} cursor shape. Note observed 401/cooldown behavior. Depends on: T1.0. Gates: T1.4, T1.5. Done when: each relied-upon field/behavior observed live or flagged as changed.
### T1.3 — HTTP client wrapper
Thin wrapper over curl_cffi with impersonate="chrome", mandatory x-ig-app-id header on every API call, anonymous-only enforcement (no cookie jar, no auth params — a code-level guard). Distinguish metadata calls (instagram.com, metered) from CDN (fbcdn.net, unmetered, redirect-follow) — carry redirect-follow capability though CDN download is out of T1 scope. Depends on: T1.0; informed by T1.2. Done when: wrapper issues an anonymous GET with the required header and exposes 401 status distinctly.
### T1.4 — Fetch primitive: handle -> user_id
Resolve a public handle to user_id via web_profile_info. Cache resolved user_id into state (avoid re-resolving on resume). Depends on: T1.3, T1.2. Feeds: T1.5, AC2. Done when: a public handle yields a stable user_id.
### T1.5 — Fetch primitive: paced feed pagination + normalization + politeness (AC1 fetch half, AC4)
Paginate /api/v1/feed/user/{id}/ via max_id, cap ~4 pages/call, pace pages ~1-2s. Normalize each item to a reel record (shortcode, play_count, video_url from video_versions[0].url, fetched_at, filtering to product_type=="clips"). Politeness (load-bearing): stop and return a partial on the FIRST 401; NEVER sleep in this synchronous path; never poll during cooldown. Emit deep cursor (next_max_id) and newest-seen id. Depends on: T1.4. Done when: a call returns a normalized list + cursor + partial flag; injecting a 401 mid-run yields a clean partial with cursor intact and no sleep.
### T1.6 — Store layer: CSV manifest + YAML state, skip-seen, resume (AC2, AC3)
CSV manifest writer (store/<handle>.csv, token-lean columns mirroring yt-media-kit; TSV fallback if captions carry commas). YAML state (store/<handle>.state.yaml) holding user_id, high_water_id, deep cursor. Per-shortcode skip-seen dedupe and cursor resume (resume from saved next_max_id, don't re-page). Never destructively cap — append/accumulate toward scan_depth; top-N computed over the pool later. Depends on: T1.0. Consumes: T1.5 output. Done when: a normalized batch writes rows + state; a second write with overlapping shortcodes adds zero duplicates; state round-trips.
### T1.7 — Synchronous window call (wire fetch -> normalize -> store) (AC1 end-to-end, AC3, AC4)
Compose T1.5 + T1.6 into one synchronous "fetch a window for handle H" entry point: load state -> resume cursor -> paced fetch -> normalize -> dedupe -> append CSV -> update state. The primitive list_reels and the batch runner both call this (batch adds sleeping later — out of T1 scope). Depends on: T1.5, T1.6. Done when: one call on a live handle produces CSV + state; re-run resumes and dedupes; a 401 mid-window still persists a valid partial + cursor.
### T1.8 — FastMCP server skeleton (AC6)
ig_media_kit/mcp_server.py with a FastMCP instance and a __main__ entry so python -m ig_media_kit.mcp_server boots. Register a thin list_reels stub calling T1.7 for one handle to prove wiring — the other three tools are later tickets. Depends on: T1.1, T1.7. Done when: python -m ig_media_kit.mcp_server boots the server and the skeleton tool is registered.
### T1.9 — Live acceptance pass (all ACs)
Run the full flow against a real public handle: confirm AC1-AC6 in one sitting. Depends on: all above. Done when: every acceptance criterion is observed live.

## Dependency Summary
T1.0 -> T1.1, T1.2 (probe gate), T1.6. T1.2 gates T1.3 -> T1.4 -> T1.5. T1.5 + T1.6 -> T1.7 -> T1.8 -> T1.9. T1.1 and T1.6 can proceed in parallel with the probe.

## Out of Scope (T1)
- The three other MCP tools' full surfaces (list_reels full, batch runner, download) — T1 ships the shared primitive + a proof-of-wiring stub.
- mp4 downloading from fbcdn (wrapper carries redirect-follow, but the downloader tool is later).
- The async batch runner and any sleeping/cooldown-waiting path (T1's sync path never sleeps).
- Top-N ranking / filter application over the pool.
- Signed-URL re-resolution logic (store fetched_at now; ~24h re-resolve is a consumer concern).

## Risks
- IG surface drift (endpoints, fields, cursor shape rotate). Mitigation: T1.2 probe gate; re-run rune per CLAUDE.md triggers if behavior changed.
- Cooldown escalation under abuse during development. Mitigation: probe sparingly, respect page cap even in T1.2, never poll during cooldown.
- Signed-URL expiry mid-test (~36h TTL) could make a stored video_url look broken — not a fetch bug. Mitigation: store fetched_at; judge freshness by it.
- CSV comma collision in captions. Mitigation: TSV fallback rule in T1.6 / proper quoting.

## Open Questions
- Exact yt-media-kit manifest column set to mirror — confirm before finalizing the CSV header in T1.6.
- Does T1 enforce stop-at-scan_depth=90 across calls, or only leave the cursor so a later call continues? (Leaning: T1 leaves the cursor; accumulation cap is a call-driven-fill concern.)
- Is a single live pilot handle designated for acceptance, or does the tester pick one?

## Handoff Notes
- Build order is the dependency graph; T1.2 is a hard gate — do not implement T1.4/T1.5 against assumed field shapes.
- The politeness rules in T1.5 are invariants, not preferences: first-401-stop, no-sleep-in-sync, <=4 pages/call, no cooldown polling. A reviewer treats any violation as a blocker.
- Enforce anonymous-only at the client layer (T1.3) as code, so no later tool can accidentally introduce auth.
- product_type dispatch should be a switch even now (clips-only today) so image/carousel/story slot in later without a rewrite.
