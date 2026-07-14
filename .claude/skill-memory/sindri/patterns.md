# Learned Patterns — IG Media Kit
<!-- rune-generated: 2026-07-14 | git: acdd3c5 | rune: 1.0 -->

## Known Hot Spots

- Fetcher rate-limit / backoff path — the politeness invariant lives here — every change: verify pace, page cap, partial-on-401, no cooldown polling.
- Download path — fbcdn signed URLs — watch: redirect-follow required (`-L`), and ~36 h TTL (re-resolve via owner feed if stale).
- Cursor / coverage merge — `next_max_id` = `{media_id}_{user_id}` — watch: contiguity, skip-seen dedupe, gap backfill when >1 window of new posts.

## Recurring False Positives

- Single-IP, no-proxy, no-rotation — **intentional**, not a scaling gap (small scale by design; multi-IP was researched and rejected).
- No auth / no credentials anywhere — **intentional** (anonymous-only invariant), not missing functionality.
- `count=12` per page hardcoded — **intentional**; IG hard-caps the page size regardless of the requested count.

## Established Conventions (Not in CLAUDE.md)

- Reel record fields mirror the feed JSON keys: `code`(shortcode), `play_count`, `ig_play_count`, `like_count`, `comment_count`, `caption.text`, `taken_at`, `video_duration`, `product_type`, `video_versions[0].url`.
- Store writes are checkpointed after every page (crash-safe resume).

## Accepted Debt

- Async batch runner is an in-process background thread, not a job queue — location: batch subsystem — follow-up: revisit only if scale grows past manual/occasional use.
- URL-TTL treated as a fixed ~24 h refresh margin (under the observed ~36 h) — follow-up: make configurable if fbcdn TTL changes.
