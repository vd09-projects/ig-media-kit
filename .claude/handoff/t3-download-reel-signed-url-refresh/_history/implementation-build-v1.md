---
artifact_type: handoff
artifact_version: 2
producer_role: implementation
consumer_role: review
plan_type: build
slug: t3-download-reel-signed-url-refresh
scope_hint: T3 download_reel — mp4 download + signed-URL refresh
canonical_name: implementation-build
overlays: []
status: draft
version: 1
created: 2026-07-16T01:20:00Z
updated: 2026-07-16T01:20:00Z
prior_versions: []
---

# Build summary: T3 download_reel — mp4 download + signed-URL refresh

Implemented all 8 subtasks (T3.1–T3.8) of the approved plan on branch `feat/t3-download-reel`, plus the folded-in plan-review fix (ftyp signature at offset 4, not 0). All CLAUDE.md invariants honored: anonymous-only (no auth path added), metered vs. unmetered asymmetry (metadata re-resolve paced/stop-guarded; CDN download unmetered, never sleeps), `x-ig-app-id` via `AnonymousClient`, redirect-follow on the CDN GET, atomic CSV rewrite, and the standing order (identity-anchored re-resolve, never positional). Every failure returns a typed envelope — no exception reaches the MCP client.

## Files modified

- **`src/ig_media_kit/http_client.py`** — added `CdnResponse` dataclass + `AnonymousClient.download_cdn(url)`: a redirect-following, anonymity-guarded binary GET that returns raw `content` bytes (not `.text`, which corrupts an mp4). CDN is unmetered — no pacing/stop handling.
- **`src/ig_media_kit/store.py`** — T3.1 `find_reel(shortcode, handles=…)` (config channels ∪ on-disk `*.csv`, first match wins, pure CSV read) + `handles_on_disk()` helper; T3.6 `update_local_mp4(handle, shortcode, local_mp4, video_url=, fetched_at=)` (full-manifest temp-write + `os.replace`, `CSV_COLUMNS` order + `QUOTE_MINIMAL` preserved, only the target row's three columns change).
- **`src/ig_media_kit/fetch.py`** — T3.4 `resolve_reel_url(...)` + `ResolveResult`/`ResolveOutcome` + `_identity_matches`: a find-by-identity traversal distinct from `fetch_window` (no seen/pin/watermark logic), matching `code == shortcode` (primary) / `pk == media_id` (numeric backstop), with an in-code assertion forbidding a positional pick. Politeness identical to the metered window (classify each page, stop on first stop_signal, cap pages, `sleep=None`).
- **`src/ig_media_kit/download.py`** (new) — T3.2/T3.3/T3.5/T3.7 orchestration: `run_download_reel(shortcode, *, config, client=None, store=None, now=…)`. Cached-hit gate (provable local artifact → return before touching the client), TTL-margin freshness (`URL_REFRESH_MARGIN_SECONDS = 24h`, documented under the ~36h TTL), reuse-or-re-resolve, `_download_to` (ftyp-verify at offset 4 → temp-write → `os.replace`; 0-byte/302/non-mp4 rejected without clobbering a prior file), atomic manifest update, and typed envelope builders.
- **`src/ig_media_kit/mcp_server.py`** — replaced the `download_reel` stub with real wiring (keeps `config_path`), wrapped in a final `try/except` backstop so the tool never throws.
- **`tests/conftest.py`** — `FakeResponse` now carries a binary `content` attribute for CDN download tests (default `b""`, existing callers unaffected).

## Tests written (`tests/test_download.py`, 16 tests)

ftyp offset-4 verification (rejects `ftyp` at offset 0); store resolver union + not-found; atomic manifest update changes only the target row and preserves comma-caption quoting; **cached-hit provably network-free** (`_NoNet` transport raises on any touch); stale-`local_mp4`-falls-through; in-margin reuse → no metadata call + exactly one redirect-following CDN GET; expired URL → one re-resolve + download + persisted fresh URL/`fetched_at`; **standing-order guard** (target NOT first in feed → identity match, not `items[0]`); numeric media_id backstop; re-resolve stop_signal → clean partial; not-found-in-budget → typed error; unknown shortcode → typed error (no network); ftyp rejects 0-byte / non-mp4 / non-200 bodies (no file written); happy path writes a valid `ftyp` on disk with no leftover temp; anonymity asserted on every recorded call.

Live pilot `probe/probe_download.py` written (opt-in, not in CI) — drives list_reels → download_reel end-to-end for one config channel and asserts a valid `ftyp` on disk; exits politely on any stop_signal (no retry/poll during cooldown). **Not run** (would hit IG; per instruction, offline unit tests only).

## Quality gate

`pytest`: **106 passed** (16 new), 0 failed, ~0.2s. Probe byte-compiles; package imports clean. No lint/type tooling is configured in the repo (no ruff/mypy/flake8/black in `pyproject.toml` or on PATH), so no separate lint/type step applies.

## Discovered follow-up

`# TODO:` marker left in `src/ig_media_kit/download.py` (at the download-failure branch, no issue number): when a *reused* in-margin stored URL fails to download (fbcdn can rotate a signed URL before the 24h margin → 403/302), fall back to one metered re-resolve rather than hard-erroring — guarded so it never retries after an already-refreshed URL fails.

`Ready for review — recommend multi-perspective-review.` (medium scope, multiple modules, an IG-hitting metered path, and the load-bearing standing-order correctness anchor.)
