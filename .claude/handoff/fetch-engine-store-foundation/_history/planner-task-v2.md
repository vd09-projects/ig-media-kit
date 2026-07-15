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
version: 2
created: 2026-07-14T17:50:46Z
updated: 2026-07-14T18:01:21Z
prior_versions:
  - planner-task-v1.md
---

# Task plan: T1 — Fetch engine + store foundation (anonymous IG reel fetcher)

Task-level breakdown for the foundational plumbing all four MCP tools depend on: an anonymous curl_cffi fetch primitive, a persistent CSV+YAML store with skip-seen dedupe and durable-first partial persistence, load-bearing politeness (a stop-signal classifier, not just literal 401) baked into the fetch loop, a config schema mirroring yt-media-kit, and a bootable FastMCP skeleton.

**Revision note (round 1 review):** This version resolves the four mandatory findings — (1) a `stop_signal` classifier replaces literal-401 detection, (2) durable-first / persist-rows-before-cursor ordering with atomic state writes, (3) a precise auth-cookie-keyed definition of ANONYMOUS, and (4) explicit top-scan vs deep-resume traversal modes with disambiguated state roles. Each is acknowledged inline where it lands, with one partial disagreement noted (finding 4, scope of what T1 ships).

## Success Metric
- Primary: Against a real public handle, a single synchronous window call writes store/<handle>.csv with >=1 reel row carrying non-null real play_count and a resolvable video_url, plus store/<handle>.state.yaml carrying user_id, high_water_id (newest shortcode seen), deep_cursor (next_max_id toward scan_depth), and last_stop_reason. A second call in **top-scan mode** adds only reels newer than high_water_id (zero duplicate shortcodes, and if new reels exist they appear). Verified end-to-end against a live handle before T1 is done.
- Counter-metric (must not regress): Zero authenticated requests on any path — no sessionid/ds_user_id/auth cookies, no login/auth params, ever (benign anonymous cookies are permitted, see T1.3). The synchronous path never sleeps and never issues >4 feed pages per call. Any throttle/block signal (the whole stop_signal family, not just 401) leaves a valid partial store + saved cursor with no traceback, and no reel that was fetched-and-shown is ever lost to a cursor that advanced past unpersisted rows.
- Evaluation window: one live pilot run + one resume run, within the build session. Evaluator: sindri's verify step / build-session review, against a real handle.

## Assumptions & Grounding
- IG behavior claims (endpoint shapes, play_count location, 12/page cap, cursor format, **the full set of throttle/block responses**, **whether anonymous cookies are required**) are HYPOTHESES to confirm by probe (T1.2) before the fetch primitive is built on them.
- yt-media-kit is the ergonomics reference for config schema, filter shape, manifest columns; mirror where it translates.
- "Window" = the paced ~40-item span assembled from ~4 feed pages (12/page hard cap).
- "Stop signal" = any response indicating IG is throttling, blocking, or challenging us — HTTP 401/403/429, a 302/redirect to a login/challenge URL, or a 200 whose body is a challenge/checkpoint/login-required JSON rather than feed items. Enumerated concretely by T1.2, classified by the T1.3 contract.

