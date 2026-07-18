<!-- rune-generated: 2026-07-14 | git: acdd3c5 | rune: 1.0 -->

# IG Media Kit

Self-hosted MCP that fetches top Instagram **reels** (with view counts) from a list of
channels — **anonymously: no login, no account, no personal-identity link**. The Instagram
counterpart to `yt-media-kit`.

## Stack

- **Language:** Python 3.12+
- **Framework:** FastMCP (`mcp[cli]` SDK) — exposes the tools as an MCP server
- **HTTP client:** `curl_cffi` (`impersonate="chrome"`) — TLS-fingerprint durability. NOT requests/httpx; NOT a browser (no Playwright/Selenium); NOT yt-dlp.
- **Storage:** flat files — CSV manifests + YAML state per handle; mp4s on disk. No DB.
- **Key libs:** `curl_cffi`, `mcp[cli]`, `PyYAML`

## Architecture

Four MCP tools over one shared fetch engine. **Fetcher** (one paced ~40-item window: `web_profile_info` → `user_id` → paginate `/api/v1/feed/user/{id}/`) is the primitive, wrapped by the command-side **fill primitive** (`fill.run_fill` — top-check + deepen); the **Batch runner** loops it in a background job (the only writer that advances coverage toward `scan_depth=90`). **`list_reels` is READ-ONLY over the store** (CQRS split) — it ranks/serves the already-fetched pool and NEVER hits IG; an un-analyzed handle gets a typed `not_analyzed` error steering to `start_batch_fetch`. **Store** (CSV manifest + YAML state, per handle) is the source of truth; **Downloader** GETs the mp4 URL that already arrives in the feed JSON. Data flows: feed JSON → normalized reel → store → (read-only) `list_reels` serve / (on demand) download. No background worker except the async batch tool.

## Invariants

- **ANONYMOUS ONLY.** No login, no cookies, no session, no account, no burner — ever. (Burner was researched and explicitly rejected: consumable + nonzero risk to the user's real account. See `research/no-login-reel-fetch/report.md`.) No code path may authenticate.
- **Politeness is load-bearing.** The metadata API is IP-rate-limited to ~48 items per ~6.6-min window, and the cooldown **escalates under abuse** (measured 6.6→13 min; budget 48→36→12). Every IG-hitting path must: pace pages ~1-2s, cap ~4 pages/call, **stop + return partial on the first 401**, and **never poll during a cooldown** (it extends it). Only **two** paths hit IG: the async batch runner (`fill.run_fill` under the gate — the only sleeper) and `download_reel`'s >24h re-resolve. **`list_reels` never even attempts IG** (read-only over the store), so "never sleeps" is now fully true — it can't emit a cooldown at all.
- **Required header:** `x-ig-app-id: 936619743392459` (IG public web app id) on every API call.
- **Metadata metered, CDN not.** Getting URLs (`instagram.com`) is the bottleneck; downloading mp4s (`fbcdn.net`) is unmetered — pace the former, download the latter freely.
- **Signed-URL TTL ≈ 36 h.** `video_versions[0].url` expires (`oe=` param). Store `video_url`+`fetched_at`; re-resolve via the owner feed if older than ~24 h.
- **Store is never destructively capped.** `scan_depth=90` is a fetch-effort target; keep everything fetched; top-N is computed over the pool. Skip-seen = per-shortcode dedupe.
- **Mirror yt-media-kit ergonomics** (config schema, filters, manifest shape) where it translates.

## Gotchas

- The per-media endpoint `/api/v1/media/{id}/info/` is **DEAD anonymously** (302 → login). Use `/api/v1/feed/user/{id}/` — it carries `play_count` + `ig_play_count`.
- **Downloads need redirect-follow** (`curl -L` / `follow_redirects=True`) — fbcdn does 1 redirect; a bare GET returns 302/0 bytes.
- `play_count` (the real "plays") comes from the **feed** endpoint; `web_profile_info` only has the smaller, sometimes-0 `video_view_count`.
- Feed `count=` is **hard-capped at 12/page** regardless of value — paginate with `max_id`.
- Cursor `next_max_id` = `{media_id}_{user_id}`, content-anchored — durable across runs; resume from it, don't re-page.
- **GraphQL `doc_id`s rotate/expire** (~q2-4wk) — don't build on them; the v1 feed endpoint is the stable path.
- Reels are `product_type == "clips"`; `carousel_container`/image posts have `play_count == null`.

## Quick conventions

- **Config:** `config.yaml` (mirrors yt-media-kit) — `channels[]`, `top_reels` filters, dirs; `$IG_MK_CONFIG` override.
- **Manifests:** CSV (token-lean for a future LLM layer), TSV if captions carry commas. **State:** YAML. Per handle.
- **Files:** `store/<handle>.csv`, `store/<handle>.state.yaml`, `media/<handle>/<shortcode>.mp4`, `store/_batch/<job_id>.*`.
- **Extensibility:** `product_type` dispatch (clips now; image/carousel/story later) — a switch, not a rewrite.
- **Verify by pilot:** any claim about IG behavior (endpoints, limits, fields) is confirmed by a real probe before it's relied on — docs describe intent, probes observe reality.
- **Commits:** use the `conventional-commits` skill to generate commit messages — do not hand-author your own. It enforces the Conventional Commits format for this repo.

## Skills installed

- `sindri` — implementation (plan/build/iterate/spike)
- `mimir` — pre-code planning / architecture breakdown
- `multi-perspective-review` — code review
- `skald` — artifact persistence / handoff protocol
- `rune` — project onboarding (re-run on major changes)

## Re-run rune when

- The fetch mechanism changes (IG blocks the feed endpoint, a new endpoint is adopted)
- The anonymous-only invariant is ever revisited
- Language/framework/storage changes, or core architectural boundaries are redrawn
