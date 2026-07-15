---
artifact_type: handoff
artifact_version: 2
producer_role: planner
consumer_role: implementation
plan_type: task
slug: t3-download-reel-signed-url-refresh
scope_hint: T3 download_reel — mp4 download + signed-URL refresh
canonical_name: planner-task
overlays: []
status: draft
version: 1
created: 2026-07-15T19:25:20Z
updated: 2026-07-15T19:25:20Z
prior_versions: []
---

# Task plan: T3 download_reel — mp4 download + signed-URL refresh

## Problem

Implement `download_reel(shortcode)` — an MCP tool that downloads a single reel's mp4 on demand, transparently re-resolving the fbcdn signed URL when the stored one has aged past its TTL margin, and serving already-downloaded files with zero network.

## Constraints

- **ANONYMOUS ONLY** — every metadata hit goes through `AnonymousClient` (sole owner of `x-ig-app-id`); `assert_anonymous` guards every send. No auth path introduced.
- **Politeness is asymmetric here.** The mp4 download hits `fbcdn.net` — *unmetered*, follow redirects freely, never sleep. Only the *re-resolve* (owner feed on `instagram.com`) is metered: cap pages, stop-on-first-`stop_signal`, return partial, never poll during cooldown.
- **Redirect-follow is mandatory** for the CDN GET — a bare non-following GET returns 302/0 bytes.
- **Signed-URL TTL ≈ 36 h** (`oe=` param). Re-resolve when the stored `fetched_at` is older than a safety margin (~24 h) below that TTL.
- **Store is the source of truth, never destructively capped.** The `local_mp4` update rewrites a row's column atomically (temp + `os.replace`), preserving every other row and column.
- **Re-resolution locates the target by shortcode / numeric media_id — never positional/newest-first feed order** (standing order).
- Per-media endpoint `/api/v1/media/{id}/info/` is **DEAD** anonymously — re-resolve only through `/api/v1/feed/user/{id}/`.
- Single developer, small scale.

## Success Metric