## Task Breakdown (ordered)
### T1.0 — Package scaffold & dependencies
Create ig_media_kit package (src layout), pyproject.toml pinning Python 3.12+, deps: curl_cffi, mcp[cli], PyYAML. Add store/ and media/ dir conventions (git-ignored data). Depends on: nothing. Done when: pip install -e . succeeds and import ig_media_kit works.
### T1.1 — Config schema + loader (AC5)
config.yaml mirroring yt-media-kit: channels[], top_reels filters, output dirs. Loader resolves $IG_MK_CONFIG override, parses via PyYAML, exposes a typed config object. Per-call-override-merge rule (call args shallow-merge over config defaults). Depends on: T1.0. Done when: sample config.yaml parses; channels + filters readable; a per-call override dict merges over config and wins.
### T1.2 — Probe spike (verify-by-pilot gate)
Throwaway probe (not shipped) hitting web_profile_info and /api/v1/feed/user/{id}/ anonymously with x-ig-app-id: 936619743392459 + impersonate="chrome". Confirm: user_id resolution, feed items carry play_count/ig_play_count, product_type=="clips" for reels, 12/page cap, next_max_id = {media_id}_{user_id} cursor shape.
**(Finding 1 — enumerate the stop-signal family.)** Deliberately observe and record the concrete responses IG returns under throttle/block/challenge: exact status codes (401/403/429/302), redirect targets (login/challenge URLs), and any 200-with-challenge-body shapes (e.g. `require_login`, `checkpoint_required`, `spam` JSON). This enumeration is the input to the T1.3 `stop_signal` classifier — the probe is the only place we learn the real family before coding it.
**(Finding 3 — observe cookie requirement.)** Record which cookies IG sets on the first anonymous hit (e.g. mid/csrftoken/ig_did) and test whether the feed call succeeds with those benign cookies carried vs. with all cookies stripped. Decide empirically whether anonymous cookies must be retained; feed the result into T1.3's cookie policy.
Depends on: T1.0. Gates: T1.3, T1.4, T1.5. Done when: each relied-upon field/behavior observed live or flagged as changed, the stop-signal family is enumerated, and the anonymous-cookie requirement is settled.
### T1.3 — HTTP client wrapper + stop_signal classifier + anonymity guard
Thin wrapper over curl_cffi with impersonate="chrome" and the mandatory x-ig-app-id header on every API call. Three load-bearing contracts:
- **(Finding 1) `stop_signal` classifier — the load-bearing contract.** A single function classifies every response into `ok` | `stop(reason)` | `error`, where `stop` covers the whole throttled/blocked/challenged family enumerated by T1.2 (401/403/429, 302-to-login/challenge, 200+challenge-JSON), not just literal 401. The classifier returns a typed reason (e.g. `rate_limited`, `login_redirect`, `challenge`, `forbidden`). Every IG-hitting caller branches on this classifier, never on a bare status-code check. This replaces the v1 "expose 401 distinctly" contract.
- **(Finding 3) ANONYMOUS defined precisely — key off auth cookies, not all cookies.** Anonymous = no `sessionid`/`ds_user_id`/authenticated-session cookies and no login/auth params on any request; benign anonymous cookies (mid/csrftoken/ig_did) ARE permitted and, per T1.2, may be required for the fetch to work. The code-level guard asserts the *absence of auth cookies/params*, not the absence of a cookie jar. If T1.2 shows anonymous cookies are needed, the wrapper keeps a per-process cookie jar that is asserted to never contain auth cookies. This corrects the v1 "no cookie jar" phrasing, which conflated "no authenticated session" with "no cookies at all."
- Distinguish metadata calls (instagram.com, metered) from CDN (fbcdn.net, unmetered, redirect-follow) — carry redirect-follow capability for CDN though CDN download is out of T1 scope. Note: metadata calls must NOT blindly follow a 302, because a login/challenge redirect is a stop_signal — the classifier inspects the redirect target rather than transparently following it.
Depends on: T1.0; gated by T1.2. Done when: the wrapper issues an anonymous GET with the required header; the stop_signal classifier maps each enumerated throttle/block/challenge response to a typed stop reason; and the anonymity guard rejects any request carrying auth cookies/params while permitting benign anonymous cookies.
### T1.4 — Fetch primitive: handle -> user_id
Resolve a public handle to user_id via web_profile_info. Cache resolved user_id into state (avoid re-resolving on resume). A stop_signal on this call returns cleanly (no user_id, typed reason) — it does not raise. Depends on: T1.3, T1.2. Feeds: T1.5, AC2. Done when: a public handle yields a stable user_id, and a stop_signal on resolution returns a clean typed failure.
### T1.5 — Fetch primitive: paced feed pagination + normalization + politeness (AC1 fetch half, AC4)
Paginate /api/v1/feed/user/{id}/ via max_id, cap ~4 pages/call, pace pages ~1-2s. Normalize each item to a reel record (shortcode, play_count, video_url from video_versions[0].url, fetched_at, filtering to product_type=="clips"; product_type dispatch is a switch even now). Politeness (load-bearing, all invariants):
- **(Finding 1)** Stop and return a partial on the FIRST stop_signal of ANY kind (per the T1.3 classifier), not only literal 401. The returned partial carries the typed stop reason so the store can record last_stop_reason.
- NEVER sleep in this synchronous path; never poll during a cooldown (polling extends it).
- **(Finding 4) Two explicit traversal modes.** The primitive takes a `mode` param:
  - `top_scan` — page from the newest item forward and STOP as soon as an already-seen shortcode (<= high_water_id) is reached; this surfaces reels posted since the last run. This is the mode T1 wires into the synchronous window call (T1.7), because the Success Metric's "second call adds only new rows" means NEWER posts.
  - `deep_resume` — page older/deeper from the saved deep_cursor (next_max_id) toward scan_depth=90, for backfilling history. The primitive SUPPORTS this mode (same pagination code, different start cursor + stop condition), but T1's synchronous entry point exercises top_scan; a dedicated deep-backfill caller is a follow-up ticket (see Out of Scope). This is the partial disagreement with finding 4 — see the note there.
