# ig-media-kit

Self-hosted MCP that fetches top Instagram **reels** (with view counts) from a list of
channels — no login, no account, no personal-identity link. The Instagram counterpart to
`yt-media-kit`.

**Status:** research complete, implementation pending (see open issues).

## Start here
- [`problem-statement.md`](problem-statement.md) — what we're building and why.
- [`research/no-login-reel-fetch/report.md`](research/no-login-reel-fetch/report.md) —
  the verified architecture decision (v4): anonymous `feed/user` fetch, 4-tool MCP,
  call-driven fill, async batch. All 12 load-bearing claims pilot-verified 2026-07-14.
- `research/no-login-reel-fetch/architecture.html` — visual architecture + flow diagrams.

## The one-line design
Anonymous `GET /api/v1/feed/user/{id}/` returns `play_count` + metadata + the mp4 URL in
one call (no cookies). Metadata API is metered (~48 items / ~6.6-min window per IP); the
video CDN is not. Python + curl_cffi + FastMCP. No yt-dlp, no browser automation.
