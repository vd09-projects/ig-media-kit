---
artifact_type: handoff
artifact_version: 2
producer_role: review
consumer_role: implementation
plan_type: findings
slug: t3-download-reel-signed-url-refresh
scope_hint: T3 download_reel — mp4 download + signed-URL refresh
canonical_name: review-findings
overlays: []
status: draft
version: 1
created: 2026-07-16T01:40:00Z
updated: 2026-07-16T01:40:00Z
prior_versions: []
---

# Review findings: T3 download_reel — mp4 download + signed-URL refresh

## Triage Decision
Scope: medium (5 files touched + 2 new modules, ~350 lines; an IG-hitting metered re-resolve path)
Partition: backend
Memory overrides: `always_include` Reliability / Rate-Limit Reviewer; `always_exclude` Accessibility. Custom rule fired: IG-hitting path → Reliability mandatory, scope floored at medium.

Selected Reviewers:
- Reliability / Rate-Limit Reviewer (backend) — the re-resolve hits the metered feed API; politeness is load-bearing
- Security & Trust Reviewer (common) — anonymous-only invariant, new CDN GET path
- Data Integrity & Migration Reviewer (backend) — atomic CSV rewrite preserving all rows/columns/quoting
- Error Handling & Resilience Inspector (common) — typed-envelope contract, never-throw guarantee
- Domain Logic Reviewer (backend) — the STANDING ORDER (identity vs positional match)
- Test Coverage Auditor (common) — 16 new tests
- Tech Debt Sentinel (baseline) — the left-in TODO
- Naming & Clarity Guardian (baseline)

Skipped: Concurrency (single-process, no shared mutable state), Performance (bounded page walk, unmetered CDN), API/Contract (MCP tool signature unchanged — `config_path` kept), Migration/DB (flat-file, no schema).

---

## Reliability / Rate-Limit Reviewer — VERDICT: APPROVE (HIGH)

The metered/unmetered asymmetry is honored precisely.

- `download_cdn` (http_client.py:376) — unmetered fbcdn GET: no sleep, no `classify_response`, no stop handling, `allow_redirects=True` (redirect-follow invariant met; test asserts `cdn["allow_redirects"] is True` at test_download.py:194). Correct.
- `resolve_reel_url` (fetch.py:425) — metered path: caps at `max_pages` (wired to `config.fetch.max_pages_per_call`=4, download.py:123), calls `classify_response` on **every** page, and returns `STOP_SIGNAL` on the **first** `cls.is_stop` (fetch.py:466) — no continued paging, no poll during cooldown. `Outcome.ERROR` is also treated as stop (fetch.py:472), so a transient error can't loop.
- **Never sleeps on the sync download path:** `sleep=None` is passed (download.py:124) and the sleep is guarded by `sleep is not None and pace_seconds > 0` (fetch.py:453). Confirmed no sleep reachable.
- `x-ig-app-id` rides every metered call via `AnonymousClient` / `_get_feed_page`; test asserts `meta["headers"].get("x-ig-app-id") == IG_APP_ID` (test_download.py:217).
- Partial-on-stop: a 401 yields a typed partial with `stop_reason="rate_limited"` and a "budget cooling" note, exactly one feed page then stop (test_download.py:270–282).

No politeness violation. The probe (probe_download.py:60–68, 85–87) exits politely on any stop_signal and explicitly does not retry.

## Security & Trust Reviewer — VERDICT: APPROVE (HIGH)

No anonymity leak. `download_cdn` calls `assert_anonymous(headers=headers)` before sending, passes `cookies=None`, no auth header (http_client.py:389–398). The re-resolve reuses the existing anonymous transport. Test `_assert_anonymous_calls` scans every recorded call for `authorization` / `sessionid` / `ds_user_id` and is applied on the metered paths (test_download.py:195, 225). No login, cookie, token, or account path introduced. Invariant upheld.

## Data Integrity & Migration Reviewer — VERDICT: APPROVE (HIGH)

`update_local_mp4` (store.py:163) is a correct atomic full-manifest rewrite: read all rows → mutate only the matched row → write temp with `DictWriter(fieldnames=CSV_COLUMNS, quoting=QUOTE_MINIMAL)` → `fh.flush()` + `os.fsync` → `os.replace` (store.py:205–216). Matches the existing `_append_csv` / `_write_state_atomic` discipline. All other rows preserved verbatim; column order pinned to `CSV_COLUMNS`; `{col: row.get(col, "")}` projection prevents an unexpected key wedging the writer while filling any missing column with `""`. Comma-caption quoting round-trips (test_download.py:130). Returns `False` without writing when the shortcode is absent (store.py:200). `_download_to` likewise writes to `.tmp` and `os.replace`s only **after** ftyp-verify passes, so a bad body never clobbers a prior file (download.py:182–189; tests 315–345 assert no file written). No corruption or partial-write risk.

Note (FYI, not blocking): the mp4 `os.replace` lands before the manifest `update_local_mp4`. A crash between the two leaves the mp4 on disk with an empty `local_mp4` → next call simply re-downloads (idempotent). Acceptable; not corruption.

## Domain Logic Reviewer — VERDICT: APPROVE (HIGH)

The STANDING ORDER is correctly enforced. `resolve_reel_url` returns **only** on `_identity_matches` (shortcode primary, numeric `media_id` backstop — fetch.py:479, 505–509), never on `items[0]`. The in-code `assert` (fetch.py:480–483) guards against a positional pick. `test_reresolve_matches_identity_not_positional` (test_download.py:230) deliberately places the target at position 2 behind two newer reels and proves the position-0 trap is avoided. `_video_url`-is-None (e.g., a non-clip) is handled: `FOUND` with no URL falls into the "could not re-resolve" error branch (download.py:130). Correct.

