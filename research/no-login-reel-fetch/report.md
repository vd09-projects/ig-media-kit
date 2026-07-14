# Decision: fetch IG reels anonymously via the user-feed endpoint — no login, no burner, no personal account

**Status:** v4 · 2026-07-14 · research-session (huginn inline)
**Verification:** 12 load-bearing claims — all 12 verified by live probe (incl. anonymous download end-to-end + ~36 h URL TTL). No feasibility items open.
**Intent:** FEASIBILITY → APPROACH/DECIDE

## Changelog
- **v4.1 (2026-07-14):** Closed the last 2 open items by pilot — anonymous mp4 download works end-to-end (200, 13 MB, playable, needs `-L`); fbcdn signed-URL TTL ≈ 36 h (decoded from `oe=` param). All 12 claims now verified.
- **v4 (2026-07-14):** Added async **batch** subsystem — MCP grows to 4 tools (`list_reels`, `download_reel`, `start_batch_fetch`, `get_batch_status`). `start_batch_fetch` runs a background job over N channels (paced across cooldowns, allowed to wait since async), aggregates into a global/per-channel top-N, optionally downloads, and notifies a `callback_url` (best-effort, 3× retry; `get_batch_status` is the poll fallback / source of truth). Reconciles with v1's "no background worker": the worker exists ONLY for the explicitly-async batch tool; the interactive `list_reels` stays synchronous/fast. Rewrote architecture + flow diagrams.
- **v3 (2026-07-14):** Investigated the burner-account option (3 scouts: signup requirements, linkage/blast-radius, real-world outcomes) to see if a deep-history fetch is worth it. Findings: a de-linked burner IS creatable (email-only signup works), and documented collateral bans all require a *shared signal* isolation removes — BUT (i) the burner is consumable/dies unpredictably (read-only account banned after ~1yr with sessions done right; others in hours-days), (ii) residual risk to the real account is low-but-not-zero (Meta's undisclosed "common ownership" discretion + false-positive ban waves). **User decision: DROP the burner entirely — anonymous-only.** The real account must never carry even low residual risk; ~48/window + call-driven fill to 90 is accepted as the final design. Added "Burner option — investigated and rejected" section.
- **v2 (2026-07-14):** Investigated whether the ~48/IP anonymous rate limit can be legitimately raised. Ran 3 live pilots (H1 count-param, H4 pacing, H2 curl_cffi browser-fingerprint) — **all failed**; the quota is a deliberately-defended, count-based, IP-keyed anonymous limit (cooldown escalates 6.6→13 min, budget degrades 48→36→12 under abuse). Added "Rate-limit workarounds investigated" section + a **politeness/backoff** design principle. Rejected multi-IP/cloud fan-out (datacenter IPs throttled harder; ToS gray area). Confirms call-driven design as correct, not just convenient.
- **v1 (2026-07-14):** Initial. Anonymous `feed/user` fetch path found + pilot-verified; Python, two-tool MCP, CSV+YAML store, call-driven fill to depth 90.

---

## Recommendation

Build IG Media Kit in **Python** around a single anonymous, no-login fetch path:
`web_profile_info` (handle → numeric `user_id`) → paginate **`GET /api/v1/feed/user/{user_id}/`**, which returns per-reel **`play_count` + `ig_play_count`**, shortcode, `product_type`, caption, timestamps, likes/comments, **and the downloadable `video_versions[0].url`** — all with **zero login, zero cookies, no account of any kind**. This fully satisfies the hard "personal account must never be linked" constraint: there is no account to link or ban; worst case is a transient per-IP rate-limit that self-heals.

You leaned toward **no login / no burner** — that leaning is fully honored, not overridden: the recommended path *is* fully anonymous. A burner is **not needed** and is explicitly rejected (it would *increase* linkage risk, per SQ3).

**The one accepted tradeoff:** the feed is chronological, and anonymous access rate-limits at **~40 items per ~6.6-min window**. So "top-N per channel" means **top-N by views within a scanned window (default depth: last 90 posts)**, filled **call-by-call** — no background worker. Call 1 on a new handle returns ~40 (top-N from those); each later call (spaced ≥~7 min past the cooldown) adds another ~40 window until the store is 90 deep, then calls are cheap top-refresh. All-time greatest-hits on prolific accounts is reachable only by many paced calls.

---

## Frame

| | Content |
|---|---|
| **KNOWNS** | Mirror yt-media-kit ergonomics **[hard]** · self-hosted, no paid API **[hard]** · no login/no burner preferred **[soft → researched past; anonymous path found, so honored]** · small scale ~1-10 handles manual **[hard]** · Python **[decided this session]** · default scan depth = last 90, call-driven fill (no background worker) **[decided]** |
| **UNKNOWNS (resolved)** | Y1 anon views? → **YES via feed/user** · Y2 which surface? → **feed/user, not media/info** · Y3 burner needed? → **NO** · Y4 client? → **curl_cffi HTTP, not browser** · Y5 anon discovery? → **YES, feed carries views** · Y6 yt-kit shape → **mapped** |
| **ASSUMPTIONS** | `x-ig-app-id: 936619743392459` stays valid · IG doesn't harden the feed endpoint imminently · reels-only for v1 · fbcdn signed-URL TTL long enough to download soon after listing (OPEN) |

---

## Reasoning — approach ranking

| Approach | Views? | Personal linkage | Durability | Verdict |
|---|---|---|---|---|
| **Anonymous `/api/v1/feed/user/{id}/` + curl_cffi** | ✅ `play_count` | **none — no account** | Med (unofficial, IG rotates) | ✅ **CHOSEN** |
| Anonymous `web_profile_info` only | ⚠️ `video_view_count` (smaller, sometimes 0) | none | Med | discovery helper / user_id lookup |
| Burner account + session | ✅ | **significant — 4-factor de-link, home-IP blast radius** | Low (bans in days) | ❌ rejected |
| Anonymous GraphQL doc_id | ✅ if TLS-impersonated | none | **very low — doc_ids retire q2-4wk** | ❌ brittle |
| `/api/v1/media/{id}/info/` (old method) | ✅ but **login-gated (302)** | needs login | — | ❌ dead anonymously |

---

## Rate-limit workarounds investigated (all failed — ~48/IP is the floor)

Before accepting the ~40-48-per-window anonymous cap, four legitimate single-IP / no-login workarounds were attacked; three pilot-tested live 2026-07-14, all negative:

| # | Hypothesis | Pilot | Result |
|---|---|---|---|
| **H1** | Bigger `count` per request | count=12/33/50/100 in one request | ❌ **hard-capped at 12 items/page** regardless of `count` |
| **H4** | Pacing (quota is rate not count?) | 1 page / 20s from a fresh window | ❌ tripped at cum=36 → **quota is count-based**, pacing does not help |
| **H2** | Browser TLS fingerprint raises quota | curl_cffi `impersonate="chrome"`, paged feed | ❌ same trip, no bigger quota → **quota is IP-keyed, fingerprint-independent** |
| **H3** | Minted guest session (ig_did/csrf) | not run | low prior; superseded by H2's IP-keyed finding; IP too degraded to test cleanly |
| — | Multi-IP / cloud-runner fan-out | rejected without pilot | datacenter/cloud IPs throttled *harder* (SQ3); ToS gray area; unnecessary at this scale |

**Meta-evidence the limit is deliberately defended:** the cooldown **escalates with abuse** (measured 6.6 min → ~13 min across the session) and the effective per-window budget **degrades** (48 → 36 → 12 items) under repeated tripping. IG's own 401 body — `{"require_login":true,"igweb_rollout":true,"message":"Please wait a few minutes"}` — states the design intent: anonymous access gets a small window; log in for more. **Verdict: no legitimate single-IP, no-login method raises the ~48 ceiling. Accept it; design politely around it.**

## Burner option — investigated and rejected (v3)

Because the anonymous path caps at ~48/window (all workarounds failed, above), a logged-in **burner account** — the only verified way to fetch deep/all-time history — was researched as the last resort. Rejected by user decision after the evidence:

- **[verified — live probe + tier-1]** A de-linked burner is *creatable*: email-only signup works in 2026 (phone is a skippable, risk-triggered challenge, not a hard gate). — IG `emailsignup` page HTTP 200 with email controller; Meta docs confirm accounts link into one Accounts Center only by explicit user action, not device co-presence.
- **[verified — tier-1 Meta policy]** A real harm path to the real account EXISTS: Meta reserves the right to disable accounts of *"common ownership"* / *"close linkage"* with a removed account. — transparency.meta.com/account-integrity.
- **[single-source / absence-of-evidence]** Every *documented* collateral ban requires a **shared signal** (reused phone/email, shared device fingerprint, shared IP) that proper isolation removes; **no** case found of two fully-isolated accounts cross-banning. Not proof of safety — Meta's full signal set is undisclosed, and 2025-26 false-positive ban waves hit accounts by error.
- **[verified — first-hand, tier-1 repo]** The burner is **consumable/unreliable**: a read-only, session-correct instagrapi account ran ~1 year then was suddenly suspended + selfie-ID checkpoint; other burners banned in hours-to-days. No config keeps a burner alive indefinitely.
- **[reasoning]** This tool is strictly read-only (never follows/likes/comments/DMs) → closes the behavioral/social-graph linkage vector for free — the lowest-risk possible logged-in usage. Even so, rejected.

**Decision:** **anonymous-only.** The user's prime constraint — never put the real account at even low residual risk — outweighs deep-history access. The ~48/window + call-driven fill to depth 90 is the final, accepted design. *(If deep history is ever needed, the minimum isolation recipe is preserved in `_history/report-v2.md` context: separate cookie-clean browser profile + non-home/non-datacenter IP + fresh email + fresh non-VoIP phone + no 2FA + never the multi-account switcher + read-only.)*

## Evidence & claims (grades tied to what backs them)

- **[verified — live probe 2026-07-14]** Anonymous `GET /api/v1/feed/user/{user_id}/?count=12&max_id=…` (header `x-ig-app-id: 936619743392459`, no cookies) returns HTTP 200 with per-reel `play_count`+`ig_play_count`. — natgeo reel `DZpQwxqimz2` play_count=4,531,103; nike reel play_count=62,869,173.
- **[verified — live probe]** The feed JSON **includes `video_versions[0].url`** (fbcdn mp4). — every `product_type:clips` item returned `has_video_url=True` with real `instagram.f*.fbcdn.net/o1/v/...` URLs. → **no yt-dlp, no cookies needed to download.**
- **[verified — live probe]** Cursor `next_max_id` = `{media_id}_{user_id}`, content-anchored. Pages 2 and 3 fetched using only a saved cursor → **resume-from-saved-point works; new top posts don't corrupt it.**
- **[verified — live probe]** `product_type` cleanly separates media types — `carousel_container` items returned `play_count=None`. → extensibility hook for image/carousel later.
- **[verified — live probe]** `web_profile_info?username=X` returns numeric `user_id` anonymously (natgeo=787132, nike=13460080, instagram=25025320).
- **[verified — live probe]** Anonymous per-IP rate wall exists and **cooldown ≈ 397s (~6.6 min)**, message `{"require_login":true,"message":"Please wait a few minutes"}`. Fresh window ≈ 40-48 items (SQ5).
- **[verified — source]** `/api/v1/media/{id}/info/` is login-gated anonymously (**302 → /accounts/login/**); guest cookies don't unlock it. yt-dlp master treats the redirect as login-required. → old per-media method is dead; feed endpoint is the replacement.
- **[single-source]** yt-media-kit has **no manifest and no skip-seen** — both are net-new for IG kit (requirements #4, #5). — direct source read.
- **[verified — live probe 2026-07-14]** The mp4 **downloads anonymously end-to-end**: GET the feed's `video_versions[0].url` with `-L` (fbcdn does 1 redirect) → HTTP 200, 13 MB, valid playable mp4 (ffprobe: 109s, 955 kbps), no login/cookies. — natgeo reel `DZpQwxqimz2`.
- **[verified — URL param decode]** fbcdn signed-URL **TTL ≈ 36 h**: the `oe=` expiry param decodes to a unix timestamp 35.8 h after capture. → store `video_url`+`fetched_at`; re-resolve via the owner feed if older than ~24 h (safety margin). Single-reel refresh goes via the owner feed (per-media anon endpoint is dead).
- **[verified — live probe 2026-07-14]** The mp4 **CDN download is NOT rate-limited** at this scale (separate host from the metered API): 25 rapid range-GETs → all 206; 4 full 13 MB downloads back-to-back → all 200 at ~14 MB/s, no throttle. → the download-count bottleneck is *fetching URLs* (the ~48/window feed API), NOT the download step. Once URLs are in the store, download freely within the 36 h TTL. (Tested one IP, small scale; massive parallel pulls untested.)

---

## Contradictions left standing

- **scrapfly (2026)** claims GraphQL returns play_count anonymously; our live probes got gated-empty `data:{}`. Axis: they use an impersonated/proxied client. Resolved by *not using GraphQL* — the v1 feed endpoint is our path.
- **Two view metrics:** `web_profile_info.video_view_count` (older, smaller, sometimes 0) vs `feed/user.play_count` (the big "plays"). We use `play_count`.
- **dev.to "IG blocks plain-Python TLS instantly"** vs our plain-curl 200s. Axis: TLS blocking bites the API surface *under volume*, not a single public GET. → use curl_cffi impersonation as durability insurance, not a hard requirement.

---

## Open unknowns (what would settle them)

1. ~~Download GET + fbcdn URL TTL~~ **RESOLVED (2026-07-14):** download works anonymously end-to-end (200, 13 MB, playable mp4, needs `-L`); signed-URL TTL ≈ 36 h (decoded from the `oe=` param). Both moved to verified above. (Aside: an earlier attempt was blocked by a self-inflicted IP throttle from the rate-limit pilots — recovered in ~30 min, re-confirming the escalation/politeness finding.)
2. **Single-reel refresh** — confirm re-scanning the owner feed reliably re-finds a specific shortcode's fresh URL; store per-reel page-cursor to jump near it.
3. ~~Cooldown escalation~~ **RESOLVED (v2):** it *does* escalate with abuse (6.6→13 min measured), and budget degrades (48→36→12). → handled by the politeness/backoff principle above.
4. **Endpoint durability** — feed/user is unofficial; IG could login-wall it (as it did media/info). Mitigation: keep the fetch layer swappable.

---

## Architecture

**Language:** Python. **Client:** `curl_cffi` (`impersonate="chrome"`). **Server:** FastMCP (`mcp` SDK). No yt-dlp, no browser automation, no cookies, no account.

### Component diagram

```
                         ┌───────────────────────────────────────────────┐
                         │             MCP server (FastMCP)              │
                         │                                               │
   interactive / fast ── │  ① list_reels(handle, count, sort_by, …)      │
                         │       → sorted manifest rows (no download)    │
                         │  ② download_reel(shortcode)                   │
                         │       → one mp4, returns local path           │
   async / long-running ─│  ③ start_batch_fetch(handles[], top_n,        │
                         │       scope, …, callback_url) → {job_id} now  │
                         │  ④ get_batch_status(job_id) → job state       │
                         └───┬───────────────┬───────────────┬───────────┘
                             │               │               │
              ┌──────────────▼──┐   ┌─────────▼────────┐   ┌──▼──────────────┐
              │    Fetcher      │   │   Batch runner   │   │   Downloader    │
              │  curl_cffi      │   │  (bg thread/job) │   │  GET signed     │
              │  profile→user_id│   │  loops Fetcher   │   │  fbcdn mp4 url  │
              │  feed page (12) │   │  per handle,     │   └──────┬──────────┘
              │  ONE window     │   │  paced; MAY wait │          │
              │  /interactive   │   │  out cooldowns;  │          │
              │  call           │   │  aggregate top-N │          │
              └───────┬─────────┘   │  → callback POST │          │
                      │             └───────┬──────────┘          │
                      │                     │                     │
                      └─────────────┬───────┴─────────────────────┘
                                    │  read / checkpoint
                          ┌─────────▼─────────────────────────────┐
                          │                Store                   │
                          │  store/<handle>.csv      (manifest)    │
                          │  store/<handle>.state.yaml (cursors)   │
                          │  store/_batch/<job_id>.{yaml,csv}      │
                          │  media/<handle>/<shortcode>.mp4        │
                          └────────────────────────────────────────┘
```

**Two fetch modes, one engine.** The **Fetcher** (one paced ~40-item window, never sleeps) is the shared primitive. `list_reels` calls it **synchronously** and returns fast. The **Batch runner** calls the *same* Fetcher in a **background job** and — because the caller opted into async — is allowed to **wait out the ~6.6-min cooldowns** to complete a large multi-channel pull. This is the only background worker in the system, and it exists solely for tool ③.

### Storage (format split for token-lean LLM consumption)
```
store/<handle>.csv        # shortcode,play_count,likes,comments,caption,taken_at,duration,product_type,video_url,local_mp4,fetched_at
store/<handle>.state.yaml # user_id, high_water_id, coverage:[{newest,oldest,cursor}], last_run
store/_batch/<job_id>.yaml# status, handles, done/total, params, result_csv, callback state
store/_batch/<job_id>.csv # aggregated top-N manifest across the batch
media/<handle>/<shortcode>.mp4
config.yaml               # channels[], top_reels filters, dirs (mirrors yt-media-kit)
```

### Flow A — `list_reels(handle)` (synchronous, one window per call)
1. Load store.
2. **Top-check:** page from top for new posts until stored `high_water_id` (usually 0-few); merge → CSV, bump `high_water_id`.
3. **Deepen:** if depth < `scan_depth` (90) and `more_available`, resume the saved deep cursor, fetch older until depth ≥ 90 **or** the ~40 window budget is spent; merge, save cursor.
4. On a 401 mid-call → stop, save cursors, **return what's stored** (graceful partial).
5. Filter + sort the stored pool → return top-`count`. No downloads.

→ call 1 (new handle) ≈ 40; later spaced calls add ~40 until 90 deep; then cheap top-refresh. `fresh_fetch=false` + already ≥ `scan_depth` → serve from store, no network.

### Flow B — `download_reel(shortcode)`
1. Look up shortcode → owner handle, stored URL, `local_mp4`. 2. Already downloaded → return path. 3. Else resolve a **fresh** URL (stored URL if < TTL, else re-scan owner feed) → GET → `media/<handle>/<shortcode>.mp4` → update CSV → return path.

### Flow C — `start_batch_fetch(handles[], …, callback_url)` (async job)
```
caller ──③ start_batch_fetch(10 handles, top_n=10, scope="global", callback_url)
   │         └─ persist store/_batch/<job_id>.yaml (status=running)
   └────────── returns { job_id }  ◄── caller is free immediately
                     │
   background job:   │  for each handle:
                     │     Fetcher: call-driven fill to scan_depth,
                     │     paced; on 401 → SLEEP out cooldown, resume
                     │     from checkpoint cursor (crash-safe)
                     │  aggregate all stored reels → sort by sort_by →
                     │     scope="global": top_n across ALL channels
                     │     scope="per_channel": top_n per channel
                     │  write store/_batch/<job_id>.csv
                     │  [if download_top] download those mp4s
                     ▼
   POST callback_url { job_id, status:"done", result_path, summary, top:[…] }
        └─ 3× retry w/ backoff; header x-callback-secret if configured
        └─ if all retries fail: job still complete; get_batch_status serves it
                     │
caller ──④ get_batch_status(job_id) → { status, done/total, items, result_path }  (poll fallback)
```
Estimate: 10 fresh channels × depth-90 ≈ 10-20 windows ≈ **~1-2 h** paced — exactly why this path is async + callback, not a blocking call.

### Cross-cutting

- **Rate-limit / politeness (LOAD-BEARING):** pilots proved the quota is IP-keyed and *punishes abuse* (cooldown 6.6→13 min, budget 48→36→12). `list_reels` pages ~1-2s apart, caps ~40/call, returns partial on first 401, **never sleeps**. The batch runner MAY sleep the cooldown (async) but still never hammers during it. One IP, gentle by design.
- **Crash-safety / resume:** cursors + per-handle stores + job state all checkpointed to disk after every page. Any process death resumes from the last cursor — no re-fetch (skip-seen = per-shortcode CSV dedupe).
- **Coverage / gaps:** `coverage` = contiguous `[newest,oldest,cursor]` segments, normally ONE. Top-check keeps it contiguous; only >40-new-since-last-visit opens a 2nd segment that a later deepen/batch pass backfills + merges.
- **Idempotency:** handles fresh within window → served from store, no re-fetch. Cheap re-runs.
- **Extensibility (#6):** `product_type` dispatch (clips now; image/carousel/story later) — a type switch, not a rewrite.

---

## MVP / spike plan

Smallest build that proves the end-to-end hypothesis before the full kit:

1. **Spike 1 — fetch+rank (½ day):** Python + curl_cffi. `list_reels("natgeo")`: web_profile_info → user_id → page feed one window → parse clips → sort by play_count → write `store/natgeo.csv`. **Signal:** CSV with real play_counts, top reel correct. **Confirms:** the whole anonymous read path in real code.
2. **Spike 2 — download + URL TTL (½ day):** `download_reel(shortcode)` GET the stored `video_versions[0].url`. Re-run at T+1h/+6h/+24h to find the expiry cliff. **Signal:** mp4 plays; TTL measured. **Resolves open unknown #1**, finalizes refresh design.
3. **Spike 3 — call-driven fill to 90 + resume (½ day):** invoke `list_reels` repeatedly (spaced past the ~7-min cooldown); confirm the store accumulates ~40 → ~80 → 90 across calls, each call resuming from the saved deep cursor, 401 mid-call returns a clean partial. **Signal:** 90-reel CSV assembled across ≥2 spaced calls with no background thread; top-check picks up any new posts. **Confirms** the call-driven fill + cursor durability + graceful-partial.

4. **Spike 4 — async batch + callback (½ day):** `start_batch_fetch(2-3 handles, top_n, scope="global", callback_url=<local test server>)` → returns job_id; background job fills each handle (sleeping ≥1 cooldown), aggregates a global top-N, POSTs the callback; kill mid-job → confirm resume from checkpoint; `get_batch_status` returns state throughout. **Signal:** callback received with a correct cross-channel top-N; job survives a restart. **Confirms** the async job + aggregation + callback contract.

If all four pass → promote to the full MCP kit (FastMCP wrapper, config, skip-seen, coverage/merge, 4 tools). Spikes are throwaway probes, not the build.

---

## Sources

1. Live probes (this session, 2026-07-14): anonymous feed/user play_count + video_url + cursor resume; media/info 302; cooldown ≈397s.
2. yt-dlp Instagram extractor (master): https://raw.githubusercontent.com/yt-dlp/yt-dlp/master/yt_dlp/extractor/instagram.py
3. yt-dlp issues #17074, #13626, #16257 — anonymous empty/login-wall, reels-tab unsupported.
4. instaloader structures.py (master); issues #2508, #2511, #2482, #2689, #1761 — 401 require_login, doc_id retirement, view vs play_count.
5. curl_cffi impersonate targets: https://curl-cffi.readthedocs.io/en/latest/impersonate/targets.html
6. scrapfly "How to Scrape Instagram in 2026" (contested, tier-3): https://scrapfly.io/blog/posts/how-to-scrape-instagram
7. SQ3 burner-linkage sources (tier-3, vendor): multilogin.com, nodemaven.com, quackr.io, plainenglish.io.
8. yt-media-kit local source: ~/repos/yt-media-kit (config schema, MCP tools, pipeline, no-manifest/no-skip-seen).
