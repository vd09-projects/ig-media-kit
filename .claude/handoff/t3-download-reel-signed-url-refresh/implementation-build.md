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
version: 2
created: 2026-07-16T01:20:00Z
updated: 2026-07-16T01:45:00Z
prior_versions:
  - implementation-build-v1.md
---

# Build summary: T3 download_reel — mp4 download + signed-URL refresh

Iterate mode — surgical. Addressed exactly the two build-review suggestions on branch `feat/t3-download-reel`; no refactors, no unrelated changes. The reused-in-margin-URL 403 fallback TODO at `download.py` was left AS-IS (tracked as a separate follow-up). All CLAUDE.md invariants held: anonymous-only (no auth path touched), typed envelopes, politeness (metered re-resolve still stops on first stop_signal, CDN download still never sleeps).

## Files modified

- **`src/ig_media_kit/download.py`** — (B) In `run_download_reel`, the aged-out / not-found-in-budget branch (`rr.outcome is NOT_FOUND or not rr.video_url`) no longer sets `partial=True`. It now returns a clean **non-partial typed error**, disambiguated from the stop_signal `_partial` case (metered cooldown, retryable). A consumer branching on retryability can now distinguish "metered cooldown — retry in minutes" (`partial=True` + `stop_reason`) from "reel aged out of the reachable page budget" (typed `error`, `partial=False`). Added an inline comment documenting the two distinct semantics. The `_error` builder signature is unchanged (its `partial` kwarg defaults to `False`); no other branch altered. The 403-fallback TODO left untouched.

- **`tests/test_download.py`** —
  - (A) `test_reresolve_by_numeric_media_id_backstop`: the target feed item now carries a **different `code` (`"RENAMED"`) but the same numeric `pk=555`** as the seeded shortcode (`"OLDCODE"`). Since no feed item shares the seeded shortcode, only the numeric media_id backstop (`pk == media_id`) can select it — proving the non-positional, identity-by-media_id behavior rather than accidentally matching on shortcode. Added `assert all(it["code"] != "OLDCODE" ...)` to lock the premise and an assertion that the selected URL is not the positional `items[0]`.
  - (B) `test_reresolve_not_found_in_budget_returns_typed_error`: now asserts `env["partial"] is False` (was `is True`) while still asserting the typed `error` message and `local_mp4 is None`. `test_reresolve_stop_signal_returns_partial` confirmed unchanged — still asserts `partial is True` + `stop_reason == "rate_limited"`, so both envelope shapes are pinned.

## Tests written / updated

Two existing tests amended (no new test files); the suite now asserts the two envelope shapes are genuinely distinct on the `partial` flag, and the media_id backstop is exercised by identity rather than shortcode coincidence.

## Quality gate

`uv run pytest -q`: **106 passed, 0 failed** (~0.24s). No lint/type tooling configured in the repo (unchanged).

## Discovered follow-up

`# TODO:` marker at `src/ig_media_kit/download.py` (download-failure branch, no issue number) intentionally retained: fall back to ONE metered re-resolve when a reused in-margin stored URL 403/302s before the 24 h margin. Tracked as a separate task.

`Ready for review.`
