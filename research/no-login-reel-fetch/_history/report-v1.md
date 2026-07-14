# Decision: fetch IG reels anonymously via the user-feed endpoint — no login, no burner, no personal account

**Status:** v1 · 2026-07-14 · research-session (huginn inline)
**Verification:** 9 load-bearing claims — 7 verified by live probe, 2 open (download-URL TTL, single-reel refresh)
**Intent:** FEASIBILITY → APPROACH/DECIDE

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

## Evidence & claims (grades tied to what backs them)

- **[verified — live probe 2026-07-14]** Anonymous `GET /api/v1/feed/user/{user_id}/?count=12&max_id=…` (header `x-ig-app-id: 936619743392459`, no cookies) returns HTTP 200 with per-reel `play_count`+`ig_play_count`. — natgeo reel `DZpQwxqimz2` play_count=4,531,103; nike reel play_count=62,869,173.
- **[verified — live probe]** The feed JSON **includes `video_versions[0].url`** (fbcdn mp4). — every `product_type:clips` item returned `has_video_url=True` with real `instagram.f*.fbcdn.net/o1/v/...` URLs. → **no yt-dlp, no cookies needed to download.**
- **[verified — live probe]** Cursor `next_max_id` = `{media_id}_{user_id}`, content-anchored. Pages 2 and 3 fetched using only a saved cursor → **resume-from-saved-point works; new top posts don't corrupt it.**
- **[verified — live probe]** `product_type` cleanly separates media types — `carousel_container` items returned `play_count=None`. → extensibility hook for image/carousel later.
- **[verified — live probe]** `web_profile_info?username=X` returns numeric `user_id` anonymously (natgeo=787132, nike=13460080, instagram=25025320).
- **[verified — live probe]** Anonymous per-IP rate wall exists and **cooldown ≈ 397s (~6.6 min)**, message `{"require_login":true,"message":"Please wait a few minutes"}`. Fresh window ≈ 40-48 items (SQ5).
- **[verified — source]** `/api/v1/media/{id}/info/` is login-gated anonymously (**302 → /accounts/login/**); guest cookies don't unlock it. yt-dlp master treats the redirect as login-required. → old per-media method is dead; feed endpoint is the replacement.
- **[single-source]** yt-media-kit has **no manifest and no skip-seen** — both are net-new for IG kit (requirements #4, #5). — direct source read.
- **[open — needs pilot]** fbcdn signed-URL TTL (how long before a stored `video_url` expires) — decides refresh frequency. And single-reel URL refresh must go via the owner feed (per-media anon endpoint is dead).

---

## Contradictions left standing

- **scrapfly (2026)** claims GraphQL returns play_count anonymously; our live probes got gated-empty `data:{}`. Axis: they use an impersonated/proxied client. Resolved by *not using GraphQL* — the v1 feed endpoint is our path.
- **Two view metrics:** `web_profile_info.video_view_count` (older, smaller, sometimes 0) vs `feed/user.play_count` (the big "plays"). We use `play_count`.
- **dev.to "IG blocks plain-Python TLS instantly"** vs our plain-curl 200s. Axis: TLS blocking bites the API surface *under volume*, not a single public GET. → use curl_cffi impersonation as durability insurance, not a hard requirement.

---

## Open unknowns (what would settle them)

1. **fbcdn URL TTL** — download a stored URL at T+1h, +6h, +24h; find the expiry cliff. Gates the download-refresh design.
2. **Single-reel refresh** — confirm re-scanning the owner feed reliably re-finds a specific shortcode's fresh URL; store per-reel page-cursor to jump near it.
3. **Cooldown escalation** — is the ~6.6 min stable, or does it grow with repeat offenses? One data point only.
4. **Endpoint durability** — feed/user is unofficial; IG could login-wall it (as it did media/info). Mitigation: keep the fetch layer swappable.

---

## Architecture

**Language:** Python. **Client:** `curl_cffi` (`impersonate="chrome"`). **Server:** FastMCP (`mcp` SDK). No yt-dlp, no browser automation, no cookies.

```
┌──────────────── MCP server (FastMCP) ────────────────┐
│ Tool 1  list_reels(handle, count, sort_by,           │
│         min_views, min_duration, max_age_days,       │
│         scan_depth=90, fresh_fetch=false)            │
│         → sorted manifest rows (NO mp4 download)      │
│ Tool 2  download_reel(shortcode)                     │
│         → downloads that one mp4, returns local path │
└──────┬──────────────────────────────────┬───────────┘
       │                                  │
  ┌────▼─────┐   ┌───────────────┐  ┌──────▼──────┐
  │ Fetcher  │   │    Store      │  │ Downloader  │
  │ curl_cffi│◄─►│ CSV manifest  │◄─│ GET mp4 url │
  │ ONE      │   │ + YAML state  │  └─────────────┘
  │ window   │   └───────────────┘
  │ per call │   NO background worker, NO thread, NO cron.
  └──────────┘   Each call = one ~40-item window, synchronous,
                 returns fast. Store accumulates across calls
                 until scan_depth (90) reached. The ~6.6-min
                 cooldown is absorbed by the spacing between
                 manual calls — no single call ever waits.
```

**Storage (format split for token-lean LLM consumption):**
```
store/<handle>.csv        # shortcode,play_count,likes,comments,caption,taken_at,duration,local_mp4,fetched_at  (TSV if captions carry commas)
store/<handle>.state.yaml # user_id, high_water_id, coverage:[{newest,oldest,cursor}], last_run
media/<handle>/<shortcode>.mp4
config.yaml               # channels[], top_reels filters, dirs  (mirrors yt-media-kit)
```

**Flow A — `list_reels(handle)` (call-driven, one window per call, synchronous):**
1. Load store.
2. **Top-check:** page from top for new posts until stored `high_water_id` (usually 0-few); merge → CSV, update `high_water_id`.
3. **Deepen:** if store depth < `scan_depth` (90) and `more_available`, resume the saved deep cursor and fetch older items until depth ≥ 90 **or** this window's budget (~40) is spent; merge, save deep cursor.
4. If a 401 trips mid-call → stop immediately, save cursors, **return what's stored** (graceful partial; caller re-invokes after the ~7-min cooldown to continue).
5. Filter + sort the stored pool → return top-`count`. No downloads.

Result: call 1 (new handle) ≈ 40 reels; each later call (spaced past the cooldown) adds ~40 until 90 deep; thereafter calls are cheap top-refresh. `fresh_fetch=false` + already ≥ `scan_depth` → serve from store, no network.

**Flow B — `download_reel(shortcode)`:**
1. Look up shortcode → owner handle, stored URL, `local_mp4`. 2. Already downloaded → return path. 3. Else resolve **fresh** URL (stored URL if <TTL, else re-scan owner feed) → GET → `media/<handle>/<shortcode>.mp4` → update CSV → return path.

**Rate-limit handling (no worker, no sleep):** within a call, pace pages (~1-2s apart), cap ~4 pages (~40 items) per call. On 401 → **stop and return partial** — never sleep inside a call. The ~6.6-min cooldown is absorbed by the spacing between manual calls; a call arriving <~7 min after the previous one simply returns what's stored with a "budget cooling" note. Steady-state top-refresh (<40 new items) never trips it. **Store is never destructively capped** — `scan_depth=90` is the fetch-effort target; everything fetched is kept, and top-`count` is computed over the pool.

**Coverage / gaps:** coverage = list of contiguous `[newest,oldest,cursor]` segments, normally ONE. Top-check walks top→down to prior high-water → stays contiguous. Only >40 new-since-last-visit opens a 2nd segment → a later call's deepen step backfills between and merges. Skip-seen = per-shortcode dedupe in CSV.

**Extensibility (#6):** `product_type` dispatch (clips now; image/carousel/story later) — a type switch, not a rewrite.

---

## MVP / spike plan

Smallest build that proves the end-to-end hypothesis before the full kit:

1. **Spike 1 — fetch+rank (½ day):** Python + curl_cffi. `list_reels("natgeo")`: web_profile_info → user_id → page feed one window → parse clips → sort by play_count → write `store/natgeo.csv`. **Signal:** CSV with real play_counts, top reel correct. **Confirms:** the whole anonymous read path in real code.
2. **Spike 2 — download + URL TTL (½ day):** `download_reel(shortcode)` GET the stored `video_versions[0].url`. Re-run at T+1h/+6h/+24h to find the expiry cliff. **Signal:** mp4 plays; TTL measured. **Resolves open unknown #1**, finalizes refresh design.
3. **Spike 3 — call-driven fill to 90 + resume (½ day):** invoke `list_reels` repeatedly (spaced past the ~7-min cooldown); confirm the store accumulates ~40 → ~80 → 90 across calls, each call resuming from the saved deep cursor, 401 mid-call returns a clean partial. **Signal:** 90-reel CSV assembled across ≥2 spaced calls with no background thread; top-check picks up any new posts. **Confirms** the call-driven fill + cursor durability + graceful-partial.

If all three pass → promote to the full MCP kit (FastMCP wrapper, config, skip-seen, coverage/merge). Spikes are throwaway probes, not the build.

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