- **Primary:** `download_reel(shortcode)` on a live reel produces a file on disk whose first bytes are a valid MP4 `ftyp` box, and returns that path. Verified by a real pilot probe against one config channel's reel (verify-by-pilot).
- **Secondary observable behaviours (each unit-tested with an injected transport):**
  - Second call for the same shortcode → returns the existing path with **zero network calls**.
  - Stored `fetched_at` older than the TTL margin → exactly one metered owner-feed re-resolve occurs, a fresh URL is obtained by shortcode/media_id match, and the download still succeeds.
  - A CDN response that is a 302 to be followed is handled (redirect-follow proven; a non-following GET's 302/0-byte failure does not occur).
  - The handle's CSV `local_mp4` column is updated for that shortcode and no other row/column changes.
- **Counter-metrics (must not regress):** no authenticated request ever issued (anonymity test stays green); the cached-hit path issues **no** metadata call and never sleeps; the CDN download never sleeps/blocks; a re-resolve `stop_signal` returns a clean typed partial, never an exception to the MCP client.
- **Evaluation window:** at implementation — unit suite + one live pilot probe.
- **Evaluator:** pytest suite + the pilot probe under `probe/`.

## Ordered breakdown

### T3.1 — Shortcode → owner-handle + row resolver (store)

*What the code does:* Add a store read that, given a bare shortcode, locates the owning handle and its manifest row. Iterate candidate handles — `config.channels` unioned with the `*.csv` files already present in `store_dir` (a reel may be in the store from a prior `list_reels` even if the channel was later dropped from config). For each, scan the CSV for a row whose `shortcode` matches; first match wins (a reel has exactly one owner). Return `(handle, row_dict)` or `None`.

- Row carries the fields T3 needs: `media_id`, `video_url`, `fetched_at`, `local_mp4`, `product_type`.
- No network. Reuses `ranking.load_pool` / `csv.DictReader` shape already in the repo.
- **Not-found** → the tool returns a clean typed error envelope (`"shortcode not in store; run list_reels for its owner first"`), never a traceback. Reflects the "no-network idempotent path stays strict" decision — the tool does not go hunting IG-wide for an unknown shortcode.

*Files:* `store.py` (new read method), consumed by the new download module.

### T3.2 — Idempotent cached-hit gate (no network)

*What the code does:* Before any network, if the resolved row's `local_mp4` is non-empty **and** that file exists on disk (and is non-empty), return its path immediately with a `cached: true` envelope. Zero metadata calls, zero CDN calls, no sleep.

- Strict gate, mirroring the accepted *"serve-from-store no-network gate keys on coverage contiguity"* decision: the no-network path fires only on a **provable** local artifact (column set *and* file present), not on the column alone — a stale `local_mp4` pointing at a deleted file falls through to re-download.

*Files:* new `download.py` orchestration.

### T3.3 — Freshness decision (TTL margin)

*What the code does:* Compute the stored URL's age = `now - fetched_at`. If age < `URL_REFRESH_MARGIN_SECONDS` (a named constant, ~24 h, deliberately below the ~36 h TTL) and `video_url` is non-empty → reuse the stored URL. Otherwise → re-resolve (T3.4). Also re-resolve if `video_url` is blank (older T1 rows, or a non-clip).

- Margin is a module constant with a comment tying it to the measured 36 h TTL; a future config knob is out of scope.

*Files:* `download.py`.

### T3.4 — Targeted owner-feed re-resolve (metered path, shortcode/media_id-anchored)

*What the code does:* Add a fetch-layer function that, given `user_id`, the target `shortcode`, and target numeric `media_id`, pages `/api/v1/feed/user/{id}/` and returns the **fresh** `video_versions[0].url` for the item whose `code == shortcode` (primary) / `pk == media_id` (numeric backstop) — **never** the newest/positional item. Politeness identical to the existing window: `x-ig-app-id` header via `AnonymousClient`, `classify_response` on every page, **stop and return on the first `stop_signal`**, cap at `max_pages_per_call`, `sleep=None` (sync tool never sleeps).

- Cannot reuse `fetch_window(TOP_SCAN)` — top_scan treats the (already-seen) target as the caught-up boundary and collects nothing. This is a distinct *find-by-identity* traversal that does not apply the seen/pin stop logic; it walks pages until the identity match is found or the page/stop budget is exhausted.
- Resolve `user_id` from the handle's state (`store.load_state(handle).user_id`); if absent, `resolve_user_id` first (also a metered call, same stop handling).
- Return shape: fresh URL + a fetch outcome (found / not-found-in-budget / stop_signal partial). Not-found-in-budget and stop_signal both surface as a typed non-fatal result the tool turns into an envelope note — no exception.
- **Standing-order compliance is the crux of this subtask:** the match key is the opaque shortcode and the numeric media_id, asserted in code and covered by a unit test that feeds a feed page where the target is NOT first and confirms the correct (non-positional) item is chosen.

*Files:* `fetch.py` (new targeted-resolve function reusing `_get_feed_page`, `_item_shortcode`, `_item_media_id`, `_video_url`, `classify_response`).

### T3.5 — Binary CDN download with redirect-follow

*What the code does:* Add a bytes-returning CDN GET. The existing `http_client.get_cdn` already sets `allow_redirects=True` and asserts anonymity, but it funnels through `_to_view`, which reads `.text` — unsuitable for a binary mp4. Add a sibling method (e.g. `download_cdn(url, dest_path)`) that issues the redirect-following GET and writes the response **content bytes** to disk, streaming where the transport allows.

- Write to a temp path, verify the payload is non-empty and begins with a valid MP4 signature (`ftyp` box within the first bytes), then `os.replace` into `media/<handle>/<shortcode>.mp4`. A 0-byte / 302-shaped / non-mp4 body fails the check and does **not** clobber any prior file — surfaces a typed error.
- Create `media/<handle>/` if absent.
- No sleep, no rate-limit handling — the CDN is unmetered.

*Files:* `http_client.py` (binary CDN GET), `download.py` (temp-write + ftyp-verify + atomic move).

### T3.6 — Atomic manifest `local_mp4` update (+ refreshed URL persistence)

*What the code does:* Add a store write that sets the `local_mp4` column for a given `(handle, shortcode)` row and writes the CSV back atomically (write full CSV to temp, `os.replace`), preserving header, column order (`store.CSV_COLUMNS`), and every other row/column verbatim. When T3.4 produced a fresh URL, also refresh that row's `video_url` **and** `fetched_at` in the same rewrite, so the next call sees an in-margin URL and avoids a needless re-resolve.

- Atomic rewrite mirrors the store's existing `_write_state_atomic` discipline (temp + `os.replace`); consistent with "store is never observed half-written."
- Uses `csv.DictWriter` with `QUOTE_MINIMAL` exactly as `_append_csv` does, so caption-comma quoting is preserved.

*Files:* `store.py` (new update method).

### T3.7 — `download.py` orchestration + wire the MCP tool

*What the code does:* Compose T3.1–T3.6 into `run_download_reel(shortcode, *, config, client=None, store=None, now=...)` mirroring `run_list_reels`'s testable shape (injectable client/store/now). Flow: resolve row (T3.1) → cached-hit gate (T3.2) → freshness decision (T3.3) → reuse-or-re-resolve URL (T3.4) → binary download + ftyp-verify (T3.5) → atomic manifest update (T3.6) → return an envelope `{ shortcode, handle, local_mp4, cached, refreshed, partial, stop_reason, note }`. Replace the `download_reel` **stub** in `mcp_server.py` with a call into this, keeping the `config_path` arg and never letting an exception reach the MCP client (typed error envelope on every failure branch: unknown shortcode, re-resolve stop_signal, not-found-in-budget, bad download).

*Files:* new `src/ig_media_kit/download.py`; `mcp_server.py` (stub → real, matching the `list_reels` wiring pattern).

### T3.8 — Tests + live pilot (verify-by-pilot)

*What the code does:* Unit tests with an injected fake transport (no network), covering: cached-hit → zero network; in-margin URL reuse → no metadata call, one CDN GET; expired URL → one re-resolve + download; **re-resolve picks the target by shortcode/media_id when it is not first in the feed** (standing-order guard); re-resolve `stop_signal` → clean partial envelope; unknown shortcode → typed error; CDN redirect-follow proven; ftyp-verify rejects a 0-byte/302 body; manifest rewrite changes only the target row's `local_mp4`/`video_url`/`fetched_at` and preserves quoting; anonymity assertion holds on every issued request. Then a **live pilot** under `probe/` that downloads one real reel from a config channel and asserts a valid `ftyp` on disk — the load-bearing IG-behaviour confirmation (bare-GET-302 handling, real signed-URL download).

*Files:* `tests/test_download.py` (+ shared fixtures in `conftest.py`); `probe/` pilot script.

## Out of scope

- Batch / concurrent downloading (the async batch runner is its own ticket; T3 is single-reel on demand).
- A config knob for the TTL margin (module constant now; promote later if needed).
- Re-download / force-refresh flags, checksum/dedupe of identical mp4s, thumbnail or audio extraction.
- Downloading non-clip product types (images/carousels have no `video_url`; the `product_type` dispatch is where they slot in later).
- Any change to `list_reels` / fetch traversal semantics beyond adding the new targeted-resolve function.

## Risks

- **`get_cdn`/transport can't stream bytes cleanly.** `_to_view` is text-oriented; the fake `Transport` in tests must expose a bytes-bearing attribute. Mitigation: define the binary read against the curl_cffi response's `.content`, and give the test fake a matching `content` attribute. *(Known unknown — confirm curl_cffi's streaming/`.content` surface during T3.5.)*
- **Target reel aged out of the reachable feed.** A reel older than what `max_pages_per_call` pages can reach can't be re-resolved within the polite budget. Mitigation: return a typed "could not re-resolve within page budget — retry later" partial; never widen the budget past the metered cap.
- **Positional-order temptation.** The naive re-resolve grabs `items[0]`. Mitigation: the standing-order guard test (target not first) is mandatory, not optional.
- **Stale `local_mp4` pointing at a deleted file.** Mitigation: the cached gate checks file existence, not just the column.
- **CSV full-rewrite races a concurrent `list_reels` append.** Single-dev, single-process assumption holds for now; the atomic `os.replace` bounds the window. Note it; don't build locking yet.

## Open questions

1. **TTL margin value** — 24 h proposed against the measured ~36 h TTL. Comfortable, or tighter/looser? (Affects re-resolve frequency.)
2. **Persist the refreshed `video_url` + `fetched_at` on re-resolve?** Plan assumes **yes** (T3.6) to avoid repeat re-resolves. Confirm that writing those two columns back is acceptable given the store's "keep everything fetched" posture (it's an in-place freshness update, not a destructive cap).
3. **Unknown-shortcode policy** — plan treats "not in any store CSV" as a typed error (no IG-wide search). Confirm we don't want a fallback that resolves an arbitrary handle from the shortcode.

## Handoff notes

- Reuses, does not re-invent: `AnonymousClient` (anonymity + `x-ig-app-id`), `classify_response`/`StopReason` (stop_signal handling), `_get_feed_page`/`_video_url`/`_item_shortcode`/`_item_media_id` (feed parsing), the temp+`os.replace` atomic-write discipline, and the `run_list_reels` injectable-seams shape.
- New surface area is small and additive: one store resolver + one store updater, one fetch targeted-resolve, one binary CDN GET, one orchestration module, and the stub→real MCP wiring.
- The two load-bearing correctness anchors for review: (a) the re-resolve match is by shortcode/media_id, never positional; (b) the cached path is provably network-free (assert the transport is never called).