- Emit, per call: the normalized reel list, the newest-seen shortcode/id (candidate high_water_id), the deep_cursor (next_max_id) for the deep path, a partial flag, and the typed stop reason (or none).
Depends on: T1.3, T1.4. Done when: a call in top_scan mode returns a normalized list + newest-id + partial flag; a call in deep_resume mode pages from a supplied cursor; injecting ANY stop_signal (not just 401) mid-run yields a clean partial with the cursor/newest-id intact, a typed reason, and no sleep.
### T1.6 — Store layer: CSV manifest + YAML state, skip-seen, durable-first partial persistence (AC2, AC3)
CSV manifest writer (store/<handle>.csv, token-lean columns mirroring yt-media-kit; TSV fallback if captions carry commas / proper quoting). YAML state (store/<handle>.state.yaml) holding user_id, **high_water_id** (newest shortcode ingested — anchor for top_scan), **deep_cursor** (next_max_id — anchor for deep_resume), and last_stop_reason. These two state fields are disambiguated by role: high_water_id governs "have we caught up to new posts?"; deep_cursor governs "how far back have we backfilled?". They advance independently.
- **(Finding 2) Durable-first ordering + atomic state write — an explicit acceptance check.** The write sequence on every window (including a partial stop) is: (a) flush/append the CSV rows for the items actually normalized and fsync/close so they are durable on disk FIRST; (b) ONLY THEN advance high_water_id / deep_cursor to reflect exactly the items that were persisted — never past unpersisted rows; (c) write state.yaml atomically via temp-file + os.replace (atomic rename), never an in-place rewrite. If the process dies between (a) and (c), the worst case is re-fetching already-persisted rows (dedupe absorbs them) — never a skipped-forever reel. This directly upholds "store is never destructively capped."
- Per-shortcode skip-seen dedupe. Never destructively cap — append/accumulate toward scan_depth; top-N computed over the pool later.
Depends on: T1.0. Consumes: T1.5 output. Done when: a normalized batch writes rows + state; a second write with overlapping shortcodes adds zero duplicates; state round-trips; AND the durable-first ordering is proven — an injected failure after CSV flush but before state write leaves the cursor NOT advanced past those rows (they re-appear and dedupe on retry), and state.yaml is only ever observed fully-written (temp+rename), never half-written.
### T1.7 — Synchronous window call (wire fetch -> normalize -> store) (AC1 end-to-end, AC3, AC4)
Compose T1.5 (in `top_scan` mode) + T1.6 into one synchronous "fetch a window for handle H" entry point: load state -> top_scan from high_water_id -> paced fetch -> normalize -> dedupe -> **CSV rows durable FIRST -> then advance high_water_id/deep_cursor for persisted items only -> then atomic state write** -> record last_stop_reason. The primitive list_reels and the batch runner both call this (batch adds sleeping later — out of T1 scope). Depends on: T1.5, T1.6. Done when: one call on a live handle produces CSV + state; a re-run in top_scan mode adds only reels newer than high_water_id and dedupes the rest; ANY stop_signal mid-window still persists a valid partial with rows-before-cursor ordering intact and a typed last_stop_reason.
### T1.8 — FastMCP server skeleton (AC6)
ig_media_kit/mcp_server.py with a FastMCP instance and a __main__ entry so python -m ig_media_kit.mcp_server boots. Register a thin list_reels stub calling T1.7 for one handle to prove wiring — the other three tools are later tickets. Depends on: T1.1, T1.7. Done when: python -m ig_media_kit.mcp_server boots the server and the skeleton tool is registered.
### T1.9 — Live acceptance pass (all ACs)
Run the full flow against a real public handle: confirm AC1-AC6 in one sitting, including the round-1 checks — a non-401 stop_signal is caught and returns a partial; the durable-first ordering holds under an injected mid-write failure; only auth cookies are asserted-absent (benign cookies allowed); a second top_scan run surfaces new posts and dedupes. Depends on: all above. Done when: every acceptance criterion + the four review checks are observed live.