## Error Handling & Resilience Inspector — VERDICT: APPROVE (MED)

Never-throw contract holds: `run_download_reel` returns typed envelopes on every branch, and `mcp_server.download_reel` wraps it in a final `try/except` backstop (mcp_server.py:88–96). Envelope shape is consistent via `_base`. One minor semantic overlap (suggestion, non-blocking): the not-found-in-budget case returns `_error(..., partial=True)` (download.py:130–136), so the envelope carries **both** `error` and `partial=True`, while the stop_signal case returns `_partial` with `stop_reason` and no `error`. A consumer that branches "retry vs hard-fail" on `partial` vs `error` sees an ambiguous record for the aged-out case. It is internally documented and the probe tolerates it (checks `partial` first), so it is a clarity nit, not a defect.

## Test Coverage Auditor — VERDICT: APPROVE with one gap (HIGH)

Strong coverage: ftyp offset-4 (positive + offset-0 negative + empty + wrong-tag), cached-hit provably network-free (`_NoNet`), stale-local falls through, in-margin reuse (1 CDN GET, no metadata), expired→re-resolve→persist, standing-order positional trap, stop_signal partial, not-found-in-budget, unknown shortcode, 0-byte/non-mp4/non-200 rejection with no file written, happy-path valid ftyp on disk + no leftover temp.

- **Suggestion (non-blocking) — the numeric media_id backstop is not actually exercised.** `test_reresolve_by_numeric_media_id_backstop` (test_download.py:251) seeds `code="OLDCODE"` and the feed item also uses `code="OLDCODE"` (test_download.py:259), so `_identity_matches` succeeds on the **shortcode** branch and never reaches the `pk == media_id` backstop. The test comment claims "shortcode differs in the feed" but it does not. To genuinely cover the backstop, give the feed item a *different* `code` (e.g. `"NEWCODE"`) with the same `pk=555`, so only the numeric match can fire.

## Tech Debt Sentinel — VERDICT: APPROVE (HIGH)

One deliberate, well-documented TODO at download.py:145–149: when a **reused** in-margin stored URL fails to download (fbcdn can rotate a signed URL before the 24 h margin → 403/302), it hard-errors instead of falling back to one metered re-resolve. The comment correctly notes the guard needed (never re-resolve after an already-refreshed URL fails, to avoid a metered retry loop). It is surfaced in the build summary's "Discovered follow-up." Accepted debt — recommend it become a tracked task, not a bare in-code TODO.

## Naming & Clarity Guardian — VERDICT: APPROVE (HIGH)

Names are precise and self-documenting (`ResolveOutcome`, `_identity_matches`, `URL_REFRESH_MARGIN_SECONDS`, `_looks_like_mp4`). `_FTYP_OFFSET = 4` with the box-layout comment (download.py:39–44) makes the offset-4 invariant legible. No concerns.

---

## Review Summary

| Reviewer | Verdict | Blocking | Suggestions | FYI | Confidence |
|---|---|---|---|---|---|
| Reliability / Rate-Limit | APPROVE | 0 | 0 | 0 | HIGH |
| Security & Trust | APPROVE | 0 | 0 | 0 | HIGH |
| Data Integrity | APPROVE | 0 | 0 | 1 | HIGH |
| Domain Logic | APPROVE | 0 | 0 | 0 | HIGH |
| Error Handling | APPROVE | 0 | 1 | 0 | MED |
| Test Coverage | APPROVE | 0 | 1 | 0 | HIGH |
| Tech Debt Sentinel | APPROVE | 0 | 1 | 0 | HIGH |
| Naming & Clarity | APPROVE | 0 | 0 | 0 | HIGH |

**Overall Recommendation:** APPROVE

**Rationale:** Every load-bearing CLAUDE.md invariant is satisfied and independently test-proven: anonymous-only (assert + cookie/auth-free CDN GET), metered/unmetered asymmetry (CDN never sleeps; re-resolve caps pages, classifies every page, stops on first 401, never polls a cooldown), `x-ig-app-id` on metered calls, redirect-follow on the CDN GET, atomic full-manifest CSV rewrite preserving all rows/columns/quoting, ftyp verify at byte offset 4, and the standing order (identity-anchored match with a positional-trap regression test). No blocking correctness bug, anonymity leak, politeness violation, or store-corruption risk found. The residual items are quality polish.

**Blocking Items:** none.

**Top Suggestions:**
1. Fix `test_reresolve_by_numeric_media_id_backstop` so the feed item's `code` differs from the seeded shortcode — currently it matches on shortcode and never exercises the numeric `media_id` backstop it claims to test (test_download.py:251–265).
2. Promote the reused-in-margin-URL 403 fallback TODO (download.py:145) to a tracked task with the "no metered retry after an already-refreshed URL fails" guard.
3. Consider disambiguating the aged-out envelope (download.py:130) so it does not carry both `error` and `partial=True` — pick one semantic, or add a distinct marker, for consumers that branch on retryability.

**Corroborated Findings:** none (no issue flagged by 2+ reviewers).

**Accepted Debt:** reused-in-margin signed-URL rotation fallback (download.py:145) — follow-up task, before/with the batch runner ticket that will exercise re-resolve at volume.