## Dependency Summary
T1.0 -> T1.1, T1.2 (probe gate), T1.6. T1.2 now gates T1.3 (the stop_signal classifier and cookie policy both depend on probe observations) -> T1.4 -> T1.5. T1.5 + T1.6 -> T1.7 -> T1.8 -> T1.9. T1.1 and T1.6 can proceed in parallel with the probe; but T1.3 no longer starts before T1.2 reports the stop-signal family and cookie requirement.

## Out of Scope (T1)
- The three other MCP tools' full surfaces (list_reels full, batch runner, download) — T1 ships the shared primitive + a proof-of-wiring stub.
- **Deep-backfill caller.** T1's fetch primitive SUPPORTS `deep_resume` mode, but a dedicated caller that walks deep_cursor toward scan_depth=90 across calls is a follow-up ticket. T1's synchronous entry point exercises `top_scan` only.
- mp4 downloading from fbcdn (wrapper carries redirect-follow, but the downloader tool is later).
- The async batch runner and any sleeping/cooldown-waiting path (T1's sync path never sleeps).
- Top-N ranking / filter application over the pool.
- Signed-URL re-resolution logic (store fetched_at now; ~24h re-resolve is a consumer concern).

## Risks
- IG surface drift (endpoints, fields, cursor shape, **and the stop-signal response set** rotate). Mitigation: T1.2 probe gate now explicitly enumerates the throttle/block family; re-run rune per CLAUDE.md triggers if behavior changed.
- **Stop-signal under-detection.** If a new throttle shape appears that the classifier doesn't recognize, the loop could keep paging and escalate the cooldown. Mitigation: the classifier defaults to `stop(unknown)` for any non-`ok` response it can't positively classify as feed data — fail closed, not open.
- Cooldown escalation under abuse during development. Mitigation: probe sparingly, respect page cap even in T1.2, never poll during cooldown.
- **Partial-write / cursor-skip corruption.** A cursor advancing past unpersisted rows would silently drop reels. Mitigation: T1.6 durable-first ordering + atomic state rename, with an explicit injected-failure acceptance check.
- **Over-stripping cookies increases blocking.** Removing benign anonymous cookies may raise block rates or break the fetch. Mitigation: T1.2 settles the cookie requirement empirically; the guard keys off auth cookies only.
- Signed-URL expiry mid-test (~36h TTL) could make a stored video_url look broken — not a fetch bug. Mitigation: store fetched_at; judge freshness by it.
- CSV comma collision in captions. Mitigation: TSV fallback rule in T1.6 / proper quoting.

## Open Questions
- Exact yt-media-kit manifest column set to mirror — confirm before finalizing the CSV header in T1.6.
- Does T1 enforce stop-at-scan_depth=90 across calls, or only leave the deep_cursor so a later deep-backfill caller continues? (Leaning: T1 leaves deep_cursor; accumulation cap is the deferred deep-backfill caller's concern. T1's top_scan run does not need scan_depth.)
- Is a single live pilot handle designated for acceptance, or does the tester pick one?
- Does the 200-with-challenge body ever coexist with a partial page of real feed items (i.e. can a single response be both partly data and a stop)? T1.2 should note this; if so, the classifier must persist the real items before stopping (durable-first still applies).

## Handoff Notes
- Build order is the dependency graph; T1.2 is a hard gate — do not implement T1.3's classifier/cookie policy or T1.4/T1.5 against assumed shapes. The probe now also gates the stop-signal family and cookie requirement, not just field shapes.
- **The stop_signal classifier (T1.3) is the single load-bearing politeness contract.** No IG-hitting caller may branch on a raw status code; all branch on the classifier. Treating literal-401 as the only stop is a blocker-level regression.
- The politeness rules in T1.5 are invariants, not preferences: first-stop-signal-stop (any kind), no-sleep-in-sync, <=4 pages/call, no cooldown polling. A reviewer treats any violation as a blocker.
- **Durable-first is an invariant (T1.6/T1.7):** CSV rows fsync'd before cursor advance; cursor advances only over persisted items; state.yaml written via temp-file + os.replace. Any ordering that can advance a cursor past unpersisted rows is a blocker.
- **ANONYMOUS is auth-cookie-keyed, not cookie-free (T1.3):** assert absence of sessionid/ds_user_id/auth params; permit benign anonymous cookies (which may be required). Enforce as code so no later tool can introduce auth.
- **Two traversal modes, disambiguated state (T1.5/T1.6):** high_water_id anchors top_scan (new posts); deep_cursor anchors deep_resume (backfill). T1 wires top_scan; deep-backfill caller is a follow-up.
- product_type dispatch should be a switch even now (clips-only today) so image/carousel/story slot in later without a rewrite.

---

### Disposition of round-1 feedback
- **Finding 1 (stop-signal family) — ACCEPTED in full.** T1.2 enumerates the real throttle/block/challenge responses; T1.3 adds a `stop_signal` classifier as the load-bearing contract; T1.5 stops on any stop_signal, not just 401. Classifier fails closed on unknown responses.
- **Finding 2 (atomicity + cursor-advance ordering) — ACCEPTED in full.** T1.6/T1.7 mandate durable-first (CSV fsync before cursor advance), advance the cursor only over persisted items, and write state.yaml via temp-file + os.replace, with an injected-failure acceptance check.
- **Finding 3 (anonymous ≠ cookie-free) — ACCEPTED in full.** ANONYMOUS redefined to key off auth cookies/params; benign anonymous cookies permitted; T1.2 settles empirically whether they're required before T1.3 codes the policy.
- **Finding 4 (new-posts vs deep-paging) — ACCEPTED, with one scope disagreement noted.** Both traversal modes are now defined and the state roles (high_water_id vs deep_cursor) disambiguated. Disagreement: the feedback implies T1 should resolve the traversal ambiguity but is neutral on whether both ship. I judge that T1 should IMPLEMENT the `top_scan` mode end-to-end (it matches the Success Metric's "new rows") and only SUPPORT `deep_resume` in the primitive, deferring the deep-backfill *caller* to a follow-up ticket — shipping both callers now widens T1 past "foundation" and adds cooldown-risk surface for a path the Success Metric doesn't exercise. The primitive is built to do both; only one caller ships in T1.
